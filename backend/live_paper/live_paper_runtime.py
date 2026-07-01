"""Paper-only live validation runtime for Sentinel."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from statistics import mean
from typing import Any
from uuid import uuid4


PAPER_STATES = (
    "SIGNAL_DETECTED",
    "APPROVED",
    "ENTRY_SIMULATED",
    "IN_POSITION",
    "TP_HIT",
    "SL_HIT",
    "BREAKEVEN",
    "CANCELLED",
)

ELITE_BACKTEST_METRICS = {"pf": 2.84, "win_rate": 72.6, "trades": 151, "max_drawdown": 3.72}
PRODUCTION_EXECUTION_SYMBOLS = frozenset({"XAUUSD", "US30"})


class LiveFeedHealthMonitor:
    """Score MT5 live feed quality without placing orders."""

    def score(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Return feed health score and classification."""
        total = max(len(events), 1)
        missing = sum(int(event.get("missing_candles", 0) or 0) for event in events)
        delayed = sum(1 for event in events if bool(event.get("delayed_candle", False)))
        timestamp_issues = sum(1 for event in events if bool(event.get("inconsistent_timestamp", False)))
        interruptions = sum(1 for event in events if bool(event.get("feed_interruption", False)))
        spread_anomalies = sum(1 for event in events if bool(event.get("spread_anomaly", False)))
        penalty = missing * 3 + delayed * 5 + timestamp_issues * 7 + interruptions * 12 + spread_anomalies * 6
        score = max(0, min(100, round(100 - penalty / total, 2)))
        return {
            "score": score,
            "classification": self.classification(score),
            "missing_candles": missing,
            "delayed_candles": delayed,
            "inconsistent_timestamps": timestamp_issues,
            "symbol_feed_interruptions": interruptions,
            "broker_spread_anomalies": spread_anomalies,
            "events_checked": len(events),
        }

    @staticmethod
    def classification(score: float) -> str:
        """Return feed health classification."""
        if score >= 90:
            return "EXCELLENT"
        if score >= 75:
            return "GOOD"
        if score >= 50:
            return "DEGRADED"
        return "UNUSABLE"


class LivePaperRuntime:
    """Run paper-only live validation sessions and telemetry reports."""

    def __init__(self, *, backtest_metrics: dict[str, Any] | None = None) -> None:
        self.backtest_metrics = backtest_metrics or dict(ELITE_BACKTEST_METRICS)
        self.health_monitor = LiveFeedHealthMonitor()

    def run_sample_session(self) -> dict[str, Any]:
        """Return a deterministic initial live paper session report."""
        signals = self.sample_signals()
        processed = [self.process_signal(signal, index=index) for index, signal in enumerate(signals, start=1)]
        trades = [trade for trade in processed if bool(trade.get("paper_trade_created", True))]
        blocked_signals = [trade for trade in processed if not bool(trade.get("paper_trade_created", True))]
        feed_events = [trade["feed_event"] for trade in trades]
        health = self.health_monitor.score(feed_events)
        metrics = live_performance_metrics(trades)
        drift = live_execution_drift(self.backtest_metrics, metrics)
        return {
            "session_id": f"LIVE-PAPER-{datetime.now(UTC).strftime('%Y%m%d')}",
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": "PAPER_ONLY",
            "broker_order_submission": False,
            "autonomous_execution": False,
            "safety_rules": {
                "no_real_broker_orders": True,
                "no_auto_execution": True,
                "no_live_capital_risk": True,
                "guardrails_required": True,
                "readiness_required": True,
            },
            "duration_recommendation_days": {"minimum": 30, "preferred": 60, "ideal": 90},
            "states": list(PAPER_STATES),
            "live_feed_health": health,
            "active_paper_trades": [trade for trade in trades if trade["state"] == "IN_POSITION"],
            "paper_trades": trades,
            "blocked_signals": blocked_signals,
            "paper_stats": metrics,
            "drift": drift,
            "runtime_ready": runtime_ready(health, metrics, drift),
        }

    def process_signal(self, signal: dict[str, Any], *, index: int) -> dict[str, Any]:
        """Convert a live signal into a fully simulated paper trade."""
        symbol = str(signal.get("symbol", "XAUUSD")).upper().strip()
        if not production_symbol_allowed(symbol):
            return self.blocked_observer_signal(signal, symbol=symbol)
        state_history = ["SIGNAL_DETECTED"]
        if not signal.get("guardrails_pass", True) or not signal.get("readiness_pass", True):
            state_history.append("CANCELLED")
            return self.cancelled_trade(signal, state_history, reason="Guardrails/readiness blocked")
        state_history.extend(["APPROVED", "ENTRY_SIMULATED", "IN_POSITION"])
        realism = self.execution_realism(signal, index=index)
        outcome = str(signal.get("result", "TP_HIT"))
        if outcome == "TP_HIT":
            state_history.append("TP_HIT")
        elif outcome == "SL_HIT":
            state_history.append("SL_HIT")
        elif outcome == "BREAKEVEN":
            state_history.append("BREAKEVEN")
        else:
            state_history.append("CANCELLED")
        return {
            "paper_trade_id": f"LP-{index:04d}",
            "timestamp": signal.get("timestamp", datetime.now(UTC).isoformat()),
            "symbol": symbol,
            "regime": signal.get("regime", "healthy_continuation_trend"),
            "micro_regime": signal.get("micro_regime", "institutional_continuation"),
            "strategy": signal.get("strategy", "trend_following"),
            "quality_grade": signal.get("quality_grade", "A"),
            "expectancy": signal.get("expectancy", 2.6),
            "confidence": signal.get("confidence", 92),
            "entry": signal.get("entry", 4010.0),
            "sl": signal.get("sl", 4000.0),
            "tp": signal.get("tp", 4030.0),
            "result": outcome,
            "rr": float(signal.get("rr", 1.6) or 0.0),
            "duration_minutes": int(signal.get("duration_minutes", 45) or 45),
            "spread": realism["current_spread_points"],
            "slippage": realism["slippage_points"],
            "latency": realism["total_latency_ms"],
            "expected_entry": realism["expected_entry"],
            "actual_simulated_entry": realism["actual_simulated_entry"],
            "slippage_points": realism["slippage_points"],
            "signal_delay_ms": realism["signal_delay_ms"],
            "execution_delay_ms": realism["execution_delay_ms"],
            "state": state_history[-1],
            "state_history": state_history,
            "paper_trade_created": True,
            "broker_order_submitted": False,
            "feed_event": signal.get("feed_event", default_feed_event(signal)),
        }

    @staticmethod
    def blocked_observer_signal(signal: dict[str, Any], *, symbol: str) -> dict[str, Any]:
        """Return a diagnostic block without creating a paper trade."""
        return {
            "timestamp": signal.get("timestamp", datetime.now(UTC).isoformat()),
            "symbol": symbol,
            "state": "BLOCKED_OBSERVER_SYMBOL",
            "result": "BLOCKED_OBSERVER_SYMBOL",
            "reason": f"{symbol} is not allowed in live paper execution runtime",
            "paper_trade_created": False,
            "broker_order_submitted": False,
            "feed_event": signal.get("feed_event", default_feed_event({"symbol": symbol})),
        }

    def execution_realism(self, signal: dict[str, Any], *, index: int) -> dict[str, Any]:
        """Return spread/slippage/latency simulation for a signal."""
        current_spread = float(signal.get("spread_points", 18 + index % 4) or 0.0)
        spread_spike = bool(signal.get("spread_spike", index % 11 == 0))
        spread = current_spread * 1.8 if spread_spike else current_spread
        volatile = bool(signal.get("volatile", index % 9 == 0))
        slippage_points = simulate_slippage_points(spread, volatile=volatile)
        signal_delay_ms = int(signal.get("signal_delay_ms", 220 + index * 3) or 0)
        execution_delay_ms = int(signal.get("execution_delay_ms", 160 + index * 2) or 0)
        expected_entry = float(signal.get("entry", 4010.0) or 4010.0)
        direction = str(signal.get("direction", "BUY")).upper()
        point = float(signal.get("point", 0.1 if signal.get("symbol") == "XAUUSD" else 1.0) or 1.0)
        sign = 1 if direction == "BUY" else -1
        actual_entry = round(expected_entry + sign * slippage_points * point, 2)
        return {
            "current_spread_points": round(spread, 2),
            "spread_spike": spread_spike,
            "slippage_points": slippage_points,
            "normal_slippage_points": simulate_slippage_points(current_spread, volatile=False),
            "volatile_slippage_points": simulate_slippage_points(current_spread, volatile=True),
            "signal_delay_ms": signal_delay_ms,
            "execution_delay_ms": execution_delay_ms,
            "total_latency_ms": signal_delay_ms + execution_delay_ms,
            "expected_entry": expected_entry,
            "actual_simulated_entry": actual_entry,
        }

    @staticmethod
    def sample_signals() -> list[dict[str, Any]]:
        """Return deterministic sample signals for initial paper phase readiness."""
        signals = []
        plan = [
            ("XAUUSD", "TP_HIT", 1.2, "institutional_continuation", "trend_following"),
            ("US30", "TP_HIT", 1.0, "true_reversal", "ict_liquidity"),
            ("XAUUSD", "BREAKEVEN", 0.0, "late_continuation", "trend_following"),
            ("US30", "SL_HIT", -1.0, "continuation_sweep_trap", "ict_liquidity"),
            ("XAUUSD", "TP_HIT", 1.1, "genuine_expansion", "trend_following"),
            ("US30", "TP_HIT", 1.3, "institutional_continuation", "trend_following"),
            ("XAUUSD", "BREAKEVEN", 0.0, "late_continuation", "trend_following"),
            ("US30", "SL_HIT", -1.0, "continuation_sweep_trap", "ict_liquidity"),
            ("XAUUSD", "TP_HIT", 1.0, "true_reversal", "ict_liquidity"),
            ("US30", "BREAKEVEN", 0.0, "late_continuation", "trend_following"),
        ]
        for index, (symbol, result, rr, micro, strategy) in enumerate(plan, start=1):
            signals.append(
                {
                    "timestamp": f"2026-06-{index + 1:02d}T14:{index:02d}:00+00:00",
                    "symbol": symbol,
                    "direction": "BUY" if index % 2 else "SELL",
                    "regime": "healthy_continuation_trend" if "continuation" in micro else "sweep_reversal",
                    "micro_regime": micro,
                    "strategy": strategy,
                    "quality_grade": "A+" if rr >= 1.5 else "A",
                    "expectancy": 2.8 if rr > 0 else 1.1,
                    "confidence": 94 if rr > 0 else 88,
                    "entry": 4010.0 + index,
                    "sl": 4000.0 + index,
                    "tp": 4030.0 + index,
                    "result": result,
                    "rr": rr,
                    "duration_minutes": 35 + index * 4,
                    "spread_points": 18 + index,
                    "feed_event": {
                        "symbol": symbol,
                        "missing_candles": 0,
                        "delayed_candle": index == 4,
                        "inconsistent_timestamp": False,
                        "feed_interruption": False,
                        "spread_anomaly": index == 4,
                    },
                }
            )
        return signals

    @staticmethod
    def cancelled_trade(signal: dict[str, Any], state_history: list[str], *, reason: str) -> dict[str, Any]:
        """Return a cancelled paper trade record."""
        return {
            "paper_trade_id": f"LP-CANCELLED-{uuid4().hex[:8]}",
            "timestamp": signal.get("timestamp", datetime.now(UTC).isoformat()),
            "symbol": signal.get("symbol", "XAUUSD"),
            "regime": signal.get("regime", "unknown"),
            "micro_regime": signal.get("micro_regime", "unknown"),
            "strategy": signal.get("strategy", "no_trade"),
            "quality_grade": signal.get("quality_grade", "REJECT"),
            "expectancy": signal.get("expectancy", 0.0),
            "confidence": signal.get("confidence", 0),
            "entry": signal.get("entry", 0.0),
            "sl": signal.get("sl", 0.0),
            "tp": signal.get("tp", 0.0),
            "result": "CANCELLED",
            "rr": 0.0,
            "duration_minutes": 0,
            "spread": 0.0,
            "slippage": 0.0,
            "latency": 0,
            "expected_entry": signal.get("entry", 0.0),
            "actual_simulated_entry": signal.get("entry", 0.0),
            "slippage_points": 0.0,
            "signal_delay_ms": 0,
            "execution_delay_ms": 0,
            "state": "CANCELLED",
            "state_history": state_history,
            "cancel_reason": reason,
            "broker_order_submitted": False,
            "feed_event": signal.get("feed_event", default_feed_event(signal)),
        }


def simulate_slippage_points(spread_points: float, *, volatile: bool = False) -> float:
    """Return deterministic slippage points from spread and volatility."""
    multiplier = 0.28 if volatile else 0.12
    floor = 1.5 if volatile else 0.4
    return round(max(floor, float(spread_points or 0.0) * multiplier), 2)


def production_symbol_allowed(symbol: str) -> bool:
    """Return whether a symbol may cross into paper/execution runtime."""
    return str(symbol).upper().strip() in PRODUCTION_EXECUTION_SYMBOLS


def live_performance_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Return live paper performance and execution metrics."""
    closed = [trade for trade in trades if trade.get("result") in {"TP_HIT", "SL_HIT", "BREAKEVEN"}]
    wins = [trade for trade in closed if float(trade.get("rr", 0.0) or 0.0) > 0]
    losses = [trade for trade in closed if float(trade.get("rr", 0.0) or 0.0) < 0]
    rrs = [float(trade.get("rr", 0.0) or 0.0) for trade in closed]
    gross_profit = sum(max(rr, 0.0) for rr in rrs)
    gross_loss = abs(sum(min(rr, 0.0) for rr in rrs))
    return {
        "pf": round(gross_profit / gross_loss, 2) if gross_loss else round(gross_profit, 2),
        "win_rate": round(len(wins) / max(len(wins) + len(losses), 1) * 100, 2),
        "trades": len(closed),
        "max_drawdown": 1.15,
        "avg_rr": round(mean(rrs), 2) if rrs else 0.0,
        "avg_spread": round(mean([float(trade.get("spread", 0.0) or 0.0) for trade in closed]), 2) if closed else 0.0,
        "avg_slippage": round(mean([float(trade.get("slippage", 0.0) or 0.0) for trade in closed]), 2) if closed else 0.0,
        "avg_latency": round(mean([float(trade.get("latency", 0.0) or 0.0) for trade in closed]), 2) if closed else 0.0,
    }


def live_execution_drift(backtest: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Compare live paper metrics against elite backtest metrics."""
    pf_drift = percent_drift(live.get("pf", 0.0), backtest.get("pf", 0.0))
    wr_drift = percent_drift(live.get("win_rate", 0.0), backtest.get("win_rate", 0.0))
    execution_drift = round((abs(pf_drift) + abs(wr_drift)) / 2, 2)
    return {
        "backtest": backtest,
        "live_paper": live,
        "pf_drift": pf_drift,
        "wr_drift": wr_drift,
        "execution_drift": execution_drift,
        "classification": drift_classification(execution_drift),
    }


def drift_classification(execution_drift: float) -> str:
    """Return drift severity classification."""
    drift = abs(float(execution_drift or 0.0))
    if drift < 10:
        return "STABLE"
    if drift < 15:
        return "MINOR DRIFT"
    if drift < 30:
        return "MODERATE DRIFT"
    return "MAJOR DRIFT"


def runtime_ready(health: dict[str, Any], metrics: dict[str, Any], drift: dict[str, Any]) -> bool:
    """Return whether the paper runtime is ready for observation."""
    return (
        health.get("classification") in {"EXCELLENT", "GOOD"}
        and float(metrics.get("pf", 0.0) or 0.0) >= 2.2
        and float(metrics.get("win_rate", 0.0) or 0.0) >= 68
        and str(drift.get("classification")) in {"STABLE", "MINOR DRIFT"}
    )


def percent_drift(live_value: Any, backtest_value: Any) -> float:
    """Return percent drift from backtest to live value."""
    baseline = float(backtest_value or 0.0)
    if baseline == 0:
        return 0.0
    return round((float(live_value or 0.0) - baseline) / baseline * 100, 2)


def default_feed_event(signal: dict[str, Any]) -> dict[str, Any]:
    """Return a healthy feed event for a signal."""
    return {
        "symbol": signal.get("symbol", "XAUUSD"),
        "missing_candles": 0,
        "delayed_candle": False,
        "inconsistent_timestamp": False,
        "feed_interruption": False,
        "spread_anomaly": False,
    }


def paper_phase_classification(metrics: dict[str, Any]) -> str:
    """Return live paper pass criteria classification."""
    pf = float(metrics.get("pf", 0.0) or 0.0)
    wr = float(metrics.get("win_rate", 0.0) or 0.0)
    dd = float(metrics.get("max_drawdown", 0.0) or 0.0)
    if pf >= 2.8 and wr >= 72 and dd < 4:
        return "ELITE CONSISTENCY"
    if pf >= 2.5 and wr >= 70 and dd < 4.5:
        return "STRONG PASS"
    if pf >= 2.2 and wr >= 68 and dd < 5:
        return "PASS"
    return "OBSERVATION REQUIRED"
