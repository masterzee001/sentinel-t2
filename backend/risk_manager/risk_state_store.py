"""Persistent runtime risk state for Project Sentinel.

The Risk Governor derives every operational limit (max trades per day,
consecutive-loss stop, cooldown, daily loss, open planned risk) from a
caller-supplied runtime state. Before this store existed, no live caller
supplied that state, so those limits never fired and peak-equity tracking
reset on every process restart. This store is the single durable source of
that state for all live callers.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger


class RiskStateStore:
    """File-backed daily risk counters that survive process restarts."""

    WAT_TIMEZONE = ZoneInfo("Africa/Lagos")

    DEFAULT_STATE: dict[str, Any] = {
        "state_date": None,
        "starting_balance": None,
        "peak_equity": None,
        "daily_start_equity": None,
        "last_equity": None,
        "trades_taken_today": 0,
        "consecutive_losses": 0,
        "last_loss_time": None,
        "realized_daily_pnl": 0.0,
        "total_open_planned_risk_percent": 0.0,
        "last_updated": None,
    }

    def __init__(self, state_path: str | Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.state_path = (
            Path(state_path) if state_path else project_root / ".sentinel_runtime" / "risk_state.json"
        )

    def now(self) -> datetime:
        """Return the current WAT time."""
        return datetime.now(self.WAT_TIMEZONE)

    def load(self, now: datetime | None = None) -> dict[str, Any]:
        """Return persisted state rolled over to the current WAT day."""
        state = dict(self.DEFAULT_STATE)
        if self.state_path.exists():
            try:
                loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state.update({key: loaded.get(key, value) for key, value in self.DEFAULT_STATE.items()})
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Risk state file {} unreadable ({}); starting fresh.", self.state_path, exc)
        return self.rollover(state, now or self.now())

    def save(self, state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        """Atomically persist state and return it."""
        state["last_updated"] = (now or self.now()).isoformat()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp_path, self.state_path)
        return state

    @classmethod
    def rollover(cls, state: dict[str, Any], now: datetime) -> dict[str, Any]:
        """Reset per-day counters when the stored day is not the current WAT day.

        Loss streaks, peak equity, and starting balance intentionally survive
        the rollover: a consecutive-loss stop must span sessions, and drawdown
        must be measured from the all-time peak, not the daily one.
        """
        today = now.astimezone(cls.WAT_TIMEZONE).date().isoformat()
        if state.get("state_date") != today:
            state["state_date"] = today
            state["trades_taken_today"] = 0
            state["realized_daily_pnl"] = 0.0
            state["daily_start_equity"] = None
            state["last_equity"] = None
        return state

    def observe_account(
        self,
        balance: float,
        equity: float,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Fold a live account reading into persistent state."""
        now = now or self.now()
        state = self.load(now)
        if state["starting_balance"] is None:
            state["starting_balance"] = round(float(balance), 2)
        if state["daily_start_equity"] is None:
            state["daily_start_equity"] = round(float(equity), 2)
        state["last_equity"] = round(float(equity), 2)
        previous_peak = state["peak_equity"] if state["peak_equity"] is not None else equity
        state["peak_equity"] = round(max(float(previous_peak), float(equity)), 2)
        return self.save(state, now)

    def record_trade_opened(
        self,
        planned_risk_percent: float = 0.0,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Count an opened trade and reserve its planned risk."""
        now = now or self.now()
        state = self.load(now)
        state["trades_taken_today"] = int(state["trades_taken_today"]) + 1
        state["total_open_planned_risk_percent"] = round(
            float(state["total_open_planned_risk_percent"]) + max(float(planned_risk_percent), 0.0), 4
        )
        return self.save(state, now)

    def record_trade_closed(
        self,
        pnl_amount: float,
        planned_risk_percent: float = 0.0,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Record a closed trade outcome, updating streaks and cooldown anchor."""
        now = now or self.now()
        state = self.load(now)
        state["realized_daily_pnl"] = round(float(state["realized_daily_pnl"]) + float(pnl_amount), 2)
        state["total_open_planned_risk_percent"] = round(
            max(float(state["total_open_planned_risk_percent"]) - max(float(planned_risk_percent), 0.0), 0.0), 4
        )
        if pnl_amount < 0:
            state["consecutive_losses"] = int(state["consecutive_losses"]) + 1
            state["last_loss_time"] = now.isoformat()
        elif pnl_amount > 0:
            state["consecutive_losses"] = 0
        return self.save(state, now)

    @staticmethod
    def daily_loss_percent(state: dict[str, Any]) -> float | None:
        """Return today's equity-based loss percent, or None before any reading.

        Equity vs. start-of-day equity captures both realized and floating
        loss, matching how prop firms evaluate the daily limit.
        """
        start = state.get("daily_start_equity")
        last = state.get("last_equity")
        if not start or last is None:
            return None
        loss = (float(start) - float(last)) / float(start) * 100
        return round(max(loss, 0.0), 2)

    def runtime_state(self, now: datetime | None = None) -> dict[str, Any]:
        """Return the persisted counters shaped as Risk Governor runtime input.

        ``daily_loss_percent`` is only present after an account reading today,
        so the governor's verified/unverified distinction stays honest.
        """
        now = now or self.now()
        state = self.load(now)
        runtime: dict[str, Any] = {
            "trades_taken_today": int(state["trades_taken_today"]),
            "consecutive_losses": int(state["consecutive_losses"]),
            "total_open_planned_risk_percent": float(state["total_open_planned_risk_percent"]),
            "analysis_time": now,
        }
        if state.get("last_loss_time"):
            runtime["last_loss_time"] = state["last_loss_time"]
        loss = self.daily_loss_percent(state)
        if loss is not None:
            runtime["daily_loss_percent"] = loss
        return runtime
