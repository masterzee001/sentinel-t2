"""Sentinel supervisor: keeps the live book alive and reporting.

One detached process that owns operations:
  1. Ensures the MT5 terminal is running (starts it if not).
  2. Starts the trading engines and restarts any whose heartbeat goes stale —
     with a Telegram alert each time.
  3. Sends a daily Telegram digest of the whole book at 07:00 WAT.

Heartbeats are the engines' own status files (rewritten every cycle):
  meanrev:  data/reports/meanrev_live_status.json     (stale > 20 min)

The champion engine was RETIRED on 2026-08-12: it failed its own promotion
gate on its own tape (PF 1.073 vs 1.10; 50% positive quarters vs 60%) and was
negative at the honest cost assumption (-31.4R at 2.31x the modelled spread,
breakeven at only 1.66x).

Run at boot via the SentinelSupervisor scheduled task. The engines must NOT
be started by hand while the supervisor runs — it is the single owner.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.shared.telegram_notify import notify_telegram

PYTHON = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
ENGINES = {
    "meanrev": {
        "args": [PYTHON, "-u", "scripts/run_mean_reversion_live.py", "--interval-seconds", "300", "--execute-demo"],
        "status": PROJECT_ROOT / "data" / "reports" / "meanrev_live_status.json",
        "log": PROJECT_ROOT / "data" / "live_paper" / "meanrev.log",
        "stale_seconds": 1200,
        "data_stale_seconds": 3600,
    },
}
DIGEST_HOUR_UTC = 6  # 07:00 WAT.
CHECK_INTERVAL_SECONDS = 120
DIGEST_MARK = PROJECT_ROOT / "data" / "live_paper" / "last_digest_date.txt"

# An engine we have just started has not written a heartbeat yet, so its age
# reads as infinity — which cleared every staleness threshold instantly and got
# it killed on the next check, forever, without ever surviving long enough to
# write the file it was being judged on. Its first cycle connects to MT5 and
# runs the per-symbol preflight, so it needs room. Well inside stale_seconds
# (1200) so a genuinely hung engine is still caught. Incident 2026-08-12: the
# heartbeat file was deleted under a live engine and this loop ran silently.
ENGINE_GRACE_SECONDS = 600.0


# The supervisor runs under pythonw (no console). A console program like
# tasklist spawned from a console-less process gets a brand-new console
# window, and on Windows 11 the default console host is Windows Terminal -
# so every 120s health check flashed a PowerShell-looking window at the
# operator (reported 2026-08-13). CREATE_NO_WINDOW runs it invisibly.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def mt5_running() -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq terminal64.exe"],
        capture_output=True, text=True, creationflags=NO_WINDOW,
    )
    return "terminal64.exe" in (result.stdout or "")


def ensure_mt5() -> bool:
    if mt5_running():
        return True
    path = os.getenv("MT5_TERMINAL_PATH")
    if not path or not Path(path).exists():
        return False
    # /config forces AllowLiveTrading=1. The AutoTrading toggle is a GUI
    # switch that defaults OFF, and while it is off the broker refuses every
    # programmatic order with retcode 10027 — it silently blocked the first
    # real signal on Pepperstone (2026-08-12). Nobody can click a button on
    # a VPS, so it is set at launch.
    startup_config = PROJECT_ROOT / "config" / "mt5_startup.ini"
    args = [path, f"/config:{startup_config}"] if startup_config.exists() else [path]
    subprocess.Popen(args, creationflags=subprocess.DETACHED_PROCESS)
    time.sleep(45)  # First boot needs time to connect before engines start.
    return mt5_running()


def status_age_seconds(engine: dict) -> float:
    status: Path = engine["status"]
    if not status.exists():
        return float("inf")
    return time.time() - status.stat().st_mtime


SUPERVISOR_LOG = PROJECT_ROOT / "data" / "live_paper" / "supervisor.log"
LOG_MAX_BYTES = 1_000_000


def log_line(message: str) -> None:
    """Timestamped supervisor output, written to its own file.

    Writing the file directly rather than relying on redirected stdout: the
    supervisor normally starts from the Windows logon script under pythonw,
    which has nowhere to send stdout, so every line was being discarded on the
    one launch path that matters. The engine died five times on 2026-08-12 and
    left nothing to work from - partly because these lines had no timestamp,
    and partly because on a logon start they were never written at all.
    """
    line = f"{datetime.now(UTC).isoformat(timespec='seconds')} {message}"
    print(line, flush=True)
    try:
        SUPERVISOR_LOG.parent.mkdir(parents=True, exist_ok=True)
        if SUPERVISOR_LOG.exists() and SUPERVISOR_LOG.stat().st_size > LOG_MAX_BYTES:
            SUPERVISOR_LOG.replace(SUPERVISOR_LOG.with_suffix(".log.1"))
        with SUPERVISOR_LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        # Logging must never be the reason the supervisor stops supervising.
        pass


def describe_exit(code: int | None) -> str:
    """Explain how a child ended, in the terms that tell them apart.

    The exit code was previously read only for None-ness and thrown away, so
    five deaths in one day produced no evidence at all. It is the one number
    that separates the possibilities: 0 means the engine chose to return, a
    small positive code means something terminated it deliberately, and a
    large 0xC0000005-style code means the process crashed - which for this
    engine points at the MetaTrader5 C extension, since a Python-level failure
    would have left a traceback in meanrev.log.
    """
    if code is None:
        return "no child of ours (nothing to report)"
    if code == 0:
        return "exit 0 - returned normally, so it chose to stop"
    if code < 0:
        return f"killed by signal {-code}"
    return f"exit {code} (0x{code & 0xFFFFFFFF:08X})"


def describe_age(seconds: float) -> str:
    """Render an age for an alert without assuming it is finite.

    status_age_seconds returns infinity when the heartbeat file is absent, and
    int(infinity) raises OverflowError. That used to fire AFTER the restart had
    already happened, so the outer handler swallowed it and the alert was never
    sent — the supervisor restarted the engine over and over in total silence.
    """
    if not math.isfinite(seconds):
        return "never written"
    return f"{int(seconds)}s"


def decide_action(
    age: float,
    feed_age: float,
    child_state: str,
    child_age: float,
    engine: dict,
) -> str:
    """Decide what to do about one engine. Pure, so the paths that kill live
    processes can be tested without spawning any.

    age         seconds since the heartbeat file last changed (inf if absent)
    feed_age    seconds since the engine last got an answer out of MT5
    child_state "alive" | "exited" (we started it and it is gone) | "unknown"
                (we never started one, so whatever is running is not ours)
    child_age   seconds since we started it (inf if we never did)
    """
    if child_state == "exited":
        # We watched this one die, so there is no ambiguity to wait out. The
        # heartbeat gate below would hold recovery for up to stale_seconds
        # (20 min) - long enough to miss an entry window outright, which
        # nearly happened on 2026-08-12 when the engine died five times.
        return "start"
    if child_state == "unknown":
        # Never started one. A fresh heartbeat therefore means somebody else's
        # engine is alive and writing it - most likely another supervisor's.
        # Starting a second would put two books on the same account, so wait
        # for the heartbeat to go stale before assuming the field is clear.
        if age > engine["stale_seconds"]:
            return "start"
        return "none"
    looks_stale = age > engine["stale_seconds"]
    looks_feedless = feed_age > engine["data_stale_seconds"]
    if child_age < ENGINE_GRACE_SECONDS and (looks_stale or looks_feedless):
        # Ours, too young to have reported yet, and only failing because of
        # that. Nothing it could have done differently, so nothing to judge it
        # on. Note the guard is deliberately narrow: a young engine that IS
        # reporting normally falls through to the checks below, so the grace
        # period buys silence for a slow start rather than blanket immunity.
        return "wait_first_heartbeat"
    if looks_feedless:
        return "kill_restart_dead_feed"
    # Alive but stale gets one extra interval before we kill it: MT5 IPC
    # hiccups usually self-recover.
    if age > engine["stale_seconds"] * 3:
        return "kill_restart_hung"
    return "none"


def start_engine(name: str, engine: dict, children: dict, started: dict) -> None:
    log = open(engine["log"], "a", encoding="utf-8")
    children[name] = subprocess.Popen(
        engine["args"],
        cwd=str(PROJECT_ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.DETACHED_PROCESS,
    )
    started[name] = time.time()


def read_status(engine: dict) -> dict:
    try:
        return json.loads(engine["status"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def data_age_seconds(engine: dict) -> float:
    """Seconds since the engine last got a real answer out of MT5.

    File mtime only proves the Python process is alive; a dead or hung
    terminal keeps the heartbeat fresh while the book silently stops
    trading. This reads the engine-reported MT5 health instead.
    """
    status = read_status(engine)
    health = status.get("mt5_health") or {}
    last = health.get("last_success_utc")
    if not last:
        return 0.0  # engine has not reported health yet — do not act on silence
    try:
        stamp = datetime.fromisoformat(str(last))
    except ValueError:
        return 0.0
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return (datetime.now(UTC) - stamp).total_seconds()


def build_digest() -> str:
    meanrev = read_status(ENGINES["meanrev"])
    lines = ["SENTINEL DAILY DIGEST"]
    open_mr = meanrev.get("open_positions", {})
    lines.append(
        f"MeanRev: {meanrev.get('closed_trades', 0)} closed | net {meanrev.get('net_rr', 0)}R | "
        f"rwPF {meanrev.get('risk_weighted_pf', 0)} (target 1.85) | open {len(open_mr)}"
    )
    for symbol, position in (open_mr or {}).items():
        lines.append(f"  MR open: {symbol} @ {position.get('entry')} day {position.get('held_days')}")
    lines.append("Demo-only; kill switch: data/live_paper/KILL_SWITCH")
    return "\n".join(lines)


def maybe_send_digest(now: datetime) -> None:
    if now.hour != DIGEST_HOUR_UTC:
        return
    today = now.date().isoformat()
    last = DIGEST_MARK.read_text(encoding="utf-8").strip() if DIGEST_MARK.exists() else ""
    if last == today:
        return
    notify_telegram(build_digest())
    DIGEST_MARK.parent.mkdir(parents=True, exist_ok=True)
    DIGEST_MARK.write_text(today, encoding="utf-8")


LOCK_PATH = PROJECT_ROOT / "data" / "live_paper" / "supervisor.lock"


def another_supervisor_alive() -> bool:
    """Singleton guard: refuse to start if a live supervisor holds the lock."""
    if not LOCK_PATH.exists():
        return False
    try:
        other_pid = int(LOCK_PATH.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return False
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {other_pid}"],
        capture_output=True, text=True, creationflags=NO_WINDOW,
    )
    return "python" in (result.stdout or "").lower()


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    if another_supervisor_alive():
        print("Another supervisor is already running; exiting.", flush=True)
        return 0
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
    children: dict[str, subprocess.Popen] = {}
    started: dict[str, float] = {}
    notify_telegram("Sentinel SUPERVISOR online: watching MT5 + the mean-reversion engine; daily digest 07:00 WAT.")
    log_line("SENTINEL SUPERVISOR online")
    while True:
        try:
            if not ensure_mt5():
                notify_telegram("SUPERVISOR ALERT: MT5 terminal is not running and could not be started.")
                time.sleep(600)
                continue
            for name, engine in ENGINES.items():
                age = status_age_seconds(engine)
                feed_age = data_age_seconds(engine)
                child = children.get(name)
                # Read the code before start_engine replaces the handle: it is
                # the only surviving evidence of HOW the last engine ended.
                exit_code = child.poll() if child is not None else None
                if child is None:
                    child_state = "unknown"
                elif exit_code is not None:
                    child_state = "exited"
                else:
                    child_state = "alive"
                child_age = time.time() - started[name] if name in started else math.inf
                action = decide_action(age, feed_age, child_state, child_age, engine)
                # Act first, alert second: a Telegram call that throws must
                # never be the reason the book fails to come back up.
                if action == "start":
                    ended = describe_exit(exit_code)
                    start_engine(name, engine, children, started)
                    log_line(f"(re)started {name} - previous instance: {ended}")
                    notify_telegram(
                        f"SUPERVISOR: {name} engine heartbeat {describe_age(age)} - (re)started it.\n"
                        f"Previous instance: {ended}"
                    )
                elif action == "kill_restart_dead_feed":
                    # A live process with a dead MT5 feed is the silent failure
                    # mode the heartbeat cannot see: force a restart on it.
                    child.kill()
                    start_engine(name, engine, children, started)
                    log_line(f"{name}: MT5 feed dead {describe_age(feed_age)} - restarting")
                    notify_telegram(
                        f"SUPERVISOR: {name} process is alive but MT5 returned nothing for "
                        f"{describe_age(feed_age)} - restarting it. Check the terminal is logged in."
                    )
                elif action == "kill_restart_hung":
                    child.kill()
                    start_engine(name, engine, children, started)
                    log_line(f"{name}: hung {describe_age(age)} - killed and restarted")
                    notify_telegram(
                        f"SUPERVISOR: {name} engine hung ({describe_age(age)}) - killed and restarted."
                    )
                elif action == "wait_first_heartbeat":
                    log_line(f"{name}: started {int(child_age)}s ago, waiting for its first heartbeat")
            maybe_send_digest(datetime.now(UTC))
        except Exception as exc:  # Supervisor must never die of its own bugs.
            log_line(f"supervisor error: {exc}")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
