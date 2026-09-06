"""
AlphaAbsolute — Market Cap Cache Builder
=========================================
Fetches market cap for all theme-mapped tickers from Finnhub.
Saves to data/themes/market_cap_cache.json.

Used by Monster Scout v2 for the $20B market cap hard gate.

Run: manually when needed, or weekly via pre_market_runner.py
Sources: Finnhub /stock/profile2 (primary) → FMP /v3/profile (fallback)
Rate limit: Finnhub free = 60 req/min → 1.1s sleep between calls
Cost: $0 (existing API keys)
"""

from __future__ import annotations
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

OUT_FILE = ROOT / "data" / "themes" / "market_cap_cache.json"
OUT_FILE.parent.mkdir(parents=True, exist_ok=True)


def _load_env() -> tuple[str, str]:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    return os.getenv("FINNHUB_API_KEY", ""), os.getenv("FMP_API_KEY", "")


def _get_theme_tickers() -> list[str]:
    """Load all tickers from theme_rs_latest.json."""
    theme_file = ROOT / "data" / "rs_universe" / "theme_rs_latest.json"
    if not theme_file.exists():
        return []
    try:
        data = json.loads(theme_file.read_text(encoding="utf-8"))
        themes = data.get("themes", data)
        tickers: set[str] = set()
        for theme_id, td in themes.items():
            if theme_id.startswith("_") or not isinstance(td, dict):
                continue
            members = td.get("members", {})
            iter_m = members.keys() if isinstance(members, dict) else members
            for t in iter_m:
                if isinstance(t, str):
                    tickers.add(t.upper())
        return sorted(tickers)
    except Exception as e:
        print(f"  [WARN] Could not load theme tickers: {e}")
        return []


def _load_existing_cache() -> dict:
    """Load existing cache to avoid refetching recent data."""
    if OUT_FILE.exists():
        try:
            return json.loads(OUT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_updated": None, "tickers": {}}


def _fetch_finnhub(ticker: str, api_key: str) -> float | None:
    """Fetch market cap from Finnhub /stock/profile2.
    Returns market cap in USD, or None on failure.
    """
    try:
        import urllib.request
        url = f"https://finnhub.io/api/v1/stock/profile2?symbol={ticker}&token={api_key}"
        req = urllib.request.urlopen(url, timeout=8)
        data = json.loads(req.read().decode("utf-8"))
        mc = data.get("marketCapitalization")  # in millions
        if mc and mc > 0:
            return float(mc) * 1_000_000  # convert to dollars
    except Exception:
        pass
    return None


def _fetch_fmp(ticker: str, api_key: str) -> float | None:
    """Fetch market cap from FMP /v3/profile as fallback.
    Returns market cap in USD, or None on failure.
    """
    try:
        import urllib.request
        url = f"https://financialmodelingprep.com/api/v3/profile/{ticker}?apikey={api_key}"
        req = urllib.request.urlopen(url, timeout=8)
        data = json.loads(req.read().decode("utf-8"))
        if isinstance(data, list) and data:
            mc = data[0].get("mktCap")
            if mc and mc > 0:
                return float(mc)
    except Exception:
        pass
    return None


def run(force_refresh: bool = False, max_tickers: int | None = None) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*55}")
    print(f"  Market Cap Cache Builder  [{today}]")
    print(f"{'='*55}")

    fh_key, fmp_key = _load_env()
    if not fh_key and not fmp_key:
        print("  [ERROR] No API keys found (FINNHUB_API_KEY, FMP_API_KEY)")
        return {}

    tickers = _get_theme_tickers()
    if not tickers:
        print("  [ERROR] No theme tickers found — run rs_theme_ranker.py first")
        return {}

    if max_tickers:
        tickers = tickers[:max_tickers]

    print(f"  Fetching market cap for {len(tickers)} theme tickers...")

    # Load existing cache
    cache = _load_existing_cache()
    ticker_data = cache.get("tickers", {})

    # Check how stale existing data is (skip if < 7 days old)
    cache_date = cache.get("last_updated", "")
    fresh_enough = False
    if cache_date and not force_refresh:
        try:
            from datetime import date
            d = date.fromisoformat(cache_date[:10])
            age_days = (date.today() - d).days
            if age_days < 7:
                fresh_enough = True
                print(f"  Cache is {age_days} days old — only fetching missing tickers")
        except Exception:
            pass

    fetched = 0
    skipped = 0
    failed = 0

    for i, ticker in enumerate(tickers):
        # Skip if already in cache and fresh enough
        if fresh_enough and ticker in ticker_data and ticker_data[ticker].get("market_cap"):
            skipped += 1
            continue

        # Try Finnhub first
        mc = _fetch_finnhub(ticker, fh_key) if fh_key else None

        # Fallback to FMP
        if mc is None and fmp_key:
            mc = _fetch_fmp(ticker, fmp_key)
            if mc:
                print(f"  [{i+1}/{len(tickers)}] {ticker}: ${mc/1e9:.2f}B (FMP fallback)")
            else:
                failed += 1
                ticker_data[ticker] = {"market_cap": None, "fetched": today}
                time.sleep(0.5)
                continue

        if mc:
            # Bucket classification
            if mc < 2_000_000_000:
                bucket = "elite"       # <$2B
            elif mc < 10_000_000_000:
                bucket = "standard"    # $2B-$10B
            elif mc < 20_000_000_000:
                bucket = "large"       # $10B-$20B
            else:
                bucket = "mega"        # >$20B (fails Monster Scout gate)

            ticker_data[ticker] = {
                "market_cap":    round(mc, 0),
                "market_cap_b":  round(mc / 1_000_000_000, 2),
                "bucket":        bucket,
                "passes_20b":    mc < 20_000_000_000,
                "fetched":       today,
            }
            fetched += 1
            if (i + 1) % 10 == 0:
                print(f"  [{i+1}/{len(tickers)}] {ticker}: ${mc/1e9:.2f}B ({bucket})")
        else:
            failed += 1
            ticker_data[ticker] = {"market_cap": None, "fetched": today}

        # Rate limit: Finnhub free = 60 req/min
        time.sleep(1.1)

    # Save updated cache
    result = {
        "last_updated": today,
        "source":       "finnhub_profile2 + fmp_profile fallback",
        "total_tickers": len(ticker_data),
        "fetched_today": fetched,
        "skipped_fresh": skipped,
        "failed":        failed,
        "tickers":       ticker_data,
    }

    OUT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"\n  Fetched: {fetched} | Skipped (fresh): {skipped} | Failed: {failed}")
    print(f"  -> Written: {OUT_FILE}")

    # Quick summary by bucket
    buckets: dict[str, int] = {}
    for td in ticker_data.values():
        b = td.get("bucket", "unknown")
        buckets[b] = buckets.get(b, 0) + 1
    print("  Bucket summary:", buckets)

    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Force refresh all tickers")
    parser.add_argument("--max", type=int, default=None, help="Max tickers to fetch (for testing)")
    args = parser.parse_args()
    run(force_refresh=args.force, max_tickers=args.max)
