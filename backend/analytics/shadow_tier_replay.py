"""STAGE O2.46: Shadow Tier Replay Adapter

Runs TierAuthority in parallel with legacy BacktestEngine guardrails.
Records divergence without changing any production decisions.

This is analytics-only shadow mode: zero decision impact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from collections import Counter, defaultdict
from datetime import datetime


@dataclass
class CandidateSnapshot:
    """Snapshot of a replay candidate with both legacy and tier verdicts."""

    # Candidate identity
    symbol: str
    timestamp: str
    direction: str  # LONG or SHORT

    # Legacy BacktestEngine decision
    legacy_approved: bool
    legacy_reasons: list[str] = field(default_factory=list)

    # TierAuthority decision
    tier_1_pass: bool = True
    tier_2a_pass: bool = True
    tier_2b_penalty: float = 0.0  # 0.0 = no penalty, >0 = penalty applied
    tier_3a_pass: bool = True
    tier_3b_scale: float = 1.0  # 1.0 = no scaling, <1.0 = scaled down

    # Tier reasoning
    tier_1_reasons: list[str] = field(default_factory=list)
    tier_2a_reasons: list[str] = field(default_factory=list)
    tier_2b_reasons: list[str] = field(default_factory=list)
    tier_3a_reasons: list[str] = field(default_factory=list)
    tier_3b_reasons: list[str] = field(default_factory=list)

    # Outcome if executed (known from backtest results)
    actually_executed: bool = False
    win_or_loss: Optional[str] = None  # "WIN", "LOSS", "BREAKEVEN", None
    rr_realized: Optional[float] = None

    def tier_would_approve(self) -> bool:
        """Determine if TierAuthority would approve this candidate."""
        # Tier 1, 3A are hard blocks (veto)
        if not self.tier_1_pass or not self.tier_3a_pass:
            return False

        # Tier 2A is hard block (veto)
        if not self.tier_2a_pass:
            return False

        # Tier 2B and 3B are penalties/scaling only (not blocks)
        # They reduce confidence/position but don't block
        return True

    def legacy_tier_disagree(self) -> bool:
        """Check if legacy and tier verdicts disagree."""
        legacy_verdict = self.legacy_approved
        tier_verdict = self.tier_would_approve()
        return legacy_verdict != tier_verdict

    def disagreement_type(self) -> str:
        """Classify the type of disagreement (if any)."""
        if not self.legacy_tier_disagree():
            if self.legacy_approved:
                return "AGREE_APPROVE"
            else:
                return "AGREE_REJECT"

        if self.legacy_approved and not self.tier_would_approve():
            return "LEGACY_APPROVE_TIER_REJECT"
        elif not self.legacy_approved and self.tier_would_approve():
            return "LEGACY_REJECT_TIER_APPROVE"
        else:
            return "UNKNOWN_DIVERGENCE"


@dataclass
class ShadowReplayResults:
    """Aggregated results from shadow tier replay."""

    candidates: list[CandidateSnapshot] = field(default_factory=list)

    def add_candidate(self, snapshot: CandidateSnapshot) -> None:
        """Add a candidate snapshot."""
        self.candidates.append(snapshot)

    def disagreement_distribution(self) -> dict[str, int]:
        """Count disagreement types."""
        types = [c.disagreement_type() for c in self.candidates]
        return dict(Counter(types))

    def disagreement_percentages(self) -> dict[str, float]:
        """Calculate disagreement percentages."""
        total = len(self.candidates)
        if total == 0:
            return {}

        dist = self.disagreement_distribution()
        return {k: (v / total) * 100 for k, v in dist.items()}

    def winner_conflict_analysis(self) -> dict[str, Any]:
        """Analyze tier decisions on known winning trades."""
        winners = [c for c in self.candidates if c.actually_executed and c.win_or_loss == "WIN"]

        if not winners:
            return {
                "total_winners": 0,
                "tier_would_reject": 0,
                "tier_would_admit": 0,
                "rejected_by_tier": {
                    "tier_1": 0,
                    "tier_2a": 0,
                    "tier_3a": 0,
                    "detail": [],
                },
            }

        tier_rejects = [w for w in winners if not w.tier_would_approve()]
        tier_rejects_by_reason = defaultdict(list)

        for winner in tier_rejects:
            reasons = []
            if not winner.tier_1_pass:
                reasons.append("Tier 1 (hard safety)")
                tier_rejects_by_reason["tier_1"].append(winner)
            if not winner.tier_2a_pass:
                reasons.append("Tier 2A (macro truth)")
                tier_rejects_by_reason["tier_2a"].append(winner)
            if not winner.tier_3a_pass:
                reasons.append("Tier 3A (structural)")
                tier_rejects_by_reason["tier_3a"].append(winner)

            tier_rejects_by_reason["detail"].append({
                "symbol": winner.symbol,
                "timestamp": winner.timestamp,
                "reasons": reasons,
                "rr_realized": winner.rr_realized,
            })

        return {
            "total_winners": len(winners),
            "tier_would_reject": len(tier_rejects),
            "tier_would_admit": len(winners) - len(tier_rejects),
            "rejected_by_tier": {
                "tier_1": len(tier_rejects_by_reason["tier_1"]),
                "tier_2a": len(tier_rejects_by_reason["tier_2a"]),
                "tier_3a": len(tier_rejects_by_reason["tier_3a"]),
                "detail": tier_rejects_by_reason["detail"],
            },
        }

    def false_reject_analysis(self) -> dict[str, Any]:
        """Analyze tier potential improvements on legacy rejects."""
        legacy_rejects = [c for c in self.candidates if not c.legacy_approved]
        tier_would_admit = [c for c in legacy_rejects if c.tier_would_approve()]

        if not tier_would_admit:
            return {
                "total_legacy_rejects": len(legacy_rejects),
                "tier_would_admit": 0,
                "potential_improvement": 0.0,
                "top_false_rejects": [],
            }

        # Among tier admissions, how many were profitable?
        profitable_tier_admits = [
            c for c in tier_would_admit
            if c.actually_executed and c.win_or_loss == "WIN"
        ]

        return {
            "total_legacy_rejects": len(legacy_rejects),
            "tier_would_admit": len(tier_would_admit),
            "potential_improvement_pct": (len(tier_would_admit) / len(legacy_rejects)) * 100 if legacy_rejects else 0,
            "tier_admits_that_were_winners": len(profitable_tier_admits),
            "profitable_recovery_pct": (len(profitable_tier_admits) / len(tier_would_admit)) * 100 if tier_would_admit else 0,
        }

    def tier_disagreement_ranking(self) -> list[tuple[str, int]]:
        """Rank which tiers cause most disagreements."""
        tier_reject_counts = Counter()

        for candidate in self.candidates:
            if candidate.legacy_tier_disagree():
                if candidate.legacy_approved and not candidate.tier_would_approve():
                    # Legacy approved but tier rejects - determine which tier
                    if not candidate.tier_1_pass:
                        tier_reject_counts["Tier 1 (hard safety)"] += 1
                    elif not candidate.tier_2a_pass:
                        tier_reject_counts["Tier 2A (macro truth)"] += 1
                    elif not candidate.tier_3a_pass:
                        tier_reject_counts["Tier 3A (structural)"] += 1

        return tier_reject_counts.most_common()

    def final_verdict(self) -> dict[str, Any]:
        """Determine overall: would TierAuthority improve or worsen baseline?"""
        winners = [c for c in self.candidates if c.actually_executed and c.win_or_loss == "WIN"]
        losers = [c for c in self.candidates if c.actually_executed and c.win_or_loss == "LOSS"]

        total_executed = len(winners) + len(losers)

        if total_executed == 0:
            return {
                "verdict": "INSUFFICIENT_DATA",
                "reason": "No execution data available",
            }

        # Calculate impact
        tier_rejects_winners = len([c for c in winners if not c.tier_would_approve()])
        tier_admits_losers = len([c for c in losers if c.tier_would_approve()])

        net_impact = tier_admits_losers - tier_rejects_winners

        if net_impact < 0:
            return {
                "verdict": "IMPROVE",
                "reason": f"TierAuthority would reject {tier_rejects_winners} winners and admit {tier_admits_losers} losers. Net: -{abs(net_impact)} trades (improvement)",
                "winners_rejected": tier_rejects_winners,
                "losers_admitted": tier_admits_losers,
                "net_impact": net_impact,
                "pct_winners_rejected": (tier_rejects_winners / len(winners)) * 100 if winners else 0,
            }
        elif net_impact > 0:
            return {
                "verdict": "WORSEN",
                "reason": f"TierAuthority would admit {tier_admits_losers} losers while rejecting {tier_rejects_winners} winners. Net: +{net_impact} trades (worse)",
                "winners_rejected": tier_rejects_winners,
                "losers_admitted": tier_admits_losers,
                "net_impact": net_impact,
                "pct_losers_admitted": (tier_admits_losers / len(losers)) * 100 if losers else 0,
            }
        else:
            return {
                "verdict": "NEUTRAL",
                "reason": "TierAuthority rejects same number of winners as losers it admits",
                "winners_rejected": tier_rejects_winners,
                "losers_admitted": tier_admits_losers,
                "net_impact": 0,
            }


def print_divergence_matrix(results: ShadowReplayResults):
    """Print divergence distribution matrix."""
    print("\n" + "="*70)
    print("DIVERGENCE MATRIX: Legacy vs TierAuthority Verdicts")
    print("="*70)

    dist = results.disagreement_distribution()
    pcts = results.disagreement_percentages()
    total = len(results.candidates)

    print(f"\nTotal candidates analyzed: {total}")
    print("\nDistribution:")
    print(f"  AGREE_APPROVE      : {dist.get('AGREE_APPROVE', 0):5d} ({pcts.get('AGREE_APPROVE', 0):6.1f}%)")
    print(f"  AGREE_REJECT       : {dist.get('AGREE_REJECT', 0):5d} ({pcts.get('AGREE_REJECT', 0):6.1f}%)")
    print(f"  LEGACY_APPROVE_TIER_REJECT : {dist.get('LEGACY_APPROVE_TIER_REJECT', 0):5d} ({pcts.get('LEGACY_APPROVE_TIER_REJECT', 0):6.1f}%)")
    print(f"  LEGACY_REJECT_TIER_APPROVE : {dist.get('LEGACY_REJECT_TIER_APPROVE', 0):5d} ({pcts.get('LEGACY_REJECT_TIER_APPROVE', 0):6.1f}%)")

    total_disagreement = dist.get('LEGACY_APPROVE_TIER_REJECT', 0) + dist.get('LEGACY_REJECT_TIER_APPROVE', 0)
    total_agreement = dist.get('AGREE_APPROVE', 0) + dist.get('AGREE_REJECT', 0)

    print(f"\nSummary:")
    print(f"  Total agreement    : {total_agreement} ({(total_agreement/total)*100:.1f}%)")
    print(f"  Total disagreement : {total_disagreement} ({(total_disagreement/total)*100:.1f}%)")


def print_winner_conflict_analysis(results: ShadowReplayResults):
    """Print analysis of tier decisions on known winners."""
    print("\n" + "="*70)
    print("WINNER CONFLICT ANALYSIS: Would TierAuthority Reject Known Winners?")
    print("="*70)

    analysis = results.winner_conflict_analysis()
    total_winners = analysis["total_winners"]

    if total_winners == 0:
        print("\nNo winner data available.")
        return

    tier_rejects = analysis["rejected_by_tier"]

    print(f"\nTotal executed winners: {total_winners}")
    print(f"TierAuthority would REJECT: {analysis['tier_would_reject']} ({(analysis['tier_would_reject']/total_winners)*100:.1f}%)")
    print(f"TierAuthority would ADMIT:  {analysis['tier_would_admit']} ({(analysis['tier_would_admit']/total_winners)*100:.1f}%)")

    if analysis['tier_would_reject'] > 0:
        print(f"\nBreakdown of rejected winners by tier:")
        print(f"  Tier 1 (hard safety): {tier_rejects['tier_1']}")
        print(f"  Tier 2A (macro):      {tier_rejects['tier_2a']}")
        print(f"  Tier 3A (structural): {tier_rejects['tier_3a']}")

        if tier_rejects["detail"]:
            print(f"\nRejected winners (first 5):")
            for i, detail in enumerate(tier_rejects["detail"][:5], 1):
                print(f"  {i}. {detail['symbol']} @ {detail['timestamp']} - Reasons: {', '.join(detail['reasons'])} (RR: {detail['rr_realized']})")


def print_false_reject_analysis(results: ShadowReplayResults):
    """Print analysis of tier improvements on legacy rejects."""
    print("\n" + "="*70)
    print("FALSE REJECT ANALYSIS: Could TierAuthority Recover Profitable Setups?")
    print("="*70)

    analysis = results.false_reject_analysis()
    total_rejects = analysis["total_legacy_rejects"]

    if total_rejects == 0:
        print("\nNo legacy rejects to analyze.")
        return

    print(f"\nTotal legacy rejects: {total_rejects}")
    print(f"TierAuthority would ADMIT:  {analysis['tier_would_admit']} ({analysis['potential_improvement_pct']:.1f}%)")
    print(f"  - Among admits, were winners: {analysis['tier_admits_that_were_winners']} ({analysis['profitable_recovery_pct']:.1f}%)")

    print(f"\nInterpretation:")
    if analysis["potential_improvement_pct"] > 10:
        print(f"  TierAuthority could recover {analysis['potential_improvement_pct']:.1f}% of legacy rejects")
    if analysis['profitable_recovery_pct'] > 50:
        print(f"  Of those recoverable, {analysis['profitable_recovery_pct']:.1f}% were actually profitable")


def print_tier_disagreement_ranking(results: ShadowReplayResults):
    """Print which tiers cause most disagreement."""
    print("\n" + "="*70)
    print("TIER DISAGREEMENT RANKING: Which Tiers Create Most Conflicts?")
    print("="*70)

    ranking = results.tier_disagreement_ranking()

    if not ranking:
        print("\nNo disagreements detected.")
        return

    print(f"\nTiers causing legacy-tier divergence (where legacy approved but tier rejected):")
    for i, (tier, count) in enumerate(ranking, 1):
        print(f"  {i}. {tier}: {count} conflicts")


def print_final_verdict(results: ShadowReplayResults):
    """Print final verdict on TierAuthority impact."""
    print("\n" + "="*70)
    print("FINAL VERDICT: Would TierAuthority Improve or Worsen 56-Trade Baseline?")
    print("="*70)

    verdict = results.final_verdict()

    print(f"\nVerdict: {verdict['verdict']}")
    print(f"Reason:  {verdict['reason']}")

    if verdict['verdict'] != 'INSUFFICIENT_DATA':
        print(f"\nMetrics:")
        print(f"  Winners TierAuthority would reject: {verdict['winners_rejected']}")
        print(f"  Losers TierAuthority would admit:  {verdict['losers_admitted']}")
        print(f"  Net impact: {verdict['net_impact']:+d} trades")

        if "pct_winners_rejected" in verdict:
            print(f"  Percentage of winners rejected: {verdict['pct_winners_rejected']:.1f}%")
        if "pct_losers_admitted" in verdict:
            print(f"  Percentage of losers admitted: {verdict['pct_losers_admitted']:.1f}%")


if __name__ == "__main__":
    print("\n[Shadow Tier Replay Adapter module loaded]")
    print("[Ready to process backtest candidates in parallel with TierAuthority]")
