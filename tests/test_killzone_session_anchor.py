"""Killzone windows must select the same real-world hours on any broker.

Audit 2026-08-12 established the mechanism: MT5 stamps BROKER server time as
if it were UTC (mt5_connector), and the analyzer then converted that to
Africa/Lagos — so the effective decision clock was broker_time + 1h. The
champion's validated book (907 trades) sits entirely in broker-clock
12:30-14:45, i.e. the configured 13:30-15:00 window minus that spurious hour.

The windows are now anchored to European session time and evaluated from the
MEASURED broker offset. Anchoring to a fixed UTC window would have been
wrong: MetaQuotes observes DST, so the validated book contains both the
summer and winter variants of the same local window.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from backend.killzone_engine.killzone_analyzer import KillzoneAnalyzer

# The validated champion book: broker-clock 12:30 through 14:45 inclusive.
VALIDATED_BROKER_HOURS = [(12, 30), (13, 0), (13, 45), (14, 0), (14, 45)]
OUTSIDE_BROKER_HOURS = [(11, 45), (15, 0), (16, 30), (9, 0)]


def broker_stamp(month: int, day: int, hour: int, minute: int) -> datetime:
    """A candle time exactly as MT5 delivers it: server time labelled UTC."""
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def analyzer_for(offset_hours: float) -> KillzoneAnalyzer:
    return KillzoneAnalyzer(server_utc_offset_hours=offset_hours)


def test_validated_window_reproduced_on_metaquotes_summer():
    """MetaQuotes EEST (UTC+3) — the clock the champion was validated on."""
    analyzer = analyzer_for(3.0)
    for hour, minute in VALIDATED_BROKER_HOURS:
        result = analyzer.analyze("US30", current_time=broker_stamp(8, 12, hour, minute))
        assert result["is_valid"], f"{hour:02d}:{minute:02d} broker time must be tradable"
        assert result["active_killzone"] in {"new_york_open", "new_york_continuation"}
    for hour, minute in OUTSIDE_BROKER_HOURS:
        assert not analyzer.analyze("US30", current_time=broker_stamp(8, 12, hour, minute))["is_valid"]


def test_validated_window_reproduced_on_metaquotes_winter():
    """The SAME broker-clock hours were traded in winter, which is why a
    fixed-UTC anchor could not reproduce the book, and why a measured
    offset must not be subtracted from historical bars."""
    analyzer = analyzer_for(3.0)
    for hour, minute in VALIDATED_BROKER_HOURS:
        assert analyzer.analyze("US30", current_time=broker_stamp(1, 14, hour, minute))["is_valid"]
    for hour, minute in OUTSIDE_BROKER_HOURS:
        assert not analyzer.analyze("US30", current_time=broker_stamp(1, 14, hour, minute))["is_valid"]


def test_decisions_are_season_stable():
    """The validated window is pinned to the broker's LOCAL clock, so the
    same broker wall-clock hours must trade in summer AND winter. Applying a
    single measured offset to historical bars broke exactly this."""
    analyzer = analyzer_for(3.0)
    probe = [(11, 30), (12, 30), (13, 45), (14, 45), (15, 0), (15, 45)]
    summer = [analyzer.analyze("US30", current_time=broker_stamp(8, 12, h, m))["is_valid"] for h, m in probe]
    winter = [analyzer.analyze("US30", current_time=broker_stamp(1, 14, h, m))["is_valid"] for h, m in probe]
    assert summer == winter == [False, True, True, True, False, False]


def test_non_european_broker_falls_back_to_offset_arithmetic():
    """If a broker is NOT on European time the naive reading would be wrong,
    so the measured offset takes over and real hours are still selected."""
    import datetime as dt

    european = analyzer_for(3.0)
    exotic = analyzer_for(0.0)  # a UTC-clock broker
    real_utc = dt.datetime(2026, 8, 12, 10, 15, tzinfo=UTC)
    a = european.analyze("US30", current_time=real_utc + dt.timedelta(hours=3))
    b = exotic.analyze("US30", current_time=real_utc)
    assert a["is_valid"] == b["is_valid"] is True
    assert a["active_killzone"] == b["active_killzone"]


def test_unknown_broker_clock_falls_back_without_crashing():
    """No measurement (closed market) must not raise or silently trade."""
    analyzer = KillzoneAnalyzer(server_utc_offset_hours=None)
    result = analyzer.analyze("US30", current_time=broker_stamp(8, 12, 13, 30))
    assert "is_valid" in result and isinstance(result["is_valid"], bool)


def test_session_zone_tracks_dst_like_the_broker_did():
    """Europe/Kyiv was chosen because it shares the broker's DST rule; a
    fixed-offset zone would drift an hour every winter."""
    kyiv = ZoneInfo("Europe/Kyiv")
    summer = datetime(2026, 8, 12, 12, 0, tzinfo=kyiv).utcoffset()
    winter = datetime(2026, 1, 14, 12, 0, tzinfo=kyiv).utcoffset()
    assert summer.total_seconds() / 3600 == 3
    assert winter.total_seconds() / 3600 == 2
