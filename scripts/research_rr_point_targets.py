"""2.5:1 reward-to-risk on fixed point targets, resolved on intraday bars.

Operator question (2026-08-12): take the point targets from
point_target_exits.json and apply a 2.5 R:R - target N points, stop N/2.5
points - aiming for a decision inside a day or less.

WHY THIS NEEDS H1 AND THE PREVIOUS TEST DID NOT. With a target-only exit, the
daily high answers the only question that matters: was the target reached.
Add a stop and the answer becomes path-dependent - on any day whose high
reached the target AND whose low reached the stop, the daily bar cannot say
which came first, and at 2.5:1 that ordering IS the result. So entries are
still taken from the D1 close (identical signal to the live book) but the
outcome is walked forward one H1 bar at a time.

Residual ambiguity is not hidden: when a single H1 bar contains both levels,
the STOP is assumed to fill first, and the share of trades decided that way is
reported as `ambiguous_pct`. A high value there means the result rests on an
assumption rather than on the data.

Costs and the risk_unit definition are carried over from research_exit_rules.py
so R is comparable across every exit study in this repo. Results are also given
in stop units - the operator's own framing - where a win is +2.5 and a loss -1,
and break-even needs a 28.6% win rate.
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
H1_BARS = 50000
IBS_ENTRY, VOL_WINDOW = 0.2, 20
FINANCING_PER_DAY = 0.0002
POINT_SIZE = 0.1
RR = 2.5
TARGETS = (1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000)
HORIZONS = (8, 24, 48)  # hours to resolve in
REPORT = PROJECT_ROOT / "data" / "reports" / "rr_point_targets.json"


def entry_signals(frame: pd.DataFrame) -> list[dict]:
    """IBS<0.2 at the daily close - the live book's entry, unchanged."""
    highs = [float(v) for v in frame["high"]]
    lows = [float(v) for v in frame["low"]]
    closes = [float(v) for v in frame["close"]]
    times = [pd.Timestamp(v) for v in frame["time"]]
    out = []
    for i in range(VOL_WINDOW + 3, len(closes)):
        rng = highs[i] - lows[i]
        ibs = (closes[i] - lows[i]) / rng if rng > 0 else 0.5
        if ibs >= IBS_ENTRY:
            continue
        moves = [abs(closes[j] - closes[j - 1]) for j in range(i - VOL_WINDOW, i)]
        ru = sum(moves) / len(moves) if moves else 0.0
        if ru > 0:
            out.append({"date": times[i].date(), "close": closes[i], "ru": ru})
    return out


def simulate(signals: list[dict], h1: pd.DataFrame, symbol: str,
             target_points: int, horizon_hours: int) -> list[dict]:
    """Walk each entry forward hour by hour until target, stop, or horizon."""
    h_time = [pd.Timestamp(v) for v in h1["time"]]
    h_high = [float(v) for v in h1["high"]]
    h_low = [float(v) for v in h1["low"]]
    h_close = [float(v) for v in h1["close"]]
    # last H1 bar of each date == the moment the daily bar closes
    last_of_date: dict[Any, int] = {}
    for i, t in enumerate(h_time):
        last_of_date[t.date()] = i

    cost = COSTS.get(symbol, 2.0)
    tp_dist = target_points * POINT_SIZE
    sl_dist = (target_points / RR) * POINT_SIZE
    trades: list[dict] = []
    for sig in signals:
        start = last_of_date.get(sig["date"])
        if start is None or start + 1 >= len(h_time):
            continue
        entry_px = h_close[start]
        tp, sl = entry_px + tp_dist, entry_px - sl_dist
        outcome, exit_px, hours, ambiguous = None, None, 0, False
        for j in range(start + 1, min(start + 1 + horizon_hours, len(h_time))):
            hours = j - start
            hit_tp, hit_sl = h_high[j] >= tp, h_low[j] <= sl
            if hit_tp and hit_sl:
                # Both inside one hour: assume the stop filled first. This is
                # the pessimistic reading and it is counted, not buried.
                outcome, exit_px, ambiguous = "stop", sl, True
                break
            if hit_sl:
                outcome, exit_px = "stop", sl
                break
            if hit_tp:
                outcome, exit_px = "target", tp
                break
        if outcome is None:
            j = min(start + horizon_hours, len(h_time) - 1)
            hours = j - start
            outcome, exit_px = "timeout", h_close[j]
        days = max(hours / 24.0, 1 / 24.0)
        pnl = exit_px - entry_px - cost - FINANCING_PER_DAY * entry_px * days
        trades.append({
            "symbol": symbol, "exit_time": str(h_time[min(start + hours, len(h_time) - 1)]),
            "rr": pnl / sig["ru"],              # in risk units, comparable to every other study
            "stop_r": pnl / sl_dist,            # in stop units, the 2.5:1 framing
            "outcome": outcome, "hours": hours, "ambiguous": ambiguous,
            "stop_frac_ru": sl_dist / sig["ru"],
        })
    return trades


def book(trades: list[dict]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
    rrs = [t["rr"] for t in trades]
    stop_rs = [t["stop_r"] for t in trades]
    wins = [r for r in rrs if r > 0]
    gross_loss = abs(sum(r for r in rrs if r < 0))
    q: dict[str, float] = defaultdict(float)
    for t in trades:
        s = pd.Timestamp(t["exit_time"])
        q[f"{s.year}-Q{(s.month - 1) // 3 + 1}"] += t["rr"]
    active = [v for v in q.values() if v != 0]
    n = len(trades)
    return {
        "trades": n,
        "win_rate": round(len(wins) / n * 100, 1),
        "net_rr": round(sum(rrs), 2),
        "rw_pf": round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy": round(sum(rrs) / n, 3),
        "net_stop_r": round(sum(stop_rs), 1),
        "expectancy_stop_r": round(sum(stop_rs) / n, 3),
        "hit_target_pct": round(sum(1 for t in trades if t["outcome"] == "target") / n * 100, 1),
        "hit_stop_pct": round(sum(1 for t in trades if t["outcome"] == "stop") / n * 100, 1),
        "timeout_pct": round(sum(1 for t in trades if t["outcome"] == "timeout") / n * 100, 1),
        "ambiguous_pct": round(sum(1 for t in trades if t["ambiguous"]) / n * 100, 1),
        "avg_hours": round(sum(t["hours"] for t in trades) / n, 1),
        "median_stop_frac_of_daily_move": round(
            sorted(t["stop_frac_ru"] for t in trades)[n // 2], 3),
        "positive_quarter_share": round(sum(1 for v in active if v > 0) / len(active), 2) if active else 0.0,
    }


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

    common = max(f["time"].min() for f in d1.values())
    d1 = {s: f[f["time"] >= common].reset_index(drop=True) for s, f in d1.items()}
    signals = {s: entry_signals(f) for s, f in d1.items()}
    total_sig = sum(len(v) for v in signals.values())
    print(f"entries from the live signal: {total_sig} across {', '.join(SYMBOLS)}")
    print(f"span {str(common)[:10]} -> {str(max(f['time'].max() for f in d1.values()))[:10]}")
    print(f"reward:risk {RR}:1  -> break-even win rate {100 / (1 + RR):.1f}%\n")

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "question": f"{RR}:1 reward-to-risk on fixed point targets, resolved intraday",
        "rr": RR, "point_size": POINT_SIZE, "results": {},
        "method": "entry at D1 close (live signal); outcome walked forward on H1; "
                  "stop assumed first when one H1 bar holds both levels",
    }

    for horizon in HORIZONS:
        print(f"=== HORIZON {horizon}h "
              f"({'intraday' if horizon <= 24 else 'up to two sessions'}) ===")
        print(f"{'target':>7s} {'stop':>6s} {'stop/ATR':>9s} {'trades':>7s} {'WR%':>6s} "
              f"{'tgt%':>6s} {'stop%':>6s} {'t/o%':>6s} {'amb%':>6s} {'netR':>8s} "
              f"{'exp R':>7s} {'net 2.5R':>9s} {'expR/stop':>10s}")
        print("-" * 118)
        for tp in TARGETS:
            trades: list[dict] = []
            for s in SYMBOLS:
                trades.extend(simulate(signals[s], h1[s], s, tp, horizon))
            b = book(trades)
            report["results"][f"{tp}pts_rr{RR}_{horizon}h"] = {**b, "params": {
                "target_points": tp, "stop_points": round(tp / RR), "horizon_hours": horizon}}
            if not b.get("trades"):
                continue
            print(f"{tp:7d} {round(tp / RR):6d} {b['median_stop_frac_of_daily_move']:9.2f} "
                  f"{b['trades']:7d} {b['win_rate']:6.1f} {b['hit_target_pct']:6.1f} "
                  f"{b['hit_stop_pct']:6.1f} {b['timeout_pct']:6.1f} {b['ambiguous_pct']:6.1f} "
                  f"{b['net_rr']:8.1f} {b['expectancy']:7.3f} {b['net_stop_r']:9.1f} "
                  f"{b['expectancy_stop_r']:10.3f}")
        print()

    REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"report -> {REPORT.relative_to(PROJECT_ROOT)}")
    print("\nLIVE BOOK for reference: 685 trades, +261.9R, rwPF 1.59, WR 69.9%, avg hold 2.8 DAYS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
