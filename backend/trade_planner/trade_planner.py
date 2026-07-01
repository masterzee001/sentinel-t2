"""Trade Planner engine for Project Sentinel Advisor Mode."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from backend.confidence_engine.confidence_analyzer import ConfidenceAnalyzer, ConfidenceAnalyzerError
from backend.ict_engine.ict_analyzer import ICTAnalyzer, ICTAnalyzerError
from backend.liquidity_engine.liquidity_analyzer import LiquidityAnalyzer, LiquidityAnalyzerError
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.risk_manager.risk_governor import RiskGovernor, RiskGovernorError


class TradePlannerError(RuntimeError):
    """Raised when a trade plan cannot be produced."""


class TradePlanner:
    """Convert approved Sentinel setup analysis into an Advisor Mode trade plan."""

    DEFAULT_ALLOWED_SYMBOLS = frozenset({"XAUUSD", "US30", "EURUSD", "GBPUSD"})
    DEFAULT_CONFIG = {
        "planner_test_mode": False,
        "minimum_rr_to_final_target": 3.0,
        "stop_loss_buffers": {
            "XAUUSD": 1.0,
            "US30": 10.0,
            "EURUSD": 0.0005,
            "GBPUSD": 0.0005,
        },
        "lot_sizing": {
            "default_tick_value": 1.0,
            "default_tick_size": 1.0,
            "min_lot": 0.01,
            "lot_step": 0.01,
            "lot_precision": 2,
        },
    }

    def __init__(
        self,
        connector: MT5Connector | None = None,
        confidence_analyzer: ConfidenceAnalyzer | None = None,
        risk_governor: RiskGovernor | None = None,
        ict_analyzer: ICTAnalyzer | None = None,
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
        self.ict_analyzer = ict_analyzer or ICTAnalyzer(
            connector=self.connector,
            liquidity_analyzer=self.liquidity_analyzer,
            config_dir=self.config_dir,
        )
        self.confidence_analyzer = confidence_analyzer or ConfidenceAnalyzer(
            connector=self.connector,
            liquidity_analyzer=self.liquidity_analyzer,
            ict_analyzer=self.ict_analyzer,
            config_dir=self.config_dir,
        )
        self.risk_governor = risk_governor or RiskGovernor(
            connector=self.connector,
            config_dir=self.config_dir,
        )
        self.config = self._load_config()
        self.allowed_symbols = self.DEFAULT_ALLOWED_SYMBOLS

    def analyze(
        self,
        symbol: str,
        confidence_context: dict[str, Any] | None = None,
        risk_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a complete Advisor Mode trade plan for a symbol."""
        normalized_symbol = self._validate_symbol(symbol)
        risk_state = {**(risk_state or {}), "symbol": normalized_symbol}
        logger.info("Starting trade planning for {}.", normalized_symbol)

        try:
            liquidity = self.liquidity_analyzer.analyze(normalized_symbol)
            ict = self.ict_analyzer.analyze(normalized_symbol)
            risk = self.risk_governor.evaluate(risk_state)
            confidence = self.confidence_analyzer.analyze(
                normalized_symbol,
                context={"risk_reward": 3.0, **(confidence_context or {})},
            )
            symbol_info = self.connector.get_symbol_info(normalized_symbol)
        except (
            ConfidenceAnalyzerError,
            RiskGovernorError,
            ICTAnalyzerError,
            LiquidityAnalyzerError,
            MT5ConnectorError,
            ValueError,
        ) as exc:
            raise TradePlannerError(f"Could not build trade plan for {normalized_symbol}: {exc}") from exc

        planner_test_mode = self.is_planner_test_mode()
        synthetic_plan = False
        direction = self.normalize_direction(confidence.get("direction") or ict.get("mss", {}).get("direction"))
        if planner_test_mode and direction is None:
            direction = self.infer_test_mode_direction(liquidity, ict)
            synthetic_plan = synthetic_plan or direction is not None

        entry = self.calculate_entry(ict, direction)
        if planner_test_mode and not entry.get("price"):
            entry = self.calculate_test_mode_entry(ict, direction, liquidity)
            synthetic_plan = True

        stop_loss = self.calculate_stop_loss(
            symbol=normalized_symbol,
            direction=direction,
            entry_price=entry["price"],
            latest_sweep=liquidity.get("latest_sweep"),
            order_block=ict.get("order_block", {}),
            liquidity=liquidity,
        )
        if planner_test_mode and not stop_loss.get("distance"):
            stop_loss = self.calculate_test_mode_stop_loss(
                symbol=normalized_symbol,
                direction=direction,
                entry_price=entry["price"],
                liquidity=liquidity,
            )
            synthetic_plan = True

        take_profit = self.select_take_profit_targets(
            direction=direction,
            entry_price=entry["price"],
            stop_distance=stop_loss["distance"],
            liquidity=liquidity,
            minimum_final_rr=self.minimum_rr_to_final_target,
        )
        if planner_test_mode and not all(float(value or 0.0) > 0 for value in take_profit.values()):
            take_profit = self.fill_test_mode_take_profit_targets(
                direction=direction,
                entry_price=entry["price"],
                stop_distance=stop_loss["distance"],
                take_profit=take_profit,
            )
            synthetic_plan = True

        risk_block = self.build_risk_block(
            entry_price=entry["price"],
            stop_distance=stop_loss["distance"],
            take_profit=take_profit,
            risk_amount=float(risk.get("risk", {}).get("risk_amount", 0.0)),
            symbol_info=symbol_info,
        )
        rejection_reasons = self.evaluate_execution_gates(
            confidence=confidence,
            risk=risk,
            ict=ict,
            direction=direction,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_block=risk_block,
            minimum_final_rr=self.minimum_rr_to_final_target,
        )
        execution_allowed = len(rejection_reasons) == 0 and not synthetic_plan
        plan_quality = self.determine_plan_quality(
            execution_allowed=execution_allowed,
            planner_test_mode=planner_test_mode,
            synthetic_plan=synthetic_plan,
            rejection_reasons=rejection_reasons,
        )

        result = {
            "symbol": normalized_symbol,
            "direction": direction,
            "entry": entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk_block,
            "management": self.management_plan(),
            "planner_test_mode": planner_test_mode,
            "synthetic_plan": synthetic_plan,
            "plan_quality": plan_quality,
            "execution_allowed": execution_allowed,
            "rejection_reasons": rejection_reasons,
            "explanation": self.build_explanation(
                entry,
                stop_loss,
                take_profit,
                risk_block,
                rejection_reasons,
                planner_test_mode,
                synthetic_plan,
                plan_quality,
            ),
        }
        logger.info("Completed trade planning for {}: {}", normalized_symbol, result)
        return result

    @classmethod
    def calculate_entry(cls, ict: dict[str, Any], direction: str | None) -> dict[str, Any]:
        """Return entry using OB/FVG confluence, then FVG midpoint, then OB midpoint."""
        fvg = ict.get("fvg", {})
        order_block = ict.get("order_block", {})
        premium_discount = ict.get("premium_discount", {})

        if direction == "bullish" and premium_discount.get("current_zone") not in {"discount", "equilibrium"}:
            return cls.empty_entry("Bullish setup is not in discount.")
        if direction == "bearish" and premium_discount.get("current_zone") not in {"premium", "equilibrium"}:
            return cls.empty_entry("Bearish setup is not in premium.")

        if fvg.get("detected") and order_block.get("detected"):
            overlap_low = max(float(fvg["low"]), float(order_block["low"]))
            overlap_high = min(float(fvg["high"]), float(order_block["high"]))
            if overlap_low <= overlap_high:
                return {
                    "type": "limit",
                    "price": cls.midpoint(overlap_low, overlap_high),
                    "source": "OB_FVG_confluence",
                }

        if fvg.get("detected"):
            return {
                "type": "limit",
                "price": cls.midpoint(float(fvg["low"]), float(fvg["high"])),
                "source": "FVG_midpoint",
            }

        if order_block.get("detected"):
            return {
                "type": "limit",
                "price": cls.midpoint(float(order_block["low"]), float(order_block["high"])),
                "source": "order_block_midpoint",
            }

        return cls.empty_entry("No valid FVG or order block entry source.")

    @classmethod
    def calculate_test_mode_entry(
        cls,
        ict: dict[str, Any],
        direction: str | None,
        liquidity: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a synthetic entry for calculation testing when live gates reject."""
        fvg = ict.get("fvg", {})
        order_block = ict.get("order_block", {})

        if fvg.get("detected") and order_block.get("detected"):
            overlap_low = max(float(fvg["low"]), float(order_block["low"]))
            overlap_high = min(float(fvg["high"]), float(order_block["high"]))
            if overlap_low <= overlap_high:
                return {
                    "type": "limit",
                    "price": cls.midpoint(overlap_low, overlap_high),
                    "source": "OB_FVG_confluence_test_mode",
                }

        if fvg.get("detected"):
            return {
                "type": "limit",
                "price": cls.midpoint(float(fvg["low"]), float(fvg["high"])),
                "source": "FVG_midpoint_test_mode",
            }

        if order_block.get("detected"):
            return {
                "type": "limit",
                "price": cls.midpoint(float(order_block["low"]), float(order_block["high"])),
                "source": "order_block_midpoint_test_mode",
            }

        current_price = float(liquidity.get("current_price", 0.0) or 0.0)
        if current_price and direction in {"bullish", "bearish"}:
            return {
                "type": "limit",
                "price": round(current_price, 5),
                "source": "current_price_test_mode",
            }

        return cls.empty_entry("Planner test mode could not synthesize an entry.")

    def calculate_stop_loss(
        self,
        symbol: str,
        direction: str | None,
        entry_price: float,
        latest_sweep: dict[str, Any] | None,
        order_block: dict[str, Any],
        liquidity: dict[str, Any],
    ) -> dict[str, Any]:
        """Return stop loss using sweep point, then OB boundary, then swing level."""
        buffer = self.get_stop_buffer(symbol)
        source = "unavailable"
        base_price = 0.0

        if latest_sweep and latest_sweep.get("level_price"):
            base_price = float(latest_sweep["level_price"])
            source = "liquidity_sweep"
        elif order_block.get("detected") and direction == "bullish":
            base_price = float(order_block["low"])
            source = "order_block_boundary"
        elif order_block.get("detected") and direction == "bearish":
            base_price = float(order_block["high"])
            source = "order_block_boundary"
        else:
            swing = self.select_stop_swing(direction, entry_price, liquidity)
            if swing:
                base_price = float(swing["price"])
                source = "swing_high_low"

        if not base_price or not entry_price or direction not in {"bullish", "bearish"}:
            return {"price": 0.0, "distance": 0.0, "source": source}

        stop_price = base_price - buffer if direction == "bullish" else base_price + buffer
        distance = abs(entry_price - stop_price)
        return {
            "price": round(stop_price, 5),
            "distance": round(distance, 5),
            "source": source,
        }

    def calculate_test_mode_stop_loss(
        self,
        symbol: str,
        direction: str | None,
        entry_price: float,
        liquidity: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a synthetic stop loss for calculation testing."""
        if not entry_price or direction not in {"bullish", "bearish"}:
            return {"price": 0.0, "distance": 0.0, "source": "unavailable"}

        buffer = self.get_stop_buffer(symbol)
        candidate = self.select_directional_stop_level(direction, entry_price, liquidity)
        if candidate:
            base_price = float(candidate["price"])
            stop_price = base_price - buffer if direction == "bullish" else base_price + buffer
            return {
                "price": round(stop_price, 5),
                "distance": round(abs(entry_price - stop_price), 5),
                "source": "test_mode_directional_liquidity",
            }

        fallback_distance = max(buffer * 5, entry_price * 0.001)
        stop_price = entry_price - fallback_distance if direction == "bullish" else entry_price + fallback_distance
        return {
            "price": round(stop_price, 5),
            "distance": round(abs(entry_price - stop_price), 5),
            "source": "test_mode_fallback_distance",
        }

    @staticmethod
    def select_take_profit_targets(
        direction: str | None,
        entry_price: float,
        stop_distance: float,
        liquidity: dict[str, Any],
        minimum_final_rr: float = 3.0,
    ) -> dict[str, float]:
        """Return directional targets ordered by distance from entry."""
        classification = liquidity.get("liquidity_classification", {})
        internal_targets = TradePlanner.valid_target_prices(classification.get("internal", []), direction, entry_price)
        engineered_targets = TradePlanner.valid_target_prices(classification.get("engineered", []), direction, entry_price)
        external_targets = TradePlanner.valid_target_prices(
            classification.get("external", []),
            direction,
            entry_price,
            stop_distance=stop_distance,
            minimum_rr=minimum_final_rr,
        )

        target_sequence = TradePlanner.sort_directional_prices(
            internal_targets + engineered_targets + external_targets,
            entry_price,
        )
        tp1 = target_sequence[0] if len(target_sequence) >= 1 else 0.0
        tp2 = target_sequence[1] if len(target_sequence) >= 2 else 0.0
        tp3 = external_targets[0] if external_targets else 0.0

        return {"tp1": tp1, "tp2": tp2, "tp3": tp3}

    @staticmethod
    def fill_test_mode_take_profit_targets(
        direction: str | None,
        entry_price: float,
        stop_distance: float,
        take_profit: dict[str, float],
    ) -> dict[str, float]:
        """Fill missing TP levels with synthetic 1R/2R/3R targets for testing."""
        if not entry_price or stop_distance <= 0 or direction not in {"bullish", "bearish"}:
            return take_profit

        direction_multiplier = 1 if direction == "bullish" else -1
        filled = dict(take_profit)
        synthetic_targets = {
            "tp1": entry_price + direction_multiplier * stop_distance,
            "tp2": entry_price + direction_multiplier * stop_distance * 2,
            "tp3": entry_price + direction_multiplier * stop_distance * 3,
        }
        for key, value in synthetic_targets.items():
            if not filled.get(key):
                filled[key] = round(value, 5)
        return filled

    @staticmethod
    def infer_test_mode_direction(liquidity: dict[str, Any], ict: dict[str, Any]) -> str | None:
        """Infer a calculation-only direction when live confirmation is missing."""
        mss_direction = ict.get("mss", {}).get("direction")
        if mss_direction in {"bullish", "bearish"}:
            return mss_direction

        latest_sweep = liquidity.get("latest_sweep") or {}
        if latest_sweep.get("side") == "sell_side":
            return "bullish"
        if latest_sweep.get("side") == "buy_side":
            return "bearish"
        return None

    def build_risk_block(
        self,
        entry_price: float,
        stop_distance: float,
        take_profit: dict[str, float],
        risk_amount: float,
        symbol_info: dict[str, Any],
    ) -> dict[str, float]:
        """Build lot size and reward-to-risk metrics."""
        lot_size = self.calculate_lot_size(risk_amount, stop_distance, symbol_info, self.config.get("lot_sizing", {}))
        return {
            "risk_amount": round(risk_amount, 2),
            "lot_size": lot_size,
            "rr_to_tp1": self.calculate_rr(entry_price, take_profit.get("tp1", 0.0), stop_distance),
            "rr_to_tp2": self.calculate_rr(entry_price, take_profit.get("tp2", 0.0), stop_distance),
            "rr_to_tp3": self.calculate_rr(entry_price, take_profit.get("tp3", 0.0), stop_distance),
        }

    @staticmethod
    def evaluate_execution_gates(
        confidence: dict[str, Any],
        risk: dict[str, Any],
        ict: dict[str, Any],
        direction: str | None,
        entry: dict[str, Any],
        stop_loss: dict[str, Any],
        take_profit: dict[str, float],
        risk_block: dict[str, float] | None = None,
        minimum_final_rr: float = 3.0,
    ) -> list[str]:
        """Return execution blocking reasons from planner and upstream engines."""
        reasons: list[str] = []
        risk_block = risk_block or {}

        if confidence.get("decision") != "APPROVED":
            reasons.append("Confidence Engine decision is not APPROVED")
            reasons.extend(confidence.get("rejection_reasons", []))
        if not risk.get("permission", {}).get("trade_allowed"):
            reasons.append("Risk Governor blocked trading")
            reasons.extend(risk.get("permission", {}).get("block_reasons", []))
        if not ict.get("execution_ready"):
            reasons.append("ICT execution is not ready")
            reasons.extend(ict.get("rejection_reasons", []))
        if direction not in {"bullish", "bearish"}:
            reasons.append("No directional setup")
        if not entry.get("price"):
            reasons.append("Entry price unavailable")
        if not stop_loss.get("distance"):
            reasons.append("Stop loss unavailable")
        if not any(float(value or 0.0) > 0 for value in take_profit.values()):
            reasons.append("Directional take profit targets unavailable")
        if float(risk_block.get("rr_to_tp3", 0.0)) < minimum_final_rr:
            reasons.append("RR below minimum 3")

        return TradePlanner.dedupe_reasons(reasons)

    @staticmethod
    def calculate_lot_size(
        risk_amount: float,
        stop_distance: float,
        symbol_info: dict[str, Any],
        lot_config: dict[str, Any] | None = None,
    ) -> float:
        """Return broker-safe lot size from risk amount, stop distance, and tick data."""
        lot_config = lot_config or {}
        tick_value = float(symbol_info.get("trade_tick_value") or symbol_info.get("tick_value") or lot_config.get("default_tick_value", 1.0))
        tick_size = float(symbol_info.get("trade_tick_size") or symbol_info.get("tick_size") or lot_config.get("default_tick_size", 1.0))
        min_lot = float(symbol_info.get("volume_min") or lot_config.get("min_lot", 0.01))
        max_lot = float(symbol_info.get("volume_max") or 100.0)
        lot_step = float(symbol_info.get("volume_step") or lot_config.get("lot_step", 0.01))
        precision = int(lot_config.get("lot_precision", 2))

        if risk_amount <= 0 or stop_distance <= 0 or tick_value <= 0 or tick_size <= 0:
            return 0.0

        value_per_price_unit = tick_value / tick_size
        raw_lot = risk_amount / (stop_distance * value_per_price_unit)
        stepped_lot = math.floor((raw_lot / lot_step) + 1e-9) * lot_step
        broker_safe_lot = min(max(stepped_lot, min_lot), max_lot)
        return round(broker_safe_lot, precision)

    @staticmethod
    def calculate_rr(entry_price: float, target_price: float, stop_distance: float) -> float:
        """Return reward-to-risk from entry, target, and stop distance."""
        if not entry_price or not target_price or stop_distance <= 0:
            return 0.0
        return round(abs(target_price - entry_price) / stop_distance, 2)

    @staticmethod
    def select_nearest_target_price(
        levels: list[dict[str, Any]],
        direction: str | None,
        entry_price: float,
        stop_distance: float = 0.0,
        minimum_rr: float = 0.0,
    ) -> float:
        """Return nearest valid directional target price from a liquidity class."""
        candidates = TradePlanner.valid_target_prices(levels, direction, entry_price, stop_distance, minimum_rr)
        return candidates[0] if candidates else 0.0

    @staticmethod
    def valid_target_prices(
        levels: list[dict[str, Any]],
        direction: str | None,
        entry_price: float,
        stop_distance: float = 0.0,
        minimum_rr: float = 0.0,
    ) -> list[float]:
        """Return sorted valid target prices in the trade direction."""
        desired_side = TradePlanner.desired_target_side(direction)
        candidates = []
        for level in levels:
            price = float(level.get("price", 0.0))
            side = level.get("side")
            if desired_side and side != desired_side:
                continue
            if direction == "bullish" and price <= entry_price:
                continue
            if direction == "bearish" and price >= entry_price:
                continue
            if minimum_rr > 0 and TradePlanner.calculate_rr(entry_price, price, stop_distance) < minimum_rr:
                continue
            candidates.append(price)

        return TradePlanner.sort_directional_prices(candidates, entry_price)

    @staticmethod
    def sort_directional_prices(prices: list[float], entry_price: float) -> list[float]:
        """Return unique target prices sorted by distance from entry."""
        unique_prices = sorted({round(float(price), 5) for price in prices})
        unique_prices.sort(key=lambda price: abs(price - entry_price))
        return unique_prices

    @staticmethod
    def desired_target_side(direction: str | None) -> str | None:
        """Return required liquidity side for target selection."""
        if direction == "bullish":
            return "buy_side"
        if direction == "bearish":
            return "sell_side"
        return None

    @staticmethod
    def select_stop_swing(direction: str | None, entry_price: float, liquidity: dict[str, Any]) -> dict[str, Any] | None:
        """Return a fallback swing stop level."""
        internal = liquidity.get("liquidity_classification", {}).get("internal", [])
        if direction == "bullish":
            candidates = [level for level in internal if level.get("side") == "sell_side" and float(level["price"]) < entry_price]
            return max(candidates, key=lambda level: float(level["price"]), default=None)
        if direction == "bearish":
            candidates = [level for level in internal if level.get("side") == "buy_side" and float(level["price"]) > entry_price]
            return min(candidates, key=lambda level: float(level["price"]), default=None)
        return None

    @staticmethod
    def select_directional_stop_level(
        direction: str | None,
        entry_price: float,
        liquidity: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return the nearest protective liquidity level for test-mode stops."""
        levels = []
        for classified_levels in liquidity.get("liquidity_classification", {}).values():
            levels.extend(classified_levels)

        latest_sweep = liquidity.get("latest_sweep")
        if latest_sweep and latest_sweep.get("level_price"):
            levels.append(
                {
                    "price": float(latest_sweep["level_price"]),
                    "side": latest_sweep.get("side"),
                }
            )

        if direction == "bullish":
            candidates = [level for level in levels if float(level.get("price", 0.0)) < entry_price]
            return max(candidates, key=lambda level: float(level["price"]), default=None)
        if direction == "bearish":
            candidates = [level for level in levels if float(level.get("price", 0.0)) > entry_price]
            return min(candidates, key=lambda level: float(level["price"]), default=None)
        return None

    def get_stop_buffer(self, symbol: str) -> float:
        """Return configured stop buffer for a symbol."""
        return float(self.config.get("stop_loss_buffers", {}).get(symbol, self.DEFAULT_CONFIG["stop_loss_buffers"][symbol]))

    def is_planner_test_mode(self) -> bool:
        """Return whether planner test mode is enabled."""
        return bool(self.config.get("planner_test_mode", False))

    @property
    def minimum_rr_to_final_target(self) -> float:
        """Return minimum required RR to TP3/final target."""
        return float(self.config.get("minimum_rr_to_final_target", 3.0))

    @staticmethod
    def determine_plan_quality(
        execution_allowed: bool,
        planner_test_mode: bool,
        synthetic_plan: bool,
        rejection_reasons: list[str],
    ) -> str:
        """Return valid, invalid, or diagnostic_only for the trade plan."""
        if execution_allowed:
            return "valid"
        if planner_test_mode and (synthetic_plan or rejection_reasons):
            return "diagnostic_only"
        return "invalid"

    @staticmethod
    def management_plan() -> dict[str, Any]:
        """Return Sentinel's default management plan."""
        return {
            "breakeven_at": "1R",
            "partial_profit_at": "2R",
            "partial_close_percent": 30,
            "trail_mode": "structure",
        }

    @staticmethod
    def normalize_direction(direction: str | None) -> str | None:
        """Normalize direction labels to bullish or bearish."""
        if direction in {"bullish", "bearish"}:
            return direction
        return None

    @staticmethod
    def midpoint(low: float, high: float) -> float:
        """Return midpoint between two prices."""
        return round((low + high) / 2, 5)

    @staticmethod
    def empty_entry(reason: str) -> dict[str, Any]:
        """Return an unavailable entry object."""
        return {
            "type": "limit",
            "price": 0.0,
            "source": "unavailable",
            "reason": reason,
        }

    @staticmethod
    def dedupe_reasons(reasons: list[str]) -> list[str]:
        """Preserve-order de-duplication."""
        deduped: list[str] = []
        for reason in reasons:
            if reason and reason not in deduped:
                deduped.append(reason)
        return deduped

    @staticmethod
    def build_explanation(
        entry: dict[str, Any],
        stop_loss: dict[str, Any],
        take_profit: dict[str, float],
        risk: dict[str, float],
        rejection_reasons: list[str],
        planner_test_mode: bool = False,
        synthetic_plan: bool = False,
        plan_quality: str = "invalid",
    ) -> list[str]:
        """Build human-readable trade plan explanation."""
        explanation = [
            f"Entry source is {entry.get('source')} at {entry.get('price')}.",
            f"Stop loss source is {stop_loss.get('source')} with distance {stop_loss.get('distance')}.",
            f"Take profits are TP1 {take_profit.get('tp1')}, TP2 {take_profit.get('tp2')}, TP3 {take_profit.get('tp3')}.",
            f"Risk amount is {risk.get('risk_amount')} and lot size is {risk.get('lot_size')}.",
            f"Plan quality is {plan_quality}.",
        ]
        if planner_test_mode:
            explanation.append(
                "Planner test mode is enabled; synthetic plan fields may be generated while execution_allowed remains unchanged."
            )
        if synthetic_plan:
            explanation.append("Synthetic trade-plan values were generated for calculation validation.")
        if plan_quality == "diagnostic_only":
            explanation.append("Trade plan generated for diagnostics only because setup is not execution-ready.")
        if rejection_reasons:
            explanation.append(f"Execution not allowed because: {', '.join(rejection_reasons)}.")
        else:
            explanation.append("Execution is allowed by Confidence, Risk, and ICT gates.")
        explanation.append("Advisor Mode only: no live trade execution was taken.")
        return explanation

    def _load_config(self) -> dict[str, Any]:
        config = self._load_yaml_file(self.config_dir / "trade_planner.yaml")
        return self._deep_merge(self.DEFAULT_CONFIG, config)

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist; using trade planner defaults.", path)
            return {}

        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise TradePlannerError(f"Failed to load config {path}: {exc}") from exc

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _validate_symbol(self, symbol: str) -> str:
        normalized_symbol = symbol.upper().strip()
        if normalized_symbol not in self.allowed_symbols:
            supported = ", ".join(sorted(self.allowed_symbols))
            raise ValueError(f"Unsupported symbol '{symbol}'. Supported symbols: {supported}.")
        return normalized_symbol
