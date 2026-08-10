"""
Unit tests for ReasonLedger (O2A)

Tests validate:
- ReasonEntry creation and validation
- ReasonLedger creation and lifecycle
- Adding entries (veto, penalty, scaling, check, info)
- Primary reason selection
- Data export (to_dict)
- Live vs. replay mode constraints
- No decision logic in ledger
- Enums and constants stability
"""

import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.shared.reason_ledger import (
    DecisionMode,
    DecisionType,
    ReasonTier,
    ReasonAction,
    ReasonEntry,
    ReasonLedger,
    current_timestamp,
)


class TestReasonEntry:
    """Test individual reason entry."""

    def test_create_valid_veto_entry(self):
        """Create a valid veto entry."""
        entry = ReasonEntry(
            tier=ReasonTier.TIER_1_SAFETY,
            action=ReasonAction.VETO,
            reason_code="DAILY_LOSS_EXCEEDED",
            severity=1.0,
            message="Daily loss limit breached",
            confidence_before=85,
            confidence_after=0,
            risk_before=0.003,
            risk_after=0.0,
        )
        assert entry.tier == ReasonTier.TIER_1_SAFETY
        assert entry.action == ReasonAction.VETO
        assert entry.reason_code == "DAILY_LOSS_EXCEEDED"
        assert entry.severity == 1.0

    def test_create_penalty_entry(self):
        """Create a penalty entry."""
        entry = ReasonEntry(
            tier=ReasonTier.TIER_2B_MACRO_CONFIDENCE,
            action=ReasonAction.PENALTY,
            reason_code="WEAK_MACRO_ALIGNMENT",
            severity=0.3,
            message="Macro alignment weak",
            confidence_before=85,
            confidence_after=75,
            risk_before=0.003,
            risk_after=0.003,
        )
        assert entry.action == ReasonAction.PENALTY
        assert entry.confidence_after < entry.confidence_before

    def test_severity_validation_zero(self):
        """Severity 0.0 is valid."""
        entry = ReasonEntry(
            tier=ReasonTier.TIER_5_LIFECYCLE,
            action=ReasonAction.INFO,
            reason_code="INFO_MESSAGE",
            severity=0.0,
            message="Just info",
            confidence_before=85,
            confidence_after=85,
            risk_before=0.003,
            risk_after=0.003,
        )
        assert entry.severity == 0.0

    def test_severity_validation_one(self):
        """Severity 1.0 is valid."""
        entry = ReasonEntry(
            tier=ReasonTier.TIER_1_SAFETY,
            action=ReasonAction.VETO,
            reason_code="FATAL",
            severity=1.0,
            message="Fatal issue",
            confidence_before=85,
            confidence_after=0,
            risk_before=0.003,
            risk_after=0.0,
        )
        assert entry.severity == 1.0

    def test_severity_validation_negative_fails(self):
        """Negative severity raises error."""
        with pytest.raises(ValueError, match="Severity must be 0.0-1.0"):
            ReasonEntry(
                tier=ReasonTier.TIER_3A_STRUCTURAL_VALIDITY,
                action=ReasonAction.VETO,
                reason_code="TEST",
                severity=-0.1,
                message="Bad severity",
                confidence_before=85,
                confidence_after=75,
                risk_before=0.003,
                risk_after=0.003,
            )

    def test_severity_validation_over_one_fails(self):
        """Severity > 1.0 raises error."""
        with pytest.raises(ValueError, match="Severity must be 0.0-1.0"):
            ReasonEntry(
                tier=ReasonTier.TIER_3A_STRUCTURAL_VALIDITY,
                action=ReasonAction.VETO,
                reason_code="TEST",
                severity=1.1,
                message="Bad severity",
                confidence_before=85,
                confidence_after=75,
                risk_before=0.003,
                risk_after=0.003,
            )

    def test_confidence_validation_negative_fails(self):
        """Negative confidence raises error."""
        with pytest.raises(ValueError, match="confidence_before must be 0-100"):
            ReasonEntry(
                tier=ReasonTier.TIER_3A_STRUCTURAL_VALIDITY,
                action=ReasonAction.VETO,
                reason_code="TEST",
                severity=0.5,
                message="Bad confidence",
                confidence_before=-1,
                confidence_after=75,
                risk_before=0.003,
                risk_after=0.003,
            )

    def test_confidence_validation_over_100_fails(self):
        """Confidence > 100 raises error."""
        with pytest.raises(ValueError, match="confidence_before must be 0-100"):
            ReasonEntry(
                tier=ReasonTier.TIER_3A_STRUCTURAL_VALIDITY,
                action=ReasonAction.VETO,
                reason_code="TEST",
                severity=0.5,
                message="Bad confidence",
                confidence_before=101,
                confidence_after=75,
                risk_before=0.003,
                risk_after=0.003,
            )

    def test_risk_validation_negative_fails(self):
        """Negative risk raises error."""
        with pytest.raises(ValueError, match="Risk cannot be negative"):
            ReasonEntry(
                tier=ReasonTier.TIER_3A_STRUCTURAL_VALIDITY,
                action=ReasonAction.VETO,
                reason_code="TEST",
                severity=0.5,
                message="Bad risk",
                confidence_before=85,
                confidence_after=75,
                risk_before=-0.001,
                risk_after=0.003,
            )

    def test_entry_to_dict(self):
        """Export entry as dictionary."""
        entry = ReasonEntry(
            tier=ReasonTier.TIER_2A_MACRO_TRUTH,
            action=ReasonAction.VETO,
            reason_code="MACRO_OPPOSITE",
            severity=0.8,
            message="HTF narrative opposite to setup",
            confidence_before=85,
            confidence_after=40,
            risk_before=0.003,
            risk_after=0.001,
            metadata={"narrative_phase": "distribution"},
        )
        data = entry.to_dict()
        assert isinstance(data, dict)
        assert data["tier"] == "tier_2a_macro_truth"
        assert data["action"] == "veto"
        assert data["reason_code"] == "MACRO_OPPOSITE"
        assert data["severity"] == 0.8
        assert data["metadata"]["narrative_phase"] == "distribution"


class TestReasonLedger:
    """Test complete reason ledger."""

    def test_create_replay_ledger(self):
        """Create a replay-mode ledger."""
        ledger = ReasonLedger(
            symbol="US30",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.REPLAY,
            decision=DecisionType.ADMIT,
            confidence_original=85,
            confidence_final=85,
            risk_original=0.003,
            risk_final=0.003,
        )
        assert ledger.symbol == "US30"
        assert ledger.mode == DecisionMode.REPLAY
        assert ledger.decision == DecisionType.ADMIT

    def test_create_live_ledger(self):
        """Create a live-mode ledger."""
        ledger = ReasonLedger(
            symbol="XAUUSD",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.LIVE,
            decision=DecisionType.REJECT,
            confidence_original=75,
            confidence_final=45,
            risk_original=0.0015,
            risk_final=0.0005,
        )
        assert ledger.mode == DecisionMode.LIVE
        assert ledger.decision == DecisionType.REJECT

    def test_live_mode_cannot_have_replay_outcome(self):
        """Live mode ledger cannot store replay_outcome_status."""
        with pytest.raises(ValueError, match="Live mode decision must not contain replay_outcome_status"):
            ReasonLedger(
                symbol="US30",
                timestamp="2026-07-01T14:30:00Z",
                mode=DecisionMode.LIVE,
                decision=DecisionType.ADMIT,
                confidence_original=85,
                confidence_final=85,
                risk_original=0.003,
                risk_final=0.003,
                replay_outcome_status="would_have_won",  # Invalid for live!
            )

    def test_replay_mode_can_have_replay_outcome(self):
        """Replay mode ledger can store replay_outcome_status."""
        ledger = ReasonLedger(
            symbol="US30",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.REPLAY,
            decision=DecisionType.REJECT,
            confidence_original=85,
            confidence_final=45,
            risk_original=0.003,
            risk_final=0.0005,
            replay_outcome_status="would_have_won",
        )
        assert ledger.replay_outcome_status == "would_have_won"

    def test_add_veto_entry(self):
        """Add a veto entry to ledger."""
        ledger = ReasonLedger(
            symbol="US30",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.LIVE,
            decision=DecisionType.REJECT,
            confidence_original=85,
            confidence_final=0,
            risk_original=0.003,
            risk_final=0.0,
        )
        ledger.add_veto(
            tier=ReasonTier.TIER_1_SAFETY,
            reason_code="DAILY_LOSS_EXCEEDED",
            severity=1.0,
            message="Daily loss limit exceeded",
            confidence_before=85,
            confidence_after=0,
            risk_before=0.003,
            risk_after=0.0,
        )
        assert len(ledger.entries) == 1
        assert ledger.entries[0].action == ReasonAction.VETO

    def test_add_penalty_entry(self):
        """Add a penalty entry."""
        ledger = ReasonLedger(
            symbol="XAUUSD",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.REPLAY,
            decision=DecisionType.REJECT,
            confidence_original=80,
            confidence_final=50,
            risk_original=0.0015,
            risk_final=0.0015,
        )
        ledger.add_penalty(
            tier=ReasonTier.TIER_2B_MACRO_CONFIDENCE,
            reason_code="WEAK_MACRO",
            severity=0.4,
            message="Weak macro alignment",
            confidence_before=80,
            confidence_after=50,
            risk_before=0.0015,
            risk_after=0.0015,
        )
        assert len(ledger.entries) == 1
        assert ledger.entries[0].action == ReasonAction.PENALTY

    def test_add_scaling_entry(self):
        """Add a scaling (risk reduction) entry."""
        ledger = ReasonLedger(
            symbol="NAS100",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.LIVE,
            decision=DecisionType.ADMIT,
            confidence_original=85,
            confidence_final=85,
            risk_original=0.003,
            risk_final=0.0015,
        )
        ledger.add_scaling(
            tier=ReasonTier.TIER_3B_SETUP_QUALITY,
            reason_code="WEAK_FVG_QUALITY",
            severity=0.2,
            message="FVG quality weak, reduced risk",
            confidence_before=85,
            confidence_after=85,
            risk_before=0.003,
            risk_after=0.0015,
        )
        assert ledger.entries[0].action == ReasonAction.SCALING

    def test_add_check_entry(self):
        """Add an informational check entry."""
        ledger = ReasonLedger(
            symbol="US30",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.REPLAY,
            decision=DecisionType.ADMIT,
            confidence_original=85,
            confidence_final=85,
            risk_original=0.003,
            risk_final=0.003,
        )
        ledger.add_check(
            tier=ReasonTier.TIER_3A_STRUCTURAL_VALIDITY,
            reason_code="MSS_CONFIRMED",
            severity=0.0,
            message="MSS confirmed (non-blocking check)",
            confidence_before=85,
            confidence_after=85,
            risk_before=0.003,
            risk_after=0.003,
        )
        assert ledger.entries[0].action == ReasonAction.CHECK

    def test_add_info_entry(self):
        """Add informational entry."""
        ledger = ReasonLedger(
            symbol="EURUSD",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.LIVE,
            decision=DecisionType.ADMIT,
            confidence_original=90,
            confidence_final=90,
            risk_original=0.002,
            risk_final=0.002,
        )
        ledger.add_info(
            tier=ReasonTier.TIER_5_LIFECYCLE,
            reason_code="TRADE_JOURNAL",
            message="Trade logged to journal",
        )
        assert ledger.entries[0].action == ReasonAction.INFO
        assert ledger.entries[0].severity == 0.0

    def test_primary_reason_veto_first(self):
        """Primary reason returns first veto if present."""
        ledger = ReasonLedger(
            symbol="US30",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.LIVE,
            decision=DecisionType.REJECT,
            confidence_original=85,
            confidence_final=10,
            risk_original=0.003,
            risk_final=0.001,
        )
        # Add a penalty first
        ledger.add_penalty(
            tier=ReasonTier.TIER_2B_MACRO_CONFIDENCE,
            reason_code="WEAK_MACRO",
            severity=0.3,
            message="Weak macro",
            confidence_before=85,
            confidence_after=70,
            risk_before=0.003,
            risk_after=0.003,
        )
        # Then add a veto
        ledger.add_veto(
            tier=ReasonTier.TIER_3A_STRUCTURAL_VALIDITY,
            reason_code="MSS_ABSENT",
            severity=0.9,
            message="MSS not detected",
            confidence_before=70,
            confidence_after=10,
            risk_before=0.003,
            risk_after=0.001,
        )
        # Primary reason should be the veto, not the penalty
        assert ledger.primary_reason() == "MSS_ABSENT"

    def test_primary_reason_no_veto(self):
        """Primary reason returns highest-severity penalty if no veto."""
        ledger = ReasonLedger(
            symbol="XAUUSD",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.REPLAY,
            decision=DecisionType.ADMIT,
            confidence_original=85,
            confidence_final=60,
            risk_original=0.0015,
            risk_final=0.001,
        )
        # Add low-severity penalty
        ledger.add_penalty(
            tier=ReasonTier.TIER_2B_MACRO_CONFIDENCE,
            reason_code="WEAK_MACRO",
            severity=0.2,
            message="Weak macro",
            confidence_before=85,
            confidence_after=75,
            risk_before=0.0015,
            risk_after=0.0015,
        )
        # Add higher-severity penalty
        ledger.add_penalty(
            tier=ReasonTier.TIER_3B_SETUP_QUALITY,
            reason_code="WEAK_FVG",
            severity=0.5,
            message="Weak FVG quality",
            confidence_before=75,
            confidence_after=60,
            risk_before=0.0015,
            risk_after=0.001,
        )
        # Primary reason should be the higher-severity one
        assert ledger.primary_reason() == "WEAK_FVG"

    def test_primary_reason_none_if_empty(self):
        """Primary reason returns None if no entries."""
        ledger = ReasonLedger(
            symbol="US30",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.LIVE,
            decision=DecisionType.ADMIT,
            confidence_original=85,
            confidence_final=85,
            risk_original=0.003,
            risk_final=0.003,
        )
        assert ledger.primary_reason() is None

    def test_vetoes_filter(self):
        """Vetoes() returns only veto entries."""
        ledger = ReasonLedger(
            symbol="US30",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.LIVE,
            decision=DecisionType.REJECT,
            confidence_original=85,
            confidence_final=0,
            risk_original=0.003,
            risk_final=0.0,
        )
        ledger.add_veto(
            tier=ReasonTier.TIER_1_SAFETY,
            reason_code="DAILY_LOSS",
            severity=1.0,
            message="Daily loss hit",
            confidence_before=85,
            confidence_after=0,
            risk_before=0.003,
            risk_after=0.0,
        )
        ledger.add_penalty(
            tier=ReasonTier.TIER_2B_MACRO_CONFIDENCE,
            reason_code="WEAK_MACRO",
            severity=0.3,
            message="Weak macro",
            confidence_before=0,
            confidence_after=0,
            risk_before=0.0,
            risk_after=0.0,
        )
        assert len(ledger.vetoes()) == 1
        assert len(ledger.penalties()) == 1

    def test_penalties_filter(self):
        """Penalties() returns only penalty entries."""
        ledger = ReasonLedger(
            symbol="XAUUSD",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.REPLAY,
            decision=DecisionType.ADMIT,
            confidence_original=80,
            confidence_final=60,
            risk_original=0.0015,
            risk_final=0.0015,
        )
        ledger.add_penalty(
            tier=ReasonTier.TIER_2B_MACRO_CONFIDENCE,
            reason_code="WEAK_MACRO_1",
            severity=0.3,
            message="Weak macro 1",
            confidence_before=80,
            confidence_after=70,
            risk_before=0.0015,
            risk_after=0.0015,
        )
        ledger.add_penalty(
            tier=ReasonTier.TIER_3B_SETUP_QUALITY,
            reason_code="WEAK_SETUP_1",
            severity=0.2,
            message="Weak setup",
            confidence_before=70,
            confidence_after=60,
            risk_before=0.0015,
            risk_after=0.0015,
        )
        assert len(ledger.penalties()) == 2

    def test_scalings_filter(self):
        """Scalings() returns only scaling entries."""
        ledger = ReasonLedger(
            symbol="NAS100",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.LIVE,
            decision=DecisionType.ADMIT,
            confidence_original=85,
            confidence_final=85,
            risk_original=0.003,
            risk_final=0.0015,
        )
        ledger.add_scaling(
            tier=ReasonTier.TIER_3B_SETUP_QUALITY,
            reason_code="WEAK_FVG",
            severity=0.2,
            message="Weak FVG",
            confidence_before=85,
            confidence_after=85,
            risk_before=0.003,
            risk_after=0.0015,
        )
        assert len(ledger.scalings()) == 1

    def test_checks_filter(self):
        """Checks() returns only check entries."""
        ledger = ReasonLedger(
            symbol="US30",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.REPLAY,
            decision=DecisionType.ADMIT,
            confidence_original=85,
            confidence_final=85,
            risk_original=0.003,
            risk_final=0.003,
        )
        ledger.add_check(
            tier=ReasonTier.TIER_3A_STRUCTURAL_VALIDITY,
            reason_code="STRUCTURE_OK",
            severity=0.0,
            message="Structure validated",
            confidence_before=85,
            confidence_after=85,
            risk_before=0.003,
            risk_after=0.003,
        )
        assert len(ledger.checks()) == 1

    def test_ledger_to_dict(self):
        """Export full ledger as dictionary."""
        ledger = ReasonLedger(
            symbol="US30",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.LIVE,
            decision=DecisionType.REJECT,
            confidence_original=85,
            confidence_final=30,
            confidence_components={"daily_bias": 15, "h4_narrative": 20, "liquidity": 20, "mss": 20},
            risk_original=0.003,
            risk_final=0.001,
            portfolio_state={"existing_us30_trades": 1},
        )
        ledger.add_veto(
            tier=ReasonTier.TIER_2A_MACRO_TRUTH,
            reason_code="HTF_NARRATIVE_OPPOSITE",
            severity=0.8,
            message="HTF narrative opposite",
            confidence_before=85,
            confidence_after=30,
            risk_before=0.003,
            risk_after=0.001,
        )
        data = ledger.to_dict()
        assert isinstance(data, dict)
        assert data["symbol"] == "US30"
        assert data["mode"] == "live"
        assert data["decision"] == "reject"
        assert data["confidence_original"] == 85
        assert data["confidence_final"] == 30
        assert data["confidence_components"]["mss"] == 20
        assert len(data["entries"]) == 1
        assert data["primary_reason"] == "HTF_NARRATIVE_OPPOSITE"
        assert data["veto_count"] == 1

    def test_finalize_method(self):
        """Finalize method can be called (currently no-op)."""
        ledger = ReasonLedger(
            symbol="XAUUSD",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.REPLAY,
            decision=DecisionType.ADMIT,
            confidence_original=85,
            confidence_final=85,
            risk_original=0.0015,
            risk_final=0.0015,
        )
        ledger.finalize()  # Should not raise


class TestEnumsAndConstants:
    """Test enums and constants."""

    def test_decision_modes_exist(self):
        """All decision modes are defined."""
        assert DecisionMode.REPLAY.value == "replay"
        assert DecisionMode.LIVE.value == "live"
        assert DecisionMode.DEMO.value == "demo"
        assert DecisionMode.BACKTEST.value == "backtest"
        assert DecisionMode.PAPER.value == "paper"

    def test_decision_types_exist(self):
        """All decision types are defined."""
        assert DecisionType.ADMIT.value == "admit"
        assert DecisionType.REJECT.value == "reject"
        assert DecisionType.PENDING.value == "pending"

    def test_reason_tiers_exist(self):
        """All reason tiers are defined."""
        assert ReasonTier.TIER_1_SAFETY.value == "tier_1_safety"
        assert ReasonTier.TIER_2A_MACRO_TRUTH.value == "tier_2a_macro_truth"
        assert ReasonTier.TIER_2B_MACRO_CONFIDENCE.value == "tier_2b_macro_confidence"
        assert ReasonTier.TIER_3A_STRUCTURAL_VALIDITY.value == "tier_3a_structural_validity"
        assert ReasonTier.TIER_3B_SETUP_QUALITY.value == "tier_3b_setup_quality"
        assert ReasonTier.TIER_4A_PORTFOLIO_ADMISSION.value == "tier_4a_portfolio_admission"
        assert ReasonTier.TIER_4B_EXECUTION_OPTIMIZATION.value == "tier_4b_execution_optimization"
        assert ReasonTier.TIER_5_LIFECYCLE.value == "tier_5_lifecycle"

    def test_reason_actions_exist(self):
        """All reason actions are defined."""
        assert ReasonAction.VETO.value == "veto"
        assert ReasonAction.PENALTY.value == "penalty"
        assert ReasonAction.SCALING.value == "scaling"
        assert ReasonAction.CHECK.value == "check"
        assert ReasonAction.INFO.value == "info"


class TestNoDecisionLogic:
    """Verify ledger contains no decision logic."""

    def test_ledger_does_not_decide(self):
        """Ledger records reasons but does not decide trades."""
        # Ledger is pure data structure; it has no methods that decide YES/NO
        ledger = ReasonLedger(
            symbol="US30",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.REPLAY,
            decision=DecisionType.PENDING,  # User sets this externally
            confidence_original=85,
            confidence_final=85,
            risk_original=0.003,
            risk_final=0.003,
        )
        # Ledger methods only add entries and query them
        ledger.add_info(
            tier=ReasonTier.TIER_5_LIFECYCLE,
            reason_code="TEST",
            message="Test message",
        )
        # No method that computes decision (ADMIT vs REJECT)
        assert not hasattr(ledger, "compute_decision")
        assert not hasattr(ledger, "should_admit")
        assert not hasattr(ledger, "should_reject")

    def test_ledger_does_not_modify_confidence(self):
        """Ledger records confidence but does not calculate it."""
        ledger = ReasonLedger(
            symbol="US30",
            timestamp="2026-07-01T14:30:00Z",
            mode=DecisionMode.LIVE,
            decision=DecisionType.ADMIT,
            confidence_original=85,
            confidence_final=85,  # User sets final confidence
            risk_original=0.003,
            risk_final=0.003,
        )
        # Ledger has no method that changes confidence_final
        assert not hasattr(ledger, "apply_penalties")
        assert not hasattr(ledger, "calculate_confidence")
        assert not hasattr(ledger, "adjust_for_guardrails")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
