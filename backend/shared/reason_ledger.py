"""
Reason Ledger: Structured tracking of admission decisions and rejection rationales.

Per SENTINEL_CONSTITUTION.md Law 4:
  "Every rejection must be measurable."
  "Every rejection must produce a reason code, tier, severity, confidence-before,
   confidence-after, risk-before, risk-after, and replay/live outcome status."

This module is data-structure only. It does not contain decision logic, does not
route to engines, and does not modify confidence scoring or guardrails.

CONSTRAINT: This module records reasoning. It does NOT decide trades.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from zoneinfo import ZoneInfo


# ============================================================================
# SPEC COMPLIANT: Enums and Constants
# ============================================================================

class DecisionMode(Enum):
    """Mode in which the decision was made."""
    REPLAY = "replay"      # Historical backtest/causal replay
    LIVE = "live"          # Real-time live trading
    DEMO = "demo"          # Demo account
    BACKTEST = "backtest"  # Full backtest (non-causal)
    PAPER = "paper"        # Paper trading


class DecisionType(Enum):
    """Type of decision outcome."""
    ADMIT = "admit"        # Setup approved for execution
    REJECT = "reject"      # Setup rejected, not executed
    PENDING = "pending"    # Decision in progress (for testing)


class ReasonTier(Enum):
    """Constitutional tier where reason originated."""
    TIER_1_SAFETY = "tier_1_safety"
    TIER_2A_MACRO_TRUTH = "tier_2a_macro_truth"
    TIER_2B_MACRO_CONFIDENCE = "tier_2b_macro_confidence"
    TIER_3A_STRUCTURAL_VALIDITY = "tier_3a_structural_validity"
    TIER_3B_SETUP_QUALITY = "tier_3b_setup_quality"
    TIER_4A_PORTFOLIO_ADMISSION = "tier_4a_portfolio_admission"
    TIER_4B_EXECUTION_OPTIMIZATION = "tier_4b_execution_optimization"
    TIER_5_LIFECYCLE = "tier_5_lifecycle"


class ReasonAction(Enum):
    """What action the reason took."""
    VETO = "veto"          # Hard block (prevents admission)
    PENALTY = "penalty"    # Reduced confidence/risk (allows admission with modifier)
    SCALING = "scaling"    # Position size reduction (proportional modifier)
    CHECK = "check"        # Information check (passed or failed, non-blocking)
    INFO = "info"          # Informational only (no decision impact)


# ============================================================================
# SPEC COMPLIANT: ReasonEntry Dataclass
# ============================================================================

@dataclass
class ReasonEntry:
    """
    One entry in the reason ledger: a single reason for a decision outcome.

    Per Law 4: Every rejection must include tier, action, reason_code, severity,
    confidence_before/after, risk_before/after.
    """

    # Decision classification
    tier: ReasonTier
    action: ReasonAction
    reason_code: str  # e.g., "MSS_ABSENT", "DAILY_LOSS_EXCEEDED", "WEAK_SMT"

    # Severity: 0.0 (negligible) to 1.0 (fatal)
    severity: float

    # Human-readable message
    message: str

    # Confidence impact (absolute points, 0-100)
    confidence_before: int
    confidence_after: int

    # Risk allocation impact (as % of account)
    risk_before: float
    risk_after: float

    # Optional structured metadata (for future extensibility)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate entry consistency."""
        if not (0.0 <= self.severity <= 1.0):
            raise ValueError(f"Severity must be 0.0-1.0, got {self.severity}")

        if not (0 <= self.confidence_before <= 100):
            raise ValueError(f"confidence_before must be 0-100, got {self.confidence_before}")

        if not (0 <= self.confidence_after <= 100):
            raise ValueError(f"confidence_after must be 0-100, got {self.confidence_after}")

        if self.risk_before < 0.0 or self.risk_after < 0.0:
            raise ValueError(f"Risk cannot be negative: before={self.risk_before}, after={self.risk_after}")

    def to_dict(self) -> dict[str, Any]:
        """Export entry as dictionary."""
        return {
            "tier": self.tier.value,
            "action": self.action.value,
            "reason_code": self.reason_code,
            "severity": self.severity,
            "message": self.message,
            "confidence_before": self.confidence_before,
            "confidence_after": self.confidence_after,
            "risk_before": self.risk_before,
            "risk_after": self.risk_after,
            "metadata": self.metadata,
        }


# ============================================================================
# SPEC COMPLIANT: ReasonLedger Dataclass
# ============================================================================

@dataclass
class ReasonLedger:
    """
    Complete ledger of reasons for a single decision.

    Records all tiers, all checks, all penalties, all vetos for traceability
    and root-cause analysis (QAER/FRR calculation in O2.5).
    """

    # Decision identity (non-default)
    symbol: str
    timestamp: str  # ISO8601
    mode: DecisionMode

    # Decision outcome (non-default)
    decision: DecisionType

    # Confidence tracking (non-default)
    confidence_original: int  # Before any penalties
    confidence_final: int     # After all penalties

    # Risk tracking (non-default)
    risk_original: float
    risk_final: float

    # Confidence components (default)
    confidence_components: dict[str, int] = field(default_factory=dict)  # Per Law 7

    # Portfolio context (default)
    portfolio_state: dict[str, Any] = field(default_factory=dict)

    # Replay-only outcome (default, must not be used in live decisions)
    replay_outcome_status: Optional[str] = None  # "would_have_won", "would_have_lost", "inconclusive"
    live_outcome_status: Optional[str] = None    # "executed", "rejected", "pending", "unknown"

    # Entries: all reasons for this decision (default)
    entries: list[ReasonEntry] = field(default_factory=list)

    def __post_init__(self):
        """Validate ledger consistency."""
        if self.mode == DecisionMode.LIVE and self.replay_outcome_status is not None:
            raise ValueError(
                "Live mode decision must not contain replay_outcome_status. "
                "Replay outcomes are only valid in REPLAY mode."
            )

    def add_veto(
        self,
        tier: ReasonTier,
        reason_code: str,
        severity: float,
        message: str,
        confidence_before: int,
        confidence_after: int,
        risk_before: float,
        risk_after: float,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Add a veto (hard block) entry."""
        entry = ReasonEntry(
            tier=tier,
            action=ReasonAction.VETO,
            reason_code=reason_code,
            severity=severity,
            message=message,
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            risk_before=risk_before,
            risk_after=risk_after,
            metadata=metadata or {},
        )
        self.entries.append(entry)

    def add_penalty(
        self,
        tier: ReasonTier,
        reason_code: str,
        severity: float,
        message: str,
        confidence_before: int,
        confidence_after: int,
        risk_before: float,
        risk_after: float,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Add a penalty (reduced confidence/risk) entry."""
        entry = ReasonEntry(
            tier=tier,
            action=ReasonAction.PENALTY,
            reason_code=reason_code,
            severity=severity,
            message=message,
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            risk_before=risk_before,
            risk_after=risk_after,
            metadata=metadata or {},
        )
        self.entries.append(entry)

    def add_scaling(
        self,
        tier: ReasonTier,
        reason_code: str,
        severity: float,
        message: str,
        confidence_before: int,
        confidence_after: int,
        risk_before: float,
        risk_after: float,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Add a scaling (position size reduction) entry."""
        entry = ReasonEntry(
            tier=tier,
            action=ReasonAction.SCALING,
            reason_code=reason_code,
            severity=severity,
            message=message,
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            risk_before=risk_before,
            risk_after=risk_after,
            metadata=metadata or {},
        )
        self.entries.append(entry)

    def add_check(
        self,
        tier: ReasonTier,
        reason_code: str,
        severity: float,
        message: str,
        confidence_before: int,
        confidence_after: int,
        risk_before: float,
        risk_after: float,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Add an informational check (non-blocking)."""
        entry = ReasonEntry(
            tier=tier,
            action=ReasonAction.CHECK,
            reason_code=reason_code,
            severity=severity,
            message=message,
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            risk_before=risk_before,
            risk_after=risk_after,
            metadata=metadata or {},
        )
        self.entries.append(entry)

    def add_info(
        self,
        tier: ReasonTier,
        reason_code: str,
        message: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Add informational entry (no confidence/risk impact)."""
        entry = ReasonEntry(
            tier=tier,
            action=ReasonAction.INFO,
            reason_code=reason_code,
            severity=0.0,
            message=message,
            confidence_before=self.confidence_final,
            confidence_after=self.confidence_final,
            risk_before=self.risk_final,
            risk_after=self.risk_final,
            metadata=metadata or {},
        )
        self.entries.append(entry)

    def finalize(self) -> None:
        """Mark ledger as finalized (optional validation checkpoint)."""
        # Currently a no-op. Could add validation or state tracking if needed.
        pass

    def primary_reason(self) -> Optional[str]:
        """
        Return the most critical reason code (first veto, then highest severity).

        Per Law 4: Every rejection should have a primary reason.
        """
        # Vetos first (highest priority)
        for entry in self.entries:
            if entry.action == ReasonAction.VETO:
                return entry.reason_code

        # Then highest severity penalty
        max_severity = -1.0
        max_code = None
        for entry in self.entries:
            if entry.action == ReasonAction.PENALTY and entry.severity > max_severity:
                max_severity = entry.severity
                max_code = entry.reason_code

        if max_code:
            return max_code

        # Fallback
        return None

    def vetoes(self) -> list[ReasonEntry]:
        """Return all veto entries."""
        return [e for e in self.entries if e.action == ReasonAction.VETO]

    def penalties(self) -> list[ReasonEntry]:
        """Return all penalty entries."""
        return [e for e in self.entries if e.action == ReasonAction.PENALTY]

    def scalings(self) -> list[ReasonEntry]:
        """Return all scaling entries."""
        return [e for e in self.entries if e.action == ReasonAction.SCALING]

    def checks(self) -> list[ReasonEntry]:
        """Return all check entries."""
        return [e for e in self.entries if e.action == ReasonAction.CHECK]

    def infos(self) -> list[ReasonEntry]:
        """Return all informational entries."""
        return [e for e in self.entries if e.action == ReasonAction.INFO]

    def to_dict(self) -> dict[str, Any]:
        """Export ledger as dictionary (JSON-ready)."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "mode": self.mode.value,
            "decision": self.decision.value,
            "confidence_original": self.confidence_original,
            "confidence_final": self.confidence_final,
            "confidence_components": self.confidence_components,
            "risk_original": self.risk_original,
            "risk_final": self.risk_final,
            "portfolio_state": self.portfolio_state,
            "replay_outcome_status": self.replay_outcome_status,
            "live_outcome_status": self.live_outcome_status,
            "entries": [e.to_dict() for e in self.entries],
            "primary_reason": self.primary_reason(),
            "veto_count": len(self.vetoes()),
            "penalty_count": len(self.penalties()),
            "scaling_count": len(self.scalings()),
        }


# ============================================================================
# DEV PROPOSALS — REQUIRES APPROVAL
# ============================================================================

# PROPOSED — NOT CANONICAL: Default timezone for timestamps
DEFAULT_TIMEZONE = ZoneInfo("UTC")

# PROPOSED — NOT CANONICAL: Helper to create timestamp
def current_timestamp() -> str:
    """Return current time in ISO8601 format."""
    return datetime.now(DEFAULT_TIMEZONE).isoformat()


if __name__ == "__main__":
    # Quick validation on module load
    print("✓ ReasonLedger module loaded successfully")
    print(f"✓ DecisionMode: {[m.value for m in DecisionMode]}")
    print(f"✓ DecisionType: {[t.value for t in DecisionType]}")
    print(f"✓ ReasonTier: {[tier.value for tier in ReasonTier]}")
    print(f"✓ ReasonAction: {[action.value for action in ReasonAction]}")
