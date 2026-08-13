param(
    [switch]$IncludeMT5,
    [switch]$Force,
    [switch]$DryRun
)

# Stops the live book: the supervisor first, then the trading engine, and
# optionally the MetaTrader 5 terminal.
#
# The previous version of this script targeted the Telegram bot / dashboard /
# live-monitor stack via .sentinel_runtime\sentinel_processes.json. That stack
# is retired and the file is never written any more, so the script printed
# "NOT RUNNING" three times and stopped nothing while the book kept trading.
#
# ORDER MATTERS. The supervisor restarts the engine when its heartbeat goes
# stale and relaunches MT5 whenever it finds the terminal missing
# (run_sentinel_supervisor.py: ensure_mt5). Kill the engine or the terminal
# first and the supervisor simply puts them back.

$ErrorActionPreference = "Stop"
# Lives in scripts/, so the project root is one level up. The operator
# clicks STOP_SENTINEL.bat in the root; this is the implementation.
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LockPath = Join-Path $ProjectRoot "data\live_paper\supervisor.lock"
$StatusPath = Join-Path $ProjectRoot "data\reports\meanrev_live_status.json"

function Find-Sentinel {
    param([string]$Marker)
    $all = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'")
    return @($all | Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Marker*" })
}

function Stop-Group {
    param([string]$Label, [array]$Procs)
    if ($Procs.Count -eq 0) {
        Write-Host ("  {0}: not running" -f $Label)
        return 0
    }
    $ids = ($Procs | ForEach-Object { $_.ProcessId }) -join ", "
    if ($DryRun) {
        Write-Host ("  {0}: WOULD STOP (pid {1})" -f $Label, $ids)
        return 0
    }
    foreach ($p in $Procs) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Write-Host ("  {0}: STOPPED (pid {1})" -f $Label, $ids)
    return $Procs.Count
}

Write-Host ""
Write-Host "PROJECT SENTINEL STOP"
if ($DryRun) { Write-Host "(dry run - nothing will actually be stopped)" }
Write-Host ""

# --- refuse to walk away from an open position without being told twice -----
# There is no server-side stop loss (run_mean_reversion_live.py: the executor
# is built with server_stop_loss=False) and exits are only evaluated inside
# the nightly window. Stopping while holding means nothing checks the exit
# condition until the engine is back - and if it is still down at the window,
# the position rides an extra day unmanaged.
$openCount = 0
$openNames = ""
if (Test-Path -LiteralPath $StatusPath) {
    try {
        $status = Get-Content -LiteralPath $StatusPath -Raw | ConvertFrom-Json
        if ($null -ne $status.open_positions) {
            $props = @($status.open_positions.PSObject.Properties)
            $openCount = $props.Count
            $openNames = ($props | ForEach-Object { $_.Name }) -join ", "
        }
    } catch {
        Write-Host "  (could not read the status file; assuming no open positions)"
    }
}

if ($openCount -gt 0 -and -not $Force -and -not $DryRun) {
    Write-Host "REFUSING TO STOP - the book is holding $openCount position(s): $openNames"
    Write-Host ""
    Write-Host "  There is no stop loss sitting on the broker, and the engine only"
    Write-Host "  checks its exit rule during the nightly window. If it is still"
    Write-Host "  stopped at that window, the position is held another day with"
    Write-Host "  nothing watching it."
    Write-Host ""
    Write-Host "  If you want to stop anyway:   .\STOP_SENTINEL.bat -Force"
    Write-Host "  To halt NEW entries but keep exits running, use the kill switch"
    Write-Host "  instead - create this empty file:"
    Write-Host ("    {0}" -f (Join-Path $ProjectRoot "data\live_paper\KILL_SWITCH"))
    Write-Host ""
    exit 2
}

if ($openCount -gt 0) {
    Write-Host "WARNING: stopping while holding $openCount position(s): $openNames"
    Write-Host ""
}

# --- stop, in dependency order ---------------------------------------------
$stopped = 0
$stopped += Stop-Group -Label "Supervisor" -Procs (Find-Sentinel "run_sentinel_supervisor")
$stopped += Stop-Group -Label "Mean-reversion engine" -Procs (Find-Sentinel "run_mean_reversion_live")

if ($IncludeMT5) {
    $mt5 = @(Get-Process -Name terminal64 -ErrorAction SilentlyContinue)
    if ($mt5.Count -eq 0) {
        Write-Host "  MetaTrader 5: not running"
    } elseif ($DryRun) {
        Write-Host ("  MetaTrader 5: WOULD STOP (pid {0})" -f (($mt5 | ForEach-Object { $_.Id }) -join ", "))
    } else {
        foreach ($t in $mt5) { Stop-Process -Id $t.Id -Force -ErrorAction SilentlyContinue }
        Write-Host ("  MetaTrader 5: STOPPED (pid {0})" -f (($mt5 | ForEach-Object { $_.Id }) -join ", "))
        $stopped += $mt5.Count
    }
} else {
    Write-Host "  MetaTrader 5: LEFT RUNNING (pass -IncludeMT5 to close it too)"
}

# The lock stores the supervisor's pid and blocks a second supervisor from
# starting. Leaving it behind is a small landmine: if Windows later hands that
# pid to another python process, the next start would decide a supervisor is
# already alive and quietly exit.
if (-not $DryRun -and (Test-Path -LiteralPath $LockPath)) {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    Write-Host "  Supervisor lock cleared"
}

Write-Host ""
if ($DryRun) {
    Write-Host "Dry run complete - nothing was stopped."
} elseif ($stopped -eq 0) {
    Write-Host "Nothing was running. Sentinel is already stopped."
} else {
    Write-Host "SENTINEL STOPPED. No entries or exits will be evaluated until you start it again."
}

Write-Host ""
Write-Host "To start it again:   double-click START_SENTINEL.bat"
Write-Host "To check the state:  double-click CHECK_SENTINEL.bat"
if (-not $IncludeMT5) {
    Write-Host ""
    Write-Host "Note on data usage: the engine barely uses any - outside its nightly"
    Write-Host "window it only reads the account every 5 minutes. The MT5 terminal is"
    Write-Host "what streams prices continuously, so -IncludeMT5 is the flag that"
    Write-Host "actually reduces internet usage."
}
Write-Host ""
Write-Host "The supervisor also starts automatically when you next log in to Windows."
Write-Host ""
