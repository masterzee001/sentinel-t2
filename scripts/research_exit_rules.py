"""RESEARCH — can the mean-reversion EXIT be improved? (pre-registered)

The price-action design study rated support/resistance hypotheses unlikely to
survive (a binary filter on 509 trades would need a +123% lift in mean R to be
detectable) but rated the EXIT hypotheses as the real opportunity, because the
current exit is crude: IBS>0.8 or a flat 10-day timeout, never optimised.

The observation motivating it: mean R falls monotonically with holding time,
and the small tail of trades that run to the timeout is heavily negative. That
observation came from looking at the data, so it is NOT blind-pre-registered —
which is exactly why the holdout below decides it, not the discovery half.

Hypotheses, all reported, none hidden:
  H5 time stop        MAX_HOLD in {2,3,4,5,6,7,8,10}
  H6 close-based stop exit when close <= entry - k*risk_unit, k in {2,3,4,5}
  H7 prior-day-high   exit on first close above the previous bar's high
                      (a) replacing the IBS exit, (b) in addition to it

METHOD (the discipline that makes this trustworthy):
  * FULL RE-SIMULATION, never post-hoc filtering of the baseline trade list.
    With one position per symbol, exiting earlier frees the slot for a signal
    that the baseline never took, so the books genuinely differ.
  * SELECTION-THEN-VALIDATION: the winner is chosen on the DISCOVERY half only
    and must then survive the untouched HOLDOUT half. A variant that wins on
    discovery and fails holdout is rejected, however good the full-sample
    number looks.
  * Financing accrues per calendar day held, so shorter holds are genuinely
    cheaper and longer holds genuinely dearer.
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
REPORT = PROJECT_ROOT / "data" / "reports" / "exit_rules_research.json"


def simulate(frame: pd.DataFrame, symbol: str, max_hold: int = 10,
             stop_k: float | None = None, pdh_exit: str | None = None) -> list[dict]:
    """Re-simulate the book end to end under one exit rule."""
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
            held = i - entry["i"]
            hit_ibs = ibs > IBS_EXIT
            hit_pdh = pdh_exit is not None and closes[i] > highs[i - 1]
            hit_stop = stop_k is not None and closes[i] <= closes[entry["i"]] - stop_k * entry["ru"]
            if pdh_exit == "replace":
                exit_now = hit_pdh or held >= max_hold or hit_stop
            elif pdh_exit == "either":
                exit_now = hit_ibs or hit_pdh or held >= max_hold or hit_stop
            else:
                exit_now = hit_ibs or held >= max_hold or hit_stop
            if exit_now:
                days = max((times[i] - times[entry["i"]]).days, 1)
                pnl = closes[i] - closes[entry["i"]] - cost - FINANCING_PER_DAY * closes[entry["i"]] * days
                trades.append({"symbol": symbol, "exit_time": str(times[i]),
                               "hold": held, "rr": pnl / entry["ru"]})
                entry = None
        if entry is None and ibs < IBS_ENTRY:
            moves = [abs(closes[j] - closes[j - 1]) for j in range(i - VOL_WINDOW, i)]
            ru = sum(moves) / len(moves) if moves else 0.0
            if ru > 0:
                entry = {"i": i, "ru": ru}
    return trades


def book(trades: list[dict]) -> dict[str, Any]:
    rrs = [t["rr"] for t in trades]
    if not rrs:
        return {"trades": 0, "net_rr": 0.0, "rw_pf": 0.0}
    wins = [r for r in rrs if r > 0]
    gross_loss = abs(sum(r for r in rrs if r < 0))
    q: dict[str, float] = defaultdict(float)
    for t in trades:
        s = pd.Timestamp(t["exit_time"])
        q[f"{s.year}-Q{(s.month - 1) // 3 + 1}"] += t["rr"]
    active = [v for v in q.values() if v != 0]
    return {
        "trades": len(rrs), "win_rate": round(len(wins) / len(rrs) * 100, 1),
        "net_rr": round(sum(rrs), 2),
        # Guard the divide-by-zero flattery: a filtered book with no losers
        # would otherwise report a spectacular pseudo profit factor.
        "rw_pf": round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy": round(sum(rrs) / len(rrs), 3),
        "avg_hold": round(sum(t["hold"] for t in trades) / len(trades), 1),
        "positive_quarter_share": round(sum(1 for v in active if v > 0) / len(active), 2) if active else 0.0,
    }


def run(frames: dict[str, pd.DataFrame], **kw) -> dict[str, Any]:
    trades: list[dict] = []
    for s, f in frames.items():
        trades.extend(simulate(f, s, **kw))
    return book(trades)


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

    report: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "status": "RESEARCH_ONLY", "results": {}}
    base_full, base_d, base_h = run(frames), run(disc), run(hold)
    print(f"BASELINE (IBS>0.8 or 10d): full {base_full['trades']}t net {base_full['net_rr']}R "
          f"rwPF {base_full['rw_pf']} | discovery {base_d['net_rr']}R | holdout {base_h['net_rr']}R\n")
    report["baseline"] = {"full": base_full, "discovery": base_d, "holdout": base_h}

    print(f"{'variant':26s} {'trades':>7s} {'netR':>8s} {'rwPF':>6s} {'avgHold':>8s} {'q+':>5s} {'discR':>8s} {'holdR':>8s}")
    print("-" * 84)
    variants: dict[str, dict] = {}
    for h in (2, 3, 4, 5, 6, 7, 8, 10):
        variants[f"H5_time_stop_{h}d"] = {"max_hold": h}
    for k in (2, 3, 4, 5):
        variants[f"H6_close_stop_{k}u"] = {"stop_k": float(k)}
    variants["H7_pdh_replace"] = {"pdh_exit": "replace"}
    variants["H7_pdh_either"] = {"pdh_exit": "either"}

    for name, kw in variants.items():
        full, d, hld = run(frames, **kw), run(disc, **kw), run(hold, **kw)
        report["results"][name] = {"full": full, "discovery": d, "holdout": hld, "params": kw}
        print(f"{name:26s} {full['trades']:7d} {full['net_rr']:8.1f} {str(full['rw_pf']):>6s} "
              f"{full['avg_hold']:8.1f} {full['positive_quarter_share']:5.2f} "
              f"{d['net_rr']:8.1f} {hld['net_rr']:8.1f}")

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
                    else "REJECTED - discovery-only improvement, does not survive the holdout"),
    }
    print(f"\nchosen on discovery: {winner}")
    print(f"  discovery {w['discovery']['net_rr']}R vs baseline {base_d['net_rr']}R")
    print(f"  HOLDOUT   {w['holdout']['net_rr']}R vs baseline {base_h['net_rr']}R")
    print(f"  VERDICT: {report['selection']['verdict']}")
    REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"report -> {REPORT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
