# install_hooks.ps1 — Install git pre-push hook for AlphaAbsolute
# Run once: powershell -ExecutionPolicy Bypass -File scripts/runners/install_hooks.ps1

$repo  = "C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute"
$hooks = "$repo\.git\hooks"

$hook = @'
#!/bin/sh
# pre-push hook — runs audit_gate.py before every push
# Installed by install_hooks.ps1

REPO_ROOT="$(git rev-parse --show-toplevel)"
echo ""
echo "================================================================"
echo "  AlphaAbsolute Pre-Push Audit Gate"
echo "================================================================"

python "$REPO_ROOT/scripts/paper_trading/audit_gate.py"
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "  PUSH BLOCKED — fix audit failures above, then push again."
    echo "  To skip (NOT recommended): git push --no-verify"
    echo "================================================================"
    exit 1
fi

echo "================================================================"
exit 0
'@

$hook | Set-Content -Path "$hooks\pre-push" -Encoding UTF8 -NoNewline

# Git hooks must be executable (on Windows this is handled by git itself for sh scripts)
Write-Host "[OK] Pre-push hook installed at $hooks\pre-push"
Write-Host "[INFO] Every 'git push' will now run audit_gate.py first."
Write-Host "[INFO] To bypass (emergency only): git push --no-verify"
