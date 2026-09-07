"""
AlphaAbsolute — Session Start Hook
Displays System 4 portfolio state and data health.
"""
import json, os, sys, sqlite3
from pathlib import Path
from datetime import datetime, date, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]

def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default or {}

def _days_old(date_str: str) -> int:
    try:
        dt = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        return (date.today() - dt).days
    except Exception:
        return -1

def _last_trading_day() -> str:
    d = date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()

def _session_health_check() -> list[str]:
    issues = []
    last_td = _last_trading_day()

    # OHLCV freshness
    db = ROOT / "data" / "ohlcv.db"
    if not db.exists():
        issues.append("[FAIL] OHLCV database missing — run: python scripts/runners/pre_market_runner.py")
    else:
        try:
            with sqlite3.connect(str(db)) as conn:
                row = conn.execute("SELECT MAX(date) FROM ohlcv").fetchone()
                latest = row[0] if row else None
                days = _days_old(latest or "")
                if days > 5:
                    issues.append(f"[FAIL] OHLCV {days}d old ({latest}) — run: python scripts/runners/pre_market_runner.py --mode eod")
                elif days > 2:
                    issues.append(f"[!]   OHLCV {days}d old ({latest} vs expected {last_td})")
        except Exception as e:
            issues.append(f"[!]   OHLCV DB check error: {e}")

    # RS data freshness
    rs_file = ROOT / "data/rs_universe/latest.json"
    if rs_file.exists():
        rs = load_json(rs_file, {})
        run_date = rs.get("run_date", "")
        days = _days_old(run_date) if run_date else 99
        if days > 3:
            issues.append(f"[!]   RS data {days}d old ({run_date}) — run: python scripts/runners/pre_market_runner.py")
    else:
        issues.append("[!]   RS data missing — run: python scripts/runners/pre_market_runner.py")

    return issues


def _git_pull_latest() -> str:
    import subprocess
    try:
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=5
        )
        if staged.stdout.strip():
            return ""
        fetch = subprocess.run(
            ["git", "fetch", "--quiet", "origin", "main"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=10
        )
        if fetch.returncode != 0:
            return ""
        behind = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/main"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=5
        )
        n = int(behind.stdout.strip() or "0")
        if n == 0:
            return ""
        pull = subprocess.run(
            ["git", "pull", "--ff-only", "--quiet", "origin", "main"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=15
        )
        if pull.returncode == 0:
            return f"[Git] Pulled {n} new commit{'s' if n > 1 else ''} from cloud pipeline"
        return ""
    except Exception:
        return ""


def session_start():
    today = date.today().strftime("%Y-%m-%d")
    lines = [f"\n{'='*60}", f"  AlphaAbsolute — System 4  [{today}]", f"{'='*60}"]

    # Auto-sync
    pull_msg = _git_pull_latest()
    if pull_msg:
        lines.append(f"  {pull_msg}")

    # System Health
    health_issues = _session_health_check()
    if health_issues:
        lines.append("\n[!] SYSTEM HEALTH — issues detected:")
        for issue in health_issues:
            lines.append(f"  {issue}")
        lines.append("  -> Fix above before analysis.")

    # System 4 Portfolio
    v4_state_file = ROOT / "data/paper_trading/state.json"
    v4 = load_json(v4_state_file)
    positions = v4.get("positions", {})
    if v4:
        nav       = float(v4.get("nav", 0))
        cash      = float(v4.get("cash", 0))
        inc_nav   = float(v4.get("inception_nav", nav or 1_000_000))
        inc_date  = v4.get("inception_date", "?")
        regime_v4 = v4.get("regime", "?")
        total_ret = round((nav / max(inc_nav, 1) - 1) * 100, 2)
        cash_pct  = round(cash / max(nav, 1) * 100, 1)
        deployed  = round(100 - cash_pct, 1)
        ret_sign  = "+" if total_ret >= 0 else ""
        n_pos     = len(positions)

        # QQQ comparison
        qqq_nav_hist = v4.get("qqq_nav_history", {})
        qqq_inc = v4.get("qqq_inception", 0)
        qqq_now = list(qqq_nav_hist.values())[-1] if qqq_nav_hist else 0
        qqq_ret = round((qqq_now / max(qqq_inc, 1) - 1) * 100, 2) if qqq_inc else 0
        alpha   = round(total_ret - qqq_ret, 2)
        alpha_sign = "+" if alpha >= 0 else ""

        lines.append(
            f"\n[System 4] {n_pos} positions | Cash {cash_pct}% | Deployed {deployed}% | "
            f"Regime: {regime_v4}"
        )
        lines.append(
            f"  NAV ${nav:,.0f} | Return: {ret_sign}{total_ret:.2f}% vs QQQ {qqq_ret:+.2f}% "
            f"| Alpha: {alpha_sign}{alpha:.2f}% (since {inc_date})"
        )

        # Top positions by weight
        sorted_pos = sorted(positions.items(), key=lambda x: -x[1].get("weight_target", 0))
        top5 = [
            f"{t}({d.get('weight_target',0)*100:.1f}%,RS={d.get('rs_pct',0):.0f})"
            for t, d in sorted_pos[:5]
        ]
        if top5:
            lines.append(f"  Top: {' | '.join(top5)}")
        if n_pos > 5:
            rest = [t for t, _ in sorted_pos[5:]]
            lines.append(f"  Also: {' '.join(rest)}")
    else:
        lines.append(
            "\n[System 4] No state file — run: python scripts/paper_trading/v4_paper_trader.py --mode rebalance"
        )

    # TD Sequential
    td_state_file = ROOT / "data/td_sequential/_market_regime.json"
    if td_state_file.exists():
        try:
            td_data = load_json(td_state_file)
            mod   = td_data.get("regime_modifier", "neutral").upper()
            summ  = td_data.get("summary", "")
            score = td_data.get("score", 0)
            sigs  = td_data.get("signals", {})
            sig_parts = []
            for sym, sig in sigs.items():
                s  = sig.get("setup_count", 0)
                cd = sig.get("countdown_count", 0)
                sig_parts.append(f"{sym} Setup={s:+d}/9 CD={cd:+d}/13")
            td_line = f"[TD Sequential] {mod} (score={score}) | {summ}"
            if sig_parts:
                td_line += "\n  " + " | ".join(sig_parts)
            lines.append(f"\n{td_line}")
        except Exception:
            pass

    # Last ops log
    ops_files = sorted((ROOT / "output").glob("ops_log_*.md"), reverse=True)
    if ops_files:
        last_ops = ops_files[0]
        content  = last_ops.read_text(encoding="utf-8", errors="ignore")
        last_decision = [l for l in content.splitlines() if l.startswith("##")]
        if last_decision:
            lines.append(f"\n[Last Ops] {last_ops.name}: {last_decision[-1]}")

    lines.append(f"\n{'='*60}\n")
    print("\n".join(lines), flush=True)

if __name__ == "__main__":
    session_start()
