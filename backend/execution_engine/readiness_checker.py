"""Final assisted-execution readiness checklist for Project Sentinel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from backend.market_data.mt5_connector import MT5Connector


class ReadinessCheckerError(RuntimeError):
    """Raised when readiness configuration cannot be loaded."""


class ReadinessChecker:
    """Run the final pre-flight checklist before assisted order submission."""

    DEFAULT_CONFIG = {
        "enabled": True,
        "checks": {
            "mt5_connected": True,
            "account_verified": True,
            "risk_allowed": True,
            "news_clear": True,
            "killzone_valid": True,
            "guardrails_pass": True,
            "spread_acceptable": True,
            "lot_valid": True,
            "rr_minimum": 3.0,
            "execution_mode_assisted": True,
            "manual_confirmation_required": True,
        },
        "spread_limits": {
            "XAUUSD": 80,
            "US30": 150,
            "EURUSD": 20,
            "GBPUSD": 25,
            "BTCUSD": 300,
        },
        "allowed_accounts": {
            "demo": ["MetaQuotes-Demo"],
            "live": [],
        },
    }
    CHECK_ORDER = (
        "mt5_connected",
        "account_verified",
        "risk_allowed",
        "news_clear",
        "killzone_valid",
        "guardrails_pass",
        "spread_acceptable",
        "lot_valid",
        "rr_validation",
        "execution_mode_assisted",
        "manual_confirmation_required",
    )
    DISPLAY_NAMES = {
        "mt5_connected": "MT5 Connection",
        "account_verified": "Account Verification",
        "risk_allowed": "Risk Governor",
        "news_clear": "News Filter",
        "killzone_valid": "Killzone",
        "guardrails_pass": "Guardrails",
        "spread_acceptable": "Spread",
        "lot_valid": "Lot Validation",
        "rr_validation": "RR Validation",
        "execution_mode_assisted": "Execution Mode",
        "manual_confirmation_required": "Manual Confirmation",
    }

    def __init__(
        self,
        *,
        connector: MT5Connector | None = None,
        config_dir: str | Path | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.connector = connector
        self.config = self._deep_merge(self.DEFAULT_CONFIG, self._load_yaml_file(self.config_dir / "readiness.yaml"))

    def check(
        self,
        trade_plan: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        execution_mode: str = "advisor",
        manual_confirmation_required: bool = True,
        connector: MT5Connector | None = None,
    ) -> dict[str, Any]:
        """Return the final readiness checklist result."""
        context = context or {}
        connector = connector or self.connector
        if not self.enabled:
            return self.build_result([self.result("readiness_enabled", "PASS", None)])

        results = [
            self.check_mt5_connected(connector, context),
            self.check_account_verified(connector, context),
            self.check_risk_allowed(context),
            self.check_news_clear(context),
            self.check_killzone_valid(context),
            self.check_guardrails_pass(context),
            self.check_spread_acceptable(trade_plan, context, connector),
            self.check_lot_valid(trade_plan, context, connector),
            self.check_rr_minimum(trade_plan),
            self.check_execution_mode(execution_mode),
            self.check_manual_confirmation(manual_confirmation_required),
        ]
        return self.build_result(results)

    def check_mt5_connected(self, connector: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Fail when MT5 is unavailable or disconnected."""
        if not self.check_enabled("mt5_connected"):
            return self.result("mt5_connected", "PASS", None)
        if context.get("mt5_connected") is not None:
            return self.pass_fail("mt5_connected", bool(context.get("mt5_connected")), "MT5 terminal disconnected")
        if connector is None:
            return self.result("mt5_connected", "FAIL", "MT5 connector unavailable")
        if hasattr(connector, "is_initialized"):
            return self.pass_fail("mt5_connected", bool(connector.is_initialized()), "MT5 terminal disconnected")
        return self.pass_fail("mt5_connected", getattr(connector, "mt5", None) is not None, "MT5 terminal disconnected")

    def check_account_verified(self, connector: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Verify login/server/account mode against safe config."""
        if not self.check_enabled("account_verified"):
            return self.result("account_verified", "PASS", None)
        account = self.extract_account(connector, context)
        login = account.get("login")
        server = str(account.get("server", "") or "")
        mode = str(account.get("account_mode", context.get("account_mode", "demo")) or "demo").lower()
        if not login:
            return self.result("account_verified", "FAIL", "Account login unavailable")
        if not server:
            return self.result("account_verified", "FAIL", "Account server unavailable")
        allowed = [str(value) for value in self.config.get("allowed_accounts", {}).get(mode, [])]
        if allowed and server not in allowed:
            return self.result("account_verified", "FAIL", f"Account server {server} is not allowed for {mode}")
        if mode == "live" and not allowed:
            return self.result("account_verified", "FAIL", "Live account aliases are not configured")
        return self.result("account_verified", "PASS", None)

    def check_risk_allowed(self, context: dict[str, Any]) -> dict[str, Any]:
        """Require Risk Governor ALLOWED status."""
        if not self.check_enabled("risk_allowed"):
            return self.result("risk_allowed", "PASS", None)
        permission = context.get("risk", {}).get("permission", {})
        status = str(permission.get("status", "") or "").upper()
        trade_allowed = permission.get("trade_allowed")
        passed = status == "ALLOWED" or trade_allowed is True
        return self.pass_fail("risk_allowed", passed, "Risk Governor blocked")

    def check_news_clear(self, context: dict[str, Any]) -> dict[str, Any]:
        """Require no active high-impact news lock."""
        if not self.check_enabled("news_clear"):
            return self.result("news_clear", "PASS", None)
        news = context.get("news", {})
        locked = bool(context.get("news_lock_active", False) or news.get("lock_active", False))
        status = str(news.get("status", "") or "").upper()
        passed = not locked and status in {"", "CLEAR"}
        return self.pass_fail("news_clear", passed, news.get("reason") or "News lock active")

    def check_killzone_valid(self, context: dict[str, Any]) -> dict[str, Any]:
        """Require valid killzone."""
        if not self.check_enabled("killzone_valid"):
            return self.result("killzone_valid", "PASS", None)
        killzone = context.get("killzone", {})
        passed = bool(killzone.get("is_valid", False))
        return self.pass_fail("killzone_valid", passed, "Invalid killzone")

    def check_guardrails_pass(self, context: dict[str, Any]) -> dict[str, Any]:
        """Require strategy guardrails not blocked."""
        if not self.check_enabled("guardrails_pass"):
            return self.result("guardrails_pass", "PASS", None)
        confidence = context.get("confidence", {})
        guardrail = context.get("guardrail", confidence.get("guardrail", {}))
        passed = str(guardrail.get("status", "PASS")).upper() != "BLOCKED"
        reasons = guardrail.get("reasons", []) or []
        reason = ", ".join(str(item) for item in reasons) if reasons else "Strategy guardrail blocked"
        return self.pass_fail("guardrails_pass", passed, reason)

    def check_spread_acceptable(self, trade_plan: dict[str, Any], context: dict[str, Any], connector: Any) -> dict[str, Any]:
        """Require spread at or below configured symbol limit."""
        if not self.check_enabled("spread_acceptable"):
            return self.result("spread_acceptable", "PASS", None)
        symbol = self.symbol(trade_plan)
        spread = self.current_spread(symbol, context, connector)
        limit = float(context.get("max_spread_points") or self.config.get("spread_limits", {}).get(symbol, 0) or 0)
        if spread is None:
            return self.result("spread_acceptable", "FAIL", "Spread unavailable")
        if limit <= 0:
            return self.result("spread_acceptable", "FAIL", f"Spread limit unavailable for {symbol}")
        return self.pass_fail("spread_acceptable", float(spread) <= limit, f"Spread too high: {spread} > {limit}")

    def check_lot_valid(self, trade_plan: dict[str, Any], context: dict[str, Any], connector: Any) -> dict[str, Any]:
        """Require lot size above zero and inside broker constraints."""
        if not self.check_enabled("lot_valid"):
            return self.result("lot_valid", "PASS", None)
        lot = float(trade_plan.get("risk", {}).get("lot_size", trade_plan.get("lot_size", 0.0)) or 0.0)
        if lot <= 0:
            return self.result("lot_valid", "FAIL", "Lot size must be greater than zero")
        constraints = context.get("symbol_info") or self.symbol_info(self.symbol(trade_plan), connector)
        minimum = float(constraints.get("volume_min", 0.0) or 0.0)
        maximum = float(constraints.get("volume_max", 0.0) or 0.0)
        step = float(constraints.get("volume_step", 0.0) or 0.0)
        if minimum and lot < minimum:
            return self.result("lot_valid", "FAIL", f"Lot below broker minimum {minimum}")
        if maximum and lot > maximum:
            return self.result("lot_valid", "FAIL", f"Lot above broker maximum {maximum}")
        if step and minimum:
            steps = round((lot - minimum) / step)
            aligned = abs((minimum + steps * step) - lot) < 1e-8
            if not aligned:
                return self.result("lot_valid", "FAIL", f"Lot does not match broker step {step}")
        return self.result("lot_valid", "PASS", None)

    def check_rr_minimum(self, trade_plan: dict[str, Any]) -> dict[str, Any]:
        """Require minimum RR to final target."""
        minimum = float(self.config.get("checks", {}).get("rr_minimum", 3.0))
        rr = float(trade_plan.get("risk", {}).get("rr_to_tp3", trade_plan.get("rr_to_tp3", 0.0)) or 0.0)
        return self.pass_fail("rr_validation", rr >= minimum, f"RR below {minimum}")

    def check_execution_mode(self, execution_mode: str) -> dict[str, Any]:
        """Require assisted mode."""
        if not self.check_enabled("execution_mode_assisted"):
            return self.result("execution_mode_assisted", "PASS", None)
        return self.pass_fail("execution_mode_assisted", str(execution_mode).lower() == "assisted", "Execution mode must be assisted")

    def check_manual_confirmation(self, manual_confirmation_required: bool) -> dict[str, Any]:
        """Require manual confirmation to remain enabled."""
        if not self.check_enabled("manual_confirmation_required"):
            return self.result("manual_confirmation_required", "PASS", None)
        return self.pass_fail("manual_confirmation_required", bool(manual_confirmation_required), "Manual confirmation is required")

    def current_spread(self, symbol: str, context: dict[str, Any], connector: Any) -> float | None:
        """Return spread in points from context or MT5."""
        if context.get("spread_points") is not None:
            return float(context["spread_points"])
        if context.get("tick") and context.get("symbol_info"):
            tick = context["tick"]
            info = context["symbol_info"]
            point = float(info.get("point", 0.0) or 0.0)
            if point:
                return round((float(tick.get("ask", 0.0)) - float(tick.get("bid", 0.0))) / point, 2)
        if connector is not None and hasattr(connector, "get_latest_tick") and hasattr(connector, "get_symbol_info"):
            try:
                tick = connector.get_latest_tick(symbol)
                info = connector.get_symbol_info(symbol)
                point = float(info.get("point", 0.0) or 0.0)
                if point:
                    return round((float(tick.get("ask", 0.0)) - float(tick.get("bid", 0.0))) / point, 2)
            except Exception as exc:
                logger.warning("Readiness spread check unavailable for {}: {}", symbol, exc)
        return None

    def extract_account(self, connector: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Return account info from context or connector."""
        account = context.get("account") or context.get("risk", {}).get("account")
        if account:
            return dict(account)
        if connector is not None and hasattr(connector, "get_account_info"):
            try:
                return connector.get_account_info()
            except Exception as exc:
                logger.warning("Readiness account verification unavailable: {}", exc)
        return {}

    @staticmethod
    def symbol_info(symbol: str, connector: Any) -> dict[str, Any]:
        """Return broker symbol info if available."""
        if connector is not None and hasattr(connector, "get_symbol_info"):
            try:
                return connector.get_symbol_info(symbol)
            except Exception as exc:
                logger.warning("Readiness lot constraints unavailable for {}: {}", symbol, exc)
        return {}

    @staticmethod
    def symbol(trade_plan: dict[str, Any]) -> str:
        return str(trade_plan.get("symbol", "")).upper().strip()

    def build_result(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        """Return canonical readiness payload."""
        blocking_reasons = [str(item["reason"]) for item in results if item["status"] == "FAIL" and item.get("reason")]
        checks_passed = sum(1 for item in results if item["status"] == "PASS")
        checks_failed = sum(1 for item in results if item["status"] == "FAIL")
        ready = checks_failed == 0 and checks_passed == len(self.CHECK_ORDER)
        return {
            "ready": ready,
            "score": checks_passed,
            "checks_passed": checks_passed,
            "checks_failed": checks_failed,
            "results": results,
            "blocking_reasons": blocking_reasons,
        }

    def check_enabled(self, key: str) -> bool:
        return bool(self.config.get("checks", {}).get(key, True))

    @classmethod
    def pass_fail(cls, check: str, passed: bool, reason: str | None) -> dict[str, Any]:
        return cls.result(check, "PASS" if passed else "FAIL", None if passed else reason)

    @staticmethod
    def result(check: str, status: str, reason: str | None) -> dict[str, Any]:
        return {"check": check, "status": status, "reason": reason}

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    @classmethod
    def format_report(cls, readiness: dict[str, Any]) -> str:
        """Return terminal-friendly readiness report."""
        lines = ["PROJECT SENTINEL READINESS CHECK", ""]
        lookup = {item["check"]: item for item in readiness.get("results", [])}
        for key in cls.CHECK_ORDER:
            result = lookup.get(key, {"status": "FAIL", "reason": "Missing check"})
            lines.append(f"{cls.DISPLAY_NAMES[key]}: {result.get('status', 'FAIL')}")
        lines.extend(["", "FINAL STATUS:"])
        lines.append("READY FOR ASSISTED EXECUTION" if readiness.get("ready") else "BLOCKED")
        if readiness.get("blocking_reasons"):
            lines.extend(["", "Reasons:"])
            lines.extend(f"- {reason}" for reason in readiness["blocking_reasons"])
        return "\n".join(lines)

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist; using readiness defaults.", path)
            return {}
        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise ReadinessCheckerError(f"Failed to load config {path}: {exc}") from exc

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
