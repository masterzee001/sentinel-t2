"""Sentinel supervisor: keeps the two-engine live book alive and reporting.

One detached process that owns operations:
  1. Ensures the MT5 terminal is running (starts it if not).
  2. Starts both engines (champion + mean reversion) and restarts either one
     whose heartbeat goes stale — with a Telegram alert each time.
  3. Sends a daily Telegram digest of the whole book at 07:00 WAT.

Heartbeats are the engines' own status files (rewritten every cycle):
  champion: data/reports/champion_paper_status.json   (stale > 5 min)
  meanrev:  data/reports/meanrev_live_status.json     (stale > 20 min)

Run at boot via the SentinelSupervisor scheduled task. The engines must NOT
be started by hand while the supervisor runs — it is the single owner.
"""

from __future__ import annotations

import json
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

from scripts.run_champion_paper import notify_telegram

PYTHON = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
ENGINES = {
    "champion": {
        # Live again (2026-08-12): killzone windows are now session-anchored
        # and verified to reproduce the validated broker-clock 12:30-14:45
        # window on this broker, in both DST seasons.
        "args": [PYTHON, "-u", "scripts/run_champion_paper.py", "--interval-seconds", "60", "--execute-demo"],
        "status": PROJECT_ROOT / "data" / "reports" / "champion_paper_status.json",
        "log": PROJECT_ROOT / "data" / "live_paper" / "champion_paper.log",
        "stale_seconds": 300,
        "data_stale_seconds": 1800,
    },
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


def mt5_running() -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq terminal64.exe"], capture_output=True, text=True
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


def start_engine(name: str, engine: dict, children: dict) -> None:
    log = open(engine["log"], "a", encoding="utf-8")
    children[name] = subprocess.Popen(
        engine["args"],
        cwd=str(PROJECT_ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.DETACHED_PROCESS,
    )


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
    champion = read_status(ENGINES["champion"])
    meanrev = read_status(ENGINES["meanrev"])
    lines = ["SENTINEL DAILY DIGEST"]
    lines.append(
        f"Champion: {champion.get('closed_trades', 0)} closed | net {champion.get('net_rr', 0)}R | "
        f"rwPF {champion.get('risk_weighted_pf', 0)} (target 1.18) | open {len(champion.get('open_positions', []))}"
    )
    open_mr = meanrev.get("open_positions", {})
    lines.append(
        f"MeanRev: {meanrev.get('closed_trades', 0)} closed | net {meanrev.get('net_rr', 0)}R | "
        f"rwPF {meanrev.get('risk_weighted_pf', 0)} (target 1.85) | open {len(open_mr)}"
    )
    for symbol, position in (open_mr or {}).items():
        lines.append(f"  MR open: {symbol} @ {position.get('entry')} day {position.get('held_days')}")
    lines.append("Both engines demo-only; kill switch: data/live_paper/KILL_SWITCH")
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
    result = subprocess.run(["tasklist", "/FI", f"PID eq {other_pid}"], capture_output=True, text=True)
    return "python" in (result.stdout or "").lower()


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    if another_supervisor_alive():
        print("Another supervisor is already running; exiting.", flush=True)
        return 0
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
    children: dict[str, subprocess.Popen] = {}
    notify_telegram("Sentinel SUPERVISOR online: watching MT5 + both engines; daily digest 07:00 WAT.")
    print("SENTINEL SUPERVISOR online", flush=True)
    while True:
        try:
            if not ensure_mt5():
                notify_telegram("SUPERVISOR ALERT: MT5 terminal is not running and could not be started.")
                time.sleep(600)
                continue
            for name, engine in ENGINES.items():
                age = status_age_seconds(engine)
                child = children.get(name)
                child_dead = child is None or child.poll() is not None
                # A live process with a dead MT5 feed is the silent failure
                # mode the heartbeat cannot see: force a restart on it.
                feed_age = data_age_seconds(engine)
                if feed_age > engine["data_stale_seconds"] and not child_dead:
                    notify_telegram(
                        f"SUPERVISOR: {name} process is alive but MT5 returned nothing for "
                        f"{int(feed_age / 60)} min - restarting it. Check the terminal is logged in."
                    )
                    print(f"{name}: MT5 feed dead {int(feed_age)}s - restarting", flush=True)
                    child.kill()
                    start_engine(name, engine, children)
                    continue
                if age > engine["stale_seconds"]:
                    if child_dead:
                        start_engine(name, engine, children)
                        notify_telegram(
                            f"SUPERVISOR: {name} engine heartbeat stale ({int(age)}s) - (re)started it."
                        )
                        print(f"(re)started {name}", flush=True)
                    # If a child is alive but stale, give it one more interval
                    # before killing: MT5 IPC hiccups usually self-recover.
                    elif age > engine["stale_seconds"] * 3:
                        child.kill()
                        start_engine(name, engine, children)
                        notify_telegram(f"SUPERVISOR: {name} engine hung ({int(age)}s) - killed and restarted.")
            maybe_send_digest(datetime.now(UTC))
        except Exception as exc:  # Supervisor must never die of its own bugs.
            print(f"supervisor error: {exc}", flush=True)
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
