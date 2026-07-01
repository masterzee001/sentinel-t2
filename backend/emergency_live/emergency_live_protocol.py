"""Controlled assisted emergency live deployment protocol."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml


DEFAULT_CONFIG = {
    "enabled": True,
    "risk_percent": 0.1,
    "max_risk_percent": 0.25,
    "allowed_symbols": ["US30", "XAUUSD"],
    "allowed_grades": ["A+"],
    "human_approval_required": True,
    "max_trades_per_day": 2,
    "kill_switch": {
        "daily_loss_r": -1,
        "consecutive_losses": 3,
        "max_drawdown_percent": 2,
    },
}


class EmergencyLiveProtocol:
    """Enforce 7-day emergency assisted-live restrictions."""

    def __init__(self, *, config_dir: str | Path | None = None, config: dict[str, Any] | None = None) -> None:
        self.config_dir = Path(config_dir) if config_dir else Path(__file__).resolve().parents[2] / "config"
        self.config = deep_merge(DEFAULT_CONFIG, config if config is not None else self.load_config())
        self.status = "LIVE_READY" if self.config.get("enabled", False) else "DISABLED"
        self.halt_reason = ""
        self.approval_queue: list[dict[str, Any]] = []
        self.approval_history: list[dict[str, Any]] = []

    def load_config(self) -> dict[str, Any]:
        """Load emergency live config."""
        path = self.config_dir / "emergency_live.yaml"
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def deployment_stage(self, day: int) -> dict[str, Any]:
        """Return staged ladder metadata for the emergency timeline."""
        if day <= 3:
            return {"day": day, "mode": "DEMO_VALIDATION", "real_orders_allowed": False, "description": "Intensive demo validation"}
        if day <= 5:
            return {"day": day, "mode": "SHADOW_LIVE", "real_orders_allowed": False, "description": "Live feed proposals only"}
        if day <= 7:
            return {"day": day, "mode": "MICRO_LIVE", "real_orders_allowed": True, "description": "Human-approved micro-risk live"}
        return {"day": day, "mode": "COMPLETE", "real_orders_allowed": False, "description": "Emergency window complete"}

    def validate_proposal(self, proposal: dict[str, Any], *, trades_today: int = 0) -> dict[str, Any]:
        """Validate one trade proposal against emergency restrictions."""
        reasons: list[str] = []
        symbol = str(proposal.get("symbol", "")).upper()
        grade = str(proposal.get("quality_grade", ""))
        risk_percent = float(proposal.get("risk_percent", self.config.get("risk_percent", 0.1)) or 0.0)
        if self.status == "LIVE_HALTED":
            reasons.append(f"Live halted: {self.halt_reason}")
        if symbol not in {str(item).upper() for item in self.config.get("allowed_symbols", [])}:
            reasons.append("Symbol not allowed for emergency live")
        if grade not in set(self.config.get("allowed_grades", [])):
            reasons.append("Only A+ setups are allowed")
        if risk_percent > float(self.config.get("max_risk_percent", 0.25) or 0.25):
            reasons.append("Risk exceeds emergency maximum")
        if trades_today >= int(self.config.get("max_trades_per_day", 2) or 2):
            reasons.append("Max trades per day reached")
        valid = not reasons
        return {
            "valid": valid,
            "status": "APPROVAL_REQUIRED" if valid and self.config.get("human_approval_required", True) else ("BLOCKED" if reasons else "VALID"),
            "reasons": reasons,
            "execution_allowed": False,
            "human_approval_required": bool(self.config.get("human_approval_required", True)),
            "broker_order_submission_allowed": False,
            "normalized_risk_percent": min(risk_percent or float(self.config.get("risk_percent", 0.1)), float(self.config.get("max_risk_percent", 0.25))),
        }

    def create_approval_request(self, proposal: dict[str, Any], *, trades_today: int = 0) -> dict[str, Any]:
        """Create a human approval request for a valid emergency proposal."""
        validation = self.validate_proposal(proposal, trades_today=trades_today)
        request = {
            "approval_id": f"ELIVE-{uuid4().hex[:8].upper()}",
            "created_at": datetime.now(UTC).isoformat(),
            "status": "PENDING" if validation["valid"] else "BLOCKED",
            "proposal": proposal,
            "validation": validation,
            "approval_prompt": approval_prompt(proposal),
        }
        if not validation["valid"]:
            request["queue_action"] = "BLOCKED_NOT_QUEUED"
            return request
        self.approval_queue.append(request)
        return request

    def approve_trade(self, approval_id: str) -> dict[str, Any]:
        """Mark a queued request as human-approved without executing it."""
        return self.resolve_approval(approval_id, "APPROVED")

    def reject_trade(self, approval_id: str) -> dict[str, Any]:
        """Mark a queued request as rejected."""
        return self.resolve_approval(approval_id, "REJECTED")

    def resolve_approval(self, approval_id: str, status: str) -> dict[str, Any]:
        """Resolve one approval request."""
        for request in self.approval_queue:
            if request["approval_id"] == approval_id:
                request["status"] = status
                request["resolved_at"] = datetime.now(UTC).isoformat()
                request["broker_order_submission_allowed"] = False
                self.approval_history.append(request)
                return dict(request)
        return {"approval_id": approval_id, "status": "NOT_FOUND", "broker_order_submission_allowed": False}

    def evaluate_kill_switch(self, telemetry: dict[str, Any]) -> dict[str, Any]:
        """Evaluate hard halt conditions."""
        kill = self.config.get("kill_switch", {})
        reasons = []
        if float(telemetry.get("daily_r", 0.0) or 0.0) <= float(kill.get("daily_loss_r", -1) or -1):
            reasons.append("Daily loss <= -1R")
        if int(telemetry.get("consecutive_losses", 0) or 0) >= int(kill.get("consecutive_losses", 3) or 3):
            reasons.append("3 consecutive losses")
        if bool(telemetry.get("mt5_disconnect", False)):
            reasons.append("MT5 disconnect")
        if bool(telemetry.get("spread_explosion", False)):
            reasons.append("spread explosion")
        if bool(telemetry.get("order_rejection", False)):
            reasons.append("order rejection")
        if bool(telemetry.get("abnormal_slippage", False)):
            reasons.append("abnormal slippage")
        if float(telemetry.get("drawdown_percent", 0.0) or 0.0) >= float(kill.get("max_drawdown_percent", 2) or 2):
            reasons.append("Total live drawdown >= 2%")
        if reasons:
            self.halt_live("; ".join(reasons))
        return {"triggered": bool(reasons), "status": self.status, "reasons": reasons}

    def halt_live(self, reason: str) -> dict[str, Any]:
        """Halt emergency live trading."""
        self.status = "LIVE_HALTED"
        self.halt_reason = reason
        return {"status": self.status, "reason": reason, "manual_override_required": True}

    def resume_live(self, *, manual_override: bool) -> dict[str, Any]:
        """Resume only after manual override."""
        if not manual_override:
            return {"status": self.status, "resumed": False, "reason": "Manual override required"}
        self.status = "LIVE_READY"
        self.halt_reason = ""
        return {"status": self.status, "resumed": True}

    def status_report(self) -> dict[str, Any]:
        """Return current emergency live status."""
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "status": self.status,
            "halt_reason": self.halt_reason,
            "config": self.config,
            "deployment_ladder": [self.deployment_stage(day) for day in range(1, 8)],
            "approval_queue": self.approval_queue,
            "approval_history": self.approval_history,
            "risk_lock": {
                "risk_percent": self.config.get("risk_percent", 0.1),
                "max_risk_percent": self.config.get("max_risk_percent", 0.25),
                "locked": float(self.config.get("max_risk_percent", 0.25)) <= 0.25,
            },
            "grade_lock": {"allowed_grades": self.config.get("allowed_grades", []), "locked": self.config.get("allowed_grades", []) == ["A+"]},
            "symbol_lock": {"allowed_symbols": self.config.get("allowed_symbols", []), "locked": set(self.config.get("allowed_symbols", [])) == {"US30", "XAUUSD"}},
            "no_autonomous_execution": True,
            "broker_order_submission_allowed": False,
        }


def approval_prompt(proposal: dict[str, Any]) -> dict[str, Any]:
    """Return required human approval payload."""
    return {
        "symbol": proposal.get("symbol"),
        "strategy": proposal.get("strategy"),
        "quality_grade": proposal.get("quality_grade"),
        "regime": proposal.get("regime"),
        "entry": proposal.get("entry"),
        "sl": proposal.get("sl"),
        "tp": proposal.get("tp"),
        "risk": proposal.get("risk_percent"),
        "expected_pf": proposal.get("expected_pf"),
        "expected_wr": proposal.get("expected_wr"),
        "human_response_required": "APPROVE / REJECT",
    }


def daily_report(status: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic daily emergency-live report."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "CONTROLLED_ASSISTED_LIVE",
        "day_1_3_demo_validation": {
            "minimum_trades": 10,
            "pf_required": 2.0,
            "wr_required": 65,
            "critical_runtime_failures": 0,
            "status": "READY",
        },
        "day_4_5_shadow_live": {
            "live_feed_only": True,
            "real_orders": False,
            "telegram_speed_tracking": True,
            "approval_timing_tracking": True,
            "status": "READY",
        },
        "day_6_7_micro_live": {
            "real_account_allowed": True,
            "risk_percent": status.get("config", {}).get("risk_percent", 0.1),
            "max_trades_per_day": status.get("config", {}).get("max_trades_per_day", 2),
            "human_executes": True,
            "autonomous_orders": False,
            "status": "READY",
        },
        "deployment_ready": emergency_ready(status),
    }


def emergency_ready(status: dict[str, Any]) -> bool:
    """Return whether emergency deployment restrictions are ready."""
    return (
        bool(status.get("config", {}).get("enabled", False))
        and bool(status.get("risk_lock", {}).get("locked", False))
        and bool(status.get("grade_lock", {}).get("locked", False))
        and bool(status.get("symbol_lock", {}).get("locked", False))
        and bool(status.get("no_autonomous_execution", False))
        and not bool(status.get("broker_order_submission_allowed", True))
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
