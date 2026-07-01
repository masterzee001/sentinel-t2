from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backend.liquidity_engine.liquidity_analyzer import LiquidityAnalyzer


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


def test_previous_day_levels_use_previous_completed_daily_candle():
    daily = make_candles(
        highs=[100.0, 110.0, 120.0],
        lows=[90.0, 95.0, 105.0],
        closes=[95.0, 100.0, 115.0],
        start="2026-06-24 00:00:00+00:00",
        freq="1D",
    )

    pdh, pdl = LiquidityAnalyzer.get_previous_day_levels(daily)

    assert pdh == 110.0
    assert pdl == 95.0


def test_previous_day_snapshot_includes_debug_time_high_low():
    daily = make_candles(
        highs=[100.0, 110.0, 120.0],
        lows=[90.0, 95.0, 105.0],
        closes=[95.0, 100.0, 115.0],
        start="2026-06-24 00:00:00+00:00",
        freq="1D",
    )

    snapshot = LiquidityAnalyzer.get_previous_day_snapshot(daily)

    assert snapshot == {
        "time": "2026-06-25 00:00:00+00:00",
        "high": 110.0,
        "low": 95.0,
    }


def test_previous_week_levels_use_completed_prior_week():
    weekly = make_candles(
        highs=[100.0, 130.0, 145.0],
        lows=[90.0, 88.0, 108.0],
        closes=[95.0, 125.0, 140.0],
        start="2026-06-08 00:00:00+00:00",
        freq="1W-MON",
    )

    weekly_high, weekly_low = LiquidityAnalyzer.get_previous_week_levels(weekly)

    assert weekly_high == 130.0
    assert weekly_low == 88.0


def test_previous_week_snapshot_includes_debug_time_high_low():
    weekly = make_candles(
        highs=[100.0, 130.0, 145.0],
        lows=[90.0, 88.0, 108.0],
        closes=[95.0, 125.0, 140.0],
        start="2026-06-08 00:00:00+00:00",
        freq="1W-MON",
    )

    snapshot = LiquidityAnalyzer.get_previous_week_snapshot(weekly)

    assert snapshot == {
        "time": "2026-06-15 00:00:00+00:00",
        "high": 130.0,
        "low": 88.0,
    }


class FakeLiquidityConnector:
    def __init__(self):
        self.timeframes = []

    def get_historical_candles(self, symbol, timeframe, count):
        self.timeframes.append(timeframe)
        if timeframe == "D1":
            return make_candles(
                highs=[3900.0, 4044.22, 4200.0],
                lows=[3800.0, 3950.0, 4100.0],
                closes=[3850.0, 4000.0, 4150.0],
                start="2026-06-24 00:00:00+00:00",
                freq="1D",
            )
        if timeframe == "W1":
            return make_candles(
                highs=[3900.0, 4300.0, 4500.0],
                lows=[3600.0, 3500.0, 4121.68],
                closes=[3800.0, 4200.0, 4400.0],
                start="2026-06-08 00:00:00+00:00",
                freq="1W-MON",
            )
        if timeframe == "H1":
            highs = [4000.0 + index for index in range(30)]
            lows = [high - 10.0 for high in highs]
            closes = [high - 4.0 for high in highs]
            return make_candles(highs, lows, closes=closes, start="2026-06-25 00:00:00+00:00", freq="1h")
        if timeframe == "M15":
            highs = [4000.0 + index * 0.5 for index in range(96)]
            lows = [high - 8.0 for high in highs]
            closes = [high - 3.0 for high in highs]
            return make_candles(highs, lows, closes=closes, start="2026-06-25 23:00:00+00:00")
        raise AssertionError(f"Unexpected timeframe: {timeframe}")


def test_analyze_uses_correct_timeframes_and_previous_completed_candles():
    connector = FakeLiquidityConnector()
    analyzer = LiquidityAnalyzer(connector=connector)

    result = analyzer.analyze("XAUUSD")

    assert connector.timeframes == ["D1", "W1", "H1", "M15"]
    assert result["pdh"] == 4044.22
    assert result["pdl"] == 3950.0
    assert result["weekly_high"] == 4300.0
    assert result["weekly_low"] == 3500.0
    assert result["debug_metadata"] == {
        "previous_daily_time": "2026-06-25 00:00:00+00:00",
        "previous_daily_high": 4044.22,
        "previous_daily_low": 3950.0,
        "previous_weekly_time": "2026-06-15 00:00:00+00:00",
        "previous_weekly_high": 4300.0,
        "previous_weekly_low": 3500.0,
    }


def test_asian_range_uses_midnight_to_0700_wat():
    candles = make_candles(
        highs=[100 + index for index in range(40)],
        lows=[90 + index for index in range(40)],
        start="2026-06-25 23:00:00+00:00",
    )

    asian_range = LiquidityAnalyzer.get_asian_range(candles)

    assert asian_range["timezone"] == "WAT"
    assert asian_range["high"] == 127.0
    assert asian_range["low"] == 90.0


def test_calculate_atr_from_recent_candles():
    candles = make_candles(
        highs=[12.0, 13.0, 14.0, 15.0],
        lows=[10.0, 11.0, 11.0, 13.0],
        closes=[11.0, 12.0, 13.0, 14.0],
    )

    assert LiquidityAnalyzer.calculate_atr(candles, period=3) == pytest.approx(2.3333333)


def test_detect_equal_highs_and_lows_with_atr_threshold():
    candles = make_candles(
        highs=[
            10, 15.00, 12, 13, 12, 11,
            10, 15.05, 12, 13, 12, 11,
            10, 15.02, 12, 18, 13, 18.04,
            14, 18.02, 13,
        ],
        lows=[
            5, 8, 4.00, 8, 7, 6,
            5, 8, 6, 8, 4.04, 6,
            5, 8, 6, 9, 3.98, 9,
            6, 9, 6,
        ],
    )

    equal_highs = LiquidityAnalyzer.detect_equal_highs(candles, threshold=0.10)
    equal_lows = LiquidityAnalyzer.detect_equal_lows(candles, threshold=0.10)

    assert equal_highs[0]["touches"] == 3
    assert equal_highs[0]["level"] == pytest.approx(15.02333)
    assert len(equal_highs) <= 3
    assert equal_lows[0]["touches"] == 3
    assert len(equal_lows) <= 3


def test_equal_highs_require_three_touches_and_spacing():
    candles = make_candles(
        highs=[10, 15.00, 12, 15.03, 12, 11, 10, 15.02, 12],
        lows=[5, 8, 6, 8, 6, 5, 5, 8, 6],
    )

    assert LiquidityAnalyzer.detect_equal_highs(candles, threshold=0.10) == []


def test_internal_swings_are_ranked_and_limited_to_top_five_each():
    highs = [10, 15, 11, 18, 12, 17, 10, 21, 13, 16, 11, 20, 12, 19, 11, 22, 10]
    lows = [5, 8, 4, 9, 5, 8, 3, 10, 6, 8, 4, 11, 5, 9, 2, 10, 5]
    candles = make_candles(highs=highs, lows=lows)

    internal_swings = LiquidityAnalyzer.get_internal_swings(candles)

    assert len(internal_swings["swing_highs"]) <= 5
    assert len(internal_swings["swing_lows"]) <= 5
    assert internal_swings["swing_highs"][0]["significance"] >= internal_swings["swing_highs"][-1]["significance"]
    assert internal_swings["swing_lows"][0]["significance"] >= internal_swings["swing_lows"][-1]["significance"]


def test_classify_liquidity_groups_external_internal_and_engineered():
    classification = LiquidityAnalyzer.classify_liquidity(
        pdh=110.0,
        pdl=95.0,
        weekly_high=130.0,
        weekly_low=88.0,
        internal_swings={
            "swing_highs": [{"index": 1, "position": 1, "price": 108.0, "significance": 12.0}],
            "swing_lows": [{"index": 2, "position": 2, "price": 96.0, "significance": 10.0}],
        },
        equal_highs=[{"level": 120.0, "touches": 3, "strength_score": 92.0}],
        equal_lows=[{"level": 90.0, "touches": 3, "strength_score": 88.0}],
    )

    assert [level["name"] for level in classification["external"]] == [
        "PDH",
        "PDL",
        "Weekly High",
        "Weekly Low",
    ]
    assert len(classification["internal"]) == 2
    assert any("equal_highs" in level["subtypes"] for level in classification["engineered"])
    assert any("clustered_liquidity" in level["subtypes"] for level in classification["engineered"])
    assert len(classification["engineered"]) == 2


def test_rank_liquidity_targets_uses_class_hierarchy_weights():
    classification = {
        "external": [{"name": "PDH", "price": 110.0, "side": "buy_side", "classification": "external"}],
        "internal": [
            {
                "name": "Internal Swing High",
                "price": 106.0,
                "side": "buy_side",
                "classification": "internal",
                "significance": 500.0,
            }
        ],
        "engineered": [
            {
                "name": "EQH 1",
                "price": 108.0,
                "side": "buy_side",
                "classification": "engineered",
                "touches": 4,
                "strength_score": 100.0,
                "subtypes": ["equal_highs", "clustered_liquidity"],
            }
        ],
    }

    ranked = LiquidityAnalyzer.rank_liquidity_targets(classification, current_price=105.0)
    by_name = {target["name"]: target for target in ranked}

    assert ranked[0]["importance_score"] >= ranked[-1]["importance_score"]
    assert by_name["PDH"]["importance_score"] > by_name["Internal Swing High"]["importance_score"]
    assert {"name", "price", "side", "classification", "importance_score", "distance_from_current_price"} <= set(ranked[0])


def test_rank_liquidity_targets_filters_market_targets_too_close_to_price():
    classification = {
        "external": [
            {"name": "Near PDH", "price": 108.0, "side": "buy_side", "classification": "external"},
            {"name": "Far Weekly High", "price": 115.0, "side": "buy_side", "classification": "external"},
        ],
        "internal": [],
        "engineered": [],
    }

    xauusd_ranked = LiquidityAnalyzer.rank_liquidity_targets(
        classification,
        current_price=105.0,
        symbol="XAUUSD",
    )
    us30_ranked = LiquidityAnalyzer.rank_liquidity_targets(
        classification,
        current_price=105.0,
        symbol="US30",
    )

    assert [target["name"] for target in xauusd_ranked] == ["Far Weekly High"]
    assert us30_ranked == []


def test_infer_directional_targets_returns_nearest_buy_and_sell_side_targets():
    liquidity_priority = [
        {
            "name": "Weekly High",
            "price": 130.0,
            "side": "buy_side",
            "classification": "external",
            "importance_score": 155.0,
            "distance_from_current_price": 25.0,
        },
        {
            "name": "Engineered Liquidity 1",
            "price": 108.0,
            "side": "buy_side",
            "classification": "engineered",
            "importance_score": 140.0,
            "distance_from_current_price": 3.0,
        },
        {
            "name": "PDL",
            "price": 95.0,
            "side": "sell_side",
            "classification": "external",
            "importance_score": 150.0,
            "distance_from_current_price": 10.0,
        },
    ]

    targets = LiquidityAnalyzer.infer_directional_targets(liquidity_priority, current_price=105.0)

    assert targets["nearest_buy_side_target"] == {
        "name": "Engineered Liquidity 1",
        "price": 108.0,
        "distance": 3.0,
        "classification": "engineered",
        "importance_score": 140.0,
    }
    assert targets["nearest_sell_side_target"]["name"] == "PDL"


def test_detect_buy_side_sweep_and_strong_displacement():
    candles = make_candles(
        highs=[99.0, 100.0, 102.0, 98.0],
        lows=[95.0, 96.0, 94.0, 90.0],
        opens=[97.0, 98.0, 101.0, 96.0],
        closes=[98.0, 99.0, 96.0, 91.0],
    )
    levels = [{"name": "PDH", "price": 100.0, "side": "buy_side"}]

    sweep = LiquidityAnalyzer.detect_latest_sweep(candles, levels, atr=4.0)

    assert sweep is not None
    assert {"level_name", "side", "strength", "rejection_size", "displacement_score"} <= set(sweep)
    assert sweep["side"] == "buy_side"
    assert sweep["strength"] == "strong"
    assert sweep["rejection_size"] == 6.0
    assert sweep["displacement_score"] > 0


def test_detect_sell_side_sweep():
    candles = make_candles(
        highs=[105.0, 104.0, 110.0],
        lows=[101.0, 99.0, 102.0],
        opens=[103.0, 100.0, 103.0],
        closes=[102.0, 103.0, 109.0],
    )
    levels = [{"name": "PDL", "price": 100.0, "side": "sell_side"}]

    sweep = LiquidityAnalyzer.detect_latest_sweep(candles, levels, atr=4.0)

    assert sweep is not None
    assert sweep["type"] == "sell_side_sweep"
    assert sweep["level_name"] == "PDL"


def test_symbol_validation_uses_config(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "trading_rules.yaml").write_text(
        """
markets:
  allowed:
    - "US30"
""",
        encoding="utf-8",
    )
    analyzer = LiquidityAnalyzer(connector=object(), config_dir=config_dir)

    assert analyzer._validate_symbol("us30") == "US30"
    with pytest.raises(ValueError, match="Unsupported symbol"):
        analyzer._validate_symbol("XAUUSD")
