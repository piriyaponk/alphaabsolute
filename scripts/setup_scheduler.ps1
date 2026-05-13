# AlphaAbsolute — Setup Windows Task Scheduler
# Runs daily_report.py at 9:00 AM Thailand time (02:00 UTC)
# Run this script ONCE as Administrator:
#   Right-click → Run with PowerShell

$TaskName    = "AlphaAbsolute_DailyPulse"
$ScriptPath  = "C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute\scripts\daily_report.py"
$Python      = "C:\Users\Pizza\AppData\Local\Programs\Python\Python312\python.exe"
$LogPath     = "C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute\output\scheduler_log.txt"

# Thailand 9:00 AM = UTC 02:00
# Windows Task Scheduler uses LOCAL time — set to 9:00 AM and ensure system clock is TH time
$TriggerTime = "09:00"

Write-Host "Setting up AlphaAbsolute Daily Pulse scheduler..." -ForegroundColor Cyan

# Remove existing task if exists
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Create action: run python script
$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory "C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute"

# Trigger: daily at 9:00 AM
$Trigger = New-ScheduledTaskTrigger -Daily -At $TriggerTime

# Settings: run even on battery, wake if needed
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

# Register task
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "✅ Task '$TaskName' created — runs daily at $TriggerTime Thailand time" -ForegroundColor Green
Write-Host ""
Write-Host "To test immediately:" -ForegroundColor Yellow
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor White
Write-Host ""
Write-Host "To check status:" -ForegroundColor Yellow
Write-Host "  Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo" -ForegroundColor White
Write-Host ""
Write-Host "To remove:" -ForegroundColor Yellow
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false" -ForegroundColor White
