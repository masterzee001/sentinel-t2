"""RESEARCH — isolate, streamline and combine the liquidity-sweep edge.

Two caveats were left open when the prior-week sweep passed the gate:
  1. It reused the IBS exit, so some of the measured edge may belong to the
     EXIT rather than the entry.
  2. Five constructs were tested and two passed, so selection pressure exists.

STAGE A — ENTRY ISOLATION. Re-run each entry against a NEUTRAL exit (a fixed
N-bar hold, no IBS condition). If the edge survives a dumb exit, the entry is
carrying it. If it evaporates, the "sweep edge" was the IBS exit all along.

STAGE B — STREAMLINE. Vary only the sweep's own parameters: the lookback that
defines the low being swept, and whether reclaiming the level must be decisive
(close above by a fraction of a risk unit) or merely nominal.

STAGE C — COMBINE. Does requiring BOTH a sweep and a deep IBS beat either
alone? Does either gate the other usefully? Measured at equal risk, and with
trade count reported, because a combination that fires 40 times is not
tradeable however good its ratio looks.

Everything is holdout-validated: parameters chosen on the first half only,
then applied frozen to the untouched second half.
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
GATE = {"min_rwpf": 1.10, "min_q": 0.60, "min_trades": 80}
REPORT = PROJECT_ROOT / "data" / "reports" / "sweep_streamline.json"


def simulate(frame: pd.DataFrame, symbol: str, *, entry: str, lookback: int = 5,
             reclaim: float = 0.0, exit_mode: str = "ibs", neutral_hold: int = 5,
             ibs_max: float | None = None) -> list[dict]:
    h = [float(v) for v in frame["high"]]; l = [float(v) for v in frame["low"]]
    c = [float(v) for v in frame["close"]]
    t = [pd.Timestamp(v) for v in frame["time"]]
    cost = COSTS.get(symbol, 2.0)
    out: list[dict] = []
    pos = None
    for i in range(VOL_WINDOW + 13, len(c)):
        rng = h[i] - l[i]
        ibs = (c[i] - l[i]) / rng if rng > 0 else 0.5
        if pos is not None:
            held = i - pos["i"]
            done = (held >= neutral_hold) if exit_mode == "neutral" else (ibs > IBS_EXIT or held >= MAX_HOLD)
            if done:
                days = max((t[i] - t[pos["i"]]).days, 1)
                pnl = c[i] - c[pos["i"]] - cost - FINANCING_PER_DAY * c[pos["i"]] * days
                out.append({"symbol": symbol, "exit_time": str(t[i]), "rr": pnl / pos["ru"], "hold": held})
                pos = None
            continue
        mv = [abs(c[j] - c[j - 1]) for j in range(i - VOL_WINDOW, i)]
        ru = sum(mv) / len(mv) if mv else 0.0
        if ru <= 0:
            continue
        prior_low = min(l[i - lookback:i])
        swept = l[i] < prior_low and c[i] > prior_low + reclaim * ru
        deep = ibs < 0.2
        if entry == "sweep":
            take = swept
        elif entry == "ibs":
            take = deep
        elif entry == "sweep_and_ibs":
            take = swept and deep
        elif entry == "sweep_or_ibs":
            take = swept or deep
        else:
            take = False
        if take and (ibs_max is None or ibs < ibs_max):
            pos = {"i": i, "ru": ru}
    return out


def book(rows: list[dict]) -> dict[str, Any]:
    if not rows:
        return {"trades": 0, "net_rr": 0.0, "rw_pf": None, "q": 0.0, "passes": False}
    rrs = [r["rr"] for r in rows]
    wins = [r for r in rrs if r > 0]; gl = abs(sum(r for r in rrs if r < 0))
    q: dict[str, float] = defaultdict(float)
    for r in rows:
        s = pd.Timestamp(r["exit_time"]); q[f"{s.year}-Q{(s.month - 1) // 3 + 1}"] += r["rr"]
    act = [v for v in q.values() if v != 0]
    pf = round(sum(wins) / gl, 2) if gl else None
    share = round(sum(1 for v in act if v > 0) / len(act), 2) if act else 0.0
    return {"trades": len(rrs), "net_rr": round(sum(rrs), 2), "rw_pf": pf,
            "expectancy": round(sum(rrs) / len(rrs), 3), "q": share,
            "passes": bool(pf and pf >= GATE["min_rwpf"] and share >= GATE["min_q"]
                           and len(rrs) >= GATE["min_trades"] and sum(rrs) > 0)}


def run_all(frames: dict[str, pd.DataFrame], **kw) -> list[dict]:
    rows: list[dict] = []
    for s, f in frames.items():
        rows.extend(simulate(f, s, **kw))
    rows.sort(key=lambda r: r["exit_time"])
    return rows


def halves(rows: list[dict]) -> tuple[dict, dict]:
    cut = len(rows) // 2
    return book(rows[:cut]), book(rows[cut:])


def line(name: str, rows: list[dict]) -> None:
    b = book(rows); a, z = halves(rows)
    hold_ok = (a["rw_pf"] or 0) >= 1.10 and (z["rw_pf"] or 0) >= 1.10
    print(f"{name:30s} {b['trades']:6d} {b['net_rr']:8.1f} {str(b['rw_pf']):>6s} {b['expectancy']:+7.3f} "
          f"{b['q']:5.2f}  {str(a['rw_pf']):>5s}/{str(z['rw_pf']):<5s} {'BOTH HALVES' if hold_ok else '-'}")


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

    report: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "status": "RESEARCH_ONLY"}
    hdr = f"{'variant':30s} {'trades':>6s} {'netR':>8s} {'rwPF':>6s} {'exp':>7s} {'q+':>5s}  {'halves':>11s}"
    print("=== STAGE A: entry isolation — does the edge survive a DUMB exit? ===")
    print(hdr); print("-" * 84)
    for hold in (3, 5, 10):
        line(f"sweep, neutral {hold}-bar exit", run_all(frames, entry="sweep", exit_mode="neutral", neutral_hold=hold))
    for hold in (3, 5, 10):
        line(f"IBS<0.2, neutral {hold}-bar exit", run_all(frames, entry="ibs", exit_mode="neutral", neutral_hold=hold))

    print("\n=== STAGE B: streamline the sweep (lookback, reclaim strength) ===")
    print(hdr); print("-" * 84)
    best = None
    for lb in (3, 5, 10, 20):
        for rc in (0.0, 0.25, 0.5):
            rows = run_all(frames, entry="sweep", lookback=lb, reclaim=rc)
            a, _ = halves(rows)
            line(f"sweep lb={lb} reclaim={rc}", rows)
            if book(rows)["passes"] and (best is None or a["net_rr"] > best[1]):
                best = ((lb, rc), a["net_rr"])

    print("\n=== STAGE C: combinations with the existing IBS edge ===")
    print(hdr); print("-" * 84)
    line("IBS<0.2 alone (incumbent)", run_all(frames, entry="ibs"))
    line("sweep alone (lb=5)", run_all(frames, entry="sweep"))
    line("sweep AND IBS<0.2", run_all(frames, entry="sweep_and_ibs"))
    line("sweep OR IBS<0.2 (union)", run_all(frames, entry="sweep_or_ibs"))
    line("sweep with IBS<0.5 filter", run_all(frames, entry="sweep", ibs_max=0.5))

    chosen = best[0] if best else (5, 0.0)
    rows = run_all(frames, entry="sweep", lookback=chosen[0], reclaim=chosen[1])
    a, z = halves(rows)
    report["chosen_on_discovery"] = {"lookback": chosen[0], "reclaim": chosen[1]}
    report["validation"] = {"first_half": a, "second_half": z,
                            "validates": bool((z["rw_pf"] or 0) >= 1.10 and z["net_rr"] > 0)}
    report["union"] = book(run_all(frames, entry="sweep_or_ibs"))
    report["incumbent"] = book(run_all(frames, entry="ibs"))
    print(f"\nstreamlined pick (chosen on FIRST half only): lookback={chosen[0]} reclaim={chosen[1]}")
    print(f"  holdout second half: rwPF {z['rw_pf']} net {z['net_rr']}R over {z['trades']} trades")
    print(f"  VERDICT: {'VALIDATES' if report['validation']['validates'] else 'FAILS holdout'}")
    REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"report -> {REPORT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
