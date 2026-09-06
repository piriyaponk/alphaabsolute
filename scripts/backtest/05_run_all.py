"""
Master runner: download → signals → baseline backtest → optimize
Run: python 05_run_all.py [download|signals|baseline|grid|ga|all]
"""
import sys, subprocess
from pathlib import Path

SCRIPTS = Path(__file__).parent

def run(script, *args):
    cmd = [sys.executable, str(SCRIPTS / script)] + list(args)
    print(f"\n{'='*60}")
    print(f"Running: {' '.join(cmd)}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, cwd=str(SCRIPTS))
    if result.returncode != 0:
        print(f"FAILED: {script}")
        sys.exit(1)

mode = sys.argv[1] if len(sys.argv) > 1 else "all"

if mode in ("download", "all"):
    run("01_download_data.py")

if mode in ("signals", "all"):
    run("02_compute_signals.py")

if mode in ("baseline", "all"):
    run("03_backtest_engine.py")

if mode in ("grid", "all"):
    run("04_optimize.py", "grid")

if mode in ("ga",):
    run("04_optimize.py", "ga")

if mode in ("both",):
    run("04_optimize.py", "both")

print("\nAll steps complete.")
