"""Shared confidence display helpers.

These helpers do not score setups or change guardrails. They only normalize
operator-facing labels across terminal, Telegram, and dashboard surfaces.
"""

from __future__ import annotations

from typing import Any

from backend.shared.confidence_band_registry import (
    OPERATOR_ACTIONS,
    action_for_band,
    observer_display_state,
    observer_state,
    production_band,
)

ACTION_LABELS = OPERATOR_ACTIONS
OBSERVER_ONLY_LABEL = "OBSERVER_ONLY"
DEMO_SANDBOX_LABEL = "DEMO_SANDBOX"


def action_for_state(state: Any) -> str:
    """Return the shared operator action for a confidence state."""
    if str(state or "").upper().strip() == "UNAVAILABLE":
        return ACTION_LABELS["UNAVAILABLE"]
    return action_for_band(state, operator=True)


def confidence_state_for_score(score: int | float) -> str:
    """Return the raw confidence band used for display comparisons."""
    return production_band(score).band


def confidence_display_fields(confidence: dict[str, Any] | None, *, fallback_state: str = "COLD") -> dict[str, Any]:
    """Return raw/adjusted confidence fields for display-only use."""
    confidence = confidence or {}
    guardrail = confidence.get("guardrail", {}) if isinstance(confidence.get("guardrail", {}), dict) else {}
    raw_confidence = to_int(confidence.get("total_confidence", confidence.get("raw_confidence", 0)))
    adjusted_confidence = to_int(
        first_present(
            guardrail.get("guardrail_adjusted_confidence"),
            guardrail.get("adjusted_confidence"),
            confidence.get("adjusted_confidence"),
            raw_confidence,
        )
    )
    raw_band = str(confidence.get("raw_confidence_band") or confidence_state_for_score(raw_confidence)).upper()
    adjusted_band = str(
        confidence.get("confidence_band")
        or guardrail.get("adjusted_confidence_band")
        or confidence_state_for_score(adjusted_confidence)
        or fallback_state
    ).upper()
    penalty_reasons = guardrail_penalty_reasons(guardrail)
    return {
        "raw_confidence": raw_confidence,
        "adjusted_confidence": adjusted_confidence,
        "raw_band": raw_band,
        "adjusted_band": adjusted_band,
        "action": action_for_state(adjusted_band),
        "band_differs": raw_band != adjusted_band or raw_confidence != adjusted_confidence,
        "guardrail_penalty": ", ".join(penalty_reasons) if penalty_reasons else "none",
    }


def observer_display_fields(symbol: str, state: Any, score: Any) -> dict[str, Any]:
    """Return observer-only diagnostic display fields."""
    canonical_state = observer_state(state)
    return {
        "symbol": str(symbol or "UNKNOWN").upper().strip(),
        "mode": OBSERVER_ONLY_LABEL,
        "observer_state": canonical_state,
        "display_state": observer_display_state(canonical_state),
        "score": to_int(score),
        "action": "OBSERVE",
        "execution_note": "No execution",
    }


def guardrail_penalty_reasons(guardrail: dict[str, Any]) -> list[str]:
    """Return compact guardrail penalty labels for display."""
    labels: list[str] = []
    for item in guardrail.get("penalties", []) or []:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason", "")).strip()
        if reason:
            labels.append(slug_reason(reason))
    if labels:
        return dedupe(labels)
    penalty_total = int(guardrail.get("guardrail_penalty_total", 0) or 0)
    if penalty_total <= 0:
        return []
    warnings = guardrail.get("guardrail_warnings") or guardrail.get("warnings") or []
    return dedupe(slug_reason(str(reason)) for reason in warnings if reason)


def slug_reason(reason: str) -> str:
    """Return a compact display label for one guardrail reason."""
    text = reason.strip().lower()
    for suffix in (" robustness penalty", " penalty"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return "_".join(text.replace("-", " ").split())


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def dedupe(values: Any) -> list[str]:
    output: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in output:
            output.append(text)
    return output
