"""Live IBS mean-reversion engine (promoted e4cb1cf) — demo execution.

Evaluates once per day per symbol in the 30 minutes BEFORE the daily rollover
(server 23:30-23:59 UTC+3 = ~4:30pm ET), when the daily bar is effectively
complete but spreads are still normal — the window the rollover-stress test
was protecting against. Long-only: enter IBS<0.2, exit IBS>0.8 or 10 trading
days, one position per symbol, disaster stop at 3x the trailing risk unit,
0.5% equity risk per risk-unit move. Magic 22078 keeps it fully separate from
the champion engine (22077). Demo gate, risk governor, and kill switch as in
the champion executor.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.live_paper.demo_order_executor import DemoExecutionError, DemoOrderExecutor
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.risk_manager.risk_governor import RiskGovernor
from backend.risk_manager.risk_state_store import RiskStateStore
from scripts.run_champion_paper import notify_telegram

SYMBOLS = ("US30", "NAS100", "US500")
IBS_ENTRY = 0.2
IBS_EXIT = 0.8
MAX_HOLD_DAYS = 10
VOL_WINDOW = 20
DISASTER_STOP_UNITS = 3.0
MAGIC = 22078
STATE_PATH = PROJECT_ROOT / "data" / "live_paper" / "meanrev_state.json"
ACTIONS_PATH = PROJECT_ROOT / "data" / "live_paper" / "meanrev_actions.jsonl"
STATUS_PATH = PROJECT_ROOT / "data" / "reports" / "meanrev_live_status.json"


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"open_positions": {}, "closed_trades": [], "last_processed": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, indent=2, default=str) + "\n", encoding="utf-8")
    temp.replace(STATE_PATH)


def ibs_and_risk_unit(candles: Any) -> tuple[float, float]:
    """IBS of the (nearly complete) current daily bar + trailing risk unit."""
    bar = candles.iloc[-1]
    bar_range = float(bar["high"]) - float(bar["low"])
    ibs = (float(bar["close"]) - float(bar["low"])) / bar_range if bar_range > 0 else 0.5
    closes = [float(v) for v in candles["close"]]
    moves = [abs(closes[j] - closes[j - 1]) for j in range(len(closes) - VOL_WINDOW, len(closes) - 1)]
    risk_unit = sum(moves) / len(moves) if moves else 0.0
    return ibs, risk_unit


def record(action: dict[str, Any]) -> None:
    ACTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ACTIONS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(action, default=str) + "\n")


def write_status(state: dict[str, Any]) -> None:
    rrs = [float(t.get("rr", 0.0)) for t in state["closed_trades"]]
    gross_win = sum(rr for rr in rrs if rr > 0)
    gross_loss = abs(sum(rr for rr in rrs if rr < 0))
    STATUS_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "mode": "DEMO_EXECUTION_REAL_ORDERS",
                "engine": "ibs_mean_reversion",
                "open_positions": state["open_positions"],
                "closed_trades": len(rrs),
                "net_rr": round(sum(rrs), 2),
                "risk_weighted_pf": round(gross_win / gross_loss, 2) if gross_loss else round(gross_win, 2),
                "replay_expectation": {"risk_weighted_pf": 1.85, "note": "rollover-stressed audited figure"},
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-demo", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--force-window", action="store_true", help="Testing: treat now as inside the trade window.")
    parser.add_argument("--cycles", type=int, default=0)
    args = parser.parse_args()

    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
    except MT5ConnectorError as exc:
        print(f"Mean reversion live failed to start: {exc}")
        return 1
    connector.supported_symbols = frozenset(set(connector.supported_symbols) | set(SYMBOLS))
    executor = None
    if args.execute_demo:
        store = RiskStateStore(PROJECT_ROOT / ".sentinel_runtime" / "meanrev_risk_state.json")
        governor = RiskGovernor(connector=connector, state_store=store)
        executor = DemoOrderExecutor(
            connector,
            governor,
            PROJECT_ROOT / "data" / "live_paper" / "KILL_SWITCH",
            risk_percent=1.5,  # Disaster stop is 3 units -> 0.5% per risk unit.
            magic=MAGIC,
            comment="sentinel-meanrev",
        )
        try:
            account = executor.verify_demo_account()
        except DemoExecutionError as exc:
            print(str(exc))
            connector.shutdown()
            return 1
        print(f"MEANREV DEMO EXECUTION ENABLED on {account.get('server')}", flush=True)
    state = load_state()
    mode = "DEMO EXECUTION" if executor else "paper only"
    notify_telegram(
        f"Sentinel MEAN-REVERSION engine ONLINE ({mode}, magic {MAGIC}).\n"
        f"IBS<{IBS_ENTRY} buys near the daily close on {', '.join(SYMBOLS)}; exits IBS>{IBS_EXIT} or {MAX_HOLD_DAYS}d."
    )
    print(f"MEAN-REVERSION LIVE ({mode}) | window: server 23:30-23:59 (UTC+3)", flush=True)

    completed = 0
    try:
        while True:
            server_now = datetime.now(UTC)
            server_hm = ((server_now.hour + 3) % 24, server_now.minute)  # Server = UTC+3.
            in_window = args.force_window or (server_hm[0] == 23 and server_hm[1] >= 30)
            server_date = str((server_now.timestamp() + 3 * 3600) // 86400)
            if in_window:
                for symbol in SYMBOLS:
                    if state["last_processed"].get(symbol) == server_date:
                        continue
                    try:
                        candles = connector.get_historical_candles(symbol, "D1", count=VOL_WINDOW + 5)
                    except Exception as exc:
                        print(f"{symbol}: candle fetch failed ({exc})", flush=True)
                        continue
                    state["last_processed"][symbol] = server_date
                    ibs, risk_unit = ibs_and_risk_unit(candles)
                    price = float(candles.iloc[-1]["close"])
                    position = state["open_positions"].get(symbol)
                    if position:
                        position["held_days"] = int(position.get("held_days", 0)) + 1
                        if ibs > IBS_EXIT or position["held_days"] >= MAX_HOLD_DAYS:
                            rr = (price - float(position["entry"])) / float(position["risk_unit"])
                            action = {
                                "event": "CLOSE",
                                "symbol": symbol,
                                "exit_price": price,
                                "rr": round(rr, 3),
                                "held_days": position["held_days"],
                                "reason": "ibs_exit" if ibs > IBS_EXIT else "timeout",
                                "time": server_now.isoformat(),
                            }
                            if executor:
                                action["demo_close"] = executor.close_symbol_positions(symbol)
                            state["closed_trades"].append({**position, **action})
                            del state["open_positions"][symbol]
                            record(action)
                            notify_telegram(
                                f"MEANREV CLOSE {symbol} {round(rr, 2)}R ({action['reason']}) @ {price}"
                            )
                    elif ibs < IBS_ENTRY and risk_unit > 0:
                        position = {
                            "symbol": symbol,
                            "direction": "bullish",
                            "entry": price,
                            "stop_loss": round(price - DISASTER_STOP_UNITS * risk_unit, 5),
                            "take_profit": 0.0,
                            "risk_unit": round(risk_unit, 5),
                            "ibs": round(ibs, 3),
                            "held_days": 0,
                            "opened_at": server_now.isoformat(),
                        }
                        action = {"event": "OPEN", **position}
                        if executor:
                            action["demo_order"] = executor.open_position(position)
                            position["demo_order"] = action["demo_order"]
                        state["open_positions"][symbol] = position
                        record(action)
                        fill = action.get("demo_order", {})
                        suffix = (
                            f"\nDEMO ORDER FILLED: ticket {fill['order_ticket']} | {fill['lots']} lots"
                            if fill.get("submitted")
                            else (f"\nDEMO ORDER NOT SENT: {fill.get('reason')}" if fill else "")
                        )
                        notify_telegram(
                            f"MEANREV OPEN {symbol} long @ {price} (IBS {round(ibs, 2)})\n"
                            f"disaster SL {position['stop_loss']} | exit IBS>{IBS_EXIT} or {MAX_HOLD_DAYS}d{suffix}"
                        )
                save_state(state)
            write_status(state)
            completed += 1
            if args.cycles and completed >= args.cycles:
                print(f"cycles={completed} open={len(state['open_positions'])} closed={len(state['closed_trades'])}")
                return 0
            time.sleep(max(args.interval_seconds, 10))
    except KeyboardInterrupt:
        save_state(state)
        print("Stopped; state saved.")
        return 0
    finally:
        connector.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
