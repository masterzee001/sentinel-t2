"""Historical trade simulation utilities for Project Sentinel backtesting."""

from __future__ import annotations

from typing import Any

import pandas as pd

from backend.shared.cost_engine import CostInput, estimate_execution_cost


class TradeSimulator:
    """Walk forward through candles to determine simulated trade outcome.

    Execution costs (spread + slippage, optional commission) are applied to
    every fill through the shared cost engine. A simulated result without a
    cost model is flagged ``NO_COST_MODEL`` so missing costs are visible
    instead of silently zero.
    """

    DEFAULT_CONFIG = {
        "use_tp1_as_win": True,
        "use_tp3_as_full_win": True,
        "stop_at_sl": True,
        "apply_costs": True,
        "cost_mode": "normal",
    }

    # Round-trip execution costs in raw price units per symbol.
    # Overridable via config/backtesting.yaml simulation.costs.
    DEFAULT_SYMBOL_COSTS = {
        "XAUUSD": {"spread": 0.35, "slippage": 0.10},
        "US30": {"spread": 3.5, "slippage": 1.5},
        "NAS100": {"spread": 2.5, "slippage": 1.0},
        "EURUSD": {"spread": 0.00012, "slippage": 0.00004},
        "GBPUSD": {"spread": 0.00015, "slippage": 0.00005},
        "BTCUSD": {"spread": 30.0, "slippage": 10.0},
    }

    NO_COST = {"cost_price": 0.0, "cost_rr": 0.0, "cost_status": "NOT_OPENED"}

    def __init__(
        self,
        simulation_config: dict[str, Any] | None = None,
        symbol_costs: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self.config = {**self.DEFAULT_CONFIG, **(simulation_config or {})}
        configured_costs = symbol_costs or self.config.get("costs") or {}
        self.symbol_costs = {
            str(symbol).upper().strip(): dict(model)
            for symbol, model in {**self.DEFAULT_SYMBOL_COSTS, **configured_costs}.items()
        }

    def simulate(self, trade_plan: dict[str, Any], forward_candles: pd.DataFrame) -> dict[str, Any]:
        """Simulate a trade from entry, stop, and TP levels over future candles."""
        direction = trade_plan.get("direction")
        entry = float(trade_plan.get("entry", {}).get("price", 0.0) or 0.0)
        stop_loss = float(trade_plan.get("stop_loss", {}).get("price", 0.0) or 0.0)
        take_profit = trade_plan.get("take_profit", {})
        tp1 = float(take_profit.get("tp1", 0.0) or 0.0)
        tp2 = float(take_profit.get("tp2", 0.0) or 0.0)
        tp3 = float(take_profit.get("tp3", 0.0) or 0.0)

        if direction not in {"bullish", "bearish"} or not entry or not stop_loss or forward_candles.empty:
            return self.result("BREAKEVEN", 0.0, "invalid_or_no_data", None, dict(self.NO_COST))

        stop_distance = abs(entry - stop_loss)
        if stop_distance <= 0:
            return self.result("BREAKEVEN", 0.0, "invalid_stop", None, dict(self.NO_COST))

        cost = self.execution_cost(trade_plan.get("symbol"), stop_distance)
        cost_rr = float(cost.get("cost_rr", 0.0))

        for position, candle in forward_candles.reset_index(drop=True).iterrows():
            high = float(candle["high"])
            low = float(candle["low"])
            hit = self.evaluate_candle_hit(direction, high, low, stop_loss, tp1, tp2, tp3)
            if hit is None:
                continue
            outcome, target_price, target_name = hit
            if outcome == "LOSS":
                return self.result("LOSS", round(-1.0 - cost_rr, 4), "SL", int(position), cost)
            rr = self.calculate_rr(entry, target_price, stop_distance)
            return self.result("WIN", round(rr - cost_rr, 4), target_name, int(position), cost)

        # Timeout exits are market closes: they still pay execution costs.
        return self.result("BREAKEVEN", round(-cost_rr, 4), "no_target_hit", None, cost)

    def execution_cost(self, symbol: Any, stop_distance: float) -> dict[str, Any]:
        """Return round-trip execution cost for a symbol, in price units and R."""
        if not self.config.get("apply_costs", True):
            return {"cost_price": 0.0, "cost_rr": 0.0, "cost_status": "DISABLED"}
        model = self.symbol_costs.get(str(symbol or "").upper().strip())
        if not model:
            return {"cost_price": 0.0, "cost_rr": 0.0, "cost_status": "NO_COST_MODEL"}
        estimate = estimate_execution_cost(
            CostInput(
                spread_points=float(model.get("spread", 0.0)),
                slippage_points=float(model.get("slippage", 0.0)),
                mode=str(self.config.get("cost_mode", "normal")),
            )
        )
        cost_price = float(estimate["total_cost_points"]) + float(model.get("commission", 0.0))
        cost_rr = round(cost_price / stop_distance, 4) if stop_distance > 0 else 0.0
        return {
            "cost_price": round(cost_price, 5),
            "cost_rr": cost_rr,
            "cost_status": "APPLIED",
            "cost_mode": estimate["mode"],
        }

    def evaluate_candle_hit(
        self,
        direction: str,
        high: float,
        low: float,
        stop_loss: float,
        tp1: float,
        tp2: float,
        tp3: float,
    ) -> tuple[str, float, str] | None:
        """Return candle hit outcome using conservative SL-first handling."""
        targets = self.target_sequence(direction, tp1, tp2, tp3)
        if direction == "bullish":
            stop_hit = low <= stop_loss
            target_hit = next(((price, name) for price, name in targets if price and high >= price), None)
        else:
            stop_hit = high >= stop_loss
            target_hit = next(((price, name) for price, name in targets if price and low <= price), None)

        if stop_hit and (self.config.get("stop_at_sl", True) or target_hit is None):
            return ("LOSS", stop_loss, "SL")
        if target_hit:
            return ("WIN", target_hit[0], target_hit[1])
        if stop_hit:
            return ("LOSS", stop_loss, "SL")
        return None

    def target_sequence(self, direction: str, tp1: float, tp2: float, tp3: float) -> list[tuple[float, str]]:
        """Return configured target sequence for the simulation."""
        if self.config.get("use_tp3_as_full_win", True) and tp3:
            targets = [(tp3, "TP3"), (tp2, "TP2"), (tp1, "TP1")]
        elif self.config.get("use_tp1_as_win", True) and tp1:
            targets = [(tp1, "TP1"), (tp2, "TP2"), (tp3, "TP3")]
        else:
            targets = [(tp2, "TP2"), (tp1, "TP1"), (tp3, "TP3")]

        valid_targets = [(price, name) for price, name in targets if price]
        if direction == "bullish":
            return sorted(valid_targets, key=lambda item: item[0], reverse=self.config.get("use_tp3_as_full_win", True))
        return sorted(valid_targets, key=lambda item: item[0], reverse=not self.config.get("use_tp3_as_full_win", True))

    @staticmethod
    def calculate_rr(entry: float, target: float, stop_distance: float) -> float:
        """Return RR achieved to target."""
        if stop_distance <= 0:
            return 0.0
        return round(abs(target - entry) / stop_distance, 2)

    @staticmethod
    def result(
        outcome: str,
        rr: float,
        hit_level: str,
        exit_candle_index: int | None,
        cost: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return standard simulation result."""
        return {
            "outcome": outcome,
            "rr": rr,
            "hit_level": hit_level,
            "exit_candle_index": exit_candle_index,
            "cost": cost or dict(TradeSimulator.NO_COST),
        }
