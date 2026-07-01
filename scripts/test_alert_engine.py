"""Smoke test for Project Sentinel Alert Engine."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.alerts.alert_engine import AlertEngine


def main() -> int:
    """Simulate meaningful alert transitions."""
    engine = AlertEngine()
    now = datetime.now(ZoneInfo("Africa/Lagos"))
    scenarios = [
        ("WARM -> HOT", {"previous_state": "WARM", "current_state": "HOT", "confidence": 78}),
        (
            "HOT -> EXECUTION_READY",
            {"previous_state": "HOT", "current_state": "EXECUTION_READY", "confidence": 92},
        ),
        (
            "EXECUTION_READY -> WARM",
            {"previous_state": "EXECUTION_READY", "current_state": "WARM", "confidence": 65},
        ),
        ("Repeated within cooldown", {"previous_state": "WARM", "current_state": "HOT", "confidence": 79}),
        ("Risk blocked", {"previous_state": "HOT", "current_state": "HOT", "confidence": 80, "risk_status": "BLOCKED"}),
        ("News lock active", {"previous_state": "HOT", "current_state": "HOT", "confidence": 80, "news_lock_active": True}),
    ]

    print("ALERT ENGINE")
    for index, (label, kwargs) in enumerate(scenarios):
        alert = engine.evaluate(symbol="XAUUSD", timestamp=now + timedelta(minutes=index), **kwargs)
        print(f"\n{label}")
        print(f"Triggered:  {alert['alert_triggered']}")
        print(f"Transition: {alert['transition']}")
        print(f"Message:    {alert['message'] or 'none'}")
        if alert.get("suppressed_by_cooldown"):
            print("Cooldown:   suppressed")
    print("\nAdvisor Mode only: no execution action was taken.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
