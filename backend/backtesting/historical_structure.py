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


SMT_RECENT_BARS = 6
SMT_PRIOR_BARS = 12


def smt_divergence(own: pd.DataFrame, reference: pd.DataFrame, direction: str) -> dict[str, Any]:
    """Classic index SMT divergence, computed causally at the candidate bar.

    Bullish: the traded symbol prints a lower low over the recent bars while
    the correlated reference holds a higher low (failure to confirm the sweep).
    Bearish is the mirror on highs. Feature-only: this function never touches
    admission; it annotates trades so the filter can be judged by the gate.
    """
    needed = SMT_RECENT_BARS + SMT_PRIOR_BARS
    if len(own) < needed or len(reference) < needed:
        return {"available": False, "detected": False, "pattern": "insufficient_data"}
    own_recent, own_prior = own.tail(SMT_RECENT_BARS), own.tail(needed).head(SMT_PRIOR_BARS)
    ref_recent, ref_prior = reference.tail(SMT_RECENT_BARS), reference.tail(needed).head(SMT_PRIOR_BARS)
    own_lower_low = float(own_recent["low"].min()) < float(own_prior["low"].min())
    ref_lower_low = float(ref_recent["low"].min()) < float(ref_prior["low"].min())
    own_higher_high = float(own_recent["high"].max()) > float(own_prior["high"].max())
    ref_higher_high = float(ref_recent["high"].max()) > float(ref_prior["high"].max())
    bullish_divergence = own_lower_low != ref_lower_low
    bearish_divergence = own_higher_high != ref_higher_high
    if direction == "bullish":
        detected = bullish_divergence
        pattern = "bullish_smt" if detected else ("aligned_lows" if own_lower_low == ref_lower_low else "none")
    else:
        detected = bearish_divergence
        pattern = "bearish_smt" if detected else ("aligned_highs" if own_higher_high == ref_higher_high else "none")
    return {"available": True, "detected": bool(detected), "pattern": pattern}


H4_BARS = 16   # 4 hours of M15 bars.
D1_BARS = 96   # 24 hours of M15 bars.


def htf_bias(history: pd.DataFrame, direction: str) -> dict[str, Any]:
    """Higher-timeframe drift bias at the candidate bar, computed causally.

    H4 bias = sign of close vs close 16 bars back; D1 bias = vs 96 bars back.
    Annotation-only: alignment flags let the walk-forward judge HTF filters
    without touching admission.
    """
    closes = [float(value) for value in history["close"]]
    if len(closes) <= D1_BARS:
        return {"available": False}
    last = closes[-1]

    def drift(bars_back: int) -> str:
        prior = closes[-1 - bars_back]
        if last > prior:
            return "bullish"
        if last < prior:
            return "bearish"
        return "flat"

    h4 = drift(H4_BARS)
    d1 = drift(D1_BARS)
    return {
        "available": True,
        "h4_bias": h4,
        "d1_bias": d1,
        "aligned_h4": h4 == direction,
        "aligned_d1": d1 == direction,
        "aligned_both": h4 == direction and d1 == direction,
    }
