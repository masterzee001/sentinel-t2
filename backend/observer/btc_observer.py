"""BTCUSD observer mode for Project Sentinel diagnostics."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from backend.killzone_engine.killzone_analyzer import KillzoneAnalyzer
from backend.market_data.mt5_connector import MT5Connector
from backend.smt_engine.smt_analyzer import SMTAnalyzer
from backend.shared.confidence_band_registry import observer_state


class BTCObserver:
    """Collect BTCUSD diagnostics without ever allowing execution."""

    SYMBOL = "BTCUSD"
    DISPLAY_SYMBOL = "BTCUSD (EXPERIMENTAL)"
    REJECTION_REASON = "BTCUSD demo sandbox: production execution disabled"
    WAT_TIMEZONE = ZoneInfo("Africa/Lagos")

    def __init__(
        self,
        connector: MT5Connector,
        config_dir: str | Path | None = None,
        killzone_analyzer: KillzoneAnalyzer | None = None,
        smt_analyzer: SMTAnalyzer | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.connector = connector
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.killzone_analyzer = killzone_analyzer or KillzoneAnalyzer(config_dir=self.config_dir)
        self.smt_analyzer = smt_analyzer or SMTAnalyzer(connector=connector, config_dir=self.config_dir)

    def observe(self) -> dict[str, Any]:
        """Return a normalized observer-only BTC snapshot."""
        killzone = self.safe_killzone()
        smt = self.safe_smt(killzone)
        try:
            tick = self.connector.get_latest_tick(self.SYMBOL)
            candles = self.connector.get_historical_candles(self.SYMBOL, "M15", count=80)
            confidence_score, state, narrative = self.classify(candles, tick)
            available = True
            error = ""
        except Exception as exc:
            tick = {}
            candles = pd.DataFrame()
            confidence_score = 0
            state = "UNAVAILABLE"
            narrative = f"BTCUSD live diagnostics unavailable: {exc}"
            available = False
            error = str(exc)

        confidence = self.build_confidence(
            confidence_score=confidence_score,
            state=state,
            killzone=killzone,
            narrative=narrative,
            smt=smt,
        )
        trade_plan = self.build_trade_plan()
        return {
            "symbol": self.SYMBOL,
            "display_symbol": self.DISPLAY_SYMBOL,
            "experimental": True,
            "observer_mode": True,
            "mode": "DEMO_SANDBOX",
            "sandbox_mode": True,
            "available": available,
            "state": state,
            "score": confidence_score,
            "action": "OBSERVE" if available else "UNAVAILABLE",
            "tick": tick,
            "candles_loaded": int(len(candles)),
            "killzone": killzone,
            "narrative": confidence["narrative"],
            "smt": smt,
            "confidence": confidence,
            "trade_plan": trade_plan,
            "execution_allowed": False,
            "error": error,
        }

    def safe_killzone(self) -> dict[str, Any]:
        """Return killzone status, falling back to neutral observer metadata."""
        try:
            return self.killzone_analyzer.analyze(self.SYMBOL)
        except Exception:
            now = datetime.now(self.WAT_TIMEZONE)
            return {
                "symbol": self.SYMBOL,
                "current_time_wat": now.strftime("%H:%M"),
                "active_killzone": "none",
                "is_valid": False,
                "quality_score": 0,
                "commentary": "BTCUSD observer mode has no active execution killzone.",
                "minutes_to_next_killzone": 0,
            }

    def safe_smt(self, killzone: dict[str, Any]) -> dict[str, Any]:
        """Return SMT status, falling back to no SMT for BTC observer mode."""
        try:
            return self.smt_analyzer.analyze_for_symbol(
                self.SYMBOL,
                active_killzone=str(killzone.get("active_killzone", "none")),
            )
        except Exception:
            return {
                "pair_name": "none",
                "primary": self.SYMBOL,
                "comparison": "none",
                "timeframe": "M15",
                "smt_detected": False,
                "direction": None,
                "confidence": 0,
                "explanation": ["No BTCUSD SMT pair configured."],
            }

    @classmethod
    def classify(cls, candles: pd.DataFrame, tick: dict[str, Any]) -> tuple[int, str, str]:
        """Classify BTC observer state from recent M15 movement."""
        if candles.empty or "close" not in candles.columns:
            return 0, "UNAVAILABLE", "BTCUSD candle diagnostics unavailable."

        first_close = float(candles["close"].iloc[0])
        last_close = float(candles["close"].iloc[-1])
        if first_close <= 0:
            return 0, "UNAVAILABLE", "BTCUSD baseline price unavailable."

        percent_change = ((last_close - first_close) / first_close) * 100
        recent_range = float(candles["high"].tail(20).max() - candles["low"].tail(20).min())
        latest_price = float(tick.get("bid", last_close) or last_close)
        magnitude = abs(percent_change)
        confidence = min(80, max(20, int(35 + magnitude * 10)))
        if magnitude >= 2.0:
            state = "HOT"
        elif magnitude >= 0.75:
            state = "WARM"
        else:
            state = "COLD"
        direction = "up" if percent_change > 0 else ("down" if percent_change < 0 else "flat")
        narrative = (
            f"BTCUSD observer mode: price is {direction} {round(percent_change, 2)}% "
            f"over the sampled M15 window. Latest price {round(latest_price, 2)}; "
            f"recent range {round(recent_range, 2)}."
        )
        return confidence, state, narrative

    @classmethod
    def build_confidence(
        cls,
        *,
        confidence_score: int,
        state: str,
        killzone: dict[str, Any],
        narrative: str,
        smt: dict[str, Any],
    ) -> dict[str, Any]:
        """Return confidence-shaped observer payload."""
        return {
            "symbol": cls.SYMBOL,
            "display_symbol": cls.DISPLAY_SYMBOL,
            "confidence_band": state,
            "total_confidence": confidence_score,
            "decision": "REJECTED",
            "recommended_action": "Observe",
            "mode": "DEMO_SANDBOX",
            "observer_state": observer_state(state),
            "state_kind": "OBSERVER_MOVEMENT",
            "rejection_reasons": [cls.REJECTION_REASON],
            "warnings": ["Experimental observer symbol"],
            "guardrail_status": "BLOCKED",
            "guardrail_reasons": [cls.REJECTION_REASON],
            "guardrail": {
                "status": "BLOCKED",
                "execution_allowed": False,
                "reasons": [cls.REJECTION_REASON],
                "warnings": ["Experimental observer symbol"],
            },
            "killzone": killzone,
            "smt": smt,
            "narrative": {
                "symbol": cls.SYMBOL,
                "phase": "observer",
                "summary": narrative,
            },
            "explanation": [
                narrative,
                cls.REJECTION_REASON,
                "Advisor Mode only: BTCUSD diagnostics do not create execution permission.",
            ],
        }

    @classmethod
    def build_trade_plan(cls) -> dict[str, Any]:
        """Return a permanently blocked plan-shaped payload."""
        return {
            "symbol": cls.SYMBOL,
            "display_symbol": cls.DISPLAY_SYMBOL,
            "direction": None,
            "entry": {"price": 0.0, "source": "observer_only"},
            "stop_loss": {"price": 0.0, "distance": 0.0, "source": "observer_only"},
            "take_profit": {"tp1": 0.0, "tp2": 0.0, "tp3": 0.0},
            "risk": {"risk_amount": 0.0, "lot_size": 0.0, "rr_to_tp1": 0.0, "rr_to_tp2": 0.0, "rr_to_tp3": 0.0},
            "management": {},
            "plan_quality": "observer_only",
            "execution_allowed": False,
            "rejection_reasons": [cls.REJECTION_REASON],
            "explanation": [cls.REJECTION_REASON, "No order placement from BTC observer mode."],
        }
