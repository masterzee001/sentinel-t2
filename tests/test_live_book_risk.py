"""Live-book risk profile: prop-firm limits must NOT bind the live engines.

The champion and mean-reversion engines were promoted by backtests that took
every signal with no operational limits. These tests pin the contract that
the live path is governed by config/live_book_risk.yaml malfunction tripwires
(never touched by audited behavior) while the advisor stack keeps the
prop-firm profile in risk_profile.yaml unchanged.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.live_paper.demo_order_executor import DemoOrderExecutor
from backend.risk_manager.risk_governor import RiskGovernor
from backend.risk_manager.risk_state_store import RiskStateStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIVE_PROFILE = PROJECT_ROOT / "config" / "live_book_risk.yaml"


class StaticConnector:
    """Connector stub returning a fixed account snapshot."""

    def __init__(self, balance: float = 3000.0, equity: float = 3000.0) -> None:
        self._balance = balance
        self._equity = equity

    def get_account_info(self):
        return {
            "login": 110883498,
            "server": "MetaQuotes-Demo",
            "currency": "USD",
            "balance": self._balance,
            "equity": self._equity,
            "profit": self._equity - self._balance,
        }


def live_governor(connector=None) -> RiskGovernor:
    return RiskGovernor(
        connector=connector or StaticConnector(),
        risk_profile_file=LIVE_PROFILE,
        environment_mode="development",
    )


def test_live_profile_loads_tripwire_values():
    governor = live_governor()
    assert governor.max_trades_per_day == 15
    assert governor.daily_loss_limit == 18.0
    assert governor.max_consecutive_losses == 50
    assert governor.cooldown_after_loss_minutes == 0
    # Meanrev's audited trough at 1%/risk-unit sizing is ~30-42% account DD;
    # tripwires must sit OUTSIDE audited behavior (user mandate: no risk
    # limitation on trading — tripwires are bug brakes only).
    assert governor.internal_drawdown_limit == 50.0
    assert governor.firm_drawdown_limit == 60.0
    assert governor.max_total_open_planned_risk_percent == 10.0


def test_missing_explicit_profile_fails_loud(tmp_path: Path):
    """A vanished live profile must refuse to start, never silently revert
    to the prop-firm defaults (2 trades/day, 4% DD block)."""
    import pytest

    from backend.risk_manager.risk_governor import RiskGovernorError

    with pytest.raises(RiskGovernorError):
        RiskGovernor(
            connector=StaticConnector(),
            risk_profile_file=tmp_path / "does_not_exist.yaml",
            environment_mode="development",
        )
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(RiskGovernorError):
        RiskGovernor(connector=StaticConnector(), risk_profile_file=empty, environment_mode="development")


def test_profile_overrides_merge_on_top():
    """Meanrev tightens the runaway tripwire to 6/day via overrides."""
    governor = RiskGovernor(
        connector=StaticConnector(),
        risk_profile_file=LIVE_PROFILE,
        risk_profile_overrides={"daily_limits": {"max_trades_per_day": 6}},
        environment_mode="development",
    )
    assert governor.max_trades_per_day == 6
    assert governor.daily_loss_limit == 18.0  # everything else untouched


def test_advisor_governor_still_uses_prop_firm_profile():
    # No risk_profile_file -> risk_profile.yaml: the advisor stack keeps its limits.
    governor = RiskGovernor(connector=StaticConnector(), environment_mode="development")
    assert governor.max_trades_per_day == 2
    assert governor.daily_loss_limit == 1.0
    assert governor.internal_drawdown_limit == 4.0


def test_loss_streaks_and_cooldown_do_not_block_live_book():
    """A 3R system strings 8+ losses routinely; live trading must continue."""
    governor = live_governor()
    reasons = governor.evaluate_blocks(
        daily_loss_percent=13.0,  # worst legit stops-fill-at-distance day
        daily_loss_verified=True,
        current_drawdown_percent=42.0,  # audited meanrev trough at 1%/unit sizing
        trades_taken_today=7,  # legit busiest day
        consecutive_losses=10,
        cooldown_active=governor.is_cooldown_active(
            last_loss_time=None, now=governor.parse_datetime("2026-08-11T12:00:00+01:00"), cooldown_minutes=0
        ),
        total_open_planned_risk_percent=9.9,  # meanrev at broker minimums + champion
    )
    assert reasons == []


def test_malfunction_tripwires_still_fire():
    governor = live_governor()
    reasons = governor.evaluate_blocks(
        daily_loss_percent=19.0,
        daily_loss_verified=True,
        current_drawdown_percent=60.0,
        trades_taken_today=15,
        consecutive_losses=50,
        cooldown_active=False,
        total_open_planned_risk_percent=11.0,
    )
    assert "Daily loss limit hit" in reasons
    assert "Firm drawdown limit hit" in reasons
    assert "Max trades per day hit" in reasons
    assert "Max consecutive losses hit" in reasons
    assert "Max total open planned risk exceeded" in reasons


def test_full_evaluate_allows_trading_at_audited_drawdown(tmp_path: Path):
    """End-to-end: 8% drawdown from peak (audited envelope) must stay ALLOWED."""
    store = RiskStateStore(tmp_path / "state.json")
    store.observe_account(balance=3000.0, equity=3000.0)  # sets peak
    governor = RiskGovernor(
        connector=StaticConnector(balance=3000.0, equity=2760.0),  # 8% off peak
        state_store=store,
        risk_profile_file=LIVE_PROFILE,
        environment_mode="development",
    )
    result = governor.evaluate()
    assert result["permission"]["trade_allowed"] is True
    assert result["permission"]["block_reasons"] == []


class _ExecutorMT5:
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009

    def __init__(self, retcode: int = 10009) -> None:
        self._retcode = retcode

    def symbol_select(self, symbol, enabled):
        return True

    def symbol_info(self, symbol):
        return SimpleNamespace(
            trade_tick_value=1.0, trade_tick_size=1.0, volume_min=0.1, volume_step=0.1, volume_max=50.0
        )

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(ask=44120.5, bid=44119.5)

    def order_send(self, request):
        return SimpleNamespace(retcode=self._retcode, order=777, price=request["price"], comment="x")


class _ExecutorConnector:
    def __init__(self, retcode: int = 10009) -> None:
        self.mt5 = _ExecutorMT5(retcode)

    def get_account_info(self):
        return {"login": 1, "server": "MetaQuotes-Demo", "equity": 3000.0, "balance": 3000.0}


class _StoreGovernor:
    def __init__(self, store: RiskStateStore) -> None:
        self.state_store = store

    def evaluate(self):
        return {"permission": {"trade_allowed": True, "block_reasons": []}}


POSITION = {
    "symbol": "US30",
    "direction": "bullish",
    "entry": 44120.0,
    "stop_loss": 44020.0,
    "take_profit": 44420.0,
}


def test_executor_counts_filled_opens_for_daily_tripwire(tmp_path: Path):
    store = RiskStateStore(tmp_path / "state.json")
    executor = DemoOrderExecutor(_ExecutorConnector(), _StoreGovernor(store), tmp_path / "KILL")
    result = executor.open_position(dict(POSITION))
    assert result["submitted"] is True
    state = store.load()
    assert state["trades_taken_today"] == 1
    # Planned risk deliberately NOT reserved (never released on server-side closes).
    assert state["total_open_planned_risk_percent"] == 0.0


def test_executor_does_not_count_rejected_orders(tmp_path: Path):
    store = RiskStateStore(tmp_path / "state.json")
    executor = DemoOrderExecutor(_ExecutorConnector(retcode=10019), _StoreGovernor(store), tmp_path / "KILL")
    result = executor.open_position(dict(POSITION))
    assert result["submitted"] is False
    assert store.load()["trades_taken_today"] == 0


def test_governor_crash_refuses_order_but_does_not_raise(tmp_path: Path):
    """An MT5 hiccup during risk evaluation must refuse the order (fail
    closed) without killing the engine process."""

    class CrashingGovernor:
        state_store = None

        def evaluate(self):
            raise RuntimeError("account info unavailable")

    executor = DemoOrderExecutor(_ExecutorConnector(), CrashingGovernor(), tmp_path / "KILL")
    result = executor.open_position(dict(POSITION))
    assert result["submitted"] is False
    assert "risk evaluation failed" in result["reason"]


def test_implausibly_small_stop_is_refused(tmp_path: Path):
    """Feed-glitch guard: a near-zero stop distance would size a position
    risking many times the budget; both engines' real stops are structural."""
    executor = DemoOrderExecutor(_ExecutorConnector(), _StoreGovernor(RiskStateStore(tmp_path / "s.json")), tmp_path / "KILL")
    glitched = {**POSITION, "stop_loss": POSITION["entry"] - 1.0}  # 1pt on a 44k index
    result = executor.open_position(glitched)
    assert result["submitted"] is False
    assert "implausibly small stop" in result["reason"]


def test_lots_sanity_ceiling_refuses_oversized_orders(tmp_path: Path):
    """Corrupt broker tick metadata must not slip a 10-50x position through."""
    connector = _ExecutorConnector()
    connector.mt5.symbol_info = lambda symbol: SimpleNamespace(
        trade_tick_value=0.001, trade_tick_size=1.0, volume_min=0.1, volume_step=0.1, volume_max=200.0
    )
    executor = DemoOrderExecutor(connector, _StoreGovernor(RiskStateStore(tmp_path / "s.json")), tmp_path / "KILL")
    result = executor.open_position(dict(POSITION))
    assert result["submitted"] is False
    assert "sanity ceiling" in result["reason"]


def test_unwritable_risk_state_fails_closed_after_three_failures(tmp_path: Path):
    """Silent tripwire disable is not allowed: if trade counting keeps
    failing, the executor refuses new orders instead of trading uncounted."""

    class BrokenStore:
        def record_trade_opened(self, planned_risk_percent=0.0):
            raise OSError("disk full")

    class BrokenStoreGovernor:
        state_store = BrokenStore()

        def evaluate(self):
            return {"permission": {"trade_allowed": True, "block_reasons": []}}

    executor = DemoOrderExecutor(_ExecutorConnector(), BrokenStoreGovernor(), tmp_path / "KILL")
    for _ in range(3):
        result = executor.open_position(dict(POSITION))
        assert result["submitted"] is True  # fills happened; counting failed silently
    result = executor.open_position(dict(POSITION))
    assert result["submitted"] is False
    assert "unwritable" in result["reason"]


def test_champion_admission_context_stays_pure():
    """Regression pin (audit 2026-08-11): the champion's brain admission must
    never receive RiskGovernor-derived flags — the audited backtest brain
    never saw them, and feeding them would contaminate admission parity.
    The governor may gate ORDER EXECUTION only."""
    source = (PROJECT_ROOT / "backend" / "live_paper" / "champion_paper_trader.py").read_text(encoding="utf-8")
    for forbidden in (
        "decision_context_from_result",
        "risk_blocked",
        "daily_loss_limit_hit",
        "max_trades_per_day_hit",
        "high_impact_news_lock_active",
        "RiskGovernor",
    ):
        assert forbidden not in source, f"champion admission contaminated by '{forbidden}'"


def test_orders_use_broker_alias_symbol(tmp_path: Path):
    """NAS100 trades as USTEC on MetaQuotes-Demo: candle fetches already
    resolve aliases, and order execution must use the same resolution or the
    orders silently fail to size (found live 2026-08-11)."""
    connector = _ExecutorConnector()
    connector.broker_symbol = lambda symbol: "USTEC" if symbol == "NAS100" else symbol
    sent = []
    original_send = connector.mt5.order_send
    connector.mt5.order_send = lambda request: (sent.append(request), original_send(request))[1]
    executor = DemoOrderExecutor(connector, _StoreGovernor(RiskStateStore(tmp_path / "s.json")), tmp_path / "KILL")
    result = executor.open_position({**POSITION, "symbol": "NAS100"})
    assert result["submitted"] is True
    assert sent[0]["symbol"] == "USTEC"


def test_min_lot_acceptance_takes_minimum_within_cap(tmp_path: Path):
    """User mandate: signals must reach the account. When the risk-budget lot
    is below the broker minimum, take the minimum if its implied risk is
    under the cap; refuse only past the cap."""
    connector = _ExecutorConnector()
    connector.mt5.symbol_info = lambda symbol: SimpleNamespace(
        trade_tick_value=1.0, trade_tick_size=1.0, volume_min=0.1, volume_step=0.1, volume_max=50.0
    )
    governor = _StoreGovernor(RiskStateStore(tmp_path / "s.json"))
    # equity 3000, stop 1200 -> raw lot at 3% risk = 0.075 < 0.1 minimum;
    # minimum implies 1200*0.1/3000 = 4% risk.
    accepting = DemoOrderExecutor(
        connector, governor, tmp_path / "KILL", risk_percent=3.0, min_lot_risk_cap_percent=6.0
    )
    assert accepting.lot_size("US30", 1200.0, 3000.0) == 0.1
    tight_cap = DemoOrderExecutor(
        connector, governor, tmp_path / "KILL", risk_percent=3.0, min_lot_risk_cap_percent=2.0
    )
    assert tight_cap.lot_size("US30", 1200.0, 3000.0) == 0.0
    strict_default = DemoOrderExecutor(connector, governor, tmp_path / "KILL", risk_percent=3.0)
    assert strict_default.lot_size("US30", 1200.0, 3000.0) == 0.0


def test_live_engines_observe_account_every_cycle():
    """Regression pin: both engine loops must feed equity observations all
    day so the daily-loss baseline is not set at the first open attempt."""
    for script in ("run_champion_paper.py", "run_mean_reversion_live.py"):
        source = (PROJECT_ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert "observe_account" in source, f"{script} no longer observes account equity per cycle"
        assert "live_book_risk.yaml" in source, f"{script} not wired to the live-book profile"
