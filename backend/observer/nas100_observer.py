"""NAS100 observer mode for Project Sentinel diagnostics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from backend.killzone_engine.killzone_analyzer import KillzoneAnalyzer
from backend.market_data.mt5_connector import MT5Connector
from backend.shared.confidence_band_registry import observer_state
from backend.symbols.symbol_registry import SymbolRegistry


class NAS100Observer:
    """Collect NAS100 diagnostics without allowing execution."""

    SYMBOL = "NAS100"
    DISPLAY_SYMBOL = "NAS100 (OBSERVER)"
    REJECTION_REASON = "NAS100 demo sandbox: production execution disabled"

    def __init__(
        self,
        connector: MT5Connector,
        config_dir: str | Path | None = None,
        killzone_analyzer: KillzoneAnalyzer | None = None,
        registry: SymbolRegistry | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.connector = connector
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.killzone_analyzer = killzone_analyzer or KillzoneAnalyzer(config_dir=self.config_dir)
        self.registry = registry or SymbolRegistry(config_dir=self.config_dir)

    def observe(self) -> dict[str, Any]:
        """Return a normalized observer-only NAS100 snapshot."""
        killzone = self.safe_killzone()
        try:
            tick = self.connector.get_latest_tick(self.SYMBOL)
            candles = self.connector.get_historical_candles(self.SYMBOL, "M15", count=80)
            score, state, narrative = self.classify(candles, tick)
            available = True
            error = ""
        except Exception as exc:
            tick = {}
            candles = pd.DataFrame()
            score = 0
            state = "UNAVAILABLE"
            narrative = f"NAS100 live diagnostics unavailable: {exc}"
            available = False
            error = str(exc)
        confidence = self.build_confidence(score=score, state=state, killzone=killzone, narrative=narrative)
        trade_plan = self.build_trade_plan()
        return {
            "symbol": self.SYMBOL,
            "display_symbol": self.DISPLAY_SYMBOL,
            "experimental": False,
            "observer_mode": True,
            "mode": "DEMO_SANDBOX",
            "sandbox_mode": True,
            "available": available,
            "state": state,
            "score": score,
            "action": "OBSERVE" if available else "UNAVAILABLE",
            "tick": tick,
            "candles_loaded": int(len(candles)),
            "killzone": killzone,
            "narrative": confidence["narrative"],
            "smt": {"smt_detected": False, "confidence": 0, "explanation": ["NAS100 SMT not configured."]},
            "confidence": confidence,
            "trade_plan": trade_plan,
            "execution_allowed": False,
            "error": error,
        }

    def safe_killzone(self) -> dict[str, Any]:
        """Return killzone status or neutral observer metadata."""
        try:
            return self.killzone_analyzer.analyze(self.SYMBOL)
        except Exception:
            return {
                "symbol": self.SYMBOL,
                "active_killzone": "none",
                "is_valid": False,
                "quality_score": 0,
                "commentary": "NAS100 observer mode has no active execution killzone.",
            }

    @classmethod
    def classify(cls, candles: pd.DataFrame, tick: dict[str, Any]) -> tuple[int, str, str]:
        """Classify NAS100 observer state from recent M15 movement."""
        if candles.empty or "close" not in candles.columns:
            return 0, "UNAVAILABLE", "NAS100 candle diagnostics unavailable."
        first_close = float(candles["close"].iloc[0])
        last_close = float(candles["close"].iloc[-1])
        if first_close <= 0:
            return 0, "UNAVAILABLE", "NAS100 baseline price unavailable."
        percent_change = ((last_close - first_close) / first_close) * 100
        latest_price = float(tick.get("bid", last_close) or last_close)
        magnitude = abs(percent_change)
        score = min(85, max(20, int(35 + magnitude * 12)))
        if magnitude >= 1.5:
            state = "HOT"
        elif magnitude >= 0.5:
            state = "WARM"
        else:
            state = "COLD"
        direction = "up" if percent_change > 0 else ("down" if percent_change < 0 else "flat")
        narrative = (
            f"NAS100 observer mode: price is {direction} {round(percent_change, 2)}% "
            f"over the sampled M15 window. Latest price {round(latest_price, 2)}."
        )
        return score, state, narrative

    @classmethod
    def build_confidence(cls, *, score: int, state: str, killzone: dict[str, Any], narrative: str) -> dict[str, Any]:
        """Return confidence-shaped observer payload."""
        return {
            "symbol": cls.SYMBOL,
            "display_symbol": cls.DISPLAY_SYMBOL,
            "confidence_band": state,
            "total_confidence": score,
            "decision": "REJECTED",
            "recommended_action": "Observe",
            "mode": "DEMO_SANDBOX",
            "observer_state": observer_state(state),
            "state_kind": "OBSERVER_MOVEMENT",
            "rejection_reasons": [cls.REJECTION_REASON],
            "warnings": ["Observer-only symbol"],
            "guardrail_status": "BLOCKED",
            "guardrail_reasons": [cls.REJECTION_REASON],
            "guardrail": {
                "status": "BLOCKED",
                "execution_allowed": False,
                "reasons": [cls.REJECTION_REASON],
                "warnings": ["Observer-only symbol"],
            },
            "killzone": killzone,
            "smt": {"smt_detected": False, "confidence": 0},
            "narrative": {"symbol": cls.SYMBOL, "phase": "observer", "summary": narrative},
            "explanation": [
                narrative,
                cls.REJECTION_REASON,
                "Advisor Mode only: NAS100 diagnostics do not create execution permission.",
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
            "explanation": [cls.REJECTION_REASON, "No order placement from NAS100 observer mode."],
        }
