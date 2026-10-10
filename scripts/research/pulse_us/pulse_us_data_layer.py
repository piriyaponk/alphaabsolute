"""
PULSE-US Data Layer
===================
Lightweight OHLCV fetcher for PULSE-US screener.
Designed to run in GitHub Actions — no local ohlcv.db needed.

Fetches ~400 days of OHLCV for RS>=90 tickers from rs_universe/latest.json.
Stores in data/research/pulse_us/pulse_us_ohlcv.db (SQLite, same schema as ohlcv.db).

Sources (priority order):
  1. Tiingo REST API  (free, 500 req/day, no SSL issue)
  2. Yahoo Finance query2 (free, no key, fallback)

Commands:
  python pulse_us_data_layer.py           # update (default)
  python pulse_us_data_layer.py --status  # show coverage
"""

import sqlite3, json, time, os, sys, argparse
import urllib.request, urllib.parse
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT     = Path(__file__).resolve().parents[3]
DB_PATH  = ROOT / "data" / "research" / "pulse_us" / "pulse_us_ohlcv.db"
RS_PATH  = ROOT / "data" / "rs_universe" / "latest.json"

LOOKBACK_DAYS = 420   # ~400 trading days + buffer
MIN_TICKERS_RS = 90   # RS percentile threshold to include


# ── DB init ────────────────────────────────────────────────────────────────
def _get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            ticker TEXT NOT NULL,
            date   TEXT NOT NULL,
            open   REAL,
            high   REAL,
            low    REAL,
            close  REAL,
            volume INTEGER,
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.commit()
    return conn


# ── Universe ───────────────────────────────────────────────────────────────
def load_universe(rs_min=MIN_TICKERS_RS):
    """Return list of tickers with RS >= rs_min from latest.json."""
    with open(RS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    universe = data.get("universe", {})
    tickers = []
    for tkr, item in universe.items():
        rs = float(item.get("rs_composite_pct") or item.get("rs_pct_3m") or 0)
        adtv = float(item.get("adtv_6m_usd") or 0)
        if rs >= rs_min and adtv >= 15_000_000:
            tickers.append(tkr)
    print(f"[universe] RS>={rs_min} + ADTV>=$15M: {len(tickers)} tickers")
    return tickers


# ── Tiingo fetch ───────────────────────────────────────────────────────────
def _fetch_tiingo(ticker, start, end, api_key):
    url = (
        f"https://api.tiingo.com/tiingo/daily/{ticker}/prices"
        f"?startDate={start}&endDate={end}&token={api_key}"
    )
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            rows = json.loads(resp.read())
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df = df.rename(columns={"adjClose": "close", "adjOpen": "open",
                                 "adjHigh": "high", "adjLow": "low"})
        df["ticker"] = ticker
        df["date"]   = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df[["ticker", "date", "open", "high", "low", "close", "volume"]]
    except Exception as e:
        print(f"  [tiingo] {ticker}: {e}")
        return None


# ── Yahoo Finance fallback ─────────────────────────────────────────────────
def _fetch_yahoo(ticker, start, end):
    try:
        s = int(datetime.strptime(start, "%Y-%m-%d").timestamp())
        e = int(datetime.strptime(end,   "%Y-%m-%d").timestamp()) + 86400
        url = (
            f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?period1={s}&period2={e}&interval=1d&events=adjsplits"
        )
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        q = result["indicators"]["quote"][0]
        adj = result["indicators"].get("adjclose", [{}])[0].get("adjclose", q["close"])
        rows = []
        for i, ts in enumerate(timestamps):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            rows.append({
                "ticker": ticker,
                "date":   dt,
                "open":   q["open"][i],
                "high":   q["high"][i],
                "low":    q["low"][i],
                "close":  adj[i] if adj else q["close"][i],
                "volume": int(q["volume"][i] or 0),
            })
        df = pd.DataFrame(rows).dropna(subset=["close"])
        return df[["ticker", "date", "open", "high", "low", "close", "volume"]]
    except Exception as e:
        print(f"  [yahoo] {ticker}: {e}")
        return None


# ── Upsert to DB ───────────────────────────────────────────────────────────
def _upsert(conn, df):
    if df is None or df.empty:
        return 0
    df = df.dropna(subset=["close"])
    rows = [tuple(r) for r in df[["ticker","date","open","high","low","close","volume"]].itertuples(index=False)]
    conn.executemany("""
        INSERT OR REPLACE INTO ohlcv (ticker, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    return len(rows)


# ── Main update ────────────────────────────────────────────────────────────
def update(rs_min=MIN_TICKERS_RS):
    tiingo_key = os.environ.get("TIINGO_API_KEY", "")
    conn       = _get_conn()
    tickers    = load_universe(rs_min)

    end   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    # Find last date per ticker in DB — only fetch what's missing
    existing = {}
    rows = conn.execute("SELECT ticker, MAX(date) FROM ohlcv GROUP BY ticker").fetchall()
    for tkr, last in rows:
        existing[tkr] = last

    ok = err = skipped = 0
    for i, tkr in enumerate(tickers):
        last = existing.get(tkr)
        fetch_start = start
        if last and last >= end:
            skipped += 1
            continue
        if last and last > start:
            # incremental: start from day after last
            next_day = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            fetch_start = next_day

        df = None
        if tiingo_key:
            df = _fetch_tiingo(tkr, fetch_start, end, tiingo_key)
            if df is not None and not df.empty:
                n = _upsert(conn, df)
                print(f"  [{i+1}/{len(tickers)}] {tkr}: tiingo +{n} rows")
                ok += 1
                time.sleep(0.3)   # ~3 req/sec — safe for free tier
                continue
            time.sleep(1)         # brief backoff (dead ticker or 429)

        # Yahoo fallback
        df = _fetch_yahoo(tkr, fetch_start, end)
        if df is not None and not df.empty:
            n = _upsert(conn, df)
            print(f"  [{i+1}/{len(tickers)}] {tkr}: yahoo  +{n} rows")
            ok += 1
        else:
            print(f"  [{i+1}/{len(tickers)}] {tkr}: FAIL (both sources)")
            err += 1
        time.sleep(0.5)

    conn.close()
    print(f"\n[done] ok={ok}  err={err}  skipped={skipped}  db={DB_PATH}")
    return err == 0


def status():
    if not DB_PATH.exists():
        print("[status] DB not found")
        return
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT ticker, COUNT(*) as n, MIN(date) as first, MAX(date) as last
        FROM ohlcv GROUP BY ticker ORDER BY last DESC
    """).fetchall()
    conn.close()
    print(f"[status] {len(rows)} tickers in pulse_us_ohlcv.db")
    for r in rows[:10]:
        print(f"  {r[0]:6s}  {r[1]:4d} rows  {r[2]} → {r[3]}")
    if len(rows) > 10:
        print(f"  ... and {len(rows)-10} more")


def run():
    """Entry point for pre_market_runner."""
    update()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--rs-min", type=float, default=MIN_TICKERS_RS)
    args = ap.parse_args()
    if args.status:
        status()
    else:
        ok = update(rs_min=args.rs_min)
        sys.exit(0 if ok else 1)
