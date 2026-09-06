"""
AlphaAbsolute -- Historical OHLCV Backfill
==========================================
Backfills 3 years of daily OHLCV data for the full universe via Polygon.io.
Only fetches dates NOT already in SQLite (safe to re-run).

Why this matters:
  - MA200 requires 200 trading days (~10 months)
  - RS 12M requires 252 trading days (~12 months)
  - Current data starts 2025-04 -> only 13 months, missing 23 months of history
  - Full 3Y: MA200 covers 100% of universe (vs 83% now), RS 12M becomes usable

Usage:
  python scripts/pre_compute/backfill_ohlcv_historical.py
  python scripts/pre_compute/backfill_ohlcv_historical.py --start 2023-01-01
  python scripts/pre_compute/backfill_ohlcv_historical.py --tickers NVDA AAPL MSFT
  python scripts/pre_compute/backfill_ohlcv_historical.py --missing-ma200   # only fill gaps

Polygon free tier: 5 calls/min -> sleep 12s between calls.
Polygon paid tier: 5,000 calls/min -> reduce SLEEP to 0.05.

Each call fetches up to 5,000 bars (5000 / 252 = ~20 years per call), so one
call per ticker covers the full 3-year window.

Runtime estimate (free tier, 1,325 tickers):
  1,325 tickers x 12s = ~4.4 hours
  Run overnight or on a machine that stays on.
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

urllib3.disable_warnings()
load_dotenv()

BASE_DIR    = Path(__file__).resolve().parents[2]
DB_PATH     = str(BASE_DIR / "data" / "ohlcv.db")
LABELS_PATH = str(BASE_DIR / "data" / "themes" / "ticker_labels.json")

POLYGON_KEY = os.getenv("POLYGON_API_KEY", "")

# Conservative default for free tier (5 req/min = 12s between calls).
# Polygon paid ($29/mo) allows unlimited -> set SLEEP=0.2 in .env override.
SLEEP_FREE    = 12.0
SLEEP_PAID    = 0.2

# Default backfill window: 3 years
BACKFILL_YEARS = 3


def get_universe():
    with open(LABELS_PATH) as f:
        data = json.load(f)
    return list(data["labels"].keys())


def get_existing_ranges(conn, universe):
    """
    Return {ticker: (first_date, last_date, n_bars)} for tickers already in DB.
    """
    c = conn.cursor()
    placeholders = ",".join("?" * len(universe))
    c.execute(f"""
        SELECT ticker, MIN(date), MAX(date), COUNT(*)
        FROM ohlcv
        WHERE ticker IN ({placeholders})
        GROUP BY ticker
    """, universe)
    return {r[0]: (r[1], r[2], r[3]) for r in c.fetchall()}


def detect_tier(polygon_key: str) -> str:
    """
    Quick check: make one API call and inspect rate-limit headers.
    Returns 'paid' or 'free'.
    """
    if not polygon_key:
        return "free"
    url = f"https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-01-05?limit=5&apiKey={polygon_key}"
    try:
        r = requests.get(url, verify=False, timeout=10)
        # Paid plans have x-ratelimit-limit >= 5000
        limit_hdr = int(r.headers.get("x-ratelimit-limit", "5"))
        return "paid" if limit_hdr >= 1000 else "free"
    except Exception:
        return "free"


def fetch_polygon_bars(ticker: str, start_date: str, end_date: str, polygon_key: str) -> list:
    """
    Fetch up to 5,000 daily OHLCV bars from Polygon for one ticker.
    Returns list of (date_str, close, volume) tuples.
    """
    if not polygon_key:
        return []

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day"
        f"/{start_date}/{end_date}"
        f"?adjusted=true&sort=asc&limit=5000&apiKey={polygon_key}"
    )
    try:
        r = requests.get(url, verify=False, timeout=30)
        if r.status_code == 429:
            print(f"    [RATE] {ticker} -- sleeping 60s")
            time.sleep(60)
            r = requests.get(url, verify=False, timeout=30)
        if r.status_code == 403:
            print(f"    [AUTH] Polygon key invalid or expired")
            return []
        if r.status_code != 200:
            return []

        data = r.json()
        if data.get("status") == "NOT_FOUND" or not data.get("results"):
            return []

        bars = []
        for bar in data["results"]:
            ts   = bar.get("t", 0)
            d    = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            close  = bar.get("c")
            volume = bar.get("v", 0)
            open_  = bar.get("o")
            high   = bar.get("h")
            low    = bar.get("l")
            if close:
                bars.append((d, float(close), int(volume or 0),
                             float(open_) if open_ else None,
                             float(high)  if high  else None,
                             float(low)   if low   else None))
        return bars

    except Exception as e:
        print(f"    [ERR] {ticker}: {e}")
        return []


def insert_bars(conn, ticker: str, bars: list) -> int:
    """
    Insert bars into ohlcv. Each bar: (date, close, volume[, open, high, low]).
    INSERT OR IGNORE for new rows; UPDATE to fill NULL open/high/low on existing rows.
    Returns count of newly inserted rows.
    """
    c = conn.cursor()
    inserted = 0
    for bar in bars:
        d, close, volume = bar[0], bar[1], bar[2]
        open_ = bar[3] if len(bar) > 3 else None
        high  = bar[4] if len(bar) > 4 else None
        low   = bar[5] if len(bar) > 5 else None
        try:
            c.execute(
                "INSERT OR IGNORE INTO ohlcv (ticker, date, close, volume, open, high, low) VALUES (?,?,?,?,?,?,?)",
                (ticker, d, close, volume, open_, high, low)
            )
            if c.rowcount > 0:
                inserted += 1
            elif open_ is not None:
                c.execute(
                    "UPDATE ohlcv SET open=?, high=?, low=? WHERE ticker=? AND date=? AND open IS NULL",
                    (open_, high, low, ticker, d)
                )
        except Exception:
            pass
    conn.commit()
    return inserted


def update_ticker_meta(conn, ticker: str) -> None:
    """Refresh ticker_meta aggregates after bulk insert."""
    c = conn.cursor()
    c.execute("""
        SELECT MIN(date), MAX(date), COUNT(*),
               (SELECT close FROM ohlcv WHERE ticker=? ORDER BY date DESC LIMIT 1)
        FROM ohlcv WHERE ticker=?
    """, (ticker, ticker))
    row = c.fetchone()
    if row and row[0]:
        c.execute("""
            INSERT INTO ticker_meta (ticker, first_date, last_date, n_bars, last_close, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                first_date   = excluded.first_date,
                last_date    = excluded.last_date,
                n_bars       = excluded.n_bars,
                last_close   = excluded.last_close,
                last_updated = excluded.last_updated
        """, (ticker, row[0], row[1], row[2], row[3], datetime.now().isoformat()))
        conn.commit()


def run(start_date: str | None = None,
        tickers: list | None = None,
        missing_ma200_only: bool = False,
        dry_run: bool = False) -> dict:

    print("\n=== AlphaAbsolute Historical OHLCV Backfill ===")

    if not POLYGON_KEY:
        print("ERROR: POLYGON_API_KEY not set in .env — cannot backfill")
        print("  Add: POLYGON_API_KEY=your_key_here")
        return {"error": "no_polygon_key"}

    # Detect tier and set sleep
    tier = detect_tier(POLYGON_KEY)
    sleep_time = SLEEP_PAID if tier == "paid" else SLEEP_FREE
    print(f"  Polygon tier detected: {tier} (sleep={sleep_time}s between calls)")

    # Date window
    today_str = date.today().isoformat()
    if start_date is None:
        start_date = (date.today() - timedelta(days=365 * BACKFILL_YEARS)).isoformat()
    print(f"  Backfill window: {start_date} -> {today_str}")

    # Universe
    universe = tickers if tickers else get_universe()
    print(f"  Universe: {len(universe)} tickers")

    conn = sqlite3.connect(DB_PATH)
    existing = get_existing_ranges(conn, universe)

    # Decide which tickers to fetch
    to_fetch = []
    for ticker in universe:
        info = existing.get(ticker)
        if info is None:
            # No data at all
            to_fetch.append((ticker, start_date, "no data"))
        else:
            first, last, n_bars = info
            needs_backfill = first > start_date   # missing older data
            if missing_ma200_only:
                # Only backfill if MA200 would be affected (< 200 bars before today)
                # 200 bars = ~10 months
                ma200_start = (date.today() - timedelta(days=300)).isoformat()
                needs_backfill = first > ma200_start
            if needs_backfill:
                to_fetch.append((ticker, start_date, f"first={first}, {n_bars} bars"))

    print(f"  Needs backfill: {len(to_fetch)} tickers")
    if not to_fetch:
        print("  All tickers have sufficient history. Done.")
        conn.close()
        return {"status": "nothing_to_do", "checked": len(universe)}

    if dry_run:
        print("\n  DRY RUN — would fetch:")
        for t, s, reason in to_fetch[:20]:
            print(f"    {t:<8} from {s}  ({reason})")
        if len(to_fetch) > 20:
            print(f"    ... and {len(to_fetch)-20} more")
        conn.close()
        return {"status": "dry_run", "would_fetch": len(to_fetch)}

    # ETA
    eta_min = len(to_fetch) * sleep_time / 60
    print(f"  ETA: ~{eta_min:.0f} minutes at {sleep_time}s/ticker")
    print(f"  (Free tier: ~{len(to_fetch)*SLEEP_FREE/3600:.1f} hours — run overnight)")
    print()

    # --- Main fetch loop ---
    total_inserted = 0
    success        = 0
    failed         = 0
    skipped        = 0

    for i, (ticker, fetch_from, reason) in enumerate(to_fetch, 1):
        bars = fetch_polygon_bars(ticker, fetch_from, today_str, POLYGON_KEY)

        if not bars:
            failed += 1
            if i <= 10 or i % 100 == 0:
                print(f"  {i:>5}/{len(to_fetch)} {ticker:<8} NO DATA ({reason})")
        else:
            # Count how many are new (before existing data range)
            info = existing.get(ticker)
            first_existing = info[0] if info else "9999-12-31"

            # FIX: preserve full 6-tuple (d, close, volume, open, high, low)
            new_bars = [bar for bar in bars if bar[0] < first_existing]
            n_new    = len(new_bars)

            # Pass ALL bars to insert_bars — handles both:
            #   a) INSERT new historical rows (dates before first_existing)
            #   b) UPDATE NULL open/high/low on existing rows (INSERT OR IGNORE path)
            insert_bars(conn, ticker, bars)
            update_ticker_meta(conn, ticker)

            if n_new == 0:
                skipped += 1
            else:
                total_inserted += n_new
                success += 1
                if i <= 20 or i % 100 == 0:
                    print(f"  {i:>5}/{len(to_fetch)} {ticker:<8} +{n_new:>5} bars  "
                          f"({fetch_from} -> {new_bars[-1][0]})")

        time.sleep(sleep_time)

    # Final summary
    print(f"\n=== Backfill Complete ===")
    print(f"  Tickers processed : {len(to_fetch)}")
    print(f"  Success (new bars): {success}")
    print(f"  Skipped (up2date) : {skipped}")
    print(f"  Failed (no data)  : {failed}")
    print(f"  Total new bars    : {total_inserted:,}")

    # Verify MA200 coverage after backfill
    c = conn.cursor()
    cutoff_200 = (date.today() - timedelta(days=300)).isoformat()
    c.execute("SELECT COUNT(DISTINCT ticker) FROM ohlcv WHERE date <= ?", (cutoff_200,))
    ma200_tickers = c.fetchone()[0]
    print(f"  MA200 coverage now: {ma200_tickers}/{len(universe)} tickers")

    conn.close()

    result = {
        "status":        "complete",
        "date":          today_str,
        "tickers_processed": len(to_fetch),
        "success":       success,
        "skipped":       skipped,
        "failed":        failed,
        "total_new_bars": total_inserted,
        "ma200_coverage": ma200_tickers,
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill 3 years of historical OHLCV data from Polygon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/pre_compute/backfill_ohlcv_historical.py
  python scripts/pre_compute/backfill_ohlcv_historical.py --start 2023-01-01
  python scripts/pre_compute/backfill_ohlcv_historical.py --missing-ma200
  python scripts/pre_compute/backfill_ohlcv_historical.py --dry-run
  python scripts/pre_compute/backfill_ohlcv_historical.py --tickers NVDA AAPL MSFT
        """
    )
    parser.add_argument("--start",        type=str,  default=None,
                        help="Start date YYYY-MM-DD (default: 3 years ago)")
    parser.add_argument("--tickers",      nargs="+", default=None,
                        help="Specific tickers to backfill (default: full universe)")
    parser.add_argument("--missing-ma200", action="store_true",
                        help="Only backfill tickers that lack enough history for MA200")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Show what would be fetched without fetching")
    args = parser.parse_args()

    run(
        start_date=args.start,
        tickers=args.tickers,
        missing_ma200_only=args.missing_ma200,
        dry_run=args.dry_run,
    )
