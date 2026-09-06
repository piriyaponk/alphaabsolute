"""
AlphaAbsolute -- System Audit (Self-Healing)
Checks every integration point. When something fails -> auto-fix -> re-check.
Loops until ALL checks pass or max_attempts reached.

Checks:
  C01  Vault: Obsidian folders exist + core notes present
  C02  Brain wiring: every scripts/brain/*.py imported in session_start.py
  C03  Runner wiring: critical brain scripts in pre_market_runner.py STEPS
  C04  Output freshness: key pipeline outputs not stale (age thresholds)
  C05  Env vars: required .env keys present (non-empty)
  C06  Market Memory: historical cases seeded
  C07  Playbooks: minimum playbook count in vault
  C08  Trade logger: 14_Post_Mortem folders exist

Auto-fix actions (per check):
  C01  -> run vault_init.py
  C02  -> add import to session_start.py
  C03  -> add step to pre_market_runner.py
  C04  -> re-run the stale script
  C05  -> ESCALATE (cannot auto-fix secrets)
  C06  -> run market_memory.py
  C07  -> run market_memory.py
  C08  -> run vault_init.py

Run:  python scripts/brain/system_audit.py [--fix] [--max-attempts N] [--telegram]
      --fix          attempt auto-fix (default: True)
      --max-attempts max self-healing iterations (default: 3)
      --telegram     send Telegram summary when done

Called by: .github/workflows/sunday_research.yml (weekly)
           session_start.py can call get_audit_summary() for lightweight status
"""
import sys, os, json, subprocess, re, time
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DOTENV = ROOT / ".env"
SESSION_START = ROOT / "scripts/hooks/session_start.py"
RUNNER        = ROOT / "scripts/runners/pre_market_runner.py"
BRAIN_DIR     = ROOT / "scripts/brain"

# ── Telegram helper ────────────────────────────────────────────────────────────
def _load_env() -> dict:
    env = {}
    if DOTENV.exists():
        for line in DOTENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env

def _telegram(text: str):
    env = _load_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat  = env.get("TELEGRAM_CHAT_ID",   "")
    if not token or not chat:
        return
    import urllib.request
    payload = json.dumps({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        try:
            payload2 = json.dumps({"chat_id": chat, "text": text}).encode()
            req2 = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload2, headers={"Content-Type": "application/json"}, method="POST",
            )
            urllib.request.urlopen(req2, timeout=10)
        except Exception:
            pass


# ── Check result ───────────────────────────────────────────────────────────────
class CheckResult:
    def __init__(self, id: str, name: str):
        self.id     = id
        self.name   = name
        self.passed = False
        self.detail = ""
        self.fixed  = False
        self.fix_log = []

    def ok(self, detail: str = "") -> "CheckResult":
        self.passed = True
        self.detail = detail
        return self

    def fail(self, detail: str) -> "CheckResult":
        self.passed = False
        self.detail = detail
        return self

    def __str__(self):
        icon = "PASS" if self.passed else ("FIXED" if self.fixed else "FAIL")
        return f"  [{icon}] {self.id} {self.name}: {self.detail}"


# ── Individual checks ──────────────────────────────────────────────────────────

def check_c01_vault() -> CheckResult:
    r = CheckResult("C01", "Vault folders + core notes")
    try:
        from scripts.brain.obsidian_writer_v2 import VAULT, is_available
        if not is_available():
            return r.fail(f"Vault not found at {VAULT}")
        required = [
            "00_System/README.md",
            "00_System/Templates/decision.md",
            "00_System/Templates/postmortem.md",
            "01_Investment_Philosophy/Core_Principles.md",
            "99_Current_State/state.md",
            "00_System/Knowledge_Graph.md",
        ]
        missing = [p for p in required if not (VAULT / p).exists()]
        if missing:
            return r.fail(f"{len(missing)} core notes missing: {missing[:3]}")
        folders_ok = sum(1 for f in ["10_Playbooks","12_Market_Memory","13_Decision_Journal",
                                      "14_Post_Mortem"] if (VAULT / f).exists())
        return r.ok(f"Vault OK | {folders_ok}/4 key folders present")
    except Exception as e:
        return r.fail(str(e))


def check_c02_session_wiring() -> CheckResult:
    r = CheckResult("C02", "Brain scripts wired in session_start.py")
    if not SESSION_START.exists():
        return r.fail("session_start.py not found")
    content = SESSION_START.read_text(encoding="utf-8")
    required_imports = [
        ("daily_insight",        "scripts.brain.daily_insight"),
        ("performance_monitor",  "scripts.brain.performance_monitor"),
        ("current_state_updater","scripts.brain.current_state_updater"),
    ]
    missing = [(name, mod) for name, mod in required_imports if mod not in content]
    if missing:
        return r.fail(f"Not imported: {[n for n,_ in missing]}")
    return r.ok(f"All {len(required_imports)} brain modules wired")


def check_c03_runner_wiring() -> CheckResult:
    r = CheckResult("C03", "Brain steps in pre_market_runner.py")
    if not RUNNER.exists():
        return r.fail("pre_market_runner.py not found")
    content = RUNNER.read_text(encoding="utf-8")
    required = ["brain_current_state", "scripts.brain.current_state_updater"]
    missing = [s for s in required if s not in content]
    if missing:
        return r.fail(f"Missing in runner: {missing}")
    return r.ok("brain_current_state step present")


def check_c04_output_freshness() -> CheckResult:
    r = CheckResult("C04", "Key pipeline outputs not stale")
    checks = [
        ("data/regime/market_health.json",   3,  "A01 regime"),
        ("data/regime/macro_state.json",      3,  "A02 macro"),
        ("data/rs_universe/latest.json",      3,  "A03 RS universe"),
        ("data/rs_universe/theme_rs_latest.json", 3, "A05 themes"),
    ]
    stale = []
    now = datetime.now()
    for rel_path, max_days, label in checks:
        p = ROOT / rel_path
        if not p.exists():
            stale.append(f"{label} (missing)")
        else:
            age = (now - datetime.fromtimestamp(p.stat().st_mtime)).days
            if age > max_days:
                stale.append(f"{label} ({age}d old)")
    if stale:
        return r.fail(f"Stale: {', '.join(stale)}")
    return r.ok("All key outputs fresh (<=3d)")


def check_c05_env_vars() -> CheckResult:
    r = CheckResult("C05", "Required .env keys present")
    required = ["POLYGON_API_KEY", "TIINGO_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    env = _load_env()
    missing = [k for k in required if not env.get(k, "").strip()]
    if missing:
        return r.fail(f"Missing/empty: {missing}")
    return r.ok(f"All {len(required)} required keys set")


def check_c06_market_memory() -> CheckResult:
    r = CheckResult("C06", "Historical cases in 12_Market_Memory/")
    try:
        from scripts.brain.obsidian_writer_v2 import VAULT
        folder = VAULT / "12_Market_Memory"
        if not folder.exists():
            return r.fail("12_Market_Memory/ folder missing")
        cases = list(folder.glob("*.md"))
        if len(cases) < 4:
            return r.fail(f"Only {len(cases)}/5 cases seeded")
        return r.ok(f"{len(cases)} historical cases present")
    except Exception as e:
        return r.fail(str(e))


def check_c07_playbooks() -> CheckResult:
    r = CheckResult("C07", "Playbooks seeded (min 4)")
    try:
        from scripts.brain.obsidian_writer_v2 import VAULT
        folder = VAULT / "10_Playbooks"
        if not folder.exists():
            return r.fail("10_Playbooks/ folder missing")
        pbs = list(folder.glob("*.md"))
        if len(pbs) < 4:
            return r.fail(f"Only {len(pbs)}/4 playbooks")
        return r.ok(f"{len(pbs)} playbooks present")
    except Exception as e:
        return r.fail(str(e))


def check_c08_trade_logger_folders() -> CheckResult:
    r = CheckResult("C08", "Trade logger 14_Post_Mortem/ folders")
    try:
        from scripts.brain.obsidian_writer_v2 import VAULT
        required = [
            "14_Post_Mortem/Winners",
            "14_Post_Mortem/Losers",
            "14_Post_Mortem/False_Negative",
            "14_Post_Mortem/False_Positive",
            "14_Post_Mortem/Process_Error",
            "13_Decision_Journal",
        ]
        missing = [f for f in required if not (VAULT / f).exists()]
        if missing:
            return r.fail(f"Missing folders: {missing}")
        return r.ok(f"All {len(required)} folders present")
    except Exception as e:
        return r.fail(str(e))


ALL_CHECKS = [
    check_c01_vault,
    check_c02_session_wiring,
    check_c03_runner_wiring,
    check_c04_output_freshness,
    check_c05_env_vars,
    check_c06_market_memory,
    check_c07_playbooks,
    check_c08_trade_logger_folders,
]


# ── Auto-fixers ────────────────────────────────────────────────────────────────

def _run_python(script_module: str, func: str = "run", *args) -> tuple[bool, str]:
    """Run a Python module function as a subprocess."""
    cmd = [sys.executable, "-X", "utf8", "-c",
           f"import sys; sys.path.insert(0,'{ROOT}'); "
           f"from {script_module} import {func}; print({func}(*{list(args)!r}))"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(ROOT))
        ok = out.returncode == 0
        log = (out.stdout + out.stderr).strip()[-500:]
        return ok, log
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT after 120s"
    except Exception as e:
        return False, str(e)


def _run_script_file(rel_path: str) -> tuple[bool, str]:
    """Run a script file directly."""
    p = ROOT / rel_path
    if not p.exists():
        return False, f"Script not found: {p}"
    try:
        out = subprocess.run(
            [sys.executable, "-X", "utf8", str(p)],
            capture_output=True, text=True, timeout=300, cwd=str(ROOT)
        )
        ok = out.returncode == 0
        log = (out.stdout + out.stderr).strip()[-800:]
        return ok, log
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, str(e)


def fix_c01(r: CheckResult) -> tuple[bool, str]:
    ok, log = _run_script_file("scripts/brain/vault_init.py")
    return ok, f"vault_init.py: {log[-200:]}"


def fix_c02(r: CheckResult) -> tuple[bool, str]:
    """Add missing brain imports to session_start.py."""
    if not SESSION_START.exists():
        return False, "session_start.py not found"
    content = SESSION_START.read_text(encoding="utf-8")
    original = content

    # Find injection point (before context reminder)
    inject_before = "    # ── Today's context reminder"
    if inject_before not in content:
        return False, "Cannot find injection point in session_start.py"

    additions = []
    if "scripts.brain.current_state_updater" not in content:
        additions.append("""\
    # ── Current State Updater (auto-added by system_audit) ───────────────────
    try:
        from scripts.brain.current_state_updater import run as run_cs
        cs = run_cs()
        lines.append(f"\\n{cs}")
    except Exception as _e:
        lines.append(f"\\n[CurrentState] skipped ({type(_e).__name__}: {_e})")

""")
    if not additions:
        return True, "Nothing to add (already wired)"

    new_content = content.replace(inject_before, "".join(additions) + inject_before)
    SESSION_START.write_text(new_content, encoding="utf-8")
    return True, f"Added {len(additions)} missing import(s) to session_start.py"


def fix_c03(r: CheckResult) -> tuple[bool, str]:
    """Add missing step to pre_market_runner.py."""
    if not RUNNER.exists():
        return False, "pre_market_runner.py not found"
    content = RUNNER.read_text(encoding="utf-8")
    if "brain_current_state" in content:
        return True, "Already present"
    # Find injection point before weekly_picker
    inject_before = "    # ── Weekly Prediction System"
    if inject_before not in content:
        return False, "Cannot find injection point"
    step = """\
    # ── Brain: Obsidian sync (auto-added by system_audit) ────────────────────
    {
        "id":       "brain_current_state",
        "layer":    "4-Output",
        "name":     "Brain: Sync Obsidian Current State",
        "module":   "scripts.brain.current_state_updater",
        "func":     "run",
        "output":   "data/regime/market_health.json",
        "desc":     "Writes live data to Obsidian 99_Current_State/state.md",
        "critical": False,
        "modes":    ["premarket"],
    },

"""
    new_content = content.replace(inject_before, step + inject_before)
    RUNNER.write_text(new_content, encoding="utf-8")
    return True, "Added brain_current_state step to runner"


def fix_c04(r: CheckResult) -> tuple[bool, str]:
    """Re-run stale scripts."""
    stale_map = {
        "A01 regime":   "scripts/pre_compute/market_regime.py",
        "A02 macro":    "scripts/pre_compute/macro_monitor.py",
        "A03 RS universe": "scripts/pre_compute/rs_ranker.py",
        "A05 themes":   "scripts/pre_compute/rs_theme_ranker.py",
    }
    logs = []
    fixed_any = False
    for label, script in stale_map.items():
        p = ROOT / script
        if not p.exists():
            logs.append(f"{label}: script missing")
            continue
        # Check if this one is actually stale
        output_map = {
            "market_regime.py":   "data/regime/market_health.json",
            "macro_monitor.py":   "data/regime/macro_state.json",
            "rs_ranker.py":       "data/rs_universe/latest.json",
            "rs_theme_ranker.py": "data/rs_universe/theme_rs_latest.json",
        }
        out_file = ROOT / output_map.get(p.name, "")
        if out_file.exists():
            age = (datetime.now() - datetime.fromtimestamp(out_file.stat().st_mtime)).days
            if age <= 3:
                continue  # not stale, skip
        ok, log = _run_script_file(str(p.relative_to(ROOT)))
        logs.append(f"{label}: {'OK' if ok else 'FAIL'} {log[-100:]}")
        if ok:
            fixed_any = True
    return fixed_any or not logs, " | ".join(logs) if logs else "All fresh"


def fix_c05(r: CheckResult) -> tuple[bool, str]:
    return False, "ESCALATE: Missing .env keys require manual setup -- check .env file"


def fix_c06(r: CheckResult) -> tuple[bool, str]:
    ok, log = _run_script_file("scripts/brain/market_memory.py")
    return ok, f"market_memory.py: {log[-200:]}"


def fix_c07(r: CheckResult) -> tuple[bool, str]:
    ok, log = _run_script_file("scripts/brain/market_memory.py")
    return ok, f"market_memory.py: {log[-200:]}"


def fix_c08(r: CheckResult) -> tuple[bool, str]:
    ok, log = _run_script_file("scripts/brain/vault_init.py")
    return ok, f"vault_init.py: {log[-200:]}"


FIX_MAP = {
    "C01": fix_c01,
    "C02": fix_c02,
    "C03": fix_c03,
    "C04": fix_c04,
    "C05": fix_c05,
    "C06": fix_c06,
    "C07": fix_c07,
    "C08": fix_c08,
}


# ── Main audit loop ────────────────────────────────────────────────────────────

def run_audit(fix: bool = True, max_attempts: int = 3, verbose: bool = True) -> dict:
    """
    Run all checks. For each failure: attempt auto-fix, re-check.
    Loop until all pass OR max_attempts reached.
    Returns summary dict.
    """
    history = []  # one entry per iteration

    for attempt in range(1, max_attempts + 1):
        if verbose:
            print(f"\n{'='*60}")
            print(f"[SystemAudit] Iteration {attempt}/{max_attempts} -- {datetime.now().strftime('%H:%M:%S')}")
            print(f"{'='*60}")

        results = []
        for check_fn in ALL_CHECKS:
            r = check_fn()
            results.append(r)
            if verbose:
                print(str(r))

        passed  = [r for r in results if r.passed]
        failed  = [r for r in results if not r.passed]
        n_pass  = len(passed)
        n_fail  = len(failed)

        history.append({
            "attempt": attempt,
            "passed":  n_pass,
            "failed":  n_fail,
            "failures": [(r.id, r.detail) for r in failed],
        })

        if n_fail == 0:
            if verbose:
                print(f"\n[SystemAudit] ALL {n_pass} CHECKS PASSED")
            break

        if not fix or attempt == max_attempts:
            if verbose:
                if not fix:
                    print(f"\n[SystemAudit] {n_fail} failures (--fix not enabled)")
                else:
                    print(f"\n[SystemAudit] {n_fail} failures remain after {max_attempts} attempts")
            break

        # Auto-fix all failures
        if verbose:
            print(f"\n[SystemAudit] {n_fail} failures -- running auto-fix...")

        for r in failed:
            fixer = FIX_MAP.get(r.id)
            if not fixer:
                if verbose:
                    print(f"  [SKIP] {r.id} -- no fixer registered")
                continue
            if verbose:
                print(f"  [FIX] {r.id} {r.name}...")
            ok, fix_log = fixer(r)
            r.fixed  = ok
            r.fix_log.append(fix_log)
            if verbose:
                status = "OK" if ok else "FAIL"
                print(f"    -> {status}: {fix_log[:120]}")

        if verbose:
            print(f"\n[SystemAudit] Re-checking after fixes...")
        time.sleep(1)  # brief pause for file writes to flush

    # Final state
    final_results = [check_fn() for check_fn in ALL_CHECKS]
    n_final_pass  = sum(1 for r in final_results if r.passed)
    n_final_fail  = len(final_results) - n_final_pass
    all_passed    = n_final_fail == 0

    summary = {
        "date":          date.today().isoformat(),
        "all_passed":    all_passed,
        "total_checks":  len(final_results),
        "passed":        n_final_pass,
        "failed":        n_final_fail,
        "attempts_used": len(history),
        "failures":      [(r.id, r.detail) for r in final_results if not r.passed],
        "history":       history,
    }

    if verbose:
        print(f"\n{'='*60}")
        print(f"[SystemAudit] FINAL: {n_final_pass}/{len(final_results)} pass | "
              f"{'ALL CLEAR' if all_passed else f'{n_final_fail} UNRESOLVED'}")
        print(f"{'='*60}\n")

    return summary


def get_audit_summary() -> str:
    """Lightweight version for session_start -- runs all checks, no fixing, returns 1-line status."""
    results = [fn() for fn in ALL_CHECKS]
    passed = sum(1 for r in results if r.passed)
    failed = [r for r in results if not r.passed]
    if not failed:
        return f"[SysAudit] ALL {passed} checks PASS"
    issues = " | ".join(f"{r.id}:{r.detail[:40]}" for r in failed[:3])
    return f"[SysAudit] {len(failed)} FAIL: {issues}"


def _format_telegram(summary: dict) -> str:
    today = summary["date"]
    icon  = "OK" if summary["all_passed"] else "WARN"
    lines = [
        f"[{icon}] AlphaAbsolute System Audit -- {today}",
        f"Result: {summary['passed']}/{summary['total_checks']} checks pass | {summary['attempts_used']} iteration(s)",
    ]
    if summary["failures"]:
        lines.append("Unresolved issues:")
        for check_id, detail in summary["failures"]:
            lines.append(f"  {check_id}: {detail[:80]}")
    else:
        lines.append("All integration checks green. Pipeline healthy.")
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="AlphaAbsolute System Audit (Self-Healing)")
    p.add_argument("--no-fix",       action="store_true", help="Report only, no auto-fix")
    p.add_argument("--max-attempts", type=int, default=3, help="Max healing iterations (default 3)")
    p.add_argument("--telegram",     action="store_true", help="Send Telegram summary")
    p.add_argument("--summary-only", action="store_true", help="1-line summary (for session_start)")
    args = p.parse_args()

    if args.summary_only:
        print(get_audit_summary())
        sys.exit(0)

    summary = run_audit(
        fix=not args.no_fix,
        max_attempts=args.max_attempts,
        verbose=True,
    )

    if args.telegram:
        msg = _format_telegram(summary)
        _telegram(msg)
        print(f"[SystemAudit] Telegram sent")

    sys.exit(0 if summary["all_passed"] else 1)
