"""RESEARCH — can the one-position-per-symbol rule be relaxed profitably?

The audited book allows a single open position per symbol. Round-1 research
showed that loosening the ENTRY threshold lost money by slot-crowding: the
extra shallow-dip trades occupied the slot and blocked deeper entries worth
~0.7-0.8R each. This tests the other side of that coin — keep the proven
IBS<0.2 entry, but allow more concurrent positions.

Variants (rules fixed before looking at results):
  A base        1 position/symbol                     (the audited book)
  B two         up to 2/symbol, any new signal
  C two_deeper  up to 2/symbol, only if price < the open position's entry
  D three       up to 3/symbol, any new signal
  E unlimited   every signal taken
  F deeper_only unlimited, but each add must be below the last entry
  G aged        up to 2/symbol, second only after the first is >=3 days old

EQUAL-RISK COMPARISON: raw variants risk more simply by holding more, which
inflates return without any edge improvement. Each variant is therefore ALSO
scored with per-trade size divided by its average concurrent exposure, so the
books are compared at the same average risk. A variant only earns promotion
if it improves the RISK-ADJUSTED figure, not just the raw one.

Nothing here is wired to the live engines.
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
GATE = {"min_rwpf": 1.10, "min_positive_quarter_share": 0.6}
REPORT = PROJECT_ROOT / "data" / "reports" / "slot_rules_research.json"

VARIANTS = {
    "A_base_1_per_symbol": {"max_open": 1, "deeper_only": False, "min_age_days": 0},
    "B_two_any": {"max_open": 2, "deeper_only": False, "min_age_days": 0},
    "C_two_deeper": {"max_open": 2, "deeper_only": True, "min_age_days": 0},
    "D_three_any": {"max_open": 3, "deeper_only": False, "min_age_days": 0},
    "E_unlimited": {"max_open": 99, "deeper_only": False, "min_age_days": 0},
    "F_unlimited_deeper": {"max_open": 99, "deeper_only": True, "min_age_days": 0},
    "G_two_aged": {"max_open": 2, "deeper_only": False, "min_age_days": 3},
}


def run_symbol(symbol: str, frame: pd.DataFrame, rule: dict) -> tuple[list[dict], list[int]]:
    """Return closed trades and the per-bar count of open positions."""
    highs = [float(v) for v in frame["high"]]
    lows = [float(v) for v in frame["low"]]
    closes = [float(v) for v in frame["close"]]
    times = [pd.Timestamp(v) for v in frame["time"]]
    cost = COSTS.get(symbol, 2.0)
    trades: list[dict] = []
    open_positions: list[dict] = []
    occupancy: list[int] = []

    for i in range(VOL_WINDOW + 3, len(closes)):
        rng = highs[i] - lows[i]
        ibs = (closes[i] - lows[i]) / rng if rng > 0 else 0.5
        # Exits first: each position lives or dies on its own terms.
        still_open = []
        for pos in open_positions:
            held = i - pos["i"]
            if ibs > IBS_EXIT or held >= MAX_HOLD:
                days = max((times[i] - times[pos["i"]]).days, 1)
                pnl = closes[i] - closes[pos["i"]] - cost - FINANCING_PER_DAY * closes[pos["i"]] * days
                trades.append({
                    "symbol": symbol, "timestamp": str(times[i]), "entry_time": str(times[pos["i"]]),
                    "entry_price": round(closes[pos["i"]], 5), "risk_unit": round(pos["risk_unit"], 5),
                    "hold_days": held, "pnl_points": round(pnl, 5),
                    "rr": round(pnl / pos["risk_unit"], 4),
                })
            else:
                still_open.append(pos)
        open_positions = still_open

        if ibs < IBS_ENTRY and len(open_positions) < rule["max_open"]:
            moves = [abs(closes[j] - closes[j - 1]) for j in range(i - VOL_WINDOW, i)]
            risk_unit = sum(moves) / len(moves) if moves else 0.0
            allowed = risk_unit > 0
            if allowed and rule["deeper_only"] and open_positions:
                allowed = closes[i] < min(closes[p["i"]] for p in open_positions)
            if allowed and rule["min_age_days"] and open_positions:
                allowed = min(i - p["i"] for p in open_positions) >= rule["min_age_days"]
            if allowed:
                open_positions.append({"i": i, "risk_unit": risk_unit})
        occupancy.append(len(open_positions))
    return trades, occupancy


def book(trades: list[dict], occupancy: list[int]) -> dict[str, Any]:
    rrs = [t["rr"] for t in trades]
    if not rrs:
        return {"trades": 0}
    wins = [r for r in rrs if r > 0]
    gross_loss = abs(sum(r for r in rrs if r < 0))
    by_quarter: dict[str, float] = defaultdict(float)
    for t in trades:
        stamp = pd.Timestamp(t["timestamp"])
        by_quarter[f"{stamp.year}-Q{(stamp.month - 1) // 3 + 1}"] += t["rr"]
    active = [v for v in by_quarter.values() if v != 0]
    avg_occ = sum(occupancy) / len(occupancy) if occupancy else 0.0
    net = sum(rrs)
    return {
        "trades": len(rrs),
        "win_rate": round(len(wins) / len(rrs) * 100, 1),
        "net_rr": round(net, 2),
        "rw_pf": round(sum(wins) / gross_loss, 2) if gross_loss else round(sum(wins), 2),
        "expectancy": round(net / len(rrs), 3),
        "avg_concurrent": round(avg_occ, 2),
        "max_concurrent": max(occupancy) if occupancy else 0,
        # Equal-risk view: holding more positions is more risk, so normalise.
        "net_rr_per_unit_exposure": round(net / avg_occ, 2) if avg_occ else 0.0,
        "positive_quarter_share": round(sum(1 for v in active if v > 0) / len(active), 2) if active else 0.0,
    }


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
        frames = {}
        for s in SYMBOLS:
            frames[s] = connector.get_historical_candles(s, "D1", count=DAILY_BARS).iloc[:-1].reset_index(drop=True)
        common = max(f["time"].min() for f in frames.values())
        frames = {s: f[f["time"] >= common].reset_index(drop=True) for s, f in frames.items()}
        print(f"common window from {common} | symbols {', '.join(SYMBOLS)}\n")
        report: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "status": "RESEARCH_ONLY",
                                  "common_window_start": str(common), "variants": {}}
        header = f"{'variant':22s} {'trades':>7s} {'WR%':>6s} {'netR':>9s} {'rwPF':>6s} {'avgOpen':>8s} {'maxOpen':>8s} {'netR/exposure':>14s} {'q+':>5s}"
        print(header); print("-" * len(header))
        best_raw, best_adj = None, None
        for name, rule in VARIANTS.items():
            all_trades: list[dict] = []
            occ_total: list[int] = []
            per_symbol_occ: dict[str, list[int]] = {}
            for s, f in frames.items():
                t, occ = run_symbol(s, f, rule)
                all_trades.extend(t)
                per_symbol_occ[s] = occ
            length = min(len(v) for v in per_symbol_occ.values())
            occ_total = [sum(per_symbol_occ[s][k] for s in per_symbol_occ) for k in range(length)]
            summary = book(all_trades, occ_total)
            summary["passes_gate"] = (
                summary.get("rw_pf", 0) >= GATE["min_rwpf"]
                and summary.get("positive_quarter_share", 0) >= GATE["min_positive_quarter_share"]
            )
            report["variants"][name] = {**summary, "rule": rule}
            print(f"{name:22s} {summary['trades']:7d} {summary['win_rate']:6.1f} {summary['net_rr']:9.1f} "
                  f"{summary['rw_pf']:6.2f} {summary['avg_concurrent']:8.2f} {summary['max_concurrent']:8d} "
                  f"{summary['net_rr_per_unit_exposure']:14.1f} {summary['positive_quarter_share']:5.2f}"
                  f"{'' if summary['passes_gate'] else '   FAILS GATE'}")
            if best_raw is None or summary["net_rr"] > report["variants"][best_raw]["net_rr"]:
                best_raw = name
            if best_adj is None or summary["net_rr_per_unit_exposure"] > report["variants"][best_adj]["net_rr_per_unit_exposure"]:
                best_adj = name
        report["best_raw_net_rr"] = best_raw
        report["best_risk_adjusted"] = best_adj
        print(f"\nmost raw R: {best_raw}")
        print(f"most R per unit of exposure (the honest comparison): {best_adj}")
        REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"report -> {REPORT.relative_to(PROJECT_ROOT)}")
    finally:
        connector.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
