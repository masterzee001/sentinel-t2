"""How far do IBS mean-reversion trades actually travel, in pips?

Operator question (2026-08-12): with the live entry, how many trades made
between 10 and 100 PIPS - and is that reachable inside a day?

PIP DEFINITION, stated because it is the whole question: 1 pip = 1 index
point. The broker's point is 0.1 (symbol_info.point, digits=1), so 10 pips =
100 broker points and 100 pips = 1000 broker points. The smallest target in
point_target_exits.json was 1000 broker points, i.e. 100 pips - the TOP of the
range asked about here. Everything below is new ground.

Entries are the live signal, unchanged (IBS<0.2 at the daily close). Each trade
is walked forward on H1 bars until the live exit (IBS>0.8 or 6 days), and two
things are recorded: the maximum favourable excursion, and how long it took to
first touch each pip level. MFE answers "was the move ever there to take",
which is the only fair way to ask whether a target is reachable - the realized
exit is a different question and is reported separately.

COSTS ARE NOT NETTED OUT of the reach figures, deliberately: they are gross
distances. The cost line is reported alongside, because at these sizes it
dominates - a 5 pip US30 round trip against a 10 pip target is half the target.
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
COSTS = {"US30": 5.0, "NAS100": 3.5, "US500": 0.85, "DE40": 1.75}  # index points = pips
DAILY_BARS = 1400
H1_BARS = 50000
IBS_ENTRY, IBS_EXIT, VOL_WINDOW, MAX_HOLD = 0.2, 0.8, 20, 6
PIP_LEVELS = (10, 20, 30, 40, 50, 75, 100, 150, 200)
REPORT = PROJECT_ROOT / "data" / "reports" / "pip_reach.json"


def trades_with_paths(d1: pd.DataFrame, h1: pd.DataFrame, symbol: str) -> list[dict]:
    """Replay the live book and record each trade's intraday path."""
    highs = [float(v) for v in d1["high"]]
    lows = [float(v) for v in d1["low"]]
    closes = [float(v) for v in d1["close"]]
    times = [pd.Timestamp(v) for v in d1["time"]]

    ht = [pd.Timestamp(v) for v in h1["time"]]
    hh = [float(v) for v in h1["high"]]
    hl = [float(v) for v in h1["low"]]
    hc = [float(v) for v in h1["close"]]
    last_of_date: dict[Any, int] = {}
    for i, t in enumerate(ht):
        last_of_date[t.date()] = i

    out: list[dict] = []
    entry = None
    for i in range(VOL_WINDOW + 3, len(closes)):
        rng = highs[i] - lows[i]
        ibs = (closes[i] - lows[i]) / rng if rng > 0 else 0.5
        if entry is not None:
            held = i - entry["i"]
            if ibs > IBS_EXIT or held >= MAX_HOLD:
                start = last_of_date.get(entry["date"])
                stop_idx = last_of_date.get(times[i].date())
                if start is not None and stop_idx is not None and stop_idx > start:
                    px = entry["price"]
                    mfe = mae = 0.0
                    first_touch: dict[int, int] = {}
                    for j in range(start + 1, stop_idx + 1):
                        up = hh[j] - px
                        dn = px - hl[j]
                        mfe = max(mfe, up)
                        mae = max(mae, dn)
                        for lvl in PIP_LEVELS:
                            if lvl not in first_touch and up >= lvl:
                                first_touch[lvl] = j - start
                    out.append({
                        "symbol": symbol,
                        "exit_date": str(times[i].date()),
                        "mfe": mfe, "mae": mae,
                        "realized": hc[stop_idx] - px,
                        "hours": stop_idx - start,
                        "first_touch": first_touch,
                    })
                entry = None
        if entry is None and ibs < IBS_ENTRY:
            moves = [abs(closes[j] - closes[j - 1]) for j in range(i - VOL_WINDOW, i)]
            if sum(moves) > 0:
                entry = {"i": i, "date": times[i].date(), "price": closes[i]}
    return out


def summarise(trades: list[dict], label: str) -> dict[str, Any]:
    n = len(trades)
    if not n:
        return {}
    rows = []
    for lvl in PIP_LEVELS:
        reached = [t for t in trades if t["mfe"] >= lvl]
        within24 = [t for t in reached if t["first_touch"].get(lvl, 10 ** 9) <= 24]
        hrs = sorted(t["first_touch"][lvl] for t in reached if lvl in t["first_touch"])
        rows.append({
            "pips": lvl,
            "reached": len(reached),
            "reached_pct": round(len(reached) / n * 100, 1),
            "within_24h": len(within24),
            "within_24h_pct": round(len(within24) / n * 100, 1),
            "median_hours_to_touch": hrs[len(hrs) // 2] if hrs else None,
        })
    band = [t for t in trades if 10 <= t["realized"] <= 100]
    band_mfe = [t for t in trades if 10 <= t["mfe"] <= 100]
    return {
        "label": label, "trades": n, "levels": rows,
        "realized_in_10_100_band": len(band),
        "realized_in_10_100_band_pct": round(len(band) / n * 100, 1),
        "mfe_in_10_100_band": len(band_mfe),
        "mfe_in_10_100_band_pct": round(len(band_mfe) / n * 100, 1),
        "median_mfe_pips": round(sorted(t["mfe"] for t in trades)[n // 2], 1),
        "median_mae_pips": round(sorted(t["mae"] for t in trades)[n // 2], 1),
        "median_realized_pips": round(sorted(t["realized"] for t in trades)[n // 2], 1),
    }


def show(s: dict[str, Any]) -> None:
    print(f"\n=== {s['label']}  ({s['trades']} trades) ===")
    print(f"median MFE {s['median_mfe_pips']} pips | median MAE {s['median_mae_pips']} pips "
          f"| median realized {s['median_realized_pips']} pips")
    print(f"{'target':>8s} {'ever reached':>14s} {'reached in 24h':>16s} {'median hrs':>11s}")
    print("-" * 54)
    for r in s["levels"]:
        h = r["median_hours_to_touch"]
        print(f"{r['pips']:6d}p {r['reached']:6d} ({r['reached_pct']:4.1f}%) "
              f"{r['within_24h']:8d} ({r['within_24h_pct']:4.1f}%) {str(h) + 'h' if h else '-':>11s}")
    print(f"realized result landed inside 10-100 pips: {s['realized_in_10_100_band']} "
          f"({s['realized_in_10_100_band_pct']}%)")


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

    all_trades: list[dict] = []
    per_symbol: dict[str, Any] = {}
    for s in SYMBOLS:
        t = trades_with_paths(d1[s], h1[s], s)
        per_symbol[s] = summarise(t, s)
        all_trades.extend(t)

    all_trades.sort(key=lambda t: t["exit_date"])
    half = len(all_trades) // 2
    full = summarise(all_trades, "ALL TRADES")
    fwd = summarise(all_trades[half:], "FORWARD HALF (unseen)")

    print(f"span {str(common)[:10]} -> {str(max(f['time'].max() for f in d1.values()))[:10]}")
    print("1 pip = 1 index point = 10 broker points")
    print("round-trip cost, pips: " + ", ".join(f"{k} {v}" for k, v in COSTS.items()))
    show(full)
    show(fwd)
    print("\nper symbol, 'ever reached' only:")
    print(f"{'symbol':8s} " + " ".join(f"{p:>6d}p" for p in PIP_LEVELS))
    for s in SYMBOLS:
        cells = " ".join(f"{r['reached_pct']:5.1f}%" for r in per_symbol[s]["levels"])
        print(f"{s:8s} {cells}")

    REPORT.write_text(json.dumps({
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "pip_definition": "1 pip = 1 index point = 10 broker points",
        "costs_pips": COSTS,
        "all": full, "forward_half": fwd, "per_symbol": per_symbol,
    }, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nreport -> {REPORT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
