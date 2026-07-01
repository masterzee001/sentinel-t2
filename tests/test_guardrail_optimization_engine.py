from __future__ import annotations

from backend.guardrail_optimization.guardrail_optimization_engine import (
    GuardrailOptimizationEngine,
    conditional_relaxation_simulator,
    guardrail_attribution,
    guardrail_leak_analysis,
)
from backend.shadow_learning.shadow_learning_engine import ShadowLearningEngine
from scripts.run_guardrail_optimization_engine import build_guardrail_optimization_report


def classified_shadow_rows():
    return ShadowLearningEngine().build_report()["classified_outcomes"]


def test_guardrail_attribution_contains_required_block_fields():
    attribution = guardrail_attribution(classified_shadow_rows())
    first = attribution[0]

    assert attribution
    assert first["symbol"]
    assert first["strategy"]
    assert first["regime"]
    assert first["micro_regime"]
    assert first["grade"]
    assert "confidence" in first
    assert first["block_stage"] in {
        "killzone",
        "symbol_lock",
        "grade_lock",
        "no_trade",
        "risk_lock",
        "late_execution_guardrail",
        "memory_penalty",
    }
    assert first["guardrail"]
    assert first["block_reason"]
    assert first["production_metrics_affected"] is False


def test_leak_scoring_ranks_best_and_worst_guardrails():
    leak = guardrail_leak_analysis(guardrail_attribution(classified_shadow_rows()))

    assert leak["best_guardrail"] in {"killzone", "grade_lock"}
    assert leak["worst_guardrail"] == "symbol_lock"
    assert leak["by_guardrail"]["symbol_lock"]["leak_ratio"] > leak["by_guardrail"]["killzone"]["leak_ratio"]
    assert leak["ranked_best_to_worst"][0]["leak_ratio"] == 0.0


def test_relaxation_simulation_preserves_elite_thresholds():
    scenarios = conditional_relaxation_simulator(guardrail_attribution(classified_shadow_rows()))

    for scenario in scenarios.values():
        metrics = scenario["metrics"]
        assert metrics["pf"] >= 2.8
        assert metrics["win_rate"] >= 72.0
        assert metrics["max_drawdown"] < 4.0
        assert scenario["production_rule_change"] is False


def test_combined_controlled_relaxation_reaches_preferred_trade_window():
    report = build_guardrail_optimization_report()
    metrics = report["conditional_relaxation"]["scenario_4_combined_controlled_relaxation"]["metrics"]

    assert metrics["pf"] >= 2.9
    assert 160 <= metrics["trades"] <= 170
    assert metrics["max_drawdown"] <= 4.0


def test_symbol_optimization_ranks_candidates_from_data():
    report = build_guardrail_optimization_report()
    symbol = report["symbol_lock_optimization"]
    ranked = sorted(symbol.items(), key=lambda item: item[1]["rank"])

    assert ranked[0][1]["conditional_unlock_candidate"] is True
    assert ranked[0][1]["best_strategy"] == "trend_following"
    assert ranked[0][1]["best_micro_regime"] == "institutional_continuation"
    assert {data["rank"] for data in symbol.values()} == {1, 2, 3, 4}


def test_no_trade_optimization_finds_institutional_continuation_leaks():
    report = build_guardrail_optimization_report()
    no_trade = report["no_trade_optimization"]

    assert no_trade["pass"] is True
    assert no_trade["institutional_continuation_leaks"] >= 1
    assert no_trade["strong_continuation_or_displacement_leaks"] >= 1


def test_a_plus_override_simulation_improves_trade_count_safely():
    report = build_guardrail_optimization_report()
    override = report["a_plus_override_simulation"]

    assert override["pass"] is True
    assert override["improves_trade_count"] is True
    assert override["metrics"]["pf"] >= 2.8
    assert override["metrics"]["max_drawdown"] < 4.0


def test_market_watch_iq_v6_contains_safe_candidates():
    report = build_guardrail_optimization_report()
    iq = report["market_watch_iq_v6"]

    assert iq["guardrail_leak_iq"] > 0
    assert iq["guardrail_efficiency_score"] > 0
    assert iq["relaxation_benefit_score"] > 0
    assert iq["conditional_unlock_score"] > 0
    assert iq["late_block_severity"] > 0
    assert "conditional_symbol_lock_review" in iq["safe_relaxation_candidates"]
    assert "a_plus_override_review" in iq["safe_relaxation_candidates"]


def test_production_baseline_preservation_and_advisory_mode():
    report = build_guardrail_optimization_report()

    assert report["original_elite"] == {"pf": 2.84, "win_rate": 72.6, "trades": 151, "max_drawdown": 3.72}
    assert report["production_baseline_preserved"] is True
    assert report["production_metrics_affected"] is False
    assert report["live_rules_modified"] is False
    assert report["broker_order_submission"] is False
    assert report["autonomous_execution"] is False
    assert report["decision"] == "PASS"
