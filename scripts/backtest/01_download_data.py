# -*- coding: utf-8 -*-
"""
Step 1: Download historical price data for backtest universe.
Parallel download (5 threads), Yahoo Finance query2 API, verify=False for WARP.
Universe: S&P 500 current constituents + QQQ/SPY/IWM benchmarks (~506 tickers).
Period: 2015-01-01 to today (~10 years).
Storage: data/backtest/prices.parquet
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import ssl as _ssl
_ssl._create_default_https_context = _ssl._create_unverified_context
import urllib3; urllib3.disable_warnings()

import os
os.chdir(str(__import__('pathlib').Path(__file__).resolve().parents[2]))
sys.path.insert(0, 'scripts')

import pandas as pd
import numpy as np
import time, json, concurrent.futures
from pathlib import Path
from datetime import datetime

from utils.data_engine import _YF_SESSION, _get_yahoo_crumb

ROOT     = Path(__file__).resolve().parents[2]
OUT_DIR  = ROOT / "data" / "backtest"
OUT_DIR.mkdir(parents=True, exist_ok=True)
PRICE_FILE  = OUT_DIR / "prices.parquet"
CHECKPOINT  = OUT_DIR / "download_checkpoint.json"

START_TS = int(pd.Timestamp("2015-01-01").timestamp())
END_TS   = int(pd.Timestamp(datetime.today().strftime("%Y-%m-%d")).timestamp())
BENCHMARKS = ["QQQ", "SPY", "IWM"]
N_THREADS  = 5   # parallel workers


def get_sp500_tickers() -> list[str]:
    r = _YF_SESSION.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", timeout=20)
    tables = pd.read_html(pd.io.common.StringIO(r.text))
    tickers = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
    print(f"S&P500 loaded: {len(tickers)} tickers")
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
        dates = pd.to_datetime(ts, unit="s", utc=True).tz_localize(None).normalize()
        df = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"),
                           "open":   q.get("open",   [np.nan]*len(ts)),
                           "high":   q.get("high",   [np.nan]*len(ts)),
                           "low":    q.get("low",    [np.nan]*len(ts)),
                           "close":  adj,
                           "volume": q.get("volume", [0]*len(ts)),
                           "ticker": ticker})
        df = df.dropna(subset=["close"])
        return ticker, df
    except Exception as e:
        return ticker, None


def download_all(tickers: list[str]):
    checkpoint = json.loads(CHECKPOINT.read_text()) if CHECKPOINT.exists() else {"done": []}
    done_set   = set(checkpoint["done"])

    all_frames: list[pd.DataFrame] = []
    if PRICE_FILE.exists():
        existing = pd.read_parquet(PRICE_FILE)
        all_frames.append(existing)
        print(f"Existing data: {len(existing):,} rows, {existing['ticker'].nunique()} tickers")

    remaining = [t for t in tickers if t not in done_set]
    print(f"Remaining: {len(remaining)} tickers | threads: {N_THREADS}")

    crumb = _get_yahoo_crumb()
    print(f"Crumb: {'ok' if crumb else 'none'}")

    total   = len(remaining)
    done_n  = 0
    t_start = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=N_THREADS) as ex:
        futures = {ex.submit(fetch_ticker, t, crumb): t for t in remaining}
        for fut in concurrent.futures.as_completed(futures):
            ticker, df = fut.result()
            done_n += 1
            if df is not None and len(df) > 0:
                all_frames.append(df)
                rows = len(df)
            else:
                rows = 0

            done_set.add(ticker)
            elapsed = time.time() - t_start
            rate    = done_n / elapsed
            eta     = (total - done_n) / rate if rate > 0 else 0
            print(f"[{done_n}/{total}] {ticker}: {rows} rows | ETA {eta/60:.1f}m")

            # Checkpoint every 50 completions
            if done_n % 50 == 0:
                checkpoint["done"] = list(done_set)
                CHECKPOINT.write_text(json.dumps(checkpoint))
                combined = pd.concat(all_frames, ignore_index=True)
                combined = combined.drop_duplicates(subset=["ticker", "date"])
                combined.to_parquet(PRICE_FILE, index=False)
                print(f"  [checkpoint] {len(combined):,} rows, {combined['ticker'].nunique()} tickers")

    # Final save
    combined = pd.concat(all_frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["ticker", "date"])
    combined = combined.sort_values(["ticker", "date"]).reset_index(drop=True)
    combined.to_parquet(PRICE_FILE, index=False)
    checkpoint["done"] = list(done_set)
    CHECKPOINT.write_text(json.dumps(checkpoint))

    elapsed = time.time() - t_start
    print(f"\nDone in {elapsed/60:.1f}m")
    print(f"  Total rows : {len(combined):,}")
    print(f"  Tickers    : {combined['ticker'].nunique()}")
    print(f"  Date range : {combined['date'].min()} to {combined['date'].max()}")
    return combined


if __name__ == "__main__":
    sp500    = get_sp500_tickers()
    tickers  = list(dict.fromkeys(BENCHMARKS + sp500))
    download_all(tickers)
