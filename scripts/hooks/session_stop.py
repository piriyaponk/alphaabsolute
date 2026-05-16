"""
AlphaAbsolute — Session Stop Hook
Fires when Claude Code session ends (Stop event).
Auto-saves session summary to output/ops_log_YYMMDD.md.
"""
import os, sys, json
from pathlib import Path
from datetime import date, datetime

ROOT = Path(__file__).resolve().parents[2]

def session_stop():
    today     = date.today()
    log_path  = ROOT / f"output/ops_log_{today.strftime('%y%m%d')}.md"
    timestamp = datetime.now().strftime("%H:%M")

    # Append session-end marker to today's ops log
    entry = f"""
## Session End [{timestamp}]
*Auto-captured by session_stop hook*

- Date: {today.isoformat()}
- Session closed at: {timestamp}
- Review output/ for any reports generated this session

"""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)

    # Update framework_updates memory note with timestamp
    mem_path = ROOT / "memory/framework_updates.md"
    if mem_path.exists():
        content = mem_path.read_text(encoding="utf-8")
        # Just confirm it exists and is readable
        lines = [l for l in content.splitlines() if l.strip()]
        if lines:
            print(f"[Memory] framework_updates.md: {len(lines)} lines active", flush=True)

    print(f"[Session] Closed {today.isoformat()} {timestamp} -> {log_path.name}", flush=True)

if __name__ == "__main__":
    session_stop()
