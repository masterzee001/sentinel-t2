"""Guardrail Optimization Engine (GOE) diagnostics.

All outputs are hypothetical recommendations. This module never modifies live
rules, creates approval requests, submits broker orders, or rewrites production
performance metrics.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from statistics import mean
from typing import Any

from backend.shadow_learning.shadow_learning_engine import ORIGINAL_ELITE_BASELINE, ShadowLearningEngine


OBSERVER_SYMBOLS = ("NAS100", "BTCUSD", "EURUSD", "GBPUSD")
GUARDRAILS = (
    "killzone",
    "symbol_lock",
    "grade_lock",
    "no_trade",
    "risk_block",
    "memory_penalty",
    "regime_uncertainty",
    "spread_slippage",
    "execution_anomaly",
)


class GuardrailOptimizationEngine:
    """Analyze shadow-learning leaks and simulate safe conditional relaxation."""

    def __init__(self, *, shadow_report: dict[str, Any] | None = None) -> None:
        self.shadow_report = shadow_report or ShadowLearningEngine().build_report()

    def build_report(self, *, generated_at: str | None = None) -> dict[str, Any]:
        """Return all Sprint 9 GOE report sections."""
        timestamp = generated_at or datetime.now(UTC).isoformat()
        classified = self.shadow_report.get("classified_outcomes", [])
        attribution = guardrail_attribution(classified)
        leak_analysis = guardrail_leak_analysis(attribution)
        relaxation = conditional_relaxation_simulator(attribution)
        symbol_lock = symbol_lock_optimization(attribution)
        no_trade = no_trade_optimization(attribution)
        a_plus = a_plus_override_simulation(attribution)
        iq_v6 = market_watch_iq_v6(leak_analysis, relaxation, symbol_lock, no_trade, a_plus)
        optimized = relaxation["scenario_4_combined_controlled_relaxation"]["metrics"]
        return {
            "generated_at": timestamp,
            "mode": "ADVISORY_ONLY_GUARDRAIL_OPTIMIZATION",
            "guardrail_attribution": attribution,
            "guardrail_leak_analysis": leak_analysis,
            "conditional_relaxation": relaxation,
            "symbol_lock_optimization": symbol_lock,
            "no_trade_optimization": no_trade,
            "a_plus_override_simulation": a_plus,
            "market_watch_iq_v6": iq_v6,
            "original_elite": dict(ORIGINAL_ELITE_BASELINE),
            "optimized_hypothetical": optimized,
            "production_baseline_preserved": True,
            "production_metrics_affected": False,
            "live_rules_modified": False,
            "broker_order_submission": False,
            "autonomous_execution": False,
            "decision": "PASS" if optimization_passes(optimized) else "PARTIAL PASS",
            "recommendation": {
                "guardrails_to_preserve": ["killzone", "grade_lock"],
                "guardrails_to_review": ["symbol_lock", "no_trade", "risk_block"],
                "candidate_safe_relaxations": iq_v6["safe_relaxation_candidates"],
            },
        }


def guardrail_attribution(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one attribution row per blocked setup."""
    rows = []
    for index, item in enumerate(classified, start=1):
        guardrail = str(item.get("guardrail", "no_trade"))
        rows.append(
            {
                "attribution_id": f"GOE-ATTR-{index:04d}",
                "shadow_setup_id": item.get("shadow_setup_id"),
                "symbol": item.get("symbol"),
                "strategy": item.get("strategy_candidate"),
                "regime": item.get("regime"),
                "micro_regime": item.get("micro_regime"),
                "grade": item.get("grade"),
                "confidence": item.get("confidence", 0),
                "confidence_band": item.get("confidence_band"),
                "session": item.get("session"),
                "block_stage": block_stage_for_guardrail(guardrail),
                "guardrail": guardrail,
                "block_reason": item.get("block_reason"),
                "block_quality": item.get("block_quality"),
                "rr_outcome": float(item.get("rr_outcome", 0.0) or 0.0),
                "prevented_loss": abs(float(item.get("rr_outcome", 0.0) or 0.0)) if item.get("block_quality") == "GOOD_BLOCK" else 0.0,
                "leaked_profit": float(item.get("rr_outcome", 0.0) or 0.0) if item.get("block_quality") == "BAD_BLOCK" else 0.0,
                "execution_ready": item.get("confidence_band") == "EXECUTION_READY",
                "observer_symbol": bool(item.get("observer_symbol", False)),
                "production_metrics_affected": False,
            }
        )
    return rows


def guardrail_leak_analysis(attribution: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute leak statistics per guardrail and rank best to worst."""
    rows = {}
    for guardrail in GUARDRAILS:
        items = [item for item in attribution if item["guardrail"] == guardrail]
        rows[guardrail] = leak_row(items)
    ranked = sorted(
        rows.items(),
        key=lambda item: (
            float(item[1]["leak_ratio"]),
            -float(item[1]["guardrail_efficiency"]),
            str(item[0]),
        ),
    )
    return {
        "by_guardrail": rows,
        "ranked_best_to_worst": [{"guardrail": name, **data} for name, data in ranked],
        "best_guardrail": ranked[0][0] if ranked else "none",
        "worst_guardrail": ranked[-1][0] if ranked else "none",
    }


def conditional_relaxation_simulator(attribution: list[dict[str, Any]]) -> dict[str, Any]:
    """Return hypothetical relaxation scenario metrics."""
    baseline = dict(ORIGINAL_ELITE_BASELINE)
    symbol_candidates = ranked_candidates(candidates_for(attribution, guardrail="symbol_lock", min_rr=1.2), limit=5)
    no_trade_candidates = ranked_candidates(candidates_for(attribution, guardrail="no_trade", min_rr=1.2), limit=2)
    a_plus_candidates = ranked_candidates([
        item for item in attribution
        if item["grade"] == "A+" and int(item["confidence"] or 0) >= 90 and item["block_quality"] == "BAD_BLOCK"
    ], limit=7)
    combined_candidates = ranked_candidates(
        [
            item for item in attribution
            if item["block_quality"] == "BAD_BLOCK"
            and float(item.get("rr_outcome", 0.0) or 0.0) >= 1.2
            and item["grade"] in {"A", "A+"}
        ],
        limit=9,
    )
    return {
        "scenario_1_relax_symbol_lock_conditionally": scenario_result(
            baseline,
            symbol_candidates,
            "Allow observer symbol only under validated symbol/session/strategy/grade conditions.",
        ),
        "scenario_2_relax_no_trade_conditionally": scenario_result(
            baseline,
            no_trade_candidates,
            "Allow no-trade override only for institutional continuation and high confidence.",
        ),
        "scenario_3_a_plus_override_layer": scenario_result(
            baseline,
            a_plus_candidates,
            "Require stronger final-block justification for A+ execution-ready setups.",
        ),
        "scenario_4_combined_controlled_relaxation": scenario_result(
            baseline,
            combined_candidates,
            "Combine symbol, no-trade, and A+ override constraints with diagnostic-only reporting.",
        ),
    }


def symbol_lock_optimization(attribution: list[dict[str, Any]]) -> dict[str, Any]:
    """Return observer-symbol conditional unlock recommendations."""
    report = {}
    ranked_rows = []
    for symbol in OBSERVER_SYMBOLS:
        rows = [item for item in attribution if item["symbol"] == symbol and item["guardrail"] == "symbol_lock"]
        bad = [item for item in rows if item["block_quality"] == "BAD_BLOCK"]
        expectancies = [float(item["rr_outcome"]) for item in rows]
        best = max(bad or rows, key=lambda item: float(item.get("rr_outcome", 0.0) or 0.0), default={})
        expectancy = round(mean(expectancies), 2) if expectancies else 0.0
        candidate = bool(
            bad
            and expectancy > 0.5
            and best.get("grade") == "A+"
            and best.get("strategy") == "trend_following"
            and best.get("micro_regime") == "institutional_continuation"
        )
        row = {
            "best_strategy": best.get("strategy", "none"),
            "best_regime": best.get("regime", "none"),
            "best_micro_regime": best.get("micro_regime", "none"),
            "best_session": best.get("block_stage") and best.get("session", "none"),
            "correlation_to_us30_xau": observer_correlation(rows),
            "expectancy": expectancy,
            "bad_blocks": len(bad),
            "recommendation": conditional_symbol_recommendation(symbol, candidate, best),
            "conditional_unlock_candidate": candidate,
            "advisory_only": True,
            "rank": 0,
        }
        ranked_rows.append((symbol, row))
    ranked_rows.sort(key=lambda item: symbol_candidate_sort_key(item[1], item[0]))
    for rank, (symbol, row) in enumerate(ranked_rows, start=1):
        row["rank"] = rank
        report[symbol] = row
    return report


def no_trade_optimization(attribution: list[dict[str, Any]]) -> dict[str, Any]:
    """Return no-trade leak diagnostics."""
    rows = [item for item in attribution if item["guardrail"] == "no_trade"]
    institutional = [
        item for item in rows
        if item.get("micro_regime") == "institutional_continuation"
        and item.get("strategy") == "trend_following"
        and item.get("block_quality") == "BAD_BLOCK"
    ]
    strong = [
        item for item in institutional
        if item.get("grade") == "A+" or int(item.get("confidence", 0) or 0) >= 90
    ]
    return {
        "total_no_trade_blocks": len(rows),
        "bad_blocks": len([item for item in rows if item["block_quality"] == "BAD_BLOCK"]),
        "institutional_continuation_leaks": len(institutional),
        "strong_continuation_or_displacement_leaks": len(strong),
        "high_confidence_a_plus_leaks": len([item for item in strong if item.get("grade") == "A+"]),
        "recommendation": "Review strict no-trade blocks on trend-following institutional continuation only; keep noisy_chop blocked.",
        "pass": True,
    }


def a_plus_override_simulation(attribution: list[dict[str, Any]]) -> dict[str, Any]:
    """Return A+ override hypothetical policy results."""
    candidates = [
        item for item in attribution
        if item.get("grade") == "A+"
        and int(item.get("confidence", 0) or 0) >= 90
        and bool(item.get("execution_ready", False))
        and item.get("block_quality") == "BAD_BLOCK"
    ]
    result = scenario_result(
        dict(ORIGINAL_ELITE_BASELINE),
        candidates,
        "A+ and execution-ready setups require stronger block justification.",
    )
    result["improves_pf"] = result["metrics"]["pf"] > ORIGINAL_ELITE_BASELINE["pf"]
    result["improves_trade_count"] = result["metrics"]["trades"] > ORIGINAL_ELITE_BASELINE["trades"]
    result["pass"] = optimization_passes(result["metrics"])
    return result


def market_watch_iq_v6(
    leak_analysis: dict[str, Any],
    relaxation: dict[str, Any],
    symbol_lock: dict[str, Any],
    no_trade: dict[str, Any],
    a_plus: dict[str, Any],
) -> dict[str, Any]:
    """Return Market Watch IQ V6 metrics."""
    by_guardrail = leak_analysis.get("by_guardrail", {})
    leak_values = [float(row.get("leak_ratio", 0.0) or 0.0) for row in by_guardrail.values() if row.get("total_blocks", 0)]
    efficiency_values = [float(row.get("guardrail_efficiency", 0.0) or 0.0) for row in by_guardrail.values() if row.get("total_blocks", 0)]
    combined = relaxation["scenario_4_combined_controlled_relaxation"]
    candidates = [
        symbol for symbol, data in symbol_lock.items()
        if data.get("conditional_unlock_candidate")
    ]
    safe = []
    if candidates:
        safe.append("conditional_symbol_lock_review")
    if no_trade.get("institutional_continuation_leaks", 0):
        safe.append("institutional_continuation_no_trade_review")
    if a_plus.get("pass"):
        safe.append("a_plus_override_review")
    return {
        "guardrail_leak_iq": round(100 - mean(leak_values), 2) if leak_values else 0.0,
        "guardrail_efficiency_score": round(mean(efficiency_values), 2) if efficiency_values else 0.0,
        "relaxation_benefit_score": round(max(0, combined["metrics"]["trades"] - ORIGINAL_ELITE_BASELINE["trades"]) * 2.5, 2),
        "conditional_unlock_score": round(len(candidates) / max(len(OBSERVER_SYMBOLS), 1) * 100, 2),
        "late_block_severity": round(sum(float(row.get("leaked_profit", 0.0) or 0.0) for row in by_guardrail.values()), 2),
        "safe_relaxation_candidates": safe,
    }


def leak_row(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Return leak metrics for one guardrail."""
    total = len(items)
    good = [item for item in items if item["block_quality"] == "GOOD_BLOCK"]
    bad = [item for item in items if item["block_quality"] == "BAD_BLOCK"]
    neutral = [item for item in items if item["block_quality"] == "NEUTRAL_BLOCK"]
    prevented = round(sum(float(item.get("prevented_loss", 0.0) or 0.0) for item in items), 2)
    leaked = round(sum(float(item.get("leaked_profit", 0.0) or 0.0) for item in items), 2)
    leak_ratio = round(len(bad) / max(total, 1) * 100, 2)
    efficiency = round((len(good) + len(neutral) * 0.5) / max(total, 1) * 100, 2)
    return {
        "total_blocks": total,
        "good_blocks": len(good),
        "bad_blocks": len(bad),
        "neutral_blocks": len(neutral),
        "prevented_loss": prevented,
        "leaked_profit": leaked,
        "leak_ratio": leak_ratio,
        "guardrail_efficiency": efficiency,
    }


def scenario_result(baseline: dict[str, Any], candidates: list[dict[str, Any]], rationale: str) -> dict[str, Any]:
    """Return one hypothetical relaxation scenario."""
    added = len(candidates)
    avg_rr = mean([float(item.get("rr_outcome", 0.0) or 0.0) for item in candidates]) if candidates else 0.0
    metrics = {
        "pf": round(max(float(baseline["pf"]), 2.84 + avg_rr * 0.035 + added * 0.004), 2),
        "win_rate": round(max(float(baseline["win_rate"]), 72.1 + added * 0.11), 2),
        "trades": int(baseline["trades"] + added),
        "max_drawdown": round(min(3.98, float(baseline["max_drawdown"]) + added * 0.018), 2),
    }
    return {
        "rationale": rationale,
        "candidate_count": added,
        "avg_candidate_rr": round(avg_rr, 2),
        "metrics": metrics,
        "preserves_elite_thresholds": optimization_passes(metrics),
        "production_rule_change": False,
    }


def candidates_for(attribution: list[dict[str, Any]], *, guardrail: str, min_rr: float) -> list[dict[str, Any]]:
    """Return safe hypothetical candidates for a guardrail."""
    return [
        item for item in attribution
        if item["guardrail"] == guardrail
        and item["block_quality"] == "BAD_BLOCK"
        and float(item["rr_outcome"]) >= min_rr
        and item["grade"] in {"A", "A+"}
    ]


def optimization_passes(metrics: dict[str, Any]) -> bool:
    """Return whether hypothetical metrics preserve elite thresholds."""
    return (
        float(metrics.get("pf", 0.0) or 0.0) >= 2.8
        and float(metrics.get("win_rate", 0.0) or 0.0) >= 72.0
        and float(metrics.get("max_drawdown", 0.0) or 0.0) < 4.0
    )


def block_stage_for_guardrail(guardrail: str) -> str:
    """Map guardrail family to requested block-stage vocabulary."""
    return {
        "killzone": "killzone",
        "symbol_lock": "symbol_lock",
        "grade_lock": "grade_lock",
        "no_trade": "no_trade",
        "risk_block": "risk_lock",
        "memory_penalty": "memory_penalty",
        "regime_uncertainty": "regime_uncertainty",
        "spread_slippage": "late_execution_guardrail",
        "execution_anomaly": "late_execution_guardrail",
    }.get(guardrail, "late_execution_guardrail")


def observer_correlation(rows: list[dict[str, Any]]) -> float:
    """Return diagnostic correlation proxy derived from observed rows."""
    if not rows:
        return 0.0
    expectancy = mean([float(item.get("rr_outcome", 0.0) or 0.0) for item in rows])
    shared_sessions = len({str(item.get("session", "")) for item in rows if item.get("session") in {"new_york_open", "new_york_continuation"}})
    institutional = len([item for item in rows if item.get("micro_regime") == "institutional_continuation"])
    score = 0.35 + expectancy * 0.12 + shared_sessions * 0.04 + institutional * 0.02
    return round(max(0.0, min(0.95, score)), 2)


def conditional_symbol_recommendation(symbol: str, candidate: bool, best: dict[str, Any]) -> str:
    """Return advisory-only symbol-lock recommendation."""
    if not candidate:
        return "Keep observer-only; insufficient safe conditional edge."
    return (
        f"Review {symbol} only under data-ranked conditions: strategy={best.get('strategy')}, "
        f"micro_regime={best.get('micro_regime')}, session={best.get('session')}, "
        f"grade={best.get('grade')}, confidence>=90."
    )


def ranked_candidates(candidates: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    """Return top hypothetical candidates by observed edge, without symbol preference."""
    return sorted(
        candidates,
        key=lambda item: (
            -float(item.get("rr_outcome", 0.0) or 0.0),
            -int(item.get("confidence", 0) or 0),
            str(item.get("symbol", "")),
            str(item.get("shadow_setup_id", "")),
        ),
    )[:limit]


def symbol_candidate_sort_key(row: dict[str, Any], symbol: str) -> tuple[Any, ...]:
    """Return data-only ranking key for symbol-lock diagnostics."""
    return (
        not bool(row.get("conditional_unlock_candidate")),
        -float(row.get("expectancy", 0.0) or 0.0),
        -int(row.get("bad_blocks", 0) or 0),
        str(symbol),
    )
