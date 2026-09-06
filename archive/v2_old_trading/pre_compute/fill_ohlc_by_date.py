"""
AlphaAbsolute -- Fill OHLC via Polygon Grouped Daily  (date-based batch filler)
================================================================================
ohlcv_store.py only writes close+volume (no open/high/low).
fill_ohlc_gaps.py fills per-ticker (12s × 673 tickers = 134 min — too slow).

This script uses Polygon's GROUPED DAILY endpoint instead:
  GET /v2/aggs/grouped/locale/us/market/stocks/{date}
Returns ALL US stocks for ONE date in a single API call.

Strategy:
  1. Find all dates that have NULL open in ohlcv table
  2. For each date, fetch grouped daily from Polygon (one call)
  3. UPDATE all matching tickers for that date

Result: 30 dates × ~12s each = ~6 minutes vs 134 minutes for ticker approach.
"""

import os, sys, time, json, sqlite3
import requests
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH  = BASE_DIR / "data" / "ohlcv.db"

# ── Load .env ─────────────────────────────────────────────────────────────────
def _load_env():
    env = BASE_DIR / ".env"
    if env.exists():
        for ln in env.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()
POLYGON_KEY = os.getenv("POLYGON_API_KEY", "")

# Apply WARP SSL patch
try:
    sys.path.insert(0, str(BASE_DIR / "scripts" / "utils"))
    import ssl_patch
except ImportError:
    pass
import urllib3
urllib3.disable_warnings()

SLEEP = 12.0   # Polygon free tier: 5 calls/min -> 12s between calls

# ─────────────────────────────────────────────────────────────────────────────

def get_null_dates(con: sqlite3.Connection) -> list[str]:
    """Return dates that have ≥1 NULL open rows, oldest first."""
    cur = con.execute("""
        SELECT date, COUNT(DISTINCT ticker) as n
        FROM ohlcv WHERE open IS NULL
        GROUP BY date ORDER BY date
    """)
    rows = cur.fetchall()
    return [(r[0], r[1]) for r in rows]


def fetch_grouped_daily(date: str) -> dict:
    """
    Fetch all US stock OHLCV for a given date via Polygon grouped daily.
    Returns {ticker: {open, high, low, close, volume}} dict.
    """
    url = (f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date}"
           f"?adjusted=true&include_otc=false&apiKey={POLYGON_KEY}")
    try:
        r = requests.get(url, verify=False, timeout=30)
        if r.status_code == 429:
            print(f"    [429] Rate limit — sleeping 60s")
            time.sleep(60)
            r = requests.get(url, verify=False, timeout=30)
        if r.status_code != 200:
            print(f"    [ERR] HTTP {r.status_code} for {date}")
            return {}
        data = r.json()
        results = data.get("results", [])
        if not results:
            # Market may have been closed that day
            status = data.get("status", "")
            print(f"    No results for {date} (status={status})")
            return {}
        out = {}
        for bar in results:
            ticker = bar.get("T", "")
            if ticker:
                out[ticker] = {
                    "open":   bar.get("o"),
                    "high":   bar.get("h"),
                    "low":    bar.get("l"),
                    "close":  bar.get("c"),
                    "volume": bar.get("v"),
                }
        return out
    except Exception as e:
        print(f"    [ERR] {date}: {e}")
        return {}


def fill_date(con: sqlite3.Connection, date: str, daily_data: dict) -> int:
    """
    UPDATE ohlcv SET open=?, high=?, low=? WHERE ticker=? AND date=? AND open IS NULL.
    Returns number of rows updated.
    """
    if not daily_data:
        return 0

    # Get tickers with NULL open for this date
    cur = con.execute(
        "SELECT ticker FROM ohlcv WHERE date=? AND open IS NULL", (date,)
    )
    null_tickers = {r[0] for r in cur.fetchall()}

    updates = []
    for ticker in null_tickers:
        bar = daily_data.get(ticker)
        if bar and bar.get("open") is not None:
            updates.append((bar["open"], bar.get("high"), bar.get("low"), ticker, date))

    if updates:
        con.executemany(
            "UPDATE ohlcv SET open=?, high=?, low=? WHERE ticker=? AND date=? AND open IS NULL",
            updates
        )
        con.commit()

    return len(updates)


# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not POLYGON_KEY:
        print("[ERR] POLYGON_API_KEY not set in .env")
        sys.exit(1)

    con = sqlite3.connect(DB_PATH)

    null_dates = get_null_dates(con)
    if not null_dates:
        print("All open/high/low already filled — nothing to do.")
        return

    total_dates   = len(null_dates)
    total_tickers = sum(n for _, n in null_dates)
    eta_min = total_dates * SLEEP / 60

    print(f"\n{'='*60}")
    print(f"  OHLC Date-Batch Fill  [{datetime.now().strftime('%Y-%m-%d')}]")
    print(f"{'='*60}")
    print(f"  Dates with NULL open: {total_dates}")
    print(f"  Total NULL rows across {total_tickers} ticker-days")
    print(f"  Strategy: Polygon grouped daily (1 call/date)")
    print(f"  ETA: ~{eta_min:.0f} minutes ({SLEEP:.0f}s/date)\n")

    # Check current fill %
    cur = con.execute("SELECT COUNT(*), SUM(CASE WHEN open IS NULL THEN 1 ELSE 0 END) FROM ohlcv")
    total_rows, null_rows = cur.fetchone()
    print(f"  Before: {(total_rows-null_rows)/total_rows*100:.1f}% filled ({null_rows:,} NULL)\n")

    filled_total = 0
    failed_dates = []

    for i, (date, n_null) in enumerate(null_dates, 1):
        print(f"  [{i:2}/{total_dates}] {date}  ({n_null} tickers with NULL open)", end="", flush=True)

        t0 = time.time()
        daily_data = fetch_grouped_daily(date)
        fetch_time = time.time() - t0

        if not daily_data:
            failed_dates.append(date)
            print(f" -> NO DATA ({fetch_time:.1f}s)")
        else:
            n_filled = fill_date(con, date, daily_data)
            filled_total += n_filled
            print(f" -> filled {n_filled}/{n_null} ({fetch_time:.1f}s)")

        # Rate limit: ensure 12s between calls
        elapsed = time.time() - t0
        if elapsed < SLEEP and i < total_dates:
            time.sleep(SLEEP - elapsed)

    # Final stats
    cur2 = con.execute("SELECT COUNT(*), SUM(CASE WHEN open IS NULL THEN 1 ELSE 0 END) FROM ohlcv")
    total_rows2, null_rows2 = cur2.fetchone()

    print(f"\n{'='*60}")
    print(f"  Done!")
    print(f"  Filled this run: {filled_total:,} rows")
    print(f"  After: {(total_rows2-null_rows2)/total_rows2*100:.1f}% filled ({null_rows2:,} NULL)")
    if failed_dates:
        print(f"  Failed dates ({len(failed_dates)}): {', '.join(failed_dates)}")
    print(f"{'='*60}\n")

    con.close()


if __name__ == "__main__":
    main()
