from __future__ import annotations

import json
from pathlib import Path

from backend.demo_sandbox.demo_sandbox_engine import DemoSandboxEngine, SandboxLearningMemory
from backend.symbols.symbol_registry import SymbolRegistry
from backend.telegram_bot.telegram_command_bot import TelegramCommandBot
from dashboard.utils.data_loader import demo_sandbox_performance_dataframe, load_demo_sandbox_summary


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
        return {"retcode": self.TRADE_RETCODE_DONE, "order": 9001, "comment": "sandbox demo done"}


class MockConnector:
    def __init__(self, *, account_mode: str = "demo") -> None:
        self.mt5 = MockMT5()
        self.account_mode = account_mode

    def get_account_info(self) -> dict:
        server = "MetaQuotes-Demo" if self.account_mode == "demo" else "Live-Server"
        return {"login": 123456, "server": server, "account_mode": self.account_mode, "balance": 10000.0}


ENABLED_SANDBOX = {
    "enabled": True,
    "mode": "DEMO_ONLY",
    "allowed_symbols": ["BTCUSD", "NAS100"],
    "allowed_grades": ["A+", "A"],
    "default_risk_percent": 0.05,
    "max_risk_percent": 0.10,
    "human_approval_required": True,
    "submit_orders": False,
    "production_metrics_excluded": True,
    "challenge_mode_allowed": False,
    "max_slippage_points": {"BTCUSD": 300, "NAS100": 120},
    "max_spread_points": {"BTCUSD": 500, "NAS100": 150},
    "duplicate_order_protection": True,
    "require_fresh_ticket_seconds": 120,
}


def context(**overrides) -> dict:
    payload = {
        "spread_points": 20,
        "slippage_points": 0,
        "account": {"account_mode": "demo", "server": "MetaQuotes-Demo", "balance": 10000.0},
    }
    payload.update(overrides)
    return payload


def make_engine(*, config: dict | None = None, account_mode: str = "demo") -> DemoSandboxEngine:
    return DemoSandboxEngine(connector=MockConnector(account_mode=account_mode), config=config if config is not None else ENABLED_SANDBOX)


def test_sandbox_disabled_by_default():
    engine = DemoSandboxEngine(config={})

    assert engine.config["enabled"] is False
    assert engine.config["mode"] == "DEMO_ONLY"
    assert engine.config["submit_orders"] is False


def test_btc_nas_blocked_from_production_but_allowed_in_sandbox():
    registry = SymbolRegistry()
    engine = make_engine()

    assert registry.execution_allowed("BTCUSD") is False
    assert registry.execution_allowed("NAS100") is False
    assert registry.sandbox_execution_allowed("BTCUSD") is True
    assert registry.sandbox_execution_allowed("NAS100") is True

    ticket = engine.create_ticket(symbol="BTCUSD")
    validation = engine.sandbox_gate(ticket, context=context(), human_approved=True)

    assert validation["checks"]["symbol_tier"] is True


def test_sandbox_ticket_labeled_sandbox_demo_and_dry_run_never_sends():
    engine = make_engine()
    ticket = engine.create_ticket(symbol="NAS100", entry_price=18000.0, stop_loss=17950.0, take_profit=18150.0)

    dry_run = engine.dry_run(ticket, context=context())

    assert dry_run["ticket_type"] == "SANDBOX_DEMO"
    assert dry_run["order_send_called"] is False
    assert "SANDBOX DEMO ONLY" in dry_run["safety_banner"]
    assert engine.connector.mt5.requests == []


def test_eurusd_gbpusd_remain_observer_only_and_blocked_from_sandbox():
    registry = SymbolRegistry()
    engine = make_engine()

    assert registry.tier_for("EURUSD") == "OBSERVER_ONLY"
    assert registry.tier_for("GBPUSD") == "OBSERVER_ONLY"

    ticket = engine.create_ticket(symbol="EURUSD", entry_price=1.1, stop_loss=1.09, take_profit=1.12)
    validation = engine.sandbox_gate(ticket, context=context(), human_approved=True)

    assert validation["checks"]["symbol_tier"] is False
    assert "symbol is not DEMO_SANDBOX" in validation["reasons"]


def test_submit_blocked_until_submit_orders_true_and_demo_approved():
    dry_engine = make_engine()
    ticket = dry_engine.transition_ticket(dry_engine.create_ticket(symbol="BTCUSD"), "APPROVED")

    dry_result = dry_engine.submit_demo_order(ticket, context=context(), human_approved=True)

    assert dry_result["order_submitted"] is False
    assert dry_result["order_send_called"] is False
    assert "dry-run only" in dry_result["reason"]

    submit_engine = make_engine(config={**ENABLED_SANDBOX, "submit_orders": True})
    submitted = submit_engine.submit_demo_order(ticket, context=context(), human_approved=True)

    assert submitted["order_submitted"] is True
    assert submitted["order_send_called"] is True
    assert submit_engine.connector.mt5.requests[0]["symbol"] == "BTCUSD"


def test_live_account_kill_switch_and_challenge_mode_block_sandbox_submit():
    live_engine = make_engine(config={**ENABLED_SANDBOX, "submit_orders": True}, account_mode="live")
    ticket = live_engine.transition_ticket(live_engine.create_ticket(symbol="BTCUSD"), "APPROVED")

    live = live_engine.submit_demo_order(ticket, context=context(account={"account_mode": "live", "server": "Live-Server"}), human_approved=True)
    kill = make_engine(config={**ENABLED_SANDBOX, "submit_orders": True}).submit_demo_order(
        ticket,
        context=context(kill_switch_active=True),
        human_approved=True,
    )
    challenge = make_engine(config={**ENABLED_SANDBOX, "submit_orders": True}).submit_demo_order(
        ticket,
        context=context(challenge_mode_active=True),
        human_approved=True,
    )

    assert "MT5 account is not demo" in live["reason"]
    assert "kill switch active" in kill["reason"]
    assert "challenge mode is active" in challenge["reason"]
    assert live_engine.connector.mt5.requests == []


def test_sandbox_learning_memory_updates_and_excludes_production_metrics():
    memory = SandboxLearningMemory.build(
        [
            {"symbol": "BTCUSD", "rr": 2.0, "spread": 100, "slippage": 20, "latency": 300, "setup_type": "trend", "regime": "expansion"},
            {"symbol": "BTCUSD", "rr": -1.0, "spread": 120, "slippage": 25, "latency": 330, "setup_type": "reversal", "regime": "range"},
            {
                "symbol": "NAS100",
                "rr": 1.5,
                "spread": 50,
                "slippage": 10,
                "latency": 200,
                "setup_type": "trend",
                "regime": "new_york_open",
                "execution_anomaly": "minor_slippage",
            },
        ],
        symbols=["BTCUSD", "NAS100"],
    )

    assert memory["production_metrics_excluded"] is True
    assert memory["symbols"]["BTCUSD"]["trade_count"] == 2
    assert memory["symbols"]["BTCUSD"]["PF"] == 2.0
    assert memory["symbols"]["NAS100"]["execution_anomaly_clusters"] == {"minor_slippage": 1}


def test_status_report_dashboard_loader_and_production_baseline_preserved(tmp_path: Path):
    report = make_engine().status_report(trades=[])
    path = tmp_path / "data" / "reports" / "demo_sandbox_status.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(report), encoding="utf-8")

    summary = load_demo_sandbox_summary(tmp_path, {"demo_sandbox_report_path": "data/reports/demo_sandbox_status.json"})
    frame = demo_sandbox_performance_dataframe(summary)

    assert summary["available"] is True
    assert report["production_baseline_preserved"] is True
    assert report["sandbox"]["production_metrics_excluded"] is True
    assert set(report["symbol_tiers"]["demo_sandbox"]) == {"BTCUSD", "NAS100"}
    assert set(report["symbol_tiers"]["observer_only"]) == {"EURUSD", "GBPUSD"}
    assert {"BTCUSD", "NAS100"}.issubset(set(frame["symbol"]))


def test_telegram_sandbox_commands(monkeypatch):
    report = make_engine().status_report(trades=[])
    bot = TelegramCommandBot(snapshot_provider=lambda: {"demo_sandbox": {"available": True, "data": report}})
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    status = bot.handle_command("/sandbox_status", "123")
    symbols = bot.handle_command("/sandbox_symbols", "123")
    ticket = bot.handle_command("/sandbox_ticket SBX-SAMPLE", "123")
    dry_run = bot.handle_command("/sandbox_dry_run SBX-SAMPLE", "123")
    approve = bot.handle_command("/sandbox_approve SBX-SAMPLE", "123")

    assert "SANDBOX DEMO ONLY" in status["response_text"]
    assert "Demo Sandbox: BTCUSD, NAS100" in symbols["response_text"]
    assert "Ticket Type: SANDBOX_DEMO" in ticket["response_text"]
    assert "Order Send: NOT CALLED" in dry_run["response_text"]
    assert "Final Decision: APPROVED_DRY_RUN" in approve["response_text"]
    assert "Order Send: NOT CALLED" in approve["response_text"]
