# -*- coding: utf-8 -*-
"""
01c_download_sectors.py — Download sector ETFs + build ticker→sector mapping.

Outputs:
  data/backtest/sector_prices.parquet  — daily OHLCV for 11 sector ETFs
  data/backtest/ticker_sector.json     — {ticker: sector_etf} mapping

Sector ETFs (SPDR GICS):
  XLK  → Technology          XLF  → Financials
  XLV  → Health Care         XLE  → Energy
  XLI  → Industrials         XLP  → Consumer Staples
  XLY  → Consumer Disc.      XLU  → Utilities
  XLB  → Materials           XLRE → Real Estate
  XLC  → Communication Svcs
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import ssl as _ssl
_ssl._create_default_https_context = _ssl._create_unverified_context
import urllib3; urllib3.disable_warnings()

import os
os.chdir(str(__import__('pathlib').Path(__file__).resolve().parents[2]))
sys.path.insert(0, 'scripts')

import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from utils.data_engine import _YF_SESSION, _get_yahoo_crumb

ROOT     = Path(__file__).resolve().parents[2]
BT_DIR   = ROOT / "data" / "backtest"

SECTOR_ETFS = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLV":  "Health Care",
    "XLE":  "Energy",
    "XLI":  "Industrials",
    "XLP":  "Consumer Staples",
    "XLY":  "Consumer Discretionary",
    "XLU":  "Utilities",
    "XLB":  "Materials",
    "XLRE": "Real Estate",
    "XLC":  "Communication Services",
}

# GICS sector name → ETF ticker (must match Wikipedia column exactly)
GICS_TO_ETF = {
    "Information Technology":    "XLK",
    "Financials":                "XLF",
    "Health Care":               "XLV",
    "Energy":                    "XLE",
    "Industrials":               "XLI",
    "Consumer Staples":          "XLP",
    "Consumer Discretionary":    "XLY",
    "Utilities":                 "XLU",
    "Materials":                 "XLB",
    "Real Estate":               "XLRE",
    "Communication Services":    "XLC",
}

START_TS = int(pd.Timestamp("2015-01-01").timestamp())
END_TS   = int(pd.Timestamp(datetime.today().strftime("%Y-%m-%d")).timestamp())


def fetch_ticker(ticker: str, crumb) -> pd.DataFrame | None:
    url    = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"period1": START_TS, "period2": END_TS, "interval": "1d",
              "includeAdjustedClose": "true"}
    if crumb:
        params["crumb"] = crumb
    try:
        r = _YF_SESSION.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return None
        result = r.json().get("chart", {}).get("result", [])
        if not result:
            return None
        res = result[0]
        ts  = res.get("timestamp", [])
        if not ts:
            return None
        q   = res["indicators"]["quote"][0]
        adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose", q["close"])
        dates = pd.to_datetime(ts, unit="s", utc=True).tz_localize(None).normalize()
        df = pd.DataFrame({
            "date":      dates.strftime("%Y-%m-%d"),
            "open":      q.get("open",   [np.nan]*len(ts)),
            "high":      q.get("high",   [np.nan]*len(ts)),
            "low":       q.get("low",    [np.nan]*len(ts)),
            "close":     q.get("close",  [np.nan]*len(ts)),
            "volume":    q.get("volume", [0]*len(ts)),
            "adj_close": adj if adj else q["close"],
            "ticker":    ticker,
        })
        return df.dropna(subset=["close"])
    except Exception as e:
        print(f"  ERROR {ticker}: {e}")
        return None


def build_ticker_sector_map() -> dict:
    """Scrape Wikipedia S&P500 table for GICS sector per ticker."""
    print("Fetching S&P500 sector map from Wikipedia...")
    r = _YF_SESSION.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", timeout=20)
    tables = pd.read_html(pd.io.common.StringIO(r.text))
    df = tables[0][["Symbol", "GICS Sector"]].copy()
    df["Symbol"] = df["Symbol"].str.replace(".", "-", regex=False)

    mapping = {}
    unmapped = []
    for _, row in df.iterrows():
        ticker = row["Symbol"]
        gics   = row["GICS Sector"]
        etf    = GICS_TO_ETF.get(gics)
        if etf:
            mapping[ticker] = etf
        else:
            unmapped.append((ticker, gics))

    print(f"  Mapped: {len(mapping)} tickers")
    if unmapped:
        print(f"  Unmapped ({len(unmapped)}): {unmapped[:5]}")
    return mapping


def main():
    crumb = _get_yahoo_crumb()
    print(f"Crumb: {crumb[:20] if crumb else 'None'}\n")

    # 1. Download sector ETFs
    print("Downloading sector ETFs...")
    frames = []
    for etf, sector_name in SECTOR_ETFS.items():
        df = fetch_ticker(etf, crumb)
        if df is not None and len(df) > 100:
            frames.append(df)
            print(f"  {etf} ({sector_name}): {len(df)} rows OK")
        else:
            print(f"  {etf}: FAIL")

    if frames:
        sector_prices = pd.concat(frames, ignore_index=True)
        sector_prices.to_parquet(BT_DIR / "sector_prices.parquet", index=False)
        print(f"\nSaved sector_prices.parquet: {len(frames)} ETFs, {len(sector_prices):,} rows")
    else:
        print("No sector ETF data downloaded!")
        return

    # 2. Build ticker→sector ETF mapping
    mapping = build_ticker_sector_map()
    out_path = BT_DIR / "ticker_sector.json"
    with open(out_path, "w") as f:
        json.dump(mapping, f, indent=2)
    print(f"Saved ticker_sector.json: {len(mapping)} tickers mapped")

    # Show distribution
    from collections import Counter
    dist = Counter(mapping.values())
    print("\nSector distribution:")
    for etf, count in sorted(dist.items(), key=lambda x: -x[1]):
        print(f"  {etf:6s} ({SECTOR_ETFS[etf]:30s}): {count} stocks")


if __name__ == "__main__":
    main()
