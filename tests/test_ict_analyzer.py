from __future__ import annotations

import pandas as pd
import pytest

from backend.ict_engine.ict_analyzer import ICTAnalyzer


def make_candles(highs, lows, closes=None, opens=None, start="2026-06-26 00:00:00+00:00", freq="15min"):
    if closes is None:
        closes = [(high + low) / 2 for high, low in zip(highs, lows)]
    if opens is None:
        opens = closes
    return pd.DataFrame(
        {
            "time": pd.date_range(start=start, periods=len(highs), freq=freq),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
        }
    )


def test_detect_mss_requires_sweep_and_displacement():
    candles = make_candles(
        highs=[100, 101, 102, 103, 104, 105, 104, 103, 102, 101, 106, 108, 112, 118],
        lows=[95, 96, 97, 98, 99, 100, 99, 98, 97, 94, 96, 100, 104, 110],
        opens=[98, 99, 100, 101, 102, 103, 103, 102, 101, 100, 98, 101, 105, 111],
        closes=[99, 100, 101, 102, 103, 104, 100, 99, 98, 97, 104, 107, 111, 117],
    )
    sweep = {"side": "sell_side", "level_price": 95.0, "sweep_index": 9}

    mss = ICTAnalyzer.detect_mss(candles, sweep, atr=4.0, structure_lookback=6)

    assert mss["detected"] is True
    assert mss["direction"] == "bullish"
    assert mss["break_level"] == 105.0
    assert mss["displacement_score"] >= 60.0


def test_detect_fvg_uses_classic_three_candle_imbalance_with_displacement():
    candles = make_candles(
        highs=[100, 104, 112],
        lows=[96, 99, 106],
        opens=[98, 100, 107],
        closes=[99, 103, 111],
    )

    fvg = ICTAnalyzer.detect_fvg(candles, atr=4.0, direction="bullish")

    assert fvg["detected"] is True
    assert fvg["direction"] == "bullish"
    assert fvg["low"] == 100.0
    assert fvg["high"] == 106.0
    assert fvg["grade"] in {"A", "B", "C"}


def test_calculate_premium_discount_uses_sweep_to_mss_range():
    sweep = {"level_price": 100.0}
    mss = {"detected": True, "break_level": 120.0}

    premium_discount = ICTAnalyzer.calculate_premium_discount(sweep, mss, current_price=106.0)

    assert premium_discount == {
        "range_high": 120.0,
        "range_low": 100.0,
        "equilibrium": 110.0,
        "current_zone": "discount",
        "reason": None,
    }


def test_premium_discount_is_unavailable_without_confirmed_mss():
    premium_discount = ICTAnalyzer.calculate_premium_discount(
        sweep={"level_price": 100.0},
        mss={"detected": False, "break_level": 120.0},
        current_price=106.0,
    )

    assert premium_discount == {
        "range_high": 0.0,
        "range_low": 0.0,
        "equilibrium": 0.0,
        "current_zone": "unavailable",
        "reason": "MSS not confirmed; dealing range cannot be anchored.",
    }


def test_detect_order_block_finds_last_opposing_candle_before_mss():
    candles = make_candles(
        highs=[100, 101, 102, 103, 108],
        lows=[95, 96, 97, 98, 101],
        opens=[98, 100, 101, 102, 102],
        closes=[99, 99, 100, 101, 107],
    )

    order_block = ICTAnalyzer.detect_order_block(candles, direction="bullish", reference_index=4)

    assert order_block["detected"] is True
    assert order_block["direction"] == "bullish"
    assert order_block["open"] == 102.0
    assert order_block["close"] == 101.0


def test_bearish_order_block_finds_last_bullish_candle_before_mss():
    candles = make_candles(
        highs=[110, 111, 112, 108, 103],
        lows=[105, 106, 107, 100, 95],
        opens=[108, 109, 108, 106, 101],
        closes=[109, 110, 111, 102, 96],
    )

    order_block = ICTAnalyzer.detect_order_block(candles, direction="bearish", reference_index=4)

    assert order_block["detected"] is True
    assert order_block["direction"] == "bearish"
    assert order_block["open"] == 108.0
    assert order_block["close"] == 111.0


def test_execution_ready_requires_zone_alignment_and_return_to_fvg():
    mss = {"detected": True, "direction": "bullish"}
    fvg = {"detected": True, "direction": "bullish"}
    order_block = {"detected": True, "direction": "bullish"}
    premium_discount = {"current_zone": "discount"}

    assert ICTAnalyzer.is_execution_ready(mss, fvg, order_block, premium_discount, return_to_fvg=True) is True


def test_execution_rejected_when_mss_is_not_confirmed_even_if_components_detected():
    mss = {"detected": False, "direction": "bullish"}
    fvg = {"detected": True, "direction": "bullish"}
    order_block = {"detected": True, "direction": "bullish"}
    premium_discount = {
        "current_zone": "unavailable",
        "reason": "MSS not confirmed; dealing range cannot be anchored.",
    }

    readiness = ICTAnalyzer.validate_execution_readiness(
        mss,
        fvg,
        order_block,
        premium_discount,
        return_to_fvg=True,
    )

    assert readiness["execution_ready"] is False
    assert "MSS not confirmed" in readiness["rejection_reasons"]
    assert "Premium/discount unavailable" in readiness["rejection_reasons"]


def test_execution_rejected_when_fvg_direction_disagrees_with_mss():
    readiness = ICTAnalyzer.validate_execution_readiness(
        mss={"detected": True, "direction": "bearish"},
        fvg={"detected": True, "direction": "bullish"},
        order_block={"detected": True, "direction": "bearish"},
        premium_discount={"current_zone": "premium"},
        return_to_fvg=True,
    )

    assert readiness["execution_ready"] is False
    assert "FVG direction not aligned with MSS" in readiness["rejection_reasons"]


def test_execution_rejected_when_order_block_direction_disagrees_with_mss():
    readiness = ICTAnalyzer.validate_execution_readiness(
        mss={"detected": True, "direction": "bullish"},
        fvg={"detected": True, "direction": "bullish"},
        order_block={"detected": True, "direction": "bearish"},
        premium_discount={"current_zone": "discount"},
        return_to_fvg=True,
    )

    assert readiness["execution_ready"] is False
    assert "Order block direction not aligned with MSS" in readiness["rejection_reasons"]


def test_explanation_mentions_rejection_when_mss_not_confirmed():
    explanation = ICTAnalyzer.build_explanation(
        mss={"detected": False, "direction": "bearish"},
        bos={"detected": False, "direction": None},
        fvg={"detected": True, "grade": "B"},
        order_block={"detected": True},
        premium_discount={"current_zone": "unavailable"},
        return_to_fvg=True,
        rejection_reasons=["MSS not confirmed", "Premium/discount unavailable"],
    )

    assert "Execution rejected because MSS is not confirmed." in explanation
    assert any("Rejection reasons:" in line for line in explanation)


def test_fvg_returns_false_when_displacement_is_weak():
    candles = make_candles(
        highs=[100, 101, 104],
        lows=[96, 98, 102],
        opens=[98, 99, 102],
        closes=[99, 100, 103],
    )

    fvg = ICTAnalyzer.detect_fvg(candles, atr=20.0, direction="bullish")

    assert fvg["detected"] is False
