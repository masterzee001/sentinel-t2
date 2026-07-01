from __future__ import annotations

from backend.market_watch_engine.elite_validation import (
    build_elite_validation_report,
    build_market_watch_iq_v4,
    classify_edge_contribution,
    classify_micro_regime,
    edge_leak_analysis,
    elite_filter_decision,
    no_trade_score,
)
from scripts.run_backtest_365d import approved_robustness_metrics, metrics_within_tolerance, normalize_metrics
from scripts.run_market_watch_backtest import build_market_watch_report


def test_edge_leak_classification_flags_bad_accepted_loss():
    record = {
        "outcome": "LOSS",
        "rr": -1.0,
        "routing_class": "MISROUTED_TO_ICT",
        "noise_score": 72,
        "likely_draw_on_liquidity": "",
    }

    assert classify_edge_contribution(record) == "EDGE LEAK"


def test_edge_leak_analysis_counts_contributors_and_leaks():
    report = build_market_watch_report({})
    analysis = edge_leak_analysis(report["routing_forensics"]["records"])

    assert analysis["summary"]["ELITE CONTRIBUTOR"] > 0
    assert analysis["summary"]["EDGE LEAK"] > 0
    assert analysis["edge_leak_rate"] > 0


def test_no_trade_scoring_returns_no_trade_for_high_risk_context():
    result = no_trade_score(
        {
            "trend_strength": 82,
            "range_score": 74,
            "exhaustion_score": 80,
            "noise_score": 76,
            "likely_draw_on_liquidity": "",
            "sweep_detected": False,
        },
        memory_penalty=0.9,
    )

    assert result["classification"] == "NO TRADE"
    assert result["no_trade_confidence"] >= 70
    assert result["reasons"]["high_noise"] is True


def test_micro_regime_classification_splits_parent_regimes():
    assert (
        classify_micro_regime({"trend_strength": 84, "volatility_expansion": 68, "noise_score": 12, "exhaustion_score": 38})
        == "institutional_continuation"
    )
    assert (
        classify_micro_regime(
            {"sweep_detected": True, "mss_confirmed": True, "range_score": 55, "exhaustion_score": 72, "noise_score": 12}
        )
        == "true_reversal"
    )
    assert (
        classify_micro_regime({"trend_strength": 86, "volatility_expansion": 76, "exhaustion_score": 78, "noise_score": 16})
        == "exhaustion_expansion"
    )


def test_elite_filter_rejects_edge_leak_and_accepts_quality_setup():
    reject = elite_filter_decision(
        {
            "outcome": "LOSS",
            "routing_class": "MISROUTED_TO_TREND",
            "noise_score": 78,
            "confidence": 88,
            "session_quality": 42,
            "displacement_score": 40,
            "premium_discount_alignment": 35,
            "likely_draw_on_liquidity": "",
            "trend_strength": 40,
            "range_score": 75,
        }
    )
    accept = elite_filter_decision(
        {
            "outcome": "WIN",
            "routing_class": "CORRECTLY_ROUTED",
            "noise_score": 16,
            "confidence": 95,
            "session_quality": 88,
            "displacement_score": 78,
            "premium_discount_alignment": 82,
            "likely_draw_on_liquidity": "external_liquidity",
            "trend_strength": 84,
            "volatility_expansion": 72,
        }
    )

    assert reject["decision"] == "REJECT"
    assert accept["decision"] == "ACCEPT"


def test_iq_v4_scoring_contains_required_metrics():
    iq_v4 = build_market_watch_iq_v4(
        iq_v3={"routing_accuracy": 84.8},
        edge={"edge_leak_rate": 4.4},
        no_trade={"no_trade_accuracy": 92.4},
        micro={"accuracy": 91.2},
        elite_filter={"elite_filter_accuracy": 93.6},
    )

    assert iq_v4["routing_accuracy"] >= 87
    assert iq_v4["srms"] >= 94
    assert iq_v4["regime_accuracy"] >= 91
    assert iq_v4["no_trade_accuracy"] >= 92
    assert iq_v4["elite_filter_accuracy"] >= 93


def test_elite_validation_report_qualifies_without_touching_baseline():
    report = build_market_watch_report({})
    validation = build_elite_validation_report(
        records=report["routing_forensics"]["records"],
        iq_v3=report["elite_edge"]["market_watch_iq_v3"],
        before=report["market_watch_sprint5_result"],
    )

    assert validation["target_assessment"]["elite_qualified"] is True
    assert validation["after"]["pf"] >= 2.8
    assert validation["after"]["trades"] >= 150
    assert report["matches_approved_baseline"] is True
    assert metrics_within_tolerance(normalize_metrics(approved_robustness_metrics()), report["market_watch_advisory_mode"])


def test_market_watch_report_exports_sprint6_iq_v4():
    report = build_market_watch_report({})

    assert report["elite_validation"]["market_watch_iq_v4"]["elite_filter_accuracy"] >= 93
    assert report["elite_validation"]["target_assessment"]["classification"] == "ELITE QUALIFIED"
    assert report["decision"] == "ELITE QUALIFIED"
