"""Strategy selection for Market Watch."""

from __future__ import annotations

from typing import Any


class StrategySelector:
    """Select the highest-expectancy advisory strategy."""

    DEFAULT_THRESHOLD = 70
    DEFAULT_WEIGHTED_THRESHOLD = 75
    DEFAULT_MINIMUM_SESSION_QUALITY = 50
    DEFAULT_EXPECTANCY_MULTIPLIERS = {
        "ict_liquidity": 0.8,
        "trend_following": 1.2,
        "mean_reversion": 1.0,
    }
    DEFAULT_PATTERN_MULTIPLIERS = {
        "noisy_chop": 0.0,
    }
    STRATEGY_LABELS = {
        "ict_liquidity": "ICT Liquidity",
        "trend_following": "Trend Following",
        "mean_reversion": "Mean Reversion",
        "no_trade": "No Trade",
    }

    def __init__(
        self,
        minimum_advisory_threshold: int = DEFAULT_THRESHOLD,
        *,
        minimum_weighted_score: int = DEFAULT_WEIGHTED_THRESHOLD,
        minimum_session_quality: int = DEFAULT_MINIMUM_SESSION_QUALITY,
        expectancy_multipliers: dict[str, Any] | None = None,
        pattern_multipliers: dict[str, Any] | None = None,
    ) -> None:
        self.minimum_advisory_threshold = int(minimum_advisory_threshold)
        self.minimum_weighted_score = int(minimum_weighted_score)
        self.minimum_session_quality = int(minimum_session_quality)
        self.expectancy_multipliers = {
            **self.DEFAULT_EXPECTANCY_MULTIPLIERS,
            **{str(key): float(value) for key, value in (expectancy_multipliers or {}).items()},
        }
        self.pattern_multipliers = {
            **self.DEFAULT_PATTERN_MULTIPLIERS,
            **{str(key): float(value) for key, value in (pattern_multipliers or {}).items()},
        }

    def select(
        self,
        scores: dict[str, Any],
        *,
        session: dict[str, Any] | None = None,
        pattern: dict[str, Any] | None = None,
        risk: dict[str, Any] | None = None,
        news: dict[str, Any] | None = None,
        readiness: dict[str, Any] | None = None,
        guardrail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return advisory selection or no_trade/blocked."""
        risk = risk or {}
        news = news or {}
        readiness = readiness or {}
        guardrail = guardrail or {}
        session = session or {}
        pattern = pattern or {}
        block_reason = self.block_reason(risk=risk, news=news, readiness=readiness, guardrail=guardrail)
        if block_reason:
            return {
                "selected_strategy": "no_trade",
                "selected_score": 0,
                "selected_raw_score": 0,
                "selected_weighted_score": 0.0,
                "weighted_scores": {},
                "status": "BLOCKED",
                "reason": block_reason,
            }

        numeric_scores = {
            key: int(scores.get(key, 0) or 0)
            for key in ("ict_liquidity", "trend_following", "mean_reversion")
        }
        dominant = str(pattern.get("dominant_pattern", "no_clear_pattern"))
        if dominant == "noisy_chop":
            return self.no_trade(
                weighted_scores={strategy: 0.0 for strategy in numeric_scores},
                reason="Noisy chop pattern blocks Market Watch strategy selection",
            )
        if not self.session_acceptable(session):
            return self.no_trade(
                weighted_scores=self.weighted_scores(numeric_scores, session=session, pattern=pattern),
                reason="Session quality is below Market Watch advisory threshold",
            )

        weighted_scores = self.weighted_scores(numeric_scores, session=session, pattern=pattern)
        preferred = self.trend_continuation_preference(numeric_scores, weighted_scores, dominant)
        if preferred:
            strategy = preferred
        else:
            strategy = max(weighted_scores.items(), key=lambda item: (item[1], self.tie_breaker(item[0], session, pattern)))[0]
        raw_score = numeric_scores.get(strategy, 0)
        weighted_score = float(weighted_scores.get(strategy, 0.0))
        if raw_score < self.minimum_advisory_threshold or weighted_score < self.minimum_weighted_score:
            return {
                "selected_strategy": "no_trade",
                "selected_score": round(weighted_score, 2),
                "selected_raw_score": raw_score,
                "selected_weighted_score": round(weighted_score, 2),
                "weighted_scores": weighted_scores,
                "status": "NO_TRADE",
                "reason": "Market Watch weighted expectancy is below advisory threshold",
            }
        return {
            "selected_strategy": strategy,
            "selected_score": round(weighted_score, 2),
            "selected_raw_score": raw_score,
            "selected_weighted_score": round(weighted_score, 2),
            "weighted_scores": weighted_scores,
            "expectancy_multiplier": self.expectancy_multipliers.get(strategy, 1.0),
            "status": "ADVISORY_SELECTED",
            "reason": f"{self.STRATEGY_LABELS[strategy]} has highest weighted expectancy for current Market Watch context",
        }

    def weighted_scores(self, scores: dict[str, int], *, session: dict[str, Any], pattern: dict[str, Any]) -> dict[str, float]:
        """Return strategy scores after historical expectancy weighting."""
        dominant = str(pattern.get("dominant_pattern", "no_clear_pattern"))
        pattern_multiplier = float(self.pattern_multipliers.get(dominant, 1.0))
        weighted: dict[str, float] = {}
        for strategy, raw_score in scores.items():
            multiplier = float(self.expectancy_multipliers.get(strategy, 1.0)) * pattern_multiplier
            penalty = self.strategy_penalty(strategy, pattern)
            weighted[strategy] = round(max(0.0, min(100.0, float(raw_score) * multiplier * penalty)), 2)
        return weighted

    @staticmethod
    def strategy_penalty(strategy: str, pattern: dict[str, Any]) -> float:
        """Apply advisory penalties for strategy-pattern conflicts."""
        if strategy != "ict_liquidity":
            return 1.0
        dominant = str(pattern.get("dominant_pattern", "no_clear_pattern"))
        mss_confirmed = bool(pattern.get("mss_confirmed", False))
        smt_sample = int(pattern.get("smt_sample", 0) or 0)
        if dominant == "noisy_chop":
            return 0.0
        if not mss_confirmed and smt_sample < 10:
            return 0.25
        return 1.0

    def session_acceptable(self, session: dict[str, Any]) -> bool:
        """Return whether session quality can support an advisory selection."""
        status = str(session.get("session_status", "")).lower()
        if status in {"blocked", "disabled", "avoid"}:
            return False
        return int(session.get("session_quality", 0) or 0) >= self.minimum_session_quality

    def trend_continuation_preference(
        self,
        raw_scores: dict[str, int],
        weighted_scores: dict[str, float],
        dominant_pattern: str,
    ) -> str:
        """Prefer trend-following when trend continuation is confirmed."""
        if (
            dominant_pattern == "trend_continuation"
            and raw_scores.get("trend_following", 0) >= self.minimum_advisory_threshold
            and weighted_scores.get("trend_following", 0.0) >= self.minimum_weighted_score
        ):
            return "trend_following"
        return ""

    @staticmethod
    def no_trade(*, weighted_scores: dict[str, float], reason: str) -> dict[str, Any]:
        return {
            "selected_strategy": "no_trade",
            "selected_score": 0,
            "selected_raw_score": 0,
            "selected_weighted_score": 0.0,
            "weighted_scores": weighted_scores,
            "status": "NO_TRADE",
            "reason": reason,
        }

    @staticmethod
    def block_reason(
        *,
        risk: dict[str, Any],
        news: dict[str, Any],
        readiness: dict[str, Any],
        guardrail: dict[str, Any],
    ) -> str:
        if risk.get("permission", {}).get("status") == "BLOCKED" or risk.get("permission", {}).get("trade_allowed") is False:
            return "Risk Governor blocked Market Watch advisory selection"
        if bool(news.get("lock_active", False)):
            return "News lock blocks Market Watch advisory selection"
        if readiness and readiness.get("ready") is False and readiness.get("strict_market_watch_block"):
            return "Readiness checker blocks Market Watch advisory selection"
        if guardrail.get("status") == "BLOCKED":
            return "Strategy guardrails block Market Watch advisory selection"
        return ""

    @staticmethod
    def tie_breaker(strategy: str, session: dict[str, Any] | None, pattern: dict[str, Any] | None) -> int:
        """Prefer strategy aligned with session and pattern in score ties."""
        session = session or {}
        pattern = pattern or {}
        dominant = pattern.get("dominant_pattern")
        session_quality = int(session.get("session_quality", 0) or 0)
        if strategy == "ict_liquidity" and dominant == "liquidity_sweep_reversal":
            return 3 + (1 if session_quality >= 80 else 0)
        if strategy == "trend_following" and dominant == "trend_continuation":
            return 3
        if strategy == "mean_reversion" and dominant in {"range_mean_reversion", "exhaustion_reversal"}:
            return 3
        return 0
