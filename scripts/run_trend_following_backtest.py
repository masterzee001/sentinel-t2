"""Time-series momentum (trend following) engine, judged by the gate.

Classic Moskowitz/Ooi/Pedersen construction, PRE-REGISTERED to prevent
cherry-picking: the promotion verdict is taken ONLY from the primary config —
sign of the trailing 252-day return, rebalanced every 21 trading days, across
the full symbol universe. 63d and 126d lookbacks are reported as robustness
context, never as the verdict.

Costs are explicit: spread+slippage charged on every position change, plus CFD
overnight financing at 0.02%/day of price while holding (~7.3%/yr, symmetric —
a deliberately conservative assumption; a no-financing sensitivity line is
reported so the assumption's weight is visible). Monthly pnl is R-normalized
by the trailing average absolute 21-day move (vol scaling in the paper's
spirit), making rwPF comparable across engines.
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
from scripts.run_extended_backtest import quarter_key
from scripts.run_sizing_walkforward import book_metrics

SYMBOLS = ("US30", "NAS100", "XAUUSD", "EURUSD", "GBPUSD", "BTCUSD")
DAILY_BARS = 1400  # ~5.5 years requested; broker serves what it has.
# Calendar-based horizons (audit F4/F6): the rebalance grid is anchored to
# calendar month starts (reproducible run-to-run) and lookbacks are calendar
# days (identical horizons for 5-day and 7-day instruments).
PRIMARY_LOOKBACK_DAYS = 365
ROBUSTNESS_LOOKBACK_DAYS = (91, 182)
VOL_WINDOW_MONTHS = 6
MIN_MONTHS_OF_DATA = 15
FINANCING_PER_DAY = 0.0002  # 0.02%/CALENDAR day of price while holding (~7.3%/yr).
ROUND_TRIP_COSTS = {
    "US30": 5.0,
    "NAS100": 3.5,
    "XAUUSD": 0.45,
    "EURUSD": 0.00016,
    "GBPUSD": 0.0002,
    "BTCUSD": 40.0,
}
REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "trend_following_report.json"
PROMOTION = {"min_net_rr": 0.0, "min_rwpf": 1.10, "min_positive_quarter_share": 0.5}


def main() -> int:
    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
        closes_by_symbol: dict[str, pd.DataFrame] = {}
        for symbol in SYMBOLS:
            try:
                candles = connector.get_historical_candles(symbol, "D1", count=DAILY_BARS)
                closes_by_symbol[symbol] = candles[["time", "close"]].reset_index(drop=True)
            except Exception as exc:
                print(f"{symbol}: daily candles unavailable ({exc})")
        report: dict[str, Any] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "primary_lookback_days": PRIMARY_LOOKBACK_DAYS,
            "financing_per_day": FINANCING_PER_DAY,
            "data_span": {
                symbol: {"bars": int(len(frame)), "first": str(frame["time"].min()), "last": str(frame["time"].max())}
                for symbol, frame in closes_by_symbol.items()
            },
            "configs": {},
        }
        for lookback in (PRIMARY_LOOKBACK_DAYS, *ROBUSTNESS_LOOKBACK_DAYS):
            trades = []
            for symbol, frame in closes_by_symbol.items():
                trades.extend(evaluate_symbol(symbol, frame, lookback, financing=True))
            no_financing = []
            for symbol, frame in closes_by_symbol.items():
                no_financing.extend(evaluate_symbol(symbol, frame, lookback, financing=False))
            summary = summarize(trades)
            summary["no_financing_sensitivity"] = {
                key: book_metrics([float(t["rr"]) for t in no_financing])[key]
                for key in ("net_rr", "risk_weighted_pf")
            }
            summary["is_primary"] = lookback == PRIMARY_LOOKBACK_DAYS
            if not summary["is_primary"]:
                summary["promote"] = None  # Robustness context only, never a verdict.
            report["configs"][f"lookback_{lookback}d"] = summary
            label = "PRIMARY" if lookback == PRIMARY_LOOKBACK_DAYS else "robustness"
            print(
                f"lookback {lookback}d ({label}): {summary['periods']} periods | net {summary['net_rr']}R | "
                f"rwPF {summary['risk_weighted_pf']} | {summary['positive_quarter_share'] * 100:.0f}% quarters+ | "
                f"promote={summary['promote']} | no-financing net {summary['no_financing_sensitivity']['net_rr']}R"
            )
            for symbol, row in summary["by_symbol"].items():
                print(f"  {symbol}: net {row['net_rr']}R | rwPF {row['risk_weighted_pf']}")
        REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        print(f"Report: {REPORT_PATH.relative_to(PROJECT_ROOT)}")
        return 0
    except (MT5ConnectorError, ValueError) as exc:
        print(f"Trend following backtest failed: {exc}")
        return 1
    finally:
        connector.shutdown()


def month_start_indices(times: list[pd.Timestamp]) -> list[int]:
    """Index of the first bar of each calendar month (reproducible grid)."""
    indices: list[int] = []
    seen: set[tuple[int, int]] = set()
    for index, stamp in enumerate(times):
        key = (stamp.year, stamp.month)
        if key not in seen:
            seen.add(key)
            indices.append(index)
    return indices


def lookback_index(times: list[pd.Timestamp], index: int, lookback_days: int) -> int | None:
    """Last bar at or before times[index] - lookback_days (calendar horizon)."""
    cutoff = times[index] - pd.Timedelta(days=lookback_days)
    for j in range(index, -1, -1):
        if times[j] <= cutoff:
            return j
    return None


def evaluate_symbol(
    symbol: str, frame: pd.DataFrame, lookback_days: int, *, financing: bool
) -> list[dict[str, Any]]:
    """Return calendar-month rebalance results for one symbol, costed and R-normalized."""
    closes = [float(value) for value in frame["close"]]
    times = [pd.Timestamp(value) for value in frame["time"]]
    cost = ROUND_TRIP_COSTS.get(symbol, 0.0)
    anchors = month_start_indices(times)
    if len(anchors) < MIN_MONTHS_OF_DATA:
        return []  # Data-quality guard: too little history for a trend verdict.
    results: list[dict[str, Any]] = []
    position = 0  # -1, 0, +1
    monthly_moves: list[float] = []
    for anchor_pos in range(len(anchors) - 1):
        index = anchors[anchor_pos]
        next_index = anchors[anchor_pos + 1]
        month_move = closes[next_index] - closes[index]
        signal_index = lookback_index(times, index, lookback_days)
        # Risk unit strictly trails the evaluated month (no self-normalization).
        trailing = monthly_moves[-VOL_WINDOW_MONTHS:]
        risk_unit = sum(abs(move) for move in trailing) / len(trailing) if trailing else 0.0
        monthly_moves.append(month_move)
        if signal_index is None or risk_unit <= 0 or len(monthly_moves) <= VOL_WINDOW_MONTHS:
            continue
        signal_return = closes[index] - closes[signal_index]
        target = 1 if signal_return > 0 else (-1 if signal_return < 0 else 0)
        pnl_points = target * month_move
        if target != position:
            pnl_points -= cost  # Charged on every position change (incl. flips).
        if financing and target != 0:
            held_calendar_days = max((times[next_index] - times[index]).days, 1)
            pnl_points -= FINANCING_PER_DAY * closes[index] * held_calendar_days
        position = target
        results.append(
            {
                "symbol": symbol,
                "timestamp": str(times[next_index]),
                "position": target,
                "pnl_points": round(pnl_points, 5),
                "rr": round(pnl_points / risk_unit, 4),
            }
        )
    return results


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    rrs = [float(trade["rr"]) for trade in trades]
    metrics = book_metrics(rrs)
    quarters: dict[str, float] = {}
    for trade in trades:
        quarter = quarter_key(trade["timestamp"])
        if quarter:
            quarters[quarter] = round(quarters.get(quarter, 0.0) + float(trade["rr"]), 2)
    positive_share = (
        sum(1 for value in quarters.values() if value > 0) / len(quarters) if quarters else 0.0
    )
    promote = (
        metrics["net_rr"] > PROMOTION["min_net_rr"]
        and metrics["risk_weighted_pf"] > PROMOTION["min_rwpf"]
        and positive_share > PROMOTION["min_positive_quarter_share"]
    )
    return {
        "periods": len(trades),
        **metrics,
        "by_symbol": {
            symbol: book_metrics([float(t["rr"]) for t in trades if t["symbol"] == symbol])
            for symbol in SYMBOLS
            if any(t["symbol"] == symbol for t in trades)
        },
        "per_quarter_net_rr": dict(sorted(quarters.items())),
        "positive_quarter_share": round(positive_share, 2),
        "promotion_rule": PROMOTION,
        "promote": promote,
    }


if __name__ == "__main__":
    raise SystemExit(main())
