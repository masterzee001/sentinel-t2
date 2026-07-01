from __future__ import annotations

from backend.a_plus_override.a_plus_override_engine import (
    APlusOverrideEngine,
    classify_block_severity,
    override_eligible,
    override_success,
)
from scripts.run_a_plus_override_engine import build_a_plus_override_report


def test_severity_classification_examples():
    assert classify_block_severity({"block_reason": "kill_switch"}) == "CRITICAL"
    assert classify_block_severity({"block_reason": "execution_anomaly"}) == "CRITICAL"
    assert classify_block_severity({"block_reason": "risk_lock"}) == "STRONG"
    assert classify_block_severity({"block_reason": "no_trade"}) == "MEDIUM"
    assert classify_block_severity({"block_reason": "secondary_noise"}) == "WEAK"


def test_override_eligibility_requires_a_plus_confidence_and_execution_ready():
    assert override_eligible({"grade": "A+", "confidence": 90, "execution_ready": True}) is True
    assert override_eligible({"grade": "A", "confidence": 95, "execution_ready": True}) is False
    assert override_eligible({"grade": "A+", "confidence": 89, "execution_ready": True}) is False
    assert override_eligible({"grade": "A+", "confidence": 95, "execution_ready": False}) is False


def test_override_denied_on_critical_block():
    report = APlusOverrideEngine(
        attribution=[
            {
                "shadow_setup_id": "T1",
                "symbol": "XAUUSD",
                "guardrail": "execution_anomaly",
                "block_reason": "execution_anomaly",
                "block_stage": "late_execution_guardrail",
                "grade": "A+",
                "confidence": 99,
                "execution_ready": True,
                "block_quality": "BAD_BLOCK",
                "rr_outcome": 2.0,
            }
        ]
    ).build_report()

    decision = report["override_decisions"][0]
    assert decision["severity"] == "CRITICAL"
    assert decision["override_decision"] == "OVERRIDE_DENIED"


def test_override_simulation_meets_success_criteria():
    report = build_a_plus_override_report()
    backtest = report["a_plus_override_backtest"]
    enhanced = backtest["override_enhanced"]

    assert backtest["recovered_trades"] >= 7
    assert enhanced["pf"] >= 2.9
    assert enhanced["win_rate"] >= 73
    assert enhanced["trades"] >= 158
    assert enhanced["max_drawdown"] < 4
    assert backtest["false_override_rate"] < 10
    assert override_success(backtest) is True


def test_market_watch_iq_v8_contains_override_metrics():
    report = build_a_plus_override_report()
    iq = report["market_watch_iq_v8"]

    assert iq["override_accuracy"] == 100.0
    assert iq["false_override_rate"] == 0.0
    assert iq["override_benefit_score"] > 0
    assert iq["severity_classification_accuracy"] == 100.0
    assert iq["eligible_count"] > 0


def test_production_baseline_preservation_and_advisory_mode():
    report = build_a_plus_override_report()

    assert report["production_baseline_preserved"] is True
    assert report["production_rules_modified"] is False
    assert report["live_override_enabled"] is False
    assert report["kill_switch_bypassed"] is False
    assert report["broker_execution"] is False
    assert report["autonomous_execution"] is False
    assert report["decision"] == "PASS"

