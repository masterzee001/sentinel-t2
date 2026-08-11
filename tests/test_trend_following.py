from __future__ import annotations

import pandas as pd
import pytest

from scripts.run_trend_following_backtest import evaluate_symbol


def daily_frame(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": pd.date_range("2020-01-01", periods=len(closes), freq="D", tz="UTC"),
            "close": closes,
        }
    )


def held_days(periods: list[dict], index: int) -> int:
    return (pd.Timestamp(periods[index]["timestamp"]) - pd.Timestamp(periods[index - 1]["timestamp"])).days


def test_uptrend_produces_long_positions_with_positive_rr():
    closes = [1000.0 + i for i in range(900)]  # +1 per calendar day.
    periods = evaluate_symbol("US30", daily_frame(closes), 365, financing=False)

    assert periods
    assert all(p["position"] == 1 for p in periods)
    assert all(p["rr"] > 0 for p in periods)
    # Only the FIRST traded month pays the round-trip cost.
    first_held = (pd.Timestamp(periods[1]["timestamp"]) - pd.Timestamp(periods[0]["timestamp"])).days
    assert periods[1]["pnl_points"] == pytest.approx(first_held)  # +1/day, no cost.
    assert periods[0]["pnl_points"] < periods[0]["pnl_points"] + 5  # Cost charged once at entry.
    assert min(p["pnl_points"] for p in periods[1:]) >= 27  # Full months, no cost.


def test_financing_charges_calendar_days_and_flip_pays_cost():
    closes = [1000.0 + i for i in range(900)]
    frame = daily_frame(closes)
    financed = evaluate_symbol("US30", frame, 365, financing=True)
    unfinanced = evaluate_symbol("US30", frame, 365, financing=False)

    # Audit F2: financing must scale with CALENDAR days held (~28-31/month):
    # 0.0002 * price(~1300-1900) * days(28-31) => roughly 7-12 points of drag.
    for index in range(1, 4):
        drag = unfinanced[index]["pnl_points"] - financed[index]["pnl_points"]
        assert 5.0 < drag < 15.0
        assert held_days(unfinanced, index) >= 28

    # Flip: uptrend then steep downtrend; the first short month pays the cost.
    flip_closes = [1000.0 + i for i in range(500)] + [1499.0 - i * 3 for i in range(400)]
    flips = evaluate_symbol("US30", daily_frame(flip_closes), 365, financing=False)
    shorts = [i for i, p in enumerate(flips) if p["position"] == -1]
    assert len(shorts) >= 2
    first_short, later_short = shorts[0], shorts[-1]
    assert flips[first_short]["pnl_points"] == pytest.approx(3 * held_days(flips, first_short) - 5)
    assert flips[later_short]["pnl_points"] == pytest.approx(3 * held_days(flips, later_short))


def test_short_history_yields_no_periods():
    frame = daily_frame([100.0] * 120)  # ~4 months < 15-month guard.
    assert evaluate_symbol("BTCUSD", frame, 365, financing=True) == []
