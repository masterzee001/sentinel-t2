"""Single source of truth for confidence bands and rejection labels.

This module is reporting/classification infrastructure only. It does not score
setups, relax guardrails, or change execution permissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


PRODUCTION_RAW_THRESHOLDS = {
    "warm_minimum": 40,
    "hot_minimum": 70,
    "execution_ready_minimum": 90,
}
GUARDRAIL_ADJUSTED_THRESHOLDS = {
    "warm_minimum": 40,
    "hot_minimum": 70,
    "execution_ready_minimum": 95,
}

ENGINE_ACTIONS = {
    "COLD": "Ignore",
    "WARM": "Monitor",
    "HOT": "Prepare",
    "EXECUTION_READY": "Trade Allowed",
}
OPERATOR_ACTIONS = {
    "COLD": "WAIT",
    "WARM": "MONITOR",
    "HOT": "PREPARE",
    "EXECUTION_READY": "TRADE CANDIDATE",
    "UNAVAILABLE": "UNAVAILABLE",
}

PRODUCTION_BANDS = ("COLD", "WARM", "HOT", "EXECUTION_READY")
EXCLUSIVE_BANDS = ("COLD_ONLY", "WARM_ONLY", "HOT_ONLY", "EXECUTION_READY")
OBSERVER_STATES = ("OBSERVER_COLD", "OBSERVER_WARM", "OBSERVER_HOT", "OBSERVER_UNAVAILABLE")

ALLOWED_REJECTION_REASONS = (
    "MSS_NOT_CONFIRMED",
    "BELOW_MIN_CONFIDENCE",
    "INVALID_KILLZONE",
    "SYMBOL_LOCK",
    "RISK_LOCK",
    "NO_TRADE_WINDOW",
    "NEWS_LOCK",
    "SPREAD_LOCK",
    "DUPLICATE_LOCK",
)

MSS_MODE_SYNTHETIC_ASSUMED_TRUE = "SYNTHETIC_ASSUMED_TRUE"
MSS_MODE_HISTORICAL_STRUCTURE_BREAK = "HISTORICAL_STRUCTURE_BREAK"
MSS_MODE_REPLAY_CAUSAL_CANDLE_CLOSE = "REPLAY_CAUSAL_CANDLE_CLOSE"
MSS_MODE_LIVE_EVALUATED = "LIVE_EVALUATED"


@dataclass(frozen=True)
class BandResult:
    """Canonical band classification result."""

    band: str
    engine_action: str
    operator_action: str
    score: int


def production_band(score: int | float) -> BandResult:
    """Return the raw production confidence band."""
    band = band_for_score(score, PRODUCTION_RAW_THRESHOLDS)
    return BandResult(
        band=band,
        engine_action=ENGINE_ACTIONS[band],
        operator_action=OPERATOR_ACTIONS[band],
        score=to_int(score),
    )


def guardrail_adjusted_band(score: int | float) -> BandResult:
    """Return the guardrail-adjusted execution band."""
    band = band_for_score(score, GUARDRAIL_ADJUSTED_THRESHOLDS)
    return BandResult(
        band=band,
        engine_action=ENGINE_ACTIONS[band],
        operator_action=OPERATOR_ACTIONS[band],
        score=to_int(score),
    )


def band_for_score(score: int | float, thresholds: dict[str, int]) -> str:
    """Return COLD/WARM/HOT/EXECUTION_READY from a score and threshold set."""
    value = to_int(score)
    if value >= int(thresholds["execution_ready_minimum"]):
        return "EXECUTION_READY"
    if value >= int(thresholds["hot_minimum"]):
        return "HOT"
    if value >= int(thresholds["warm_minimum"]):
        return "WARM"
    return "COLD"


def action_for_band(band: Any, *, operator: bool = False) -> str:
    """Return the action label for a production band."""
    value = normalize_production_band(band)
    actions = OPERATOR_ACTIONS if operator else ENGINE_ACTIONS
    return actions.get(value, actions.get("COLD", "Ignore"))


def normalize_production_band(band: Any) -> str:
    """Return a valid production band label."""
    value = str(band or "COLD").upper().strip()
    return value if value in PRODUCTION_BANDS else "COLD"


def observer_state(state: Any) -> str:
    """Return canonical OBSERVER_* state."""
    value = str(state or "UNAVAILABLE").upper().strip()
    if value in OBSERVER_STATES:
        return value
    if value in PRODUCTION_BANDS:
        return f"OBSERVER_{value}" if value != "EXECUTION_READY" else "OBSERVER_HOT"
    return "OBSERVER_UNAVAILABLE"


def observer_display_state(state: Any) -> str:
    """Return display-only observer state without the OBSERVER_ prefix."""
    canonical = observer_state(state)
    return canonical.replace("OBSERVER_", "")


def exclusive_band_distribution(bands: Iterable[Any]) -> dict[str, int]:
    """Return mutually-exclusive band buckets."""
    output = {band: 0 for band in EXCLUSIVE_BANDS}
    for band in bands:
        normalized = normalize_production_band(band)
        key = f"{normalized}_ONLY" if normalized != "EXECUTION_READY" else "EXECUTION_READY"
        output[key] += 1
    return output


def cumulative_funnel(
    *,
    qualifying_setups: int,
    exclusive_distribution: dict[str, int],
    approved_trades: int,
    wins: int,
    losses: int,
    total_scans: int | None = None,
) -> dict[str, int]:
    """Return a cumulative funnel that cannot be confused with band buckets."""
    hot_or_better = int(exclusive_distribution.get("HOT_ONLY", 0)) + int(
        exclusive_distribution.get("EXECUTION_READY", 0)
    )
    payload = {
        "qualifying_setups": int(qualifying_setups),
        "HOT_OR_BETTER": hot_or_better,
        "EXECUTION_READY": int(exclusive_distribution.get("EXECUTION_READY", 0)),
        "approved_trades": int(approved_trades),
        "wins": int(wins),
        "losses": int(losses),
    }
    if total_scans is not None:
        payload = {"total_scans": int(total_scans), **payload}
    return payload


def rejection_reason_code(reason: Any) -> str:
    """Return one allowed rejection reason code from an actual payload reason."""
    text = str(reason or "").lower().strip()
    if "mss" in text and ("not" in text or "missing" in text or "unconfirmed" in text):
        return "MSS_NOT_CONFIRMED"
    if "confidence" in text or "minimum" in text or "below" in text:
        return "BELOW_MIN_CONFIDENCE"
    if "killzone" in text and ("invalid" in text or "outside" in text):
        return "INVALID_KILLZONE"
    if "symbol" in text or "observer" in text or "production execution disabled" in text:
        return "SYMBOL_LOCK"
    if "risk" in text or "daily loss" in text or "drawdown" in text:
        return "RISK_LOCK"
    if "news" in text:
        return "NEWS_LOCK"
    if "spread" in text or "slippage" in text:
        return "SPREAD_LOCK"
    if "duplicate" in text:
        return "DUPLICATE_LOCK"
    return "NO_TRADE_WINDOW"


def rejection_distribution(reasons: Iterable[Any]) -> dict[str, int]:
    """Return counts by allowed rejection reason code."""
    output = {reason: 0 for reason in ALLOWED_REJECTION_REASONS}
    for reason in reasons:
        output[rejection_reason_code(reason)] += 1
    return output


def to_int(value: Any) -> int:
    """Return an int score without raising on display payloads."""
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0
