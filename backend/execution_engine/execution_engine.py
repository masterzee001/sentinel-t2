"""Semi-automated execution engine for Project Sentinel."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml
from loguru import logger

from backend.execution_engine.readiness_checker import ReadinessChecker
from backend.journal.journal_engine import JournalEngine
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.symbols.symbol_registry import SymbolRegistry


class ExecutionEngineError(RuntimeError):
    """Raised when execution preparation or submission fails."""


ManualConfirmation = Callable[[dict[str, Any]], bool]


class ExecutionEngine:
    """Validate, prepare, and optionally submit assisted MT5 orders."""

    DEFAULT_CONFIG = {
        "execution_mode": "advisor",
        "allowed_modes": ["advisor", "assisted", "autonomous"],
        "assisted_mode": {
            "require_manual_confirmation": True,
            "max_slippage_points": 50,
            "allow_market_orders": True,
            "allow_limit_orders": True,
            "allow_stop_orders": False,
        },
        "safety": {
            "reject_if_news_lock": True,
            "reject_if_risk_blocked": True,
            "reject_if_guardrail_blocked": True,
            "reject_if_confidence_below": 90,
            "reject_if_rr_below": 3.0,
            "reject_if_spread_too_high": True,
        },
    }
    SUPPORTED_MODES = frozenset({"advisor", "assisted"})
    ORDER_TYPE_MAP = {
        ("bullish", "market"): "BUY",
        ("bearish", "market"): "SELL",
        ("bullish", "limit"): "BUY_LIMIT",
        ("bearish", "limit"): "SELL_LIMIT",
    }

    def __init__(
        self,
        connector: MT5Connector | None = None,
        config_dir: str | Path | None = None,
        journal_engine: JournalEngine | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.connector = connector or MT5Connector()
        self.journal_engine = journal_engine
        self.config = self._load_config()
        self.readiness_checker = ReadinessChecker(connector=self.connector, config_dir=self.config_dir)
        self.symbol_registry = SymbolRegistry(config_dir=self.config_dir)

    def prepare_execution(
        self,
        trade_plan: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        """Validate a trade plan and build an MT5 order request without submitting."""
        context = context or {}
        execution_mode = self.normalize_mode(mode or self.config.get("execution_mode", "advisor"))
        symbol = str(trade_plan.get("symbol", "")).upper().strip()
        symbol_block = self.symbol_tier_block_reason(symbol)
        if symbol_block:
            return {
                "symbol": symbol,
                "execution_mode": execution_mode,
                "execution_allowed": False,
                "order_type": None,
                "manual_confirmation_required": self.manual_confirmation_required(execution_mode),
                "validation_status": "BLOCKED",
                "validation_reasons": [symbol_block],
                "order_request": {},
            }
        order_type = self.determine_order_type(trade_plan)
        validation_reasons = self.validate_safety(trade_plan, context=context, order_type=order_type)
        validation_status = "PASS" if not validation_reasons else "BLOCKED"
        execution_allowed = execution_mode == "assisted" and validation_status == "PASS"
        order_request = self.build_order_request(trade_plan, order_type) if validation_status == "PASS" else {}

        return {
            "symbol": symbol,
            "execution_mode": execution_mode,
            "execution_allowed": execution_allowed,
            "order_type": order_type,
            "manual_confirmation_required": self.manual_confirmation_required(execution_mode),
            "validation_status": validation_status,
            "validation_reasons": validation_reasons,
            "order_request": order_request,
        }

    def execute(
        self,
        trade_plan: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        mode: str | None = None,
        confirmation_callback: ManualConfirmation | None = None,
    ) -> dict[str, Any]:
        """Prepare and, in assisted mode only, submit an order after manual approval."""
        prepared = self.prepare_execution(trade_plan, context=context, mode=mode)
        result = {
            **prepared,
            "order_submitted": False,
            "order_result": "NOT_SUBMITTED",
            "ticket": None,
            "broker_message": "",
        }

        if prepared["validation_status"] != "PASS":
            result["order_result"] = "BLOCKED_OBSERVER_SYMBOL" if self.has_symbol_tier_block(prepared) else "BLOCKED_BY_SAFETY"
            result["broker_message"] = "; ".join(prepared["validation_reasons"])
            return self.record_execution(result)

        if prepared["execution_mode"] == "advisor":
            result["order_result"] = "ADVISOR_MODE_NO_ORDER"
            result["broker_message"] = "Advisor mode does not submit orders."
            return self.record_execution(result)

        if prepared["execution_mode"] != "assisted":
            result["order_result"] = "MODE_NOT_IMPLEMENTED"
            result["broker_message"] = "Autonomous execution is not implemented."
            return self.record_execution(result)

        if self.manual_confirmation_required(prepared["execution_mode"]):
            approved = self.request_manual_confirmation(prepared, confirmation_callback)
            if not approved:
                result["order_result"] = "REJECTED_BY_USER"
                result["broker_message"] = "Manual confirmation rejected."
                return self.record_execution(result)

        readiness = self.readiness_checker.check(
            trade_plan,
            context=context or {},
            execution_mode=prepared["execution_mode"],
            manual_confirmation_required=self.manual_confirmation_required(prepared["execution_mode"]),
            connector=self.connector,
        )
        result["readiness"] = readiness
        if not readiness["ready"]:
            result["order_result"] = "BLOCKED_BY_READINESS"
            result["broker_message"] = "; ".join(readiness["blocking_reasons"])
            return self.record_execution(result)

        submit_result = self.submit_order(prepared["order_request"])
        result.update(submit_result)
        return self.record_execution(result)

    def validate_safety(
        self,
        trade_plan: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        order_type: str | None = None,
    ) -> list[str]:
        """Return safety rejection reasons for a candidate order."""
        context = context or {}
        safety = self.config.get("safety", {})
        assisted = self.config.get("assisted_mode", {})
        reasons: list[str] = []
        confidence = context.get("confidence", {})
        risk = context.get("risk", {})
        news = context.get("news", {})
        guardrail = context.get("guardrail", confidence.get("guardrail", {}))
        risk_blocked = bool(context.get("risk_blocked", False))

        if not trade_plan.get("execution_allowed"):
            reasons.append("Trade plan execution is not allowed")
        if safety.get("reject_if_news_lock", True) and (
            context.get("news_lock_active") or news.get("lock_active")
        ):
            reasons.append("High impact news lock active")
        if safety.get("reject_if_risk_blocked", True) and (
            risk_blocked or not risk.get("permission", {"trade_allowed": True}).get("trade_allowed", True)
        ):
            reasons.append("Risk Governor blocked")
        if safety.get("reject_if_guardrail_blocked", True) and guardrail.get("status") == "BLOCKED":
            reasons.append("Strategy guardrail blocked")

        total_confidence = self.extract_confidence(trade_plan, context)
        if total_confidence < float(safety.get("reject_if_confidence_below", 90)):
            reasons.append("Confidence below execution threshold")

        rr_to_final = float(trade_plan.get("risk", {}).get("rr_to_tp3", 0.0) or 0.0)
        if rr_to_final < float(safety.get("reject_if_rr_below", 3.0)):
            reasons.append("RR below 3")

        if safety.get("reject_if_spread_too_high", True) and self.is_spread_too_high(context):
            reasons.append("Spread too high")

        if order_type in {"BUY", "SELL"} and not bool(assisted.get("allow_market_orders", True)):
            reasons.append("Market orders disabled")
        if order_type in {"BUY_LIMIT", "SELL_LIMIT"} and not bool(assisted.get("allow_limit_orders", True)):
            reasons.append("Limit orders disabled")
        if order_type and order_type.endswith("_STOP") and not bool(assisted.get("allow_stop_orders", False)):
            reasons.append("Stop orders disabled")

        return self.dedupe(reasons)

    @classmethod
    def determine_order_type(cls, trade_plan: dict[str, Any]) -> str:
        """Return BUY/SELL/BUY_LIMIT/SELL_LIMIT from direction and entry source."""
        direction = str(trade_plan.get("direction", "")).lower().strip()
        entry = trade_plan.get("entry", {})
        entry_type = str(entry.get("type", "")).lower().strip()
        entry_source = str(entry.get("source", "")).lower()
        style = "market" if entry_type == "market" or "immediate" in entry_source else "limit"
        if "fvg" in entry_source or "ob" in entry_source or "order_block" in entry_source or "retracement" in entry_source:
            style = "limit"
        order_type = cls.ORDER_TYPE_MAP.get((direction, style))
        if not order_type:
            raise ExecutionEngineError("Cannot determine order type without bullish or bearish direction.")
        return order_type

    def build_order_request(self, trade_plan: dict[str, Any], order_type: str) -> dict[str, Any]:
        """Build an MT5-compatible order request dictionary."""
        entry = trade_plan.get("entry", {})
        stop_loss = trade_plan.get("stop_loss", {})
        take_profit = trade_plan.get("take_profit", {})
        risk = trade_plan.get("risk", {})
        symbol = str(trade_plan.get("symbol", "")).upper().strip()
        lot_size = float(risk.get("lot_size", 0.0) or 0.0)
        entry_price = float(entry.get("price", 0.0) or 0.0)

        if not symbol or lot_size <= 0 or entry_price <= 0:
            raise ExecutionEngineError("Cannot build order request without symbol, lot size, and entry price.")

        return {
            "action": self.mt5_constant("TRADE_ACTION_DEAL") if order_type in {"BUY", "SELL"} else self.mt5_constant("TRADE_ACTION_PENDING"),
            "symbol": symbol,
            "volume": lot_size,
            "type": self.mt5_constant(f"ORDER_TYPE_{order_type}"),
            "price": entry_price,
            "sl": float(stop_loss.get("price", 0.0) or 0.0),
            "tp": float(take_profit.get("tp3", 0.0) or 0.0),
            "deviation": int(self.config.get("assisted_mode", {}).get("max_slippage_points", 50)),
            "magic": 22001,
            "comment": "Project Sentinel assisted order",
            "type_time": self.mt5_constant("ORDER_TIME_GTC"),
            "type_filling": self.mt5_constant("ORDER_FILLING_RETURN"),
        }

    def submit_order(self, order_request: dict[str, Any]) -> dict[str, Any]:
        """Submit order request through the existing MT5 connector."""
        symbol = str(order_request.get("symbol", "")).upper().strip()
        symbol_block = self.symbol_tier_block_reason(symbol)
        if symbol_block:
            return {
                "order_submitted": False,
                "order_result": "BLOCKED_OBSERVER_SYMBOL",
                "ticket": None,
                "broker_message": symbol_block,
                "raw_order_result": {},
            }
        mt5_module = getattr(self.connector, "mt5", None)
        if mt5_module is None or not hasattr(mt5_module, "order_send"):
            raise ExecutionEngineError("MT5 order_send is unavailable.")

        try:
            response = mt5_module.order_send(order_request)
        except Exception as exc:  # pragma: no cover - vendor package defensive wrapper.
            raise ExecutionEngineError(f"MT5 order_send failed: {exc}") from exc

        response_dict = self.to_dict(response)
        retcode = response_dict.get("retcode")
        success_code = self.mt5_constant("TRADE_RETCODE_DONE")
        submitted = retcode in {success_code, "TRADE_RETCODE_DONE", 10009}
        return {
            "order_submitted": submitted,
            "order_result": "SUCCESS" if submitted else self.map_broker_result(response_dict),
            "ticket": response_dict.get("order") or response_dict.get("ticket"),
            "broker_message": response_dict.get("comment", "") or response_dict.get("message", ""),
            "raw_order_result": response_dict,
        }

    def request_manual_confirmation(
        self,
        prepared: dict[str, Any],
        confirmation_callback: ManualConfirmation | None = None,
    ) -> bool:
        """Return manual approval from callback or terminal prompt."""
        if confirmation_callback:
            return bool(confirmation_callback(prepared))
        answer = input(self.format_manual_confirmation_prompt(prepared))
        return answer.strip().upper() == "Y"

    @staticmethod
    def format_manual_confirmation_prompt(prepared: dict[str, Any]) -> str:
        """Return assisted-mode terminal confirmation prompt."""
        request = prepared.get("order_request", {})
        return (
            "\nPROJECT SENTINEL EXECUTION REQUEST\n\n"
            f"Symbol: {prepared.get('symbol')}\n"
            f"Order Type: {prepared.get('order_type')}\n"
            f"Entry: {request.get('price')}\n"
            f"SL: {request.get('sl')}\n"
            f"TP3: {request.get('tp')}\n"
            f"Lot: {request.get('volume')}\n\n"
            "Approve? [Y/N] "
        )

    def record_execution(self, result: dict[str, Any]) -> dict[str, Any]:
        """Attach and optionally persist execution journal fields."""
        journal_record = {
            "execution_mode": result.get("execution_mode"),
            "order_submitted": result.get("order_submitted", False),
            "order_result": result.get("order_result"),
            "ticket": result.get("ticket"),
            "broker_message": result.get("broker_message", ""),
            "readiness": result.get("readiness", {}),
        }
        result["journal_record"] = journal_record
        if self.journal_engine is not None:
            self.journal_engine.append_record({"type": "execution", **journal_record, "symbol": result.get("symbol")})
        return result

    def manual_confirmation_required(self, execution_mode: str) -> bool:
        """Return whether assisted mode requires explicit manual approval."""
        return execution_mode == "assisted" and bool(
            self.config.get("assisted_mode", {}).get("require_manual_confirmation", True)
        )

    def normalize_mode(self, mode: str) -> str:
        """Validate and normalize execution mode."""
        normalized = str(mode).lower().strip()
        if normalized not in self.config.get("allowed_modes", []):
            raise ValueError(f"Unsupported execution mode '{mode}'.")
        if normalized == "autonomous":
            raise ExecutionEngineError("Autonomous execution is not implemented.")
        if normalized not in self.SUPPORTED_MODES:
            raise ExecutionEngineError(f"Execution mode '{mode}' is not supported in V0.1.")
        return normalized

    def symbol_tier_block_reason(self, symbol: str) -> str:
        """Return hard execution-boundary reason for non-production symbols."""
        normalized = str(symbol).upper().strip()
        if not normalized or not self.symbol_registry.execution_allowed(normalized):
            return f"BLOCKED_OBSERVER_SYMBOL: {normalized or 'UNKNOWN'} is not allowed for production execution"
        return ""

    @staticmethod
    def has_symbol_tier_block(prepared: dict[str, Any]) -> bool:
        return any(str(reason).startswith("BLOCKED_OBSERVER_SYMBOL") for reason in prepared.get("validation_reasons", []))

    def mt5_constant(self, name: str) -> Any:
        """Return MT5 constant when available, otherwise a readable fallback."""
        mt5_module = getattr(self.connector, "mt5", None)
        return getattr(mt5_module, name, name)

    @staticmethod
    def extract_confidence(trade_plan: dict[str, Any], context: dict[str, Any]) -> float:
        """Return confidence from context or trade plan."""
        confidence = context.get("confidence", {})
        return float(
            confidence.get("guardrail_adjusted_confidence")
            or confidence.get("total_confidence")
            or trade_plan.get("confidence")
            or trade_plan.get("total_confidence")
            or 0.0
        )

    @staticmethod
    def is_spread_too_high(context: dict[str, Any]) -> bool:
        """Return whether current spread exceeds supplied max spread context."""
        spread = context.get("spread_points")
        max_spread = context.get("max_spread_points")
        if spread is None or max_spread is None:
            return False
        return float(spread) > float(max_spread)

    @staticmethod
    def map_broker_result(response: dict[str, Any]) -> str:
        """Return normalized broker result label."""
        comment = str(response.get("comment", "")).lower()
        retcode = str(response.get("retcode", "")).lower()
        if "invalid price" in comment:
            return "INVALID_PRICE"
        if "money" in comment or "margin" in comment:
            return "INSUFFICIENT_MARGIN"
        if "market closed" in comment or "closed" in comment:
            return "MARKET_CLOSED"
        if "reject" in comment or "reject" in retcode:
            return "REJECTED"
        return "REJECTED"

    @staticmethod
    def to_dict(value: Any) -> dict[str, Any]:
        """Convert MT5 response to dict."""
        if value is None:
            return {}
        if hasattr(value, "_asdict"):
            return dict(value._asdict())
        if isinstance(value, dict):
            return dict(value)
        if hasattr(value, "__dict__"):
            return dict(value.__dict__)
        return {"retcode": None, "comment": str(value)}

    @staticmethod
    def dedupe(reasons: list[str]) -> list[str]:
        """Preserve-order de-duplication."""
        deduped: list[str] = []
        for reason in reasons:
            if reason and reason not in deduped:
                deduped.append(reason)
        return deduped

    def _load_config(self) -> dict[str, Any]:
        config = self._load_yaml_file(self.config_dir / "execution.yaml")
        return self._deep_merge(self.DEFAULT_CONFIG, config)

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist; using execution defaults.", path)
            return {}
        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise ExecutionEngineError(f"Failed to load config {path}: {exc}") from exc

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
