"""Hybrid ICT trend analysis engine for Project Sentinel Advisor Mode."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError


class TrendAnalyzerError(RuntimeError):
    """Raised when trend analysis cannot be completed."""


class TrendAnalyzer:
    """Analyze daily, 4H, and 1H trend context for supported Sentinel markets."""

    DEFAULT_ALLOWED_SYMBOLS = frozenset({"XAUUSD", "US30", "EURUSD", "GBPUSD"})
    DEFAULT_WEIGHTS = {
        "daily_bias": 15,
        "h4_narrative": 20,
        "minimum_confidence": 90,
    }

    def __init__(
        self,
        connector: MT5Connector | None = None,
        config_dir: str | Path | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.connector = connector or MT5Connector()
        self.allowed_symbols = self._load_allowed_symbols()
        self.rule_weights = self._load_rule_weights()

    def get_daily_bias(self, symbol: str) -> str:
        """Return daily directional bias for a supported symbol."""
        normalized_symbol = self._validate_symbol(symbol)
        candles = self._fetch_candles(normalized_symbol, "D1", count=120)
        bias = self.determine_daily_bias(candles)
        logger.info("Daily bias for {}: {}", normalized_symbol, bias)
        return bias

    def get_4h_bias(self, symbol: str) -> str:
        """Return 4H structure bias for a supported symbol."""
        normalized_symbol = self._validate_symbol(symbol)
        candles = self._fetch_candles(normalized_symbol, "H4", count=100)
        bias = self.determine_h4_bias(candles)
        logger.info("4H bias for {}: {}", normalized_symbol, bias)
        return bias

    def get_1h_context(self, symbol: str) -> str:
        """Return 1H context as expansion, retracement, or consolidation."""
        normalized_symbol = self._validate_symbol(symbol)
        candles = self._fetch_candles(normalized_symbol, "H1", count=80)
        context = self.determine_h1_context(candles)
        logger.info("1H context for {}: {}", normalized_symbol, context)
        return context

    def get_overall_bias(self, symbol: str) -> dict[str, Any]:
        """Return a structured Advisor Mode trend assessment."""
        normalized_symbol = self._validate_symbol(symbol)
        logger.info("Starting trend analysis for {}.", normalized_symbol)

        try:
            daily_bias = self.get_daily_bias(normalized_symbol)
            h4_bias = self.get_4h_bias(normalized_symbol)
            h1_context = self.get_1h_context(normalized_symbol)
        except (MT5ConnectorError, ValueError) as exc:
            raise TrendAnalyzerError(f"Could not analyze trend for {normalized_symbol}: {exc}") from exc

        overall_bias = self.determine_overall_bias(daily_bias, h4_bias)
        confidence = self.calculate_confidence(daily_bias, h4_bias, h1_context, overall_bias)
        explanation = self.build_explanation(daily_bias, h4_bias, h1_context, overall_bias, confidence)

        result = {
            "symbol": normalized_symbol,
            "daily_bias": daily_bias,
            "h4_bias": h4_bias,
            "h1_context": h1_context,
            "overall_bias": overall_bias,
            "confidence": confidence,
            "explanation": explanation,
        }
        logger.info("Completed trend analysis for {}: {}", normalized_symbol, result)
        return result

    @classmethod
    def determine_daily_bias(cls, candles: pd.DataFrame) -> str:
        """Classify daily bias from recent swing highs and swing lows."""
        cls._validate_candle_frame(candles)
        swing_highs, swing_lows = cls._find_swings(candles, window=1)

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return "neutral"

        previous_high, latest_high = swing_highs[-2][1], swing_highs[-1][1]
        previous_low, latest_low = swing_lows[-2][1], swing_lows[-1][1]

        if latest_high > previous_high and latest_low > previous_low:
            return "bullish"
        if latest_high < previous_high and latest_low < previous_low:
            return "bearish"
        return "neutral"

    @classmethod
    def determine_h4_bias(cls, candles: pd.DataFrame) -> str:
        """Classify 4H bias from recent structure breaks."""
        cls._validate_candle_frame(candles)
        if len(candles) < 20:
            return "range"

        prior_candles = candles.iloc[-21:-1]
        latest_close = float(candles["close"].iloc[-1])
        prior_high = float(prior_candles["high"].max())
        prior_low = float(prior_candles["low"].min())

        if latest_close > prior_high:
            return "bullish"
        if latest_close < prior_low:
            return "bearish"
        return "range"

    @classmethod
    def determine_h1_context(cls, candles: pd.DataFrame) -> str:
        """Classify 1H context from range expansion, pullback, or compression."""
        cls._validate_candle_frame(candles)
        if len(candles) < 25:
            return "consolidation"

        ranges = candles["high"] - candles["low"]
        latest_range = float(ranges.iloc[-1])
        baseline_range = float(ranges.iloc[-25:-5].mean())
        recent_range = float(ranges.iloc[-5:].mean())

        if baseline_range > 0 and latest_range >= baseline_range * 1.5:
            return "expansion"
        if baseline_range > 0 and recent_range <= baseline_range * 0.7:
            return "consolidation"

        close_now = float(candles["close"].iloc[-1])
        close_4_bars_ago = float(candles["close"].iloc[-4])
        close_20_bars_ago = float(candles["close"].iloc[-20])
        broader_move = close_4_bars_ago - close_20_bars_ago
        recent_move = close_now - close_4_bars_ago

        if broader_move > 0 and recent_move < 0:
            return "retracement"
        if broader_move < 0 and recent_move > 0:
            return "retracement"
        return "consolidation"

    @staticmethod
    def determine_overall_bias(daily_bias: str, h4_bias: str) -> str:
        """Combine daily and 4H bias into one Advisor Mode directional bias."""
        if daily_bias == h4_bias and daily_bias in {"bullish", "bearish"}:
            return daily_bias
        if daily_bias in {"bullish", "bearish"} and h4_bias == "range":
            return daily_bias
        if daily_bias == "neutral" and h4_bias in {"bullish", "bearish"}:
            return h4_bias
        return "neutral"

    def calculate_confidence(
        self,
        daily_bias: str,
        h4_bias: str,
        h1_context: str,
        overall_bias: str,
    ) -> int:
        """Calculate a transparent trend confidence score from config weights."""
        confidence = 0

        if daily_bias in {"bullish", "bearish"}:
            confidence += int(self.rule_weights.get("daily_bias", 15))
        if h4_bias in {"bullish", "bearish"}:
            confidence += int(self.rule_weights.get("h4_narrative", 20))
        if daily_bias == h4_bias and overall_bias in {"bullish", "bearish"}:
            confidence += 35
        if h1_context == "retracement":
            confidence += 20
        elif h1_context == "expansion":
            confidence += 15
        elif h1_context == "consolidation":
            confidence += 5
        if overall_bias in {"bullish", "bearish"}:
            confidence += 10

        return min(confidence, 100)

    @staticmethod
    def build_explanation(
        daily_bias: str,
        h4_bias: str,
        h1_context: str,
        overall_bias: str,
        confidence: int,
    ) -> list[str]:
        """Build human-readable explanation lines for the trend decision."""
        explanation = [
            f"Daily bias is {daily_bias} based on recent swing structure.",
            f"4H bias is {h4_bias} based on recent structure break conditions.",
            f"1H context is {h1_context} based on range and pullback behavior.",
            f"Overall bias is {overall_bias}.",
            f"Trend confidence is {confidence}.",
            "Advisor Mode only: no execution action was taken.",
        ]
        return explanation

    def _fetch_candles(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        try:
            candles = self.connector.get_historical_candles(symbol, timeframe, count=count)
        except Exception as exc:
            raise TrendAnalyzerError(f"Failed to fetch {timeframe} candles for {symbol}: {exc}") from exc

        if candles.empty:
            raise TrendAnalyzerError(f"No {timeframe} candles returned for {symbol}.")
        return candles

    def _validate_symbol(self, symbol: str) -> str:
        normalized_symbol = symbol.upper().strip()
        if normalized_symbol not in self.allowed_symbols:
            supported = ", ".join(sorted(self.allowed_symbols))
            raise ValueError(f"Unsupported symbol '{symbol}'. Supported symbols: {supported}.")
        return normalized_symbol

    def _load_allowed_symbols(self) -> frozenset[str]:
        trading_rules_path = self.config_dir / "trading_rules.yaml"
        config = self._load_yaml_like_config(trading_rules_path)
        allowed = config.get("markets", {}).get("allowed", []) if isinstance(config, dict) else []
        if isinstance(allowed, dict) and "allowed" in allowed:
            allowed = allowed["allowed"]

        if not allowed:
            logger.warning("No allowed markets found in {}; using defaults.", trading_rules_path)
            return self.DEFAULT_ALLOWED_SYMBOLS

        return frozenset(str(symbol).upper().strip() for symbol in allowed)

    def _load_rule_weights(self) -> dict[str, Any]:
        rule_weights_path = self.config_dir / "rule_weights.yaml"
        config = self._load_yaml_like_config(rule_weights_path)
        modes = config.get("modes", {}) if isinstance(config, dict) else {}
        balanced = modes.get("balanced", {}) if isinstance(modes, dict) else {}

        if not balanced:
            logger.warning("No balanced rule weights found in {}; using defaults.", rule_weights_path)
            return dict(self.DEFAULT_WEIGHTS)

        return {**self.DEFAULT_WEIGHTS, **balanced}

    @staticmethod
    def _load_yaml_like_config(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist.", path)
            return {}

        try:
            import yaml  # type: ignore

            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except ModuleNotFoundError:
            logger.warning("PyYAML is not installed; using lightweight parser for {}.", path)
            return TrendAnalyzer._parse_simple_yaml(path)
        except Exception as exc:
            raise TrendAnalyzerError(f"Failed to load config {path}: {exc}") from exc

    @staticmethod
    def _parse_simple_yaml(path: Path) -> dict[str, Any]:
        """Parse the limited YAML shape used by Sentinel configs when PyYAML is absent."""
        result: dict[str, Any] = {}
        stack: list[tuple[int, Any, dict[str, Any] | None, str | None]] = [(-1, result, None, None)]

        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line_without_comment = raw_line.split("#", 1)[0].rstrip()
            if not line_without_comment.strip():
                continue

            indent = len(line_without_comment) - len(line_without_comment.lstrip(" "))
            line = line_without_comment.strip()

            while stack and indent <= stack[-1][0]:
                stack.pop()

            current = stack[-1][1]

            if line.startswith("- "):
                if isinstance(current, dict):
                    _, _, parent, parent_key = stack[-1]
                    if parent is None or parent_key is None:
                        continue
                    new_list: list[Any] = []
                    parent[parent_key] = new_list
                    stack[-1] = (stack[-1][0], new_list, parent, parent_key)
                    current = new_list
                if isinstance(current, list):
                    current.append(TrendAnalyzer._parse_scalar(line[2:].strip()))
                continue

            if ":" not in line:
                continue

            key, raw_value = line.split(":", 1)
            key = key.strip()
            raw_value = raw_value.strip()

            if raw_value == "":
                nested: dict[str, Any] = {}
                current[key] = nested
                stack.append((indent, nested, current, key))
            else:
                current[key] = TrendAnalyzer._parse_scalar(raw_value)

        return result

    @staticmethod
    def _parse_scalar(value: str) -> Any:
        cleaned = value.strip().strip('"').strip("'")
        if cleaned.lower() == "true":
            return True
        if cleaned.lower() == "false":
            return False
        try:
            return int(cleaned)
        except ValueError:
            pass
        try:
            return float(cleaned)
        except ValueError:
            return cleaned

    @staticmethod
    def _validate_candle_frame(candles: pd.DataFrame) -> None:
        required_columns = {"open", "high", "low", "close"}
        missing_columns = required_columns.difference(candles.columns)
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"Candle data is missing required columns: {missing}.")
        if candles.empty:
            raise ValueError("Candle data cannot be empty.")

    @staticmethod
    def _find_swings(candles: pd.DataFrame, window: int = 2) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
        swing_highs: list[tuple[int, float]] = []
        swing_lows: list[tuple[int, float]] = []

        if len(candles) < window * 2 + 1:
            return swing_highs, swing_lows

        for index in range(window, len(candles) - window):
            high = float(candles["high"].iloc[index])
            low = float(candles["low"].iloc[index])
            left_highs = candles["high"].iloc[index - window:index]
            right_highs = candles["high"].iloc[index + 1:index + window + 1]
            left_lows = candles["low"].iloc[index - window:index]
            right_lows = candles["low"].iloc[index + 1:index + window + 1]

            if high > float(left_highs.max()) and high > float(right_highs.max()):
                swing_highs.append((index, high))
            if low < float(left_lows.min()) and low < float(right_lows.min()):
                swing_lows.append((index, low))

        return swing_highs, swing_lows
