"""GATE — walk-forward the liquidity-sweep engine before building it.

The 20-day lookback scored best (rwPF 1.93) but only AFTER twelve
lookback/reclaim combinations had been inspected, so that number carries
selection pressure and cannot be promoted on its own.

This runs the honest version: an EXPANDING-WINDOW walk-forward. At each step
the lookback is chosen using only data available at that time, then applied
FORWARD to the next unseen quarter. The reported result is the concatenation
of those out-of-sample quarters — i.e. what a trader who re-fitted quarterly
in real time would actually have earned, never knowing the future.

Also re-tests entry isolation at the chosen lookback: with a NEUTRAL exit
(fixed hold, no IBS condition), does the entry still carry the edge? An
earlier isolation run weakened the 5-day sweep from 1.39 to 1.22, so this
must be re-checked for whichever lookback survives.
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
VOL_WINDOW, IBS_EXIT, MAX_HOLD = 20, 0.8, 10
FINANCING_PER_DAY = 0.0002
LOOKBACKS = (3, 5, 10, 20)
MIN_TRAIN_QUARTERS = 8
REPORT = PROJECT_ROOT / "data" / "reports" / "sweep_walkforward.json"


def trades(frame: pd.DataFrame, symbol: str, lookback: int,
           exit_mode: str = "ibs", neutral_hold: int = 5) -> list[dict]:
    h = [float(v) for v in frame["high"]]; l = [float(v) for v in frame["low"]]
    c = [float(v) for v in frame["close"]]
    t = [pd.Timestamp(v) for v in frame["time"]]
    cost = COSTS.get(symbol, 2.0)
    out: list[dict] = []
    pos = None
    for i in range(VOL_WINDOW + 22, len(c)):
        rng = h[i] - l[i]
        ibs = (c[i] - l[i]) / rng if rng > 0 else 0.5
        if pos is not None:
            held = i - pos["i"]
            done = held >= neutral_hold if exit_mode == "neutral" else (ibs > IBS_EXIT or held >= MAX_HOLD)
            if done:
                days = max((t[i] - t[pos["i"]]).days, 1)
                pnl = c[i] - c[pos["i"]] - cost - FINANCING_PER_DAY * c[pos["i"]] * days
                out.append({"symbol": symbol, "entry_time": str(t[pos["i"]]), "exit_time": str(t[i]),
                            "rr": pnl / pos["ru"], "hold": held,
                            "entry_price": round(c[pos["i"]], 5), "risk_unit": round(pos["ru"], 5),
                            "pnl_points": round(pnl, 5)})
                pos = None
            continue
        mv = [abs(c[j] - c[j - 1]) for j in range(i - VOL_WINDOW, i)]
        ru = sum(mv) / len(mv) if mv else 0.0
        if ru <= 0:
            continue
        prior_low = min(l[i - lookback:i])
        if l[i] < prior_low and c[i] > prior_low:
            pos = {"i": i, "ru": ru}
    return out


def book(rows: list[dict]) -> dict[str, Any]:
    if not rows:
        return {"trades": 0, "net_rr": 0.0, "rw_pf": None, "q": 0.0}
    rrs = [r["rr"] for r in rows]
    wins = [r for r in rrs if r > 0]; gl = abs(sum(r for r in rrs if r < 0))
    q: dict[str, float] = defaultdict(float)
    for r in rows:
        s = pd.Timestamp(r["exit_time"]); q[f"{s.year}-Q{(s.month - 1) // 3 + 1}"] += r["rr"]
    act = [v for v in q.values() if v != 0]
    return {"trades": len(rrs), "win_rate": round(len(wins) / len(rrs) * 100, 1),
            "net_rr": round(sum(rrs), 2), "rw_pf": round(sum(wins) / gl, 2) if gl else None,
            "expectancy": round(sum(rrs) / len(rrs), 3),
            "q": round(sum(1 for v in act if v > 0) / len(act), 2) if act else 0.0}


def quarter_of(ts: str) -> str:
    s = pd.Timestamp(ts)
    return f"{s.year}-Q{(s.month - 1) // 3 + 1}"


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
    finally:
        connector.shutdown()

    by_lb: dict[int, list[dict]] = {}
    for lb in LOOKBACKS:
        rows: list[dict] = []
        for s, f in frames.items():
            rows.extend(trades(f, s, lb))
        rows.sort(key=lambda r: r["exit_time"])
        by_lb[lb] = rows
    quarters = sorted({quarter_of(r["exit_time"]) for rows in by_lb.values() for r in rows})

    print("=== EXPANDING-WINDOW WALK-FORWARD (lookback re-chosen each quarter, applied forward) ===")
    print(f"{'quarter':9s} {'chosen lb':>10s} {'oos trades':>11s} {'oos netR':>9s}")
    print("-" * 44)
    oos: list[dict] = []
    picks: dict[str, int] = {}
    for k in range(MIN_TRAIN_QUARTERS, len(quarters)):
        test_q = quarters[k]
        train_qs = set(quarters[:k])
        # Choose the lookback on TRAINING quarters only.
        scored = {}
        for lb, rows in by_lb.items():
            tr = [r for r in rows if quarter_of(r["exit_time"]) in train_qs]
            b = book(tr)
            scored[lb] = b["net_rr"] if b["trades"] >= 30 else -1e9
        chosen = max(scored, key=scored.get)
        picks[test_q] = chosen
        fwd = [r for r in by_lb[chosen] if quarter_of(r["exit_time"]) == test_q]
        oos.extend(fwd)
        b = book(fwd)
        print(f"{test_q:9s} {chosen:10d} {b['trades']:11d} {b['net_rr']:9.2f}")

    result = book(oos)
    passes = bool(result["rw_pf"] and result["rw_pf"] >= 1.10 and result["q"] >= 0.60
                  and result["trades"] >= 80 and result["net_rr"] > 0)
    print(f"\nTRUE OUT-OF-SAMPLE (concatenated forward quarters):")
    print(f"  {result['trades']} trades | net {result['net_rr']}R | rwPF {result['rw_pf']} | "
          f"WR {result['win_rate']}% | q+ {result['q']}")
    print(f"  GATE: {'PASS' if passes else 'FAIL'}")
    print(f"  lookbacks chosen over time: {sorted(set(picks.values()))} "
          f"(stability matters — a parameter that keeps changing is being fitted to noise)")

    print("\n=== entry isolation at each lookback (NEUTRAL exit, no IBS) ===")
    for lb in LOOKBACKS:
        rows: list[dict] = []
        for s, f in frames.items():
            rows.extend(trades(f, s, lb, exit_mode="neutral", neutral_hold=5))
        b = book(rows)
        print(f"  lb={lb:2d}: {b['trades']:4d} trades | rwPF {b['rw_pf']} | exp {b['expectancy']:+.3f} | q+ {b['q']}")

    report = {"generated_at": datetime.now(UTC).isoformat(), "walk_forward": result,
              "passes_gate": passes, "lookback_picks": picks,
              "full_sample_by_lookback": {lb: book(rows) for lb, rows in by_lb.items()}}
    REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nreport -> {REPORT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
