from __future__ import annotations

import pandas as pd

from backend.backtesting.backtest_engine import BacktestEngine
from backend.backtesting.historical_structure import detect_ict_candidate, find_swing_points


def frame(highs: list[float], lows: list[float], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-06-01", periods=len(highs), freq="15min", tz="UTC"),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
        }
    )


def bullish_sweep_mss_sequence() -> pd.DataFrame:
    """Flat context, swing low at 99, sweep wick to 98.4 closing back above,
    then an MSS displacement bar closing above the prior swing high with a FVG."""
    n = 40
    highs = [101.0] * n
    lows = [99.6] * n
    closes = [100.3] * n
    # Fractal swing high (index 25) then swing low (index 30).
    highs[25] = 101.8
    lows[30] = 99.0
    # Sweep bar (index 36): wick below 99.0, close back above it.
    lows[36] = 98.4
    closes[36] = 99.4
    # Small pullback bar leaves room for the FVG (index 37).
    highs[37] = 100.0
    lows[37] = 99.3
    closes[37] = 99.8
    highs[38] = 100.4
    lows[38] = 99.7
    closes[38] = 100.3
    # MSS displacement bar (index 39): closes above 101.8 with a gap over bar 37.
    highs[39] = 102.6
    lows[39] = 100.9
    closes[39] = 102.4
    return frame(highs, lows, closes)


def test_swing_points_are_fractal():
    highs = [1, 2, 5, 2, 1, 2, 3]
    lows = [1, 0.5, 0.2, 0.5, 1, 0.8, 0.9]
    swing_highs, swing_lows = find_swing_points([float(x) for x in highs], [float(x) for x in lows])
    assert swing_highs == [2]
    assert swing_lows == [2]


def test_detects_bullish_sweep_mss_fvg_sequence():
    candidate = detect_ict_candidate(bullish_sweep_mss_sequence())

    assert candidate is not None and candidate != {}
    assert candidate["direction"] == "bullish"
    assert candidate["liquidity_sweep_confirmed"] is True
    assert candidate["mss_confirmed"] is True
    assert candidate["swept_level"] == 99.0
    assert candidate["stop"] == 98.4  # Sweep extreme protects the trade.
    assert candidate["fvg_gap"] > 0
    assert candidate["mss_broken_level"] == 101.8


def test_plain_breakout_without_sweep_is_not_a_candidate():
    n = 40
    highs = [101.0] * n
    lows = [99.5] * n
    closes = [100.0] * n
    highs[39] = 106.0
    lows[39] = 100.0
    closes[39] = 105.5  # Breakout with displacement but no liquidity sweep.
    assert not detect_ict_candidate(frame(highs, lows, closes))


def test_engine_builds_ict_plan_when_detector_selected():
    engine = BacktestEngine(connector=object())
    engine.config["scan"]["candidate_detector"] = "ict_structure"

    plan = engine.build_historical_plan(
        "US30",
        bullish_sweep_mss_sequence(),
        {"is_valid": True, "active_killzone": "new_york_open", "quality_score": 12},
    )

    assert plan["candidate_detected"] is True
    assert plan["planner_quality"] == "historical_ict_structure"
    assert plan["raw_score_breakdown"]["liquidity_sweep"] == 20
    assert plan["raw_score_breakdown"]["mss"] == 20
    assert plan["stop_loss"]["price"] == 98.4
    assert plan["take_profit"]["tp3"] > plan["entry"]["price"]
