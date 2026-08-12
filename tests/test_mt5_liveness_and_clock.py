"""MT5 liveness detection and broker-clock measurement.

Two audit findings from 2026-08-11, both silent-failure class:
  1. The supervisor's heartbeat (status-file mtime) only proves the Python
     process is alive. A dead or hung terminal kept the file refreshing, so
     a broken feed was indistinguishable from a quiet market and no restart
     or alert ever fired.
  2. The broker's UTC offset was hardcoded to +3. MetaQuotes-Demo runs EET,
     which drops to +2 at the late-October change — every entry window would
     have fired an hour off, silently.
"""

from __future__ import annotations

import importlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from backend.market_data.mt5_connector import MT5Connector

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _ClockMT5:
    """Serves a tick whose timestamp is server-time-encoded like MT5 does."""

    def __init__(self, offset_hours: float, tick_age_seconds: float = 2.0) -> None:
        self.offset_hours = offset_hours
        self.tick_age_seconds = tick_age_seconds

    def symbol_select(self, symbol, enable=True):
        return True

    def symbol_info_tick(self, symbol):
        server_epoch = time.time() + self.offset_hours * 3600 - self.tick_age_seconds
        return SimpleNamespace(time=server_epoch, bid=1.0, ask=1.1)


def make_connector(mt5_module) -> MT5Connector:
    connector = MT5Connector(mt5_module=mt5_module)
    connector.supported_symbols = frozenset({"US500", "US30", "EURUSD"})
    return connector


def test_server_offset_is_measured_not_assumed():
    """The broker clock is read from a fresh tick, so DST is handled."""
    assert make_connector(_ClockMT5(3.0)).server_utc_offset_hours() == 3.0
    # Winter: MetaQuotes-Demo drops to UTC+2 — must be detected, not assumed.
    assert make_connector(_ClockMT5(2.0)).server_utc_offset_hours() == 2.0


def test_stale_tick_yields_no_measurement():
    """A closed market serves an old tick; guessing from it would be wrong."""
    connector = make_connector(_ClockMT5(3.0, tick_age_seconds=40 * 60))
    assert connector.server_utc_offset_hours() is None


class _Fixed:
    def __init__(self, value):
        self.value = value

    def server_utc_offset_hours(self):
        return self.value


def test_meanrev_falls_back_to_last_known_offset():
    module = importlib.import_module("scripts.run_mean_reversion_live")
    state = {"server_offset_hours": 2.0}
    assert module.refresh_server_offset(_Fixed(None), state) == 2.0  # remembered
    assert module.refresh_server_offset(_Fixed(None), {}) == module.SERVER_OFFSET_FALLBACK_HOURS
    fresh: dict = {}
    assert module.refresh_server_offset(_Fixed(3.0), fresh) == 3.0  # first reading seeds
    assert fresh["server_offset_hours"] == 3.0


def test_offset_change_requires_sustained_confirmation():
    """A quiet market can serve a stale tick that implies a wrong whole-hour
    offset for several consecutive reads — three reads span only ~10 minutes
    at the engine's poll rate, which produced a FALSE 'UTC+3 -> UTC+2 DST
    change' live on 2026-08-12. The new value must also PERSIST."""
    module = importlib.import_module("scripts.run_mean_reversion_live")
    state = {"server_offset_hours": 3.0}
    assert module.refresh_server_offset(_Fixed(0.0), state) == 3.0  # one-off ignored
    # Many agreeing reads in quick succession must STILL not move the clock.
    for _ in range(10):
        module.refresh_server_offset(_Fixed(2.0), state)
    assert state["server_offset_hours"] == 3.0, "count alone must not be enough"
    # Backdate the first sighting past the persistence window: a real DST
    # change is permanent, so it survives this test; a frozen tick cannot.
    held = datetime.now(UTC) - timedelta(minutes=module.OFFSET_CONFIRMATION_MINUTES + 1)
    state["pending_offset_since"] = held.isoformat()
    assert module.refresh_server_offset(_Fixed(2.0), state) == 2.0
    assert state["server_offset_hours"] == 2.0


def test_flapping_measurements_do_not_move_the_clock():
    module = importlib.import_module("scripts.run_mean_reversion_live")
    state = {"server_offset_hours": 3.0}
    for value in (2.0, 1.0, 2.0, 0.0, 2.0):
        module.refresh_server_offset(_Fixed(value), state)
    assert state["server_offset_hours"] == 3.0


def test_windows_follow_the_measured_clock():
    """DE40's 22:30-22:55 window must track the broker clock, not UTC+3."""
    module = importlib.import_module("scripts.run_mean_reversion_live")
    # 19:40 UTC is 22:40 server in summer (+3) -> DE40 window open;
    # the same UTC instant is 21:40 server in winter (+2) -> closed.
    utc_hour = 19
    assert module.symbol_in_window("DE40", (utc_hour + 3) % 24, 40) is True
    assert module.symbol_in_window("DE40", (utc_hour + 2) % 24, 40) is False


def _engine(tmp_path: Path, health: dict | None) -> dict:
    status = tmp_path / "status.json"
    payload = {"mode": "TEST"}
    if health is not None:
        payload["mt5_health"] = health
    status.write_text(json.dumps(payload), encoding="utf-8")
    return {"status": status, "data_stale_seconds": 1800}


def test_supervisor_sees_dead_feed_behind_a_fresh_heartbeat(tmp_path: Path):
    supervisor = importlib.import_module("scripts.run_sentinel_supervisor")
    stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    engine = _engine(tmp_path, {"last_success_utc": stale, "consecutive_failures": 40})
    # The status file was just written (fresh heartbeat) but the feed is dead.
    assert supervisor.status_age_seconds(engine) < 60
    assert supervisor.data_age_seconds(engine) > engine["data_stale_seconds"]


def test_supervisor_tolerates_missing_or_unparsable_health(tmp_path: Path):
    """Absent health must not be read as a dead feed (no restart storms)."""
    supervisor = importlib.import_module("scripts.run_sentinel_supervisor")
    assert supervisor.data_age_seconds(_engine(tmp_path, None)) == 0.0
    assert supervisor.data_age_seconds(_engine(tmp_path, {"last_success_utc": "not-a-date"})) == 0.0
    healthy = _engine(tmp_path, {"last_success_utc": datetime.now(UTC).isoformat()})
    assert supervisor.data_age_seconds(healthy) < 60


def test_engines_report_mt5_health_in_status():
    """Regression pin: both engines must publish the liveness signal."""
    for script in ("run_champion_paper.py", "run_mean_reversion_live.py"):
        source = (PROJECT_ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert "mt5_health" in source, f"{script} no longer reports MT5 liveness"
