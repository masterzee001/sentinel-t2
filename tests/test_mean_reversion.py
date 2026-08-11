from __future__ import annotations

import pandas as pd
import pytest

from scripts.run_mean_reversion_backtest import evaluate_symbol, rsi


def frame(highs: list[float], lows: list[float], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=len(highs), freq="D", tz="UTC"),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
        }
    )


def test_ibs_buys_weak_close_and_exits_strong_close_with_costs():
    n = 40
    highs = [110.0] * n
    lows = [90.0] * n
    # Mid-range closes with daily wiggle so the trailing risk unit is nonzero.
    closes = [100.0 + 0.5 * (-1) ** i for i in range(n)]
    closes[25] = 91.0  # IBS 0.05 -> entry at close 91.
    closes[27] = 109.0  # IBS 0.95 -> exit at close 109 (2 bars held).
    trades = evaluate_symbol("US30", frame(highs, lows, closes), "ibs")

    assert len(trades) == 1
    trade = trades[0]
    assert trade["hold_days"] == 2
    # 109 - 91 = 18, minus 5 cost, minus financing 0.0002*91*2 days.
    assert trade["pnl_points"] == pytest.approx(18 - 5 - 0.0002 * 91 * 2, abs=0.01)
    assert trade["rr"] > 0


def test_max_hold_timeout_exits_flat_market():
    n = 60
    highs = [110.0] * n
    lows = [90.0] * n
    closes = [100.0 + 0.5 * (-1) ** i for i in range(n)]
    closes[30] = 91.0  # Entry; nothing strong afterward -> 10-day timeout.
    trades = evaluate_symbol("US30", frame(highs, lows, closes), "ibs")

    assert len(trades) == 1
    assert trades[0]["hold_days"] == 10
    # Exit at 100 from 91: +9 minus costs/financing.
    assert 3.0 < trades[0]["pnl_points"] < 9.0


def test_rsi2_math_and_variant_signal():
    closes = [100.0, 99.0, 98.0, 97.0, 101.0]
    assert rsi(closes, 3) == 0.0  # Two straight down days.
    assert rsi(closes, 4) == pytest.approx(100.0 * 4 / 5)  # +4 gain vs 1 loss.
