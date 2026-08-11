# Deploying Sentinel T2 to a Windows VPS

Decision (2026-08-11): **Windows VPS**, not Linux. MT5 runs on Linux under
Wine, but every Linux path adds a translation layer (Wine, virtual display,
or an RPC hop) to the MT5 layer — historically the least observable part of
this system — to save roughly $15/month. See the note at the bottom if that
trade ever changes.

Budget ~$25/month: 2 vCPU, 4 GB RAM, NVMe, x86_64, from a provider with a
real SLA and a network path near the broker. Avoid the cheapest tiers —
oversubscribed CPU shows up as slippage and requotes, not clean errors.

---

## 1. Provision

- Windows Server 2022, x86_64, 4 GB+ RAM.
- Set the system clock to **UTC** and enable time sync.
- Windows Update: fixed maintenance window, **automatic restart disabled**.
  An unscheduled reboot mid-session suspends position management until the
  boot task brings the supervisor back.

## 2. Install the runtime

```powershell
# Python must match the desktop exactly: 3.14.6 (64-bit)
git clone https://github.com/masterzee001/sentinel-t2.git C:\sentinel-t2
cd C:\sentinel-t2
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run the suite **before wiring any credentials**. Expect ~643 passing tests.
Treat any deviation as a blocker, not a rounding error:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\ -p no:cacheprovider -q
```

## 3. Install MetaTrader 5

Install **build 5.0.6090** — it must match the pinned `metatrader5` package
or the IPC handshake fails. Install in `/portable` mode so an in-place
self-update is harder to land. Log into the broker demo account and confirm
every traded symbol is visible in Market Watch: `US30`, `NAS100` (may trade
under a broker alias — the connector resolves aliases automatically),
`US500`, `DE40`.

**Verify indices are actually tradable on this broker** (MetaQuotes-Demo is
quote-only and refuses every index order with retcode 10017):

```powershell
.\.venv\Scripts\python.exe -c "import MetaTrader5 as m; m.initialize(); print({s: m.symbol_info(s).trade_mode for s in ('US30','US500','DE40')})"
```

`4` means full trading. `0` means DISABLED — stop, the broker is unusable
for this system.

## 4. Credentials

Copy `.env` from the desktop (it is gitignored and must never be committed)
and update `MT5_TERMINAL_PATH` to the VPS install path. `MT5_SERVER` must
still contain the word `demo` — `DemoOrderExecutor` refuses to trade any
account whose server name fails that check, and that interlock is the last
line of defence.

## 5. Cut over — with both engines STOPPED and FLAT

State does not travel with a clone, and that is deliberate. Migrate it
consciously:

1. Stop the desktop supervisor; confirm no open positions.
2. Copy `.sentinel_runtime/champion_risk_state.json` and
   `meanrev_risk_state.json` (gitignored) — these carry the drawdown
   baseline and daily counters. Starting fresh resets the high-water mark.
3. Copy `data/live_paper/*_state.json` if you want the paper parity books to
   continue rather than restart.
4. Delete any stale `data/live_paper/supervisor.lock` before first start.

## 6. Boot persistence

Task Scheduler → Create Task:
- Trigger: **At startup**
- Run whether user is logged on or not, highest privileges
- Action: `C:\sentinel-t2\.venv\Scripts\pythonw.exe`
  arguments `-u scripts\run_sentinel_supervisor.py`, **Start in**
  `C:\sentinel-t2`
- Settings: restart on failure, do not stop on idle/battery

The supervisor is the single owner of the engines. **Never start
`run_champion_paper.py` or `run_mean_reversion_live.py` by hand** — the
singleton lock guards the supervisor, not the engines.

## 7. Soak before trusting it

Run at least **two full trading weeks** on demo with the desktop powered
off, and watch for:

- the 07:00 WAT digest arriving daily,
- engine restart alerts firing when expected,
- `mt5_health.last_success_utc` staying fresh in both status files,
- the `demo_orders.refused` reason map staying empty — a climbing refusal
  count with a healthy paper book means signals are not reaching the account.

Only then consider real capital, and treat that as its own review: switching
off the demo gate removes the interlock that currently makes every other bug
survivable.

---

## Operating notes

- **Dashboard/monitoring**: day-to-day monitoring is Telegram. Do not expose
  any local service to `0.0.0.0`; use an RDP or SSH tunnel.
- **MT5 auto-update** cannot be officially disabled. If the terminal build
  drifts from the pinned package, IPC breaks — `/portable` mode plus a
  build check at startup is the mitigation.
- **Rollback**: keep the desktop as a cold standby for a month (same repo,
  same `.env`, engines stopped). Rollback is then "start the supervisor on
  the desktop", not a rebuild.

## If Linux is ever revisited

The MT5 side is solvable with Wine, but the ops layer needs porting too:
`scripts/run_sentinel_supervisor.py` uses `tasklist` (2 sites),
`subprocess.DETACHED_PROCESS` (2 sites, POSIX raises `AttributeError`), and
a hardcoded `.venv/Scripts/python.exe`; boot persistence would move from the
Startup-folder VBS to systemd units. All are small changes — the reason to
prefer Windows is reliability of the MT5 layer, not the size of the port.
