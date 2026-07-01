from __future__ import annotations

from pathlib import Path

from backend.challenge_mode.challenge_profile_config import (
    ChallengeModeProfileConfig,
    emergency_isolation_pass,
    governor_pass,
    real_mode_risk_allowed,
)


CONFIG_TEXT = """
enabled: false
profile: balanced

profiles:
  balanced:
    risk_percent: 0.80
    allowed_symbols:
      - XAUUSD
      - US30
    allowed_grades:
      - A+
      - A
    efde_enabled: true
    a_plus_override_advisory: true
    human_approval_required: true
    max_trades_per_day: 4
  aggressive:
    risk_percent: 1.00
    allowed_symbols:
      - XAUUSD
      - US30
    allowed_grades:
      - A+
      - A
    efde_enabled: true
    a_plus_override_advisory: true
    human_approval_required: true
    max_trades_per_day: 4

challenge_rules:
  phase_1_target_percent: 10
  phase_2_target_percent: 5
  daily_loss_limit_percent: 5
  max_loss_limit_percent: 10

governor:
  daily_soft_stop_percent: 2
  daily_hard_stop_percent: 3
  reduce_risk_after_losses: 2
  reduced_risk_multiplier: 0.5
  profit_lock_percent: 5
"""


def write_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "challenge_mode.yaml").write_text(CONFIG_TEXT, encoding="utf-8")
    return config_dir


def test_challenge_config_loads_and_is_disabled_by_default(tmp_path: Path):
    config = ChallengeModeProfileConfig(config_dir=write_config(tmp_path))

    assert config.config["enabled"] is False
    assert config.config["profile"] == "balanced"


def test_balanced_profile_uses_preferred_080_percent_risk(tmp_path: Path):
    report = ChallengeModeProfileConfig(config_dir=write_config(tmp_path)).build_report()

    assert report["profile_validation"]["balanced"]["valid"] is True
    assert report["profile_validation"]["balanced"]["risk_percent"] == 0.80
    assert report["checks"]["balanced_profile"] is True


def test_aggressive_profile_uses_backup_100_percent_risk(tmp_path: Path):
    report = ChallengeModeProfileConfig(config_dir=write_config(tmp_path)).build_report()

    assert report["profile_validation"]["aggressive"]["valid"] is True
    assert report["profile_validation"]["aggressive"]["risk_percent"] == 1.00
    assert report["checks"]["aggressive_profile"] is True


def test_120_percent_is_rejected_for_real_challenge_mode(tmp_path: Path):
    config = ChallengeModeProfileConfig(config_dir=write_config(tmp_path))

    assert real_mode_risk_allowed(config.config, 0.80) is True
    assert real_mode_risk_allowed(config.config, 1.00) is True
    assert real_mode_risk_allowed(config.config, 1.20) is False
    assert config.build_report()["checks"]["rejected_risk_1_20"] is True


def test_emergency_mode_isolation_and_human_approval(tmp_path: Path):
    config = ChallengeModeProfileConfig(config_dir=write_config(tmp_path))
    report = config.build_report()

    assert emergency_isolation_pass(config.config) is True
    assert report["safety"]["emergency_live_mode_separate"] is True
    assert report["safety"]["does_not_override_kill_switch"] is True
    assert report["safety"]["does_not_override_broker_submission_disabled"] is True
    assert report["safety"]["does_not_override_autonomous_execution_disabled"] is True
    assert report["safety"]["human_approval_required"] is True


def test_governor_rules_match_challenge_profile_requirements(tmp_path: Path):
    config = ChallengeModeProfileConfig(config_dir=write_config(tmp_path))

    assert governor_pass(config.config["governor"]) is True
    assert config.build_report()["checks"]["governor"] is True


def test_production_baseline_preservation_flag(tmp_path: Path):
    report = ChallengeModeProfileConfig(config_dir=write_config(tmp_path)).build_report()

    assert report["checks"]["production_baseline_preserved"] is True
    assert report["production_baseline"] == {"pf": 2.84, "wr": 72.6, "trades": 151, "dd": 3.72}
    assert report["decision"] == "PASS"
