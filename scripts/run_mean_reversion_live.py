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
    # 20 trailing moves ending BEFORE the current bar — exactly the audited
    # backtest's window (audit 2026-08-11 found this was averaging 19).
    moves = [abs(closes[j] - closes[j - 1]) for j in range(len(closes) - VOL_WINDOW - 1, len(closes) - 1)]
    risk_unit = sum(moves) / len(moves) if moves else 0.0
    return ibs, risk_unit


def record(action: dict[str, Any]) -> None:
    ACTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ACTIONS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(action, default=str) + "\n")


def count_order_result(state: dict[str, Any], demo_order: dict[str, Any]) -> None:
    """Track submitted vs refused real orders so silent suppression is visible.

    The paper book stays faithful to the audited strategy even when an order
    is refused; these counters are how paper-vs-account divergence shows up.
    """
    counters = state.setdefault("demo_orders", {"submitted": 0, "refused": {}})
    if demo_order.get("submitted"):
        counters["submitted"] = int(counters.get("submitted", 0)) + 1
    else:
        reason = str(demo_order.get("reason", "unknown"))[:80]
        counters["refused"][reason] = int(counters["refused"].get(reason, 0)) + 1


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
                "demo_orders": state.get("demo_orders", {"submitted": 0, "refused": {}}),
                "replay_expectation": {"risk_weighted_pf": 1.85, "note": "rollover-stressed audited figure"},
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )


def preflight_min_lots(connector: Any, executor: DemoOrderExecutor, equity: float) -> None:
    """Report each symbol's actual startup sizing (and warn on skips).

    Silent signal deletion — a healthy paper book while the account takes no
    trades — was the audit's worst live failure mode; this makes the real
    per-symbol risk visible in Telegram at every engine start. Non-fatal:
    sizes change with equity and volatility.
    """
    lines = []
    skipped = []
    for symbol in SYMBOLS:
        try:
            candles = connector.get_historical_candles(symbol, "D1", count=VOL_WINDOW + 5)
            _, risk_unit = ibs_and_risk_unit(candles)
            if risk_unit <= 0:
                continue
            stop = DISASTER_STOP_UNITS * risk_unit
            lots = executor.lot_size(symbol, stop, equity)
            if lots <= 0:
                skipped.append(symbol)
                lines.append(f"{symbol}: SKIPPED (broker minimum above {executor.min_lot_risk_cap_percent}% risk cap)")
                continue
            info = connector.mt5.symbol_info(executor._broker_symbol(symbol))
            value_per_unit = (info.trade_tick_value / info.trade_tick_size) if info else 0.0
            implied = lots * stop * value_per_unit / equity * 100 if equity and value_per_unit else 0.0
            lines.append(f"{symbol}: {lots} lots (~{implied:.1f}% equity at disaster stop)")
        except Exception as exc:
            print(f"preflight: {symbol} check failed ({exc})", flush=True)
    message = "MEANREV SIZING AT START:\n" + "\n".join(lines)
    if skipped:
        message += "\nWARNING: skipped symbols will not reach the account."
    print(message, flush=True)
    notify_telegram(message)


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
        # Live-book profile: malfunction tripwires only, NOT the prop-firm
        # limits in risk_profile.yaml (those would strangle the audited edge).
        # Trade-cap override: legit max is 3 opens/day (one per symbol), so a
        # re-entry loop bug trips at 6 instead of the champion-sized 15.
        governor = RiskGovernor(
            connector=connector,
            state_store=store,
            risk_profile_file=PROJECT_ROOT / "config" / "live_book_risk.yaml",
            risk_profile_overrides={"daily_limits": {"max_trades_per_day": 6}},
        )
        executor = DemoOrderExecutor(
            connector,
            governor,
            PROJECT_ROOT / "data" / "live_paper" / "KILL_SWITCH",
            risk_percent=3.0,  # Disaster stop is 3 units -> 1.0% per risk unit
            # (user mandate 2026-08-11: raised from 1.5 so signals size properly).
            magic=MAGIC,
            comment="sentinel-meanrev",
            # Broker minimum on US30/USTEC implies ~3.4-3.8% at current equity;
            # take the trade at minimum rather than skip, refuse only past 6%.
            min_lot_risk_cap_percent=6.0,
            # AUDITED NO-STOP BOOK (user-directed 2026-08-11): sizing stays on
            # the 3-unit distance (1%/risk-unit) but no server-side SL is sent.
            # 3y forward test: stop halves return (+155% vs +282%) by stopping
            # out 22% of trades that mostly recover. Exits are IBS>0.8 / 10d
            # only; the supervisor watchdog is the process-death safety net.
            server_stop_loss=False,
        )
        try:
            account = executor.verify_demo_account()
        except DemoExecutionError as exc:
            print(str(exc))
            connector.shutdown()
            return 1
        print(f"MEANREV DEMO EXECUTION ENABLED on {account.get('server')}", flush=True)
        preflight_min_lots(connector, executor, float(account.get("equity", 0.0)))
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
            if executor:
                # Feed the daily-loss/drawdown tripwires all day, not only at
                # open attempts: without this the baseline equity is set
                # seconds before the nightly window and the tripwire is blind.
                try:
                    snapshot = connector.get_account_info()
                    store.observe_account(
                        balance=float(snapshot.get("balance", 0.0)),
                        equity=float(snapshot.get("equity", 0.0)),
                    )
                except Exception as exc:
                    print(f"account observation failed ({exc})", flush=True)
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
                            count_order_result(state, action["demo_order"])
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
                            f"no server SL (audited no-stop book) | exit IBS>{IBS_EXIT} or {MAX_HOLD_DAYS}d{suffix}"
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
