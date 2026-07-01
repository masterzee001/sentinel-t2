"""SMT divergence analysis engine for Project Sentinel Advisor Mode."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from loguru import logger

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError


class SMTAnalyzerError(RuntimeError):
    """Raised when SMT divergence analysis cannot be completed."""


class SMTAnalyzer:
    """Compare correlated symbols for ICT SMT divergence."""

    DEFAULT_ALLOWED_SYMBOLS = frozenset({"XAUUSD", "US30", "EURUSD", "GBPUSD", "BTCUSD", "NAS100"})
    DEFAULT_CONFIG = {
        "enabled": True,
        "pairs": [],
    }

    def __init__(self, connector: MT5Connector | None = None, config_dir: str | Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.connector = connector or MT5Connector()
        self.config = self._load_config()
        self.pairs = list(self.config.get("pairs", []))

    def analyze_pair(self, pair_name: str, timeframe: str = "M15") -> dict[str, Any]:
        """Analyze a configured SMT pair using live candles."""
        pair = self.get_pair(pair_name)
        if pair is None:
            raise ValueError(f"Unknown SMT pair '{pair_name}'.")
        return self.analyze_pair_config(pair, timeframe=timeframe)

    def analyze_all(self, timeframe: str = "M15") -> list[dict[str, Any]]:
        """Analyze all configured SMT pairs."""
        if not bool(self.config.get("enabled", True)):
            return []
        return [self.analyze_pair_config(pair, timeframe=timeframe) for pair in self.pairs]

    def analyze_for_symbol(
        self,
        symbol: str,
        timeframe: str = "M15",
        active_killzone: str | None = None,
    ) -> dict[str, Any]:
        """Return the highest-confidence SMT result where symbol is the primary market."""
        normalized_symbol = self._validate_symbol(symbol)
        candidates = [
            pair for pair in self.pairs
            if str(pair.get("primary", "")).upper().strip() == normalized_symbol
        ]
        if active_killzone:
            preferred = [
                pair for pair in candidates
                if active_killzone in pair.get("preferred_sessions", [])
            ]
            if preferred:
                candidates = preferred

        if not candidates:
            return self.no_smt_result(
                pair_name="none",
                primary=normalized_symbol,
                comparison="none",
                timeframe=timeframe,
                explanation=[f"No configured SMT pair uses {normalized_symbol} as primary."],
            )

        results = [self.analyze_pair_config(pair, timeframe=timeframe) for pair in candidates]
        detected = [result for result in results if result.get("smt_detected")]
        if detected:
            return max(detected, key=lambda result: int(result.get("confidence", 0)))
        return results[0]

    def analyze_pair_config(self, pair: dict[str, Any], timeframe: str = "M15") -> dict[str, Any]:
        """Analyze a pair config using fetched candles."""
        primary = self._validate_symbol(str(pair.get("primary", "")))
        comparison = self._validate_symbol(str(pair.get("comparison", "")))
        try:
            primary_candles = self._fetch_candles(primary, timeframe, count=80)
            comparison_candles = self._fetch_candles(comparison, timeframe, count=80)
        except (MT5ConnectorError, ValueError) as exc:
            raise SMTAnalyzerError(f"Could not analyze SMT pair {pair.get('name')}: {exc}") from exc

        return self.detect_smt(
            primary_candles=primary_candles,
            comparison_candles=comparison_candles,
            pair_name=str(pair.get("name", f"{primary}_{comparison}")),
            primary=primary,
            comparison=comparison,
            timeframe=timeframe,
            weight=int(pair.get("weight", 0)),
        )

    @classmethod
    def detect_smt(
        cls,
        *,
        primary_candles: pd.DataFrame,
        comparison_candles: pd.DataFrame,
        pair_name: str,
        primary: str,
        comparison: str,
        timeframe: str = "M15",
        weight: int = 0,
        lookback: int = 50,
        swing_window: int = 2,
    ) -> dict[str, Any]:
        """Detect bullish or bearish SMT divergence from recent swings."""
        primary_recent = cls.prepare_candles(primary_candles, lookback)
        comparison_recent = cls.prepare_candles(comparison_candles, lookback)
        if primary_recent is None or comparison_recent is None:
            return cls.no_smt_result(
                pair_name=pair_name,
                primary=primary,
                comparison=comparison,
                timeframe=timeframe,
                explanation=["Insufficient candle data for SMT analysis."],
            )

        primary_highs, primary_lows = cls.find_swings(primary_recent, swing_window)
        comparison_highs, comparison_lows = cls.find_swings(comparison_recent, swing_window)
        if min(len(primary_highs), len(primary_lows), len(comparison_highs), len(comparison_lows)) < 2:
            return cls.no_smt_result(
                pair_name=pair_name,
                primary=primary,
                comparison=comparison,
                timeframe=timeframe,
                explanation=["Insufficient swing points for SMT analysis."],
            )

        primary_higher_high = primary_highs[-1]["price"] > primary_highs[-2]["price"]
        comparison_higher_high = comparison_highs[-1]["price"] > comparison_highs[-2]["price"]
        primary_lower_low = primary_lows[-1]["price"] < primary_lows[-2]["price"]
        comparison_lower_low = comparison_lows[-1]["price"] < comparison_lows[-2]["price"]

        bearish = primary_higher_high and not comparison_higher_high
        bullish = primary_lower_low and not comparison_lower_low
        if bearish and bullish:
            if primary_highs[-1]["position"] >= primary_lows[-1]["position"]:
                bullish = False
            else:
                bearish = False

        if bearish:
            return {
                "pair_name": pair_name,
                "primary": primary,
                "comparison": comparison,
                "timeframe": timeframe,
                "smt_detected": True,
                "direction": "bearish",
                "primary_event": "higher_high",
                "comparison_event": "failed_higher_high",
                "confidence": weight,
                "explanation": [
                    f"{primary} made a higher high while {comparison} failed to confirm.",
                    "Potential buy-side raid/manipulation; bearish reversal possibility.",
                    "Advisor Mode only: no execution action was taken.",
                ],
            }

        if bullish:
            return {
                "pair_name": pair_name,
                "primary": primary,
                "comparison": comparison,
                "timeframe": timeframe,
                "smt_detected": True,
                "direction": "bullish",
                "primary_event": "lower_low",
                "comparison_event": "failed_lower_low",
                "confidence": weight,
                "explanation": [
                    f"{primary} made a lower low while {comparison} failed to confirm.",
                    "Potential sell-side raid/manipulation; bullish reversal possibility.",
                    "Advisor Mode only: no execution action was taken.",
                ],
            }

        return cls.no_smt_result(
            pair_name=pair_name,
            primary=primary,
            comparison=comparison,
            timeframe=timeframe,
            explanation=[
                f"No SMT divergence detected between {primary} and {comparison}.",
                "Advisor Mode only: no execution action was taken.",
            ],
        )

    @staticmethod
    def prepare_candles(candles: pd.DataFrame, lookback: int) -> pd.DataFrame | None:
        """Return recent candles if enough valid data exists."""
        required_columns = {"high", "low"}
        if candles.empty or not required_columns.issubset(candles.columns) or len(candles) < lookback:
            return None
        normalized = candles.copy()
        if "time" in normalized.columns:
            normalized = normalized.sort_values("time")
        return normalized.tail(lookback).reset_index(drop=True)

    @staticmethod
    def find_swings(candles: pd.DataFrame, window: int = 2) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Find swing highs and lows using left/right window confirmation."""
        swing_highs: list[dict[str, Any]] = []
        swing_lows: list[dict[str, Any]] = []
        if len(candles) < window * 2 + 1:
            return swing_highs, swing_lows

        for position in range(window, len(candles) - window):
            high = float(candles["high"].iloc[position])
            low = float(candles["low"].iloc[position])
            left_high = float(candles["high"].iloc[position - window:position].max())
            right_high = float(candles["high"].iloc[position + 1:position + window + 1].max())
            left_low = float(candles["low"].iloc[position - window:position].min())
            right_low = float(candles["low"].iloc[position + 1:position + window + 1].min())
            if high > left_high and high > right_high:
                swing_highs.append({"position": position, "price": high})
            if low < left_low and low < right_low:
                swing_lows.append({"position": position, "price": low})
        return swing_highs, swing_lows

    @staticmethod
    def no_smt_result(
        *,
        pair_name: str,
        primary: str,
        comparison: str,
        timeframe: str,
        explanation: list[str],
    ) -> dict[str, Any]:
        """Return the standard no-SMT result."""
        return {
            "pair_name": pair_name,
            "primary": primary,
            "comparison": comparison,
            "timeframe": timeframe,
            "smt_detected": False,
            "direction": None,
            "primary_event": None,
            "comparison_event": None,
            "confidence": 0,
            "explanation": explanation,
        }

    @staticmethod
    def format_summary(smt: dict[str, Any]) -> str:
        """Return command-center friendly SMT text."""
        if not smt.get("smt_detected"):
            return "none"
        direction = str(smt.get("direction", "")).title()
        return f"{direction} divergence {smt.get('primary')} vs {smt.get('comparison')}"

    def get_pair(self, pair_name: str) -> dict[str, Any] | None:
        """Return pair config by name."""
        for pair in self.pairs:
            if str(pair.get("name")) == pair_name:
                return pair
        return None

    def _fetch_candles(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        try:
            candles = self.connector.get_historical_candles(symbol, timeframe, count=count)
        except Exception as exc:
            raise SMTAnalyzerError(f"Failed to fetch {timeframe} candles for {symbol}: {exc}") from exc
        if candles.empty:
            raise SMTAnalyzerError(f"No {timeframe} candles returned for {symbol}.")
        return candles

    def _validate_symbol(self, symbol: str) -> str:
        normalized_symbol = symbol.upper().strip()
        if normalized_symbol not in self.DEFAULT_ALLOWED_SYMBOLS:
            supported = ", ".join(sorted(self.DEFAULT_ALLOWED_SYMBOLS))
            raise ValueError(f"Unsupported symbol '{symbol}'. Supported symbols: {supported}.")
        return normalized_symbol

    def _load_config(self) -> dict[str, Any]:
        config_path = self.config_dir / "smt_pairs.yaml"
        if not config_path.exists():
            logger.warning("Config file {} does not exist; using SMT defaults.", config_path)
            return dict(self.DEFAULT_CONFIG)
        try:
            with config_path.open("r", encoding="utf-8") as file:
                config = yaml.safe_load(file) or {}
        except Exception as exc:
            raise SMTAnalyzerError(f"Failed to load config {config_path}: {exc}") from exc
        return {**self.DEFAULT_CONFIG, **config}
