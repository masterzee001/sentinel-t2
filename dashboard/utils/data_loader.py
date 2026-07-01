"""Data loading helpers for the Project Sentinel Streamlit dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from loguru import logger

from backend.ai_coach.coach_analyzer import AICoachAnalyzer
from backend.analytics.monte_carlo_engine import MonteCarloEngine, MonteCarloEngineError
from backend.backtesting.report_cache import load_backtest_summary as load_cached_backtest_summary
from backend.backtesting.report_cache import normalize_backtest_summary, short_phase_decision
from backend.confidence_engine.confidence_analyzer import ConfidenceAnalyzer
from backend.display.confidence_display import DEMO_SANDBOX_LABEL, OBSERVER_ONLY_LABEL, confidence_display_fields
from backend.ict_engine.ict_analyzer import ICTAnalyzer
from backend.killzone_engine.killzone_analyzer import KillzoneAnalyzer
from backend.live_data.live_data_collector import LiveDataCollector
from backend.liquidity_engine.liquidity_analyzer import LiquidityAnalyzer
from backend.market_data.mt5_connector import MT5Connector
from backend.news_filter.news_filter import NewsFilter
from backend.observer.btc_observer import BTCObserver
from backend.observer.nas100_observer import NAS100Observer
from backend.risk_manager.risk_governor import RiskGovernor
from backend.shared.confidence_band_registry import observer_display_state, observer_state
from backend.smt_engine.smt_analyzer import SMTAnalyzer
from backend.symbols.symbol_registry import SymbolRegistry
from backend.trade_planner.trade_planner import TradePlanner
from backend.trend_engine.trend_analyzer import TrendAnalyzer


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = {
    "advisor_mode_only": True,
    "refresh_seconds": 60,
    "symbols": ["XAUUSD", "US30", "EURUSD", "GBPUSD", "BTCUSD", "NAS100"],
    "experimental_symbols": ["BTCUSD", "NAS100"],
    "journal_path": "data/journal/sentinel_decisions.jsonl",
    "live_data_config_path": "config/live_data.yaml",
    "monte_carlo_config_path": "config/monte_carlo.yaml",
    "validation_report_path": "data/reports/backtest_365d_v2_summary.json",
    "market_watch_report_path": "data/reports/market_watch_365d_summary.json",
    "live_paper_report_path": "data/reports/live_paper_session.json",
    "emergency_live_report_path": "data/reports/emergency_live_status.json",
    "challenge_command_center_report_path": "data/reports/challenge_command_center.json",
    "assisted_execution_report_path": "data/reports/assisted_execution_status.json",
    "demo_sandbox_report_path": "data/reports/demo_sandbox_status.json",
    "backtest_summary_paths": [
        "data/reports/backtest_365d_summary.json",
        "data/reports/latest_backtest_summary.json",
        "data/backtest_summary.json",
        "data/backtesting/latest_summary.json",
        "data/reports/backtest_summary.json",
    ],
    "symbol_registry_path": "config/symbol_registry.yaml",
}


def load_dashboard_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load dashboard config with safe defaults."""
    path = Path(config_path) if config_path else PROJECT_ROOT / "dashboard" / "config.yaml"
    config = load_yaml(path)
    return deep_merge(DEFAULT_CONFIG, config)


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file or return an empty dict."""
    file_path = Path(path)
    if not file_path.exists():
        return {}
    with file_path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def read_jsonl(path: str | Path, limit: int | None = None) -> list[dict[str, Any]]:
    """Read local JSONL records, ignoring malformed lines."""
    file_path = Path(path)
    if not file_path.exists():
        return []
    records: list[dict[str, Any]] = []
    with file_path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                records.append(json.loads(text))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed dashboard JSONL line {} in {}", line_number, file_path)
    return records[-limit:] if limit else records


def load_journal_records(project_root: str | Path, config: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
    """Load journal records from configured local JSONL storage."""
    path = resolve_project_path(project_root, config.get("journal_path", DEFAULT_CONFIG["journal_path"]))
    return read_jsonl(path, limit=limit)


def journal_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Return a display-friendly journal DataFrame."""
    rows = []
    for record in records:
        trade_plan = record.get("trade_plan", {})
        reasons = record.get("rejection_reasons", []) or []
        rows.append(
            {
                "timestamp": record.get("timestamp", ""),
                "symbol": record.get("symbol", ""),
                "state": record.get("state", ""),
                "confidence": record.get("confidence", 0),
                "decision": record.get("decision", ""),
                "plan_quality": trade_plan.get("plan_quality", ""),
                "top_rejection_reason": reasons[0] if reasons else "",
            }
        )
    return pd.DataFrame(rows)


def load_backtest_summary(project_root: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """Load the latest available backtest summary cache."""
    for configured_path in config.get("backtest_summary_paths", DEFAULT_CONFIG["backtest_summary_paths"]):
        path = resolve_project_path(project_root, configured_path)
        summary = load_cached_backtest_summary(path)
        if summary:
            return {"available": True, "path": str(path), "data": summary}
    return {"available": False, "data": {}}


def analytics_dataframe(backtest_summary: dict[str, Any]) -> pd.DataFrame:
    """Return chart rows for PF, win rate, drawdown, and trade count."""
    data = normalize_backtest_summary(backtest_summary.get("data", {})) if backtest_summary.get("available") else {}
    rows = []
    for days in ("30", "90", "365"):
        metrics = data.get(f"days_{days}", {})
        if not metrics or not any(float(metrics.get(key, 0.0) or 0.0) for key in ("pf", "win_rate", "max_drawdown", "trades", "net_rr")):
            continue
        rows.extend(
            [
                {"window": f"{days}D", "metric": "Profit Factor", "value": float(metrics.get("pf", 0.0) or 0.0)},
                {"window": f"{days}D", "metric": "Win Rate", "value": float(metrics.get("win_rate", 0.0) or 0.0)},
                {"window": f"{days}D", "metric": "Max Drawdown", "value": float(metrics.get("max_drawdown", 0.0) or 0.0)},
                {"window": f"{days}D", "metric": "Trade Count", "value": float(metrics.get("trades", 0) or 0)},
            ]
        )
    return pd.DataFrame(rows)


def analytics_summary(backtest_summary: dict[str, Any]) -> dict[str, Any]:
    """Return dashboard metric-card values for the cached backtest report."""
    if not backtest_summary.get("available"):
        return {"available": False}
    data = normalize_backtest_summary(backtest_summary.get("data", {}))
    return {
        "available": True,
        "generated_at": data.get("generated_at", ""),
        "phase_decision": short_phase_decision(data),
        "days_30": data.get("days_30", {}),
        "days_90": data.get("days_90", {}),
        "days_365": data.get("days_365", {}),
    }


def symbol_registry_rows(project_root: str | Path, config: dict[str, Any], backtest_summary: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return symbol registry rows with latest cached metrics when available."""
    registry = SymbolRegistry(config_dir=resolve_project_path(project_root, config.get("symbol_registry_path", "config/symbol_registry.yaml")).parent)
    metrics = symbol_metrics_from_backtest(backtest_summary or load_backtest_summary(project_root, config))
    return registry.rows(metrics)


def symbol_registry_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Return dashboard dataframe for symbol governance."""
    return pd.DataFrame(rows).reindex(columns=["symbol", "tier", "pf", "wr", "trades", "dd", "status"])


def symbol_metrics_from_backtest(backtest_summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract per-symbol metrics from the latest 365D report shape."""
    if not backtest_summary.get("available"):
        return {}
    data = backtest_summary.get("data", {})
    metrics_by_symbol: dict[str, dict[str, Any]] = {}
    candidates = [
        data.get("production_portfolio", {}).get("symbol_breakdown", {}),
        data.get("symbol_breakdown", {}),
        data.get("by_symbol", {}),
        data.get("days_365", {}).get("by_symbol", {}) if isinstance(data.get("days_365"), dict) else {},
    ]
    for candidate in candidates:
        if candidate:
            metrics_by_symbol.update({str(symbol).upper(): metrics for symbol, metrics in candidate.items()})
            break
    for symbol, diagnostics in data.get("observer_diagnostics", {}).items():
        symbol_key = str(symbol).upper()
        metrics = diagnostics.get("metrics", diagnostics) if isinstance(diagnostics, dict) else {}
        metrics_by_symbol[symbol_key] = metrics
    return metrics_by_symbol


def load_live_data_summary(project_root: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """Load live-data collection analytics for the dashboard."""
    config_path = resolve_project_path(project_root, config.get("live_data_config_path", DEFAULT_CONFIG["live_data_config_path"]))
    collector = LiveDataCollector(config_dir=config_path.parent, project_root=project_root)
    return collector.summary()


def load_monte_carlo_summary(project_root: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """Run Monte Carlo stress analytics from cached 365D backtest outcomes."""
    config_path = resolve_project_path(project_root, config.get("monte_carlo_config_path", DEFAULT_CONFIG["monte_carlo_config_path"]))
    try:
        return MonteCarloEngine(config_dir=config_path.parent, project_root=project_root).run_from_report()
    except MonteCarloEngineError as exc:
        logger.warning("Dashboard Monte Carlo summary unavailable: {}", exc)
        return {"available": False, "reason": str(exc)}


def load_validation_summary(project_root: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """Load the Master Sprint 3 validation checkpoint report."""
    path = resolve_project_path(project_root, config.get("validation_report_path", DEFAULT_CONFIG["validation_report_path"]))
    if not path.exists():
        return {"available": False}
    try:
        return {"available": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:
        logger.warning("Dashboard validation summary unavailable: {}", exc)
        return {"available": False, "reason": str(exc)}


def load_market_watch_summary(project_root: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """Load Market Watch advisory diagnostics."""
    path = resolve_project_path(project_root, config.get("market_watch_report_path", DEFAULT_CONFIG["market_watch_report_path"]))
    if not path.exists():
        return {"available": False}
    try:
        return {"available": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:
        logger.warning("Dashboard Market Watch summary unavailable: {}", exc)
        return {"available": False, "reason": str(exc)}


def load_live_paper_summary(project_root: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """Load live paper phase telemetry."""
    path = resolve_project_path(project_root, config.get("live_paper_report_path", DEFAULT_CONFIG["live_paper_report_path"]))
    if not path.exists():
        return {"available": False}
    try:
        return {"available": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:
        logger.warning("Dashboard live paper summary unavailable: {}", exc)
        return {"available": False, "reason": str(exc)}


def load_emergency_live_summary(project_root: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """Load emergency live deployment status."""
    path = resolve_project_path(project_root, config.get("emergency_live_report_path", DEFAULT_CONFIG["emergency_live_report_path"]))
    if not path.exists():
        return {"available": False}
    try:
        return {"available": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:
        logger.warning("Dashboard emergency live summary unavailable: {}", exc)
        return {"available": False, "reason": str(exc)}


def load_challenge_command_center_summary(project_root: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """Load Challenge Command Center advisory telemetry."""
    path = resolve_project_path(project_root, config.get("challenge_command_center_report_path", DEFAULT_CONFIG["challenge_command_center_report_path"]))
    if not path.exists():
        return {"available": False}
    try:
        return {"available": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:
        logger.warning("Dashboard Challenge Command Center summary unavailable: {}", exc)
        return {"available": False, "reason": str(exc)}


def load_assisted_execution_summary(project_root: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """Load assisted demo execution bridge status."""
    path = resolve_project_path(project_root, config.get("assisted_execution_report_path", DEFAULT_CONFIG["assisted_execution_report_path"]))
    if not path.exists():
        return {"available": False}
    try:
        return {"available": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:
        logger.warning("Dashboard assisted execution summary unavailable: {}", exc)
        return {"available": False, "reason": str(exc)}


def load_demo_sandbox_summary(project_root: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """Load demo sandbox status."""
    path = resolve_project_path(project_root, config.get("demo_sandbox_report_path", DEFAULT_CONFIG["demo_sandbox_report_path"]))
    if not path.exists():
        return {"available": False}
    try:
        return {"available": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:
        logger.warning("Dashboard demo sandbox summary unavailable: {}", exc)
        return {"available": False, "reason": str(exc)}


def assisted_execution_gate_dataframe(summary: dict[str, Any]) -> pd.DataFrame:
    """Return assisted execution final gate checks."""
    checks = summary.get("data", {}).get("final_safety_status", {}).get("checks", {})
    return pd.DataFrame([{"check": key, "passed": value} for key, value in checks.items()])


def demo_sandbox_performance_dataframe(summary: dict[str, Any]) -> pd.DataFrame:
    """Return demo sandbox performance rows."""
    memory = summary.get("data", {}).get("learning_memory", {}).get("symbols", {})
    return pd.DataFrame(memory.values())


def challenge_performance_dataframe(summary: dict[str, Any]) -> pd.DataFrame:
    """Return Challenge Command Center performance rows."""
    performance = summary.get("data", {}).get("trading_performance", {})
    rows = [
        {"metric": "trades_taken", "value": performance.get("trades_taken", 0)},
        {"metric": "win_rate", "value": performance.get("win_rate", 0.0)},
        {"metric": "pf", "value": performance.get("pf", 0.0)},
        {"metric": "avg_rr", "value": performance.get("avg_rr", 0.0)},
        {"metric": "avg_loss", "value": performance.get("avg_loss", 0.0)},
        {"metric": "dd", "value": performance.get("dd", 0.0)},
        {"metric": "efde_saves", "value": performance.get("efde_saves", 0)},
        {"metric": "a_plus_override_saves", "value": performance.get("a_plus_override_saves", 0)},
    ]
    return pd.DataFrame(rows)


def emergency_approval_dataframe(summary: dict[str, Any]) -> pd.DataFrame:
    """Return emergency approval queue rows."""
    rows = []
    for item in summary.get("data", {}).get("approval_queue", []):
        proposal = item.get("proposal", {})
        rows.append(
            {
                "approval_id": item.get("approval_id"),
                "status": item.get("status"),
                "symbol": proposal.get("symbol"),
                "strategy": proposal.get("strategy"),
                "quality_grade": proposal.get("quality_grade"),
                "risk_percent": proposal.get("risk_percent"),
                "expected_pf": proposal.get("expected_pf"),
                "expected_wr": proposal.get("expected_wr"),
            }
        )
    return pd.DataFrame(rows)


def live_paper_trade_dataframe(summary: dict[str, Any]) -> pd.DataFrame:
    """Return live paper trade telemetry as a DataFrame."""
    rows = []
    for trade in summary.get("data", {}).get("paper_trades", []):
        rows.append(
            {
                "paper_trade_id": trade.get("paper_trade_id"),
                "timestamp": trade.get("timestamp"),
                "symbol": trade.get("symbol"),
                "state": trade.get("state"),
                "strategy": trade.get("strategy"),
                "micro_regime": trade.get("micro_regime"),
                "quality_grade": trade.get("quality_grade"),
                "rr": trade.get("rr", 0.0),
                "spread": trade.get("spread", 0.0),
                "slippage": trade.get("slippage", 0.0),
                "latency": trade.get("latency", 0),
            }
        )
    return pd.DataFrame(rows)


def live_paper_execution_dataframe(summary: dict[str, Any]) -> pd.DataFrame:
    """Return execution realism rows for slippage and latency panels."""
    rows = []
    for trade in summary.get("data", {}).get("paper_trades", []):
        rows.append(
            {
                "paper_trade_id": trade.get("paper_trade_id"),
                "symbol": trade.get("symbol"),
                "expected_entry": trade.get("expected_entry", 0.0),
                "actual_simulated_entry": trade.get("actual_simulated_entry", 0.0),
                "slippage_points": trade.get("slippage_points", 0.0),
                "signal_delay_ms": trade.get("signal_delay_ms", 0),
                "execution_delay_ms": trade.get("execution_delay_ms", 0),
                "total_latency_ms": trade.get("latency", 0),
            }
        )
    return pd.DataFrame(rows)


def live_data_symbol_dataframe(summary: dict[str, Any]) -> pd.DataFrame:
    """Return per-symbol live-data stats as a DataFrame."""
    rows = []
    for symbol, stats in summary.get("symbols", {}).items():
        rows.append(
            {
                "symbol": symbol,
                "total_scans": stats.get("total_scans", 0),
                "warm": stats.get("warm", 0),
                "hot": stats.get("hot", 0),
                "execution_ready": stats.get("execution_ready", 0),
                "symbol_mode": stats.get("symbol_mode", "production"),
            }
        )
    return pd.DataFrame(rows)


def extract_adaptive_metrics(data: dict[str, Any], days: str) -> dict[str, Any]:
    """Extract adaptive guardrail metrics from common backtest cache shapes."""
    candidates = [
        data.get(f"days_{days}", {}),
        data.get(days, {}).get("adaptive_guardrails", {}).get("overall", {}),
        data.get(int(days), {}).get("adaptive_guardrails", {}).get("overall", {}) if days.isdigit() else {},
        data.get(f"{days}_day", {}).get("adaptive_guardrails", {}).get("overall", {}),
        data.get(f"{days}d", {}).get("adaptive_guardrails", {}).get("overall", {}),
        data.get("long_horizon", {}).get(days, {}).get("adaptive_guardrails", {}).get("overall", {}),
        data.get("adaptive_guardrails", {}).get(days, {}).get("overall", {}),
    ]
    for candidate in candidates:
        if candidate:
            normalized = normalize_backtest_summary({f"days_{days}": candidate})
            return normalized.get(f"days_{days}", {})
    return {}


def build_dashboard_snapshot(
    *,
    connector: MT5Connector | None = None,
    config: dict[str, Any] | None = None,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build a live command-center-style dashboard snapshot."""
    root = Path(project_root) if project_root else PROJECT_ROOT
    config = config or load_dashboard_config()
    connector = connector or MT5Connector(supported_symbols=set(config.get("symbols", DEFAULT_CONFIG["symbols"])))
    connected_here = False
    try:
        if not connector.is_initialized():
            connector.connect()
            connected_here = True
        return build_connected_snapshot(connector=connector, config=config, project_root=root)
    except Exception as exc:
        logger.warning("Dashboard live snapshot unavailable: {}", exc)
        return fallback_snapshot(config, error=str(exc))
    finally:
        if connected_here:
            connector.shutdown()


def build_connected_snapshot(*, connector: MT5Connector, config: dict[str, Any], project_root: str | Path) -> dict[str, Any]:
    """Build a snapshot using initialized MT5 and Sentinel engines."""
    experimental = {str(symbol).upper() for symbol in config.get("experimental_symbols", [])}
    symbols = [str(symbol).upper() for symbol in config.get("symbols", DEFAULT_CONFIG["symbols"])]
    registry = SymbolRegistry(config_dir=Path(project_root) / "config")
    news_filter = NewsFilter()
    risk_governor = RiskGovernor(connector=connector)
    liquidity_analyzer = LiquidityAnalyzer(connector=connector)
    ict_analyzer = ICTAnalyzer(connector=connector, liquidity_analyzer=liquidity_analyzer)
    trend_analyzer = TrendAnalyzer(connector=connector)
    killzone_analyzer = KillzoneAnalyzer()
    smt_analyzer = SMTAnalyzer(connector=connector)
    confidence_analyzer = ConfidenceAnalyzer(
        connector=connector,
        trend_analyzer=trend_analyzer,
        liquidity_analyzer=liquidity_analyzer,
        ict_analyzer=ict_analyzer,
        news_filter=news_filter,
        killzone_analyzer=killzone_analyzer,
        smt_analyzer=smt_analyzer,
    )
    trade_planner = TradePlanner(
        connector=connector,
        confidence_analyzer=confidence_analyzer,
        risk_governor=risk_governor,
        ict_analyzer=ict_analyzer,
        liquidity_analyzer=liquidity_analyzer,
    )
    risk = risk_governor.evaluate()
    news = news_filter.check()
    symbol_snapshots: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        if symbol == BTCObserver.SYMBOL:
            observer = BTCObserver(connector=connector, killzone_analyzer=killzone_analyzer, smt_analyzer=smt_analyzer)
            symbol_snapshots[symbol] = btc_symbol_snapshot(observer.observe())
            continue
        if symbol == NAS100Observer.SYMBOL:
            observer = NAS100Observer(connector=connector, killzone_analyzer=killzone_analyzer, registry=registry)
            symbol_snapshots[symbol] = nas100_symbol_snapshot(observer.observe())
            continue
        if symbol in experimental:
            symbol_snapshots[symbol] = experimental_symbol_snapshot(symbol)
            continue
        try:
            confidence = confidence_analyzer.analyze(symbol, context={"risk_reward": 3.0, "news_status": news})
            plan = trade_planner.analyze(symbol, confidence_context={"news_status": news}, risk_state=risk)
            symbol_snapshots[symbol] = symbol_snapshot(symbol=symbol, confidence=confidence, plan=plan)
        except Exception as exc:
            symbol_snapshots[symbol] = unavailable_symbol_snapshot(symbol, str(exc))
    return {
        "connected": True,
        "risk": risk,
        "news": news,
        "symbols": symbol_snapshots,
        "execution_mode": load_yaml(Path(project_root) / "config" / "execution.yaml").get("execution_mode", "advisor"),
        "readiness": readiness_summary("No assisted trade plan selected"),
        "symbol_registry": symbol_registry_rows(project_root, config),
        "validation": load_validation_summary(project_root, config),
        "market_watch": load_market_watch_summary(project_root, config),
        "live_paper": load_live_paper_summary(project_root, config),
        "emergency_live": load_emergency_live_summary(project_root, config),
        "challenge_command_center": load_challenge_command_center_summary(project_root, config),
        "assisted_execution": load_assisted_execution_summary(project_root, config),
        "error": "",
    }


def symbol_snapshot(*, symbol: str, confidence: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Return normalized symbol snapshot for dashboard tables."""
    killzone = confidence.get("killzone", {})
    narrative = confidence.get("narrative", {})
    display = confidence_display_fields(confidence, fallback_state=str(confidence.get("confidence_band", "UNAVAILABLE")))
    return {
        "symbol": symbol,
        "badge": "",
        "state": confidence.get("confidence_band", "UNAVAILABLE"),
        "confidence": confidence.get("total_confidence", 0),
        "raw_confidence": display["raw_confidence"],
        "adjusted_confidence": display["adjusted_confidence"],
        "raw_band": display["raw_band"],
        "guardrail_penalty": display["guardrail_penalty"] if display["band_differs"] else "",
        "mode": "PRODUCTION",
        "decision": confidence.get("decision", "UNAVAILABLE"),
        "killzone": killzone.get("active_killzone", "none"),
        "narrative": narrative.get("summary", narrative.get("phase", "none")),
        "plan_quality": plan.get("plan_quality", "unavailable"),
        "entry": plan.get("entry", {}).get("price", 0.0),
        "sl": plan.get("stop_loss", {}).get("price", 0.0),
        "tp1": plan.get("take_profit", {}).get("tp1", 0.0),
        "tp2": plan.get("take_profit", {}).get("tp2", 0.0),
        "tp3": plan.get("take_profit", {}).get("tp3", 0.0),
        "lot_size": plan.get("risk", {}).get("lot_size", 0.0),
        "execution_allowed": bool(plan.get("execution_allowed", False)),
        "rejection_reasons": ", ".join(confidence.get("rejection_reasons", plan.get("rejection_reasons", []))) or "none",
    }


def btc_symbol_snapshot(btc: dict[str, Any]) -> dict[str, Any]:
    """Return dashboard row for BTCUSD observer diagnostics."""
    confidence = btc.get("confidence", {})
    trade_plan = btc.get("trade_plan", {})
    killzone = btc.get("killzone", {})
    narrative = btc.get("narrative", {})
    canonical_state = observer_state(confidence.get("observer_state", confidence.get("confidence_band", btc.get("state", "UNAVAILABLE"))))
    return {
        "symbol": BTCObserver.SYMBOL,
        "badge": DEMO_SANDBOX_LABEL,
        "state": confidence.get("confidence_band", btc.get("state", "UNAVAILABLE")),
        "observer_state": canonical_state,
        "display_state": observer_display_state(canonical_state),
        "state_kind": "OBSERVER_MOVEMENT",
        "confidence": confidence.get("total_confidence", btc.get("score", 0)),
        "raw_confidence": confidence.get("total_confidence", btc.get("score", 0)),
        "adjusted_confidence": confidence.get("total_confidence", btc.get("score", 0)),
        "raw_band": confidence.get("confidence_band", btc.get("state", "UNAVAILABLE")),
        "guardrail_penalty": "",
        "mode": DEMO_SANDBOX_LABEL,
        "observer_note": "SANDBOX DEMO ONLY. Not production, not funded, not challenge.",
        "decision": confidence.get("decision", "REJECTED"),
        "killzone": killzone.get("active_killzone", "none"),
        "narrative": narrative.get("summary", "BTCUSD observer mode."),
        "plan_quality": trade_plan.get("plan_quality", "observer_only"),
        "entry": 0.0,
        "sl": 0.0,
        "tp1": 0.0,
        "tp2": 0.0,
        "tp3": 0.0,
        "lot_size": 0.0,
        "execution_allowed": False,
        "rejection_reasons": ", ".join(confidence.get("rejection_reasons", [BTCObserver.REJECTION_REASON])),
    }


def nas100_symbol_snapshot(nas100: dict[str, Any]) -> dict[str, Any]:
    """Return dashboard row for NAS100 observer diagnostics."""
    confidence = nas100.get("confidence", {})
    trade_plan = nas100.get("trade_plan", {})
    killzone = nas100.get("killzone", {})
    narrative = nas100.get("narrative", {})
    canonical_state = observer_state(confidence.get("observer_state", confidence.get("confidence_band", nas100.get("state", "UNAVAILABLE"))))
    return {
        "symbol": NAS100Observer.SYMBOL,
        "badge": DEMO_SANDBOX_LABEL,
        "state": confidence.get("confidence_band", nas100.get("state", "UNAVAILABLE")),
        "observer_state": canonical_state,
        "display_state": observer_display_state(canonical_state),
        "state_kind": "OBSERVER_MOVEMENT",
        "confidence": confidence.get("total_confidence", nas100.get("score", 0)),
        "raw_confidence": confidence.get("total_confidence", nas100.get("score", 0)),
        "adjusted_confidence": confidence.get("total_confidence", nas100.get("score", 0)),
        "raw_band": confidence.get("confidence_band", nas100.get("state", "UNAVAILABLE")),
        "guardrail_penalty": "",
        "mode": DEMO_SANDBOX_LABEL,
        "observer_note": "SANDBOX DEMO ONLY. Not production, not funded, not challenge.",
        "decision": confidence.get("decision", "REJECTED"),
        "killzone": killzone.get("active_killzone", "none"),
        "narrative": narrative.get("summary", "NAS100 observer mode."),
        "plan_quality": trade_plan.get("plan_quality", "observer_only"),
        "entry": 0.0,
        "sl": 0.0,
        "tp1": 0.0,
        "tp2": 0.0,
        "tp3": 0.0,
        "lot_size": 0.0,
        "execution_allowed": False,
        "rejection_reasons": ", ".join(confidence.get("rejection_reasons", [NAS100Observer.REJECTION_REASON])),
    }


def experimental_symbol_snapshot(symbol: str) -> dict[str, Any]:
    """Return a visible placeholder for experimental dashboard symbols."""
    normalized = str(symbol).upper().strip()
    demo_sandbox = normalized in {"BTCUSD", "NAS100"}
    observer_only = normalized in {"EURUSD", "GBPUSD"}
    return {
        "symbol": normalized,
        "badge": DEMO_SANDBOX_LABEL if demo_sandbox else (OBSERVER_ONLY_LABEL if observer_only else ""),
        "state": "SANDBOX" if demo_sandbox else ("OBSERVER" if observer_only else "UNAVAILABLE"),
        "observer_state": observer_state("UNAVAILABLE") if demo_sandbox or observer_only else "",
        "display_state": "UNAVAILABLE" if demo_sandbox or observer_only else "",
        "state_kind": "OBSERVER_MOVEMENT" if demo_sandbox or observer_only else "PRODUCTION_CONFIDENCE",
        "confidence": 0,
        "raw_confidence": 0,
        "adjusted_confidence": 0,
        "raw_band": "UNAVAILABLE",
        "guardrail_penalty": "",
        "mode": DEMO_SANDBOX_LABEL if demo_sandbox else (OBSERVER_ONLY_LABEL if observer_only else "PRODUCTION"),
        "observer_note": (
            "SANDBOX DEMO ONLY. Not production, not funded, not challenge."
            if demo_sandbox
            else ("Diagnostic only. No execution." if observer_only else "")
        ),
        "decision": "WATCHLIST",
        "killzone": "n/a",
        "narrative": (
            "Demo sandbox symbol. Production execution disabled."
            if demo_sandbox
            else ("Observer symbol. Execution disabled." if observer_only else "Production symbol unavailable.")
        ),
        "plan_quality": "unavailable",
        "entry": 0.0,
        "sl": 0.0,
        "tp1": 0.0,
        "tp2": 0.0,
        "tp3": 0.0,
        "lot_size": 0.0,
        "execution_allowed": False,
        "rejection_reasons": "Demo sandbox diagnostics only" if demo_sandbox else ("Observer-only diagnostics" if observer_only else "Unavailable"),
    }


def unavailable_symbol_snapshot(symbol: str, reason: str) -> dict[str, Any]:
    """Return a safe unavailable symbol row."""
    row = experimental_symbol_snapshot(symbol)
    row.update({"state": "UNAVAILABLE", "decision": "UNAVAILABLE", "narrative": reason, "rejection_reasons": reason})
    return row


def fallback_snapshot(config: dict[str, Any], error: str = "") -> dict[str, Any]:
    """Return a safe dashboard snapshot when live MT5 is unavailable."""
    symbols = {str(symbol).upper(): experimental_symbol_snapshot(str(symbol).upper()) for symbol in config.get("symbols", DEFAULT_CONFIG["symbols"])}
    return {
        "connected": False,
        "risk": {
            "account": {"balance": 0.0, "equity": 0.0, "currency": "USD"},
            "permission": {"status": "UNAVAILABLE", "warnings": [error] if error else [], "block_reasons": []},
            "risk": {"risk_amount": 0.0},
        },
        "news": {"lock_active": False, "status": "UNAVAILABLE", "event_name": None, "reason": error},
        "symbols": symbols,
        "execution_mode": "advisor",
        "readiness": readiness_summary(error or "MT5 unavailable"),
        "symbol_registry": symbol_registry_rows(PROJECT_ROOT, config),
        "validation": load_validation_summary(PROJECT_ROOT, config),
        "market_watch": load_market_watch_summary(PROJECT_ROOT, config),
        "live_paper": load_live_paper_summary(PROJECT_ROOT, config),
        "emergency_live": load_emergency_live_summary(PROJECT_ROOT, config),
        "challenge_command_center": load_challenge_command_center_summary(PROJECT_ROOT, config),
        "assisted_execution": load_assisted_execution_summary(PROJECT_ROOT, config),
        "error": error,
    }


def readiness_summary(reason: str = "No assisted trade plan selected") -> dict[str, Any]:
    """Return a safe dashboard readiness card payload."""
    return {
        "ready": False,
        "score": 0,
        "checks_passed": 0,
        "checks_failed": 1,
        "results": [{"check": "readiness", "status": "FAIL", "reason": reason}],
        "blocking_reasons": [reason],
    }


def symbol_dataframe(snapshot: dict[str, Any]) -> pd.DataFrame:
    """Return live monitor symbol DataFrame."""
    return pd.DataFrame(list(snapshot.get("symbols", {}).values()))


def plan_dataframe(snapshot: dict[str, Any]) -> pd.DataFrame:
    """Return trade plan DataFrame."""
    columns = [
        "symbol",
        "badge",
        "state",
        "plan_quality",
        "entry",
        "sl",
        "tp1",
        "tp2",
        "tp3",
        "lot_size",
        "execution_allowed",
        "rejection_reasons",
    ]
    dataframe = symbol_dataframe(snapshot)
    if dataframe.empty:
        return pd.DataFrame(columns=columns)
    return dataframe.reindex(columns=columns)


def coach_report(project_root: str | Path, backtest_summary: dict[str, Any]) -> dict[str, Any]:
    """Return AI Coach output for dashboard display."""
    analyzer = AICoachAnalyzer(project_root=project_root)
    summary = backtest_summary.get("data", {}) if backtest_summary.get("available") else {}
    return analyzer.analyze(backtest_summary=summary)


def resolve_project_path(project_root: str | Path, path: str | Path) -> Path:
    """Resolve paths relative to the repository root."""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return Path(project_root) / candidate


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge dictionaries."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
