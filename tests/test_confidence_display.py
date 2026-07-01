from __future__ import annotations

from backend.display.confidence_display import (
    OBSERVER_ONLY_LABEL,
    confidence_display_fields,
    observer_display_fields,
)


def test_raw_62_adjusted_34_displays_cold_with_penalty():
    display = confidence_display_fields(
        {
            "total_confidence": 62,
            "confidence_band": "COLD",
            "guardrail": {
                "guardrail_adjusted_confidence": 34,
                "adjusted_confidence_band": "COLD",
                "guardrail_penalty_total": 28,
                "penalties": [{"reason": "London continuation penalty", "value": 28}],
            },
        }
    )

    assert display["raw_confidence"] == 62
    assert display["adjusted_confidence"] == 34
    assert display["raw_band"] == "WARM"
    assert display["adjusted_band"] == "COLD"
    assert display["action"] == "WAIT"
    assert display["band_differs"] is True
    assert display["guardrail_penalty"] == "london_continuation"


def test_raw_44_adjusted_44_displays_warm_without_penalty():
    display = confidence_display_fields({"total_confidence": 44, "confidence_band": "WARM"})

    assert display["raw_confidence"] == 44
    assert display["adjusted_confidence"] == 44
    assert display["raw_band"] == "WARM"
    assert display["adjusted_band"] == "WARM"
    assert display["action"] == "MONITOR"
    assert display["band_differs"] is False
    assert display["guardrail_penalty"] == "none"


def test_observer_hot_display_labels_observer_only():
    display = observer_display_fields("NAS100", "HOT", 53)

    assert display["symbol"] == "NAS100"
    assert display["observer_state"] == "OBSERVER_HOT"
    assert display["display_state"] == "HOT"
    assert display["score"] == 53
    assert display["mode"] == OBSERVER_ONLY_LABEL
    assert display["execution_note"] == "No execution"
