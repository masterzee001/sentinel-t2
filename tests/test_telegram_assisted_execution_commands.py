from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.execution_engine.assisted_execution_bridge import AssistedExecutionBridge
from backend.telegram_bot.telegram_command_bot import TelegramCommandBot
from scripts.run_assisted_execution_status import build_assisted_execution_report


def write_config(config_dir: Path) -> None:
    config_dir.mkdir()
    (config_dir / "telegram_bot.yaml").write_text(
        """
enabled: true
allowed_commands:
  - /assisted_status
  - /assisted_ticket
  - /assisted_approve
  - /assisted_reject
  - /assisted_dry_run
  - /exec_approve
  - /execute_approve
advisor_mode_only: true
assisted_execution_report_path: data/reports/assisted_execution_status.json
symbols:
  XAUUSD: XAUUSD
""",
        encoding="utf-8",
    )


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
        return {"retcode": self.TRADE_RETCODE_DONE, "order": 991, "comment": "demo done"}


class MockConnector:
    def __init__(self, *, account_mode: str = "demo") -> None:
        self.mt5 = MockMT5()
        self.account_mode = account_mode

    def get_account_info(self) -> dict:
        server = "MetaQuotes-Demo" if self.account_mode == "demo" else "Live-Server"
        return {
            "login": 123456,
            "server": server,
            "account_mode": self.account_mode,
            "balance": 10000.0,
            "equity": 10000.0,
        }


BASE_ASSISTED_CONFIG = {
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


def write_report(root: Path, report: dict | None = None) -> dict:
    report = report or build_assisted_execution_report()
    path = root / "data" / "reports" / "assisted_execution_status.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    return report


def make_exec_report(
    *,
    ticket_overrides: dict | None = None,
    config_overrides: dict | None = None,
    account_mode: str = "demo",
    context_overrides: dict | None = None,
) -> dict:
    config = {**BASE_ASSISTED_CONFIG, **(config_overrides or {})}
    bridge = AssistedExecutionBridge(connector=MockConnector(account_mode=account_mode), config=config)
    now = datetime.now(UTC)
    payload = {
        "ticket_id": "AEX-TEST",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=120)).isoformat(),
        "symbol": "US30",
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
        "status": "AWAITING_APPROVAL",
    }
    payload.update(ticket_overrides or {})
    ticket = bridge.create_ticket(**payload)
    context = {
        "account": {"account_mode": account_mode, "server": "MetaQuotes-Demo" if account_mode == "demo" else "Live-Server", "balance": 10000.0},
        "spread_points": 20,
        "slippage_points": 0,
        "expected_lot_size": ticket.lot_size,
        "kill_switch_active": False,
        "now": now.isoformat(),
    }
    context.update(context_overrides or {})
    report = bridge.status_report(ticket=ticket, context=context)
    report["now"] = str(context["now"])
    return report


def make_bot(tmp_path: Path, monkeypatch, *, report: dict | None = None, connector: MockConnector | None = None) -> TelegramCommandBot:
    write_config(tmp_path / "config")
    write_report(tmp_path, report)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    assisted = TelegramCommandBot(config_dir=tmp_path / "config", project_root=tmp_path).load_assisted_execution_summary()
    return TelegramCommandBot(
        connector=connector,
        config_dir=tmp_path / "config",
        project_root=tmp_path,
        snapshot_provider=lambda: {"assisted_execution": assisted, "symbols": {}, "risk": {}, "news": {}},
    )


def test_assisted_telegram_status_ticket_and_dry_run(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    status = bot.handle_command("/assisted_status", "123")
    ticket = bot.handle_command("/assisted_ticket AEX-SAMPLE", "123")
    dry_run = bot.handle_command("/assisted_dry_run AEX-SAMPLE", "123")

    assert "ASSISTED EXECUTION STATUS" in status["response_text"]
    assert "DEMO_ONLY" in status["response_text"]
    assert "Dry Run Only: TRUE" in status["response_text"]
    assert "Submit Orders: FALSE" in status["response_text"]
    assert "AEX-SAMPLE" in ticket["response_text"]
    assert "Order Send: NOT CALLED" in dry_run["response_text"]


def test_assisted_approve_blocks_when_submit_orders_false(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    approve = bot.handle_command("/assisted_approve AEX-SAMPLE", "123")
    reject = bot.handle_command("/assisted_reject AEX-SAMPLE", "123")

    assert "ASSISTED APPROVE BLOCKED" in approve["response_text"]
    assert "No broker order submitted" in approve["response_text"]
    assert "submit_orders is false: dry-run only" in approve["response_text"]
    assert "ASSISTED REJECT" in reject["response_text"]
    assert "No broker order submitted" in reject["response_text"]


def test_exec_approve_unknown_ticket_blocked(tmp_path: Path, monkeypatch):
    connector = MockConnector()
    bot = make_bot(tmp_path, monkeypatch, report=make_exec_report(), connector=connector)

    result = bot.handle_command("/exec_approve NOPE", "123")

    assert "Final Decision: INVALID_TICKET" in result["response_text"]
    assert "ticket_id not found" in result["response_text"]
    assert connector.mt5.requests == []


def test_exec_approve_expired_ticket_blocked(tmp_path: Path, monkeypatch):
    connector = MockConnector()
    now = datetime.now(UTC)
    report = make_exec_report(
        ticket_overrides={
            "created_at": (now - timedelta(seconds=240)).isoformat(),
            "expires_at": (now - timedelta(seconds=1)).isoformat(),
        },
        context_overrides={"now": now.isoformat()},
    )
    bot = make_bot(tmp_path, monkeypatch, report=report, connector=connector)

    result = bot.handle_command("/exec_approve AEX-TEST", "123")

    assert "Final Decision: EXPIRED" in result["response_text"]
    assert "ticket expired" in result["response_text"]
    assert connector.mt5.requests == []


def test_exec_approve_observer_symbol_blocked(tmp_path: Path, monkeypatch):
    connector = MockConnector()
    bot = make_bot(tmp_path, monkeypatch, report=make_exec_report(ticket_overrides={"symbol": "NAS100"}), connector=connector)

    result = bot.handle_command("/exec_approve AEX-TEST", "123")

    assert "Final Decision: BLOCKED" in result["response_text"]
    assert "symbol not allowed" in result["response_text"]
    assert connector.mt5.requests == []


def test_exec_approve_non_a_plus_ticket_blocked(tmp_path: Path, monkeypatch):
    connector = MockConnector()
    bot = make_bot(tmp_path, monkeypatch, report=make_exec_report(ticket_overrides={"grade": "A"}), connector=connector)

    result = bot.handle_command("/exec_approve AEX-TEST", "123")

    assert "Final Decision: BLOCKED" in result["response_text"]
    assert "only A+ tickets are allowed" in result["response_text"]
    assert connector.mt5.requests == []


def test_exec_approve_dry_run_mode_does_not_call_order_send(tmp_path: Path, monkeypatch):
    connector = MockConnector()
    bot = make_bot(tmp_path, monkeypatch, report=make_exec_report(), connector=connector)

    result = bot.handle_command("/exec_approve AEX-TEST", "123")

    assert "Final Decision: APPROVED_DRY_RUN" in result["response_text"]
    assert "Submit Orders: FALSE" in result["response_text"]
    assert "Order Send: NOT CALLED" in result["response_text"]
    assert "symbol: US30" in result["response_text"]
    assert connector.mt5.requests == []


def test_execute_approve_alias_submits_demo_only_when_enabled_and_gates_pass(tmp_path: Path, monkeypatch):
    connector = MockConnector()
    report = make_exec_report(config_overrides={"submit_orders": True})
    bot = make_bot(tmp_path, monkeypatch, report=report, connector=connector)

    result = bot.handle_command("/execute_approve AEX-TEST", "123")

    assert "Final Decision: SUBMITTED_DEMO" in result["response_text"]
    assert "Submit Orders: TRUE" in result["response_text"]
    assert "Order Send: CALLED" in result["response_text"]
    assert connector.mt5.requests[0]["symbol"] == "US30"


def test_exec_approve_live_account_blocked(tmp_path: Path, monkeypatch):
    connector = MockConnector(account_mode="live")
    report = make_exec_report(config_overrides={"submit_orders": True})
    bot = make_bot(tmp_path, monkeypatch, report=report, connector=connector)

    result = bot.handle_command("/exec_approve AEX-TEST", "123")

    assert "Final Decision: BLOCKED" in result["response_text"]
    assert "MT5 account is not demo" in result["response_text"]
    assert connector.mt5.requests == []


def test_exec_approve_preserves_production_baseline(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch, report=make_exec_report(), connector=MockConnector())

    result = bot.handle_command("/exec_approve AEX-TEST", "123")

    assert "Production Baseline Preserved: True" in result["response_text"]
