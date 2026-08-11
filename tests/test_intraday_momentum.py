from __future__ import annotations

import pandas as pd

from scripts.run_intraday_momentum_backtest import build_sessions, evaluate_variant


def session_frame(days: int, drift: float) -> pd.DataFrame:
    """Full ET trading days of M15 bars; each day gaps up and drifts by `drift`."""
    rows = []
    price = 44000.0
    stamp = pd.Timestamp("2026-01-05 09:30", tz="America/New_York")  # A Monday.
    for day in range(days):
        day_start = stamp + pd.Timedelta(days=day)
        if day_start.weekday() >= 5:
            continue
        price += 10.0  # Overnight gap up -> bullish first-half-hour signal.
        bar_time = day_start
        while bar_time.strftime("%H:%M") != "16:00":
            rows.append(
                {
                    "time": bar_time.tz_convert("UTC"),
                    "open": price,
                    "high": price + 2,
                    "low": price - 2,
                    "close": price + drift / 26,
                }
            )
            price += drift / 26  # 26 bars per session 09:30-16:00.
            bar_time += pd.Timedelta(minutes=15)
    return pd.DataFrame(rows)


def test_sessions_are_anchored_in_new_york_time():
    frame = session_frame(days=10, drift=26.0)
    sessions = build_sessions(frame)

    assert len(sessions) >= 5
    first = sessions[0]
    # Overnight gap +10 plus two bars of drift means signal is positive.
    assert first["price_1000"] > first["prior_close"]
    assert first["price_1600"] > first["price_1000"]


def test_variant_pnl_and_risk_normalization():
    # 30 sessions of steady +26-point drift with +10 gaps: signal always long.
    frame = session_frame(days=50, drift=26.0)
    sessions = build_sessions(frame)
    trades = evaluate_variant("US30", sessions, "session")

    assert trades  # Warm-up (20 sessions) consumed, remainder traded.
    trade = trades[-1]
    assert trade["direction"] == "long"
    # Session ride: 10:00 -> 16:00 covers 24 of 26 bars = 24 points, minus 5 cost.
    assert 18.0 < trade["pnl_points"] < 20.0
    assert trade["rr"] > 0

    last30 = evaluate_variant("US30", sessions, "last30")
    # Last half hour captures ~2 bars of drift (~2 points) minus 5 cost: negative.
    assert last30[-1]["pnl_points"] < 0
