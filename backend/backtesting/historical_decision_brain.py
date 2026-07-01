"""Historical decision brain used by replay/backtest through SDA."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.guardrails.strategy_guardrails import StrategyGuardrails
from backend.confidence_engine.confidence_analyzer import ConfidenceAnalyzer


class HistoricalBacktestDecisionBrain:
    """Evaluate historical replay candidates through the live confidence brain.

    Replay/backtest remains side-effect free because all market-analysis payloads
    are supplied from closed historical candles, but final scoring, guardrails,
    rejection attribution, and action labeling are delegated to
    ConfidenceAnalyzer.analyze_payloads.
    """

    decision_source = "ConfidenceAnalyzer"

    def __init__(
        self,
        strategy_guardrails: StrategyGuardrails | None = None,
        config_dir: str | Path | None = None,
        confidence_analyzer: ConfidenceAnalyzer | None = None,
    ) -> None:
        self.strategy_guardrails = strategy_guardrails or StrategyGuardrails(config_dir=config_dir)
        self.confidence_analyzer = confidence_analyzer or ConfidenceAnalyzer(
            config_dir=config_dir,
            strategy_guardrails=self.strategy_guardrails,
        )
        self.confidence_analyzer.allowed_symbols = frozenset(
            set(self.confidence_analyzer.allowed_symbols)
            | {"BTCUSD", "NAS100", "USTEC", "US100", "EURUSD", "GBPUSD", "XAUUSD", "US30"}
        )

    def analyze(self, symbol: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a confidence-like decision payload for one historical candidate."""
        context = context or {}
        embedded_context = context.get("context", {}) or {}
        plan = context.get("plan", {}) or {}
        killzone = (
            context.get("killzone")
            or embedded_context.get("killzone")
            or plan.get("engine_stack", {}).get("killzone")
            or {}
        )
        replay_mss = context.get("replay_mss") or embedded_context.get("replay_mss") or {}
        historical_analysis = context.get("historical_analysis") or embedded_context.get("historical_analysis") or {}
        minimum_rr = float(context.get("minimum_rr", 3.0) or 3.0)
        trend = historical_analysis.get("trend", {}) or self.build_trend_payload(plan)
        liquidity = historical_analysis.get("liquidity", {}) or self.build_liquidity_payload(plan)
        ict = historical_analysis.get("ict", {}) or self.build_ict_payload(plan=plan, replay_mss=replay_mss)
        ict = {
            **ict,
            "mss": ict.get("mss") or replay_mss,
        }
        smt = {
            "smt_detected": bool(plan.get("smt_detected", False)),
            "confidence": plan.get("raw_score_breakdown", {}).get("smt", 0),
            "available": bool(plan.get("smt_available", False)),
            "direction": plan.get("smt_direction"),
        }
        narrative_phase = str(plan.get("narrative_phase", "range"))
        payload_context = {
            **context,
            "mode": str(context.get("mode", "BACKTEST")).upper(),
            "risk_reward": float(context.get("risk_reward", minimum_rr) or minimum_rr),
            "minimum_rr": minimum_rr,
            "killzone": killzone,
            "smt": smt,
            "narrative": {
                "phase": narrative_phase,
                "source": "historical_replay_closed_candle",
            },
            "narrative_phase": narrative_phase,
            "news_status": context.get(
                "news_status",
                {
                    "enabled": True,
                    "lock_active": False,
                    "source": "historical_replay_assumed_clear",
                },
            ),
            "replay_mss": replay_mss,
        }
        result = self.confidence_analyzer.analyze_payloads(
            symbol,
            trend=trend,
            liquidity=liquidity,
            ict=ict,
            context=payload_context,
        )
        extra_rejection_reasons = []
        if not bool(plan.get("execution_allowed", False)):
            extra_rejection_reasons.append("Historical candidate not execution allowed")
        if extra_rejection_reasons:
            result["rejection_reasons"] = self.dedupe_reasons(
                list(result.get("rejection_reasons", [])) + extra_rejection_reasons
            )
            result["decision"] = "REJECTED"
        hard_guardrail = self.strategy_guardrails.evaluate_hard(
            symbol=symbol,
            total_confidence=int(result.get("total_confidence", 0) or 0),
            killzone=killzone,
            smt=smt,
            narrative_phase=narrative_phase,
        )
        result.update(
            {
            "mode": str(context.get("mode", "BACKTEST")).upper(),
            "decision_source": self.decision_source,
            "historical_adapter_source": "HistoricalBacktestDecisionBrain",
            "sda_compliant": True,
            "live_equivalent": True,
            "score_breakdown": self.map_score_breakdown(result.get("scores", {})),
            "mss": ict.get("mss", replay_mss),
            "trend": trend,
            "liquidity": liquidity,
            "ict": ict,
            "killzone": killzone,
            "narrative_phase": narrative_phase,
            "hard_guardrail": hard_guardrail,
            "adaptive_guardrail": result.get("guardrail", {}),
            "truth_notes": [
                "Decision routed through SharedDecisionAdapter.",
                "Historical decision delegated to ConfidenceAnalyzer.analyze_payloads.",
                "Replay payloads are built from closed causal candles; no future candle data is passed into final scoring.",
            ],
            }
        )
        return result

    @staticmethod
    def build_trend_payload(plan: dict[str, Any]) -> dict[str, Any]:
        """Build a closed-candle trend payload when replay has no richer analyzer output."""
        direction = plan.get("direction")
        return {
            "daily_bias": direction,
            "h4_bias": direction,
            "overall_bias": direction,
            "source": "shared_closed_candle_candidate",
        }

    @staticmethod
    def build_liquidity_payload(plan: dict[str, Any]) -> dict[str, Any]:
        """Build a closed-candle liquidity payload from scanner evidence."""
        direction = plan.get("direction")
        target_key = "nearest_buy_side_target" if direction == "bullish" else "nearest_sell_side_target"
        targets = plan.get("take_profit", {}) or {}
        return {
            "latest_sweep": bool(plan.get("candidate_detected", False)),
            target_key: targets.get("tp3") or targets.get("tp2") or targets.get("tp1"),
            "source": "shared_closed_candle_candidate",
        }

    @staticmethod
    def build_ict_payload(*, plan: dict[str, Any], replay_mss: dict[str, Any]) -> dict[str, Any]:
        """Build a closed-candle ICT payload without future candle access."""
        direction = plan.get("direction")
        raw_scores = plan.get("raw_score_breakdown", {}) or {}
        mss_detected = bool(replay_mss.get("detected")) if replay_mss else int(raw_scores.get("mss", 0) or 0) > 0
        return {
            "mss": {
                "detected": mss_detected,
                "direction": replay_mss.get("direction") or direction,
                "mode": replay_mss.get("mode", "SHARED_CLOSED_CANDLE_SCANNER"),
            },
            "fvg": {
                "detected": int(raw_scores.get("fvg_quality", 0) or 0) > 0,
                "direction": direction,
                "grade": "A" if int(raw_scores.get("fvg_quality", 0) or 0) >= 15 else "B",
            },
            "order_block": {
                "detected": bool(plan.get("candidate_detected", False)),
                "direction": direction,
            },
            "premium_discount": {
                "current_zone": "discount" if direction == "bullish" else "premium",
            },
            "source": "shared_closed_candle_candidate",
        }

    @staticmethod
    def map_score_breakdown(scores: dict[str, int]) -> dict[str, int]:
        """Map raw confidence score names into legacy diagnostic names."""
        return {
            "daily_bias": int(scores.get("daily_bias", 0)),
            "narrative": int(scores.get("h4_narrative", 0)),
            "liquidity": int(scores.get("liquidity_sweep", 0)),
            "mss": int(scores.get("mss", 0)),
            "fvg": int(scores.get("fvg_quality", 0)),
            "killzone": int(scores.get("session_quality", 0)),
            "smt": int(scores.get("smt", 0)),
        }

    @staticmethod
    def dedupe_reasons(reasons: list[Any]) -> list[str]:
        """Return stable unique reason strings."""
        output: list[str] = []
        for reason in reasons:
            text = str(reason or "").strip()
            if text and text not in output:
                output.append(text)
        return output
