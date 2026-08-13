"""Out-of-sample test of the two genuinely uncorrelated leads.

symbol_expansion_r2.json scanned 68 markets and ended: "NOT PROMOTABLE YET,
but two genuine leads ... XAUAUD (rwPF 1.37, corr -0.13) and SpotBrent
(rwPF 1.30, corr 0.06) ... Next step is an out-of-sample walk-forward on these
two, not promotion." This is that step, run with the CURRENT live rules
(IBS<0.2 entry, IBS>0.8 or 6-day exit - the 6-day stop was promoted
2026-08-12, after the scan was written, so the scan's numbers are stale).

It also settles the caveat that report raised against itself: "plain XAUUSD
FAILS (rwPF 0.88) ... so XAUAUD's edge comes from the AUD leg, which needs
explaining before trusting." XAUAUD is gold priced in Australian dollars -
arithmetically XAUUSD divided by AUDUSD. If the edge lives in the AUD leg then
XAUAUD is an AUDUSD trade wearing a gold costume, and should be traded as
AUDUSD (tighter spread, deeper book) or not at all. So XAUUSD and AUDUSD are
tested alongside it and the three results read together.

Costs are the broker's live spread multiplied by the same rollover stress
factor (2.31x) the expansion scan used, so a candidate cannot pass by being
quoted at an unrealistically tight moment.

DIVERSIFICATION IS THE POINT, NOT THE EDGE. A candidate that merely has an
edge is not useful: the book already has one, capped at 3 slots (1 if the
concurrency work lands). Only an edge on something that does NOT fall when
the four indices fall adds anything, so correlation against the live book is
reported next to every result and is a gate, not a footnote.
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

LIVE_BOOK = ("US30", "NAS100", "US500", "DE40")
CANDIDATES = ("XAUAUD", "SpotBrent", "XAUUSD", "AUDUSD", "SpotCrude", "XAGUSD")
COST_STRESS = 2.31          # rollover-stressed, as in symbol_expansion_r2
DAILY_BARS = 1400
IBS_ENTRY, IBS_EXIT, VOL_WINDOW, MAX_HOLD = 0.2, 0.8, 20, 6
FINANCING_PER_DAY = 0.0002
GATE_PF, GATE_QUARTERS, GATE_TRADES = 1.10, 0.60, 80
REPORT = PROJECT_ROOT / "data" / "reports" / "uncorrelated_expansion.json"


def simulate(frame: pd.DataFrame, cost: float) -> list[dict]:
    """The live rule exactly: IBS<0.2 in, IBS>0.8 or 6 days out."""
    highs = [float(v) for v in frame["high"]]
    lows = [float(v) for v in frame["low"]]
    closes = [float(v) for v in frame["close"]]
    times = [pd.Timestamp(v) for v in frame["time"]]
    trades: list[dict] = []
    entry = None
    for i in range(VOL_WINDOW + 3, len(closes)):
        rng = highs[i] - lows[i]
        ibs = (closes[i] - lows[i]) / rng if rng > 0 else 0.5
        if entry is not None:
            held = i - entry["i"]
            if ibs > IBS_EXIT or held >= MAX_HOLD:
                days = max((times[i] - times[entry["i"]]).days, 1)
                pnl = closes[i] - closes[entry["i"]] - cost - FINANCING_PER_DAY * closes[entry["i"]] * days
                trades.append({"exit_time": str(times[i]), "hold": held, "rr": pnl / entry["ru"]})
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
        return {"trades": 0, "net_rr": 0.0, "rw_pf": None, "positive_quarter_share": 0.0}
    wins = [r for r in rrs if r > 0]
    gross_loss = abs(sum(r for r in rrs if r < 0))
    q: dict[str, float] = defaultdict(float)
    for t in trades:
        s = pd.Timestamp(t["exit_time"])
        q[f"{s.year}-Q{(s.month - 1) // 3 + 1}"] += t["rr"]
    active = [v for v in q.values() if v != 0]
    return {
        "trades": len(rrs),
        "win_rate": round(len(wins) / len(rrs) * 100, 1),
        "net_rr": round(sum(rrs), 2),
        "rw_pf": round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy": round(sum(rrs) / len(rrs), 3),
        "avg_hold": round(sum(t["hold"] for t in trades) / len(trades), 1),
        "positive_quarter_share": round(sum(1 for v in active if v > 0) / len(active), 2) if active else 0.0,
        "quarters": len(active),
    }


def passes(b: dict) -> bool:
    return (b.get("trades", 0) >= GATE_TRADES
            and (b.get("rw_pf") or 0) >= GATE_PF
            and b.get("positive_quarter_share", 0) >= GATE_QUARTERS)


def main() -> int:
    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
    except MT5ConnectorError as exc:
        print(f"MT5 connection failed: {exc}")
        return 1
    frames: dict[str, pd.DataFrame] = {}
    costs: dict[str, float] = {}
    try:
        names = LIVE_BOOK + CANDIDATES
        connector.supported_symbols = frozenset(set(connector.supported_symbols) | set(names))
        for s in names:
            try:
                broker = connector.broker_symbol(s)
                info = connector.mt5.symbol_info(broker)
                connector.mt5.symbol_select(broker, True)
                f = connector.get_historical_candles(s, "D1", count=DAILY_BARS).iloc[:-1].reset_index(drop=True)
                frames[s] = f
                costs[s] = info.spread * info.point * COST_STRESS
            except Exception as exc:  # noqa: BLE001
                print(f"{s}: unavailable ({str(exc)[:60]})")
    finally:
        connector.shutdown()

    common = max(f["time"].min() for f in frames.values())
    frames = {s: f[f["time"] >= common].reset_index(drop=True) for s, f in frames.items()}
    print(f"common span {str(common)[:10]} -> {str(max(f['time'].max() for f in frames.values()))[:10]}\n")

    # daily-return correlation against the live book
    rets = {s: f.set_index("time")["close"].pct_change() for s, f in frames.items()}
    book_ret = sum(rets[s] for s in LIVE_BOOK) / len(LIVE_BOOK)

    report: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "status": "RESEARCH_ONLY",
                              "rule": "live: IBS<0.2 in, IBS>0.8 or 6d out", "cost_stress": COST_STRESS,
                              "results": {}}

    print(f"{'symbol':11s} {'corr':>6s} {'cost':>8s} {'trades':>7s} {'WR%':>6s} {'netR':>8s} {'rwPF':>6s} "
          f"{'q+':>5s} {'discR':>8s} {'holdR':>8s} {'GATE':>6s}")
    print("-" * 96)
    for s in LIVE_BOOK + CANDIDATES:
        if s not in frames:
            continue
        f = frames[s]
        half = len(f) // 2
        full = book(simulate(f, costs[s]))
        d = book(simulate(f.iloc[:half].reset_index(drop=True), costs[s]))
        h = book(simulate(f.iloc[half:].reset_index(drop=True), costs[s]))
        corr = float(pd.concat([rets[s], book_ret], axis=1).dropna().corr().iloc[0, 1])
        ok = passes(full) and passes(h)
        report["results"][s] = {"full": full, "discovery": d, "holdout": h,
                                "corr_with_live_book": round(corr, 3), "cost_price_units": round(costs[s], 5),
                                "passes_gate_full_and_holdout": ok}
        tag = "PASS" if ok else "fail"
        marker = "  <-- live book" if s in LIVE_BOOK else ""
        print(f"{s:11s} {corr:6.2f} {costs[s]:8.4f} {full['trades']:7d} {full['win_rate']:6.1f} "
              f"{full['net_rr']:8.1f} {str(full['rw_pf']):>6s} {full['positive_quarter_share']:5.2f} "
              f"{d['net_rr']:8.1f} {h['net_rr']:8.1f} {tag:>6s}{marker}")

    print(f"\ngate: >={GATE_TRADES} trades, rwPF >={GATE_PF}, >={GATE_QUARTERS:.0%} positive quarters "
          f"- required on the FULL sample AND the holdout half")
    print("\nDECOMPOSITION - where does XAUAUD's edge actually live?")
    for s in ("XAUAUD", "XAUUSD", "AUDUSD"):
        if s in report["results"]:
            r = report["results"][s]["full"]
            print(f"  {s:8s} rwPF {str(r['rw_pf']):>5s}  netR {r['net_rr']:7.1f}  "
                  f"trades {r['trades']:4d}  q+ {r['positive_quarter_share']:.2f}")

    REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nreport -> {REPORT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
