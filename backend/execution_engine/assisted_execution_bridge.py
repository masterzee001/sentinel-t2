"""Demo-only assisted execution bridge.

The bridge can be enabled for dry-run workflows while keeping actual
``order_send`` blocked unless ``submit_orders`` is explicitly enabled in a
demo-only test path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import yaml

from backend.market_data.mt5_connector import MT5Connector
from backend.symbols.symbol_registry import SymbolRegistry


VALID_TICKET_STATUSES = {
    "CREATED",
    "AWAITING_APPROVAL",
    "APPROVED",
    "EXPIRED",
    "REJECTED",
    "SUBMITTED_DEMO",
    "BLOCKED",
}

DEFAULT_CONFIG = {
    "enabled": False,
    "mode": "DEMO_ONLY",
    "submit_orders": False,
    "human_approval_required": True,
    "allowed_account_mode": "demo",
    "allowed_symbols": ["XAUUSD", "US30"],
    "allowed_grades": ["A+"],
    "max_risk_percent": 0.25,
    "default_risk_percent": 0.10,
    "max_slippage_points": {"XAUUSD": 50, "US30": 100},
    "max_spread_points": {"XAUUSD": 60, "US30": 120},
    "duplicate_order_protection": True,
    "require_fresh_ticket_seconds": 120,
    "broker_submission_global_override": False,
}


@dataclass(frozen=True)
class LockedTradeTicket:
    """Immutable assisted-execution ticket."""

    ticket_id: str
    created_at: str
    expires_at: str
    symbol: str
    side: str
    entry_type: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_percent: float
    lot_size: float
    grade: str
    confidence: float
    strategy: str
    killzone: str
    rationale: str
    status: str = "CREATED"

    def __post_init__(self) -> None:
        status = str(self.status).upper()
        if status not in VALID_TICKET_STATUSES:
            raise ValueError(f"Invalid ticket status: {self.status}")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "symbol", str(self.symbol).upper().strip())
        object.__setattr__(self, "side", str(self.side).upper().strip())
        object.__setattr__(self, "entry_type", str(self.entry_type).upper().strip())
        object.__setattr__(self, "grade", str(self.grade).upper().strip())

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-ready ticket payload."""
        return asdict(self)


DuplicateProvider = Callable[[LockedTradeTicket, dict[str, Any]], bool]


class AssistedExecutionBridge:
    """Validate, dry-run, and submit locked demo-only assisted tickets."""

    def __init__(
        self,
        *,
        connector: MT5Connector | Any | None = None,
        config_dir: str | Path | None = None,
        config: dict[str, Any] | None = None,
        duplicate_provider: DuplicateProvider | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.connector = connector
        self.config = deep_merge(DEFAULT_CONFIG, config if config is not None else self.load_config())
        self.symbol_registry = SymbolRegistry(config_dir=self.config_dir)
        self.duplicate_provider = duplicate_provider

    def load_config(self) -> dict[str, Any]:
        """Load config/assisted_execution.yaml."""
        path = self.config_dir / "assisted_execution.yaml"
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def create_ticket(self, **payload: Any) -> LockedTradeTicket:
        """Create a locked trade ticket from validated plan fields."""
        now = parse_datetime(payload.get("created_at")) if payload.get("created_at") else datetime.now(UTC)
        expiry_seconds = int(self.config.get("require_fresh_ticket_seconds", 120) or 120)
        expires_at = parse_datetime(payload.get("expires_at")) if payload.get("expires_at") else now + timedelta(seconds=expiry_seconds)
        return LockedTradeTicket(
            ticket_id=str(payload.get("ticket_id") or f"AEX-{uuid4().hex[:8].upper()}"),
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            symbol=str(payload["symbol"]),
            side=str(payload["side"]),
            entry_type=str(payload.get("entry_type", "LIMIT")),
            entry_price=float(payload["entry_price"]),
            stop_loss=float(payload["stop_loss"]),
            take_profit=float(payload["take_profit"]),
            risk_percent=float(payload.get("risk_percent", self.config.get("default_risk_percent", 0.10))),
            lot_size=float(payload["lot_size"]),
            grade=str(payload.get("grade", "A+")),
            confidence=float(payload.get("confidence", 0.0)),
            strategy=str(payload.get("strategy", "")),
            killzone=str(payload.get("killzone", "")),
            rationale=str(payload.get("rationale", "")),
            status=str(payload.get("status", "AWAITING_APPROVAL")),
        )

    def transition_ticket(self, ticket: LockedTradeTicket, status: str) -> LockedTradeTicket:
        """Return a new immutable ticket record with updated status."""
        return replace(ticket, status=str(status).upper())

    def dry_run(self, ticket: LockedTradeTicket, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build demo order payload and validation result without order_send."""
        context = context or {}
        validation = self.final_safety_gate(ticket, context=context, human_approved=is_ticket_approved(ticket), for_submit=False)
        order_payload = self.build_order_payload(ticket, context=context)
        return {
            "ticket_id": ticket.ticket_id,
            "mode": "DEMO_ONLY",
            "dry_run": True,
            "order_send_called": False,
            "symbol": ticket.symbol,
            "order_type": order_payload["order_type"],
            "lot_size": ticket.lot_size,
            "entry": ticket.entry_price,
            "sl": ticket.stop_loss,
            "tp": ticket.take_profit,
            "risk_amount": order_payload["risk_amount"],
            "expected_max_loss": order_payload["expected_max_loss"],
            "validation": validation,
            "order_payload": order_payload["request"],
        }

    def submit_demo_order(
        self,
        ticket: LockedTradeTicket,
        *,
        context: dict[str, Any] | None = None,
        human_approved: bool = False,
    ) -> dict[str, Any]:
        """Submit one MT5 demo order only after every final gate passes."""
        context = context or {}
        validation = self.final_safety_gate(ticket, context=context, human_approved=human_approved or is_ticket_approved(ticket), for_submit=True)
        if not validation["passed"]:
            return {
                "ticket_id": ticket.ticket_id,
                "status": "BLOCKED",
                "order_submitted": False,
                "order_send_called": False,
                "validation": validation,
                "reason": "; ".join(validation["reasons"]),
            }
        if not bool(self.config.get("submit_orders", False)):
            return {
                "ticket_id": ticket.ticket_id,
                "status": "BLOCKED",
                "order_submitted": False,
                "order_send_called": False,
                "validation": {
                    **validation,
                    "passed": False,
                    "status": "BLOCKED",
                    "reasons": [*validation["reasons"], "submit_orders is false: dry-run only"],
                },
                "reason": "submit_orders is false: dry-run only",
            }
        order_payload = self.build_order_payload(ticket, context=context)
        mt5_module = getattr(self.connector, "mt5", None)
        if mt5_module is None or not hasattr(mt5_module, "order_send"):
            return {
                "ticket_id": ticket.ticket_id,
                "status": "BLOCKED",
                "order_submitted": False,
                "order_send_called": False,
                "validation": {
                    **validation,
                    "passed": False,
                    "reasons": [*validation["reasons"], "MT5 order_send unavailable"],
                    "status": "BLOCKED",
                },
                "reason": "MT5 order_send unavailable",
            }
        response = mt5_module.order_send(order_payload["request"])
        response_dict = to_dict(response)
        retcode = response_dict.get("retcode")
        success_code = mt5_constant(self.connector, "TRADE_RETCODE_DONE")
        submitted = retcode in {success_code, "TRADE_RETCODE_DONE", 10009}
        return {
            "ticket_id": ticket.ticket_id,
            "status": "SUBMITTED_DEMO" if submitted else "BLOCKED",
            "order_submitted": submitted,
            "order_send_called": True,
            "validation": validation,
            "order_payload": order_payload["request"],
            "broker_response": response_dict,
            "ticket": response_dict.get("order") or response_dict.get("ticket"),
        }

    def final_safety_gate(
        self,
        ticket: LockedTradeTicket,
        *,
        context: dict[str, Any] | None = None,
        human_approved: bool = False,
        for_submit: bool = True,
    ) -> dict[str, Any]:
        """Return final pre-submission gate result."""
        context = context or {}
        now = parse_datetime(context.get("now")) if context.get("now") else datetime.now(UTC)
        account = self.account_info(context)
        spread_points = float(context.get("spread_points", 0.0) or 0.0)
        slippage_points = float(context.get("slippage_points", 0.0) or 0.0)
        max_spread = symbol_limit(self.config.get("max_spread_points", {}), ticket.symbol)
        max_slippage = symbol_limit(self.config.get("max_slippage_points", {}), ticket.symbol)
        checks = {
            "enabled": bool(self.config.get("enabled", False)),
            "demo_only_mode": str(self.config.get("mode", "")).upper() == "DEMO_ONLY",
            "demo_account": account_mode(account) == str(self.config.get("allowed_account_mode", "demo")).lower(),
            "human_approval": bool(human_approved) if self.config.get("human_approval_required", True) else True,
            "ticket_not_expired": now < parse_datetime(ticket.expires_at),
            "symbol_lock": ticket.symbol in normalized_set(self.config.get("allowed_symbols", [])) and self.symbol_registry.execution_allowed(ticket.symbol),
            "grade_lock": ticket.grade in normalized_set(self.config.get("allowed_grades", [])),
            "risk_lock": ticket.risk_percent <= float(self.config.get("max_risk_percent", 0.25) or 0.25),
            "kill_switch": not bool(context.get("kill_switch_active", False)),
            "spread_lock": spread_points <= max_spread,
            "slippage_lock": slippage_points <= max_slippage,
            "duplicate_lock": not self.duplicate_order_exists(ticket, context),
            "ticket_freshness": (now - parse_datetime(ticket.created_at)).total_seconds() <= int(self.config.get("require_fresh_ticket_seconds", 120) or 120),
            "stop_loss_take_profit": valid_sl_tp(ticket),
            "lot_size_risk_match": self.lot_size_matches(ticket, context, account),
            "broker_submission_global_override": not bool(self.config.get("broker_submission_global_override", False)),
        }
        reasons = [reason for key, reason in rejection_reasons().items() if not checks.get(key, False)]
        if for_submit and not checks["enabled"]:
            status = "BLOCKED"
        elif reasons:
            status = "BLOCKED"
        else:
            status = "PASS"
        return {
            "status": status,
            "passed": not reasons,
            "reasons": reasons,
            "checks": checks,
            "account_mode": account_mode(account).upper() if account else "UNKNOWN",
            "spread_points": spread_points,
            "max_spread_points": max_spread,
            "slippage_points": slippage_points,
            "max_slippage_points": max_slippage,
        }

    def build_order_payload(self, ticket: LockedTradeTicket, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build exact MT5 demo order request from the locked ticket."""
        context = context or {}
        account = self.account_info(context)
        balance = float(context.get("balance") or account.get("balance", 0.0) if account else context.get("balance", 0.0) or 0.0)
        risk_amount = round(balance * ticket.risk_percent / 100.0, 2)
        order_type = order_type_for(ticket)
        request = {
            "action": mt5_constant(self.connector, "TRADE_ACTION_DEAL") if ticket.entry_type == "MARKET" else mt5_constant(self.connector, "TRADE_ACTION_PENDING"),
            "symbol": ticket.symbol,
            "volume": ticket.lot_size,
            "type": mt5_constant(self.connector, f"ORDER_TYPE_{order_type}"),
            "price": ticket.entry_price,
            "sl": ticket.stop_loss,
            "tp": ticket.take_profit,
            "deviation": int(symbol_limit(self.config.get("max_slippage_points", {}), ticket.symbol)),
            "magic": 22014,
            "comment": f"Sentinel demo bridge {ticket.ticket_id}",
            "type_time": mt5_constant(self.connector, "ORDER_TIME_GTC"),
            "type_filling": mt5_constant(self.connector, "ORDER_FILLING_RETURN"),
        }
        return {
            "order_type": order_type,
            "risk_amount": risk_amount,
            "expected_max_loss": risk_amount,
            "request": request,
        }

    def duplicate_order_exists(self, ticket: LockedTradeTicket, context: dict[str, Any]) -> bool:
        """Return whether duplicate protection detects an existing order."""
        if not bool(self.config.get("duplicate_order_protection", True)):
            return False
        if bool(context.get("duplicate_order", False)):
            return True
        if self.duplicate_provider and self.duplicate_provider(ticket, context):
            return True
        for item in context.get("open_orders", []) or []:
            if str(item.get("ticket_id", "")).upper() == ticket.ticket_id.upper():
                return True
            same_symbol = str(item.get("symbol", "")).upper() == ticket.symbol
            same_side = str(item.get("side", "")).upper() == ticket.side
            same_entry = abs(float(item.get("entry_price", item.get("price", 0.0)) or 0.0) - ticket.entry_price) < 0.0001
            if same_symbol and same_side and same_entry:
                return True
        return False

    def lot_size_matches(self, ticket: LockedTradeTicket, context: dict[str, Any], account: dict[str, Any]) -> bool:
        """Return whether ticket lot size matches supplied risk calculation."""
        expected = context.get("expected_lot_size")
        if expected is None:
            expected = expected_lot_size(ticket, context, account)
        if expected is None:
            return not bool(context.get("require_lot_size_match", False))
        tolerance = float(context.get("lot_size_tolerance", 0.001) or 0.001)
        return abs(float(expected) - ticket.lot_size) <= tolerance

    def account_info(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return account info from context or connector without starting services."""
        account = context.get("account")
        if isinstance(account, dict):
            return account
        if self.connector is None or not hasattr(self.connector, "get_account_info"):
            return {}
        try:
            return dict(self.connector.get_account_info())
        except Exception:
            return {}

    def status_report(self, *, ticket: LockedTradeTicket | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return dashboard/Telegram status report without submitting orders."""
        context = context or {}
        ticket = ticket or self.sample_ticket()
        dry_run = self.dry_run(ticket, context=context)
        validation = dry_run["validation"]
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": "DEMO_ONLY",
            "config": self.config,
            "assisted_execution": "ENABLED" if self.config.get("enabled", False) else "DISABLED",
            "account_mode": validation.get("account_mode", "UNKNOWN"),
            "current_ticket": ticket.to_dict(),
            "dry_run": dry_run,
            "final_safety_status": validation,
            "safety": {
                "broker_orders": bool(self.config.get("submit_orders", False)),
                "autonomous_execution": False,
                "live_funded_execution": False,
                "human_approval_required": bool(self.config.get("human_approval_required", True)),
                "demo_only": str(self.config.get("mode", "")).upper() == "DEMO_ONLY",
                "dry_run_only": not bool(self.config.get("submit_orders", False)),
            },
            "production_baseline_preserved": True,
            "decision": "PASS"
            if str(self.config.get("mode", "")).upper() == "DEMO_ONLY" and not bool(self.config.get("submit_orders", False))
            else "REVIEW",
        }

    def sample_ticket(self) -> LockedTradeTicket:
        """Return a representative locked ticket for reports."""
        return self.create_ticket(
            ticket_id="AEX-SAMPLE",
            symbol="XAUUSD",
            side="BUY",
            entry_type="LIMIT",
            entry_price=4010.0,
            stop_loss=3998.0,
            take_profit=4046.0,
            risk_percent=float(self.config.get("default_risk_percent", 0.10) or 0.10),
            lot_size=0.02,
            grade="A+",
            confidence=96,
            strategy="trend_following",
            killzone="new_york_open",
            rationale="Sample locked ticket for disabled demo bridge status report.",
            status="AWAITING_APPROVAL",
        )


def order_type_for(ticket: LockedTradeTicket) -> str:
    """Return MT5 order type label."""
    suffix = "" if ticket.entry_type == "MARKET" else "_LIMIT"
    if ticket.side not in {"BUY", "SELL"}:
        raise ValueError("Ticket side must be BUY or SELL")
    return f"{ticket.side}{suffix}"


def valid_sl_tp(ticket: LockedTradeTicket) -> bool:
    """Return whether SL/TP are on the correct side of entry."""
    if ticket.entry_price <= 0 or ticket.stop_loss <= 0 or ticket.take_profit <= 0:
        return False
    if ticket.side == "BUY":
        return ticket.stop_loss < ticket.entry_price < ticket.take_profit
    if ticket.side == "SELL":
        return ticket.take_profit < ticket.entry_price < ticket.stop_loss
    return False


def expected_lot_size(ticket: LockedTradeTicket, context: dict[str, Any], account: dict[str, Any]) -> float | None:
    """Calculate expected lot size when value-per-point context is available."""
    balance = float(context.get("balance") or account.get("balance", 0.0) if account else context.get("balance", 0.0) or 0.0)
    risk_amount = balance * ticket.risk_percent / 100.0
    risk_per_lot = context.get("risk_per_lot")
    if risk_per_lot is None:
        point_value = context.get("value_per_lot_per_point")
        if point_value is None:
            return None
        risk_per_lot = abs(ticket.entry_price - ticket.stop_loss) * float(point_value)
    risk_per_lot = float(risk_per_lot or 0.0)
    if risk_per_lot <= 0:
        return None
    return round(risk_amount / risk_per_lot, 3)


def is_ticket_approved(ticket: LockedTradeTicket) -> bool:
    return ticket.status == "APPROVED"


def account_mode(account: dict[str, Any]) -> str:
    """Return normalized account mode using explicit field or server name."""
    explicit = str(account.get("account_mode", "") or account.get("mode", "")).lower().strip()
    if explicit:
        return explicit
    server = str(account.get("server", "")).lower()
    if "demo" in server:
        return "demo"
    if server:
        return "live"
    return "unknown"


def symbol_limit(limits: dict[str, Any], symbol: str) -> float:
    return float(limits.get(str(symbol).upper(), 0.0) or 0.0)


def normalized_set(values: list[Any]) -> set[str]:
    return {str(value).upper().strip() for value in values}


def parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def rejection_reasons() -> dict[str, str]:
    return {
        "enabled": "assisted_execution.enabled is false",
        "demo_only_mode": "mode is not DEMO_ONLY",
        "demo_account": "MT5 account is not demo",
        "human_approval": "human approval not received",
        "ticket_not_expired": "ticket expired",
        "symbol_lock": "symbol not allowed for assisted demo execution",
        "grade_lock": "only A+ tickets are allowed",
        "risk_lock": "risk exceeds max assisted demo risk",
        "kill_switch": "kill switch active",
        "spread_lock": "spread exceeds symbol limit",
        "slippage_lock": "slippage exceeds symbol limit",
        "duplicate_lock": "duplicate order protection blocked ticket",
        "ticket_freshness": "ticket is stale",
        "stop_loss_take_profit": "invalid stop_loss/take_profit geometry",
        "lot_size_risk_match": "lot size does not match risk calculation",
        "broker_submission_global_override": "broker submission global override must remain false",
    }


def mt5_constant(connector: Any, name: str) -> Any:
    mt5_module = getattr(connector, "mt5", None)
    return getattr(mt5_module, name, name)


def to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "_asdict"):
        return dict(value._asdict())
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {"retcode": None, "comment": str(value)}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
