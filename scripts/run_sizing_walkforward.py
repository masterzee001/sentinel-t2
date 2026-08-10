"""Walk-forward proof of the expectancy sizing layer (Phase 2 gate, Law 5).

For each evaluation quarter, the expectancy table is built ONLY from trades in
prior quarters (expanding window), then applied to that quarter's trades. The
sized book is compared to the unsized baseline across all out-of-sample
quarters. Only a positive out-of-sample result may promote this layer toward
the live path.
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
from backend.sizing.expectancy_sizer import ExpectancySizer
from backend.symbols.symbol_registry import SymbolRegistry
from scripts.run_backtest_365d import ordered_unique, split_production_observer_trades, trim_to_lookback_days
from scripts.run_extended_backtest import quarter_key

DEFAULT_DAYS = 1095
MIN_TRAINING_QUARTERS = 4
REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "sizing_walkforward_report.json"
TABLE_PATH = PROJECT_ROOT / "data" / "reports" / "expectancy_table.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument(
        "--managed-exits",
        action="store_true",
        help="Simulate the live management plan (BE at 1R, 30%% partial at 2R, run to 3R).",
    )
    parser.add_argument("--exclude", nargs="*", default=[], help="Symbols to exclude from the scan.")
    args = parser.parse_args()

    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
        engine = BacktestEngine(connector=connector)
        if args.managed_exits:
            simulation_config = {
                **engine.config.get("simulation", {}),
                "exit_management": {"enabled": True, "breakeven_at_r": 1.0, "partial_at_r": 2.0, "partial_close_percent": 30, "final_target_r": 3.0},
            }
            engine.simulator = type(engine.simulator)(simulation_config)
        trades = collect_production_trades(engine, days=args.days, exclude=args.exclude)
        report = walkforward_evaluation(trades)
        report["generated_at"] = datetime.now(UTC).isoformat()
        report["requested_days"] = args.days
        report["managed_exits"] = bool(args.managed_exits)
        save_json(REPORT_PATH, report)
        # Full-history table for inspection and (if promoted) future live use.
        ExpectancySizer.from_trades(trades).save(TABLE_PATH)
        print_report(report)
        return 0
    except (MT5ConnectorError, BacktestEngineError, ValueError) as exc:
        print(f"Sizing walk-forward failed: {exc}")
        return 1
    finally:
        connector.shutdown()


def collect_production_trades(
    engine: BacktestEngine, *, days: int, exclude: list[str] | None = None
) -> list[dict[str, Any]]:
    """Scan production symbols and return adaptive-guardrail-passing trades."""
    registry = SymbolRegistry()
    engine.reset_candidate_ledgers()
    excluded = {str(symbol).upper().strip() for symbol in (exclude or [])}
    production_symbols = [
        symbol for symbol in ordered_unique(registry.execution_symbols()) if symbol not in excluded
    ]
    all_trades: list[dict[str, Any]] = []
    for symbol in production_symbols:
        candles = engine.fetch_backtest_candles(symbol, days)
        candles = trim_to_lookback_days(candles, days)
        symbol_trades, _ = engine.scan_symbol(symbol, candles)
        all_trades.extend(symbol_trades)
    production_trades, _ = split_production_observer_trades(all_trades, production_symbols)
    enriched = BacktestEngine.enrich_trade_pnl(
        production_trades, float(engine.config["starting_balance"]) * float(engine.config["risk_per_trade_percent"]) / 100
    )
    selected = BacktestEngine.filter_trades_by_guardrail_mode(enriched, mode="adaptive")
    return sorted(selected, key=lambda trade: str(trade.get("timestamp", "")))


def walkforward_evaluation(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Expanding-window evaluation: size each quarter with prior-quarter tables only."""
    quarters = sorted({quarter_key(trade.get("timestamp", "")) for trade in trades if quarter_key(trade.get("timestamp", ""))})
    per_quarter: dict[str, dict[str, Any]] = {}
    baseline_rrs: list[float] = []
    sized_rrs: list[float] = []
    skipped_trades = 0
    taken_trades = 0
    multiplier_usage: dict[str, int] = {}

    for position, quarter in enumerate(quarters):
        if position < MIN_TRAINING_QUARTERS:
            continue
        training = [trade for trade in trades if quarter_key(trade.get("timestamp", "")) in quarters[:position]]
        evaluation = [trade for trade in trades if quarter_key(trade.get("timestamp", "")) == quarter]
        if not evaluation:
            continue
        sizer = ExpectancySizer.from_trades(training)
        quarter_baseline: list[float] = []
        quarter_sized: list[float] = []
        for trade in evaluation:
            rr = float(trade.get("rr", 0.0))
            sizing = sizer.multiplier_for(
                trade.get("symbol"), trade.get("narrative_phase"), trade.get("killzone")
            )
            multiplier = float(sizing["multiplier"])
            multiplier_usage[sizing["classification"]] = multiplier_usage.get(sizing["classification"], 0) + 1
            quarter_baseline.append(rr)
            quarter_sized.append(rr * multiplier)
            if multiplier > 0:
                taken_trades += 1
            else:
                skipped_trades += 1
        baseline_rrs.extend(quarter_baseline)
        sized_rrs.extend(quarter_sized)
        per_quarter[quarter] = {
            "trades": len(evaluation),
            "baseline_net_rr": round(sum(quarter_baseline), 2),
            "sized_net_rr": round(sum(quarter_sized), 2),
        }

    return {
        "mode": "EXPANDING_WINDOW_OUT_OF_SAMPLE",
        "min_training_quarters": MIN_TRAINING_QUARTERS,
        "evaluated_quarters": len(per_quarter),
        "evaluated_trades": len(baseline_rrs),
        "trades_taken": taken_trades,
        "trades_skipped_by_sizer": skipped_trades,
        "multiplier_usage": multiplier_usage,
        "baseline": book_metrics(baseline_rrs),
        "sized": book_metrics(sized_rrs),
        "per_quarter": per_quarter,
        "verdict": sizing_verdict(book_metrics(baseline_rrs), book_metrics(sized_rrs)),
    }


def book_metrics(rrs: list[float]) -> dict[str, Any]:
    """Risk-weighted book metrics from a series of (possibly scaled) R values."""
    gross_win = sum(rr for rr in rrs if rr > 0)
    gross_loss = abs(sum(rr for rr in rrs if rr < 0))
    active = [rr for rr in rrs if rr != 0.0]
    return {
        "net_rr": round(sum(rrs), 2),
        "gross_win_rr": round(gross_win, 2),
        "gross_loss_rr": round(gross_loss, 2),
        "risk_weighted_pf": round(gross_win / gross_loss, 2) if gross_loss else round(gross_win, 2),
        "active_positions": len(active),
    }


def sizing_verdict(baseline: dict[str, Any], sized: dict[str, Any]) -> dict[str, Any]:
    """Judge the sizing layer strictly on out-of-sample improvement."""
    net_improved = float(sized["net_rr"]) > float(baseline["net_rr"])
    pf_improved = float(sized["risk_weighted_pf"]) > float(baseline["risk_weighted_pf"])
    return {
        "net_rr_improved": net_improved,
        "risk_weighted_pf_improved": pf_improved,
        "promote": net_improved and pf_improved,
        "note": "Both net R and risk-weighted PF must improve out-of-sample before this layer may touch the live path.",
    }


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temp_path.replace(path)


def print_report(report: dict[str, Any]) -> None:
    print("EXPECTANCY SIZING WALK-FORWARD (out-of-sample)")
    print("----------------------------------------------")
    print(f"Evaluated quarters: {report['evaluated_quarters']} | trades: {report['evaluated_trades']}")
    print(f"Taken: {report['trades_taken']} | skipped by sizer: {report['trades_skipped_by_sizer']}")
    print(f"Multiplier usage: {report['multiplier_usage']}")
    print("")
    baseline, sized = report["baseline"], report["sized"]
    print(f"BASELINE (flat 1.0x): net {baseline['net_rr']}R | risk-weighted PF {baseline['risk_weighted_pf']}")
    print(f"SIZED (expectancy):   net {sized['net_rr']}R | risk-weighted PF {sized['risk_weighted_pf']}")
    print("")
    print("PER QUARTER (baseline -> sized)")
    for quarter, row in report["per_quarter"].items():
        print(f"{quarter}: {row['baseline_net_rr']}R -> {row['sized_net_rr']}R ({row['trades']} trades)")
    verdict = report["verdict"]
    print("")
    print(f"VERDICT: promote={verdict['promote']} (net improved: {verdict['net_rr_improved']}, PF improved: {verdict['risk_weighted_pf_improved']})")
    print(f"Report: {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"Table:  {TABLE_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
