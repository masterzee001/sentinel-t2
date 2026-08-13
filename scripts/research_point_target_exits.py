"""Fixed POINT profit targets in place of the time/IBS exit.

Operator question (2026-08-12): keep the IBS<0.2 entry exactly as it is, but
exit on a fixed profit in broker points - 1000 to 10000 - instead of on IBS>0.8
or the 6-day stop.

Distinct from profit_exits_research.json, which tested targets scaled in RISK
UNITS and was rejected on the holdout. A fixed point target is not
volatility-scaled, so it is a different question: the same 1000 points is
0.19% of US30 and 1.29% of US500.

Method mirrors research_exit_rules.py exactly so the numbers are comparable:
same entry, same costs, same financing, same discovery/holdout split, and the
winner chosen on the discovery half then validated on the untouched holdout.

TWO MODELLING CHOICES, both stated because they flatter the result:

  1. A target is treated as hit when the daily HIGH reaches it, filled at
     exactly the target. That is fair for a resting limit order - there is no
     stop for it to race against in this book - but it is still an intrabar
     assumption.

  2. The pure variant has NO time limit and NO stop, so a trade closes only
     when its target is reached. Trades still open at the end of the data are
     reported separately and excluded from the R statistics. They are the
     whole risk of this design: a position that never reaches its target
     occupies its symbol's only slot indefinitely, and the entries it blocks
     never appear in the trade count as a cost.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
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
COSTS = {"US30": 5.0, "NAS100": 3.5, "US500": 0.85, "DE40": 1.75}
DAILY_BARS = 1400
IBS_ENTRY, IBS_EXIT, VOL_WINDOW = 0.2, 0.8, 20
FINANCING_PER_DAY = 0.0002
POINT_SIZE = 0.1  # all four symbols: symbol_info.point, digits=1
TARGETS = (1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000)
REPORT = PROJECT_ROOT / "data" / "reports" / "point_target_exits.json"


def simulate_baseline(frame: pd.DataFrame, symbol: str, max_hold: int = 6) -> tuple[list[dict], int]:
    """The live book: IBS>0.8 or the 6-day stop."""
    highs = [float(v) for v in frame["high"]]
    lows = [float(v) for v in frame["low"]]
    closes = [float(v) for v in frame["close"]]
    times = [pd.Timestamp(v) for v in frame["time"]]
    cost = COSTS.get(symbol, 2.0)
    trades: list[dict] = []
    entry = None
    bars_held = bars_total = blocked = 0
    for i in range(VOL_WINDOW + 3, len(closes)):
        bars_total += 1
        rng = highs[i] - lows[i]
        ibs = (closes[i] - lows[i]) / rng if rng > 0 else 0.5
        if entry is not None:
            bars_held += 1
            if ibs < IBS_ENTRY:
                blocked += 1
            held = i - entry["i"]
            if ibs > IBS_EXIT or held >= max_hold:
                days = max((times[i] - times[entry["i"]]).days, 1)
                pnl = closes[i] - closes[entry["i"]] - cost - FINANCING_PER_DAY * closes[entry["i"]] * days
                trades.append({"symbol": symbol, "exit_time": str(times[i]), "hold": held, "rr": pnl / entry["ru"]})
                entry = None
        if entry is None and ibs < IBS_ENTRY:
            moves = [abs(closes[j] - closes[j - 1]) for j in range(i - VOL_WINDOW, i)]
            ru = sum(moves) / len(moves) if moves else 0.0
            if ru > 0:
                entry = {"i": i, "ru": ru}
    return trades, (1 if entry is not None else 0), (bars_held, bars_total, blocked)


def simulate_points(frame: pd.DataFrame, symbol: str, target_points: int,
                    max_hold: int | None = None) -> tuple[list[dict], int]:
    """Exit when price reaches entry + target_points, filled at the target.

    max_hold=None is the pure form the operator asked for: profit target only,
    no time limit at all. Passing a number keeps the time stop as a backstop.
    """
    highs = [float(v) for v in frame["high"]]
    lows = [float(v) for v in frame["low"]]
    closes = [float(v) for v in frame["close"]]
    times = [pd.Timestamp(v) for v in frame["time"]]
    cost = COSTS.get(symbol, 2.0)
    distance = target_points * POINT_SIZE
    trades: list[dict] = []
    entry = None
    bars_held = bars_total = blocked = 0
    for i in range(VOL_WINDOW + 3, len(closes)):
        bars_total += 1
        rng = highs[i] - lows[i]
        ibs = (closes[i] - lows[i]) / rng if rng > 0 else 0.5
        if entry is not None:
            bars_held += 1
            if ibs < IBS_ENTRY:
                blocked += 1
            held = i - entry["i"]
            target = entry["price"] + distance
            hit = highs[i] >= target
            timed_out = max_hold is not None and held >= max_hold
            if hit or timed_out:
                exit_price = target if hit else closes[i]
                days = max((times[i] - times[entry["i"]]).days, 1)
                pnl = exit_price - entry["price"] - cost - FINANCING_PER_DAY * entry["price"] * days
                trades.append({
                    "symbol": symbol, "exit_time": str(times[i]), "hold": held,
                    "rr": pnl / entry["ru"], "reason": "target" if hit else "timeout",
                })
                entry = None
        if entry is None and ibs < IBS_ENTRY:
            moves = [abs(closes[j] - closes[j - 1]) for j in range(i - VOL_WINDOW, i)]
            ru = sum(moves) / len(moves) if moves else 0.0
            if ru > 0:
                entry = {"i": i, "ru": ru, "price": closes[i]}
    return trades, (1 if entry is not None else 0), (bars_held, bars_total, blocked)


def book(trades: list[dict], unclosed: int = 0, occupancy: tuple[int, int, int] = (0, 0, 0)) -> dict[str, Any]:
    """occupancy = (bars_held, bars_total, entries_blocked).

    Net R alone cannot compare these variants: a book that holds one position
    for two years and books +4R looks better per trade than one that turns over
    weekly, while doing nothing with the capital and refusing every signal that
    arrives meanwhile. entries_blocked counts those refusals - the cost that
    never shows up in a trade list because the trades were never taken.
    """
    bars_held, bars_total, blocked = occupancy
    rrs = [t["rr"] for t in trades]
    if not rrs:
        return {"trades": 0, "net_rr": 0.0, "rw_pf": None, "still_open_at_end": unclosed}
    wins = [r for r in rrs if r > 0]
    gross_loss = abs(sum(r for r in rrs if r < 0))
    q: dict[str, float] = defaultdict(float)
    for t in trades:
        s = pd.Timestamp(t["exit_time"])
        q[f"{s.year}-Q{(s.month - 1) // 3 + 1}"] += t["rr"]
    active = [v for v in q.values() if v != 0]
    holds = [t["hold"] for t in trades]
    return {
        "trades": len(rrs),
        "win_rate": round(len(wins) / len(rrs) * 100, 1),
        "net_rr": round(sum(rrs), 2),
        # A book with no losers would otherwise report a spectacular pseudo
        # profit factor; None says "undefined", not "infinite edge".
        "rw_pf": round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy": round(sum(rrs) / len(rrs), 3),
        "avg_hold": round(sum(holds) / len(holds), 1),
        "max_hold": max(holds),
        "positive_quarter_share": round(sum(1 for v in active if v > 0) / len(active), 2) if active else 0.0,
        "still_open_at_end": unclosed,
        "occupancy_pct": round(bars_held / bars_total * 100, 1) if bars_total else 0.0,
        "entries_blocked": blocked,
    }


def run(frames: dict[str, pd.DataFrame], fn, **kw) -> dict[str, Any]:
    trades: list[dict] = []
    unclosed = held = total = blocked = 0
    for s, f in frames.items():
        t, u, (bh, bt, bl) = fn(f, s, **kw)
        trades.extend(t)
        unclosed += u
        held += bh
        total += bt
        blocked += bl
    return book(trades, unclosed, (held, total, blocked))


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
        frames = {s: connector.get_historical_candles(s, "D1", count=DAILY_BARS).iloc[:-1].reset_index(drop=True)
                  for s in SYMBOLS}
        common = max(f["time"].min() for f in frames.values())
        frames = {s: f[f["time"] >= common].reset_index(drop=True) for s, f in frames.items()}
        half = {s: len(f) // 2 for s, f in frames.items()}
        disc = {s: f.iloc[:half[s]].reset_index(drop=True) for s, f in frames.items()}
        hold = {s: f.iloc[half[s]:].reset_index(drop=True) for s, f in frames.items()}
        spans = {s: (str(f["time"].min())[:10], str(f["time"].max())[:10], len(f)) for s, f in frames.items()}
    finally:
        connector.shutdown()

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "question": "fixed point profit target instead of the IBS/time exit",
        "point_size": POINT_SIZE,
        "data_span": spans,
        "results": {},
    }

    base_full = run(frames, simulate_baseline)
    base_d = run(disc, simulate_baseline)
    base_h = run(hold, simulate_baseline)
    report["baseline"] = {"full": base_full, "discovery": base_d, "holdout": base_h,
                          "rule": "IBS>0.8 or 6d (the live book)"}
    print(f"BASELINE (live book: IBS>0.8 or 6d): {base_full['trades']}t  net {base_full['net_rr']}R  "
          f"rwPF {base_full['rw_pf']}  WR {base_full['win_rate']}%  avgHold {base_full['avg_hold']}d")
    print(f"  discovery {base_d['net_rr']}R | holdout {base_h['net_rr']}R\n")

    print("PURE POINT TARGET - no time limit, no stop (what was asked for)")
    print(f"{'target':>8s} {'trades':>7s} {'WR%':>6s} {'netR':>9s} {'rwPF':>7s} {'expect':>7s} "
          f"{'avgHold':>8s} {'maxHold':>8s} {'q+':>5s} {'inPos%':>7s} {'blocked':>8s} {'discR':>8s} {'holdR':>8s}")
    print("-" * 120)
    for tp in TARGETS:
        full = run(frames, simulate_points, target_points=tp)
        d = run(disc, simulate_points, target_points=tp)
        h = run(hold, simulate_points, target_points=tp)
        report["results"][f"pure_{tp}pts"] = {"full": full, "discovery": d, "holdout": h,
                                              "params": {"target_points": tp, "max_hold": None}}
        print(f"{tp:8d} {full['trades']:7d} {full['win_rate']:6.1f} {full['net_rr']:9.1f} "
              f"{str(full['rw_pf']):>7s} {full['expectancy']:7.3f} {full['avg_hold']:8.1f} "
              f"{full['max_hold']:8d} {full['positive_quarter_share']:5.2f} {full['occupancy_pct']:7.1f} "
              f"{full['entries_blocked']:8d} {d['net_rr']:8.1f} {h['net_rr']:8.1f}")

    print("\nSAME TARGETS, KEEPING THE 6-DAY STOP AS A BACKSTOP")
    print(f"{'target':>8s} {'trades':>7s} {'WR%':>6s} {'netR':>9s} {'rwPF':>7s} {'expect':>7s} "
          f"{'avgHold':>8s} {'q+':>5s} {'discR':>8s} {'holdR':>8s}")
    print("-" * 90)
    for tp in TARGETS:
        full = run(frames, simulate_points, target_points=tp, max_hold=6)
        d = run(disc, simulate_points, target_points=tp, max_hold=6)
        h = run(hold, simulate_points, target_points=tp, max_hold=6)
        report["results"][f"{tp}pts_and_6d"] = {"full": full, "discovery": d, "holdout": h,
                                                "params": {"target_points": tp, "max_hold": 6}}
        print(f"{tp:8d} {full['trades']:7d} {full['win_rate']:6.1f} {full['net_rr']:9.1f} "
              f"{str(full['rw_pf']):>7s} {full['expectancy']:7.3f} {full['avg_hold']:8.1f} "
              f"{full['positive_quarter_share']:5.2f} {d['net_rr']:8.1f} {h['net_rr']:8.1f}")

    # Selection on DISCOVERY only, then validation on the untouched holdout.
    winner = max(report["results"], key=lambda n: report["results"][n]["discovery"]["net_rr"])
    w = report["results"][winner]
    improves_disc = w["discovery"]["net_rr"] > base_d["net_rr"]
    improves_hold = w["holdout"]["net_rr"] > base_h["net_rr"]
    report["selection"] = {
        "chosen_on_discovery": winner,
        "discovery_net_rr": w["discovery"]["net_rr"], "baseline_discovery": base_d["net_rr"],
        "holdout_net_rr": w["holdout"]["net_rr"], "baseline_holdout": base_h["net_rr"],
        "beats_baseline_on_discovery": improves_disc,
        "VALIDATES_ON_HOLDOUT": improves_hold,
        "verdict": ("PROMOTABLE - improves on the half it was never chosen on" if improves_disc and improves_hold
                    else "REJECTED - does not survive the holdout"),
    }
    print(f"\nchosen on discovery: {winner}")
    print(f"  discovery {w['discovery']['net_rr']}R vs baseline {base_d['net_rr']}R")
    print(f"  HOLDOUT   {w['holdout']['net_rr']}R vs baseline {base_h['net_rr']}R")
    print(f"  VERDICT: {report['selection']['verdict']}")

    REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nreport -> {REPORT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
