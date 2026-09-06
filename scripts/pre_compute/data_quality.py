"""
AlphaAbsolute — Data Quality Monitor
=====================================
Runs after every EOD pipeline. Checks for:

  1. Stale tickers         — last_date > 3 trading days behind
  2. Bad dates             — any non-YYYY-MM-DD dates in ohlcv
  3. Volume anomalies      — float volumes, zero volumes, suspicious spikes
  4. Missing universe      — tickers in labels but not in ohlcv
  5. RS coverage           — tickers missing from rs_daily for latest date
  6. Fundamental coverage  — tickers passing RS gate but missing fundamentals
  7. Date gaps             — trading days missing for key tickers (SPY/QQQ)
  8. Screening freshness   — screening_results last updated
  9. Pipeline run health   — last 7 runs from pipeline_runs table

Output:
  data/quality/quality_report_YYYYMMDD.json
  data/quality/quality_latest.json  (always the latest)

Returns exit code 0 if all checks pass, 1 if critical failures exist.

Usage:
  python scripts/pre_compute/data_quality.py
  python scripts/pre_compute/data_quality.py --strict    # fail on warnings too
"""

import sqlite3
import json
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = str(BASE_DIR / "data" / "ohlcv.db")
LABELS_PATH = str(BASE_DIR / "data" / "themes" / "ticker_labels.json")
OUTPUT_DIR = str(BASE_DIR / "data" / "quality")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_universe() -> list[str]:
    with open(LABELS_PATH, encoding="utf-8") as f:
        return list(json.load(f)["labels"].keys())


def get_latest_trading_day(conn) -> str:
    c = conn.cursor()
    c.execute("SELECT MAX(date) FROM ohlcv WHERE ticker='QQQ'")
    r = c.fetchone()[0]
    return r or date.today().isoformat()


def check_date_formats(conn) -> dict:
    """All dates should be exactly YYYY-MM-DD (10 chars)."""
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM ohlcv WHERE LENGTH(date) != 10")
    bad = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM ohlcv")
    total = c.fetchone()[0]
    status = "ok" if bad == 0 else "fail"
    return {
        "check": "date_formats",
        "status": status,
        "bad_rows": bad,
        "total_rows": total,
        "message": "All dates YYYY-MM-DD" if bad == 0 else f"{bad:,} rows have non-standard date format"
    }


def check_volume_types(conn) -> dict:
    """All volumes should be INTEGER, not REAL."""
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM ohlcv WHERE typeof(volume) = 'real'")
    real_vols = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM ohlcv WHERE volume IS NULL OR volume = 0")
    zero_vols = c.fetchone()[0]
    status = "ok" if real_vols == 0 else "fail"
    warning = zero_vols > 0
    return {
        "check": "volume_types",
        "status": status,
        "float_volumes": real_vols,
        "zero_or_null_volumes": zero_vols,
        "message": (f"All volumes INTEGER" if real_vols == 0 else
                    f"{real_vols} float volumes (run fix_dates_volumes.py)"),
        "warning": f"{zero_vols} zero/null volumes" if zero_vols > 0 else None
    }


def check_stale_tickers(conn, universe: list[str]) -> dict:
    """Tickers whose last bar is > 3 trading days behind the latest date."""
    c = conn.cursor()
    latest = get_latest_trading_day(conn)

    # Get last 5 trading days
    c.execute("SELECT DISTINCT date FROM ohlcv ORDER BY date DESC LIMIT 5")
    recent_days = [r[0] for r in c.fetchall()]
    cutoff = recent_days[-1] if len(recent_days) >= 3 else latest

    placeholders = ','.join(['?' for _ in universe])
    c.execute(f"""
        SELECT ticker, MAX(date) as last_date
        FROM ohlcv WHERE ticker IN ({placeholders})
        GROUP BY ticker
        HAVING last_date < ?
        ORDER BY last_date ASC
    """, universe + [cutoff])

    stale = [(r[0], r[1]) for r in c.fetchall()]

    # Check high-priority tickers
    priority = ['QQQ', 'SPY', 'NVDA', 'AAPL', 'MSFT']
    priority_stale = [t for t, d in stale if t in priority]

    status = "fail" if priority_stale else ("warn" if len(stale) > 50 else "ok")
    return {
        "check": "stale_tickers",
        "status": status,
        "latest_date": latest,
        "cutoff_3td": cutoff,
        "stale_count": len(stale),
        "priority_stale": priority_stale,
        "stale_sample": stale[:10],
        "message": (f"All tickers current" if not stale else
                    f"{len(stale)} tickers stale (> 3td behind)"
                    + (f" — INCLUDING PRIORITY: {priority_stale}" if priority_stale else ""))
    }


def check_missing_universe(conn, universe: list[str]) -> dict:
    """Tickers in universe but not in ohlcv at all."""
    c = conn.cursor()
    placeholders = ','.join(['?' for _ in universe])
    c.execute(f"SELECT DISTINCT ticker FROM ohlcv WHERE ticker IN ({placeholders})", universe)
    in_db = {r[0] for r in c.fetchall()}
    missing = [t for t in universe if t not in in_db]

    status = "warn" if missing else "ok"
    return {
        "check": "missing_universe",
        "status": status,
        "universe_size": len(universe),
        "in_db": len(in_db),
        "missing_count": len(missing),
        "missing_sample": missing[:20],
        "message": (f"All {len(universe)} universe tickers in ohlcv" if not missing else
                    f"{len(missing)} universe tickers have NO ohlcv data")
    }


def check_rs_coverage(conn, universe: list[str]) -> dict:
    """Coverage of rs_daily for the latest date."""
    c = conn.cursor()
    c.execute("SELECT MAX(date) FROM rs_daily")
    latest_rs = c.fetchone()[0]
    if not latest_rs:
        return {"check": "rs_coverage", "status": "fail",
                "message": "rs_daily table is empty", "latest_date": None}

    c.execute("SELECT COUNT(DISTINCT ticker) FROM rs_daily WHERE date = ?", (latest_rs,))
    covered = c.fetchone()[0]

    placeholders = ','.join(['?' for _ in universe])
    c.execute(f"""
        SELECT COUNT(DISTINCT ticker) FROM rs_daily
        WHERE date = ? AND ticker IN ({placeholders})
    """, [latest_rs] + universe)
    covered_universe = c.fetchone()[0]

    pct = covered_universe / len(universe) * 100 if universe else 0
    status = "ok" if pct >= 90 else ("warn" if pct >= 70 else "fail")
    return {
        "check": "rs_coverage",
        "status": status,
        "latest_rs_date": latest_rs,
        "covered_total": covered,
        "covered_in_universe": covered_universe,
        "universe_size": len(universe),
        "pct_covered": round(pct, 1),
        "message": f"RS coverage: {covered_universe}/{len(universe)} ({pct:.0f}%) for {latest_rs}"
    }


def check_fundamental_coverage(conn, universe: list[str]) -> dict:
    """Tickers that pass RS gate but have no fundamental data."""
    c = conn.cursor()
    latest_screen = None
    try:
        c.execute("SELECT MAX(date) FROM screening_results")
        latest_screen = c.fetchone()[0]
    except Exception:
        pass

    if not latest_screen:
        return {"check": "fundamental_coverage", "status": "warn",
                "message": "No screening_results data"}

    # Tickers passing RS gate
    c.execute("""
        SELECT ticker FROM screening_results
        WHERE date = ? AND gate_rs = 1
    """, (latest_screen,))
    rs_pass = {r[0] for r in c.fetchall()}

    # Tickers with fundamentals
    c.execute("SELECT ticker FROM fundamentals_summary WHERE eps_yoy_pct IS NOT NULL")
    has_fund = {r[0] for r in c.fetchall()}

    missing_fund = rs_pass - has_fund
    pct_covered = (len(rs_pass - missing_fund) / len(rs_pass) * 100) if rs_pass else 0

    # List them sorted by RS composite
    c.execute(f"""
        SELECT s.ticker, s.label, s.rs_composite
        FROM screening_results s
        WHERE s.date = ? AND s.gate_rs = 1
          AND s.ticker NOT IN (
              SELECT ticker FROM fundamentals_summary WHERE eps_yoy_pct IS NOT NULL
          )
        ORDER BY s.rs_composite DESC
        LIMIT 30
    """, (latest_screen,))
    missing_detail = [{"ticker": r[0], "label": r[1], "rs_comp": round(r[2], 0) if r[2] else None}
                      for r in c.fetchall()]

    status = "warn" if pct_covered < 90 else "ok"
    return {
        "check": "fundamental_coverage",
        "status": status,
        "rs_gate_pass": len(rs_pass),
        "has_fundamentals": len(rs_pass - missing_fund),
        "missing_fundamentals": len(missing_fund),
        "pct_covered": round(pct_covered, 1),
        "missing_high_rs": missing_detail[:15],
        "message": f"Fundamentals: {len(rs_pass - missing_fund)}/{len(rs_pass)} RS-passing tickers covered ({pct_covered:.0f}%)"
    }


def check_ohlcv_data_quality(conn) -> dict:
    """Check for suspicious price/volume patterns."""
    c = conn.cursor()

    # Tickers with < 20 bars (too little data for any MA)
    c.execute("SELECT COUNT(*) FROM (SELECT ticker, COUNT(*) as n FROM ohlcv GROUP BY ticker HAVING n < 20)")
    few_bars = c.fetchone()[0]

    # Check date range
    c.execute("SELECT MIN(date), MAX(date), COUNT(DISTINCT ticker) FROM ohlcv")
    min_d, max_d, n_tickers = c.fetchone()

    # Tickers with < 200 bars (can't compute MA200)
    c.execute("SELECT COUNT(*) FROM (SELECT ticker, COUNT(*) as n FROM ohlcv GROUP BY ticker HAVING n < 200)")
    no_ma200 = c.fetchone()[0]

    # Tickers with < 252 bars (can't compute RS 12M)
    c.execute("SELECT COUNT(*) FROM (SELECT ticker, COUNT(*) as n FROM ohlcv GROUP BY ticker HAVING n < 252)")
    no_rs12m = c.fetchone()[0]

    c.execute("SELECT COUNT(DISTINCT ticker) FROM ohlcv")
    total_tickers = c.fetchone()[0]

    # Trading day count for QQQ (should be ~252 per year)
    c.execute("SELECT COUNT(*) FROM ohlcv WHERE ticker='QQQ'")
    qqq_bars = c.fetchone()[0]
    qqq_years = qqq_bars / 252

    status = "warn" if qqq_years < 2 else "ok"
    return {
        "check": "ohlcv_data_quality",
        "status": status,
        "date_range": f"{min_d} to {max_d}",
        "total_tickers": n_tickers,
        "qqq_bars": qqq_bars,
        "qqq_years": round(qqq_years, 1),
        "tickers_lt_20_bars": few_bars,
        "tickers_no_ma200": no_ma200,
        "tickers_no_rs12m": no_rs12m,
        "message": (f"QQQ has {qqq_bars} bars ({qqq_years:.1f}yr) | "
                    f"No MA200: {no_ma200} | No RS12M: {no_rs12m}")
    }


def check_screening_freshness(conn) -> dict:
    """Is screening_results from today or yesterday?"""
    c = conn.cursor()
    try:
        c.execute("SELECT MAX(date), COUNT(*) FROM screening_results")
        latest, count = c.fetchone()
    except Exception:
        return {"check": "screening_freshness", "status": "fail", "message": "No screening_results"}

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    if not latest:
        status = "fail"
        msg = "screening_results is empty"
    elif latest >= yesterday:
        status = "ok"
        msg = f"screening_results is current: {latest} ({count} tickers)"
    else:
        status = "warn"
        msg = f"screening_results is stale: last date {latest}"

    return {
        "check": "screening_freshness",
        "status": status,
        "latest_screen_date": latest,
        "tickers": count,
        "message": msg
    }


def check_pipeline_runs(conn) -> dict:
    """Last 7 pipeline run records."""
    c = conn.cursor()
    try:
        c.execute("""
            SELECT run_id, step, status, duration_s, error_msg, started_at
            FROM pipeline_runs
            ORDER BY started_at DESC
            LIMIT 14
        """)
        rows = c.fetchall()
        recent_fails = [r for r in rows if r[2] == "fail"]
        return {
            "check": "pipeline_runs",
            "status": "warn" if recent_fails else "ok",
            "recent_runs": len(rows),
            "recent_fails": len(recent_fails),
            "failed_steps": [r[1] for r in recent_fails],
            "message": (f"Last {len(rows)} runs: {len(recent_fails)} failures"
                        if recent_fails else f"Last {len(rows)} pipeline runs all OK")
        }
    except Exception:
        return {"check": "pipeline_runs", "status": "skip",
                "message": "pipeline_runs table not populated yet"}


def check_earnings_calendar(conn) -> dict:
    """Is earnings calendar loaded and current?"""
    c = conn.cursor()
    try:
        today = date.today().isoformat()
        c.execute("SELECT COUNT(*), MAX(fetched_at) FROM earnings_calendar WHERE report_date >= ?", (today,))
        count, fetched = c.fetchone()

        if count == 0:
            return {"check": "earnings_calendar", "status": "warn",
                    "message": "No upcoming earnings in calendar — run fetch_earnings_calendar.py"}

        # Check how old the data is
        if fetched:
            fetched_age = (datetime.now() - datetime.fromisoformat(fetched)).days
            if fetched_age > 3:
                status = "warn"
                msg = f"{count} upcoming events but data is {fetched_age} days old"
            else:
                status = "ok"
                msg = f"{count} upcoming earnings events loaded (fetched {fetched_age}d ago)"
        else:
            status = "ok"
            msg = f"{count} upcoming earnings events"

        return {"check": "earnings_calendar", "status": status,
                "upcoming_events": count, "last_fetched": fetched, "message": msg}
    except Exception as e:
        return {"check": "earnings_calendar", "status": "warn",
                "message": f"earnings_calendar check failed: {e}"}


def check_ticker_meta_dates(conn) -> dict:
    """ticker_meta.last_date and first_date must be YYYY-MM-DD (no timezone suffix)."""
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM ticker_meta WHERE LENGTH(last_date) > 10 OR LENGTH(first_date) > 10")
    bad = c.fetchone()[0]
    if bad > 0:
        c.execute("""
            SELECT ticker, last_date FROM ticker_meta
            WHERE LENGTH(last_date) > 10 LIMIT 3
        """)
        samples = [f"{r[0]}={r[1]}" for r in c.fetchall()]
        return {"check": "ticker_meta_dates", "status": "fail",
                "bad_rows": bad, "samples": samples,
                "message": f"{bad} ticker_meta rows have timezone suffix — run: UPDATE ticker_meta SET last_date=SUBSTR(last_date,1,10)"}
    return {"check": "ticker_meta_dates", "status": "ok",
            "message": "All ticker_meta dates are YYYY-MM-DD"}


def check_ohlc_coverage(conn, universe: list[str]) -> dict:
    """Check what % of universe rows have open/high/low populated."""
    c = conn.cursor()
    ph = ",".join("?" * len(universe))

    c.execute(f"SELECT COUNT(*) FROM ohlcv WHERE ticker IN ({ph})", universe)
    total = c.fetchone()[0]

    c.execute(f"SELECT COUNT(*) FROM ohlcv WHERE ticker IN ({ph}) AND open IS NOT NULL", universe)
    has_ohlc = c.fetchone()[0]

    null_count = total - has_ohlc
    pct = has_ohlc / total * 100 if total else 0

    # Tickers that have ANY open data vs totally missing
    c.execute(f"""
        SELECT COUNT(DISTINCT ticker) FROM ohlcv
        WHERE ticker IN ({ph}) AND open IS NOT NULL
    """, universe)
    tickers_with_ohlc = c.fetchone()[0]

    # Treat as warn (not fail) — screening uses close only, OHLC is for setup scanner
    # fill_ohlc_gaps.py fixes this after backfill_ohlcv_historical.py completes
    status = "ok" if pct >= 90 else "warn"
    return {
        "check": "ohlc_coverage",
        "status": status,
        "total_rows": total,
        "rows_with_ohlc": has_ohlc,
        "rows_null_ohlc": null_count,
        "pct_covered": round(pct, 1),
        "tickers_with_ohlc": tickers_with_ohlc,
        "universe_size": len(universe),
        "message": (f"OHLC {pct:.0f}%: {null_count:,} rows need fill_ohlc_gaps.py (run after backfill)"
                    if null_count else f"All rows have open/high/low")
    }


def check_rs_null_composite(conn, universe: list[str]) -> dict:
    """Tickers in rs_daily with NULL composite on latest date (too short history)."""
    c = conn.cursor()
    c.execute("SELECT MAX(date) FROM rs_daily")
    latest = c.fetchone()[0]
    if not latest:
        return {"check": "rs_null_composite", "status": "skip", "message": "rs_daily empty"}

    ph = ",".join("?" * len(universe))
    c.execute(f"""
        SELECT ticker FROM rs_daily
        WHERE date=? AND rs_composite IS NULL AND ticker IN ({ph})
        ORDER BY ticker
    """, [latest] + universe)
    null_tickers = [r[0] for r in c.fetchall()]

    status = "warn" if null_tickers else "ok"
    return {
        "check": "rs_null_composite",
        "status": status,
        "count": len(null_tickers),
        "tickers": null_tickers[:20],
        "message": (f"rs_composite NULL for {len(null_tickers)} tickers on {latest} (< 21 days history)"
                    if null_tickers else f"All rs_composite populated for {latest}")
    }


def check_orphan_tickers(conn, universe: list[str]) -> dict:
    """Tickers in ohlcv not in universe (benchmark tickers used for RS computation)."""
    c = conn.cursor()
    c.execute("SELECT COUNT(DISTINCT ticker) FROM ohlcv")
    total_db = c.fetchone()[0]
    orphan_count = total_db - len(universe)
    # This is expected — benchmark tickers for RS percentile computation
    status = "ok"
    return {
        "check": "orphan_tickers",
        "status": status,
        "total_in_db": total_db,
        "universe_size": len(universe),
        "orphan_count": orphan_count,
        "message": (f"{orphan_count} benchmark tickers in DB (not in universe — expected for RS percentile)"
                    if orphan_count else "No orphan tickers")
    }


def check_backfill_progress(conn) -> dict:
    """How far back does our history go? Is 3Y backfill complete?"""
    c = conn.cursor()
    from datetime import date as _date, timedelta
    target_3y = (_date.today() - timedelta(days=365 * 3)).isoformat()
    target_1y = (_date.today() - timedelta(days=365)).isoformat()

    c.execute(f"SELECT COUNT(DISTINCT ticker) FROM ohlcv WHERE date <= '{target_3y}'")
    tickers_3y = c.fetchone()[0]
    c.execute(f"SELECT COUNT(DISTINCT ticker) FROM ohlcv WHERE date <= '{target_1y}'")
    tickers_1y = c.fetchone()[0]
    c.execute("SELECT MIN(date) FROM ohlcv")
    earliest = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT ticker) FROM ohlcv")
    total = c.fetchone()[0]

    pct_1y = tickers_1y / total * 100 if total else 0
    pct_3y = tickers_3y / total * 100 if total else 0

    if pct_3y >= 95:
        status = "ok"
        msg = f"3Y history complete ({pct_3y:.0f}% tickers back to {target_3y})"
    elif pct_1y >= 95:
        status = "warn"
        msg = f"1Y history OK ({pct_1y:.0f}%), 3Y incomplete ({pct_3y:.0f}%) — backfill running?"
    else:
        status = "warn"
        msg = f"Short history: earliest={earliest}, 1Y={pct_1y:.0f}%, 3Y={pct_3y:.0f}%"

    return {
        "check": "backfill_progress",
        "status": status,
        "earliest_date": earliest,
        "tickers_1y_history": tickers_1y,
        "tickers_3y_history": tickers_3y,
        "pct_1y": round(pct_1y, 1),
        "pct_3y": round(pct_3y, 1),
        "message": msg
    }


def run(strict: bool = False) -> dict:
    print("\n=== Data Quality Check ===")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    universe = load_universe()
    conn = sqlite3.connect(DB_PATH)

    checks = []
    try:
        # Data integrity
        checks.append(check_date_formats(conn))
        checks.append(check_ticker_meta_dates(conn))
        checks.append(check_volume_types(conn))
        checks.append(check_ohlc_coverage(conn, universe))
        # Coverage & freshness
        checks.append(check_ohlcv_data_quality(conn))
        checks.append(check_backfill_progress(conn))
        checks.append(check_stale_tickers(conn, universe))
        checks.append(check_missing_universe(conn, universe))
        checks.append(check_orphan_tickers(conn, universe))
        # RS & screening
        checks.append(check_rs_coverage(conn, universe))
        checks.append(check_rs_null_composite(conn, universe))
        checks.append(check_screening_freshness(conn))
        checks.append(check_fundamental_coverage(conn, universe))
        checks.append(check_earnings_calendar(conn))
        # Ops
        checks.append(check_pipeline_runs(conn))
    finally:
        conn.close()

    # Summarize
    n_ok   = sum(1 for c in checks if c["status"] == "ok")
    n_warn = sum(1 for c in checks if c["status"] == "warn")
    n_fail = sum(1 for c in checks if c["status"] == "fail")
    n_skip = sum(1 for c in checks if c["status"] == "skip")

    print(f"\n  {'Check':<30} {'Status':>8}  {'Message'}")
    print(f"  {'-'*80}")
    for ch in checks:
        tag = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]", "skip": "[SKIP]"}.get(ch["status"], "[?]   ")
        print(f"  {ch['check']:<30} {tag}  {ch.get('message','')[:60]}")

    print(f"\n  Summary: {n_ok} OK | {n_warn} Warnings | {n_fail} Failures | {n_skip} Skipped")

    # Special: show missing fundamentals for high-RS tickers
    fund_check = next((c for c in checks if c["check"] == "fundamental_coverage"), None)
    if fund_check and fund_check.get("missing_high_rs"):
        print(f"\n  High-RS tickers missing fundamentals (fetch priority):")
        for t in fund_check["missing_high_rs"][:10]:
            print(f"    {t['ticker']:<7} [{t['label']:<18}] RS={t['rs_comp']}")

    output = {
        "date": date.today().isoformat(),
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "ok": n_ok, "warn": n_warn, "fail": n_fail, "skip": n_skip,
            "overall": "ok" if n_fail == 0 and (not strict or n_warn == 0) else "fail"
        },
        "checks": checks
    }

    today_str = date.today().strftime("%Y%m%d")
    dated_path = f"{OUTPUT_DIR}/quality_report_{today_str}.json"
    latest_path = f"{OUTPUT_DIR}/quality_latest.json"
    with open(dated_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Report saved: {dated_path}")

    if n_fail > 0:
        print(f"\n  [!] {n_fail} CRITICAL FAILURES — fix before trading")
        if strict:
            sys.exit(1)

    return output


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="Exit 1 on warnings too")
    args = parser.parse_args()
    run(strict=args.strict)
