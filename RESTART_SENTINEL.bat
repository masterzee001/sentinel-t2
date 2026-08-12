@echo off
REM Restarts the live book so it picks up the current code on disk.
REM
REM The engine keeps its open positions, day keys and order counters in
REM data\live_paper\meanrev_state.json and reloads them on start, so a restart
REM does not lose track of anything it is holding - that is why this passes
REM -Force where a plain stop would refuse.
REM
REM The status file is removed on purpose. The supervisor only (re)starts an
REM engine once its heartbeat is older than 20 minutes, and a file left behind
REM by the engine we just stopped looks fresh - so without this the book would
REM sit dead for up to 20 minutes. A missing file reads as infinitely stale and
REM starts it on the supervisor's first pass. Nothing is lost: the status file
REM is rewritten from meanrev_state.json every cycle.
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0STOP_SENTINEL.ps1" -Force
if exist "data\reports\meanrev_live_status.json" del /q "data\reports\meanrev_live_status.json"
del /q "data\live_paper\supervisor.lock" 2>nul

echo Starting the supervisor again...
start "" /min ".venv\Scripts\pythonw.exe" -u scripts\run_sentinel_supervisor.py
echo.
echo Give it about two minutes, then run CHECK_SENTINEL.bat to confirm the
echo supervisor, the engine and MetaTrader 5 are all up.
echo.
pause
