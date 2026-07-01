"""Market narrative engine for Project Sentinel Advisor Mode."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from loguru import logger

from backend.ict_engine.ict_analyzer import ICTAnalyzer, ICTAnalyzerError
from backend.liquidity_engine.liquidity_analyzer import LiquidityAnalyzer, LiquidityAnalyzerError
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.trend_engine.trend_analyzer import TrendAnalyzer, TrendAnalyzerError


class NarrativeAnalyzerError(RuntimeError):
    """Raised when narrative analysis cannot be completed."""


class NarrativeAnalyzer:
    """Combine trend, liquidity, ICT, and session context into a market story."""

    DEFAULT_ALLOWED_SYMBOLS = frozenset({"XAUUSD", "US30", "EURUSD", "GBPUSD"})
    DEFAULT_SESSIONS = {
        "XAUUSD": {"london": (8 * 60, 11 * 60), "new_york": (13 * 60 + 30, 16 * 60)},
        "US30": {"new_york": (13 * 60 + 30, 16 * 60)},
        "EURUSD": {"london": (8 * 60, 11 * 60), "new_york": (13 * 60 + 30, 16 * 60)},
        "GBPUSD": {"london": (8 * 60, 11 * 60), "new_york": (13 * 60 + 30, 16 * 60)},
    }
    ALLOWED_PHASES = frozenset({"expansion", "reversal", "range", "accumulation", "distribution"})
    WAT_TIMEZONE = ZoneInfo("Africa/Lagos")

    def __init__(
        self,
        connector: MT5Connector | None = None,
        trend_analyzer: TrendAnalyzer | None = None,
        liquidity_analyzer: LiquidityAnalyzer | None = None,
        ict_analyzer: ICTAnalyzer | None = None,
        config_dir: str | Path | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
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
        self.trading_rules = self._load_yaml_file(self.config_dir / "trading_rules.yaml")
        self.market_sessions = self._load_market_sessions()
        self.allowed_symbols = self._load_allowed_symbols()

    def analyze(self, symbol: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a structured Advisor Mode narrative for a supported symbol."""
        normalized_symbol = self._validate_symbol(symbol)
        context = context or {}
        analysis_time = self.normalize_analysis_time(context.get("analysis_time"))
        logger.info("Starting narrative analysis for {}.", normalized_symbol)

        try:
            trend = context.get("trend") or self.trend_analyzer.get_overall_bias(normalized_symbol)
            liquidity = context.get("liquidity") or self.liquidity_analyzer.analyze(normalized_symbol)
            ict = context.get("ict") or self.ict_analyzer.analyze(normalized_symbol)
        except (TrendAnalyzerError, LiquidityAnalyzerError, ICTAnalyzerError, MT5ConnectorError, ValueError) as exc:
            raise NarrativeAnalyzerError(f"Could not analyze narrative for {normalized_symbol}: {exc}") from exc

        active_session = self.get_active_session(normalized_symbol, analysis_time)
        swept_liquidity = self.get_swept_liquidity(liquidity)
        unswept_liquidity = self.get_unswept_liquidity(liquidity, swept_liquidity)
        current_zone = self.get_current_zone(ict, liquidity)
        likely_draw = self.select_likely_draw(trend, liquidity, ict, current_zone)
        phase = self.classify_phase(trend, liquidity, ict, likely_draw, current_zone=current_zone)
        bias = str(trend.get("daily_bias") or trend.get("overall_bias") or "neutral")
        summary = self.build_summary(
            swept_liquidity=swept_liquidity,
            current_zone=current_zone,
            phase=phase,
            likely_draw=likely_draw,
            bias=bias,
        )

        result = {
            "symbol": normalized_symbol,
            "bias": bias,
            "phase": phase,
            "swept_liquidity": swept_liquidity,
            "unswept_liquidity": unswept_liquidity,
            "current_zone": current_zone,
            "active_session": active_session,
            "likely_draw": likely_draw,
            "summary": summary,
            "explanation": self.build_explanation(
                trend=trend,
                liquidity=liquidity,
                ict=ict,
                active_session=active_session,
                phase=phase,
                likely_draw=likely_draw,
            ),
        }
        logger.info("Completed narrative analysis for {}: {}", normalized_symbol, result)
        return result

    @classmethod
    def classify_phase(
        cls,
        trend: dict[str, Any],
        liquidity: dict[str, Any],
        ict: dict[str, Any],
        likely_draw: str | None = None,
        current_zone: str | None = None,
    ) -> str:
        """Classify the current market phase from narrative components."""
        daily_bias = trend.get("daily_bias")
        h4_bias = trend.get("h4_bias")
        overall_bias = trend.get("overall_bias")
        h1_context = trend.get("h1_context")
        sweep = liquidity.get("latest_sweep")
        mss = ict.get("mss", {})
        zone = current_zone or cls.get_current_zone(ict, liquidity)
        mss_direction = mss.get("direction")
        mss_detected = bool(mss.get("detected"))
        displacement_score = float(mss.get("displacement_score", 0.0))

        if (
            daily_bias == h4_bias == overall_bias
            and overall_bias in {"bullish", "bearish"}
            and sweep
            and mss_detected
            and mss_direction == overall_bias
            and cls.has_external_target(liquidity, mss_direction)
        ):
            return "expansion"

        if (
            sweep
            and mss_detected
            and mss_direction in {"bullish", "bearish"}
            and overall_bias in {"bullish", "bearish"}
            and mss_direction != overall_bias
            and cls.is_zone_favorable(mss_direction, zone)
        ):
            return "reversal"

        if sweep and cls.is_swept_against_bias(sweep, overall_bias):
            if mss_detected and mss_direction != overall_bias and cls.is_zone_favorable(str(mss_direction), zone):
                return "reversal"
            return "distribution"

        if sweep and cls.is_rejection_distribution(sweep, zone, liquidity):
            return "distribution"

        liquidity_building = bool(liquidity.get("equal_highs") or liquidity.get("equal_lows"))
        low_delivery = not mss_detected and displacement_score < 25.0
        no_meaningful_sweep = not sweep or str(sweep.get("strength", "weak")) == "none"
        low_atr = cls.has_low_atr_context(trend, liquidity)
        if h1_context == "consolidation" and liquidity_building and low_delivery and no_meaningful_sweep and low_atr:
            return "accumulation"

        no_directional_bias = overall_bias in {None, "neutral", "range"}
        low_displacement = displacement_score < 40.0
        if no_directional_bias and not sweep and low_displacement:
            return "range"

        if likely_draw == cls.NO_DRAW:
            return "range"
        return "range"

    NO_DRAW = "No clear liquidity draw available."

    @staticmethod
    def get_swept_liquidity(liquidity: dict[str, Any]) -> list[str]:
        """Return human-readable swept liquidity labels."""
        sweep = liquidity.get("latest_sweep")
        if not sweep:
            return []

        side = NarrativeAnalyzer.human_side(str(sweep.get("side", "")))
        level_name = str(sweep.get("level_name") or "Unknown liquidity")
        return [f"{level_name} {side}".strip()]

    @staticmethod
    def get_unswept_liquidity(liquidity: dict[str, Any], swept_liquidity: list[str] | None = None) -> list[str]:
        """Return ranked unswept liquidity labels from remaining targets."""
        swept_names = {
            item.rsplit(" ", maxsplit=1)[0]
            for item in swept_liquidity or []
        }
        targets = NarrativeAnalyzer.rank_narrative_targets(liquidity)
        unswept: list[str] = []

        for target in targets:
            name = str(target.get("name", "Unknown liquidity"))
            if name in swept_names:
                continue
            label = f"{name} {NarrativeAnalyzer.human_side(str(target.get('side', '')))}".strip()
            if label not in unswept:
                unswept.append(label)
            if len(unswept) == 3:
                break

        return unswept

    @classmethod
    def get_current_zone(cls, ict: dict[str, Any], liquidity: dict[str, Any] | None = None) -> str:
        """Return premium, discount, equilibrium, or unavailable."""
        ict_zone = str(ict.get("premium_discount", {}).get("current_zone") or "unavailable")
        if ict_zone in {"premium", "discount", "equilibrium"}:
            return ict_zone
        if liquidity is None:
            return "unavailable"
        return cls.calculate_fallback_zone(liquidity)

    @classmethod
    def select_likely_draw(
        cls,
        trend: dict[str, Any],
        liquidity: dict[str, Any],
        ict: dict[str, Any],
        current_zone: str | None = None,
    ) -> str:
        """Select the likely draw on liquidity from MSS first, then trend and zone context."""
        current_zone = current_zone or cls.get_current_zone(ict)
        mss_direction = ict.get("mss", {}).get("direction")
        sweep = liquidity.get("latest_sweep") or {}

        if current_zone == "premium" and sweep.get("side") == "buy_side":
            return cls.format_target(cls.select_ranked_target_for_side(liquidity, "sell_side"))
        if current_zone == "discount" and sweep.get("side") == "sell_side":
            return cls.format_target(cls.select_ranked_target_for_side(liquidity, "buy_side"))

        if mss_direction == "bullish":
            return cls.format_target(cls.select_ranked_target_for_side(liquidity, "buy_side"))
        if mss_direction == "bearish":
            return cls.format_target(cls.select_ranked_target_for_side(liquidity, "sell_side"))

        overall_bias = trend.get("overall_bias") or trend.get("daily_bias")
        if overall_bias == "bullish":
            return cls.format_target(cls.select_ranked_target_for_side(liquidity, "buy_side"))
        if overall_bias == "bearish":
            return cls.format_target(cls.select_ranked_target_for_side(liquidity, "sell_side"))

        return cls.format_target(None)

    @classmethod
    def build_summary(
        cls,
        swept_liquidity: list[str],
        current_zone: str,
        phase: str,
        likely_draw: str,
        bias: str | None = None,
    ) -> str:
        """Build the human-readable market narrative summary."""
        sweep_text = cls.describe_sweep_for_summary(swept_liquidity)
        zone_text = (
            f"price is trading in {current_zone}"
            if current_zone != "unavailable"
            else "the dealing range is unclear"
        )
        intent_text = cls.describe_phase_for_summary(phase, swept_liquidity, bias)
        draw_text = (
            f"with {likely_draw} as the main draw."
            if likely_draw != cls.NO_DRAW
            else "until a cleaner liquidity draw forms."
        )
        return f"{sweep_text} and {zone_text}. {intent_text} {draw_text}"

    @staticmethod
    def build_explanation(
        trend: dict[str, Any],
        liquidity: dict[str, Any],
        ict: dict[str, Any],
        active_session: str,
        phase: str,
        likely_draw: str,
    ) -> list[str]:
        """Build human-readable reasoning lines for the narrative."""
        explanation = [
            f"Daily bias is {trend.get('daily_bias', 'neutral')}.",
            f"4H bias is {trend.get('h4_bias', 'range')} and 1H context is {trend.get('h1_context', 'unknown')}.",
            f"Latest sweep is {liquidity.get('latest_sweep') or 'none'}.",
            f"MSS detected: {ict.get('mss', {}).get('detected', False)} direction: {ict.get('mss', {}).get('direction')}.",
            f"Current zone is {NarrativeAnalyzer.get_current_zone(ict, liquidity)}.",
            f"Active session is {active_session}.",
            f"Market phase is {phase}.",
            f"Likely draw on liquidity is {likely_draw}.",
            "Advisor Mode only: no execution action was taken.",
        ]
        return explanation

    def get_active_session(self, symbol: str, analysis_time: datetime | None = None) -> str:
        """Return active configured session, asian context, or off_session."""
        normalized_symbol = self._validate_symbol(symbol)
        wat_time = self.normalize_analysis_time(analysis_time)
        minute_of_day = wat_time.hour * 60 + wat_time.minute

        for session_name, (start, end) in self.market_sessions.get(normalized_symbol, {}).items():
            if start <= minute_of_day <= end:
                return session_name

        if 0 <= minute_of_day < 7 * 60:
            return "asian"
        return "off_session"

    @classmethod
    def normalize_analysis_time(cls, analysis_time: Any | None) -> datetime:
        """Return analysis time normalized to WAT."""
        if analysis_time is None:
            return datetime.now(cls.WAT_TIMEZONE)
        if isinstance(analysis_time, str):
            analysis_time = datetime.fromisoformat(analysis_time)
        if analysis_time.tzinfo is None:
            analysis_time = analysis_time.replace(tzinfo=cls.WAT_TIMEZONE)
        return analysis_time.astimezone(cls.WAT_TIMEZONE)

    @staticmethod
    def is_zone_favorable(direction: str, zone: str) -> bool:
        """Return whether the current zone favors the direction."""
        return (direction == "bullish" and zone == "discount") or (direction == "bearish" and zone == "premium")

    @staticmethod
    def is_swept_against_bias(sweep: dict[str, Any], bias: Any) -> bool:
        """Return whether the sweep supports rejection against the current bias."""
        if bias == "bearish" and sweep.get("side") == "buy_side":
            return True
        return False

    @staticmethod
    def has_low_atr_context(trend: dict[str, Any], liquidity: dict[str, Any]) -> bool:
        """Return whether context explicitly indicates low volatility delivery."""
        tight_range = trend.get("h1_context") == "consolidation" or bool(liquidity.get("tight_range"))
        low_atr = bool(liquidity.get("low_atr")) or liquidity.get("atr_state") == "low"
        return tight_range and low_atr

    @staticmethod
    def calculate_fallback_zone(liquidity: dict[str, Any]) -> str:
        """Calculate premium/discount from PDH/PDL first, then weekly high/low."""
        current_price = liquidity.get("current_price")
        if current_price is None:
            return "unavailable"

        ranges = [
            (liquidity.get("pdh"), liquidity.get("pdl")),
            (liquidity.get("weekly_high"), liquidity.get("weekly_low")),
        ]
        for high, low in ranges:
            if high is None or low is None:
                continue
            range_high = float(max(high, low))
            range_low = float(min(high, low))
            if range_high <= range_low:
                continue
            equilibrium = (range_high + range_low) / 2
            price = float(current_price)
            if abs(price - equilibrium) <= max((range_high - range_low) * 0.01, 0.00001):
                return "equilibrium"
            if price > equilibrium:
                return "premium"
            return "discount"

        return "unavailable"

    @staticmethod
    def rank_narrative_targets(liquidity: dict[str, Any]) -> list[dict[str, Any]]:
        """Return narrative-ranked liquidity targets with external levels first."""
        targets = [dict(target) for target in liquidity.get("liquidity_priority", [])]
        current_price = liquidity.get("current_price")

        for name, price, side in (
            ("Asian High", liquidity.get("asian_high"), "buy_side"),
            ("Asian Low", liquidity.get("asian_low"), "sell_side"),
        ):
            if price is None:
                continue
            target = {
                "name": name,
                "price": float(price),
                "side": side,
                "classification": "external",
                "importance_score": 0.0,
            }
            if current_price is not None:
                target["distance_from_current_price"] = round(abs(float(price) - float(current_price)), 5)
            targets.append(target)

        unique_targets: dict[tuple[str, str], dict[str, Any]] = {}
        for target in targets:
            name = str(target.get("name", "Unknown liquidity"))
            side = str(target.get("side", ""))
            unique_targets[(name, side)] = target

        return sorted(unique_targets.values(), key=NarrativeAnalyzer.narrative_target_rank_key)

    @classmethod
    def select_ranked_target_for_side(cls, liquidity: dict[str, Any], side: str) -> dict[str, Any] | None:
        """Return the highest-quality directional target for a side."""
        current_price = liquidity.get("current_price")
        fallback = liquidity.get("nearest_buy_side_target") if side == "buy_side" else liquidity.get("nearest_sell_side_target")

        for target in cls.rank_narrative_targets(liquidity):
            if target.get("side") != side:
                continue
            if current_price is None:
                return target
            price = target.get("price")
            if price is None:
                return target
            if side == "buy_side" and float(price) >= float(current_price):
                return target
            if side == "sell_side" and float(price) <= float(current_price):
                return target

        return fallback

    @staticmethod
    def narrative_target_rank_key(target: dict[str, Any]) -> tuple[int, int, float, float]:
        """Sort target priority for narrative display."""
        name = str(target.get("name", ""))
        classification = str(target.get("classification", ""))
        if name in {"Weekly High", "Weekly Low"}:
            group = 0
            subtype = 0
        elif name in {"PDH", "PDL"}:
            group = 0
            subtype = 1
        elif name in {"Asian High", "Asian Low"}:
            group = 0
            subtype = 2
        elif classification == "engineered" or name.startswith(("EQH", "EQL", "Engineered Liquidity")):
            group = 1
            subtype = 0
        elif classification == "internal" or "Swing" in name:
            group = 2
            subtype = 0
        else:
            group = 3
            subtype = 0

        importance = -float(target.get("importance_score", 0.0))
        distance = float(target.get("distance_from_current_price", 0.0))
        return (group, subtype, importance, distance)

    @classmethod
    def is_rejection_distribution(cls, sweep: dict[str, Any], zone: str, liquidity: dict[str, Any]) -> bool:
        """Return whether a sweep implies distribution away from rejected liquidity."""
        if sweep.get("side") == "buy_side" and zone == "premium":
            return cls.has_external_target(liquidity, "bearish")
        if sweep.get("side") == "sell_side" and zone == "discount":
            return cls.has_external_target(liquidity, "bullish")
        return False

    @staticmethod
    def has_external_target(liquidity: dict[str, Any], direction: str | None) -> bool:
        """Return whether a clear external target exists in the requested direction."""
        if direction == "bullish":
            target = liquidity.get("nearest_buy_side_target")
        elif direction == "bearish":
            target = liquidity.get("nearest_sell_side_target")
        else:
            return False
        return bool(target and target.get("classification") == "external")

    @classmethod
    def format_target(cls, target: dict[str, Any] | None) -> str:
        """Return a human-readable target label or fallback."""
        if not target:
            return cls.NO_DRAW
        name = target.get("name", "Unknown liquidity")
        price = target.get("price")
        if price is None:
            return str(name)
        return f"{name} at {price}"

    @staticmethod
    def human_side(side: str) -> str:
        """Return readable buy-side/sell-side text."""
        if side == "buy_side":
            return "buy-side"
        if side == "sell_side":
            return "sell-side"
        return side.replace("_", "-")

    @staticmethod
    def describe_sweep_for_summary(swept_liquidity: list[str]) -> str:
        """Return analyst-style sweep text."""
        if not swept_liquidity:
            return "No meaningful liquidity sweep is confirmed"
        swept = swept_liquidity[0]
        if "buy-side" in swept:
            return "Buy-side liquidity has been swept"
        if "sell-side" in swept:
            return "Sell-side liquidity has been taken"
        return f"{swept} has been swept"

    @staticmethod
    def describe_phase_for_summary(phase: str, swept_liquidity: list[str], bias: str | None = None) -> str:
        """Return desk-commentary language for the classified phase."""
        swept_text = swept_liquidity[0] if swept_liquidity else ""
        if phase == "distribution":
            return "Market appears to be distributing toward sell-side liquidity"
        if phase == "accumulation":
            return "Market appears to be accumulating for expansion toward buy-side liquidity"
        if phase == "reversal":
            return "Market appears to be reversing away from the swept liquidity"
        if phase == "expansion":
            direction = "buy-side" if bias == "bullish" or "sell-side" in swept_text else "sell-side"
            return f"Market appears to be expanding toward {direction} liquidity"
        return "Market remains rotational until a cleaner delivery leg forms"

    def _load_allowed_symbols(self) -> frozenset[str]:
        allowed = self.trading_rules.get("markets", {}).get("allowed", [])
        if not allowed:
            return self.DEFAULT_ALLOWED_SYMBOLS
        return frozenset(str(symbol).upper().strip() for symbol in allowed)

    def _load_market_sessions(self) -> dict[str, dict[str, tuple[int, int]]]:
        config = self._load_yaml_file(self.config_dir / "market_sessions.yaml")
        markets = config.get("markets", {})
        sessions: dict[str, dict[str, tuple[int, int]]] = {}

        for symbol, symbol_config in markets.items():
            symbol_sessions: dict[str, tuple[int, int]] = {}
            for session_name, session in symbol_config.get("sessions", {}).items():
                start = self._parse_hhmm(str(session.get("start", "00:00")))
                end = self._parse_hhmm(str(session.get("end", "00:00")))
                symbol_sessions[str(session_name)] = (start, end)
            if symbol_sessions:
                sessions[str(symbol).upper().strip()] = symbol_sessions

        return sessions or self.DEFAULT_SESSIONS

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist.", path)
            return {}

        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise NarrativeAnalyzerError(f"Failed to load config {path}: {exc}") from exc

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
