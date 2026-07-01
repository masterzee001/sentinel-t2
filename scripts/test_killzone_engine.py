"""Manual smoke test for the Project Sentinel Killzone Engine."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.killzone_engine.killzone_analyzer import KillzoneAnalyzer


SYMBOLS = ("XAUUSD", "US30", "EURUSD", "GBPUSD")
TEST_TIMES = ("08:30", "10:00", "14:00", "17:00")


def print_status(status: dict) -> None:
    """Print one clean killzone status line."""
    print(
        f"{status['symbol']} | {status['current_time_wat']} | "
        f"{status['active_killzone']} | valid={status['is_valid']} | "
        f"quality={status['quality_score']} | next={status['minutes_to_next_killzone']}m | "
        f"{status['commentary']}"
    )


def main() -> int:
    analyzer = KillzoneAnalyzer()

    print("KILLZONE ENGINE")
    print("Current WAT status")
    for symbol in SYMBOLS:
        print_status(analyzer.analyze(symbol))

    for test_time in TEST_TIMES:
        print("")
        print(f"Test time {test_time} WAT")
        for symbol in SYMBOLS:
            print_status(analyzer.analyze(symbol, current_time=test_time))

    print("")
    print("Advisor Mode only: no execution action was taken.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

