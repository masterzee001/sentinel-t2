"""Export the champion's 3-year adaptive trade book for the combined forward test.

Runs the CURRENT phase-robust engine (step_candles=1, one position per
symbol, costs on, live-parity brain) over ~3 years of M15 candles for
US30+NAS100 and writes one record per adaptive-book trade:
timestamp, symbol, direction, stop distance in points, cost-inclusive rr.

This replaces the stale O2.47 exporter, whose result fields never populate
under the post-truth-reset engine.
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

DAYS = 1095
SYMBOLS = ("US30", "NAS100")
EXPORT_PATH = PROJECT_ROOT / "data" / "reports" / "champion_trades_3y.json"


def main() -> int:
    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
    except MT5ConnectorError as exc:
        print(f"MT5 connection failed: {exc}")
        return 1
    try:
        engine = BacktestEngine(connector=connector)
        engine.reset_candidate_ledgers()
        all_trades: list[dict[str, Any]] = []
        spans: dict[str, dict[str, str]] = {}
        for symbol in SYMBOLS:
            candles = engine.fetch_backtest_candles(symbol, DAYS)
            spans[symbol] = {"first": str(candles["time"].min()), "last": str(candles["time"].max())}
            symbol_trades, _ = engine.scan_symbol(symbol, candles)
            all_trades.extend(symbol_trades)
            print(f"{symbol}: {len(symbol_trades)} candidate trades | span {spans[symbol]}", flush=True)
        adaptive = BacktestEngine.filter_trades_by_guardrail_mode(all_trades, mode="adaptive")
        records = []
        for trade in adaptive:
            simulation = trade.get("simulation", {}) or {}
            plan = trade.get("plan", {}) or {}
            stop = plan.get("stop_loss", {}) or {}
            if simulation.get("rr") is None:
                continue
            records.append(
                {
                    "symbol": trade.get("symbol"),
                    "timestamp": str(trade.get("timestamp")),
                    "direction": trade.get("direction"),
                    "stop_distance": float(stop.get("distance", 0.0) or 0.0),
                    "rr": float(simulation.get("rr", 0.0)),
                    "outcome": simulation.get("outcome"),
                }
            )
        EXPORT_PATH.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(UTC).isoformat(),
                    "days": DAYS,
                    "spans": spans,
                    "guardrail_mode": "adaptive",
                    "trades": records,
                },
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        rrs = [r["rr"] for r in records]
        print(
            f"EXPORTED {len(records)} adaptive trades -> {EXPORT_PATH.name} | "
            f"net {sum(rrs):.2f}R | winners {sum(1 for r in rrs if r > 0)}",
            flush=True,
        )
        return 0
    except (BacktestEngineError, ValueError) as exc:
        print(f"Export failed: {exc}")
        return 1
    finally:
        connector.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
