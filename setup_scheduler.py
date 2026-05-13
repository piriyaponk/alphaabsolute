"""
AlphaAbsolute — Windows Task Scheduler Setup
Creates automated daily + weekly + quarterly jobs.
Run once as administrator: python setup_scheduler.py
"""
import subprocess
import sys
from pathlib import Path

BASE_DIR  = Path(__file__).parent
PYTHON    = sys.executable
DAILY     = str(BASE_DIR / "scripts" / "runners" / "daily_runner.py")
WEEKLY    = str(BASE_DIR / "scripts" / "runners" / "weekly_runner.py")
LOG_DIR   = str(BASE_DIR / "data" / "state")

def create_task(name: str, script: str, schedule: str, time: str):
    """Create Windows Task Scheduler job (runs as current user, no admin needed)."""
    import getpass
    user = getpass.getuser()

    # Build cmd using /TR with pythonw to avoid console popup
    # Use pythonw if available (no console window), else python
    pythonw = PYTHON.replace("python.exe", "pythonw.exe")
    if not Path(pythonw).exists():
        pythonw = PYTHON

    tr = f'"{pythonw}" "{script}" >> "{LOG_DIR}\\{name}.log" 2>&1'

    cmd = [
        "schtasks", "/Create", "/F",
        "/TN", f"AlphaAbsolute\\{name}",
        "/TR", tr,
        "/SC", schedule,
        "/ST", time,
        "/RU", user,          # current user — no admin needed
    ]
    if schedule == "WEEKLY":
        cmd.extend(["/D", "SUN"])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  [OK] Task created: {name} ({schedule} @ {time}) — runs as {user}")
        else:
            print(f"  [ERR] {name}: {result.stderr.strip()}")
    except Exception as e:
        print(f"  [ERR] {name}: {e}")


def setup_all():
    print("\nAlphaAbsolute — Task Scheduler Setup")
    print("=" * 45)

    tasks = [
        # Daily jobs (weekdays)
        ("AA_Daily_0630",  DAILY,  "DAILY", "06:30"),   # morning: news + portfolio update
        ("AA_Daily_1800",  DAILY,  "DAILY", "18:00"),   # evening: insider + after-hours

        # Weekly synthesis (Sunday morning — full learning loop)
        ("AA_Weekly_Sun",  WEEKLY, "WEEKLY","08:00"),
    ]

    for name, script, schedule, time in tasks:
        create_task(name, script, schedule, time)

    print("\nSetup complete. Tasks registered under AlphaAbsolute\\")
    print("\nTo verify: schtasks /Query /TN AlphaAbsolute /FO LIST")
    print("To run now: schtasks /Run /TN AlphaAbsolute\\AA_Weekly_Sun")


if __name__ == "__main__":
    setup_all()
