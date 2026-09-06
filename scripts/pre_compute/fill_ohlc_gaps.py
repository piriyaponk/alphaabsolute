"""
AlphaAbsolute -- Fill Missing Open/High/Low
============================================
Fetches open/high/low from Polygon for rows that only have close/volume.
Run ONCE after adding open/high/low columns to ohlcv.

This is separate from the 3Y backfill because that backfill was already
running before the columns were added.

Strategy:
  - For each ticker that has NULL open values in ohlcv:
      - Find the date range with missing data
      - Fetch from Polygon (limit=5000 covers any range in one call)
      - UPDATE rows (not INSERT, because close/volume already exist)

Usage:
  python scripts/pre_compute/fill_ohlc_gaps.py
  python scripts/pre_compute/fill_ohlc_gaps.py --dry-run
  python scripts/pre_compute/fill_ohlc_gaps.py --tickers NVDA AAPL
"""

import sqlite3, os, sys, time, argparse, json, requests, urllib3
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

urllib3.disable_warnings()
load_dotenv()

BASE_DIR    = Path(__file__).resolve().parents[2]
DB_PATH     = str(BASE_DIR / "data" / "ohlcv.db")
LABELS_PATH = str(BASE_DIR / "data" / "themes" / "ticker_labels.json")
POLYGON_KEY = os.getenv("POLYGON_API_KEY", "")

SLEEP_FREE = 12.0
SLEEP_PAID =  0.2


def detect_tier(key):
    if not key:
        return "free"
    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-01-05?limit=5&apiKey={key}"
        r = requests.get(url, verify=False, timeout=10)
        return "paid" if int(r.headers.get("x-ratelimit-limit", "5")) >= 1000 else "free"
    except Exception:
        return "free"


def get_tickers_with_gaps(conn, universe):
    """Return list of (ticker, min_date, max_date) that have NULL open values."""
    c = conn.cursor()
    ph = ",".join("?" * len(universe))
    c.execute(f"""
        SELECT ticker, MIN(date), MAX(date), COUNT(*) as n_missing
        FROM ohlcv
        WHERE open IS NULL AND ticker IN ({ph})
        GROUP BY ticker
        HAVING n_missing > 0
        ORDER BY n_missing DESC
    """, universe)
    return c.fetchall()


def fetch_ohlcv(ticker, start_date, end_date, key):
    """Fetch full OHLCV from Polygon. Returns {date: (o,h,l,c,v)}."""
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day"
           f"/{start_date}/{end_date}?adjusted=true&sort=asc&limit=5000&apiKey={key}")
    try:
        r = requests.get(url, verify=False, timeout=30)
        if r.status_code == 429:
            time.sleep(60)
            r = requests.get(url, verify=False, timeout=30)
        if r.status_code != 200:
            return {}
        data = r.json()
        result = {}
        for bar in data.get("results", []):
            d = datetime.fromtimestamp(bar["t"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            result[d] = (
                float(bar.get("o", 0) or 0),
                float(bar.get("h", 0) or 0),
                float(bar.get("l", 0) or 0),
                float(bar.get("c", 0) or 0),
                int(bar.get("v",   0) or 0),
            )
        return result
    except Exception as e:
        print(f"    [ERR] {ticker}: {e}")
        return {}


def fill_ticker(conn, ticker, start_date, end_date, ohlcv_data):
    """UPDATE existing ohlcv rows to add open/high/low. Returns count updated."""
    c = conn.cursor()
    updates = []
    for d, (o, h, l, cl, v) in ohlcv_data.items():
        if start_date <= d <= end_date and o > 0:
            updates.append((o, h, l, ticker, d))

    if not updates:
        return 0

    c.executemany(
        "UPDATE ohlcv SET open=?, high=?, low=? WHERE ticker=? AND date=? AND open IS NULL",
        updates
    )
    conn.commit()
    return c.rowcount


def run(tickers=None, dry_run=False):
    print("\n=== Fill Missing Open/High/Low ===")

    if not POLYGON_KEY:
        print("ERROR: POLYGON_API_KEY not set")
        return

    tier  = detect_tier(POLYGON_KEY)
    sleep = SLEEP_PAID if tier == "paid" else SLEEP_FREE
    print(f"  Polygon tier: {tier} (sleep={sleep}s)")

    with open(LABELS_PATH) as f:
        universe = list(json.load(f)["labels"].keys())
    if tickers:
        universe = [t for t in tickers if t in set(universe)]

    conn = sqlite3.connect(DB_PATH)
    gaps = get_tickers_with_gaps(conn, universe)
    print(f"  Tickers with NULL open: {len(gaps)}")

    if not gaps:
        print("  No gaps to fill.")
        conn.close()
        return

    total_rows = sum(g[3] for g in gaps)
    eta_min = len(gaps) * sleep / 60
    print(f"  Total rows to fill: {total_rows:,}")
    print(f"  ETA: ~{eta_min:.0f} min\n")

    if dry_run:
        print("  DRY RUN — first 10:")
        for t, mn, mx, n in gaps[:10]:
            print(f"    {t:<8} {mn} -> {mx}  ({n} rows)")
        conn.close()
        return

    filled = 0
    failed = 0

    for i, (ticker, min_date, max_date, n_missing) in enumerate(gaps, 1):
        ohlcv = fetch_ohlcv(ticker, min_date, max_date, POLYGON_KEY)
        if not ohlcv:
            failed += 1
            if i <= 10 or i % 200 == 0:
                print(f"  {i:>5}/{len(gaps)} {ticker:<8} NO DATA")
        else:
            n = fill_ticker(conn, ticker, min_date, max_date, ohlcv)
            filled += n
            if i <= 20 or i % 200 == 0:
                print(f"  {i:>5}/{len(gaps)} {ticker:<8} filled {n:>5}/{n_missing} rows")

        time.sleep(sleep)

    # Final check
    conn2 = sqlite3.connect(DB_PATH)
    c2 = conn2.cursor()
    c2.execute("SELECT COUNT(*) FROM ohlcv WHERE open IS NULL")
    still_null = c2.fetchone()[0]
    conn2.close()

    print(f"\n=== Done ===")
    print(f"  Filled     : {filled:,} rows")
    print(f"  Failed     : {failed} tickers")
    print(f"  Still NULL : {still_null:,} rows")
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", nargs="+", default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(tickers=args.tickers, dry_run=args.dry_run)
