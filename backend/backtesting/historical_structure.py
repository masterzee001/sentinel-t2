"""Real ICT structure detection on closed historical candles.

Detects the classic ICT reversal sequence, candle-by-candle and causally:

  1. A fractal swing low (sell-side liquidity) forms.
  2. A sweep bar wicks BELOW that swing low but closes back above it
     (liquidity taken, failure to continue).
  3. The current bar confirms a market structure shift: it closes above the
     most recent fractal swing high with displacement (range >= average).
  4. The displacement leg leaves a fair value gap (3-candle imbalance).

Bearish is the exact mirror. Entry is the MSS-confirmation close; the stop is
the sweep extreme. This replaces the 20-bar breakout proxy as the candidate
detector once (and only if) it beats the proxy through the phase-robust
walk-forward gate.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

SWING_STRENGTH = 2       # Bars each side required to confirm a fractal swing.
SWEEP_RECENCY_BARS = 16  # Sweep must have happened within this many bars.
LOOKBACK_BARS = 60       # Structure window examined behind the current bar.
FVG_A_GRADE_FRACTION = 0.15  # Gap >= 15% of stop distance grades the FVG "A".


def find_swing_points(highs: list[float], lows: list[float], strength: int = SWING_STRENGTH) -> tuple[list[int], list[int]]:
    """Return indices of fractal swing highs and swing lows."""
    swing_highs: list[int] = []
    swing_lows: list[int] = []
    for i in range(strength, len(highs) - strength):
        window_high = [highs[j] for j in range(i - strength, i + strength + 1) if j != i]
        window_low = [lows[j] for j in range(i - strength, i + strength + 1) if j != i]
        if highs[i] > max(window_high):
            swing_highs.append(i)
        if lows[i] < min(window_low):
            swing_lows.append(i)
    return swing_highs, swing_lows


def detect_ict_candidate(history: pd.DataFrame, lookback: int = LOOKBACK_BARS) -> dict[str, Any] | None:
    """Return the ICT sweep->MSS->FVG candidate confirmed by the FINAL bar, or None."""
    window = history.tail(lookback).reset_index(drop=True)
    if len(window) < SWING_STRENGTH * 2 + 5:
        return None
    highs = [float(value) for value in window["high"]]
    lows = [float(value) for value in window["low"]]
    closes = [float(value) for value in window["close"]]
    last = len(window) - 1
    average_range = sum(high - low for high, low in zip(highs, lows)) / len(window)
    last_range = highs[last] - lows[last]
    if average_range <= 0 or last_range < average_range:
        return None  # MSS confirmation requires displacement.

    swing_highs, swing_lows = find_swing_points(highs, lows)

    bullish = _direction_candidate(
        swing_levels=swing_lows,
        opposing_levels=swing_highs,
        highs=highs,
        lows=lows,
        closes=closes,
        last=last,
        bullish=True,
    )
    if bullish:
        return _build_candidate(bullish, highs, lows, closes, last, direction="bullish")
    bearish = _direction_candidate(
        swing_levels=swing_highs,
        opposing_levels=swing_lows,
        highs=highs,
        lows=lows,
        closes=closes,
        last=last,
        bullish=False,
    )
    if bearish:
        return _build_candidate(bearish, highs, lows, closes, last, direction="bearish")
    return None


def _direction_candidate(
    *,
    swing_levels: list[int],
    opposing_levels: list[int],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    last: int,
    bullish: bool,
) -> dict[str, Any] | None:
    """Find sweep + MSS for one direction; returns sweep/mss anchors or None."""
    for swing_index in reversed(swing_levels):
        if swing_index >= last - 1:
            continue
        swept_level = lows[swing_index] if bullish else highs[swing_index]
        for sweep_bar in range(max(swing_index + 1, last - SWEEP_RECENCY_BARS), last):
            if bullish:
                swept = lows[sweep_bar] < swept_level and closes[sweep_bar] > swept_level
            else:
                swept = highs[sweep_bar] > swept_level and closes[sweep_bar] < swept_level
            if not swept:
                continue
            broken = _most_recent_opposing(opposing_levels, before=last)
            if broken is None or broken == swing_index:
                continue
            broken_level = highs[broken] if bullish else lows[broken]
            mss = closes[last] > broken_level if bullish else closes[last] < broken_level
            if not mss:
                continue
            return {
                "swing_index": swing_index,
                "swept_level": swept_level,
                "sweep_bar": sweep_bar,
                "broken_swing_index": broken,
                "broken_level": broken_level,
            }
    return None


def _most_recent_opposing(indices: list[int], *, before: int) -> int | None:
    candidates = [index for index in indices if index < before]
    return candidates[-1] if candidates else None


def _build_candidate(
    anchors: dict[str, Any],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    last: int,
    *,
    direction: str,
) -> dict[str, Any]:
    entry = closes[last]
    if direction == "bullish":
        stop = min(lows[anchors["sweep_bar"]], lows[last])
        fvg_gap = lows[last] - highs[last - 2] if last >= 2 else 0.0
    else:
        stop = max(highs[anchors["sweep_bar"]], highs[last])
        fvg_gap = lows[last - 2] - highs[last] if last >= 2 else 0.0
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        return {}
    fvg_present = fvg_gap > 0
    if not fvg_present:
        return {}  # The ICT sequence requires the displacement imbalance.
    fvg_grade = "A" if fvg_gap >= FVG_A_GRADE_FRACTION * stop_distance else "B"
    return {
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "stop_distance": stop_distance,
        "swept_level": anchors["swept_level"],
        "sweep_bar_offset": last - anchors["sweep_bar"],
        "mss_broken_level": anchors["broken_level"],
        "fvg_gap": fvg_gap,
        "fvg_grade": fvg_grade,
        "liquidity_sweep_confirmed": True,
        "mss_confirmed": True,
    }
