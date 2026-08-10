"""Tests for O2B: SDA Tier Wrapper Without Behavior Change.

Tests verify that:
1. Tier hierarchy is correctly structured (tiers evaluate in order)
2. Tier outcomes match current SDA decisions (behavior preserved)
3. Reason classification into tiers is consistent
4. Edge cases are handled identically to pre-O2B
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from backend.shared.shared_decision_adapter import SharedDecisionAdapter, DecisionRequest
from backend.shared.sda_tier_authority import TierAuthority
from backend.shared.reason_ledger import DecisionMode, ReasonTier, ReasonAction


class TestTierAuthorityCreation:
    """Test TierAuthority initialization and basic structure."""

    def test_tier_authority_init_with_engine(self):
        """TierAuthority can be created with a confidence engine."""
        mock_engine = Mock()
        authority = TierAuthority(confidence_engine=mock_engine)
        assert authority.confidence_engine is mock_engine
        assert authority.reason_entries == []

    def test_tier_authority_init_without_engine(self):
        """TierAuthority can be created without engine (graceful fail)."""
        authority = TierAuthority(confidence_engine=None)
        assert authority.confidence_engine is None


class TestReasonClassification:
    """Test that rejection reasons are correctly classified into tiers."""

    def test_classify_tier_1_daily_loss(self):
        """Daily loss reasons classified as Tier 1."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = ["Daily loss limit hit"]
        authority = TierAuthority(confidence_engine=mock_engine)

        result = authority.evaluate_all_tiers(
            symbol="US30",
            trend={"daily_bias": "bullish"},
            liquidity={},
            ict={},
            scores={},
            total_confidence=50,
            context={},
            direction="bullish",
        )

        assert result["tier_1_pass"] is False
        assert len(result["tier_breakdown"]["tier_1"]) > 0
        assert "Daily loss limit hit" in result["tier_breakdown"]["tier_1"]

    def test_classify_tier_1_news_lock(self):
        """News lock classified as Tier 1."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = ["High impact news lock active"]
        authority = TierAuthority(confidence_engine=mock_engine)

        result = authority.evaluate_all_tiers(
            symbol="US30",
            trend={},
            liquidity={},
            ict={},
            scores={},
            total_confidence=50,
            context={},
            direction=None,
        )

        assert result["tier_1_pass"] is False
        assert "High impact news lock active" in result["tier_breakdown"]["tier_1"]

    def test_classify_tier_3a_mss_absent(self):
        """MSS absence classified as Tier 3A."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = ["MSS not confirmed"]
        authority = TierAuthority(confidence_engine=mock_engine)

        result = authority.evaluate_all_tiers(
            symbol="US30",
            trend={},
            liquidity={},
            ict={},
            scores={},
            total_confidence=50,
            context={},
            direction=None,
        )

        assert result["tier_3a_pass"] is False
        assert "MSS not confirmed" in result["tier_breakdown"]["tier_3a"]

    def test_classify_tier_3a_fvg_not_detected(self):
        """FVG absence classified as Tier 3A."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = ["FVG not detected"]
        authority = TierAuthority(confidence_engine=mock_engine)

        result = authority.evaluate_all_tiers(
            symbol="US30",
            trend={},
            liquidity={},
            ict={},
            scores={},
            total_confidence=50,
            context={},
            direction=None,
        )

        assert result["tier_3a_pass"] is False
        assert "FVG not detected" in result["tier_breakdown"]["tier_3a"]

    def test_classify_multiple_reasons_across_tiers(self):
        """Multiple reasons classified into their respective tiers."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = [
            "Daily loss limit hit",
            "MSS not confirmed",
            "FVG not detected",
        ]
        authority = TierAuthority(confidence_engine=mock_engine)

        result = authority.evaluate_all_tiers(
            symbol="US30",
            trend={},
            liquidity={},
            ict={},
            scores={},
            total_confidence=50,
            context={},
            direction=None,
        )

        assert len(result["tier_breakdown"]["tier_1"]) == 1
        assert len(result["tier_breakdown"]["tier_3a"]) == 2
        assert result["tier_1_pass"] is False
        assert result["tier_3a_pass"] is False


class TestTierEvaluationOrdering:
    """Test that tiers evaluate in correct order."""

    def test_all_tiers_evaluated_no_engine(self):
        """With no engine, all tiers fail gracefully."""
        authority = TierAuthority(confidence_engine=None)
        result = authority.evaluate_all_tiers(
            symbol="US30",
            trend={},
            liquidity={},
            ict={},
            scores={},
            total_confidence=50,
            context={},
            direction=None,
        )

        assert result["tier_1_pass"] is False
        assert result["tier_2a_pass"] is False
        assert result["tier_3a_pass"] is False

    def test_tier_results_are_boolean_or_numeric(self):
        """Tier results are proper types (bool or float)."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = []
        authority = TierAuthority(confidence_engine=mock_engine)

        result = authority.evaluate_all_tiers(
            symbol="US30",
            trend={},
            liquidity={},
            ict={},
            scores={},
            total_confidence=50,
            context={},
            direction=None,
        )

        assert isinstance(result["tier_1_pass"], bool)
        assert isinstance(result["tier_2a_pass"], bool)
        assert isinstance(result["tier_3a_pass"], bool)
        assert isinstance(result["tier_4a_pass"], bool)
        assert isinstance(result["tier_2b_multiplier"], float)
        assert isinstance(result["tier_3b_multiplier"], float)


class TestSharedDecisionAdapterWithTiers:
    """Test SDA integration with tier authority."""

    def test_sda_init_with_tier_authority(self):
        """SDA can be initialized with TierAuthority."""
        mock_analyzer = Mock()
        mock_tier_auth = Mock()
        sda = SharedDecisionAdapter(
            confidence_analyzer=mock_analyzer,
            tier_authority=mock_tier_auth,
        )
        assert sda.confidence_analyzer is mock_analyzer
        assert sda.tier_authority is mock_tier_auth

    def test_sda_creates_default_tier_authority(self):
        """SDA creates default TierAuthority if not provided."""
        mock_analyzer = Mock()
        sda = SharedDecisionAdapter(confidence_analyzer=mock_analyzer)
        assert sda.tier_authority is not None
        assert isinstance(sda.tier_authority, TierAuthority)

    def test_sda_evaluate_includes_tier_result(self):
        """SDA evaluate() includes tier_evaluation in output."""
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = {
            "symbol": "US30",
            "decision": "APPROVED",
            "total_confidence": 80,
        }

        sda = SharedDecisionAdapter(confidence_analyzer=mock_analyzer)
        request = DecisionRequest(mode="LIVE", symbol="US30", context={})
        result = sda.evaluate(request)

        assert "decision" in result
        assert "tier_evaluation" in result["decision"]


class TestBehaviorPreservation:
    """Test that tier wrapper does NOT change decisions."""

    def test_approval_decision_unchanged(self):
        """APPROVED decision remains APPROVED after tier wrapping."""
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = {
            "symbol": "US30",
            "decision": "APPROVED",
            "total_confidence": 85,
            "rejection_reasons": [],
        }

        sda = SharedDecisionAdapter(confidence_analyzer=mock_analyzer)
        request = DecisionRequest(mode="LIVE", symbol="US30", context={})
        result = sda.evaluate(request)

        assert result["status"] == "PASS"
        assert result["decision"]["decision"] == "APPROVED"

    def test_rejection_decision_unchanged(self):
        """REJECTED decision remains REJECTED after tier wrapping."""
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = {
            "symbol": "US30",
            "decision": "REJECTED",
            "total_confidence": 45,
            "rejection_reasons": ["MSS not confirmed"],
        }

        sda = SharedDecisionAdapter(confidence_analyzer=mock_analyzer)
        request = DecisionRequest(mode="REPLAY", symbol="US30", context={})
        result = sda.evaluate(request)

        assert result["status"] == "PASS"
        assert result["decision"]["decision"] == "REJECTED"
        assert "MSS not confirmed" in result["decision"]["rejection_reasons"]

    def test_confidence_score_unchanged(self):
        """Total confidence value unchanged by tier wrapping."""
        mock_analyzer = Mock()
        original_confidence = 72
        mock_analyzer.analyze.return_value = {
            "symbol": "XAUUSD",
            "decision": "APPROVED",
            "total_confidence": original_confidence,
        }

        sda = SharedDecisionAdapter(confidence_analyzer=mock_analyzer)
        request = DecisionRequest(mode="BACKTEST", symbol="XAUUSD", context={})
        result = sda.evaluate(request)

        assert result["decision"]["total_confidence"] == original_confidence

    def test_explanation_unchanged(self):
        """Explanation field preserved through tier wrapping."""
        mock_analyzer = Mock()
        explanation = "Setup meets all criteria with strong SMT confirmation"
        mock_analyzer.analyze.return_value = {
            "symbol": "US30",
            "decision": "APPROVED",
            "explanation": explanation,
        }

        sda = SharedDecisionAdapter(confidence_analyzer=mock_analyzer)
        request = DecisionRequest(mode="LIVE", symbol="US30", context={})
        result = sda.evaluate(request)

        assert result["decision"]["explanation"] == explanation


class TestReasonEntryTracking:
    """Test TierAuthority reason entry tracking."""

    def test_add_reason_entry(self):
        """Can add reason entries to authority."""
        authority = TierAuthority()
        authority.add_reason_entry(
            tier=ReasonTier.TIER_1_SAFETY,
            action=ReasonAction.VETO,
            reason_code="DAILY_LOSS_EXCEEDED",
            severity=1.0,
            message="Daily loss limit reached",
            confidence_before=60,
            confidence_after=0,
            risk_before=0.3,
            risk_after=0.0,
        )

        assert len(authority.reason_entries) == 1
        assert authority.reason_entries[0].tier == ReasonTier.TIER_1_SAFETY
        assert authority.reason_entries[0].reason_code == "DAILY_LOSS_EXCEEDED"

    def test_clear_entries(self):
        """Can clear reason entries."""
        authority = TierAuthority()
        authority.add_reason_entry(
            tier=ReasonTier.TIER_1_SAFETY,
            action=ReasonAction.VETO,
            reason_code="TEST",
            severity=1.0,
            message="Test",
            confidence_before=50,
            confidence_after=50,
            risk_before=0.0,
            risk_after=0.0,
        )

        assert len(authority.reason_entries) == 1
        authority.clear_entries()
        assert len(authority.reason_entries) == 0


class TestTierMechanics:
    """Test tier pass/fail determination."""

    def test_no_reasons_all_tiers_pass(self):
        """With no rejection reasons, all tiers pass."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = []
        authority = TierAuthority(confidence_engine=mock_engine)

        result = authority.evaluate_all_tiers(
            symbol="US30",
            trend={},
            liquidity={},
            ict={},
            scores={},
            total_confidence=50,
            context={},
            direction=None,
        )

        assert result["tier_1_pass"] is True
        assert result["tier_2a_pass"] is True
        assert result["tier_3a_pass"] is True

    def test_tier_4a_defaults_to_pass(self):
        """Tier 4A (portfolio) defaults to pass (not implemented yet)."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = []
        authority = TierAuthority(confidence_engine=mock_engine)

        result = authority.evaluate_all_tiers(
            symbol="US30",
            trend={},
            liquidity={},
            ict={},
            scores={},
            total_confidence=50,
            context={},
            direction=None,
        )

        # Tier 4A doesn't exist yet, should pass
        assert result["tier_4a_pass"] is True

    def test_multipliers_default_to_no_penalty(self):
        """Tier 2B and 3B multipliers default to 1.0 (no penalty)."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = []
        authority = TierAuthority(confidence_engine=mock_engine)

        result = authority.evaluate_all_tiers(
            symbol="US30",
            trend={},
            liquidity={},
            ict={},
            scores={},
            total_confidence=50,
            context={},
            direction=None,
        )

        assert result["tier_2b_multiplier"] == 1.0
        assert result["tier_3b_multiplier"] == 1.0


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_rejection_reasons(self):
        """Empty rejection list handled correctly."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = []
        authority = TierAuthority(confidence_engine=mock_engine)

        result = authority.evaluate_all_tiers(
            symbol="US30",
            trend={},
            liquidity={},
            ict={},
            scores={},
            total_confidence=100,
            context={},
            direction="bullish",
        )

        assert result["all_tier_reasons"] == []
        assert result["tier_1_pass"] is True

    def test_unknown_rejection_reason(self):
        """Unknown rejection reason goes to 'other' category."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = ["Unknown custom rejection reason"]
        authority = TierAuthority(confidence_engine=mock_engine)

        result = authority.evaluate_all_tiers(
            symbol="US30",
            trend={},
            liquidity={},
            ict={},
            scores={},
            total_confidence=50,
            context={},
            direction=None,
        )

        assert "Unknown custom rejection reason" in result["tier_breakdown"]["other"]

    def test_sda_with_no_analyzer(self):
        """SDA handles missing analyzer gracefully."""
        sda = SharedDecisionAdapter(confidence_analyzer=None)
        request = DecisionRequest(mode="LIVE", symbol="US30", context={})
        result = sda.evaluate(request)

        assert result["status"] == "FAIL"
        assert result["decision_source"] == "UNAVAILABLE"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
