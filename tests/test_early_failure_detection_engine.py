from __future__ import annotations

from backend.early_failure_detection.early_failure_detection_engine import (
    EarlyFailureDetectionEngine,
    adverse_zone,
    efde_calibration_report,
    efde_learning_memory,
    efde_passes,
    failure_probability_score,
    fps_classification,
    replay_trades,
    winner_hold_signals,
    zone2_exit_signals,
)
from scripts.run_early_failure_detection_engine import build_early_failure_detection_report


def test_adverse_zone_detection():
    assert adverse_zone(0.29) == "NONE"
    assert adverse_zone(0.3) == "ADVERSE_ZONE_1"
    assert adverse_zone(0.49) == "ADVERSE_ZONE_1"
    assert adverse_zone(0.5) == "ADVERSE_ZONE_2"


def test_fps_scoring_and_classification():
    high = failure_probability_score(zone2_exit_signals()["ADVERSE_ZONE_2"])
    low = failure_probability_score(winner_hold_signals()["ADVERSE_ZONE_1"])

    assert high >= 75
    assert low < 40
    assert fps_classification(39) == "HOLD"
    assert fps_classification(40) == "WATCH"
    assert fps_classification(75) == "EARLY_EXIT_RECOMMENDED"


def test_false_early_exit_detection():
    replay = replay_trades(
        [
            {
                "trade_id": "WIN-CUT",
                "symbol": "XAUUSD",
                "strategy": "trend_following",
                "original_outcome": "WIN",
                "original_rr": 1.5,
                "adverse_excursion_pct": 0.55,
                "signals": zone2_exit_signals(),
            }
        ]
    )

    assert replay[0]["early_exit_recommended"] is True
    assert replay[0]["false_early_exit"] is True
    assert replay[0]["missed_winner"] > 0


def test_backtest_replay_reduces_losses_without_false_exits():
    report = build_early_failure_detection_report()
    summary = report["early_failure_detection_report"]

    assert summary["saved_losses"] > 0
    assert summary["false_exit_rate"] < 5.0
    assert summary["average_loss_after"] > summary["average_loss_before"]
    assert summary["missed_winner_value"] == 0.0
    assert efde_passes(summary) is True


def test_efde_enhanced_metrics_pass_success_criteria():
    report = build_early_failure_detection_report()
    summary = report["early_failure_detection_report"]
    enhanced = summary["efde_enhanced"]

    assert summary["pf_delta"] > 0
    assert summary["dd_delta"] < 0
    assert summary["wr_delta"] >= -1.0
    assert enhanced["average_loss"] > report["original_elite"]["average_loss"]
    assert report["decision"] == "PASS"


def test_production_baseline_preservation_and_advisory_mode():
    report = EarlyFailureDetectionEngine().build_report()

    assert report["production_baseline_preserved"] is True
    assert report["production_rules_modified"] is False
    assert report["live_auto_exit_enabled"] is False
    assert report["broker_order_modified"] is False
    assert report["autonomous_execution"] is False


def test_learning_memory_records_zone_evaluations_and_labels():
    report = build_early_failure_detection_report()
    memory = report["efde_learning_memory"]
    counts = memory["correctness_counts"]

    assert memory["records"]
    assert counts["CORRECT_EXIT"] > 0
    assert counts["FALSE_EXIT"] == 0
    assert counts["MISSED_EXIT"] == 0
    assert counts["CORRECT_HOLD"] > 0
    first = memory["records"][0]
    assert {"timestamp", "symbol", "strategy", "grade", "confidence", "regime", "micro_regime", "adverse_zone", "fps_score", "efde_decision", "final_correctness_label"}.issubset(first)


def test_calibration_report_groups_by_symbol_strategy_and_regime():
    report = build_early_failure_detection_report()
    calibration = report["efde_calibration_report"]

    assert calibration["by_symbol"]
    assert calibration["by_strategy"]
    assert calibration["by_regime"]
    assert calibration["recommended_threshold"] == 75
    assert calibration["confidence"] == "HIGH"
    assert calibration["threshold_auto_changed"] is False


def test_market_watch_iq_v9_contains_learning_fields():
    report = build_early_failure_detection_report()
    iq = report["market_watch_iq_v9"]

    assert iq["efde_learning_score"] == 100.0
    assert iq["fps_calibration_accuracy"] == 100.0
    assert iq["false_exit_cluster_count"] == 0
    assert iq["missed_exit_cluster_count"] == 0
