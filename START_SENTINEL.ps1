param(
    [switch]$DryRun,
    [switch]$NoLiveMonitor
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeDir = Join-Path $ProjectRoot ".sentinel_runtime"
$PidFile = Join-Path $RuntimeDir "sentinel_processes.json"
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonExe = "python"
}

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null

function Start-SentinelProcess {
    param(
        [string]$Name,
        [string]$Command,
        [string]$Role
    )

    if ($DryRun) {
        return [ordered]@{
            name = $Name
            role = $Role
            pid = $null
            status = "DRY_RUN"
            command = $Command
        }
    }

    $process = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $Command) `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Normal `
        -PassThru

    Start-Sleep -Milliseconds 800
    $status = if ($process.HasExited) { "EXITED" } else { "RUNNING" }
    return [ordered]@{
        name = $Name
        role = $Role
        pid = $process.Id
        status = $status
        command = $Command
    }
}

$processes = @()
$processes += Start-SentinelProcess `
    -Name "Telegram Bot" `
    -Role "telegram" `
    -Command "& '$PythonExe' 'scripts\run_telegram_bot.py'"

$processes += Start-SentinelProcess `
    -Name "Dashboard" `
    -Role "dashboard" `
    -Command "& '$PythonExe' -m streamlit run 'dashboard\app.py' --server.port 8502"

if (-not $NoLiveMonitor -and (Test-Path -LiteralPath (Join-Path $ProjectRoot "scripts\run_sentinel_live.py"))) {
    $processes += Start-SentinelProcess `
        -Name "Live Monitor" `
        -Role "live_monitor" `
        -Command "& '$PythonExe' 'scripts\run_sentinel_live.py'"
} else {
    $processes += [ordered]@{
        name = "Live Monitor"
        role = "live_monitor"
        pid = $null
        status = "SKIPPED"
        command = ""
    }
}

$state = [ordered]@{
    started_at = (Get-Date).ToString("o")
    mode = "Advisor"
    execution = "Disabled"
    dashboard_url = "http://localhost:8502/"
    processes = $processes
}

$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $PidFile -Encoding UTF8

function Status-ForRole {
    param([string]$Role)
    $item = $processes | Where-Object { $_.role -eq $Role } | Select-Object -First 1
    if ($null -eq $item) { return "SKIPPED" }
    return $item.status
}

Write-Host ""
Write-Host "PROJECT SENTINEL STARTUP COMPLETE"
Write-Host ""
Write-Host ("Telegram Bot: {0}" -f (Status-ForRole "telegram"))
Write-Host ("Dashboard: {0}" -f (Status-ForRole "dashboard"))
Write-Host ("Live Monitor: {0}" -f (Status-ForRole "live_monitor"))
Write-Host "Mode: Advisor"
Write-Host "Execution: Disabled"
Write-Host ""
Write-Host "Dashboard URL:"
Write-Host "http://localhost:8502/"
Write-Host ""
Write-Host ("State file: {0}" -f $PidFile)
