"""Smoke test for Project Sentinel News Filter."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.news_filter.news_filter import NewsFilter


def main() -> int:
    """Print current high-impact news status."""
    news_filter = NewsFilter()
    status = news_filter.check()

    print("NEWS FILTER")
    print(f"Enabled:          {status['enabled']}")
    print(f"Status:           {NewsFilter.format_status(status)}")
    print(f"Lock Active:      {status['lock_active']}")
    print(f"Event:            {status['event_name'] or 'none'}")
    print(f"Minutes To Event: {status['minutes_to_event']}")
    print(f"Affected Symbols: {', '.join(status['affected_symbols']) if status['affected_symbols'] else 'none'}")
    print(f"Reason:           {status['reason'] or 'none'}")
    print("Advisor Mode only: no execution action was taken.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
