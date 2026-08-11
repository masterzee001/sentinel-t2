"""RESEARCH ONLY — aggressiveness options for the mean-reversion book.

Explicitly NOT a promotion run and NOT wired to anything live. Quantifies,
against the same D1 data / costs / causality rules as the audited backtest:

  1. MORE TRADES via looser IBS entry thresholds (0.25, 0.30), a tighter
     exit (0.75), and shorter/longer hold caps.
  2. A SHORT side (IBS > 0.8 short, exit IBS < 0.2 / timeout) — the mirror
     of the audited long book, same costs + financing charged.
  3. NEW SYMBOLS: every index/metal on this server with enough history,
     baseline IBS(0.2/0.8/10d). Costs for new symbols are ESTIMATED from
     current spreads calibrated against the three known round-trip costs —
     labeled as estimates in the output.
  4. An RSI2(<10/>90) union book to see how many genuinely NEW trade days a
     second entry signal adds on the audited universe.

Promotion rules still apply before anything here goes live: pre-registered
primary, out-of-sample gate, adversarial audit. This script only maps the
territory.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError

AUDITED_SYMBOLS = ("US30", "NAS100", "US500")
KNOWN_COSTS = {"US30": 5.0, "NAS100": 3.5, "US500": 0.85}  # audited round trips
CANDIDATE_SYMBOLS = ("DE40", "JP225", "UK100", "AUS200", "HK50", "FR40", "EU50", "US2000", "XAUUSD", "XAGUSD")
DAILY_BARS = 1400
MIN_BARS = 600
VOL_WINDOW = 20
FINANCING_PER_DAY = 0.0002
RSI_PERIOD = 2
REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "aggressive_options_research.json"


def rsi(closes: list[float], index: int, period: int = RSI_PERIOD) -> float | None:
    if index < period + 1:
        return None
    gains = losses = 0.0
    for j in range(index - period + 1, index + 1):
        change = closes[j] - closes[j - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    if gains + losses == 0:
        return 50.0
    return 100.0 * gains / (gains + losses)


def evaluate(
    symbol: str,
    frame: pd.DataFrame,
    cost: float,
    *,
    side: str = "long",
    entry_threshold: float = 0.2,
    exit_threshold: float = 0.8,
    max_hold: int = 10,
    signal: str = "ibs",
) -> list[dict[str, Any]]:
    """Causal close-to-close mean reversion, mirroring the audited engine."""
    highs = [float(v) for v in frame["high"]]
    lows = [float(v) for v in frame["low"]]
    closes = [float(v) for v in frame["close"]]
    times = [pd.Timestamp(v) for v in frame["time"]]
    trades: list[dict[str, Any]] = []
    entry_index: int | None = None
    sign = 1 if side == "long" else -1
    for index in range(VOL_WINDOW + RSI_PERIOD + 1, len(closes)):
        bar_range = highs[index] - lows[index]
        ibs = (closes[index] - lows[index]) / bar_range if bar_range > 0 else 0.5
        if signal == "ibs":
            value = ibs
        else:
            value = rsi(closes, index)
            if value is None:
                continue
            value = value / 100.0  # scale RSI to 0..1 so thresholds read alike
        if side == "long":
            entry_signal = value < entry_threshold
            exit_signal = value > exit_threshold
        else:
            entry_signal = value > exit_threshold
            exit_signal = value < entry_threshold
        if entry_index is None:
            if entry_signal:
                entry_index = index
            continue
        held = index - entry_index
        if exit_signal or held >= max_hold:
            moves = [abs(closes[j] - closes[j - 1]) for j in range(entry_index - VOL_WINDOW, entry_index)]
            risk_unit = sum(moves) / len(moves) if moves else 0.0
            if risk_unit > 0:
                held_calendar = max((times[index] - times[entry_index]).days, 1)
                pnl_points = (
                    sign * (closes[index] - closes[entry_index])
                    - cost
                    - FINANCING_PER_DAY * closes[entry_index] * held_calendar
                )
                trades.append(
                    {
                        "symbol": symbol,
                        "entry_time": str(times[entry_index]),
                        "rr": round(pnl_points / risk_unit, 4),
                    }
                )
            entry_index = None
    return trades


def book(trades: list[dict[str, Any]], years: float) -> dict[str, Any]:
    rrs = [t["rr"] for t in trades]
    wins = [r for r in rrs if r > 0]
    losses = [r for r in rrs if r < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(rrs),
        "trades_per_year": round(len(rrs) / years, 1) if years else 0,
        "win_rate": round(len(wins) / len(rrs) * 100, 1) if rrs else 0.0,
        "net_rr": round(sum(rrs), 2),
        "rw_pf": round(gross_win / gross_loss, 2) if gross_loss else round(gross_win, 2),
        "expectancy_rr": round(sum(rrs) / len(rrs), 3) if rrs else 0.0,
        "worst_trade_rr": round(min(rrs), 2) if rrs else 0.0,
    }


def main() -> int:
    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
    except MT5ConnectorError as exc:
        print(f"MT5 connection failed: {exc}")
        return 1
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY_NOT_PROMOTED",
        "variants": {},
        "new_symbols": {},
        "notes": [],
    }
    try:
        connector.supported_symbols = frozenset(
            set(connector.supported_symbols) | set(AUDITED_SYMBOLS) | set(CANDIDATE_SYMBOLS)
        )
        frames: dict[str, pd.DataFrame] = {}
        for symbol in AUDITED_SYMBOLS:
            frames[symbol] = connector.get_historical_candles(symbol, "D1", count=DAILY_BARS).iloc[:-1].reset_index(drop=True)
        common_start = max(frame["time"].min() for frame in frames.values())
        frames = {s: f[f["time"] >= common_start].reset_index(drop=True) for s, f in frames.items()}
        years = (pd.Timestamp(frames["US30"]["time"].max()) - pd.Timestamp(common_start)).days / 365.25

        def run_variant(name: str, **kwargs: Any) -> dict[str, Any]:
            trades: list[dict[str, Any]] = []
            for symbol, frame in frames.items():
                trades.extend(evaluate(symbol, frame, KNOWN_COSTS[symbol], **kwargs))
            summary = book(trades, years)
            report["variants"][name] = {**summary, "params": {k: v for k, v in kwargs.items()}}
            print(f"{name:34s} {summary['trades']:4d} trades ({summary['trades_per_year']:5.1f}/y) | "
                  f"WR {summary['win_rate']:5.1f}% | net {summary['net_rr']:8.1f}R | rwPF {summary['rw_pf']:5.2f} | "
                  f"exp {summary['expectancy_rr']:+.3f}R | worst {summary['worst_trade_rr']}R", flush=True)
            return summary

        print(f"Common window from {common_start} ({years:.1f}y)\n-- audited universe, real costs --", flush=True)
        run_variant("V0 baseline long .2/.8/10d")
        run_variant("V1 looser entry .25", entry_threshold=0.25)
        run_variant("V2 looser entry .30", entry_threshold=0.30)
        run_variant("V3 entry .25 exit .75", entry_threshold=0.25, exit_threshold=0.75)
        run_variant("V4 faster recycle hold 5d", max_hold=5)
        run_variant("V5 longer hold 15d", max_hold=15)
        run_variant("V6 SHORT side .8/.2/10d", side="short")
        run_variant("V7 SHORT extreme .9/.3/5d", side="short", exit_threshold=0.9, entry_threshold=0.3, max_hold=5)
        run_variant("V8 RSI2 long <10/>90", signal="rsi2", entry_threshold=0.10, exit_threshold=0.90)

        # Union overlap: how many NEW entry days does RSI2 add over IBS?
        ibs_days = set()
        rsi_days = set()
        for symbol, frame in frames.items():
            ibs_days |= {(symbol, t["entry_time"][:10]) for t in evaluate(symbol, frame, KNOWN_COSTS[symbol])}
            rsi_days |= {(symbol, t["entry_time"][:10]) for t in evaluate(symbol, frame, KNOWN_COSTS[symbol], signal="rsi2", entry_threshold=0.10, exit_threshold=0.90)}
        report["rsi2_union"] = {
            "ibs_entries": len(ibs_days),
            "rsi2_entries": len(rsi_days),
            "rsi2_only_new": len(rsi_days - ibs_days),
        }
        print(f"RSI2 union: adds {len(rsi_days - ibs_days)} genuinely new entry days over IBS's {len(ibs_days)}", flush=True)

        # Cost calibration for new symbols: k = known_cost / current spread price.
        ratios = []
        for symbol in AUDITED_SYMBOLS:
            broker_name = connector.broker_symbol(symbol)
            info = connector.mt5.symbol_info(broker_name)
            if info and info.spread and info.point:
                ratios.append(KNOWN_COSTS[symbol] / (info.spread * info.point))
        k = sorted(ratios)[len(ratios) // 2] if ratios else 2.2
        report["notes"].append(f"new-symbol costs = {round(k, 2)} x current spread (median calibration on audited universe)")

        print(f"\n-- new symbols, baseline IBS .2/.8/10d, ESTIMATED costs (k={k:.2f}) --", flush=True)
        for symbol in CANDIDATE_SYMBOLS:
            try:
                broker_name = connector.broker_symbol(symbol)
                info = connector.mt5.symbol_info(broker_name)
                if info is None or not info.point:
                    print(f"{symbol}: not available", flush=True)
                    continue
                frame = connector.get_historical_candles(symbol, "D1", count=DAILY_BARS).iloc[:-1].reset_index(drop=True)
                if len(frame) < MIN_BARS:
                    print(f"{symbol}: only {len(frame)} bars (<{MIN_BARS}) - skipped", flush=True)
                    continue
                cost = k * info.spread * info.point
                sym_years = (pd.Timestamp(frame["time"].max()) - pd.Timestamp(frame["time"].min())).days / 365.25
                summary = book(evaluate(symbol, frame, cost), sym_years)
                report["new_symbols"][symbol] = {**summary, "est_round_trip_cost": round(cost, 4), "bars": len(frame), "years": round(sym_years, 1)}
                print(f"{symbol:8s} {summary['trades']:4d} trades ({summary['trades_per_year']:5.1f}/y) | WR {summary['win_rate']:5.1f}% | "
                      f"net {summary['net_rr']:8.1f}R | rwPF {summary['rw_pf']:5.2f} | cost~{cost:.2f} | {sym_years:.1f}y", flush=True)
            except Exception as exc:
                print(f"{symbol}: failed ({exc})", flush=True)
    finally:
        connector.shutdown()

    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nreport -> {REPORT_PATH.relative_to(PROJECT_ROOT)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
