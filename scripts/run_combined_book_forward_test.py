"""Combined-book forward test: what would TODAY'S live configuration have done
over the last 3 years, starting from the current account equity?

Replays both engines' audited trade streams chronologically on ONE account:
  champion  (US30+NAS100 M15 breakout, same-day resolution) sized at 0.5%
            flat with min-lot acceptance cap 1.5% — as run_champion_paper
  meanrev   (IBS daily, US30+NAS100+US500, multi-day holds) sized at 3.0%
            over 3 risk-units (1%/risk-unit) with min-lot acceptance cap 6%
            — as run_mean_reversion_live. Since 2026-08-11 live sends NO
            server-side stop (audited no-stop book); --stop-units N models
            a disaster stop against daily opens/lows with gap fills for
            sensitivity comparisons.

Sizing uses the REAL DemoOrderExecutor.lot_size (broker minimum 0.1, step
0.1, $1/point — measured live on MetaQuotes-Demo 2026-08-11) against MARKED
equity (realized + floating, what MT5 reports live), compounding. Live
refusal gates are mirrored: implausibly-small stops and the 5-lot ceiling.
Reports whether the live-book tripwires (daily loss 18%, DD 50%/60%, 15
trades/day) would ever have fired.

Intraday chronology per day: (1) MR disaster stops (can fire any time),
(2) champion trades (12:00-15:00 broker, resolved by ~21:00), (3) MR
scheduled exits at the 23:30+ close, (4) MR entries at the close, (5) mark.

Inputs (must exist):
  data/reports/champion_trades_3y.json      (scripts/export_champion_trades_3y.py)
  data/reports/meanrev_trades_export.json   (scripts/run_mean_reversion_backtest.py)

Known approximations (stated, not hidden):
  - Broker lot constraints/point values assumed constant over the window.
  - Disaster stop inactive during the entry evening's final minutes.
  - Champion intraday floating excursions not marked (same-day resolution).
  - Stopped-out trades pay full costs + financing to the stop day.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.live_paper.demo_order_executor import DemoOrderExecutor
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError

STARTING_EQUITY = 3000.0
CHAMPION_TRADES = PROJECT_ROOT / "data" / "reports" / "champion_trades_3y.json"
MEANREV_TRADES = PROJECT_ROOT / "data" / "reports" / "meanrev_trades_export.json"
REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "combined_book_forward_test.json"
SYMBOLS = ("US30", "NAS100", "US500")
DAILY_BARS = 1400
DISASTER_STOP_UNITS = 3.0
MAX_LOTS_PER_ORDER = 5.0  # live open_position refuses (not clips) above this
ROUND_TRIP_COSTS = {"US30": 5.0, "NAS100": 3.5, "US500": 0.85}
FINANCING_PER_DAY = 0.0002
TRIPWIRES = {"daily_loss_percent": 18.0, "internal_dd_percent": 50.0, "firm_dd_percent": 60.0, "max_trades_per_day": 15}


class _SizingMT5:
    """Broker constraints measured live on MetaQuotes-Demo (2026-08-11)."""

    def symbol_info(self, symbol: str):
        return SimpleNamespace(
            trade_tick_value=1.0, trade_tick_size=1.0, volume_min=0.1, volume_step=0.1, volume_max=100.0
        )


class _SizingConnector:
    def __init__(self) -> None:
        self.mt5 = _SizingMT5()


def make_sizer(risk_percent: float, min_lot_cap: float) -> DemoOrderExecutor:
    """Executor used ONLY for lot_size — the same code the live engines run."""
    return DemoOrderExecutor(
        _SizingConnector(),
        risk_governor=None,
        kill_switch_path=PROJECT_ROOT / "data" / "reports" / "_nonexistent_kill_switch",
        risk_percent=risk_percent,
        min_lot_risk_cap_percent=min_lot_cap,
    )


def day_of(timestamp: str) -> str:
    return str(pd.Timestamp(timestamp).date())


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mr-risk-per-unit", type=float, default=1.0,
                        help="RESEARCH: meanrev risk percent per risk-unit (live = 1.0).")
    parser.add_argument("--meanrev-trades", type=str, default=str(MEANREV_TRADES),
                        help="RESEARCH: alternate meanrev trade stream (e.g. a relaxed slot rule).")
    parser.add_argument("--max-open-per-symbol", type=int, default=1,
                        help="RESEARCH: concurrent meanrev positions allowed per symbol.")
    parser.add_argument("--max-open-total", type=int, default=0,
                        help="RESEARCH: cap on concurrent meanrev positions across ALL symbols (0 = no cap). "
                             "Bounds crash concentration, which per-symbol caps do not.")
    parser.add_argument("--max-lots", type=float, default=MAX_LOTS_PER_ORDER,
                        help="RESEARCH: per-order lot ceiling (live executor refuses above 5.0).")
    parser.add_argument("--stop-units", type=float, default=0.0,
                        help="Disaster stop in risk units; 0 (the live default since 2026-08-11) = "
                             "audited no-stop book, sizing still on 3 risk-units.")
    args = parser.parse_args()
    stop_units = float(args.stop_units)
    logger.remove()
    champion_raw = json.loads(CHAMPION_TRADES.read_text(encoding="utf-8"))["trades"]
    meanrev_raw = json.loads(Path(args.meanrev_trades).read_text(encoding="utf-8"))["trades"]
    champion_sizer = make_sizer(risk_percent=0.5, min_lot_cap=1.5)
    mr_risk_percent = 3.0 * float(args.mr_risk_per_unit)
    meanrev_sizer = make_sizer(risk_percent=mr_risk_percent, min_lot_cap=6.0)

    # Daily OHLC for stop checks and mark-to-market.
    connector = MT5Connector()
    try:
        connector.connect()
    except MT5ConnectorError as exc:
        print(f"MT5 required for daily bars: {exc}")
        return 1
    try:
        connector.supported_symbols = frozenset(set(connector.supported_symbols) | set(SYMBOLS))
        bars: dict[str, dict[str, dict[str, float]]] = {}
        today = datetime.now(UTC).date()
        for symbol in SYMBOLS:
            frame = connector.get_historical_candles(symbol, "D1", count=DAILY_BARS)
            # Drop the last bar ONLY if it is actually still forming — on a
            # weekend the position-0 bar is Friday's completed bar and
            # dropping it would silently orphan that day's exits and P&L.
            if pd.Timestamp(frame["time"].iloc[-1]).date() >= today:
                frame = frame.iloc[:-1]
            bars[symbol] = {
                day_of(str(t)): {"open": float(o), "low": float(lo), "close": float(c)}
                for t, o, lo, c in zip(frame["time"], frame["open"], frame["low"], frame["close"])
            }
    finally:
        connector.shutdown()

    champion_days = [day_of(t["timestamp"]) for t in champion_raw]
    meanrev_days = [day_of(t["entry_time"]) for t in meanrev_raw]
    start_day = max(min(champion_days), min(meanrev_days))
    end_day = min(max(champion_days), max(day_of(t["timestamp"]) for t in meanrev_raw))
    # User request: forward test FROM 3 YEARS AGO (not the full data span).
    start_day = max(start_day, str((pd.Timestamp(end_day) - pd.Timedelta(days=1095)).date()))

    champion_by_day: dict[str, list[dict]] = defaultdict(list)
    for trade in champion_raw:
        day = day_of(trade["timestamp"])
        if start_day <= day <= end_day:
            champion_by_day[day].append(trade)
    mr_entries_by_day: dict[str, list[dict]] = defaultdict(list)
    mr_exits_by_day: dict[str, list[dict]] = defaultdict(list)
    for trade in meanrev_raw:
        entry_day, exit_day = day_of(trade["entry_time"]), day_of(trade["timestamp"])
        if start_day <= entry_day and exit_day <= end_day:
            mr_entries_by_day[entry_day].append(trade)
            mr_exits_by_day[exit_day].append(trade)

    all_days = sorted(day for day in set().union(*[set(b) for b in bars.values()]) if start_day <= day <= end_day)
    all_days_set = set(all_days)
    for day in set(champion_by_day) | set(mr_exits_by_day) | set(mr_entries_by_day):
        if day not in all_days_set:
            raise AssertionError(f"Trade day {day} missing from daily-bar calendar — refusing a silent P&L drop.")

    equity = STARTING_EQUITY  # realized
    peak = STARTING_EQUITY
    open_mr: dict[str, list[dict]] = defaultdict(list)  # symbol -> [positions]
    last_close: dict[str, float] = {}
    curve: list[dict[str, Any]] = []
    stats = {
        "champion": {"filled": 0, "at_min_lot": 0, "skipped_min": 0, "skipped_small_stop": 0, "skipped_over_ceiling": 0, "pnl": 0.0},
        "meanrev": {"filled": 0, "at_min_lot": 0, "skipped_min": 0, "skipped_over_ceiling": 0, "stopped_out": 0, "pnl": 0.0},
    }
    tripwire_hits: list[dict[str, Any]] = []
    max_dd = 0.0
    worst_day = {"day": None, "loss_percent": 0.0}
    prev_marked = STARTING_EQUITY
    blown = False

    def floating_pnl() -> float:
        total = 0.0
        for symbol, positions in open_mr.items():
            close = last_close.get(symbol)
            if close is None:
                continue
            for position in positions:
                total += position["lots"] * (close - position["entry"])
        return total

    for day in all_days:
        trades_today = 0
        # 1) Disaster stops on open MR positions (live SL fires intraday).
        for symbol in list(open_mr):
            if not stop_units:
                break  # stop disabled: audited no-stop replay
            bar = bars[symbol].get(day)
            if bar is None:
                continue
            for position in list(open_mr[symbol]):
                if day <= day_of(position["trade"]["entry_time"]):
                    continue
                stop_price = position["stop_price"]
                fill = None
                if bar["open"] <= stop_price:
                    fill = bar["open"]  # gap through the stop fills at the open
                elif bar["low"] <= stop_price:
                    fill = stop_price
                if fill is None:
                    continue
                trade = position["trade"]
                held_days = max((pd.Timestamp(day) - pd.Timestamp(trade["entry_time"]).tz_localize(None)).days, 1)
                pnl_points = (
                    fill - position["entry"]
                    - ROUND_TRIP_COSTS.get(symbol, 0.0)
                    - FINANCING_PER_DAY * position["entry"] * held_days
                )
                pnl = position["lots"] * pnl_points
                equity += pnl
                stats["meanrev"]["pnl"] += pnl
                stats["meanrev"]["stopped_out"] += 1
                trade["_consumed"] = True  # scheduled exit must not double-count
                open_mr[symbol].remove(position)
        # 2) Champion trades (sized on marked equity, live refusal gates mirrored).
        for trade in champion_by_day.get(day, ()):
            stop = float(trade["stop_distance"])
            price_proxy = bars.get(trade["symbol"], {}).get(day, {}).get("close") or last_close.get(trade["symbol"], 0.0)
            if price_proxy and stop < price_proxy * 0.0005:
                stats["champion"]["skipped_small_stop"] += 1  # live executor refuses these
                continue
            marked_equity = equity + floating_pnl()
            lots = champion_sizer.lot_size(trade["symbol"], stop, marked_equity)
            if lots <= 0:
                stats["champion"]["skipped_min"] += 1
                continue
            if lots > float(args.max_lots):
                stats["champion"]["skipped_over_ceiling"] += 1  # live refuses, not clips
                continue
            if marked_equity * 0.005 / stop < 0.1:
                stats["champion"]["at_min_lot"] += 1
            pnl = lots * float(trade["rr"]) * stop
            equity += pnl
            stats["champion"]["filled"] += 1
            stats["champion"]["pnl"] += pnl
            trades_today += 1
        # 3) Scheduled MR exits at this day's close (skip if stopped earlier).
        for trade in mr_exits_by_day.get(day, ()):
            if trade.get("_consumed"):
                continue
            match = next((p for p in open_mr.get(trade["symbol"], []) if p["trade"] is trade), None)
            if match is None:
                continue  # entry was skipped below-minimum
            pnl = match["lots"] * float(trade["pnl_points"])
            equity += pnl
            stats["meanrev"]["pnl"] += pnl
            trade["_consumed"] = True
            open_mr[trade["symbol"]].remove(match)
        # 4) MR entries at this day's close.
        for trade in mr_entries_by_day.get(day, ()):
            if len(open_mr.get(trade["symbol"], [])) >= int(args.max_open_per_symbol):
                continue
            total_open = sum(len(v) for v in open_mr.values())
            if args.max_open_total and total_open >= int(args.max_open_total):
                continue
            stop = (stop_units or DISASTER_STOP_UNITS) * float(trade["risk_unit"])
            marked_equity = equity + floating_pnl()
            lots = meanrev_sizer.lot_size(trade["symbol"], stop, marked_equity)
            if lots <= 0:
                stats["meanrev"]["skipped_min"] += 1
                trade["_consumed"] = True  # no position -> its exit must not fire
                continue
            if lots > float(args.max_lots):
                stats["meanrev"]["skipped_over_ceiling"] += 1
                trade["_consumed"] = True
                continue
            if marked_equity * (mr_risk_percent / 100.0) / stop < 0.1:
                stats["meanrev"]["at_min_lot"] += 1
            entry = float(trade["entry_price"])
            open_mr[trade["symbol"]].append({
                "trade": trade,
                "lots": lots,
                "entry": entry,
                "stop_price": entry - stop,
            })
            stats["meanrev"]["filled"] += 1
            trades_today += 1
        # 5) Mark to market on last known closes (carried through holidays).
        for symbol in SYMBOLS:
            bar = bars[symbol].get(day)
            if bar is not None:
                last_close[symbol] = bar["close"]
        marked = equity + floating_pnl()
        peak = max(peak, marked)
        drawdown = (peak - marked) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, drawdown)
        day_loss = (prev_marked - marked) / prev_marked * 100 if prev_marked > 0 else 0.0
        if day_loss > worst_day["loss_percent"]:
            worst_day = {"day": day, "loss_percent": round(day_loss, 2)}
        for name, threshold, value in (
            ("daily_loss", TRIPWIRES["daily_loss_percent"], day_loss),
            ("internal_dd", TRIPWIRES["internal_dd_percent"], drawdown),
            ("firm_dd", TRIPWIRES["firm_dd_percent"], drawdown),
            ("max_trades_per_day", TRIPWIRES["max_trades_per_day"], trades_today),
        ):
            if value >= threshold:
                tripwire_hits.append({"day": day, "tripwire": name, "value": round(float(value), 2)})
        curve.append({"day": day, "equity_closed": round(equity, 2), "equity_marked": round(marked, 2), "dd": round(drawdown, 2)})
        prev_marked = marked
        if marked <= 0:
            blown = True
            break

    open_mr = {k: v for k, v in open_mr.items() if v}
    if open_mr and not blown:
        raise AssertionError(f"Positions still open at end of window: {sorted(open_mr)} — window trim is broken.")

    ending = curve[-1]["equity_marked"] if curve else STARTING_EQUITY
    years = max((pd.Timestamp(end_day) - pd.Timestamp(start_day)).days / 365.25, 0.01)
    yearly: dict[str, dict[str, Any]] = {}
    prev_year_end = STARTING_EQUITY
    for point in curve:
        year = point["day"][:4]
        if year not in yearly:
            yearly[year] = {"first": prev_year_end, "last": point["equity_marked"], "max_dd": 0.0}
        yearly[year]["last"] = point["equity_marked"]
        yearly[year]["max_dd"] = max(yearly[year]["max_dd"], point["dd"])
        prev_year_end = yearly[year]["last"]

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window": {"start": start_day, "end": end_day, "years": round(years, 2)},
        "stop_units": stop_units,
        "starting_equity": STARTING_EQUITY,
        "ending_equity": round(ending, 2),
        "total_return_percent": round((ending / STARTING_EQUITY - 1) * 100, 1),
        "cagr_percent": round(((ending / STARTING_EQUITY) ** (1 / years) - 1) * 100, 1) if ending > 0 else None,
        "max_drawdown_percent": round(max_dd, 2),
        "worst_day": worst_day,
        "account_blown": blown,
        "engine_stats": {k: {**v, "pnl": round(v["pnl"], 2)} for k, v in stats.items()},
        "tripwire_hits": tripwire_hits[:50],
        "tripwire_hit_count": len(tripwire_hits),
        "yearly": {
            year: {"end_equity": data["last"], "return_percent": round((data["last"] / data["first"] - 1) * 100, 1), "max_dd": round(data["max_dd"], 2)}
            for year, data in yearly.items()
        },
        "model_notes": [
            "MR disaster stop (3 units) modeled on daily opens/lows with gap fills; stopped trades pay costs+financing to the stop day",
            "sizing on MARKED equity via the real DemoOrderExecutor.lot_size; live refusal gates (small stop, 5-lot ceiling) mirrored",
            "volume_min 0.1 / step 0.1 / $1 per point (measured live 2026-08-11), assumed constant over window",
        ],
        "curve_last_30": curve[-30:],
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")

    print(f"COMBINED BOOK FORWARD TEST {start_day} -> {end_day} ({report['window']['years']}y), start ${STARTING_EQUITY:.0f}")
    print(f"  ending equity  ${report['ending_equity']:,} ({report['total_return_percent']}% total, CAGR {report['cagr_percent']}%)")
    print(f"  max drawdown   {report['max_drawdown_percent']}% (marked-to-market) | worst day {worst_day['loss_percent']}% on {worst_day['day']}")
    print(f"  account blown  {blown}")
    for engine, data in report["engine_stats"].items():
        extras = " ".join(f"{key}={value}" for key, value in data.items() if key not in {"pnl", "filled"} and value)
        print(f"  {engine}: filled {data['filled']} | P&L ${data['pnl']:,} | {extras}")
    print(f"  tripwire hits  {report['tripwire_hit_count']}")
    for year, data in report["yearly"].items():
        print(f"  {year}: end ${data['end_equity']:,} ({data['return_percent']}%) maxDD {data['max_dd']}%")
    print(f"  report -> {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
