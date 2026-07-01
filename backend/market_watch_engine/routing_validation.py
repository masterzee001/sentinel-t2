"""Routing validation and failure attribution for Market Watch."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


STRATEGIES = ("ict_liquidity", "trend_following", "mean_reversion")
ROUTING_CLASSES = (
    "CORRECTLY_ROUTED",
    "MISROUTED_TO_ICT",
    "MISROUTED_TO_TREND",
    "MISROUTED_TO_MEAN_REVERSION",
    "SHOULD_HAVE_BEEN_NO_TRADE",
)
STAGE_BASELINE = {"pf": 1.58, "win_rate": 58.7, "trades": 56, "max_drawdown": 2.97}
STAGE_1 = {"pf": 1.9, "win_rate": 62.0, "trades": 90, "max_drawdown": 3.0}
STAGE_2 = {"pf": 2.2, "win_rate": 68.0, "trades": 120, "max_drawdown": 3.5}
ELITE = {"pf": 2.8, "win_rate": 72.0, "trades": 150, "max_drawdown": 4.0}


def strategy_checklists(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return strategy checklist validation for a forensic trade record."""
    confidence_ok = int(record.get("confidence", 0) or 0) >= 90
    session_ok = int(record.get("session_quality", 0) or 0) >= 50
    noise_ok = int(record.get("noise_score", 0) or 0) < 60
    ict_items = {
        "liquidity_sweep_detected": bool(record.get("sweep_detected", False)),
        "confirmed_mss": bool(record.get("mss_confirmed", False)),
        "strong_displacement": int(record.get("displacement_score", 0) or 0) >= 65,
        "valid_fvg_or_ob": bool(record.get("fvg_detected", False) or record.get("ob_detected", False)),
        "premium_discount_aligned": int(record.get("premium_discount_alignment", 0) or 0) >= 60,
        "target_liquidity_clear": bool(record.get("likely_draw_on_liquidity", "")),
        "noise_below_threshold": noise_ok,
        "session_acceptable": session_ok,
        "confidence_above_threshold": confidence_ok,
    }
    trend_items = {
        "trend_strength_high": int(record.get("trend_strength", 0) or 0) >= 75,
        "volatility_expansion_present": int(record.get("volatility_expansion", 0) or 0) >= 50,
        "pullback_quality_acceptable": int(record.get("pullback_quality", 0) or 0) >= 60,
        "continuation_structure_intact": int(record.get("continuation_structure", 0) or 0) >= 60,
        "htf_bias_aligned": bool(record.get("htf_bias_aligned", False)),
        "not_exhausted_against_trend": int(record.get("exhaustion_score", 0) or 0) <= 75,
        "session_acceptable": session_ok,
        "continuation_target_clear": bool(record.get("continuation_target_clear", False)),
        "confidence_above_threshold": confidence_ok,
    }
    mean_confirmations = {
        "overextension_high": int(record.get("overextension_score", 0) or 0) >= 70,
        "exhaustion_high": int(record.get("exhaustion_score", 0) or 0) >= 65,
        "failed_breakout_or_liquidity_raid": bool(record.get("failed_breakout", False) or record.get("sweep_detected", False)),
        "far_from_fair_value": int(record.get("fair_value_distance", 0) or 0) >= 65,
        "equilibrium_target_clear": bool(record.get("equilibrium_target_clear", False)),
        "session_acceptable": session_ok,
        "range_visible": int(record.get("range_score", 0) or 0) >= 45,
        "confidence_above_threshold": confidence_ok,
    }
    mean_count = sum(1 for value in mean_confirmations.values() if value)
    return {
        "ict_liquidity": checklist_result(ict_items, required=len(ict_items)),
        "trend_following": checklist_result(trend_items, required=len(trend_items)),
        "mean_reversion": checklist_result(mean_confirmations, required=3, confirmations=mean_count),
    }


def checklist_result(items: dict[str, bool], *, required: int, confirmations: int | None = None) -> dict[str, Any]:
    """Return normalized checklist result."""
    passed = sum(1 for value in items.values() if value)
    return {
        "passed": passed,
        "required": required,
        "complete": passed >= required,
        "items": items,
        "missing": [name for name, value in items.items() if not value],
        "confirmations": confirmations if confirmations is not None else passed,
    }


def recommended_strategy(record: dict[str, Any], checklists: dict[str, dict[str, Any]] | None = None) -> str:
    """Return the strategy Market Watch should have selected for the record."""
    if str(record.get("dominant_pattern", "")) == "noisy_chop" or int(record.get("noise_score", 0) or 0) >= 70:
        return "no_trade"
    checklists = checklists or strategy_checklists(record)
    candidates = {
        strategy: float(record.get("strategy_scores", {}).get(strategy, 0.0) or 0.0)
        for strategy in STRATEGIES
        if checklists.get(strategy, {}).get("complete")
    }
    if not candidates:
        return "no_trade"
    dominant = str(record.get("dominant_pattern", ""))
    if dominant == "liquidity_sweep_reversal" and "ict_liquidity" in candidates:
        return "ict_liquidity"
    if dominant == "trend_continuation" and "trend_following" in candidates:
        return "trend_following"
    if dominant in {"range_mean_reversion", "exhaustion_reversal", "liquidity_sweep_reversal"} and "mean_reversion" in candidates:
        return "mean_reversion"
    return max(candidates.items(), key=lambda item: item[1])[0]


def classify_routing(record: dict[str, Any]) -> dict[str, Any]:
    """Classify whether a forensic trade was routed correctly."""
    checklists = strategy_checklists(record)
    selected = str(record.get("selected_strategy", "no_trade"))
    recommended = recommended_strategy(record, checklists)
    noisy = str(record.get("dominant_pattern", "")) == "noisy_chop" or int(record.get("noise_score", 0) or 0) >= 70
    if selected in STRATEGIES and not checklists.get(selected, {}).get("complete") and not noisy:
        routing_class = {
            "ict_liquidity": "MISROUTED_TO_ICT",
            "trend_following": "MISROUTED_TO_TREND",
            "mean_reversion": "MISROUTED_TO_MEAN_REVERSION",
        }[selected]
    elif recommended == "no_trade":
        routing_class = "SHOULD_HAVE_BEEN_NO_TRADE"
    elif selected == recommended:
        routing_class = "CORRECTLY_ROUTED"
    elif selected == "ict_liquidity":
        routing_class = "MISROUTED_TO_ICT"
    elif selected == "trend_following":
        routing_class = "MISROUTED_TO_TREND"
    elif selected == "mean_reversion":
        routing_class = "MISROUTED_TO_MEAN_REVERSION"
    else:
        routing_class = "SHOULD_HAVE_BEEN_NO_TRADE"
    return {
        "routing_class": routing_class,
        "selected_strategy": selected,
        "recommended_strategy": recommended,
        "strategy_checklists": checklists,
        "reason": routing_reason(record, selected, recommended, checklists),
    }


def routing_reason(
    record: dict[str, Any],
    selected: str,
    recommended: str,
    checklists: dict[str, dict[str, Any]],
) -> str:
    """Return concise routing attribution."""
    if recommended == "no_trade":
        return "No valid strategy checklist or noisy market condition"
    if selected == recommended:
        return "Selected strategy matched completed checklist and expectancy"
    missing = checklists.get(selected, {}).get("missing", [])
    if missing:
        return f"Selected strategy checklist incomplete: {', '.join(missing[:3])}"
    return f"{recommended} had stronger checklist and expectancy alignment"


def annotate_routing(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach checklist and routing metadata to forensic records."""
    annotated = []
    for record in records:
        classification = classify_routing(record)
        annotated.append({**record, **classification})
    return annotated


def routing_summary(records: list[dict[str, Any]]) -> dict[str, int]:
    """Return counts for routing classes."""
    counts = Counter(str(record.get("routing_class", "")) for record in records)
    return {name: int(counts.get(name, 0)) for name in ROUTING_CLASSES}


def loss_attribution(record: dict[str, Any]) -> list[str]:
    """Return failure buckets for a losing forensic record."""
    buckets: list[str] = []
    if int(record.get("confidence", 0) or 0) < 90:
        buckets.append("low confidence override")
    if int(record.get("noise_score", 0) or 0) >= 60:
        buckets.append("noisy market")
    if str(record.get("routing_class", "")) != "CORRECTLY_ROUTED":
        buckets.append("wrong strategy routing")
    if int(record.get("session_quality", 0) or 0) < 50:
        buckets.append("poor session")
    if not bool(record.get("mss_confirmed", False)):
        buckets.append("missing MSS")
    if int(record.get("displacement_score", 0) or 0) < 60:
        buckets.append("weak displacement")
    if bool(record.get("counter_trend_selection", False)):
        buckets.append("counter-trend selection")
    if not bool(record.get("likely_draw_on_liquidity", "")):
        buckets.append("no clear target")
    if float(record.get("rr", 0.0) or 0.0) < 1.5:
        buckets.append("poor RR")
    if bool(record.get("spread_news_invalidation", False)):
        buckets.append("spread/news invalidation")
    if not buckets:
        buckets.append("weak confirmation")
    return buckets


def loss_clusters(records: list[dict[str, Any]], group_key: str) -> dict[str, dict[str, int]]:
    """Return loss attribution counts grouped by strategy, pattern, or session."""
    grouped: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        if str(record.get("outcome", "")) != "LOSS":
            continue
        key = str(record.get(group_key, "unknown"))
        for bucket in loss_attribution(record):
            grouped[key][bucket] += 1
    return {key: dict(counter) for key, counter in grouped.items()}


def condition_signature(record: dict[str, Any]) -> str:
    """Return stable lossy-condition signature for repeated routing memory."""
    noise = "noise>60" if int(record.get("noise_score", 0) or 0) > 60 else "noise<=60"
    mss = "mss" if bool(record.get("mss_confirmed", False)) else "weak_mss"
    trend = "trend>80" if int(record.get("trend_strength", 0) or 0) > 80 else "trend<=80"
    sweep = "sweep" if bool(record.get("sweep_detected", False)) else "no_sweep"
    pattern = str(record.get("dominant_pattern", "unknown"))
    return "|".join([pattern, noise, mss, trend, sweep])


def repeated_bad_routing(records: list[dict[str, Any]], *, minimum_repeats: int = 2) -> dict[str, Any]:
    """Detect repeated losing-condition routing by selected strategy."""
    signatures: dict[str, Counter[str]] = {strategy: Counter() for strategy in STRATEGIES}
    for record in records:
        selected = str(record.get("selected_strategy", ""))
        if selected not in signatures:
            continue
        if str(record.get("outcome", "")) != "LOSS":
            continue
        if str(record.get("routing_class", "")) == "CORRECTLY_ROUTED":
            continue
        signatures[selected][condition_signature(record)] += 1
    repeated: dict[str, dict[str, int]] = {}
    counts: dict[str, int] = {}
    for strategy, counter in signatures.items():
        repeated[strategy] = {signature: count for signature, count in counter.items() if count >= minimum_repeats}
        counts[strategy] = int(sum(repeated[strategy].values()))
    return {
        "counts": counts,
        "conditions": repeated,
        "total": sum(counts.values()),
    }


def counterfactual_reroutes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return counterfactual rerouting results for losing records."""
    reroutes = []
    for record in records:
        if str(record.get("outcome", "")) != "LOSS":
            continue
        recommended = str(record.get("recommended_strategy", "no_trade"))
        original_rr = float(record.get("rr", -1.0) or -1.0)
        rerouted_rr = 0.0 if recommended == "no_trade" else round(max(0.25, abs(original_rr) * 0.5), 2)
        reroutes.append(
            {
                "trade_id": record.get("trade_id"),
                "original_strategy": record.get("selected_strategy"),
                "recommended_strategy": recommended,
                "original_outcome": record.get("outcome"),
                "original_rr": original_rr,
                "rerouted_outcome": "AVOIDED_LOSS" if recommended == "no_trade" else "REDUCED_LOSS_OR_RECOVERY",
                "rerouted_rr": rerouted_rr,
                "rr_delta": round(rerouted_rr - original_rr, 2),
            }
        )
    return reroutes


def market_watch_iq(records: list[dict[str, Any]], repeated: dict[str, Any], avoidable_opportunities: int | None = None) -> dict[str, float]:
    """Return routing intelligence metrics."""
    total = max(len(records), 1)
    summary = routing_summary(records)
    correct = summary["CORRECTLY_ROUTED"]
    misrouted = total - correct
    avoidable_losses = avoidable_opportunities or len(
        [record for record in records if str(record.get("outcome", "")) == "LOSS" and str(record.get("routing_class", "")) != "CORRECTLY_ROUTED"]
    )
    repeated_total = int(repeated.get("total", 0) or 0)
    aligned = len(
        [
            record
            for record in records
            if str(record.get("recommended_strategy", "")) == str(record.get("selected_strategy", ""))
            or str(record.get("recommended_strategy", "")) == "no_trade"
        ]
    )
    return {
        "routing_accuracy": round(correct / total * 100, 2),
        "misrouting": round(misrouted / total * 100, 2),
        "repeated_mistakes": round(repeated_total / max(avoidable_losses, 1) * 100, 2),
        "learning_success": round((1 - repeated_total / max(avoidable_losses, 1)) * 100, 2),
        "strategy_expectancy_alignment": round(aligned / total * 100, 2),
    }


def srms(repeated_total: int, avoidable_opportunities: int) -> dict[str, Any]:
    """Return Strategy Routing Memory Score."""
    if avoidable_opportunities <= 0:
        value = 1.0
    else:
        value = max(0.0, min(1.0, 1 - repeated_total / avoidable_opportunities))
    score = round(value * 100, 2)
    return {"value": score, "classification": srms_classification(score)}


def srms_classification(score: float) -> str:
    """Return SRMS class."""
    if score < 50:
        return "POOR"
    if score < 70:
        return "WEAK"
    if score < 85:
        return "GOOD"
    if score < 95:
        return "STRONG"
    return "ELITE"


def classify_performance_ladder(metrics: dict[str, Any], *, baseline_preserved: bool) -> str:
    """Classify Market Watch performance against the Sentinel ladder."""
    pf = float(metrics.get("pf", metrics.get("profit_factor", 0.0)) or 0.0)
    wr = float(metrics.get("win_rate", metrics.get("wr", 0.0)) or 0.0)
    trades = int(metrics.get("trades", metrics.get("trades_approved", 0)) or 0)
    dd = float(metrics.get("max_drawdown", metrics.get("dd", 0.0)) or 0.0)
    if pf >= ELITE["pf"] and wr >= ELITE["win_rate"] and trades >= ELITE["trades"] and dd < ELITE["max_drawdown"]:
        return "ELITE QUALIFIED"
    if pf >= STAGE_2["pf"] and wr >= STAGE_2["win_rate"] and trades >= STAGE_2["trades"] and dd <= STAGE_2["max_drawdown"]:
        return "STAGE 2 QUALIFIED"
    if pf >= STAGE_1["pf"] and wr >= STAGE_1["win_rate"] and trades >= STAGE_1["trades"] and dd <= STAGE_1["max_drawdown"]:
        return "STAGE 1 QUALIFIED"
    if baseline_preserved:
        return "BASELINE PRESERVED"
    if pf < STAGE_BASELINE["pf"] or wr < STAGE_BASELINE["win_rate"] or trades < STAGE_BASELINE["trades"] or dd > STAGE_BASELINE["max_drawdown"]:
        return "BELOW BASELINE"
    return "BASELINE PRESERVED"
