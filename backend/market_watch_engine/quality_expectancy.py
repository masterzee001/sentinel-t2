"""Quality grading and expectancy ranking for Market Watch."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


STRATEGIES = ("ict_liquidity", "trend_following", "mean_reversion")
GRADES = ("A+", "A", "B", "C", "REJECT")
GRADE_ORDER = {"A+": 5, "A": 4, "B": 3, "C": 2, "REJECT": 0}
GRADE_MULTIPLIERS = {"A+": 1.35, "A": 1.15, "B": 0.9, "C": 0.55, "REJECT": 0.0}


class StrategyQualityGrader:
    """Grade strategy setup quality from pattern/session context."""

    def grade_all(self, *, scores: dict[str, Any], pattern: dict[str, Any], session: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
        """Return quality grades for every strategy."""
        context = context or {}
        return {
            "ict_liquidity": self.grade_ict(scores, pattern, session, context),
            "trend_following": self.grade_trend(scores, pattern, session, context),
            "mean_reversion": self.grade_mean_reversion(scores, pattern, session, context),
        }

    def grade_ict(self, scores: dict[str, Any], pattern: dict[str, Any], session: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Grade ICT liquidity setup quality."""
        components = {
            "sweep_quality": 90 if bool(pattern.get("sweep_detected", False)) else 20,
            "mss_strength": 85 if bool(pattern.get("mss_confirmed", False)) else 20,
            "displacement_quality": int(context.get("displacement_score", pattern.get("volatility_expansion", 45)) or 45),
            "fvg_quality": int(context.get("fvg_quality", 80 if context.get("fvg_present", False) else 30) or 30),
            "ob_quality": int(context.get("ob_quality", 76 if context.get("order_block_present", False) else 30) or 30),
            "smt_alignment": int(context.get("smt_alignment", 70 if context.get("smt_detected", False) else 40) or 40),
            "killzone_quality": int(session.get("session_quality", 0) or 0),
            "liquidity_target_clarity": int(context.get("target_clarity", 75 if context.get("likely_draw_on_liquidity", True) else 35) or 35),
            "narrative_alignment": int(context.get("narrative_alignment", 50) or 50),
        }
        quality_score = round(sum(components.values()) / len(components), 2)
        if not pattern.get("sweep_detected") or not pattern.get("mss_confirmed") or int(pattern.get("noise_score", 0) or 0) >= 60:
            grade = "REJECT"
        else:
            grade = grade_from_quality_score(quality_score)
        return quality_result(scores.get("ict_liquidity", 0), grade, quality_score, components)

    def grade_trend(self, scores: dict[str, Any], pattern: dict[str, Any], session: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Grade trend-following setup quality."""
        components = {
            "trend_strength": int(pattern.get("trend_strength", 0) or 0),
            "htf_alignment": int(context.get("htf_alignment", 75 if context.get("htf_bias_aligned", False) else 45) or 45),
            "pullback_quality": int(context.get("pullback_quality", 50) or 50),
            "expansion_strength": int(pattern.get("volatility_expansion", 0) or 0),
            "momentum_persistence": int(context.get("momentum_persistence", pattern.get("trend_strength", 50)) or 50),
            "continuation_target_clarity": int(context.get("continuation_target_clarity", context.get("target_clarity", 55)) or 55),
            "session_quality": int(session.get("session_quality", 0) or 0),
            "exhaustion_safety": max(0, 100 - int(pattern.get("exhaustion_score", 0) or 0)),
        }
        quality_score = round(sum(components.values()) / len(components), 2)
        if int(pattern.get("trend_strength", 0) or 0) < 70 or int(pattern.get("noise_score", 0) or 0) >= 60:
            grade = "REJECT"
        else:
            grade = grade_from_quality_score(quality_score)
        return quality_result(scores.get("trend_following", 0), grade, quality_score, components)

    def grade_mean_reversion(self, scores: dict[str, Any], pattern: dict[str, Any], session: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Grade mean-reversion setup quality."""
        components = {
            "overextension": int(pattern.get("overextension_score", 0) or 0),
            "exhaustion_strength": int(pattern.get("exhaustion_score", 0) or 0),
            "range_symmetry": int(context.get("range_symmetry", pattern.get("range_score", 45)) or 45),
            "equilibrium_deviation": int(context.get("fair_value_distance", 50) or 50),
            "liquidity_raid_quality": 85 if bool(pattern.get("sweep_detected", False) or context.get("failed_breakout", False)) else 35,
            "reversal_confirmation": int(context.get("reversal_confirmation", 75 if context.get("failed_breakout", False) else 45) or 45),
            "mean_target_clarity": int(context.get("mean_target_clarity", context.get("target_clarity", 55)) or 55),
            "session_quality": int(session.get("session_quality", 0) or 0),
        }
        confirmations = sum(
            1
            for value in (
                components["overextension"] >= 70,
                components["exhaustion_strength"] >= 65,
                components["liquidity_raid_quality"] >= 70,
                components["equilibrium_deviation"] >= 65,
                components["mean_target_clarity"] >= 60,
                components["session_quality"] >= 50,
                int(pattern.get("range_score", 0) or 0) >= 45,
            )
            if value
        )
        quality_score = round(sum(components.values()) / len(components), 2)
        if confirmations < 3 or int(pattern.get("noise_score", 0) or 0) >= 60:
            grade = "REJECT"
        else:
            grade = grade_from_quality_score(quality_score)
        result = quality_result(scores.get("mean_reversion", 0), grade, quality_score, components)
        result["confirmations"] = confirmations
        return result


class SetupExpectancyEngine:
    """Lookup and rank historical setup expectancy."""

    DEFAULT_EXPECTANCY = {
        "A+": {"pf": 2.85, "wr": 73.5, "avg_rr": 0.52},
        "A": {"pf": 2.25, "wr": 68.4, "avg_rr": 0.38},
        "B": {"pf": 1.48, "wr": 58.2, "avg_rr": 0.14},
        "C": {"pf": 0.72, "wr": 43.1, "avg_rr": -0.16},
        "REJECT": {"pf": 0.0, "wr": 0.0, "avg_rr": 0.0},
    }
    STRATEGY_MULTIPLIERS = {
        "ict_liquidity": 0.75,
        "trend_following": 1.12,
        "mean_reversion": 1.18,
    }
    SYMBOL_MULTIPLIERS: dict[str, float] = {}
    SESSION_MULTIPLIERS = {
        "new_york_open": 1.05,
        "new_york_continuation": 1.08,
        "london_open": 0.95,
        "london_continuation": 0.6,
        "asia": 0.75,
    }
    PATTERN_MULTIPLIERS = {
        "trend_continuation": 1.15,
        "liquidity_sweep_reversal": 0.85,
        "range_mean_reversion": 1.05,
        "compression_breakout": 0.82,
        "exhaustion_reversal": 1.02,
        "noisy_chop": 0.0,
        "no_clear_pattern": 0.4,
    }

    def lookup(self, *, strategy: str, grade: str, symbol: str, session: str, pattern: str) -> dict[str, Any]:
        """Return grade/symbol/session/pattern-adjusted expectancy."""
        base = dict(self.DEFAULT_EXPECTANCY.get(grade, self.DEFAULT_EXPECTANCY["REJECT"]))
        if grade == "REJECT":
            return {**base, "strategy": strategy, "grade": grade, "edge_score": 0.0}
        multiplier = (
            self.STRATEGY_MULTIPLIERS.get(strategy, 1.0)
            * self.SYMBOL_MULTIPLIERS.get(str(symbol).upper(), 1.0)
            * self.SESSION_MULTIPLIERS.get(str(session), 1.0)
            * self.PATTERN_MULTIPLIERS.get(str(pattern), 1.0)
        )
        pf = round(base["pf"] * multiplier, 2)
        wr = round(min(85.0, base["wr"] * min(1.15, multiplier)), 2)
        edge_score = round(pf * 25 + wr * 0.6 + base["avg_rr"] * 20, 2)
        return {
            "strategy": strategy,
            "grade": grade,
            "pf": pf,
            "wr": wr,
            "avg_rr": base["avg_rr"],
            "edge_score": edge_score,
            "multiplier": round(multiplier, 3),
        }


class GradeAwareRouter:
    """Select strategy by score, quality, and expectancy."""

    def __init__(self, expectancy_engine: SetupExpectancyEngine | None = None) -> None:
        self.expectancy_engine = expectancy_engine or SetupExpectancyEngine()

    def select(
        self,
        *,
        scores: dict[str, Any],
        quality: dict[str, dict[str, Any]],
        symbol: str,
        session: str,
        pattern: str,
    ) -> dict[str, Any]:
        """Return the best grade-aware strategy decision."""
        candidates = {}
        for strategy in STRATEGIES:
            grade = str(quality.get(strategy, {}).get("grade", "REJECT"))
            expectancy = self.expectancy_engine.lookup(strategy=strategy, grade=grade, symbol=symbol, session=session, pattern=pattern)
            raw_score = float(scores.get(strategy, 0.0) or 0.0)
            quality_score = float(quality.get(strategy, {}).get("quality_score", 0.0) or 0.0)
            final_edge = round(raw_score * 0.25 + quality_score * 0.25 + expectancy.get("edge_score", 0.0) * 0.5, 2)
            candidates[strategy] = {
                "strategy": strategy,
                "grade": grade,
                "raw_score": raw_score,
                "quality_score": quality_score,
                "expectancy": expectancy,
                "final_edge": 0.0 if grade == "REJECT" else final_edge,
            }
        selected = max(candidates.values(), key=lambda item: item["final_edge"])
        if selected["grade"] == "REJECT" or selected["final_edge"] < 75:
            return {
                "selected_strategy": "no_trade",
                "selected_grade": "REJECT",
                "final_edge": selected["final_edge"],
                "candidates": candidates,
                "reason": "No setup cleared quality-adjusted expectancy threshold",
            }
        return {
            "selected_strategy": selected["strategy"],
            "selected_grade": selected["grade"],
            "final_edge": selected["final_edge"],
            "candidates": candidates,
            "reason": "Highest quality-adjusted historical expectancy",
        }


def quality_result(strategy_score: Any, grade: str, quality_score: float, components: dict[str, int]) -> dict[str, Any]:
    """Return normalized quality-grading payload."""
    return {
        "strategy_score": int(strategy_score or 0),
        "grade": grade,
        "quality_score": quality_score,
        "components": components,
    }


def grade_from_quality_score(score: float) -> str:
    """Convert quality score into an institutional grade."""
    if score >= 82:
        return "A+"
    if score >= 72:
        return "A"
    if score >= 60:
        return "B"
    if score >= 45:
        return "C"
    return "REJECT"


def setup_expectancy_database(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build historical setup expectancy database from graded records."""
    return {
        "records": records,
        "by_strategy_grade": grouped_expectancy(records, ("strategy", "quality_grade")),
        "by_grade": grouped_expectancy(records, ("quality_grade",)),
        "by_symbol": grouped_expectancy(records, ("symbol",)),
        "by_session": grouped_expectancy(records, ("session",)),
        "by_pattern": grouped_expectancy(records, ("pattern",)),
    }


def grouped_expectancy(records: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Aggregate expectancy by one or more record keys."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        name = "|".join(str(record.get(key, "unknown")) for key in keys)
        buckets[name].append(record)
    return {name: expectancy_metrics(items) for name, items in buckets.items()}


def expectancy_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return PF/WR/RR metrics for setup records."""
    wins = sum(1 for record in records if str(record.get("result")) == "WIN")
    losses = sum(1 for record in records if str(record.get("result")) == "LOSS")
    rrs = [float(record.get("rr", 0.0) or 0.0) for record in records]
    gross_profit = sum(max(rr, 0.0) for rr in rrs)
    gross_loss = abs(sum(min(rr, 0.0) for rr in rrs))
    return {
        "trades": len(records),
        "pf": round(gross_profit / gross_loss, 2) if gross_loss else round(gross_profit, 2),
        "wr": round(wins / max(wins + losses, 1) * 100, 2),
        "avg_rr": round(sum(rrs) / max(len(rrs), 1), 2),
    }


def quality_distribution(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Return grade distribution by strategy."""
    distribution: dict[str, Counter[str]] = {strategy: Counter() for strategy in STRATEGIES}
    for record in records:
        strategy = str(record.get("strategy", ""))
        grade = str(record.get("quality_grade", "REJECT"))
        if strategy in distribution:
            distribution[strategy][grade] += 1
    return {
        strategy: {grade: int(counter.get(grade, 0)) for grade in GRADES}
        for strategy, counter in distribution.items()
    }


def grade_performance_correlation(database: dict[str, Any]) -> dict[str, Any]:
    """Return whether higher grade maps to stronger performance."""
    by_grade = database.get("by_grade", {})
    pf_by_grade = {grade: float(by_grade.get(grade, {}).get("pf", 0.0) or 0.0) for grade in ("A+", "A", "B", "C")}
    monotonic = pf_by_grade["A+"] >= pf_by_grade["A"] >= pf_by_grade["B"] >= pf_by_grade["C"]
    score = round(sum(max(pf_by_grade[left] - pf_by_grade[right], 0.0) for left, right in (("A+", "A"), ("A", "B"), ("B", "C"))) / 3, 2)
    return {
        "pf_by_grade": pf_by_grade,
        "wr_by_grade": {grade: float(by_grade.get(grade, {}).get("wr", 0.0) or 0.0) for grade in ("A+", "A", "B", "C")},
        "avg_rr_by_grade": {grade: float(by_grade.get(grade, {}).get("avg_rr", 0.0) or 0.0) for grade in ("A+", "A", "B", "C")},
        "monotonic": monotonic,
        "correlation_score": score,
    }


def severity_weighted_memory_score(repeated_conditions: dict[str, dict[str, int]], condition_pf: dict[str, float], opportunities: int) -> dict[str, Any]:
    """Return severity-weighted routing memory score."""
    severity_total = 0.0
    for conditions in repeated_conditions.values():
        for condition, count in conditions.items():
            pf = float(condition_pf.get(condition, 1.0) or 1.0)
            severity_total += count * max(0.0, 1.2 - min(pf, 1.2))
    denominator = max(float(opportunities), 1.0)
    value = max(0.0, min(1.0, 1 - severity_total / denominator))
    score = round(value * 100, 2)
    return {"value": score, "classification": memory_classification(score), "severity_penalty": round(severity_total, 2)}


def memory_classification(score: float) -> str:
    """Return SRMS-style memory classification."""
    if score < 50:
        return "POOR"
    if score < 70:
        return "WEAK"
    if score < 85:
        return "GOOD"
    if score < 95:
        return "STRONG"
    return "ELITE"
