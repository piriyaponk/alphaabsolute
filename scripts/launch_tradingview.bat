@echo off
REM AlphaAbsolute — TradingView Desktop Launcher
REM Run this BEFORE opening Claude Code each morning

echo Starting TradingView Desktop with debug port 9222...

SET TV_EXE=C:\Program Files\WindowsApps\TradingView.Desktop_3.1.0.7818_x64__n534cwy3pjxzj\TradingView.exe

IF NOT EXIST "%TV_EXE%" (
  echo ERROR: TradingView not found at expected path.
  echo Run this in PowerShell to find it:
  echo   (Get-AppxPackage -Name "TradingView.Desktop").InstallLocation
  pause
  exit /b 1
)

start "" "%TV_EXE%" --remote-debugging-port=9222
echo TradingView launched with debug port 9222.
echo MCP will auto-connect when Claude Code starts.
