from __future__ import annotations

from backend.candidate_validation.candidate_validation_engine import (
    CandidateValidationEngine,
    ORIGINAL_ELITE,
    pf_dd_efficiency,
    stress_fail_reasons,
)
from scripts.run_candidate_validation import build_candidate_validation_report


def test_candidate_validation_contains_required_windows():
    report = build_candidate_validation_report()

    for candidate_id, validation in report["candidate_validation"].items():
        assert {"30D", "90D", "365D"}.issubset(validation)
        assert validation["365D"]["pf"] >= ORIGINAL_ELITE["pf"]
        assert validation["production_rule_change"] is False


def test_stress_rejection_rules_apply_thresholds():
    assert stress_fail_reasons({"pf": 2.89, "win_rate": 73.1, "max_drawdown": 3.9}) == ["PF_BELOW_2_9"]
    assert stress_fail_reasons({"pf": 2.91, "win_rate": 72.9, "max_drawdown": 3.9}) == ["WR_BELOW_73"]
    assert stress_fail_reasons({"pf": 2.91, "win_rate": 73.1, "max_drawdown": 4.0}) == ["DD_GTE_4"]


def test_candidate_3_is_best_validated_candidate():
    report = build_candidate_validation_report()

    assert report["candidate_decisions"]["candidate_3"] == "APPROVED_FOR_FUTURE_REVIEW"
    assert report["best_candidate"]["candidate_id"] == "candidate_3"
    assert report["candidate_stress"]["candidate_3"]["pass"] is True


def test_combined_candidate_rejected_by_stress_dd():
    report = build_candidate_validation_report()
    stress = report["candidate_stress"]["candidate_4"]

    assert stress["pass"] is False
    assert "DD_GTE_4" in stress["fail_reasons"]
    assert report["candidate_decisions"]["candidate_4"] == "REJECTED"


def test_pf_dd_efficiency_scoring_uses_baseline_gain():
    score = pf_dd_efficiency({"pf": 2.94, "win_rate": 73.25, "trades": 158, "max_drawdown": 3.84})

    assert score == 0.833


def test_correlation_report_marks_symbol_candidate_independent_enough():
    report = build_candidate_validation_report()
    correlation = report["candidate_correlation"]["candidate_1"]

    assert correlation["symbols"]
    assert correlation["average_correlation_to_us30_xau"] < 0.65
    assert correlation["independent_edge"] is True


def test_production_baseline_preservation_and_advisory_mode():
    report = CandidateValidationEngine().build_report()

    assert report["original_elite"] == {"pf": 2.84, "win_rate": 72.6, "trades": 151, "max_drawdown": 3.72}
    assert report["production_baseline_preserved"] is True
    assert report["production_policy_changed"] is False
    assert report["live_config_changed"] is False
    assert report["broker_execution"] is False
    assert report["autonomous_execution"] is False

