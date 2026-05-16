"""
AlphaAbsolute — Post-Runner Log Hook
Fires after Bash tool use when weekly_runner.py or daily_runner.py is detected.
Auto-logs run result to ops_log.
Usage: called by PostToolUse hook in settings.json
"""
import os, sys, json
from pathlib import Path
from datetime import date, datetime

ROOT = Path(__file__).resolve().parents[2]

def post_runner_log():
    """Check if a runner was just executed and log it."""
    # This hook reads the CLAUDE_TOOL_OUTPUT env var if set by Claude Code
    tool_input  = os.environ.get("CLAUDE_TOOL_INPUT", "")
    tool_output = os.environ.get("CLAUDE_TOOL_OUTPUT", "")

    # Only act if a runner script was involved
    runner_keywords = ["weekly_runner", "daily_runner", "monthly_runner", "run_screener"]
    if not any(kw in tool_input for kw in runner_keywords):
        return

    today    = date.today()
    log_path = ROOT / f"output/ops_log_{today.strftime('%y%m%d')}.md"
    ts       = datetime.now().strftime("%H:%M")

    runner_name = next((kw for kw in runner_keywords if kw in tool_input), "runner")
    # Extract last 3 lines of output as summary
    out_lines = [l for l in tool_output.splitlines() if l.strip()][-3:]
    summary   = " | ".join(out_lines) if out_lines else "(no output captured)"

    entry = f"""
## {runner_name} run [{ts}]
- Input: `{tool_input[:120]}`
- Summary: {summary}

"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)

    print(f"[PostRunner] Logged {runner_name} -> {log_path.name}", flush=True)

if __name__ == "__main__":
    post_runner_log()
