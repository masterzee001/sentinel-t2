from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.risk_manager.risk_governor import RiskGovernor
from backend.risk_manager.risk_state_store import RiskStateStore

WAT = ZoneInfo("Africa/Lagos")
DAY_1 = datetime(2026, 8, 10, 9, 0, tzinfo=WAT)
DAY_2 = datetime(2026, 8, 11, 9, 0, tzinfo=WAT)


class FakeConnector:
    def __init__(self, account_info: dict):
        self.account_info = account_info

    def get_account_info(self) -> dict:
        return self.account_info


def write_configs(config_dir: Path) -> None:
    config_dir.mkdir()
    (config_dir / "risk_profile.yaml").write_text(
        """
drawdown:
  firm_max_drawdown_percent: 6.0
  internal_max_drawdown_percent: 4.0
daily_limits:
  max_trades_per_day: 2
  daily_loss_limit_percent: 1.0
  stop_after_consecutive_losses: 2
  cooldown_after_stop_loss_minutes: 15
risk_per_trade:
  week_1_percent: 0.5
  week_2_percent_if_profitable: 1.0
  forex_development_percent: 0.25
portfolio_limits:
  max_total_open_planned_risk_percent: 1.0
""",
        encoding="utf-8",
    )
    (config_dir / "trading_rules.yaml").write_text(
        """
markets:
  allowed:
    - "XAUUSD"
    - "US30"
  forex:
    symbols:
      - "EURUSD"
      - "GBPUSD"
""",
        encoding="utf-8",
    )


def make_governor(tmp_path: Path, store: RiskStateStore, balance: float = 5000.0, equity: float = 5000.0) -> RiskGovernor:
    config_dir = tmp_path / "config"
    if not config_dir.exists():
        write_configs(config_dir)
    connector = FakeConnector(
        {
            "login": 123456,
            "server": "Demo-Server",
            "currency": "USD",
            "balance": balance,
            "equity": equity,
            "profit": equity - balance,
        }
    )
    return RiskGovernor(connector=connector, config_dir=config_dir, state_store=store)


def make_store(tmp_path: Path) -> RiskStateStore:
    return RiskStateStore(tmp_path / "runtime" / "risk_state.json")


def test_daily_rollover_resets_daily_counters_but_preserves_streaks(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.observe_account(balance=5000.0, equity=5000.0, now=DAY_1)
    store.record_trade_opened(planned_risk_percent=0.5, now=DAY_1)
    store.record_trade_closed(pnl_amount=-25.0, planned_risk_percent=0.5, now=DAY_1)

    state = store.load(now=DAY_2)
    assert state["trades_taken_today"] == 0
    assert state["realized_daily_pnl"] == 0.0
    assert state["daily_start_equity"] is None
    assert state["consecutive_losses"] == 1
    assert state["last_loss_time"] is not None
    assert state["peak_equity"] == 5000.0
    assert state["starting_balance"] == 5000.0


def test_observe_account_tracks_peak_and_daily_loss(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.observe_account(balance=5000.0, equity=5000.0, now=DAY_1)
    store.observe_account(balance=5000.0, equity=5100.0, now=DAY_1 + timedelta(hours=1))
    state = store.observe_account(balance=5000.0, equity=4940.0, now=DAY_1 + timedelta(hours=2))

    assert state["peak_equity"] == 5100.0
    assert state["daily_start_equity"] == 5000.0
    assert RiskStateStore.daily_loss_percent(state) == 1.2


def test_daily_loss_is_none_before_any_reading(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    state = store.load(now=DAY_1)
    assert RiskStateStore.daily_loss_percent(state) is None
    assert "daily_loss_percent" not in store.runtime_state(now=DAY_1)


def test_trade_records_update_counters_and_win_resets_streak(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.record_trade_opened(planned_risk_percent=0.5, now=DAY_1)
    store.record_trade_opened(planned_risk_percent=0.5, now=DAY_1)
    state = store.load(now=DAY_1)
    assert state["trades_taken_today"] == 2
    assert state["total_open_planned_risk_percent"] == 1.0

    store.record_trade_closed(pnl_amount=-25.0, planned_risk_percent=0.5, now=DAY_1)
    store.record_trade_closed(pnl_amount=-25.0, planned_risk_percent=0.5, now=DAY_1)
    state = store.load(now=DAY_1)
    assert state["consecutive_losses"] == 2
    assert state["total_open_planned_risk_percent"] == 0.0

    store.record_trade_opened(planned_risk_percent=0.5, now=DAY_1)
    store.record_trade_closed(pnl_amount=40.0, planned_risk_percent=0.5, now=DAY_1)
    state = store.load(now=DAY_1)
    assert state["consecutive_losses"] == 0


def test_corrupt_state_file_starts_fresh(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.state_path.parent.mkdir(parents=True, exist_ok=True)
    store.state_path.write_text("{not json", encoding="utf-8")
    state = store.load(now=DAY_1)
    assert state["trades_taken_today"] == 0
    assert state["peak_equity"] is None


def test_state_survives_new_store_instance(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "risk_state.json"
    RiskStateStore(path).observe_account(balance=5000.0, equity=5300.0, now=DAY_1)
    reloaded = RiskStateStore(path).load(now=DAY_1)
    assert reloaded["peak_equity"] == 5300.0


def test_governor_with_store_blocks_after_max_trades(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.record_trade_opened(planned_risk_percent=0.5)
    store.record_trade_opened(planned_risk_percent=0.5)
    governor = make_governor(tmp_path, store)

    result = governor.evaluate()
    assert "Max trades per day hit" in result["permission"]["block_reasons"]
    assert result["permission"]["trade_allowed"] is False


def test_governor_with_store_blocks_after_consecutive_losses_and_cooldown(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.record_trade_closed(pnl_amount=-25.0)
    store.record_trade_closed(pnl_amount=-25.0)
    governor = make_governor(tmp_path, store)

    result = governor.evaluate()
    reasons = result["permission"]["block_reasons"]
    assert "Max consecutive losses hit" in reasons
    assert "Cooldown after loss active" in reasons


def test_governor_with_store_verifies_daily_loss_and_blocks(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.observe_account(balance=5000.0, equity=5000.0)
    governor = make_governor(tmp_path, store, balance=5000.0, equity=4930.0)

    result = governor.evaluate()
    assert result["risk"]["daily_loss_percent"] == 1.4
    assert "Daily loss limit hit" in result["permission"]["block_reasons"]
    assert "Daily loss history unavailable" not in result["permission"]["warnings"]


def test_governor_peak_equity_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "risk_state.json"
    first_store = RiskStateStore(path)
    first = make_governor(tmp_path, first_store, balance=5000.0, equity=5300.0)
    first.evaluate()

    fresh_store = RiskStateStore(path)
    fresh = make_governor(tmp_path, fresh_store, balance=5000.0, equity=5000.0)
    result = fresh.evaluate()
    assert result["risk"]["current_drawdown_percent"] == 5.66
    assert "Internal drawdown limit hit" in result["permission"]["block_reasons"]


def test_governor_caller_supplied_state_wins_over_store(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.record_trade_opened(planned_risk_percent=0.5)
    store.record_trade_opened(planned_risk_percent=0.5)
    governor = make_governor(tmp_path, store)

    result = governor.evaluate({"trades_taken_today": 0})
    assert "Max trades per day hit" not in result["permission"]["block_reasons"]


def test_runtime_state_from_result_round_trip(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.observe_account(balance=5000.0, equity=5000.0)
    store.record_trade_opened(planned_risk_percent=0.5)
    governor = make_governor(tmp_path, store)
    result = governor.evaluate()

    runtime = RiskGovernor.runtime_state_from_result(result)
    assert runtime["trades_taken_today"] == 1
    assert runtime["consecutive_losses"] == 0
    assert "daily_loss_percent" in runtime


def test_runtime_state_from_result_omits_unverified_daily_loss(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    write_configs(config_dir)
    connector = FakeConnector(
        {"login": 1, "server": "s", "currency": "USD", "balance": 5000.0, "equity": 5000.0, "profit": 0.0}
    )
    governor = RiskGovernor(connector=connector, config_dir=config_dir)
    result = governor.evaluate()

    runtime = RiskGovernor.runtime_state_from_result(result)
    assert "daily_loss_percent" not in runtime


def test_decision_context_from_result_flags_blocks(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.record_trade_opened(planned_risk_percent=0.5)
    store.record_trade_opened(planned_risk_percent=0.5)
    governor = make_governor(tmp_path, store)
    result = governor.evaluate()

    flags = RiskGovernor.decision_context_from_result(result)
    assert flags["max_trades_per_day_hit"] is True
    assert flags["risk_blocked"] is True
    assert flags["daily_loss_limit_hit"] is False
