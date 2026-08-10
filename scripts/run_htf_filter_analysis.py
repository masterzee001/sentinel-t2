"""HTF bias filter analysis on the champion book (Tier 1 experiment #1).

Three parameter-free filters judged in one pass: take only trades aligned with
the D1 drift, the H4 drift, or both. Annotation is causal; admission untouched;
the whole phase-robust book is out-of-sample for a parameter-free filter.
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
SYMBOLS = ["US30", "NAS100"]
REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "htf_filter_report.json"


def main() -> int:
    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
        engine = BacktestEngine(connector=connector)
        trades: list[dict[str, Any]] = []
        for symbol in SYMBOLS:
            candles = trim_to_lookback_days(engine.fetch_backtest_candles(symbol, DAYS), DAYS)
            symbol_trades, _ = engine.scan_symbol(symbol, candles)
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
        print(f"HTF filter analysis failed: {exc}")
        return 1
    finally:
        connector.shutdown()


def analyze(trades: list[dict[str, Any]]) -> dict[str, Any]:
    full = [float(trade.get("rr", 0.0)) for trade in trades]
    baseline = book_metrics(full)
    filters: dict[str, dict[str, Any]] = {}
    for name in ("aligned_d1", "aligned_h4", "aligned_both"):
        kept = [
            float(trade.get("rr", 0.0))
            for trade in trades
            if not (trade.get("htf_feature") or {}).get("available")
            or (trade.get("htf_feature") or {}).get(name)
        ]
        metrics = book_metrics(kept)
        filters[name] = {
            "kept_trades": len(kept),
            "dropped_trades": len(full) - len(kept),
            **metrics,
            "verdict": sizing_verdict(baseline, metrics),
        }
    counter_trend = [
        float(trade.get("rr", 0.0))
        for trade in trades
        if (trade.get("htf_feature") or {}).get("available")
        and not (trade.get("htf_feature") or {}).get("aligned_d1")
    ]
    return {
        "trades_total": len(full),
        "baseline": baseline,
        "filters": filters,
        "counter_trend_d1_bucket": {"count": len(counter_trend), **book_metrics(counter_trend)},
    }


def print_report(report: dict[str, Any]) -> None:
    print("HTF BIAS FILTER ANALYSIS (champion book, out-of-sample)")
    print("-------------------------------------------------------")
    baseline = report["baseline"]
    print(f"BASELINE: {report['trades_total']} trades | net {baseline['net_rr']}R | rwPF {baseline['risk_weighted_pf']}")
    counter = report["counter_trend_d1_bucket"]
    print(f"Counter-D1-trend bucket: {counter['count']} trades | net {counter['net_rr']}R | rwPF {counter['risk_weighted_pf']}")
    print("")
    for name, row in report["filters"].items():
        verdict = row["verdict"]
        print(
            f"{name}: kept {row['kept_trades']} (dropped {row['dropped_trades']}) | "
            f"net {row['net_rr']}R | rwPF {row['risk_weighted_pf']} | promote={verdict['promote']}"
        )
    print(f"Report: {REPORT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
