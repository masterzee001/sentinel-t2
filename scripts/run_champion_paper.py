"""Run the champion-config live paper trader (advisor mode, no orders ever).

Evaluates the promoted configuration on each newly CLOSED M15 candle for
US30+NAS100, journals every open/close/reject, and maintains a rolling parity
summary against the replay expectation (rwPF 1.18). Leave it running in a
terminal; stop with Ctrl+C. State survives restarts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
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


def notify_telegram(text: str) -> bool:
    """Best-effort Telegram push so paper trades are visible on the phone.

    MT5 never shows these trades (advisor mode sends no orders), so Telegram
    plus the JSONL journal are the windows into the paper book.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        return response.status_code == 200
    except requests.RequestException:
        return False


def format_action(action: dict, summary: dict) -> str:
    event = action.get("event")
    if event == "OPEN":
        return (
            f"PAPER OPEN {action['symbol']} {action['direction']} @ {action['entry']}\n"
            f"SL {action['stop_loss']} | TP3 {action['take_profit']} | conf {action['confidence']}"
        )
    if event == "CLOSE":
        icon = {"WIN": "WIN +", "LOSS": "LOSS ", "BREAKEVEN": "FLAT "}.get(action.get("outcome", ""), "")
        return (
            f"PAPER CLOSE {action['symbol']} {icon}{action['rr']}R ({action['outcome']})\n"
            f"Book: {summary['closed_trades']} closed | net {summary['net_rr']}R | rwPF {summary['risk_weighted_pf']} (target 1.18)"
        )
    return ""


def main() -> int:
    load_dotenv()
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
    startup_sent = notify_telegram(
        "Sentinel champion paper trader ONLINE (advisor mode, no real orders).\n"
        f"Watching {', '.join(SYMBOLS)} on closed M15 candles. You will get a message on every paper open/close."
    )
    print(f"telegram_startup_message_sent={startup_sent}", flush=True)

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
                print(json.dumps(action, default=str), flush=True)
                if action.get("event") in {"OPEN", "CLOSE"}:
                    notify_telegram(format_action(action, result["summary"]))
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
