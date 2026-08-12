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
from backend.shared.telegram_notify import notify_telegram

SYMBOLS = ("US30", "NAS100", "US500", "DE40")
# Per-symbol entry windows in SERVER time (UTC+3): (hour, minute_from, minute_to).
# US indices quote to 23:59; DE40 STOPS QUOTING at 22:58 (verified 2026-08-11)
# so it gets its own window just before its close — orders at 23:30 would hit
# a closed market and fill at the gapped 01:02 reopen instead.
ENTRY_WINDOWS = {
    "US30": (23, 30, 59),
    "NAS100": (23, 30, 59),
    "US500": (23, 30, 59),
    "DE40": (22, 30, 55),
}
IBS_ENTRY = 0.2
IBS_EXIT = 0.8
# DEPTH TILT (promoted 2026-08-12). Entry depth predicts trade quality:
# IBS<0.05 earns +0.71R per trade at rwPF 2.25 against +0.29-0.32R for
# shallower dips — monotone in the threshold, consistent across ALL FOUR
# symbols and both halves of history. Risk is REALLOCATED toward that tail
# rather than simply increased: shallow dips are cut to 0.75x, which is why
# this improves return per unit of drawdown (account-level CAGR/maxDD 2.66
# vs 2.38 flat) instead of just adding leverage.
# Honest cost of it: deeper drawdowns (46.7% vs 28.7% on the 3y replay) and
# a worst day of -23% vs -13%. Set DEPTH_TILT to a flat 1.0 to disable.
DEPTH_TILT = {"deep": (0.05, 3.0), "mid": (0.10, 1.5), "shallow": (IBS_ENTRY, 0.75)}


def depth_multiplier(ibs: float) -> float:
    """Risk multiplier for an entry, by how deep the dip is."""
    if ibs < DEPTH_TILT["deep"][0]:
        return DEPTH_TILT["deep"][1]
    if ibs < DEPTH_TILT["mid"][0]:
        return DEPTH_TILT["mid"][1]
    return DEPTH_TILT["shallow"][1]
MAX_HOLD_DAYS = 10
VOL_WINDOW = 20
DISASTER_STOP_UNITS = 3.0
# CONCURRENCY CAP (2026-08-12). The four traded indices correlate 0.68-0.95
# (NAS100/US500 = 0.947, average 0.80), so concurrent positions are close to
# one leveraged bet, not four diversified ones. Measured on 5.4y of history,
# the uncapped depth-tilted book peaks at 36% of equity across four positions
# and its worst day is -43.8% (2025-04-04) — versus the -16% the risk config
# had assumed. Capping at 3 cuts the worst day to -28.7% and drawdown from
# 71% to 54%, for ~19% less compounded return. That is the correct trade
# before real capital: the tail shrinks faster than the return does.
MAX_CONCURRENT_POSITIONS = 3
MAGIC = 22078
STATE_PATH = PROJECT_ROOT / "data" / "live_paper" / "meanrev_state.json"
ACTIONS_PATH = PROJECT_ROOT / "data" / "live_paper" / "meanrev_actions.jsonl"
STATUS_PATH = PROJECT_ROOT / "data" / "reports" / "meanrev_live_status.json"


SERVER_OFFSET_FALLBACK_HOURS = 3.0
OFFSET_CONFIRMATIONS_REQUIRED = 3
# A real DST change is permanent; a stale-tick misreading is not. Requiring
# the new value to persist across a long span defeats the frozen-tick case:
# a frozen tick's implied offset drifts a full hour every hour, so it cannot
# hold the same rounded value for 45 minutes. Count alone was too weak — the
# engine polls every 5 min, so three agreeing reads span only 10 minutes and
# a quiet market produced a false "UTC+3 -> UTC+2" change (2026-08-12).
OFFSET_CONFIRMATION_MINUTES = 45.0


def refresh_server_offset(connector: Any, state: dict[str, Any]) -> float:
    """Return the broker's measured UTC offset, remembering the last good value.

    Hardcoding +3 breaks silently at the late-October EET change: every entry
    window would fire an hour off (audit finding 2026-08-11). A single
    measurement can be fooled by a frozen tick, so a CHANGE must be confirmed
    by several consecutive agreeing readings — a frozen tick's implied offset
    drifts, so it cannot survive that test.
    """
    current = state.get("server_offset_hours")
    measured = connector.server_utc_offset_hours()
    if measured is None:
        return float(current if current is not None else SERVER_OFFSET_FALLBACK_HOURS)
    if current is None:
        state["server_offset_hours"] = measured
        return measured
    if measured == float(current):
        for key in ("pending_offset", "pending_offset_count", "pending_offset_since"):
            state.pop(key, None)
        return measured
    now_iso = datetime.now(UTC).isoformat()
    pending = state.get("pending_offset")
    if pending == measured:
        count = int(state.get("pending_offset_count", 0)) + 1
        first_seen = state.get("pending_offset_since", now_iso)
    else:
        count, first_seen = 1, now_iso
    state["pending_offset"] = measured
    state["pending_offset_count"] = count
    state["pending_offset_since"] = first_seen
    held_minutes = (datetime.now(UTC) - datetime.fromisoformat(str(first_seen))).total_seconds() / 60.0
    if count < OFFSET_CONFIRMATIONS_REQUIRED or held_minutes < OFFSET_CONFIRMATION_MINUTES:
        return float(current)
    notify_telegram(
        f"MEANREV: broker server clock moved UTC+{current} -> UTC+{measured} "
        f"(held {int(held_minutes)} min across {count} reads). Entry windows re-anchored automatically."
    )
    state["server_offset_hours"] = measured
    for key in ("pending_offset", "pending_offset_count", "pending_offset_since"):
        state.pop(key, None)
    return measured


def symbol_in_window(symbol: str, server_hour: int, server_minute: int) -> bool:
    """Return whether the symbol's own entry window is open (server time)."""
    hour, minute_from, minute_to = ENTRY_WINDOWS[symbol]
    return server_hour == hour and minute_from <= server_minute <= minute_to


def candles_are_fresh(candles: Any, server_now_date: Any) -> bool:
    """The last bar must be TODAY's (still-forming) bar — a stale last bar
    means a frozen/broken feed (DE40 audit finding) and garbage IBS."""
    try:
        return pd_timestamp_date(candles.iloc[-1]["time"]) == server_now_date
    except Exception:
        return False


def pd_timestamp_date(value: Any):
    import pandas as pd

    return pd.Timestamp(value).date()


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


def write_status(state: dict[str, Any], health: dict[str, Any] | None = None) -> None:
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
                "mt5_health": health or {},
                "server_offset_hours": state.get("server_offset_hours"),
                "replay_expectation": {
                    "risk_weighted_pf": 1.85,
                    "note": "rollover-stressed audited figure (US book); DE40 audited rwPF 1.30",
                },
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
            # DE40 audit finding: MT5 serves the last tick after a market
            # closes — refuse anything quoted >2 minutes ago.
            max_tick_age_seconds=120.0,
        )
        try:
            account = executor.verify_demo_account()
        except DemoExecutionError as exc:
            print(str(exc))
            connector.shutdown()
            return 1
        print(f"MEANREV DEMO EXECUTION ENABLED on {account.get('server')}", flush=True)
        autotrading_ok, autotrading_reason = executor.verify_autotrading_enabled()
        if not autotrading_ok:
            print(f"WARNING: {autotrading_reason}", flush=True)
            notify_telegram(f"MEANREV WARNING: {autotrading_reason}")
        preflight_min_lots(connector, executor, float(account.get("equity", 0.0)))
    state = load_state()
    mode = "DEMO EXECUTION" if executor else "paper only"
    notify_telegram(
        f"Sentinel MEAN-REVERSION engine ONLINE ({mode}, magic {MAGIC}).\n"
        f"IBS<{IBS_ENTRY} buys near the daily close on {', '.join(SYMBOLS)}; exits IBS>{IBS_EXIT} or {MAX_HOLD_DAYS}d."
    )
    windows = ", ".join(f"{s} {h:02d}:{m1:02d}-{h:02d}:{m2:02d}" for s, (h, m1, m2) in ENTRY_WINDOWS.items())
    print(f"MEAN-REVERSION LIVE ({mode}) | server windows: {windows}", flush=True)

    completed = 0
    health: dict[str, Any] = {"last_success_utc": None, "consecutive_failures": 0}
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
                    # A live account read is the engine's proof that MT5 is
                    # actually answering — the heartbeat file alone only
                    # proves this Python process is alive (audit 2026-08-11).
                    health["last_success_utc"] = datetime.now(UTC).isoformat()
                    health["consecutive_failures"] = 0
                except Exception as exc:
                    health["consecutive_failures"] = int(health.get("consecutive_failures", 0)) + 1
                    print(f"account observation failed ({exc})", flush=True)
            server_now = datetime.now(UTC)
            offset_seconds = refresh_server_offset(connector, state) * 3600.0
            server_clock = datetime.fromtimestamp(server_now.timestamp() + offset_seconds, UTC)
            server_hm = (server_clock.hour, server_clock.minute)
            server_date = str((server_now.timestamp() + offset_seconds) // 86400)
            server_now_date = server_clock.date()
            eligible = [
                symbol for symbol in SYMBOLS
                if args.force_window or symbol_in_window(symbol, server_hm[0], server_hm[1])
            ]
            if eligible:
                for symbol in eligible:
                    if state["last_processed"].get(symbol) == server_date:
                        continue
                    try:
                        candles = connector.get_historical_candles(symbol, "D1", count=VOL_WINDOW + 5)
                    except Exception as exc:
                        print(f"{symbol}: candle fetch failed ({exc})", flush=True)
                        continue
                    if not args.force_window and not candles_are_fresh(candles, server_now_date):
                        print(f"{symbol}: feed stale (last bar not today) - skipping evaluation", flush=True)
                        notify_telegram(f"MEANREV WARNING: {symbol} feed stale at its entry window - evaluation skipped.")
                        state["last_processed"][symbol] = server_date  # do not retry a broken feed all window
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
                    elif (
                        ibs < IBS_ENTRY
                        and risk_unit > 0
                        and len(state["open_positions"]) < MAX_CONCURRENT_POSITIONS
                    ):
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
                        tilt = depth_multiplier(ibs)
                        position["size_multiplier"] = tilt
                        action = {"event": "OPEN", **position}
                        submitted = True
                        if executor:
                            # Size this entry by dip depth, then restore the
                            # base risk so the multiplier can never leak into
                            # the next trade.
                            base_risk = executor.risk_percent
                            executor.risk_percent = base_risk * tilt
                            try:
                                action["demo_order"] = executor.open_position(position)
                            finally:
                                executor.risk_percent = base_risk
                            position["demo_order"] = action["demo_order"]
                            count_order_result(state, action["demo_order"])
                            submitted = bool(action["demo_order"].get("submitted"))
                        if submitted:
                            state["open_positions"][symbol] = position
                        # A REFUSED order must not occupy the symbol's only
                        # slot: it would block re-entry for up to 10 days and
                        # later book a fictional result against a position the
                        # account never held (found live 2026-08-12 after an
                        # AutoTrading refusal).
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
            write_status(state, health)
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
