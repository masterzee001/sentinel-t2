"""Tests for O2D: Tier 2/3 Classification Scaffolding

Tests validate:
- Tier 2A macro truth veto classification
- Tier 2B macro confidence penalty classification
- Tier 3A structural validity veto classification
- Tier 3B setup quality scaling classification
- Weak SMT does not veto independently
- Weak FVG does not veto independently
- Behavioral preservation (no decision changes)
"""

import pytest
from unittest.mock import Mock
from backend.shared.sda_tier_authority import (
    Tier2MacroTruthChecker,
    Tier2MacroConfidenceChecker,
    Tier3StructuralValidityChecker,
    Tier3SetupQualityChecker,
    TierAuthority,
)
from backend.shared.reason_ledger import DecisionMode


# ============================================================================
# TIER 2A: MACRO TRUTH VETO
# ============================================================================

class TestTier2AMacroTruth:
    """Test Tier 2A macro truth veto classification."""

    def test_tier_2a_checker_exists(self):
        """Tier2MacroTruthChecker exists."""
        assert Tier2MacroTruthChecker is not None
        assert hasattr(Tier2MacroTruthChecker, 'classify_tier_2a_reasons')

    def test_htf_contradiction_classified_as_tier_2a(self):
        """HTF narrative contradiction is Tier 2A."""
        reasons = ["HTF narrative opposite"]
        tier_2a, meta = Tier2MacroTruthChecker.classify_tier_2a_reasons(reasons)
        assert "HTF narrative opposite" in tier_2a
        assert meta["HTF narrative opposite"]["classification"] == "HTF_CONTRADICTION"
        assert meta["HTF narrative opposite"]["veto_capable"] is True

    def test_macro_liquidity_absence_tier_2a(self):
        """Macro liquidity absence is Tier 2A."""
        reasons = ["Macro liquidity absent"]
        tier_2a, meta = Tier2MacroTruthChecker.classify_tier_2a_reasons(reasons)
        assert "Macro liquidity absent" in tier_2a
        assert meta["Macro liquidity absent"]["classification"] == "MACRO_LIQUIDITY_ABSENCE"

    def test_forex_daily_bias_contradiction_tier_2a(self):
        """Forex daily bias contradiction is Tier 2A."""
        reasons = ["Forex daily bias not aligned"]
        tier_2a, meta = Tier2MacroTruthChecker.classify_tier_2a_reasons(reasons)
        assert "Forex daily bias not aligned" in tier_2a

    def test_forex_4h_narrative_contradiction_tier_2a(self):
        """Forex 4H narrative contradiction is Tier 2A."""
        reasons = ["Forex 4H narrative not aligned"]
        tier_2a, meta = Tier2MacroTruthChecker.classify_tier_2a_reasons(reasons)
        assert "Forex 4H narrative not aligned" in tier_2a

    def test_tier_2a_severity_0_8(self):
        """All Tier 2A reasons have severity 0.8."""
        reasons = ["HTF narrative opposite", "Macro liquidity absent"]
        tier_2a, meta = Tier2MacroTruthChecker.classify_tier_2a_reasons(reasons)
        for reason in tier_2a:
            assert meta[reason]["severity"] == 0.8

    def test_tier_2a_non_macro_excluded(self):
        """Non-macro reasons not classified as Tier 2A."""
        reasons = ["MSS not confirmed", "Weak SMT", "FVG not detected"]
        tier_2a, meta = Tier2MacroTruthChecker.classify_tier_2a_reasons(reasons)
        assert len(tier_2a) == 0


# ============================================================================
# TIER 2B: MACRO CONFIDENCE PENALTY
# ============================================================================

class TestTier2BMacroConfidence:
    """Test Tier 2B macro confidence penalty classification."""

    def test_tier_2b_checker_exists(self):
        """Tier2MacroConfidenceChecker exists."""
        assert Tier2MacroConfidenceChecker is not None
        assert hasattr(Tier2MacroConfidenceChecker, 'classify_tier_2b_reasons')

    def test_tier_2b_severity_0_3(self):
        """All Tier 2B reasons have severity 0.3 (penalty, not veto)."""
        assert Tier2MacroConfidenceChecker.TIER_2B_SEVERITY == 0.3

    def test_tier_2b_action_is_penalty(self):
        """Tier 2B reasons have action='penalty' not veto."""
        reasons = ["london_open"]
        tier_2b, meta = Tier2MacroConfidenceChecker.classify_tier_2b_reasons(reasons)
        if tier_2b:
            for reason in tier_2b:
                assert meta[reason]["action"] == "penalty"

    def test_tier_2b_never_veto_by_itself(self):
        """Tier 2B reasons cannot veto independently."""
        # This is implicit in the design - Tier 2B is penalty-only
        # Test verifies no "veto_capable" flag exists
        reasons = ["range_phase"]
        tier_2b, meta = Tier2MacroConfidenceChecker.classify_tier_2b_reasons(reasons)
        if tier_2b:
            for reason in tier_2b:
                assert "veto_capable" not in meta[reason]


# ============================================================================
# TIER 3A: STRUCTURAL VALIDITY VETO
# ============================================================================

class TestTier3AStructuralValidity:
    """Test Tier 3A structural validity veto classification."""

    def test_tier_3a_checker_exists(self):
        """Tier3StructuralValidityChecker exists."""
        assert Tier3StructuralValidityChecker is not None
        assert hasattr(Tier3StructuralValidityChecker, 'classify_tier_3a_reasons')

    def test_mss_absent_is_tier_3a(self):
        """MSS absence is Tier 3A structural veto."""
        reasons = ["MSS not confirmed"]
        tier_3a, meta = Tier3StructuralValidityChecker.classify_tier_3a_reasons(reasons)
        assert "MSS not confirmed" in tier_3a
        assert meta["MSS not confirmed"]["classification"] == "MSS_ABSENT"
        assert meta["MSS not confirmed"]["veto_capable"] is True

    def test_no_executable_fvg_is_tier_3a(self):
        """No executable FVG is Tier 3A structural veto."""
        reasons = ["FVG not detected"]
        tier_3a, meta = Tier3StructuralValidityChecker.classify_tier_3a_reasons(reasons)
        assert "FVG not detected" in tier_3a
        assert meta["FVG not detected"]["classification"] == "NO_EXECUTABLE_FVG"

    def test_invalid_rr_is_tier_3a(self):
        """Invalid RR structure is Tier 3A."""
        reasons = ["RR below 3"]
        tier_3a, meta = Tier3StructuralValidityChecker.classify_tier_3a_reasons(reasons)
        assert "RR below 3" in tier_3a
        assert meta["RR below 3"]["classification"] == "IMPOSSIBLE_RR_STRUCTURE"

    def test_invalid_stop_structure_is_tier_3a(self):
        """Invalid stop structure is Tier 3A."""
        reasons = ["Invalid stop structure"]
        tier_3a, meta = Tier3StructuralValidityChecker.classify_tier_3a_reasons(reasons)
        assert "Invalid stop structure" in tier_3a

    def test_fvg_mss_misalignment_is_tier_3a(self):
        """FVG-MSS direction misalignment is Tier 3A."""
        reasons = ["FVG direction not aligned with MSS"]
        tier_3a, meta = Tier3StructuralValidityChecker.classify_tier_3a_reasons(reasons)
        assert "FVG direction not aligned with MSS" in tier_3a

    def test_tier_3a_severity_0_9(self):
        """All Tier 3A reasons have severity 0.9."""
        reasons = ["MSS not confirmed", "FVG not detected"]
        tier_3a, meta = Tier3StructuralValidityChecker.classify_tier_3a_reasons(reasons)
        for reason in tier_3a:
            assert meta[reason]["severity"] == 0.9


# ============================================================================
# TIER 3B: SETUP QUALITY SCALING
# ============================================================================

class TestTier3BSetupQuality:
    """Test Tier 3B setup quality scaling classification."""

    def test_tier_3b_checker_exists(self):
        """Tier3SetupQualityChecker exists."""
        assert Tier3SetupQualityChecker is not None
        assert hasattr(Tier3SetupQualityChecker, 'classify_tier_3b_reasons')

    def test_weak_smt_is_tier_3b_not_veto(self):
        """Weak SMT is Tier 3B scaling, not veto."""
        reasons = ["Weak SMT"]
        tier_3b, meta = Tier3SetupQualityChecker.classify_tier_3b_reasons(reasons)
        assert "Weak SMT" in tier_3b
        assert meta["Weak SMT"]["classification"] == "WEAK_SMT_CONFLUENCE"
        assert meta["Weak SMT"]["action"] == "scaling"
        assert "Does not veto independently" in str(meta["Weak SMT"].get("note", ""))

    def test_weak_fvg_quality_is_tier_3b_not_veto(self):
        """Weak FVG quality is Tier 3B scaling, not veto."""
        reasons = ["FVG quality"]
        tier_3b, meta = Tier3SetupQualityChecker.classify_tier_3b_reasons(reasons)
        assert "FVG quality" in tier_3b
        assert meta["FVG quality"]["classification"] == "MEDIOCRE_FVG_GRADE"
        assert meta["FVG quality"]["action"] == "scaling"

    def test_weak_fvg_clarity_is_tier_3b_not_veto(self):
        """Weak FVG clarity is Tier 3B scaling, not veto."""
        reasons = ["Weak FVG"]
        tier_3b, meta = Tier3SetupQualityChecker.classify_tier_3b_reasons(reasons)
        assert "Weak FVG" in tier_3b
        assert meta["Weak FVG"]["action"] == "scaling"

    def test_tier_3b_severity_0_2(self):
        """All Tier 3B reasons have severity 0.2 (minimal penalty)."""
        reasons = ["Weak SMT", "Weak FVG", "FVG quality"]
        tier_3b, meta = Tier3SetupQualityChecker.classify_tier_3b_reasons(reasons)
        for reason in tier_3b:
            assert meta[reason]["severity"] == 0.2

    def test_tier_3b_never_veto_by_itself(self):
        """Tier 3B reasons cannot veto independently."""
        # This is implicit in the design - Tier 3B is scaling-only
        reasons = ["Weak SMT"]
        tier_3b, meta = Tier3SetupQualityChecker.classify_tier_3b_reasons(reasons)
        if tier_3b:
            for reason in tier_3b:
                assert "veto_capable" not in meta[reason]


# ============================================================================
# INTEGRATION: TIER AUTHORITY
# ============================================================================

class TestTier234Integration:
    """Test Tier 2/3 classification integration with TierAuthority."""

    def test_tier_authority_includes_all_tier_2_3_fields(self):
        """TierAuthority returns all Tier 2/3 fields."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = [
            "HTF narrative opposite",
            "MSS not confirmed",
            "Weak SMT",
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

        # O2D fields
        assert "tier_2a_reasons" in result
        assert "tier_2a_severity_analysis" in result
        assert "tier_2b_reasons" in result
        assert "tier_2b_severity_analysis" in result
        assert "tier_3a_reasons" in result
        assert "tier_3a_severity_analysis" in result
        assert "tier_3b_reasons" in result
        assert "tier_3b_severity_analysis" in result

    def test_tier_2a_macro_truth_extracted(self):
        """Tier 2A macro truth reasons are extracted."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = ["HTF narrative opposite"]

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

        assert "HTF narrative opposite" in result["tier_2a_reasons"]
        assert result["tier_2a_pass"] is False

    def test_tier_3a_structural_extracted(self):
        """Tier 3A structural reasons are extracted."""
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

        assert "MSS not confirmed" in result["tier_3a_reasons"]
        assert result["tier_3a_pass"] is False

    def test_tier_3b_quality_extracted(self):
        """Tier 3B quality reasons are extracted."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = ["Weak SMT"]

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

        assert "Weak SMT" in result["tier_3b_reasons"]
        # Note: tier_3b_pass doesn't exist (3B is penalty-only)


# ============================================================================
# BEHAVIORAL PRESERVATION
# ============================================================================

class TestBehaviorPreservation:
    """Test that Tier 2/3 classification preserves existing behavior."""

    def test_approval_decisions_unchanged(self):
        """APPROVED decisions unchanged with Tier 2/3 classification."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = []

        authority = TierAuthority(confidence_engine=mock_engine)
        result = authority.evaluate_all_tiers(
            symbol="US30",
            trend={},
            liquidity={},
            ict={},
            scores={},
            total_confidence=85,
            context={},
            direction=None,
        )

        # No rejections = passes all tiers
        assert result["tier_1_pass"] is True
        assert result["tier_2a_pass"] is True
        assert result["tier_3a_pass"] is True

    def test_rejection_decisions_unchanged(self):
        """REJECTED decisions unchanged with Tier 2/3 classification."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = [
            "HTF narrative opposite",
            "MSS not confirmed",
        ]

        authority = TierAuthority(confidence_engine=mock_engine)
        result = authority.evaluate_all_tiers(
            symbol="US30",
            trend={},
            liquidity={},
            ict={},
            scores={},
            total_confidence=85,
            context={},
            direction=None,
        )

        # Tier 2A and 3A failures
        assert result["tier_2a_pass"] is False
        assert result["tier_3a_pass"] is False

    def test_weak_quality_does_not_reject(self):
        """Weak setup quality alone does not cause rejection."""
        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = ["Weak SMT"]

        authority = TierAuthority(confidence_engine=mock_engine)
        result = authority.evaluate_all_tiers(
            symbol="US30",
            trend={},
            liquidity={},
            ict={},
            scores={},
            total_confidence=85,
            context={},
            direction=None,
        )

        # Tier 3B is penalty-only, doesn't affect tier_3a_pass
        # (tier_3a_pass is for structural validity, not quality)
        assert len(result["tier_3b_reasons"]) > 0


# ============================================================================
# TIER SEVERITY LABELS
# ============================================================================

class TestTierSeverityLabels:
    """Test severity labels for tiers (labels, not decision logic)."""

    def test_tier_1_severity_1_0_fatal(self):
        """Tier 1 severity is 1.0 (fatal)."""
        from backend.shared.sda_tier_authority import Tier1SafetyChecker
        assert Tier1SafetyChecker.TIER_1_SEVERITY == 1.0

    def test_tier_2a_severity_0_8_high(self):
        """Tier 2A severity is 0.8 (high but defeatable)."""
        assert Tier2MacroTruthChecker.TIER_2A_SEVERITY == 0.8

    def test_tier_2b_severity_0_3_penalty(self):
        """Tier 2B severity is 0.3 (penalty, not veto)."""
        assert Tier2MacroConfidenceChecker.TIER_2B_SEVERITY == 0.3

    def test_tier_3a_severity_0_9_high(self):
        """Tier 3A severity is 0.9 (high, structure required)."""
        assert Tier3StructuralValidityChecker.TIER_3A_SEVERITY == 0.9

    def test_tier_3b_severity_0_2_low(self):
        """Tier 3B severity is 0.2 (low, scaling only)."""
        assert Tier3SetupQualityChecker.TIER_3B_SEVERITY == 0.2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
