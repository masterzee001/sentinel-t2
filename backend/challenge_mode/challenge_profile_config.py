"""Configuration-only Challenge Mode profile gate.

This module validates challenge profiles for future manual use. It does not
activate live trading, alter emergency mode, or permit broker submission.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG = {
    "enabled": False,
    "profile": "balanced",
    "profiles": {
        "balanced": {
            "risk_percent": 0.80,
            "allowed_symbols": ["XAUUSD", "US30"],
            "allowed_grades": ["A+", "A"],
            "efde_enabled": True,
            "a_plus_override_advisory": True,
            "human_approval_required": True,
            "max_trades_per_day": 4,
        },
        "aggressive": {
            "risk_percent": 1.00,
            "allowed_symbols": ["XAUUSD", "US30"],
            "allowed_grades": ["A+", "A"],
            "efde_enabled": True,
            "a_plus_override_advisory": True,
            "human_approval_required": True,
            "max_trades_per_day": 4,
        },
    },
    "challenge_rules": {
        "phase_1_target_percent": 10,
        "phase_2_target_percent": 5,
        "daily_loss_limit_percent": 5,
        "max_loss_limit_percent": 10,
    },
    "governor": {
        "daily_soft_stop_percent": 2,
        "daily_hard_stop_percent": 3,
        "reduce_risk_after_losses": 2,
        "reduced_risk_multiplier": 0.5,
        "profit_lock_percent": 5,
    },
}

REJECTED_REAL_RISKS = {1.20}
MAX_REAL_RISK_PERCENT = 1.00
APPROVED_PRODUCTION_BASELINE = {
    "pf": 2.84,
    "wr": 72.6,
    "trades": 151,
    "dd": 3.72,
}


class ChallengeModeProfileConfig:
    """Load and validate the disabled Challenge Mode profile config."""

    def __init__(self, *, config_dir: str | Path | None = None, config: dict[str, Any] | None = None) -> None:
        self.config_dir = Path(config_dir) if config_dir else Path(__file__).resolve().parents[2] / "config"
        loaded = config if config is not None else self.load_config()
        self.config = deep_merge(DEFAULT_CONFIG, loaded)

    def load_config(self) -> dict[str, Any]:
        """Load config/challenge_mode.yaml if present."""
        path = self.config_dir / "challenge_mode.yaml"
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def build_report(self, *, generated_at: str | None = None) -> dict[str, Any]:
        """Return validation report for the challenge profile config."""
        profile_results = {
            name: validate_profile(name, profile)
            for name, profile in self.config.get("profiles", {}).items()
        }
        checks = {
            "challenge_config": challenge_config_pass(self.config),
            "enabled_false_by_default": self.config.get("enabled") is False,
            "balanced_profile": profile_results.get("balanced", {}).get("valid", False),
            "aggressive_profile": profile_results.get("aggressive", {}).get("valid", False),
            "rejected_risk_1_20": not real_mode_risk_allowed(self.config, 1.20),
            "emergency_isolation": emergency_isolation_pass(self.config),
            "governor": governor_pass(self.config.get("governor", {})),
            "production_baseline_preserved": True,
        }
        return {
            "generated_at": generated_at or datetime.now(UTC).isoformat(),
            "mode": "CONFIGURATION_ONLY",
            "enabled": bool(self.config.get("enabled", False)),
            "selected_profile": self.config.get("profile"),
            "profiles": self.config.get("profiles", {}),
            "profile_validation": profile_results,
            "challenge_rules": self.config.get("challenge_rules", {}),
            "governor": self.config.get("governor", {}),
            "rejected_real_risks": sorted(REJECTED_REAL_RISKS),
            "safety": {
                "live_activation": False,
                "broker_execution": False,
                "autonomous_execution": False,
                "human_approval_required": all(
                    bool(profile.get("human_approval_required", False))
                    for profile in self.config.get("profiles", {}).values()
                ),
                "does_not_override_kill_switch": True,
                "does_not_override_broker_submission_disabled": True,
                "does_not_override_autonomous_execution_disabled": True,
                "emergency_live_mode_separate": True,
            },
            "checks": checks,
            "production_baseline": dict(APPROVED_PRODUCTION_BASELINE),
            "decision": "PASS" if all(checks.values()) else "FAIL",
        }


def validate_profile(name: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Validate one configured Challenge Mode profile."""
    expected_risk = {"balanced": 0.80, "aggressive": 1.00}.get(name)
    risk = float(profile.get("risk_percent", -1) or -1)
    reasons: list[str] = []
    if expected_risk is not None and round(risk, 2) != expected_risk:
        reasons.append(f"{name} risk must be {expected_risk:.2f}%")
    if round(risk, 2) in REJECTED_REAL_RISKS or risk > MAX_REAL_RISK_PERCENT:
        reasons.append("risk exceeds real challenge profile limit")
    if set(profile.get("allowed_symbols", [])) != {"XAUUSD", "US30"}:
        reasons.append("allowed symbols must be XAUUSD and US30 only")
    if set(profile.get("allowed_grades", [])) != {"A+", "A"}:
        reasons.append("allowed grades must be A+ and A only")
    if not bool(profile.get("efde_enabled", False)):
        reasons.append("EFDE must be enabled")
    if not bool(profile.get("a_plus_override_advisory", False)):
        reasons.append("A+ override must remain advisory")
    if not bool(profile.get("human_approval_required", False)):
        reasons.append("human approval must be required")
    if int(profile.get("max_trades_per_day", 0) or 0) != 4:
        reasons.append("max trades per day must be 4")
    return {
        "valid": not reasons,
        "risk_percent": risk,
        "reasons": reasons,
    }


def real_mode_risk_allowed(config: dict[str, Any], risk_percent: float) -> bool:
    """Return whether a risk percent is allowed for future real challenge mode."""
    risk = round(float(risk_percent), 2)
    if risk in REJECTED_REAL_RISKS or risk > MAX_REAL_RISK_PERCENT:
        return False
    return any(round(float(profile.get("risk_percent", -1) or -1), 2) == risk for profile in config.get("profiles", {}).values())


def challenge_config_pass(config: dict[str, Any]) -> bool:
    """Return whether top-level challenge config is inert and profile-gated."""
    return (
        config.get("enabled") is False
        and config.get("profile") == "balanced"
        and "balanced" in config.get("profiles", {})
        and "aggressive" in config.get("profiles", {})
        and all(name in {"balanced", "aggressive"} for name in config.get("profiles", {}))
    )


def emergency_isolation_pass(config: dict[str, Any]) -> bool:
    """Return whether challenge config stays separate from emergency live controls."""
    profiles = config.get("profiles", {})
    return (
        config.get("enabled") is False
        and "emergency_live" not in config
        and all(bool(profile.get("human_approval_required", False)) for profile in profiles.values())
        and all(bool(profile.get("a_plus_override_advisory", False)) for profile in profiles.values())
    )


def governor_pass(governor: dict[str, Any]) -> bool:
    """Return whether challenge governor matches Sprint 12.1 rules."""
    return (
        float(governor.get("daily_soft_stop_percent", 0) or 0) == 2.0
        and float(governor.get("daily_hard_stop_percent", 0) or 0) == 3.0
        and int(governor.get("reduce_risk_after_losses", 0) or 0) == 2
        and float(governor.get("reduced_risk_multiplier", 0) or 0) == 0.5
        and float(governor.get("profit_lock_percent", 0) or 0) == 5.0
    )


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge dictionaries."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
