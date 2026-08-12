"""Plain-language health check for the live book. Safe to run any time.

Read-only: it inspects processes and status files and never touches the
engine, the terminal or the account. Written for a quick glance rather than
for debugging — anything genuinely wrong should already have reached Telegram.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATUS = PROJECT_ROOT / "data" / "reports" / "meanrev_live_status.json"
OK, BAD, WARN = "[ OK ]", "[FAIL]", "[WARN]"


def processes() -> str:
    """Return the running python command lines (Windows)."""
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name like 'python%'", "get", "commandline"],
            capture_output=True, text=True, timeout=30,
        ).stdout
        if out.strip():
            return out
    except Exception:  # noqa: BLE001
        pass
    try:  # wmic is deprecated on newer Windows; fall back to PowerShell
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
             "Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, timeout=60,
        ).stdout
    except Exception:  # noqa: BLE001
        return ""


def terminal_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe"],
                             capture_output=True, text=True, timeout=30).stdout
        return "terminal64.exe" in out
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    print("=" * 62)
    print("  SENTINEL HEALTH CHECK    " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("=" * 62)
    problems: list[str] = []

    cmds = processes()
    supervisor = "run_sentinel_supervisor" in cmds
    engine = "run_mean_reversion_live" in cmds
    term = terminal_running()

    print(f"\n{OK if supervisor else BAD}  Supervisor (restarts things if they die)")
    print(f"{OK if engine else WARN}  Mean-reversion engine")
    print(f"{OK if term else BAD}  MetaTrader 5 terminal")
    if not supervisor:
        problems.append("The supervisor is NOT running. Nothing will restart the engine if it stops.\n"
                        "     Fix: double-click START_SENTINEL.bat (or ask Claude to restart it).")
    if not engine and supervisor:
        print("       (the supervisor restarts the engine within ~20 min, so this may be temporary)")
    if not term:
        problems.append("MetaTrader 5 is not running. The engine cannot see prices or place orders.\n"
                        "     The supervisor normally relaunches it within a couple of minutes.")

    if not STATUS.exists():
        problems.append("No status file yet — the engine has not completed a cycle.")
    else:
        data = json.loads(STATUS.read_text(encoding="utf-8"))
        health = data.get("mt5_health") or {}
        last = health.get("last_success_utc")
        age_min = None
        if last:
            try:
                stamp = datetime.fromisoformat(str(last))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=UTC)
                age_min = (datetime.now(UTC) - stamp).total_seconds() / 60
            except ValueError:
                pass
        file_age_min = (time.time() - STATUS.stat().st_mtime) / 60

        print("\n--- data feed ---")
        if age_min is None:
            print(f"{WARN}  Engine has not reported a successful price read yet")
        elif age_min < 30:
            print(f"{OK}  Last saw live prices {int(age_min)} min ago")
        else:
            print(f"{BAD}  No live prices for {int(age_min)} min")
            problems.append("The engine is not receiving prices. Check MT5 is logged in.")
        if file_age_min > 30:
            print(f"{WARN}  Status file is {int(file_age_min)} min old (engine may be restarting)")

        print("\n--- the book ---")
        openpos = data.get("open_positions") or {}
        print(f"       Closed trades: {data.get('closed_trades', 0)}   "
              f"Open now: {len(openpos)}   Net: {data.get('net_rr', 0)}R")
        for sym, p in openpos.items():
            print(f"         holding {sym} from {p.get('entry')} (day {p.get('held_days', 0)})")
        orders = data.get("demo_orders") or {}
        refused = orders.get("refused") or {}
        print(f"       Real orders sent: {orders.get('submitted', 0)}")
        if refused:
            # A refusal from a since-fixed condition must not alarm forever.
            ref_age_h = None
            last_ref = orders.get("last_refusal_utc")
            if last_ref:
                try:
                    stamp2 = datetime.fromisoformat(str(last_ref))
                    if stamp2.tzinfo is None:
                        stamp2 = stamp2.replace(tzinfo=UTC)
                    ref_age_h = (datetime.now(UTC) - stamp2).total_seconds() / 3600
                except ValueError:
                    pass
            recent = ref_age_h is not None and ref_age_h < 24
            when = f"most recent {int(ref_age_h)}h ago" if ref_age_h is not None else "before refusal timestamps existed"
            print(f"{WARN if recent else '      '}  Orders refused ({when}):")
            for reason, n in refused.items():
                print(f"         {n}x  {reason}")
            if recent and any("10027" in r for r in refused):
                problems.append("Orders were refused because AutoTrading was off in MT5.\n"
                                "     Fix: click the 'Algo Trading' button so it is green (Ctrl+E).")
            elif not recent:
                print("       (historical - not a live fault; a new refusal would show as recent)")

        kill = PROJECT_ROOT / "data" / "live_paper" / "KILL_SWITCH"
        if kill.exists():
            print(f"\n{WARN}  KILL SWITCH IS ON — no new trades will be opened.")
            print(f"       Delete this file to resume: {kill}")

    print("\n" + "=" * 62)
    if problems:
        print("  NEEDS ATTENTION:\n")
        for p in problems:
            print(f"   *  {p}")
    else:
        print("  ALL GOOD - the system is running and watching the market.")
        print("  You do not need to do anything.")
    print("=" * 62)
    print("\nReminders: the daily summary arrives on Telegram at 07:00.")
    print("Trades happen near 22:30 (DE40) and 23:30 (US) broker time.")
    print("To stop everything immediately, create this empty file:")
    print(f"  {PROJECT_ROOT / 'data' / 'live_paper' / 'KILL_SWITCH'}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
