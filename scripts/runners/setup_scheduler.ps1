# AlphaAbsolute v2 — Windows Task Scheduler Setup
# =================================================
# Creates two scheduled tasks:
#   1. alphaabsolute-premarket  : runs at 6:00 AM daily (Mon-Fri)
#   2. alphaabsolute-eod        : runs at 4:30 PM daily (Mon-Fri)
#
# Usage (run once as Administrator):
#   cd C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute
#   powershell -ExecutionPolicy Bypass -File scripts\runners\setup_scheduler.ps1
#
# To verify tasks were created:
#   Get-ScheduledTask -TaskPath "\AlphaAbsolute\" | Select-Object TaskName, State
#
# To remove tasks:
#   Unregister-ScheduledTask -TaskName "alphaabsolute-premarket" -Confirm:$false
#   Unregister-ScheduledTask -TaskName "alphaabsolute-eod" -Confirm:$false

$Python    = "C:\Users\Pizza\AppData\Local\Programs\Python\Python312\python.exe"
$ScriptDir = "C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute"
$Runner    = "scripts\runners\pre_market_runner.py"
$LogDir    = "$ScriptDir\data\runner_logs"

# Ensure log directory exists
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Write-Host "=== AlphaAbsolute Task Scheduler Setup ===" -ForegroundColor Cyan
Write-Host ""

# ── Helper: register one task ─────────────────────────────────────────────────
function Register-AlphaTask {
    param(
        [string]$TaskName,
        [string]$Mode,
        [string]$TimeHHMM,
        [string]$Description
    )

    $logFile  = "$LogDir\scheduler_${Mode}.log"
    $args     = "-u `"$ScriptDir\$Runner`" --mode $Mode"
    $action   = New-ScheduledTaskAction `
                    -Execute $Python `
                    -Argument $args `
                    -WorkingDirectory $ScriptDir

    # Mon-Fri only
    $trigger  = New-ScheduledTaskTrigger `
                    -Weekly `
                    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
                    -At $TimeHHMM

    $settings = New-ScheduledTaskSettingsSet `
                    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
                    -RestartCount 1 `
                    -RestartInterval (New-TimeSpan -Minutes 5) `
                    -StartWhenAvailable `
                    -WakeToRun $false `
                    -MultipleInstances IgnoreNew

    # Run as current user (no password needed for interactive session)
    $principal = New-ScheduledTaskPrincipal `
                    -UserId $env:USERNAME `
                    -LogonType Interactive `
                    -RunLevel Limited

    # Remove existing task if present
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

    Register-ScheduledTask `
        -TaskName    $TaskName `
        -TaskPath    "\AlphaAbsolute\" `
        -Action      $action `
        -Trigger     $trigger `
        -Settings    $settings `
        -Principal   $principal `
        -Description $Description `
        -Force | Out-Null

    Write-Host "  [OK] $TaskName" -ForegroundColor Green
    Write-Host "       $Description" -ForegroundColor Gray
    Write-Host "       Time: $TimeHHMM (Mon-Fri)" -ForegroundColor Gray
    Write-Host ""
}

# ── Pre-market: 6:00 AM Mon-Fri ───────────────────────────────────────────────
Register-AlphaTask `
    -TaskName    "alphaabsolute-premarket" `
    -Mode        "premarket" `
    -TimeHHMM    "6:00AM" `
    -Description "AlphaAbsolute: Earnings gate + regime + RS rank + screen + report + Telegram (6AM)"

# ── EOD: 4:30 PM Mon-Fri ──────────────────────────────────────────────────────
Register-AlphaTask `
    -TaskName    "alphaabsolute-eod" `
    -Mode        "eod" `
    -TimeHHMM    "4:30PM" `
    -Description "AlphaAbsolute: OHLCV update + pipeline metrics + screening history + post-mortem (4:30PM)"

# ── Monthly: 1st of month at 7:00 AM ─────────────────────────────────────────
# (only fires if 1st falls on a weekday — checked inside runner anyway)
Register-AlphaTask `
    -TaskName    "alphaabsolute-monthly" `
    -Mode        "monthly" `
    -TimeHHMM    "7:00AM" `
    -Description "AlphaAbsolute: Bayesian calibration + monthly performance report (1st of month)"

# ── Verify ────────────────────────────────────────────────────────────────────
Write-Host "=== Registered Tasks ===" -ForegroundColor Cyan
Get-ScheduledTask -TaskPath "\AlphaAbsolute\" -ErrorAction SilentlyContinue |
    Select-Object TaskName, State |
    Format-Table -AutoSize

Write-Host "Setup complete. Tasks will run Mon-Fri automatically." -ForegroundColor Green
Write-Host ""
Write-Host "To test immediately (runs in background):" -ForegroundColor Yellow
Write-Host "  Start-ScheduledTask -TaskName 'alphaabsolute-eod' -TaskPath '\AlphaAbsolute\'" -ForegroundColor Yellow
