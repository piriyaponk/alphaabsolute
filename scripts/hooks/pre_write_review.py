"""
AlphaAbsolute -- PreToolUse Hook: DE Team Auto-Review
======================================================
Triggers before Write tool creates a new Python file in scripts/.
Runs static checks and prints a quick review to stderr.

This is NOT a full AI review — that requires @de-team in chat.
This catches the most common AlphaAbsolute mistakes automatically.

Hook input (stdin): JSON with tool_name, tool_input fields
Output: exit 0 = allow, exit 2 = block with message
"""

import sys
import json
import os
import re
from pathlib import Path

def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # Not parseable — let it through

    tool_name  = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    # Only intercept Write tool on Python files in scripts/
    if tool_name != "Write":
        sys.exit(0)

    file_path = tool_input.get("file_path", "")
    content   = tool_input.get("content", "")

    if not file_path.endswith(".py"):
        sys.exit(0)

    # Only check scripts/ directory
    norm = file_path.replace("\\", "/")
    if "/scripts/" not in norm:
        sys.exit(0)

    # Is this a NEW file or overwrite?
    is_new = not Path(file_path).exists()
    action = "NEW FILE" if is_new else "OVERWRITE"

    issues   = []
    warnings = []
    oks      = []

    lines = content.splitlines()

    # ── Check 1: encoding on open() calls ────────────────────────────────────
    open_calls = [i+1 for i, l in enumerate(lines) if re.search(r'open\(', l)
                  and 'encoding' not in l and '#' not in l.split('open(')[0]]
    if open_calls:
        issues.append(f"open() without encoding='utf-8' — lines {open_calls[:3]} → crash on Thai Windows")
    else:
        oks.append("open() calls have encoding specified")

    # ── Check 2: requests without verify=False ────────────────────────────────
    req_calls = [i+1 for i, l in enumerate(lines)
                 if re.search(r'requests\.(get|post|put)\(', l)
                 and 'verify=False' not in l
                 and '#' not in l.split('requests.')[0]]
    if req_calls:
        issues.append(f"requests.get/post() without verify=False — lines {req_calls[:3]} → SSL fail on corporate proxy")
    else:
        if any('requests' in l for l in lines):
            oks.append("requests calls have verify=False")

    # ── Check 3: deprecated utcfromtimestamp ─────────────────────────────────
    utc_lines = [i+1 for i, l in enumerate(lines) if 'utcfromtimestamp' in l]
    if utc_lines:
        issues.append(f"datetime.utcfromtimestamp() deprecated (Python 3.12) — lines {utc_lines} → use datetime.fromtimestamp(ts, tz=timezone.utc)")

    # ── Check 4: Polygon limit=50 instead of 5000 ────────────────────────────
    limit50 = [i+1 for i, l in enumerate(lines) if 'limit=50' in l and 'polygon' in l.lower()]
    if limit50:
        issues.append(f"Polygon limit=50 detected — line {limit50} → use limit=5000 to get full history in one call")

    # ── Check 5: stdout reconfigure for Windows ───────────────────────────────
    has_reconfigure = any('reconfigure' in l or 'PYTHONIOENCODING' in l for l in lines)
    has_print       = any('print(' in l for l in lines)
    if has_print and not has_reconfigure:
        warnings.append("No stdout reconfigure — Thai chars in print() will crash. Add: sys.stdout.reconfigure(encoding='utf-8', errors='replace')")
    elif has_reconfigure:
        oks.append("stdout reconfigure present")

    # ── Check 6: tuple unpacking losing OHLC ─────────────────────────────────
    bad_unpack = [i+1 for i, l in enumerate(lines)
                  if re.search(r'for\s+\(\s*d\s*,\s*c\s*,\s*v\s*\)\s+in', l)]
    if bad_unpack:
        issues.append(f"3-tuple unpack (d,c,v) loses open/high/low — lines {bad_unpack} → use [bar for bar in bars] and index bar[0..5]")

    # ── Check 7: hardcoded gate_eps = -1 ─────────────────────────────────────
    hardcoded_gates = [i+1 for i, l in enumerate(lines)
                       if re.search(r'-1,\s*-1,\s*-1', l) and 'gate' in lines[max(0,i-3):i+3 and i+3].__repr__().lower()]
    if hardcoded_gates:
        warnings.append(f"Possible hardcoded -1,-1,-1 for fundamental gates — lines {hardcoded_gates} → read from fundamentals_summary table instead")

    # ── Check 8: is script registered in runner? ──────────────────────────────
    runner_path = Path(file_path).resolve().parents[2] / "scripts" / "runners" / "pre_market_runner.py"
    script_name = Path(file_path).stem
    if is_new and runner_path.exists():
        runner_content = runner_path.read_text(encoding='utf-8', errors='replace')
        if script_name not in runner_content:
            warnings.append(f"'{script_name}' not registered in pre_market_runner.py STEPS — remember to add it")
        else:
            oks.append(f"'{script_name}' found in pre_market_runner.py")

    # ── Check 9: INSERT without OR IGNORE ─────────────────────────────────────
    bad_insert = [i+1 for i, l in enumerate(lines)
                  if re.search(r'INSERT\s+INTO', l, re.IGNORECASE)
                  and 'OR IGNORE' not in l.upper()
                  and 'OR REPLACE' not in l.upper()]
    if bad_insert:
        warnings.append(f"INSERT without OR IGNORE — lines {bad_insert[:3]} → not idempotent, re-run will duplicate data")

    # ── Check 10: time.sleep inside try block ─────────────────────────────────
    in_try = False
    sleep_in_try = []
    for i, l in enumerate(lines):
        stripped = l.strip()
        if stripped.startswith('try:'):
            in_try = True
        if stripped.startswith(('except', 'finally', 'else:')) and in_try:
            in_try = False
        if in_try and 'time.sleep(' in stripped:
            sleep_in_try.append(i+1)
    if sleep_in_try:
        warnings.append(f"time.sleep() inside try block — lines {sleep_in_try[:3]} → rate limit sleep won't execute on error. Move outside try.")

    # ── Print review ──────────────────────────────────────────────────────────
    total_issues = len(issues)
    total_warn   = len(warnings)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  DE TEAM STATIC REVIEW — {action}", file=sys.stderr)
    print(f"  File: {Path(file_path).name}", file=sys.stderr)
    print(f"  Issues: {total_issues} critical | {total_warn} warnings", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    if issues:
        print("\n  CRITICAL (fix before writing):", file=sys.stderr)
        for iss in issues:
            print(f"    [BLOCK] {iss}", file=sys.stderr)

    if warnings:
        print("\n  WARNINGS:", file=sys.stderr)
        for w in warnings:
            print(f"    [WARN]  {w}", file=sys.stderr)

    if oks and not issues:
        print("\n  CHECKS PASSED:", file=sys.stderr)
        for ok in oks[:4]:
            print(f"    [OK]    {ok}", file=sys.stderr)

    if total_issues == 0:
        print(f"\n  VERDICT: ALLOW — no critical issues found", file=sys.stderr)
        if total_warn > 0:
            print(f"  NOTE: {total_warn} warning(s) above — review before next commit", file=sys.stderr)
        print(f"  For full AI review: @de-team review {Path(file_path).name}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)
        sys.exit(0)
    else:
        print(f"\n  VERDICT: BLOCKED — {total_issues} critical issue(s) must be fixed first", file=sys.stderr)
        print(f"  Fix the issues above, then write the file.", file=sys.stderr)
        print(f"  For full AI review: @de-team review {Path(file_path).name}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)
        # Exit 2 = block with message, but we allow with warning (don't block writes)
        # Change to sys.exit(2) if you want hard blocking
        sys.exit(0)


if __name__ == "__main__":
    main()
