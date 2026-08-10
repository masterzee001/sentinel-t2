"""Run a 365-day adaptive-guardrail backtest and export a detailed report."""

from __future__ import annotations

import json
import sys
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.backtesting.backtest_engine import BacktestEngine, BacktestEngineError
from backend.killzone_engine.killzone_analyzer import KillzoneAnalyzer
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.shared.confidence_band_registry import MSS_MODE_HISTORICAL_STRUCTURE_BREAK
from backend.symbols.symbol_registry import SymbolRegistry


DAYS = 365
PRODUCTION_SYMBOLS = ["US30", "NAS100"]
OBSERVER_DIAGNOSTIC_SYMBOLS = ["XAUUSD", "BTCUSD", "EURUSD", "GBPUSD"]
SYMBOLS = [*PRODUCTION_SYMBOLS, *OBSERVER_DIAGNOSTIC_SYMBOLS]
REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "backtest_365d_summary.json"
REASON_LEDGER_PATH = PROJECT_ROOT / "data" / "reports" / "reason_ledger_365d.json"
RAW_BASELINE_FINDINGS = {
    "profit_factor": 1.16,
    "win_rate": 52.22,
    "trades_approved": 123,
    "max_drawdown": 3.94,
}
BASELINE_FINDINGS = RAW_BASELINE_FINDINGS
APPROVED_ROBUSTNESS_BASELINE = {
    "average_rr": 0.2,
    "avg_rr": 0.2,
    "breakevens": 10,
    "gross_loss": 475.0,
    "gross_profit": 750.0,
    "losses": 19,
    "max_drawdown": 2.97,
    "net_profit": 275.0,
    "net_rr": 11.0,
    "profit_factor": 1.58,
    "setups_scanned": 4802,
    "trades": 56,
    "trades_approved": 56,
    "win_rate": 58.7,
    "winrate": 58.7,
    "wins": 27,
}
APPROVED_ROBUSTNESS_SYMBOL_BREAKDOWN = {
    "US30": {
        "average_rr": 0.23,
        "avg_rr": 0.23,
        "breakevens": 7,
        "gross_loss": 400.0,
        "gross_profit": 675.0,
        "losses": 16,
        "max_drawdown": 2.0,
        "net_profit": 275.0,
        "net_rr": 11.0,
        "profit_factor": 1.69,
        "setups_scanned": 0,
        "trades": 47,
        "trades_approved": 47,
        "win_rate": 60.0,
        "winrate": 60.0,
        "wins": 24,
    },
    "XAUUSD": {
        "average_rr": 0.0,
        "avg_rr": 0.0,
        "breakevens": 3,
        "gross_loss": 75.0,
        "gross_profit": 75.0,
        "losses": 3,
        "max_drawdown": 0.99,
        "net_profit": 0.0,
        "net_rr": 0.0,
        "profit_factor": 1.0,
        "setups_scanned": 0,
        "trades": 9,
        "trades_approved": 9,
        "win_rate": 50.0,
        "winrate": 50.0,
        "wins": 3,
    },
}
COMPARISON_TOLERANCE = {
    "pf": 0.01,
    "win_rate": 0.1,
    "trades": 0,
    "max_drawdown": 0.05,
}


def main() -> int:
    """Run the requested one-year Advisor Mode backtest."""
    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
        engine = BacktestEngine(connector=connector)
        registry = SymbolRegistry()
        report = build_365d_report(engine, registry=registry)
        observer_only_metrics = report.get("symbol_expansion_observer_only", report.get("global_metrics", {}))
        report["comparison"] = build_observer_only_comparison(
            raw_baseline=raw_baseline_metrics(),
            approved_baseline=approved_robustness_metrics(),
            observer_only=observer_only_metrics,
        )
        save_report(
            {
                "generated_at": report["generated_at"],
                "summary": report.get("reason_ledger_summary", {}),
                "candidates": engine.candidate_ledgers,
            },
            REASON_LEDGER_PATH,
        )
        save_report(report, REPORT_PATH)
        print_report(report, REPORT_PATH)
        if report.get("reconciliation", {}).get("status") != "PASS":
            print("RECONCILIATION FAILED: headline metrics do not match their own breakdowns. Report is not evidence.")
            return 2
        return 0
    except (MT5ConnectorError, BacktestEngineError, ValueError) as exc:
        print(f"365-day backtest failed: {exc}")
        return 1
    finally:
        connector.shutdown()


def build_365d_report(engine: BacktestEngine, registry: SymbolRegistry | None = None) -> dict[str, Any]:
    """Build a detailed 365-day adaptive-guardrail report."""
    registry = registry or SymbolRegistry()
    engine.reset_candidate_ledgers()
    production_symbols = ordered_unique(registry.execution_symbols() or PRODUCTION_SYMBOLS)
    observer_symbols = ordered_unique(
        [symbol for symbol in registry.symbols() if symbol not in set(production_symbols)]
        or OBSERVER_DIAGNOSTIC_SYMBOLS
    )
    requested_symbols = ordered_unique([*production_symbols, *observer_symbols])
    all_trades: list[dict[str, Any]] = []
    setups_by_symbol: dict[str, int] = {}
    included_symbols: list[str] = []
    skipped_symbols: dict[str, str] = {}

    for symbol in requested_symbols:
        try:
            candles = engine.fetch_backtest_candles(symbol, DAYS)
            candles = trim_to_lookback_days(candles, DAYS)
            symbol_trades, symbol_setups = engine.scan_symbol(symbol, candles)
        except Exception as exc:
            skipped_symbols[symbol] = str(exc)
            continue
        all_trades.extend(symbol_trades)
        setups_by_symbol[symbol] = symbol_setups
        included_symbols.append(symbol)

    production_trades, observer_trades = split_production_observer_trades(all_trades, production_symbols)
    production_setups_scanned = sum(setups_by_symbol.get(symbol, 0) for symbol in production_symbols)
    results = BacktestEngine.aggregate_results(
        trades=production_trades,
        setups_scanned=production_setups_scanned,
        starting_balance=float(engine.config["starting_balance"]),
        risk_per_trade_percent=float(engine.config["risk_per_trade_percent"]),
        symbols=production_symbols,
        strategy_guardrails=engine.strategy_guardrails,
    )
    adaptive = BacktestEngine.summarize_guardrail_mode(
        results,
        mode="adaptive",
        starting_balance=float(engine.config["starting_balance"]),
        symbols=production_symbols,
        killzones=BacktestEngine.DIAGNOSTIC_KILLZONES,
        narrative_phases=BacktestEngine.NARRATIVE_PHASES,
    )
    selected_trades = BacktestEngine.filter_trades_by_guardrail_mode(results.get("trades", []), mode="adaptive")
    observer_results = BacktestEngine.aggregate_results(
        trades=observer_trades,
        setups_scanned=sum(setups_by_symbol.get(symbol, 0) for symbol in observer_symbols),
        starting_balance=float(engine.config["starting_balance"]),
        risk_per_trade_percent=float(engine.config["risk_per_trade_percent"]),
        symbols=observer_symbols,
        strategy_guardrails=engine.strategy_guardrails,
    )
    observer_selected_trades = BacktestEngine.filter_trades_by_guardrail_mode(observer_results.get("trades", []), mode="adaptive")
    observer_diagnostics = build_observer_diagnostics(
        selected_trades=observer_selected_trades,
        raw_trades=observer_results.get("trades", []),
        observer_symbols=observer_symbols,
        included_symbols=included_symbols,
        skipped_symbols=skipped_symbols,
        setups_by_symbol=setups_by_symbol,
        starting_balance=float(engine.config["starting_balance"]),
    )
    production_payload = build_production_payload(
        adaptive=adaptive,
        selected_trades=selected_trades,
        engine_config=engine.config,
        starting_balance=float(engine.config["starting_balance"]),
    )
    production_metrics = production_payload["metrics"]
    production_symbol_breakdown = production_payload["symbol_breakdown"]
    xau_smt_split = production_payload["xau_smt_split"]
    by_killzone = adaptive.get("by_killzone", {})
    by_narrative = adaptive.get("by_narrative", {})
    best_killzone, best_killzone_metrics = BacktestEngine.best_bucket(by_killzone)
    worst_killzone, worst_killzone_metrics = BacktestEngine.worst_bucket(by_killzone)
    monthly = monthly_breakdown(
        selected_trades,
        starting_balance=float(engine.config["starting_balance"]),
    )
    reconciliation = build_reconciliation(
        metrics=production_metrics,
        symbol_breakdown=production_symbol_breakdown,
        monthly=monthly,
    )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "days": DAYS,
        "guardrails": "ADAPTIVE",
        "mss_mode": MSS_MODE_HISTORICAL_STRUCTURE_BREAK,
        "mss_mode_note": "Historical MSS is grounded on a detected structure break with displacement; scoring and guardrails run through the live confidence brain with execution costs applied.",
        "advisor_mode_only": True,
        "requested_symbols": requested_symbols,
        "production_symbols": production_symbols,
        "observer_symbols": observer_symbols,
        "symbol_expansion": symbol_expansion_settings(engine.config),
        "symbols_included": included_symbols,
        "symbols_skipped": skipped_symbols,
        "setup_count_by_symbol": setups_by_symbol,
        "raw_baseline": normalize_metrics(raw_baseline_metrics()),
        "approved_robustness_baseline": normalize_metrics(approved_robustness_metrics()),
        "symbol_expansion_observer_only": production_metrics,
        "global_metrics": production_metrics,
        "production_portfolio": {
            "symbols": production_symbols,
            "metrics": production_metrics,
            "symbol_breakdown": production_symbol_breakdown,
            "xau_smt_split": xau_smt_split,
            "source": production_payload["source"],
        },
        "production_recalculation_diagnostics": {
            "not_used_for_production": False,
            "note": "Production metrics ARE the recomputed scan since Phase 0; this block is kept for schema compatibility.",
            "metrics": adaptive.get("overall", {}),
            "symbol_breakdown": adaptive.get("by_symbol", {}),
        },
        "reconciliation": reconciliation,
        "reason_ledger_summary": BacktestEngine.summarize_candidate_ledgers(engine.candidate_ledgers),
        "observer_diagnostics": observer_diagnostics,
        "symbol_breakdown": production_symbol_breakdown,
        "killzone_breakdown": {
            "best_killzone": {"name": best_killzone, "metrics": best_killzone_metrics},
            "worst_killzone": {"name": worst_killzone, "metrics": worst_killzone_metrics},
            "pf_by_killzone": {
                name: metrics.get("profit_factor", 0.0)
                for name, metrics in by_killzone.items()
            },
            "metrics": by_killzone,
        },
        "narrative_breakdown": {
            phase: metrics
            for phase, metrics in by_narrative.items()
            if phase in {"accumulation", "expansion", "distribution", "reversal", "range"}
        },
        "loss_clusters": loss_clusters(selected_trades),
        "monthly_breakdown": monthly,
        "xau_smt_split": xau_smt_split,
        "days_365": production_metrics,
    }


def build_production_payload(
    *,
    adaptive: dict[str, Any],
    selected_trades: list[dict[str, Any]],
    engine_config: dict[str, Any],
    starting_balance: float,
) -> dict[str, Any]:
    """Return production metrics recomputed from the current scan.

    The pre-Phase-0 version restored a stored constant baseline whenever the
    symbol-expansion gate was active (its default), which made the production
    number unfalsifiable. Observer symbols are already excluded upstream by
    split_production_observer_trades, so production metrics always come from
    the actual scan now. Stored baselines remain available only as labeled
    legacy references for comparison.
    """
    return {
        "source": "current_backtest_scan",
        "metrics": adaptive.get("overall", {}),
        "symbol_breakdown": adaptive.get("by_symbol", {}),
        "xau_smt_split": build_xau_smt_split(selected_trades, starting_balance=starting_balance),
    }


def build_reconciliation(
    *,
    metrics: dict[str, Any],
    symbol_breakdown: dict[str, dict[str, Any]],
    monthly: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Cross-check headline metrics against their own breakdowns.

    The legacy 365D report failed exactly this check (headline 56 trades vs.
    breakdowns summing to 73) without noticing. A report whose breakdowns do
    not sum to its headline is not evidence.
    """
    total_trades = int(metrics.get("trades_approved", metrics.get("trades", 0)) or 0)
    total_wins = int(metrics.get("wins", 0) or 0)
    total_losses = int(metrics.get("losses", 0) or 0)
    symbol_trades = sum(int(data.get("trades_approved", data.get("trades", 0)) or 0) for data in symbol_breakdown.values())
    symbol_wins = sum(int(data.get("wins", 0) or 0) for data in symbol_breakdown.values())
    symbol_losses = sum(int(data.get("losses", 0) or 0) for data in symbol_breakdown.values())
    monthly_trades = sum(int(data.get("trades_approved", data.get("trades", 0)) or 0) for data in monthly.values())
    checks = {
        "symbol_trades_match_total": symbol_trades == total_trades,
        "symbol_wins_match_total": symbol_wins == total_wins,
        "symbol_losses_match_total": symbol_losses == total_losses,
        "monthly_trades_match_total": monthly_trades == total_trades,
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "total_trades": total_trades,
        "symbol_trades_sum": symbol_trades,
        "monthly_trades_sum": monthly_trades,
        "checks": checks,
    }


def symbol_expansion_settings(config: dict[str, Any]) -> dict[str, bool]:
    """Return normalized symbol expansion safety settings."""
    settings = config.get("symbol_expansion", {}) if isinstance(config.get("symbol_expansion", {}), dict) else {}
    return {
        "observer_only": bool(settings.get("observer_only", True)),
        "affect_production": bool(settings.get("affect_production", False)),
    }


def raw_baseline_metrics() -> dict[str, Any]:
    """Return the pre-robustness 365D baseline."""
    return deepcopy(RAW_BASELINE_FINDINGS)


def approved_robustness_metrics() -> dict[str, Any]:
    """Return the approved US30/XAUUSD robustness baseline."""
    return deepcopy(APPROVED_ROBUSTNESS_BASELINE)


def approved_robustness_symbol_breakdown() -> dict[str, dict[str, Any]]:
    """Return approved production symbol metrics."""
    return deepcopy(APPROVED_ROBUSTNESS_SYMBOL_BREAKDOWN)


def approved_xau_smt_split() -> dict[str, Any]:
    """Return approved XAU split with SMT treated as an unproven sample."""
    with_smt = BacktestEngine.calculate_metrics([], 0, 5000.0)
    without_smt = deepcopy(APPROVED_ROBUSTNESS_SYMBOL_BREAKDOWN["XAUUSD"])
    return {
        "data_status": "AVAILABLE",
        "total_trades": int(without_smt.get("trades_approved", without_smt.get("trades", 0)) or 0),
        "with_smt": with_smt,
        "without_smt": without_smt,
        "dependency": "NO_SMT_SAMPLE",
        "rule": {
            "hard_block_enabled": False,
            "minimum_smt_sample": 10,
            "current_smt_sample": 0,
            "policy": "bonus_if_present_warning_if_absent",
        },
    }


def ordered_unique(values: list[str]) -> list[str]:
    """Return uppercase symbols without changing first-seen order."""
    seen: set[str] = set()
    symbols: list[str] = []
    for value in values:
        symbol = str(value).upper().strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def split_production_observer_trades(
    trades: list[dict[str, Any]],
    production_symbols: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split trades so observers cannot affect production portfolio metrics."""
    production = set(ordered_unique(production_symbols))
    production_trades: list[dict[str, Any]] = []
    observer_trades: list[dict[str, Any]] = []
    for trade in trades:
        symbol = str(trade.get("symbol", "")).upper().strip()
        if symbol in production:
            production_trades.append(trade)
        else:
            observer_trades.append(trade)
    return production_trades, observer_trades


def build_observer_diagnostics(
    *,
    selected_trades: list[dict[str, Any]],
    raw_trades: list[dict[str, Any]],
    observer_symbols: list[str],
    included_symbols: list[str],
    skipped_symbols: dict[str, str],
    setups_by_symbol: dict[str, int],
    starting_balance: float,
) -> dict[str, dict[str, Any]]:
    """Return diagnostics for non-production symbols without production impact."""
    included = set(ordered_unique(included_symbols))
    diagnostics: dict[str, dict[str, Any]] = {}
    for symbol in ordered_unique(observer_symbols):
        selected = [trade for trade in selected_trades if str(trade.get("symbol", "")).upper().strip() == symbol]
        raw = [trade for trade in raw_trades if str(trade.get("symbol", "")).upper().strip() == symbol]
        guarded_metrics = BacktestEngine.calculate_metrics(selected, 0, starting_balance)
        observer_metrics = BacktestEngine.calculate_metrics(raw, setups_by_symbol.get(symbol, 0), starting_balance)
        candles_available = symbol in included
        setups_forming = setups_by_symbol.get(symbol, 0) > 0
        guardrails_blocking_all = bool(raw and not selected)
        if symbol in skipped_symbols:
            status = "NO_HISTORICAL_DATA"
        elif symbol not in included:
            status = "NOT_REQUESTED"
        elif guardrails_blocking_all:
            status = "GUARDRAILS_BLOCKING_ALL_OPPORTUNITIES"
        elif selected:
            status = "OBSERVER_EVALUATED"
        elif candles_available and setups_forming:
            status = "SETUPS_FORMING_NO_ELIGIBLE_TRADES"
        elif candles_available:
            status = "CANDLES_AVAILABLE_NO_SETUPS"
        else:
            status = "NO_HISTORICAL_DATA"
        diagnostics[symbol] = {
            "symbol": symbol,
            "data_status": status,
            "execution_allowed": False,
            "production_excluded": True,
            "historical_data": candles_available,
            "candles_available": candles_available,
            "setups_forming": setups_forming,
            "guardrails_blocking_all_opportunities": guardrails_blocking_all,
            "setups_scanned": setups_by_symbol.get(symbol, 0),
            "raw_trades": len(raw),
            "guarded_trades": guarded_metrics.get("trades_approved", 0),
            "trades": observer_metrics.get("trades_approved", 0),
            "pf": observer_metrics.get("profit_factor", 0.0),
            "wr": observer_metrics.get("win_rate", 0.0),
            "profit_factor": observer_metrics.get("profit_factor", 0.0),
            "win_rate": observer_metrics.get("win_rate", 0.0),
            "max_drawdown": observer_metrics.get("max_drawdown", 0.0),
            "metrics": observer_metrics,
            "observer_only_metrics": observer_metrics,
            "guarded_metrics": guarded_metrics,
            "skip_reason": skipped_symbols.get(symbol, ""),
        }
    return diagnostics


def build_xau_smt_split(trades: list[dict[str, Any]], *, starting_balance: float) -> dict[str, Any]:
    """Return explicit XAU with-SMT and without-SMT metrics."""
    xau_trades = [trade for trade in trades if str(trade.get("symbol", "")).upper().strip() == "XAUUSD"]
    with_smt = [trade for trade in xau_trades if bool(trade.get("smt_detected", False))]
    without_smt = [trade for trade in xau_trades if not bool(trade.get("smt_detected", False))]
    with_metrics = BacktestEngine.calculate_metrics(with_smt, 0, starting_balance)
    without_metrics = BacktestEngine.calculate_metrics(without_smt, 0, starting_balance)
    return {
        "data_status": "AVAILABLE" if xau_trades else "NO_XAU_TRADES",
        "total_trades": len(xau_trades),
        "with_smt": with_metrics,
        "without_smt": without_metrics,
        "dependency": xau_smt_dependency(with_metrics, without_metrics, total_trades=len(xau_trades)),
    }


def xau_smt_dependency(with_smt: dict[str, Any], without_smt: dict[str, Any], *, total_trades: int) -> str:
    """Classify SMT dependency without returning an unknown state."""
    with_trades = int(with_smt.get("trades_approved", with_smt.get("trades", 0)) or 0)
    without_trades = int(without_smt.get("trades_approved", without_smt.get("trades", 0)) or 0)
    if total_trades <= 0:
        return "NO_XAU_TRADES"
    if with_trades <= 0:
        return "NO_SMT_SAMPLE"
    if without_trades <= 0:
        return "SMT_ONLY_SAMPLE"
    present_pf = float(with_smt.get("profit_factor", 0.0) or 0.0)
    absent_pf = float(without_smt.get("profit_factor", 0.0) or 0.0)
    if present_pf > absent_pf and absent_pf < 1.0:
        return "MANDATORY"
    if present_pf > absent_pf:
        return "HELPFUL"
    return "NOT_PROVEN"


def loss_clusters(trades: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """Return recurring losing-condition clusters."""
    losses = [trade for trade in trades if trade.get("simulation", {}).get("outcome") == "LOSS"]
    counter: Counter[tuple[str, str, str, str, str]] = Counter()
    for trade in losses:
        key = (
            str(trade.get("symbol", "unknown")),
            str(trade.get("killzone", "none")),
            str(trade.get("narrative_phase", "unknown")),
            str(trade.get("guarded_confidence_band", trade.get("confidence_band", "unknown"))),
            "SMT" if bool(trade.get("smt_detected", False)) else "No SMT",
        )
        counter[key] += 1
    return [
        {
            "symbol": symbol,
            "killzone": killzone,
            "narrative": narrative,
            "confidence_band": confidence_band,
            "smt_state": smt_state,
            "losses": count,
        }
        for (symbol, killzone, narrative, confidence_band, smt_state), count in counter.most_common(limit)
    ]


def monthly_breakdown(trades: list[dict[str, Any]], *, starting_balance: float) -> dict[str, dict[str, Any]]:
    """Return PF, win rate, trades, and drawdown grouped by month."""
    months = sorted({month_key(trade.get("timestamp", "")) for trade in trades if month_key(trade.get("timestamp", ""))})
    return {
        month: BacktestEngine.calculate_metrics(
            [trade for trade in trades if month_key(trade.get("timestamp", "")) == month],
            0,
            starting_balance,
        )
        for month in months
    }


def month_key(timestamp: Any) -> str:
    """Return YYYY-MM from an ISO-like timestamp."""
    text = str(timestamp or "")
    return text[:7] if len(text) >= 7 else ""


def trim_to_lookback_days(candles: Any, days: int) -> Any:
    """Limit fetched candles to the requested calendar lookback window."""
    if candles.empty or "time" not in candles.columns:
        return candles
    latest = candles["time"].max()
    cutoff = latest - timedelta(days=int(days))
    return candles[candles["time"] >= cutoff].reset_index(drop=True)


def save_report(report: dict[str, Any], path: Path) -> None:
    """Write the detailed 365-day report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temp_path.replace(path)


def load_baseline_metrics(path: Path) -> dict[str, Any]:
    """Return the pre-robustness 365D findings as the before baseline."""
    return raw_baseline_metrics()


def normalize_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Return common PF/WR/trade/DD keys for comparison output."""
    return {
        "pf": round(float(metrics.get("profit_factor", metrics.get("pf", 0.0)) or 0.0), 2),
        "win_rate": round(float(metrics.get("win_rate", metrics.get("wr", 0.0)) or 0.0), 2),
        "trades": int(metrics.get("trades_approved", metrics.get("trades", 0)) or 0),
        "max_drawdown": round(float(metrics.get("max_drawdown", metrics.get("dd", 0.0)) or 0.0), 2),
    }


def build_observer_only_comparison(
    *,
    raw_baseline: dict[str, Any],
    approved_baseline: dict[str, Any],
    observer_only: dict[str, Any],
) -> dict[str, Any]:
    """Return raw, approved, and observer-only production comparison."""
    raw = normalize_metrics(raw_baseline)
    approved = normalize_metrics(approved_baseline)
    observer = normalize_metrics(observer_only)
    return {
        "raw_baseline": raw,
        "approved_robustness_baseline": approved,
        "symbol_expansion_observer_only": observer,
        "before": approved,
        "after": observer,
        "within_tolerance": metrics_within_tolerance(approved, observer),
        "tolerance": deepcopy(COMPARISON_TOLERANCE),
        "targets": {
            "pf_above_1_5": observer["pf"] > 1.5,
            "wr_above_55": observer["win_rate"] > 55.0,
            "max_dd_below_4": observer["max_drawdown"] < 4.0,
            "trades_at_least_50": observer["trades"] >= 50,
        },
    }


def metrics_within_tolerance(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Return whether observer-only production matches the approved baseline."""
    return (
        abs(float(expected.get("pf", 0.0)) - float(actual.get("pf", 0.0))) <= COMPARISON_TOLERANCE["pf"]
        and abs(float(expected.get("win_rate", 0.0)) - float(actual.get("win_rate", 0.0))) <= COMPARISON_TOLERANCE["win_rate"]
        and abs(int(expected.get("trades", 0)) - int(actual.get("trades", 0))) <= COMPARISON_TOLERANCE["trades"]
        and abs(float(expected.get("max_drawdown", 0.0)) - float(actual.get("max_drawdown", 0.0))) <= COMPARISON_TOLERANCE["max_drawdown"]
    )


def build_comparison(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Return concise before-vs-after robustness metrics."""
    return {
        "before": {
            "pf": before.get("profit_factor", before.get("pf", 0.0)),
            "win_rate": before.get("win_rate", 0.0),
            "trades": before.get("trades_approved", before.get("trades", 0)),
            "max_drawdown": before.get("max_drawdown", 0.0),
        },
        "after": {
            "pf": after.get("profit_factor", after.get("pf", 0.0)),
            "win_rate": after.get("win_rate", 0.0),
            "trades": after.get("trades_approved", after.get("trades", 0)),
            "max_drawdown": after.get("max_drawdown", 0.0),
        },
        "targets": {
            "pf_above_1_5": float(after.get("profit_factor", 0.0) or 0.0) > 1.5,
            "wr_above_55": float(after.get("win_rate", 0.0) or 0.0) > 55.0,
            "max_dd_below_4": float(after.get("max_drawdown", 0.0) or 0.0) < 4.0,
            "trades_at_least_50": int(after.get("trades_approved", after.get("trades", 0)) or 0) >= 50,
        },
    }


def print_report(report: dict[str, Any], path: Path) -> None:
    """Print the concise terminal summary requested by Prompt 027."""
    metrics = report.get("production_portfolio", {}).get("metrics", report.get("global_metrics", {}))
    comparison = report.get("comparison", {})
    raw = comparison.get("raw_baseline", report.get("raw_baseline", {}))
    approved = comparison.get("approved_robustness_baseline", report.get("approved_robustness_baseline", {}))
    observer_only = comparison.get("symbol_expansion_observer_only", report.get("symbol_expansion_observer_only", {}))
    print("365-DAY ADAPTIVE BACKTEST")
    print("-------------------------")
    print("Advisor Mode only. No live execution.")
    print(f"Report saved: {path.relative_to(PROJECT_ROOT)}")
    if report.get("symbols_skipped"):
        print(f"Skipped symbols: {', '.join(report['symbols_skipped'])}")
    print("")
    print("A. RAW BASELINE")
    print(f"PF: {raw.get('pf', 0.0)}")
    print(f"WR: {raw.get('win_rate', 0.0)}%")
    print(f"Trades: {raw.get('trades', 0)}")
    print(f"DD: {raw.get('max_drawdown', 0.0)}%")
    print("")
    print("B. APPROVED ROBUSTNESS BASELINE")
    print(f"PF: {approved.get('pf', 0.0)}")
    print(f"WR: {approved.get('win_rate', 0.0)}%")
    print(f"Trades: {approved.get('trades', 0)}")
    print(f"DD: {approved.get('max_drawdown', 0.0)}%")
    print("")
    print("C. SYMBOL EXPANSION OBSERVER-ONLY")
    print(f"PF: {observer_only.get('pf', 0.0)}")
    print(f"WR: {observer_only.get('win_rate', 0.0)}%")
    print(f"Trades: {observer_only.get('trades', 0)}")
    print(f"DD: {observer_only.get('max_drawdown', 0.0)}%")
    print(f"Matches approved baseline: {comparison.get('within_tolerance', False)}")
    print("")
    print("PRODUCTION PORTFOLIO")
    print(f"Symbols: {', '.join(report.get('production_symbols', []))}")
    print(f"Source: {report.get('production_portfolio', {}).get('source', 'current_backtest_scan')}")
    print(f"Total setups scanned: {metrics.get('setups_scanned', 0)}")
    print(f"Trades approved: {metrics.get('trades_approved', 0)}")
    print(f"Wins: {metrics.get('wins', 0)}")
    print(f"Losses: {metrics.get('losses', 0)}")
    print(f"Breakevens: {metrics.get('breakevens', 0)}")
    print(f"Win rate: {metrics.get('win_rate', 0.0)}%")
    print(f"Profit factor: {metrics.get('profit_factor', 0.0)}")
    print(f"Average RR: {metrics.get('average_rr', 0.0)}")
    print(f"Net RR: {metrics.get('net_rr', 0.0)}")
    print(f"Max drawdown: {metrics.get('max_drawdown', 0.0)}%")
    print("")
    print("PRODUCTION SYMBOL BREAKDOWN")
    for symbol, data in report.get("symbol_breakdown", {}).items():
        print(
            f"{symbol}: trades {data.get('trades_approved', 0)}, WR {data.get('win_rate', 0.0)}%, "
            f"PF {data.get('profit_factor', 0.0)}, avg RR {data.get('average_rr', 0.0)}, "
            f"max DD {data.get('max_drawdown', 0.0)}%"
        )
    print("")
    print("D. OBSERVER DIAGNOSTICS")
    observer_diagnostics = report.get("observer_diagnostics", {})
    for symbol in report.get("observer_symbols", []):
        data = observer_diagnostics.get(symbol, {})
        label = observer_display_name(symbol)
        print(f"{label}:")
        print(f"Data status: {data.get('data_status', 'NOT_REQUESTED')}")
        print(f"Historical data: {data.get('historical_data', False)}")
        print(f"Candles available: {data.get('candles_available', False)}")
        print(f"Setups forming: {data.get('setups_forming', False)}")
        print(f"Guardrails blocking all opportunities: {data.get('guardrails_blocking_all_opportunities', False)}")
        print(f"Trades: {data.get('trades', 0)}")
        print(f"PF: {data.get('pf', 0.0)}")
        print(f"WR: {data.get('wr', 0.0)}%")
    print("")
    print("XAU SMT SPLIT")
    xau_split = report.get("xau_smt_split", {})
    for label, key in (("XAU with SMT", "with_smt"), ("XAU without SMT", "without_smt")):
        data = xau_split.get(key, {})
        print(
            f"{label}: trades {data.get('trades_approved', data.get('trades', 0))}, "
            f"PF {data.get('profit_factor', 0.0)}, WR {data.get('win_rate', 0.0)}%"
        )
    print(f"SMT Dependency: {xau_split.get('dependency', 'NO_XAU_TRADES')}")
    print("")
    print("KILLZONE BREAKDOWN")
    killzone = report.get("killzone_breakdown", {})
    print(f"Best: {display_killzone(killzone.get('best_killzone', {}).get('name', 'none'))}")
    print(f"Worst: {display_killzone(killzone.get('worst_killzone', {}).get('name', 'none'))}")
    for name, pf in killzone.get("pf_by_killzone", {}).items():
        print(f"{display_killzone(name)} PF: {pf}")
    print("")
    print("NARRATIVE BREAKDOWN")
    for phase, data in report.get("narrative_breakdown", {}).items():
        print(f"{phase}: trades {data.get('trades_approved', 0)}, WR {data.get('win_rate', 0.0)}%, PF {data.get('profit_factor', 0.0)}")
    print("")
    print("TOP LOSS CLUSTERS")
    clusters = report.get("loss_clusters", [])
    if not clusters:
        print("none")
    for cluster in clusters[:5]:
        print(
            f"{cluster.get('symbol')} | {display_killzone(cluster.get('killzone', 'none'))} | "
            f"{cluster.get('narrative')} | {cluster.get('confidence_band')} | "
            f"{cluster.get('smt_state')} -> {cluster.get('losses')} losses"
        )
    print("")
    print("MONTHLY BREAKDOWN")
    for month, data in report.get("monthly_breakdown", {}).items():
        print(
            f"{month}: PF {data.get('profit_factor', 0.0)}, WR {data.get('win_rate', 0.0)}%, "
            f"trades {data.get('trades_approved', 0)}, DD {data.get('max_drawdown', 0.0)}%"
        )
    ledger = report.get("reason_ledger_summary", {})
    if ledger:
        print("")
        print("REASON LEDGER (ADMISSION EFFICIENCY)")
        print(f"Candidates: {ledger.get('total_candidates', 0)} | Admitted: {ledger.get('admitted', 0)} | Rejected: {ledger.get('rejected', 0)}")
        print(f"QAER (winners admitted / HQ candidates): {ledger.get('qaer', 0.0)}%")
        print(f"FRR (winners rejected / HQ candidates): {ledger.get('frr', 0.0)}%")
        print(f"Rejected would-have-won: {ledger.get('rejected_would_have_won', 0)} | would-have-lost: {ledger.get('rejected_would_have_lost', 0)}")
        print("Top rejection reasons:")
        for code, row in list(ledger.get("rejection_reason_breakdown", {}).items())[:5]:
            print(
                f"  {code}: {row.get('count', 0)} blocks, {row.get('would_have_won', 0)} winners lost"
                f" ({row.get('missed_rr', 0.0)}R missed), {row.get('would_have_lost', 0)} losses avoided"
            )


def display_killzone(name: Any) -> str:
    """Return readable killzone text."""
    value = str(name or "none")
    if value == "none":
        return "None"
    return KillzoneAnalyzer.display_name(value)


def observer_display_name(symbol: Any) -> str:
    """Return concise observer labels for terminal output."""
    value = str(symbol or "").upper().strip()
    if value == "BTCUSD":
        return "BTC"
    if value == "NAS100":
        return "NAS100/USTEC"
    return value


if __name__ == "__main__":
    raise SystemExit(main())
