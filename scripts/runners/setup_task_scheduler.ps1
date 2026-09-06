# AlphaAbsolute v2 — Windows Task Scheduler Setup
# Run this script ONCE as Administrator to register the two daily automation tasks.
#
# Tasks created:
#   AlphaAbsolute_PreMarket  — 6:00 AM Mon-Fri  (premarket mode: regime + screen + setups + brief)
#   AlphaAbsolute_EOD        — 4:30 PM Mon-Fri  (eod mode: OHLCV update + metrics + postmortem)
#
# Usage:
#   Right-click PowerShell → Run as Administrator
#   cd C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute
#   .\scripts\runners\setup_task_scheduler.ps1
#
# To remove tasks:
#   Unregister-ScheduledTask -TaskName "AlphaAbsolute_PreMarket" -Confirm:$false
#   Unregister-ScheduledTask -TaskName "AlphaAbsolute_EOD" -Confirm:$false

$BASE_DIR   = "C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute"
$RUNNER     = "$BASE_DIR\scripts\runners\pre_market_runner.py"
$LOG_DIR    = "$BASE_DIR\data\runner_logs"
$PYTHON     = (Get-Command python).Source

# Ensure log directory exists
if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory -Force $LOG_DIR | Out-Null }

Write-Host ""
Write-Host "AlphaAbsolute v2 — Task Scheduler Setup" -ForegroundColor Cyan
Write-Host "========================================"
Write-Host "Python:  $PYTHON"
Write-Host "Runner:  $RUNNER"
Write-Host "Log dir: $LOG_DIR"
Write-Host ""

# ── Task 1: Pre-Market (6:00 AM) ──────────────────────────────────────────────

$premarket_action = New-ScheduledTaskAction `
    -Execute $PYTHON `
    -Argument "`"$RUNNER`" --mode premarket" `
    -WorkingDirectory $BASE_DIR

$premarket_trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At "06:00AM"

$premarket_settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

$premarket_principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType S4U `
    -RunLevel Highest

try {
    $existing = Get-ScheduledTask -TaskName "AlphaAbsolute_PreMarket" -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName "AlphaAbsolute_PreMarket" -Confirm:$false
        Write-Host "[UPDATE] Removed existing AlphaAbsolute_PreMarket task" -ForegroundColor Yellow
    }

    Register-ScheduledTask `
        -TaskName "AlphaAbsolute_PreMarket" `
        -Description "AlphaAbsolute v2: Daily pre-market pipeline (A01-A11) at 6:00 AM" `
        -Action $premarket_action `
        -Trigger $premarket_trigger `
        -Settings $premarket_settings `
        -Principal $premarket_principal `
        -Force | Out-Null

    Write-Host "[OK] AlphaAbsolute_PreMarket registered (Mon-Fri 6:00 AM)" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Pre-market task: $_" -ForegroundColor Red
}

# ── Task 2: End-of-Day (4:30 PM) ─────────────────────────────────────────────

$eod_action = New-ScheduledTaskAction `
    -Execute $PYTHON `
    -Argument "`"$RUNNER`" --mode eod" `
    -WorkingDirectory $BASE_DIR

$eod_trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At "04:30PM"

$eod_settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -MultipleInstances IgnoreNew

$eod_principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType S4U `
    -RunLevel Highest

try {
    $existing = Get-ScheduledTask -TaskName "AlphaAbsolute_EOD" -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName "AlphaAbsolute_EOD" -Confirm:$false
        Write-Host "[UPDATE] Removed existing AlphaAbsolute_EOD task" -ForegroundColor Yellow
    }

    Register-ScheduledTask `
        -TaskName "AlphaAbsolute_EOD" `
        -Description "AlphaAbsolute v2: Daily EOD pipeline (OHLCV update + metrics + postmortem) at 4:30 PM" `
        -Action $eod_action `
        -Trigger $eod_trigger `
        -Settings $eod_settings `
        -Principal $eod_principal `
        -Force | Out-Null

    Write-Host "[OK] AlphaAbsolute_EOD registered (Mon-Fri 4:30 PM)" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] EOD task: $_" -ForegroundColor Red
}

# ── Task 3: Monthly (1st of each month, 7:00 AM) ─────────────────────────────

$monthly_action = New-ScheduledTaskAction `
    -Execute $PYTHON `
    -Argument "`"$RUNNER`" --mode monthly" `
    -WorkingDirectory $BASE_DIR

# Monthly trigger: 1st of every month at 7:00 AM
$monthly_trigger = New-ScheduledTaskTrigger -Monthly -DaysOfMonth 1 -At "07:00AM"

$monthly_settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

try {
    $existing = Get-ScheduledTask -TaskName "AlphaAbsolute_Monthly" -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName "AlphaAbsolute_Monthly" -Confirm:$false
        Write-Host "[UPDATE] Removed existing AlphaAbsolute_Monthly task" -ForegroundColor Yellow
    }

    Register-ScheduledTask `
        -TaskName "AlphaAbsolute_Monthly" `
        -Description "AlphaAbsolute v2: Monthly Bayesian calibration + performance report (1st of month)" `
        -Action $monthly_action `
        -Trigger $monthly_trigger `
        -Settings $monthly_settings `
        -Principal $eod_principal `
        -Force | Out-Null

    Write-Host "[OK] AlphaAbsolute_Monthly registered (1st of month 7:00 AM)" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Monthly task: $_" -ForegroundColor Red
}

# ── Summary ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "Registered tasks:" -ForegroundColor Cyan
Get-ScheduledTask | Where-Object { $_.TaskName -like "AlphaAbsolute*" } | ForEach-Object {
    $info = $_ | Get-ScheduledTaskInfo
    Write-Host "  $($_.TaskName)" -NoNewline
    Write-Host "  Last: $($info.LastRunTime)" -NoNewline
    Write-Host "  Next: $($info.NextRunTime)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "To test immediately (pre-market):" -ForegroundColor Yellow
Write-Host "  Start-ScheduledTask -TaskName 'AlphaAbsolute_PreMarket'"
Write-Host ""
Write-Host "To view logs:"
Write-Host "  Get-ScheduledTaskInfo -TaskName 'AlphaAbsolute_PreMarket'"
Write-Host "  dir $LOG_DIR"
Write-Host ""
Write-Host "IMPORTANT: Tasks run as current user. Computer must be on (not sleep) at trigger times." -ForegroundColor Yellow
Write-Host "For always-on execution: enable 'Run whether user is logged in or not' in Task Scheduler GUI." -ForegroundColor Yellow
