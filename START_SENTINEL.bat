@echo off
REM Starts the Sentinel supervisor, which then starts MetaTrader 5 and the
REM trading engine. Safe to run twice: a singleton lock means a second
REM supervisor exits immediately rather than competing with the first.
REM
REM You normally do NOT need this - the supervisor starts automatically when
REM you log in to Windows. Use it only if CHECK_SENTINEL says the supervisor
REM is not running.
REM
REM No output redirection here on purpose: the supervisor writes its own
REM timestamped data\live_paper\supervisor.log, so it logs identically however
REM it was launched.
cd /d "%~dp0"
del /q "data\live_paper\supervisor.lock" 2>nul
start "" /min ".venv\Scripts\pythonw.exe" -u scripts\run_sentinel_supervisor.py
echo Supervisor starting. Give it about a minute, then run CHECK_SENTINEL.bat
echo to confirm it is up.
echo.
pause
