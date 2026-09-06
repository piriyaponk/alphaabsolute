"""
AlphaAbsolute — Earnings Calendar Fetcher
==========================================
Fetches upcoming earnings dates for the next 45 days and stores in SQLite.
Used by screening gates to enforce: no new entry within 5 trading days of earnings.

Sources (in order):
  1. FMP earnings_calendar endpoint (bulk, single call covers all tickers)
  2. FMP individual ticker endpoint as fallback for high-priority tickers

Output:
  - SQLite: earnings_calendar table
  - data/regime/earnings_next30.json  (quick lookup for gate checks)

Usage:
  python scripts/pre_compute/fetch_earnings_calendar.py
  python scripts/pre_compute/fetch_earnings_calendar.py --days 60
  python scripts/pre_compute/fetch_earnings_calendar.py --tickers NVDA AAPL MSFT
"""

import sqlite3
import json
import os
import sys
import time
import requests
import argparse
import urllib3
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

urllib3.disable_warnings()
load_dotenv()

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(_BASE, "data", "ohlcv.db")
LABELS_PATH = os.path.join(_BASE, "data", "themes", "ticker_labels.json")
OUTPUT_DIR = os.path.join(_BASE, "data", "regime")
# Guard: only call makedirs if the directory doesn't already exist.
# PermissionError on makedirs(..., exist_ok=True) is a Windows/OneDrive bug where
# accessing the parent briefly raises Access Denied even when the dir exists fine.
# Checking existence first avoids the syscall entirely when the dir is already there.
if not os.path.isdir(OUTPUT_DIR):
    for _attempt in range(5):
        try:
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            break
        except PermissionError:
            if _attempt == 4:
                raise
            time.sleep(2)

FMP_KEY = os.getenv("FMP_API_KEY", "")
SLEEP = 0.25


FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "")


def get_universe():
    with open(LABELS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return list(data["labels"].keys())


def _ensure_table(conn):
    """Create earnings_calendar table if it doesn't exist (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS earnings_calendar (
            ticker           TEXT NOT NULL,
            report_date      TEXT NOT NULL,
            fiscal_period    TEXT,
            eps_estimate     REAL,
            revenue_estimate REAL,
            time_of_day      TEXT,
            fetched_at       TEXT,
            PRIMARY KEY (ticker, report_date)
        )
    """)
    conn.commit()


def fetch_finnhub_bulk_calendar(from_date: str, to_date: str) -> list[dict]:
    """
    Fetch all earnings in a date range from Finnhub (primary source).
    Returns list normalized to internal format with 'symbol' and 'date' keys.
    Finnhub free tier supports earnings calendar endpoint.
    """
    if not FINNHUB_KEY:
        print("  [WARN] FINNHUB_API_KEY not set — skipping Finnhub bulk fetch")
        return []
    url = (f"https://finnhub.io/api/v1/calendar/earnings"
           f"?from={from_date}&to={to_date}&token={FINNHUB_KEY}")
    try:
        r = requests.get(url, timeout=20, verify=False)
        if r.status_code == 429:
            print("  [WARN] Finnhub rate limit — sleeping 30s")
            time.sleep(30)
            r = requests.get(url, timeout=20, verify=False)
        if r.status_code != 200:
            print(f"  [WARN] Finnhub earnings returned {r.status_code}")
            return []
        data = r.json()
        events = data.get("earningsCalendar", []) if isinstance(data, dict) else []
        # Normalize Finnhub format to internal format
        normalized = []
        for ev in events:
            sym = ev.get("symbol") or ev.get("ticker")
            dt  = ev.get("date")
            if sym and dt:
                normalized.append({
                    "symbol":           sym,
                    "date":             dt,
                    "epsEstimated":     ev.get("epsEstimate"),
                    "revenueEstimated": ev.get("revenueEstimate"),
                    "time":             ev.get("hour", "BMO") or "BMO",
                    "period":           f"Q{ev.get('quarter', '')} {ev.get('year', '')}",
                })
        return normalized
    except Exception as e:
        print(f"  [WARN] Finnhub earnings fetch failed: {e}")
        return []


def fetch_fmp_bulk_calendar(from_date: str, to_date: str) -> list[dict]:
    """
    Fetch earnings from FMP (fallback — may require premium plan).
    NOTE: FMP free key (plan < Starter) returns 401 for earning_calendar endpoint.
    Kept as fallback in case key is upgraded.
    """
    if not FMP_KEY:
        return []
    url = (f"https://financialmodelingprep.com/api/v3/earning_calendar"
           f"?from={from_date}&to={to_date}&apiKey={FMP_KEY}")
    try:
        r = requests.get(url, timeout=20, verify=False)
        if r.status_code == 401:
            print("  [WARN] FMP key invalid/expired (401) — earnings endpoint may require premium plan")
            return []
        if r.status_code == 429:
            print("  [WARN] FMP rate limit hit — sleeping 60s")
            time.sleep(60)
            r = requests.get(url, timeout=20, verify=False)
        if r.status_code != 200:
            print(f"  [WARN] FMP calendar returned {r.status_code}")
            return []
        data = r.json()
        if isinstance(data, dict) and "Error Message" in data:
            return []
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"  [WARN] FMP calendar fetch failed: {e}")
        return []


def fetch_fmp_ticker_calendar(ticker: str, from_date: str, to_date: str) -> list[dict]:
    """Fetch earnings for a single ticker from FMP (fallback for high-priority tickers)."""
    if not FMP_KEY:
        return []
    url = (f"https://financialmodelingprep.com/api/v3/historical/earning_calendar/{ticker}"
           f"?limit=5&apiKey={FMP_KEY}")
    try:
        r = requests.get(url, timeout=10, verify=False)
        if r.status_code != 200:
            return []
        data = r.json()
        if not isinstance(data, list):
            return []
        return [e for e in data if from_date <= e.get("date", "") <= to_date]
    except Exception:
        return []


def upsert_earnings(conn, records: list[dict]) -> int:
    """Upsert earnings records into SQLite."""
    # Ensure table exists before any INSERT attempt
    _ensure_table(conn)

    c = conn.cursor()
    now = datetime.now().isoformat()
    inserted = 0

    for rec in records:
        ticker = rec.get("symbol") or rec.get("ticker")
        report_date = rec.get("date") or rec.get("reportDate")
        if not ticker or not report_date:
            continue

        # Normalize report_date to YYYY-MM-DD
        if len(report_date) > 10:
            report_date = report_date[:10]

        try:
            c.execute("""
                INSERT INTO earnings_calendar
                    (ticker, report_date, fiscal_period, eps_estimate,
                     revenue_estimate, time_of_day, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, report_date) DO UPDATE SET
                    fiscal_period    = excluded.fiscal_period,
                    eps_estimate     = excluded.eps_estimate,
                    revenue_estimate = excluded.revenue_estimate,
                    time_of_day      = excluded.time_of_day,
                    fetched_at       = excluded.fetched_at
            """, (
                ticker,
                report_date,
                rec.get("fiscalDateEnding") or rec.get("period"),
                rec.get("epsEstimated") or rec.get("eps_estimate"),
                rec.get("revenueEstimated") or rec.get("revenue_estimate"),
                rec.get("time") or "BMO",  # FMP gives 'BMO' or 'AMC'
                now,
            ))
            if c.rowcount > 0:
                inserted += 1
        except Exception as e:
            # Log insert failures — never silently swallow DB errors
            print(f"  [WARN] earnings upsert failed for {ticker}/{report_date}: {e}")

    conn.commit()
    return inserted


def build_quick_lookup(conn, universe_set: set, from_date: str, to_date: str) -> dict:
    """Build quick-lookup JSON: ticker -> list of earnings dates in window.

    Also flags tickers with earnings in the next 5 trading days.
    """
    c = conn.cursor()
    c.execute("""
        SELECT ticker, report_date, time_of_day
        FROM earnings_calendar
        WHERE report_date BETWEEN ? AND ?
        ORDER BY report_date ASC
    """, (from_date, to_date))

    by_ticker = {}
    by_date = {}
    for ticker, rdate, tod in c.fetchall():
        by_ticker.setdefault(ticker, []).append({"date": rdate, "time": tod})
        by_date.setdefault(rdate, []).append(ticker)

    # Compute next 5 trading days algorithmically (ohlcv only has HISTORICAL data,
    # future dates don't exist in the table — using ohlcv caused empty list bug)
    today_str = date.today().isoformat()
    upcoming_days = []
    _d = date.today()
    while len(upcoming_days) < 5:
        _d += timedelta(days=1)
        if _d.weekday() < 5:  # Monday-Friday only
            upcoming_days.append(_d.isoformat())

    # Flag tickers with earnings within 5 trading days
    within_5td = set()
    for d in upcoming_days:
        for t in by_date.get(d, []):
            within_5td.add(t)

    output = {
        "generated_at": datetime.now().isoformat(),
        "window": {"from": from_date, "to": to_date},
        "total_events": sum(len(v) for v in by_ticker.values()),
        "tickers_in_universe_with_earnings": sum(
            1 for t in by_ticker if t in universe_set
        ),
        "within_5_trading_days": sorted(within_5td),
        "by_date": by_date,
        "by_ticker": {k: v for k, v in by_ticker.items() if k in universe_set},
    }
    return output


def run(days: int = 45, tickers: list[str] | None = None):
    print("\n=== Earnings Calendar Fetch ===")
    from_date = date.today().isoformat()
    to_date = (date.today() + timedelta(days=days)).isoformat()
    print(f"  Window: {from_date} to {to_date} ({days} days)")

    universe = get_universe()
    universe_set = set(universe)

    conn = sqlite3.connect(DB_PATH)

    try:
        # Ensure earnings_calendar table exists (idempotent — safe to call every run)
        _ensure_table(conn)

        # 1. Finnhub bulk fetch (primary — free tier supported)
        print(f"  Fetching Finnhub bulk earnings calendar...")
        bulk = fetch_finnhub_bulk_calendar(from_date, to_date)
        print(f"  Finnhub returned {len(bulk)} earnings events")

        # 2. FMP bulk as fallback if Finnhub returned nothing
        if not bulk:
            print(f"  Finnhub returned 0 events — trying FMP bulk as fallback...")
            bulk = fetch_fmp_bulk_calendar(from_date, to_date)
            print(f"  FMP bulk returned {len(bulk)} earnings events")

        # Filter to universe only to reduce noise
        bulk_universe = [e for e in bulk if (e.get("symbol") or e.get("ticker")) in universe_set]
        print(f"  In universe: {len(bulk_universe)} events (of {len(bulk)} total)")

        inserted = upsert_earnings(conn, bulk)
        print(f"  Upserted: {inserted} records")

        # 3. Fill gaps: find universe tickers still missing from calendar
        #    Use FMP per-ticker calls (free plan supported, 200 calls/day budget)
        #    Cap at MAX_FILL to stay within daily quota
        MAX_FILL = 180  # leave 20 calls headroom for other FMP uses
        if FMP_KEY:
            c = conn.cursor()
            c.execute("""
                SELECT DISTINCT ticker FROM earnings_calendar
                WHERE report_date >= ?
            """, (from_date,))
            already_have = {r[0] for r in c.fetchall()}
            missing = sorted(t for t in universe_set if t not in already_have)
            print(f"\n  Gap fill: {len(missing)} universe tickers missing from calendar")

            if missing and len(missing) > 0:
                to_fill = missing[:MAX_FILL]
                if len(missing) > MAX_FILL:
                    print(f"  Capped at {MAX_FILL}/run (FMP daily budget) — {len(missing)-MAX_FILL} deferred")
                filled = 0
                for tkr in to_fill:
                    events = fetch_fmp_ticker_calendar(tkr, from_date, to_date)
                    if events:
                        n = upsert_earnings(conn, events)
                        if n > 0:
                            filled += 1
                    time.sleep(SLEEP)
                print(f"  Gap fill complete: {filled}/{len(to_fill)} tickers got earnings dates")

        # 4. If specific tickers requested, do individual lookups
        if tickers:
            print(f"\n  Individual lookup for {len(tickers)} high-priority tickers...")
            for ticker in tickers:
                events = fetch_fmp_ticker_calendar(ticker, from_date, to_date)
                if events:
                    n = upsert_earnings(conn, events)
                    print(f"    {ticker}: {n} events")
                time.sleep(SLEEP)

        # 3. Build quick-lookup JSON
        lookup = build_quick_lookup(conn, universe_set, from_date, to_date)
        out_path = f"{OUTPUT_DIR}/earnings_next{days}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(lookup, f, indent=2)

        # Also write "earnings_next30.json" as canonical name
        canonical = f"{OUTPUT_DIR}/earnings_next30.json"
        with open(canonical, "w", encoding="utf-8") as f:
            json.dump(lookup, f, indent=2)

        print(f"\n  Quick lookup: {lookup['total_events']} events, "
              f"{lookup['tickers_in_universe_with_earnings']} in universe")
        within5 = lookup["within_5_trading_days"]
        if within5:
            print(f"  GATE BLOCK (earnings in 5td): {within5}")
        else:
            print(f"  No earnings in next 5 trading days")
        print(f"  Saved: {out_path}")

        return lookup

    finally:
        conn.close()


def is_within_5td(ticker: str, conn=None) -> bool:
    """Quick helper: check if ticker has earnings in next 5 trading days.

    Can be called by pipeline_metrics/screening gate.

    Safe default: returns True (BLOCK) when file is missing or unreadable.
    Conservative design — missing data should never allow an unsafe entry.
    """
    earnings_file = f"{OUTPUT_DIR}/earnings_next30.json"
    if not os.path.exists(earnings_file):
        # File missing → assume earnings risk → conservative block
        return True
    try:
        with open(earnings_file, encoding="utf-8") as f:
            data = json.load(f)
        return ticker in data.get("within_5_trading_days", [])
    except Exception:
        # Unreadable → conservative block
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument("--tickers", nargs="+", default=None)
    args = parser.parse_args()
    run(days=args.days, tickers=args.tickers)
