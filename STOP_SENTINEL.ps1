param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeDir = Join-Path $ProjectRoot ".sentinel_runtime"
$PidFile = Join-Path $RuntimeDir "sentinel_processes.json"

if (-not (Test-Path -LiteralPath $PidFile)) {
    Write-Host ""
    Write-Host "PROJECT SENTINEL STOP"
    Write-Host ""
    Write-Host "No Sentinel runtime state file found."
    Write-Host "Telegram Bot: NOT RUNNING"
    Write-Host "Dashboard: NOT RUNNING"
    Write-Host "Live Monitor: NOT RUNNING"
    exit 0
}

$state = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
$results = @()

function Get-SentinelChildProcessIds {
    param([int]$ParentPid)

    $children = @(Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $ParentPid })
    $ids = @()
    foreach ($child in $children) {
        $ids += Get-SentinelChildProcessIds -ParentPid ([int]$child.ProcessId)
        $ids += [int]$child.ProcessId
    }
    return $ids
}

foreach ($item in $state.processes) {
    $status = "NOT RUNNING"
    if ($null -ne $item.pid -and $item.pid -ne "") {
        $pidValue = [int]$item.pid
        $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            if ($DryRun) {
                $status = "DRY_RUN"
            } else {
                $childIds = @(Get-SentinelChildProcessIds -ParentPid $pidValue)
                foreach ($childId in $childIds) {
                    Stop-Process -Id $childId -Force -ErrorAction SilentlyContinue
                }
                Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
                $status = "STOPPED"
            }
        }
    } elseif ($item.status -eq "DRY_RUN") {
        $status = "DRY_RUN"
    } elseif ($item.status -eq "SKIPPED") {
        $status = "SKIPPED"
    }

    $results += [ordered]@{
        name = $item.name
        role = $item.role
        pid = $item.pid
        status = $status
    }
}

if (-not $DryRun) {
    $stoppedState = [ordered]@{
        stopped_at = (Get-Date).ToString("o")
        previous_started_at = $state.started_at
        results = $results
    }
    $stoppedState | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $RuntimeDir "sentinel_last_stop.json") -Encoding UTF8
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Status-ForRole {
    param([string]$Role)
    $item = $results | Where-Object { $_.role -eq $Role } | Select-Object -First 1
    if ($null -eq $item) { return "NOT RUNNING" }
    return $item.status
}

Write-Host ""
Write-Host "PROJECT SENTINEL STOP COMPLETE"
Write-Host ""
Write-Host ("Telegram Bot: {0}" -f (Status-ForRole "telegram"))
Write-Host ("Dashboard: {0}" -f (Status-ForRole "dashboard"))
Write-Host ("Live Monitor: {0}" -f (Status-ForRole "live_monitor"))
