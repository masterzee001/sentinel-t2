@echo off
REM Stops the live book: supervisor first, then the trading engine.
REM
REM It refuses to stop while a position is open (nothing would watch the exit)
REM unless you pass -Force. MetaTrader 5 is left running; add -IncludeMT5 if
REM you also want to close the terminal, which is what actually cuts internet
REM usage.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0STOP_SENTINEL.ps1" %*
echo.
pause
