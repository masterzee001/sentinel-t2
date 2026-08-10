"""SMT feature analysis on the champion book (US30<->NAS100 divergence).

The SMT feature is annotation-only during the scan; this script measures
whether it predicts outcomes and judges an SMT-contradiction filter by the
standard promotion rule (net R AND risk-weighted PF must both improve).
The filter has no fitted parameters, so the whole phase-robust book is
out-of-sample for it.
"""

from __future__ import annotations

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
from scripts.run_backtest_365d import trim_to_lookback_days
from scripts.run_sizing_walkforward import book_metrics, sizing_verdict

DAYS = 1095
PAIR = {"US30": "NAS100", "NAS100": "US30"}
REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "smt_feature_report.json"


def main() -> int:
    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
        engine = BacktestEngine(connector=connector)
        frames = {
            symbol: trim_to_lookback_days(engine.fetch_backtest_candles(symbol, DAYS), DAYS)
            for symbol in PAIR
        }
        engine.smt_reference_frames = {symbol: frames[reference] for symbol, reference in PAIR.items()}
        trades: list[dict[str, Any]] = []
        for symbol in PAIR:
            symbol_trades, _ = engine.scan_symbol(symbol, frames[symbol])
            trades.extend(symbol_trades)
        selected = BacktestEngine.filter_trades_by_guardrail_mode(
            BacktestEngine.enrich_trade_pnl(trades, 25.0), mode="adaptive"
        )
        report = analyze(selected)
        report["generated_at"] = datetime.now(UTC).isoformat()
        REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        print_report(report)
        return 0
    except (MT5ConnectorError, BacktestEngineError, ValueError) as exc:
        print(f"SMT feature analysis failed: {exc}")
        return 1
    finally:
        connector.shutdown()


def analyze(trades: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[float]] = {"smt_aligned": [], "no_divergence": [], "unavailable": []}
    for trade in trades:
        rr = float(trade.get("rr", 0.0))
        feature = trade.get("smt_feature") or {}
        if not feature.get("available"):
            buckets["unavailable"].append(rr)
        elif feature.get("detected"):
            buckets["smt_aligned"].append(rr)
        else:
            buckets["no_divergence"].append(rr)
    full = [float(trade.get("rr", 0.0)) for trade in trades]
    # Filter under judgment: only take SMT-aligned trades (plus unavailable
    # kept, since live would not drop trades for missing reference data).
    filtered = buckets["smt_aligned"] + buckets["unavailable"]
    baseline_metrics = book_metrics(full)
    filtered_metrics = book_metrics(filtered)
    return {
        "trades_total": len(full),
        "buckets": {
            name: {"count": len(values), **book_metrics(values)} for name, values in buckets.items()
        },
        "baseline": baseline_metrics,
        "smt_aligned_only_filter": filtered_metrics,
        "verdict": sizing_verdict(baseline_metrics, filtered_metrics),
    }


def print_report(report: dict[str, Any]) -> None:
    print("SMT FEATURE ANALYSIS (US30<->NAS100, champion book, out-of-sample)")
    print("------------------------------------------------------------------")
    print(f"Trades: {report['trades_total']}")
    for name, row in report["buckets"].items():
        print(
            f"{name}: {row['count']} trades | net {row['net_rr']}R | rwPF {row['risk_weighted_pf']}"
        )
    print("")
    print(f"BASELINE (all trades): net {report['baseline']['net_rr']}R | rwPF {report['baseline']['risk_weighted_pf']}")
    print(
        f"SMT-ALIGNED FILTER:    net {report['smt_aligned_only_filter']['net_rr']}R | "
        f"rwPF {report['smt_aligned_only_filter']['risk_weighted_pf']}"
    )
    verdict = report["verdict"]
    print(f"VERDICT: promote={verdict['promote']} (net: {verdict['net_rr_improved']}, PF: {verdict['risk_weighted_pf_improved']})")
    print(f"Report: {REPORT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
