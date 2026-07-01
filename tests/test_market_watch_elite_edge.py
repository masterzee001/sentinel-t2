from __future__ import annotations

from backend.market_watch_engine.elite_edge import (
    build_elite_edge_report,
    build_loss_memory_database,
    build_market_watch_iq_v3,
    classify_regime,
    ict_forensics,
    refined_ict_grade,
    regime_confusion_analysis,
    regime_expectancy_lookup,
    severity_weighted_memory_v2,
)
from scripts.run_backtest_365d import approved_robustness_metrics, metrics_within_tolerance, normalize_metrics
from scripts.run_market_watch_backtest import build_market_watch_report


def test_ict_grade_refinement_rejects_weak_liquidity_setup():
    weak_record = {
        "selected_strategy": "ict_liquidity",
        "sweep_detected": True,
        "mss_confirmed": False,
        "displacement_score": 48,
        "noise_score": 22,
        "likely_draw_on_liquidity": "",
    }
    strong_record = {
        "selected_strategy": "ict_liquidity",
        "sweep_detected": True,
        "mss_confirmed": True,
        "displacement_score": 78,
        "noise_score": 18,
        "likely_draw_on_liquidity": "external_liquidity",
        "fvg_detected": True,
        "premium_discount_alignment": 78,
        "smt_present": True,
        "session_quality": 82,
    }

    assert refined_ict_grade(weak_record) == "REJECT"
    assert refined_ict_grade(strong_record) == "A+"


def test_ict_forensics_prunes_low_quality_b_c_distribution():
    report = build_market_watch_report({})
    ict = ict_forensics(report["routing_forensics"]["records"])

    assert ict["original_distribution"]["B"] == 10
    assert ict["refined_distribution"]["B"] < ict["original_distribution"]["B"]
    assert ict["refined_distribution"]["C"] < ict["original_distribution"]["C"]
    assert ict["refined_distribution"]["REJECT"] > 0


def test_loss_memory_database_contains_required_condition_fields():
    report = build_market_watch_report({})
    memory = build_loss_memory_database(report["routing_forensics"]["records"])

    assert memory["conditions"]
    required = {
        "strategy",
        "symbol",
        "session",
        "pattern",
        "regime",
        "quality_grade",
        "loss_severity",
        "rr_impact",
        "repeat_count",
    }
    assert required.issubset(memory["conditions"][0])


def test_severity_weighted_memory_v2_clears_target_srms():
    report = build_market_watch_report({})
    memory_database = build_loss_memory_database(report["routing_forensics"]["records"])
    memory = severity_weighted_memory_v2(memory_database, opportunities=42)

    assert memory["repeated_mistakes"] < 10
    assert memory["srms"] >= 90
    assert memory["severity_memory_score"] >= 90


def test_regime_classification_taxonomy_examples():
    assert classify_regime({"trend_strength": 84, "volatility_expansion": 72, "noise_score": 10}) == "expansion_impulse"
    assert classify_regime({"noise_score": 76, "range_score": 70}) == "noisy_chop"
    assert (
        classify_regime({"sweep_detected": True, "mss_confirmed": True, "range_score": 52, "noise_score": 12})
        == "sweep_reversal"
    )


def test_regime_expectancy_lookup_maps_best_strategy():
    trend = regime_expectancy_lookup("healthy_continuation_trend")
    chop = regime_expectancy_lookup("noisy_chop")

    assert trend["best_strategy"] == "trend_following"
    assert trend["pf"] > 2.5
    assert chop["best_strategy"] == "no_trade"
    assert chop["pf"] == 0.0


def test_regime_confusion_detection_returns_accuracy_and_pairs():
    report = build_market_watch_report({})
    confusion = regime_confusion_analysis(report["routing_forensics"]["records"])

    assert confusion["regime_classification_accuracy"] >= 85
    assert confusion["regime_confusion_rate"] < 15
    assert confusion["confusion_pairs"]


def test_iq_v3_scoring_includes_sprint5_metrics():
    iq_v3 = build_market_watch_iq_v3(
        iq_v2={"quality_grading_accuracy": 91.4, "expectancy_alignment": 92.6},
        memory={"repeated_mistakes": 8.6, "srms": 91.4, "srms_classification": "STRONG"},
        regime={"regime_classification_accuracy": 88.6, "regime_confusion_rate": 11.4},
        ict={"ict_grading_accuracy": 89.4},
    )

    assert iq_v3["repeated_mistakes"] < 10
    assert iq_v3["srms"] >= 90
    assert iq_v3["regime_classification_accuracy"] >= 85
    assert iq_v3["ict_grading_accuracy"] >= 85


def test_elite_edge_report_is_advisory_and_preserves_baseline():
    report = build_market_watch_report({})
    elite_edge = report["elite_edge"]

    assert report["matches_approved_baseline"] is True
    assert metrics_within_tolerance(normalize_metrics(approved_robustness_metrics()), report["market_watch_advisory_mode"])
    assert elite_edge["target_assessment"]["strong_pass"] is True
    assert elite_edge["after"]["pf"] >= 2.55
    assert report["market_watch_advisory_mode"] == report["approved_baseline"]


def test_build_elite_edge_report_exports_iq_v3_and_memory_database():
    report = build_market_watch_report({})
    elite = build_elite_edge_report(
        records=report["routing_forensics"]["records"],
        iq_v2=report["market_watch_iq"]["market_watch_iq_v2"],
        before=report["market_watch_stage2_result"],
        opportunities=42,
    )

    assert elite["loss_memory_database"]["conditions"]
    assert elite["market_watch_iq_v3"]["quality_accuracy"] >= 93.8
    assert elite["regime_intelligence_v2"]["strategy_expectancy"]["healthy_continuation_trend"]["best_strategy"] == "trend_following"
