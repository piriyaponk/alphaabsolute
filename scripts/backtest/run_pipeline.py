# -*- coding: utf-8 -*-
"""
run_pipeline.py — Full backtest pipeline runner (auto mode).

Runs the complete chain in correct order:
  01  Download prices (S&P500 + benchmarks)
  01b Patch missing tickers
  01c Download sector ETFs
  02  Compute signals
  [GUARDIAN] Data quality gate — abort if any check fails
  03b Baseline backtest (print quick summary)
  07  GA optimization

Usage:
  python scripts/backtest/run_pipeline.py           # full run
  python scripts/backtest/run_pipeline.py --from 02 # restart from signals
  python scripts/backtest/run_pipeline.py --from guardian  # just guardian + beyond
  python scripts/backtest/run_pipeline.py --only guardian  # just guardian
"""
import sys, io, os, subprocess, argparse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "backtest"))

SCRIPTS = ROOT / "scripts" / "backtest"

STEPS = [
    ("01",       SCRIPTS / "01_download_data.py",     "Download prices"),
    ("01b",      SCRIPTS / "01b_patch_missing.py",    "Patch missing tickers"),
    ("01c",      SCRIPTS / "01c_download_sectors.py", "Download sector ETFs"),
    ("02",       SCRIPTS / "02_compute_signals.py",   "Compute signals"),
    ("guardian", None,                                  "Data Guardian check"),
    ("03b",      SCRIPTS / "03b_backtest_v2.py",       "Baseline backtest"),
    ("07",       SCRIPTS / "07_ga_optimize.py",        "GA optimization"),
]

STEP_ORDER = [s[0] for s in STEPS]


def run_script(path: Path, label: str) -> bool:
    print(f"\n{'='*60}")
    print(f"  STEP: {label}")
    print(f"{'='*60}")
    result = subprocess.run(
        [sys.executable, str(path)],
        cwd=str(ROOT),
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"\n  [FAIL] {label} exited with code {result.returncode}")
        return False
    return True


def run_guardian() -> bool:
    from data_guardian import guardian_check
    return guardian_check(verbose=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from",  dest="from_step",  default="01",
                        help="Start from this step (e.g. 02, guardian, 07)")
    parser.add_argument("--only",  dest="only_step",  default=None,
                        help="Run only this one step")
    args = parser.parse_args()

    if args.only_step:
        steps_to_run = [s for s in STEPS if s[0] == args.only_step]
    else:
        start_idx = STEP_ORDER.index(args.from_step) if args.from_step in STEP_ORDER else 0
        steps_to_run = STEPS[start_idx:]

    print(f"\nAlphaAbsolute Backtest Pipeline")
    print(f"Steps: {[s[0] for s in steps_to_run]}")

    for step_id, script_path, label in steps_to_run:
        if step_id == "guardian":
            ok = run_guardian()
        else:
            ok = run_script(script_path, label)

        if not ok:
            print(f"\n[PIPELINE ABORTED] at step {step_id}: {label}")
            print("Fix issues above, then re-run with --from", step_id)
            sys.exit(1)

    print(f"\n{'='*60}")
    print("  PIPELINE COMPLETE")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
