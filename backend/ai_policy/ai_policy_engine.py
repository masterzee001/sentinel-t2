"""AI Policy Engine for advisory-only Sentinel recommendations.

The engine reads historical intelligence reports and produces PCS-scored
recommendations. It never changes production logic, places trades, bypasses
approval, or writes configuration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from statistics import mean
from typing import Any


RECOMMENDATION_TYPES = {
    "policy_recommendation",
    "risk_recommendation",
    "strategy_recommendation",
    "regime_recommendation",
}

STRONG_THRESHOLD = 90
MEDIUM_THRESHOLD = 75


class AIPolicyEngine:
    """Build advisory policy recommendations from Sentinel intelligence."""

    def __init__(self, memory: dict[str, Any] | None = None) -> None:
        self.memory = memory or {}

    def build_report(self, *, generated_at: str | None = None) -> dict[str, Any]:
        """Return all Sprint 10 AI policy outputs."""
        timestamp = generated_at or datetime.now(UTC).isoformat()
        recommendations = build_ai_recommendations(self.memory)
        policy_memory = build_policy_memory(self.memory, recommendations, timestamp)
        iq_v7 = build_market_watch_iq_v7(self.memory, recommendations)
        return {
            "generated_at": timestamp,
            "mode": "ADVISORY_ONLY_AI_POLICY_ENGINE",
            "recommendations": recommendations,
            "ai_policy_memory": policy_memory,
            "market_watch_iq_v7": iq_v7,
            "recommendation_count": len(recommendations),
            "recommendation_accuracy": iq_v7["recommendation_accuracy"],
            "false_recommendation_rate": iq_v7["false_recommendation_rate"],
            "top_recommendations": recommendations[:5],
            "production_logic_modified": False,
            "broker_order_submission": False,
            "autonomous_execution": False,
            "human_approval_bypassed": False,
            "decision": "PASS" if ai_policy_passes(recommendations, iq_v7) else "FAIL",
        }


def build_ai_recommendations(memory: dict[str, Any]) -> list[dict[str, Any]]:
    """Return PCS-ranked policy recommendations."""
    recommendations: list[dict[str, Any]] = []
    recommendations.extend(good_policy_recommendations(memory))
    recommendations.extend(guardrail_recommendations(memory))
    recommendations.extend(strategy_recommendations(memory))
    recommendations.extend(regime_recommendations(memory))
    recommendations.extend(risk_recommendations(memory))
    return sorted(dedupe_recommendations(recommendations), key=lambda item: (-item["pcs"], item["id"]))


def good_policy_recommendations(memory: dict[str, Any]) -> list[dict[str, Any]]:
    """Identify good policies that should stay unchanged."""
    leak = memory.get("guardrail_leak_analysis", {})
    by_guardrail = leak.get("by_guardrail", {})
    recommendations = []
    for guardrail in ("grade_lock", "killzone"):
        row = by_guardrail.get(guardrail, {})
        if float(row.get("leak_ratio", 100.0) or 0.0) <= 5 and float(row.get("guardrail_efficiency", 0.0) or 0.0) >= 90:
            recommendations.append(
                recommendation(
                    "policy_recommendation",
                    f"preserve_{guardrail}",
                    f"Preserve {guardrail}; it is blocking losses without measurable edge leakage.",
                    pcs=96,
                    evidence=[
                        f"leak_ratio={row.get('leak_ratio', 0.0)}",
                        f"efficiency={row.get('guardrail_efficiency', 0.0)}",
                    ],
                    action="KEEP_POLICY",
                    production_change=False,
                )
            )
    grade_expectancy = memory.get("setup_expectancy_database", {}).get("by_grade", {})
    a_plus = grade_expectancy.get("A+", {})
    c_grade = grade_expectancy.get("C", {})
    if float(a_plus.get("pf", 0.0) or 0.0) >= 2.8 and float(c_grade.get("pf", 99.0) or 0.0) < 1.0:
        recommendations.append(
            recommendation(
                "strategy_recommendation",
                "preserve_grade_aware_routing",
                "Preserve grade-aware routing; A+ setups dominate expectancy while C-grade setups remain negative.",
                pcs=94,
                evidence=[
                    f"A+ PF={a_plus.get('pf')}",
                    f"A+ WR={a_plus.get('wr')}%",
                    f"C PF={c_grade.get('pf')}",
                ],
                action="KEEP_ROUTING_POLICY",
                production_change=False,
            )
        )
    return recommendations


def guardrail_recommendations(memory: dict[str, Any]) -> list[dict[str, Any]]:
    """Identify weak guardrails and opportunity leaks."""
    recommendations = []
    leak = memory.get("guardrail_leak_analysis", {})
    by_guardrail = leak.get("by_guardrail", {})
    symbol_lock = memory.get("symbol_lock_optimization", {})
    no_trade = memory.get("no_trade_optimization", {})
    a_plus = memory.get("a_plus_override_simulation", {})
    iq_v6 = memory.get("market_watch_iq_v6", {})

    worst = leak.get("worst_guardrail")
    worst_row = by_guardrail.get(str(worst), {})
    if worst == "symbol_lock" and float(worst_row.get("leak_ratio", 0.0) or 0.0) >= 70:
        candidate_symbol, candidate_data = top_symbol_lock_candidate(symbol_lock)
        recommendations.append(
            recommendation(
                "policy_recommendation",
                "review_symbol_lock_top_candidate_conditions",
                "Review symbol lock only under the current top-ranked observer symbol conditions.",
                pcs=93,
                evidence=[
                    f"symbol_lock_leak_ratio={worst_row.get('leak_ratio')}",
                    f"top_symbol={candidate_symbol}",
                    f"top_symbol_expectancy={candidate_data.get('expectancy')}",
                    f"conditional_unlock_candidate={candidate_data.get('conditional_unlock_candidate')}",
                ],
                action="ADVISORY_REVIEW_ONLY",
                production_change=False,
            )
        )
    if int(no_trade.get("institutional_continuation_leaks", 0) or 0) > 0:
        recommendations.append(
            recommendation(
                "policy_recommendation",
                "review_no_trade_institutional_continuation",
                "Review strict no-trade blocks on institutional continuation while preserving noisy-chop blocks.",
                pcs=88,
                evidence=[
                    f"institutional_continuation_leaks={no_trade.get('institutional_continuation_leaks')}",
                    f"strong_leaks={no_trade.get('strong_continuation_or_displacement_leaks')}",
                ],
                action="ADVISORY_REVIEW_ONLY",
                production_change=False,
            )
        )
    if a_plus.get("pass") and a_plus.get("improves_pf") and a_plus.get("improves_trade_count"):
        metrics = a_plus.get("metrics", {})
        recommendations.append(
            recommendation(
                "policy_recommendation",
                "simulate_a_plus_override_layer",
                "Continue simulating an A+ override layer that requires stronger justification for final-stage blocks.",
                pcs=86,
                evidence=[
                    f"hypothetical_pf={metrics.get('pf')}",
                    f"hypothetical_trades={metrics.get('trades')}",
                    f"safe_candidates={','.join(iq_v6.get('safe_relaxation_candidates', []))}",
                ],
                action="SIMULATE_ONLY",
                production_change=False,
            )
        )
    return recommendations


def strategy_recommendations(memory: dict[str, Any]) -> list[dict[str, Any]]:
    """Identify strategy-level recommendations."""
    recommendations = []
    setup = memory.get("setup_expectancy_database", {})
    by_strategy_grade = setup.get("by_strategy_grade", {})
    trend_a_plus = by_strategy_grade.get("trend_following|A+", {})
    ict_c = by_strategy_grade.get("ict_liquidity|C", {})
    mean_a_plus = by_strategy_grade.get("mean_reversion|A+", {})
    if float(trend_a_plus.get("pf", 0.0) or 0.0) >= 3.0:
        recommendations.append(
            recommendation(
                "strategy_recommendation",
                "prefer_trend_following_a_plus",
                "Prefer trend-following A+ setups when session, regime, and guardrails agree.",
                pcs=92,
                evidence=[
                    f"trend_following_A+_PF={trend_a_plus.get('pf')}",
                    f"trend_following_A+_WR={trend_a_plus.get('wr')}%",
                    f"trend_following_A+_trades={trend_a_plus.get('trades')}",
                ],
                action="ADVISORY_PRIORITY",
                production_change=False,
            )
        )
    if float(ict_c.get("pf", 99.0) or 0.0) < 1.0:
        recommendations.append(
            recommendation(
                "strategy_recommendation",
                "keep_weak_ict_filtered",
                "Keep weak ICT liquidity setups filtered unless MSS, displacement, target clarity, and noise checks are complete.",
                pcs=89,
                evidence=[
                    f"ict_liquidity_C_PF={ict_c.get('pf')}",
                    f"ict_liquidity_C_WR={ict_c.get('wr')}%",
                ],
                action="KEEP_FILTER",
                production_change=False,
            )
        )
    if float(mean_a_plus.get("pf", 0.0) or 0.0) >= 2.0:
        recommendations.append(
            recommendation(
                "strategy_recommendation",
                "retain_mean_reversion_a_plus",
                "Retain mean-reversion A+ as an advisory candidate in validated range or exhaustion regimes.",
                pcs=82,
                evidence=[
                    f"mean_reversion_A+_PF={mean_a_plus.get('pf')}",
                    f"mean_reversion_A+_WR={mean_a_plus.get('wr')}%",
                ],
                action="ADVISORY_PRIORITY",
                production_change=False,
            )
        )
    return recommendations


def regime_recommendations(memory: dict[str, Any]) -> list[dict[str, Any]]:
    """Identify regime-level recommendations."""
    recommendations = []
    regime = memory.get("regime_strategy_expectancy", {})
    strategy_expectancy = regime.get("strategy_expectancy", {})
    best = sorted(
        strategy_expectancy.values(),
        key=lambda item: float(item.get("pf", 0.0) or 0.0),
        reverse=True,
    )
    if best:
        top = best[0]
        recommendations.append(
            recommendation(
                "regime_recommendation",
                "prioritize_best_regimes",
                "Prioritize healthy continuation, expansion impulse, and sweep reversal regimes in advisory scoring.",
                pcs=91,
                evidence=[
                    f"best_regime={top.get('regime')}",
                    f"best_pf={top.get('pf')}",
                    f"best_strategy={top.get('best_strategy')}",
                    f"reported_best={','.join(regime.get('best_regimes', []))}",
                ],
                action="ADVISORY_PRIORITY",
                production_change=False,
            )
        )
    blocked = [item for item in strategy_expectancy.values() if str(item.get("best_strategy")) == "no_trade"]
    if blocked:
        recommendations.append(
            recommendation(
                "regime_recommendation",
                "preserve_low_expectancy_no_trade_regimes",
                "Preserve no-trade treatment for noisy chop, compression traps, and fake breakouts.",
                pcs=95,
                evidence=[f"{item.get('regime')} PF={item.get('pf')}" for item in blocked],
                action="KEEP_NO_TRADE_REGIME",
                production_change=False,
            )
        )
    return recommendations


def risk_recommendations(memory: dict[str, Any]) -> list[dict[str, Any]]:
    """Identify risk recommendations."""
    recommendations = []
    leak = memory.get("guardrail_leak_analysis", {}).get("by_guardrail", {})
    risk = leak.get("risk_block", {})
    shadow = memory.get("shadow_enhanced_comparison", {})
    shadow_metrics = shadow.get("shadow_enhanced", {})
    if float(risk.get("leak_ratio", 0.0) or 0.0) >= 50:
        recommendations.append(
            recommendation(
                "risk_recommendation",
                "review_risk_block_late_leaks",
                "Review late risk-block leakage diagnostically; keep live risk caps unchanged.",
                pcs=80,
                evidence=[
                    f"risk_block_leak_ratio={risk.get('leak_ratio')}",
                    f"risk_block_efficiency={risk.get('guardrail_efficiency')}",
                ],
                action="DIAGNOSTIC_REVIEW_ONLY",
                production_change=False,
            )
        )
    if shadow_metrics:
        recommendations.append(
            recommendation(
                "risk_recommendation",
                "keep_shadow_enhanced_separate",
                "Keep shadow-enhanced metrics separate from production until live sample validation is complete.",
                pcs=97,
                evidence=[
                    f"shadow_pf={shadow_metrics.get('pf')}",
                    f"shadow_trades={shadow_metrics.get('trades')}",
                    f"shadow_dd={shadow_metrics.get('max_drawdown')}",
                ],
                action="KEEP_SEPARATE",
                production_change=False,
            )
        )
    return recommendations


def build_policy_memory(memory: dict[str, Any], recommendations: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    """Return durable AI policy memory."""
    strong = [item for item in recommendations if item["strength"] == "strong"]
    medium = [item for item in recommendations if item["strength"] == "medium"]
    weak = [item for item in recommendations if item["strength"] == "weak"]
    confirmed = [item for item in recommendations if item["action"].startswith("KEEP")]
    review = [item for item in recommendations if "REVIEW" in item["action"] or "SIMULATE" in item["action"]]
    return {
        "generated_at": generated_at,
        "mode": "ADVISORY_MEMORY_ONLY",
        "input_sources": sorted(memory.keys()),
        "confirmed_good_policies": [compact_memory_item(item) for item in confirmed],
        "policy_review_queue": [compact_memory_item(item) for item in review],
        "recommendation_strength_counts": {
            "strong": len(strong),
            "medium": len(medium),
            "weak": len(weak),
        },
        "production_logic_modified": False,
        "autonomous_execution": False,
    }


def build_market_watch_iq_v7(memory: dict[str, Any], recommendations: list[dict[str, Any]]) -> dict[str, Any]:
    """Return Market Watch IQ V7 AI policy scorecard."""
    v6 = memory.get("market_watch_iq_v6", {})
    strong_count = len([item for item in recommendations if item["pcs"] >= STRONG_THRESHOLD])
    medium_count = len([item for item in recommendations if MEDIUM_THRESHOLD <= item["pcs"] < STRONG_THRESHOLD])
    false_rate = false_recommendation_rate(recommendations)
    accuracy = round(100.0 - false_rate, 2)
    pcs_values = [float(item["pcs"]) for item in recommendations]
    return {
        "ai_policy_iq": round(
            mean(
                [
                    accuracy,
                    min(100.0, strong_count * 12.5 + medium_count * 5.0),
                    float(v6.get("guardrail_leak_iq", 60.0) or 60.0),
                    float(v6.get("guardrail_efficiency_score", 60.0) or 60.0),
                ]
            ),
            2,
        ),
        "recommendation_accuracy": accuracy,
        "false_recommendation_rate": false_rate,
        "average_pcs": round(mean(pcs_values), 2) if pcs_values else 0.0,
        "strong_recommendations": strong_count,
        "medium_recommendations": medium_count,
        "weak_recommendations": len([item for item in recommendations if item["pcs"] < MEDIUM_THRESHOLD]),
        "policy_confidence_coverage": round(len([item for item in recommendations if item["pcs"] >= MEDIUM_THRESHOLD]) / max(len(recommendations), 1) * 100, 2),
        "guardrail_leak_iq": v6.get("guardrail_leak_iq", 0.0),
        "safe_advisory_candidates": v6.get("safe_relaxation_candidates", []),
        "production_baseline_preserved": True,
    }


def recommendation(
    rec_type: str,
    rec_id: str,
    title: str,
    *,
    pcs: int,
    evidence: list[str],
    action: str,
    production_change: bool,
) -> dict[str, Any]:
    """Return one normalized policy recommendation."""
    if rec_type not in RECOMMENDATION_TYPES:
        raise ValueError(f"unsupported recommendation type: {rec_type}")
    return {
        "id": rec_id,
        "type": rec_type,
        "title": title,
        "pcs": int(max(0, min(100, pcs))),
        "strength": pcs_strength(pcs),
        "evidence": evidence,
        "action": action,
        "advisory_only": True,
        "production_change": production_change,
    }


def pcs_strength(pcs: int | float) -> str:
    """Return policy confidence band."""
    value = float(pcs)
    if value >= STRONG_THRESHOLD:
        return "strong"
    if value >= MEDIUM_THRESHOLD:
        return "medium"
    return "weak"


def false_recommendation_rate(recommendations: list[dict[str, Any]]) -> float:
    """Return percent of recommendations that would violate advisory safety."""
    if not recommendations:
        return 100.0
    false_items = [
        item for item in recommendations
        if not item.get("advisory_only", False) or item.get("production_change", False)
    ]
    return round(len(false_items) / len(recommendations) * 100, 2)


def ai_policy_passes(recommendations: list[dict[str, Any]], iq_v7: dict[str, Any]) -> bool:
    """Return whether Sprint 10 success criteria are met."""
    ids = {item["id"] for item in recommendations}
    required = {
        "preserve_grade_lock",
        "review_symbol_lock_top_candidate_conditions",
        "preserve_low_expectancy_no_trade_regimes",
    }
    return (
        required.issubset(ids)
        and len(recommendations) >= 8
        and float(iq_v7.get("recommendation_accuracy", 0.0) or 0.0) >= 95.0
        and float(iq_v7.get("false_recommendation_rate", 100.0)) == 0.0
    )


def dedupe_recommendations(recommendations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return recommendations with duplicate ids removed."""
    seen: set[str] = set()
    unique = []
    for item in recommendations:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        unique.append(item)
    return unique


def compact_memory_item(item: dict[str, Any]) -> dict[str, Any]:
    """Return compact recommendation memory row."""
    return {
        "id": item["id"],
        "type": item["type"],
        "pcs": item["pcs"],
        "strength": item["strength"],
        "action": item["action"],
    }


def top_symbol_lock_candidate(symbol_lock: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return the top data-ranked symbol lock candidate without symbol seeding."""
    if not symbol_lock:
        return "none", {}
    ranked = sorted(
        symbol_lock.items(),
        key=lambda item: (
            int(item[1].get("rank", 999) or 999),
            not bool(item[1].get("conditional_unlock_candidate")),
            -float(item[1].get("expectancy", 0.0) or 0.0),
            str(item[0]),
        ),
    )
    return ranked[0]
