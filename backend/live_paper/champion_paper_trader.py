"""Champion-config live paper trader (advisor mode, no orders ever).

Runs the EXACT promoted configuration against live closed M15 candles:
US30+NAS100, displacement-breakout candidates via BacktestEngine's plan
builder, admission via HistoricalBacktestDecisionBrain, naked SL/TP3 at 3R,
execution costs from the shared cost model, one position per symbol, 24-bar
timeout. Every decision and outcome is journaled so live results can be
compared against the replay expectation (rwPF 1.18) — the parity gate that
must pass before any real-risk decision.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.backtesting.backtest_engine import BacktestEngine

SYMBOLS = ("US30", "NAS100")
TIMEOUT_BARS = 24
HISTORY_BARS = 120
REPLAY_EXPECTATION = {"risk_weighted_pf": 1.18, "avg_rr_per_trade": 0.077}


class ChampionPaperTrader:
    """Stateful paper trader over live closed candles; pure logic, no I/O side effects beyond files given."""

    def __init__(self, engine: BacktestEngine, state_path: str | Path) -> None:
        self.engine = engine
        self.state_path = Path(state_path)
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"open_positions": {}, "closed_trades": [], "last_candle_time": {}}

    def save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self.state, indent=2, default=str) + "\n", encoding="utf-8")
        temp.replace(self.state_path)

    def process_cycle(self, candles_by_symbol: dict[str, Any]) -> dict[str, Any]:
        """One evaluation cycle over the latest closed candles per symbol."""
        actions: list[dict[str, Any]] = []
        for symbol in SYMBOLS:
            candles = candles_by_symbol.get(symbol)
            if candles is None or len(candles) < 80:
                continue
            last_time = str(candles.iloc[-1]["time"])
            if self.state["last_candle_time"].get(symbol) == last_time:
                continue  # No new closed candle since the previous cycle.
            self.state["last_candle_time"][symbol] = last_time
            self._manage_open_position(symbol, candles, actions)
            if symbol not in self.state["open_positions"]:
                self._try_open(symbol, candles, last_time, actions)
        return {"actions": actions, "summary": self.summary()}

    def _manage_open_position(self, symbol: str, candles: Any, actions: list[dict[str, Any]]) -> None:
        position = self.state["open_positions"].get(symbol)
        if not position:
            return
        bar = candles.iloc[-1]
        high, low = float(bar["high"]), float(bar["low"])
        direction = position["direction"]
        stop, target = float(position["stop_loss"]), float(position["take_profit"])
        cost_rr = float(position["cost_rr"])
        position["bars_open"] = int(position.get("bars_open", 0)) + 1
        outcome = None
        if direction == "bullish":
            if low <= stop:
                outcome = ("LOSS", -1.0 - cost_rr)
            elif high >= target:
                outcome = ("WIN", 3.0 - cost_rr)
        else:
            if high >= stop:
                outcome = ("LOSS", -1.0 - cost_rr)
            elif low <= target:
                outcome = ("WIN", 3.0 - cost_rr)
        if outcome is None and position["bars_open"] >= TIMEOUT_BARS:
            outcome = ("BREAKEVEN", -cost_rr)
        if outcome:
            closed = {**position, "outcome": outcome[0], "rr": round(outcome[1], 4), "closed_at": str(bar["time"])}
            self.state["closed_trades"].append(closed)
            del self.state["open_positions"][symbol]
            actions.append({"event": "CLOSE", **closed})

    def _try_open(self, symbol: str, candles: Any, last_time: str, actions: list[dict[str, Any]]) -> None:
        history = candles.tail(HISTORY_BARS)
        killzone = self.engine.killzone_analyzer.analyze(symbol, current_time=candles.iloc[-1].get("time"))
        if not killzone.get("is_valid"):
            return
        plan = self.engine.build_historical_plan(symbol, history, killzone)
        if not plan.get("candidate_detected"):
            return
        decision = self.engine.decision_brain.analyze(
            symbol,
            context={
                "mode": "PAPER",
                "minimum_rr": 3.0,
                "risk_reward": 3.0,
                "plan": plan,
                "killzone": killzone,
                "replay_mss": {"detected": True, "direction": plan.get("direction"), "mode": "HISTORICAL_STRUCTURE_BREAK"},
            },
        )
        guardrail = decision.get("adaptive_guardrail") or decision.get("guardrail", {}) or {}
        blocked = guardrail.get("status") == "BLOCKED"
        non_guardrail = [
            reason for reason in decision.get("rejection_reasons", []) or []
            if reason not in set(guardrail.get("reasons", []) or [])
        ]
        if blocked or non_guardrail:
            actions.append({"event": "REJECT", "symbol": symbol, "time": last_time, "reasons": decision.get("rejection_reasons", [])})
            return
        stop_distance = float(plan["stop_loss"]["distance"])
        cost = self.engine.simulator.execution_cost(symbol, stop_distance)
        position = {
            "symbol": symbol,
            "direction": plan["direction"],
            "opened_at": last_time,
            "entry": float(plan["entry"]["price"]),
            "stop_loss": float(plan["stop_loss"]["price"]),
            "take_profit": float(plan["take_profit"]["tp3"]),
            "cost_rr": float(cost.get("cost_rr", 0.0)),
            "confidence": int(decision.get("total_confidence", 0) or 0),
            "bars_open": 0,
        }
        self.state["open_positions"][symbol] = position
        actions.append({"event": "OPEN", **position})

    def summary(self) -> dict[str, Any]:
        closed = self.state["closed_trades"]
        rrs = [float(trade["rr"]) for trade in closed]
        gross_win = sum(rr for rr in rrs if rr > 0)
        gross_loss = abs(sum(rr for rr in rrs if rr < 0))
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": "ADVISOR_PAPER_NO_ORDERS",
            "open_positions": list(self.state["open_positions"].values()),
            "closed_trades": len(closed),
            "wins": sum(1 for trade in closed if trade["outcome"] == "WIN"),
            "losses": sum(1 for trade in closed if trade["outcome"] == "LOSS"),
            "net_rr": round(sum(rrs), 4),
            "risk_weighted_pf": round(gross_win / gross_loss, 2) if gross_loss else round(gross_win, 2),
            "replay_expectation": REPLAY_EXPECTATION,
            "parity_note": "Compare risk_weighted_pf and avg rr/trade to replay_expectation once 50+ trades close.",
        }
