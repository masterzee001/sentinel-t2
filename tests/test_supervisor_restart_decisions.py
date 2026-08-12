"""The supervisor must survive a missing heartbeat file.

Incident 2026-08-12. The engine's status file was deleted from under a live
engine (a git checkout removed it while untracking it). status_age_seconds
returns infinity for an absent file, which cleared every staleness threshold
at once, so the supervisor killed and restarted the engine on every 120s pass
while its first cycle - MT5 connect plus the per-symbol preflight - never had
time to write the file it was being judged on.

It was silent, too: the alert interpolated int(age), and int(infinity) raises
OverflowError. That fired after the restart had already happened, so the outer
"supervisor must never die of its own bugs" handler swallowed it and no
Telegram message was ever sent. A crash loop that reports nothing is the exact
failure class this project keeps auditing for.

The restart decision is a pure function so these paths can be exercised
without spawning processes.
"""

from __future__ import annotations

import importlib
import math

supervisor = importlib.import_module("scripts.run_sentinel_supervisor")

ENGINE = {"stale_seconds": 1200, "data_stale_seconds": 3600}

FRESH = 10.0        # heartbeat written moments ago
STALE = 1500.0      # past stale_seconds
HUNG = 5000.0       # past stale_seconds * 3
MISSING = math.inf  # no heartbeat file at all
YOUNG = 30.0        # we started it 30s ago
OLD = 4000.0        # it has been up long enough to have reported


def test_missing_heartbeat_does_not_kill_a_freshly_started_engine():
    """The regression. Infinity must not be read as 'hung'."""
    action = supervisor.decide_action(MISSING, 0.0, "alive", YOUNG, ENGINE)
    assert action == "wait_first_heartbeat"


def test_missing_heartbeat_still_starts_an_engine_that_is_not_running():
    """A fresh machine has no status file and no engine - that must start it."""
    assert supervisor.decide_action(MISSING, 0.0, "unknown", math.inf, ENGINE) == "start"


def test_a_child_we_watched_die_is_restarted_at_once():
    """No ambiguity to wait out. Deferring to the heartbeat gate cost up to 20
    minutes of downtime per death, and the engine died five times on
    2026-08-12 - one of those landing near an entry window would have lost the
    night's trades outright."""
    assert supervisor.decide_action(FRESH, 0.0, "exited", OLD, ENGINE) == "start"
    assert supervisor.decide_action(MISSING, 0.0, "exited", YOUNG, ENGINE) == "start"


def test_an_engine_that_never_reports_is_eventually_killed():
    """The grace period delays the kill; it must not cancel it."""
    assert supervisor.decide_action(MISSING, 0.0, "alive", OLD, ENGINE) == "kill_restart_hung"


def test_describe_age_survives_an_absent_heartbeat():
    """int(inf) raised OverflowError and lost the alert after the restart."""
    assert supervisor.describe_age(MISSING) == "never written"
    assert supervisor.describe_age(42.7) == "42s"


def test_a_healthy_engine_is_left_alone():
    assert supervisor.decide_action(FRESH, 0.0, "alive", OLD, ENGINE) == "none"


def test_a_young_engine_that_is_reporting_normally_is_not_called_silent():
    """The grace period buys silence for a slow start, not blanket immunity.
    The first version returned wait_first_heartbeat for ANY young child, so a
    perfectly healthy engine logged 'waiting for its first heartbeat' every
    two minutes while its heartbeat sat 194s old (observed 2026-08-12)."""
    assert supervisor.decide_action(FRESH, 0.0, "alive", YOUNG, ENGINE) == "none"


def test_a_dead_feed_behind_a_fresh_heartbeat_is_still_caught():
    """The silent failure the heartbeat cannot see must survive this refactor."""
    assert supervisor.decide_action(FRESH, 4000.0, "alive", OLD, ENGINE) == "kill_restart_dead_feed"


def test_a_dead_feed_does_not_kill_an_engine_that_has_not_reported_yet():
    """The status file still holds the PREVIOUS engine's health after a
    restart, so a young child would otherwise be killed for its predecessor's
    dead feed."""
    assert supervisor.decide_action(FRESH, 4000.0, "alive", YOUNG, ENGINE) == "wait_first_heartbeat"


def test_a_stale_but_live_engine_gets_one_more_interval():
    """Past stale_seconds but not yet stale_seconds * 3: MT5 IPC hiccups
    usually self-recover, so do not kill on the first sighting."""
    assert supervisor.decide_action(STALE, 0.0, "alive", OLD, ENGINE) == "none"
    assert supervisor.decide_action(HUNG, 0.0, "alive", OLD, ENGINE) == "kill_restart_hung"


def test_a_dead_child_behind_a_fresh_heartbeat_is_not_restarted():
    """Something else is writing that file - most likely another supervisor's
    engine. Starting a second one would put two books on the same account."""
    assert supervisor.decide_action(FRESH, 0.0, "unknown", math.inf, ENGINE) == "none"
    assert supervisor.decide_action(STALE, 0.0, "unknown", math.inf, ENGINE) == "start"


def test_grace_period_is_shorter_than_the_staleness_threshold():
    """Otherwise a genuinely hung engine would outlive its own detection."""
    assert supervisor.ENGINE_GRACE_SECONDS < ENGINE["stale_seconds"]
