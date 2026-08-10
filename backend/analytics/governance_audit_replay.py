"""STAGE O2.4: Governance Audit Replay Run

Analyzes historical decision journal against tier-based governance.

This is analytics-only: zero decision changes.
"""

import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from backend.analytics.governance_audit import GovernanceAudit


# ============================================================================
# TIER CLASSIFICATION MAPPING
# ============================================================================
# Maps rejection reasons to tiers based on O2C/O2D classifications

TIER_CLASSIFICATION = {
    # TIER 1: Hard Safety (severity 1.0)
    "Daily loss limit hit": ("tier_1", "DAILY_LOSS_BREACH", 1.0),
    "High impact news lock active": ("tier_1", "NEWS_LOCK", 1.0),
    "Broker locked": ("tier_1", "BROKER_LOCK", 1.0),
    "Symbol locked": ("tier_1", "SYMBOL_LOCK", 1.0),
    "Max trades per day hit": ("tier_1", "MAX_TRADES_EXCEEDED", 1.0),
    "Outside valid killzone": ("tier_1", "KILLZONE_INVALID", 1.0),

    # TIER 2A: Macro Truth Veto (severity 0.8)
    "HTF narrative opposite": ("tier_2a", "HTF_CONTRADICTION", 0.8),
    "Macro liquidity absent": ("tier_2a", "MACRO_LIQUIDITY_ABSENCE", 0.8),
    "Forex daily bias not aligned": ("tier_2a", "FX_DAILY_BIAS_CONTRADICTION", 0.8),
    "Forex 4H narrative not aligned": ("tier_2a", "FX_4H_NARRATIVE_CONTRADICTION", 0.8),

    # TIER 2B: Macro Confidence Penalty (severity 0.3)
    "london_open": ("tier_2b", "LONDON_OPEN", 0.3),
    "range_phase": ("tier_2b", "RANGE_PHASE", 0.3),
    "News event window": ("tier_2b", "NEWS_WINDOW", 0.3),
    "Low volatility phase": ("tier_2b", "LOW_VOLATILITY", 0.3),

    # TIER 3A: Structural Validity Veto (severity 0.9)
    "MSS not confirmed": ("tier_3a", "MSS_ABSENT", 0.9),
    "FVG not detected": ("tier_3a", "NO_EXECUTABLE_FVG", 0.9),
    "RR below 3": ("tier_3a", "IMPOSSIBLE_RR_STRUCTURE", 0.9),
    "Invalid stop structure": ("tier_3a", "INVALID_STOP_STRUCTURE", 0.9),
    "FVG direction not aligned with MSS": ("tier_3a", "FVG_MSS_MISALIGNMENT", 0.9),

    # TIER 3B: Setup Quality Scaling (severity 0.2)
    "Weak SMT": ("tier_3b", "WEAK_SMT_CONFLUENCE", 0.2),
    "Weak FVG": ("tier_3b", "WEAK_FVG_CLARITY", 0.2),
    "FVG quality": ("tier_3b", "MEDIOCRE_FVG_GRADE", 0.2),
}

# Fallback classification for unmapped reasons
FALLBACK_TIER_MAP = {
    "killzone": ("tier_1", "KILLZONE_INVALID", 1.0),
    "daily": ("tier_1", "DAILY_LOSS_BREACH", 1.0),
    "news": ("tier_1", "NEWS_LOCK", 1.0),
    "mss": ("tier_3a", "MSS_ABSENT", 0.9),
    "fvg": ("tier_3a", "NO_EXECUTABLE_FVG", 0.9),
    "weak": ("tier_3b", "WEAK_SMT_CONFLUENCE", 0.2),
}


def classify_rejection(reason: str) -> tuple[str, str, float]:
    """Classify a rejection reason to (tier, code, severity)."""
    # Exact match
    if reason in TIER_CLASSIFICATION:
        return TIER_CLASSIFICATION[reason]

    # Fuzzy match on lowercase
    reason_lower = reason.lower()
    for key, value in TIER_CLASSIFICATION.items():
        if key.lower() in reason_lower or reason_lower in key.lower():
            return value

    # Fallback: check keyword patterns
    for keyword, tier_info in FALLBACK_TIER_MAP.items():
        if keyword in reason_lower:
            return tier_info

    # Default: classify as Tier 3B (lowest severity for unknown)
    return ("tier_3b", "UNCLASSIFIED", 0.2)


def load_decisions(filepath: str) -> list[dict]:
    """Load decision journal from JSONL file."""
    decisions = []
    try:
        with open(filepath, 'r') as f:
            for line in f:
                if line.strip():
                    try:
                        decisions.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        print(f"Warning: {filepath} not found")
        return []

    return decisions


def filter_production_symbols(decisions: list[dict]) -> list[dict]:
    """Filter to production symbols only (US30, XAUUSD)."""
    production_symbols = {"US30", "XAUUSD"}
    return [d for d in decisions if d.get("symbol") in production_symbols]


def run_governance_audit(decisions: list[dict]) -> GovernanceAudit:
    """Process decisions through governance audit."""
    audit = GovernanceAudit()

    for decision in decisions:
        symbol = decision.get("symbol", "UNKNOWN")
        timestamp = decision.get("timestamp", datetime.now().isoformat())
        decision_type = decision.get("decision", "UNKNOWN").upper()
        confidence = decision.get("confidence", 0)
        rejection_reasons = decision.get("rejection_reasons", [])

        # Record scan
        audit.record_scan()

        # If rejected, record rejection details
        if decision_type == "REJECTED" and rejection_reasons:
            audit.record_candidate_formed()

            # For each rejection reason, track tier distribution and metrics
            # but only record one rejection event per candidate (on highest severity)
            highest_severity = -1.0
            primary_tier = "tier_3b"
            primary_code = "UNCLASSIFIED"
            rejection_count = 0

            for reason in rejection_reasons:
                tier, code, severity = classify_rejection(reason)
                rejection_count += 1

                # Track tier distribution by recording each reason
                audit.tier_analyzer.record_rejection(tier, code, severity)

                # Track the highest severity reason
                if severity > highest_severity:
                    highest_severity = severity
                    primary_tier = tier
                    primary_code = code

            # Record one rejection metric for the candidate
            confidence_after = max(0, confidence - int(highest_severity * 50))
            risk_before = decision.get("risk", {}).get("risk_amount", 0.0)
            risk_after = max(0.0, risk_before * (1 - highest_severity))

            # Create single rejection metric
            from backend.analytics.governance_audit import RejectionMetric
            metric = RejectionMetric(
                symbol=symbol,
                timestamp=timestamp,
                tier=primary_tier,
                reason_code=primary_code,
                severity=highest_severity,
                confidence_before=confidence,
                confidence_after=confidence_after,
                risk_before=risk_before,
                risk_after=risk_after,
            )
            audit.rejection_metrics.append(metric)
            audit.funnel.candidates_rejected += 1

        elif decision_type == "APPROVED":
            audit.record_candidate_formed()
            audit.record_admission()
            audit.record_execution()

    return audit


def print_funnel_report(audit: GovernanceAudit):
    """Print admission funnel report."""
    funnel = audit.funnel
    print("\n" + "="*70)
    print("FUNNEL REPORT: Admission Pipeline")
    print("="*70)
    print(f"\nScanned setups:      {funnel.scanned:,}")
    print(f"Candidates formed:   {funnel.candidates_formed:,} ({funnel.completion_rate():.1f}%)")
    print(f"  - Rejected:        {funnel.candidates_rejected:,} ({funnel.rejection_rate():.1f}% of formed)")
    print(f"  - Admitted:        {funnel.candidates_admitted:,} ({funnel.admission_rate():.1f}% of formed)")
    print(f"Executed trades:     {funnel.executed_trades:,} ({funnel.execution_rate():.1f}% of admitted)")

    print(f"\nFunnel Efficiency:")
    print(f"  Completion rate:   {funnel.completion_rate():.1f}%")
    print(f"  Rejection rate:    {funnel.rejection_rate():.1f}%")
    print(f"  Admission rate:    {funnel.admission_rate():.1f}%")
    print(f"  Execution rate:    {funnel.execution_rate():.1f}%")


def print_tier_distribution(audit: GovernanceAudit):
    """Print tier rejection distribution."""
    print("\n" + "="*70)
    print("TIER REJECTION DISTRIBUTION")
    print("="*70)

    dist = audit.tier_analyzer.get_tier_distribution()
    if not dist:
        print("\nNo rejections recorded.")
        return

    # Sort by percentage descending
    sorted_tiers = sorted(dist.items(), key=lambda x: x[1], reverse=True)

    print(f"\nTotal rejections: {audit.tier_analyzer.total_rejections}")
    print("\nBy tier:")
    for tier, percentage in sorted_tiers:
        count = audit.tier_analyzer.rejections_by_tier[tier]
        print(f"  {tier:20s}: {count:5d} ({percentage:6.1f}%)")


def print_top_rejection_codes(audit: GovernanceAudit, limit: int = 20):
    """Print top rejection codes by frequency."""
    print("\n" + "="*70)
    print(f"TOP {limit} REJECTION CODES")
    print("="*70)

    top_codes = audit.tier_analyzer.get_top_rejection_codes(limit=limit)
    if not top_codes:
        print("\nNo rejections recorded.")
        return

    print(f"\nRank | Code                          | Count | Tier      | % of Total")
    print("-" * 80)

    for i, (code, count) in enumerate(top_codes, 1):
        pct = (count / audit.tier_analyzer.total_rejections) * 100
        code_data = audit.tier_analyzer.rejections_by_code[code]
        tier = code_data.get("tier", "UNKNOWN")
        severity = code_data.get("severity_avg", 0.0)

        print(f"{i:4d} | {code:30s} | {count:5d} | {tier:9s} | {pct:6.1f}%")


def print_alpha_leak_report(audit: GovernanceAudit):
    """Print alpha leak analysis (replay-only)."""
    print("\n" + "="*70)
    print("ALPHA LEAK REPORT (Replay-Only)")
    print("="*70)

    summary = audit.get_alpha_leak_summary()
    total_leaks = summary["total_eventual_winners_rejected"]

    if total_leaks == 0:
        print("\nNo alpha leaks detected.")
        return

    print(f"\nTotal eventual winners rejected: {total_leaks}")

    print("\nBy tier (alpha leak count):")
    for item in summary["by_tier"]:
        tier = item["tier"]
        count = item["count"]
        pct = (count / total_leaks) * 100 if total_leaks > 0 else 0
        print(f"  {tier:20s}: {count:5d} ({pct:6.1f}%)")

    print("\nTop alpha leak reasons (code-level):")
    for i, item in enumerate(summary["by_code"][:10], 1):
        code = item["code"]
        count = item["count"]
        pct = (count / total_leaks) * 100 if total_leaks > 0 else 0
        print(f"  {i:2d}. {code:30s}: {count:5d} ({pct:6.1f}%)")

    # MFE statistics
    mfe_stats = audit.false_rejection_instrumentor.get_mfe_statistics()
    if mfe_stats["samples"] > 0:
        print(f"\nMax Favorable Excursion (MFE) Statistics:")
        print(f"  Samples:             {mfe_stats['samples']}")
        print(f"  Average MFE:         {mfe_stats['avg_mfe']:.4f}")
        print(f"  Average RR Potential: {mfe_stats['avg_rr_potential']:.4f}")
        print(f"  Max MFE:             {mfe_stats['max_mfe']:.4f}")
        print(f"  Min MFE:             {mfe_stats['min_mfe']:.4f}")


def print_tier_leak_severity_ranking(audit: GovernanceAudit):
    """Rank tiers by alpha damage (leak count × severity)."""
    print("\n" + "="*70)
    print("TIER LEAK SEVERITY RANKING")
    print("="*70)

    summary = audit.get_alpha_leak_summary()
    total_leaks = summary["total_eventual_winners_rejected"]

    if total_leaks == 0:
        print("\nNo alpha leaks to rank.")
        return

    # Calculate damage score: leak_count × average_severity
    tier_damage = {}
    for item in summary["by_tier"]:
        tier = item["tier"]
        count = item["count"]

        # Get average severity for this tier
        code_leak = audit.false_rejection_instrumentor.get_code_alpha_leak()
        tier_codes = [code for code, data in audit.tier_analyzer.rejections_by_code.items()
                      if data.get("tier") == tier and code in code_leak]

        avg_severity = 0.0
        if tier_codes:
            severities = [audit.tier_analyzer.rejections_by_code[code]["severity_avg"]
                         for code in tier_codes]
            avg_severity = sum(severities) / len(severities)

        damage_score = count * avg_severity
        tier_damage[tier] = (count, avg_severity, damage_score)

    # Sort by damage score
    sorted_tiers = sorted(tier_damage.items(), key=lambda x: x[1][2], reverse=True)

    print(f"\nTier Damage Ranking (leak_count × avg_severity):")
    print(f"\n{'Rank':<5} {'Tier':<20} {'Leaks':<8} {'Avg Sev':<10} {'Damage':<10}")
    print("-" * 60)

    for i, (tier, (count, avg_sev, damage)) in enumerate(sorted_tiers, 1):
        severity_label = {
            0.2: "LOW     ",
            0.3: "PENALTY ",
            0.8: "HIGH    ",
            0.9: "CRITICAL",
            1.0: "FATAL   ",
        }.get(avg_sev, f"{avg_sev:.1f}    ")

        print(f"{i:<5} {tier:<20} {count:<8} {severity_label:<10} {damage:<10.1f}")


def print_constitutional_diagnosis(audit: GovernanceAudit):
    """Answer constitutional diagnosis questions."""
    print("\n" + "="*70)
    print("CONSTITUTIONAL DIAGNOSIS")
    print("="*70)

    funnel = audit.funnel
    summary = audit.get_alpha_leak_summary()
    total_leaks = summary["total_eventual_winners_rejected"]

    # Q1: Is Sentinel overblocking?
    rejection_rate = funnel.rejection_rate() if funnel.candidates_formed > 0 else 0
    is_overblocking = rejection_rate > 30  # Threshold: >30% rejection rate

    print(f"\n1. Is Sentinel overblocking?")
    print(f"   Rejection rate: {rejection_rate:.1f}%")
    if is_overblocking:
        print(f"   >> YES. Sentinel rejects >{rejection_rate:.1f}% of candidates.")
    else:
        print(f"   >> NO. Rejection rate is reasonable ({rejection_rate:.1f}%).")

    # Q2: Which tier is most restrictive?
    dist = audit.tier_analyzer.get_tier_distribution()
    if dist:
        most_restrictive = max(dist.items(), key=lambda x: x[1])
        print(f"\n2. Which tier is most restrictive?")
        print(f"   {most_restrictive[0]}: {most_restrictive[1]:.1f}% of all rejections")
        print(f"   >> {most_restrictive[0]} is the primary rejection source.")

    # Q3: Which rejection reason leaks most alpha?
    if total_leaks > 0:
        top_leak_reasons = audit.get_alpha_leak_summary()["by_code"]
        if top_leak_reasons:
            top_reason = top_leak_reasons[0]
            print(f"\n3. Which rejection reason leaks most alpha?")
            print(f"   {top_reason['code']}: {top_reason['count']} false rejections")
            print(f"   >> {top_reason['code']} is the primary alpha leak source.")
    else:
        print(f"\n3. Which rejection reason leaks most alpha?")
        print(f"   (No replay data available)")

    # Q4: Is O2E dual admission still necessary?
    tier_3a_pct = dist.get("tier_3a", 0) if dist else 0
    tier_3b_pct = dist.get("tier_3b", 0) if dist else 0
    tier_2a_pct = dist.get("tier_2a", 0) if dist else 0

    print(f"\n4. Is O2E dual admission still necessary?")
    print(f"   Tier 3A (structural): {tier_3a_pct:.1f}%")
    print(f"   Tier 3B (quality):    {tier_3b_pct:.1f}%")

    if tier_3a_pct + tier_3b_pct > 50:
        print(f"   >> YES. Structural/quality issues drive >{tier_3a_pct + tier_3b_pct:.1f}% of rejections.")
        print(f"      Dual admission (allowing Tier 3B admits despite 3B soft penalties) could help.")
    else:
        print(f"   >> MAYBE. Structural/quality issues drive {tier_3a_pct + tier_3b_pct:.1f}% of rejections.")
        print(f"      Consider O3 guardrail softening first.")

    # Q5: Should O3 or O4 be prioritized?
    if tier_2a_pct > 30:
        print(f"\n5. Should O3 (guardrail softening) or O4 (O3 Gold MPV) be prioritized?")
        print(f"   Tier 2A (macro truth): {tier_2a_pct:.1f}%")
        print(f"   >> Prioritize O3 guardrail softening to reduce macro truth vetoes.")
    elif tier_3a_pct > 50:
        print(f"\n5. Should O3 (guardrail softening) or O4 (O3 Gold MPV) be prioritized?")
        print(f"   Tier 3A (structural):  {tier_3a_pct:.1f}%")
        print(f"   >> Prioritize O2E dual admission (Tier 3B scaling) first.")
    else:
        print(f"\n5. Should O3 (guardrail softening) or O4 (O3 Gold MPV) be prioritized?")
        print(f"   Rejection distributed across tiers.")
        print(f"   >> Need more data to recommend priority.")


def main():
    """Run complete governance audit replay analysis."""
    print("\n" + "="*70)
    print("STAGE O2.4: GOVERNANCE AUDIT REPLAY RUN")
    print("="*70)
    print("\nAnalyzing historical decision journal through tier-based governance...")

    # Load decisions
    decisions_path = Path("data/journal/sentinel_decisions.jsonl")
    print(f"\nLoading decisions from {decisions_path}...")
    all_decisions = load_decisions(str(decisions_path))
    print(f"Loaded {len(all_decisions)} total decisions")

    # Filter to production symbols
    prod_decisions = filter_production_symbols(all_decisions)
    print(f"Filtered to production symbols (US30, XAUUSD): {len(prod_decisions)} decisions")

    # Run governance audit
    print("\nProcessing through governance audit harness...")
    audit = run_governance_audit(prod_decisions)

    # Generate reports
    print_funnel_report(audit)
    print_tier_distribution(audit)
    print_top_rejection_codes(audit, limit=20)
    print_alpha_leak_report(audit)
    print_tier_leak_severity_ranking(audit)
    print_constitutional_diagnosis(audit)

    # Generate structured report
    report = audit.generate_report()

    return audit, report


if __name__ == "__main__":
    audit, report = main()

    # Save report to JSON
    report_path = Path("data/reports/governance_audit_replay_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"\n[OK] Detailed report saved to {report_path}")
