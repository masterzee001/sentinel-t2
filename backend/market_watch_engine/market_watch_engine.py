"""Market Watch advisory orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from backend.market_watch_engine.pattern_detector import PatternDetector
from backend.market_watch_engine.quality_expectancy import GradeAwareRouter, SetupExpectancyEngine, StrategyQualityGrader
from backend.market_watch_engine.session_specialization import SessionSpecialization
from backend.market_watch_engine.strategy_planners import StrategyPlanners
from backend.market_watch_engine.strategy_scorer import StrategyScorer
from backend.market_watch_engine.strategy_selector import StrategySelector


class MarketWatchEngine:
    """Run advisory-only market pattern, strategy scoring, and planning."""

    DEFAULT_CONFIG = {
        "market_watch": {
            "enabled": True,
            "affect_production": False,
            "advisory_only": True,
            "minimum_advisory_threshold": 70,
            "minimum_weighted_score": 75,
            "minimum_session_quality": 50,
            "expectancy_multipliers": {
                "ict_liquidity": 0.8,
                "trend_following": 1.2,
                "mean_reversion": 1.0,
            },
            "pattern_multipliers": {
                "noisy_chop": 0.0,
            },
        }
    }

    def __init__(
        self,
        *,
        config_dir: str | Path | None = None,
        session_specialization: SessionSpecialization | None = None,
        pattern_detector: PatternDetector | None = None,
        strategy_scorer: StrategyScorer | None = None,
        strategy_selector: StrategySelector | None = None,
        strategy_planners: StrategyPlanners | None = None,
        quality_grader: StrategyQualityGrader | None = None,
        grade_aware_router: GradeAwareRouter | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.config = self._deep_merge(self.DEFAULT_CONFIG, self._load_yaml_file(self.config_dir / "market_watch.yaml"))
        market_watch_config = self.config.get("market_watch", {})
        self.session_specialization = session_specialization or SessionSpecialization()
        self.pattern_detector = pattern_detector or PatternDetector()
        self.strategy_scorer = strategy_scorer or StrategyScorer()
        self.strategy_selector = strategy_selector or StrategySelector(
            minimum_advisory_threshold=int(market_watch_config.get("minimum_advisory_threshold", 70)),
            minimum_weighted_score=int(market_watch_config.get("minimum_weighted_score", 75)),
            minimum_session_quality=int(market_watch_config.get("minimum_session_quality", 50)),
            expectancy_multipliers=market_watch_config.get("expectancy_multipliers", {}),
            pattern_multipliers=market_watch_config.get("pattern_multipliers", {}),
        )
        self.strategy_planners = strategy_planners or StrategyPlanners()
        self.quality_grader = quality_grader or StrategyQualityGrader()
        self.grade_aware_router = grade_aware_router or GradeAwareRouter(SetupExpectancyEngine())

    def analyze(
        self,
        symbol: str,
        *,
        candles: Any | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the full Market Watch advisory flow for one symbol."""
        context = context or {}
        normalized_symbol = str(symbol).upper().strip()
        session_name = context.get("session") or context.get("active_killzone") or context.get("killzone", {}).get("active_killzone")
        session = self.session_specialization.evaluate(normalized_symbol, session_name, context=context.get("killzone", context))
        pattern = self.pattern_detector.detect(
            symbol=normalized_symbol,
            candles=candles,
            trend=context.get("trend"),
            liquidity=context.get("liquidity"),
            ict=context.get("ict"),
            narrative=context.get("narrative"),
            session=session,
            context=context,
        )
        scores = self.strategy_scorer.score(pattern=pattern, session=session, context=context)
        numeric_scores = {
            "ict_liquidity": scores.get("ict_liquidity", 0),
            "trend_following": scores.get("trend_following", 0),
            "mean_reversion": scores.get("mean_reversion", 0),
        }
        quality = self.quality_grader.grade_all(scores=numeric_scores, pattern=pattern, session=session, context=context)
        grade_aware = self.grade_aware_router.select(
            scores=numeric_scores,
            quality=quality,
            symbol=normalized_symbol,
            session=str(session.get("session", "")),
            pattern=str(pattern.get("dominant_pattern", "no_clear_pattern")),
        )
        selection = self.strategy_selector.select(
            scores,
            session=session,
            pattern=pattern,
            risk=context.get("risk"),
            news=context.get("news"),
            readiness=context.get("readiness"),
            guardrail=context.get("guardrail"),
        )
        if selection.get("status") == "ADVISORY_SELECTED" and grade_aware.get("selected_strategy") != "no_trade":
            selection = {
                **selection,
                "selected_strategy": grade_aware.get("selected_strategy"),
                "selected_grade": grade_aware.get("selected_grade"),
                "selected_quality_edge": grade_aware.get("final_edge"),
                "reason": f"{selection.get('reason', '')}; grade-aware override: {grade_aware.get('reason')}",
            }
        plan = self.strategy_planners.plan(
            symbol=normalized_symbol,
            selection=selection,
            pattern=pattern,
            session=session,
            context=context,
        )
        return {
            "symbol": normalized_symbol,
            "market_watch_status": "ADVISORY" if self.advisory_only else "EXPERIMENTAL",
            "enabled": self.enabled,
            "advisory_only": self.advisory_only,
            "affects_production": self.affect_production,
            "production_impact": self.affect_production,
            "session": session,
            "session_quality": session.get("session_quality", 0),
            "dominant_pattern": pattern.get("dominant_pattern", "no_clear_pattern"),
            "pattern": pattern,
            "scores": {
                "ict_liquidity": scores.get("ict_liquidity", 0),
                "trend_following": scores.get("trend_following", 0),
                "mean_reversion": scores.get("mean_reversion", 0),
            },
            "score_reasoning": scores.get("reasoning", {}),
            "selected_strategy": selection.get("selected_strategy", "no_trade"),
            "selected_score": selection.get("selected_score", 0),
            "selected_raw_score": selection.get("selected_raw_score", selection.get("selected_score", 0)),
            "selected_weighted_score": selection.get("selected_weighted_score", selection.get("selected_score", 0)),
            "weighted_scores": selection.get("weighted_scores", {}),
            "quality_grades": quality,
            "grade_aware_selection": grade_aware,
            "selection_status": selection.get("status", "NO_TRADE"),
            "selection_reason": selection.get("reason", ""),
            "plan_status": plan.get("plan_status", "NO_TRADE"),
            "trade_plan": plan,
        }

    def analyze_many(self, symbols: list[str], *, contexts: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Run Market Watch for multiple symbols."""
        contexts = contexts or {}
        return [self.analyze(symbol, context=contexts.get(symbol.upper(), {})) for symbol in symbols]

    @property
    def market_watch_config(self) -> dict[str, Any]:
        return self.config.get("market_watch", {})

    @property
    def enabled(self) -> bool:
        return bool(self.market_watch_config.get("enabled", True))

    @property
    def affect_production(self) -> bool:
        return bool(self.market_watch_config.get("affect_production", False))

    @property
    def advisory_only(self) -> bool:
        return bool(self.market_watch_config.get("advisory_only", True))

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as file:
            return yaml.safe_load(file) or {}

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
