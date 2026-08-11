"""RESEARCH ONLY — profit-laddered risk sizing on the 3y combined book.

User-specified ladder: trade low gear until a profit milestone, shift to a
higher gear with a SMALL profit target, bank it, retreat. Stages advance
when MARKED equity gains the stage target from the stage's starting equity;
after the last stage the ladder cycles. Mean-reversion risk-per-unit is the
laddered knob; the champion stays at its audited 0.5% flat.

Two modes:
  default          no tripwires anywhere (pure ladder)
  --tripwires      during gears >= 3.0%/unit only, a daily loss >= 18% or
                   drawdown-from-peak >= 50% demotes immediately to a 1.0%
                   recovery stage (+40% target), then resumes the ladder at
                   the stage AFTER the one that tripped.

All other mechanics identical to the audited forward test: no-stop meanrev
book, marked-equity sizing via the real DemoOrderExecutor.lot_size, min-lot
acceptance 6%, live 5-lot refusal ceiling, champion gates. Single historical
path; no margin modeling; daily marks understate intraday ruin.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.live_paper.demo_order_executor import DemoOrderExecutor
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError

STARTING_EQUITY = 3000.0
CHAMPION_TRADES = PROJECT_ROOT / "data" / "reports" / "champion_trades_3y.json"
MEANREV_TRADES = PROJECT_ROOT / "data" / "reports" / "meanrev_trades_export.json"
SYMBOLS = ("US30", "NAS100", "US500")
DAILY_BARS = 1400
SIZING_STOP_UNITS = 3.0
MAX_LOTS_PER_ORDER = 5.0

# (risk percent per risk-unit, profit target to advance) — user spec 2026-08-11.
LADDER = [
    (1.0, 0.40), (2.0, 0.25), (1.25, 0.45), (3.0, 0.175), (1.0, 0.40),
    (4.0, 0.085), (1.0, 0.40), (5.0, 0.07), (1.0, 0.40), (6.0, 0.06),
    (1.0, 0.40), (7.0, 0.05), (1.0, 0.40), (8.0, 0.04), (1.0, 0.40),
    (9.0, 0.035), (1.0, 0.40), (10.0, 0.03),
]
RECOVERY_STAGE = (1.0, 0.40)
TRIP_DAILY_LOSS = 18.0
TRIP_DRAWDOWN = 50.0
TRIP_GEAR_FLOOR = 3.0  # tripwires act only at gears >= this (user spec)


class _SizingMT5:
    def symbol_info(self, symbol: str):
        return SimpleNamespace(
            trade_tick_value=1.0, trade_tick_size=1.0, volume_min=0.1, volume_step=0.1, volume_max=100.0
        )


class _SizingConnector:
    def __init__(self) -> None:
        self.mt5 = _SizingMT5()


def day_of(timestamp: str) -> str:
    return str(pd.Timestamp(timestamp).date())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tripwires", action="store_true", help="enforce tripwires at gears >= 3%%/unit")
    args = parser.parse_args()
    logger.remove()

    champion_raw = json.loads(CHAMPION_TRADES.read_text(encoding="utf-8"))["trades"]
    meanrev_raw = json.loads(MEANREV_TRADES.read_text(encoding="utf-8"))["trades"]
    kill = PROJECT_ROOT / "data" / "reports" / "_nonexistent_kill_switch"
    champion_sizer = DemoOrderExecutor(_SizingConnector(), None, kill, risk_percent=0.5, min_lot_risk_cap_percent=1.5)
    meanrev_sizer = DemoOrderExecutor(_SizingConnector(), None, kill, risk_percent=3.0, min_lot_risk_cap_percent=6.0)

    connector = MT5Connector()
    try:
        connector.connect()
    except MT5ConnectorError as exc:
        print(f"MT5 required for daily bars: {exc}")
        return 1
    try:
        connector.supported_symbols = frozenset(set(connector.supported_symbols) | set(SYMBOLS))
        bars: dict[str, dict[str, dict[str, float]]] = {}
        today = datetime.now(UTC).date()
        for symbol in SYMBOLS:
            frame = connector.get_historical_candles(symbol, "D1", count=DAILY_BARS)
            if pd.Timestamp(frame["time"].iloc[-1]).date() >= today:
                frame = frame.iloc[:-1]
            bars[symbol] = {
                day_of(str(t)): {"open": float(o), "low": float(lo), "close": float(c)}
                for t, o, lo, c in zip(frame["time"], frame["open"], frame["low"], frame["close"])
            }
    finally:
        connector.shutdown()

    champion_days = [day_of(t["timestamp"]) for t in champion_raw]
    meanrev_days = [day_of(t["entry_time"]) for t in meanrev_raw]
    start_day = max(min(champion_days), min(meanrev_days))
    end_day = min(max(champion_days), max(day_of(t["timestamp"]) for t in meanrev_raw))
    start_day = max(start_day, str((pd.Timestamp(end_day) - pd.Timedelta(days=1095)).date()))

    champion_by_day: dict[str, list[dict]] = defaultdict(list)
    for trade in champion_raw:
        day = day_of(trade["timestamp"])
        if start_day <= day <= end_day:
            champion_by_day[day].append(trade)
    mr_entries_by_day: dict[str, list[dict]] = defaultdict(list)
    mr_exits_by_day: dict[str, list[dict]] = defaultdict(list)
    for trade in meanrev_raw:
        entry_day, exit_day = day_of(trade["entry_time"]), day_of(trade["timestamp"])
        if start_day <= entry_day and exit_day <= end_day:
            mr_entries_by_day[entry_day].append(trade)
            mr_exits_by_day[exit_day].append(trade)
    all_days = sorted(day for day in set().union(*[set(b) for b in bars.values()]) if start_day <= day <= end_day)

    equity = STARTING_EQUITY
    peak = STARTING_EQUITY
    open_mr: dict[str, dict] = {}
    last_close: dict[str, float] = {}
    max_dd = 0.0
    worst_day = {"day": None, "loss_percent": 0.0}
    prev_marked = STARTING_EQUITY
    blown = False
    curve: list[dict[str, Any]] = []

    # Ladder state.
    stage_idx = 0
    in_recovery = False
    resume_idx = 0
    gear, target = LADDER[0]
    stage_start = STARTING_EQUITY
    stage_log: list[dict[str, Any]] = [{"day": start_day, "gear": gear, "target": target, "equity": equity}]
    demotions: list[dict[str, Any]] = []
    gear_days: dict[float, int] = defaultdict(int)

    def floating_pnl() -> float:
        return sum(
            p["lots"] * (last_close[s] - p["entry"]) for s, p in open_mr.items() if s in last_close
        )

    for day in all_days:
        meanrev_sizer.risk_percent = 3.0 * gear
        gear_days[gear] += 1
        # Champion trades first (audited flat 0.5%).
        for trade in champion_by_day.get(day, ()):
            stop = float(trade["stop_distance"])
            price_proxy = bars.get(trade["symbol"], {}).get(day, {}).get("close") or last_close.get(trade["symbol"], 0.0)
            if price_proxy and stop < price_proxy * 0.0005:
                continue
            marked_equity = equity + floating_pnl()
            lots = champion_sizer.lot_size(trade["symbol"], stop, marked_equity)
            if lots <= 0 or lots > MAX_LOTS_PER_ORDER:
                continue
            equity += lots * float(trade["rr"]) * stop
        # Meanrev exits at the close.
        for trade in mr_exits_by_day.get(day, ()):
            position = open_mr.get(trade["symbol"])
            if position is None or position["trade"] is not trade:
                continue
            equity += position["lots"] * float(trade["pnl_points"])
            del open_mr[trade["symbol"]]
        # Meanrev entries at the close, sized at the CURRENT gear.
        for trade in mr_entries_by_day.get(day, ()):
            if trade["symbol"] in open_mr:
                continue
            stop = SIZING_STOP_UNITS * float(trade["risk_unit"])
            marked_equity = equity + floating_pnl()
            lots = meanrev_sizer.lot_size(trade["symbol"], stop, marked_equity)
            if lots <= 0 or lots > MAX_LOTS_PER_ORDER:
                continue
            open_mr[trade["symbol"]] = {"trade": trade, "lots": lots, "entry": float(trade["entry_price"])}
        # Mark.
        for symbol in SYMBOLS:
            bar = bars[symbol].get(day)
            if bar is not None:
                last_close[symbol] = bar["close"]
        marked = equity + floating_pnl()
        peak = max(peak, marked)
        drawdown = (peak - marked) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, drawdown)
        day_loss = (prev_marked - marked) / prev_marked * 100 if prev_marked > 0 else 0.0
        if day_loss > worst_day["loss_percent"]:
            worst_day = {"day": day, "loss_percent": round(day_loss, 2)}
        curve.append({"day": day, "marked": round(marked, 2), "gear": gear, "dd": round(drawdown, 2)})
        prev_marked = marked
        if marked <= 0:
            blown = True
            break

        # Ladder transitions on the daily mark.
        tripped = (
            args.tripwires
            and gear >= TRIP_GEAR_FLOOR
            and (day_loss >= TRIP_DAILY_LOSS or drawdown >= TRIP_DRAWDOWN)
        )
        if tripped:
            demotions.append({"day": day, "from_gear": gear, "day_loss": round(day_loss, 2), "dd": round(drawdown, 2), "equity": round(marked, 2)})
            resume_idx = (stage_idx + 1) % len(LADDER)
            in_recovery = True
            gear, target = RECOVERY_STAGE
            stage_start = marked
            stage_log.append({"day": day, "gear": gear, "target": target, "equity": round(marked, 2), "reason": "TRIPWIRE DEMOTION"})
        elif marked >= stage_start * (1.0 + target):
            if in_recovery:
                stage_idx = resume_idx
                in_recovery = False
            else:
                stage_idx = (stage_idx + 1) % len(LADDER)
            gear, target = LADDER[stage_idx]
            stage_start = marked
            stage_log.append({"day": day, "gear": gear, "target": target, "equity": round(marked, 2)})

    ending = curve[-1]["marked"] if curve else STARTING_EQUITY
    years = max((pd.Timestamp(end_day) - pd.Timestamp(start_day)).days / 365.25, 0.01)
    mode = "WITH tripwires (gears >= 3%)" if args.tripwires else "NO tripwires"
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "mode": mode,
        "window": {"start": start_day, "end": end_day},
        "ending_equity": round(ending, 2),
        "total_return_percent": round((ending / STARTING_EQUITY - 1) * 100, 1),
        "cagr_percent": round(((ending / STARTING_EQUITY) ** (1 / years) - 1) * 100, 1) if ending > 0 else None,
        "max_drawdown_percent": round(max_dd, 2),
        "worst_day": worst_day,
        "account_blown": blown,
        "blown_on": curve[-1]["day"] if blown else None,
        "stage_transitions": stage_log,
        "tripwire_demotions": demotions,
        "days_per_gear": {str(k): v for k, v in sorted(gear_days.items())},
        "highest_gear_reached": max(g for g, _ in [(s["gear"], 0) for s in stage_log]),
    }
    out = PROJECT_ROOT / "data" / "reports" / f"ladder_research_{'trip' if args.tripwires else 'notrip'}.json"
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")

    print(f"LADDER ({mode}) {start_day} -> {end_day}, start ${STARTING_EQUITY:.0f}")
    print(f"  ending ${report['ending_equity']:,} ({report['total_return_percent']}%, CAGR {report['cagr_percent']}%) | maxDD {report['max_drawdown_percent']}% | worst day {worst_day['loss_percent']}% {worst_day['day']}")
    print(f"  blown: {blown}{' on ' + str(report['blown_on']) if blown else ''} | highest gear reached: {report['highest_gear_reached']}% | demotions: {len(demotions)}")
    print("  stages:")
    for s in stage_log:
        note = f"  <- {s['reason']}" if "reason" in s else ""
        print(f"    {s['day']}  gear {s['gear']}%/unit, target +{int(s['target']*100)}%  @ ${s['equity']:,}{note}")
    print(f"  days per gear: {report['days_per_gear']}")
    print(f"  report -> {out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
