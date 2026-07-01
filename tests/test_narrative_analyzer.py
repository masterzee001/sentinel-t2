from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.narrative_engine.narrative_analyzer import NarrativeAnalyzer


def make_trend(
    daily_bias: str = "bullish",
    h4_bias: str = "bullish",
    overall_bias: str = "bullish",
    h1_context: str = "expansion",
) -> dict:
    return {
        "daily_bias": daily_bias,
        "h4_bias": h4_bias,
        "overall_bias": overall_bias,
        "h1_context": h1_context,
    }


def make_liquidity(
    sweep: dict | None = None,
    buy_target: dict | None = None,
    sell_target: dict | None = None,
    equal_highs: list | None = None,
    equal_lows: list | None = None,
    current_price: float = 4150.0,
    low_atr: bool = False,
) -> dict:
    buy_target = buy_target or {
        "name": "PDH",
        "price": 4200.0,
        "classification": "external",
    }
    sell_target = sell_target or {
        "name": "PDL",
        "price": 4100.0,
        "classification": "external",
    }
    return {
        "latest_sweep": sweep,
        "current_price": current_price,
        "pdh": 4200.0,
        "pdl": 4100.0,
        "weekly_high": 4300.0,
        "weekly_low": 4050.0,
        "asian_high": 4180.0,
        "asian_low": 4120.0,
        "nearest_buy_side_target": buy_target,
        "nearest_sell_side_target": sell_target,
        "liquidity_priority": [
            {"name": buy_target["name"], "price": buy_target["price"], "side": "buy_side", "classification": "external"},
            {"name": sell_target["name"], "price": sell_target["price"], "side": "sell_side", "classification": "external"},
        ],
        "equal_highs": equal_highs or [],
        "equal_lows": equal_lows or [],
        "low_atr": low_atr,
    }


def make_ict(
    mss_detected: bool = True,
    mss_direction: str = "bullish",
    zone: str = "discount",
    displacement_score: float = 80.0,
) -> dict:
    return {
        "mss": {
            "detected": mss_detected,
            "direction": mss_direction,
            "displacement_score": displacement_score,
        },
        "premium_discount": {"current_zone": zone},
    }


def test_phase_classification_expansion():
    phase = NarrativeAnalyzer.classify_phase(
        trend=make_trend(),
        liquidity=make_liquidity(sweep={"side": "sell_side", "level_name": "PDL"}),
        ict=make_ict(mss_direction="bullish", zone="discount"),
        likely_draw="PDH at 4200.0",
    )

    assert phase == "expansion"


def test_phase_classification_reversal():
    phase = NarrativeAnalyzer.classify_phase(
        trend=make_trend(daily_bias="bearish", h4_bias="bearish", overall_bias="bearish"),
        liquidity=make_liquidity(sweep={"side": "sell_side", "level_name": "PDL"}),
        ict=make_ict(mss_direction="bullish", zone="discount"),
        likely_draw="PDH at 4200.0",
    )

    assert phase == "reversal"


def test_phase_classification_accumulation():
    phase = NarrativeAnalyzer.classify_phase(
        trend=make_trend(daily_bias="neutral", h4_bias="range", overall_bias="neutral", h1_context="consolidation"),
        liquidity=make_liquidity(sweep=None, equal_lows=[{"level": 4100.0}], low_atr=True),
        ict=make_ict(mss_detected=False, mss_direction=None, zone="unavailable", displacement_score=0.0),
    )

    assert phase == "accumulation"


def test_phase_classification_range():
    phase = NarrativeAnalyzer.classify_phase(
        trend=make_trend(daily_bias="neutral", h4_bias="range", overall_bias="neutral", h1_context="consolidation"),
        liquidity=make_liquidity(sweep=None, buy_target=None, sell_target=None),
        ict=make_ict(mss_detected=False, mss_direction=None, zone="unavailable", displacement_score=0.0),
    )

    assert phase == "range"


def test_phase_classification_distribution():
    phase = NarrativeAnalyzer.classify_phase(
        trend=make_trend(),
        liquidity=make_liquidity(sweep={"side": "buy_side", "level_name": "PDH"}),
        ict=make_ict(mss_detected=False, mss_direction="bearish", zone="premium", displacement_score=25.0),
        likely_draw="PDL at 4100.0",
    )

    assert phase == "distribution"


def test_bearish_buy_side_sweep_does_not_overclassify_as_accumulation():
    phase = NarrativeAnalyzer.classify_phase(
        trend=make_trend(daily_bias="bearish", h4_bias="range", overall_bias="bearish", h1_context="consolidation"),
        liquidity=make_liquidity(
            sweep={"side": "buy_side", "level_name": "PDH", "strength": "weak"},
            equal_highs=[{"level": 4200.0}],
            low_atr=True,
        ),
        ict=make_ict(mss_detected=False, mss_direction="bearish", zone="premium", displacement_score=0.0),
    )

    assert phase == "distribution"


def test_likely_draw_selection_uses_mss_direction():
    draw = NarrativeAnalyzer.select_likely_draw(
        trend=make_trend(overall_bias="neutral"),
        liquidity=make_liquidity(),
        ict=make_ict(mss_direction="bearish", zone="premium"),
    )

    assert draw == "PDL at 4100.0"


def test_unswept_liquidity_excludes_multi_word_swept_level():
    liquidity = {
        "current_price": 4150.0,
        "liquidity_priority": [
            {"name": "Asian High", "price": 4190.0, "side": "buy_side", "classification": "internal"},
            {"name": "PDL", "price": 4100.0, "side": "sell_side", "classification": "external"},
        ]
    }

    unswept = NarrativeAnalyzer.get_unswept_liquidity(liquidity, ["Asian High buy-side"])

    assert unswept == ["PDL sell-side"]


def test_unswept_liquidity_returns_top_three_ranked_targets():
    liquidity = {
        "current_price": 4150.0,
        "liquidity_priority": [
            {"name": "Internal Swing High", "price": 4160.0, "side": "buy_side", "classification": "internal", "importance_score": 500.0},
            {"name": "Engineered Liquidity 1", "price": 4170.0, "side": "buy_side", "classification": "engineered", "importance_score": 400.0},
            {"name": "PDL", "price": 4100.0, "side": "sell_side", "classification": "external", "importance_score": 100.0},
            {"name": "Weekly High", "price": 4300.0, "side": "buy_side", "classification": "external", "importance_score": 10.0},
            {"name": "PDH", "price": 4200.0, "side": "buy_side", "classification": "external", "importance_score": 20.0},
        ],
    }

    unswept = NarrativeAnalyzer.get_unswept_liquidity(liquidity)

    assert unswept == ["Weekly High buy-side", "PDL sell-side", "PDH buy-side"]


def test_current_zone_falls_back_to_daily_dealing_range():
    liquidity = make_liquidity(current_price=4175.0)
    ict = make_ict(mss_detected=False, zone="unavailable")

    assert NarrativeAnalyzer.get_current_zone(ict, liquidity) == "premium"


def test_summary_generation_mentions_sweep_zone_phase_and_draw():
    summary = NarrativeAnalyzer.build_summary(
        swept_liquidity=["Asian High buy-side"],
        current_zone="premium",
        phase="distribution",
        likely_draw="PDL at 4100.0",
    )

    assert summary == (
        "Buy-side liquidity has been swept and price is trading in premium. "
        "Market appears to be distributing toward sell-side liquidity with PDL at 4100.0 as the main draw."
    )


def test_no_liquidity_fallback():
    liquidity = {
        "latest_sweep": None,
        "nearest_buy_side_target": None,
        "nearest_sell_side_target": None,
        "liquidity_priority": [],
        "equal_highs": [],
        "equal_lows": [],
    }

    draw = NarrativeAnalyzer.select_likely_draw(
        trend=make_trend(overall_bias="neutral", daily_bias="neutral"),
        liquidity=liquidity,
        ict=make_ict(mss_detected=False, mss_direction=None, zone="unavailable", displacement_score=0.0),
    )
    summary = NarrativeAnalyzer.build_summary([], "unavailable", "range", draw)

    assert draw == NarrativeAnalyzer.NO_DRAW
    assert "No meaningful liquidity sweep is confirmed" in summary
    assert "until a cleaner liquidity draw forms" in summary


def test_analyze_returns_required_structure_and_session():
    analyzer = NarrativeAnalyzer(
        trend_analyzer=object(),
        liquidity_analyzer=object(),
        ict_analyzer=object(),
    )
    narrative = analyzer.analyze(
        "XAUUSD",
        context={
            "analysis_time": datetime(2026, 6, 28, 9, 30, tzinfo=ZoneInfo("Africa/Lagos")),
            "trend": make_trend(),
            "liquidity": make_liquidity(sweep={"side": "sell_side", "level_name": "PDL"}),
            "ict": make_ict(mss_direction="bullish", zone="discount"),
        },
    )

    assert set(narrative) == {
        "symbol",
        "bias",
        "phase",
        "swept_liquidity",
        "unswept_liquidity",
        "current_zone",
        "active_session",
        "likely_draw",
        "summary",
        "explanation",
    }
    assert narrative["symbol"] == "XAUUSD"
    assert narrative["active_session"] == "london"
    assert narrative["phase"] in NarrativeAnalyzer.ALLOWED_PHASES
