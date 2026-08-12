"""RESEARCH — profit-aware exits (does the book give back accumulated gains?)

User's objection to a pure time stop, and it is correct: exiting on day 6 vs
day 10 says nothing about profit. A trade can be +2 risk units on day 2 and
hand it all back by the exit, and a calendar rule cannot see that.

STAGE 1 quantifies the problem before solving it: for every baseline trade,
track the peak favourable close since entry (MFE) and compare it with what
was actually realised. If give-back is small there is nothing to fix.

STAGE 2 tests exits that react to profit:
  P1 take-profit    exit at the first close >= entry + k*risk_unit
  P2 give-back stop exit when close falls g*risk_unit from the PEAK close
  P3 breakeven      after touching +k, exit if close returns to entry
  P4 combined       best time stop + best profit rule

HONESTY CONSTRAINT — intrabar path: on a daily bar it is unknowable whether
the high or the low came first, so any rule needing both a target and a stop
INSIDE one bar is untestable here. Every rule below is therefore CLOSE-BASED
only. That is conservative: a real intrabar target would fill better, so
these results understate P1/P3 rather than flatter them.

PRIOR WARNING FROM THIS PROJECT'S OWN HISTORY: profit management destroyed
the champion engine (breakeven+partials took it from +12.59R to -26.71R)
because that edge lives in its uncapped runner. Mean reversion has the
opposite shape - high win rate, bounded reversion - so the same medicine may
help here. That is a hypothesis, not an assumption; the holdout decides.
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
REPORT = PROJECT_ROOT / "data" / "reports" / "profit_exits_research.json"


def simulate(frame: pd.DataFrame, symbol: str, max_hold: int = 10, take_profit: float | None = None,
             give_back: float | None = None, breakeven_after: float | None = None) -> list[dict]:
    highs = [float(v) for v in frame["high"]]
    lows = [float(v) for v in frame["low"]]
    closes = [float(v) for v in frame["close"]]
    times = [pd.Timestamp(v) for v in frame["time"]]
    cost = COSTS.get(symbol, 2.0)
    trades: list[dict] = []
    entry = None
    for i in range(VOL_WINDOW + 3, len(closes)):
        rng = highs[i] - lows[i]
        ibs = (closes[i] - lows[i]) / rng if rng > 0 else 0.5
        if entry is not None:
            gain = (closes[i] - closes[entry["i"]]) / entry["ru"]  # in risk units, on closes
            entry["peak"] = max(entry["peak"], gain)
            held = i - entry["i"]
            reason = None
            if take_profit is not None and gain >= take_profit:
                reason = "take_profit"
            elif give_back is not None and entry["peak"] > 0 and gain <= entry["peak"] - give_back:
                reason = "give_back"
            elif breakeven_after is not None and entry["peak"] >= breakeven_after and gain <= 0:
                reason = "breakeven"
            elif ibs > IBS_EXIT:
                reason = "ibs"
            elif held >= max_hold:
                reason = "timeout"
            if reason:
                days = max((times[i] - times[entry["i"]]).days, 1)
                pnl = closes[i] - closes[entry["i"]] - cost - FINANCING_PER_DAY * closes[entry["i"]] * days
                trades.append({"symbol": symbol, "exit_time": str(times[i]), "hold": held,
                               "rr": pnl / entry["ru"], "peak_r": entry["peak"], "reason": reason})
                entry = None
        if entry is None and ibs < IBS_ENTRY:
            moves = [abs(closes[j] - closes[j - 1]) for j in range(i - VOL_WINDOW, i)]
            ru = sum(moves) / len(moves) if moves else 0.0
            if ru > 0:
                entry = {"i": i, "ru": ru, "peak": 0.0}
    return trades


def book(trades: list[dict]) -> dict[str, Any]:
    rrs = [t["rr"] for t in trades]
    if not rrs:
        return {"trades": 0, "net_rr": 0.0}
    wins = [r for r in rrs if r > 0]
    gross_loss = abs(sum(r for r in rrs if r < 0))
    q: dict[str, float] = defaultdict(float)
    for t in trades:
        s = pd.Timestamp(t["exit_time"])
        q[f"{s.year}-Q{(s.month - 1) // 3 + 1}"] += t["rr"]
    active = [v for v in q.values() if v != 0]
    return {"trades": len(rrs), "win_rate": round(len(wins) / len(rrs) * 100, 1),
            "net_rr": round(sum(rrs), 2),
            "rw_pf": round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
            "avg_hold": round(sum(t["hold"] for t in trades) / len(trades), 1),
            "positive_quarter_share": round(sum(1 for v in active if v > 0) / len(active), 2) if active else 0.0}


def run(frames: dict[str, pd.DataFrame], **kw) -> tuple[dict, list[dict]]:
    trades: list[dict] = []
    for s, f in frames.items():
        trades.extend(simulate(f, s, **kw))
    return book(trades), trades


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
    finally:
        connector.shutdown()

    report: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "status": "RESEARCH_ONLY"}
    base, base_trades = run(frames)
    base_d, _ = run(disc)
    base_h, _ = run(hold)

    # ---- STAGE 1: is there give-back worth chasing? -----------------------
    gave_back = [t for t in base_trades if t["peak_r"] > 0.5 and t["rr"] < t["peak_r"] - 0.5]
    total_peak = sum(max(t["peak_r"], 0) for t in base_trades)
    realised = sum(t["rr"] for t in base_trades)
    print("=== STAGE 1: how much profit does the book actually hand back? ===")
    print(f"  trades {len(base_trades)} | realised {realised:.1f}R | sum of PEAK unrealised {total_peak:.1f}R")
    print(f"  capture rate: {realised / total_peak * 100:.1f}% of peak profit is kept")
    print(f"  trades giving back >0.5R from their peak: {len(gave_back)} ({len(gave_back)/len(base_trades)*100:.0f}%)")
    print(f"  average give-back on those: {sum(t['peak_r'] - t['rr'] for t in gave_back)/max(len(gave_back),1):.2f}R\n")
    report["give_back"] = {"realised_r": round(realised, 2), "peak_r": round(total_peak, 2),
                           "capture_rate_pct": round(realised / total_peak * 100, 1),
                           "trades_giving_back": len(gave_back)}

    # ---- STAGE 2: profit-aware exits --------------------------------------
    variants: dict[str, dict] = {}
    for k in (0.5, 1.0, 1.5, 2.0):
        variants[f"P1_take_profit_{k}u"] = {"take_profit": k}
    for g in (0.5, 1.0, 1.5):
        variants[f"P2_give_back_{g}u"] = {"give_back": g}
    for k in (0.5, 1.0):
        variants[f"P3_breakeven_after_{k}u"] = {"breakeven_after": k}
    variants["P4_tp1.0_and_6d"] = {"take_profit": 1.0, "max_hold": 6}
    variants["P4_giveback1.0_and_6d"] = {"give_back": 1.0, "max_hold": 6}
    variants["P4_tp1.5_and_6d"] = {"take_profit": 1.5, "max_hold": 6}

    print(f"{'variant':26s} {'trades':>7s} {'netR':>8s} {'rwPF':>6s} {'WR%':>6s} {'hold':>5s} {'q+':>5s} {'discR':>8s} {'holdR':>8s}")
    print("-" * 90)
    print(f"{'BASELINE (ibs/10d)':26s} {base['trades']:7d} {base['net_rr']:8.1f} {str(base['rw_pf']):>6s} "
          f"{base['win_rate']:6.1f} {base['avg_hold']:5.1f} {base['positive_quarter_share']:5.2f} "
          f"{base_d['net_rr']:8.1f} {base_h['net_rr']:8.1f}")
    results: dict[str, Any] = {}
    for name, kw in variants.items():
        full, _ = run(frames, **kw)
        d, _ = run(disc, **kw)
        h, _ = run(hold, **kw)
        results[name] = {"full": full, "discovery": d, "holdout": h, "params": kw}
        print(f"{name:26s} {full['trades']:7d} {full['net_rr']:8.1f} {str(full['rw_pf']):>6s} "
              f"{full['win_rate']:6.1f} {full['avg_hold']:5.1f} {full['positive_quarter_share']:5.2f} "
              f"{d['net_rr']:8.1f} {h['net_rr']:8.1f}")
    report["baseline"] = {"full": base, "discovery": base_d, "holdout": base_h}
    report["results"] = results

    winner = max(results, key=lambda n: results[n]["discovery"]["net_rr"])
    w = results[winner]
    validates = w["holdout"]["net_rr"] > base_h["net_rr"] and w["discovery"]["net_rr"] > base_d["net_rr"]
    report["selection"] = {"chosen_on_discovery": winner,
                           "discovery": w["discovery"]["net_rr"], "baseline_discovery": base_d["net_rr"],
                           "holdout": w["holdout"]["net_rr"], "baseline_holdout": base_h["net_rr"],
                           "VALIDATES_ON_HOLDOUT": validates,
                           "verdict": "PROMOTABLE" if validates else "REJECTED - does not survive the holdout"}
    print(f"\nchosen on discovery: {winner}")
    print(f"  discovery {w['discovery']['net_rr']}R vs baseline {base_d['net_rr']}R")
    print(f"  HOLDOUT   {w['holdout']['net_rr']}R vs baseline {base_h['net_rr']}R")
    print(f"  VERDICT: {report['selection']['verdict']}")
    REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"report -> {REPORT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
