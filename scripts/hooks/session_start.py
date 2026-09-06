"""
AlphaAbsolute -- Session Start Hook
Fires when Claude Code session begins.
Displays: portfolio state, EMLS top scores, open risk flags, today's key events.
"""
import json, os, sys, sqlite3
from pathlib import Path
from datetime import datetime, date, timedelta

# Force UTF-8 stdout so emoji/special chars don't crash on Windows cp874 terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]

def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default or {}

# ── System Health Check (runs every session — fast local checks only) ──────────

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
    """
    Fast data-freshness + schema checks at every session open.
    Returns list of warning lines (empty = all good).
    Reads cached health_report.json for slow checks (imports, runner log).
    """
    issues = []
    last_td = _last_trading_day()

    # ── 1. OHLCV freshness ──────────────────────────────────────────────────
    db = ROOT / "data" / "ohlcv.db"
    if not db.exists():
        issues.append("[FAIL] OHLCV database missing — pipeline cannot run")
    else:
        try:
            with sqlite3.connect(str(db)) as conn:
                row = conn.execute("SELECT MAX(date) FROM ohlcv").fetchone()
                latest = row[0] if row else None
                days = _days_old(latest or "")
                if days > 5:
                    issues.append(f"[FAIL] OHLCV {days}d old ({latest}) — run EOD: python scripts/runners/pre_market_runner.py --mode eod")
                elif days > 2:
                    issues.append(f"[!]   OHLCV {days}d old ({latest} vs expected {last_td})")

                # Schema: check rs_line columns exist
                pragma = {r[1] for r in conn.execute("PRAGMA table_info(ticker_meta)").fetchall()}
                missing_cols = [c for c in ("rs_line_current", "rs_line_direction", "rs_line_near_high")
                                if c not in pragma]
                if missing_cols:
                    pass  # rs_line columns optional — trend_template_screener removed
        except Exception as e:
            issues.append(f"[!]   OHLCV DB check error: {e}")

    # ── 2. Breadth freshness ────────────────────────────────────────────────
    bh = ROOT / "data" / "breadth" / "market_breadth_history.json"
    if bh.exists():
        hist = load_json(bh, [])
        if not hist:
            issues.append("[!]   Breadth history empty — run: python scripts/pre_compute/fetch_market_breadth.py --bootstrap")
        else:
            days = _days_old(hist[-1].get("date", ""))
            if days > 5:
                issues.append(f"[!]   Breadth {days}d old ({hist[-1].get('date')}) — run fetch_market_breadth.py")

    # ── 3. Regime freshness ─────────────────────────────────────────────────
    for fname, label, script in [
        ("data/regime/market_health.json",         "Regime",   "market_regime.py"),
    ]:
        f = ROOT / fname
        if not f.exists():
            issues.append(f"[!]   {label} file missing — run {script}")
        else:
            d = load_json(f, {})
            key = "date" if "date" in d else ("generated_at" if "generated_at" in d else None)
            date_str = d.get(key, "")[:10] if key else ""
            days = _days_old(date_str) if date_str else 99
            if days > 5:
                issues.append(f"[!]   {label} {days}d old ({date_str}) — re-run {script}")

    # ── 4. Read cached health_report for slow checks (imports, runner log) ─
    report_f = ROOT / "data" / "health" / "health_report.json"
    if report_f.exists():
        try:
            report = load_json(report_f, {})
            gen_at = report.get("generated_at", "")
            hours_old = -1
            if gen_at:
                try:
                    dt = datetime.fromisoformat(gen_at)
                    hours_old = (datetime.now() - dt).total_seconds() / 3600
                except Exception:
                    pass
            # Show FAIL checks from last deep scan (if < 25h old)
            if hours_old >= 0 and hours_old < 25:
                for c in report.get("checks", []):
                    if c["status"] == "FAIL" and c["id"] not in (
                        "ohlcv_fresh", "breadth_fresh", "market_health",
                    ):
                        msg = c.get("msg", "")[:80]
                        issues.append(f"[FAIL] {c['id']}: {msg} [from last deep scan]")
            elif hours_old > 25:
                issues.append(f"[!]   Health scan > 25h old — run pre_market_runner.py for full check")
        except Exception:
            pass
    else:
        issues.append("[!]   No health_report.json — run: python scripts/diagnostics/health_check.py")

    return issues


def _git_pull_latest() -> str:
    """
    Pull latest data from GitHub (committed by cloud pipeline bot).
    Silent on success with no new commits. Shows summary if new commits pulled.
    Skips if: offline, merge conflict risk, or dirty working tree.
    Returns one-line status string (empty string = nothing to report).
    """
    import subprocess
    try:
        # Check for staged changes first — don't pull if there's a merge risk
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=5
        )
        if staged.stdout.strip():
            return ""  # staged changes — skip pull silently

        # Fetch quietly (network call — timeout 10s)
        fetch = subprocess.run(
            ["git", "fetch", "--quiet", "origin", "main"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=10
        )
        if fetch.returncode != 0:
            return ""  # offline or fetch error — skip silently

        # Check how many commits behind
        behind = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/main"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=5
        )
        n = int(behind.stdout.strip() or "0")
        if n == 0:
            return ""  # already up to date — nothing to show

        # Pull (fast-forward only — safe, no merge conflicts)
        pull = subprocess.run(
            ["git", "pull", "--ff-only", "--quiet", "origin", "main"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=15
        )
        if pull.returncode == 0:
            return f"[Git] Pulled {n} new commit{'s' if n > 1 else ''} from cloud pipeline"
        else:
            return ""  # can't fast-forward (local changes) — skip silently

    except Exception:
        return ""  # any error — skip silently, never block session start


def session_start():
    today = date.today().strftime("%Y-%m-%d")
    lines = [f"\n{'='*60}", f"  AlphaAbsolute -- Session Start  [{today}]", f"{'='*60}"]

    # ── Auto-sync: pull latest data committed by cloud pipeline ───────────────
    pull_msg = _git_pull_latest()
    if pull_msg:
        lines.append(f"  {pull_msg}")

    # ── System Health (always first — surface problems before anything else) ──
    health_issues = _session_health_check()
    if health_issues:
        lines.append("\n[!] SYSTEM HEALTH — issues detected:")
        for issue in health_issues:
            lines.append(f"  {issue}")
        lines.append("  -> Fix above before trading. Run: python scripts/diagnostics/health_check.py --report")
    # (if no issues, show nothing — silence = healthy)

    # ── M0 Market Regime (PRIMARY -- always show first) ────────────────────────
    # v2: reads from data/regime/market_health.json (written by market_regime.py)
    regime_file = ROOT / "data/regime/market_health.json"
    if regime_file.exists():
        try:
            m0 = load_json(regime_file)
            _gen_at = m0.get("generated_at", "")
            _date   = m0.get("date", "")
            # generated_at may be time-only ("23:34") or full ISO datetime
            # Use date field + time suffix for clean display
            if len(_gen_at) >= 10 and _gen_at[4] == "-":
                computed = _gen_at[:10]   # full ISO datetime: take date part
            elif _date:
                computed = f"{_date} {_gen_at}"  # date + time-only string
            else:
                computed = _gen_at[:10]
            regime_name  = m0.get("regime", "UNKNOWN")
            regime_score = m0.get("regime_score", 0)
            cash_floor   = m0.get("cash_floor", 0.0)
            max_deployed = m0.get("max_deployed", 1.0)
            spy_td       = m0.get("spy_td_signal", "Neutral")
            qqq_td       = m0.get("qqq_td_signal", "Neutral")
            pct_50       = m0.get("pct_above_50dma")
            pct_200      = m0.get("pct_above_200dma")
            dist_days    = m0.get("distribution_days", 0)
            note         = m0.get("regime_note", "")
            early_warns  = m0.get("early_warnings", [])
            liq_gate     = m0.get("liquidity_gate", "")
            macro        = m0.get("macro", {})

            # Regime emoji (v2 4-state)
            REGIME_EMOJI = {
                "Markup":       "[OK]",
                "Distribution": "[YLW]",
                "Sideways":     "[!]",
                "Markdown":     "[RED]",
            }
            emoji = REGIME_EMOJI.get(regime_name, "❓")

            lines.append(
                f"\n[M0 REGIME] {emoji} {regime_name} | Score={regime_score}/85"
                f" | Cash>={cash_floor:.0%} | Deploy<={max_deployed:.0%}"
                f" | Data:{computed}"
            )

            # Breadth + TD
            breadth_parts = []
            if pct_50  is not None: breadth_parts.append(f"Abv50d={pct_50:.0f}%")
            if pct_200 is not None: breadth_parts.append(f"Abv200d={pct_200:.0f}%")
            if dist_days:           breadth_parts.append(f"DistDays={dist_days}")
            breadth_parts.append(f"SPY-TD={spy_td}")
            breadth_parts.append(f"QQQ-TD={qqq_td}")
            lines.append(f"  Breadth: {' | '.join(breadth_parts)}")

            if note:
                lines.append(f"  Note: {note}")

            # Early warnings
            if early_warns:
                if isinstance(early_warns, list):
                    warn_strs = [w.get("signal", str(w)) if isinstance(w, dict) else str(w)
                                 for w in early_warns]
                    lines.append(f"  [!] Early warnings ({len(early_warns)}): {', '.join(warn_strs[:4])}")

            # Liquidity gate (if present from A01)
            if liq_gate:
                gate_emoji = {"SUPPORTIVE": "[OK]", "CAUTIOUS": "[YLW]", "TIGHT": "[RED]"}.get(liq_gate, "?")
                liq_cauts  = m0.get("liquidity_cautions", 0)
                macro_parts = []
                if macro.get("hy_spread") is not None:
                    macro_parts.append(f"HY={macro['hy_spread']:.2f}%")
                if macro.get("yield_curve") is not None:
                    macro_parts.append(f"Curve={macro['yield_curve']:+.2f}%")
                lines.append(
                    f"  [Liquidity] {gate_emoji} {liq_gate} ({liq_cauts}/5 cautions)"
                    + (f" | {' | '.join(macro_parts)}" if macro_parts else "")
                )

        except Exception as e:
            lines.append(f"\n[M0 REGIME] File exists but error loading: {e}")
    else:
        lines.append(f"\n[M0 REGIME] ❓ No data -- run scripts/pre_compute/market_regime.py")

    # ── System 4 Portfolio (v4_paper_trader — RS + vol_trend + MA200 + vol-parity) ──
    v4_state_file = ROOT / "data/paper_trading/state.json"
    v4 = load_json(v4_state_file)
    positions = v4.get("positions", {})  # kept for downstream sections
    if v4:
        nav         = float(v4.get("nav", 0))
        cash        = float(v4.get("cash", 0))
        inc_nav     = float(v4.get("inception_nav", nav or 1_000_000))
        inc_date    = v4.get("inception_date", "?")
        regime_v4   = v4.get("regime", "?")
        total_ret   = round((nav / max(inc_nav, 1) - 1) * 100, 2)
        cash_pct    = round(cash / max(nav, 1) * 100, 1)
        deployed    = round(100 - cash_pct, 1)
        ret_sign    = "+" if total_ret >= 0 else ""
        n_pos       = len(positions)
        lines.append(f"\n[Alpha Portfolio] {n_pos} positions | Cash {cash_pct}% | Deployed {deployed}% | "
                     f"NAV ${nav:,.0f} ({ret_sign}{total_ret:.2f}% since {inc_date}) | Regime: {regime_v4}")
        # Top 5 positions by weight
        sorted_pos = sorted(positions.items(), key=lambda x: -x[1].get("weight_target", 0))
        top5 = [
            f"{t}({round(d.get('weight_target',0)*100,1)}%,RS={d.get('rs_pct',0):.0f})"
            for t, d in sorted_pos[:5]
        ]
        if top5:
            lines.append(f"  Top: {' | '.join(top5)}")
        if n_pos > 5:
            rest = [t for t, _ in sorted_pos[5:]]
            lines.append(f"  Also: {' '.join(rest)}")
    else:
        lines.append("\n[Alpha Portfolio] No state file — run: python scripts/paper_trading/v4_paper_trader.py --mode daily")


    # ── TD Sequential Market Regime (PRIMARY indicator -- market timing)
    td_state_file = ROOT / "data/td_sequential/_market_regime.json"
    if td_state_file.exists():
        try:
            td_data = load_json(td_state_file)
            as_of   = td_data.get("as_of", "?")
            mod     = td_data.get("regime_modifier", "neutral").upper()
            summ    = td_data.get("summary", "")
            score   = td_data.get("score", 0)
            sigs    = td_data.get("signals", {})
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

    # Trend Template, PULSE, W1 Watchlist, NRGC removed — not part of System 4

    # ── Upcoming Earnings for Held Positions (Risk Gate Reminder) ────────────────
    if positions:
        try:
            sys.path.insert(0, str(ROOT / "scripts" / "learning"))
            from earnings_miner import get_next_earnings_date
            from datetime import date as _date
            earn_alerts = []
            for t in positions:
                try:
                    nd = get_next_earnings_date(t.upper())
                    if nd:
                        days_to = (nd - _date.today()).days
                        if 0 <= days_to <= 10:
                            earn_alerts.append((t, days_to, nd))
                except Exception:
                    pass
            if earn_alerts:
                earn_parts = " | ".join(
                    f"{t}({d}d, {nd.strftime('%b-%d')})" for t, d, nd in sorted(earn_alerts, key=lambda x: x[1])
                )
                lines.append(f"\n[Earnings Watch] {earn_parts} -- reduce to <3% if < 5 days")
        except Exception:
            pass

    # ── Data Source Health Summary ────────────────────────────────────────────
    src_health_file = ROOT / "data/source_health.json"
    if src_health_file.exists():
        try:
            sh = load_json(src_health_file)
            # Show any source with score < 60 (degraded) or recent failure
            degraded = []
            for src, info in sh.items():
                score = info.get("score", 100)
                calls = info.get("total_calls", 0)
                wins  = info.get("success_calls", 0)
                if calls > 5 and score < 60:
                    degraded.append(f"{src}({score:.0f})")
            if degraded:
                lines.append(f"\n[Data] [!] Degraded sources: {', '.join(degraded)} -- check connectivity")
        except Exception:
            pass

    # ── Latest ops log snippet
    ops_files = sorted((ROOT / "output").glob("ops_log_*.md"), reverse=True)
    if ops_files:
        last_ops = ops_files[0]
        content  = last_ops.read_text(encoding="utf-8", errors="ignore")
        last_decision = [l for l in content.splitlines() if l.startswith("##")]
        if last_decision:
            lines.append(f"\n[Last Ops] {last_ops.name}: {last_decision[-1]}")

    # ── Team K: Daily Research Digest ─────────────────────────────────────────
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from scripts.brain.daily_insight import run as run_daily_insight
        insight = run_daily_insight(silent=True)
        lines.append(f"\n{insight}")
    except Exception as _e:
        lines.append(f"\n[Team K] skipped ({type(_e).__name__}: {_e})")

    # ── A15: Performance Monitor ──────────────────────────────────────────────
    try:
        from scripts.brain.performance_monitor import run as run_perf
        perf = run_perf()
        lines.append(f"\n{perf}")
    except Exception as _e:
        lines.append(f"\n[A15] skipped ({type(_e).__name__}: {_e})")

    # ── System Audit: lightweight check (no fixing -- just flags issues) ────────
    try:
        from scripts.brain.system_audit import get_audit_summary
        audit = get_audit_summary()
        lines.append(f"\n{audit}")
    except Exception as _e:
        pass  # non-critical

    # ── Current State Updater: sync Obsidian 99_Current_State/state.md ───────
    try:
        from scripts.brain.current_state_updater import run as run_cs
        cs = run_cs()
        lines.append(f"\n{cs}")
    except Exception as _e:
        lines.append(f"\n[CurrentState] skipped ({type(_e).__name__}: {_e})")

    # ── Learning Loop Stats ───────────────────────────────────────────────────
    try:
        from scripts.brain.trade_logger import get_learning_stats
        ls = get_learning_stats()
        total = ls["total"]
        if total > 0:
            lines.append(
                f"\n[Learning Loop] {total} post-mortems | "
                f"W={ls['winners']} L={ls['losers']} "
                f"FN={ls['false_neg']} (missed runs)"
            )
    except Exception as _e:
        pass  # non-critical

    # ── Today's context reminder
    lines.append(f"\n[Context] Load mode: research.md / execution.md / review.md")
    lines.append(f"{'='*60}\n")

    print("\n".join(lines), flush=True)

if __name__ == "__main__":
    session_start()
