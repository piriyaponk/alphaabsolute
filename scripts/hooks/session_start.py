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
                    issues.append(f"[FAIL] ticker_meta missing columns: {missing_cols} — run trend_template_screener.py")
                else:
                    # Check populated
                    n_pop = conn.execute(
                        "SELECT COUNT(*) FROM ticker_meta WHERE rs_line_current IS NOT NULL"
                    ).fetchone()[0]
                    if n_pop < 200:
                        issues.append(f"[!]   rs_line mostly NULL ({n_pop} rows) — re-run trend_template_screener.py")
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

    # ── 3. Setups / Watchlist freshness ────────────────────────────────────
    for fname, label, script in [
        ("data/setups/setups_today.json",         "Setups",   "setup_scanner.py"),
        ("data/leadership/top30_watchlist.json",   "Watchlist","trend_template_screener.py"),
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

    # ── 4. Setup integrity: context_only / FIB must not be in actionable ──
    setups_f = ROOT / "data" / "setups" / "setups_today.json"
    if setups_f.exists():
        s = load_json(setups_f, {})
        leaks = [x.get("ticker","?") + "(" + x.get("setup_type","?") + ")"
                 for x in s.get("setups", [])
                 if x.get("context_only") or x.get("setup_type") in ("FIB", "EMA", "VPS")]
        if leaks:
            issues.append(f"[FAIL] Context-only setups in actionable list: {', '.join(leaks)} — fix setup_scanner.py filter")

    # ── 5. Read cached health_report for slow checks (imports, runner log) ─
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
                        "setups_fresh", "watchlist_fresh",  # already checked above
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
            leaders_ok   = m0.get("leaders_ok", True)
            bigshot_ok   = m0.get("bigshot_ok", False)
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
            emoji    = REGIME_EMOJI.get(regime_name, "❓")
            buy_mode = "A+B" if leaders_ok and bigshot_ok else ("A-only" if leaders_ok else "NO-BUY")

            lines.append(
                f"\n[M0 REGIME] {emoji} {regime_name} | Score={regime_score}/85"
                f" | Cash>={cash_floor:.0%} | Deploy<={max_deployed:.0%}"
                f" | Buys={buy_mode} | Data:{computed}"
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

    # ── Portfolio state (uses daily_mtm.py updated prices)
    port_path = ROOT / "data/paper_trading/portfolio_state.json"
    port = load_json(port_path)
    positions  = port.get("positions", {})
    cash       = port.get("cash", 0)
    capital    = port.get("capital", 100_000)
    nav_stored = port.get("nav")   # set by daily_mtm.py

    if positions or cash:
        # Use stored NAV if daily_mtm ran; else calculate from stored prices
        if nav_stored:
            nav = nav_stored
        else:
            nav = cash + sum(
                v.get("shares", 0) * v.get("current_price", v.get("entry_price", 0))
                for v in positions.values()
            )
        cash_pct    = round(cash / max(nav, 1) * 100, 1)
        total_ret   = port.get("total_return") or round((nav / max(capital, 1) - 1) * 100, 2)
        realized    = port.get("realized_pnl_usd", 0)
        ret_sign    = "+" if total_ret >= 0 else ""
        nav_date    = port.get("nav_date", "")
        date_tag    = f" [{nav_date}]" if nav_date else ""
        lines.append(f"\n[Portfolio] {len(positions)} positions | Cash {cash_pct}% | "
                     f"NAV ${nav:,.0f} ({ret_sign}{total_ret:.2f}%){date_tag}")
        if realized:
            lines.append(f"  Realized P&L: ${realized:+,.0f}")
        # Preload NRGC state and Health Check for held positions
        nrgc_states = {}
        nrgc_state_dir = ROOT / "data/nrgc/state"
        for ticker in positions:
            nf = nrgc_state_dir / f"{ticker.upper()}.json"
            if nf.exists():
                try:
                    nrgc_states[ticker.upper()] = load_json(nf)
                except Exception:
                    pass

        hc_states = {}
        hc_dir = ROOT / "data/health_checks"
        for ticker in positions:
            hf = hc_dir / f"{ticker.upper()}.json"
            if hf.exists():
                try:
                    hc_states[ticker.upper()] = load_json(hf)
                except Exception:
                    pass

        # Show positions with real P&L from MTM + NRGC conviction
        for ticker, pos in sorted(positions.items()):
            entry    = pos.get("entry_price", 0)
            curr     = pos.get("current_price", entry)
            pnl_pct  = pos.get("pnl_pct") or (round((curr - entry) / max(entry, 0.01) * 100, 1) if entry else 0)
            pnl_usd  = pos.get("pnl_usd", 0)
            sign     = "+" if pnl_pct >= 0 else ""
            usd_sign = "+" if pnl_usd >= 0 else ""
            stop_pct = round((pos.get("stop", entry * 0.92) / max(entry, 0.01) - 1) * 100, 1)
            # NRGC conviction for held position
            nd        = nrgc_states.get(ticker.upper(), {})
            nd_ph     = nd.get("phase", "")
            nd_conv   = nd.get("nrgc_conviction", {}).get("conviction", "")
            conv_abbr = {"STRONG": "STRO", "PREFERRED": "PREF", "PARTIAL": "PART",
                         "WEAK": "WEAK"}.get(nd_conv, "")
            nrgc_tag  = f" [Ph{nd_ph}/{conv_abbr}]" if nd_ph != "" and conv_abbr else ""
            # Health check score
            hc_d      = hc_states.get(ticker.upper(), {})
            hc_score  = hc_d.get("score")
            hc_total  = hc_d.get("total_indicators", 8)
            hc_tag    = f" HC={hc_score}/{hc_total}" if hc_score is not None else ""
            lines.append(f"  {ticker:<6} {sign}{pnl_pct:.1f}% (${usd_sign}{pnl_usd:,.0f}) | "
                         f"${curr:.2f} vs entry ${entry:.2f} | stop {stop_pct:.0f}%{nrgc_tag}{hc_tag}")
    else:
        lines.append("\n[Portfolio] No open positions | Cash only")

    # ── Base Count for held positions ─────────────────────────────────────────
    if positions:
        bc_dir = ROOT / "data/base_counts"
        bc_lines = []
        for ticker in positions:
            bc_file = bc_dir / f"{ticker}.json"
            if bc_file.exists():
                try:
                    bc = load_json(bc_file)
                    label   = bc.get("base_label", "?")
                    conv    = bc.get("conviction", "?")
                    q_data  = bc.get("base_quality", {})
                    q_score = q_data.get("total_score", 0) if isinstance(q_data, dict) else 0
                    vcp     = bc.get("vcp", {}).get("quality", "?")
                    piv     = bc.get("pivot_info", {})
                    warn    = bc.get("warning", "")
                    at_piv  = piv.get("at_pivot", False)
                    pct_piv = piv.get("pct_to_pivot", None)
                    pivot   = piv.get("pivot", 0)
                    parts   = [f"{ticker}: {label} | {vcp}"]
                    if q_score:
                        parts.append(f"Q={q_score:.0f}/100")
                    if pivot:
                        pct_str = f"({pct_piv:+.1f}%)" if pct_piv is not None else ""
                        parts.append(f"Pivot ${pivot:.2f} {pct_str}")
                    if at_piv:
                        parts.append("AT PIVOT!")
                    if warn:
                        parts.append(f"WARN:{warn[:40]}")
                    bc_lines.append("  " + " | ".join(parts))
                except Exception:
                    pass
        if bc_lines:
            lines.append("\n[Base Count]")
            lines.extend(bc_lines)

    # ── NRGC v3.0 top picks (from nrgc/state/) ───────────────────────────────
    # v3.0: Pure signal fingerprint — no composite score for phase decision.
    # EPS direction + acceleration + consistency + Rev direction + GM trend
    nrgc_dir = ROOT / "data/nrgc/state"
    if nrgc_dir.exists():
        nrgc_all = {}
        for f in nrgc_dir.glob("*.json"):
            try:
                d = load_json(f)
                if d and isinstance(d, dict):
                    nrgc_all[f.stem] = d
            except Exception:
                pass
        if nrgc_all:
            # ── ⚡ SUPER HIGH CONVICTION ALERT ──────────────────────────────────
            # Fires when: PULSE Grade A/B (all 4 gates pass) AND NRGC Phase 2-3
            # AND NRGC Conviction = STRONG — both independent systems agree.
            # This is the maximum-size entry signal. Do NOT trim on minor signals.
            try:
                _tt_data = load_json(ROOT / "data/trend_template/screener_latest.json")
                _tt_grades = {r["ticker"]: r.get("grade", "F")
                              for r in _tt_data.get("results", [])}
                super_conv = []
                for _t, _d in nrgc_all.items():
                    _ph  = _d.get("phase", 0)
                    _cnv = _d.get("nrgc_conviction", {}).get("conviction", "")
                    _g   = _tt_grades.get(_t, "F")
                    if _ph in (2, 3) and _cnv == "STRONG" and _g in ("A", "B"):
                        _eps = _d.get("signals", {}).get("eps", {}).get("q0_yoy_pct")
                        _rev = _d.get("signals", {}).get("revenue", {}).get("q0_qoq_pct")
                        super_conv.append((_t, _ph, _g, _eps, _rev))
                if super_conv:
                    _sc_parts = []
                    for _t, _ph, _g, _eps, _rev in super_conv:
                        _ep = f"EPS={_eps:.0f}%" if _eps else "EPS=?"
                        _rv = f" Rev={_rev:.0f}%Q" if _rev else ""
                        _sc_parts.append(f"{_t}(Ph{_ph},TT={_g},{_ep}{_rv})")
                    lines.append(f"\n[⚡ SUPER HIGH CONVICTION] PULSE+NRGC STRONG: {' | '.join(_sc_parts)}")
                    lines.append(f"   -> Both systems agree independently. Maximum conviction = full position size.")
            except Exception:
                pass

            def _nrgc_ta(d):
                """Get turnaround label from v3.0 signals dict or v2.0 source_data."""
                sigs = d.get("signals", {})
                ta = sigs.get("turnaround_label") or d.get("source_data", {}).get("turnaround_label", "")
                return ta or ""

            def _nrgc_sort_key(item):
                """Sort Ph3 > Ph2, then by eps_acceleration quality, then EPS YoY magnitude."""
                t, d = item
                ph = d.get("phase", 0)
                sigs = d.get("signals", {})
                eps  = sigs.get("eps", {})
                eps_acc = eps.get("acceleration", "")
                acc_rank = {"ACCELERATING": 3, "JUST_TURNED_POSITIVE": 2, "STABLE": 1}.get(eps_acc, 0)
                # Use EPS YoY % as secondary — bigger = earlier discovery
                eps_yoy = eps.get("q0_yoy_pct") or eps.get("q0_yoy") or 0
                eps_yoy = min(abs(eps_yoy), 600)   # cap outliers
                return (-ph, -acc_rank, -eps_yoy)

            entry_zone = sorted(
                [(t, d) for t, d in nrgc_all.items() if (d.get("phase") or 0) in (2, 3)],
                key=_nrgc_sort_key
            )[:5]

            phase4 = [(t, d.get("nrgc_composite_score", 0))
                      for t, d in nrgc_all.items() if (d.get("phase") or 0) == 4]
            phase5 = [t for t, d in nrgc_all.items() if (d.get("phase") or 0) == 5]

            if entry_zone:
                parts = []
                for t, d in entry_zone:
                    ph   = d.get("phase", 0)
                    sigs = d.get("signals", {})
                    eps  = sigs.get("eps", {})
                    rev  = sigs.get("revenue", {})
                    ta   = _nrgc_ta(d)
                    conv = d.get("nrgc_conviction", {})

                    eps_acc = eps.get("acceleration", "?")[:5]
                    eps_yoy = eps.get("q0_yoy_pct") or eps.get("q0_yoy")
                    rev_qoq = rev.get("q0_qoq_pct") or rev.get("q0_qoq")
                    ma_dist = eps.get("ma_distorted", False)

                    eps_str = f"EPS={eps_yoy:.0f}%({eps_acc})" if eps_yoy is not None else f"EPS({eps_acc})"
                    rev_str = f" Rev={rev_qoq:.0f}%QoQ" if rev_qoq is not None else ""
                    ta_str  = f" [{ta}]" if ta and ta not in ("NONE", "ACCELERATING") else ""
                    ma_str  = " [MA]" if ma_dist else ""
                    # Operating leverage tag: strong EPS acceleration despite negative/flat revenue
                    # (cost-cutting / margin expansion driving earnings even as top-line dips)
                    _ol_tag = (
                        eps_acc in ("ACCEL", "STRON") and
                        rev_qoq is not None and rev_qoq < 0
                    )
                    ol_str = " [OL]" if _ol_tag else ""

                    # Conviction label: STRO/PREF/PART/WEAK
                    conv_label = conv.get("conviction", "")
                    conv_abbr  = {"STRONG": "STRO", "PREFERRED": "PREF",
                                  "PARTIAL": "PART", "WEAK": "WEAK"}.get(conv_label, "")
                    conv_str   = f" {conv_abbr}" if conv_abbr else ""

                    # Discovery Index — narrative lifecycle stage
                    disc       = nd.get("discovery_index", {})
                    disc_stage = disc.get("stage", "")
                    disc_count = disc.get("analyst_count")
                    disc_heat  = disc.get("theme_heat", "")
                    disc_emoji = {
                        "NEGLECT": "🔍",          # pre-discovery — early edge
                        "EARLY_DISCOVERY": "🚀",   # sweet spot
                        "DISCOVERING": "📈",        # institutions building
                        "CONSENSUS": "⚠️",          # narrative aging
                        "CROWDED": "🔴",            # reduce risk
                        "UNKNOWN": "",
                    }.get(disc_stage, "")
                    disc_count_s = f"{disc_count}a" if disc_count is not None else "?a"
                    disc_str = f" {disc_emoji}[{disc_stage[:8]}/{disc_count_s}/{disc_heat}]" if disc_stage else ""

                    # Theme tag from rs_universe data (if available)
                    rs_all_data = load_json(ROOT / "data/rs_universe/latest.json")
                    rs_entry = rs_all_data.get("universe", {}).get(t, {})
                    thm_tag = rs_entry.get("pulse_rs_theme_name", "")
                    thm_str = f" [{thm_tag[:10]}]" if thm_tag else ""

                    parts.append(f"{t} Ph{ph}{conv_str}{disc_str} | {eps_str}{rev_str}{ta_str}{ma_str}{ol_str}{thm_str}")
                lines.append("\n[NRGC v3.0 Entry Zones Ph2-3] " + " | ".join(parts))
            if phase4:
                names = " | ".join(f"{t}({sc:.0f})" for t, sc in sorted(phase4, key=lambda x: -x[1])[:6])
                lines.append(f"[NRGC Phase 4 Hold] {names}")
            if phase5:
                lines.append(f"[NRGC Phase 5 EXIT!!] {' | '.join(phase5[:5])}")

            # ── NRGC Phase Change Alerts (EARLIEST monster-return signal) ─────
            # Ph1->Ph2 = INFLECTION (fundamental thesis just confirmed, buy before crowds)
            # Ph2->Ph3 = ADVANCEMENT (institutions entering, add to position)
            changes_file = ROOT / "data/nrgc/phase_changes.json"
            if changes_file.exists():
                try:
                    ch_data = load_json(changes_file)
                    today_str = date.today().isoformat()
                    if ch_data.get("date") == today_str:
                        inflections = ch_data.get("inflections", [])
                        advancements = [c for c in ch_data.get("changes", [])
                                        if c.get("change_type") == "ADVANCEMENT"]
                        if inflections:
                            inf_str = " | ".join(
                                f"{c['ticker']}(Ph{c['prev_phase']}->Ph{c['new_phase']},{c.get('conviction','?')[:4]})"
                                for c in inflections[:4]
                            )
                            lines.append(f"[!! PHASE INFLECTION] {inf_str} — earliest signal, review for entry")
                        if advancements:
                            adv_str = " | ".join(
                                f"{c['ticker']}(Ph{c['prev_phase']}->Ph{c['new_phase']})"
                                for c in advancements[:4]
                            )
                            lines.append(f"[+] Phase Advance: {adv_str}")
                except Exception:
                    pass

            # ── Sitting Rule Alert (Livermore) ─────────────────────────────
            # Held positions in Phase 2-3 with strong NRGC → remind to HOLD not exit on TD signals
            portfolio_file = ROOT / "data/portfolio_state.json"
            if portfolio_file.exists():
                port = load_json(portfolio_file)
                positions = port.get("positions", {})
                held_preferred = []
                for tkr, pos in positions.items():
                    nd = nrgc_all.get(tkr.upper(), {})
                    if nd.get("phase") in (2, 3):
                        held_preferred.append(tkr.upper())
                if held_preferred:
                    lines.append(f"[SITTING RULE] {', '.join(held_preferred)} in Phase 2-3 — "
                                 f"hold thesis; TD signals = reduce 25% only, not full exit")

    # ── EMLS top scores (from smart signals if available)
    signals_path = ROOT / "data/smart_signals/latest.json"
    signals = load_json(signals_path)
    if signals:
        top = sorted(
            [(t, d.get("emls_boost", 0)) for t, d in signals.items() if d.get("emls_boost", 0) >= 3],
            key=lambda x: -x[1]
        )[:5]
        if top:
            lines.append("\n[Top Edge Signals] " + " | ".join(f"{t} +{b}" for t, b in top))

    # ── Risk Guardian Summary ─────────────────────────────────────────────────
    rg_file = ROOT / "data/risk_guardian/daily_report.json"
    if rg_file.exists():
        try:
            rg = load_json(rg_file)
            today_date = date.today().isoformat()
            if rg.get("date") == today_date:
                imm = rg.get("immediate_count", 0)
                tod = rg.get("today_count", 0)
                rev = rg.get("review_count", 0)
                if imm > 0:
                    lines.append(f"\n[[!!] RISK] IMMEDIATE={imm} | TODAY={tod} | REVIEW={rev}")
                    for f in rg.get("immediate", [])[:3]:
                        lines.append(f"  [NO] {f['ticker']}: {f['action'][:60]}")
                elif tod > 0:
                    lines.append(f"\n[Risk] No immediate flags | TODAY={tod} | REVIEW={rev}")
                else:
                    lines.append(f"\n[Risk] [OK] Clear -- no flags")

                # ── QQQ Benchmark Performance ─────────────────────────────
                bench = rg.get("qqq_benchmark", {})
                if bench.get("alpha_pct") is not None:
                    port_ret   = bench.get("portfolio_return_pct", 0)
                    qqq_ret    = bench.get("qqq_return_pct", 0)
                    alpha      = bench.get("alpha_pct", 0)
                    days       = bench.get("trading_days_tracked", 0)
                    since      = bench.get("inception_date", "")
                    sign       = "+" if alpha >= 0 else ""
                    beat_str   = "✅ BEATING" if alpha > 0 else ("🔴 LAGGING" if alpha < -1 else "≈ INLINE with")
                    cycle_flag = bench.get("full_cycle_flag", False)
                    ctx_note   = bench.get("context_note", "")
                    cycle_tag  = " 🔄full-cycle" if cycle_flag else " ⚠bull-only"
                    lines.append(
                        f"  [vs QQQ] Portfolio={port_ret:+.1f}% | QQQ={qqq_ret:+.1f}% | "
                        f"Alpha={sign}{alpha:.1f}% — {beat_str} QQQ ({days}d since {since}{cycle_tag})"
                    )
                    if ctx_note and not cycle_flag:
                        lines.append(f"           ↳ {ctx_note}")
        except Exception:
            pass

    # ── Exhaustion Alerts ─────────────────────────────────────────────────────
    ex_file = ROOT / "data/exhaustion/latest.json"
    if ex_file.exists():
        try:
            ex = load_json(ex_file)
            if ex.get("date") == date.today().isoformat():
                ex_alerts = ex.get("alerts", [])
                if ex_alerts:
                    lines.append(f"\n[Exhaustion] {len(ex_alerts)} alert(s):")
                    for a in ex_alerts[:3]:
                        lines.append(f"  [!] {a['ticker']}: score={a['exhaustion_score']:.0f} | {a['action'][:50]}")
        except Exception:
            pass

    # ── Risk flags (any position near stop)
    risk_flags = []
    for ticker, pos in positions.items():
        entry = pos.get("entry_price", 0)
        curr  = pos.get("current_price", entry)
        if entry and curr:
            drawdown = (curr - entry) / entry * 100
            if drawdown <= -5:
                risk_flags.append(f"{ticker} {drawdown:.1f}% (stop at -8%)")
    if risk_flags:
        lines.append("\n[!] RISK FLAGS: " + " | ".join(risk_flags))

    # ── RS Leaders + 3-TF Momentum ───────────────────────────────────────────
    rs_dir = ROOT / "data/rs_universe"
    rs_latest = rs_dir / "latest.json"

    def _mom_str(v):
        """Format momentum value as compact signed string."""
        if v is None: return "n/a"
        return f"{v:+.0f}"

    if rs_latest.exists():
        try:
            rs_data = load_json(rs_latest)
            universe_dict = rs_data.get("universe", {})

            # Top 5 RS leaders — show 6M/3M/1M + momentum
            top_10 = rs_data.get("top_10_leaders", [])
            if top_10:
                leader_parts = []
                for r in top_10[:5]:
                    t   = r["ticker"]
                    d   = universe_dict.get(t, r)
                    r6  = d.get("rs_6m_pct")
                    r3  = d.get("rs_3m_pct")
                    r1  = d.get("rs_1m_pct") or d.get("rs_composite_pct", 0)
                    m13 = d.get("rs_mom_1m_3m")
                    infl = "[RED]" if d.get("inflection") else ""
                    # Format: MRAM(6M=100,3M=100,1M=100,D=+0)
                    parts = [f"1M={r1:.0f}"]
                    if r3: parts.append(f"3M={r3:.0f}")
                    if r6: parts.append(f"6M={r6:.0f}")
                    parts.append(f"D={_mom_str(m13)}")
                    leader_parts.append(f"{infl}{t}({','.join(parts)})")
                lines.append(f"\n[RS Leaders] " + " | ".join(leader_parts))

            # RS inflections
            inflect_count = rs_data.get("inflection_count", 0)
            if inflect_count > 0:
                inflect_tickers = [
                    t for t, d in universe_dict.items() if d.get("inflection")
                ]
                lines.append(f"  [RED] RS INFLECTIONS ({inflect_count}): "
                              f"{', '.join(inflect_tickers[:6])} -- Phase 2->3 transition!")

            # RS laggards in portfolio
            laggards = rs_data.get("rs_laggards", [])
            if laggards:
                lag_str = " | ".join(
                    f"{r['ticker']}({r.get('rs_composite_pct',0):.0f}th)"
                    for r in laggards
                )
                lines.append(f"  [!] RS LAGGARDS (held): {lag_str} -- review for exit")
        except Exception:
            pass

    # ── Theme Heatmap + 3-TF Theme Momentum ──────────────────────────────────
    theme_rs_file = rs_dir / "theme_rs_latest.json"
    if theme_rs_file.exists():
        try:
            theme_data = load_json(theme_rs_file)
            themes = list(theme_data.get("themes", {}).values())
            # Use theme_1m_pct = rank vs other themes (0-100) as primary sort key
            # This shows RELATIVE theme strength (which theme is LEADING), not absolute member RS
            # theme_mom_1m_3m_pct = how much theme's rank changed vs 3M ago (positive = climbing)
            themes_with_rs = [
                (t["name"],
                 t.get("theme_1m_pct") or t.get("theme_rs_pct") or t.get("theme_rs_composite") or 0,
                 t.get("phase", "?"),
                 t.get("chg_4w"),
                 t.get("theme_mom_1m_3m_pct"),   # 1M percentile rank minus 3M percentile rank
                 t.get("n_leaders", 0),           # RS>70 stocks in this theme
                 [m["ticker"] for m in t.get("leaders", [])[:3]])
                for t in themes
                if t.get("theme_1m_pct") is not None or t.get("theme_rs_composite") is not None
            ]
            themes_with_rs.sort(key=lambda x: x[1] or 0, reverse=True)

            if themes_with_rs:
                # HOT = top quartile vs other themes (≥75th pct rank)
                # WARM = 55th-75th pct rank
                # WEAK = <35th pct rank
                hot  = [t for t in themes_with_rs if (t[1] or 0) >= 75]
                warm = [t for t in themes_with_rs if 55 <= (t[1] or 0) < 75]
                weak = [t for t in themes_with_rs if (t[1] or 0) < 35]

                def _fmt_theme(t):
                    # Format: "Memory(93th,D=-12)" — pct = rank vs themes, D = 1M-3M change
                    name  = t[0].split('/')[0].strip()[:12]
                    rs    = f"{t[1]:.0f}th"
                    m13   = f",D={_mom_str(t[4])}" if t[4] is not None else ""
                    return f"{name}({rs}{m13})"

                if hot:
                    lines.append(f"\n[Theme Heatmap] HOT: " +
                                 " | ".join(_fmt_theme(t) for t in hot[:5]))
                if warm:
                    lines.append(f"  WARM: " +
                                 " | ".join(_fmt_theme(t) for t in warm[:4]))
                if weak:
                    lines.append(f"  WEAK: " +
                                 " | ".join(_fmt_theme(t) for t in weak[:4]))
        except Exception:
            pass

    # ── Strong Leaders — 3-TF market + theme RS + momentum ───────────────────
    leader_file = rs_dir / "strong_leader_latest.json"
    if leader_file.exists():
        try:
            leader_data = load_json(leader_file)
            stocks = leader_data.get("stocks", {})
            # Top leaders with SL >= 80, sorted by score
            top_leaders = sorted(
                [(t, d) for t, d in stocks.items() if d.get("strong_leader_score", 0) >= 80],
                key=lambda x: -x[1]["strong_leader_score"]
            )[:6]
            if top_leaders:
                ldr_parts = []
                for t, d in top_leaders:
                    sl    = d["strong_leader_score"]
                    m1    = d.get("rs_1m_pct") or d.get("rs_market_pct", 0)
                    m3    = d.get("rs_3m_pct")
                    m6    = d.get("rs_6m_pct")
                    dm13  = d.get("rs_mom_1m_3m")
                    wt1   = d.get("within_theme_1m_pct")
                    wt3   = d.get("within_theme_3m_pct")
                    dtm13 = d.get("theme_mom_1m_3m")
                    tv    = d.get("theme_vs_themes_pct", 0)
                    thm   = (d.get("best_theme") or "N/A").split('/')[0].strip()[:8]
                    # Format: RKLB(SL=99,Mkt1M=97,D=+2,Thm1M=100,DT=+50,TV=100th)
                    p = [f"SL={sl:.0f}"]
                    p.append(f"Mkt={m1:.0f}" + (f"/{m3:.0f}" if m3 else ""))
                    p.append(f"D={_mom_str(dm13)}")
                    if wt1 is not None:
                        p.append(f"T={wt1:.0f}" + (f"/{wt3:.0f}" if wt3 else ""))
                        p.append(f"DT={_mom_str(dtm13)}")
                    p.append(f"TV={tv:.0f}")
                    ldr_parts.append(f"{t}({','.join(p)})[{thm}]")
                lines.append(f"  [Strong Leaders] " + " | ".join(ldr_parts[:4]))
                if len(ldr_parts) > 4:
                    lines.append(f"                   " + " | ".join(ldr_parts[4:]))
        except Exception:
            pass

    # ── Mode A Full Screen (AlphaAbsolute v2 -- all 6 gates) ─────────────────
    mode_a_file = ROOT / "data/screening/mode_a_full_latest.json"
    if mode_a_file.exists():
        try:
            ma = load_json(mode_a_file)
            ma_date = ma.get("date", "?")
            candidates = ma.get("candidates", [])
            n_total = ma.get("full_mode_a_count", 0)
            if candidates:
                # Show top 8 by RS composite, themed stocks first
                top8 = candidates[:8]
                parts = []
                for c in top8:
                    t   = c["ticker"]
                    rs  = c.get("rs_composite") or 0
                    eps = c.get("eps_yoy_pct")
                    rev = c.get("rev_yoy_pct")
                    lbl = (c.get("label") or "?")[:10]
                    eps_s = f"E={eps:+.0f}%" if eps is not None else ""
                    rev_s = f"R={rev:+.0f}%" if rev is not None else ""
                    parts.append(f"{t}({lbl},RS={rs:.0f},{eps_s},{rev_s})")
                lines.append(f"\n[Mode A Leaders] {n_total} pass 6/6 gates | {ma_date}")
                lines.append("  " + " | ".join(parts[:4]))
                if len(parts) > 4:
                    lines.append("  " + " | ".join(parts[4:8]))
            else:
                lines.append(f"\n[Mode A Leaders] 0 pass 6/6 gates | {ma_date} -- run pipeline_metrics + pipeline_fundamentals")
        except Exception:
            pass

    # ── CANSLIM Top Picks ─────────────────────────────────────────────────────
    canslim_summary = ROOT / "data/canslim_scores/_summary.json"
    if canslim_summary.exists():
        try:
            cs = load_json(canslim_summary)
            top_buys = sorted(
                [(t, d) for t, d in cs.items()
                 if isinstance(d, dict) and d.get("composite_score", 0) >= 60
                 and d.get("m_gate_pass", True)],
                key=lambda x: -x[1].get("composite_score", 0)
            )[:5]
            if top_buys:
                cs_parts = []
                for t, d in top_buys:
                    grade = d.get("grade", "?")
                    score = d.get("composite_score", 0)
                    c_s   = d.get("c_score", 0)
                    l_s   = d.get("l_score", 0)
                    cs_parts.append(f"{t} {grade}({score:.0f}|C{c_s:.0f}L{l_s:.0f})")
                lines.append(f"\n[CANSLIM Top] " + " | ".join(cs_parts))
        except Exception:
            pass

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

    # ── Smart Watchlist (W1) -- Priority Entries ───────────────────────────────
    wl_file = ROOT / "data/watchlist/latest.json"
    if wl_file.exists():
        try:
            wl = load_json(wl_file)
            if wl.get("date") == date.today().isoformat():
                imm  = wl.get("immediate_count", 0)
                hot  = wl.get("hot_count", 0)
                ph2  = wl.get("phase2_count", 0)
                mon  = wl.get("monitoring_watch", [])
                # ── Super High Conviction (PULSE Grade A + NRGC Ph2-3 STRONG) ──
                super_entries = [r for r in wl.get("immediate", []) + wl.get("hot", [])
                                 if r.get("super_conviction") or r.get("priority") == "SUPER_HIGH_CONVICTION"]
                if super_entries:
                    _sc_strs = []
                    for _r in super_entries[:4]:
                        _sig = _r.get("signals", [""])[0][:30] if _r.get("signals") else ""
                        _sc_strs.append(f"{_r['ticker']}(TT={_r.get('pulse_grade','?')},Ph{_r.get('nrgc_phase',0)},{_sig})")
                    lines.append(f"\n[⚡ W1 SUPER HIGH CONVICTION] PULSE+NRGC BOTH agree: {' | '.join(_sc_strs)}")
                    lines.append(f"   -> Maximum position size. Do NOT exit on minor signals.")

                if imm > 0:
                    lines.append(f"\n[[HOT] W1 WATCHLIST] IMMEDIATE={imm} | HOT={hot} | PHASE2={ph2}")
                    for r in wl.get("immediate", [])[:3]:
                        sig = r.get("signals", [""])[0][:40] if r.get("signals") else ""
                        pg  = r.get("pulse_grade", "?")
                        lines.append(f"  [!] {r['ticker']}(TT={pg}): score={r['score']:.0f} | RS={r.get('rs_pct',0):.0f}th | {sig}")
                elif hot > 0:
                    lines.append(f"\n[W1 Watchlist] No immediate | HOT={hot} | PHASE2={ph2}")
                    for r in wl.get("hot", [])[:3]:
                        pg = r.get("pulse_grade", "?")
                        lines.append(f"  [.] {r['ticker']}(TT={pg}): RS={r.get('rs_pct',0):.0f}th | {r.get('base_alert','')}")
                elif ph2 > 0:
                    lines.append(f"\n[W1 Watchlist] PHASE2 INFLECTIONS={ph2} -- early radar")
                    for r in wl.get("phase2", [])[:3]:
                        lines.append(f"  [~] {r['ticker']}: infl={r.get('inflection',0):.0f}")
                else:
                    lines.append(f"\n[W1 Watchlist] No setups today | Developing: {wl.get('watchlist_count',0)}")

                # ── Monitoring Watch (shown during CORRECTION/BEAR — buy list for regime turn) ──
                if mon and wl.get("m0_buys") == "BLOCKED":
                    top_str = " | ".join(
                        f"{r['ticker']}({r['nrgc_conviction'][:4]},RS={r['rs_pct']:.0f},{r['pulse_grade']})"
                        for r in mon[:8]
                    )
                    lines.append(f"[Watch/Regime Turn] {top_str}")
        except Exception:
            pass

    # ── Earnings Inflection Alerts ────────────────────────────────────────────
    infl_file = ROOT / "data/earnings_inflection/latest.json"
    if infl_file.exists():
        try:
            infl = load_json(infl_file)
            if infl.get("date") == date.today().isoformat() and infl.get("phase2_count", 0) > 0:
                alerts = infl.get("phase2_alerts", [])
                lines.append(f"\n[[HOT] I2 Inflection] {len(alerts)} Phase 2 EPS confirmed:")
                for a in alerts[:3]:
                    lines.append(f"  {a['ticker']}: EPS YoY={a.get('latest_eps_yoy',0):+.0f}% | "
                                 f"Rev YoY={a.get('latest_rev_yoy',0):+.0f}% | {a.get('signals',[''])[0][:35]}")
        except Exception:
            pass

    # ── Trend Template Screener -- Grade A stocks ─────────────────────────────
    tt_file = ROOT / "data/trend_template/screener_latest.json"
    if tt_file.exists():
        try:
            tt = load_json(tt_file)
            tt_date = tt.get("date", "")
            n_a  = tt.get("n_grade_a", 0)
            n_univ = tt.get("n_universe", 0)
            results = tt.get("results", [])
            # n_pass: derive from file field or count all_pass=True in results
            n_pass = tt.get("n_pass") or sum(1 for r in results if r.get("all_pass") or r.get("full_pass"))
            grade_a = [r for r in results if r.get("grade") == "A"]
            grade_b = [r for r in results if r.get("grade") == "B" and (r.get("all_pass") or r.get("full_pass"))]
            # Show Grade A always, Grade B if A is empty
            show = grade_a if grade_a else grade_b
            if show:
                ticker_parts = []
                for r in show[:6]:
                    t = r["ticker"]
                    tier = r.get("pulse_tier", "")
                    r3m  = r.get("rs_3m")
                    r6m  = r.get("rs_6m")
                    d36  = r.get("rs_mom") or r.get("rs_mom_3m_6m")
                    ep   = r.get("eps_yoy")
                    d_str = f"D={d36:+.0f}" if d36 is not None else ""
                    ep_str = f"EPS={ep:+.0f}%" if ep is not None else ""
                    parts = [f"RS={r3m:.0f}/{r6m:.0f}" if (r3m and r6m) else ""]
                    if d_str: parts.append(d_str)
                    if ep_str: parts.append(ep_str)
                    label = "A" if r in grade_a else "B"
                    ticker_parts.append(f"{t}({label},{','.join(p for p in parts if p)})")
                label_str = "Grade A" if grade_a else "Grade B leaders"
                lines.append(f"\n[Trend Template] {label_str}: {' | '.join(ticker_parts)}")
                lines.append(f"  Pass: {n_pass}/{n_univ} | A:{n_a} | "
                             f"B:{sum(1 for r in results if r.get('grade')=='B')} | "
                             f"data: {tt_date}")
            else:
                lines.append(f"\n[Trend Template] No Grade A today | Pass: {n_pass}/{n_univ} | {tt_date}")
        except Exception:
            pass

    # ── PULSE Backtest Summary (from pulse_backtester.py) ────────────────────
    bt_stats_file = ROOT / "data/backtest/pulse_signal_stats.json"
    if bt_stats_file.exists():
        try:
            bt = load_json(bt_stats_file)
            ga = bt.get("Grade_A", {})
            if ga.get("n", 0) >= 5:
                n        = ga["n"]
                hr60     = ga.get("hit_rate_60d_pct", "?")
                alpha60  = ga.get("avg_alpha_60d", "?")
                beat_qqq = ga.get("alpha_hit_rate_60d_pct", "?")
                verdict  = "✅ VALIDATED" if (isinstance(beat_qqq, (int, float)) and beat_qqq >= 55) else \
                           ("⚠️ MARGINAL" if (isinstance(beat_qqq, (int, float)) and beat_qqq >= 48) else "🔴 CALIBRATE")
                lines.append(
                    f"\n[Backtest] Grade A (n={n}): HitRate60D={hr60}% | "
                    f"Alpha60D={alpha60}% | BeatQQQ60D={beat_qqq}% → {verdict}"
                )
        except Exception:
            pass

    # ── Post-Mortem / Learning Summary ────────────────────────────────────────
    cal_file = ROOT / "data/framework_calibrator/signal_weights.json"
    if cal_file.exists():
        try:
            cal = load_json(cal_file)
            trades = cal.get("trades_used", 0)
            if trades >= 3:
                wr  = cal.get("win_rate", 0)
                ev  = cal.get("expected_value", 0)
                pay = cal.get("payoff_ratio", 0)
                lines.append(f"\n[I9 Learn] {trades} trades | Win={wr:.0f}% | "
                             f"Payoff={pay:.1f}x | EV={ev:+.1f}%/trade")
        except Exception:
            pass

    # ── NRGC Signal Performance (20D accuracy tracker) ────────────────────────
    sig_perf_file = ROOT / "data/nrgc/signal_performance.json"
    sig_log_file  = ROOT / "data/nrgc/signal_log.jsonl"
    if sig_perf_file.exists():
        try:
            perf = load_json(sig_perf_file)
            by_conv = perf.get("by_conviction", {})
            total   = perf.get("total_signals", 0)
            if by_conv:
                # Has resolved 20D outcomes
                conv_parts = []
                for conv in ["STRONG", "PREFERRED", "PARTIAL"]:
                    s = by_conv.get(conv)
                    if s and s.get("count", 0) >= 3:
                        conv_parts.append(f"{conv[:4]}:{s['win_rate']:.0f}%wr({s['count']})")
                if conv_parts:
                    lines.append(f"\n[Signal Track] {' | '.join(conv_parts)} (20D outcomes)")
            elif sig_log_file.exists():
                # Signals logged but none resolved yet — show pipeline status
                n_pending = sum(1 for l in sig_log_file.read_text(encoding="utf-8").splitlines()
                                if l.strip() and '"outcome": null' in l)
                lines.append(f"\n[Signal Track] {total} signals logged | "
                             f"{n_pending} pending 20D outcome | accuracy emerging")
        except Exception:
            pass

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

    # ── Today's context reminder
    lines.append(f"\n[Context] Load mode: research.md / execution.md / review.md")
    lines.append(f"{'='*60}\n")

    print("\n".join(lines), flush=True)

if __name__ == "__main__":
    session_start()
