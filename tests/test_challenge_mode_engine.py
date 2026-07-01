from __future__ import annotations

import random
from datetime import date

from backend.challenge_mode.challenge_mode_engine import (
    ChallengeModeEngine,
    calendar_months,
    governed_risk,
    monthly_window_analysis,
    rolling_2month_window_analysis,
    simulate_phase,
    window_profile_result,
)
from scripts.run_challenge_mode_simulator import build_challenge_mode_report


def test_challenge_governor_halves_risk_after_two_losses_and_derisks_profit():
    assert governed_risk(base_risk=1.0, consecutive_losses=2, phase_profit=0.0, target=10.0) == 0.5
    assert governed_risk(base_risk=1.0, consecutive_losses=0, phase_profit=5.0, target=10.0) == 0.7
    assert governed_risk(base_risk=1.0, consecutive_losses=2, phase_profit=5.0, target=10.0) == 0.35


def test_drawdown_breach_detection_in_phase_logic():
    result = simulate_phase(
        phase_name="phase_1",
        risk_percent=5.0,
        rr_pool=[-1.5],
        rng=random.Random(1),
    )

    assert result["passed"] is False
    assert result["failure_mode"] in {"daily_loss_breach", "total_drawdown_breach"}


def test_phase_pass_logic_hits_target():
    result = simulate_phase(
        phase_name="phase_2",
        risk_percent=1.2,
        rr_pool=[1.4],
        rng=random.Random(2),
    )

    assert result["passed"] is True
    assert result["failure_mode"] == "target_hit"
    assert result["return_percent"] >= 5.0


def test_monte_carlo_challenge_simulation_profiles():
    report = ChallengeModeEngine(runs=100).build_report()

    assert report["profiles"]["profile_1"]["combined_pass_probability"] >= 60.0
    assert report["profiles"]["profile_0"]["combined_pass_probability"] < report["profiles"]["profile_1"]["combined_pass_probability"]
    assert report["challenge_verdict"] == "PASSABLE"
    assert report["runs_per_profile"] == 100


def test_monthly_windows_cover_january_last_year_to_current_date():
    months = calendar_months(date(2025, 1, 1), date(2026, 6, 30))
    monthly = monthly_window_analysis(today=date(2026, 6, 30))

    assert len(months) == 18
    assert len(monthly["windows"]) == 18
    assert monthly["windows"][0]["start_date"] == "2025-01-01"
    assert monthly["windows"][-1]["end_date"] == "2026-06-30"
    assert monthly["summary"]["best_window"]
    assert monthly["summary"]["worst_window"]


def test_rolling_2month_windows_and_profile_outputs():
    monthly = monthly_window_analysis(today=date(2026, 6, 30))
    rolling = rolling_2month_window_analysis(monthly["windows"])

    assert len(rolling["windows"]) == 17
    first = rolling["windows"][0]
    profile = first["profiles"]["profile_2"]
    assert {"trades", "pf", "wr", "net_return_percent", "max_dd_percent", "target_reached", "failure_reason"}.issubset(profile)
    assert rolling["summary"]["most_consistent_risk_profile"] in {"profile_0", "profile_1", "profile_2", "profile_3", "profile_4"}


def test_window_profile_result_flags_target_and_failure_reason():
    period = {"trades": 10, "pf": 3.0, "wr": 72.0, "net_r": 25.0, "quality": 1.2, "start_date": "2026-01-01", "end_date": "2026-01-31"}
    high = window_profile_result(period, 0.8)
    low = window_profile_result(period, 0.25)

    assert high["target_reached"] is True
    assert high["days_to_target"] is not None
    assert low["target_reached"] is False
    assert low["failure_reason"] in {"timeout", "stagnation"}


def test_production_baseline_preservation_and_simulation_only():
    report = build_challenge_mode_report()

    assert report["production_baseline_preserved"] is True
    assert report["production_rules_modified"] is False
    assert report["live_config_changed"] is False
    assert report["real_challenge_activation"] is False
    assert report["broker_execution"] is False
    assert report["autonomous_execution"] is False

