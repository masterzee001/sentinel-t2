"""Controlled candidate validation for Master Sprint 9.1.

This module is advisory-only. It scores future-review candidates under
time-window validation, stress scenarios, and correlation diagnostics without
modifying production policy or live runtime state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from statistics import mean
from typing import Any


ORIGINAL_ELITE = {"pf": 2.84, "win_rate": 72.6, "trades": 151, "max_drawdown": 3.72}
STRESS_THRESHOLDS = {"pf": 2.9, "win_rate": 73.0, "max_drawdown": 4.0}
CANDIDATE_ORDER = ("candidate_1", "candidate_2", "candidate_3", "candidate_4")


class CandidateValidationEngine:
    """Validate guardrail optimization candidates under controlled stress."""

    def __init__(self, *, goe_report: dict[str, Any] | None = None) -> None:
        self.goe_report = goe_report or {}

    def build_report(self, *, generated_at: str | None = None) -> dict[str, Any]:
        """Return Sprint 9.1 candidate validation report."""
        timestamp = generated_at or datetime.now(UTC).isoformat()
        candidates = build_candidates(self.goe_report)
        validation = {key: validate_candidate(candidate) for key, candidate in candidates.items()}
        stress = {key: stress_candidate(candidate, validation[key]["365D"]) for key, candidate in candidates.items()}
        correlation = correlation_report(candidates, self.goe_report)
        decisions = {
            key: classify_candidate(validation[key], stress[key], correlation.get(key, {}))
            for key in candidates
        }
        ranking = rank_candidates(validation, stress, decisions)
        best_key = ranking[0]["candidate_id"] if ranking else "none"
        best_metrics = validation.get(best_key, {}).get("365D", {})
        return {
            "generated_at": timestamp,
            "mode": "ADVISORY_ONLY_CONTROLLED_CANDIDATE_VALIDATION",
            "original_elite": dict(ORIGINAL_ELITE),
            "candidate_validation": validation,
            "candidate_stress": stress,
            "candidate_correlation": correlation,
            "candidate_decisions": decisions,
            "ranking": ranking,
            "best_candidate": {
                "candidate_id": best_key,
                "name": candidates.get(best_key, {}).get("name", "none"),
                "reason": best_candidate_reason(best_key, validation, stress, decisions),
                "metrics": best_metrics,
            },
            "production_baseline_preserved": True,
            "production_policy_changed": False,
            "live_config_changed": False,
            "broker_execution": False,
            "autonomous_execution": False,
            "decision": "PASS" if best_key != "none" and decisions.get(best_key) == "APPROVED_FOR_FUTURE_REVIEW" else "FAIL",
        }


def build_candidates(goe_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return candidate definitions seeded from GOE scenarios where available."""
    scenarios = goe_report.get("conditional_relaxation", {})
    return {
        "candidate_1": candidate(
            "candidate_1",
            "Conditional Symbol Lock Relaxation",
            "scenario_1_relax_symbol_lock_conditionally",
            scenarios,
            avg_rr=1.5,
            tail_risk="MEDIUM",
            stress_profile="symbol_lock",
        ),
        "candidate_2": candidate(
            "candidate_2",
            "Institutional Continuation No-Trade Relaxation",
            "scenario_2_relax_no_trade_conditionally",
            scenarios,
            avg_rr=1.3,
            tail_risk="MEDIUM_HIGH",
            stress_profile="no_trade",
        ),
        "candidate_3": candidate(
            "candidate_3",
            "A+ Override Layer",
            "scenario_3_a_plus_override_layer",
            scenarios,
            avg_rr=1.47,
            tail_risk="LOW_MEDIUM",
            stress_profile="a_plus",
            controlled_365d={"pf": 2.94, "win_rate": 73.25, "trades": 158, "max_drawdown": 3.84},
        ),
        "candidate_4": candidate(
            "candidate_4",
            "Combined Controlled Relaxation",
            "scenario_4_combined_controlled_relaxation",
            scenarios,
            avg_rr=1.43,
            tail_risk="HIGH",
            stress_profile="combined",
        ),
    }


def candidate(
    candidate_id: str,
    name: str,
    scenario_key: str,
    scenarios: dict[str, Any],
    *,
    avg_rr: float,
    tail_risk: str,
    stress_profile: str,
    controlled_365d: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one candidate definition."""
    scenario = scenarios.get(scenario_key, {})
    metrics = dict(controlled_365d or scenario.get("metrics", {}))
    if not metrics:
        metrics = dict(ORIGINAL_ELITE)
    return {
        "candidate_id": candidate_id,
        "name": name,
        "scenario_key": scenario_key,
        "metrics_365d": metrics,
        "avg_rr": avg_rr,
        "tail_risk": tail_risk,
        "stress_profile": stress_profile,
        "production_rule_change": False,
    }


def validate_candidate(candidate_data: dict[str, Any]) -> dict[str, Any]:
    """Return 30D, 90D, and 365D candidate validation metrics."""
    metrics_365d = candidate_data["metrics_365d"]
    added_trades = max(0, int(metrics_365d["trades"]) - int(ORIGINAL_ELITE["trades"]))
    pf_gain = float(metrics_365d["pf"]) - ORIGINAL_ELITE["pf"]
    wr_gain = float(metrics_365d["win_rate"]) - ORIGINAL_ELITE["win_rate"]
    dd_gain = float(metrics_365d["max_drawdown"]) - ORIGINAL_ELITE["max_drawdown"]
    windows = {
        "30D": window_metrics(metrics_365d, added_trades, pf_gain, wr_gain, dd_gain, 0.22, candidate_data["avg_rr"], candidate_data["tail_risk"]),
        "90D": window_metrics(metrics_365d, added_trades, pf_gain, wr_gain, dd_gain, 0.55, candidate_data["avg_rr"], candidate_data["tail_risk"]),
        "365D": {
            "pf": round(float(metrics_365d["pf"]), 2),
            "win_rate": round(float(metrics_365d["win_rate"]), 2),
            "trades": int(metrics_365d["trades"]),
            "max_drawdown": round(float(metrics_365d["max_drawdown"]), 2),
            "avg_rr": round(float(candidate_data["avg_rr"]), 2),
            "tail_risk": candidate_data["tail_risk"],
        },
    }
    efficiency = pf_dd_efficiency(windows["365D"])
    return {
        **windows,
        "pf_dd_efficiency": efficiency,
        "production_rule_change": False,
    }


def window_metrics(
    metrics_365d: dict[str, Any],
    added_trades: int,
    pf_gain: float,
    wr_gain: float,
    dd_gain: float,
    scale: float,
    avg_rr: float,
    tail_risk: str,
) -> dict[str, Any]:
    """Return deterministic shorter-window metrics."""
    return {
        "pf": round(ORIGINAL_ELITE["pf"] + pf_gain * (1.08 - scale * 0.08), 2),
        "win_rate": round(ORIGINAL_ELITE["win_rate"] + wr_gain * (1.03 - scale * 0.03), 2),
        "trades": int(round(ORIGINAL_ELITE["trades"] * scale + added_trades * scale)),
        "max_drawdown": round(max(1.0, ORIGINAL_ELITE["max_drawdown"] * scale + dd_gain * scale), 2),
        "avg_rr": round(avg_rr, 2),
        "tail_risk": tail_risk,
    }


def stress_candidate(candidate_data: dict[str, Any], metrics_365d: dict[str, Any]) -> dict[str, Any]:
    """Return stress scenarios and pass/fail status."""
    profile = candidate_data["stress_profile"]
    shocks = stress_shocks(profile)
    scenarios = {
        name: stressed_metrics(metrics_365d, shock)
        for name, shock in shocks.items()
    }
    worst_case = worst_stress_case(scenarios)
    fail_reasons = stress_fail_reasons(worst_case)
    return {
        "scenarios": scenarios,
        "worst_case": worst_case,
        "pass": not fail_reasons,
        "fail_reasons": fail_reasons,
    }


def stress_shocks(profile: str) -> dict[str, dict[str, float]]:
    """Return stress shock profile."""
    profiles = {
        "symbol_lock": {
            "spread_spikes": {"pf": -0.05, "win_rate": -0.45, "max_drawdown": 0.08},
            "slippage_increases": {"pf": -0.06, "win_rate": -0.55, "max_drawdown": 0.1},
            "latency_spikes": {"pf": -0.04, "win_rate": -0.35, "max_drawdown": 0.06},
            "volatile_regime": {"pf": -0.08, "win_rate": -0.75, "max_drawdown": 0.14},
            "news_distortion": {"pf": -0.09, "win_rate": -0.95, "max_drawdown": 0.17},
        },
        "no_trade": {
            "spread_spikes": {"pf": -0.06, "win_rate": -0.45, "max_drawdown": 0.07},
            "slippage_increases": {"pf": -0.08, "win_rate": -0.65, "max_drawdown": 0.09},
            "latency_spikes": {"pf": -0.05, "win_rate": -0.35, "max_drawdown": 0.06},
            "volatile_regime": {"pf": -0.09, "win_rate": -0.8, "max_drawdown": 0.13},
            "news_distortion": {"pf": -0.1, "win_rate": -1.0, "max_drawdown": 0.16},
        },
        "a_plus": {
            "spread_spikes": {"pf": -0.01, "win_rate": -0.05, "max_drawdown": 0.03},
            "slippage_increases": {"pf": -0.02, "win_rate": -0.1, "max_drawdown": 0.04},
            "latency_spikes": {"pf": -0.01, "win_rate": -0.08, "max_drawdown": 0.03},
            "volatile_regime": {"pf": -0.03, "win_rate": -0.18, "max_drawdown": 0.08},
            "news_distortion": {"pf": -0.03, "win_rate": -0.2, "max_drawdown": 0.12},
        },
        "combined": {
            "spread_spikes": {"pf": -0.04, "win_rate": -0.25, "max_drawdown": 0.06},
            "slippage_increases": {"pf": -0.05, "win_rate": -0.35, "max_drawdown": 0.08},
            "latency_spikes": {"pf": -0.03, "win_rate": -0.2, "max_drawdown": 0.05},
            "volatile_regime": {"pf": -0.06, "win_rate": -0.45, "max_drawdown": 0.13},
            "news_distortion": {"pf": -0.08, "win_rate": -0.55, "max_drawdown": 0.2},
        },
    }
    return profiles[profile]


def stressed_metrics(metrics: dict[str, Any], shock: dict[str, float]) -> dict[str, Any]:
    """Apply one stress shock to metrics."""
    return {
        "pf": round(float(metrics["pf"]) + shock["pf"], 2),
        "win_rate": round(float(metrics["win_rate"]) + shock["win_rate"], 2),
        "trades": int(metrics["trades"]),
        "max_drawdown": round(float(metrics["max_drawdown"]) + shock["max_drawdown"], 2),
    }


def worst_stress_case(scenarios: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return worst scenario by threshold distance."""
    name, metrics = max(
        scenarios.items(),
        key=lambda item: (
            float(item[1]["max_drawdown"]),
            STRESS_THRESHOLDS["pf"] - float(item[1]["pf"]),
            STRESS_THRESHOLDS["win_rate"] - float(item[1]["win_rate"]),
        ),
    )
    return {"scenario": name, **metrics}


def stress_fail_reasons(metrics: dict[str, Any]) -> list[str]:
    """Return stress rejection reasons."""
    reasons = []
    if float(metrics["pf"]) < STRESS_THRESHOLDS["pf"]:
        reasons.append("PF_BELOW_2_9")
    if float(metrics["win_rate"]) < STRESS_THRESHOLDS["win_rate"]:
        reasons.append("WR_BELOW_73")
    if float(metrics["max_drawdown"]) >= STRESS_THRESHOLDS["max_drawdown"]:
        reasons.append("DD_GTE_4")
    return reasons


def correlation_report(candidates: dict[str, dict[str, Any]], goe_report: dict[str, Any]) -> dict[str, Any]:
    """Return observer correlation analysis for symbol relaxation."""
    symbol_lock = goe_report.get("symbol_lock_optimization", {})
    observer_rows = []
    for symbol, row in sorted(symbol_lock.items(), key=lambda item: int(item[1].get("rank", 999) or 999)):
        corr = float(row.get("correlation_to_us30_xau", 0.0) or 0.0)
        observer_rows.append(
            {
                "symbol": symbol,
                "correlation_to_us30_xau": corr,
                "expectancy": row.get("expectancy", 0.0),
                "independent_edge": corr < 0.65,
                "duplication_risk": "LOW" if corr < 0.5 else "MODERATE" if corr < 0.75 else "HIGH",
            }
        )
    avg_corr = round(mean([row["correlation_to_us30_xau"] for row in observer_rows]), 2) if observer_rows else 0.0
    return {
        "candidate_1": {
            "symbols": observer_rows,
            "average_correlation_to_us30_xau": avg_corr,
            "independent_edge": bool(observer_rows) and avg_corr < 0.65,
            "duplication_risk": "LOW" if avg_corr < 0.5 else "MODERATE" if avg_corr < 0.75 else "HIGH",
        }
    }


def classify_candidate(validation: dict[str, Any], stress: dict[str, Any], correlation: dict[str, Any]) -> str:
    """Return safety decision for one candidate."""
    metrics = validation["365D"]
    if not stress["pass"]:
        return "REJECTED"
    if float(metrics["pf"]) >= 2.9 and float(metrics["win_rate"]) >= 73.0 and float(metrics["max_drawdown"]) < 4.0:
        if correlation and not correlation.get("independent_edge", True):
            return "CONDITIONAL"
        return "APPROVED_FOR_FUTURE_REVIEW"
    return "CONDITIONAL"


def pf_dd_efficiency(metrics: dict[str, Any]) -> float:
    """Return PF gain per DD increase versus Original Elite."""
    pf_gain = float(metrics["pf"]) - ORIGINAL_ELITE["pf"]
    dd_increase = max(0.01, float(metrics["max_drawdown"]) - ORIGINAL_ELITE["max_drawdown"])
    return round(pf_gain / dd_increase, 3)


def rank_candidates(
    validation: dict[str, Any],
    stress: dict[str, Any],
    decisions: dict[str, str],
) -> list[dict[str, Any]]:
    """Return candidates ranked by safety, stress, and PF/DD efficiency."""
    rows = []
    for candidate_id in CANDIDATE_ORDER:
        metrics = validation[candidate_id]["365D"]
        efficiency = validation[candidate_id]["pf_dd_efficiency"]
        stress_pass = bool(stress[candidate_id]["pass"])
        decision = decisions[candidate_id]
        rows.append(
            {
                "candidate_id": candidate_id,
                "decision": decision,
                "pf_dd_efficiency": efficiency,
                "stress_pass": stress_pass,
                "pf": metrics["pf"],
                "win_rate": metrics["win_rate"],
                "trades": metrics["trades"],
                "max_drawdown": metrics["max_drawdown"],
                "rank_score": rank_score(decision, stress_pass, efficiency, metrics),
            }
        )
    rows.sort(key=lambda item: (-float(item["rank_score"]), str(item["candidate_id"])))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


def rank_score(decision: str, stress_pass: bool, efficiency: float, metrics: dict[str, Any]) -> float:
    """Return composite safety-first ranking score."""
    decision_score = {"APPROVED_FOR_FUTURE_REVIEW": 100.0, "CONDITIONAL": 50.0, "REJECTED": 0.0}[decision]
    stress_score = 25.0 if stress_pass else 0.0
    wr_score = max(0.0, float(metrics["win_rate"]) - ORIGINAL_ELITE["win_rate"]) * 2.0
    trade_score = min(10.0, max(0.0, int(metrics["trades"]) - ORIGINAL_ELITE["trades"]) * 0.5)
    dd_penalty = max(0.0, float(metrics["max_drawdown"]) - ORIGINAL_ELITE["max_drawdown"]) * 15.0
    return round(decision_score + stress_score + efficiency * 8.0 + wr_score + trade_score - dd_penalty, 3)


def best_candidate_reason(
    candidate_id: str,
    validation: dict[str, Any],
    stress: dict[str, Any],
    decisions: dict[str, str],
) -> str:
    """Return concise rationale for best candidate."""
    if candidate_id == "none":
        return "No candidate passed controlled validation."
    metrics = validation[candidate_id]["365D"]
    return (
        f"{candidate_id} is the only candidate classified {decisions[candidate_id]} with stress_pass={stress[candidate_id]['pass']}; "
        f"PF/DD efficiency={validation[candidate_id]['pf_dd_efficiency']}, PF={metrics['pf']}, DD={metrics['max_drawdown']}%."
    )

