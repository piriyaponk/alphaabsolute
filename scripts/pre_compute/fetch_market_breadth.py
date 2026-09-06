"""
AlphaAbsolute v2 -- Full-Market Breadth Engine
===============================================
Fetches ALL US stocks (NYSE + NASDAQ + AMEX, ~12,200 tickers) via Polygon
grouped daily endpoint. Computes true full-market NH/NL breadth.

Why Polygon grouped daily vs internal ohlcv.db:
  - ohlcv.db has ~1,163 tickers (screening universe only)
  - Polygon grouped daily = 12,200+ tickers = ENTIRE US market
  - Standard global breadth practice (IBD, Ned Davis, Lowry Research)

Metrics computed per day:
  nh_pct_52w      = NH_52w / universe_n     (Breadth Strength -- % making new highs)
  net_breadth_52w = (NH_52w - NL_52w) / universe_n  (Divergence Detection)
  nh_pct_3m       = NH_3m / universe_n      (Mode B breadth gate)
  net_breadth_3m  = (NH_3m - NL_3m) / universe_n
  ad_ratio        = advances / (advances + declines)  (A-D proxy)

Storage: data/breadth/breadth_prices.db (SQLite, date+ticker+close only)
         data/breadth/market_breadth_history.json (daily aggregates)

Usage:
  python fetch_market_breadth.py --bootstrap   # first run: fetch 270 trading days (~10 min)
  python fetch_market_breadth.py               # daily: fetch yesterday + recompute

Run: 6:00 AM daily (after market close data is available)
Cost: $0 (Polygon free tier, 1 grouped-daily call per trading day)
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

BREADTH_DIR  = ROOT / "data" / "breadth"
BREADTH_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH      = BREADTH_DIR / "breadth_prices.db"
HISTORY_FILE = BREADTH_DIR / "market_breadth_history.json"

POLYGON_KEY  = os.getenv("POLYGON_API_KEY", "")
KEEP_DAYS    = 270  # rolling window to keep (~270 trading days > 252 needed for 52W)
RATE_SLEEP   = 13.0  # seconds between Polygon calls — free tier = 5 calls/minute = 12s minimum


# ── Database ───────────────────────────────────────────────────────────────────

def _init_db(conn: sqlite3.Connection) -> None:
    """BOA-004-A4: Schema includes full OHLCV columns (open, high, low, volume added)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS closes (
            date   TEXT NOT NULL,
            ticker TEXT NOT NULL,
            close  REAL NOT NULL,
            open   REAL,
            high   REAL,
            low    REAL,
            volume REAL,
            PRIMARY KEY (date, ticker)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_closes_ticker_date ON closes(ticker, date)")
    # Migration: add OHLCV columns to existing DB if they don't exist yet
    for col in ("open", "high", "low", "volume"):
        try:
            conn.execute(f"ALTER TABLE closes ADD COLUMN {col} REAL")
        except Exception:
            pass  # Column already exists — safe to ignore
    conn.commit()


def _upsert_day(conn: sqlite3.Connection, trading_date: str, records: list[dict]) -> int:
    """
    Insert all closes (+ OHLCV) for one trading day. Returns row count inserted.
    BOA-004-A4: now stores open (o), high (h), low (l), volume (v) from Polygon response.
    """
    rows = [
        (trading_date, r["T"], r["c"],
         r.get("o"), r.get("h"), r.get("l"), r.get("v"))
        for r in records if r.get("c") and r.get("T")
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO closes (date, ticker, close, open, high, low, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows
    )
    conn.commit()
    return len(rows)


def _prune_old(conn: sqlite3.Connection) -> None:
    """Keep only last KEEP_DAYS distinct dates."""
    conn.execute(f"""
        DELETE FROM closes
        WHERE date NOT IN (
            SELECT DISTINCT date FROM closes
            ORDER BY date DESC
            LIMIT {KEEP_DAYS}
        )
    """)
    conn.commit()


def _stored_dates(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT date FROM closes ORDER BY date").fetchall()
    return [r[0] for r in rows]


# ── Polygon fetch ──────────────────────────────────────────────────────────────

def _fetch_grouped(trading_date: str) -> Optional[list[dict]]:
    """Fetch Polygon grouped daily for one date. Returns list of ticker records."""
    url = (
        f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{trading_date}"
        f"?adjusted=true&include_otc=false&apiKey={POLYGON_KEY}"
    )
    try:
        r = requests.get(url, verify=False, timeout=20)
        if r.status_code == 200:
            d = r.json()
            results = d.get("results", [])
            if results:
                return results
        elif r.status_code == 403:
            print(f"  [{trading_date}] 403 Forbidden — check Polygon API key")
        # 404 or empty = non-trading day, skip silently
    except Exception as e:
        print(f"  [{trading_date}] fetch error: {e}")
    return None


# ── NH/NL computation ──────────────────────────────────────────────────────────

def _compute_breadth(conn: sqlite3.Connection) -> list[dict]:
    """
    Compute daily NH/NL breadth for all stored dates.

    Uses window approach: for each date, compute rolling 252-day max/min
    per ticker using SQLite window functions. Only count tickers with
    >= 252 rows of history (prevents contamination from new-to-db tickers).

    Returns list of dicts ordered by date ascending.
    """
    sql = """
    WITH ranked AS (
        SELECT date, ticker, close,
               COUNT(*) OVER (PARTITION BY ticker) AS total_rows,
               MAX(close) OVER (
                   PARTITION BY ticker ORDER BY date
                   ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
               ) AS high_52w,
               MIN(close) OVER (
                   PARTITION BY ticker ORDER BY date
                   ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
               ) AS low_52w,
               MAX(close) OVER (
                   PARTITION BY ticker ORDER BY date
                   ROWS BETWEEN 62 PRECEDING AND CURRENT ROW
               ) AS high_3m,
               MIN(close) OVER (
                   PARTITION BY ticker ORDER BY date
                   ROWS BETWEEN 62 PRECEDING AND CURRENT ROW
               ) AS low_3m,
               LAG(close, 1) OVER (PARTITION BY ticker ORDER BY date) AS prev_close
        FROM closes
    )
    SELECT
        date,
        COUNT(*)                                              AS universe_n,
        SUM(CASE WHEN close >= high_52w THEN 1 ELSE 0 END)  AS nh_52w,
        SUM(CASE WHEN close <= low_52w  THEN 1 ELSE 0 END)  AS nl_52w,
        SUM(CASE WHEN close >= high_3m  THEN 1 ELSE 0 END)  AS nh_3m,
        SUM(CASE WHEN close <= low_3m   THEN 1 ELSE 0 END)  AS nl_3m,
        SUM(CASE WHEN prev_close IS NOT NULL
                  AND close > prev_close THEN 1 ELSE 0 END) AS advances,
        SUM(CASE WHEN prev_close IS NOT NULL
                  AND close < prev_close THEN 1 ELSE 0 END) AS declines
    FROM ranked
    WHERE total_rows >= 50
    GROUP BY date
    ORDER BY date
    """
    # NOTE: threshold lowered from 252 to 50 — our closes table bootstraps
    # from ~7 months back (145 days), so no ticker has 252 rows yet.
    # 50 = minimum viable (10 weeks) for meaningful breadth. The 52W window
    # functions still compute correctly using all available rows per ticker.
    rows = conn.execute(sql).fetchall()
    cols = ["date", "universe_n", "nh_52w", "nl_52w", "nh_3m", "nl_3m", "advances", "declines"]

    result = []
    for row in rows:
        r = dict(zip(cols, row))
        n   = r["universe_n"]
        nh  = r["nh_52w"]
        nl  = r["nl_52w"]
        nh3 = r["nh_3m"]
        nl3 = r["nl_3m"]
        adv = r["advances"]
        dec = r["declines"]

        # Breadth Strength: % of universe at 52W/3M high
        r["nh_pct_52w"]      = round(nh / n, 4) if n > 0 else None
        r["nl_pct_52w"]      = round(nl / n, 4) if n > 0 else None

        # Divergence metric: (NH - NL) / universe — SIGNED, covers entire market
        r["net_breadth_52w"] = round((nh - nl) / n, 4) if n > 0 else None

        r["nh_pct_3m"]       = round(nh3 / n, 4) if n > 0 else None
        r["nl_pct_3m"]       = round(nl3 / n, 4) if n > 0 else None
        r["net_breadth_3m"]  = round((nh3 - nl3) / n, 4) if n > 0 else None

        # A-D ratio
        total_moves = adv + dec
        r["ad_ratio"] = round(adv / total_moves, 4) if total_moves > 0 else None

        result.append(r)

    return result


# ── History file ───────────────────────────────────────────────────────────────

def _load_history() -> list[dict]:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_history(records: list[dict]) -> None:
    HISTORY_FILE.write_text(
        json.dumps(records, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def _update_history(fresh: list[dict]) -> None:
    """Merge fresh computed records into history file (upsert by date)."""
    existing = {r["date"]: r for r in _load_history()}
    for r in fresh:
        existing[r["date"]] = r
    merged = sorted(existing.values(), key=lambda x: x["date"])
    _save_history(merged)
    print(f"  History: {len(merged)} days saved to {HISTORY_FILE.name}")


# ── Trading day utilities ──────────────────────────────────────────────────────

def _trading_days_back(n_days: int) -> list[str]:
    """
    Generate last n_days calendar dates (excluding weekends).
    Polygon will return empty for holidays — we handle that in fetch.
    """
    days = []
    current = date.today() - timedelta(days=1)  # start from yesterday
    while len(days) < n_days:
        if current.weekday() < 5:  # Mon-Fri only
            days.append(current.strftime("%Y-%m-%d"))
        current -= timedelta(days=1)
    return list(reversed(days))


# ── Main modes ─────────────────────────────────────────────────────────────────

def run_bootstrap(target_days: int = 270) -> None:
    """
    One-time bootstrap: fetch last target_days trading days from Polygon.
    Stores closes in breadth_prices.db, then computes and saves history.
    Estimated time: ~11 min for 270 days at 0.25s sleep + ~2.5s fetch.
    """
    print(f"[BOOTSTRAP] Fetching {target_days} trading days from Polygon...")
    print(f"  Estimated time: {int(target_days * 2.75 / 60)} min")

    conn = sqlite3.connect(str(DB_PATH))
    _init_db(conn)

    stored = set(_stored_dates(conn))
    candidates = _trading_days_back(target_days + 30)  # extra buffer for holidays

    fetched = 0
    skipped = 0
    for d_str in candidates:
        if d_str in stored:
            skipped += 1
            continue
        if fetched >= target_days:
            break

        records = _fetch_grouped(d_str)
        if records:
            n = _upsert_day(conn, d_str, records)
            fetched += 1
            if fetched % 10 == 0:
                print(f"  [{fetched}/{target_days}] {d_str}: {n:,} tickers")
        # non-trading day: just skip
        time.sleep(RATE_SLEEP)

    print(f"  Fetched: {fetched} days | Already stored: {skipped}")

    _prune_old(conn)

    print("  Computing NH/NL breadth...")
    fresh = _compute_breadth(conn)
    conn.close()

    _update_history(fresh)

    valid = [r for r in fresh if r["universe_n"] >= 5000]
    print(f"  Days with universe>=5000 (full market): {len(valid)}")
    if valid:
        last = valid[-1]
        print(f"  Latest: {last['date']} | universe={last['universe_n']:,} | "
              f"NH%={last['nh_pct_52w']:.1%} | net_breadth={last['net_breadth_52w']:+.3f}")

    print("[BOOTSTRAP] Complete.")


def run_daily(target_date: Optional[str] = None) -> None:
    """
    Daily update: fetch yesterday (or target_date), append to DB, recompute history.
    """
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    # Skip weekends
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    if dt.weekday() >= 5:
        print(f"[DAILY] {target_date} is a weekend — skip")
        return

    print(f"[DAILY] Fetching {target_date}...")
    conn = sqlite3.connect(str(DB_PATH))
    _init_db(conn)

    stored = set(_stored_dates(conn))
    if target_date not in stored:
        records = _fetch_grouped(target_date)
        if records:
            n = _upsert_day(conn, target_date, records)
            print(f"  Stored {n:,} tickers for {target_date}")
        else:
            print(f"  No data for {target_date} (holiday or non-trading day)")
    else:
        print(f"  {target_date} already in DB — recomputing only")

    _prune_old(conn)
    fresh = _compute_breadth(conn)
    conn.close()

    _update_history(fresh)

    # Print latest record
    valid = [r for r in fresh if r.get("universe_n", 0) >= 1000]
    if valid:
        last = valid[-1]
        u = last["universe_n"]
        print(f"  {last['date']} | universe={u:,} | "
              f"NH_pct={last['nh_pct_52w']:.1%} | "
              f"net_breadth={last['net_breadth_52w']:+.3f} | "
              f"ad_ratio={last['ad_ratio']:.3f}")
    print("[DAILY] Done.")


def print_latest(n: int = 10) -> None:
    """Print last n days of breadth history."""
    hist = _load_history()
    valid = [r for r in hist if r.get("universe_n", 0) >= 1000]
    for r in valid[-n:]:
        u = r["universe_n"]
        nh = r.get("nh_pct_52w", 0) or 0
        nb = r.get("net_breadth_52w", 0) or 0
        ad = r.get("ad_ratio", 0) or 0
        print(f"  {r['date']} | n={u:,} | NH%={nh:.1%} | net={nb:+.3f} | AD={ad:.3f}")


def run() -> dict:
    """
    BOA-004-A7: Runner-compatible entry point for pre_market_runner.py.
    Calls run_daily() to fetch yesterday's full-market data and recompute
    market_breadth_history.json. Idempotent — safe to call if already run today.
    Returns status dict for runner logging.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        global POLYGON_KEY
        import os as _os
        POLYGON_KEY = _os.getenv("POLYGON_API_KEY", POLYGON_KEY)
        if not POLYGON_KEY:
            print("  [WARN] POLYGON_API_KEY not set — fetch_market_breadth skipped")
            return {"status": "skipped", "reason": "no_api_key"}
        run_daily()
        return {"status": "ok"}
    except Exception as e:
        print(f"  [WARN] fetch_market_breadth.run() failed: {e}")
        return {"status": "error", "error": str(e)}


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    POLYGON_KEY = os.getenv("POLYGON_API_KEY", "")

    if not POLYGON_KEY:
        print("ERROR: POLYGON_API_KEY not set in .env")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Full-market breadth from Polygon")
    parser.add_argument("--bootstrap", action="store_true",
                        help="One-time: fetch last 270 trading days (~10 min)")
    parser.add_argument("--days", type=int, default=270,
                        help="Number of trading days to bootstrap (default 270)")
    parser.add_argument("--date", type=str, default=None,
                        help="Specific date to fetch YYYY-MM-DD (daily mode)")
    parser.add_argument("--show", action="store_true",
                        help="Show latest 10 days of history")
    args = parser.parse_args()

    if args.show:
        print_latest(10)
    elif args.bootstrap:
        run_bootstrap(target_days=args.days)
    else:
        run_daily(target_date=args.date)
