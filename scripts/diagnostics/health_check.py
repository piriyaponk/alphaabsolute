"""
AlphaAbsolute — Comprehensive System Health Check + Auto-Healer
===============================================================
v2 — Full rewrite incorporating DE Team + DA Analyst review (2026-05-24)

MODES:
  run()           — one-pass check, write health_report.json, print results
  run_with_heal() — detect → auto-fix → re-check (max 2 passes)
                    SILENT during healing; output only after final state

CHECKS (35 total):
  Infrastructure  — DB existence, schema, runner logs, API keys, imports
  Data Freshness  — OHLCV, breadth, regime, setups, watchlist, macro, screener
  Investment Logic— RR variance, universe count, RS distribution, fundamentals
                    staleness, gate unknown rates, setup geometry, cross-file
                    consistency, deduplication, Stage2/regime alignment

AUTO-HEAL (fast scripts only, < 60s each):
  Fixable:  market_regime, macro_monitor, fetch_market_breadth, fetch_earnings_calendar,
            trend_template_screener, setup_scanner, monster_scout, risk_guardian
            + inline SQL deduplication
  NOT_FIXABLE: API keys, syntax errors, DB schema corruption, logic bugs
"""

from __future__ import annotations
import json, os, sys, sqlite3, subprocess, importlib.util, traceback
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional

ROOT        = Path(__file__).resolve().parents[2]
HEALTH_DIR  = ROOT / "data" / "health"
REPORT_FILE = HEALTH_DIR / "health_report.json"
OHLCV_DB    = ROOT / "data" / "ohlcv.db"

HEALTH_DIR.mkdir(parents=True, exist_ok=True)

# US NYSE market holidays 2025–2026 (official NYSE calendar).
# Used by _last_trading_day() and _days_old() to skip market closures.
# Without this, a 3-day holiday weekend (Mon = holiday) triggers false
# "OHLCV 3d old" WARN every Tuesday morning.
_US_MARKET_HOLIDAYS: frozenset = frozenset({
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01",
    "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
})

# ── Result builders ────────────────────────────────────────────────────────────

def _ok(check_id: str, value=None, msg: str = "") -> dict:
    return {"id": check_id, "status": "OK",   "value": str(value) if value is not None else "", "msg": msg}

def _warn(check_id: str, value=None, msg: str = "") -> dict:
    return {"id": check_id, "status": "WARN", "value": str(value) if value is not None else "", "msg": msg}

def _fail(check_id: str, value=None, msg: str = "") -> dict:
    return {"id": check_id, "status": "FAIL", "value": str(value) if value is not None else "", "msg": msg}

def _skip(check_id: str, msg: str = "") -> dict:
    return {"id": check_id, "status": "SKIP", "value": "", "msg": msg}

# ── Utilities ──────────────────────────────────────────────────────────────────

def _load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default

def _days_old(date_str: str) -> int:
    """Trading days between date_str and last expected trading day.

    Compares against _last_trading_day() (US-close-aware) rather than
    date.today() — prevents false 'stale' warnings when market hasn't closed yet.

    FIX: counts TRADING days (skip weekends + US market holidays), not calendar days.
    Old calendar-day count caused false "OHLCV 3d old" WARN after 3-day holiday
    weekends (e.g., Memorial Day Monday) even though only 2 trading days had passed.

    Returns 0 when data is current (matches last expected trading day).
    """
    try:
        dt  = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        ref = datetime.strptime(_last_trading_day(), "%Y-%m-%d").date()
        if dt >= ref:
            return 0
        days = 0
        d = dt + timedelta(days=1)
        while d <= ref:
            if d.weekday() < 5 and d.isoformat() not in _US_MARKET_HOLIDAYS:
                days += 1
            d += timedelta(days=1)
        return days
    except Exception:
        return 9999

def _last_trading_day() -> str:
    """Last weekday where US market data should be available.

    US market closes 4 PM EST = 21:00 UTC. Allow 30-min Polygon settle time → 21:30 UTC.
    If it's before 21:30 UTC, today's data isn't available yet — use yesterday's trading day.
    This prevents false 'stale' warnings every morning before US market has even opened.
    """
    from datetime import timezone as _tz
    now_utc = datetime.now(_tz.utc)
    market_closed_today = (now_utc.hour > 21) or (now_utc.hour == 21 and now_utc.minute >= 30)

    # FIX: use now_utc.date() not date.today().
    # date.today() returns Bangkok local date (UTC+7). At 6 AM Bangkok = 23:00 UTC,
    # date.today() in Bangkok is already "tomorrow" relative to UTC, making the reference
    # 1 day too far ahead and triggering false stale-data WARN every morning.
    d = now_utc.date()
    if not market_closed_today:
        d -= timedelta(days=1)   # today's data not yet available

    # Walk back to nearest trading day (skip weekends AND US market holidays)
    while d.weekday() >= 5 or d.isoformat() in _US_MARKET_HOLIDAYS:
        d -= timedelta(days=1)
    return d.isoformat()

def _db_tables() -> set[str]:
    if not OHLCV_DB.exists():
        return set()
    try:
        with sqlite3.connect(str(OHLCV_DB)) as conn:
            return {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except Exception:
        return set()

def _db_columns(table: str) -> set[str]:
    try:
        with sqlite3.connect(str(OHLCV_DB)) as conn:
            return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()

# ═══════════════════════════════════════════════════════════════════════════════
# ── GROUP 1: Infrastructure ────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def check_ohlcv_db() -> list[dict]:
    """DB file exists, has data, ticker count sane."""
    results = []
    if not OHLCV_DB.exists():
        return [_fail("ohlcv_db", msg="data/ohlcv.db missing — run rs_benchmark.py")]

    try:
        with sqlite3.connect(str(OHLCV_DB)) as conn:
            # Freshness
            row = conn.execute("SELECT MAX(date) FROM ohlcv").fetchone()
            latest = row[0] if row else None
            days = _days_old(latest or "")
            last_td = _last_trading_day()

            if days > 5:
                results.append(_fail("ohlcv_fresh", latest,
                    f"{days}d old (last trading day={last_td}) — run EOD updater"))
            elif days > 2:
                results.append(_warn("ohlcv_fresh", latest,
                    f"{days}d old (expected {last_td}) — run pre_market_runner --mode eod"))
            else:
                results.append(_ok("ohlcv_fresh", f"{latest}"))

            # Ticker count today vs yesterday — catch batch-fetch failures
            row2 = conn.execute("""
                SELECT
                  SUM(CASE WHEN date=(SELECT MAX(date) FROM ohlcv) THEN 1 ELSE 0 END),
                  SUM(CASE WHEN date=(SELECT MAX(date) FROM ohlcv WHERE date<(SELECT MAX(date) FROM ohlcv)) THEN 1 ELSE 0 END)
                FROM ohlcv
                WHERE date >= date((SELECT MAX(date) FROM ohlcv), '-5 days')
            """).fetchone()
            today_n, yest_n = (row2[0] or 0), (row2[1] or 1)

            if today_n < 800:
                results.append(_fail("ohlcv_universe_count", today_n,
                    f"Only {today_n} tickers on latest date — data fetch likely failed (expect >1000)"))
            elif yest_n > 0 and today_n < yest_n * 0.90:
                results.append(_warn("ohlcv_universe_count", f"{today_n} (was {yest_n})",
                    f"Ticker count dropped {yest_n-today_n} ({(1-today_n/yest_n)*100:.0f}%) vs yesterday"))
            else:
                results.append(_ok("ohlcv_universe_count", f"{today_n:,} tickers"))

            # SPY + QQQ specifically on latest date — regime engine depends on these two
            spy_qqq = conn.execute(
                "SELECT ticker FROM ohlcv WHERE date=(SELECT MAX(date) FROM ohlcv) AND ticker IN ('SPY','QQQ')"
            ).fetchall()
            found = {r[0] for r in spy_qqq}
            missing = {"SPY", "QQQ"} - found
            if missing:
                results.append(_fail("ohlcv_spy_qqq", str(missing),
                    f"{missing} missing from latest OHLCV — A01 regime engine will use stale prices"))
            else:
                results.append(_ok("ohlcv_spy_qqq", "SPY+QQQ present on latest date"))

    except Exception as e:
        results.append(_fail("ohlcv_db", msg=f"DB error: {e}"))

    return results


def check_db_schema() -> list[dict]:
    """Required columns in ticker_meta, ohlcv, screening_results."""
    results = []
    tables = _db_tables()

    # ticker_meta — rs_line columns added by step_b5
    if "ticker_meta" not in tables:
        results.append(_fail("schema_ticker_meta", msg="ticker_meta table missing"))
    else:
        required = ["ticker", "last_close", "n_bars", "high_52w", "low_52w",
                    "rs_line_current", "rs_line_direction", "rs_line_near_high"]
        existing = _db_columns("ticker_meta")
        missing = [c for c in required if c not in existing]
        if missing:
            results.append(_fail("schema_ticker_meta", msg=f"Missing columns: {missing}"))
        else:
            try:
                with sqlite3.connect(str(OHLCV_DB)) as conn:
                    n_pop = conn.execute(
                        "SELECT COUNT(*) FROM ticker_meta WHERE rs_line_current IS NOT NULL"
                    ).fetchone()[0]
                if n_pop < 500:
                    results.append(_warn("schema_ticker_meta", f"{n_pop} rows",
                        "rs_line_current mostly NULL — re-run trend_template_screener.py"))
                else:
                    results.append(_ok("schema_ticker_meta", f"all cols present | rs_line: {n_pop:,} populated"))
            except Exception as e:
                results.append(_warn("schema_ticker_meta", msg=f"Column check error: {e}"))

    # fundamentals_summary — gate columns
    if "fundamentals_summary" in tables:
        required_f = ["gate_eps", "gate_rev", "gate_gm", "eps_yoy_pct", "rev_yoy_pct"]
        existing_f = _db_columns("fundamentals_summary")
        missing_f = [c for c in required_f if c not in existing_f]
        if missing_f:
            results.append(_fail("schema_fundamentals", msg=f"Missing columns: {missing_f}"))
        else:
            try:
                with sqlite3.connect(str(OHLCV_DB)) as conn:
                    # Sentinel bug: gate_eps=0 AND eps_yoy_pct IS NULL means "missing" was written as 0 (FAIL) not -1 (unknown)
                    sentinel_bad = conn.execute(
                        "SELECT COUNT(*) FROM fundamentals_summary WHERE gate_eps=0 AND eps_yoy_pct IS NULL"
                    ).fetchone()[0]
                    if sentinel_bad > 0:
                        results.append(_fail("schema_fundamentals", sentinel_bad,
                            f"{sentinel_bad} rows: gate_eps=0 but no EPS data — sentinel bug (should be -1 for unknown)"))
                    else:
                        results.append(_ok("schema_fundamentals", "all gate cols present | no sentinel bug"))
            except Exception as e:
                results.append(_warn("schema_fundamentals", msg=str(e)))
    else:
        results.append(_warn("schema_fundamentals", msg="fundamentals_summary table missing — run pipeline_fundamentals.py"))

    # screening_results — gate_stage2 added later; if missing A06 uses fallback
    if "screening_results" in tables:
        required_s = ["gate_rs", "gate_adtv", "gate_52w", "gate_stage2", "gate_eps", "gate_rev", "gate_gm"]
        existing_s = _db_columns("screening_results")
        missing_s = [c for c in required_s if c not in existing_s]
        if missing_s:
            results.append(_fail("schema_screening", msg=f"Missing gate columns: {missing_s} — run pipeline_metrics.py migration"))
        else:
            results.append(_ok("schema_screening", "all 7 gate columns present"))
    else:
        results.append(_warn("schema_screening", msg="screening_results table missing — run trend_template_screener.py"))

    # earnings_calendar — needed for gate_earnings
    if "earnings_calendar" in tables:
        try:
            with sqlite3.connect(str(OHLCV_DB)) as conn:
                today_str = date.today().isoformat()
                cutoff = (date.today() + timedelta(days=7)).isoformat()
                n = conn.execute(
                    "SELECT COUNT(*) FROM earnings_calendar WHERE report_date BETWEEN ? AND ?",
                    (today_str, cutoff)
                ).fetchone()[0]
                if n == 0:
                    results.append(_warn("schema_earnings",
                        msg="earnings_calendar has 0 rows for next 7 days — gate_earnings may miss upcoming reports. Run fetch_earnings_calendar.py"))
                else:
                    results.append(_ok("schema_earnings", f"{n} earnings events in next 7 days"))
        except Exception as e:
            results.append(_warn("schema_earnings", msg=str(e)))
    else:
        results.append(_warn("schema_earnings", msg="earnings_calendar table missing — run fetch_earnings_calendar.py"))

    return results


def check_api_keys() -> list[dict]:
    """All required .env keys present."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    CRITICAL = {"POLYGON_API_KEY": "OHLCV + breadth", "FMP_API_KEY": "Fundamentals"}
    OPTIONAL  = {"TIINGO_API_KEY": "Price backup", "FINNHUB_API_KEY": "Market cap",
                 "FRED_API_KEY": "Macro/yields (A02)", "TELEGRAM_BOT_TOKEN": "Telegram (A11)"}

    missing_crit = [k for k in CRITICAL if not os.getenv(k, "").strip()]
    missing_opt  = [k for k in OPTIONAL  if not os.getenv(k, "").strip()]

    if missing_crit:
        return [_fail("api_keys", msg=f"CRITICAL keys missing: {', '.join(missing_crit)}")]
    if missing_opt:
        return [_warn("api_keys", msg=f"Optional keys missing: {', '.join(missing_opt)}")]
    n = len(CRITICAL) + len(OPTIONAL)
    return [_ok("api_keys", f"{n}/{n} keys configured")]


PIPELINE_MODULES = [
    ("market_regime",           "scripts/pre_compute/market_regime.py"),
    ("macro_monitor",           "scripts/pre_compute/macro_monitor.py"),
    ("fetch_market_breadth",    "scripts/pre_compute/fetch_market_breadth.py"),
    ("rs_ranker",               "scripts/pre_compute/rs_ranker.py"),
    ("rs_theme_ranker",         "scripts/pre_compute/rs_theme_ranker.py"),
    ("trend_template_screener", "scripts/pre_compute/trend_template_screener.py"),
    ("setup_scanner",           "scripts/pre_compute/setup_scanner.py"),
    ("risk_guardian",           "scripts/pre_compute/risk_guardian.py"),
    ("fetch_earnings_calendar", "scripts/pre_compute/fetch_earnings_calendar.py"),
    ("portfolio_manager",       "scripts/portfolio/portfolio_manager.py"),
    ("report_writer",           "scripts/output/report_writer.py"),
]

def check_imports() -> list[dict]:
    """Syntax-check all pipeline scripts."""
    failures = []
    for mod_name, rel_path in PIPELINE_MODULES:
        path = ROOT / rel_path
        if not path.exists():
            continue  # skip_if_missing: optional scripts
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")
        except SyntaxError as e:
            failures.append(f"{mod_name}:L{e.lineno}:{e.msg}")
        except Exception as e:
            failures.append(f"{mod_name}:{type(e).__name__}:{str(e)[:60]}")

    if failures:
        return [_fail("python_imports", f"{len(failures)} errors",
            " | ".join(failures[:3]) + ("..." if len(failures) > 3 else ""))]
    return [_ok("python_imports", f"{len(PIPELINE_MODULES)} modules syntax-OK")]


def check_runner_log() -> list[dict]:
    """Parse most recent runner log JSON for failed steps."""
    logs_dir = ROOT / "data" / "runner_logs"
    if not logs_dir.exists():
        return [_warn("runner_log", msg="data/runner_logs/ dir missing — has pre_market_runner.py run yet?")]

    logs = sorted(logs_dir.glob("runner_*.json"), reverse=True)[:1]
    if not logs:
        return [_warn("runner_log", msg="No runner_*.json found in data/runner_logs/")]

    log_file = logs[0]
    try:
        log = _load_json(log_file, {})
        failed_steps = [s["name"] for s in log.get("steps", []) if s.get("status") == "fail"]
        log_date = log.get("date", log_file.stem)
        if failed_steps:
            return [_warn("runner_log", log_file.name,
                f"Last run had {len(failed_steps)} failed step(s): {', '.join(failed_steps[:4])}")]
        return [_ok("runner_log", f"{log_date} — all steps passed")]
    except Exception as e:
        return [_warn("runner_log", log_file.name, f"Could not parse: {e}")]


CRITICAL_FILES = [
    ("data/regime/market_health.json",              "A01 regime output"),
    ("data/regime/macro_state.json",                "A02 macro output"),
    ("data/rs_universe/latest.json",                "A03 RS universe"),
    ("data/rs_universe/theme_rs_latest.json",       "A05 theme heatmap"),
    (".env",                                         "API keys config"),
]

# BOA-022 OPTION A: These files are produced by scripts NOT in the active runner
# (trend_template_screener, monster_scout, setup_scanner, risk_guardian).
# Removed from CRITICAL_FILES to stop false WARN/FAIL on every pipeline run.
# Re-add when Option B is reactivated (N_closed_trades >= 20, est. Q1-Q2 2027).
#   ("data/setups/setups_today.json",               "A08 setup scanner"),
#   ("data/leadership/top30_watchlist.json",        "A06 watchlist"),
#   ("data/leadership/top10_active.json",           "A06 top10"),
#   ("data/trend_template/screener_latest.json",    "A06 full screener (A08 input)"),
#   ("data/bigshot/candidates.json",                "A07 Monster Scout"),
#   ("data/risk/risk_report.json",                  "A10 Risk Guardian"),

def check_critical_files() -> list[dict]:
    """All 11 critical output files exist."""
    missing = [f"{rel} ({desc})" for rel, desc in CRITICAL_FILES if not (ROOT / rel).exists()]
    if missing:
        return [_warn("critical_files", f"{len(missing)} missing", " | ".join(missing))]
    return [_ok("critical_files", f"all {len(CRITICAL_FILES)} files present")]


# ═══════════════════════════════════════════════════════════════════════════════
# ── GROUP 2: Data Freshness ────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def _freshness_check(check_id: str, rel_path: str, date_key: str,
                     label: str, script: str,
                     warn_days: int = 3, fail_days: int = 6,
                     extra_check=None) -> dict:
    """Generic freshness check for a JSON output file."""
    f = ROOT / rel_path
    if not f.exists():
        return _warn(check_id, msg=f"{label} missing — run {script}")
    d = _load_json(f, {})
    date_str = str(d.get(date_key, ""))[:10]
    days = _days_old(date_str)
    if extra_check:
        extra = extra_check(d)
        if extra: return extra
    if days > fail_days:
        return _fail(check_id, date_str, f"{days}d old — re-run {script}")
    if days > warn_days:
        return _warn(check_id, date_str, f"{days}d old — re-run {script}")
    return _ok(check_id, date_str)


def check_data_freshness() -> list[dict]:
    results = []

    # Breadth — special: check universe_n too
    bh = ROOT / "data" / "breadth" / "market_breadth_history.json"
    if not bh.exists():
        results.append(_warn("breadth_fresh", msg="breadth history missing — run fetch_market_breadth.py --bootstrap"))
    else:
        hist = _load_json(bh, [])
        if not hist:
            results.append(_warn("breadth_fresh", msg="breadth history empty — run fetch_market_breadth.py"))
        else:
            last = hist[-1]
            days = _days_old(last.get("date", ""))
            n = last.get("universe_n", 0)
            if n == 0:
                results.append(_warn("breadth_fresh", last.get("date"),
                    "Latest breadth entry has universe_n=0 — Polygon grouped-daily returned empty"))
            elif days > 5:
                results.append(_warn("breadth_fresh", last.get("date"),
                    f"{days}d old | n={n:,} — run fetch_market_breadth.py"))
            else:
                results.append(_ok("breadth_fresh", f"{last['date']} | {len(hist)} entries | n={n:,}"))

    # regime files
    results.append(_freshness_check("market_health",  "data/regime/market_health.json",
        "date", "Market Health (A01)", "market_regime.py", warn_days=2, fail_days=5))
    results.append(_freshness_check("macro_fresh",    "data/regime/macro_state.json",
        "date", "Macro State (A02)", "macro_monitor.py"))

    # BOA-022 OPTION A: trend_template_screener, setup_scanner, monster_scout not in active runner.
    # Skip freshness checks for their outputs — files are stale by design, not a pipeline error.
    # Re-enable these blocks when Option B is reactivated (N_closed_trades >= 20, est. Q1-Q2 2027).

    w30 = ROOT / "data/leadership/top30_watchlist.json"
    if w30.exists():
        d30 = _load_json(w30, {})
        n30 = len(d30.get("watchlist", []))
        results.append(_skip("watchlist_fresh",
            f"BOA-022: trend_template_screener not in active runner — {n30} stocks cached"))
    else:
        results.append(_skip("watchlist_fresh",
            "BOA-022: trend_template_screener not in active runner"))

    results.append(_skip("setups_fresh",
        "BOA-022: setup_scanner not in active runner"))

    t10 = ROOT / "data/leadership/top10_active.json"
    results.append(_skip("top10_fresh",
        "BOA-022: trend_template_screener not in active runner"))

    results.append(_skip("screener_fresh",
        "BOA-022: trend_template_screener not in active runner"))

    results.append(_skip("bigshot_fresh",
        "BOA-022: monster_scout not in active runner"))

    return results


def check_regime_ohlcv_consistency() -> list[dict]:
    """Regime date should not lag OHLCV by more than 2 trading days."""
    mh = _load_json(ROOT / "data/regime/market_health.json", {})
    regime_date = str(mh.get("date", ""))[:10]
    if not regime_date:
        return [_skip("regime_ohlcv_consistency", "market_health.json not found")]
    if not OHLCV_DB.exists():
        return [_skip("regime_ohlcv_consistency", "ohlcv.db not found")]
    try:
        with sqlite3.connect(str(OHLCV_DB)) as conn:
            ohlcv_max = conn.execute("SELECT MAX(date) FROM ohlcv").fetchone()[0]
        gap = abs(_days_old(regime_date) - _days_old(ohlcv_max))
        if gap > 3:
            return [_warn("regime_ohlcv_consistency",
                f"regime={regime_date}, ohlcv={ohlcv_max}",
                f"{gap}d gap — regime may use stale prices; re-run market_regime.py after OHLCV update")]
        return [_ok("regime_ohlcv_consistency", f"regime={regime_date} | ohlcv={ohlcv_max} | gap={gap}d")]
    except Exception as e:
        return [_warn("regime_ohlcv_consistency", msg=str(e))]


# ═══════════════════════════════════════════════════════════════════════════════
# ── GROUP 3: Investment Logic Validation ───────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def check_rs_distribution() -> list[dict]:
    """RS percentile distribution should be roughly uniform (avg~50, spread 0-100)."""
    tables = _db_tables()
    if "rs_daily" not in tables:
        return [_skip("rs_distribution", "rs_daily table missing")]
    try:
        with sqlite3.connect(str(OHLCV_DB)) as conn:
            row = conn.execute("""
                SELECT AVG(rs_3m_pct), MIN(rs_3m_pct), MAX(rs_3m_pct),
                       SUM(CASE WHEN rs_3m_pct BETWEEN 45 AND 55 THEN 1 ELSE 0 END)*100.0/COUNT(*),
                       COUNT(*)
                FROM rs_daily WHERE date=(SELECT MAX(date) FROM rs_daily)
            """).fetchone()
        avg_pct, min_pct, max_pct, pct_clustered, n = row

        if pct_clustered and pct_clustered > 30:
            return [_fail("rs_distribution",
                f"avg={avg_pct:.1f} clustered={pct_clustered:.0f}%",
                "RS percentiles collapsed to band 45-55% — benchmark calculation broken")]
        if max_pct is not None and max_pct < 80:
            return [_fail("rs_distribution", f"max={max_pct:.1f}",
                "No stock above 80th percentile — RS calculation likely broken")]
        if min_pct is not None and min_pct > 20:
            return [_fail("rs_distribution", f"min={min_pct:.1f}",
                "No stock below 20th percentile — RS calculation likely broken")]
        return [_ok("rs_distribution",
            f"avg={avg_pct:.1f} | min={min_pct:.1f} | max={max_pct:.1f} | n={n:,} | clustered={pct_clustered:.0f}%")]
    except Exception as e:
        return [_warn("rs_distribution", msg=str(e))]


def check_fundamentals_data() -> list[dict]:
    """Fundamental gate unknown rates should stay below thresholds."""
    results = []
    tables = _db_tables()
    try:
        with sqlite3.connect(str(OHLCV_DB)) as conn:
            # FIX (2026-06-14): Read from fundamentals_summary (fresh, updated daily by
            # stockanalysis_fetcher/pipeline_fundamentals) instead of screening_results
            # (only updated when trend_template_screener writes to SQLite — currently
            # screener writes JSON only, so screening_results can be weeks stale).
            if "fundamentals_summary" in tables:
                row = conn.execute("""
                    SELECT
                      ROUND(100.0*SUM(CASE WHEN gate_eps=-1 THEN 1 ELSE 0 END)/COUNT(*),1),
                      ROUND(100.0*SUM(CASE WHEN gate_rev=-1 THEN 1 ELSE 0 END)/COUNT(*),1),
                      COUNT(*),
                      MAX(last_fetched)
                    FROM fundamentals_summary
                """).fetchone()
                eps_unk, rev_unk, total, last_fetched = row
                src_label = f"fundamentals_summary [{last_fetched}]"
            elif "screening_results" in tables:
                row = conn.execute("""
                    SELECT
                      ROUND(100.0*SUM(CASE WHEN gate_eps=-1 THEN 1 ELSE 0 END)/COUNT(*),1),
                      ROUND(100.0*SUM(CASE WHEN gate_rev=-1 THEN 1 ELSE 0 END)/COUNT(*),1),
                      COUNT(*)
                    FROM screening_results WHERE date=(SELECT MAX(date) FROM screening_results)
                """).fetchone()
                eps_unk, rev_unk, total = row
                src_label = "screening_results (fallback)"
            else:
                return [_skip("gate_unknown_rate", "neither fundamentals_summary nor screening_results exists")]

            # EPS unknown > 50% → gate effectively disabled
            if eps_unk and eps_unk > 50:
                results.append(_fail("gate_unknown_rate",
                    f"eps_unk={eps_unk}% rev_unk={rev_unk}%",
                    f"EPS gate unknown >50% [{src_label}] — run prewarm_analyst_cache.py"))
            elif eps_unk and eps_unk > 25:
                results.append(_warn("gate_unknown_rate",
                    f"eps_unk={eps_unk}% rev_unk={rev_unk}%",
                    f"EPS unknown rate elevated [{src_label}] — run prewarm_analyst_cache.py"))
            else:
                results.append(_ok("gate_unknown_rate",
                    f"eps_unk={eps_unk}% | rev_unk={rev_unk}% | n={total:,} [{src_label}]"))

            # Grade A count sanity — compare vs screening_history baseline.
            # FIX: was hardcoded `= 7`, but pipeline_fundamentals.py may produce up to
            # 8 gate columns (e.g. gate_earnings added later). Use dynamic max so the
            # check doesn't permanently return 0 after a new gate is added.
            # `>= max_gates - 1` = "passed all but at most 1 gate" — still a real leader.
            if "screening_history" in tables:
                row_max = conn.execute("""
                    SELECT MAX(gates_passed) FROM screening_results
                    WHERE date=(SELECT MAX(date) FROM screening_results)
                """).fetchone()
                max_gates = (row_max[0] or 7) if row_max else 7
                gate_threshold = max(max_gates - 1, 6)   # at least 6 — never below 6

                row2 = conn.execute("""
                    SELECT COUNT(*) as cnt
                    FROM screening_results WHERE date=(SELECT MAX(date) FROM screening_results)
                      AND gates_passed >= ?
                """, (gate_threshold,)).fetchone()
                grade_a_today = row2[0] if row2 else 0

                # Baseline from last 5 days (same dynamic threshold)
                rows_hist = conn.execute("""
                    SELECT date, SUM(CASE WHEN gates_passed >= ? THEN 1 ELSE 0 END)
                    FROM screening_results
                    WHERE date < (SELECT MAX(date) FROM screening_results)
                    GROUP BY date ORDER BY date DESC LIMIT 5
                """, (gate_threshold,)).fetchall()
                # Skip sanity check if screening data is stale (health check runs before A06)
                from datetime import date as _date
                max_screening_date = conn.execute(
                    "SELECT MAX(date) FROM screening_results"
                ).fetchone()
                max_sd = (max_screening_date[0] or "") if max_screening_date else ""
                if max_sd != _date.today().isoformat():
                    results.append(_skip("grade_a_sanity",
                        f"screening_results is {max_sd} (stale) — re-check after A06 runs"))
                elif rows_hist:
                    baseline_avg = sum(r[1] for r in rows_hist) / len(rows_hist)
                    regime = _load_json(ROOT / "data/regime/market_health.json", {}).get("regime", "")

                    if baseline_avg > 0 and grade_a_today > baseline_avg * 3:
                        results.append(_fail("grade_a_sanity",
                            f"today={grade_a_today} baseline_avg={baseline_avg:.0f}",
                            "Grade A count 3x above baseline — a gate may have defaulted to always-pass"))
                    elif regime == "Markup" and grade_a_today == 0 and baseline_avg > 5:
                        results.append(_warn("grade_a_sanity",
                            f"today=0 in {regime}",
                            "0 Grade A stocks in Markup regime — gate logic may be broken"))
                    else:
                        results.append(_ok("grade_a_sanity",
                            f"today={grade_a_today} | 5d_avg={baseline_avg:.0f}"))
                else:
                    results.append(_ok("grade_a_sanity", f"today={grade_a_today} (no baseline yet)"))

    except Exception as e:
        results.append(_warn("gate_unknown_rate", msg=str(e)))

    return results


def check_adtv_large_caps() -> list[dict]:
    """AAPL/NVDA/MSFT ADTV should be > $1B — if not, ADTV calculation is broken."""
    tables = _db_tables()
    if "screening_results" not in tables:
        return [_skip("adtv_large_cap", "screening_results table missing")]
    try:
        with sqlite3.connect(str(OHLCV_DB)) as conn:
            rows = conn.execute("""
                SELECT ticker, adtv_6m_usd FROM screening_results
                WHERE date=(SELECT MAX(date) FROM screening_results)
                  AND ticker IN ('AAPL','NVDA','MSFT')
            """).fetchall()
        broken = [f"{t}(${v/1e9:.1f}B)" for t, v in rows if v is None or v < 1e9]
        found_tickers = {r[0] for r in rows}
        missing = {"AAPL", "NVDA", "MSFT"} - found_tickers
        if missing:
            return [_warn("adtv_large_cap", str(missing),
                f"{missing} not in screening_results — possible universe gap")]
        if broken:
            return [_fail("adtv_large_cap", str(broken),
                f"Large caps with ADTV <$1B — ADTV calculation broken: {broken}")]
        summary = " | ".join(f"{t}=${v/1e9:.1f}B" for t, v in rows)
        return [_ok("adtv_large_cap", summary)]
    except Exception as e:
        return [_warn("adtv_large_cap", msg=str(e))]


def check_screening_deduplication() -> list[dict]:
    """Duplicate tickers on same date in screening_results break funnel math."""
    tables = _db_tables()
    if "screening_results" not in tables:
        return [_skip("screening_dedup", "screening_results table missing")]
    try:
        with sqlite3.connect(str(OHLCV_DB)) as conn:
            n_dups = conn.execute("""
                SELECT COUNT(*) FROM (
                    SELECT ticker FROM screening_results
                    WHERE date=(SELECT MAX(date) FROM screening_results)
                    GROUP BY ticker HAVING COUNT(*) > 1
                )
            """).fetchone()[0]
            if n_dups > 0:
                # Auto-fixable: keep MIN(rowid) per ticker+date
                conn.execute("""
                    DELETE FROM screening_results WHERE rowid NOT IN (
                        SELECT MIN(rowid) FROM screening_results GROUP BY ticker, date
                    ) AND date=(SELECT MAX(date) FROM screening_results)
                """)
                conn.commit()
                return [_warn("screening_dedup", f"{n_dups} dupes",
                    f"Removed {n_dups} duplicate rows from screening_results (auto-fixed)")]
            return [_ok("screening_dedup", "no duplicate tickers")]
    except Exception as e:
        return [_warn("screening_dedup", msg=str(e))]


def check_setup_quality() -> list[dict]:
    """Validate setup geometry, RR distribution, and context_only integrity."""
    results = []
    sf = ROOT / "data/setups/setups_today.json"
    if not sf.exists():
        return [_skip("setup_quality", "setups_today.json missing")]

    s = _load_json(sf, {})
    setups = s.get("setups", [])
    if not setups:
        return [_skip("setup_quality", "0 setups — nothing to validate")]

    # Context-only / FIB leak check
    leaks = [f"{x.get('ticker')}({x.get('setup_type')})"
             for x in setups
             if x.get("context_only") or
             (x.get("setup_type") in ("FIB", "EMA", "VPS") and x.get("recommended_size_pct", 1) > 0)]
    if leaks:
        results.append(_fail("setup_integrity",
            f"{len(leaks)} leaks",
            f"Context-only in actionable list: {', '.join(leaks)}"))
    else:
        results.append(_ok("setup_integrity", "no context_only leaks"))

    # Stop/Target geometry — inverted = bad trade signal
    geo_fails = []
    for x in setups:
        pivot  = x.get("pivot", 0) or 0
        stop   = x.get("stop",  0) or 0
        tgt1   = x.get("target_1", 0) or 0
        ticker = x.get("ticker", "?")
        if pivot > 0 and stop >= pivot:
            geo_fails.append(f"{ticker}:stop({stop:.2f})>=pivot({pivot:.2f})")
        if pivot > 0 and tgt1 > 0 and tgt1 <= pivot:
            geo_fails.append(f"{ticker}:tgt1({tgt1:.2f})<=pivot({pivot:.2f})")
    if geo_fails:
        results.append(_fail("setup_stop_logic", f"{len(geo_fails)} bad",
            "Inverted stop/target: " + " | ".join(geo_fails[:3])))
    else:
        results.append(_ok("setup_stop_logic", f"all {len(setups)} setups pass geometry check"))

    # RR variance — all at floor = degenerate target calc
    # Exception: Distribution/Sideways/Markdown — stocks near 52W highs have narrow
    # headroom to 52W target, so floored RR is EXPECTED behavior, not a bug.
    min_rr  = s.get("min_rr", 3.0)
    rr_vals = [x.get("rr_ratio") for x in setups if x.get("rr_ratio") is not None]
    regime  = _load_json(ROOT / "data/regime/market_health.json", {}).get("regime", "Unknown")
    rr_floor_ok_regimes = {"Distribution", "Sideways", "Markdown"}
    if len(rr_vals) >= 3:
        at_floor = sum(1 for r in rr_vals if abs(r - min_rr) < 0.01)
        floor_pct = at_floor / len(rr_vals)
        if floor_pct > 0.7 and regime not in rr_floor_ok_regimes:
            results.append(_warn("setup_rr_variance",
                f"{at_floor}/{len(rr_vals)} at floor",
                f"{at_floor}/{len(rr_vals)} setups at RR={min_rr} floor — target logic suspect (regime={regime})"))
        else:
            results.append(_ok("setup_rr_variance",
                f"{len(rr_vals)} setups | floor={at_floor} | regime={regime}"))
    else:
        results.append(_ok("setup_rr_variance", f"{len(rr_vals)} setups"))

    return results


def check_cross_file_consistency() -> list[dict]:
    """top10_active leaders should exist in rs_universe/latest.json."""
    results = []
    t10 = _load_json(ROOT / "data/leadership/top10_active.json", {})
    rs  = _load_json(ROOT / "data/rs_universe/latest.json", {})
    if not t10 or not rs:
        return [_skip("cross_file_rs", "top10_active or rs_universe missing")]

    top10_tickers = {l["ticker"] for l in t10.get("leaders", [])}
    rs_tickers    = set(rs.get("universe", {}).keys())

    if not top10_tickers:
        return [_skip("cross_file_rs", "top10_active has 0 leaders")]

    missing = top10_tickers - rs_tickers
    t10_date = str(t10.get("date", ""))[:10]
    rs_date  = str(rs.get("date",  ""))[:10]
    date_gap = abs(_days_old(t10_date) - _days_old(rs_date))

    if missing:
        results.append(_warn("cross_file_rs",
            f"{len(missing)} missing",
            f"Top10 tickers not in RS universe: {missing} — A03 and A06 may have run on different dates"))
    elif date_gap > 1:
        results.append(_warn("cross_file_rs",
            f"top10={t10_date} rs={rs_date}",
            f"{date_gap}d date gap between top10_active and rs_universe — RS values may be stale"))
    else:
        results.append(_ok("cross_file_rs",
            f"all {len(top10_tickers)} top10 tickers in rs_universe | dates aligned"))

    return results


def check_stage2_regime() -> list[dict]:
    """Stage2 should pass >0 stocks unless in Markdown regime."""
    tables = _db_tables()
    if "screening_results" not in tables:
        return [_skip("stage2_regime", "screening_results missing")]
    mh = _load_json(ROOT / "data/regime/market_health.json", {})
    regime = mh.get("regime", "")
    try:
        with sqlite3.connect(str(OHLCV_DB)) as conn:
            row = conn.execute("""
                SELECT SUM(gate_stage2), COUNT(*) FROM screening_results
                WHERE date=(SELECT MAX(date) FROM screening_results)
            """).fetchone()
        stage2_pass = row[0] or 0
        if regime in ("Markup",) and stage2_pass == 0:
            return [_fail("stage2_regime", f"0 in {regime}",
                "0 stocks pass Stage2 in Markup regime — MA data stale or SEPA logic broken")]
        if stage2_pass < 10 and regime != "Markdown":
            return [_warn("stage2_regime", f"{stage2_pass} in {regime}",
                f"Only {stage2_pass} stocks pass Stage2 — MA calculation may be degraded")]
        return [_ok("stage2_regime", f"{stage2_pass} pass Stage2 | regime={regime}")]
    except Exception as e:
        return [_warn("stage2_regime", msg=str(e))]


# ═══════════════════════════════════════════════════════════════════════════════
# ── Main check runner ──────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def _run_all_checks() -> dict:
    """Run every check group. Return raw report dict (no print, no write)."""
    all_checks: list[dict] = []

    all_checks += check_ohlcv_db()
    all_checks += check_db_schema()
    all_checks += check_api_keys()
    all_checks += check_imports()
    all_checks += check_runner_log()
    all_checks += check_critical_files()
    all_checks += check_data_freshness()
    all_checks += [check_regime_ohlcv_consistency()]
    all_checks += check_rs_distribution()
    all_checks += check_fundamentals_data()
    all_checks += check_adtv_large_caps()
    all_checks += check_screening_deduplication()
    all_checks += check_setup_quality()
    all_checks += check_cross_file_consistency()
    all_checks += check_stage2_regime()

    # Flatten any accidentally nested lists
    flat: list[dict] = []
    for c in all_checks:
        if isinstance(c, list):
            flat.extend(c)
        else:
            flat.append(c)

    fail  = sum(1 for c in flat if c["status"] == "FAIL")
    warn  = sum(1 for c in flat if c["status"] == "WARN")
    ok    = sum(1 for c in flat if c["status"] == "OK")
    skip  = sum(1 for c in flat if c["status"] == "SKIP")

    overall = "FAIL" if fail > 0 else ("WARN" if warn > 0 else "PASS")

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "overall":      overall,
        "fail_count":   fail,
        "warn_count":   warn,
        "ok_count":     ok,
        "skip_count":   skip,
        "total_checks": len(flat),
        "checks":       flat,
    }


def _write_report(report: dict) -> None:
    REPORT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def _print_report(report: dict) -> None:
    overall = report.get("overall", "?")
    gen_at  = report.get("generated_at", "")[:16]
    fail_c  = report.get("fail_count", 0)
    warn_c  = report.get("warn_count", 0)
    ok_c    = report.get("ok_count", 0)
    skip_c  = report.get("skip_count", 0)
    sym     = {"PASS": "[OK]", "WARN": "[!]", "FAIL": "[FAIL]"}.get(overall, "?")

    print(f"\n[System Health] {sym} {overall} | {ok_c} OK, {warn_c} WARN, {fail_c} FAIL, {skip_c} SKIP | {gen_at}")
    for c in report.get("checks", []):
        if c["status"] in ("OK", "SKIP"):
            continue
        sym2 = {"WARN": "[!]", "FAIL": "[FAIL]"}.get(c["status"], "?")
        val  = f" ({c['value']})" if c.get("value") else ""
        msg  = f" — {c['msg']}" if c.get("msg") else ""
        print(f"  {sym2} {c['id']}{val}{msg}")
    if fail_c == 0 and warn_c == 0:
        print("  All checks passed — pipeline ready")


# ═══════════════════════════════════════════════════════════════════════════════
# ── Auto-Healer ────────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

# Map: check_id → (script_module_path, func_name, speed)
# speed: "fast"  = safe to run pre-market (< 60s)
#        "slow"  = only in EOD mode (> 60s)
#        None    = NOT_FIXABLE (requires human)

HEAL_MAP: dict[str, tuple[str, str, str]] = {
    # ── RULE: Only lightweight prerequisite scripts belong here.
    # Heavy curation scripts (trend_template_screener, monster_scout, setup_scanner)
    # all run as proper pipeline steps (a06/a07/a08). Running them via importlib in
    # the heal phase hangs the entire pipeline for 20+ minutes with no output.
    # ── Regime prereqs (fast, needed before pipeline steps read these files) ──
    "market_health":    ("scripts.pre_compute.market_regime",           "run", "fast"),
    "macro_fresh":      ("scripts.pre_compute.macro_monitor",           "run", "fast"),
    "breadth_fresh":    ("scripts.pre_compute.fetch_market_breadth",    "run", "fast"),
    "schema_earnings":  ("scripts.pre_compute.fetch_earnings_calendar", "run", "fast"),
    # ── Data quality fixes (slow — EOD only) ───────────────────────────────
    "ohlcv_fresh":      ("scripts.pre_compute.update_ohlcv_daily",     "run", "slow"),
    "ohlcv_spy_qqq":    ("scripts.pre_compute.update_ohlcv_daily",     "run", "slow"),
    "schema_ticker_meta":("scripts.pre_compute.pipeline_metrics",      "run", "slow"),
    # ── Not fixable automatically ───────────────────────────────────────────
    # watchlist_fresh, setups_fresh, bigshot_fresh, top10_fresh, screener_fresh,
    # critical_files → all handled by main pipeline steps a06/a07/a08
    # api_keys, python_imports, ohlcv_db, schema_fundamentals, schema_screening,
    # rs_distribution, setup_stop_logic, adtv_large_cap, etc.
}

def _call_module_func(module_dotted: str, func_name: str) -> bool:
    """Import module from file path and call func_name(). Return True on success."""
    # Convert dotted path to file path
    rel_path = module_dotted.replace(".", "/") + ".py"
    script_path = ROOT / rel_path
    if not script_path.exists():
        print(f"    [heal-skip] {module_dotted}: script not found")
        return False
    try:
        spec = importlib.util.spec_from_file_location(f"_heal_{module_dotted}", str(script_path))
        mod  = importlib.util.module_from_spec(spec)
        # Load .env before calling
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
        spec.loader.exec_module(mod)
        fn = getattr(mod, func_name, None)
        if fn is None:
            print(f"    [heal-skip] {module_dotted}.{func_name}() not found")
            return False
        result = fn()
        return True
    except Exception as e:
        print(f"    [heal-error] {module_dotted}.{func_name}(): {e}")
        return False


def _attempt_heals(report: dict, mode: str = "premarket") -> bool:
    """
    For each failed/warned check with a HEAL_MAP entry, run the fix.
    Returns True if at least one fix was attempted.
    Only runs 'slow' fixes in EOD mode.
    """
    attempted = set()
    healed_any = False

    for check in report.get("checks", []):
        if check["status"] not in ("FAIL", "WARN"):
            continue
        cid = check["id"]
        heal = HEAL_MAP.get(cid)
        if not heal:
            continue
        module_path, func_name, speed = heal

        # Don't run slow heals in premarket (would delay daily brief past 9 AM)
        if speed == "slow" and mode == "premarket":
            continue

        # Deduplicate — don't run same script twice per heal pass
        key = (module_path, func_name)
        if key in attempted:
            continue
        attempted.add(key)

        print(f"    [heal] {cid} → running {module_path}.{func_name}()...")
        ok = _call_module_func(module_path, func_name)
        if ok:
            healed_any = True

    return healed_any


# ═══════════════════════════════════════════════════════════════════════════════
# ── Public entry points ────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def run() -> dict:
    """Single-pass check. Always prints + writes report. Used for standalone runs."""
    print("  [HealthCheck] Running diagnostics...")
    report = _run_all_checks()
    _write_report(report)
    sym = {"PASS": "[OK]", "WARN": "[!]", "FAIL": "[FAIL]"}.get(report["overall"], "?")
    print(f"  [HealthCheck] {sym} {report['overall']} — "
          f"{report['ok_count']} OK, {report['warn_count']} WARN, {report['fail_count']} FAIL")
    for c in report["checks"]:
        if c["status"] == "FAIL":
            print(f"    [FAIL] {c['id']}: {c['msg']}")
    for c in report["checks"]:
        if c["status"] == "WARN":
            print(f"    [!]   {c['id']}: {c['msg']}")
    return report


def run_with_heal(max_passes: int = 2, mode: str = "premarket") -> dict:
    """
    AUTO-HEALING entry point (used by pre_market_runner.py).

    Behaviour:
      Pass 1: run all checks silently.
        → If PASS: write report, print results, done.
        → If FAIL/WARN: attempt fixes silently (no output during heal).
          → If nothing fixable: break, surface current results.
          → If fixed something: go to Pass 2.
      Pass 2: re-run all checks.
        → Write report, print results regardless of outcome.

    The user sees output ONLY after the final state — healed or not.

    FIX: old code always called _run_all_checks() one extra time after the loop,
    causing 3 full check runs when max_passes=2 and nothing was healable (the
    "break early" path already had a valid final report but discarded it).
    Now: only run a final re-check when heals were attempted in the last pass
    (need to see their effect); reuse the last report when break was early.
    """
    last_report: dict | None = None
    need_final_recheck = False

    for attempt in range(max_passes):
        report = _run_all_checks()
        last_report = report
        need_final_recheck = False   # fresh check just ran — nothing pending

        if report["overall"] == "PASS":
            _write_report(report)
            _print_report(report)
            return report

        # Not PASS — try to heal (silent)
        healed_any = _attempt_heals(report, mode=mode)

        if not healed_any:
            # Nothing auto-fixable — current report IS the final state
            break

        # Heals were attempted: next loop pass will re-check their effect.
        # If this was the last allowed pass, flag that we need one more re-check.
        need_final_recheck = True

    if need_final_recheck:
        # Heals ran on the last pass — run one final check to reflect their outcome
        last_report = _run_all_checks()

    _write_report(last_report)
    _print_report(last_report)
    return last_report


def print_report() -> None:
    """Print the last saved health report (used by session_start)."""
    if not REPORT_FILE.exists():
        print("  [HealthCheck] No report — run: python scripts/diagnostics/health_check.py")
        return
    _print_report(_load_json(REPORT_FILE, {}))


# ═══════════════════════════════════════════════════════════════════════════════
# ── Update pre_market_runner.py to use run_with_heal ──────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    import argparse
    p = argparse.ArgumentParser(description="AlphaAbsolute Health Check")
    p.add_argument("--report",     action="store_true", help="Print last saved report only")
    p.add_argument("--heal",       action="store_true", help="Run with auto-healing (default)")
    p.add_argument("--no-heal",    action="store_true", help="Single pass, no healing")
    p.add_argument("--mode",       default="premarket", choices=["premarket","eod","monthly"])
    args = p.parse_args()

    if args.report:
        print_report()
    elif args.no_heal:
        run()
    else:
        run_with_heal(max_passes=2, mode=args.mode)
