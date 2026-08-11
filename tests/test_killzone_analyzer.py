from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.killzone_engine.killzone_analyzer import KillzoneAnalyzer


def write_killzone_config(config_dir: Path) -> None:
    config_dir.mkdir()
    (config_dir / "killzones.yaml").write_text(
        """
timezone: WAT
killzones:
  london_open:
    start: "08:00"
    end: "09:30"
    symbols:
      - XAUUSD
      - EURUSD
      - GBPUSD
    quality_score: 10
    commentary: "London open raid window."
  london_continuation:
    start: "09:30"
    end: "11:00"
    symbols:
      - XAUUSD
      - EURUSD
      - GBPUSD
    quality_score: 7
    commentary: "London continuation or reversal confirmation window."
  new_york_open:
    start: "13:30"
    end: "15:00"
    symbols:
      - XAUUSD
      - US30
      - EURUSD
      - GBPUSD
    quality_score: 10
    commentary: "New York open volatility and displacement window."
  new_york_continuation:
    start: "15:00"
    end: "16:00"
    symbols:
      - XAUUSD
      - US30
      - EURUSD
      - GBPUSD
    quality_score: 7
    commentary: "New York continuation or target delivery window."
  dead_zone:
    start: "16:00"
    end: "23:59"
    symbols:
      - XAUUSD
      - US30
      - EURUSD
      - GBPUSD
    quality_score: 0
    commentary: "Outside preferred execution window. Avoid new trades."
""",
        encoding="utf-8",
    )


def make_analyzer(tmp_path: Path) -> KillzoneAnalyzer:
    config_dir = tmp_path / "config"
    write_killzone_config(config_dir)
    return KillzoneAnalyzer(config_dir=config_dir)


def test_london_open_detection(tmp_path: Path):
    # Candle times arrive as broker server time stamped as UTC; the analyzer
    # reads that wall clock as session time (see test_killzone_session_anchor).
    status = make_analyzer(tmp_path).analyze(
        "XAUUSD",
        datetime(2026, 6, 28, 8, 30, tzinfo=ZoneInfo("UTC")),
    )

    assert status["active_killzone"] == "london_open"
    assert status["is_valid"] is True
    assert status["quality_score"] == 10
    assert status["minutes_to_next_killzone"] == 60


def test_ny_open_detection(tmp_path: Path):
    status = make_analyzer(tmp_path).analyze("US30", "14:00")

    assert status["active_killzone"] == "new_york_open"
    assert status["is_valid"] is True
    assert status["quality_score"] == 10
    assert status["minutes_to_next_killzone"] == 60


def test_us30_invalid_during_london(tmp_path: Path):
    status = make_analyzer(tmp_path).analyze("US30", "08:30")

    assert status["active_killzone"] == "none"
    assert status["is_valid"] is False
    assert status["quality_score"] == 0
    assert status["minutes_to_next_killzone"] == 300


def test_outside_killzone_rejection_dead_zone(tmp_path: Path):
    status = make_analyzer(tmp_path).analyze("XAUUSD", "17:00")

    assert status["active_killzone"] == "dead_zone"
    assert status["is_valid"] is False
    assert status["quality_score"] == 0
    assert status["commentary"] == "Outside preferred execution window. Avoid new trades."


def test_minutes_to_next_killzone(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)

    assert analyzer.analyze("XAUUSD", "10:00")["minutes_to_next_killzone"] == 210
    assert analyzer.analyze("XAUUSD", "17:00")["minutes_to_next_killzone"] == 900

