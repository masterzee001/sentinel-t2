"""Tests for O2C: Tier 1 Safety Mapping

Tests validate:
- Tier 1 hard safety centralization
- Severity 1.0 assignment for Tier 1 vetoes
- Killzone classification as TEMPORARY BACKWARD COMPATIBILITY
- All true Tier 1 checks are covered
- Backward compatibility preserved
"""

import pytest
from backend.shared.sda_tier_authority import Tier1SafetyChecker, TierAuthority
from backend.shared.reason_ledger import DecisionMode


class TestTier1SafetyCentral:
    """Test centralized Tier 1 safety checker."""

    def test_tier_1_checker_exists(self):
        """Tier1SafetyChecker class exists and is accessible."""
        assert Tier1SafetyChecker is not None
        assert hasattr(Tier1SafetyChecker, 'classify_tier_1_reasons')
        assert hasattr(Tier1SafetyChecker, 'extract_tier_1_with_severity')

    def test_tier_1_severity_constant(self):
        """Tier 1 severity constant is 1.0 (fatal)."""
        assert Tier1SafetyChecker.TIER_1_SEVERITY == 1.0


class TestTier1Classification:
    """Test Tier 1 reason classification."""

    def test_daily_loss_classified_as_tier_1(self):
        """Daily loss is classified as Tier 1."""
        reasons = ["Daily loss limit hit", "MSS not confirmed"]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        assert "Daily loss limit hit" in tier_1
        assert len(tier_1) == 1

    def test_news_lock_classified_as_tier_1(self):
        """News lock is classified as Tier 1."""
        reasons = ["High impact news lock active"]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        assert "High impact news lock active" in tier_1

    def test_broker_lock_classified_as_tier_1(self):
        """Broker lock is classified as Tier 1."""
        reasons = ["Broker locked"]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        assert "Broker locked" in tier_1

    def test_symbol_lock_classified_as_tier_1(self):
        """Symbol lock is classified as Tier 1."""
        reasons = ["Symbol locked"]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        assert "Symbol locked" in tier_1

    def test_max_trades_classified_as_tier_1(self):
        """Max trades per day is classified as Tier 1."""
        reasons = ["Max trades per day hit"]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        assert "Max trades per day hit" in tier_1

    def test_killzone_classified_as_tier_1_with_temporary_label(self):
        """Killzone is classified as Tier 1 (TEMPORARY BACKWARD COMPATIBILITY)."""
        reasons = ["Outside valid killzone"]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        assert "Outside valid killzone" in tier_1
        # Verify TEMPORARY BACKWARD COMPATIBILITY marking
        assert meta["Outside valid killzone"]["permanent"] is False
        assert "TEMPORARY" in str(meta["Outside valid killzone"].get("note", ""))

    def test_non_tier_1_reasons_excluded(self):
        """Non-Tier 1 reasons are not classified as Tier 1."""
        reasons = [
            "MSS not confirmed",
            "FVG not detected",
            "Weak SMT",
        ]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        assert len(tier_1) == 0

    def test_mixed_tier_reasons(self):
        """Only true Tier 1 reasons are extracted from mixed list."""
        reasons = [
            "Daily loss limit hit",
            "MSS not confirmed",
            "High impact news lock active",
            "FVG not detected",
            "Broker locked",
        ]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        assert len(tier_1) == 3
        assert "Daily loss limit hit" in tier_1
        assert "High impact news lock active" in tier_1
        assert "Broker locked" in tier_1


class TestTier1Severity:
    """Test Tier 1 severity assignment."""

    def test_all_tier_1_have_severity_1_0(self):
        """All Tier 1 reasons assigned severity 1.0 (fatal)."""
        reasons = [
            "Daily loss limit hit",
            "High impact news lock active",
            "Broker locked",
            "Symbol locked",
            "Max trades per day hit",
        ]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        severity_entries = Tier1SafetyChecker.extract_tier_1_with_severity(tier_1, meta)

        for entry in severity_entries:
            assert entry["severity"] == 1.0, f"Expected severity 1.0 for {entry['reason']}"

    def test_killzone_severity_1_0_with_temporary_note(self):
        """Killzone has severity 1.0 but marked TEMPORARY BACKWARD COMPATIBILITY."""
        reasons = ["Outside valid killzone"]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        severity_entries = Tier1SafetyChecker.extract_tier_1_with_severity(tier_1, meta)

        assert len(severity_entries) == 1
        entry = severity_entries[0]
        assert entry["severity"] == 1.0
        assert entry["permanent_tier_1"] is False
        assert "TEMPORARY" in str(entry.get("note", ""))

    def test_severity_entry_structure(self):
        """Tier 1 severity entries have required fields."""
        reasons = ["Daily loss limit hit"]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        entries = Tier1SafetyChecker.extract_tier_1_with_severity(tier_1, meta)

        assert len(entries) == 1
        entry = entries[0]
        assert "reason" in entry
        assert "severity" in entry
        assert "classification" in entry
        assert "permanent_tier_1" in entry


class TestTier1IntegrationWithAuthority:
    """Test Tier 1 safety integration with TierAuthority."""

    def test_tier_authority_includes_tier_1_severity_analysis(self):
        """TierAuthority evaluate_all_tiers includes tier_1_severity_analysis."""
        from unittest.mock import Mock

        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = ["Daily loss limit hit", "MSS not confirmed"]

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

        assert "tier_1_severity_analysis" in result
        assert isinstance(result["tier_1_severity_analysis"], list)
        assert len(result["tier_1_severity_analysis"]) > 0

    def test_tier_authority_tier_1_reasons_extracted(self):
        """TierAuthority extracts tier_1_reasons separately."""
        from unittest.mock import Mock

        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = ["Daily loss limit hit"]

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

        assert "tier_1_reasons" in result
        assert "Daily loss limit hit" in result["tier_1_reasons"]

    def test_tier_1_pass_depends_on_tier_1_reasons(self):
        """tier_1_pass is False if any tier_1_reasons exist."""
        from unittest.mock import Mock

        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = ["Daily loss limit hit"]

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
        assert len(result["tier_1_reasons"]) > 0

    def test_tier_1_pass_true_when_no_tier_1_reasons(self):
        """tier_1_pass is True when no tier_1_reasons exist."""
        from unittest.mock import Mock

        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = ["MSS not confirmed", "FVG not detected"]

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
        assert len(result["tier_1_reasons"]) == 0


class TestBackwardCompatibility:
    """Test that Tier 1 centralization preserves backward compatibility."""

    def test_approval_decisions_unchanged(self):
        """APPROVED decisions still approved with Tier 1 mapping."""
        from unittest.mock import Mock

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

        # No tier 1 issues = passes tier 1
        assert result["tier_1_pass"] is True

    def test_rejection_decisions_unchanged(self):
        """REJECTED decisions still rejected with Tier 1 mapping."""
        from unittest.mock import Mock

        mock_engine = Mock()
        mock_engine.evaluate_hard_rejections.return_value = ["Daily loss limit hit"]

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

        # Tier 1 failure = overall rejection
        assert result["tier_1_pass"] is False
        assert len(result["tier_1_reasons"]) > 0


class TestTier1Classification:
    """Test Tier 1 classification metadata."""

    def test_daily_loss_classification_code(self):
        """Daily loss has classification code DAILY_LOSS_BREACH."""
        reasons = ["Daily loss limit hit"]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        assert meta["Daily loss limit hit"]["classification"] == "DAILY_LOSS_BREACH"

    def test_news_lock_classification_code(self):
        """News lock has classification code NEWS_LOCK."""
        reasons = ["High impact news lock active"]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        assert meta["High impact news lock active"]["classification"] == "NEWS_LOCK"

    def test_broker_lock_classification_code(self):
        """Broker lock has classification code BROKER_LOCK."""
        reasons = ["Broker locked"]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        assert meta["Broker locked"]["classification"] == "BROKER_LOCK"

    def test_symbol_lock_classification_code(self):
        """Symbol lock has classification code SYMBOL_LOCK."""
        reasons = ["Symbol locked"]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        assert meta["Symbol locked"]["classification"] == "SYMBOL_LOCK"

    def test_max_trades_classification_code(self):
        """Max trades has classification code MAX_TRADES_EXCEEDED."""
        reasons = ["Max trades per day hit"]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        assert meta["Max trades per day hit"]["classification"] == "MAX_TRADES_EXCEEDED"

    def test_killzone_classification_code(self):
        """Killzone has classification code KILLZONE_INVALID."""
        reasons = ["Outside valid killzone"]
        tier_1, meta = Tier1SafetyChecker.classify_tier_1_reasons(reasons)
        assert meta["Outside valid killzone"]["classification"] == "KILLZONE_INVALID"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
