from __future__ import annotations

from pathlib import Path

import pandas as pd

from backend.backtesting.backtest_engine import BacktestEngine
from backend.live_paper.champion_paper_trader import ChampionPaperTrader


class FakeKillzoneAnalyzer:
    def analyze(self, symbol: str, current_time=None):
        return {"is_valid": True, "active_killzone": "new_york_open", "quality_score": 12}


def breakout_candles(periods: int = 90, breakout_at: int = 89) -> pd.DataFrame:
    highs = [101.0] * periods
    lows = [99.5] * periods
    closes = [100.0] * periods
    highs[breakout_at] = 106.0
    lows[breakout_at] = 100.0
    closes[breakout_at] = 105.5
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-06-01", periods=periods, freq="15min", tz="UTC"),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
        }
    )


def make_trader(tmp_path: Path) -> ChampionPaperTrader:
    engine = BacktestEngine(connector=object())
    engine.killzone_analyzer = FakeKillzoneAnalyzer()
    return ChampionPaperTrader(engine, tmp_path / "state.json")


def append_bar(candles: pd.DataFrame, *, high: float, low: float, close: float) -> pd.DataFrame:
    next_time = candles.iloc[-1]["time"] + pd.Timedelta(minutes=15)
    row = pd.DataFrame({"time": [next_time], "open": [close], "high": [high], "low": [low], "close": [close]})
    return pd.concat([candles, row], ignore_index=True)


def test_opens_position_on_breakout_and_ignores_stale_candle(tmp_path: Path):
    trader = make_trader(tmp_path)
    candles = breakout_candles()

    first = trader.process_cycle({"US30": candles})
    events = [action["event"] for action in first["actions"]]
    assert "OPEN" in events
    position = trader.state["open_positions"]["US30"]
    assert position["direction"] == "bullish"
    assert position["take_profit"] > position["entry"] > position["stop_loss"]

    # Same candle again: no duplicate action.
    second = trader.process_cycle({"US30": candles})
    assert second["actions"] == []


def test_closes_position_on_target_and_survives_restart(tmp_path: Path):
    trader = make_trader(tmp_path)
    candles = breakout_candles()
    trader.process_cycle({"US30": candles})
    position = trader.state["open_positions"]["US30"]
    target = position["take_profit"]
    cost_rr = position["cost_rr"]
    trader.save_state()

    # Restart from disk, then a new bar sweeps through the 3R target.
    reloaded = make_trader(tmp_path)
    assert "US30" in reloaded.state["open_positions"]
    candles = append_bar(candles, high=target + 1.0, low=target - 3.0, close=target + 0.5)
    result = reloaded.process_cycle({"US30": candles})

    closes = [action for action in result["actions"] if action["event"] == "CLOSE"]
    assert closes and closes[0]["outcome"] == "WIN"
    assert closes[0]["rr"] == round(3.0 - cost_rr, 4)  # Full 3R minus real execution cost.
    # The sweep bar itself qualifies as a fresh breakout, so an immediate
    # re-entry on the new closed bar is legitimate one-position behavior.
    assert result["summary"]["closed_trades"] == 1
    assert reloaded.state["closed_trades"][0]["outcome"] == "WIN"


def test_stop_loss_and_timeout_paths(tmp_path: Path):
    trader = make_trader(tmp_path)
    candles = breakout_candles()
    trader.process_cycle({"US30": candles})
    stop = trader.state["open_positions"]["US30"]["stop_loss"]

    candles = append_bar(candles, high=stop + 0.5, low=stop - 0.5, close=stop)
    result = trader.process_cycle({"US30": candles})
    closed = [action for action in result["actions"] if action["event"] == "CLOSE"][0]
    assert closed["outcome"] == "LOSS"
    assert closed["rr"] < -0.99

    # Fresh trader: ride 24 quiet bars to the timeout exit.
    trader2 = make_trader(tmp_path / "t2")
    candles2 = breakout_candles()
    trader2.process_cycle({"US30": candles2})
    entry = trader2.state["open_positions"]["US30"]["entry"]
    for _ in range(24):
        candles2 = append_bar(candles2, high=entry + 0.5, low=entry - 0.5, close=entry)
        result = trader2.process_cycle({"US30": candles2})
    closed = [action for action in result["actions"] if action["event"] == "CLOSE"][0]
    assert closed["outcome"] == "BREAKEVEN"
    assert closed["rr"] <= 0.0
