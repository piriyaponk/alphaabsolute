"""
AlphaAbsolute — Batch Name + SIC Fetcher
=========================================
Uses Polygon /v3/reference/tickers (list endpoint) to fetch company names and
SIC codes for all universe tickers in 2-3 API calls instead of 1,000+ individual calls.

Populates data/themes/company_info_cache/{ticker}.json with name + SIC,
so theme_labeler can match on company name without needing full descriptions
for every ticker.

Speed: 2-3 calls × 13s delay = ~30 seconds vs 219 minutes for individual calls.

Usage:
  python scripts/pre_compute/batch_name_fetch.py
  python scripts/pre_compute/batch_name_fetch.py --force   # re-fetch all
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

import urllib3
import requests
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR          = Path(__file__).resolve().parents[2]
COMPANY_CACHE_DIR = BASE_DIR / "data" / "themes" / "company_info_cache"
RS_UNIVERSE_FILE  = BASE_DIR / "data" / "rs_universe" / "latest.json"
LABELS_FILE       = BASE_DIR / "data" / "themes" / "ticker_labels.json"
COMPANY_CACHE_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))


def _load_env():
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for ln in env_path.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()
POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "")


def fetch_polygon_ticker_batch(cursor: str | None = None) -> tuple[list, str | None]:
    """
    Fetch one page (up to 1,000) of ticker reference data from Polygon.
    Returns (results_list, next_cursor).
    """
    url = "https://api.polygon.io/v3/reference/tickers"
    params = {
        "market":   "stocks",
        "locale":   "us",
        "active":   "true",
        "limit":    1000,
        "sort":     "ticker",
        "order":    "asc",
        "apiKey":   POLYGON_KEY,
    }
    if cursor:
        params["cursor"] = cursor

    resp = requests.get(url, params=params, timeout=15, verify=False)
    if resp.status_code == 429:
        print("  [429] Rate limited — sleeping 65s")
        time.sleep(65)
        resp = requests.get(url, params=params, timeout=15, verify=False)

    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])

    # Pagination: Polygon returns next_url with cursor embedded
    next_cursor = None
    next_url = data.get("next_url", "")
    if next_url and "cursor=" in next_url:
        next_cursor = next_url.split("cursor=")[-1].split("&")[0]

    return results, next_cursor


def save_to_cache(ticker: str, name: str, sic: str, exchange: str = "",
                  description: str = "") -> None:
    path = COMPANY_CACHE_DIR / f"{ticker}.json"
    # Don't overwrite a cache file that has a real description
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("description") and not description:
                return  # keep the richer existing file
        except Exception:
            pass
    data = {
        "name":        name,
        "description": description,
        "sic_code":    sic,
        "primary_exchange": exchange,
        "fetched":     datetime.now().isoformat(),
        "source":      "polygon_batch",
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def run(force: bool = False) -> dict:
    if not POLYGON_KEY:
        print("[!] POLYGON_API_KEY not set")
        return {}

    # Load our universe
    if not RS_UNIVERSE_FILE.exists():
        print(f"[!] RS universe not found: {RS_UNIVERSE_FILE}")
        return {}
    rs_data = json.loads(RS_UNIVERSE_FILE.read_text(encoding="utf-8"))
    universe_tickers = {r["ticker"] for r in rs_data.get("ranked", [])}

    # Load already-labeled (skip those)
    labeled: set = set()
    if LABELS_FILE.exists():
        lf = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
        labeled = set(lf.get("labels", {}).keys())

    # Already cached
    cached = {f.stem for f in COMPANY_CACHE_DIR.glob("*.json")}
    if not force:
        need = universe_tickers - cached
    else:
        need = universe_tickers

    print(f"\n{'='*55}")
    print(f"  Batch Name Fetch — Polygon reference/tickers")
    print(f"{'='*55}")
    print(f"  Universe:     {len(universe_tickers)} tickers")
    print(f"  Already cached: {len(cached)}")
    print(f"  Need batch:   {len(need)}")

    # Fetch all Polygon tickers (paginated, 1,000/page)
    print(f"\n  Fetching Polygon ticker list (paginated 1,000/page)...")
    all_polygon: dict[str, dict] = {}
    cursor = None
    page = 0

    while True:
        page += 1
        print(f"  Page {page}...", end=" ", flush=True)
        try:
            results, cursor = fetch_polygon_ticker_batch(cursor)
        except Exception as e:
            print(f"\n  [ERR] Page {page}: {e}")
            break

        for r in results:
            t = r.get("ticker", "")
            if t:
                all_polygon[t] = {
                    "name":        r.get("name", ""),
                    "sic_code":    str(r.get("sic_code", "") or ""),
                    "exchange":    r.get("primary_exchange", ""),
                }
        print(f"{len(results)} tickers", flush=True)

        if not cursor:
            break
        # Respect rate limit between pages (Polygon free = 5 req/min)
        time.sleep(13)

    print(f"\n  Total from Polygon: {len(all_polygon)} tickers")

    # Match our universe against Polygon results
    found = n_saved = 0
    for ticker in sorted(need):
        if ticker in all_polygon:
            info = all_polygon[ticker]
            save_to_cache(
                ticker   = ticker,
                name     = info["name"],
                sic      = info["sic_code"],
                exchange = info["exchange"],
            )
            n_saved += 1
            found += 1

    not_found = need - set(all_polygon.keys())
    print(f"\n  Saved to cache: {n_saved} tickers")
    print(f"  Not in Polygon: {len(not_found)} (OTC / delisted)")
    if not_found:
        print(f"  Examples: {sorted(not_found)[:10]}")

    # Summary
    new_cached = {f.stem for f in COMPANY_CACHE_DIR.glob("*.json")}
    print(f"\n  Cache now covers: {len(new_cached)} tickers")
    print(f"  Still need individual fetch: {len(universe_tickers - new_cached - labeled)}")

    return {"polygon_total": len(all_polygon), "saved": n_saved, "not_found": len(not_found)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Batch fetch ticker names from Polygon")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if cached")
    args = parser.parse_args()
    run(force=args.force)
