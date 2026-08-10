"""Governance Audit Replay Harness: O2.25 Instrumentation

Purpose:
Measure Sentinel's current admission-control behavior using tier architecture.
Quantify alpha strangulation and governance efficiency.

CONSTRAINT: This module is READ-ONLY relative to trading behavior.
- No changes to trade decisions
- No changes to admission logic
- No changes to confidence scoring
- No changes to risk sizing
- No changes to guardrails
- Instrumentation only

Per SENTINEL_CONSTITUTION.md Law 4:
"Every rejection must be measurable."

This module provides measurement infrastructure for tier-based rejection analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime
from collections import defaultdict


@dataclass
class RejectionMetric:
    """Single rejection measurement point."""

    symbol: str
    timestamp: str
    tier: str
    reason_code: str
    severity: float
    confidence_before: int
    confidence_after: int
    risk_before: float
    risk_after: float

    # Replay-only outcome tracking
    replay_outcome: Optional[str] = None  # "would_have_won", "would_have_lost", "inconclusive"
    max_favorable_excursion: Optional[float] = None
    realized_rr_potential: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        """Export as dictionary."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "tier": self.tier,
            "reason_code": self.reason_code,
            "severity": self.severity,
            "confidence_before": self.confidence_before,
            "confidence_after": self.confidence_after,
            "risk_before": self.risk_before,
            "risk_after": self.risk_after,
            "replay_outcome": self.replay_outcome,
            "max_favorable_excursion": self.max_favorable_excursion,
            "realized_rr_potential": self.realized_rr_potential,
        }


@dataclass
class FunnelMetrics:
    """Candidate funnel tracking."""

    scanned: int = 0           # Total setups scanned
    candidates_formed: int = 0 # Setups with valid structure
    candidates_rejected: int = 0 # Rejected at admission
    candidates_admitted: int = 0 # Passed admission
    executed_trades: int = 0   # Actually executed

    def completion_rate(self) -> float:
        """Percentage of scanned that became candidates."""
        if self.scanned == 0:
            return 0.0
        return (self.candidates_formed / self.scanned) * 100

    def rejection_rate(self) -> float:
        """Percentage of candidates rejected."""
        if self.candidates_formed == 0:
            return 0.0
        return (self.candidates_rejected / self.candidates_formed) * 100

    def admission_rate(self) -> float:
        """Percentage of candidates admitted."""
        if self.candidates_formed == 0:
            return 0.0
        return (self.candidates_admitted / self.candidates_formed) * 100

    def execution_rate(self) -> float:
        """Percentage of admitted that executed."""
        if self.candidates_admitted == 0:
            return 0.0
        return (self.executed_trades / self.candidates_admitted) * 100


class TierRejectionAnalyzer:
    """Analyze rejection distribution across tiers."""

    def __init__(self) -> None:
        self.rejections_by_tier: dict[str, int] = defaultdict(int)
        self.rejections_by_code: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "count": 0,
                "percentage": 0.0,
                "tier": None,
                "severity_avg": 0.0,
                "severity_samples": 0,
            }
        )
        self.total_rejections: int = 0

    def record_rejection(
        self,
        tier: str,
        reason_code: str,
        severity: float,
    ) -> None:
        """Record a rejection event."""
        self.rejections_by_tier[tier] += 1
        self.rejections_by_code[reason_code]["count"] += 1
        self.rejections_by_code[reason_code]["tier"] = tier

        # Track severity average
        code_data = self.rejections_by_code[reason_code]
        old_avg = code_data["severity_avg"]
        old_samples = code_data["severity_samples"]
        new_samples = old_samples + 1
        code_data["severity_avg"] = (old_avg * old_samples + severity) / new_samples
        code_data["severity_samples"] = new_samples

        self.total_rejections += 1

    def get_tier_distribution(self) -> dict[str, float]:
        """Get percentage distribution by tier."""
        if self.total_rejections == 0:
            return {}

        return {
            tier: (count / self.total_rejections) * 100
            for tier, count in self.rejections_by_tier.items()
        }

    def get_code_distribution(self) -> dict[str, dict[str, Any]]:
        """Get rejection code statistics."""
        for reason_code in self.rejections_by_code:
            count = self.rejections_by_code[reason_code]["count"]
            self.rejections_by_code[reason_code]["percentage"] = (
                (count / self.total_rejections) * 100 if self.total_rejections > 0 else 0.0
            )

        return dict(self.rejections_by_code)

    def get_top_rejection_codes(self, limit: int = 10) -> list[tuple[str, int]]:
        """Get most common rejection codes."""
        codes = [
            (code, data["count"])
            for code, data in self.rejections_by_code.items()
        ]
        codes.sort(key=lambda x: x[1], reverse=True)
        return codes[:limit]


class FalseRejectionInstrumentor:
    """Track replay-only false rejection metrics (raw counts)."""

    def __init__(self) -> None:
        self.false_rejections_by_tier: dict[str, int] = defaultdict(int)
        self.false_rejections_by_code: dict[str, int] = defaultdict(int)
        self.eventual_winners_rejected: int = 0
        self.mfe_tracking: list[dict[str, Any]] = []

    def record_eventual_winner(
        self,
        tier: str,
        reason_code: str,
        max_favorable_excursion: float,
        realized_rr_potential: float,
    ) -> None:
        """Record a setup that was rejected but would have been profitable."""
        self.false_rejections_by_tier[tier] += 1
        self.false_rejections_by_code[reason_code] += 1
        self.eventual_winners_rejected += 1

        self.mfe_tracking.append({
            "tier": tier,
            "reason_code": reason_code,
            "mfe": max_favorable_excursion,
            "rr_potential": realized_rr_potential,
        })

    def get_tier_alpha_leak(self) -> dict[str, int]:
        """Report rejected setups that would have been winners by tier."""
        return dict(self.false_rejections_by_tier)

    def get_code_alpha_leak(self) -> dict[str, int]:
        """Report rejected setups that would have been winners by reason code."""
        return dict(self.false_rejections_by_code)

    def get_mfe_statistics(self) -> dict[str, Any]:
        """Get max favorable excursion statistics."""
        if not self.mfe_tracking:
            return {
                "samples": 0,
                "avg_mfe": 0.0,
                "avg_rr_potential": 0.0,
                "max_mfe": 0.0,
                "min_mfe": 0.0,
            }

        mfes = [m["mfe"] for m in self.mfe_tracking]
        rrs = [m["rr_potential"] for m in self.mfe_tracking]

        return {
            "samples": len(self.mfe_tracking),
            "avg_mfe": sum(mfes) / len(mfes),
            "avg_rr_potential": sum(rrs) / len(rrs) if rrs else 0.0,
            "max_mfe": max(mfes),
            "min_mfe": min(mfes),
        }


class GovernanceAudit:
    """
    Complete governance audit harness.

    Measures tier-based admission behavior without changing trade decisions.
    """

    def __init__(self) -> None:
        self.tier_analyzer = TierRejectionAnalyzer()
        self.false_rejection_instrumentor = FalseRejectionInstrumentor()
        self.funnel = FunnelMetrics()
        self.rejection_metrics: list[RejectionMetric] = []

    def record_scan(self) -> None:
        """Record a setup scan event."""
        self.funnel.scanned += 1

    def record_candidate_formed(self) -> None:
        """Record a valid candidate formed."""
        self.funnel.candidates_formed += 1

    def record_rejection(
        self,
        tier: str,
        reason_code: str,
        severity: float,
        symbol: str,
        timestamp: str,
        confidence_before: int,
        confidence_after: int,
        risk_before: float,
        risk_after: float,
        replay_outcome: Optional[str] = None,
        max_favorable_excursion: Optional[float] = None,
        realized_rr_potential: Optional[float] = None,
    ) -> None:
        """Record a rejection event."""
        self.tier_analyzer.record_rejection(tier, reason_code, severity)
        self.funnel.candidates_rejected += 1

        metric = RejectionMetric(
            symbol=symbol,
            timestamp=timestamp,
            tier=tier,
            reason_code=reason_code,
            severity=severity,
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            risk_before=risk_before,
            risk_after=risk_after,
            replay_outcome=replay_outcome,
            max_favorable_excursion=max_favorable_excursion,
            realized_rr_potential=realized_rr_potential,
        )
        self.rejection_metrics.append(metric)

        # Track eventual winners (replay-only)
        if (
            replay_outcome == "would_have_won"
            and max_favorable_excursion is not None
            and realized_rr_potential is not None
        ):
            self.false_rejection_instrumentor.record_eventual_winner(
                tier,
                reason_code,
                max_favorable_excursion,
                realized_rr_potential,
            )

    def record_admission(self) -> None:
        """Record a candidate admitted to execution."""
        self.funnel.candidates_admitted += 1

    def record_execution(self) -> None:
        """Record a trade executed."""
        self.funnel.executed_trades += 1

    def generate_report(self) -> dict[str, Any]:
        """Generate complete governance audit report."""
        return {
            "timestamp": datetime.now().isoformat(),
            "funnel": {
                "scanned": self.funnel.scanned,
                "candidates_formed": self.funnel.candidates_formed,
                "candidates_rejected": self.funnel.candidates_rejected,
                "candidates_admitted": self.funnel.candidates_admitted,
                "executed_trades": self.funnel.executed_trades,
                "completion_rate_pct": round(self.funnel.completion_rate(), 2),
                "rejection_rate_pct": round(self.funnel.rejection_rate(), 2),
                "admission_rate_pct": round(self.funnel.admission_rate(), 2),
                "execution_rate_pct": round(self.funnel.execution_rate(), 2),
            },
            "tier_distribution": {
                tier: round(pct, 2)
                for tier, pct in self.tier_analyzer.get_tier_distribution().items()
            },
            "rejection_codes": {
                code: {
                    "count": data["count"],
                    "percentage": round(data["percentage"], 2),
                    "tier": data["tier"],
                    "avg_severity": round(data["severity_avg"], 2),
                }
                for code, data in self.tier_analyzer.get_code_distribution().items()
            },
            "top_rejection_codes": [
                {"code": code, "count": count}
                for code, count in self.tier_analyzer.get_top_rejection_codes()
            ],
            "false_rejections": {
                "eventual_winners_rejected": self.false_rejection_instrumentor.eventual_winners_rejected,
                "by_tier": self.false_rejection_instrumentor.get_tier_alpha_leak(),
                "by_code": self.false_rejection_instrumentor.get_code_alpha_leak(),
                "mfe_statistics": self.false_rejection_instrumentor.get_mfe_statistics(),
            },
            "total_rejection_metrics": len(self.rejection_metrics),
        }

    def get_alpha_leak_summary(self) -> dict[str, Any]:
        """Get summary of which tiers reject eventual winners."""
        tier_leak = self.false_rejection_instrumentor.get_tier_alpha_leak()
        code_leak = self.false_rejection_instrumentor.get_code_alpha_leak()

        # Sort by leak count
        sorted_tiers = sorted(tier_leak.items(), key=lambda x: x[1], reverse=True)
        sorted_codes = sorted(code_leak.items(), key=lambda x: x[1], reverse=True)

        return {
            "total_eventual_winners_rejected": self.false_rejection_instrumentor.eventual_winners_rejected,
            "by_tier": [{"tier": tier, "count": count} for tier, count in sorted_tiers],
            "by_code": [{"code": code, "count": count} for code, count in sorted_codes[:10]],
        }
