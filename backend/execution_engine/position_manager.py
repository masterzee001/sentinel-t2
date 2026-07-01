"""Position lifecycle manager for Project Sentinel assisted execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml
from loguru import logger

from backend.market_data.mt5_connector import MT5Connector
from backend.production_promotion import ProductionPromotionEngine
from backend.symbols.symbol_registry import SymbolRegistry


class PositionManagerError(RuntimeError):
    """Raised when position management cannot be completed."""


ActionConfirmation = Callable[[dict[str, Any], dict[str, Any]], bool]


class PositionManager:
    """Recommend and optionally submit assisted position-management actions."""

    DEFAULT_CONFIG = {
        "enabled": True,
        "mode": "assisted",
        "breakeven": {
            "enabled": True,
            "trigger_r": 1.0,
            "buffer_points": {
                "XAUUSD": 20,
                "US30": 50,
                "EURUSD": 5,
                "GBPUSD": 5,
            },
        },
        "partial_profit": {
            "enabled": True,
            "trigger_r": 2.0,
            "close_percent": 30,
        },
        "trailing": {
            "enabled": True,
            "mode": "structure",
            "assisted_only": True,
        },
        "pending_invalidation": {
            "cancel_on_news_lock": True,
            "cancel_on_risk_block": True,
            "cancel_on_killzone_expired": True,
            "cancel_on_confidence_below": "HOT",
            "cancel_on_mss_invalidated": True,
            "cancel_if_target_hit_before_entry": True,
        },
        "efde": {
            "enabled": True,
            "assisted_only": True,
            "human_approval_required": True,
        },
    }
    SENTINEL_MAGIC = 22001
    SENTINEL_COMMENT = "Project Sentinel"
    CONFIDENCE_RANK = {
        "COLD": 0,
        "WARM": 1,
        "HOT": 2,
        "EXECUTION_READY": 3,
    }

    def __init__(
        self,
        connector: MT5Connector | None = None,
        config_dir: str | Path | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.connector = connector or MT5Connector()
        self.config = self._load_config()
        self.symbol_registry = SymbolRegistry(config_dir=self.config_dir)

    def manage_all(
        self,
        *,
        context: dict[str, Any] | None = None,
        mode: str | None = None,
        confirmation_callback: ActionConfirmation | None = None,
    ) -> list[dict[str, Any]]:
        """Read MT5 positions/orders and return management results."""
        results = []
        for position in self.get_open_positions():
            if self.is_sentinel_order(position):
                results.append(
                    self.manage_position(
                        position,
                        context=context,
                        mode=mode,
                        confirmation_callback=confirmation_callback,
                    )
                )
        for order in self.get_pending_orders():
            if self.is_sentinel_order(order):
                results.append(
                    self.manage_pending_order(
                        order,
                        context=context,
                        mode=mode,
                        confirmation_callback=confirmation_callback,
                    )
                )
        return results

    def manage_position(
        self,
        position: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        mode: str | None = None,
        confirmation_callback: ActionConfirmation | None = None,
    ) -> dict[str, Any]:
        """Return recommended and optionally submitted actions for one open position."""
        context = context or {}
        mode = self.normalize_mode(mode)
        symbol = str(position.get("symbol", "")).upper().strip()
        current_r = self.calculate_current_r(
            position,
            current_price=context.get("current_price"),
            initial_stop_loss=context.get("initial_stop_loss"),
        )
        actions = self.recommend_position_actions(position, current_r=current_r, context=context)
        submitted_actions = self.submit_actions(
            subject=position,
            actions=actions,
            mode=mode,
            confirmation_callback=confirmation_callback,
        )
        return {
            "symbol": symbol,
            "ticket": int(position.get("ticket", 0) or 0),
            "position_status": "OPEN",
            "current_r": current_r,
            "actions": actions,
            "submitted_actions": submitted_actions,
            "explanation": self.build_explanation("OPEN", current_r, actions, submitted_actions, mode),
        }

    def manage_pending_order(
        self,
        order: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        mode: str | None = None,
        confirmation_callback: ActionConfirmation | None = None,
    ) -> dict[str, Any]:
        """Return recommended and optionally submitted actions for one pending order."""
        context = context or {}
        mode = self.normalize_mode(mode)
        actions = self.recommend_pending_actions(order, context=context)
        submitted_actions = self.submit_actions(
            subject=order,
            actions=actions,
            mode=mode,
            confirmation_callback=confirmation_callback,
        )
        return {
            "symbol": str(order.get("symbol", "")).upper().strip(),
            "ticket": int(order.get("ticket", order.get("order", 0)) or 0),
            "position_status": "PENDING",
            "current_r": 0.0,
            "actions": actions,
            "submitted_actions": submitted_actions,
            "explanation": self.build_explanation("PENDING", 0.0, actions, submitted_actions, mode),
        }

    def recommend_position_actions(
        self,
        position: dict[str, Any],
        *,
        current_r: float,
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return BE, partial, and trailing recommendations."""
        context = context or {}
        actions: list[dict[str, Any]] = []
        if not bool(self.config.get("enabled", True)):
            return actions

        efde_action = self.efde_early_exit_action(position, context=context)
        if efde_action:
            actions.append(efde_action)

        if self.should_move_to_breakeven(position, current_r, context=context):
            actions.append(
                {
                    "type": "MOVE_SL_TO_BE",
                    "recommended": True,
                    "requires_confirmation": True,
                    "reason": "Price reached 1R",
                    "request": self.build_move_sl_request(position, context=context),
                }
            )

        if self.should_take_partial(position, current_r):
            actions.append(
                {
                    "type": "PARTIAL_CLOSE",
                    "recommended": True,
                    "requires_confirmation": True,
                    "reason": "Price reached 2R",
                    "close_percent": int(self.config.get("partial_profit", {}).get("close_percent", 30)),
                    "request": self.build_partial_close_request(position),
                }
            )

        if self.should_trail_structure(position, current_r):
            actions.append(
                {
                    "type": "TRAIL_STRUCTURE",
                    "recommended": True,
                    "requires_confirmation": True,
                    "reason": "Trail remaining position behind structure",
                    "request": {},
                }
            )

        return actions

    def efde_early_exit_action(self, position: dict[str, Any], *, context: dict[str, Any]) -> dict[str, Any] | None:
        """Return EFDE early-exit recommendation without an MT5 request."""
        if not bool(self.config.get("efde", {}).get("enabled", True)):
            return None
        decision = ProductionPromotionEngine(config_dir=self.config_dir).evaluate_efde_position(position, context=context)
        if decision.get("recommendation") != "EARLY_EXIT_RECOMMENDED":
            return None
        return {
            "type": "EARLY_EXIT_RECOMMENDED",
            "recommended": True,
            "requires_confirmation": True,
            "reason": f"EFDE FPS {decision['fps']} reached threshold {decision['threshold']} in {decision['adverse_zone']}",
            "request": {},
            "efde": decision,
        }

    def recommend_pending_actions(
        self,
        order: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return pending-order cancellation recommendations."""
        context = context or {}
        reason = self.pending_invalidation_reason(order, context)
        if not reason:
            return []
        return [
            {
                "type": "CANCEL_PENDING_ORDER",
                "recommended": True,
                "requires_confirmation": True,
                "reason": reason,
                "request": self.build_cancel_pending_request(order),
            }
        ]

    def submit_actions(
        self,
        *,
        subject: dict[str, Any],
        actions: list[dict[str, Any]],
        mode: str,
        confirmation_callback: ActionConfirmation | None = None,
    ) -> list[dict[str, Any]]:
        """Submit confirmed assisted actions; advisor mode is always no-op."""
        submitted: list[dict[str, Any]] = []
        if mode == "advisor":
            return submitted
        if mode != "assisted":
            raise PositionManagerError("Only advisor and assisted modes are supported in V0.1.")

        for action in actions:
            if not action.get("recommended"):
                continue
            approved = self.request_confirmation(subject, action, confirmation_callback)
            if not approved:
                submitted.append(
                    {
                        "type": action.get("type"),
                        "submitted": False,
                        "result": "REJECTED_BY_USER",
                        "broker_message": "Manual confirmation rejected.",
                    }
                )
                continue
            request = action.get("request", {})
            if not request:
                submitted.append(
                    {
                        "type": action.get("type"),
                        "submitted": False,
                        "result": "NO_MT5_REQUEST",
                        "broker_message": "Action is recommendation-only in V0.1.",
                    }
                )
                continue
            submitted.append({"type": action.get("type"), **self.submit_mt5_request(request)})
        return submitted

    def calculate_current_r(
        self,
        position: dict[str, Any],
        *,
        current_price: float | None = None,
        initial_stop_loss: float | None = None,
    ) -> float:
        """Return current R multiple for an open position."""
        direction = self.position_direction(position)
        entry = float(position.get("price_open", position.get("entry_price", 0.0)) or 0.0)
        stop_loss = float(initial_stop_loss or position.get("initial_sl", position.get("sl", 0.0)) or 0.0)
        current = float(current_price or position.get("price_current", position.get("current_price", 0.0)) or 0.0)
        risk_distance = abs(entry - stop_loss)
        if direction not in {"BUY", "SELL"} or entry <= 0 or risk_distance <= 0 or current <= 0:
            return 0.0
        if direction == "BUY":
            return round((current - entry) / risk_distance, 2)
        return round((entry - current) / risk_distance, 2)

    def should_move_to_breakeven(
        self,
        position: dict[str, Any],
        current_r: float,
        *,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Return whether BE move should be recommended."""
        breakeven = self.config.get("breakeven", {})
        if not bool(breakeven.get("enabled", True)):
            return False
        if current_r < float(breakeven.get("trigger_r", 1.0)):
            return False
        target_sl = self.calculate_breakeven_price(position, context=context)
        current_sl = float(position.get("sl", 0.0) or 0.0)
        direction = self.position_direction(position)
        if direction == "BUY":
            return current_sl < target_sl
        if direction == "SELL":
            return current_sl > target_sl or current_sl == 0.0
        return False

    def should_take_partial(self, position: dict[str, Any], current_r: float) -> bool:
        """Return whether partial close should be recommended."""
        partial = self.config.get("partial_profit", {})
        if not bool(partial.get("enabled", True)):
            return False
        if bool(position.get("sentinel_partial_closed", position.get("partial_closed", False))):
            return False
        return current_r >= float(partial.get("trigger_r", 2.0))

    def should_trail_structure(self, position: dict[str, Any], current_r: float) -> bool:
        """Return whether structure trailing should be recommended."""
        trailing = self.config.get("trailing", {})
        if not bool(trailing.get("enabled", True)):
            return False
        return current_r >= float(self.config.get("partial_profit", {}).get("trigger_r", 2.0))

    def pending_invalidation_reason(self, order: dict[str, Any], context: dict[str, Any]) -> str:
        """Return pending cancellation reason, if invalidated."""
        rules = self.config.get("pending_invalidation", {})
        if rules.get("cancel_on_news_lock", True) and (context.get("news_lock_active") or context.get("news", {}).get("lock_active")):
            return "News lock became active"
        if rules.get("cancel_on_risk_block", True) and (
            context.get("risk_blocked")
            or not context.get("risk", {}).get("permission", {"trade_allowed": True}).get("trade_allowed", True)
        ):
            return "Risk Governor blocked trading"
        if rules.get("cancel_on_killzone_expired", True) and context.get("killzone_expired"):
            return "Killzone expired"
        confidence_floor = str(rules.get("cancel_on_confidence_below", "HOT")).upper()
        confidence_band = str(context.get("confidence_band", context.get("confidence", {}).get("confidence_band", "HOT"))).upper()
        if self.CONFIDENCE_RANK.get(confidence_band, 0) < self.CONFIDENCE_RANK.get(confidence_floor, 2):
            return f"Setup confidence dropped below {confidence_floor}"
        if rules.get("cancel_on_mss_invalidated", True) and context.get("mss_invalidated"):
            return "MSS invalidated"
        if rules.get("cancel_if_target_hit_before_entry", True) and self.target_hit_before_entry(order, context):
            return "Price reached target before entry"
        return ""

    def target_hit_before_entry(self, order: dict[str, Any], context: dict[str, Any]) -> bool:
        """Return whether target was reached before pending entry filled."""
        if context.get("target_hit_before_entry"):
            return True
        current_price = float(context.get("current_price", order.get("price_current", 0.0)) or 0.0)
        target = float(context.get("target_price", order.get("tp", 0.0)) or 0.0)
        direction = self.pending_order_direction(order)
        if not current_price or not target:
            return False
        if direction == "BUY":
            return current_price >= target
        if direction == "SELL":
            return current_price <= target
        return False

    def calculate_breakeven_price(
        self,
        position: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> float:
        """Return BE price adjusted by configured spread/commission buffer."""
        context = context or {}
        symbol = str(position.get("symbol", "")).upper().strip()
        entry = float(position.get("price_open", position.get("entry_price", 0.0)) or 0.0)
        point = float(context.get("point", position.get("point", 0.01)) or 0.01)
        buffer_points = int(self.config.get("breakeven", {}).get("buffer_points", {}).get(symbol, 0))
        buffer_price = buffer_points * point
        if self.position_direction(position) == "BUY":
            return round(entry + buffer_price, 5)
        return round(entry - buffer_price, 5)

    def build_move_sl_request(self, position: dict[str, Any], *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build MT5 SL modification request."""
        return {
            "action": self.mt5_constant("TRADE_ACTION_SLTP"),
            "position": int(position.get("ticket", 0) or 0),
            "symbol": str(position.get("symbol", "")).upper().strip(),
            "sl": self.calculate_breakeven_price(position, context=context),
            "tp": float(position.get("tp", 0.0) or 0.0),
            "magic": self.SENTINEL_MAGIC,
            "comment": "Project Sentinel BE move",
        }

    def build_partial_close_request(self, position: dict[str, Any]) -> dict[str, Any]:
        """Build MT5 partial close request."""
        close_percent = float(self.config.get("partial_profit", {}).get("close_percent", 30))
        volume = round(float(position.get("volume", 0.0) or 0.0) * (close_percent / 100), 2)
        return {
            "action": self.mt5_constant("TRADE_ACTION_DEAL"),
            "position": int(position.get("ticket", 0) or 0),
            "symbol": str(position.get("symbol", "")).upper().strip(),
            "volume": volume,
            "type": self.mt5_constant("ORDER_TYPE_SELL" if self.position_direction(position) == "BUY" else "ORDER_TYPE_BUY"),
            "price": float(position.get("price_current", position.get("current_price", 0.0)) or 0.0),
            "magic": self.SENTINEL_MAGIC,
            "comment": "Project Sentinel partial close",
        }

    def build_cancel_pending_request(self, order: dict[str, Any]) -> dict[str, Any]:
        """Build MT5 pending order cancellation request."""
        return {
            "action": self.mt5_constant("TRADE_ACTION_REMOVE"),
            "order": int(order.get("ticket", order.get("order", 0)) or 0),
            "symbol": str(order.get("symbol", "")).upper().strip(),
            "magic": self.SENTINEL_MAGIC,
            "comment": "Project Sentinel pending cancel",
        }

    def submit_mt5_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Submit a management request through MT5."""
        boundary = self.validate_management_request_boundary(request)
        if boundary:
            return {
                "submitted": False,
                "result": "BLOCKED_OBSERVER_SYMBOL",
                "ticket": None,
                "broker_message": boundary,
                "raw_result": {},
            }
        mt5_module = getattr(self.connector, "mt5", None)
        if mt5_module is None or not hasattr(mt5_module, "order_send"):
            raise PositionManagerError("MT5 order_send is unavailable.")
        response = mt5_module.order_send(request)
        response_dict = self.to_dict(response)
        retcode = response_dict.get("retcode")
        success_code = self.mt5_constant("TRADE_RETCODE_DONE")
        submitted = retcode in {success_code, "TRADE_RETCODE_DONE", 10009}
        return {
            "submitted": submitted,
            "result": "SUCCESS" if submitted else "REJECTED",
            "ticket": response_dict.get("order") or response_dict.get("ticket"),
            "broker_message": response_dict.get("comment", "") or response_dict.get("message", ""),
            "raw_result": response_dict,
        }

    def validate_management_request_boundary(self, request: dict[str, Any]) -> str:
        """Return hard block reason before any MT5 management request."""
        symbol = str(request.get("symbol", "")).upper().strip()
        if not symbol or not self.symbol_registry.execution_allowed(symbol):
            return f"BLOCKED_OBSERVER_SYMBOL: {symbol or 'UNKNOWN'} is not allowed for position management"
        if int(request.get("magic", 0) or 0) != self.SENTINEL_MAGIC:
            return "BLOCKED_NON_SENTINEL_ORDER: management request missing Sentinel magic"
        return ""

    def request_confirmation(
        self,
        subject: dict[str, Any],
        action: dict[str, Any],
        confirmation_callback: ActionConfirmation | None,
    ) -> bool:
        """Return manual confirmation for an action."""
        if confirmation_callback:
            return bool(confirmation_callback(subject, action))
        answer = input(self.format_confirmation_prompt(subject, action))
        return answer.strip().upper() == "Y"

    @staticmethod
    def format_confirmation_prompt(subject: dict[str, Any], action: dict[str, Any]) -> str:
        """Return assisted-mode confirmation prompt."""
        current_r = subject.get("current_r", "")
        return (
            "\nPROJECT SENTINEL POSITION MANAGER\n\n"
            f"Ticket: {subject.get('ticket', subject.get('order'))}\n"
            f"Symbol: {subject.get('symbol')}\n"
            f"Current R: {current_r}\n\n"
            "Recommended Action:\n"
            f"{action.get('type')} - {action.get('reason')}\n\n"
            "Approve? [Y/N] "
        )

    def get_open_positions(self) -> list[dict[str, Any]]:
        """Return open MT5 positions as dictionaries."""
        mt5_module = getattr(self.connector, "mt5", None)
        if mt5_module is None or not hasattr(mt5_module, "positions_get"):
            return []
        return [self.to_dict(position) for position in (mt5_module.positions_get() or [])]

    def get_pending_orders(self) -> list[dict[str, Any]]:
        """Return pending MT5 orders as dictionaries."""
        mt5_module = getattr(self.connector, "mt5", None)
        if mt5_module is None or not hasattr(mt5_module, "orders_get"):
            return []
        return [self.to_dict(order) for order in (mt5_module.orders_get() or [])]

    @classmethod
    def is_sentinel_order(cls, value: dict[str, Any]) -> bool:
        """Return whether a position/order belongs to Sentinel."""
        magic = int(value.get("magic", 0) or 0)
        comment = str(value.get("comment", ""))
        return magic == cls.SENTINEL_MAGIC or cls.SENTINEL_COMMENT.lower() in comment.lower()

    @staticmethod
    def position_direction(position: dict[str, Any]) -> str:
        """Return BUY or SELL for an MT5 position."""
        value = position.get("type", position.get("position_type", ""))
        if isinstance(value, str):
            text = value.upper()
            if "BUY" in text:
                return "BUY"
            if "SELL" in text:
                return "SELL"
        if int(value or 0) == 0:
            return "BUY"
        if int(value or 0) == 1:
            return "SELL"
        return ""

    @staticmethod
    def pending_order_direction(order: dict[str, Any]) -> str:
        """Return BUY or SELL for a pending order."""
        value = order.get("type", "")
        if isinstance(value, str):
            text = value.upper()
            if "BUY" in text:
                return "BUY"
            if "SELL" in text:
                return "SELL"
        numeric = int(value or 0)
        if numeric in {0, 2, 4}:
            return "BUY"
        if numeric in {1, 3, 5}:
            return "SELL"
        return ""

    def normalize_mode(self, mode: str | None = None) -> str:
        """Return advisor or assisted mode."""
        normalized = str(mode or self.config.get("mode", "assisted")).lower().strip()
        if normalized not in {"advisor", "assisted"}:
            raise PositionManagerError("Position Manager V0.1 supports only advisor and assisted modes.")
        return normalized

    def mt5_constant(self, name: str) -> Any:
        """Return MT5 constant when present, otherwise readable fallback."""
        mt5_module = getattr(self.connector, "mt5", None)
        return getattr(mt5_module, name, name)

    @staticmethod
    def to_dict(value: Any) -> dict[str, Any]:
        """Convert MT5 namedtuple/object/dict responses to dict."""
        if value is None:
            return {}
        if hasattr(value, "_asdict"):
            return dict(value._asdict())
        if isinstance(value, dict):
            return dict(value)
        if hasattr(value, "__dict__"):
            return dict(value.__dict__)
        return {"value": value}

    @staticmethod
    def build_explanation(
        status: str,
        current_r: float,
        actions: list[dict[str, Any]],
        submitted_actions: list[dict[str, Any]],
        mode: str,
    ) -> list[str]:
        """Return human-readable management explanation."""
        if not actions:
            return [f"{status} item has no recommended management action.", "No modification was submitted."]
        explanation = [f"{status} item evaluated at {current_r}R.", f"{len(actions)} action(s) recommended."]
        if mode == "advisor":
            explanation.append("Advisor mode only: no position modification was submitted.")
        elif submitted_actions:
            explanation.append(f"{len(submitted_actions)} assisted action result(s) recorded.")
        else:
            explanation.append("Assisted mode requires manual confirmation before modification.")
        return explanation

    def _load_config(self) -> dict[str, Any]:
        config = self._load_yaml_file(self.config_dir / "position_manager.yaml")
        return self._deep_merge(self.DEFAULT_CONFIG, config)

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist; using position manager defaults.", path)
            return {}
        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise PositionManagerError(f"Failed to load config {path}: {exc}") from exc

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
