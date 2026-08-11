"""RESEARCH — edge hunt round 2. Pre-registered hypothesis grid, honest costs.

Hypotheses (all rules fixed before looking at results):
  H1 Turn-of-month: long index last 4 + first 3 trading days of each month.
  H2 Overnight vs intraday return decomposition (diagnostic + net strategy).
  H3 Gap-fade: open gapped down >= g x risk_unit from prior close -> buy at
     open, exit same close. Grid g in {0.3, 0.5, 1.0}.
  H4 IBS edge by volatility regime (risk_unit terciles at entry).
  H5 IBS edge by day of week.
  H6 DE40 IBS(0.2/0.8/10d) promotion-style gate: in-session spread measured
     live, quarterly stability, rwPF/net/positive-quarter thresholds.
  H7 RSI2 shared-slot second book: incremental trades (no open IBS position
     in the symbol) through the same quarterly gate.

Gate for promotion candidates: net R > 0, rwPF >= 1.10, >= 60% positive
quarters, >= 80 trades. Everything else is reported as conditioning
knowledge, not tradable claims. Nothing here touches live engines.
"""

from __future__ import annotations

import json
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

SYMBOLS = ("US30", "NAS100", "US500")
COSTS = {"US30": 5.0, "NAS100": 3.5, "US500": 0.85}
DAILY_BARS = 1400
VOL_WINDOW = 20
FINANCING_PER_DAY = 0.0002
GATE = {"min_net_rr": 0.0, "min_rwpf": 1.10, "min_positive_quarter_share": 0.6, "min_trades": 80}
REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "edge_hunt_r2.json"


def risk_unit_at(closes: list[float], index: int) -> float:
    moves = [abs(closes[j] - closes[j - 1]) for j in range(index - VOL_WINDOW, index)]
    return sum(moves) / len(moves) if moves else 0.0


def book(rrs: list[float]) -> dict[str, Any]:
    wins = [r for r in rrs if r > 0]
    losses = [r for r in rrs if r < 0]
    gross_loss = abs(sum(losses))
    return {
        "trades": len(rrs),
        "win_rate": round(len(wins) / len(rrs) * 100, 1) if rrs else 0.0,
        "net_rr": round(sum(rrs), 2),
        "rw_pf": round(sum(wins) / gross_loss, 2) if gross_loss else round(sum(wins), 2),
        "expectancy": round(sum(rrs) / len(rrs), 3) if rrs else 0.0,
    }


def quarterly(trades: list[tuple[str, float]]) -> dict[str, Any]:
    by_quarter: dict[str, list[float]] = defaultdict(list)
    for ts, rr in trades:
        stamp = pd.Timestamp(ts)
        by_quarter[f"{stamp.year}-Q{(stamp.month - 1) // 3 + 1}"].append(rr)
    rows = {q: round(sum(v), 2) for q, v in sorted(by_quarter.items())}
    active = [v for v in rows.values() if v != 0]
    share = sum(1 for v in active if v > 0) / len(active) if active else 0.0
    return {"per_quarter_net_rr": rows, "positive_share": round(share, 2)}


def gate_check(summary: dict[str, Any], quarters: dict[str, Any]) -> bool:
    return (
        summary["net_rr"] > GATE["min_net_rr"]
        and summary["rw_pf"] >= GATE["min_rwpf"]
        and quarters["positive_share"] >= GATE["min_positive_quarter_share"]
        and summary["trades"] >= GATE["min_trades"]
    )


def main() -> int:
    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
    except MT5ConnectorError as exc:
        print(f"MT5 connection failed: {exc}")
        return 1
    report: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "status": "RESEARCH_ONLY", "hypotheses": {}}
    try:
        connector.supported_symbols = frozenset(set(connector.supported_symbols) | set(SYMBOLS) | {"DE40"})
        frames: dict[str, pd.DataFrame] = {}
        for symbol in SYMBOLS:
            frames[symbol] = connector.get_historical_candles(symbol, "D1", count=DAILY_BARS).iloc[:-1].reset_index(drop=True)
        common_start = max(f["time"].min() for f in frames.values())
        frames = {s: f[f["time"] >= common_start].reset_index(drop=True) for s, f in frames.items()}

        # ---- H1 turn-of-month -------------------------------------------------
        tom: list[tuple[str, float]] = []
        for symbol, frame in frames.items():
            closes = [float(v) for v in frame["close"]]
            times = [pd.Timestamp(v) for v in frame["time"]]
            in_pos_from: int | None = None
            for i in range(VOL_WINDOW + 1, len(times) - 1):
                # (year, month) — a month-only compare counts NEXT year's same
                # month and silently restricted entries to the final year (bug
                # caught by adversarial audit 2026-08-11).
                key_now = (times[i].year, times[i].month)
                days_left = sum(1 for j in range(i + 1, len(times)) if (times[j].year, times[j].month) == key_now)
                if in_pos_from is None and days_left == 3 and i + 7 < len(times):  # enter close, 4th-last day
                    in_pos_from = i
                elif in_pos_from is not None:
                    into_new = times[i].month != times[in_pos_from].month
                    held_new = sum(1 for j in range(in_pos_from + 1, i + 1) if times[j].month != times[in_pos_from].month)
                    if into_new and held_new >= 3:
                        ru = risk_unit_at(closes, in_pos_from)
                        if ru > 0:
                            days = max((times[i] - times[in_pos_from]).days, 1)
                            pnl = closes[i] - closes[in_pos_from] - COSTS[symbol] - FINANCING_PER_DAY * closes[in_pos_from] * days
                            tom.append((str(times[i]), pnl / ru))
                        in_pos_from = None
        summary = book([r for _, r in tom]); quarters = quarterly(tom)
        report["hypotheses"]["H1_turn_of_month"] = {**summary, **quarters, "gate": gate_check(summary, quarters)}

        # ---- H2 overnight vs intraday decomposition ---------------------------
        h2 = {}
        for symbol, frame in frames.items():
            opens = [float(v) for v in frame["open"]]
            closes = [float(v) for v in frame["close"]]
            overnight = sum(opens[i] - closes[i - 1] for i in range(1, len(opens)))
            intraday = sum(closes[i] - opens[i] for i in range(1, len(opens)))
            n = len(opens) - 1
            net_overnight = overnight - n * COSTS[symbol]  # daily round trip
            h2[symbol] = {
                "gross_overnight_points": round(overnight, 1),
                "gross_intraday_points": round(intraday, 1),
                "overnight_net_of_costs_points": round(net_overnight, 1),
                "verdict": "cost-dead" if net_overnight < 0 else "investigate",
            }
        report["hypotheses"]["H2_overnight_premium"] = h2

        # ---- H3 gap fade ------------------------------------------------------
        for g in (0.3, 0.5, 1.0):
            fades: list[tuple[str, float]] = []
            for symbol, frame in frames.items():
                opens = [float(v) for v in frame["open"]]
                closes = [float(v) for v in frame["close"]]
                times = [pd.Timestamp(v) for v in frame["time"]]
                for i in range(VOL_WINDOW + 1, len(opens)):
                    ru = risk_unit_at(closes, i)
                    if ru <= 0:
                        continue
                    gap = closes[i - 1] - opens[i]  # positive = gapped DOWN
                    if gap >= g * ru:
                        pnl = closes[i] - opens[i] - COSTS[symbol]
                        fades.append((str(times[i]), pnl / ru))
            summary = book([r for _, r in fades]); quarters = quarterly(fades)
            report["hypotheses"][f"H3_gap_fade_{g}"] = {**summary, **quarters, "gate": gate_check(summary, quarters)}

        # ---- H4/H5 IBS conditioning ------------------------------------------
        ibs_trades: list[dict[str, Any]] = []
        for symbol, frame in frames.items():
            highs = [float(v) for v in frame["high"]]
            lows = [float(v) for v in frame["low"]]
            closes = [float(v) for v in frame["close"]]
            times = [pd.Timestamp(v) for v in frame["time"]]
            entry: int | None = None
            for i in range(VOL_WINDOW + 3, len(closes)):
                rng = highs[i] - lows[i]
                ibs = (closes[i] - lows[i]) / rng if rng > 0 else 0.5
                if entry is None:
                    if ibs < 0.2:
                        entry = i
                    continue
                if ibs > 0.8 or i - entry >= 10:
                    ru = risk_unit_at(closes, entry)
                    if ru > 0:
                        days = max((times[i] - times[entry]).days, 1)
                        pnl = closes[i] - closes[entry] - COSTS[symbol] - FINANCING_PER_DAY * closes[entry] * days
                        rel_vol = ru / closes[entry]
                        ibs_trades.append({"ts": str(times[i]), "rr": pnl / ru, "rel_vol": rel_vol, "dow": times[entry].dayofweek})
                    entry = None
        vols = sorted(t["rel_vol"] for t in ibs_trades)
        t1, t2 = vols[len(vols) // 3], vols[2 * len(vols) // 3]
        report["hypotheses"]["H4_ibs_by_vol_regime"] = {
            "low_vol": book([t["rr"] for t in ibs_trades if t["rel_vol"] <= t1]),
            "mid_vol": book([t["rr"] for t in ibs_trades if t1 < t["rel_vol"] <= t2]),
            "high_vol": book([t["rr"] for t in ibs_trades if t["rel_vol"] > t2]),
        }
        day_names = ("Mon", "Tue", "Wed", "Thu", "Fri")
        report["hypotheses"]["H5_ibs_by_entry_dow"] = {
            day_names[d]: book([t["rr"] for t in ibs_trades if t["dow"] == d]) for d in range(5)
        }

        # ---- H6 DE40 promotion-style gate ------------------------------------
        spreads = []
        de40_name = connector.broker_symbol("DE40")
        for _ in range(5):
            info = connector.mt5.symbol_info(de40_name)
            if info and info.spread and info.point:
                spreads.append(info.spread * info.point)
            time.sleep(1)
        de40_spread = sorted(spreads)[len(spreads) // 2] if spreads else None
        de40_cost = round(3.5 * de40_spread, 3) if de40_spread else 1.75
        de40 = connector.get_historical_candles("DE40", "D1", count=DAILY_BARS).iloc[:-1].reset_index(drop=True)
        highs = [float(v) for v in de40["high"]]; lows = [float(v) for v in de40["low"]]
        closes = [float(v) for v in de40["close"]]; times = [pd.Timestamp(v) for v in de40["time"]]
        de40_trades: list[tuple[str, float]] = []
        entry = None
        for i in range(VOL_WINDOW + 3, len(closes)):
            rng = highs[i] - lows[i]
            ibs = (closes[i] - lows[i]) / rng if rng > 0 else 0.5
            if entry is None:
                if ibs < 0.2:
                    entry = i
                continue
            if ibs > 0.8 or i - entry >= 10:
                ru = risk_unit_at(closes, entry)
                if ru > 0:
                    days = max((times[i] - times[entry]).days, 1)
                    pnl = closes[i] - closes[entry] - de40_cost - FINANCING_PER_DAY * closes[entry] * days
                    de40_trades.append((str(times[i]), pnl / ru))
                entry = None
        summary = book([r for _, r in de40_trades]); quarters = quarterly(de40_trades)
        report["hypotheses"]["H6_DE40_ibs_gate"] = {
            **summary, **quarters,
            "in_session_spread_measured": de40_spread, "round_trip_cost_used": de40_cost,
            "span": {"first": str(times[0]), "last": str(times[-1])},
            "gate": gate_check(summary, quarters),
        }

        # ---- H7 RSI2 shared-slot incremental book ----------------------------
        def rsi2(closes_list: list[float], i: int) -> float | None:
            if i < 3:
                return None
            gains = losses = 0.0
            for j in (i - 1, i):
                change = closes_list[j] - closes_list[j - 1]
                gains += max(change, 0.0); losses += max(-change, 0.0)
            return 50.0 if gains + losses == 0 else 100.0 * gains / (gains + losses)

        incremental: list[tuple[str, float]] = []
        for symbol, frame in frames.items():
            highs = [float(v) for v in frame["high"]]; lows = [float(v) for v in frame["low"]]
            closes = [float(v) for v in frame["close"]]; times = [pd.Timestamp(v) for v in frame["time"]]
            slot: dict[str, Any] | None = None  # {'src','i'}
            for i in range(VOL_WINDOW + 3, len(closes)):
                rng = highs[i] - lows[i]
                ibs = (closes[i] - lows[i]) / rng if rng > 0 else 0.5
                r2 = rsi2(closes, i)
                if slot is not None:
                    exit_now = (ibs > 0.8) if slot["src"] == "ibs" else (r2 is not None and r2 > 90)
                    if exit_now or i - slot["i"] >= 10:
                        if slot["src"] == "rsi2":
                            ru = risk_unit_at(closes, slot["i"])
                            if ru > 0:
                                days = max((times[i] - times[slot["i"]]).days, 1)
                                pnl = closes[i] - closes[slot["i"]] - COSTS[symbol] - FINANCING_PER_DAY * closes[slot["i"]] * days
                                incremental.append((str(times[i]), pnl / ru))
                        slot = None
                if slot is None:
                    if ibs < 0.2:
                        slot = {"src": "ibs", "i": i}  # IBS keeps priority
                    elif r2 is not None and r2 < 10:
                        slot = {"src": "rsi2", "i": i}
        summary = book([r for _, r in incremental]); quarters = quarterly(incremental)
        report["hypotheses"]["H7_rsi2_incremental_gate"] = {**summary, **quarters, "gate": gate_check(summary, quarters)}
    finally:
        connector.shutdown()

    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    for name, data in report["hypotheses"].items():
        if "trades" in data:
            print(f"{name:28s} {data['trades']:4d} trades | WR {data['win_rate']:5.1f}% | net {data['net_rr']:8.1f}R | "
                  f"rwPF {data['rw_pf']:5.2f} | exp {data['expectancy']:+.3f}R | q+ {data.get('positive_share', '-')} | GATE {'PASS' if data.get('gate') else 'fail'}")
        else:
            print(f"{name}: {json.dumps({k: (v if not isinstance(v, dict) else v) for k, v in list(data.items())[:3]}, default=str)[:220]}")
    print(f"report -> {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
