# -*- coding: utf-8 -*-
"""
01b_patch_missing.py — Download missing S&P500 tickers and merge into prices.parquet
Run after 01_download_data.py if some tickers failed.
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
import time
import concurrent.futures
from pathlib import Path
from datetime import datetime
from utils.data_engine import _YF_SESSION, _get_yahoo_crumb

ROOT       = Path(__file__).resolve().parents[2]
OUT_DIR    = ROOT / "data" / "backtest"
PRICE_FILE = OUT_DIR / "prices.parquet"

START_TS = int(pd.Timestamp("2015-01-01").timestamp())
END_TS   = int(pd.Timestamp(datetime.today().strftime("%Y-%m-%d")).timestamp())


def fetch_ticker(ticker: str, crumb: str | None) -> tuple[str, pd.DataFrame | None]:
    url    = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
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
        df = pd.DataFrame({
            "date":   dates.strftime("%Y-%m-%d"),
            "open":   q.get("open",   [np.nan] * len(ts)),
            "high":   q.get("high",   [np.nan] * len(ts)),
            "low":    q.get("low",    [np.nan] * len(ts)),
            "close":  q.get("close",  [np.nan] * len(ts)),
            "volume": q.get("volume", [0]      * len(ts)),
            "adj_close": adj if adj else q["close"],
            "ticker": ticker,
        })
        df = df.dropna(subset=["close"])
        return ticker, df
    except Exception as e:
        return ticker, None


def main():
    # Load existing prices
    existing = pd.read_parquet(PRICE_FILE)
    have     = set(existing["ticker"].unique())
    print(f"Existing prices: {len(have)} tickers, {len(existing):,} rows")

    # Get full S&P500 list
    r = _YF_SESSION.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", timeout=20)
    tables  = pd.read_html(pd.io.common.StringIO(r.text))
    sp500   = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
    missing = [t for t in sp500 if t not in have]
    print(f"S&P500: {len(sp500)} tickers | Missing: {len(missing)}")

    if not missing:
        print("Nothing to download.")
        return

    # Get crumb once
    crumb = _get_yahoo_crumb()
    print(f"Crumb: {crumb[:20] if crumb else 'None'}")

    # Download in parallel (5 threads)
    results = []
    failed  = []
    done    = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futs = {pool.submit(fetch_ticker, t, crumb): t for t in missing}
        for fut in concurrent.futures.as_completed(futs):
            ticker, df = fut.result()
            done += 1
            if df is not None and len(df) > 100:
                results.append(df)
                print(f"  [{done}/{len(missing)}] {ticker}: {len(df)} rows OK")
            else:
                failed.append(ticker)
                print(f"  [{done}/{len(missing)}] {ticker}: FAIL")

    if results:
        new_data = pd.concat(results, ignore_index=True)
        combined = pd.concat([existing, new_data], ignore_index=True)
        combined = combined.drop_duplicates(subset=["ticker", "date"])
        combined = combined.sort_values(["ticker", "date"]).reset_index(drop=True)
        combined.to_parquet(PRICE_FILE, index=False)
        print(f"\nSaved: {combined['ticker'].nunique()} tickers, {len(combined):,} rows")
        print(f"Added: {len(results)} new tickers")
    else:
        print("No new data downloaded.")

    if failed:
        print(f"Failed ({len(failed)}): {failed}")


if __name__ == "__main__":
    main()
