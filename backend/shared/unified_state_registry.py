"""Unified state registry for Sentinel decision and trade lifecycle labels.

This module is classification infrastructure only. It does not score setups,
route orders, or change execution permissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.shared.confidence_band_registry import normalize_production_band, observer_state


SETUP_STATES = (
    "SETUP_INVALID",
    "SETUP_FORMING",
    "SETUP_VALID",
    "SETUP_ELITE",
    "TRADE_CANDIDATE",
)
TRADE_STATES = (
    "CANDIDATE",
    "APPROVED",
    "WAITING_ENTRY",
    "ENTERED",
    "ACTIVE",
    "DEFENSIVE",
    "PARTIAL_EXIT",
    "FULL_EXIT",
    "CLOSED",
)
UNIFIED_STATES = (*SETUP_STATES, *TRADE_STATES)

CONFIDENCE_BAND_TO_STATE = {
    "COLD": "SETUP_INVALID",
    "WARM": "SETUP_FORMING",
    "HOT": "SETUP_VALID",
    "EXECUTION_READY": "TRADE_CANDIDATE",
}
OBSERVER_STATE_TO_STATE = {
    "OBSERVER_COLD": "SETUP_INVALID",
    "OBSERVER_WARM": "SETUP_FORMING",
    "OBSERVER_HOT": "SETUP_VALID",
    "OBSERVER_UNAVAILABLE": "SETUP_INVALID",
}
LEGACY_AMBIGUITIES = {
    "HOT": [
        "Production confidence band",
        "Observer movement state when OBSERVER_ prefix is omitted",
        "Terminal/Telegram urgency label",
    ],
    "ACTIVE": [
        "Symbol registry status",
        "Trade lifecycle state",
        "Runtime service health label",
    ],
    "APPROVED": [
        "Human approval status",
        "Backtest selected trade status",
        "Ticket lifecycle state",
    ],
}


@dataclass(frozen=True)
class UnifiedStateResult:
    """Canonical state mapping result."""

    input_label: str
    unified_state: str
    source: str
    execution_allowed: bool


def state_from_confidence_band(band: Any) -> UnifiedStateResult:
    """Map a production confidence band to a unified setup state."""
    normalized = normalize_production_band(band)
    state = CONFIDENCE_BAND_TO_STATE[normalized]
    return UnifiedStateResult(
        input_label=normalized,
        unified_state=state,
        source="production_confidence_band",
        execution_allowed=state == "TRADE_CANDIDATE",
    )


def state_from_observer_label(label: Any) -> UnifiedStateResult:
    """Map an observer movement label to a unified non-execution state."""
    normalized = observer_state(label)
    return UnifiedStateResult(
        input_label=normalized,
        unified_state=OBSERVER_STATE_TO_STATE[normalized],
        source="observer_movement_state",
        execution_allowed=False,
    )


def state_from_lifecycle(label: Any) -> UnifiedStateResult:
    """Map a lifecycle label to its canonical trade state."""
    normalized = str(label or "").upper().strip()
    if normalized == "IN_POSITION":
        normalized = "ACTIVE"
    if normalized == "ENTRY_SIMULATED":
        normalized = "ENTERED"
    if normalized == "SIGNAL_DETECTED":
        normalized = "CANDIDATE"
    if normalized not in TRADE_STATES:
        normalized = "CANDIDATE"
    return UnifiedStateResult(
        input_label=normalized,
        unified_state=normalized,
        source="trade_lifecycle",
        execution_allowed=normalized in {"APPROVED", "WAITING_ENTRY", "ENTERED", "ACTIVE"},
    )


def ambiguity_report() -> list[dict[str, Any]]:
    """Return known state-label ambiguity sources."""
    return [
        {"label": label, "meanings": meanings, "status": "AMBIGUOUS"}
        for label, meanings in LEGACY_AMBIGUITIES.items()
    ]
