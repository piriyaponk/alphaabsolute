# -*- coding: utf-8 -*-
"""
Expand universe: S&P500 + Russell 1000 + NASDAQ 100
Downloads only tickers NOT already in prices.parquet.
Target: ~1,000-1,200 unique tickers with $15M+ ADTV.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import ssl as _ssl
_ssl._create_default_https_context = _ssl._create_unverified_context
import urllib3; urllib3.disable_warnings()

import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, 'scripts')

import pandas as pd
import numpy as np
import time, json, concurrent.futures
from pathlib import Path
from utils.data_engine import _YF_SESSION, _get_yahoo_crumb

BT_DIR     = Path(ROOT) / "data" / "backtest"
PRICE_FILE = BT_DIR / "prices.parquet"
CHECKPOINT = BT_DIR / "expand_checkpoint.json"

START_TS = int(pd.Timestamp("2015-01-01").timestamp())
END_TS   = int(pd.Timestamp("2026-08-28").timestamp())
N_THREADS = 6


def get_russell1000() -> list[str]:
    """iShares Russell 1000 ETF holdings page via Wikipedia-like approach."""
    # Use iShares IWB ETF constituents from Wikipedia table
    # Fallback: scrape from known public source
    try:
        r = _YF_SESSION.get(
            "https://en.wikipedia.org/wiki/Russell_1000_Index",
            timeout=15
        )
        tables = pd.read_html(pd.io.common.StringIO(r.text))
        for t in tables:
            if "Symbol" in t.columns or "Ticker" in t.columns:
                col = "Symbol" if "Symbol" in t.columns else "Ticker"
                tickers = t[col].dropna().str.replace(".", "-", regex=False).tolist()
                if len(tickers) > 50:
                    print(f"Russell 1000 from Wikipedia: {len(tickers)} tickers")
                    return tickers
    except Exception as e:
        print(f"Wikipedia failed: {e}")

    # Fallback: use iShares IWB ETF holdings CSV
    try:
        r = _YF_SESSION.get(
            "https://www.ishares.com/us/products/239707/ISHARES-RUSSELL-1000-ETF/1467271812596.ajax?fileType=csv&fileName=IWB_holdings&dataType=fund",
            timeout=20
        )
        from io import StringIO
        text = r.text
        # Skip header rows
        lines = text.split('\n')
        start_row = next(i for i, l in enumerate(lines) if 'Ticker' in l or 'ticker' in l)
        df = pd.read_csv(StringIO('\n'.join(lines[start_row:])))
        col = [c for c in df.columns if 'ticker' in c.lower() or 'symbol' in c.lower()][0]
        tickers = df[col].dropna().str.strip().str.replace(".", "-", regex=False).tolist()
        tickers = [t for t in tickers if len(t) >= 1 and len(t) <= 5 and t.isalpha()]
        print(f"Russell 1000 from iShares: {len(tickers)} tickers")
        return tickers
    except Exception as e:
        print(f"iShares failed: {e}")
        return []


def get_nasdaq100() -> list[str]:
    """NASDAQ 100 from Wikipedia."""
    r = _YF_SESSION.get(
        "https://en.wikipedia.org/wiki/Nasdaq-100",
        timeout=15
    )
    tables = pd.read_html(pd.io.common.StringIO(r.text))
    for t in tables:
        for col in ["Ticker", "Symbol", "ticker"]:
            if col in t.columns and len(t) > 80:
                tickers = t[col].dropna().str.replace(".", "-", regex=False).tolist()
                print(f"NASDAQ 100 from Wikipedia: {len(tickers)} tickers")
                return tickers
    return []


def get_sp500() -> list[str]:
    r = _YF_SESSION.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", timeout=15)
    tables = pd.read_html(pd.io.common.StringIO(r.text))
    tickers = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
    print(f"S&P500: {len(tickers)} tickers")
    return tickers


def fetch_ticker(ticker: str, crumb: str | None) -> tuple[str, pd.DataFrame | None]:
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"period1": START_TS, "period2": END_TS, "interval": "1d",
              "events": "div,splits", "includeAdjustedClose": "true"}
    if crumb:
        params["crumb"] = crumb
    try:
        r = _YF_SESSION.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return ticker, None
        result = r.json().get("chart", {}).get("result", [])
        if not result:
            return ticker, None
        res = result[0]
        ts  = res.get("timestamp", [])
        if not ts:
            return ticker, None
        q   = res["indicators"]["quote"][0]
        adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose", q["close"])
        dates = pd.to_datetime(ts, unit="s", utc=True).tz_localize(None).normalize().strftime("%Y-%m-%d")
        df = pd.DataFrame({"date": dates, "open": q.get("open"), "high": q.get("high"),
                           "low": q.get("low"), "close": adj, "volume": q.get("volume"),
                           "ticker": ticker})
        return ticker, df.dropna(subset=["close"])
    except Exception:
        return ticker, None


def expand_universe():
    # Load existing tickers
    existing = pd.read_parquet(PRICE_FILE)
    existing_tickers = set(existing["ticker"].unique())
    print(f"Existing: {len(existing_tickers)} tickers")

    # Gather all target tickers
    sp500   = get_sp500()
    nasdaq  = get_nasdaq100()
    russell = get_russell1000()

    all_targets = list(dict.fromkeys(sp500 + nasdaq + russell))
    new_tickers = [t for t in all_targets if t not in existing_tickers]
    print(f"Total target universe: {len(all_targets)} | New to download: {len(new_tickers)}")

    if not new_tickers:
        print("Nothing new to download.")
        return existing

    # Checkpoint
    chk = json.loads(CHECKPOINT.read_text()) if CHECKPOINT.exists() else {"done": []}
    done_set = set(chk["done"])
    remaining = [t for t in new_tickers if t not in done_set]
    print(f"Remaining after checkpoint: {len(remaining)}")

    crumb = _get_yahoo_crumb()
    print(f"Crumb: {'ok' if crumb else 'none'}")

    all_frames = [existing]
    total = len(remaining)
    done_n = 0
    t0 = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=N_THREADS) as ex:
        futures = {ex.submit(fetch_ticker, t, crumb): t for t in remaining}
        for fut in concurrent.futures.as_completed(futures):
            ticker, df = fut.result()
            done_n += 1
            if df is not None and len(df) > 100:
                all_frames.append(df)
                rows = len(df)
            else:
                rows = 0
            done_set.add(ticker)
            eta = ((time.time() - t0) / done_n) * (total - done_n)
            print(f"[{done_n}/{total}] {ticker}: {rows} rows | ETA {eta/60:.1f}m")

            if done_n % 100 == 0:
                chk["done"] = list(done_set)
                CHECKPOINT.write_text(json.dumps(chk))
                combined = pd.concat(all_frames, ignore_index=True).drop_duplicates(["ticker","date"])
                combined.to_parquet(PRICE_FILE, index=False)
                print(f"  Saved: {len(combined):,} rows, {combined['ticker'].nunique()} tickers")

    # Final save
    combined = pd.concat(all_frames, ignore_index=True).drop_duplicates(["ticker","date"])
    combined = combined.sort_values(["ticker","date"]).reset_index(drop=True)
    combined.to_parquet(PRICE_FILE, index=False)
    chk["done"] = list(done_set)
    CHECKPOINT.write_text(json.dumps(chk))

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed/60:.1f}m")
    print(f"  Total rows : {len(combined):,}")
    print(f"  Tickers    : {combined['ticker'].nunique()}")
    print(f"  Date range : {combined['date'].min()} to {combined['date'].max()}")
    return combined


if __name__ == "__main__":
    expand_universe()
