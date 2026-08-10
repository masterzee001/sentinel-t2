"""Final confidence scoring engine for Project Sentinel Advisor Mode."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from loguru import logger

from backend.ict_engine.ict_analyzer import ICTAnalyzer, ICTAnalyzerError
from backend.killzone_engine.killzone_analyzer import KillzoneAnalyzer
from backend.liquidity_engine.liquidity_analyzer import LiquidityAnalyzer, LiquidityAnalyzerError
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.news_filter.news_filter import NewsFilter
from backend.narrative_engine.narrative_analyzer import NarrativeAnalyzer, NarrativeAnalyzerError
from backend.smt_engine.smt_analyzer import SMTAnalyzer, SMTAnalyzerError
from backend.guardrails.strategy_guardrails import StrategyGuardrails
from backend.shared.confidence_band_registry import production_band
from backend.trend_engine.trend_analyzer import TrendAnalyzer, TrendAnalyzerError


class ConfidenceAnalyzerError(RuntimeError):
    """Raised when confidence analysis cannot be completed."""


class ConfidenceAnalyzer:
    """Combine Sentinel engines into one final setup confidence decision."""

    DEFAULT_ALLOWED_SYMBOLS = frozenset({"XAUUSD", "US30", "EURUSD", "GBPUSD"})
    DEFAULT_FOREX_SYMBOLS = frozenset({"EURUSD", "GBPUSD"})
    DEFAULT_SESSIONS = {
        "XAUUSD": [(8 * 60, 11 * 60), (13 * 60 + 30, 16 * 60)],
        "US30": [(13 * 60 + 30, 16 * 60)],
        "EURUSD": [(8 * 60, 11 * 60), (13 * 60 + 30, 16 * 60)],
        "GBPUSD": [(8 * 60, 11 * 60), (13 * 60 + 30, 16 * 60)],
    }
    DEFAULT_WEIGHTS = {
        "daily_bias": 15,
        "h4_narrative": 20,
        "liquidity_sweep": 20,
        "mss": 20,
        "fvg_quality": 15,
        "session_quality": 5,
        "target_clarity": 5,
        "minimum_confidence": 90,
    }
    WAT_TIMEZONE = ZoneInfo("Africa/Lagos")

    def __init__(
        self,
        connector: MT5Connector | None = None,
        trend_analyzer: TrendAnalyzer | None = None,
        liquidity_analyzer: LiquidityAnalyzer | None = None,
        ict_analyzer: ICTAnalyzer | None = None,
        news_filter: NewsFilter | None = None,
        killzone_analyzer: KillzoneAnalyzer | None = None,
        smt_analyzer: SMTAnalyzer | None = None,
        narrative_analyzer: NarrativeAnalyzer | None = None,
        strategy_guardrails: StrategyGuardrails | None = None,
        config_dir: str | Path | None = None,
        mode: str = "balanced",
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.mode = mode
        self.connector = connector or MT5Connector()
        self.trend_analyzer = trend_analyzer or TrendAnalyzer(connector=self.connector, config_dir=self.config_dir)
        self.liquidity_analyzer = liquidity_analyzer or LiquidityAnalyzer(
            connector=self.connector,
            config_dir=self.config_dir,
        )
        self.ict_analyzer = ict_analyzer or ICTAnalyzer(
            connector=self.connector,
            liquidity_analyzer=self.liquidity_analyzer,
            config_dir=self.config_dir,
        )
        self.news_filter = news_filter or NewsFilter(config_dir=self.config_dir)
        self.killzone_analyzer = killzone_analyzer or KillzoneAnalyzer(config_dir=self.config_dir)
        self.smt_analyzer = smt_analyzer or SMTAnalyzer(connector=self.connector, config_dir=self.config_dir)
        self.narrative_analyzer = narrative_analyzer or NarrativeAnalyzer(
            connector=self.connector,
            trend_analyzer=self.trend_analyzer,
            liquidity_analyzer=self.liquidity_analyzer,
            ict_analyzer=self.ict_analyzer,
            config_dir=self.config_dir,
        )
        self.strategy_guardrails = strategy_guardrails or StrategyGuardrails(config_dir=self.config_dir)
        self.rule_weights = self._load_rule_weights()
        self.trading_rules = self._load_trading_rules()
        self.market_sessions = self._load_market_sessions()
        self.allowed_symbols = self._load_allowed_symbols()
        self.forex_symbols = self._load_forex_symbols()

    def analyze(self, symbol: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return final confidence score and Advisor Mode decision."""
        normalized_symbol = self._validate_symbol(symbol)
        context = {
            "analysis_time": datetime.now(self.WAT_TIMEZONE),
            **(context or {}),
            "symbol": normalized_symbol,
        }
        logger.info("Starting confidence analysis for {}.", normalized_symbol)

        try:
            trend = self.trend_analyzer.get_overall_bias(normalized_symbol)
            liquidity = self.liquidity_analyzer.analyze(normalized_symbol)
            ict = self.ict_analyzer.analyze(normalized_symbol)
        except (TrendAnalyzerError, LiquidityAnalyzerError, ICTAnalyzerError, MT5ConnectorError, ValueError) as exc:
            raise ConfidenceAnalyzerError(f"Could not analyze confidence for {normalized_symbol}: {exc}") from exc

        return self.analyze_payloads(
            normalized_symbol,
            trend=trend,
            liquidity=liquidity,
            ict=ict,
            context=context,
        )

    def analyze_payloads(
        self,
        symbol: str,
        *,
        trend: dict[str, Any],
        liquidity: dict[str, Any],
        ict: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Analyze already-built engine payloads through the live confidence logic.

        Replay/backtest use this method with closed-candle causal payloads so the
        final score, guardrails, rejection reasons, and action labels stay aligned
        with live without replay-only scoring branches.
        """
        normalized_symbol = self._validate_symbol(symbol)
        context = {
            "analysis_time": datetime.now(self.WAT_TIMEZONE),
            **(context or {}),
            "symbol": normalized_symbol,
        }
        direction = self.infer_direction(trend, ict)
        context["news_status"] = context.get("news_status") or self.news_filter.check(
            normalized_symbol,
            current_time=context.get("analysis_time"),
        )
        context["killzone"] = context.get("killzone") or self.killzone_analyzer.analyze(
            normalized_symbol,
            current_time=context.get("analysis_time"),
        )
        context["smt"] = context.get("smt") or self.get_smt_status(normalized_symbol, context)
        context["narrative"] = context.get("narrative") or self.get_narrative_status(
            normalized_symbol,
            context=context,
            trend=trend,
            liquidity=liquidity,
            ict=ict,
        )
        scores = self.calculate_scores(
            trend=trend,
            liquidity=liquidity,
            ict=ict,
            context=context,
            direction=direction,
        )
        memory_advisory = self.calculate_memory_advisory_scores(context=context, ict=ict)
        total_confidence = self.calculate_total_confidence(scores)
        rejection_reasons = self.evaluate_hard_rejections(
            symbol=normalized_symbol,
            trend=trend,
            liquidity=liquidity,
            ict=ict,
            scores=scores,
            total_confidence=total_confidence,
            context=context,
            direction=direction,
        )
        minimum_required = self.get_minimum_confidence(normalized_symbol)
        guardrail = context.get("guardrail") or self.evaluate_guardrails(
            symbol=normalized_symbol,
            total_confidence=total_confidence,
            context=context,
        )
        rejection_reasons = self.dedupe_reasons(rejection_reasons + list(guardrail.get("reasons", [])))
        warnings = self.build_warnings(context=context, direction=direction)
        warnings = self.dedupe_reasons(warnings + list(guardrail.get("warnings", [])))
        decision = "APPROVED" if total_confidence >= minimum_required and not rejection_reasons else "REJECTED"
        confidence_band = str(guardrail.get("adjusted_confidence_band", self.get_confidence_band(total_confidence)[0]))
        recommended_action = str(guardrail.get("adjusted_recommended_action", self.get_confidence_band(total_confidence)[1]))

        result = {
            "symbol": normalized_symbol,
            "direction": direction,
            "scores": scores,
            "memory_advisory_scores": memory_advisory,
            "total_confidence": total_confidence,
            "confidence_band": confidence_band,
            "recommended_action": recommended_action,
            "minimum_required": minimum_required,
            "decision": decision,
            "rejection_reasons": rejection_reasons,
            "warnings": warnings,
            "guardrail": guardrail,
            "guardrail_status": guardrail.get("status", "PASS"),
            "guardrail_reasons": guardrail.get("reasons", []),
            "killzone": context["killzone"],
            "smt": context["smt"],
            "narrative": context["narrative"],
            "explanation": self.build_explanation(
                direction=direction,
                scores=scores,
                total_confidence=total_confidence,
                confidence_band=confidence_band,
                recommended_action=recommended_action,
                minimum_required=minimum_required,
                decision=decision,
                rejection_reasons=rejection_reasons,
            ),
        }
        logger.info("Completed confidence analysis for {}: {}", normalized_symbol, result)
        return result

    def calculate_scores(
        self,
        trend: dict[str, Any],
        liquidity: dict[str, Any],
        ict: dict[str, Any],
        context: dict[str, Any],
        direction: str | None,
    ) -> dict[str, int]:
        """Calculate weighted confidence components from engine outputs."""
        weights = self.rule_weights
        scores = {
            "daily_bias": 0,
            "h4_narrative": 0,
            "liquidity_sweep": 0,
            "mss": 0,
            "fvg_quality": 0,
            "session_quality": 0,
            "target_clarity": 0,
            "smt": 0,
        }

        if direction and trend.get("daily_bias") == direction:
            scores["daily_bias"] = int(weights["daily_bias"])
        if direction and trend.get("h4_bias") == direction:
            scores["h4_narrative"] = int(weights["h4_narrative"])
        if liquidity.get("latest_sweep"):
            scores["liquidity_sweep"] = int(weights["liquidity_sweep"])
        if ict.get("mss", {}).get("detected"):
            scores["mss"] = int(weights["mss"])
        if ict.get("fvg", {}).get("detected"):
            scores["fvg_quality"] = self.score_fvg_quality(ict["fvg"])
        scores["session_quality"] = self.score_session_quality(context)
        if self.has_clear_target(liquidity):
            scores["target_clarity"] = int(weights["target_clarity"])
        scores["smt"] = self.score_smt(context, direction)

        return scores

    @staticmethod
    def calculate_memory_advisory_scores(context: dict[str, Any], ict: dict[str, Any]) -> dict[str, Any]:
        """Return optional M5/M1 memory scores without changing production confidence."""
        memory = context.get("hierarchical_memory") or context.get("memory") or {}
        trigger = memory.get("trigger_memory", memory.get("trigger", {}))
        if not memory:
            return {
                "status": "NOT_PROVIDED",
                "m5_trigger_score": 0,
                "m1_precision_score": 0,
                "memory_alignment_score": 0,
                "production_score_impact": 0,
            }
        if not bool(ict.get("mss", {}).get("detected")):
            return {
                "status": "BLOCKED_NO_M15_STRUCTURE",
                "m5_trigger_score": 0,
                "m1_precision_score": 0,
                "memory_alignment_score": 0,
                "production_score_impact": 0,
            }
        m5_mss = trigger.get("m5_mss", {})
        m1_mss = trigger.get("m1_mss", {})
        trigger_fvg = trigger.get("trigger_fvg", {})
        m5_score = 10 if m5_mss.get("detected") else -5
        if float(m5_mss.get("displacement_score", 0) or 0) >= 75:
            m5_score += 5
        m1_score = 7 if m1_mss.get("detected") else -4
        if trigger_fvg.get("detected") and not trigger_fvg.get("mitigated"):
            m1_score += 3
        alignment_score = int(memory.get("confidence_integration", {}).get("memory_alignment_score", 0) or 0)
        return {
            "status": "ADVISORY_READY",
            "m5_trigger_score": max(min(m5_score, 15), -10),
            "m1_precision_score": max(min(m1_score, 10), -10),
            "memory_alignment_score": max(min(alignment_score, 15), -10),
            "production_score_impact": 0,
        }

    @staticmethod
    def calculate_total_confidence(scores: dict[str, int]) -> int:
        """Return total confidence capped to 100."""
        return max(min(sum(scores.values()), 100), 0)

    def evaluate_hard_rejections(
        self,
        symbol: str,
        trend: dict[str, Any],
        liquidity: dict[str, Any],
        ict: dict[str, Any],
        scores: dict[str, int],
        total_confidence: int,
        context: dict[str, Any],
        direction: str | None,
    ) -> list[str]:
        """Evaluate constitution hard rejection rules."""
        reasons: list[str] = []

        if not self.is_valid_killzone(context):
            reasons.append("Outside valid killzone")
        if not ict.get("mss", {}).get("detected"):
            reasons.append("MSS not confirmed")
        if not ict.get("fvg", {}).get("detected"):
            reasons.append("FVG not detected")
        if ict.get("premium_discount", {}).get("current_zone") == "unavailable":
            reasons.append("Premium/discount unavailable")

        mss_direction = ict.get("mss", {}).get("direction")
        fvg_direction = ict.get("fvg", {}).get("direction")
        order_block_direction = ict.get("order_block", {}).get("direction")

        if mss_direction and fvg_direction and fvg_direction != mss_direction:
            reasons.append("FVG direction not aligned with MSS")
        if mss_direction and order_block_direction and order_block_direction != mss_direction:
            reasons.append("Order block direction not aligned with MSS")
        if float(context.get("risk_reward", 0.0)) < float(self.trading_rules.get("hard_filters", {}).get("minimum_rr", 3.0)):
            reasons.append("RR below 3")
        news_status = context.get("news_status") or {}
        if context.get("high_impact_news_lock_active", False) or news_status.get("lock_active", False):
            reasons.append("High impact news lock active")
        if context.get("daily_loss_limit_hit", False):
            reasons.append("Daily loss limit hit")
        if context.get("max_trades_per_day_hit", False):
            reasons.append("Max trades per day hit")
        if self.is_forex_symbol(symbol):
            if direction and trend.get("daily_bias") != direction:
                reasons.append("Forex daily bias not aligned")
            if direction and trend.get("h4_bias") != direction:
                reasons.append("Forex 4H narrative not aligned")
            if not liquidity.get("latest_sweep"):
                reasons.append("Forex liquidity sweep missing")
            if ict.get("fvg", {}).get("grade") != "A":
                reasons.append("Forex requires A-grade setup")
        if total_confidence < self.get_minimum_confidence(symbol):
            reasons.append("Confidence below minimum threshold")
        if direction in {None, "neutral"}:
            reasons.append("No directional setup")

        guardrail = self.evaluate_guardrails(symbol=symbol, total_confidence=total_confidence, context=context)
        context["guardrail"] = guardrail
        reasons.extend(guardrail.get("reasons", []))

        return self.dedupe_reasons(reasons + list(ict.get("rejection_reasons", [])))

    def infer_direction(self, trend: dict[str, Any], ict: dict[str, Any]) -> str | None:
        """Infer final setup direction from MSS first, then trend context."""
        mss_direction = ict.get("mss", {}).get("direction")
        if mss_direction in {"bullish", "bearish"}:
            return mss_direction

        trend_direction = trend.get("overall_bias")
        if trend_direction in {"bullish", "bearish"}:
            return trend_direction

        return None

    def score_fvg_quality(self, fvg: dict[str, Any]) -> int:
        """Score FVG quality from grade."""
        if not fvg.get("detected"):
            return 0

        max_score = int(self.rule_weights["fvg_quality"])
        grade = fvg.get("grade")
        if grade == "A":
            return max_score
        if grade == "B":
            return round(max_score * 0.75)
        if grade == "C":
            return round(max_score * 0.5)
        return 0

    def score_session_quality(self, context: dict[str, Any]) -> int:
        """Score session quality from active killzone quality."""
        max_score = int(self.rule_weights["session_quality"])
        quality_score = int(self.get_killzone_status(context).get("quality_score", 0))
        if quality_score >= 10:
            return max_score
        if quality_score >= 7:
            return round(max_score * 0.7)
        return 0

    @staticmethod
    def score_smt(context: dict[str, Any], direction: str | None) -> int:
        """Return SMT confirmation bonus or conflict penalty."""
        smt = context.get("smt") or {}
        if not smt.get("smt_detected") or not direction:
            return 0
        confidence = int(smt.get("confidence", 0))
        if smt.get("direction") == direction:
            return confidence
        return -confidence

    def get_smt_status(self, symbol: str, context: dict[str, Any]) -> dict[str, Any]:
        """Return SMT status for a symbol without making SMT mandatory."""
        try:
            killzone = context.get("killzone") or {}
            return self.smt_analyzer.analyze_for_symbol(
                symbol,
                active_killzone=killzone.get("active_killzone"),
            )
        except (SMTAnalyzerError, ValueError) as exc:
            logger.warning("SMT analysis unavailable for {}: {}", symbol, exc)
            return SMTAnalyzer.no_smt_result(
                pair_name="none",
                primary=symbol,
                comparison="none",
                timeframe="M15",
                explanation=[f"SMT unavailable: {exc}"],
            )

    def get_narrative_status(
        self,
        symbol: str,
        *,
        context: dict[str, Any],
        trend: dict[str, Any],
        liquidity: dict[str, Any],
        ict: dict[str, Any],
    ) -> dict[str, Any]:
        """Return narrative status for guardrail checks."""
        try:
            return self.narrative_analyzer.analyze(
                symbol,
                context={
                    "analysis_time": context.get("analysis_time"),
                    "trend": trend,
                    "liquidity": liquidity,
                    "ict": ict,
                },
            )
        except (NarrativeAnalyzerError, ValueError) as exc:
            logger.warning("Narrative analysis unavailable for {}: {}", symbol, exc)
            return {
                "symbol": symbol,
                "phase": context.get("narrative_phase", "unknown"),
                "summary": f"Narrative unavailable: {exc}",
            }

    def evaluate_guardrails(
        self,
        *,
        symbol: str,
        total_confidence: int,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Evaluate strategy guardrails from current confidence context."""
        narrative = context.get("narrative") or {}
        news_status = context.get("news_status") or {}
        return self.strategy_guardrails.evaluate(
            symbol=symbol,
            total_confidence=total_confidence,
            killzone=context.get("killzone", {}),
            smt=context.get("smt", {}),
            narrative_phase=context.get("narrative_phase") or narrative.get("phase"),
            risk_blocked=bool(context.get("risk_blocked", False)),
            news_lock_active=bool(
                context.get("high_impact_news_lock_active", False) or news_status.get("lock_active", False)
            ),
        )

    @staticmethod
    def build_warnings(context: dict[str, Any], direction: str | None) -> list[str]:
        """Build non-hard warnings for confirmation conflicts."""
        smt = context.get("smt") or {}
        if smt.get("smt_detected") and direction and smt.get("direction") != direction:
            return ["SMT conflicts with setup direction"]
        return []

    def is_valid_killzone(self, context: dict[str, Any]) -> bool:
        """Return whether context has a valid active killzone."""
        killzone = self.get_killzone_status(context)
        return bool(killzone.get("is_valid", False)) and int(killzone.get("quality_score", 0)) > 0

    def get_killzone_status(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return cached or computed killzone status for context."""
        killzone = context.get("killzone")
        if killzone:
            return killzone
        symbol = context.get("symbol")
        if not symbol:
            return {}
        killzone = self.killzone_analyzer.analyze(
            str(symbol),
            current_time=context.get("analysis_time"),
        )
        context["killzone"] = killzone
        return killzone

    def is_valid_session(self, context: dict[str, Any]) -> bool:
        """Return whether analysis is inside an approved trading session."""
        if "killzone" in context:
            return self.is_valid_killzone(context)

        analysis_time = context.get("analysis_time")
        symbol = context.get("symbol")
        if not analysis_time or not symbol:
            return False

        if isinstance(analysis_time, str):
            analysis_time = datetime.fromisoformat(analysis_time)
        if analysis_time.tzinfo is None:
            analysis_time = analysis_time.replace(tzinfo=self.WAT_TIMEZONE)
        wat_time = analysis_time.astimezone(self.WAT_TIMEZONE)

        return self.is_time_in_configured_session(str(symbol).upper(), wat_time)

    @staticmethod
    def get_confidence_band(total_confidence: int) -> tuple[str, str]:
        """Return radar band and recommended action from total confidence."""
        band = production_band(total_confidence)
        return band.band, band.engine_action

    @staticmethod
    def is_time_in_session(symbol: str, wat_time: datetime) -> bool:
        """Return whether WAT time is inside Sentinel's approved sessions."""
        hour_minute = wat_time.hour * 60 + wat_time.minute
        sessions = {
            "XAUUSD": [(8 * 60, 11 * 60), (13 * 60 + 30, 16 * 60)],
            "US30": [(13 * 60 + 30, 16 * 60)],
            "EURUSD": [(8 * 60, 11 * 60), (13 * 60 + 30, 16 * 60)],
            "GBPUSD": [(8 * 60, 11 * 60), (13 * 60 + 30, 16 * 60)],
        }
        return any(start <= hour_minute <= end for start, end in sessions.get(symbol, []))

    def is_time_in_configured_session(self, symbol: str, wat_time: datetime) -> bool:
        """Return whether WAT time is inside configured approved sessions."""
        sessions = self.market_sessions.get(symbol.upper(), self.DEFAULT_SESSIONS.get(symbol.upper(), []))
        hour_minute = wat_time.hour * 60 + wat_time.minute
        return any(start <= hour_minute <= end for start, end in sessions)

    def get_minimum_confidence(self, symbol: str) -> int:
        """Return symbol-specific minimum confidence."""
        if self.is_forex_symbol(symbol):
            return int(self.trading_rules.get("markets", {}).get("forex", {}).get("minimum_confidence", 95))
        return int(self.rule_weights["minimum_confidence"])

    def is_forex_symbol(self, symbol: str) -> bool:
        """Return whether a symbol is a forex watchlist market."""
        return symbol.upper().strip() in self.forex_symbols

    @staticmethod
    def has_clear_target(liquidity: dict[str, Any]) -> bool:
        """Return whether liquidity analyzer has a clear directional target."""
        return bool(liquidity.get("nearest_buy_side_target") or liquidity.get("nearest_sell_side_target"))

    @staticmethod
    def dedupe_reasons(reasons: list[str]) -> list[str]:
        """Preserve-order de-duplication for rejection reasons."""
        deduped: list[str] = []
        for reason in reasons:
            if reason and reason not in deduped:
                deduped.append(reason)
        return deduped

    @staticmethod
    def build_explanation(
        direction: str | None,
        scores: dict[str, int],
        total_confidence: int,
        confidence_band: str,
        recommended_action: str,
        minimum_required: int,
        decision: str,
        rejection_reasons: list[str],
    ) -> list[str]:
        """Build human-readable confidence explanation lines."""
        explanation = [
            f"Final direction is {direction or 'none'}.",
            f"Total confidence is {total_confidence} with minimum required {minimum_required}.",
            f"Confidence band is {confidence_band}; recommended action is {recommended_action}.",
            f"Decision is {decision}.",
            f"Score breakdown: {scores}.",
        ]
        if rejection_reasons:
            explanation.append(f"Rejected because: {', '.join(rejection_reasons)}.")
        explanation.append("Advisor Mode only: no execution action was taken.")
        return explanation

    def _load_rule_weights(self) -> dict[str, Any]:
        config = self._load_yaml_file(self.config_dir / "rule_weights.yaml")
        mode_weights = config.get("modes", {}).get(self.mode, {})
        return {**self.DEFAULT_WEIGHTS, **mode_weights}

    def _load_trading_rules(self) -> dict[str, Any]:
        return self._load_yaml_file(self.config_dir / "trading_rules.yaml")

    def _load_market_sessions(self) -> dict[str, list[tuple[int, int]]]:
        config = self._load_yaml_file(self.config_dir / "market_sessions.yaml")
        markets = config.get("markets", {})
        sessions: dict[str, list[tuple[int, int]]] = {}
        for symbol, symbol_config in markets.items():
            symbol_sessions = []
            for session in symbol_config.get("sessions", {}).values():
                start = self._parse_hhmm(str(session.get("start", "00:00")))
                end = self._parse_hhmm(str(session.get("end", "00:00")))
                symbol_sessions.append((start, end))
            if symbol_sessions:
                sessions[str(symbol).upper().strip()] = symbol_sessions
        return sessions or self.DEFAULT_SESSIONS

    def _load_allowed_symbols(self) -> frozenset[str]:
        allowed = self.trading_rules.get("markets", {}).get("allowed", [])
        if not allowed:
            return self.DEFAULT_ALLOWED_SYMBOLS
        return frozenset(str(symbol).upper().strip() for symbol in allowed)

    def _load_forex_symbols(self) -> frozenset[str]:
        forex_symbols = self.trading_rules.get("markets", {}).get("forex", {}).get("symbols", [])
        if not forex_symbols:
            return self.DEFAULT_FOREX_SYMBOLS
        return frozenset(str(symbol).upper().strip() for symbol in forex_symbols)

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist.", path)
            return {}

        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise ConfidenceAnalyzerError(f"Failed to load config {path}: {exc}") from exc

    def _validate_symbol(self, symbol: str) -> str:
        normalized_symbol = symbol.upper().strip()
        if normalized_symbol not in self.allowed_symbols:
            supported = ", ".join(sorted(self.allowed_symbols))
            raise ValueError(f"Unsupported symbol '{symbol}'. Supported symbols: {supported}.")
        return normalized_symbol

    @staticmethod
    def _parse_hhmm(value: str) -> int:
        hour, minute = value.split(":", maxsplit=1)
        return int(hour) * 60 + int(minute)
