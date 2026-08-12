"""RESEARCH — which additional markets can join the mean-reversion book?

The slot-rule study (2026-08-12) established that more trades must come from
MORE MARKETS, not more positions per market: extra positions cluster during
selloffs across correlated indices and drawdown grows faster than return.
So a candidate must add trades WITHOUT adding concentration.

Every candidate must clear, in order:
  1. DATA INTEGRITY  - no gap > 10 calendar days, no runs of frozen (zero
     range) bars, series current. This is the UK100 lesson: a 1,183-day hole
     produced a beautiful and completely meaningless profit factor.
  2. COST REALITY    - round trip estimated from spreads sampled repeatedly
     in-session, never a single snapshot (a closed-market snapshot reads 0
     and flatters everything).
  3. THE GATE        - rwPF >= 1.10, >= 60% positive quarters, >= 80 trades,
     net R > 0, on the audited IBS(0.2/0.8/10d) rules.
  4. DIVERSIFICATION - daily-return correlation with the existing book, and
     the share of its entries that fire while a book position is already
     open. A candidate that only trades when everything else does is extra
     size on the same bet, which is what the slot study just rejected.

Nothing here is wired live; promotion still needs an out-of-sample verdict.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
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

BOOK_SYMBOLS = ("US30", "NAS100", "US500", "DE40")  # DE40 resolves to GER40
BOOK_BROKER_NAMES = ("US30", "NAS100", "US500", "GER40")
KNOWN_COSTS = {"US30": 5.0, "NAS100": 3.5, "US500": 0.85, "GER40": 1.75}
DAILY_BARS = 1400
MIN_BARS = 600
MAX_GAP_DAYS = 10
VOL_WINDOW = 20
IBS_ENTRY, IBS_EXIT, MAX_HOLD = 0.2, 0.8, 10
FINANCING_PER_DAY = 0.0002
SPREAD_SAMPLES = 5
GATE = {"min_rwpf": 1.10, "min_positive_quarter_share": 0.60, "min_trades": 80}
REPORT = PROJECT_ROOT / "data" / "reports" / "symbol_expansion_r2.json"


def integrity(frame: pd.DataFrame) -> tuple[bool, str]:
    if len(frame) < MIN_BARS:
        return False, f"only {len(frame)} bars (<{MIN_BARS})"
    times = pd.to_datetime(frame["time"])
    gaps = times.diff().dt.days.dropna()
    if len(gaps) and gaps.max() > MAX_GAP_DAYS:
        worst = times[gaps.idxmax() - 1].date() if gaps.idxmax() > 0 else "?"
        return False, f"data hole of {int(gaps.max())} days near {worst}"
    ranges = (frame["high"] - frame["low"]).astype(float)
    frozen = int((ranges <= 0).sum())
    if frozen > 5:
        return False, f"{frozen} frozen (zero-range) bars - stale feed"
    age_days = (pd.Timestamp.now(tz="UTC") - times.max().tz_localize("UTC") if times.max().tzinfo is None
                else pd.Timestamp.now(tz="UTC") - times.max()).days
    if age_days > 5:
        return False, f"series ends {age_days} days ago"
    return True, "ok"


def ibs_trades(symbol: str, frame: pd.DataFrame, cost: float) -> list[dict]:
    highs = [float(v) for v in frame["high"]]
    lows = [float(v) for v in frame["low"]]
    closes = [float(v) for v in frame["close"]]
    times = [pd.Timestamp(v) for v in frame["time"]]
    trades: list[dict] = []
    entry = None
    for i in range(VOL_WINDOW + 3, len(closes)):
        rng = highs[i] - lows[i]
        ibs = (closes[i] - lows[i]) / rng if rng > 0 else 0.5
        if entry is None:
            if ibs < IBS_ENTRY:
                entry = i
            continue
        if ibs > IBS_EXIT or i - entry >= MAX_HOLD:
            moves = [abs(closes[j] - closes[j - 1]) for j in range(entry - VOL_WINDOW, entry)]
            ru = sum(moves) / len(moves) if moves else 0.0
            if ru > 0:
                days = max((times[i] - times[entry]).days, 1)
                pnl = closes[i] - closes[entry] - cost - FINANCING_PER_DAY * closes[entry] * days
                trades.append({"symbol": symbol, "entry_time": str(times[entry]),
                               "exit_time": str(times[i]), "rr": pnl / ru})
            entry = None
    return trades


def score(trades: list[dict]) -> dict[str, Any]:
    rrs = [t["rr"] for t in trades]
    if not rrs:
        return {"trades": 0, "passes_gate": False}
    wins = [r for r in rrs if r > 0]
    gross_loss = abs(sum(r for r in rrs if r < 0))
    q: dict[str, float] = defaultdict(float)
    for t in trades:
        s = pd.Timestamp(t["exit_time"])
        q[f"{s.year}-Q{(s.month - 1) // 3 + 1}"] += t["rr"]
    active = [v for v in q.values() if v != 0]
    share = sum(1 for v in active if v > 0) / len(active) if active else 0.0
    rwpf = round(sum(wins) / gross_loss, 2) if gross_loss else round(sum(wins), 2)
    out = {"trades": len(rrs), "win_rate": round(len(wins) / len(rrs) * 100, 1),
           "net_rr": round(sum(rrs), 2), "rw_pf": rwpf,
           "expectancy": round(sum(rrs) / len(rrs), 3),
           "positive_quarter_share": round(share, 2)}
    out["passes_gate"] = (out["net_rr"] > 0 and rwpf >= GATE["min_rwpf"]
                          and share >= GATE["min_positive_quarter_share"]
                          and out["trades"] >= GATE["min_trades"])
    return out


def main() -> int:
    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
    except MT5ConnectorError as exc:
        print(f"MT5 connection failed: {exc}")
        return 1
    report: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "status": "RESEARCH_ONLY",
                              "gate": GATE, "candidates": {}, "rejected": {}}
    try:
        all_symbols = connector.mt5.symbols_get() or ()
        wanted = [s.name for s in all_symbols
                  if s.path and ("Index" in s.path or "Indices" in s.path or "Commodit" in s.path or "Metal" in s.path)]
        wanted = [n for n in wanted if n not in BOOK_BROKER_NAMES]
        print(f"scanning {len(wanted)} index/commodity symbols (book: {', '.join(BOOK_BROKER_NAMES)})\n")

        # Spread sampling in-session, repeated (a single read can catch a gap).
        spreads: dict[str, list[float]] = defaultdict(list)
        probe = list(wanted) + list(BOOK_BROKER_NAMES)
        for _ in range(SPREAD_SAMPLES):
            for name in probe:
                connector.mt5.symbol_select(name, True)
                info = connector.mt5.symbol_info(name)
                if info and info.spread and info.point:
                    spreads[name].append(info.spread * info.point)
            time.sleep(1)
        ratios = [KNOWN_COSTS[n] / statistics.median(spreads[n]) for n in BOOK_BROKER_NAMES
                  if spreads.get(n) and statistics.median(spreads[n]) > 0]
        k = statistics.median(ratios) if ratios else 3.5
        report["cost_multiplier_k"] = round(k, 2)
        print(f"cost model: round trip = {k:.2f} x median in-session spread (calibrated on the book)\n")

        book_frames = {}
        for name in BOOK_BROKER_NAMES:
            raw = connector.mt5.copy_rates_from_pos(name, connector.mt5.TIMEFRAME_D1, 0, DAILY_BARS)
            frame = pd.DataFrame(raw)
            frame["time"] = pd.to_datetime(frame["time"], unit="s")
            book_frames[name] = frame.iloc[:-1].reset_index(drop=True)
        book_returns = {n: f.set_index("time")["close"].astype(float).pct_change() for n, f in book_frames.items()}
        book_windows = []
        for n, f in book_frames.items():
            for t in ibs_trades(n, f, KNOWN_COSTS[n]):
                book_windows.append((pd.Timestamp(t["entry_time"]), pd.Timestamp(t["exit_time"])))

        header = f"{'symbol':16s} {'trades':>7s} {'WR%':>6s} {'netR':>8s} {'rwPF':>6s} {'q+':>5s} {'corr':>6s} {'overlap%':>9s}  verdict"
        print(header); print("-" * len(header))
        for name in sorted(wanted):
            try:
                if not spreads.get(name):
                    report["rejected"][name] = "no in-session spread (never quoted while sampling)"
                    continue
                frame = connector.mt5.copy_rates_from_pos(name, connector.mt5.TIMEFRAME_D1, 0, DAILY_BARS)
                if frame is None or len(frame) == 0:
                    report["rejected"][name] = "no daily data"
                    continue
                df = pd.DataFrame(frame)
                df["time"] = pd.to_datetime(df["time"], unit="s")
                df = df.iloc[:-1].reset_index(drop=True)
                ok, why = integrity(df)
                if not ok:
                    report["rejected"][name] = why
                    continue
                cost = k * statistics.median(spreads[name])
                trades = ibs_trades(name, df, cost)
                summary = score(trades)
                if summary["trades"] == 0:
                    report["rejected"][name] = "no trades generated"
                    continue
                rets = df.set_index("time")["close"].astype(float).pct_change()
                corrs = [rets.corr(br) for br in book_returns.values()]
                corr = max(c for c in corrs if pd.notna(c)) if any(pd.notna(c) for c in corrs) else 0.0
                overlaps = sum(1 for t in trades
                               if any(a <= pd.Timestamp(t["entry_time"]) <= b for a, b in book_windows))
                overlap_pct = round(overlaps / len(trades) * 100, 1)
                verdict = "PASS" if summary["passes_gate"] else "fails gate"
                if summary["passes_gate"] and corr > 0.75:
                    verdict = "PASS but CORRELATED"
                report["candidates"][name] = {**summary, "est_round_trip": round(cost, 4),
                                              "max_corr_with_book": round(float(corr), 2),
                                              "entry_overlap_percent": overlap_pct,
                                              "bars": len(df), "verdict": verdict}
                print(f"{name:16s} {summary['trades']:7d} {summary['win_rate']:6.1f} {summary['net_rr']:8.1f} "
                      f"{summary['rw_pf']:6.2f} {summary['positive_quarter_share']:5.2f} {corr:6.2f} "
                      f"{overlap_pct:9.1f}  {verdict}")
            except Exception as exc:  # noqa: BLE001
                report["rejected"][name] = f"error: {exc}"
        passes = {n: c for n, c in report["candidates"].items() if c["verdict"].startswith("PASS")}
        report["passing"] = sorted(passes, key=lambda n: (-passes[n]["rw_pf"]))
        print(f"\npassed the gate: {report['passing'] or 'none'}")
        print(f"rejected before scoring: {len(report['rejected'])} (integrity/cost/data)")
        REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"report -> {REPORT.relative_to(PROJECT_ROOT)}")
    finally:
        connector.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
