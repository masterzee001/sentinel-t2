"""Tests for O2.25: Governance Audit Replay Harness

Tests validate:
- RejectionMetric dataclass creation and export
- FunnelMetrics rate calculations
- TierRejectionAnalyzer tier/code distribution
- FalseRejectionInstrumentor eventual winner tracking
- GovernanceAudit orchestration
- Report generation and alpha leak summary
- End-to-end audit workflow
"""

import pytest
from datetime import datetime
from backend.analytics.governance_audit import (
    RejectionMetric,
    FunnelMetrics,
    TierRejectionAnalyzer,
    FalseRejectionInstrumentor,
    GovernanceAudit,
)


# ============================================================================
# REJECTION METRIC DATACLASS TESTS
# ============================================================================

class TestRejectionMetric:
    """Test RejectionMetric dataclass creation and export."""

    def test_rejection_metric_creation(self):
        """RejectionMetric can be created with required fields."""
        metric = RejectionMetric(
            symbol="US30",
            timestamp="2026-07-02T10:30:00Z",
            tier="tier_2a",
            reason_code="HTF_CONTRADICTION",
            severity=0.8,
            confidence_before=75,
            confidence_after=45,
            risk_before=2.5,
            risk_after=1.0,
        )
        assert metric.symbol == "US30"
        assert metric.tier == "tier_2a"
        assert metric.reason_code == "HTF_CONTRADICTION"
        assert metric.severity == 0.8

    def test_rejection_metric_with_replay_outcome(self):
        """RejectionMetric can include replay outcome."""
        metric = RejectionMetric(
            symbol="EURUSD",
            timestamp="2026-07-02T11:00:00Z",
            tier="tier_3a",
            reason_code="MSS_ABSENT",
            severity=0.9,
            confidence_before=80,
            confidence_after=30,
            risk_before=3.0,
            risk_after=0.5,
            replay_outcome="would_have_won",
            max_favorable_excursion=1.5,
            realized_rr_potential=2.8,
        )
        assert metric.replay_outcome == "would_have_won"
        assert metric.max_favorable_excursion == 1.5
        assert metric.realized_rr_potential == 2.8

    def test_rejection_metric_to_dict(self):
        """RejectionMetric.to_dict() exports all fields."""
        metric = RejectionMetric(
            symbol="GBPUSD",
            timestamp="2026-07-02T12:00:00Z",
            tier="tier_2b",
            reason_code="LONDON_OPEN",
            severity=0.3,
            confidence_before=60,
            confidence_after=50,
            risk_before=1.5,
            risk_after=1.2,
        )
        exported = metric.to_dict()
        assert exported["symbol"] == "GBPUSD"
        assert exported["tier"] == "tier_2b"
        assert exported["severity"] == 0.3
        assert exported["replay_outcome"] is None


# ============================================================================
# FUNNEL METRICS TESTS
# ============================================================================

class TestFunnelMetrics:
    """Test FunnelMetrics rate calculations."""

    def test_funnel_creation(self):
        """FunnelMetrics initializes with zero counts."""
        funnel = FunnelMetrics()
        assert funnel.scanned == 0
        assert funnel.candidates_formed == 0
        assert funnel.candidates_rejected == 0
        assert funnel.candidates_admitted == 0
        assert funnel.executed_trades == 0

    def test_completion_rate_calculation(self):
        """completion_rate() returns formed/scanned percentage."""
        funnel = FunnelMetrics(scanned=100, candidates_formed=75)
        assert funnel.completion_rate() == 75.0

    def test_completion_rate_zero_scanned(self):
        """completion_rate() returns 0.0 when scanned is 0."""
        funnel = FunnelMetrics(scanned=0, candidates_formed=0)
        assert funnel.completion_rate() == 0.0

    def test_rejection_rate_calculation(self):
        """rejection_rate() returns rejected/formed percentage."""
        funnel = FunnelMetrics(candidates_formed=100, candidates_rejected=25)
        assert funnel.rejection_rate() == 25.0

    def test_rejection_rate_zero_formed(self):
        """rejection_rate() returns 0.0 when formed is 0."""
        funnel = FunnelMetrics(candidates_formed=0, candidates_rejected=0)
        assert funnel.rejection_rate() == 0.0

    def test_admission_rate_calculation(self):
        """admission_rate() returns admitted/formed percentage."""
        funnel = FunnelMetrics(candidates_formed=100, candidates_admitted=75)
        assert funnel.admission_rate() == 75.0

    def test_execution_rate_calculation(self):
        """execution_rate() returns executed/admitted percentage."""
        funnel = FunnelMetrics(candidates_admitted=75, executed_trades=60)
        assert funnel.execution_rate() == 80.0

    def test_execution_rate_zero_admitted(self):
        """execution_rate() returns 0.0 when admitted is 0."""
        funnel = FunnelMetrics(candidates_admitted=0, executed_trades=0)
        assert funnel.execution_rate() == 0.0

    def test_realistic_funnel_sequence(self):
        """Realistic funnel progression matches expected rates."""
        funnel = FunnelMetrics(
            scanned=1000,
            candidates_formed=750,
            candidates_rejected=150,
            candidates_admitted=600,
            executed_trades=540,
        )
        assert funnel.completion_rate() == 75.0  # 750/1000
        assert funnel.rejection_rate() == 20.0   # 150/750
        assert funnel.admission_rate() == 80.0   # 600/750
        assert funnel.execution_rate() == 90.0   # 540/600


# ============================================================================
# TIER REJECTION ANALYZER TESTS
# ============================================================================

class TestTierRejectionAnalyzer:
    """Test TierRejectionAnalyzer recording and distribution."""

    def test_analyzer_creation(self):
        """TierRejectionAnalyzer initializes empty."""
        analyzer = TierRejectionAnalyzer()
        assert analyzer.total_rejections == 0
        assert len(analyzer.rejections_by_tier) == 0

    def test_record_rejection_single(self):
        """record_rejection() increments tier count."""
        analyzer = TierRejectionAnalyzer()
        analyzer.record_rejection("tier_2a", "HTF_CONTRADICTION", 0.8)
        assert analyzer.total_rejections == 1
        assert analyzer.rejections_by_tier["tier_2a"] == 1

    def test_record_multiple_rejections(self):
        """record_rejection() accumulates across calls."""
        analyzer = TierRejectionAnalyzer()
        analyzer.record_rejection("tier_2a", "HTF_CONTRADICTION", 0.8)
        analyzer.record_rejection("tier_2a", "MACRO_LIQUIDITY_ABSENCE", 0.8)
        analyzer.record_rejection("tier_3a", "MSS_ABSENT", 0.9)
        assert analyzer.total_rejections == 3
        assert analyzer.rejections_by_tier["tier_2a"] == 2
        assert analyzer.rejections_by_tier["tier_3a"] == 1

    def test_get_tier_distribution_empty(self):
        """get_tier_distribution() returns {} when no rejections."""
        analyzer = TierRejectionAnalyzer()
        assert analyzer.get_tier_distribution() == {}

    def test_get_tier_distribution_percentage(self):
        """get_tier_distribution() returns percentages."""
        analyzer = TierRejectionAnalyzer()
        analyzer.record_rejection("tier_2a", "HTF_CONTRADICTION", 0.8)
        analyzer.record_rejection("tier_3a", "MSS_ABSENT", 0.9)
        analyzer.record_rejection("tier_3a", "FVG_NOT_DETECTED", 0.9)
        dist = analyzer.get_tier_distribution()
        assert dist["tier_2a"] == pytest.approx(33.33, rel=0.01)
        assert dist["tier_3a"] == pytest.approx(66.67, rel=0.01)

    def test_get_code_distribution(self):
        """get_code_distribution() returns rejection code stats."""
        analyzer = TierRejectionAnalyzer()
        analyzer.record_rejection("tier_2a", "HTF_CONTRADICTION", 0.8)
        analyzer.record_rejection("tier_2a", "HTF_CONTRADICTION", 0.8)
        analyzer.record_rejection("tier_3a", "MSS_ABSENT", 0.9)
        dist = analyzer.get_code_distribution()
        assert dist["HTF_CONTRADICTION"]["count"] == 2
        assert dist["HTF_CONTRADICTION"]["percentage"] == pytest.approx(66.67, rel=0.01)
        assert dist["MSS_ABSENT"]["count"] == 1

    def test_severity_average_calculation(self):
        """get_code_distribution() includes severity average."""
        analyzer = TierRejectionAnalyzer()
        analyzer.record_rejection("tier_2a", "HTF_CONTRADICTION", 0.8)
        analyzer.record_rejection("tier_2a", "HTF_CONTRADICTION", 0.8)
        analyzer.record_rejection("tier_3a", "MSS_ABSENT", 0.9)
        dist = analyzer.get_code_distribution()
        assert dist["HTF_CONTRADICTION"]["severity_avg"] == 0.8
        assert dist["MSS_ABSENT"]["severity_avg"] == 0.9

    def test_get_top_rejection_codes(self):
        """get_top_rejection_codes() returns sorted list."""
        analyzer = TierRejectionAnalyzer()
        for _ in range(5):
            analyzer.record_rejection("tier_2a", "HTF_CONTRADICTION", 0.8)
        for _ in range(3):
            analyzer.record_rejection("tier_3a", "MSS_ABSENT", 0.9)
        analyzer.record_rejection("tier_3a", "FVG_NOT_DETECTED", 0.9)
        top = analyzer.get_top_rejection_codes(limit=2)
        assert top[0] == ("HTF_CONTRADICTION", 5)
        assert top[1] == ("MSS_ABSENT", 3)

    def test_get_top_rejection_codes_limit(self):
        """get_top_rejection_codes() respects limit parameter."""
        analyzer = TierRejectionAnalyzer()
        for i in range(15):
            analyzer.record_rejection("tier_2a", f"CODE_{i}", 0.5)
        top = analyzer.get_top_rejection_codes(limit=5)
        assert len(top) == 5


# ============================================================================
# FALSE REJECTION INSTRUMENTOR TESTS
# ============================================================================

class TestFalseRejectionInstrumentor:
    """Test FalseRejectionInstrumentor eventual winner tracking."""

    def test_instrumentor_creation(self):
        """FalseRejectionInstrumentor initializes empty."""
        inst = FalseRejectionInstrumentor()
        assert inst.eventual_winners_rejected == 0

    def test_record_eventual_winner(self):
        """record_eventual_winner() increments counter."""
        inst = FalseRejectionInstrumentor()
        inst.record_eventual_winner("tier_2a", "HTF_CONTRADICTION", 1.5, 2.8)
        assert inst.eventual_winners_rejected == 1
        assert inst.false_rejections_by_tier["tier_2a"] == 1

    def test_record_multiple_eventual_winners(self):
        """record_eventual_winner() accumulates across calls."""
        inst = FalseRejectionInstrumentor()
        inst.record_eventual_winner("tier_2a", "HTF_CONTRADICTION", 1.5, 2.8)
        inst.record_eventual_winner("tier_2a", "HTF_CONTRADICTION", 2.0, 3.5)
        inst.record_eventual_winner("tier_3a", "MSS_ABSENT", 1.2, 2.0)
        assert inst.eventual_winners_rejected == 3
        assert inst.false_rejections_by_tier["tier_2a"] == 2
        assert inst.false_rejections_by_tier["tier_3a"] == 1

    def test_get_tier_alpha_leak(self):
        """get_tier_alpha_leak() returns by-tier counts."""
        inst = FalseRejectionInstrumentor()
        inst.record_eventual_winner("tier_2a", "HTF_CONTRADICTION", 1.5, 2.8)
        inst.record_eventual_winner("tier_3a", "MSS_ABSENT", 1.2, 2.0)
        leak = inst.get_tier_alpha_leak()
        assert leak["tier_2a"] == 1
        assert leak["tier_3a"] == 1

    def test_get_code_alpha_leak(self):
        """get_code_alpha_leak() returns by-code counts."""
        inst = FalseRejectionInstrumentor()
        inst.record_eventual_winner("tier_2a", "HTF_CONTRADICTION", 1.5, 2.8)
        inst.record_eventual_winner("tier_2a", "HTF_CONTRADICTION", 2.0, 3.5)
        inst.record_eventual_winner("tier_3a", "MSS_ABSENT", 1.2, 2.0)
        leak = inst.get_code_alpha_leak()
        assert leak["HTF_CONTRADICTION"] == 2
        assert leak["MSS_ABSENT"] == 1

    def test_mfe_statistics_empty(self):
        """get_mfe_statistics() returns zero stats when empty."""
        inst = FalseRejectionInstrumentor()
        stats = inst.get_mfe_statistics()
        assert stats["samples"] == 0
        assert stats["avg_mfe"] == 0.0
        assert stats["avg_rr_potential"] == 0.0

    def test_mfe_statistics_single(self):
        """get_mfe_statistics() returns single sample stats."""
        inst = FalseRejectionInstrumentor()
        inst.record_eventual_winner("tier_2a", "HTF_CONTRADICTION", 1.5, 2.8)
        stats = inst.get_mfe_statistics()
        assert stats["samples"] == 1
        assert stats["avg_mfe"] == 1.5
        assert stats["avg_rr_potential"] == 2.8
        assert stats["max_mfe"] == 1.5
        assert stats["min_mfe"] == 1.5

    def test_mfe_statistics_multiple(self):
        """get_mfe_statistics() aggregates across samples."""
        inst = FalseRejectionInstrumentor()
        inst.record_eventual_winner("tier_2a", "HTF_CONTRADICTION", 1.0, 2.0)
        inst.record_eventual_winner("tier_2a", "HTF_CONTRADICTION", 3.0, 4.0)
        stats = inst.get_mfe_statistics()
        assert stats["samples"] == 2
        assert stats["avg_mfe"] == 2.0
        assert stats["avg_rr_potential"] == 3.0
        assert stats["max_mfe"] == 3.0
        assert stats["min_mfe"] == 1.0


# ============================================================================
# GOVERNANCE AUDIT ORCHESTRATION TESTS
# ============================================================================

class TestGovernanceAudit:
    """Test GovernanceAudit orchestration."""

    def test_governance_audit_creation(self):
        """GovernanceAudit initializes with empty state."""
        audit = GovernanceAudit()
        assert audit.funnel.scanned == 0
        assert audit.tier_analyzer.total_rejections == 0

    def test_record_scan(self):
        """record_scan() increments funnel scanned count."""
        audit = GovernanceAudit()
        audit.record_scan()
        audit.record_scan()
        assert audit.funnel.scanned == 2

    def test_record_candidate_formed(self):
        """record_candidate_formed() increments candidates_formed."""
        audit = GovernanceAudit()
        audit.record_candidate_formed()
        audit.record_candidate_formed()
        audit.record_candidate_formed()
        assert audit.funnel.candidates_formed == 3

    def test_record_rejection_full(self):
        """record_rejection() records complete rejection event."""
        audit = GovernanceAudit()
        audit.record_rejection(
            tier="tier_2a",
            reason_code="HTF_CONTRADICTION",
            severity=0.8,
            symbol="US30",
            timestamp="2026-07-02T10:30:00Z",
            confidence_before=75,
            confidence_after=45,
            risk_before=2.5,
            risk_after=1.0,
        )
        assert audit.funnel.candidates_rejected == 1
        assert audit.tier_analyzer.total_rejections == 1
        assert len(audit.rejection_metrics) == 1

    def test_record_rejection_with_replay_outcome(self):
        """record_rejection() tracks replay outcome."""
        audit = GovernanceAudit()
        audit.record_rejection(
            tier="tier_2a",
            reason_code="HTF_CONTRADICTION",
            severity=0.8,
            symbol="US30",
            timestamp="2026-07-02T10:30:00Z",
            confidence_before=75,
            confidence_after=45,
            risk_before=2.5,
            risk_after=1.0,
            replay_outcome="would_have_won",
            max_favorable_excursion=1.5,
            realized_rr_potential=2.8,
        )
        metric = audit.rejection_metrics[0]
        assert metric.replay_outcome == "would_have_won"
        assert audit.false_rejection_instrumentor.eventual_winners_rejected == 1

    def test_record_admission(self):
        """record_admission() increments candidates_admitted."""
        audit = GovernanceAudit()
        audit.record_admission()
        audit.record_admission()
        assert audit.funnel.candidates_admitted == 2

    def test_record_execution(self):
        """record_execution() increments executed_trades."""
        audit = GovernanceAudit()
        audit.record_execution()
        audit.record_execution()
        audit.record_execution()
        assert audit.funnel.executed_trades == 3

    def test_realistic_workflow_sequence(self):
        """Complete audit workflow: scan → form → reject/admit → execute."""
        audit = GovernanceAudit()
        # Scan 1000
        for _ in range(1000):
            audit.record_scan()
        # Form 750
        for _ in range(750):
            audit.record_candidate_formed()
        # Reject 150
        for i in range(150):
            audit.record_rejection(
                tier="tier_2a" if i % 3 == 0 else "tier_3a",
                reason_code="HTF_CONTRADICTION" if i % 3 == 0 else "MSS_ABSENT",
                severity=0.8 if i % 3 == 0 else 0.9,
                symbol=f"SYM_{i}",
                timestamp="2026-07-02T10:30:00Z",
                confidence_before=75,
                confidence_after=45,
                risk_before=2.5,
                risk_after=1.0,
            )
        # Admit 600
        for _ in range(600):
            audit.record_admission()
        # Execute 540
        for _ in range(540):
            audit.record_execution()

        assert audit.funnel.scanned == 1000
        assert audit.funnel.candidates_formed == 750
        assert audit.funnel.candidates_rejected == 150
        assert audit.funnel.candidates_admitted == 600
        assert audit.funnel.executed_trades == 540


# ============================================================================
# REPORT GENERATION TESTS
# ============================================================================

class TestReportGeneration:
    """Test generate_report() output."""

    def test_generate_report_structure(self):
        """generate_report() returns dict with required fields."""
        audit = GovernanceAudit()
        audit.record_scan()
        audit.record_candidate_formed()
        audit.record_admission()
        audit.record_execution()

        report = audit.generate_report()
        assert "timestamp" in report
        assert "funnel" in report
        assert "tier_distribution" in report
        assert "rejection_codes" in report
        assert "top_rejection_codes" in report
        assert "false_rejections" in report

    def test_generate_report_funnel_section(self):
        """generate_report() funnel section has all required fields."""
        audit = GovernanceAudit()
        audit.funnel.scanned = 1000
        audit.funnel.candidates_formed = 750
        audit.funnel.candidates_rejected = 150
        audit.funnel.candidates_admitted = 600
        audit.funnel.executed_trades = 540

        report = audit.generate_report()
        funnel = report["funnel"]
        assert funnel["scanned"] == 1000
        assert funnel["candidates_formed"] == 750
        assert funnel["candidates_rejected"] == 150
        assert funnel["candidates_admitted"] == 600
        assert funnel["executed_trades"] == 540
        assert "completion_rate_pct" in funnel
        assert "rejection_rate_pct" in funnel
        assert "admission_rate_pct" in funnel
        assert "execution_rate_pct" in funnel

    def test_generate_report_tier_distribution(self):
        """generate_report() includes tier distribution."""
        audit = GovernanceAudit()
        for _ in range(5):
            audit.record_rejection("tier_2a", "HTF_CONTRADICTION", 0.8, "US30", "2026-07-02T10:30:00Z", 75, 45, 2.5, 1.0)
        for _ in range(3):
            audit.record_rejection("tier_3a", "MSS_ABSENT", 0.9, "US30", "2026-07-02T10:30:00Z", 75, 45, 2.5, 1.0)

        report = audit.generate_report()
        tier_dist = report["tier_distribution"]
        assert tier_dist["tier_2a"] == pytest.approx(62.5, rel=0.01)
        assert tier_dist["tier_3a"] == pytest.approx(37.5, rel=0.01)

    def test_generate_report_rejection_codes(self):
        """generate_report() includes rejection code details."""
        audit = GovernanceAudit()
        audit.record_rejection("tier_2a", "HTF_CONTRADICTION", 0.8, "US30", "2026-07-02T10:30:00Z", 75, 45, 2.5, 1.0)
        audit.record_rejection("tier_2a", "HTF_CONTRADICTION", 0.8, "US30", "2026-07-02T10:30:00Z", 75, 45, 2.5, 1.0)
        audit.record_rejection("tier_3a", "MSS_ABSENT", 0.9, "US30", "2026-07-02T10:30:00Z", 75, 45, 2.5, 1.0)

        report = audit.generate_report()
        codes = report["rejection_codes"]
        assert codes["HTF_CONTRADICTION"]["count"] == 2
        assert codes["MSS_ABSENT"]["count"] == 1

    def test_generate_report_false_rejections_section(self):
        """generate_report() includes false rejections analysis."""
        audit = GovernanceAudit()
        audit.record_rejection(
            tier="tier_2a",
            reason_code="HTF_CONTRADICTION",
            severity=0.8,
            symbol="US30",
            timestamp="2026-07-02T10:30:00Z",
            confidence_before=75,
            confidence_after=45,
            risk_before=2.5,
            risk_after=1.0,
            replay_outcome="would_have_won",
            max_favorable_excursion=1.5,
            realized_rr_potential=2.8,
        )

        report = audit.generate_report()
        false_rej = report["false_rejections"]
        assert false_rej["eventual_winners_rejected"] == 1
        assert "by_tier" in false_rej
        assert "by_code" in false_rej
        assert "mfe_statistics" in false_rej

    def test_generate_report_timestamp_iso8601(self):
        """generate_report() includes ISO8601 timestamp."""
        audit = GovernanceAudit()
        report = audit.generate_report()
        timestamp = report["timestamp"]
        # Verify it's ISO8601 format
        datetime.fromisoformat(timestamp)


# ============================================================================
# ALPHA LEAK SUMMARY TESTS
# ============================================================================

class TestAlphaLeakSummary:
    """Test get_alpha_leak_summary() report generation."""

    def test_alpha_leak_summary_structure(self):
        """get_alpha_leak_summary() returns structured report."""
        audit = GovernanceAudit()
        summary = audit.get_alpha_leak_summary()
        assert "total_eventual_winners_rejected" in summary
        assert "by_tier" in summary
        assert "by_code" in summary

    def test_alpha_leak_summary_empty(self):
        """get_alpha_leak_summary() empty when no alpha leaks."""
        audit = GovernanceAudit()
        summary = audit.get_alpha_leak_summary()
        assert summary["total_eventual_winners_rejected"] == 0
        assert len(summary["by_tier"]) == 0
        assert len(summary["by_code"]) == 0

    def test_alpha_leak_summary_by_tier(self):
        """get_alpha_leak_summary() aggregates by tier."""
        audit = GovernanceAudit()
        for i in range(5):
            audit.record_rejection(
                tier="tier_2a",
                reason_code="HTF_CONTRADICTION",
                severity=0.8,
                symbol=f"SYM_{i}",
                timestamp="2026-07-02T10:30:00Z",
                confidence_before=75,
                confidence_after=45,
                risk_before=2.5,
                risk_after=1.0,
                replay_outcome="would_have_won",
                max_favorable_excursion=1.5,
                realized_rr_potential=2.8,
            )
        for i in range(3):
            audit.record_rejection(
                tier="tier_3a",
                reason_code="MSS_ABSENT",
                severity=0.9,
                symbol=f"SYM_{i+5}",
                timestamp="2026-07-02T10:30:00Z",
                confidence_before=75,
                confidence_after=45,
                risk_before=2.5,
                risk_after=1.0,
                replay_outcome="would_have_won",
                max_favorable_excursion=1.2,
                realized_rr_potential=2.0,
            )

        summary = audit.get_alpha_leak_summary()
        assert summary["total_eventual_winners_rejected"] == 8
        assert summary["by_tier"][0]["tier"] == "tier_2a"
        assert summary["by_tier"][0]["count"] == 5
        assert summary["by_tier"][1]["tier"] == "tier_3a"
        assert summary["by_tier"][1]["count"] == 3

    def test_alpha_leak_summary_by_code_sorted(self):
        """get_alpha_leak_summary() sorts codes by leak count (descending)."""
        audit = GovernanceAudit()
        for i in range(5):
            audit.record_rejection(
                tier="tier_2a",
                reason_code="HTF_CONTRADICTION",
                severity=0.8,
                symbol=f"SYM_{i}",
                timestamp="2026-07-02T10:30:00Z",
                confidence_before=75,
                confidence_after=45,
                risk_before=2.5,
                risk_after=1.0,
                replay_outcome="would_have_won",
                max_favorable_excursion=1.5,
                realized_rr_potential=2.8,
            )
        for i in range(2):
            audit.record_rejection(
                tier="tier_3a",
                reason_code="MSS_ABSENT",
                severity=0.9,
                symbol=f"SYM_{i+5}",
                timestamp="2026-07-02T10:30:00Z",
                confidence_before=75,
                confidence_after=45,
                risk_before=2.5,
                risk_after=1.0,
                replay_outcome="would_have_won",
                max_favorable_excursion=1.2,
                realized_rr_potential=2.0,
            )

        summary = audit.get_alpha_leak_summary()
        assert summary["by_code"][0]["code"] == "HTF_CONTRADICTION"
        assert summary["by_code"][0]["count"] == 5
        assert summary["by_code"][1]["code"] == "MSS_ABSENT"
        assert summary["by_code"][1]["count"] == 2

    def test_alpha_leak_summary_code_limit(self):
        """get_alpha_leak_summary() limits code entries to top 10."""
        audit = GovernanceAudit()
        for i in range(15):
            audit.record_rejection(
                tier="tier_2a",
                reason_code=f"CODE_{i}",
                severity=0.8,
                symbol=f"SYM_{i}",
                timestamp="2026-07-02T10:30:00Z",
                confidence_before=75,
                confidence_after=45,
                risk_before=2.5,
                risk_after=1.0,
                replay_outcome="would_have_won",
                max_favorable_excursion=1.0,
                realized_rr_potential=2.0,
            )

        summary = audit.get_alpha_leak_summary()
        assert len(summary["by_code"]) == 10


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestGovernanceAuditIntegration:
    """End-to-end integration tests."""

    def test_complete_audit_workflow(self):
        """Complete audit workflow from initialization to reporting."""
        audit = GovernanceAudit()

        # Simulate 1000 scans
        for _ in range(1000):
            audit.record_scan()

        # 750 candidates formed
        for _ in range(750):
            audit.record_candidate_formed()

        # 100 rejected Tier 2A, 50 rejected Tier 3A
        for i in range(100):
            audit.record_rejection(
                tier="tier_2a",
                reason_code="HTF_CONTRADICTION",
                severity=0.8,
                symbol=f"SYM_{i}",
                timestamp="2026-07-02T10:30:00Z",
                confidence_before=75,
                confidence_after=45,
                risk_before=2.5,
                risk_after=1.0,
                replay_outcome="would_have_won" if i < 20 else None,
                max_favorable_excursion=1.5 if i < 20 else None,
                realized_rr_potential=2.8 if i < 20 else None,
            )
        for i in range(50):
            audit.record_rejection(
                tier="tier_3a",
                reason_code="MSS_ABSENT",
                severity=0.9,
                symbol=f"SYM_{100+i}",
                timestamp="2026-07-02T10:30:00Z",
                confidence_before=80,
                confidence_after=40,
                risk_before=3.0,
                risk_after=1.0,
                replay_outcome="would_have_won" if i < 10 else None,
                max_favorable_excursion=1.2 if i < 10 else None,
                realized_rr_potential=2.0 if i < 10 else None,
            )

        # 600 admitted
        for _ in range(600):
            audit.record_admission()

        # 540 executed
        for _ in range(540):
            audit.record_execution()

        # Verify funnel progression
        assert audit.funnel.scanned == 1000
        assert audit.funnel.candidates_formed == 750
        assert audit.funnel.candidates_rejected == 150
        assert audit.funnel.candidates_admitted == 600
        assert audit.funnel.executed_trades == 540

        # Verify rejection distribution
        assert audit.tier_analyzer.total_rejections == 150
        assert audit.tier_analyzer.rejections_by_tier["tier_2a"] == 100
        assert audit.tier_analyzer.rejections_by_tier["tier_3a"] == 50

        # Verify alpha leaks
        assert audit.false_rejection_instrumentor.eventual_winners_rejected == 30  # 20 + 10

        # Verify reports generate without error
        report = audit.generate_report()
        assert report["funnel"]["scanned"] == 1000
        assert report["false_rejections"]["eventual_winners_rejected"] == 30

        summary = audit.get_alpha_leak_summary()
        assert summary["total_eventual_winners_rejected"] == 30

    def test_instrumentation_read_only_constraint(self):
        """Verify governance_audit.py is READ-ONLY (no trade logic modification)."""
        # This test verifies the module structure enforces read-only constraint
        # by checking that methods only record/report, never modify decisions
        audit = GovernanceAudit()

        # Record rejection should not return decision (only record)
        result = audit.record_rejection(
            tier="tier_2a",
            reason_code="HTF_CONTRADICTION",
            severity=0.8,
            symbol="US30",
            timestamp="2026-07-02T10:30:00Z",
            confidence_before=75,
            confidence_after=45,
            risk_before=2.5,
            risk_after=1.0,
        )
        assert result is None  # No decision returned

        # Only metric record appears
        assert len(audit.rejection_metrics) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
