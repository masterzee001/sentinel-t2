"""ICT liquidity analysis engine for Project Sentinel Advisor Mode."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yaml
from loguru import logger

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError


class LiquidityAnalyzerError(RuntimeError):
    """Raised when liquidity analysis cannot be completed."""


class LiquidityAnalyzer:
    """Detect ICT liquidity levels, sweeps, and sweep strength."""

    DEFAULT_ALLOWED_SYMBOLS = frozenset({"XAUUSD", "US30", "EURUSD", "GBPUSD"})
    WAT_TIMEZONE = ZoneInfo("Africa/Lagos")

    def __init__(
        self,
        connector: MT5Connector | None = None,
        config_dir: str | Path | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.connector = connector or MT5Connector()
        self.allowed_symbols = self._load_allowed_symbols()

    def analyze(self, symbol: str) -> dict[str, Any]:
        """Return a complete Advisor Mode liquidity assessment for a symbol."""
        normalized_symbol = self._validate_symbol(symbol)
        logger.info("Starting liquidity analysis for {}.", normalized_symbol)

        try:
            daily_candles = self._fetch_candles(normalized_symbol, "D1", count=30)
            weekly_candles = self._fetch_candles(normalized_symbol, "W1", count=10)
            h1_candles = self._fetch_candles(normalized_symbol, "H1", count=200)
            intraday_candles = self._fetch_candles(normalized_symbol, "M15", count=500)
        except (MT5ConnectorError, ValueError) as exc:
            raise LiquidityAnalyzerError(f"Could not analyze liquidity for {normalized_symbol}: {exc}") from exc

        previous_day = self.get_previous_day_snapshot(daily_candles)
        previous_week = self.get_previous_week_snapshot(weekly_candles)
        pdh, pdl = previous_day["high"], previous_day["low"]
        weekly_high, weekly_low = previous_week["high"], previous_week["low"]
        asian_range = self.get_asian_range(intraday_candles)
        atr = self.calculate_atr(h1_candles, period=14)
        threshold = atr * 0.10
        equal_highs = self.detect_equal_highs(intraday_candles, threshold)
        equal_lows = self.detect_equal_lows(intraday_candles, threshold)
        internal_swings = self.get_internal_swings(intraday_candles)
        current_price = float(intraday_candles["close"].iloc[-1])

        liquidity_classification = self.classify_liquidity(
            pdh=pdh,
            pdl=pdl,
            weekly_high=weekly_high,
            weekly_low=weekly_low,
            internal_swings=internal_swings,
            equal_highs=equal_highs,
            equal_lows=equal_lows,
        )
        liquidity_priority = self.rank_liquidity_targets(
            liquidity_classification,
            current_price,
            symbol=normalized_symbol,
        )
        directional_targets = self.infer_directional_targets(liquidity_priority, current_price)
        liquidity_levels = self.build_liquidity_levels(
            pdh,
            pdl,
            weekly_high,
            weekly_low,
            asian_range,
            equal_highs,
            equal_lows,
        )
        latest_sweep = self.detect_latest_sweep(intraday_candles, liquidity_levels, atr)
        sweep_strength = latest_sweep["strength"] if latest_sweep else "none"
        debug_metadata = self.build_level_debug_metadata(previous_day, previous_week)
        logger.info("Previous daily candle debug for {}: {}", normalized_symbol, previous_day)
        logger.info("Previous weekly candle debug for {}: {}", normalized_symbol, previous_week)

        result = {
            "symbol": normalized_symbol,
            "pdh": pdh,
            "pdl": pdl,
            "weekly_high": weekly_high,
            "weekly_low": weekly_low,
            "current_price": current_price,
            "asian_high": asian_range["high"],
            "asian_low": asian_range["low"],
            "equal_highs": equal_highs,
            "equal_lows": equal_lows,
            "liquidity_classification": liquidity_classification,
            "liquidity_priority": liquidity_priority,
            "nearest_buy_side_target": directional_targets["nearest_buy_side_target"],
            "nearest_sell_side_target": directional_targets["nearest_sell_side_target"],
            "latest_sweep": latest_sweep,
            "sweep_strength": sweep_strength,
            "debug_metadata": debug_metadata,
            "explanation": self.build_explanation(
                pdh=pdh,
                pdl=pdl,
                weekly_high=weekly_high,
                weekly_low=weekly_low,
                asian_range=asian_range,
                atr=atr,
                equal_highs=equal_highs,
                equal_lows=equal_lows,
                liquidity_classification=liquidity_classification,
                liquidity_priority=liquidity_priority,
                directional_targets=directional_targets,
                latest_sweep=latest_sweep,
            ),
        }
        logger.info("Completed liquidity analysis for {}: {}", normalized_symbol, result)
        return result

    @classmethod
    def get_previous_day_levels(cls, daily_candles: pd.DataFrame) -> tuple[float, float]:
        """Return PDH and PDL from the previous completed daily candle."""
        snapshot = cls.get_previous_day_snapshot(daily_candles)
        return snapshot["high"], snapshot["low"]

    @classmethod
    def get_previous_day_snapshot(cls, daily_candles: pd.DataFrame) -> dict[str, Any]:
        """Return time, high, and low from the previous completed daily candle."""
        candles = cls.normalize_candles(daily_candles)
        cls._validate_candle_frame(candles)

        if len(candles) < 2:
            raise ValueError("At least two daily candles are required to calculate PDH and PDL.")

        previous_day = candles.iloc[-2]
        return cls.build_level_snapshot(previous_day)

    @classmethod
    def get_previous_week_levels(cls, weekly_candles: pd.DataFrame) -> tuple[float, float]:
        """Return previous completed weekly high and low from MT5 W1 candles."""
        snapshot = cls.get_previous_week_snapshot(weekly_candles)
        return snapshot["high"], snapshot["low"]

    @classmethod
    def get_previous_week_snapshot(cls, weekly_candles: pd.DataFrame) -> dict[str, Any]:
        """Return time, high, and low from the previous completed MT5 W1 candle."""
        candles = cls.normalize_candles(weekly_candles)
        cls._validate_candle_frame(candles)

        if len(candles) < 2:
            raise ValueError("At least two weekly candles are required to calculate previous weekly levels.")

        previous_week = candles.iloc[-2]
        return cls.build_level_snapshot(previous_week)

    @staticmethod
    def build_level_snapshot(candle: pd.Series) -> dict[str, Any]:
        """Build a debug-friendly candle level snapshot."""
        return {
            "time": str(candle["time"]) if "time" in candle else None,
            "high": float(candle["high"]),
            "low": float(candle["low"]),
        }

    @staticmethod
    def build_level_debug_metadata(
        previous_day: dict[str, Any],
        previous_week: dict[str, Any],
    ) -> dict[str, Any]:
        """Build flat debug metadata for previous D1 and W1 candle levels."""
        return {
            "previous_daily_time": previous_day["time"],
            "previous_daily_high": previous_day["high"],
            "previous_daily_low": previous_day["low"],
            "previous_weekly_time": previous_week["time"],
            "previous_weekly_high": previous_week["high"],
            "previous_weekly_low": previous_week["low"],
        }

    @classmethod
    def get_asian_range(cls, intraday_candles: pd.DataFrame) -> dict[str, Any]:
        """Return Asian session high and low using 00:00-07:00 WAT."""
        candles = cls.normalize_candles(intraday_candles)
        cls._validate_candle_frame(candles)

        if "time" not in candles.columns:
            raise ValueError("Intraday candles must include a time column for Asian range detection.")

        candles = candles.copy()
        candles["wat_time"] = cls.to_wat_datetime(candles["time"])
        candles["wat_date"] = candles["wat_time"].dt.date
        candles["wat_clock"] = candles["wat_time"].dt.time

        for trading_date in sorted(candles["wat_date"].dropna().unique(), reverse=True):
            day_candles = candles[candles["wat_date"] == trading_date]
            asian_candles = day_candles[
                (day_candles["wat_time"].dt.hour >= 0)
                & (day_candles["wat_time"].dt.hour < 7)
            ]
            if not asian_candles.empty:
                return {
                    "date": str(trading_date),
                    "start": "00:00",
                    "end": "07:00",
                    "timezone": "WAT",
                    "high": float(asian_candles["high"].max()),
                    "low": float(asian_candles["low"].min()),
                }

        raise ValueError("No Asian session candles found for 00:00-07:00 WAT.")

    @classmethod
    def calculate_atr(cls, candles: pd.DataFrame, period: int = 14) -> float:
        """Calculate Average True Range from recent candles."""
        normalized = cls.normalize_candles(candles)
        cls._validate_candle_frame(normalized)

        if len(normalized) < 2:
            raise ValueError("At least two candles are required to calculate ATR.")

        previous_close = normalized["close"].shift(1)
        true_ranges = pd.concat(
            [
                normalized["high"] - normalized["low"],
                (normalized["high"] - previous_close).abs(),
                (normalized["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        atr = float(true_ranges.dropna().tail(period).mean())
        if atr <= 0:
            raise ValueError("ATR must be greater than zero.")
        return atr

    @classmethod
    def detect_equal_highs(cls, candles: pd.DataFrame, threshold: float) -> list[dict[str, Any]]:
        """Detect equal highs from swing highs within an ATR-based threshold."""
        normalized = cls.normalize_candles(candles)
        swing_highs, _ = cls.find_swings(normalized)
        return cls.cluster_equal_levels(swing_highs, threshold, level_type="equal_high")

    @classmethod
    def detect_equal_lows(cls, candles: pd.DataFrame, threshold: float) -> list[dict[str, Any]]:
        """Detect equal lows from swing lows within an ATR-based threshold."""
        normalized = cls.normalize_candles(candles)
        _, swing_lows = cls.find_swings(normalized)
        return cls.cluster_equal_levels(swing_lows, threshold, level_type="equal_low")

    @classmethod
    def get_internal_swings(cls, candles: pd.DataFrame, limit: int = 20) -> dict[str, list[dict[str, Any]]]:
        """Return recent internal swing highs and lows."""
        normalized = cls.normalize_candles(candles)
        swing_highs, swing_lows = cls.find_swings(normalized)
        ranked_highs, ranked_lows = cls.rank_internal_swings(normalized, swing_highs, swing_lows)
        return {
            "swing_highs": ranked_highs[: min(limit, 5)],
            "swing_lows": ranked_lows[: min(limit, 5)],
        }

    @staticmethod
    def rank_internal_swings(
        candles: pd.DataFrame,
        swing_highs: list[dict[str, Any]],
        swing_lows: list[dict[str, Any]],
        window: int = 3,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Rank internal swing highs and lows by local significance."""
        ranked_highs: list[dict[str, Any]] = []
        ranked_lows: list[dict[str, Any]] = []

        for swing in swing_highs:
            position = int(swing["position"])
            local = candles.iloc[max(0, position - window): min(len(candles), position + window + 1)]
            significance = float(swing["price"]) - float(local["low"].min())
            ranked_highs.append({**swing, "significance": round(significance, 5)})

        for swing in swing_lows:
            position = int(swing["position"])
            local = candles.iloc[max(0, position - window): min(len(candles), position + window + 1)]
            significance = float(local["high"].max()) - float(swing["price"])
            ranked_lows.append({**swing, "significance": round(significance, 5)})

        ranked_highs.sort(key=lambda item: (item["significance"], item["price"]), reverse=True)
        ranked_lows.sort(key=lambda item: (item["significance"], -item["price"]), reverse=True)
        return ranked_highs, ranked_lows

    @classmethod
    def classify_liquidity(
        cls,
        pdh: float,
        pdl: float,
        weekly_high: float,
        weekly_low: float,
        internal_swings: dict[str, list[dict[str, Any]]],
        equal_highs: list[dict[str, Any]],
        equal_lows: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        """Classify liquidity into external, internal, and engineered groups."""
        external = [
            {"name": "PDH", "price": pdh, "side": "buy_side", "classification": "external"},
            {"name": "PDL", "price": pdl, "side": "sell_side", "classification": "external"},
            {"name": "Weekly High", "price": weekly_high, "side": "buy_side", "classification": "external"},
            {"name": "Weekly Low", "price": weekly_low, "side": "sell_side", "classification": "external"},
        ]

        internal = [
            {
                "name": "Internal Swing High",
                "price": float(swing["price"]),
                "index": swing["index"],
                "significance": swing.get("significance", 0),
                "side": "buy_side",
                "classification": "internal",
            }
            for swing in internal_swings.get("swing_highs", [])
        ]
        internal.extend(
            {
                "name": "Internal Swing Low",
                "price": float(swing["price"]),
                "index": swing["index"],
                "significance": swing.get("significance", 0),
                "side": "sell_side",
                "classification": "internal",
            }
            for swing in internal_swings.get("swing_lows", [])
        )

        engineered: list[dict[str, Any]] = []
        for index, equal_high in enumerate(equal_highs, start=1):
            subtypes = ["equal_highs"]
            if equal_high["touches"] >= 3:
                subtypes.append("clustered_liquidity")
            engineered.append(
                {
                    "name": f"Engineered Liquidity {index}",
                    "price": equal_high["level"],
                    "side": "buy_side",
                    "touches": equal_high["touches"],
                    "strength_score": equal_high["strength_score"],
                    "classification": "engineered",
                    "subtypes": subtypes,
                }
            )

        for index, equal_low in enumerate(equal_lows, start=1):
            subtypes = ["equal_lows"]
            if equal_low["touches"] >= 3:
                subtypes.append("clustered_liquidity")
            engineered.append(
                {
                    "name": f"Engineered Liquidity {index + len(equal_highs)}",
                    "price": equal_low["level"],
                    "side": "sell_side",
                    "touches": equal_low["touches"],
                    "strength_score": equal_low["strength_score"],
                    "classification": "engineered",
                    "subtypes": subtypes,
                }
            )

        return {
            "external": external,
            "internal": internal,
            "engineered": engineered,
        }

    @staticmethod
    def rank_liquidity_targets(
        liquidity_classification: dict[str, list[dict[str, Any]]],
        current_price: float,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        """Rank all liquidity targets by trader-grade importance score."""
        base_scores = {
            "external": 300.0,
            "engineered": 200.0,
            "internal": 100.0,
        }
        minimum_distance = LiquidityAnalyzer.get_minimum_target_distance(symbol)
        ranked: list[dict[str, Any]] = []

        for classification, levels in liquidity_classification.items():
            for level in levels:
                distance = abs(float(level["price"]) - current_price)
                if minimum_distance is not None and distance < minimum_distance:
                    continue

                touch_bonus = min(float(level.get("touches", 1)) * 3.0, 15.0)
                significance_bonus = min(float(level.get("significance", 0)) * 0.20, 20.0)
                strength_bonus = min(float(level.get("strength_score", 0)) * 0.10, 20.0)
                clustered_bonus = 10.0 if "clustered_liquidity" in level.get("subtypes", []) else 0.0
                proximity_bonus = LiquidityAnalyzer.calculate_proximity_bonus(distance)
                importance_score = (
                    base_scores.get(classification, 50.0)
                    + strength_bonus
                    + proximity_bonus
                    + touch_bonus
                    + significance_bonus
                    + clustered_bonus
                )

                ranked.append(
                    {
                        "name": level["name"],
                        "price": float(level["price"]),
                        "side": level["side"],
                        "classification": classification,
                        "importance_score": round(max(importance_score, 0.0), 2),
                        "distance_from_current_price": round(distance, 5),
                    }
                )

        ranked.sort(key=lambda item: item["importance_score"], reverse=True)
        return ranked

    @staticmethod
    def get_minimum_target_distance(symbol: str | None) -> float | None:
        """Return market-specific minimum distance for liquidity targets."""
        if symbol is None:
            return None

        minimum_distances = {
            "XAUUSD": 5.0,
            "US30": 15.0,
            "EURUSD": 0.0005,
            "GBPUSD": 0.0005,
        }
        return minimum_distances.get(symbol.upper().strip())

    @staticmethod
    def calculate_proximity_bonus(distance: float) -> float:
        """Return a bounded proximity bonus without breaking class hierarchy."""
        if distance <= 0:
            return 0.0
        return round(min(30.0 / distance, 30.0), 2)

    @staticmethod
    def infer_directional_targets(
        liquidity_priority: list[dict[str, Any]],
        current_price: float,
    ) -> dict[str, dict[str, Any] | None]:
        """Infer nearest buy-side and sell-side liquidity targets from ranked levels."""
        buy_side_candidates = [
            target for target in liquidity_priority
            if target["side"] == "buy_side" and target["price"] >= current_price
        ]
        sell_side_candidates = [
            target for target in liquidity_priority
            if target["side"] == "sell_side" and target["price"] <= current_price
        ]

        nearest_buy_side = LiquidityAnalyzer._nearest_target(buy_side_candidates)
        nearest_sell_side = LiquidityAnalyzer._nearest_target(sell_side_candidates)

        return {
            "nearest_buy_side_target": nearest_buy_side,
            "nearest_sell_side_target": nearest_sell_side,
        }

    @staticmethod
    def _nearest_target(targets: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not targets:
            return None

        target = min(targets, key=lambda item: item["distance_from_current_price"])
        return {
            "name": target["name"],
            "price": target["price"],
            "distance": target["distance_from_current_price"],
            "classification": target["classification"],
            "importance_score": target["importance_score"],
        }

    @classmethod
    def detect_latest_sweep(
        cls,
        candles: pd.DataFrame,
        liquidity_levels: list[dict[str, Any]],
        atr: float,
        lookback: int = 40,
    ) -> dict[str, Any] | None:
        """Return the latest liquidity sweep, if one is present."""
        if not liquidity_levels:
            return None

        normalized = cls.normalize_candles(candles)
        cls._validate_candle_frame(normalized)
        recent = normalized.tail(lookback)

        latest_sweep: dict[str, Any] | None = None
        for row_position, (index, candle) in enumerate(recent.iterrows()):
            next_candle = recent.iloc[row_position + 1] if row_position + 1 < len(recent) else None

            for level in liquidity_levels:
                price = float(level["price"])
                side = level["side"]

                if side == "buy_side" and float(candle["high"]) > price and float(candle["close"]) < price:
                    latest_sweep = cls.build_sweep(candle, index, level, "buy_side_sweep", atr, next_candle)
                elif side == "sell_side" and float(candle["low"]) < price and float(candle["close"]) > price:
                    latest_sweep = cls.build_sweep(candle, index, level, "sell_side_sweep", atr, next_candle)

        return latest_sweep

    @classmethod
    def build_sweep(
        cls,
        candle: pd.Series,
        candle_index: Any,
        level: dict[str, Any],
        sweep_type: str,
        atr: float,
        next_candle: pd.Series | None = None,
    ) -> dict[str, Any]:
        """Build a structured sweep object with strength classification."""
        candle_range = float(candle["high"] - candle["low"])
        if candle_range <= 0:
            rejection_ratio = 0.0
        elif sweep_type == "buy_side_sweep":
            rejection_ratio = float(candle["high"] - candle["close"]) / candle_range
        else:
            rejection_ratio = float(candle["close"] - candle["low"]) / candle_range

        displacement = cls.has_displacement(candle, sweep_type, atr, next_candle)
        strength = "strong" if rejection_ratio >= 0.35 and displacement else "weak"

        return {
            "level_name": level["name"],
            "side": "buy_side" if sweep_type == "buy_side_sweep" else "sell_side",
            "strength": strength,
            "rejection_size": round(abs(float(candle["high"]) - float(candle["close"]))
                                    if sweep_type == "buy_side_sweep"
                                    else abs(float(candle["close"]) - float(candle["low"])), 5),
            "displacement_score": cls.calculate_displacement_score(candle, sweep_type, atr, next_candle),
            "type": sweep_type,
            "level_price": float(level["price"]),
            "liquidity_classification": level.get("classification", "unclassified"),
            "time": cls.format_candle_time(candle, candle_index),
            "rejection_ratio": round(rejection_ratio, 4),
        }

    @classmethod
    def calculate_displacement_score(
        cls,
        candle: pd.Series,
        sweep_type: str,
        atr: float,
        next_candle: pd.Series | None = None,
    ) -> float:
        """Score displacement away from a swept level from 0 to 100."""
        if atr <= 0:
            return 0.0

        candle_range = float(candle["high"] - candle["low"])
        open_price = float(candle.get("open", candle["close"]))
        close_price = float(candle["close"])
        body_size = abs(close_price - open_price)
        range_score = min((candle_range / atr) * 35.0, 35.0)
        body_score = min((body_size / atr) * 35.0, 35.0)
        continuation_score = 0.0

        if next_candle is not None:
            if sweep_type == "buy_side_sweep" and float(next_candle["close"]) < float(candle["low"]):
                continuation_score = 30.0
            elif sweep_type == "sell_side_sweep" and float(next_candle["close"]) > float(candle["high"]):
                continuation_score = 30.0

        return round(range_score + body_score + continuation_score, 2)

    @staticmethod
    def has_displacement(
        candle: pd.Series,
        sweep_type: str,
        atr: float,
        next_candle: pd.Series | None = None,
    ) -> bool:
        """Return whether a sweep has displacement away from the swept level."""
        candle_range = float(candle["high"] - candle["low"])
        open_price = float(candle.get("open", candle["close"]))
        close_price = float(candle["close"])
        body_size = abs(close_price - open_price)

        current_displacement = candle_range >= atr * 1.20 and body_size >= atr * 0.40

        if next_candle is None:
            if sweep_type == "buy_side_sweep":
                return current_displacement and close_price < open_price
            return current_displacement and close_price > open_price

        if sweep_type == "buy_side_sweep":
            next_displacement = float(next_candle["close"]) < float(candle["low"])
        else:
            next_displacement = float(next_candle["close"]) > float(candle["high"])

        return bool(current_displacement or next_displacement)

    @classmethod
    def build_liquidity_levels(
        cls,
        pdh: float,
        pdl: float,
        weekly_high: float,
        weekly_low: float,
        asian_range: dict[str, Any],
        equal_highs: list[dict[str, Any]],
        equal_lows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Create a flat list of liquidity levels for sweep detection."""
        levels = [
            {"name": "PDH", "price": pdh, "side": "buy_side", "classification": "external"},
            {"name": "PDL", "price": pdl, "side": "sell_side", "classification": "external"},
            {"name": "Weekly High", "price": weekly_high, "side": "buy_side", "classification": "external"},
            {"name": "Weekly Low", "price": weekly_low, "side": "sell_side", "classification": "external"},
            {"name": "Asian High", "price": asian_range["high"], "side": "buy_side", "classification": "internal"},
            {"name": "Asian Low", "price": asian_range["low"], "side": "sell_side", "classification": "internal"},
        ]

        for index, equal_high in enumerate(equal_highs, start=1):
            levels.append(
                {
                    "name": f"EQH {index}",
                    "price": equal_high["level"],
                    "side": "buy_side",
                    "classification": "engineered",
                }
            )
        for index, equal_low in enumerate(equal_lows, start=1):
            levels.append(
                {
                    "name": f"EQL {index}",
                    "price": equal_low["level"],
                    "side": "sell_side",
                    "classification": "engineered",
                }
            )

        return levels

    @staticmethod
    def cluster_equal_levels(
        swings: list[dict[str, Any]],
        threshold: float,
        level_type: str,
        min_touches: int = 3,
        min_spacing: int = 5,
        max_results: int = 3,
    ) -> list[dict[str, Any]]:
        """Cluster swing points where at least two prices sit within threshold."""
        if threshold <= 0 or len(swings) < min_touches:
            return []

        sorted_swings = sorted(swings, key=lambda swing: swing["price"])
        clusters: list[list[dict[str, Any]]] = []
        current_cluster = [sorted_swings[0]]

        for swing in sorted_swings[1:]:
            cluster_prices = [item["price"] for item in current_cluster]
            if abs(float(swing["price"]) - sum(cluster_prices) / len(cluster_prices)) <= threshold:
                current_cluster.append(swing)
            else:
                spaced_cluster = LiquidityAnalyzer.select_spaced_touches(current_cluster, min_spacing)
                if len(spaced_cluster) >= min_touches:
                    clusters.append(spaced_cluster)
                current_cluster = [swing]

        spaced_cluster = LiquidityAnalyzer.select_spaced_touches(current_cluster, min_spacing)
        if len(spaced_cluster) >= min_touches:
            clusters.append(spaced_cluster)

        results: list[dict[str, Any]] = []
        for cluster in clusters:
            prices = [float(item["price"]) for item in cluster]
            results.append(
                {
                    "type": level_type,
                    "level": round(sum(prices) / len(prices), 5),
                    "touches": len(cluster),
                    "threshold": round(threshold, 5),
                    "min_spacing": min_spacing,
                    "price_span": round(max(prices) - min(prices), 5),
                    "strength_score": LiquidityAnalyzer.score_equal_level_strength(cluster, threshold),
                    "prices": [round(price, 5) for price in prices],
                    "indices": [item["index"] for item in cluster],
                    "positions": [item["position"] for item in cluster],
                }
            )

        results.sort(key=lambda item: item["strength_score"], reverse=True)
        return results[:max_results]

    @staticmethod
    def select_spaced_touches(swings: list[dict[str, Any]], min_spacing: int) -> list[dict[str, Any]]:
        """Select touches that are at least min_spacing candles apart."""
        spaced: list[dict[str, Any]] = []
        for swing in sorted(swings, key=lambda item: item["position"]):
            if not spaced or int(swing["position"]) - int(spaced[-1]["position"]) >= min_spacing:
                spaced.append(swing)
        return spaced

    @staticmethod
    def score_equal_level_strength(cluster: list[dict[str, Any]], threshold: float) -> float:
        """Score equal high/low quality from touch count, spacing, and tightness."""
        prices = [float(item["price"]) for item in cluster]
        positions = [int(item["position"]) for item in cluster]
        price_span = max(prices) - min(prices)
        spacing_span = max(positions) - min(positions)
        tightness_score = max(0.0, 30.0 - (price_span / threshold * 30.0 if threshold else 30.0))
        touch_score = len(cluster) * 20.0
        spacing_score = min(spacing_span * 1.5, 30.0)
        return round(touch_score + spacing_score + tightness_score, 2)

    @staticmethod
    def find_swings(candles: pd.DataFrame, window: int = 1) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Find local swing highs and lows."""
        LiquidityAnalyzer._validate_candle_frame(candles)
        swing_highs: list[dict[str, Any]] = []
        swing_lows: list[dict[str, Any]] = []

        if len(candles) < window * 2 + 1:
            return swing_highs, swing_lows

        for position in range(window, len(candles) - window):
            high = float(candles["high"].iloc[position])
            low = float(candles["low"].iloc[position])
            left_highs = candles["high"].iloc[position - window:position]
            right_highs = candles["high"].iloc[position + 1:position + window + 1]
            left_lows = candles["low"].iloc[position - window:position]
            right_lows = candles["low"].iloc[position + 1:position + window + 1]

            index = candles.index[position]
            if high > float(left_highs.max()) and high > float(right_highs.max()):
                swing_highs.append({"index": index, "position": position, "price": high})
            if low < float(left_lows.min()) and low < float(right_lows.min()):
                swing_lows.append({"index": index, "position": position, "price": low})

        return swing_highs, swing_lows

    @staticmethod
    def normalize_candles(candles: pd.DataFrame) -> pd.DataFrame:
        """Return candles sorted by time when a time column is available."""
        normalized = candles.copy()
        if "time" in normalized.columns:
            normalized = normalized.sort_values("time").reset_index(drop=True)
        return normalized

    @classmethod
    def to_wat_datetime(cls, values: pd.Series) -> pd.Series:
        """Convert candle timestamps to WAT timezone."""
        datetimes = pd.to_datetime(values, utc=True)
        return datetimes.dt.tz_convert(cls.WAT_TIMEZONE)

    @staticmethod
    def format_candle_time(candle: pd.Series, candle_index: Any) -> str:
        """Return a stable string timestamp for a candle."""
        if "time" in candle:
            return str(candle["time"])
        return str(candle_index)

    @staticmethod
    def build_explanation(
        pdh: float,
        pdl: float,
        weekly_high: float,
        weekly_low: float,
        asian_range: dict[str, Any],
        atr: float,
        equal_highs: list[dict[str, Any]],
        equal_lows: list[dict[str, Any]],
        liquidity_classification: dict[str, list[dict[str, Any]]],
        liquidity_priority: list[dict[str, Any]],
        directional_targets: dict[str, dict[str, Any] | None],
        latest_sweep: dict[str, Any] | None,
    ) -> list[str]:
        """Build human-readable explanation lines for the liquidity decision."""
        explanation = [
            f"Previous Day High is {pdh}.",
            f"Previous Day Low is {pdl}.",
            f"Previous weekly high is {weekly_high} and previous weekly low is {weekly_low}.",
            f"Asian range ({asian_range['date']} 00:00-07:00 WAT) high is {asian_range['high']} and low is {asian_range['low']}.",
            f"H1 14-period ATR-based equal high/low threshold is {round(atr * 0.10, 5)}.",
            f"Detected {len(equal_highs)} equal-high cluster(s) and {len(equal_lows)} equal-low cluster(s).",
            "Liquidity classification counts: "
            f"{len(liquidity_classification['external'])} external, "
            f"{len(liquidity_classification['internal'])} internal, "
            f"{len(liquidity_classification['engineered'])} engineered.",
            f"Highest priority liquidity target is {liquidity_priority[0]['name']} if any priority target exists."
            if liquidity_priority else "No liquidity priority targets were ranked.",
        ]
        if directional_targets["nearest_buy_side_target"]:
            explanation.append(
                f"Nearest buy-side target is {directional_targets['nearest_buy_side_target']['name']}."
            )
        if directional_targets["nearest_sell_side_target"]:
            explanation.append(
                f"Nearest sell-side target is {directional_targets['nearest_sell_side_target']['name']}."
            )

        if latest_sweep:
            explanation.append(
                f"Latest sweep is {latest_sweep['type']} through {latest_sweep['level_name']} with {latest_sweep['strength']} strength."
            )
        else:
            explanation.append("No recent liquidity sweep detected.")

        explanation.append("Advisor Mode only: no execution action was taken.")
        return explanation

    def _fetch_candles(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        try:
            candles = self.connector.get_historical_candles(symbol, timeframe, count=count)
        except Exception as exc:
            raise LiquidityAnalyzerError(f"Failed to fetch {timeframe} candles for {symbol}: {exc}") from exc

        if candles.empty:
            raise LiquidityAnalyzerError(f"No {timeframe} candles returned for {symbol}.")
        return candles

    def _validate_symbol(self, symbol: str) -> str:
        normalized_symbol = symbol.upper().strip()
        if normalized_symbol not in self.allowed_symbols:
            supported = ", ".join(sorted(self.allowed_symbols))
            raise ValueError(f"Unsupported symbol '{symbol}'. Supported symbols: {supported}.")
        return normalized_symbol

    def _load_allowed_symbols(self) -> frozenset[str]:
        trading_rules_path = self.config_dir / "trading_rules.yaml"
        if not trading_rules_path.exists():
            logger.warning("Config file {} does not exist; using default markets.", trading_rules_path)
            return self.DEFAULT_ALLOWED_SYMBOLS

        try:
            with trading_rules_path.open("r", encoding="utf-8") as file:
                config = yaml.safe_load(file) or {}
        except Exception as exc:
            raise LiquidityAnalyzerError(f"Failed to load config {trading_rules_path}: {exc}") from exc

        allowed = config.get("markets", {}).get("allowed", [])
        if not allowed:
            logger.warning("No allowed markets found in {}; using defaults.", trading_rules_path)
            return self.DEFAULT_ALLOWED_SYMBOLS

        return frozenset(str(symbol).upper().strip() for symbol in allowed)

    @staticmethod
    def _validate_candle_frame(candles: pd.DataFrame) -> None:
        required_columns = {"open", "high", "low", "close"}
        missing_columns = required_columns.difference(candles.columns)
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"Candle data is missing required columns: {missing}.")
        if candles.empty:
            raise ValueError("Candle data cannot be empty.")
