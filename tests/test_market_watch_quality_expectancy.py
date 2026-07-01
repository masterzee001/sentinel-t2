from __future__ import annotations

from backend.market_watch_engine.quality_expectancy import (
    GradeAwareRouter,
    SetupExpectancyEngine,
    StrategyQualityGrader,
    grade_performance_correlation,
    quality_distribution,
    setup_expectancy_database,
    severity_weighted_memory_score,
)
from scripts.run_backtest_365d import approved_robustness_metrics, metrics_within_tolerance, normalize_metrics
from scripts.run_market_watch_backtest import build_market_watch_report, build_setup_expectancy_records


def test_quality_grading_engine_returns_institutional_grade():
    grader = StrategyQualityGrader()

    result = grader.grade_trend(
        {"trend_following": 91},
        {"trend_strength": 88, "volatility_expansion": 76, "exhaustion_score": 20, "noise_score": 10},
        {"session_quality": 90},
        {"htf_alignment": 86, "pullback_quality": 82, "momentum_persistence": 84, "continuation_target_clarity": 88},
    )

    assert result["grade"] in {"A+", "A"}
    assert result["quality_score"] >= 72


def test_grade_aware_routing_prefers_better_expectancy_over_raw_score():
    router = GradeAwareRouter(SetupExpectancyEngine())
    quality = {
        "ict_liquidity": {"grade": "B", "quality_score": 65},
        "trend_following": {"grade": "A+", "quality_score": 88},
        "mean_reversion": {"grade": "A", "quality_score": 76},
    }

    result = router.select(
        scores={"ict_liquidity": 92, "trend_following": 84, "mean_reversion": 78},
        quality=quality,
        symbol="NAS100",
        session="new_york_continuation",
        pattern="trend_continuation",
    )

    assert result["selected_strategy"] == "trend_following"
    assert result["selected_grade"] == "A+"


def test_expectancy_lookup_returns_grade_adjusted_edge():
    expectancy = SetupExpectancyEngine()

    high = expectancy.lookup(strategy="trend_following", grade="A+", symbol="NAS100", session="new_york_continuation", pattern="trend_continuation")
    low = expectancy.lookup(strategy="trend_following", grade="C", symbol="NAS100", session="new_york_continuation", pattern="trend_continuation")

    assert high["pf"] > low["pf"]
    assert high["edge_score"] > low["edge_score"]


def test_expectancy_lookup_has_no_hardcoded_symbol_multiplier():
    expectancy = SetupExpectancyEngine()

    nas100 = expectancy.lookup(strategy="trend_following", grade="A+", symbol="NAS100", session="new_york_continuation", pattern="trend_continuation")
    eurusd = expectancy.lookup(strategy="trend_following", grade="A+", symbol="EURUSD", session="new_york_continuation", pattern="trend_continuation")

    assert nas100["multiplier"] == eurusd["multiplier"]
    assert nas100["edge_score"] == eurusd["edge_score"]


def test_quality_distribution_and_correlation_are_monotonic():
    records = build_setup_expectancy_records([])
    database = setup_expectancy_database(records)

    distribution = quality_distribution(records)
    correlation = grade_performance_correlation(database)

    assert distribution["trend_following"]["A+"] > 0
    assert correlation["monotonic"] is True
    assert correlation["pf_by_grade"]["A+"] > correlation["pf_by_grade"]["C"]


def test_severity_weighted_memory_scoring():
    result = severity_weighted_memory_score(
        {"ict_liquidity": {"bad-condition": 3}},
        {"bad-condition": 0.2},
        opportunities=30,
    )

    assert result["value"] < 100
    assert result["classification"] in {"GOOD", "STRONG", "ELITE"}


def test_quality_report_preserves_advisory_baseline():
    report = build_market_watch_report({})

    assert report["matches_approved_baseline"] is True
    assert metrics_within_tolerance(normalize_metrics(approved_robustness_metrics()), report["market_watch_advisory_mode"])
    assert report["quality_report"]["grade_performance_correlation"]["monotonic"] is True
    assert report["market_watch_iq"]["market_watch_iq_v2"]["quality_grading_accuracy"] >= 90
