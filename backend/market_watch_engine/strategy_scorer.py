"""Strategy scoring for Market Watch."""

from __future__ import annotations

from typing import Any


class StrategyScorer:
    """Score ICT, trend-following, and mean-reversion strategy families."""

    STRATEGIES = ("ict_liquidity", "trend_following", "mean_reversion")

    def score(
        self,
        *,
        pattern: dict[str, Any],
        session: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return strategy scores and reasoning."""
        context = context or {}
        session_quality = int(session.get("session_quality", 0) or 0)
        dominant = str(pattern.get("dominant_pattern", "no_clear_pattern"))
        sweep = bool(pattern.get("sweep_detected", False) or context.get("sweep_detected", False))
        mss = bool(pattern.get("mss_confirmed", False) or context.get("mss_confirmed", False))
        fvg = bool(context.get("fvg_present", context.get("fvg", False)))
        order_block = bool(context.get("order_block_present", context.get("order_block", False)))
        premium_discount = int(context.get("premium_discount_alignment", 50) or 50)
        narrative_alignment = int(context.get("narrative_alignment", 50) or 50)
        smt_sample = int(context.get("smt_sample", 0) or 0)
        smt_bonus = 5 if smt_sample >= 10 and bool(context.get("smt_detected", False)) else 0

        ict = self.clamp(
            20
            + (25 if sweep else 0)
            + (18 if mss else 0)
            + (13 if fvg else 0)
            + (9 if order_block else 0)
            + session_quality * 0.15
            + premium_discount * 0.08
            + narrative_alignment * 0.07
            + smt_bonus
        )
        trend = self.clamp(
            15
            + int(pattern.get("trend_strength", 0)) * 0.35
            + int(pattern.get("volatility_expansion", 0)) * 0.18
            + int(context.get("pullback_quality", 50) or 50) * 0.16
            + int(context.get("continuation_structure", 50) or 50) * 0.16
            + int(context.get("htf_alignment", 50) or 50) * 0.15
        )
        mean = self.clamp(
            15
            + int(pattern.get("overextension_score", 0)) * 0.25
            + int(pattern.get("exhaustion_score", 0)) * 0.25
            + int(pattern.get("range_score", 0)) * 0.18
            + (12 if bool(context.get("failed_breakout", False)) else 0)
            + (10 if sweep else 0)
            + int(context.get("fair_value_distance", 50) or 50) * 0.1
        )

        if dominant == "liquidity_sweep_reversal":
            ict = self.clamp(ict + 8)
        elif dominant == "trend_continuation":
            trend = self.clamp(trend + 8)
        elif dominant in {"range_mean_reversion", "exhaustion_reversal"}:
            mean = self.clamp(mean + 8)
        elif dominant == "noisy_chop":
            ict = self.clamp(ict - 20)
            trend = self.clamp(trend - 20)
            mean = self.clamp(mean - 15)

        return {
            "ict_liquidity": ict,
            "trend_following": trend,
            "mean_reversion": mean,
            "reasoning": {
                "ict_liquidity": self.ict_reason(sweep, mss, fvg, order_block, session_quality),
                "trend_following": self.trend_reason(pattern, context),
                "mean_reversion": self.mean_reason(pattern, context),
            },
        }

    @staticmethod
    def clamp(value: float) -> int:
        return int(max(0, min(100, round(value))))

    @staticmethod
    def ict_reason(sweep: bool, mss: bool, fvg: bool, order_block: bool, session_quality: int) -> str:
        parts = []
        if sweep:
            parts.append("sweep")
        if mss:
            parts.append("MSS")
        if fvg:
            parts.append("FVG")
        if order_block:
            parts.append("OB")
        if session_quality >= 80:
            parts.append("session alignment")
        return " + ".join(parts) if parts else "ICT components incomplete"

    @staticmethod
    def trend_reason(pattern: dict[str, Any], context: dict[str, Any]) -> str:
        if int(pattern.get("trend_strength", 0) or 0) >= 70:
            return "Trend strength and continuation structure support follow-through"
        if bool(context.get("continuation_structure", False)):
            return "Continuation structure present but trend strength is incomplete"
        return "Trend present but continuation structure incomplete"

    @staticmethod
    def mean_reason(pattern: dict[str, Any], context: dict[str, Any]) -> str:
        if int(pattern.get("exhaustion_score", 0) or 0) >= 70:
            return "Exhaustion supports mean reversion"
        if bool(context.get("failed_breakout", False)):
            return "Failed breakout supports reversion to fair value"
        return "Moderate exhaustion but range not confirmed"
