from __future__ import annotations

from pathlib import Path

from backend.confidence_engine.confidence_analyzer import ConfidenceAnalyzer
from backend.display.confidence_display import confidence_state_for_score, observer_display_fields
from backend.guardrails.strategy_guardrails import StrategyGuardrails
from backend.shared.confidence_band_registry import (
    ALLOWED_REJECTION_REASONS,
    MSS_MODE_HISTORICAL_STRUCTURE_BREAK,
    MSS_MODE_LIVE_EVALUATED,
    cumulative_funnel,
    exclusive_band_distribution,
    guardrail_adjusted_band,
    production_band,
    rejection_reason_code,
)
from scripts.run_truth_layer_validation import build_truth_layer_report


def test_production_thresholds_are_registry_driven():
    assert production_band(89).band == "HOT"
    assert production_band(90).band == "EXECUTION_READY"
    assert ConfidenceAnalyzer.get_confidence_band(90) == ("EXECUTION_READY", "Trade Allowed")
    assert confidence_state_for_score(90) == "EXECUTION_READY"


def test_guardrail_adjusted_thresholds_are_registry_driven(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    guardrails = StrategyGuardrails(config_dir=config_dir)

    assert guardrail_adjusted_band(94).band == "HOT"
    assert guardrail_adjusted_band(95).band == "EXECUTION_READY"
    assert guardrails.adjust_confidence_band(94) == ("HOT", "Prepare")
    assert guardrails.adjust_confidence_band(95) == ("EXECUTION_READY", "Trade Allowed")
    assert "confidence_band_adjustment" not in StrategyGuardrails.DEFAULT_CONFIG
    assert "confidence_band_adjustment" not in Path("config/strategy_guardrails.yaml").read_text(encoding="utf-8")


def test_exclusive_band_distribution_is_not_cumulative():
    exclusive = exclusive_band_distribution(["COLD", "WARM", "HOT", "EXECUTION_READY", "EXECUTION_READY"])

    assert exclusive == {"COLD_ONLY": 1, "WARM_ONLY": 1, "HOT_ONLY": 1, "EXECUTION_READY": 2}


def test_cumulative_funnel_uses_hot_or_better():
    exclusive = {"COLD_ONLY": 0, "WARM_ONLY": 0, "HOT_ONLY": 20, "EXECUTION_READY": 52}
    funnel = cumulative_funnel(
        qualifying_setups=118,
        exclusive_distribution=exclusive,
        approved_trades=56,
        wins=27,
        losses=19,
        total_scans=4802,
    )

    assert funnel["HOT_OR_BETTER"] == 72
    assert funnel["EXECUTION_READY"] == 52
    assert "HOT_ONLY" not in funnel


def test_rejection_reasons_are_allowed_payload_codes():
    assert rejection_reason_code("MSS not confirmed") == "MSS_NOT_CONFIRMED"
    assert rejection_reason_code("below minimum confidence") == "BELOW_MIN_CONFIDENCE"
    assert rejection_reason_code("outside valid killzone") == "INVALID_KILLZONE"
    assert set(ALLOWED_REJECTION_REASONS) >= {
        "MSS_NOT_CONFIRMED",
        "BELOW_MIN_CONFIDENCE",
        "INVALID_KILLZONE",
        "SYMBOL_LOCK",
    }


def test_observer_states_are_clearly_labeled():
    display = observer_display_fields("NAS100", "HOT", 53)

    assert display["observer_state"] == "OBSERVER_HOT"
    assert display["display_state"] == "HOT"
    assert display["mode"] == "OBSERVER_ONLY"


def test_truth_report_distinguishes_backtest_and_live_mss_modes():
    report = build_truth_layer_report(
        diagnostics={
            "confidence_distribution": {"COLD": 0, "WARM": 0, "HOT": 20, "EXECUTION_READY": 52},
            "signal_funnel": {
                "total_scans": 4802,
                "qualifying_setups": 118,
                "approved_trades": 56,
                "wins": 27,
                "losses": 19,
            },
            "rejection_distribution": {"MSS not confirmed": 17, "grade_lock": 20},
        },
        validation={
            "matches_approved_baseline": True,
            "approved_robustness_baseline": {
                "pf": 1.58,
                "win_rate": 58.7,
                "trades": 56,
                "max_drawdown": 2.97,
            },
        },
    )

    assert report["mss_labeling"]["backtest_mss_mode"] == MSS_MODE_HISTORICAL_STRUCTURE_BREAK
    assert report["mss_labeling"]["live_mss_mode"] == MSS_MODE_LIVE_EVALUATED
    assert report["funnel_truth"]["exclusive_band_distribution"]["HOT_ONLY"] == 20
    assert report["funnel_truth"]["cumulative_funnel"]["HOT_OR_BETTER"] == 72
    assert report["rejection_attribution"]["distribution"]["BELOW_MIN_CONFIDENCE"] == 20


def test_truth_report_requires_recomputed_and_reconciled_production_metrics():
    diagnostics = {
        "confidence_distribution": {"HOT": 1, "EXECUTION_READY": 1},
        "signal_funnel": {"qualifying_setups": 2, "approved_trades": 1, "wins": 1, "losses": 0},
        "rejection_distribution": {},
    }
    honest_validation = {
        "approved_robustness_baseline": {"pf": 1.58, "win_rate": 58.7, "trades": 56, "max_drawdown": 2.97},
        "gates": [
            {"name": "production_metrics_recomputed_from_scan", "pass": True},
            {"name": "production_reconciles_internally", "pass": True},
        ],
    }
    # A constant-matching claim without recomputation gates must no longer pass.
    legacy_validation = {
        "matches_approved_baseline": True,
        "approved_robustness_baseline": {"pf": 1.58, "win_rate": 58.7, "trades": 56, "max_drawdown": 2.97},
    }

    honest = build_truth_layer_report(diagnostics=diagnostics, validation=honest_validation)
    legacy = build_truth_layer_report(diagnostics=diagnostics, validation=legacy_validation)

    assert honest["production_baseline"]["preserved"] is True
    assert honest["decision"] == "PASS"
    assert legacy["production_baseline"]["preserved"] is False
    assert legacy["decision"] == "FAIL"
