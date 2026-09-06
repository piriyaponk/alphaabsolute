"""
AlphaAbsolute -- Daily OHLCV Update (SQL-centric)
==================================================
Fetches new OHLCV bars since the last stored date and appends to SQLite.
Source: Yahoo Finance query2 (verify=False for corporate proxy).
Falls back to Polygon if Yahoo fails.

Run daily AFTER market close (4:30 PM+):
  python scripts/pre_compute/update_ohlcv_daily.py

Will update:
  - ohlcv table: new bars
  - ticker_meta: last_date, n_bars, last_close
  - Triggers downstream: rs recomputation, ADTV/52w update

Only fetches bars newer than last stored date per ticker.
Batch mode: fetches one ticker at a time with rate limiting.
"""

import sqlite3
import json
import os
import sys
import time
import requests
import urllib3
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
import warnings

warnings.filterwarnings('ignore')
urllib3.disable_warnings()
load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = str(BASE_DIR / "data" / "ohlcv.db")
LABELS_PATH = str(BASE_DIR / "data" / "themes" / "ticker_labels.json")
POLYGON_KEY = os.getenv("POLYGON_API_KEY", "")
SLEEP = 0.3    # between API calls
MAX_TICKERS = 9999   # no cap by default

# Benchmark tickers always fetched — NOT in ticker_labels.json (they are ETFs/indices)
# Required by: market_regime.py (SPY/QQQ/IWM price analysis) + breadth engine (^VIX)
# DA Analyst fix 2026-05-22: SPY was missing from ohlcv table because it's ETF_Fund label
BENCHMARK_TICKERS = ["SPY", "QQQ", "IWM", "VIX"]  # ^VIX not supported by Yahoo, use VIX


def get_universe():
    with open(LABELS_PATH, encoding='utf-8') as f:
        data = json.load(f)
    universe = list(data['labels'].keys())
    # Add benchmark tickers — deduplicate in case they're in labels
    for t in BENCHMARK_TICKERS:
        if t not in universe:
            universe.append(t)
    return universe


def get_last_dates(conn, universe):
    """Get last stored date per ticker."""
    c = conn.cursor()
    placeholders = ','.join(['?' for _ in universe])
    c.execute(f"""
        SELECT ticker, MAX(SUBSTR(date,1,10)) as last_date
        FROM ohlcv WHERE ticker IN ({placeholders})
        GROUP BY ticker
    """, universe)
    return {r[0]: r[1] for r in c.fetchall()}


def fetch_yahoo(ticker, start_date):
    """Fetch OHLCV from Yahoo Finance query2 (verify=False)."""
    start_ts = int(datetime.strptime(start_date, '%Y-%m-%d').timestamp())
    end_ts = int(datetime.now().timestamp()) + 86400

    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1d&period1={start_ts}&period2={end_ts}")
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    try:
        r = requests.get(url, headers=headers, verify=False, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        chart = data.get('chart', {}).get('result', [{}])[0]
        timestamps = chart.get('timestamp', [])
        quotes = chart.get('indicators', {}).get('quote', [{}])[0]
        opens   = quotes.get('open',   [])
        highs   = quotes.get('high',   [])
        lows    = quotes.get('low',    [])
        closes  = quotes.get('close',  [])
        volumes = quotes.get('volume', [])

        if not timestamps:
            return None

        bars = []
        for i, ts in enumerate(timestamps):
            d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')
            close  = closes[i]  if closes  and i < len(closes)  else None
            volume = volumes[i] if volumes and i < len(volumes) else None
            open_  = opens[i]   if opens   and i < len(opens)   else None
            high   = highs[i]   if highs   and i < len(highs)   else None
            low    = lows[i]    if lows    and i < len(lows)    else None
            if close and d > start_date:
                bars.append((d, float(close), int(volume) if volume else 0,
                             float(open_) if open_ else None,
                             float(high)  if high  else None,
                             float(low)   if low   else None))
        return bars
    except Exception as e:
        return None


def fetch_polygon(ticker, start_date):
    """Fetch OHLCV from Polygon as fallback."""
    if not POLYGON_KEY:
        return None
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day"
           f"/{start_date}/{date.today().isoformat()}?adjusted=true&limit=5000&apiKey={POLYGON_KEY}")
    try:
        r = requests.get(url, verify=False, timeout=15)
        if r.status_code == 429:
            time.sleep(15)
            r = requests.get(url, verify=False, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        results = data.get('results', [])
        bars = []
        for bar in results:
            d = datetime.fromtimestamp(bar['t'] / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
            if d > start_date:
                bars.append((d, float(bar['c']), int(bar.get('v', 0)),
                             float(bar['o']) if bar.get('o') else None,
                             float(bar['h']) if bar.get('h') else None,
                             float(bar['l']) if bar.get('l') else None))
        return bars
    except Exception:
        return None


def insert_bars(conn, ticker, bars):
    """Insert new bars into ohlcv. Each bar is (date, close, volume[, open, high, low]).
    Uses INSERT OR IGNORE for new rows; UPDATE to fill NULL open/high/low on existing rows.
    """
    c = conn.cursor()
    inserted = 0
    updated_ohlc = 0
    for bar in bars:
        d, close, volume = bar[0], bar[1], bar[2]
        open_ = bar[3] if len(bar) > 3 else None
        high  = bar[4] if len(bar) > 4 else None
        low   = bar[5] if len(bar) > 5 else None
        try:
            c.execute("""
                INSERT OR IGNORE INTO ohlcv (ticker, date, close, volume, open, high, low)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (ticker, d, close, volume, open_, high, low))
            if c.rowcount > 0:
                inserted += 1
            elif open_ is not None:
                # Row exists but may have NULL open/high/low — fill it
                c.execute("""
                    UPDATE ohlcv SET open=?, high=?, low=?
                    WHERE ticker=? AND date=? AND open IS NULL
                """, (open_, high, low, ticker, d))
                if c.rowcount > 0:
                    updated_ohlc += 1
        except Exception:
            pass
    conn.commit()
    return inserted


def update_ticker_meta(conn, ticker):
    """Refresh ticker_meta from ohlcv data. Always stores normalized YYYY-MM-DD dates."""
    c = conn.cursor()
    c.execute("""
        SELECT MIN(SUBSTR(date,1,10)), MAX(SUBSTR(date,1,10)), COUNT(*),
               (SELECT close FROM ohlcv WHERE ticker = ? ORDER BY SUBSTR(date,1,10) DESC LIMIT 1)
        FROM ohlcv WHERE ticker = ?
    """, (ticker, ticker))
    row = c.fetchone()
    if row and row[0]:
        c.execute("""
            INSERT INTO ticker_meta (ticker, first_date, last_date, n_bars, last_close, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                first_date = excluded.first_date,
                last_date = excluded.last_date,
                n_bars = excluded.n_bars,
                last_close = excluded.last_close,
                last_updated = excluded.last_updated
        """, (ticker, row[0], row[1], row[2], row[3], datetime.now().isoformat()))
        conn.commit()


def main():
    print("AlphaAbsolute -- Daily OHLCV Update")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    universe = get_universe()
    print(f"Universe: {len(universe)} tickers")

    conn = sqlite3.connect(DB_PATH)
    last_dates = get_last_dates(conn, universe)

    # Only fetch tickers that need updating (last_date < today)
    to_update = []
    for ticker in universe:
        last = last_dates.get(ticker, '2020-01-01')
        if last < yesterday:  # at least 1 day behind
            to_update.append((ticker, last))

    # Sort by how many days behind (most stale first)
    to_update.sort(key=lambda x: x[1])
    to_update = to_update[:MAX_TICKERS]

    print(f"Need update: {len(to_update)} tickers (latest date < {yesterday})")
    if not to_update:
        print("All tickers up to date!")
        conn.close()
        return

    updated = 0
    failed = 0
    total_new_bars = 0

    for i, (ticker, last_date) in enumerate(to_update, 1):
        # Try Yahoo first
        bars = fetch_yahoo(ticker, last_date)
        source = "yahoo"

        if not bars:
            bars = fetch_polygon(ticker, last_date)
            source = "polygon"

        if bars:
            n = insert_bars(conn, ticker, bars)
            update_ticker_meta(conn, ticker)
            total_new_bars += n
            if n > 0:
                print(f"  {i:>4}/{len(to_update)} {ticker:<8} +{n} bars [{source}] (from {last_date})")
                updated += 1
            else:
                # Up to date
                pass
        else:
            if i % 50 == 0:  # only print failures every 50
                print(f"  {i:>4}/{len(to_update)} {ticker:<8} FAILED (from {last_date})")
            failed += 1

        time.sleep(SLEEP)

    print(f"\nUpdate complete: {updated} tickers updated, {total_new_bars} new bars, {failed} failed")
    conn.close()
    # Note: downstream recomputation (pipeline_metrics.py) is triggered by
    # pre_market_runner.py EOD mode — do not subprocess-call it here.


if __name__ == "__main__":
    main()
