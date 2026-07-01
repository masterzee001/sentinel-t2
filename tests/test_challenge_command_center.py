from __future__ import annotations

import json
from pathlib import Path

from backend.challenge_mode.challenge_command_center import (
    ChallengeCommandCenter,
    governor_state,
    profit_progress,
    recommendation_engine,
    risk_buffer,
)
from backend.challenge_mode.challenge_profile_config import ChallengeModeProfileConfig
from dashboard.utils.data_loader import challenge_performance_dataframe, load_challenge_command_center_summary
from scripts.run_challenge_command_center import build_challenge_command_center_report


def test_phase_tracking_and_target_progress():
    progress = profit_progress(
        {"starting_balance": 10000, "current_balance": 10400, "current_equity": 10400},
        {"phase_1_target_percent": 10, "phase_2_target_percent": 5},
        "PHASE_1",
    )

    assert progress["net_pnl"] == 400.0
    assert progress["net_pnl_percent"] == 4.0
    assert progress["target_percent"] == 10.0
    assert progress["progress_percent"] == 40.0
    assert progress["remaining_target_percent"] == 6.0


def test_drawdown_buffer_color_states():
    safe = risk_buffer(
        {"starting_balance": 10000, "daily_start_balance": 10000, "current_equity": 9900, "peak_balance": 10000},
        {"daily_loss_limit_percent": 5, "max_loss_limit_percent": 10},
    )
    danger = risk_buffer(
        {"starting_balance": 10000, "daily_start_balance": 10000, "current_equity": 9600, "peak_balance": 10000},
        {"daily_loss_limit_percent": 5, "max_loss_limit_percent": 10},
    )

    assert safe["color_state"] == "SAFE"
    assert danger["color_state"] == "DANGER"
    assert danger["daily_loss_limit"]["remaining_buffer_percent"] == 1.0


def test_governor_transitions_loss_streak_defensive_and_finish():
    profile = {"risk_percent": 0.80}
    governor = {
        "daily_soft_stop_percent": 2,
        "daily_hard_stop_percent": 3,
        "reduce_risk_after_losses": 2,
        "reduced_risk_multiplier": 0.5,
        "profit_lock_percent": 5,
    }

    reduced = governor_state({"loss_streak": 2}, governor, profile, {"net_pnl_percent": 1.0})
    defensive = governor_state({"loss_streak": 0}, governor, profile, {"net_pnl_percent": 5.0})
    finish = governor_state({"loss_streak": 0}, governor, profile, {"net_pnl_percent": 8.0})

    assert reduced["risk_mode"] == "REDUCED"
    assert reduced["current_risk_percent"] == 0.4
    assert defensive["risk_mode"] == "DEFENSIVE"
    assert finish["risk_mode"] == "FINISH"


def test_recommendation_engine_outputs_advisory_actions():
    recommendation = recommendation_engine(
        {"challenge_mode": "DISABLED"},
        {"net_pnl_percent": 0.5},
        {"color_state": "SAFE"},
        {"risk_mode": "NORMAL"},
        {"pf": 3.1, "win_rate": 73.0},
        "balanced",
    )
    pause = recommendation_engine(
        {"challenge_mode": "DISABLED"},
        {"net_pnl_percent": 0.5},
        {"color_state": "CRITICAL"},
        {"risk_mode": "NORMAL"},
        {"pf": 3.1, "win_rate": 73.0},
        "balanced",
    )

    assert recommendation["recommendation"] == "upgrade to 1.00%"
    assert recommendation["confidence"] == "MEDIUM"
    assert pause["recommendation"] == "pause trading today"
    assert pause["confidence"] == "HIGH"


def test_command_center_report_is_dashboard_only_and_preserves_baseline():
    report = build_challenge_command_center_report()

    assert report["challenge_status"]["challenge_mode"] == "DISABLED"
    assert report["checks"]["dashboard"] is True
    assert report["checks"]["telegram"] is True
    assert report["checks"]["production_baseline_preserved"] is True
    assert report["safety"]["broker_orders"] is False
    assert report["safety"]["autonomous_execution"] is False
    assert report["decision"] == "PASS"


def test_dashboard_loader_reads_challenge_command_center_report(tmp_path: Path):
    report_path = tmp_path / "data" / "reports" / "challenge_command_center.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps(ChallengeCommandCenter().build_report()), encoding="utf-8")

    summary = load_challenge_command_center_summary(
        tmp_path,
        {"challenge_command_center_report_path": "data/reports/challenge_command_center.json"},
    )
    frame = challenge_performance_dataframe(summary)

    assert summary["available"] is True
    assert summary["data"]["challenge_status"]["challenge_mode"] == "DISABLED"
    assert "pf" in set(frame["metric"])


def test_custom_config_still_requires_disabled_challenge_mode():
    config = ChallengeModeProfileConfig(config={"enabled": False, "profile": "balanced"})
    report = ChallengeCommandCenter(profile_config=config).build_report()

    assert report["safety"]["challenge_mode_enabled"] is False
    assert report["production_baseline_preserved"] is True
