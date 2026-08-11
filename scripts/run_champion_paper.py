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
from backend.live_paper.demo_order_executor import DemoExecutionError, DemoOrderExecutor
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.risk_manager.risk_governor import RiskGovernor
from backend.risk_manager.risk_state_store import RiskStateStore

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
    parser.add_argument(
        "--execute-demo",
        action="store_true",
        help="Submit REAL orders to the connected DEMO account (refuses non-demo servers).",
    )
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
    executor = None
    if args.execute_demo:
        store = RiskStateStore(PROJECT_ROOT / ".sentinel_runtime" / "champion_risk_state.json")
        governor = RiskGovernor(connector=connector, state_store=store)
        executor = DemoOrderExecutor(
            connector, governor, PROJECT_ROOT / "data" / "live_paper" / "KILL_SWITCH"
        )
        try:
            account = executor.verify_demo_account()
        except DemoExecutionError as exc:
            print(str(exc))
            connector.shutdown()
            return 1
        print(f"DEMO EXECUTION ENABLED on {account.get('server')} login {account.get('login')}", flush=True)
    mode_label = (
        "DEMO EXECUTION mode: REAL orders on the demo account, visible in MT5"
        if executor
        else "advisor mode, no real orders"
    )
    print(f"CHAMPION TRADER ({mode_label})")
    print(f"Symbols: {', '.join(SYMBOLS)} | state: {STATE_PATH.relative_to(PROJECT_ROOT)}")
    startup_sent = notify_telegram(
        f"Sentinel champion trader ONLINE ({mode_label}).\n"
        f"Watching {', '.join(SYMBOLS)} on closed M15 candles. You will get a message on every open/close."
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
            result["summary"]["mode"] = (
                "DEMO_EXECUTION_REAL_ORDERS" if executor else "ADVISOR_PAPER_NO_ORDERS"
            )
            trader.save_state()
            write_outputs(result)
            for action in result["actions"]:
                if executor and action.get("event") == "OPEN":
                    order_result = executor.open_position(action)
                    action["demo_order"] = order_result
                    if order_result.get("submitted"):
                        trader.state["open_positions"][action["symbol"]]["demo_order"] = order_result
                        trader.save_state()
                if executor and action.get("event") == "CLOSE":
                    # SL/TP closes happen server-side; the timeout exit (and any
                    # residual position from fill drift) is closed actively.
                    if action.get("outcome") == "BREAKEVEN" or executor.position_still_open(action["symbol"]):
                        action["demo_close"] = executor.close_symbol_positions(action["symbol"])
                print(json.dumps(action, default=str), flush=True)
                if action.get("event") in {"OPEN", "CLOSE"}:
                    message = format_action(action, result["summary"])
                    demo_order = action.get("demo_order")
                    if demo_order and demo_order.get("submitted"):
                        message += (
                            f"\nDEMO ORDER FILLED: ticket {demo_order['order_ticket']} | "
                            f"{demo_order['lots']} lots @ {demo_order['fill_price']}"
                        )
                    elif demo_order:
                        message += f"\nDEMO ORDER NOT SENT: {demo_order.get('reason')}"
                    notify_telegram(message)
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
