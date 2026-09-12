"""
AlphaAbsolute — System 4 Daily Runner
=======================================
Runs the data pipeline that supports System 4 RS-momentum paper trading.

Modes:
  premarket  (default)  Morning — OHLCV update + RS ranking
  eod                   Evening — OHLCV gap fill + data quality

Usage:
  python scripts/runners/pre_market_runner.py                 # morning run
  python scripts/runners/pre_market_runner.py --mode eod      # EOD update
  python scripts/runners/pre_market_runner.py --step a03      # single step
  python scripts/runners/pre_market_runner.py --dry-run       # check deps only

Pipeline steps:
  PREMARKET: health_check → ohlcv_update → fix_volumes → earnings_cal
             → data_quality → a03_bench [Fri] → a03 → a03c
  EOD:       ohlcv_update → fix_volumes → fill_ohlc → data_quality_eod
             → ohlcv_prefetch
"""

from __future__ import annotations
import sys
import os
import json
import time
import argparse
import importlib.util
from datetime import datetime, date
from pathlib import Path

# Fix encoding for Thai terminals (cp874 cannot handle Unicode)
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and _stream.encoding and \
            _stream.encoding.lower() in ("cp874", "cp1252", "ascii"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[2]
LOG_DIR  = BASE_DIR / "data" / "runner_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))


# ── Load .env ─────────────────────────────────────────────────────────────────
def _load_env() -> None:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for ln in env_path.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()


# ── Pipeline Step Registry ────────────────────────────────────────────────────
#
# Each step:
#   id              unique short name (used with --step)
#   layer           display grouping
#   name            human-readable name
#   module          dotted Python module path from BASE_DIR
#   func            function to call  (must accept no args)
#   output          primary output file (relative to BASE_DIR) — used for dry-run
#   desc            one-line description
#   critical        if True and step fails → abort pipeline
#   modes           which run modes include this step (premarket / eod / monthly)
#   friday_only     only runs on Fridays when in premarket mode
#   tuesday_only    only runs on Tuesdays when in premarket mode
#   skip_if_missing if True, quietly skip when script file does not yet exist
#   kwargs          extra keyword args to pass (step must accept **kwargs)

STEPS: list[dict] = [
    # ── Diagnostics ──────────────────────────────────────────────────────────
    {
        "id":       "health_check",
        "layer":    "0-Diagnostics",
        "name":     "System Health Check",
        "module":   "scripts.diagnostics.health_check",
        "func":     "run_with_heal",
        "output":   "data/health/health_report.json",
        "desc":     "Data freshness, DB schema, API keys",
        "critical": False,
        "modes":    ["premarket", "eod"],
    },

    # ── OHLCV ────────────────────────────────────────────────────────────────
    {
        "id":       "ohlcv_update",
        "layer":    "0-Data",
        "name":     "OHLCV Bulk Update",
        "module":   "scripts.pre_compute.update_ohlcv_bulk",
        "func":     "run",
        "output":   "data/ohlcv.db",
        "desc":     "Polygon grouped → all tickers",
        "critical": False,
        "modes":    ["premarket", "eod"],
    },
    {
        "id":       "fix_volumes",
        "layer":    "0-Data",
        "name":     "Fix Float Volumes",
        "module":   "scripts.pre_compute.fix_dates_volumes",
        "func":     "run",
        "output":   "data/ohlcv.db",
        "desc":     "Cast float volumes to INTEGER",
        "critical": False,
        "modes":    ["premarket", "eod"],
    },
    {
        "id":       "earnings_cal",
        "layer":    "0-Data",
        "name":     "Earnings Calendar",
        "module":   "scripts.pre_compute.fetch_earnings_calendar",
        "func":     "run",
        "output":   "data/regime/earnings_next30.json",
        "desc":     "FMP earnings calendar — S4 uses 5-day earnings gate",
        "critical": False,
        "modes":    ["premarket"],
    },
    {
        "id":       "data_quality",
        "layer":    "0-Data",
        "name":     "Data Quality Check",
        "module":   "scripts.pre_compute.data_quality",
        "func":     "run",
        "output":   "data/quality/quality_latest.json",
        "desc":     "OHLCV health audit",
        "critical": False,
        "modes":    ["premarket"],
    },

    # ── RS Ranking (S4 uses RS percentiles to select top stocks) ─────────────
    {
        "id":          "a03_bench",
        "layer":       "1-RS",
        "name":        "RS Benchmark Builder",
        "module":      "scripts.pre_compute.rs_benchmark",
        "func":        "build",
        "output":      "data/rs_universe/benchmark_distribution.json",
        "desc":        "S&P+Nasdaq RS distribution (Fridays only)",
        "critical":    False,
        "modes":       ["premarket"],
        "friday_only": True,
    },
    {
        "id":       "a03",
        "layer":    "1-RS",
        "name":     "RS Universe Ranker",
        "module":   "scripts.pre_compute.rs_ranker",
        "func":     "run",
        "output":   "data/rs_universe/latest.json",
        "desc":     "RS percentile 1M/3M/6M/12M — S4 selects top RS stocks",
        "critical": False,
        "modes":    ["premarket"],
    },
    {
        "id":       "a03c",
        "layer":    "1-RS",
        "name":     "RS Change Detector",
        "module":   "scripts.pre_compute.rs_change_detector",
        "func":     "run",
        "output":   "data/rs_universe/changes_today.json",
        "desc":     "Daily RS diff — climbers and droppers",
        "critical": False,
        "modes":    ["premarket"],
    },

    # ── EOD ──────────────────────────────────────────────────────────────────
    {
        "id":              "fill_ohlc",
        "layer":           "0-Data",
        "name":            "Fill OHLC Gaps",
        "module":          "scripts.pre_compute.fill_ohlc_gaps",
        "func":            "run",
        "output":          "data/quality/quality_latest.json",
        "desc":            "Fill NULL open/high/low via Polygon",
        "critical":        False,
        "modes":           ["eod"],
        "skip_if_missing": True,
    },
    {
        "id":       "data_quality_eod",
        "layer":    "0-Data",
        "name":     "Data Quality Check (EOD)",
        "module":   "scripts.pre_compute.data_quality",
        "func":     "run",
        "output":   "data/quality/quality_latest.json",
        "desc":     "EOD health audit after OHLCV update",
        "critical": False,
        "modes":    ["eod"],
    },
    {
        "id":       "ohlcv_prefetch",
        "layer":    "0-Data",
        "name":     "OHLCV Pre-fetch",
        "module":   "scripts.pre_compute.ohlcv_prefetch",
        "func":     "run",
        "output":   "data/ohlcv_cache/_manifest.json",
        "desc":     "290d OHLCV for benchmark tickers → disk cache",
        "critical": False,
        "modes":    ["eod"],
    },
    {
        "id":       "rs_history",
        "layer":    "1-RS",
        "name":     "RS History Backfill",
        "module":   "scripts.pre_compute.pipeline_rs_history",
        "func":     "run",
        "output":   "data/rs_universe/latest.json",
        "desc":     "Backfill rs_daily for last 30 trading dates",
        "critical": False,
        "modes":    ["eod"],
        "skip_if_missing": True,
    },

    # ── S4 Brain (Obsidian sync — runs daily after RS) ───────────────────────
    {
        "id":       "s4_brain",
        "layer":    "2-Brain",
        "name":     "S4 Brain — Obsidian Sync",
        "module":   "scripts.brain.s4_obsidian_writer",
        "func":     "run",
        "output":   None,
        "desc":     "Trade log + signal calibration → Obsidian vault",
        "critical": False,
        "modes":    ["premarket"],
    },
    # Post-mortem: 1st of each month only
    {
        "id":          "s4_postmortem",
        "layer":       "2-Brain",
        "name":        "S4 Post-Mortem",
        "module":      "scripts.brain.s4_postmortem_compute",
        "func":        "run",
        "output":      None,
        "desc":        "Monthly: missed leaders + big losers → Obsidian",
        "critical":    False,
        "modes":       ["premarket"],
        "first_of_month": True,
    },
]


# ── Logger ────────────────────────────────────────────────────────────────────

class RunnerLog:
    def __init__(self, mode: str):
        self.today    = date.today().strftime("%y%m%d")
        self.mode     = mode
        self.log_file = LOG_DIR / f"runner_{self.today}_{mode}.json"
        self.entries: list[dict] = []

    def record(self, step_id: str, name: str, success: bool,
               duration: float, output: str, error: str = "", skipped: bool = False) -> None:
        tag    = "[SKIP]" if skipped else ("[OK]" if success else "[FAIL]")
        status = "skipped" if skipped else ("ok" if success else "fail")
        entry  = {
            "step":     step_id,
            "name":     name,
            "status":   status,
            "duration": round(duration, 1),
            "output":   output,
            "error":    error,
            "time":     datetime.now().strftime("%H:%M:%S"),
        }
        self.entries.append(entry)
        msg = f"  {tag} {name} [{duration:.1f}s]"
        if error:
            msg += f" -- {error[:100]}"
        print(msg)

    def save(self) -> dict:
        passed  = sum(1 for e in self.entries if e["status"] == "ok")
        failed  = sum(1 for e in self.entries if e["status"] == "fail")
        skipped = sum(1 for e in self.entries if e["status"] == "skipped")
        summary = {
            "date":    date.today().isoformat(),
            "mode":    self.mode,
            "run_at":  datetime.now().isoformat(),
            "steps":   self.entries,
            "passed":  passed,
            "failed":  failed,
            "skipped": skipped,
        }
        self.log_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary


# ── Step Runner ───────────────────────────────────────────────────────────────

def _resolve_output(step: dict) -> Path | None:
    """Return the output path — handle both files and directories. None = no output file."""
    if not step.get("output"):
        return None
    p = BASE_DIR / step["output"]
    return p


def _execute_step_once(step: dict, full_path) -> tuple[bool, float, str]:
    """
    Execute one attempt of a pipeline step.
    Returns (success, duration, error_msg).
    Separated from run_step() so retry logic can call it cleanly.
    """
    t0 = time.time()
    try:
        spec   = importlib.util.spec_from_file_location(step["id"], full_path)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        func = getattr(module, step["func"], None)
        if func is None:
            return False, time.time() - t0, f"Function '{step['func']}' not in {step['module']}"

        kw = step.get("kwargs", {})
        func(**kw) if kw else func()
        duration = time.time() - t0

        # Verify output exists (skip if step has no output file)
        output_p = _resolve_output(step)
        if output_p is not None:
            if step["output"].endswith("/"):
                today_str = date.today().strftime("%y%m%d")
                if output_p.exists() and step["id"] == "a11":
                    brief = output_p / f"daily_brief_{today_str}.md"
                    if not brief.exists():
                        return False, duration, f"report_writer: daily_brief_{today_str}.md not written"
            else:
                if not output_p.exists():
                    return False, duration, "Output file not written"

        return True, duration, ""

    except Exception as exc:
        import traceback
        err = f"{type(exc).__name__}: {exc}"
        print(f"\n    TRACEBACK:")
        traceback.print_exc()
        return False, time.time() - t0, err[:200]


# Errors that are worth retrying (transient: network, rate-limit, SSL, file lock)
_RETRYABLE = (
    "ConnectionError", "Timeout", "HTTPError", "ReadTimeout", "ConnectTimeout",
    "429", "503", "SSLError", "RemoteDisconnected", "IncompleteRead",
    "PermissionError", "WinError 5",   # file-lock on Windows — usually clears in seconds
    "Output file not written",         # script ran but output not flushed yet
)

# Steps whose output is cheap to recompute — suppress retry noise for these
_NO_RETRY_STEPS = {"health_check", "data_quality", "data_quality_eod"}

# Max retry attempts for transient failures (total attempts = 1 + MAX_RETRIES)
MAX_RETRIES = 2
RETRY_DELAY = 5   # seconds between retries


def run_step(step: dict, dry_run: bool = False) -> tuple[bool, float, str, bool]:
    """
    Run a single pipeline step with auto-retry on transient failures.
    Returns (success, duration, error_msg, was_skipped).
    """
    t0 = time.time()

    # --- skip if day_filter doesn't match today ---
    day_filter = step.get("day_filter")
    if day_filter is not None:
        today_weekday = date.today().weekday()  # 0=Mon, 4=Fri
        if today_weekday not in day_filter:
            return True, 0.0, "", True  # silently skip — wrong day

    # --- skip if script file missing ---
    module_path = step["module"].replace(".", "/") + ".py"
    full_path   = BASE_DIR / module_path
    if not full_path.exists():
        if step.get("skip_if_missing"):
            return True, 0.0, "", True   # silently skip
        return False, 0.0, f"Script not found: {module_path}", False

    if dry_run:
        output_p = _resolve_output(step)
        if output_p is None:
            return True, 0.0, "", False   # no output to check
        if step["output"].endswith("/"):
            # Directory outputs: dry-run always passes — the script creates the dir on first run.
            return True, 0.0, "", False
        exists = output_p.exists()
        return exists, 0.0, "" if exists else f"Output missing: {step['output']}", False

    # --- Attempt 1 ---
    ok, duration, err = _execute_step_once(step, full_path)
    if ok:
        return True, duration, "", False

    # --- Retry logic ---
    # Skip retry for non-transient errors (code bugs, missing functions, etc.)
    # and for cheap diagnostic steps that aren't worth re-running
    step_id = step.get("id", "")
    is_transient = any(kw in err for kw in _RETRYABLE)
    should_retry = is_transient and step_id not in _NO_RETRY_STEPS and not dry_run

    if not should_retry:
        return False, time.time() - t0, err, False

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n    [RETRY {attempt}/{MAX_RETRIES}] {step['name']} — sleeping {RETRY_DELAY}s then retrying...")
        print(f"    Last error: {err[:120]}")
        time.sleep(RETRY_DELAY)

        ok, duration, err = _execute_step_once(step, full_path)
        if ok:
            print(f"    [RETRY {attempt}/{MAX_RETRIES}] RECOVERED after {attempt} retry(ies)")
            return True, time.time() - t0, "", False
        print(f"    [RETRY {attempt}/{MAX_RETRIES}] Still failing: {err[:120]}")

    # All retries exhausted
    print(f"\n    [RETRY] All {MAX_RETRIES} retries exhausted for {step['name']}")
    return False, time.time() - t0, f"[after {MAX_RETRIES} retries] {err}", False


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _cleanup_stale_pkl_cache() -> None:
    """Delete ohlcv_cache PKL files from previous days (BOA-022 DEC-024).
    PKLs are intraday deduplication caches — they have zero readers once the
    calendar date advances. Only today's date is kept.
    """
    cache_dir = BASE_DIR / "data" / "ohlcv_cache"
    if not cache_dir.exists():
        return
    today_str = date.today().strftime("%Y-%m-%d")
    deleted = 0
    for f in cache_dir.glob("*.pkl"):
        # Filename format: TICKER_<period>_<YYYY-MM-DD>.pkl
        # Keep files containing today's date string; delete all others.
        if today_str not in f.name:
            try:
                f.unlink()
                deleted += 1
            except OSError:
                pass
    if deleted:
        print(f"  [Cleanup] Removed {deleted} stale PKL cache files (BOA-022)")


# All directories that pipeline scripts expect to exist at import time.
# Runner creates these before any step runs → module-level mkdir() calls become
# no-ops (exist_ok=True on an existing dir is always safe, no PermissionError).
_REQUIRED_DIRS = [
    "data/regime", "data/rs_universe", "data/rs_universe/snapshots",
    "data/runner_logs", "data/health", "data/quality",
    "data/paper_trading", "data/ohlcv_cache",
    "output",
]


def _ensure_dirs() -> None:
    """Create all required output directories before any script is imported.
    Prevents PermissionError on module-level mkdir() calls on Windows/OneDrive
    (exist_ok=True on an already-existing dir is a safe no-op)."""
    for rel in _REQUIRED_DIRS:
        d = BASE_DIR / rel
        if not d.exists():
            try:
                d.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                pass   # dir was created by another process between exists() and mkdir()


def run_pipeline(mode: str = "premarket", step_filter: str | None = None,
                 dry_run: bool = False) -> dict:
    _ensure_dirs()   # guarantee all output dirs exist before any step imports
    today_str = date.today().strftime("%Y-%m-%d")
    run_mode  = "DRY-RUN" if dry_run else mode.upper()
    weekday    = date.today().weekday()   # 0=Mon, 4=Fri, 1=Tue, 6=Sun
    is_friday  = weekday == 4
    is_tuesday = weekday == 1
    is_sunday  = weekday == 6
    is_first_of_month = date.today().day == 1

    # BOA-022 DEC-024: purge stale PKL cache before pipeline starts
    if not dry_run:
        _cleanup_stale_pkl_cache()

    print(f"\n{'='*62}")
    print(f"  AlphaAbsolute v2 Runner  [{today_str}]  [{run_mode}]")
    print(f"  Started: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*62}")

    # Select steps
    if step_filter:
        steps_to_run = [s for s in STEPS if s["id"] == step_filter]
        if not steps_to_run:
            print(f"\n  ERROR: No step with id='{step_filter}'")
            print(f"  Available: {', '.join(s['id'] for s in STEPS)}")
            return {}
    else:
        is_first_of_month = (date.today().day == 1)
        steps_to_run = [
            s for s in STEPS
            if mode in s.get("modes", ["premarket"])
            and not (s.get("friday_only")      and not is_friday)
            and not (s.get("tuesday_only")     and not is_tuesday)
            and not (s.get("sunday_only")      and not is_sunday)
            and not (s.get("first_of_month")   and not is_first_of_month)
        ]

    log = RunnerLog(mode)
    aborted = False

    for step in steps_to_run:
        if aborted:
            log.record(step["id"], step["name"], False, 0.0,
                       step["output"], "Pipeline aborted by critical failure")
            continue

        print(f"\n  [{step['layer']}] {step['name']}")
        print(f"  -> {step['desc']}")

        ok, dur, err, skipped = run_step(step, dry_run=dry_run)
        log.record(step["id"], step["name"], ok, dur, step["output"], err, skipped)

        if not ok and not skipped and step.get("critical"):
            print(f"\n  !! CRITICAL STEP FAILED — pipeline aborted.")
            print(f"     Fix '{step['module']}' before retrying.")
            aborted = True

    summary = log.save()

    print(f"\n{'='*62}")
    n = len([e for e in summary["steps"] if e["status"] != "skipped"])
    print(f"  Complete: {summary['passed']}/{n} passed | "
          f"{summary['failed']} failed | {summary['skipped']} skipped")
    print(f"  Log: {log.log_file.name}")
    print(f"{'='*62}\n")

    # Send Telegram alert only on failures — skip if ALL PASS
    _send_pipeline_alert(summary, mode, aborted)
    return summary


def _send_pipeline_alert(summary: dict, mode: str, aborted: bool) -> None:
    """
    Send a Telegram pipeline status alert ONLY when there are failures.
    - ALL PASS  → silent (no message sent)
    - PARTIAL FAILURE / ABORT → warning with failed step names
    Silent if Telegram not configured.
    """
    import os, requests as _req
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return

    failed_count = summary.get("failed", 0)

    # Silent on ALL PASS — only alert when something is wrong
    if not aborted and failed_count == 0:
        print("  [Alert] Pipeline OK — no Telegram alert sent (all pass)")
        return
    passed_count = summary.get("passed", 0)
    skipped_count = summary.get("skipped", 0)
    run_date = summary.get("date", date.today().isoformat())

    try:
        failed_steps = [s["name"] for s in summary.get("steps", [])
                        if s.get("status") == "fail"]
    except Exception:
        failed_steps = []

    display_count = failed_count if failed_count != len(failed_steps) else len(failed_steps)

    if aborted:
        aborted_step = next(
            (s["name"] for s in summary.get("steps", [])
             if s.get("status") == "fail"
             and "Pipeline aborted by critical failure" not in s.get("error", "")),
            "unknown step"
        )
        status_line = f"[US] [ABORT] PIPELINE ABORTED | {mode.upper()} | {run_date}"
        detail = f"Failed: {display_count} step(s)\nAborted at: {aborted_step} — downstream skipped"
        if failed_steps:
            detail += "\nFailed steps: " + ", ".join(failed_steps[:5])
    elif failed_count > 0:
        status_line = f"[US] [WARN] PARTIAL FAILURE | {mode.upper()} | {run_date}"
        detail = f"Failed: {display_count} step(s) | Passed: {passed_count} | Skipped: {skipped_count}"
        if failed_steps:
            detail += "\nFailed: " + ", ".join(failed_steps[:5])
        else:
            detail += "\n(step names unavailable — check runner log)"
    else:
        status_line = f"[OK] ALL PASS | {mode.upper()} | {run_date}"
        detail = f"Passed: {passed_count} | Skipped: {skipped_count} | No failures"

    msg = f"{status_line}\n{detail}"

    try:
        _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=10,
            verify=False,
        )
        label = "ALL PASS" if failed_count == 0 and not aborted else ("ABORT" if aborted else "PARTIAL FAILURE")
        print(f"  [Alert] Telegram pipeline status sent: {label}")
    except Exception as e:
        print(f"  [Alert] Telegram send failed: {e}")


# ── Regime Summary ────────────────────────────────────────────────────────────

# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AlphaAbsolute v2 — Master Daily Pipeline Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  premarket   Morning pipeline (default): OHLCV + RS ranking
  eod         Evening: OHLCV gap fill + data quality

Examples:
  python scripts/runners/pre_market_runner.py
  python scripts/runners/pre_market_runner.py --mode eod
  python scripts/runners/pre_market_runner.py --step a03
  python scripts/runners/pre_market_runner.py --dry-run

Step IDs:
  Premarket: health_check ohlcv_update fix_volumes earnings_cal data_quality
             a03_bench [Fri] a03 a03c
  EOD:       health_check ohlcv_update fix_volumes fill_ohlc data_quality_eod
             ohlcv_prefetch rs_history
        """,
    )
    parser.add_argument(
        "--mode", choices=["premarket", "eod", "monthly"], default="premarket",
        help="Run mode (default: premarket)",
    )
    parser.add_argument(
        "--step", type=str, default=None,
        help="Run only one step by its ID (overrides --mode filter)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Check whether expected output files exist without running scripts",
    )
    args = parser.parse_args()

    result = run_pipeline(mode=args.mode, step_filter=args.step, dry_run=args.dry_run)
    # Exit non-zero on failures so GitHub Actions marks the workflow as failed
    if result and (result.get("failed", 0) > 0):
        sys.exit(1)
