from __future__ import annotations

from backend.shadow_learning.shadow_learning_engine import (
    SHADOW_BLOCK_TEMPLATES,
    SYMBOL_UNIVERSE,
    ShadowLearningEngine,
    classify_block_quality,
    default_shadow_setups,
    shadow_setup,
)
from scripts.run_shadow_learning_engine import build_shadow_learning_report, shadow_learning_passed


def custom_setups():
    setups = default_shadow_setups()
    setups[0]["block_reason"] = "killzone"
    setups[0]["simulated_rr_hint"] = -1.0
    setups[1]["block_reason"] = "killzone"
    setups[1]["simulated_rr_hint"] = 1.4
    setups[2]["symbol"] = "NAS100"
    setups[2]["block_reason"] = "symbol"
    setups[2]["simulated_rr_hint"] = 1.7
    return setups


def test_blocked_setup_capture_contains_required_fields():
    setups = ShadowLearningEngine(setups=custom_setups()).capture_blocked_setups(timestamp="2026-06-29T00:00:00+00:00")

    first = setups[0]
    assert first["timestamp"]
    assert first["symbol"] == "XAUUSD"
    assert first["strategy_candidate"]
    assert first["confidence_band"]
    assert first["grade"]
    assert first["regime"]
    assert first["micro_regime"]
    assert first["block_reason"] == "killzone"
    assert first["entry_reference_price"] > 0
    assert first["proposed_sl"] > 0
    assert first["proposed_tp"] > 0
    assert first["spread"] >= 0
    assert first["session"]
    assert first["expected_rr"] > 0
    assert first["execution_allowed"] is False


def test_killzone_block_shadow_simulation_classifies_outcome():
    engine = ShadowLearningEngine(setups=custom_setups())
    setups = engine.capture_blocked_setups()
    outcomes = engine.simulate_outcomes(setups)

    killzone_outcomes = [item for item in outcomes if item["guardrail"] == "killzone"]
    assert {item["block_quality"] for item in killzone_outcomes} >= {"GOOD_BLOCK", "BAD_BLOCK"}
    assert all(item["broker_order_submitted"] is False for item in outcomes)


def test_observer_symbol_shadow_capture_without_execution():
    setups = ShadowLearningEngine(setups=custom_setups()).capture_blocked_setups()
    nas100 = next(item for item in setups if item["symbol"] == "NAS100")

    assert nas100["observer_symbol"] is True
    assert nas100["execution_allowed"] is False
    assert nas100["approval_queue_created"] is False
    assert nas100["paper_trade_created"] is False
    assert nas100["broker_order_submitted"] is False


def test_shadow_learning_creates_no_approval_paper_or_broker_path():
    report = ShadowLearningEngine(setups=custom_setups()).build_report()

    safety = report["execution_safety"]
    assert safety["approval_queue"] is True
    assert safety["paper_runtime"] is True
    assert safety["broker_adapter"] is True
    assert safety["production_metrics"] is True
    assert report["production_metrics_affected"] is False


def test_block_quality_classification_good_bad_neutral_and_insufficient():
    assert classify_block_quality(-1.0) == "GOOD_BLOCK"
    assert classify_block_quality(1.2) == "BAD_BLOCK"
    assert classify_block_quality(0.2) == "NEUTRAL_BLOCK"
    assert classify_block_quality(None) == "INSUFFICIENT_DATA"


def test_guardrail_iq_scoring_reports_leak_and_prevented_loss():
    report = ShadowLearningEngine(setups=custom_setups()).build_report()
    killzone = report["guardrail_iq_report"]["killzone"]

    assert killzone["total_blocks"] >= 2
    assert killzone["good_blocks"] >= 1
    assert killzone["bad_blocks"] >= 1
    assert killzone["prevented_loss_value"] > 0
    assert killzone["leaked_profit_value"] > 0


def test_opportunity_leak_detection_groups_bad_blocks():
    report = ShadowLearningEngine(setups=custom_setups()).build_report()
    leak = report["opportunity_leak_analysis"]

    assert leak["bad_blocks"] > 0
    assert leak["opportunity_leak_rate"] > 0
    assert leak["groups"]["guardrail"]
    assert leak["top_leak_sources"]


def test_shadow_memory_flags_confirmed_and_policy_review_candidates():
    report = ShadowLearningEngine().build_report()
    memory = report["shadow_learning_memory"]

    assert memory["guardrail_confirmed"]
    assert memory["policy_review_candidates"]


def test_market_watch_iq_v5_contains_shadow_metrics():
    report = ShadowLearningEngine().build_report()
    v5 = report["market_watch_iq_v5"]

    assert v5["guardrail_iq"] > 0
    assert v5["opportunity_leak_rate"] > 0
    assert v5["shadow_simulation_accuracy"] == 100.0
    assert v5["block_decision_accuracy"] > 0
    assert v5["policy_review_candidates"] >= 1


def test_shadow_backtest_validation_is_reported_separately():
    report = build_shadow_learning_report()
    comparison = report["shadow_enhanced_comparison"]

    assert comparison["original_elite"] == {"pf": 2.84, "win_rate": 72.6, "trades": 151, "max_drawdown": 3.72}
    assert comparison["reported_separately"] is True
    assert comparison["does_not_replace_production_metrics"] is True
    assert comparison["production_baseline_preserved"] is True
    assert report["production_baseline_preserved"] is True


def test_shadow_enhanced_hypothetical_meets_validation_targets():
    report = build_shadow_learning_report()
    comparison = report["shadow_enhanced_comparison"]
    enhanced = comparison["shadow_enhanced"]

    assert enhanced["pf"] >= 2.8
    assert enhanced["win_rate"] >= 72.0
    assert enhanced["trades"] >= 160
    assert enhanced["max_drawdown"] < 4.0
    assert comparison["trade_count_delta"] > 0
    assert comparison["decision"] == "SHADOW VALIDATED"


def test_production_baseline_preservation():
    report = build_shadow_learning_report()

    assert report["production_baseline_preserved"] is True
    assert report["production_impact"] is False
    assert report["approved_baseline"]["profit_factor"] == 1.58
    assert report["approved_baseline"]["win_rate"] == 58.7
    assert report["approved_baseline"]["trades_approved"] == 56
    assert report["approved_baseline"]["max_drawdown"] == 2.97
    assert shadow_learning_passed(report) is True


def test_shadow_capture_is_symbol_agnostic_across_universe():
    report = ShadowLearningEngine().build_report()
    distribution = report["symbol_distribution"]

    assert set(distribution) == set(SYMBOL_UNIVERSE)
    assert set(distribution.values()) == {len(SHADOW_BLOCK_TEMPLATES)}


def test_shadow_capture_includes_all_block_reasons_for_each_symbol():
    setups = ShadowLearningEngine().capture_blocked_setups()
    expected_reasons = {str(template["block_reason"]) for template in SHADOW_BLOCK_TEMPLATES}

    for symbol in SYMBOL_UNIVERSE:
        reasons = {setup["block_reason"] for setup in setups if setup["symbol"] == symbol}
        assert reasons == expected_reasons


def test_shadow_simulator_is_not_symbol_biased_for_same_setup_attributes():
    setup_a = shadow_setup("XAUUSD", "trend_following", "A+", 95, "symbol_lock", "new_york_open", 1.25, "institutional_continuation", "test")
    setup_b = shadow_setup("NAS100", "trend_following", "A+", 95, "symbol_lock", "new_york_open", 1.25, "institutional_continuation", "test")
    engine = ShadowLearningEngine(setups=[setup_a, setup_b])
    outcomes = engine.simulate_outcomes(engine.capture_blocked_setups())

    assert outcomes[0]["rr_outcome"] == outcomes[1]["rr_outcome"]
    assert outcomes[0]["block_quality"] == outcomes[1]["block_quality"]
