"""STAGE O2.47: Replay Candidate Export

Export candidate-level data from 365D production backtest for real O2.46R validation.

This is export/logging only - zero decision changes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from datetime import UTC, datetime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.backtesting.backtest_engine import BacktestEngine, BacktestEngineError
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.symbols.symbol_registry import SymbolRegistry


DAYS = 365
PRODUCTION_SYMBOLS = ["US30", "XAUUSD"]
OBSERVER_DIAGNOSTIC_SYMBOLS = ["BTCUSD", "NAS100", "EURUSD", "GBPUSD"]

# Known production baseline for reconciliation
KNOWN_BASELINE = {
    "total_trades": 56,
    "us30_trades": 47,
    "xauusd_trades": 9,
    "profit_factor": 1.58,
    "win_rate": 58.7,
    "max_drawdown": 2.97,
    "setups_scanned": 4802,
}


def extract_trade_export_data(trade: dict[str, Any]) -> dict[str, Any]:
    """Extract required fields from trade dict for export."""
    plan = trade.get("plan", {})
    simulation = trade.get("simulation", {})
    guardrail = trade.get("adaptive_guardrail", trade.get("guardrail", {}))

    return {
        "symbol": trade.get("symbol"),
        "timestamp": trade.get("timestamp"),
        "direction": trade.get("direction"),
        "confidence": trade.get("confidence"),
        "guardrail_mode": "adaptive",
        "guardrail_status": guardrail.get("status"),
        "guardrail_reasons": guardrail.get("reasons", []),
        "guardrail_penalty": guardrail.get("guardrail_penalty_total", 0),
        "entry": plan.get("entry"),
        "stop_loss": plan.get("stop_loss"),
        "take_profit_1": plan.get("tp1"),
        "take_profit_2": plan.get("tp2"),
        "take_profit_3": plan.get("tp3"),
        "result": simulation.get("result"),
        "rr_realized": simulation.get("rr_realized"),
        "pnl": simulation.get("net_rr"),
        "max_favorable_excursion": simulation.get("max_favorable_excursion"),
        "max_adverse_excursion": simulation.get("max_adverse_excursion"),
        "included_in_metrics": True,  # Selected trades are included
        "production_symbol": trade.get("symbol") in PRODUCTION_SYMBOLS,
        "observer_symbol": trade.get("symbol") not in PRODUCTION_SYMBOLS,
    }


def run_candidate_export() -> dict[str, Any]:
    """Run 365D backtest and export all candidate data."""
    print("\n" + "="*70)
    print("STAGE O2.47: REPLAY CANDIDATE EXPORT")
    print("="*70)
    print("\nExporting candidate-level data from 365D production replay...")
    print("This is export/logging only - zero decision changes.\n")

    connector = MT5Connector()
    try:
        connector.connect()
        engine = BacktestEngine(connector=connector)
        registry = SymbolRegistry()

        # Get production symbols
        production_symbols = list(registry.execution_symbols() or PRODUCTION_SYMBOLS)

        # Run backtest for production symbols only
        print(f"Scanning symbols: {production_symbols}")
        print(f"Period: 365 days\n")

        all_trades: list[dict[str, Any]] = []
        setups_scanned_total = 0

        for symbol in production_symbols:
            try:
                print(f"Processing {symbol}...")
                candles = engine.fetch_backtest_candles(symbol, DAYS)
                symbol_trades, symbol_setups = engine.scan_symbol(symbol, candles)
                all_trades.extend(symbol_trades)
                setups_scanned_total += symbol_setups
                print(f"  - Scanned: {symbol_setups} setups")
                print(f"  - Candidates: {len(symbol_trades)} trades")
            except Exception as exc:
                print(f"  - ERROR: {exc}")
                continue

        # Run through guardrails
        print(f"\nRunning through guardrails...")
        results = BacktestEngine.aggregate_results(
            trades=all_trades,
            setups_scanned=setups_scanned_total,
            starting_balance=float(engine.config["starting_balance"]),
            risk_per_trade_percent=float(engine.config["risk_per_trade_percent"]),
            symbols=production_symbols,
            strategy_guardrails=engine.strategy_guardrails,
        )

        # Filter to adaptive guardrail mode (production)
        selected_trades = BacktestEngine.filter_trades_by_guardrail_mode(
            results.get("trades", []), mode="adaptive"
        )

        print(f"Total candidates: {len(all_trades)}")
        print(f"Selected trades (adaptive mode): {len(selected_trades)}")

        # Extract export data
        exported_trades = [extract_trade_export_data(trade) for trade in selected_trades]

        # Calculate reconciliation metrics
        us30_trades = [t for t in exported_trades if t["symbol"] == "US30"]
        xauusd_trades = [t for t in exported_trades if t["symbol"] == "XAUUSD"]
        winners = [t for t in exported_trades if t["result"] == "WIN"]
        losers = [t for t in exported_trades if t["result"] == "LOSS"]

        win_rate = (len(winners) / len(exported_trades) * 100) if exported_trades else 0

        # Reconciliation report
        print(f"\n" + "="*70)
        print("RECONCILIATION WITH KNOWN BASELINE")
        print("="*70)
        print(f"\nBaseline expectations:")
        print(f"  Total trades: {KNOWN_BASELINE['total_trades']}")
        print(f"  US30 trades: {KNOWN_BASELINE['us30_trades']}")
        print(f"  XAUUSD trades: {KNOWN_BASELINE['xauusd_trades']}")
        print(f"  PF: {KNOWN_BASELINE['profit_factor']}")
        print(f"  WR: {KNOWN_BASELINE['win_rate']}%")

        print(f"\nActual export:")
        print(f"  Total trades: {len(exported_trades)}")
        print(f"  US30 trades: {len(us30_trades)}")
        print(f"  XAUUSD trades: {len(xauusd_trades)}")
        print(f"  Winners: {len(winners)}")
        print(f"  Losers: {len(losers)}")
        print(f"  WR: {win_rate:.1f}%")

        # Verify reconciliation
        reconciliation_ok = (
            len(exported_trades) == KNOWN_BASELINE["total_trades"] and
            len(us30_trades) == KNOWN_BASELINE["us30_trades"] and
            len(xauusd_trades) == KNOWN_BASELINE["xauusd_trades"]
        )

        if reconciliation_ok:
            print(f"\n[OK] RECONCILIATION SUCCESSFUL")
        else:
            print(f"\n[MISMATCH] RECONCILIATION MISMATCH")
            if len(exported_trades) != KNOWN_BASELINE["total_trades"]:
                print(f"  Trade count mismatch: {len(exported_trades)} vs {KNOWN_BASELINE['total_trades']}")
            if len(us30_trades) != KNOWN_BASELINE["us30_trades"]:
                print(f"  US30 count mismatch: {len(us30_trades)} vs {KNOWN_BASELINE['us30_trades']}")
            if len(xauusd_trades) != KNOWN_BASELINE["xauusd_trades"]:
                print(f"  XAUUSD count mismatch: {len(xauusd_trades)} vs {KNOWN_BASELINE['xauusd_trades']}")

        return {
            "status": "success" if reconciliation_ok else "mismatch",
            "exported_trades": exported_trades,
            "reconciliation_ok": reconciliation_ok,
            "baseline": KNOWN_BASELINE,
            "actual": {
                "total_trades": len(exported_trades),
                "us30_trades": len(us30_trades),
                "xauusd_trades": len(xauusd_trades),
                "winners": len(winners),
                "losers": len(losers),
                "win_rate": win_rate,
                "setups_scanned": setups_scanned_total,
            },
            "generated_at": datetime.now(UTC).isoformat(),
        }

    except (MT5ConnectorError, BacktestEngineError, ValueError) as exc:
        print(f"\n[ERROR] EXPORT FAILED: {exc}")
        return {
            "status": "error",
            "error": str(exc),
            "generated_at": datetime.now(UTC).isoformat(),
        }
    finally:
        connector.shutdown()


def save_export(result: dict[str, Any], export_path: Path) -> None:
    """Save export to JSON file."""
    export_path.parent.mkdir(parents=True, exist_ok=True)

    # Create export-safe version (remove nested objects that might not be serializable)
    export_data = {
        "generated_at": result.get("generated_at"),
        "status": result.get("status"),
        "reconciliation_ok": result.get("reconciliation_ok"),
        "baseline": result.get("baseline"),
        "actual": result.get("actual"),
        "trades": result.get("exported_trades", []),
    }

    with open(export_path, 'w') as f:
        json.dump(export_data, f, indent=2)

    print(f"\n[OK] Export saved to {export_path}")


if __name__ == "__main__":
    result = run_candidate_export()

    if result.get("status") in ("success", "mismatch"):
        export_path = PROJECT_ROOT / "data" / "reports" / "backtest_365d_trade_export.json"
        save_export(result, export_path)

        if result.get("reconciliation_ok"):
            print(f"\n[OK] Candidate export ready for O2.46R real shadow replay validation")
            sys.exit(0)
        else:
            print(f"\n[WARNING] Export reconciliation mismatch - see details above")
            print(f"[INFO] Export saved anyway for analysis - check data/reports/backtest_365d_trade_export.json")
            sys.exit(0)  # Exit 0 even with mismatch - let user investigate
    else:
        print(f"\n[ERROR] Export failed - check error above")
        sys.exit(1)
