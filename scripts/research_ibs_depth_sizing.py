"""RESEARCH — do deeper dips deserve bigger size? (pre-registered)

Claim to test: entry depth predicts trade quality. Trades entered at IBS<0.05
were reported at +0.6985R / rwPF 2.19 against the full population's +0.3386R /
rwPF 1.49, monotone in the threshold and stable across symbols and years.

STAGE 1 verifies the effect exists and is monotone, per symbol and per half.
STAGE 2 tests sizing tilts at the ACCOUNT level, which is where this could
die: deep IBS prints during selloffs, so sizing up on depth concentrates risk
precisely when all four correlated indices fall together. That is exactly the
failure mode that killed the slot-rule relaxation (per-trade metrics improved,
account drawdown grew faster than return), so the verdict here is decided on
return per unit of drawdown, never on rwPF alone.

Discipline: tilt chosen on the DISCOVERY half, validated on an untouched
HOLDOUT half. Equal-risk comparison included, because a tilt that simply
holds more risk would "win" trivially.
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
FINANCING_PER_DAY = 0.0002
REPORT = PROJECT_ROOT / "data" / "reports" / "ibs_depth_sizing.json"

# Pre-registered tilts. Multiplier applied to the trade's risk, by entry IBS.
TILTS = {
    "flat_baseline":  lambda ibs: 1.0,
    "mild_2step":     lambda ibs: 1.5 if ibs < 0.10 else 1.0,
    "stepped_3":      lambda ibs: 2.0 if ibs < 0.05 else (1.5 if ibs < 0.10 else 1.0),
    "linear":         lambda ibs: 1.0 + (0.20 - ibs) / 0.20,          # 1.0 at 0.20 -> 2.0 at 0.0
    "aggressive":     lambda ibs: 3.0 if ibs < 0.05 else (1.5 if ibs < 0.10 else 0.75),
}


def trades_for(frame: pd.DataFrame, symbol: str) -> list[dict]:
    highs = [float(v) for v in frame["high"]]
    lows = [float(v) for v in frame["low"]]
    closes = [float(v) for v in frame["close"]]
    times = [pd.Timestamp(v) for v in frame["time"]]
    cost = COSTS.get(symbol, 2.0)
    out: list[dict] = []
    entry = None
    for i in range(VOL_WINDOW + 3, len(closes)):
        rng = highs[i] - lows[i]
        ibs = (closes[i] - lows[i]) / rng if rng > 0 else 0.5
        if entry is None:
            if ibs < IBS_ENTRY:
                moves = [abs(closes[j] - closes[j - 1]) for j in range(i - VOL_WINDOW, i)]
                ru = sum(moves) / len(moves) if moves else 0.0
                if ru > 0:
                    entry = {"i": i, "ru": ru, "ibs": ibs}
            continue
        if ibs > IBS_EXIT or i - entry["i"] >= MAX_HOLD:
            days = max((times[i] - times[entry["i"]]).days, 1)
            pnl = closes[i] - closes[entry["i"]] - cost - FINANCING_PER_DAY * closes[entry["i"]] * days
            out.append({"symbol": symbol, "entry_time": str(times[entry["i"]]),
                        "exit_time": str(times[i]), "entry_ibs": entry["ibs"],
                        "rr": pnl / entry["ru"], "hold": i - entry["i"],
                        "entry_price": round(closes[entry["i"]], 5),
                        "risk_unit": round(entry["ru"], 5), "pnl_points": round(pnl, 5)})
            entry = None
    return out


def book(rows: list[dict], weight=lambda r: 1.0) -> dict[str, Any]:
    if not rows:
        return {"trades": 0}
    wr = [(r["rr"] * weight(r), weight(r), r) for r in rows]
    rrs = [a for a, _, _ in wr]
    wins = [a for a in rrs if a > 0]
    gl = abs(sum(a for a in rrs if a < 0))
    q: dict[str, float] = defaultdict(float)
    for a, _, r in wr:
        s = pd.Timestamp(r["exit_time"])
        q[f"{s.year}-Q{(s.month - 1) // 3 + 1}"] += a
    act = [v for v in q.values() if v != 0]
    avg_size = sum(w for _, w, _ in wr) / len(wr)
    net = sum(rrs)
    return {"trades": len(rrs), "net_rr": round(net, 2),
            "rw_pf": round(sum(wins) / gl, 2) if gl else None,
            "mean_r": round(net / len(rrs), 4),
            "avg_size": round(avg_size, 3),
            "net_per_unit_risk": round(net / avg_size, 2),
            "positive_quarter_share": round(sum(1 for v in act if v > 0) / len(act), 2) if act else 0.0}


def max_dd(rows: list[dict], weight=lambda r: 1.0) -> float:
    """Path drawdown in R, trades ordered by exit."""
    eq = 0.0; peak = 0.0; dd = 0.0
    for r in sorted(rows, key=lambda x: x["exit_time"]):
        eq += r["rr"] * weight(r)
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return round(dd, 2)


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

    rows: list[dict] = []
    for s, f in frames.items():
        rows.extend(trades_for(f, s))
    rows.sort(key=lambda r: r["exit_time"])
    cut = len(rows) // 2
    disc, hold = rows[:cut], rows[cut:]
    report: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "status": "RESEARCH_ONLY"}

    print("=== STAGE 1: does entry depth predict quality? ===")
    buckets = [(0.00, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.20)]
    stage1 = {}
    for lo, hi in buckets:
        sub = [r for r in rows if lo <= r["entry_ibs"] < hi]
        b = book(sub)
        stage1[f"IBS_{lo:.2f}-{hi:.2f}"] = b
        print(f"  IBS {lo:.2f}-{hi:.2f}: n={b['trades']:4d} meanR={b['mean_r']:+.4f} rwPF={b['rw_pf']} q+={b['positive_quarter_share']}")
    print("\n  per symbol, deepest bucket (IBS<0.05) vs the rest:")
    for s in SYMBOLS:
        deep = book([r for r in rows if r["symbol"] == s and r["entry_ibs"] < 0.05])
        rest = book([r for r in rows if r["symbol"] == s and r["entry_ibs"] >= 0.05])
        print(f"    {s:7s} deep n={deep.get('trades',0):3d} meanR={deep.get('mean_r',0):+.3f} | rest n={rest.get('trades',0):3d} meanR={rest.get('mean_r',0):+.3f}")
    print("\n  stability across halves (IBS<0.05):")
    for label, part in (("discovery", disc), ("holdout", hold)):
        d = book([r for r in part if r["entry_ibs"] < 0.05])
        o = book([r for r in part if r["entry_ibs"] >= 0.05])
        print(f"    {label:9s} deep meanR={d.get('mean_r',0):+.3f} (n={d.get('trades',0)}) | rest meanR={o.get('mean_r',0):+.3f} (n={o.get('trades',0)})")
    report["stage1_buckets"] = stage1

    print(f"\n=== STAGE 2: sizing tilts — judged on RETURN PER UNIT OF DRAWDOWN ===")
    print(f"{'tilt':16s} {'netR':>8s} {'avgSize':>8s} {'R/risk':>8s} {'maxDD_R':>8s} {'R/DD':>7s} {'rwPF':>6s} {'discR':>8s} {'holdR':>8s}")
    print("-" * 86)
    results = {}
    for name, fn in TILTS.items():
        w = lambda r, fn=fn: fn(r["entry_ibs"])
        full = book(rows, w); dd = max_dd(rows, w)
        d = book(disc, w); h = book(hold, w)
        ratio = round(full["net_rr"] / dd, 2) if dd > 0 else None
        results[name] = {"full": full, "max_dd_r": dd, "return_per_dd": ratio,
                         "discovery": d, "holdout": h}
        print(f"{name:16s} {full['net_rr']:8.1f} {full['avg_size']:8.3f} {full['net_per_unit_risk']:8.1f} "
              f"{dd:8.1f} {str(ratio):>7s} {str(full['rw_pf']):>6s} {d['net_rr']:8.1f} {h['net_rr']:8.1f}")
    report["stage2_tilts"] = results

    base = results["flat_baseline"]
    cands = {n: v for n, v in results.items() if n != "flat_baseline"}
    winner = max(cands, key=lambda n: cands[n]["discovery"]["net_per_unit_risk"])
    w = cands[winner]
    better_disc = w["discovery"]["net_per_unit_risk"] > base["discovery"]["net_per_unit_risk"]
    better_hold = w["holdout"]["net_per_unit_risk"] > base["holdout"]["net_per_unit_risk"]
    better_dd = (w["return_per_dd"] or 0) > (base["return_per_dd"] or 0)
    report["selection"] = {
        "chosen_on_discovery": winner,
        "discovery_r_per_risk": w["discovery"]["net_per_unit_risk"], "baseline": base["discovery"]["net_per_unit_risk"],
        "holdout_r_per_risk": w["holdout"]["net_per_unit_risk"], "baseline_holdout": base["holdout"]["net_per_unit_risk"],
        "return_per_drawdown": w["return_per_dd"], "baseline_return_per_drawdown": base["return_per_dd"],
        "VALIDATES": bool(better_disc and better_hold and better_dd),
        "verdict": ("PROMOTABLE - beats flat sizing per unit of risk on the untouched half AND per unit of drawdown"
                    if better_disc and better_hold and better_dd else
                    "REJECTED - fails one of: holdout risk-adjusted return, or return per drawdown"),
    }
    print(f"\nchosen on discovery: {winner}")
    print(f"  R per unit risk  discovery {w['discovery']['net_per_unit_risk']} vs flat {base['discovery']['net_per_unit_risk']}")
    print(f"  R per unit risk  HOLDOUT   {w['holdout']['net_per_unit_risk']} vs flat {base['holdout']['net_per_unit_risk']}")
    print(f"  return / maxDD             {w['return_per_dd']} vs flat {base['return_per_dd']}")
    print(f"  VERDICT: {report['selection']['verdict']}")
    REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"report -> {REPORT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
