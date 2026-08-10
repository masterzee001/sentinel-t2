"""Tier 2 symbol expansion: measure the champion logic on new index symbols.

Candidates are scanned observer-style (guardrail tier blocks execution, so we
measure the raw structural+confidence book) over 3 years with costs, then
compared against the registry promotion rule (PF>=1.5, WR>=55, trades>=30,
DD<=4). Promotion itself stays a config change reviewed against this report.
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

DAYS = 1095
# Distinct instruments only: USTEC/DOW aliases of promoted symbols are skipped.
NAME_PATTERNS = ("US500", "SPX", "DE40", "GER40", "DAX", "US2000", "RUT", "JP225", "NIK", "UK100", "FTSE", "EU50", "STOXX")
DEFAULT_CANDIDATES = ["US500", "EDOW", "US2000"]
REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "symbol_expansion_scan.json"
PROMOTION_RULE = {"min_pf": 1.5, "min_wr": 55.0, "min_trades": 30, "max_dd": 4.0}
# Approximate round-trip costs in price units for new candidates (spread+slip).
CANDIDATE_COSTS = {
    "US500": {"spread": 0.6, "slippage": 0.25},
    "SPXC": {"spread": 0.6, "slippage": 0.25},
    "EDOW": {"spread": 3.5, "slippage": 1.5},
    "US2000": {"spread": 0.5, "slippage": 0.2},
    "DE40": {"spread": 2.5, "slippage": 1.0},
    "GER40": {"spread": 2.5, "slippage": 1.0},
    "JP225": {"spread": 12.0, "slippage": 5.0},
    "UK100": {"spread": 2.0, "slippage": 0.8},
}


def main() -> int:
    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
        available = discover(connector)
        print(f"Discovered candidates: {available}")
        connector.supported_symbols = frozenset(set(connector.supported_symbols) | set(available))
        engine = BacktestEngine(connector=connector)
        engine.simulator.symbol_costs.update(
            {symbol: dict(model) for symbol, model in CANDIDATE_COSTS.items()}
        )
        allowed = set(engine.decision_brain.confidence_analyzer.allowed_symbols) | set(available)
        engine.decision_brain.confidence_analyzer.allowed_symbols = frozenset(allowed)
        engine.killzone_analyzer.DEFAULT_ALLOWED_SYMBOLS = frozenset(
            set(engine.killzone_analyzer.DEFAULT_ALLOWED_SYMBOLS) | set(available)
        )

        class KillzoneProxy:
            """Map new candidates onto existing per-symbol killzone windows."""

            def __init__(self, inner):
                self.inner = inner
                self.mapping = {name: ("EURUSD" if name in {"DE40", "GER40", "UK100"} else "US30") for name in available}

            def analyze(self, symbol, current_time=None):
                return self.inner.analyze(self.mapping.get(symbol, symbol), current_time=current_time)

        engine.killzone_analyzer = KillzoneProxy(engine.killzone_analyzer)
        results: dict[str, Any] = {}
        for symbol in available:
            try:
                candles = trim_to_lookback_days(engine.fetch_backtest_candles(symbol, DAYS), DAYS)
                trades, setups = engine.scan_symbol(symbol, candles)
                enriched = BacktestEngine.enrich_trade_pnl(trades, 25.0)
                metrics = BacktestEngine.calculate_metrics(enriched, setups, 5000.0)
                results[symbol] = {
                    "metrics": metrics,
                    "candles": int(len(candles)),
                    "first": str(candles["time"].min()) if len(candles) else None,
                    "meets_promotion_rule": meets_rule(metrics),
                }
                print(format_row(symbol, results[symbol]))
            except Exception as exc:
                results[symbol] = {"error": str(exc)}
                print(f"{symbol}: ERROR {exc}")
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "days": DAYS,
            "promotion_rule": PROMOTION_RULE,
            "results": results,
        }
        REPORT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        print(f"Report: {REPORT_PATH.relative_to(PROJECT_ROOT)}")
        return 0
    except (MT5ConnectorError, BacktestEngineError, ValueError) as exc:
        print(f"Symbol expansion scan failed: {exc}")
        return 1
    finally:
        connector.shutdown()


def discover(connector: MT5Connector) -> list[str]:
    """Return distinct available candidate symbols matching index patterns."""
    try:
        names = {str(item.name).upper() for item in (connector.mt5.symbols_get() or [])}
    except Exception:
        names = set()
    found = [name for name in DEFAULT_CANDIDATES if name in names]
    for pattern in NAME_PATTERNS:
        for name in sorted(names):
            if name.startswith(pattern) and name not in found and len(name) <= 7:
                found.append(name)
                break
    return found[:6]


def meets_rule(metrics: dict[str, Any]) -> bool:
    return (
        float(metrics.get("profit_factor", 0.0)) >= PROMOTION_RULE["min_pf"]
        and float(metrics.get("win_rate", 0.0)) >= PROMOTION_RULE["min_wr"]
        and int(metrics.get("trades_approved", 0)) >= PROMOTION_RULE["min_trades"]
        and float(metrics.get("max_drawdown", 0.0)) <= PROMOTION_RULE["max_dd"]
    )


def format_row(symbol: str, row: dict[str, Any]) -> str:
    metrics = row["metrics"]
    return (
        f"{symbol}: PF {metrics.get('profit_factor', 0.0)} | WR {metrics.get('win_rate', 0.0)}% | "
        f"trades {metrics.get('trades_approved', 0)} | net {metrics.get('net_rr', 0.0)}R | "
        f"DD {metrics.get('max_drawdown', 0.0)}% | promotes={row['meets_promotion_rule']}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
