# sync_on_login.ps1 — auto git pull AlphaAbsolute on Windows login
$repo = "C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute"
$log  = "$repo\data\paper_trading\sync_log.txt"

$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
try {
    $result = & git -C $repo pull 2>&1
    Add-Content $log "[$ts] $result"
} catch {
    Add-Content $log "[$ts] ERROR: $_"
}
