"""RISK CORRECTNESS — the correlation-corrected crash envelope.

Not a search for edge. This exists to answer one question honestly before real
money: how bad is the worst plausible day, given that the book holds LONG
positions in indices correlating 0.93-0.94, sends NO stop loss, and now sizes
3x into the deepest dips — which are exactly the days markets are falling?

The documented worst legitimate day (~16%) assumed concurrent positions were
meaningfully independent. They are not. This measures:
  1. the real correlation matrix of the traded universe
  2. the historical distribution of CONCURRENT tilted exposure
  3. the worst single-day loss actually produced, tilted vs flat
  4. what a concurrency cap would cost in return and save in tail

Expected outcome, stated up front: this REDUCES expected return. That is the
correct trade before real capital.
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
IBS_ENTRY, IBS_EXIT, MAX_HOLD, VOL_WINDOW = 0.2, 0.8, 10, 20
DISASTER_UNITS = 3.0
RISK_PER_UNIT = 1.0          # live: 3% over a 3-unit move
REPORT = PROJECT_ROOT / "data" / "reports" / "crash_envelope.json"


def tilt(ibs: float) -> float:
    return 3.0 if ibs < 0.05 else (1.5 if ibs < 0.10 else 0.75)


def build(frames: dict[str, pd.DataFrame], cap: int | None = None, use_tilt: bool = True):
    """Walk the calendar holding real positions; return daily equity moves in % of equity."""
    idx = {s: {str(pd.Timestamp(t).date()): i for i, t in enumerate(f["time"])} for s, f in frames.items()}
    days = sorted({d for m in idx.values() for d in m})
    closes = {s: [float(v) for v in f["close"]] for s, f in frames.items()}
    highs = {s: [float(v) for v in f["high"]] for s, f in frames.items()}
    lows = {s: [float(v) for v in f["low"]] for s, f in frames.items()}
    open_pos: dict[str, dict] = {}
    daily: list[dict] = []
    for day in days:
        move = 0.0
        # mark open positions on today's close
        for s, p in list(open_pos.items()):
            i = idx[s].get(day)
            if i is None:
                continue
            prev = closes[s][i - 1] if i > 0 else p["entry"]
            move += p["risk_pct"] * (closes[s][i] - prev) / (DISASTER_UNITS * p["ru"])
        # exits
        for s, p in list(open_pos.items()):
            i = idx[s].get(day)
            if i is None:
                continue
            rng = highs[s][i] - lows[s][i]
            ibs = (closes[s][i] - lows[s][i]) / rng if rng > 0 else 0.5
            if ibs > IBS_EXIT or (i - p["i"]) >= MAX_HOLD:
                del open_pos[s]
        # entries
        for s in SYMBOLS:
            i = idx[s].get(day)
            if i is None or s in open_pos or i < VOL_WINDOW + 2:
                continue
            if cap is not None and len(open_pos) >= cap:
                continue
            rng = highs[s][i] - lows[s][i]
            ibs = (closes[s][i] - lows[s][i]) / rng if rng > 0 else 0.5
            if ibs >= IBS_ENTRY:
                continue
            mv = [abs(closes[s][j] - closes[s][j - 1]) for j in range(i - VOL_WINDOW, i)]
            ru = sum(mv) / len(mv) if mv else 0.0
            if ru <= 0:
                continue
            m = tilt(ibs) if use_tilt else 1.0
            open_pos[s] = {"i": i, "ru": ru, "entry": closes[s][i],
                           "risk_pct": RISK_PER_UNIT * DISASTER_UNITS * m}
        daily.append({"day": day, "move_pct": move, "open": len(open_pos),
                      "exposure_pct": sum(p["risk_pct"] for p in open_pos.values())})
    return daily


def summarise(daily: list[dict]) -> dict[str, Any]:
    moves = [d["move_pct"] for d in daily]
    worst = min(moves) if moves else 0.0
    worst_day = min(daily, key=lambda d: d["move_pct"])["day"] if daily else None
    eq = 100.0; peak = 100.0; dd = 0.0
    for m in moves:
        eq *= (1 + m / 100.0); peak = max(peak, eq); dd = max(dd, (peak - eq) / peak * 100)
    return {"worst_day_pct": round(worst, 2), "worst_day": worst_day,
            "max_exposure_pct": round(max((d["exposure_pct"] for d in daily), default=0), 1),
            "max_concurrent": max((d["open"] for d in daily), default=0),
            "avg_exposure_pct": round(sum(d["exposure_pct"] for d in daily) / max(len(daily), 1), 1),
            "max_drawdown_pct": round(dd, 1),
            "final_equity_index": round(eq, 1)}


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

    print("=== 1. how correlated is the traded universe, really? ===")
    rets = {s: f.set_index("time")["close"].astype(float).pct_change() for s, f in frames.items()}
    corr = pd.DataFrame(rets).corr()
    print(corr.round(3).to_string())
    worst_pair = max(((a, b, corr.loc[a, b]) for a in SYMBOLS for b in SYMBOLS if a < b), key=lambda x: x[2])
    print(f"\n  most correlated pair: {worst_pair[0]}/{worst_pair[1]} at {worst_pair[2]:.3f}")
    print(f"  average off-diagonal correlation: {(corr.values.sum() - len(SYMBOLS)) / (len(SYMBOLS)**2 - len(SYMBOLS)):.3f}")
    print("  -> concurrent positions are NOT independent; treating them as such understates the tail\n")

    print("=== 2/3. exposure and the worst day actually produced ===")
    print(f"{'configuration':34s} {'maxOpen':>8s} {'maxExp%':>8s} {'avgExp%':>8s} {'worstDay%':>10s} {'maxDD%':>8s} {'equity':>8s}")
    print("-" * 90)
    rows = {}
    for label, kw in (("flat sizing, no cap", {"use_tilt": False}),
                      ("depth tilt, no cap (LIVE NOW)", {"use_tilt": True}),
                      ("depth tilt, cap 3 concurrent", {"use_tilt": True, "cap": 3}),
                      ("depth tilt, cap 2 concurrent", {"use_tilt": True, "cap": 2})):
        s = summarise(build(frames, **kw))
        rows[label] = s
        print(f"{label:34s} {s['max_concurrent']:8d} {s['max_exposure_pct']:8.1f} {s['avg_exposure_pct']:8.1f} "
              f"{s['worst_day_pct']:10.2f} {s['max_drawdown_pct']:8.1f} {s['final_equity_index']:8.1f}")

    live = rows["depth tilt, no cap (LIVE NOW)"]
    documented = 16.0
    report = {"generated_at": datetime.now(UTC).isoformat(), "correlations": corr.round(3).to_dict(),
              "configurations": rows,
              "documented_worst_day_assumption": documented,
              "verdict": (f"The live configuration's worst historical day is {live['worst_day_pct']}% "
                          f"(on {live['worst_day']}) against a documented assumption of -{documented}%, at a peak "
                          f"exposure of {live['max_exposure_pct']}% of equity across {live['max_concurrent']} "
                          f"concurrent positions in instruments correlating ~0.93. The 18% daily tripwire and the "
                          f"50%/60% drawdown blocks must be read against THESE numbers, not the old assumption.")}
    print(f"\n{report['verdict']}")
    REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"report -> {REPORT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
