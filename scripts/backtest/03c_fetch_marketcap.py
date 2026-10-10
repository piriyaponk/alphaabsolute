# -*- coding: utf-8 -*-
"""
Fetch shares outstanding for all tickers → compute historical market cap.
market_cap_t = close_t × shares_outstanding_fetched

Limitation (noted): uses CURRENT shares outstanding as approximation.
For S&P500 large-caps, shares change <5% per year → acceptable proxy.
Stores market_cap_B (billions) in data/backtest/market_cap.parquet
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import os, time, json, ssl
import pandas as pd
import numpy as np
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT    = Path(__file__).resolve().parents[2]
BT_DIR  = ROOT / "data" / "backtest"
CACHE_F = BT_DIR / "shares_outstanding.json"

os.chdir(ROOT)
ssl._create_default_https_context = ssl._create_unverified_context

# ── Yahoo session (same as data_engine) ──────────────────────
sys.path.insert(0, str(ROOT / "scripts"))
from utils.data_engine import _YF_SESSION as SESSION

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

def fetch_shares(ticker: str) -> float | None:
    """Return shares outstanding (raw count) for a ticker."""
    url = (f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
           f"?modules=defaultKeyStatistics")
    try:
        r = SESSION.get(url, headers=HEADERS, timeout=10, verify=False)
        if r.status_code != 200:
            return None
        data = r.json()
        ks = data["quoteSummary"]["result"][0]["defaultKeyStatistics"]
        return ks.get("sharesOutstanding", {}).get("raw")
    except Exception:
        return None


def main():
    sig     = pd.read_parquet(BT_DIR / "signals.parquet")
    tickers = [t for t in sig["ticker"].unique()
               if t not in ("QQQ", "SPY", "IWM")]
    print(f"Tickers to fetch: {len(tickers)}")

    # Load cache
    cache = {}
    if CACHE_F.exists():
        cache = json.loads(CACHE_F.read_text())
        print(f"  Loaded {len(cache)} cached entries")

    missing = [t for t in tickers if t not in cache]
    print(f"  Need to fetch: {len(missing)}")

    # Fetch in parallel (5 threads, Yahoo-friendly)
    def fetch_and_cache(ticker):
        shares = fetch_shares(ticker)
        return ticker, shares

    if missing:
        done = 0
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(fetch_and_cache, t): t for t in missing}
            for fut in as_completed(futures):
                ticker, shares = fut.result()
                cache[ticker] = shares
                done += 1
                if done % 50 == 0:
                    CACHE_F.write_text(json.dumps(cache))
                    print(f"  {done}/{len(missing)} fetched, saved cache")
                time.sleep(0.05)  # polite

        CACHE_F.write_text(json.dumps(cache))
        print(f"  Done. Saved {len(cache)} entries.")

    # ── Compute market_cap ────────────────────────────────────
    sig["shares_out"] = sig["ticker"].map(lambda t: cache.get(t))
    sig["market_cap_B"] = (sig["close"] * sig["shares_out"]) / 1e9   # billions

    null_pct = sig["market_cap_B"].isna().mean() * 100
    print(f"\nmarket_cap_B: {null_pct:.1f}% null")
    print(f"  Median mktcap: ${sig['market_cap_B'].median():.1f}B")
    print(f"  Max mktcap:    ${sig['market_cap_B'].max():.0f}B")

    # Save slim file: date, ticker, market_cap_B
    out = sig[["date", "ticker", "market_cap_B"]].copy()
    out.to_parquet(BT_DIR / "market_cap.parquet", index=False)
    print(f"\nSaved: data/backtest/market_cap.parquet ({len(out):,} rows)")
    print("NOTE: shares outstanding = current values, not historical quarterly")
    print("      For S&P500 large-caps this is a reasonable approximation.")


if __name__ == "__main__":
    main()
