"""Project Sentinel historical backtesting engine."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from loguru import logger

from backend.backtesting.historical_decision_brain import HistoricalBacktestDecisionBrain
from backend.backtesting.trade_simulator import TradeSimulator
from backend.guardrails.strategy_guardrails import StrategyGuardrails
from backend.killzone_engine.killzone_analyzer import KillzoneAnalyzer
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.shared.confidence_band_registry import MSS_MODE_HISTORICAL_STRUCTURE_BREAK
from backend.shared.reason_ledger import DecisionMode, DecisionType, ReasonLedger, ReasonTier


class BacktestEngineError(RuntimeError):
    """Raised when backtesting cannot be completed."""


class BacktestEngine:
    """Run a deterministic Advisor Mode historical scan over candles."""

    CONFIDENCE_BANDS = ("HOT", "EXECUTION_READY")
    LONG_HORIZON_SYMBOLS = ("XAUUSD", "US30")
    LONG_HORIZON_KILLZONES = ("london_open", "new_york_open")
    LONG_HORIZON_DAYS = (30, 90)
    DIAGNOSTIC_KILLZONES = ("london_open", "london_continuation", "new_york_open", "new_york_continuation")
    NARRATIVE_PHASES = ("expansion", "reversal", "range", "accumulation", "distribution")
    SCORE_BREAKDOWN_KEYS = ("daily_bias", "narrative", "liquidity", "mss", "fvg", "killzone", "smt")
    RAW_SCORE_KEYS = ("daily_bias", "h4_narrative", "liquidity_sweep", "mss", "fvg_quality", "session_quality", "target_clarity", "smt")

    DEFAULT_CONFIG = {
        "lookback_days": 30,
        "symbols": ["XAUUSD", "US30", "EURUSD", "GBPUSD"],
        "symbol_expansion": {
            "observer_only": True,
            "affect_production": False,
        },
        "market_watch": {
            "enabled": True,
            "affect_production": False,
            "advisory_only": True,
        },
        "minimum_confidence": 90,
        "starting_balance": 5000,
        "risk_per_trade_percent": 0.5,
        "simulation": {
            "use_tp1_as_win": True,
            "use_tp3_as_full_win": True,
            "stop_at_sl": True,
        },
        "scan": {
            "timeframe": "M15",
            "warmup_candles": 60,
            "forward_candles": 24,
            "step_candles": 4,
            "minimum_rr": 3.0,
        },
    }

    def __init__(
        self,
        connector: MT5Connector | None = None,
        config_dir: str | Path | None = None,
        simulator: TradeSimulator | None = None,
        decision_brain: HistoricalBacktestDecisionBrain | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.connector = connector or MT5Connector()
        self.config = self._deep_merge(self.DEFAULT_CONFIG, self._load_yaml_file(self.config_dir / "backtesting.yaml"))
        self.killzone_analyzer = KillzoneAnalyzer(config_dir=self.config_dir)
        self.strategy_guardrails = StrategyGuardrails(config_dir=self.config_dir)
        self.simulator = simulator or TradeSimulator(self.config.get("simulation", {}))
        self.decision_brain = decision_brain or HistoricalBacktestDecisionBrain(
            strategy_guardrails=self.strategy_guardrails,
            config_dir=self.config_dir,
        )
        # One ReasonLedger dict per structural candidate (admitted AND rejected),
        # each with a replay outcome, so QAER/FRR are measurable per Law 4.
        self.candidate_ledgers: list[dict[str, Any]] = []

    def reset_candidate_ledgers(self) -> None:
        """Clear accumulated per-candidate reason ledgers before a new run."""
        self.candidate_ledgers = []

    def run(self, lookback_days: int | None = None, symbols: list[str] | None = None) -> dict[str, Any]:
        """Run the configured backtest and return aggregate metrics."""
        self.reset_candidate_ledgers()
        lookback_days = int(lookback_days or self.config["lookback_days"])
        symbols = [str(symbol).upper().strip() for symbol in (symbols or self.config["symbols"])]
        trades: list[dict[str, Any]] = []
        setups_scanned = 0

        for symbol in symbols:
            candles = self.fetch_backtest_candles(symbol, lookback_days)
            symbol_trades, symbol_setups = self.scan_symbol(symbol, candles)
            trades.extend(symbol_trades)
            setups_scanned += symbol_setups

        return self.aggregate_results(
            trades=trades,
            setups_scanned=setups_scanned,
            starting_balance=float(self.config["starting_balance"]),
            risk_per_trade_percent=float(self.config["risk_per_trade_percent"]),
            symbols=symbols,
            strategy_guardrails=self.strategy_guardrails,
        )

    def run_long_horizon(
        self,
        *,
        days_options: tuple[int, ...] | list[int] | None = None,
        symbols: list[str] | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Run the standard long-horizon matrix across off, hard, and adaptive modes."""
        days_values = tuple(int(days) for days in (days_options or self.LONG_HORIZON_DAYS))
        selected_symbols = [str(symbol).upper().strip() for symbol in (symbols or list(self.LONG_HORIZON_SYMBOLS))]
        matrix: dict[int, dict[str, Any]] = {}
        for days in days_values:
            raw_results = self.run(lookback_days=days, symbols=selected_symbols)
            matrix[days] = {
                "guardrails_off": self.summarize_guardrail_mode(
                    raw_results,
                    mode="off",
                    starting_balance=float(self.config["starting_balance"]),
                    symbols=selected_symbols,
                ),
                "old_hard_guardrails": self.summarize_guardrail_mode(
                    raw_results,
                    mode="hard",
                    starting_balance=float(self.config["starting_balance"]),
                    symbols=selected_symbols,
                ),
                "adaptive_guardrails": self.summarize_guardrail_mode(
                    raw_results,
                    mode="adaptive",
                    starting_balance=float(self.config["starting_balance"]),
                    symbols=selected_symbols,
                ),
            }
        return matrix

    # MT5 rejects candle requests above its max-bars-in-chart setting
    # (default 100k) with (-2, 'Invalid params'); stay safely below it.
    MAX_CANDLE_REQUEST = 99_000

    def fetch_backtest_candles(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        """Fetch enough M15 candles for the configured lookback and forward walk."""
        scan_config = self.config.get("scan", {})
        timeframe = str(scan_config.get("timeframe", "M15"))
        forward = int(scan_config.get("forward_candles", 24))
        count = max(lookback_days * 96 + forward + int(scan_config.get("warmup_candles", 60)), 120)
        if count > self.MAX_CANDLE_REQUEST:
            logger.warning(
                "Candle request for {} capped at {} bars (requested {}); actual data span is reported honestly.",
                symbol,
                self.MAX_CANDLE_REQUEST,
                count,
            )
            count = self.MAX_CANDLE_REQUEST
        try:
            candles = self.connector.get_historical_candles(symbol, timeframe, count=count)
        except Exception as exc:
            raise BacktestEngineError(f"Failed to fetch backtest candles for {symbol}: {exc}") from exc
        if candles.empty:
            raise BacktestEngineError(f"No backtest candles returned for {symbol}.")
        return self.normalize_candles(candles)

    def scan_symbol(self, symbol: str, candles: pd.DataFrame) -> tuple[list[dict[str, Any]], int]:
        """Scan one symbol's candles for historical candidate plans.

        Every candidate is scored by the live confidence brain via
        HistoricalBacktestDecisionBrain: same weights, same hard rejections,
        same guardrail path as live. Guardrail-only rejections keep the trade
        in the record so guardrails-off vs. adaptive comparisons stay possible;
        confidence or structural rejections drop it, exactly as live would.
        """
        scan_config = self.config.get("scan", {})
        warmup = int(scan_config.get("warmup_candles", 60))
        forward = int(scan_config.get("forward_candles", 24))
        step = max(int(scan_config.get("step_candles", 4)), 1)
        minimum_rr = float(scan_config.get("minimum_rr", 3.0))
        trades: list[dict[str, Any]] = []
        setups_scanned = 0

        end = max(len(candles) - forward, warmup)
        for index in range(warmup, end, step):
            history = candles.iloc[:index + 1]
            current_candle = history.iloc[-1]
            killzone = self.killzone_analyzer.analyze(symbol, current_time=current_candle.get("time"))
            if not killzone.get("is_valid"):
                continue

            setups_scanned += 1
            plan = self.build_historical_plan(symbol, history, killzone)
            if not plan.get("candidate_detected"):
                continue

            decision = self.decision_brain.analyze(
                symbol,
                context={
                    "mode": "BACKTEST",
                    "minimum_rr": minimum_rr,
                    "risk_reward": minimum_rr,
                    "plan": plan,
                    "killzone": killzone,
                    "replay_mss": {
                        "detected": True,
                        "direction": plan.get("direction"),
                        "mode": MSS_MODE_HISTORICAL_STRUCTURE_BREAK,
                    },
                },
            )
            adaptive_guardrail = decision.get("adaptive_guardrail") or decision.get("guardrail", {}) or {}
            hard_guardrail = decision.get("hard_guardrail", {}) or {}
            guardrail_reasons = set(adaptive_guardrail.get("reasons", []) or [])
            blocking_reasons = [
                reason
                for reason in decision.get("rejection_reasons", []) or []
                if reason not in guardrail_reasons
            ]

            # Simulate every structural candidate — including rejected ones —
            # so each rejection carries a measurable would-have-won outcome.
            simulation = self.simulator.simulate(plan, candles.iloc[index + 1:index + 1 + forward])
            timestamp = (
                current_candle.get("time").isoformat()
                if hasattr(current_candle.get("time"), "isoformat")
                else str(current_candle.get("time", ""))
            )
            self.record_candidate_ledger(
                symbol=symbol,
                timestamp=timestamp,
                decision=decision,
                adaptive_guardrail=adaptive_guardrail,
                blocking_reasons=blocking_reasons,
                simulation=simulation,
            )
            if blocking_reasons:
                continue

            confidence = int(decision.get("total_confidence", 0) or 0)
            confidence_band = str(decision.get("confidence_band", "HOT"))
            trade = {
                "symbol": symbol,
                "timestamp": timestamp,
                "killzone": killzone.get("active_killzone", "none"),
                "confidence_band": confidence_band,
                "guarded_confidence_band": adaptive_guardrail.get("adjusted_confidence_band", confidence_band),
                "confidence": confidence,
                "guardrail_adjusted_confidence": adaptive_guardrail.get("guardrail_adjusted_confidence", confidence),
                "guardrail_penalty_total": adaptive_guardrail.get("guardrail_penalty_total", 0),
                "guardrail_warnings": adaptive_guardrail.get("guardrail_warnings", []),
                "direction": plan.get("direction"),
                "narrative_phase": plan.get("narrative_phase", "range"),
                "smt_detected": plan.get("smt_detected", False),
                "smt_available": False,
                "planner_quality": plan.get("planner_quality", "historical_valid"),
                "score_breakdown": decision.get("score_breakdown", plan.get("score_breakdown", {})),
                "raw_score_breakdown": decision.get("scores", plan.get("raw_score_breakdown", {})),
                "guardrail": adaptive_guardrail,
                "hard_guardrail": hard_guardrail,
                "adaptive_guardrail": adaptive_guardrail,
                "mss_mode": MSS_MODE_HISTORICAL_STRUCTURE_BREAK,
                "mss_confirmed": True,
                "decision_source": decision.get("decision_source", "ConfidenceAnalyzer"),
                "sda_compliant": bool(decision.get("sda_compliant", False)),
                "rejection_reasons": decision.get("rejection_reasons", []),
                "guarded_execution_allowed": adaptive_guardrail.get("status") != "BLOCKED",
                "plan": plan,
                "simulation": simulation,
            }
            trades.append(trade)

        return trades, setups_scanned

    def record_candidate_ledger(
        self,
        *,
        symbol: str,
        timestamp: str,
        decision: dict[str, Any],
        adaptive_guardrail: dict[str, Any],
        blocking_reasons: list[str],
        simulation: dict[str, Any],
    ) -> dict[str, Any]:
        """Record one candidate's full reasoning + replay outcome (Law 4)."""
        confidence = int(decision.get("total_confidence", 0) or 0)
        adjusted = int(adaptive_guardrail.get("guardrail_adjusted_confidence", confidence) or confidence)
        guardrail_blocked = adaptive_guardrail.get("status") == "BLOCKED"
        admitted = not blocking_reasons and not guardrail_blocked
        base_risk = float(self.config.get("risk_per_trade_percent", 0.5))
        outcome = str(simulation.get("outcome", "BREAKEVEN"))
        replay_status = {
            "WIN": "would_have_won",
            "LOSS": "would_have_lost",
        }.get(outcome, "inconclusive")

        ledger = ReasonLedger(
            symbol=symbol,
            timestamp=timestamp,
            mode=DecisionMode.REPLAY,
            decision=DecisionType.ADMIT if admitted else DecisionType.REJECT,
            confidence_original=max(min(confidence, 100), 0),
            confidence_final=max(min(adjusted, 100), 0),
            risk_original=base_risk,
            risk_final=base_risk if admitted else 0.0,
            confidence_components=dict(decision.get("scores", {}) or {}),
            replay_outcome_status=replay_status,
        )
        for reason in blocking_reasons:
            tier, severity = self.classify_reason_tier(reason)
            ledger.add_veto(
                tier=tier,
                reason_code=self.reason_code_for(reason),
                severity=severity,
                message=reason,
                confidence_before=ledger.confidence_original,
                confidence_after=ledger.confidence_final,
                risk_before=base_risk,
                risk_after=0.0,
            )
        for reason in adaptive_guardrail.get("reasons", []) or []:
            tier, severity = self.classify_reason_tier(reason)
            ledger.add_veto(
                tier=tier,
                reason_code=self.reason_code_for(reason),
                severity=severity,
                message=reason,
                confidence_before=ledger.confidence_original,
                confidence_after=ledger.confidence_final,
                risk_before=base_risk,
                risk_after=0.0,
            )
        for penalty in adaptive_guardrail.get("guardrail_penalties", []) or []:
            ledger.add_penalty(
                tier=ReasonTier.TIER_2B_MACRO_CONFIDENCE,
                reason_code=self.reason_code_for(str(penalty.get("reason", "PENALTY"))),
                severity=0.3,
                message=str(penalty.get("reason", "")),
                confidence_before=ledger.confidence_original,
                confidence_after=ledger.confidence_final,
                risk_before=base_risk,
                risk_after=base_risk,
                metadata={"value": penalty.get("value", 0)},
            )
        record = ledger.to_dict()
        record["simulated_rr"] = float(simulation.get("rr", 0.0))
        self.candidate_ledgers.append(record)
        return record

    @staticmethod
    def reason_code_for(reason: str) -> str:
        """Normalize a prose reason string into a stable reason code."""
        cleaned = "".join(char if char.isalnum() or char.isspace() else " " for char in str(reason).upper())
        return "_".join(cleaned.split())[:64] or "UNKNOWN"

    @staticmethod
    def classify_reason_tier(reason: str) -> tuple[ReasonTier, float]:
        """Map a rejection reason string to its constitutional tier and severity."""
        text = str(reason).lower()
        if any(key in text for key in ("news lock", "daily loss", "max trades", "risk governor", "demo sandbox", "observer", "disabled by strategy guardrail", "not execution allowed")):
            return ReasonTier.TIER_1_SAFETY, 1.0
        if any(key in text for key in ("killzone", "session blocked", "london continuation", "london open")):
            return ReasonTier.TIER_2A_MACRO_TRUTH, 0.8
        if any(key in text for key in ("mss", "fvg", "order block", "premium", "discount", "stop", "directional", "sweep")):
            return ReasonTier.TIER_3A_STRUCTURAL_VALIDITY, 0.9
        if any(key in text for key in ("smt", "confidence", "grade")):
            return ReasonTier.TIER_3B_SETUP_QUALITY, 0.5
        return ReasonTier.TIER_2B_MACRO_CONFIDENCE, 0.4

    @staticmethod
    def summarize_candidate_ledgers(
        ledgers: list[dict[str, Any]],
        *,
        high_quality_confidence: int = 70,
    ) -> dict[str, Any]:
        """Compute QAER/FRR and rejection attribution from candidate ledgers.

        Per SENTINEL_CONSTITUTION.md section 7:
          QAER = winning admitted trades / high-quality candidates
          FRR  = rejected would-have-won candidates / high-quality candidates
        """
        total = len(ledgers)
        admitted = [item for item in ledgers if item.get("decision") == "admit"]
        rejected = [item for item in ledgers if item.get("decision") != "admit"]
        high_quality = [item for item in ledgers if int(item.get("confidence_original", 0)) >= high_quality_confidence]
        hq_admitted_winners = [
            item for item in high_quality
            if item.get("decision") == "admit" and item.get("replay_outcome_status") == "would_have_won"
        ]
        hq_rejected_winners = [
            item for item in high_quality
            if item.get("decision") != "admit" and item.get("replay_outcome_status") == "would_have_won"
        ]
        rejection_breakdown: dict[str, dict[str, Any]] = {}
        for item in rejected:
            code = str(item.get("primary_reason") or "UNCLASSIFIED")
            row = rejection_breakdown.setdefault(
                code, {"count": 0, "would_have_won": 0, "would_have_lost": 0, "inconclusive": 0, "missed_rr": 0.0}
            )
            row["count"] += 1
            status = str(item.get("replay_outcome_status") or "inconclusive")
            row[status if status in row else "inconclusive"] += 1
            if status == "would_have_won":
                row["missed_rr"] = round(row["missed_rr"] + max(float(item.get("simulated_rr", 0.0)), 0.0), 2)
        tier_breakdown: dict[str, int] = {}
        for item in rejected:
            for entry in item.get("entries", []) or []:
                if entry.get("action") == "veto":
                    tier = str(entry.get("tier", "unknown"))
                    tier_breakdown[tier] = tier_breakdown.get(tier, 0) + 1
        hq_count = len(high_quality)
        return {
            "total_candidates": total,
            "admitted": len(admitted),
            "rejected": len(rejected),
            "admitted_would_have_won": sum(1 for item in admitted if item.get("replay_outcome_status") == "would_have_won"),
            "rejected_would_have_won": sum(1 for item in rejected if item.get("replay_outcome_status") == "would_have_won"),
            "rejected_would_have_lost": sum(1 for item in rejected if item.get("replay_outcome_status") == "would_have_lost"),
            "high_quality_candidates": hq_count,
            "high_quality_threshold": high_quality_confidence,
            "qaer": round(len(hq_admitted_winners) / hq_count * 100, 2) if hq_count else 0.0,
            "frr": round(len(hq_rejected_winners) / hq_count * 100, 2) if hq_count else 0.0,
            "rejection_reason_breakdown": dict(
                sorted(rejection_breakdown.items(), key=lambda pair: -pair[1]["count"])
            ),
            "tier_veto_breakdown": tier_breakdown,
        }

    def build_historical_plan(self, symbol: str, history: pd.DataFrame, killzone: dict[str, Any]) -> dict[str, Any]:
        """Build a structural candidate plan from candle structure.

        This method only detects the candidate (structure break with
        displacement) and builds price levels. Scoring, admission, and
        guardrails belong to HistoricalBacktestDecisionBrain so that replay
        decisions match the live confidence path.
        """
        lookback = history.tail(20)
        current = history.iloc[-1]
        previous = history.iloc[-21:-1]
        if len(previous) < 20:
            return {"candidate_detected": False, "execution_allowed": False}

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
            return {"candidate_detected": False, "execution_allowed": False}

        stop_distance = abs(entry - stop)
        if stop_distance <= 0:
            return {"candidate_detected": False, "execution_allowed": False}

        minimum_rr = float(self.config.get("scan", {}).get("minimum_rr", 3.0))
        multiplier = 1 if direction == "bullish" else -1
        narrative_phase = self.classify_narrative_phase(
            direction=direction,
            killzone_name=str(killzone.get("active_killzone", "none")),
            candle_range=candle_range,
            average_range=average_range,
        )
        raw_score_breakdown = self.build_raw_score_breakdown(
            narrative_phase=narrative_phase,
            killzone=killzone,
            candle_range=candle_range,
            average_range=average_range,
        )
        score_breakdown = self.map_diagnostic_score_breakdown(raw_score_breakdown)

        return {
            "symbol": symbol,
            "direction": direction,
            "candidate_detected": True,
            "narrative_phase": narrative_phase,
            "smt_detected": False,
            "smt_available": False,
            "planner_quality": "historical_valid",
            "score_breakdown": score_breakdown,
            "raw_score_breakdown": raw_score_breakdown,
            "entry": {"price": round(entry, 5), "source": "historical_breakout"},
            "stop_loss": {"price": round(stop, 5), "distance": round(stop_distance, 5), "source": "historical_structure"},
            "take_profit": {
                "tp1": round(entry + multiplier * stop_distance, 5),
                "tp2": round(entry + multiplier * stop_distance * 2, 5),
                "tp3": round(entry + multiplier * stop_distance * minimum_rr, 5),
            },
            "execution_allowed": True,
            "engine_stack": {
                "trend": "historical_candle_structure",
                "liquidity": "breakout_level",
                "ict": "structure_break_with_displacement",
                "narrative": "historical_state_proxy",
                "killzone": killzone,
                "smt": "historical_smt_not_computed",
                "score_breakdown": score_breakdown,
                "raw_score_breakdown": raw_score_breakdown,
                "planner": "historical_plan",
                "decision_authority": "HistoricalBacktestDecisionBrain",
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
        """Classify the historical plan into a simple diagnostic narrative phase."""
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
        """Return raw confidence-style score components for auditability."""
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
        """Map raw score keys into diagnostic names for reporting."""
        return {
            "daily_bias": int(raw_scores.get("daily_bias", 0)),
            "narrative": int(raw_scores.get("h4_narrative", 0)),
            "liquidity": int(raw_scores.get("liquidity_sweep", 0)),
            "mss": int(raw_scores.get("mss", 0)),
            "fvg": int(raw_scores.get("fvg_quality", 0)),
            "killzone": int(raw_scores.get("session_quality", 0)),
            "smt": int(raw_scores.get("smt", 0)),
        }

    @classmethod
    def aggregate_results(
        cls,
        *,
        trades: list[dict[str, Any]],
        setups_scanned: int,
        starting_balance: float,
        risk_per_trade_percent: float,
        symbols: list[str],
        strategy_guardrails: StrategyGuardrails | None = None,
    ) -> dict[str, Any]:
        """Aggregate trade simulations into backtest metrics."""
        risk_amount = starting_balance * (risk_per_trade_percent / 100)
        guardrails = strategy_guardrails or StrategyGuardrails()
        enriched = cls.apply_guardrails_to_trades(cls.enrich_trade_pnl(trades, risk_amount), guardrails)
        guarded_trades = cls.filter_trades_by_guardrail_mode(enriched, mode="adaptive")
        hard_guarded_trades = cls.filter_trades_by_guardrail_mode(enriched, mode="hard")
        overall = cls.calculate_metrics(enriched, setups_scanned, starting_balance)
        after_guardrails = cls.calculate_metrics(guarded_trades, setups_scanned, starting_balance)
        after_hard_guardrails = cls.calculate_metrics(hard_guarded_trades, setups_scanned, starting_balance)
        by_symbol = {symbol: cls.calculate_metrics([trade for trade in enriched if trade["symbol"] == symbol], 0, starting_balance) for symbol in symbols}
        by_confidence_band = {
            band: cls.calculate_metrics([trade for trade in enriched if trade["confidence_band"] == band], 0, starting_balance)
            for band in cls.CONFIDENCE_BANDS
        }
        by_killzone = {
            killzone: cls.calculate_metrics([trade for trade in enriched if trade["killzone"] == killzone], 0, starting_balance)
            for killzone in cls.DIAGNOSTIC_KILLZONES
        }
        by_narrative_phase = {
            phase: cls.calculate_metrics([trade for trade in enriched if trade.get("narrative_phase") == phase], 0, starting_balance)
            for phase in cls.NARRATIVE_PHASES
        }
        diagnostics = cls.build_diagnostics(
            enriched=enriched,
            by_symbol=by_symbol,
            by_killzone=by_killzone,
            by_narrative_phase=by_narrative_phase,
        )
        return {
            "overall": overall,
            "after_guardrails": after_guardrails,
            "after_hard_guardrails": after_hard_guardrails,
            "guardrail_impact": cls.build_guardrail_impact(enriched, guarded_trades, overall, after_guardrails),
            "hard_guardrail_impact": cls.build_guardrail_impact(
                enriched,
                hard_guarded_trades,
                overall,
                after_hard_guardrails,
                guardrail_key="hard_guardrail",
            ),
            "by_symbol": by_symbol,
            "by_confidence_band": by_confidence_band,
            "by_killzone": by_killzone,
            "by_narrative_phase": by_narrative_phase,
            "losing_trade_analysis": diagnostics["losing_trade_analysis"],
            "engine_score_contribution": diagnostics["engine_score_contribution"],
            "diagnostics": diagnostics,
            "trades": enriched,
        }

    @classmethod
    def summarize_guardrail_mode(
        cls,
        results: dict[str, Any],
        *,
        starting_balance: float,
        mode: str | None = None,
        guardrails_enabled: bool | None = None,
        symbols: list[str] | None = None,
        killzones: tuple[str, ...] | list[str] | None = None,
        narrative_phases: tuple[str, ...] | list[str] | None = None,
    ) -> dict[str, Any]:
        """Return metrics and breakdowns for one guardrail mode."""
        all_trades = list(results.get("trades", []))
        selected_mode = mode or ("adaptive" if guardrails_enabled else "off")
        selected_trades = cls.filter_trades_by_guardrail_mode(all_trades, mode=selected_mode)
        setups_scanned = int(results.get("overall", {}).get("setups_scanned", 0))
        selected_symbols = symbols or sorted({str(trade.get("symbol")) for trade in all_trades if trade.get("symbol")})
        selected_killzones = tuple(killzones or cls.LONG_HORIZON_KILLZONES)
        selected_phases = tuple(narrative_phases or cls.NARRATIVE_PHASES)

        return {
            "guardrails": selected_mode.upper(),
            "overall": cls.calculate_metrics(selected_trades, setups_scanned, starting_balance),
            "by_symbol": {
                symbol: cls.calculate_metrics(
                    [trade for trade in selected_trades if trade.get("symbol") == symbol],
                    0,
                    starting_balance,
                )
                for symbol in selected_symbols
            },
            "by_killzone": {
                killzone: cls.calculate_metrics(
                    [trade for trade in selected_trades if trade.get("killzone") == killzone],
                    0,
                    starting_balance,
                )
                for killzone in selected_killzones
            },
            "by_narrative": {
                phase: cls.calculate_metrics(
                    [trade for trade in selected_trades if trade.get("narrative_phase") == phase],
                    0,
                    starting_balance,
                )
                for phase in selected_phases
            },
        }

    @staticmethod
    def filter_trades_by_guardrail_mode(
        trades: list[dict[str, Any]],
        *,
        mode: str,
    ) -> list[dict[str, Any]]:
        """Return all trades or only trades that passed a guardrail mode."""
        if mode == "off":
            return list(trades)
        if mode == "hard":
            return [trade for trade in trades if trade.get("hard_guardrail", trade.get("guardrail", {})).get("status") != "BLOCKED"]
        if mode == "adaptive":
            return [trade for trade in trades if trade.get("adaptive_guardrail", trade.get("guardrail", {})).get("status") != "BLOCKED"]
        raise ValueError("Guardrail mode must be 'off', 'hard', or 'adaptive'.")

    @staticmethod
    def qualifies_for_phase_three(metrics: dict[str, Any]) -> bool:
        """Return whether the 90-day guarded result qualifies for automation research."""
        return (
            float(metrics.get("profit_factor", 0.0)) > 1.5
            and float(metrics.get("win_rate", 0.0)) > 50.0
            and float(metrics.get("max_drawdown", 0.0)) < 6.0
        )

    @staticmethod
    def enrich_trade_pnl(trades: list[dict[str, Any]], risk_amount: float) -> list[dict[str, Any]]:
        """Add PnL values to trade records from RR results."""
        enriched = []
        for trade in trades:
            rr = float(trade.get("simulation", {}).get("rr", 0.0))
            pnl = rr * risk_amount
            enriched.append({**trade, "rr": rr, "pnl": round(pnl, 2)})
        return enriched

    @classmethod
    def apply_guardrails_to_trades(
        cls,
        trades: list[dict[str, Any]],
        guardrails: StrategyGuardrails,
    ) -> list[dict[str, Any]]:
        """Attach guardrail evaluations to backtest trade records."""
        guarded: list[dict[str, Any]] = []
        for trade in trades:
            hard_guardrail = trade.get("hard_guardrail") or guardrails.evaluate_hard(
                symbol=str(trade.get("symbol", "")),
                total_confidence=int(trade.get("confidence", 0)),
                killzone={"active_killzone": trade.get("killzone", "none")},
                smt={
                    "smt_detected": bool(trade.get("smt_detected", False)),
                    "confidence": trade.get("score_breakdown", {}).get("smt", 0),
                    "available": bool(trade.get("smt_available", False)),
                },
                narrative_phase=str(trade.get("narrative_phase", "range")),
            )
            adaptive_guardrail = trade.get("adaptive_guardrail") or trade.get("guardrail") or guardrails.evaluate(
                symbol=str(trade.get("symbol", "")),
                total_confidence=int(trade.get("confidence", 0)),
                killzone={"active_killzone": trade.get("killzone", "none")},
                smt={
                    "smt_detected": bool(trade.get("smt_detected", False)),
                    "confidence": trade.get("score_breakdown", {}).get("smt", 0),
                    "available": bool(trade.get("smt_available", False)),
                },
                narrative_phase=str(trade.get("narrative_phase", "range")),
                rr_to_final=3.0,
                mode="adaptive",
            )
            guarded.append(
                {
                    **trade,
                    "guardrail": adaptive_guardrail,
                    "hard_guardrail": hard_guardrail,
                    "adaptive_guardrail": adaptive_guardrail,
                    "guardrail_adjusted_confidence": adaptive_guardrail.get("guardrail_adjusted_confidence", trade.get("confidence", 0)),
                    "guardrail_penalty_total": adaptive_guardrail.get("guardrail_penalty_total", 0),
                    "guardrail_warnings": adaptive_guardrail.get("guardrail_warnings", []),
                    "guarded_confidence_band": adaptive_guardrail.get("adjusted_confidence_band", trade.get("confidence_band", "HOT")),
                    "guarded_execution_allowed": adaptive_guardrail.get("status") != "BLOCKED",
                }
            )
        return guarded

    @staticmethod
    def build_guardrail_impact(
        before_trades: list[dict[str, Any]],
        after_trades: list[dict[str, Any]],
        before_metrics: dict[str, Any],
        after_metrics: dict[str, Any],
        guardrail_key: str = "guardrail",
    ) -> dict[str, Any]:
        """Compare backtest metrics before and after guardrails."""
        filtered = [trade for trade in before_trades if trade.get(guardrail_key, {}).get("status") == "BLOCKED"]
        reasons = [
            reason
            for trade in filtered
            for reason in trade.get(guardrail_key, {}).get("reasons", [])
        ]
        worst_filtered_condition = Counter(reasons).most_common(1)[0][0] if reasons else "none"
        return {
            "before": {
                "trades": before_metrics.get("trades_approved", 0),
                "winrate": before_metrics.get("win_rate", 0.0),
                "profit_factor": before_metrics.get("profit_factor", 0.0),
            },
            "after": {
                "trades": after_metrics.get("trades_approved", 0),
                "winrate": after_metrics.get("win_rate", 0.0),
                "profit_factor": after_metrics.get("profit_factor", 0.0),
            },
            "trades_removed": len(before_trades) - len(after_trades),
            "worst_filtered_condition": worst_filtered_condition,
        }

    @classmethod
    def calculate_metrics(
        cls,
        trades: list[dict[str, Any]],
        setups_scanned: int = 0,
        starting_balance: float = 0.0,
    ) -> dict[str, Any]:
        """Calculate standard backtest metrics for a trade subset."""
        wins = sum(1 for trade in trades if trade.get("simulation", {}).get("outcome") == "WIN")
        losses = sum(1 for trade in trades if trade.get("simulation", {}).get("outcome") == "LOSS")
        breakevens = sum(1 for trade in trades if trade.get("simulation", {}).get("outcome") == "BREAKEVEN")
        closed = wins + losses
        gross_profit = round(sum(max(float(trade.get("pnl", 0.0)), 0.0) for trade in trades), 2)
        gross_loss = round(abs(sum(min(float(trade.get("pnl", 0.0)), 0.0) for trade in trades)), 2)
        rr_values = [float(trade.get("rr", 0.0)) for trade in trades]
        return {
            "setups_scanned": setups_scanned,
            "trades_approved": len(trades),
            "trades": len(trades),
            "wins": wins,
            "losses": losses,
            "breakevens": breakevens,
            "win_rate": round((wins / closed) * 100, 2) if closed else 0.0,
            "winrate": round((wins / closed) * 100, 2) if closed else 0.0,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "net_profit": round(gross_profit - gross_loss, 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else (round(gross_profit, 2) if gross_profit else 0.0),
            "max_drawdown": cls.calculate_max_drawdown([float(trade.get("pnl", 0.0)) for trade in trades], starting_balance),
            "average_rr": round(sum(rr_values) / len(rr_values), 2) if rr_values else 0.0,
            "avg_rr": round(sum(rr_values) / len(rr_values), 2) if rr_values else 0.0,
            "net_rr": round(sum(rr_values), 2),
        }

    @classmethod
    def build_diagnostics(
        cls,
        *,
        enriched: list[dict[str, Any]],
        by_symbol: dict[str, dict[str, Any]],
        by_killzone: dict[str, dict[str, Any]],
        by_narrative_phase: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Build diagnostic analytics that explain where losses cluster."""
        loss_trades = [trade for trade in enriched if trade.get("simulation", {}).get("outcome") == "LOSS"]
        loss_records = [cls.loss_record(trade) for trade in loss_trades]
        loss_cluster = cls.build_loss_cluster(loss_records)
        winning_score_average = cls.average_score_breakdown(
            [trade for trade in enriched if trade.get("simulation", {}).get("outcome") == "WIN"]
        )
        losing_score_average = cls.average_score_breakdown(loss_trades)
        best_symbol, best_symbol_metrics = cls.best_bucket(by_symbol)
        worst_symbol, worst_symbol_metrics = cls.worst_bucket(by_symbol)
        best_killzone, best_killzone_metrics = cls.best_bucket(by_killzone)
        worst_killzone, worst_killzone_metrics = cls.worst_bucket(by_killzone)
        worst_phase, worst_phase_metrics = cls.worst_bucket(by_narrative_phase)

        return {
            "best_symbol": {"name": best_symbol, "metrics": best_symbol_metrics},
            "worst_symbol": {"name": worst_symbol, "metrics": worst_symbol_metrics},
            "best_killzone": {"name": best_killzone, "metrics": best_killzone_metrics},
            "worst_killzone": {"name": worst_killzone, "metrics": worst_killzone_metrics},
            "worst_narrative_phase": {"name": worst_phase, "metrics": worst_phase_metrics},
            "losing_trade_analysis": {
                "records": loss_records,
                "most_common": loss_cluster,
            },
            "engine_score_contribution": {
                "winning_trades_average": winning_score_average,
                "losing_trades_average": losing_score_average,
            },
            "recommendations": cls.build_recommendations(
                worst_symbol=worst_symbol,
                best_killzone=best_killzone,
                worst_phase=worst_phase,
                loss_cluster=loss_cluster,
            ),
        }

    @staticmethod
    def loss_record(trade: dict[str, Any]) -> dict[str, Any]:
        """Return the compact loss record requested by diagnostics."""
        return {
            "symbol": trade.get("symbol", "unknown"),
            "confidence": trade.get("confidence", 0),
            "confidence_bucket": BacktestEngine.confidence_bucket(trade.get("confidence", 0)),
            "killzone": trade.get("killzone", "none"),
            "narrative_phase": trade.get("narrative_phase", "range"),
            "smt_detected": bool(trade.get("smt_detected", False)),
            "rr": float(trade.get("rr", 0.0)),
            "planner_quality": trade.get("planner_quality", "unknown"),
        }

    @staticmethod
    def confidence_bucket(confidence: Any) -> str:
        """Bucket confidence for loss clustering."""
        try:
            value = int(confidence)
        except (TypeError, ValueError):
            return "unknown"
        if value < 90:
            return "below_90"
        if value <= 94:
            return "90-94"
        if value <= 100:
            return "95-100"
        return "above_100"

    @staticmethod
    def most_common(records: list[dict[str, Any]], key: str) -> Any:
        values = [record.get(key) for record in records if record.get(key) not in (None, "", "none")]
        if not values:
            return "none"
        return Counter(values).most_common(1)[0][0]

    @classmethod
    def build_loss_cluster(cls, loss_records: list[dict[str, Any]]) -> dict[str, Any]:
        """Return the most common diagnostic features among losing trades."""
        return {
            "symbol": cls.most_common(loss_records, "symbol"),
            "killzone": cls.most_common(loss_records, "killzone"),
            "narrative_phase": cls.most_common(loss_records, "narrative_phase"),
            "smt": "SMT" if cls.most_common(loss_records, "smt_detected") is True else "No SMT",
            "confidence_bucket": cls.most_common(loss_records, "confidence_bucket"),
            "planner_quality": cls.most_common(loss_records, "planner_quality"),
        }

    @classmethod
    def average_score_breakdown(cls, trades: list[dict[str, Any]]) -> dict[str, float]:
        """Average the score breakdown for a group of trades."""
        if not trades:
            return {key: 0.0 for key in cls.SCORE_BREAKDOWN_KEYS}
        averages: dict[str, float] = {}
        for key in cls.SCORE_BREAKDOWN_KEYS:
            values = [float(trade.get("score_breakdown", {}).get(key, 0.0)) for trade in trades]
            averages[key] = round(sum(values) / len(values), 2)
        return averages

    @staticmethod
    def build_recommendations(
        *,
        worst_symbol: str,
        best_killzone: str,
        worst_phase: str,
        loss_cluster: dict[str, Any],
    ) -> list[str]:
        """Generate practical diagnostic recommendations."""
        recommendations: list[str] = []
        if worst_symbol != "none":
            recommendations.append(f"Disable {worst_symbol} temporarily")
        if loss_cluster.get("confidence_bucket") == "90-94":
            recommendations.append("Raise minimum confidence to 95")
        if worst_phase == "range" or loss_cluster.get("narrative_phase") == "range":
            recommendations.append("Avoid range-phase trades")
        if best_killzone != "none":
            recommendations.append(f"Favor {KillzoneAnalyzer.display_name(best_killzone)}")
        return list(dict.fromkeys(recommendations)) or ["No loss cluster detected; keep current filters under review"]

    @staticmethod
    def calculate_max_drawdown(pnls: list[float], starting_balance: float) -> float:
        """Return maximum equity drawdown percentage from ordered PnL values."""
        if starting_balance <= 0:
            return 0.0
        equity = starting_balance
        peak = starting_balance
        max_drawdown = 0.0
        for pnl in pnls:
            equity += pnl
            peak = max(peak, equity)
            drawdown = ((peak - equity) / peak) * 100 if peak else 0.0
            max_drawdown = max(max_drawdown, drawdown)
        return round(max_drawdown, 2)

    @staticmethod
    def normalize_candles(candles: pd.DataFrame) -> pd.DataFrame:
        """Return candles sorted by time if available."""
        normalized = candles.copy()
        if "time" in normalized.columns:
            normalized = normalized.sort_values("time")
        return normalized.reset_index(drop=True)

    @staticmethod
    def best_bucket(metrics: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        """Return the bucket with the highest win rate and at least one trade."""
        eligible = [(name, data) for name, data in metrics.items() if data.get("trades_approved", 0) > 0]
        if not eligible:
            return "none", {}
        return max(eligible, key=lambda item: (float(item[1].get("win_rate", 0.0)), int(item[1].get("trades_approved", 0))))

    @staticmethod
    def worst_bucket(metrics: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        """Return the bucket with the lowest win rate and at least one trade."""
        eligible = [(name, data) for name, data in metrics.items() if data.get("trades_approved", 0) > 0]
        if not eligible:
            return "none", {}
        return min(eligible, key=lambda item: (float(item[1].get("win_rate", 0.0)), -int(item[1].get("trades_approved", 0))))

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist; using backtesting defaults.", path)
            return {}
        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise BacktestEngineError(f"Failed to load config {path}: {exc}") from exc

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
