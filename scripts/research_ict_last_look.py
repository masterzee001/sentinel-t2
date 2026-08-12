"""RESEARCH — a final, honest look for an ICT edge (pre-registered).

Context: the champion (a breakout proxy wearing ICT vocabulary) has been
retired for failing its own gate. An earlier attempt at AUTHENTIC ICT
detection — sweep -> market structure shift -> fair value gap — scored -4.75R,
worse than the crude proxy. This is the last look, testing the ICT constructs
that were never isolated, on DAILY bars where our costs are survivable.

Why daily: ICT is taught intraday, but a 5-point US30 round trip against a
10-30 point intraday target is 17-50% cost drag — already measured as fatal
for scalping and intraday momentum. On daily bars the same round trip is ~1%
of a 400-point range. If an ICT effect is real it should be detectable at the
horizon where costs do not eat it. If it only "works" intraday, it is not
distinguishable from paying the spread.

Hypotheses (each precisely coded, no judgement calls):
  I1 sweep_pdl    prior-day low is taken out intrabar, then the bar CLOSES
                  back above it -> long (liquidity sweep / judas swing)
  I2 sweep_pwl    same against the prior WEEK's low
  I3 fvg_fill     bullish fair value gap (low[i] > high[i-2]) then a later
                  close back inside the gap -> long
  I4 order_block  last down-close bar before a >=1.5 risk-unit up-move;
                  buy the first later close that returns to its range
  I5 ote          after a swing low then a rally, buy a 62-79% retracement

ALL exit identically (IBS>0.8 or 10 days) so the ENTRY is what is measured.
Each is also tested for OVERLAP with the IBS book: an "edge" that only fires
when IBS<0.2 already fired is not a new edge, it is a worse-labelled IBS.
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
IBS_EXIT, MAX_HOLD, VOL_WINDOW = 0.8, 10, 20
FINANCING_PER_DAY = 0.0002
GATE = {"min_rwpf": 1.10, "min_q": 0.60, "min_trades": 80}
REPORT = PROJECT_ROOT / "data" / "reports" / "ict_last_look.json"


def entries_for(kind: str, h, l, c, o, ru_at, i: int) -> bool:
    """Causal entry test at bar i. Only bars <= i are ever read."""
    if kind == "sweep_pdl":
        return l[i] < l[i - 1] and c[i] > l[i - 1]
    if kind == "sweep_pwl":
        lo5 = min(l[i - 5:i])
        return l[i] < lo5 and c[i] > lo5
    if kind == "fvg_fill":
        # A bullish gap formed at i-1 (low[i-1] > high[i-3]); price returns into it now.
        if i < 4:
            return False
        gap_lo, gap_hi = h[i - 3], l[i - 1]
        return gap_hi > gap_lo and gap_lo <= c[i] <= gap_hi and c[i] < c[i - 1]
    if kind == "order_block":
        ru = ru_at(i)
        if ru <= 0 or i < 6:
            return False
        for k in range(i - 5, i - 1):
            if c[k] < o[k] and (c[k + 1] - c[k]) >= 1.5 * ru:       # down bar then displacement
                return l[i] <= h[k] and c[i] > l[k]                  # price returns to its range
        return False
    if kind == "ote":
        if i < 12:
            return False
        swing_lo = min(l[i - 10:i - 1]); swing_hi = max(h[i - 10:i - 1])
        if swing_hi <= swing_lo:
            return False
        retr = (swing_hi - c[i]) / (swing_hi - swing_lo)
        return 0.62 <= retr <= 0.79 and c[i] < c[i - 1]
    return False


def run(kind: str, frame: pd.DataFrame, symbol: str) -> list[dict]:
    h = [float(v) for v in frame["high"]]; l = [float(v) for v in frame["low"]]
    c = [float(v) for v in frame["close"]]; o = [float(v) for v in frame["open"]]
    t = [pd.Timestamp(v) for v in frame["time"]]
    cost = COSTS.get(symbol, 2.0)

    def ru_at(i: int) -> float:
        mv = [abs(c[j] - c[j - 1]) for j in range(i - VOL_WINDOW, i)]
        return sum(mv) / len(mv) if mv else 0.0

    out: list[dict] = []
    entry = None
    for i in range(VOL_WINDOW + 13, len(c)):
        rng = h[i] - l[i]
        ibs = (c[i] - l[i]) / rng if rng > 0 else 0.5
        if entry is not None:
            if ibs > IBS_EXIT or i - entry["i"] >= MAX_HOLD:
                days = max((t[i] - t[entry["i"]]).days, 1)
                pnl = c[i] - c[entry["i"]] - cost - FINANCING_PER_DAY * c[entry["i"]] * days
                out.append({"symbol": symbol, "exit_time": str(t[i]), "entry_time": str(t[entry["i"]]),
                            "rr": pnl / entry["ru"], "ibs_at_entry": entry["ibs"]})
                entry = None
            continue
        if entries_for(kind, h, l, c, o, ru_at, i):
            ru = ru_at(i)
            if ru > 0:
                entry = {"i": i, "ru": ru, "ibs": ibs}
    return out


def book(rows: list[dict]) -> dict[str, Any]:
    if not rows:
        return {"trades": 0, "net_rr": 0.0, "rw_pf": None, "positive_quarter_share": 0.0, "passes_gate": False}
    rrs = [r["rr"] for r in rows]
    wins = [r for r in rrs if r > 0]; gl = abs(sum(r for r in rrs if r < 0))
    q: dict[str, float] = defaultdict(float)
    for r in rows:
        s = pd.Timestamp(r["exit_time"]); q[f"{s.year}-Q{(s.month - 1) // 3 + 1}"] += r["rr"]
    act = [v for v in q.values() if v != 0]
    pf = round(sum(wins) / gl, 2) if gl else None
    share = round(sum(1 for v in act if v > 0) / len(act), 2) if act else 0.0
    return {"trades": len(rrs), "win_rate": round(len(wins) / len(rrs) * 100, 1),
            "net_rr": round(sum(rrs), 2), "rw_pf": pf, "expectancy": round(sum(rrs) / len(rrs), 3),
            "positive_quarter_share": share,
            "overlap_with_ibs_pct": round(sum(1 for r in rows if r["ibs_at_entry"] < 0.2) / len(rows) * 100, 1),
            "passes_gate": bool(pf and pf >= GATE["min_rwpf"] and share >= GATE["min_q"]
                                and len(rrs) >= GATE["min_trades"] and sum(rrs) > 0)}


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

    report: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "status": "RESEARCH_ONLY", "gate": GATE}
    print(f"{'ICT construct':16s} {'trades':>7s} {'WR%':>6s} {'netR':>8s} {'rwPF':>6s} {'exp':>7s} {'q+':>5s} {'IBS overlap':>12s}  verdict")
    print("-" * 96)
    results = {}
    for kind in ("sweep_pdl", "sweep_pwl", "fvg_fill", "order_block", "ote"):
        rows: list[dict] = []
        for s, f in frames.items():
            rows.extend(run(kind, f, s))
        b = book(rows)
        results[kind] = b
        v = "PASS" if b["passes_gate"] else "fails gate"
        if b["passes_gate"] and b.get("overlap_with_ibs_pct", 0) > 70:
            v = "PASS but is mostly IBS"
        print(f"{kind:16s} {b['trades']:7d} {b.get('win_rate',0):6.1f} {b['net_rr']:8.1f} {str(b['rw_pf']):>6s} "
              f"{b.get('expectancy',0):+7.3f} {b['positive_quarter_share']:5.2f} {b.get('overlap_with_ibs_pct',0):11.1f}%  {v}")
    report["results"] = results
    passed = [k for k, v in results.items() if v["passes_gate"]]
    report["verdict"] = ("NO ICT EDGE FOUND - none of the five constructs clears the gate. Combined with the "
                         "retired champion and the earlier -4.75R authentic sweep/MSS/FVG detector, ICT is "
                         "closed as a research direction for this book."
                         if not passed else f"passes gate: {passed} - requires holdout validation before belief")
    print(f"\n{report['verdict']}")
    REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"report -> {REPORT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
