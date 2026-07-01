from __future__ import annotations

from backend.ai_policy.ai_policy_engine import (
    AIPolicyEngine,
    build_ai_recommendations,
    false_recommendation_rate,
    pcs_strength,
)
from scripts.run_ai_policy_engine import build_ai_policy_report, load_memory


def test_policy_engine_generates_required_recommendation_types():
    report = build_ai_policy_report()
    types = {item["type"] for item in report["recommendations"]}

    assert "policy_recommendation" in types
    assert "risk_recommendation" in types
    assert "strategy_recommendation" in types
    assert "regime_recommendation" in types
    assert report["recommendation_count"] >= 8


def test_pcs_strength_bands():
    assert pcs_strength(90) == "strong"
    assert pcs_strength(75) == "medium"
    assert pcs_strength(74) == "weak"


def test_ai_identifies_good_policies_and_weak_guardrails():
    report = build_ai_policy_report()
    ids = {item["id"] for item in report["recommendations"]}

    assert "preserve_grade_lock" in ids
    assert "preserve_killzone" in ids
    assert "review_symbol_lock_top_candidate_conditions" in ids
    assert "review_no_trade_institutional_continuation" in ids


def test_top_recommendations_are_high_confidence_and_advisory_only():
    report = build_ai_policy_report()
    top = report["top_recommendations"]

    assert top
    assert top[0]["pcs"] >= 90
    assert all(item["advisory_only"] is True for item in top)
    assert all(item["production_change"] is False for item in top)


def test_false_recommendation_rate_detects_unsafe_items():
    recommendations = [
        {"advisory_only": True, "production_change": False},
        {"advisory_only": False, "production_change": False},
        {"advisory_only": True, "production_change": True},
    ]

    assert false_recommendation_rate(recommendations) == 66.67


def test_market_watch_iq_v7_scores_policy_quality():
    report = build_ai_policy_report()
    iq = report["market_watch_iq_v7"]

    assert iq["recommendation_accuracy"] == 100.0
    assert iq["false_recommendation_rate"] == 0.0
    assert iq["average_pcs"] >= 85
    assert iq["strong_recommendations"] >= 4
    assert iq["production_baseline_preserved"] is True


def test_ai_policy_memory_tracks_confirmed_and_review_queues():
    report = build_ai_policy_report()
    memory = report["ai_policy_memory"]

    assert memory["mode"] == "ADVISORY_MEMORY_ONLY"
    assert memory["confirmed_good_policies"]
    assert memory["policy_review_queue"]
    assert memory["production_logic_modified"] is False
    assert memory["autonomous_execution"] is False


def test_engine_preserves_safety_boundaries():
    report = AIPolicyEngine(load_memory()).build_report()

    assert report["production_logic_modified"] is False
    assert report["broker_order_submission"] is False
    assert report["autonomous_execution"] is False
    assert report["human_approval_bypassed"] is False
    assert report["decision"] == "PASS"


def test_build_recommendations_has_no_production_changes():
    recommendations = build_ai_recommendations(load_memory())

    assert recommendations
    assert all(item["production_change"] is False for item in recommendations)
    assert all(item["advisory_only"] is True for item in recommendations)
