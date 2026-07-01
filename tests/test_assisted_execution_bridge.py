from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.execution_engine.assisted_execution_bridge import AssistedExecutionBridge, LockedTradeTicket
from dashboard.utils.data_loader import assisted_execution_gate_dataframe, load_assisted_execution_summary
from scripts.run_assisted_execution_status import build_assisted_execution_report


class MockMT5:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TIME_GTC = 0
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def order_send(self, request: dict) -> dict:
        self.requests.append(request)
        return {"retcode": self.TRADE_RETCODE_DONE, "order": 777, "comment": "demo done"}


class MockConnector:
    def __init__(self, *, account_mode: str = "demo") -> None:
        self.mt5 = MockMT5()
        self.account_mode = account_mode

    def get_account_info(self) -> dict:
        server = "MetaQuotes-Demo" if self.account_mode == "demo" else "Live-Server"
        return {"login": 123456, "server": server, "account_mode": self.account_mode, "balance": 10000.0, "equity": 10000.0}


ENABLED_CONFIG = {
    "enabled": True,
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


def make_bridge(*, account_mode: str = "demo", config: dict | None = None) -> AssistedExecutionBridge:
    return AssistedExecutionBridge(connector=MockConnector(account_mode=account_mode), config=config or ENABLED_CONFIG)


def make_ticket(bridge: AssistedExecutionBridge, **overrides) -> LockedTradeTicket:
    payload = {
        "ticket_id": "AEX-TEST",
        "symbol": "XAUUSD",
        "side": "BUY",
        "entry_type": "LIMIT",
        "entry_price": 4010.0,
        "stop_loss": 3998.0,
        "take_profit": 4046.0,
        "risk_percent": 0.10,
        "lot_size": 0.02,
        "grade": "A+",
        "confidence": 96,
        "strategy": "trend_following",
        "killzone": "new_york_open",
        "rationale": "test ticket",
        "status": "APPROVED",
    }
    payload.update(overrides)
    return bridge.create_ticket(**payload)


def pass_context(**overrides) -> dict:
    context = {
        "spread_points": 20,
        "slippage_points": 0,
        "expected_lot_size": 0.02,
        "kill_switch_active": False,
    }
    context.update(overrides)
    return context


def test_project_config_activates_dry_run_only():
    bridge = AssistedExecutionBridge()

    assert bridge.config["enabled"] is True
    assert bridge.config["mode"] == "DEMO_ONLY"
    assert bridge.config["submit_orders"] is False


def test_ticket_lock_is_immutable():
    bridge = make_bridge()
    ticket = make_ticket(bridge)

    with pytest.raises(FrozenInstanceError):
        ticket.entry_price = 4011.0  # type: ignore[misc]


def test_live_account_blocked_before_order_send():
    bridge = make_bridge(account_mode="live")
    ticket = make_ticket(bridge)

    result = bridge.submit_demo_order(ticket, context=pass_context(), human_approved=True)

    assert result["order_submitted"] is False
    assert result["order_send_called"] is False
    assert "MT5 account is not demo" in result["reason"]
    assert bridge.connector.mt5.requests == []


def test_observer_symbol_blocked():
    bridge = make_bridge()
    ticket = make_ticket(bridge, symbol="NAS100")

    result = bridge.submit_demo_order(ticket, context=pass_context(), human_approved=True)

    assert result["order_submitted"] is False
    assert "symbol not allowed" in result["reason"]
    assert bridge.connector.mt5.requests == []


def test_non_a_plus_grade_blocked():
    bridge = make_bridge()
    ticket = make_ticket(bridge, grade="A")

    result = bridge.submit_demo_order(ticket, context=pass_context(), human_approved=True)

    assert result["order_submitted"] is False
    assert "only A+ tickets are allowed" in result["reason"]


def test_expired_ticket_blocked():
    bridge = make_bridge()
    now = datetime.now(UTC)
    ticket = make_ticket(
        bridge,
        created_at=(now - timedelta(seconds=20)).isoformat(),
        expires_at=(now - timedelta(seconds=1)).isoformat(),
    )

    result = bridge.submit_demo_order(ticket, context=pass_context(now=now.isoformat()), human_approved=True)

    assert result["order_submitted"] is False
    assert "ticket expired" in result["reason"]


def test_stale_ticket_blocked():
    bridge = make_bridge(config={**ENABLED_CONFIG, "require_fresh_ticket_seconds": 120})
    now = datetime.now(UTC)
    ticket = make_ticket(
        bridge,
        created_at=(now - timedelta(seconds=300)).isoformat(),
        expires_at=(now + timedelta(seconds=300)).isoformat(),
    )

    result = bridge.submit_demo_order(ticket, context=pass_context(now=now.isoformat()), human_approved=True)

    assert result["order_submitted"] is False
    assert "ticket is stale" in result["reason"]


def test_risk_above_max_blocked():
    bridge = make_bridge()
    ticket = make_ticket(bridge, risk_percent=0.50)

    result = bridge.submit_demo_order(ticket, context=pass_context(), human_approved=True)

    assert result["order_submitted"] is False
    assert "risk exceeds" in result["reason"]


def test_duplicate_order_blocked():
    bridge = make_bridge()
    ticket = make_ticket(bridge)

    result = bridge.submit_demo_order(ticket, context=pass_context(duplicate_order=True), human_approved=True)

    assert result["order_submitted"] is False
    assert "duplicate order" in result["reason"]


def test_invalid_sl_tp_blocked():
    bridge = make_bridge()
    ticket = make_ticket(bridge, stop_loss=4020.0, take_profit=4046.0)

    result = bridge.submit_demo_order(ticket, context=pass_context(), human_approved=True)

    assert result["order_submitted"] is False
    assert "invalid stop_loss" in result["reason"]


def test_dry_run_does_not_call_order_send():
    bridge = make_bridge()
    ticket = make_ticket(bridge)

    dry_run = bridge.dry_run(ticket, context=pass_context())

    assert dry_run["order_send_called"] is False
    assert dry_run["order_payload"]["symbol"] == "XAUUSD"
    assert bridge.connector.mt5.requests == []


def test_demo_submit_remains_dry_run_blocked_after_approval_and_all_gates_pass():
    bridge = make_bridge()
    ticket = make_ticket(bridge, status="AWAITING_APPROVAL")
    approved_ticket = bridge.transition_ticket(ticket, "APPROVED")

    rejected = bridge.submit_demo_order(ticket, context=pass_context(), human_approved=False)
    accepted = bridge.submit_demo_order(approved_ticket, context=pass_context(), human_approved=False)

    assert rejected["order_submitted"] is False
    assert rejected["order_send_called"] is False
    assert accepted["order_submitted"] is False
    assert accepted["order_send_called"] is False
    assert accepted["status"] == "BLOCKED"
    assert "dry-run only" in accepted["reason"]
    assert bridge.connector.mt5.requests == []


def test_status_report_preserves_production_baseline_and_dashboard_loader(tmp_path: Path):
    report = build_assisted_execution_report()
    path = tmp_path / "data" / "reports" / "assisted_execution_status.json"
    path.parent.mkdir(parents=True)
    path.write_text(__import__("json").dumps(report), encoding="utf-8")

    summary = load_assisted_execution_summary(tmp_path, {"assisted_execution_report_path": "data/reports/assisted_execution_status.json"})
    frame = assisted_execution_gate_dataframe(summary)

    assert report["production_baseline_preserved"] is True
    assert report["safety"]["broker_orders"] is False
    assert report["safety"]["dry_run_only"] is True
    assert report["config"]["enabled"] is True
    assert report["config"]["submit_orders"] is False
    assert summary["available"] is True
    assert "symbol_lock" in set(frame["check"])
