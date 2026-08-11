"""Daily mean-reversion engine (IBS primary, RSI2 robustness), judged by the gate.

PRE-REGISTERED primary hypothesis (before seeing any results): long-only IBS
mean reversion on US equity index CFDs — buy the close when IBS < 0.2, exit at
the first close with IBS > 0.8 or after 10 trading days, one position per
symbol. Universe: US30, NAS100, US500. Costs: round-trip spread+slippage per
trade plus financing at 0.02%/calendar day held. The RSI2(<10/>90) variant is
reported as robustness context only and can never produce the verdict.

R-normalization: trailing 20-day average absolute daily move (strictly
trailing). rwPF is invariant to the normalizer's scale, so the verdict does
not depend on that choice. Both variants are parameter-fixed from the
literature, so the whole history is out-of-sample.
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

SYMBOLS = ("US30", "NAS100", "US500")
DAILY_BARS = 1400
IBS_ENTRY = 0.2
IBS_EXIT = 0.8
RSI_PERIOD = 2
RSI_ENTRY = 10.0
RSI_EXIT = 90.0
MAX_HOLD_DAYS = 10
VOL_WINDOW = 20
FINANCING_PER_DAY = 0.0002  # 0.02%/calendar day of price while holding.
ROUND_TRIP_COSTS = {"US30": 5.0, "NAS100": 3.5, "US500": 0.85}
REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "mean_reversion_report.json"
PROMOTION = {"min_net_rr": 0.0, "min_rwpf": 1.10, "min_positive_quarter_share": 0.5}


def main() -> int:
    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
        connector.supported_symbols = frozenset(set(connector.supported_symbols) | set(SYMBOLS))
        frames: dict[str, pd.DataFrame] = {}
        for symbol in SYMBOLS:
            try:
                candles = connector.get_historical_candles(symbol, "D1", count=DAILY_BARS)
                # Audit fix: MT5 position 0 is the still-forming bar - drop it.
                frames[symbol] = candles.iloc[:-1].reset_index(drop=True)
            except Exception as exc:
                print(f"{symbol}: daily candles unavailable ({exc})")
        if set(frames) != set(SYMBOLS):
            print("Universe incomplete; refusing a partial-universe verdict.")
            return 1
        # Audit fix: equalize spans - NAS100's broker history starts 2022-07,
        # skipping the 2022 H1 bear; all symbols must face the same regimes.
        common_start = max(frame["time"].min() for frame in frames.values())
        common_frames = {
            symbol: frame[frame["time"] >= common_start].reset_index(drop=True)
            for symbol, frame in frames.items()
        }
        report: dict[str, Any] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "primary": "ibs",
            "rules": {
                "ibs": {"entry": IBS_ENTRY, "exit": IBS_EXIT, "max_hold_days": MAX_HOLD_DAYS, "long_only": True},
                "rsi2": {"entry": RSI_ENTRY, "exit": RSI_EXIT, "max_hold_days": MAX_HOLD_DAYS, "long_only": True},
            },
            "financing_per_day": FINANCING_PER_DAY,
            "data_span": {
                symbol: {"bars": int(len(frame)), "first": str(frame["time"].min()), "last": str(frame["time"].max())}
                for symbol, frame in frames.items()
            },
            "variants": {},
        }
        report["common_window_start"] = str(common_start)
        for variant in ("ibs", "rsi2"):
            trades: list[dict[str, Any]] = []
            for symbol, frame in common_frames.items():
                trades.extend(evaluate_symbol(symbol, frame, variant))
            if variant == "ibs":
                # Per-trade export for the combined-book forward test.
                export_path = PROJECT_ROOT / "data" / "reports" / "meanrev_trades_export.json"
                export_path.write_text(
                    json.dumps(
                        {"generated_at": report["generated_at"], "variant": "ibs", "trades": trades},
                        indent=2,
                        default=str,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            summary = summarize(trades)
            summary["is_primary"] = variant == "ibs"
            if not summary["is_primary"]:
                summary["promote"] = None  # Robustness context only.
            report["variants"][variant] = summary
            label = "PRIMARY, common window" if variant == "ibs" else "robustness"
            print(
                f"{variant} ({label}): {summary['trades']} trades | WR {summary['win_rate']}% | "
                f"avg hold {summary['avg_hold_days']}d | net {summary['net_rr']}R | "
                f"rwPF {summary['risk_weighted_pf']} | {summary['positive_quarter_share'] * 100:.0f}% quarters+ | "
                f"promote={summary['promote']}"
            )
            for symbol, row in summary["by_symbol"].items():
                print(f"  {symbol}: {row['active_positions']} trades | net {row['net_rr']}R | rwPF {row['risk_weighted_pf']}")

        # Audit sensitivity 1: full spans (NAS100 bear-free bias visible here).
        full_span = summarize(
            [t for symbol, frame in frames.items() for t in evaluate_symbol(symbol, frame, "ibs")]
        )
        # Audit sensitivity 2: rollover fills - the daily close sits at the
        # 5pm-ET rollover where CFD spreads widen; charge 4x round-trip costs.
        rollover = summarize(
            [
                t
                for symbol, frame in common_frames.items()
                for t in evaluate_symbol(symbol, frame, "ibs", cost_multiplier=4.0)
            ]
        )
        report["sensitivities"] = {"full_span_ibs": full_span, "rollover_x4_ibs": rollover}
        final_promote = bool(report["variants"]["ibs"]["promote"]) and bool(rollover["promote"])
        report["promotion_final"] = final_promote
        print(
            f"sensitivity full-span: net {full_span['net_rr']}R rwPF {full_span['risk_weighted_pf']} | "
            f"rollover x4 costs: net {rollover['net_rr']}R rwPF {rollover['risk_weighted_pf']} "
            f"({rollover['positive_quarter_share'] * 100:.0f}% q+)"
        )
        print(f"FINAL PROMOTION (primary AND rollover-stress both clear the gate): {final_promote}")
        REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        print(f"Report: {REPORT_PATH.relative_to(PROJECT_ROOT)}")
        return 0
    except (MT5ConnectorError, ValueError) as exc:
        print(f"Mean reversion backtest failed: {exc}")
        return 1
    finally:
        connector.shutdown()


def rsi(closes: list[float], index: int, period: int = RSI_PERIOD) -> float | None:
    """Classic Wilder-free simple RSI over the trailing `period` changes."""
    if index < period:
        return None
    gains = losses = 0.0
    for j in range(index - period + 1, index + 1):
        change = closes[j] - closes[j - 1]
        if change > 0:
            gains += change
        else:
            losses -= change
    if gains + losses == 0:
        return 50.0
    return 100.0 * gains / (gains + losses)


def evaluate_symbol(
    symbol: str, frame: pd.DataFrame, variant: str, cost_multiplier: float = 1.0
) -> list[dict[str, Any]]:
    """Long-only mean reversion with causal signals at the daily close."""
    highs = [float(v) for v in frame["high"]]
    lows = [float(v) for v in frame["low"]]
    closes = [float(v) for v in frame["close"]]
    times = [pd.Timestamp(v) for v in frame["time"]]
    cost = ROUND_TRIP_COSTS.get(symbol, 0.0) * cost_multiplier
    trades: list[dict[str, Any]] = []
    entry_index: int | None = None
    for index in range(VOL_WINDOW + RSI_PERIOD + 1, len(closes)):
        bar_range = highs[index] - lows[index]
        ibs = (closes[index] - lows[index]) / bar_range if bar_range > 0 else 0.5
        if variant == "ibs":
            entry_signal = ibs < IBS_ENTRY
            exit_signal = ibs > IBS_EXIT
        else:
            value = rsi(closes, index)
            entry_signal = value is not None and value < RSI_ENTRY
            exit_signal = value is not None and value > RSI_EXIT
        if entry_index is None:
            if entry_signal:
                entry_index = index  # Enter at this close (causal: signal IS the close).
            continue
        held_bars = index - entry_index
        if exit_signal or held_bars >= MAX_HOLD_DAYS:
            moves = [abs(closes[j] - closes[j - 1]) for j in range(entry_index - VOL_WINDOW, entry_index)]
            risk_unit = sum(moves) / len(moves) if moves else 0.0
            if risk_unit > 0:
                held_calendar = max((times[index] - times[entry_index]).days, 1)
                pnl_points = (
                    closes[index]
                    - closes[entry_index]
                    - cost
                    - FINANCING_PER_DAY * closes[entry_index] * held_calendar
                )
                trades.append(
                    {
                        "symbol": symbol,
                        "timestamp": str(times[index]),
                        "entry_time": str(times[entry_index]),
                        "entry_price": round(closes[entry_index], 5),
                        "exit_price": round(closes[index], 5),
                        "risk_unit": round(risk_unit, 5),
                        "hold_days": held_bars,
                        "pnl_points": round(pnl_points, 5),
                        "rr": round(pnl_points / risk_unit, 4),
                    }
                )
            entry_index = None
    return trades


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    rrs = [float(trade["rr"]) for trade in trades]
    metrics = book_metrics(rrs)
    quarters: dict[str, float] = {}
    for trade in trades:
        quarter = quarter_key(trade["timestamp"])
        if quarter:
            quarters[quarter] = quarters.get(quarter, 0.0) + float(trade["rr"])
    quarters = {key: round(value, 2) for key, value in quarters.items()}
    positive_share = (
        sum(1 for value in quarters.values() if value > 0) / len(quarters) if quarters else 0.0
    )
    promote = (
        metrics["net_rr"] > PROMOTION["min_net_rr"]
        and metrics["risk_weighted_pf"] > PROMOTION["min_rwpf"]
        and positive_share > PROMOTION["min_positive_quarter_share"]
    )
    return {
        "trades": len(trades),
        "win_rate": round(sum(1 for rr in rrs if rr > 0) / len(rrs) * 100, 2) if rrs else 0.0,
        "avg_hold_days": round(sum(t["hold_days"] for t in trades) / len(trades), 1) if trades else 0.0,
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
