"""ICT execution analysis engine for Project Sentinel Advisor Mode."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from loguru import logger

from backend.liquidity_engine.liquidity_analyzer import LiquidityAnalyzer, LiquidityAnalyzerError
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError


class ICTAnalyzerError(RuntimeError):
    """Raised when ICT execution analysis cannot be completed."""


class ICTAnalyzer:
    """Detect ICT execution conditions without placing trades."""

    DEFAULT_ALLOWED_SYMBOLS = frozenset({"XAUUSD", "US30", "EURUSD", "GBPUSD"})

    def __init__(
        self,
        connector: MT5Connector | None = None,
        liquidity_analyzer: LiquidityAnalyzer | None = None,
        config_dir: str | Path | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.connector = connector or MT5Connector()
        self.liquidity_analyzer = liquidity_analyzer or LiquidityAnalyzer(
            connector=self.connector,
            config_dir=self.config_dir,
        )
        self.allowed_symbols = self._load_allowed_symbols()

    def analyze(self, symbol: str) -> dict[str, Any]:
        """Return a complete Advisor Mode ICT execution assessment."""
        normalized_symbol = self._validate_symbol(symbol)
        logger.info("Starting ICT execution analysis for {}.", normalized_symbol)

        try:
            execution_candles = self._fetch_candles(normalized_symbol, "M15", count=300)
            h1_candles = self._fetch_candles(normalized_symbol, "H1", count=100)
            liquidity = self.liquidity_analyzer.analyze(normalized_symbol)
        except (MT5ConnectorError, LiquidityAnalyzerError, ValueError) as exc:
            raise ICTAnalyzerError(f"Could not analyze ICT execution for {normalized_symbol}: {exc}") from exc

        atr = LiquidityAnalyzer.calculate_atr(h1_candles, period=14)
        latest_sweep = liquidity.get("latest_sweep")
        mss = self.detect_mss(execution_candles, latest_sweep, atr)
        bos = self.detect_bos(execution_candles)
        fvg = self.detect_fvg(execution_candles, atr, direction=mss["direction"] if mss["detected"] else None)
        order_block = self.detect_order_block(
            execution_candles,
            direction=mss["direction"],
            reference_index=mss.get("break_index"),
        )
        premium_discount = self.calculate_premium_discount(
            sweep=latest_sweep,
            mss=mss,
            current_price=float(execution_candles["close"].iloc[-1]),
        )
        return_to_fvg = self.has_returned_to_fvg(execution_candles, fvg)
        readiness = self.validate_execution_readiness(mss, fvg, order_block, premium_discount, return_to_fvg)

        result = {
            "symbol": normalized_symbol,
            "mss": self.public_mss(mss),
            "bos": bos,
            "fvg": fvg,
            "order_block": order_block,
            "premium_discount": premium_discount,
            "return_to_fvg": return_to_fvg,
            "execution_ready": readiness["execution_ready"],
            "rejection_reasons": readiness["rejection_reasons"],
            "explanation": self.build_explanation(
                mss,
                bos,
                fvg,
                order_block,
                premium_discount,
                return_to_fvg,
                readiness["rejection_reasons"],
            ),
        }
        logger.info("Completed ICT execution analysis for {}: {}", normalized_symbol, result)
        return result

    @classmethod
    def detect_mss(
        cls,
        candles: pd.DataFrame,
        liquidity_sweep: dict[str, Any] | None,
        atr: float,
        structure_lookback: int = 12,
        displacement_threshold: float = 60.0,
    ) -> dict[str, Any]:
        """Detect market structure shift after a liquidity sweep."""
        normalized = cls.normalize_candles(candles)
        cls._validate_candle_frame(normalized)

        if not liquidity_sweep:
            return cls.empty_mss()

        direction = cls.expected_mss_direction(liquidity_sweep)
        sweep_position = cls.find_sweep_position(normalized, liquidity_sweep)
        if sweep_position is None or sweep_position < structure_lookback:
            return cls.empty_mss(direction=direction)

        prior_structure = normalized.iloc[sweep_position - structure_lookback:sweep_position]
        if direction == "bullish":
            break_level = float(prior_structure["high"].max())
            break_condition = lambda candle: float(candle["close"]) > break_level
        else:
            break_level = float(prior_structure["low"].min())
            break_condition = lambda candle: float(candle["close"]) < break_level

        post_sweep = normalized.iloc[sweep_position + 1:]
        for position, candle in post_sweep.iterrows():
            if not break_condition(candle):
                continue

            displacement_score = cls.calculate_displacement_score(candle, direction, atr)
            if displacement_score >= displacement_threshold:
                return {
                    "detected": True,
                    "direction": direction,
                    "break_level": break_level,
                    "displacement_score": displacement_score,
                    "break_index": int(position),
                    "sweep_index": int(sweep_position),
                }

        return cls.empty_mss(direction=direction, break_level=break_level)

    @classmethod
    def detect_bos(cls, candles: pd.DataFrame, lookback: int = 20) -> dict[str, Any]:
        """Detect a basic break of structure from recent candles."""
        normalized = cls.normalize_candles(candles)
        cls._validate_candle_frame(normalized)

        if len(normalized) <= lookback:
            return {"detected": False, "direction": None, "break_level": 0.0}

        prior = normalized.iloc[-lookback - 1:-1]
        latest_close = float(normalized["close"].iloc[-1])
        prior_high = float(prior["high"].max())
        prior_low = float(prior["low"].min())

        if latest_close > prior_high:
            return {"detected": True, "direction": "bullish", "break_level": prior_high}
        if latest_close < prior_low:
            return {"detected": True, "direction": "bearish", "break_level": prior_low}
        return {"detected": False, "direction": None, "break_level": 0.0}

    @classmethod
    def detect_fvg(
        cls,
        candles: pd.DataFrame,
        atr: float,
        direction: str | None = None,
        min_displacement_score: float = 55.0,
    ) -> dict[str, Any]:
        """Detect the latest valid classic three-candle fair value gap."""
        normalized = cls.normalize_candles(candles)
        cls._validate_candle_frame(normalized)

        latest_fvg: dict[str, Any] | None = None
        for position in range(2, len(normalized)):
            candle1 = normalized.iloc[position - 2]
            candle2 = normalized.iloc[position - 1]
            candle3 = normalized.iloc[position]

            if float(candle1["high"]) < float(candle3["low"]):
                candidate_direction = "bullish"
                low = float(candle1["high"])
                high = float(candle3["low"])
            elif float(candle1["low"]) > float(candle3["high"]):
                candidate_direction = "bearish"
                low = float(candle3["high"])
                high = float(candle1["low"])
            else:
                continue

            if direction and candidate_direction != direction:
                continue

            displacement_score = cls.calculate_displacement_score(candle2, candidate_direction, atr)
            if displacement_score < min_displacement_score:
                continue

            latest_fvg = {
                "detected": True,
                "direction": candidate_direction,
                "low": low,
                "high": high,
                "grade": cls.grade_fvg(displacement_score),
                "displacement_score": displacement_score,
                "start_index": position - 2,
                "middle_index": position - 1,
                "end_index": position,
            }

        return latest_fvg or cls.empty_fvg(direction=direction)

    @classmethod
    def detect_order_block(
        cls,
        candles: pd.DataFrame,
        direction: str | None,
        reference_index: int | None = None,
        lookback: int = 10,
    ) -> dict[str, Any]:
        """Detect the last opposing candle before displacement or MSS."""
        normalized = cls.normalize_candles(candles)
        cls._validate_candle_frame(normalized)

        if direction not in {"bullish", "bearish"}:
            return cls.empty_order_block(direction=direction)

        end_position = reference_index if reference_index is not None else len(normalized) - 1
        start_position = max(0, end_position - lookback)
        search = normalized.iloc[start_position:end_position]

        for position in range(len(search) - 1, -1, -1):
            candle = search.iloc[position]
            absolute_position = start_position + position
            is_bearish = float(candle["close"]) < float(candle["open"])
            is_bullish = float(candle["close"]) > float(candle["open"])

            if direction == "bullish" and is_bearish:
                return cls.build_order_block(candle, direction, absolute_position)
            if direction == "bearish" and is_bullish:
                return cls.build_order_block(candle, direction, absolute_position)

        return cls.empty_order_block(direction=direction)

    @staticmethod
    def calculate_premium_discount(
        sweep: dict[str, Any] | None,
        mss: dict[str, Any],
        current_price: float,
    ) -> dict[str, Any]:
        """Calculate premium/discount from sweep point to MSS break point."""
        if not sweep or not mss.get("detected"):
            return {
                "range_high": 0.0,
                "range_low": 0.0,
                "equilibrium": 0.0,
                "current_zone": "unavailable",
                "reason": "MSS not confirmed; dealing range cannot be anchored.",
            }

        sweep_price = float(sweep.get("level_price", 0.0))
        break_level = float(mss.get("break_level", 0.0))
        range_high = max(sweep_price, break_level)
        range_low = min(sweep_price, break_level)
        equilibrium = (range_high + range_low) / 2

        if current_price < equilibrium:
            current_zone = "discount"
        elif current_price > equilibrium:
            current_zone = "premium"
        else:
            current_zone = "equilibrium"

        return {
            "range_high": range_high,
            "range_low": range_low,
            "equilibrium": equilibrium,
            "current_zone": current_zone,
            "reason": None,
        }

    @staticmethod
    def has_returned_to_fvg(candles: pd.DataFrame, fvg: dict[str, Any]) -> bool:
        """Return whether recent price has traded back into a detected FVG."""
        if not fvg.get("detected"):
            return False

        recent = ICTAnalyzer.normalize_candles(candles).iloc[fvg.get("end_index", 0) + 1:]
        if recent.empty:
            return False

        fvg_low = float(fvg["low"])
        fvg_high = float(fvg["high"])
        return bool(((recent["low"] <= fvg_high) & (recent["high"] >= fvg_low)).any())

    @staticmethod
    def is_execution_ready(
        mss: dict[str, Any],
        fvg: dict[str, Any],
        order_block: dict[str, Any],
        premium_discount: dict[str, Any],
        return_to_fvg: bool,
    ) -> bool:
        """Return whether basic ICT execution conditions are aligned."""
        return ICTAnalyzer.validate_execution_readiness(
            mss,
            fvg,
            order_block,
            premium_discount,
            return_to_fvg,
        )["execution_ready"]

    @staticmethod
    def validate_execution_readiness(
        mss: dict[str, Any],
        fvg: dict[str, Any],
        order_block: dict[str, Any],
        premium_discount: dict[str, Any],
        return_to_fvg: bool,
    ) -> dict[str, Any]:
        """Validate execution readiness against aligned ICT constitution rules."""
        rejection_reasons: list[str] = []
        mss_direction = mss.get("direction")

        if not mss.get("detected"):
            rejection_reasons.append("MSS not confirmed")
        if premium_discount.get("current_zone") == "unavailable":
            rejection_reasons.append("Premium/discount unavailable")
        if not fvg.get("detected"):
            rejection_reasons.append("FVG not detected")
        elif mss_direction and fvg.get("direction") != mss_direction:
            rejection_reasons.append("FVG direction not aligned with MSS")
        if not order_block.get("detected"):
            rejection_reasons.append("Order block not detected")
        elif mss_direction and order_block.get("direction") != mss_direction:
            rejection_reasons.append("Order block direction not aligned with MSS")
        if not return_to_fvg:
            rejection_reasons.append("Price has not returned into FVG")

        if mss.get("detected"):
            zone = premium_discount.get("current_zone")
            if mss_direction == "bullish" and zone != "discount":
                rejection_reasons.append("Bullish setup is not in discount")
            elif mss_direction == "bearish" and zone != "premium":
                rejection_reasons.append("Bearish setup is not in premium")

        return {
            "execution_ready": len(rejection_reasons) == 0,
            "rejection_reasons": rejection_reasons,
        }

    @staticmethod
    def expected_mss_direction(liquidity_sweep: dict[str, Any]) -> str:
        """Infer expected MSS direction from sweep side."""
        side = liquidity_sweep.get("side")
        if side == "sell_side":
            return "bullish"
        return "bearish"

    @staticmethod
    def find_sweep_position(candles: pd.DataFrame, liquidity_sweep: dict[str, Any]) -> int | None:
        """Find a sweep candle position from explicit index or timestamp metadata."""
        if "sweep_index" in liquidity_sweep:
            return int(liquidity_sweep["sweep_index"])

        sweep_time = liquidity_sweep.get("time")
        if sweep_time and "time" in candles.columns:
            matching = candles.index[candles["time"].astype(str) == str(sweep_time)].tolist()
            if matching:
                return int(matching[-1])

        return max(0, len(candles) - 20)

    @staticmethod
    def calculate_displacement_score(candle: pd.Series, direction: str, atr: float) -> float:
        """Score candle displacement from 0 to 100."""
        if atr <= 0:
            return 0.0

        candle_range = float(candle["high"] - candle["low"])
        body_size = abs(float(candle["close"]) - float(candle["open"]))
        close_location = ICTAnalyzer.close_location_score(candle, direction)
        range_score = min((candle_range / atr) * 35.0, 35.0)
        body_score = min((body_size / atr) * 45.0, 45.0)
        return round(range_score + body_score + close_location, 2)

    @staticmethod
    def close_location_score(candle: pd.Series, direction: str) -> float:
        """Score whether the close is near the displacement extreme."""
        candle_range = float(candle["high"] - candle["low"])
        if candle_range <= 0:
            return 0.0

        if direction == "bullish":
            close_ratio = (float(candle["close"]) - float(candle["low"])) / candle_range
        else:
            close_ratio = (float(candle["high"]) - float(candle["close"])) / candle_range
        return min(max(close_ratio, 0.0), 1.0) * 20.0

    @staticmethod
    def grade_fvg(displacement_score: float) -> str:
        """Grade FVG quality from displacement strength."""
        if displacement_score >= 80:
            return "A"
        if displacement_score >= 65:
            return "B"
        return "C"

    @staticmethod
    def build_order_block(candle: pd.Series, direction: str, index: int) -> dict[str, Any]:
        """Build a public order block result."""
        return {
            "detected": True,
            "direction": direction,
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "index": index,
        }

    @staticmethod
    def empty_mss(direction: str | None = None, break_level: float = 0.0) -> dict[str, Any]:
        """Return an empty MSS result."""
        return {
            "detected": False,
            "direction": direction,
            "break_level": break_level,
            "displacement_score": 0.0,
            "break_index": None,
            "sweep_index": None,
        }

    @staticmethod
    def public_mss(mss: dict[str, Any]) -> dict[str, Any]:
        """Return MSS fields intended for external consumers."""
        return {
            "detected": mss["detected"],
            "direction": mss["direction"],
            "break_level": mss["break_level"],
            "displacement_score": mss["displacement_score"],
        }

    @staticmethod
    def empty_fvg(direction: str | None = None) -> dict[str, Any]:
        """Return an empty FVG result."""
        return {
            "detected": False,
            "direction": direction,
            "low": 0.0,
            "high": 0.0,
            "grade": None,
        }

    @staticmethod
    def empty_order_block(direction: str | None = None) -> dict[str, Any]:
        """Return an empty order block result."""
        return {
            "detected": False,
            "direction": direction,
            "open": 0.0,
            "high": 0.0,
            "low": 0.0,
            "close": 0.0,
        }

    @staticmethod
    def build_explanation(
        mss: dict[str, Any],
        bos: dict[str, Any],
        fvg: dict[str, Any],
        order_block: dict[str, Any],
        premium_discount: dict[str, Any],
        return_to_fvg: bool,
        rejection_reasons: list[str] | None = None,
    ) -> list[str]:
        """Build human-readable ICT execution explanation lines."""
        rejection_reasons = rejection_reasons or []
        explanation = [
            f"MSS detected: {mss.get('detected')} direction: {mss.get('direction')}.",
            f"BOS detected: {bos.get('detected')} direction: {bos.get('direction')}.",
            f"FVG detected: {fvg.get('detected')} grade: {fvg.get('grade')}.",
            f"Order block detected: {order_block.get('detected')}.",
            f"Current dealing range zone is {premium_discount.get('current_zone')}.",
            f"Return into FVG: {return_to_fvg}.",
        ]
        if "MSS not confirmed" in rejection_reasons:
            explanation.append("Execution rejected because MSS is not confirmed.")
        if rejection_reasons:
            explanation.append(f"Rejection reasons: {', '.join(rejection_reasons)}.")
        explanation.append("Advisor Mode only: no execution action was taken.")
        return explanation

    @staticmethod
    def normalize_candles(candles: pd.DataFrame) -> pd.DataFrame:
        """Return candles sorted by time when available."""
        normalized = candles.copy()
        if "time" in normalized.columns:
            normalized = normalized.sort_values("time").reset_index(drop=True)
        return normalized

    def _fetch_candles(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        try:
            candles = self.connector.get_historical_candles(symbol, timeframe, count=count)
        except Exception as exc:
            raise ICTAnalyzerError(f"Failed to fetch {timeframe} candles for {symbol}: {exc}") from exc

        if candles.empty:
            raise ICTAnalyzerError(f"No {timeframe} candles returned for {symbol}.")
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
            raise ICTAnalyzerError(f"Failed to load config {trading_rules_path}: {exc}") from exc

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
