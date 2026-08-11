"""Intraday time-series momentum engine (Gao/Han/Li/Zhou), judged by the gate.

Documented effect: the first half-hour return of the US cash session (including
the overnight gap) predicts the last half-hour return, and session drift more
broadly. Two parameter-free variants are evaluated over the full history with
real execution costs — no fitting, so every day is out-of-sample:

  last30:  enter 15:30 ET in the direction of the first-half-hour return,
           exit at the 16:00 ET close (the paper's exact trade).
  session: enter 10:00 ET in the signal direction, exit at the 16:00 ET close.

Sessions are anchored in America/New_York time, so US daylight-saving shifts
are handled correctly. Results are normalized to R units by a trailing
20-session average absolute session move, making rwPF comparable across
engines. Promotion requires positive net R, rwPF > 1.10, and a majority of
profitable quarters.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.backtesting.backtest_engine import BacktestEngine, BacktestEngineError
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from scripts.run_backtest_365d import trim_to_lookback_days
from scripts.run_extended_backtest import quarter_key
from scripts.run_sizing_walkforward import book_metrics

DAYS = 1095
SYMBOLS = ("US30", "NAS100")
NY = ZoneInfo("America/New_York")
# MT5 timestamps are BROKER SERVER TIME, not UTC. Measured empirically on
# 2026-08-11 against wall-clock UTC: MetaQuotes-Demo runs UTC+3 (EET summer).
# Without this correction the session map is shifted three hours (audit F1).
SERVER_UTC_OFFSET_HOURS = 3
ROUND_TRIP_COSTS = {"US30": 5.0, "NAS100": 3.5}  # Points, spread+slippage (validated model).
RISK_UNIT_WINDOW = 20
REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "intraday_momentum_report.json"
PROMOTION = {"min_net_rr": 0.0, "min_rwpf": 1.10, "min_positive_quarter_share": 0.5}


def main() -> int:
    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
        engine = BacktestEngine(connector=connector)
        report: dict[str, Any] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "requested_days": DAYS,
            "server_utc_offset_hours": SERVER_UTC_OFFSET_HOURS,
            "session_spans": {},
            "variants": {},
        }
        all_trades: dict[str, list[dict[str, Any]]] = {"last30": [], "session": []}
        for symbol in SYMBOLS:
            candles = trim_to_lookback_days(engine.fetch_backtest_candles(symbol, DAYS), DAYS)
            sessions = build_sessions(candles)
            report["session_spans"][symbol] = {
                "sessions": len(sessions),
                "first": sessions[0]["date"] if sessions else None,
                "last": sessions[-1]["date"] if sessions else None,
            }
            for variant in ("last30", "session"):
                all_trades[variant].extend(evaluate_variant(symbol, sessions, variant))
        for variant, trades in all_trades.items():
            report["variants"][variant] = summarize(trades)
            print_variant(variant, report["variants"][variant])
        REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        print(f"Report: {REPORT_PATH.relative_to(PROJECT_ROOT)}")
        return 0
    except (MT5ConnectorError, BacktestEngineError, ValueError) as exc:
        print(f"Intraday momentum backtest failed: {exc}")
        return 1
    finally:
        connector.shutdown()


def build_sessions(candles: pd.DataFrame) -> list[dict[str, Any]]:
    """Group M15 bars into US cash sessions keyed by New York local time.

    Bars are timestamped by open in UTC. For each ET date we need the closes of
    the 09:45, 15:15, and 15:45 bars (i.e., prices at 10:00, 15:30, 16:00 ET)
    plus the prior session's 16:00 close for the overnight-inclusive signal.
    """
    frame = candles.copy()
    true_utc = frame["time"] - pd.Timedelta(hours=SERVER_UTC_OFFSET_HOURS)
    frame["et"] = true_utc.dt.tz_convert(NY)
    frame["et_date"] = frame["et"].dt.date
    frame["et_hm"] = frame["et"].dt.strftime("%H:%M")
    sessions: list[dict[str, Any]] = []
    prior_close: float | None = None
    for et_date, day in frame.groupby("et_date", sort=True):
        bars = {row.et_hm: float(row.close) for row in day.itertuples()}
        open_1000 = bars.get("09:45")
        close_1530 = bars.get("15:15")
        close_1600 = bars.get("15:45")
        if open_1000 is None or close_1600 is None:
            # Half-day early closes must still roll prior_close forward so the
            # next overnight signal is measured from the true last price
            # (audit F3); fall back to the day's final available bar.
            day_last_close = float(day.iloc[-1]["close"]) if len(day) else None
            if close_1600 is not None:
                prior_close = close_1600
            elif open_1000 is not None and day_last_close is not None:
                prior_close = day_last_close
            continue
        if prior_close is not None:
            sessions.append(
                {
                    "date": str(et_date),
                    "prior_close": prior_close,
                    "price_1000": open_1000,
                    "price_1530": close_1530,
                    "price_1600": close_1600,
                }
            )
        prior_close = close_1600
    return sessions


def evaluate_variant(symbol: str, sessions: list[dict[str, Any]], variant: str) -> list[dict[str, Any]]:
    """Return costed, R-normalized trades for one variant on one symbol."""
    cost = ROUND_TRIP_COSTS[symbol]
    trades: list[dict[str, Any]] = []
    session_moves: list[float] = []
    for entry_session in sessions:
        signal = entry_session["price_1000"] - entry_session["prior_close"]
        session_move = abs(entry_session["price_1600"] - entry_session["price_1000"])
        risk_unit = (
            sum(session_moves[-RISK_UNIT_WINDOW:]) / min(len(session_moves), RISK_UNIT_WINDOW)
            if session_moves
            else None
        )
        session_moves.append(session_move)
        if signal == 0 or risk_unit is None or risk_unit <= 0 or len(session_moves) <= RISK_UNIT_WINDOW:
            continue  # Warm-up period for the risk normalizer; no trade.
        direction = 1 if signal > 0 else -1
        if variant == "last30":
            if entry_session["price_1530"] is None:
                continue
            pnl_points = direction * (entry_session["price_1600"] - entry_session["price_1530"]) - cost
        else:
            pnl_points = direction * (entry_session["price_1600"] - entry_session["price_1000"]) - cost
        trades.append(
            {
                "symbol": symbol,
                "timestamp": f"{entry_session['date']}T16:00:00-05:00",
                "variant": variant,
                "direction": "long" if direction > 0 else "short",
                "pnl_points": round(pnl_points, 2),
                "rr": round(pnl_points / risk_unit, 4),
            }
        )
    return trades


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    rrs = [float(trade["rr"]) for trade in trades]
    metrics = book_metrics(rrs)
    quarters: dict[str, float] = {}
    for trade in trades:
        quarter = quarter_key(trade["timestamp"])
        quarters[quarter] = round(quarters.get(quarter, 0.0) + float(trade["rr"]), 2)
    positive_share = (
        sum(1 for value in quarters.values() if value > 0) / len(quarters) if quarters else 0.0
    )
    by_symbol = {
        symbol: book_metrics([float(t["rr"]) for t in trades if t["symbol"] == symbol])
        for symbol in SYMBOLS
    }
    promote = (
        metrics["net_rr"] > PROMOTION["min_net_rr"]
        and metrics["risk_weighted_pf"] > PROMOTION["min_rwpf"]
        and positive_share > PROMOTION["min_positive_quarter_share"]
    )
    return {
        "trades": len(trades),
        "wins": sum(1 for rr in rrs if rr > 0),
        "win_rate": round(sum(1 for rr in rrs if rr > 0) / len(rrs) * 100, 2) if rrs else 0.0,
        **metrics,
        "by_symbol": by_symbol,
        "per_quarter_net_rr": dict(sorted(quarters.items())),
        "positive_quarter_share": round(positive_share, 2),
        "promotion_rule": PROMOTION,
        "promote": promote,
    }


def print_variant(variant: str, summary: dict[str, Any]) -> None:
    print(
        f"{variant}: {summary['trades']} trades | WR {summary['win_rate']}% | "
        f"net {summary['net_rr']}R | rwPF {summary['risk_weighted_pf']} | "
        f"{summary['positive_quarter_share'] * 100:.0f}% quarters positive | promote={summary['promote']}"
    )
    for symbol, row in summary["by_symbol"].items():
        print(f"  {symbol}: net {row['net_rr']}R | rwPF {row['risk_weighted_pf']}")


if __name__ == "__main__":
    raise SystemExit(main())
