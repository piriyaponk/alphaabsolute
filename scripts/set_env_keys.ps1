# AlphaAbsolute — Set API Keys in Windows Environment Variables
# Run once after getting your keys:
#   Right-click > "Run with PowerShell" — OR — paste into PowerShell terminal

# ─── FRED API KEY ─────────────────────────────────────────────────────────────
# Get free key at: https://fred.stlouisfed.org/docs/api/api_key.html
# Steps: Click "Request API Key" → Create account → Copy key → paste below

$FRED_KEY = "f7e80f207e1b111726598b20aca81c3d"

# ─── FINNHUB API KEY ──────────────────────────────────────────────────────────
# Get free key at: https://finnhub.io → Register → Dashboard → API Keys
# Free tier: 60 calls/min — EPS surprises, analyst recs, company news (US stocks)
# Note: Thai stocks (.BK) and price targets require paid plan

$FINNHUB_KEY = "d7vg531r01qldb7fsfh0d7vg531r01qldb7fsfhg"

if ($FINNHUB_KEY -ne "PASTE_YOUR_FINNHUB_KEY_HERE") {
    [System.Environment]::SetEnvironmentVariable("FINNHUB_API_KEY", $FINNHUB_KEY, "User")
    Write-Host "FINNHUB_API_KEY set successfully." -ForegroundColor Green
}

# ─── ANTHROPIC API KEY ────────────────────────────────────────────────────────
# Get at: https://console.anthropic.com → API Keys
# (Optional if using Claude Code desktop — only needed for automated scripts)

$ANTHROPIC_KEY = "PASTE_YOUR_ANTHROPIC_KEY_HERE"

# ─── Set to User environment (permanent, no admin needed) ─────────────────────

if ($FRED_KEY -ne "PASTE_YOUR_FRED_KEY_HERE") {
    [System.Environment]::SetEnvironmentVariable("FRED_API_KEY", $FRED_KEY, "User")
    Write-Host "FRED_API_KEY set successfully." -ForegroundColor Green
} else {
    Write-Host "FRED_API_KEY: paste your key into this script first." -ForegroundColor Yellow
}

if ($ANTHROPIC_KEY -ne "PASTE_YOUR_ANTHROPIC_KEY_HERE") {
    [System.Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", $ANTHROPIC_KEY, "User")
    Write-Host "ANTHROPIC_API_KEY set successfully." -ForegroundColor Green
} else {
    Write-Host "ANTHROPIC_API_KEY: skipped (not required for Claude Code desktop)." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Done! Restart any open terminals or Claude Code for keys to take effect." -ForegroundColor Cyan
Write-Host "Then run: python scripts/fetch_macro.py" -ForegroundColor Cyan
