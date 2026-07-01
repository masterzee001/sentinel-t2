from __future__ import annotations

import pandas as pd

from backend.smt_engine.smt_analyzer import SMTAnalyzer


def make_smt_candles(
    swing_highs: tuple[float, float] = (110.0, 115.0),
    swing_lows: tuple[float, float] = (95.0, 96.0),
    count: int = 60,
) -> pd.DataFrame:
    highs = [100.0 for _ in range(count)]
    lows = [100.0 for _ in range(count)]
    if count > 52:
        highs[20] = swing_highs[0]
        highs[45] = swing_highs[1]
        lows[25] = swing_lows[0]
        lows[50] = swing_lows[1]
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-06-28 00:00:00+00:00", periods=count, freq="15min"),
            "open": [(high + low) / 2 for high, low in zip(highs, lows)],
            "high": highs,
            "low": lows,
            "close": [(high + low) / 2 for high, low in zip(highs, lows)],
        }
    )


def test_bullish_smt_detection():
    result = SMTAnalyzer.detect_smt(
        primary_candles=make_smt_candles(swing_lows=(95.0, 90.0), swing_highs=(110.0, 111.0)),
        comparison_candles=make_smt_candles(swing_lows=(95.0, 96.0), swing_highs=(110.0, 111.0)),
        pair_name="EURUSD_GBPUSD",
        primary="EURUSD",
        comparison="GBPUSD",
        weight=10,
    )

    assert result["smt_detected"] is True
    assert result["direction"] == "bullish"
    assert result["primary_event"] == "lower_low"
    assert result["comparison_event"] == "failed_lower_low"
    assert result["confidence"] == 10


def test_bearish_smt_detection():
    result = SMTAnalyzer.detect_smt(
        primary_candles=make_smt_candles(swing_highs=(110.0, 120.0), swing_lows=(95.0, 96.0)),
        comparison_candles=make_smt_candles(swing_highs=(110.0, 109.0), swing_lows=(95.0, 96.0)),
        pair_name="EURUSD_GBPUSD",
        primary="EURUSD",
        comparison="GBPUSD",
        weight=10,
    )

    assert result["smt_detected"] is True
    assert result["direction"] == "bearish"
    assert result["primary_event"] == "higher_high"
    assert result["comparison_event"] == "failed_higher_high"


def test_no_smt_detection():
    result = SMTAnalyzer.detect_smt(
        primary_candles=make_smt_candles(swing_highs=(110.0, 120.0), swing_lows=(95.0, 96.0)),
        comparison_candles=make_smt_candles(swing_highs=(110.0, 121.0), swing_lows=(95.0, 96.0)),
        pair_name="EURUSD_GBPUSD",
        primary="EURUSD",
        comparison="GBPUSD",
        weight=10,
    )

    assert result["smt_detected"] is False
    assert result["direction"] is None
    assert result["confidence"] == 0


def test_insufficient_data_returns_no_smt():
    result = SMTAnalyzer.detect_smt(
        primary_candles=make_smt_candles(count=20),
        comparison_candles=make_smt_candles(count=20),
        pair_name="EURUSD_GBPUSD",
        primary="EURUSD",
        comparison="GBPUSD",
        weight=10,
    )

    assert result["smt_detected"] is False
    assert "Insufficient candle data" in result["explanation"][0]
