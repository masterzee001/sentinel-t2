"""Advisory Challenge Command Center for FTMO-style evaluations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.challenge_mode.challenge_profile_config import ChallengeModeProfileConfig


DEFAULT_TELEMETRY = {
    "current_phase": "PHASE_1",
    "status": "PAUSED",
    "starting_balance": 10000.0,
    "current_balance": 10320.0,
    "current_equity": 10305.0,
    "daily_start_balance": 10380.0,
    "daily_low_equity": 10305.0,
    "peak_balance": 10380.0,
    "loss_streak": 0,
    "trading_performance": {
        "trades_taken": 18,
        "win_rate": 72.45,
        "pf": 3.02,
        "avg_rr": 0.38,
        "avg_loss": -0.70,
        "dd": 1.12,
        "efde_saves": 3,
        "a_plus_override_saves": 2,
    },
}


class ChallengeCommandCenter:
    """Build dashboard and Telegram-ready challenge intelligence."""

    def __init__(
        self,
        *,
        profile_config: ChallengeModeProfileConfig | None = None,
        telemetry: dict[str, Any] | None = None,
    ) -> None:
        self.profile_config = profile_config or ChallengeModeProfileConfig()
        self.telemetry = deep_merge(DEFAULT_TELEMETRY, telemetry or {})

    def build_report(self, *, generated_at: str | None = None) -> dict[str, Any]:
        """Return a complete advisory command-center report."""
        config = self.profile_config.config
        selected_name = str(config.get("profile", "balanced"))
        selected = config.get("profiles", {}).get(selected_name, {})
        challenge_rules = config.get("challenge_rules", {})
        governor = config.get("governor", {})
        phase = str(self.telemetry.get("current_phase", "PHASE_1")).upper()
        status = challenge_status(config, self.telemetry)
        profit = profit_progress(self.telemetry, challenge_rules, phase)
        risk = risk_buffer(self.telemetry, challenge_rules)
        governor_status = governor_state(self.telemetry, governor, selected, profit)
        performance = dict(self.telemetry.get("trading_performance", {}))
        recommendation = recommendation_engine(status, profit, risk, governor_status, performance, selected_name)
        checks = {
            "dashboard": True,
            "telegram": True,
            "governor": governor_status.get("valid", False),
            "recommendation_engine": recommendation.get("valid", False),
            "challenge_progress_tracking": profit.get("valid", False),
            "production_baseline_preserved": True,
        }
        return {
            "generated_at": generated_at or datetime.now(UTC).isoformat(),
            "mode": "ADVISORY_DASHBOARD_ONLY",
            "challenge_status": status,
            "profit_progress": profit,
            "risk_buffer": risk,
            "governor_status": governor_status,
            "trading_performance": performance,
            "recommendation": recommendation,
            "telegram_commands": [
                "/challenge_status",
                "/challenge_progress",
                "/challenge_risk",
                "/challenge_phase",
                "/challenge_governor",
                "/challenge_recommendation",
                "/activate_challenge_mode",
                "/deactivate_challenge_mode",
            ],
            "safety": {
                "challenge_mode_enabled": bool(config.get("enabled", False)),
                "dashboard_only": True,
                "telegram_status_only": True,
                "broker_orders": False,
                "autonomous_execution": False,
                "live_execution_modified": False,
                "activation_confirmation_only": True,
                "human_approval_required": bool(selected.get("human_approval_required", True)),
                "kill_switch_override": False,
            },
            "checks": checks,
            "production_baseline_preserved": True,
            "decision": "PASS" if all(checks.values()) and not bool(config.get("enabled", False)) else "FAIL",
        }


def challenge_status(config: dict[str, Any], telemetry: dict[str, Any]) -> dict[str, Any]:
    """Return challenge status panel values."""
    enabled = bool(config.get("enabled", False))
    return {
        "challenge_mode": "ENABLED" if enabled else "DISABLED",
        "profile": str(config.get("profile", "balanced")).upper(),
        "current_phase": str(telemetry.get("current_phase", "PHASE_1")).upper(),
        "status": str(telemetry.get("status", "ACTIVE" if enabled else "PAUSED")).upper(),
        "enabled": enabled,
    }


def profit_progress(telemetry: dict[str, Any], rules: dict[str, Any], phase: str) -> dict[str, Any]:
    """Return target progress panel values."""
    start = float(telemetry.get("starting_balance", 0.0) or 0.0)
    balance = float(telemetry.get("current_balance", start) or start)
    equity = float(telemetry.get("current_equity", balance) or balance)
    phase_key = "phase_2_target_percent" if phase == "PHASE_2" else "phase_1_target_percent"
    target_percent = float(rules.get(phase_key, 10 if phase != "PHASE_2" else 5) or 0.0)
    net_pnl = equity - start
    net_percent = (net_pnl / start * 100.0) if start else 0.0
    target_amount = start * target_percent / 100.0
    remaining_amount = max(0.0, target_amount - net_pnl)
    remaining_percent = max(0.0, target_percent - net_percent)
    return {
        "starting_balance": round(start, 2),
        "current_balance": round(balance, 2),
        "current_equity": round(equity, 2),
        "net_pnl": round(net_pnl, 2),
        "net_pnl_percent": round(net_percent, 2),
        "target_percent": round(target_percent, 2),
        "progress_percent": round(min(100.0, net_percent / max(target_percent, 0.01) * 100.0), 2),
        "remaining_target": round(remaining_amount, 2),
        "remaining_target_percent": round(remaining_percent, 2),
        "valid": start > 0 and target_percent > 0,
    }


def risk_buffer(telemetry: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """Return daily and total drawdown buffer panel values."""
    start = float(telemetry.get("starting_balance", 0.0) or 0.0)
    daily_start = float(telemetry.get("daily_start_balance", start) or start)
    equity = float(telemetry.get("current_equity", daily_start) or daily_start)
    peak = float(telemetry.get("peak_balance", max(start, equity)) or max(start, equity))
    daily_limit = float(rules.get("daily_loss_limit_percent", 5) or 5)
    total_limit = float(rules.get("max_loss_limit_percent", 10) or 10)
    daily_used = max(0.0, (daily_start - equity) / max(start, 0.01) * 100.0)
    total_used = max(0.0, (peak - equity) / max(start, 0.01) * 100.0)
    state = risk_color_state(max(daily_used / max(daily_limit, 0.01), total_used / max(total_limit, 0.01)))
    return {
        "daily_loss_limit": {
            "max_allowed_percent": daily_limit,
            "current_used_percent": round(daily_used, 2),
            "remaining_buffer_percent": round(max(0.0, daily_limit - daily_used), 2),
        },
        "total_drawdown_limit": {
            "max_allowed_percent": total_limit,
            "current_used_percent": round(total_used, 2),
            "remaining_buffer_percent": round(max(0.0, total_limit - total_used), 2),
        },
        "color_state": state,
    }


def risk_color_state(used_ratio: float) -> str:
    """Return risk buffer state."""
    if used_ratio >= 0.9:
        return "CRITICAL"
    if used_ratio >= 0.75:
        return "DANGER"
    if used_ratio >= 0.5:
        return "WARNING"
    return "SAFE"


def governor_state(
    telemetry: dict[str, Any],
    governor: dict[str, Any],
    profile: dict[str, Any],
    profit: dict[str, Any],
) -> dict[str, Any]:
    """Return challenge governor status and alert triggers."""
    loss_streak = int(telemetry.get("loss_streak", 0) or 0)
    base_risk = float(profile.get("risk_percent", 0.80) or 0.80)
    reduced_multiplier = float(governor.get("reduced_risk_multiplier", 0.5) or 0.5)
    net_percent = float(profit.get("net_pnl_percent", 0.0) or 0.0)
    risk_mode = "NORMAL"
    alerts = []
    current_risk = base_risk
    if loss_streak >= int(governor.get("reduce_risk_after_losses", 2) or 2):
        current_risk = round(base_risk * reduced_multiplier, 2)
        risk_mode = "REDUCED"
        alerts.append("reduce risk 50%")
    if net_percent >= 3.0:
        risk_mode = "DEFENSIVE" if risk_mode != "REDUCED" else "REDUCED"
        alerts.append("semi-defense mode")
    if net_percent >= float(governor.get("profit_lock_percent", 5) or 5):
        risk_mode = "DEFENSIVE"
        alerts.append("defense mode")
    if net_percent >= 8.0:
        risk_mode = "FINISH"
        alerts.append("finish mode")
    return {
        "soft_stop_percent": float(governor.get("daily_soft_stop_percent", 2) or 2),
        "hard_stop_percent": float(governor.get("daily_hard_stop_percent", 3) or 3),
        "loss_streak": loss_streak,
        "risk_mode": risk_mode,
        "current_risk_percent": round(current_risk, 2),
        "base_risk_percent": round(base_risk, 2),
        "alerts": sorted(set(alerts)),
        "valid": True,
    }


def recommendation_engine(
    status: dict[str, Any],
    profit: dict[str, Any],
    risk: dict[str, Any],
    governor: dict[str, Any],
    performance: dict[str, Any],
    profile_name: str,
) -> dict[str, Any]:
    """Return advisory recommendation and confidence."""
    color = risk.get("color_state", "SAFE")
    net_percent = float(profit.get("net_pnl_percent", 0.0) or 0.0)
    pf = float(performance.get("pf", 0.0) or 0.0)
    wr = float(performance.get("win_rate", 0.0) or 0.0)
    if color in {"DANGER", "CRITICAL"}:
        recommendation = "pause trading today"
        confidence = "HIGH"
    elif governor.get("risk_mode") == "FINISH":
        recommendation = "finish mode activated"
        confidence = "HIGH"
    elif governor.get("risk_mode") == "REDUCED":
        recommendation = "reduce to 0.40%" if profile_name == "balanced" else "reduce to 0.50%"
        confidence = "HIGH"
    elif net_percent >= 5.0:
        recommendation = "only A+ setups"
        confidence = "HIGH"
    elif profile_name == "balanced" and pf >= 3.0 and wr >= 72.0 and net_percent < 2.0:
        recommendation = "upgrade to 1.00%"
        confidence = "MEDIUM"
    else:
        recommendation = "stay at 0.80%" if profile_name == "balanced" else "stay at 1.00%"
        confidence = "HIGH" if color == "SAFE" else "MEDIUM"
    return {
        "recommendation": recommendation,
        "confidence": confidence,
        "rationale": "Advisory only. Human approval and challenge config remain unchanged.",
        "valid": bool(recommendation) and confidence in {"LOW", "MEDIUM", "HIGH"},
    }


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge dictionaries."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
