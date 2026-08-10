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

        management = self.config.get("exit_management") or {}
        if management.get("enabled", False):
            return self.simulate_managed(
                direction=direction,
                entry=entry,
                stop_distance=stop_distance,
                forward_candles=forward_candles,
                cost=cost,
                breakeven_at_r=float(management.get("breakeven_at_r", 1.0)),
                partial_at_r=float(management.get("partial_at_r", 2.0)),
                partial_fraction=float(management.get("partial_close_percent", 30)) / 100.0,
                final_target_r=float(management.get("final_target_r", 3.0)),
            )

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

    def simulate_managed(
        self,
        *,
        direction: str,
        entry: float,
        stop_distance: float,
        forward_candles: pd.DataFrame,
        cost: dict[str, Any],
        breakeven_at_r: float,
        partial_at_r: float,
        partial_fraction: float,
        final_target_r: float,
    ) -> dict[str, Any]:
        """Simulate the live management plan: BE move, partial close, run to final.

        Conservative intrabar ordering: the stop is checked before targets in
        every candle. Reaching breakeven_at_r moves the stop to entry; reaching
        partial_at_r books the partial fraction; the remainder exits at the
        final target, the (possibly moved) stop, or the timeout close.
        """
        sign = 1 if direction == "bullish" else -1
        cost_rr = float(cost.get("cost_rr", 0.0))
        stop_r = -1.0  # Stop location in R (0.0 after the breakeven move).
        booked_rr = 0.0
        remaining = 1.0
        for position, candle in forward_candles.reset_index(drop=True).iterrows():
            favorable_r = sign * (float(candle["high" if sign == 1 else "low"]) - entry) / stop_distance
            adverse_r = sign * (float(candle["low" if sign == 1 else "high"]) - entry) / stop_distance
            if adverse_r <= stop_r:
                booked_rr += remaining * stop_r
                return self.result(
                    "LOSS" if stop_r < 0 else ("WIN" if booked_rr - cost_rr > 0 else "BREAKEVEN"),
                    round(booked_rr - cost_rr, 4),
                    "SL" if stop_r < 0 else "BE_STOP",
                    int(position),
                    cost,
                )
            if remaining > (1.0 - partial_fraction) and favorable_r >= partial_at_r:
                booked_rr += partial_fraction * partial_at_r
                remaining = round(remaining - partial_fraction, 6)
            if stop_r < 0 and favorable_r >= breakeven_at_r:
                stop_r = 0.0
            if favorable_r >= final_target_r:
                booked_rr += remaining * final_target_r
                return self.result("WIN", round(booked_rr - cost_rr, 4), "TP3", int(position), cost)
        net = round(booked_rr - cost_rr, 4)  # Timeout: remainder exits near entry.
        outcome = "WIN" if net > 0 else ("LOSS" if net < -0.05 else "BREAKEVEN")
        return self.result(outcome, net, "no_target_hit", None, cost)

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
