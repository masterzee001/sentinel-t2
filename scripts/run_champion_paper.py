"""Run the champion-config live paper trader (advisor mode, no orders ever).

Evaluates the promoted configuration on each newly CLOSED M15 candle for
US30+NAS100, journals every open/close/reject, and maintains a rolling parity
summary against the replay expectation (rwPF 1.18). Leave it running in a
terminal; stop with Ctrl+C. State survives restarts.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.backtesting.backtest_engine import BacktestEngine
from backend.live_paper.champion_paper_trader import SYMBOLS, ChampionPaperTrader
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError

STATE_PATH = PROJECT_ROOT / "data" / "live_paper" / "champion_paper_state.json"
ACTIONS_PATH = PROJECT_ROOT / "data" / "live_paper" / "champion_paper_actions.jsonl"
STATUS_PATH = PROJECT_ROOT / "data" / "reports" / "champion_paper_status.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=0, help="0 = run until interrupted.")
    parser.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args()

    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
    except MT5ConnectorError as exc:
        print(f"Champion paper trader failed to start: {exc}")
        return 1
    engine = BacktestEngine(connector=connector)
    trader = ChampionPaperTrader(engine, STATE_PATH)
    print("CHAMPION PAPER TRADER (advisor mode, no orders)")
    print(f"Symbols: {', '.join(SYMBOLS)} | state: {STATE_PATH.relative_to(PROJECT_ROOT)}")

    completed = 0
    try:
        while True:
            candles_by_symbol = {}
            for symbol in SYMBOLS:
                try:
                    candles = connector.get_historical_candles(symbol, "M15", count=150)
                    # The final row is the still-forming bar; act on closed bars only.
                    candles_by_symbol[symbol] = candles.iloc[:-1].reset_index(drop=True)
                except Exception as exc:
                    print(f"{symbol}: candle fetch failed ({exc})")
            result = trader.process_cycle(candles_by_symbol)
            trader.save_state()
            write_outputs(result)
            for action in result["actions"]:
                print(json.dumps(action, default=str))
            completed += 1
            if args.cycles and completed >= args.cycles:
                summary = result["summary"]
                print(
                    f"cycles={completed} closed={summary['closed_trades']} net={summary['net_rr']}R "
                    f"rwPF={summary['risk_weighted_pf']} open={len(summary['open_positions'])}"
                )
                return 0
            time.sleep(max(args.interval_seconds, 5))
    except KeyboardInterrupt:
        print("Stopped by user; state saved.")
        return 0
    finally:
        connector.shutdown()


def write_outputs(result: dict) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(result["summary"], indent=2, default=str) + "\n", encoding="utf-8")
    if result["actions"]:
        ACTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with ACTIONS_PATH.open("a", encoding="utf-8") as handle:
            for action in result["actions"]:
                handle.write(json.dumps(action, default=str) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
