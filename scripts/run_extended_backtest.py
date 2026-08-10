"""Extended multi-year walk-forward backtest with quarterly out-of-sample windows.

Runs the honest engine (live-parity decision brain + execution costs) over as
much M15 history as the broker serves, then reports performance per quarter so
edge stability is visible instead of one aggregate number fitted to one year.
No parameters are tuned here; this is measurement only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.backtesting.backtest_engine import BacktestEngine, BacktestEngineError
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.symbols.symbol_registry import SymbolRegistry
from scripts.run_backtest_365d import (
    build_reconciliation,
    ordered_unique,
    split_production_observer_trades,
    trim_to_lookback_days,
)

DEFAULT_DAYS = 1095
REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "extended_backtest_report.json"
LEDGER_PATH = PROJECT_ROOT / "data" / "reports" / "reason_ledger_extended.json"


def main() -> int:
    """Run the extended walk-forward backtest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Calendar days of history to request.")
    args = parser.parse_args()

    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
        engine = BacktestEngine(connector=connector)
        report = build_extended_report(engine, days=args.days)
        save_json(REPORT_PATH, report)
        save_json(
            LEDGER_PATH,
            {
                "generated_at": report["generated_at"],
                "summary": report["reason_ledger_summary"],
                "candidates": engine.candidate_ledgers,
            },
        )
        print_report(report)
        if report["reconciliation"]["status"] != "PASS":
            print("RECONCILIATION FAILED: report is not evidence.")
            return 2
        return 0
    except (MT5ConnectorError, BacktestEngineError, ValueError) as exc:
        print(f"Extended backtest failed: {exc}")
        return 1
    finally:
        connector.shutdown()


def build_extended_report(engine: BacktestEngine, *, days: int) -> dict[str, Any]:
    """Scan all registry symbols over the requested history and aggregate honestly."""
    registry = SymbolRegistry()
    engine.reset_candidate_ledgers()
    production_symbols = ordered_unique(registry.execution_symbols())
    observer_symbols = ordered_unique(
        [symbol for symbol in registry.symbols() if symbol not in set(production_symbols)]
    )
    requested_symbols = ordered_unique([*production_symbols, *observer_symbols])

    all_trades: list[dict[str, Any]] = []
    setups_by_symbol: dict[str, int] = {}
    data_spans: dict[str, dict[str, Any]] = {}
    skipped_symbols: dict[str, str] = {}

    for symbol in requested_symbols:
        try:
            candles = engine.fetch_backtest_candles(symbol, days)
            candles = trim_to_lookback_days(candles, days)
            data_spans[symbol] = describe_span(candles, days)
            symbol_trades, symbol_setups = engine.scan_symbol(symbol, candles)
        except Exception as exc:
            skipped_symbols[symbol] = str(exc)
            continue
        all_trades.extend(symbol_trades)
        setups_by_symbol[symbol] = symbol_setups

    production_trades, observer_trades = split_production_observer_trades(all_trades, production_symbols)
    starting_balance = float(engine.config["starting_balance"])
    results = BacktestEngine.aggregate_results(
        trades=production_trades,
        setups_scanned=sum(setups_by_symbol.get(symbol, 0) for symbol in production_symbols),
        starting_balance=starting_balance,
        risk_per_trade_percent=float(engine.config["risk_per_trade_percent"]),
        symbols=production_symbols,
        strategy_guardrails=engine.strategy_guardrails,
    )
    selected = BacktestEngine.filter_trades_by_guardrail_mode(results.get("trades", []), mode="adaptive")
    overall = BacktestEngine.calculate_metrics(
        selected,
        sum(setups_by_symbol.get(symbol, 0) for symbol in production_symbols),
        starting_balance,
    )
    by_symbol = {
        symbol: BacktestEngine.calculate_metrics(
            [trade for trade in selected if trade.get("symbol") == symbol], 0, starting_balance
        )
        for symbol in production_symbols
    }
    quarterly = quarterly_breakdown(selected, starting_balance=starting_balance)
    reconciliation = build_reconciliation(
        metrics=overall,
        symbol_breakdown=by_symbol,
        monthly=quarterly,
    )
    observer_metrics = {
        symbol: BacktestEngine.calculate_metrics(
            [trade for trade in observer_trades if trade.get("symbol") == symbol], 0, starting_balance
        )
        for symbol in observer_symbols
    }

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "requested_days": days,
        "mode": "EXTENDED_WALKFORWARD_MEASUREMENT_ONLY",
        "production_symbols": production_symbols,
        "observer_symbols": observer_symbols,
        "data_spans": data_spans,
        "symbols_skipped": skipped_symbols,
        "setup_count_by_symbol": setups_by_symbol,
        "overall": overall,
        "by_symbol": by_symbol,
        "quarterly": quarterly,
        "stability": stability_stats(quarterly),
        "observer_metrics": observer_metrics,
        "reconciliation": reconciliation,
        "reason_ledger_summary": BacktestEngine.summarize_candidate_ledgers(engine.candidate_ledgers),
    }


def describe_span(candles: Any, requested_days: int) -> dict[str, Any]:
    """Report the actual candle span so short broker history is visible."""
    if candles.empty or "time" not in candles.columns:
        return {"candles": 0, "first": None, "last": None, "actual_days": 0, "requested_days": requested_days}
    first = candles["time"].min()
    last = candles["time"].max()
    return {
        "candles": int(len(candles)),
        "first": str(first),
        "last": str(last),
        "actual_days": int((last - first).days),
        "requested_days": requested_days,
        "full_history_served": bool((last - first).days >= requested_days - 7),
    }


def quarter_key(timestamp: Any) -> str:
    """Return YYYY-Qn from an ISO-like timestamp."""
    text = str(timestamp or "")
    if len(text) < 7:
        return ""
    year, month = text[:4], text[5:7]
    try:
        quarter = (int(month) - 1) // 3 + 1
    except ValueError:
        return ""
    return f"{year}-Q{quarter}"


def quarterly_breakdown(trades: list[dict[str, Any]], *, starting_balance: float) -> dict[str, dict[str, Any]]:
    """Return metrics grouped by calendar quarter."""
    quarters = sorted({quarter_key(trade.get("timestamp", "")) for trade in trades if quarter_key(trade.get("timestamp", ""))})
    return {
        quarter: BacktestEngine.calculate_metrics(
            [trade for trade in trades if quarter_key(trade.get("timestamp", "")) == quarter],
            0,
            starting_balance,
        )
        for quarter in quarters
    }


def stability_stats(quarterly: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Summarize edge stability across quarters."""
    rows = [
        (quarter, float(data.get("net_rr", 0.0)), float(data.get("profit_factor", 0.0)), int(data.get("trades_approved", 0)))
        for quarter, data in quarterly.items()
        if int(data.get("trades_approved", 0)) > 0
    ]
    if not rows:
        return {"quarters": 0, "profitable_quarters": 0, "losing_quarters": 0}
    net_rrs = [net for _, net, _, _ in rows]
    worst = min(rows, key=lambda row: row[1])
    best = max(rows, key=lambda row: row[1])
    return {
        "quarters": len(rows),
        "profitable_quarters": sum(1 for net in net_rrs if net > 0),
        "losing_quarters": sum(1 for net in net_rrs if net < 0),
        "best_quarter": {"quarter": best[0], "net_rr": best[1], "pf": best[2], "trades": best[3]},
        "worst_quarter": {"quarter": worst[0], "net_rr": worst[1], "pf": worst[2], "trades": worst[3]},
        "average_quarterly_net_rr": round(sum(net_rrs) / len(net_rrs), 2),
    }


def save_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write a JSON report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temp_path.replace(path)


def print_report(report: dict[str, Any]) -> None:
    """Print the terminal summary."""
    overall = report["overall"]
    print("EXTENDED WALK-FORWARD BACKTEST (measurement only, costs applied)")
    print("---------------------------------------------------------------")
    print(f"Requested days: {report['requested_days']}")
    for symbol, span in report["data_spans"].items():
        print(
            f"{symbol}: {span['candles']} candles, {span['first']} -> {span['last']}"
            f" ({span['actual_days']} days, full history: {span.get('full_history_served', False)})"
        )
    if report["symbols_skipped"]:
        print(f"Skipped: {report['symbols_skipped']}")
    print("")
    print("PRODUCTION AGGREGATE (adaptive guardrails)")
    print(
        f"PF {overall.get('profit_factor', 0.0)} | WR {overall.get('win_rate', 0.0)}% | "
        f"Trades {overall.get('trades_approved', 0)} | DD {overall.get('max_drawdown', 0.0)}% | "
        f"Net {overall.get('net_rr', 0.0)}R"
    )
    print("")
    print("BY SYMBOL")
    for symbol, data in report["by_symbol"].items():
        print(
            f"{symbol}: PF {data.get('profit_factor', 0.0)} | WR {data.get('win_rate', 0.0)}% | "
            f"Trades {data.get('trades_approved', 0)} | Net {data.get('net_rr', 0.0)}R"
        )
    print("")
    print("QUARTERLY (out-of-sample windows)")
    for quarter, data in report["quarterly"].items():
        print(
            f"{quarter}: PF {data.get('profit_factor', 0.0)} | WR {data.get('win_rate', 0.0)}% | "
            f"Trades {data.get('trades_approved', 0)} | Net {data.get('net_rr', 0.0)}R"
        )
    stability = report["stability"]
    print("")
    print(
        f"STABILITY: {stability.get('profitable_quarters', 0)}/{stability.get('quarters', 0)} quarters profitable | "
        f"avg {stability.get('average_quarterly_net_rr', 0.0)}R/quarter | "
        f"worst {stability.get('worst_quarter', {}).get('quarter', 'n/a')} "
        f"({stability.get('worst_quarter', {}).get('net_rr', 0.0)}R)"
    )
    ledger = report["reason_ledger_summary"]
    print("")
    print("REASON LEDGER")
    print(
        f"Candidates {ledger.get('total_candidates', 0)} | Admitted {ledger.get('admitted', 0)} | "
        f"QAER {ledger.get('qaer', 0.0)}% | FRR {ledger.get('frr', 0.0)}%"
    )
    for code, row in list(ledger.get("rejection_reason_breakdown", {}).items())[:6]:
        print(
            f"  {code}: {row.get('count', 0)} blocks, {row.get('would_have_won', 0)} winners lost "
            f"({row.get('missed_rr', 0.0)}R missed), {row.get('would_have_lost', 0)} losses avoided"
        )
    print("")
    print(f"Reconciliation: {report['reconciliation']['status']}")
    print(f"Report: {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"Ledger: {LEDGER_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
