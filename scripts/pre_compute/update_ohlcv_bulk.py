"""
AlphaAbsolute — Bulk OHLCV Updater (Polygon Grouped Endpoint)
=============================================================
Replaces update_ohlcv_daily.py per-ticker loop with ONE Polygon grouped
call per trading date → 12,000+ tickers in <3 seconds vs 7+ minutes.

Endpoint: GET /v2/aggs/grouped/locale/us/market/stocks/{date}
          Returns ALL US stocks for that date in a single response.
          Available on free Polygon plan (5 req/min rate limit).

Strategy:
  1. Detect missing trading dates since last OHLCV bar in DB
  2. For each missing date: 1 API call → parse → filter to universe → insert
  3. Update ticker_meta (last_close, n_bars, last_date) in bulk
  4. Fallback: Yahoo Finance for any tickers missing from grouped response

Idempotent: re-running on same date is safe (INSERT OR IGNORE).
Handles:  weekends (skip), holidays (Polygon returns 0 results → skip),
          future dates (today's data only available after ~6 PM EST).

Usage:
  python scripts/pre_compute/update_ohlcv_bulk.py
  python scripts/pre_compute/update_ohlcv_bulk.py --date 2026-05-29
  python scripts/pre_compute/update_ohlcv_bulk.py --backfill-days 5
"""

import sqlite3
import json
import os
import sys
import time
import argparse
import requests
import urllib3
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
import warnings

warnings.filterwarnings('ignore')
urllib3.disable_warnings()
load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parents[2]
DB_PATH     = BASE_DIR / "data" / "ohlcv.db"
LABELS_PATH = BASE_DIR / "data" / "themes" / "ticker_labels.json"

POLYGON_KEY = os.getenv("POLYGON_API_KEY", "")
SLEEP_BETWEEN_DATES = 13   # seconds between grouped calls (free: 5/min = 12s gap)

# Always include these benchmark tickers (not in ticker_labels but needed by market regime)
# RSP = S&P 500 Equal Weight ETF — required for concentration signal
BENCHMARK_TICKERS = ["SPY", "QQQ", "IWM", "VIX", "RSP"]


# ── Universe ──────────────────────────────────────────────────────────────────

def get_universe(conn) -> set:
    """All tickers we care about: ticker_meta + benchmarks."""
    tickers = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM ticker_meta").fetchall()}
    # Also any ticker already in ohlcv (handles tickers removed from meta but still tracked)
    tickers |= {r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM ohlcv WHERE date >= date('now','-30 days')"
    ).fetchall()}
    tickers |= set(BENCHMARK_TICKERS)
    return tickers


# ── Date helpers ──────────────────────────────────────────────────────────────

def get_last_ohlcv_date(conn) -> str:
    """Most recent date where SPY has data (reliable benchmark)."""
    r = conn.execute("SELECT MAX(date) FROM ohlcv WHERE ticker='SPY'").fetchone()
    if r and r[0]:
        return r[0]
    # Fallback: max across all tickers
    r = conn.execute("SELECT MAX(date) FROM ohlcv").fetchone()
    return r[0] if r and r[0] else "2020-01-01"


def trading_dates_since(from_date: str, to_date: str = None) -> list:
    """List of weekday dates from (exclusive) to to_date (inclusive).

    FIX: default end uses UTC date, not date.today() (local Bangkok time).
    At 6 AM Bangkok = 11 PM UTC: date.today() returns Bangkok's "tomorrow"
    relative to UTC, causing a future date to be queued for fetching.
    """
    start = date.fromisoformat(from_date) + timedelta(days=1)
    end   = date.fromisoformat(to_date) if to_date else datetime.now(timezone.utc).date()
    result = []
    d = start
    while d <= end:
        if d.weekday() < 5:   # Mon–Fri only
            result.append(d.isoformat())
        d += timedelta(days=1)
    return result


# ── Polygon grouped endpoint ──────────────────────────────────────────────────

def fetch_polygon_grouped(target_date: str) -> dict | None:
    """
    Fetch ALL US stocks for one trading date via Polygon grouped endpoint.
    Returns dict {ticker: {c, o, h, l, v, vw}} or None on failure.
    One API call covers the entire universe.
    """
    if not POLYGON_KEY:
        print("  [WARN] POLYGON_API_KEY not set — cannot use bulk endpoint")
        return None

    url = (f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{target_date}"
           f"?adjusted=true&include_otc=false&apiKey={POLYGON_KEY}")

    for attempt in range(3):
        try:
            r = requests.get(url, verify=False, timeout=30)
            if r.status_code == 200:
                data = r.json()
                results = data.get("results") or []
                if not results:
                    # Holiday or non-trading day — Polygon returns empty results
                    status = data.get("status", "")
                    print(f"  [{target_date}] No results ({status}) — likely holiday/non-trading day")
                    return {}  # empty dict = skip this date, not an error
                # Build lookup dict
                return {
                    bar["T"]: {
                        "c": bar.get("c"),
                        "o": bar.get("o"),
                        "h": bar.get("h"),
                        "l": bar.get("l"),
                        "v": bar.get("v", 0),
                        "vw": bar.get("vw"),
                    }
                    for bar in results
                    if bar.get("T") and bar.get("c")
                }
            elif r.status_code == 429:
                wait = 15 * (attempt + 1)
                print(f"  [{target_date}] 429 rate limit — waiting {wait}s")
                time.sleep(wait)
            elif r.status_code == 403:
                print(f"  [{target_date}] 403 — grouped endpoint needs paid Polygon plan")
                return None
            else:
                print(f"  [{target_date}] HTTP {r.status_code}")
                return None
        except Exception as e:
            if attempt < 2:
                time.sleep(5)
            else:
                print(f"  [{target_date}] Request failed: {e}")
                return None

    return None


# ── Yahoo fallback for individual tickers ─────────────────────────────────────

def fetch_yahoo_single(ticker: str, target_date: str) -> dict | None:
    """Fetch one date for one ticker from Yahoo as fallback."""
    d = date.fromisoformat(target_date)
    start_ts = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) - 86400
    end_ts   = start_ts + 3 * 86400
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1d&period1={start_ts}&period2={end_ts}")
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, verify=False, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        chart = data.get("chart", {}).get("result", [{}])[0]
        timestamps = chart.get("timestamp", [])
        quotes = chart.get("indicators", {}).get("quote", [{}])[0]
        closes  = quotes.get("close",  [])
        opens   = quotes.get("open",   [])
        highs   = quotes.get("high",   [])
        lows    = quotes.get("low",    [])
        volumes = quotes.get("volume", [])
        for i, ts in enumerate(timestamps):
            bar_date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            if bar_date == target_date and i < len(closes) and closes[i]:
                return {
                    "c": float(closes[i]),
                    "o": float(opens[i])  if opens  and i < len(opens)  and opens[i]  else None,
                    "h": float(highs[i])  if highs  and i < len(highs)  and highs[i]  else None,
                    "l": float(lows[i])   if lows   and i < len(lows)   and lows[i]   else None,
                    "v": int(volumes[i])  if volumes and i < len(volumes) and volumes[i] else 0,
                }
        return None
    except Exception:
        return None


# ── DB writes ─────────────────────────────────────────────────────────────────

def bulk_insert(conn, target_date: str, universe: set, grouped: dict) -> tuple[int, int]:
    """
    Insert bars for all universe tickers from grouped response.
    Returns (inserted, missing_from_grouped).
    """
    c = conn.cursor()
    inserted = 0
    missing  = 0

    rows_to_insert = []
    missing_tickers = []

    for ticker in universe:
        bar = grouped.get(ticker)
        if bar:
            rows_to_insert.append((
                ticker, target_date,
                bar["c"], bar["v"],
                bar.get("o"), bar.get("h"), bar.get("l")
            ))
        else:
            missing += 1
            missing_tickers.append(ticker)

    if rows_to_insert:
        # FIX: c.rowcount after executemany is unreliable for SQLite — it returns
        # the rowcount of the LAST batch only (not cumulative). Use a before/after
        # count on the actual date to measure real insertions.
        before = conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE date = ?", (target_date,)
        ).fetchone()[0]
        c.executemany("""
            INSERT OR IGNORE INTO ohlcv (ticker, date, close, volume, open, high, low)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, rows_to_insert)
        conn.commit()
        after = conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE date = ?", (target_date,)
        ).fetchone()[0]
        inserted = after - before

    return inserted, missing_tickers


def bulk_update_meta(conn, target_date: str, grouped: dict, universe: set) -> int:
    """Update ticker_meta with latest close/date/n_bars for tickers in universe."""
    c = conn.cursor()
    updated = 0
    rows = []
    for ticker in universe:
        bar = grouped.get(ticker)
        if not bar:
            continue
        rows.append((bar["c"], target_date, ticker))

    if rows:
        # Update last_close and last_date
        c.executemany("""
            UPDATE ticker_meta
            SET last_close = ?, last_date = ?, last_updated = date('now')
            WHERE ticker = ?
        """, rows)
        updated = c.rowcount

    # Recompute n_bars for updated tickers.
    # FIX: SQLite default SQLITE_MAX_VARIABLE_NUMBER = 999. With 1200+ universe tickers,
    # a single IN(?,?,...) clause with 1200 placeholders raises:
    #   sqlite3.OperationalError: too many SQL variables
    # Fix: chunk into batches of ≤500 parameters.
    if rows:
        tickers_updated = [r[2] for r in rows]
        _CHUNK = 500
        for _i in range(0, len(tickers_updated), _CHUNK):
            _chunk = tickers_updated[_i : _i + _CHUNK]
            placeholders = ",".join("?" * len(_chunk))
            c.execute(f"""
                UPDATE ticker_meta
                SET n_bars = (
                    SELECT COUNT(*) FROM ohlcv
                    WHERE ohlcv.ticker = ticker_meta.ticker
                )
                WHERE ticker IN ({placeholders})
            """, _chunk)

    conn.commit()
    return updated


# ── Main ──────────────────────────────────────────────────────────────────────

def run(target_dates: list = None, backfill_days: int = None, force: bool = False) -> dict:
    """
    fetch_mode explanation:
      force=False (default): skip TODAY if US market hasn't closed yet (before 21:30 UTC)
      force=True:            fetch all missing dates regardless of time — for manual runs

    backfill_days=None (default): auto-detect gap size (up to 30 trading days).
      This prevents permanently skipping older dates when the pipeline misses >5 days.
      Pass an explicit int to cap manually (e.g., backfill_days=5 for fast runs).
    """
    print(f"\n{'='*60}")
    print(f"  Bulk OHLCV Updater  [{date.today().isoformat()}]")
    if force:
        print(f"  [FORCE mode] Time check bypassed — fetching all missing dates")
    print(f"{'='*60}")

    # FIX: wrap in try/finally so conn is always closed, even on KeyboardInterrupt or
    # unexpected exception mid-loop. Without this, the SQLite file lock was held until GC.
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return _run_inner(conn, target_dates, backfill_days, force)
    finally:
        conn.close()


def _run_inner(conn, target_dates, backfill_days, force) -> dict:
    """Inner implementation — called by run() which owns the conn lifecycle."""
    universe = get_universe(conn)
    print(f"  Universe: {len(universe)} tickers")

    # Determine dates to fetch
    if target_dates:
        dates = [d for d in target_dates if date.fromisoformat(d).weekday() < 5]
    else:
        last_date = get_last_ohlcv_date(conn)
        # Check up to backfill_days of missing weekdays
        dates = trading_dates_since(last_date)
        # Auto-detect gap size when backfill_days not explicitly set.
        # Hard cap at 30 to protect against a completely empty DB.
        AUTO_CAP = 30
        if backfill_days is None:
            backfill_days = min(len(dates), AUTO_CAP)
            if len(dates) > AUTO_CAP:
                print(f"  [backfill] Auto-detect: {len(dates)} missing days — capped to {AUTO_CAP}")
            else:
                print(f"  [backfill] Auto-detect: fetching all {len(dates)} missing days")

        # Enforce the cap — prevent unbounded runs after long DB gaps.
        # Use explicit `> 0` guard: `if backfill_days` would be False for 0
        # and `dates[-0:]` == `dates[:]` (full list), silently fetching everything.
        if backfill_days > 0 and len(dates) > backfill_days:
            skipped = len(dates) - backfill_days
            dates = dates[-backfill_days:]   # keep most-recent N dates
            print(f"  [backfill] Capped to {backfill_days} dates ({skipped} older dates skipped)")
        # Skip today unless market has closed (21:30 UTC = 30 min after 4 PM EST close)
        # Bypass with force=True for manual runs or when called by premarket runner
        # after 11 PM UTC (premarket is always safe — US market has been closed 2h+)
        if not force:
            # FIX: datetime.utcnow() is deprecated in Python 3.12+; use datetime.now(timezone.utc).
            # FIX: use now_utc.date() not date.today() — date.today() returns Bangkok local date
            # (UTC+7). At 6 AM Bangkok = 23:00 UTC, date.today() is already "tomorrow" in UTC,
            # so the market-close guard runs against a date not in the dates list, silently
            # missing the check and including a future date for fetching.
            now_utc   = datetime.now(timezone.utc)
            today_str = now_utc.date().isoformat()   # UTC date, not Bangkok date
            if today_str in dates:
                # Market closes 4 PM EST = 21:00 UTC. Allow 30 min for Polygon to settle.
                if now_utc.hour < 21 or (now_utc.hour == 21 and now_utc.minute < 30):
                    print(f"  [Skip] {today_str} — market not closed yet (UTC {now_utc.hour}:{now_utc.minute:02d}, need 21:30+)")
                    dates.remove(today_str)

    if not dates:
        print("  OHLCV already up to date.")
        return {"dates_processed": 0, "total_inserted": 0}

    print(f"  Dates to fetch: {dates}")

    total_inserted = 0
    total_fallback = 0
    dates_processed = 0

    for i, target_date in enumerate(dates):
        print(f"\n  [{i+1}/{len(dates)}] {target_date}")
        t0 = time.time()

        # 1. Polygon grouped call (1 call for all tickers)
        grouped = fetch_polygon_grouped(target_date)

        if grouped is None:
            # Polygon failed entirely — fall through to Yahoo for critical tickers
            print(f"    Polygon failed — Yahoo fallback for benchmarks only")
            grouped = {}
            for t in BENCHMARK_TICKERS:
                bar = fetch_yahoo_single(t, target_date)
                if bar:
                    grouped[t] = bar
                    time.sleep(0.3)

        if not grouped:
            print(f"    No data — likely holiday/non-trading day. Skipping.")
            continue

        print(f"    Polygon returned {len(grouped)} tickers in {time.time()-t0:.1f}s")

        # 2. Insert from grouped
        inserted, missing_tickers = bulk_insert(conn, target_date, universe, grouped)
        print(f"    Inserted: {inserted} | Universe tickers missing: {len(missing_tickers)}")

        # 3. Yahoo fallback for critical tickers only (benchmarks + top RS leaders)
        # Don't flood Yahoo for all 489 missing — they're likely small illiquid names
        critical_missing = [t for t in BENCHMARK_TICKERS if t in missing_tickers]
        if critical_missing:
            print(f"    Yahoo fallback for {len(critical_missing)} critical tickers: {critical_missing}")
            for t in critical_missing:
                bar = fetch_yahoo_single(t, target_date)
                if bar:
                    conn.execute("""
                        INSERT OR IGNORE INTO ohlcv (ticker, date, close, volume, open, high, low)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (t, target_date, bar["c"], bar["v"], bar.get("o"), bar.get("h"), bar.get("l")))
                    grouped[t] = bar   # add to grouped for meta update
                    total_fallback += 1
                time.sleep(0.3)
            conn.commit()

        # 4. Update ticker_meta
        meta_updated = bulk_update_meta(conn, target_date, grouped, universe)
        print(f"    Meta updated: {meta_updated} tickers | {time.time()-t0:.1f}s total")

        total_inserted += inserted
        dates_processed += 1

        # Rate limit: free Polygon = 5 calls/min = sleep 13s between dates
        if i < len(dates) - 1:
            print(f"    Rate limit pause ({SLEEP_BETWEEN_DATES}s)...")
            time.sleep(SLEEP_BETWEEN_DATES)

    print(f"\n{'='*60}")
    print(f"  Done: {dates_processed} dates | {total_inserted} rows inserted | {total_fallback} Yahoo fallbacks")
    print(f"{'='*60}")

    return {
        "dates_processed": dates_processed,
        "total_inserted": total_inserted,
        "fallback_calls": total_fallback,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulk OHLCV updater via Polygon grouped endpoint")
    parser.add_argument("--date", help="Fetch specific date (YYYY-MM-DD)")
    parser.add_argument("--backfill-days", type=int, default=10,
                        help="Max trading days to backfill (default 10)")
    parser.add_argument("--force", action="store_true",
                        help="Skip time-of-day check — fetch all missing dates immediately "
                             "(use when running manually outside scheduled windows)")
    args = parser.parse_args()

    target = [args.date] if args.date else None
    run(target_dates=target, backfill_days=args.backfill_days, force=args.force)
