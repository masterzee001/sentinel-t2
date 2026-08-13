"""The operator's start/stop/restart scripts must target the live stack.

STOP_SENTINEL.ps1 shipped in the initial baseline and still addressed the
Telegram-bot / dashboard / live-monitor stack through
.sentinel_runtime\\sentinel_processes.json. That stack was retired and the file
is no longer written, so the script reported "NOT RUNNING" three times and
stopped nothing while the supervisor and the engine kept trading. A stop
control that silently does nothing is worse than no stop control, so the
wiring is pinned here.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _code_only(text: str, comment_markers: tuple[str, ...]) -> str:
    """Drop comment lines: these tests are about what the script DOES, and the
    scripts describe the retired stack in their own comments."""
    kept = [
        line for line in text.splitlines()
        if not line.strip().lower().startswith(comment_markers)
    ]
    return "\n".join(kept)


STOP_PS1 = _code_only((PROJECT_ROOT / "STOP_SENTINEL.ps1").read_text(encoding="utf-8"), ("#",))
RESTART_BAT = _code_only(
    (PROJECT_ROOT / "RESTART_SENTINEL.bat").read_text(encoding="utf-8"), ("rem ", "::")
)


def test_stop_script_targets_the_processes_that_actually_run():
    assert "run_sentinel_supervisor" in STOP_PS1
    assert "run_mean_reversion_live" in STOP_PS1


def test_stop_script_no_longer_targets_the_retired_stack():
    assert "sentinel_processes.json" not in STOP_PS1


def test_supervisor_is_stopped_before_the_engine():
    """Order matters: the supervisor restarts a missing engine, so killing the
    engine first just gets it put back."""
    assert STOP_PS1.index("run_sentinel_supervisor") < STOP_PS1.index("run_mean_reversion_live")


def test_mt5_is_left_running_unless_explicitly_included():
    """Closing the terminal is opt-in - it is the only thing that meaningfully
    reduces data usage, but the engine cannot see prices without it."""
    assert "$IncludeMT5" in STOP_PS1
    assert "LEFT RUNNING" in STOP_PS1


def test_stop_refuses_to_abandon_an_open_position_without_force():
    """No stop loss sits on the broker and exits are only checked in the
    nightly window, so stopping while holding must be a deliberate act."""
    assert "REFUSING TO STOP" in STOP_PS1
    assert "-not $Force" in STOP_PS1


def test_stop_clears_the_supervisor_lock():
    """A leftover lock holding a recycled pid would make the next start decide
    a supervisor is already alive and quietly exit."""
    assert "supervisor.lock" in STOP_PS1
    assert "Remove-Item" in STOP_PS1


def test_restart_clears_the_status_file_so_the_engine_comes_back_at_once():
    """The supervisor only (re)starts an engine whose heartbeat is older than
    20 minutes. The status file left by the engine we just stopped looks
    fresh, so without removing it the book sits dead for up to 20 minutes."""
    assert "meanrev_live_status.json" in RESTART_BAT
    assert "del" in RESTART_BAT


def test_the_retired_stack_launcher_is_gone():
    """START_SENTINEL.ps1 started the Telegram bot, the Streamlit dashboard and
    run_sentinel_live.py. Two of those no longer exist and the third is the
    retired live monitor - and it would have started them ALONGSIDE the
    supervisor, which is the single owner of the engines."""
    assert not (PROJECT_ROOT / "START_SENTINEL.ps1").exists()


def test_the_operator_buttons_are_exactly_the_supported_ones():
    buttons = sorted(p.name for p in PROJECT_ROOT.glob("*_SENTINEL.*"))
    assert buttons == [
        "CHECK_SENTINEL.bat",
        "RESTART_SENTINEL.bat",
        "START_SENTINEL.bat",
        "STOP_SENTINEL.bat",
        "STOP_SENTINEL.ps1",
    ]


def test_supervisor_writes_its_own_log_rather_than_relying_on_redirection():
    """The logon autostart runs pythonw, which has nowhere to send stdout, so
    a redirect-only design logged nothing on the one path that matters."""
    src = (PROJECT_ROOT / "scripts" / "run_sentinel_supervisor.py").read_text(encoding="utf-8")
    assert "SUPERVISOR_LOG" in src
    assert 'SUPERVISOR_LOG.open("a"' in src
    assert "except OSError" in src, "a failed write must never stop the supervisor"


def test_launchers_do_not_also_redirect_into_the_same_log():
    """Self-logging plus redirection would write every line twice. Comments may
    mention the log; only actual redirection into it is the problem."""
    for name in ("START_SENTINEL.bat", "RESTART_SENTINEL.bat"):
        code = _code_only((PROJECT_ROOT / name).read_text(encoding="utf-8"), ("rem ", "::"))
        for line in code.splitlines():
            if "supervisor.log" in line:
                assert ">" not in line and "RedirectStandardOutput" not in line, (
                    f"{name} redirects into supervisor.log, which the supervisor already writes"
                )


def test_restart_goes_through_the_supervisor_not_the_engine():
    """The supervisor is the single owner of the engines - starting an engine
    by hand alongside it produces two competing books."""
    assert "run_sentinel_supervisor.py" in RESTART_BAT
    assert "run_mean_reversion_live.py" not in RESTART_BAT
