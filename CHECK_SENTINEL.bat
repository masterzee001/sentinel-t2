@echo off
REM Double-click this any time to see whether Sentinel is running and healthy.
REM Read-only: it never starts, stops or changes anything.
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\sentinel_status.py
echo.
pause
