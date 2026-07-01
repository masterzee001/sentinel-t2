"""Manual smoke test for the Project Sentinel Backtesting Engine."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.backtesting.backtest_engine import BacktestEngine, BacktestEngineError
from backend.backtesting.report_cache import save_backtest_summary
from backend.killzone_engine.killzone_analyzer import KillzoneAnalyzer
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError


def main() -> int:
    logger.remove()
    args = parse_args()
    connector = MT5Connector()
    try:
        connector.connect()
        engine = BacktestEngine(connector=connector)
        if args.long:
            matrix = engine.run_long_horizon(days_options=(30, 90), symbols=args.symbols)
            cache = save_backtest_summary(matrix)
            print_long_horizon_report(matrix)
            print("")
            print(f"Backtest cache saved: data/reports/latest_backtest_summary.json ({cache['generated_at']})")
        elif args.days is not None or args.guardrails is not None:
            days = int(args.days or engine.config["lookback_days"])
            mode = normalize_guardrail_mode(args.guardrails or "off")
            results = engine.run(lookback_days=days, symbols=args.symbols)
            summary = BacktestEngine.summarize_guardrail_mode(
                results,
                mode=mode,
                starting_balance=float(engine.config["starting_balance"]),
                symbols=args.symbols,
            )
            print_single_mode_results(days, summary)
        else:
            results = engine.run(lookback_days=7, symbols=args.symbols)
            print_results(results)
        return 0
    except (MT5ConnectorError, BacktestEngineError, ValueError) as exc:
        print(f"Backtesting engine test failed: {exc}")
        return 1
    finally:
        connector.shutdown()


def parse_args() -> argparse.Namespace:
    """Parse command line options for smoke and long-horizon modes."""
    parser = argparse.ArgumentParser(description="Run Project Sentinel historical backtests.")
    parser.add_argument("--days", type=int, choices=[30, 90], help="Lookback window for a single long-horizon run.")
    parser.add_argument("--guardrails", choices=["off", "on", "hard", "adaptive"], help="Apply a guardrail mode to a single run. 'on' maps to adaptive.")
    parser.add_argument("--long", action="store_true", help="Run 30-day and 90-day off/hard/adaptive comparison matrix.")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=list(BacktestEngine.LONG_HORIZON_SYMBOLS),
        help="Symbols to include. Long-horizon defaults to XAUUSD and US30.",
    )
    return parser.parse_args()


def normalize_guardrail_mode(value: str) -> str:
    """Normalize CLI guardrail aliases."""
    mode = value.lower().strip()
    return "adaptive" if mode == "on" else mode


def print_results(results: dict) -> None:
    """Print clean backtest output."""
    overall = results["overall"]
    diagnostics = results["diagnostics"]
    best_symbol = diagnostics["best_symbol"]
    worst_symbol = diagnostics["worst_symbol"]
    best_killzone = diagnostics["best_killzone"]

    print("BACKTEST RESULTS")
    print("----------------")
    print(f"Setups scanned: {overall['setups_scanned']}")
    print(f"Trades approved: {overall['trades_approved']}")
    print(f"Wins: {overall['wins']}")
    print(f"Losses: {overall['losses']}")
    print(f"Breakevens: {overall['breakevens']}")
    print(f"Win Rate: {overall['win_rate']}%")
    print(f"Profit Factor: {overall['profit_factor']}")
    print(f"Average RR: {overall['average_rr']}")
    print(f"Max Drawdown: {overall['max_drawdown']}%")
    print("")
    print("Best Symbol:")
    print(format_bucket(best_symbol["name"], best_symbol["metrics"]))
    print("")
    print("Worst Symbol:")
    print(format_bucket(worst_symbol["name"], worst_symbol["metrics"]))
    print("")
    print("Best Killzone:")
    print(format_bucket(best_killzone["name"], best_killzone["metrics"], use_killzone_display=True))
    print("")
    print_diagnostics(results)
    print("")
    print_guardrail_impact(results)
    print("")
    print("Advisor Mode only: no live trade execution was taken.")


def print_single_mode_results(days: int, summary: dict[str, Any]) -> None:
    """Print one requested backtest mode."""
    overall = summary["overall"]
    print("LONG HORIZON BACKTEST")
    print("---------------------")
    print(f"Days: {days}")
    print(f"Guardrails: {summary['guardrails']}")
    print_metrics(overall)
    print("")
    print_breakdowns(summary)
    print("")
    print("Advisor Mode only: no live trade execution was taken.")


def print_long_horizon_report(matrix: dict[int, dict[str, Any]]) -> None:
    """Print the required 30/90 day long-horizon comparison report."""
    print("LONG HORIZON BACKTEST")
    print("---------------------")
    print("")
    for days in BacktestEngine.LONG_HORIZON_DAYS:
        day_results = matrix.get(days, {})
        off = day_results.get("guardrails_off", {}).get("overall", {})
        hard = day_results.get("old_hard_guardrails", {}).get("overall", {})
        adaptive = day_results.get("adaptive_guardrails", {}).get("overall", {})
        print(f"{days} DAYS")
        print_comparison_row("Guardrails OFF", off)
        print("")
        print_comparison_row("Old Hard Guardrails", hard)
        print("")
        print_comparison_row("Adaptive Guardrails", adaptive)
        print("")
        print("Improvement:")
        print(f"Adaptive PF delta vs OFF: {round(float(adaptive.get('profit_factor', 0.0)) - float(off.get('profit_factor', 0.0)), 2)}")
        print(f"Adaptive win rate delta vs OFF: {round(float(adaptive.get('win_rate', 0.0)) - float(off.get('win_rate', 0.0)), 2)}%")
        print(f"Adaptive trade reduction vs OFF: {int(off.get('trades_approved', 0)) - int(adaptive.get('trades_approved', 0))}")
        print("")
        print_breakdowns(day_results.get("guardrails_off", {}), title=f"{days}D Guardrails OFF Breakdown")
        print("")
        print_breakdowns(day_results.get("old_hard_guardrails", {}), title=f"{days}D Old Hard Guardrails Breakdown")
        print("")
        print_breakdowns(day_results.get("adaptive_guardrails", {}), title=f"{days}D Adaptive Guardrails Breakdown")
        if days != BacktestEngine.LONG_HORIZON_DAYS[-1]:
            print("")
            print("---------------------")
            print("")

    guarded_90 = matrix.get(90, {}).get("adaptive_guardrails", {}).get("overall", {})
    print("")
    print("PHASE DECISION")
    print("--------------")
    if BacktestEngine.qualifies_for_phase_three(guarded_90):
        print("Sentinel qualifies for Phase 3: Execution Automation Research")
    else:
        print("Continue optimization.")
    print("")
    print("Advisor Mode only: no live trade execution was taken.")


def print_comparison_row(label: str, metrics: dict[str, Any]) -> None:
    """Print required comparison metrics for one guardrail mode."""
    print(f"{label}:")
    print(f"PF: {metrics.get('profit_factor', 0.0)}")
    print(f"Win Rate: {metrics.get('win_rate', 0.0)}%")
    print(f"Trades: {metrics.get('trades_approved', 0)}")
    print(f"Max Drawdown: {metrics.get('max_drawdown', 0.0)}%")
    print(f"Net RR: {metrics.get('net_rr', 0.0)}")


def print_metrics(metrics: dict[str, Any]) -> None:
    """Print required metric fields."""
    print(f"Setups scanned: {metrics.get('setups_scanned', 0)}")
    print(f"Trades approved: {metrics.get('trades_approved', 0)}")
    print(f"Wins: {metrics.get('wins', 0)}")
    print(f"Losses: {metrics.get('losses', 0)}")
    print(f"Breakevens: {metrics.get('breakevens', 0)}")
    print(f"Win Rate: {metrics.get('win_rate', 0.0)}%")
    print(f"Profit Factor: {metrics.get('profit_factor', 0.0)}")
    print(f"Average RR: {metrics.get('average_rr', 0.0)}")
    print(f"Max Drawdown: {metrics.get('max_drawdown', 0.0)}%")
    print(f"Net RR: {metrics.get('net_rr', 0.0)}")


def print_breakdowns(summary: dict[str, Any], title: str = "Breakdown") -> None:
    """Print symbol, killzone, and narrative metrics."""
    if not summary:
        print(f"{title}: none")
        return

    print(title)
    print("-" * len(title))
    print("By Symbol:")
    for name, metrics in summary.get("by_symbol", {}).items():
        print(f"- {name}: PF {metrics.get('profit_factor', 0.0)}, Win Rate {metrics.get('win_rate', 0.0)}%, Trades {metrics.get('trades_approved', 0)}, Net RR {metrics.get('net_rr', 0.0)}")
    print("By Killzone:")
    for name, metrics in summary.get("by_killzone", {}).items():
        print(f"- {display_killzone(name)}: PF {metrics.get('profit_factor', 0.0)}, Win Rate {metrics.get('win_rate', 0.0)}%, Trades {metrics.get('trades_approved', 0)}, Net RR {metrics.get('net_rr', 0.0)}")
    print("By Narrative:")
    for name, metrics in summary.get("by_narrative", {}).items():
        print(f"- {name.replace('_', ' ').title()}: PF {metrics.get('profit_factor', 0.0)}, Win Rate {metrics.get('win_rate', 0.0)}%, Trades {metrics.get('trades_approved', 0)}, Net RR {metrics.get('net_rr', 0.0)}")


def print_diagnostics(results: dict) -> None:
    """Print diagnostic analytics for the backtest."""
    diagnostics = results["diagnostics"]
    loss_cluster = diagnostics["losing_trade_analysis"]["most_common"]
    worst_symbol = diagnostics["worst_symbol"]
    best_symbol = diagnostics["best_symbol"]
    worst_killzone = diagnostics["worst_killzone"]
    best_killzone = diagnostics["best_killzone"]
    worst_phase = diagnostics["worst_narrative_phase"]

    print("BACKTEST DIAGNOSTICS")
    print("--------------------")
    print("Worst Symbol:")
    print(format_bucket(worst_symbol["name"], worst_symbol["metrics"]))
    print("")
    print("Best Symbol:")
    print(format_bucket(best_symbol["name"], best_symbol["metrics"]))
    print("")
    print("Worst Killzone:")
    print(format_bucket(worst_killzone["name"], worst_killzone["metrics"], use_killzone_display=True))
    print("")
    print("Best Killzone:")
    print(format_bucket(best_killzone["name"], best_killzone["metrics"], use_killzone_display=True))
    print("")
    print("Worst Narrative Phase:")
    print(format_bucket(worst_phase["name"], worst_phase["metrics"]))
    print("")
    print("Loss Cluster:")
    print("Most losses happened during:")
    print(f"- {display_killzone(loss_cluster.get('killzone', 'none'))}")
    print(f"- {loss_cluster.get('smt', 'No SMT')}")
    print(f"- {str(loss_cluster.get('narrative_phase', 'none')).replace('_', ' ').title()} phase")
    print(f"- Confidence {loss_cluster.get('confidence_bucket', 'unknown')}")
    print(f"- {loss_cluster.get('symbol', 'none')}")
    print("")
    print("Engine Score Contribution:")
    print(f"Winning trades avg: {diagnostics['engine_score_contribution']['winning_trades_average']}")
    print(f"Losing trades avg:  {diagnostics['engine_score_contribution']['losing_trades_average']}")
    print("")
    print("RECOMMENDATIONS")
    print("---------------")
    for recommendation in diagnostics["recommendations"]:
        print(f"- {recommendation}")


def print_guardrail_impact(results: dict) -> None:
    """Print before/after guardrail backtest impact."""
    impact = results.get("guardrail_impact", {})
    before = impact.get("before", {})
    after = impact.get("after", {})

    print("GUARDRAIL IMPACT")
    print("----------------")
    print(f"Before Trades: {before.get('trades', 0)}")
    print(f"Before Winrate: {before.get('winrate', 0.0)}%")
    print(f"Before PF: {before.get('profit_factor', 0.0)}")
    print(f"After Trades: {after.get('trades', 0)}")
    print(f"After Winrate: {after.get('winrate', 0.0)}%")
    print(f"After PF: {after.get('profit_factor', 0.0)}")
    print(f"Trades removed: {impact.get('trades_removed', 0)}")
    print(f"Worst filtered condition: {impact.get('worst_filtered_condition', 'none')}")


def format_bucket(name: str, metrics: dict, *, use_killzone_display: bool = False) -> str:
    """Return bucket summary."""
    if not metrics:
        return "none"
    display_name = display_killzone(name) if use_killzone_display and name != "none" else name
    return f"{display_name} ({metrics.get('win_rate', 0.0)}%, {metrics.get('trades_approved', 0)} trades, avg RR {metrics.get('average_rr', 0.0)})"


def display_killzone(name: str) -> str:
    """Return a clean killzone name for print output."""
    if name == "none":
        return "None"
    return KillzoneAnalyzer.display_name(name)


if __name__ == "__main__":
    raise SystemExit(main())
