"""Shared candidate scanner for Sentinel mode convergence.

The scanner creates candidate envelopes only. It does not approve trades,
submit orders, or bypass guardrails. Final approval remains the responsibility
of SharedDecisionAdapter and its decision brain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from backend.shared.confidence_band_registry import production_band


SCANNER_MODES = ("LIVE", "DEMO", "REPLAY", "BACKTEST", "PAPER")


@dataclass(frozen=True)
class CandidateEnvelope:
    """Canonical setup candidate envelope across Sentinel modes."""

    mode: str
    symbol: str
    candidate_detected: bool
    candidate_source: str
    plan: dict[str, Any]
    context: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "symbol": self.symbol,
            "candidate_detected": self.candidate_detected,
            "candidate_source": self.candidate_source,
            "plan": self.plan,
            "context": self.context,
        }


class SharedCandidateScanner:
    """Build candidate envelopes for live, demo, replay, backtest, and paper."""

    def scan_live_candidate(self, symbol: str, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a live candidate envelope without filtering the symbol scan."""
        normalized_symbol = symbol.upper().strip()
        payload = CandidateEnvelope(
            mode="LIVE",
            symbol=normalized_symbol,
            candidate_detected=True,
            candidate_source="continuous_live_symbol_scan",
            plan={"symbol": normalized_symbol, "candidate_detected": True, "execution_allowed": True},
            context=context or {},
        )
        return payload.to_dict()

    def scan_replay_candidate(
        self,
        symbol: str,
        history: pd.DataFrame,
        *,
        killzone: dict[str, Any],
        minimum_rr: float = 3.0,
    ) -> dict[str, Any]:
        """Return a causal closed-candle replay/backtest candidate envelope."""
        normalized_symbol = symbol.upper().strip()
        plan = self.build_closed_candle_plan(
            normalized_symbol,
            history,
            killzone=killzone,
            minimum_rr=minimum_rr,
        )
        payload = CandidateEnvelope(
            mode="BACKTEST",
            symbol=normalized_symbol,
            candidate_detected=bool(plan.get("candidate_detected", False)),
            candidate_source="closed_candle_shared_scanner",
            plan=plan,
            context={"killzone": killzone},
        )
        return payload.to_dict()

    def scan_paper_candidate(self, symbol: str, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a paper-mode candidate envelope."""
        normalized_symbol = symbol.upper().strip()
        payload = CandidateEnvelope(
            mode="PAPER",
            symbol=normalized_symbol,
            candidate_detected=True,
            candidate_source="paper_signal_candidate",
            plan={"symbol": normalized_symbol, "candidate_detected": True, "execution_allowed": True},
            context=context or {},
        )
        return payload.to_dict()

    @classmethod
    def build_closed_candle_plan(
        cls,
        symbol: str,
        history: pd.DataFrame,
        *,
        killzone: dict[str, Any],
        minimum_rr: float,
    ) -> dict[str, Any]:
        """Build a replay candidate from candles closed at the decision point."""
        lookback = history.tail(20)
        current = history.iloc[-1]
        previous = history.iloc[-21:-1]
        if len(previous) < 20:
            return {"symbol": symbol, "candidate_detected": False, "execution_allowed": False, "confidence": 0}

        prior_high = float(previous["high"].max())
        prior_low = float(previous["low"].min())
        close = float(current["close"])
        high = float(current["high"])
        low = float(current["low"])
        average_range = float((lookback["high"] - lookback["low"]).mean())
        candle_range = max(high - low, 0.0)

        direction = None
        if close > prior_high and candle_range >= average_range:
            direction = "bullish"
            entry = close
            stop = float(previous["low"].tail(10).min())
        elif close < prior_low and candle_range >= average_range:
            direction = "bearish"
            entry = close
            stop = float(previous["high"].tail(10).max())
        else:
            return {"symbol": symbol, "candidate_detected": False, "execution_allowed": False, "confidence": 0}

        stop_distance = abs(entry - stop)
        if stop_distance <= 0:
            return {"symbol": symbol, "candidate_detected": False, "execution_allowed": False, "confidence": 0}

        multiplier = 1 if direction == "bullish" else -1
        confidence = min(100, 70 + int(killzone.get("quality_score", 0)) * 2 + (10 if candle_range >= average_range * 1.5 else 0))
        confidence_band = production_band(confidence).band
        narrative_phase = cls.classify_narrative_phase(
            direction=direction,
            killzone_name=str(killzone.get("active_killzone", "none")),
            candle_range=candle_range,
            average_range=average_range,
        )
        raw_score_breakdown = cls.build_raw_score_breakdown(
            narrative_phase=narrative_phase,
            killzone=killzone,
            candle_range=candle_range,
            average_range=average_range,
        )
        score_breakdown = cls.map_diagnostic_score_breakdown(raw_score_breakdown)
        return {
            "symbol": symbol,
            "direction": direction,
            "confidence": confidence,
            "confidence_band": confidence_band,
            "narrative_phase": narrative_phase,
            "smt_detected": False,
            "planner_quality": "shared_closed_candle_candidate",
            "score_breakdown": score_breakdown,
            "raw_score_breakdown": raw_score_breakdown,
            "entry": {"price": round(entry, 5), "source": "shared_closed_candle_scanner"},
            "stop_loss": {"price": round(stop, 5), "distance": round(stop_distance, 5), "source": "shared_closed_candle_structure"},
            "take_profit": {
                "tp1": round(entry + multiplier * stop_distance, 5),
                "tp2": round(entry + multiplier * stop_distance * 2, 5),
                "tp3": round(entry + multiplier * stop_distance * minimum_rr, 5),
            },
            "candidate_detected": True,
            "execution_allowed": True,
            "engine_stack": {
                "scanner": "SharedCandidateScanner",
                "trend": "causal_closed_candle_structure",
                "liquidity": "closed_candle_breakout_level",
                "ict": "closed_candle_candidate_context",
                "narrative": "closed_candle_state",
                "killzone": killzone,
                "smt": "confirmation_only_not_required",
                "confidence": confidence,
                "score_breakdown": score_breakdown,
                "raw_score_breakdown": raw_score_breakdown,
                "planner": "shared_candidate_envelope",
            },
        }

    @staticmethod
    def classify_narrative_phase(
        *,
        direction: str,
        killzone_name: str,
        candle_range: float,
        average_range: float,
    ) -> str:
        if average_range <= 0:
            return "range"
        displacement_ratio = candle_range / average_range
        if displacement_ratio < 1.1:
            return "range"
        if "continuation" in killzone_name and displacement_ratio < 1.35:
            return "range"
        if direction == "bearish" and "continuation" in killzone_name:
            return "distribution"
        if direction == "bullish" and "continuation" in killzone_name:
            return "expansion"
        if displacement_ratio >= 1.5:
            return "expansion"
        return "reversal"

    @staticmethod
    def build_raw_score_breakdown(
        *,
        narrative_phase: str,
        killzone: dict[str, Any],
        candle_range: float,
        average_range: float,
    ) -> dict[str, int]:
        quality_score = int(killzone.get("quality_score", 0))
        displacement_ratio = candle_range / average_range if average_range > 0 else 0.0
        return {
            "daily_bias": 15,
            "h4_narrative": 20 if narrative_phase in {"expansion", "reversal", "distribution"} else 5,
            "liquidity_sweep": 20,
            "mss": 20 if displacement_ratio >= 1.0 else 0,
            "fvg_quality": 15 if displacement_ratio >= 1.5 else 10,
            "session_quality": 5 if quality_score >= 10 else (4 if quality_score >= 7 else 0),
            "target_clarity": 5,
            "smt": 0,
        }

    @staticmethod
    def map_diagnostic_score_breakdown(raw_scores: dict[str, int]) -> dict[str, int]:
        return {
            "daily_bias": int(raw_scores.get("daily_bias", 0)),
            "narrative": int(raw_scores.get("h4_narrative", 0)),
            "liquidity": int(raw_scores.get("liquidity_sweep", 0)),
            "mss": int(raw_scores.get("mss", 0)),
            "fvg": int(raw_scores.get("fvg_quality", 0)),
            "killzone": int(raw_scores.get("session_quality", 0)),
            "smt": int(raw_scores.get("smt", 0)),
        }


def shared_candidate_scanner_audit() -> dict[str, Any]:
    """Verify where the shared candidate scanner is actually enforced.

    Claims are recomputed from source inspection. Known honest gap: the
    backtest engine detects candidates with its own build_historical_plan
    instead of scan_replay_candidate, so backtest/replay enforcement is
    reported as False until that path converges.
    """
    from backend.shared.shared_decision_adapter import _source_contains

    violations: list[dict[str, str]] = []

    scanner_enforced_by_live = _source_contains(
        "backend.monitor.live_monitor", "LiveMonitor", "scan_live_candidate"
    )
    if not scanner_enforced_by_live:
        violations.append(
            {
                "severity": "HIGH",
                "file": "backend/monitor/live_monitor.py",
                "function": "LiveMonitor.analyze_symbol",
                "violation": "Live monitor does not call SharedCandidateScanner.scan_live_candidate.",
            }
        )

    scanner_enforced_by_backtest = _source_contains(
        "backend.backtesting.backtest_engine", "BacktestEngine", "scan_replay_candidate"
    )
    if not scanner_enforced_by_backtest:
        violations.append(
            {
                "severity": "MEDIUM",
                "file": "backend/backtesting/backtest_engine.py",
                "function": "BacktestEngine.build_historical_plan",
                "violation": "Backtest candidate detection uses build_historical_plan, not SharedCandidateScanner.scan_replay_candidate.",
            }
        )

    status = "PASS" if not violations else ("PARTIAL" if scanner_enforced_by_live else "FAIL")
    return {
        "status": status,
        "scanner_available": True,
        "scanner_enforced_by_live": scanner_enforced_by_live,
        "scanner_enforced_by_backtest": scanner_enforced_by_backtest,
        "scanner_enforced_by_replay": scanner_enforced_by_backtest,
        "remaining_violations": violations,
    }
