from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.news_filter.news_filter import NewsFilter


def write_news_config(config_dir: Path, enabled: bool = True) -> None:
    config_dir.mkdir()
    (config_dir / "news_filter.yaml").write_text(
        f"""
enabled: {str(enabled).lower()}
timezone: WAT
pre_event_block_minutes: 30
post_event_block_minutes: 30
events:
  - name: "CPI"
    currency: "USD"
    impact: "high"
    datetime: "2026-06-28 14:30"
""",
        encoding="utf-8",
    )


def make_filter(tmp_path: Path, enabled: bool = True) -> NewsFilter:
    config_dir = tmp_path / "config"
    write_news_config(config_dir, enabled=enabled)
    return NewsFilter(config_dir=config_dir)


def test_lock_active_30_minutes_before_event(tmp_path: Path):
    news_filter = make_filter(tmp_path)

    status = news_filter.check(
        "XAUUSD",
        current_time=datetime(2026, 6, 28, 14, 0, tzinfo=ZoneInfo("Africa/Lagos")),
    )

    assert status["lock_active"] is True
    assert status["event_name"] == "CPI"
    assert status["minutes_to_event"] == 30
    assert "XAUUSD" in status["affected_symbols"]


def test_lock_active_30_minutes_after_event(tmp_path: Path):
    news_filter = make_filter(tmp_path)

    status = news_filter.check(
        "US30",
        current_time=datetime(2026, 6, 28, 15, 0, tzinfo=ZoneInfo("Africa/Lagos")),
    )

    assert status["lock_active"] is True
    assert status["minutes_to_event"] == -30
    assert status["reason"] == "High impact news lock active: CPI 30 minutes ago."


def test_no_lock_outside_window(tmp_path: Path):
    news_filter = make_filter(tmp_path)

    before = news_filter.check(
        "EURUSD",
        current_time=datetime(2026, 6, 28, 13, 59, tzinfo=ZoneInfo("Africa/Lagos")),
    )
    after = news_filter.check(
        "GBPUSD",
        current_time=datetime(2026, 6, 28, 15, 1, tzinfo=ZoneInfo("Africa/Lagos")),
    )

    assert before["lock_active"] is False
    assert after["lock_active"] is False


def test_disabled_filter_does_nothing(tmp_path: Path):
    news_filter = make_filter(tmp_path, enabled=False)

    status = news_filter.check(
        "XAUUSD",
        current_time=datetime(2026, 6, 28, 14, 15, tzinfo=ZoneInfo("Africa/Lagos")),
    )

    assert status == {
        "enabled": False,
        "lock_active": False,
        "event_name": None,
        "minutes_to_event": None,
        "affected_symbols": [],
        "reason": "",
    }


def test_format_status_for_locked_event(tmp_path: Path):
    news_filter = make_filter(tmp_path)
    status = news_filter.check(
        "XAUUSD",
        current_time=datetime(2026, 6, 28, 14, 12, tzinfo=ZoneInfo("Africa/Lagos")),
    )

    assert NewsFilter.format_status(status) == "LOCKED - CPI in 18 minutes"
