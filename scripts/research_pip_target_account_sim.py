"""What a $1000 account actually does: pip targets x fixed lot sizes.

Operator question (2026-08-12): live IBS entry, no tight stop, exit on a daily
profit target of 10/20/30/50 pips, fixed lot sizes 1-10, starting from $1000,
measured over the last 1m/2m/3m/1y/2y/3y/4y.

This is an ACCOUNT simulation, not an R-space study, because at these sizes the
account is the binding constraint and R cannot see it. Modelled from the live
broker's own numbers:

  USD per pip per lot   US30 1.00  NAS100 1.00  US500 1.00  DE40 1.15
  margin per lot        US30 269   NAS100 149   US500 39    DE40 152
  stop out              margin level below 20% (Pepperstone-Demo)

Three things the earlier R-space studies could not show, all of which decide
this question:

  1. MARGIN. 10 lots of US30 needs $2,691. A $1000 account cannot open it at
     all, so the trade is skipped rather than silently taken.
  2. STOP-OUT. There is no stop loss, so the broker provides one. Equity is
     recomputed every hour from the floating position; below a 20% margin
     level everything is closed where it stands. That is the real risk of a
     no-stop book and it is invisible in closed-trade statistics.
  3. FLOATING DRAWDOWN. Median adverse excursion on this signal is ~143 pips.
     At 1 lot that is $143 on a $1000 account; at 10 lots it is $1,430.

Exit is the target OR the live book's own exit (IBS>0.8 or 6 days) as a
backstop, so a trade that never reaches its target still ends the way the live
book would end it. Costs are the audited round-trip spreads, charged in full.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError

SYMBOLS = ("US30", "NAS100", "US500", "DE40")
COSTS = {"US30": 5.0, "NAS100": 3.5, "US500": 0.85, "DE40": 1.75}      # pips, round trip
USD_PER_PIP = {"US30": 1.00, "NAS100": 1.00, "US500": 1.00, "DE40": 1.15}
MARGIN_PER_LOT = {"US30": 269.08, "NAS100": 148.53, "US500": 38.74, "DE40": 152.03}
STOP_OUT_LEVEL = 20.0
START_BALANCE = 1000.0
MAX_CONCURRENT = 3
IBS_ENTRY, IBS_EXIT, VOL_WINDOW, MAX_HOLD = 0.2, 0.8, 20, 6
FINANCING_PER_DAY = 0.0002
TARGETS = (10, 20, 30, 50)
LOTS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
PERIODS = (("1m", 30), ("2m", 60), ("3m", 90), ("1y", 365), ("2y", 730), ("3y", 1095), ("4y", 1460))
DAILY_BARS, H1_BARS = 1400, 50000
REPORT = PROJECT_ROOT / "data" / "reports" / "pip_target_account_sim.json"


def build_events(d1: dict, h1: dict) -> tuple[list, dict]:
    """One merged hourly timeline plus the entry/exit signals hanging off it."""
    bars: dict[str, dict] = {}
    signals: dict[str, dict] = {}   # (symbol, h1_index) -> "enter" / "exit"
    for s in SYMBOLS:
        f = h1[s]
        ht = [pd.Timestamp(v) for v in f["time"]]
        bars[s] = {
            "time": ht,
            "high": [float(v) for v in f["high"]],
            "low": [float(v) for v in f["low"]],
            "close": [float(v) for v in f["close"]],
        }
        last_of_date = {}
        for i, t in enumerate(ht):
            last_of_date[t.date()] = i

        d = d1[s]
        highs = [float(v) for v in d["high"]]
        lows = [float(v) for v in d["low"]]
        closes = [float(v) for v in d["close"]]
        times = [pd.Timestamp(v) for v in d["time"]]
        marks: dict[int, str] = {}
        for i in range(VOL_WINDOW + 3, len(closes)):
            rng = highs[i] - lows[i]
            ibs = (closes[i] - lows[i]) / rng if rng > 0 else 0.5
            idx = last_of_date.get(times[i].date())
            if idx is None:
                continue
            if ibs < IBS_ENTRY:
                marks[idx] = "enter"
            elif ibs > IBS_EXIT:
                marks[idx] = "exit"
        signals[s] = marks
    return bars, signals


MIN_LOT = 0.1  # broker minimum and step on all four symbols


def simulate(bars: dict, signals: dict, target: int, lots: float,
             start_time: pd.Timestamp, start_balance: float = START_BALANCE,
             compound: bool = False) -> dict[str, Any]:
    """compound=True scales position size with the account.

    Sizing is recomputed at each entry as lots * (balance / start_balance),
    floored at the broker minimum, so profits genuinely become risk capital.
    Compounding is a multiplier on the outcome, not a fix for it: it magnifies
    a positive expectancy and accelerates a negative one, which is exactly what
    the comparison here is for.
    """
    balance = start_balance
    peak = start_balance
    max_dd = 0.0
    open_pos: dict[str, dict] = {}
    trades = wins = skipped = 0
    stopped_out = 0

    idx = {s: 0 for s in SYMBOLS}
    times = {s: bars[s]["time"] for s in SYMBOLS}
    for s in SYMBOLS:
        while idx[s] < len(times[s]) and times[s][idx[s]] < start_time:
            idx[s] += 1

    # Merge the four hourly streams into one clock.
    while True:
        nxt = [(times[s][idx[s]], s) for s in SYMBOLS if idx[s] < len(times[s])]
        if not nxt:
            break
        now, sym = min(nxt)
        i = idx[sym]
        idx[sym] += 1
        b = bars[sym]

        # 1. target reached?
        if sym in open_pos:
            p = open_pos[sym]
            if b["high"][i] >= p["target_px"]:
                gross = (p["target_px"] - p["entry"]) * USD_PER_PIP[sym] * p["lots"]
                days = max((now - p["opened"]).total_seconds() / 86400.0, 1 / 24)
                fees = (COSTS[sym] + FINANCING_PER_DAY * p["entry"] * days) * USD_PER_PIP[sym] * p["lots"]
                balance += gross - fees
                trades += 1
                wins += 1 if gross - fees > 0 else 0
                del open_pos[sym]

        # 2. daily-close housekeeping: the live exit, then a new entry
        mark = signals[sym].get(i)
        if sym in open_pos:
            p = open_pos[sym]
            held_days = (now - p["opened"]).days
            if mark == "exit" or held_days >= MAX_HOLD:
                px = b["close"][i]
                gross = (px - p["entry"]) * USD_PER_PIP[sym] * p["lots"]
                days = max((now - p["opened"]).total_seconds() / 86400.0, 1 / 24)
                fees = (COSTS[sym] + FINANCING_PER_DAY * p["entry"] * days) * USD_PER_PIP[sym] * p["lots"]
                balance += gross - fees
                trades += 1
                wins += 1 if gross - fees > 0 else 0
                del open_pos[sym]
        elif mark == "enter" and len(open_pos) < MAX_CONCURRENT:
            size = lots
            if compound:
                size = max(MIN_LOT, round(lots * balance / start_balance, 1))
            need = MARGIN_PER_LOT[sym] * size
            used = sum(MARGIN_PER_LOT[s2] * p2["lots"] for s2, p2 in open_pos.items())
            floating = sum((bars[s2]["close"][max(min(idx[s2] - 1, len(bars[s2]["close"]) - 1), 0)] - p2["entry"])
                           * USD_PER_PIP[s2] * p2["lots"] for s2, p2 in open_pos.items())
            if balance + floating - used >= need:
                px = b["close"][i]
                open_pos[sym] = {"entry": px, "target_px": px + target, "opened": now, "lots": size}
            else:
                skipped += 1

        # 3. equity, drawdown and the broker's own stop
        if open_pos:
            floating = 0.0
            for s2, p2 in open_pos.items():
                j = min(idx[s2] - 1, len(bars[s2]["close"]) - 1)
                floating += (bars[s2]["close"][max(j, 0)] - p2["entry"]) * USD_PER_PIP[s2] * p2["lots"]
            equity = balance + floating
            used = sum(MARGIN_PER_LOT[s2] * p2["lots"] for s2, p2 in open_pos.items())
            if used > 0 and equity / used * 100.0 <= STOP_OUT_LEVEL:
                balance = equity
                for s2, p2 in list(open_pos.items()):
                    balance -= COSTS[s2] * USD_PER_PIP[s2] * p2["lots"]
                    trades += 1
                open_pos.clear()
                stopped_out += 1
        else:
            equity = balance

        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100.0)
        if equity <= 0:
            return {"final": 0.0, "blown": True, "trades": trades, "wins": wins,
                    "max_dd": 100.0, "stopped_out": stopped_out, "skipped": skipped}

    # close whatever is still open at the end, at the last price
    for s2, p2 in open_pos.items():
        j = len(bars[s2]["close"]) - 1
        balance += (bars[s2]["close"][j] - p2["entry"]) * USD_PER_PIP[s2] * p2["lots"]
        balance -= COSTS[s2] * USD_PER_PIP[s2] * p2["lots"]
    return {"final": round(balance, 2), "blown": balance <= 0, "trades": trades, "wins": wins,
            "max_dd": round(max_dd, 1), "stopped_out": stopped_out, "skipped": skipped}


def main() -> int:
    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
    except MT5ConnectorError as exc:
        print(f"MT5 connection failed: {exc}")
        return 1
    try:
        connector.supported_symbols = frozenset(set(connector.supported_symbols) | set(SYMBOLS))
        d1 = {s: connector.get_historical_candles(s, "D1", count=DAILY_BARS).iloc[:-1].reset_index(drop=True)
              for s in SYMBOLS}
        h1 = {s: connector.get_historical_candles(s, "H1", count=H1_BARS).reset_index(drop=True)
              for s in SYMBOLS}
    finally:
        connector.shutdown()

    bars, signals = build_events(d1, h1)
    end = max(bars[s]["time"][-1] for s in SYMBOLS)

    print("=" * 96)
    print("  $1000 ACCOUNT | live IBS entry | no stop loss | exit on pip target or the live exit")
    print("  Each cell is the FINAL BALANCE. 'BLOWN' = account wiped. 'n/a' = cannot afford 1 trade.")
    print("=" * 96)

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(), "status": "RESEARCH_ONLY",
        "start_balance": START_BALANCE, "stop_out_level": STOP_OUT_LEVEL,
        "usd_per_pip_per_lot": USD_PER_PIP, "margin_per_lot": MARGIN_PER_LOT,
        "results": {},
    }

    for label, days in PERIODS:
        start = end - pd.Timedelta(days=days)
        print(f"\n### LAST {label.upper()}  ({str(start)[:10]} -> {str(end)[:10]})")
        print(f"{'target':>8s} " + "".join(f"{l:>9d} lot" for l in LOTS))
        print("-" * (8 + 13 * len(LOTS)))
        for tgt in TARGETS:
            cells = []
            for lot in LOTS:
                r = simulate(bars, signals, tgt, lot, start)
                report["results"][f"{label}_{tgt}p_{lot}lot"] = r
                if r["blown"]:
                    cells.append(f"{'BLOWN':>13s}")
                elif r["trades"] == 0:
                    cells.append(f"{'n/a':>13s}")
                else:
                    cells.append(f"{r['final']:>13,.0f}")
            print(f"{tgt:6d}p  " + "".join(cells))

    REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nreport -> {REPORT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
