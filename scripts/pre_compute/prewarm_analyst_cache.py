"""
AlphaAbsolute -- Discovery Index Analyst Cache Pre-Warmer
=========================================================
Batch-fetches analyst coverage counts from FMP for ALL tickers in the
rs_universe benchmark so nrgc_engine.py shows real DISC stages instead
of UNKNOWN on first run.

Respects:
  - FMP 250/day limit  → skips tickers with cache < ANALYST_CACHE_DAYS old
  - Rate limit         → 0.25s between calls (~4 calls/sec, well under FMP limit)
  - Max per run        → 200 calls per invocation (leave headroom for the day)

Sources (in order):
  1. data/rs_universe/benchmark_distribution.json  (main 557-ticker universe)
  2. data/rs_universe/theme_rs_latest.json          (any extra theme members)
  3. data/universe/active_universe.json              (paper trading positions)

Run: weekly (after rs_benchmark, before nrgc_engine)
  → pre_market_runner.py already includes this after rs_benchmark

Output: data/nrgc/analyst_cache/{TICKER}.json
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

# Force UTF-8 output so Unicode chars in print() don't crash on Windows cp874 terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

ANALYST_CACHE_DIR  = ROOT / "data" / "nrgc" / "analyst_cache"
ANALYST_CACHE_DIR.mkdir(parents=True, exist_ok=True)

ANALYST_CACHE_DAYS = 90    # same as nrgc_engine.py
MAX_CALLS_PER_RUN  = 200   # FMP 250/day limit — leave 50 headroom
CALL_DELAY_SEC     = 0.25  # 4 calls/sec — safe under FMP rate limit

# ── Load env ──────────────────────────────────────────────────────────────────
def _load_env() -> dict:
    env = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

_ENV = _load_env()
FMP_KEY = os.environ.get("FMP_API_KEY", _ENV.get("FMP_API_KEY", ""))


# ── Ticker collection ─────────────────────────────────────────────────────────

def _collect_tickers() -> list[str]:
    """
    Collect full ticker universe from all available sources (deduped).

    Source priority (most comprehensive first):
      1. rs_universe/history/ — filenames like TICKER_rs_history.json (every ticker ever scored)
      2. theme_rs_latest.json — current theme members (catches any new additions)
      3. latest.json results[] — current day's ranked universe
      4. active_universe.json — paper trading watchlist
    """
    tickers: set[str] = set()

    # 1. benchmark_tickers.json — full S&P+Nasdaq universe (written by rs_benchmark.py)
    # This is the authoritative source: every ticker that successfully contributed to distribution
    bench_tickers_file = ROOT / "data" / "rs_universe" / "benchmark_tickers.json"
    if bench_tickers_file.exists():
        try:
            data = json.loads(bench_tickers_file.read_text(encoding="utf-8"))
            ticker_list = data.get("tickers", [])
            tickers.update(t.upper() for t in ticker_list if isinstance(t, str))
        except Exception:
            pass

    # 2. rs_universe/history/ directory — fallback if benchmark_tickers.json not yet generated
    # (rs_benchmark.py runs Fridays; on other days use the history files as backup)
    if not tickers:
        history_dir = ROOT / "data" / "rs_universe" / "history"
        if history_dir.is_dir():
            for f in history_dir.iterdir():
                if f.suffix == ".json" and f.name.endswith("_rs_history.json"):
                    tkr = f.name.replace("_rs_history.json", "").upper()
                    if tkr:
                        tickers.add(tkr)

    # 2. theme_rs_latest.json — current theme members (catches newly added tickers)
    theme_file = ROOT / "data" / "rs_universe" / "theme_rs_latest.json"
    if theme_file.exists():
        try:
            data = json.loads(theme_file.read_text(encoding="utf-8"))
            for theme_id, theme_data in data.items():
                if theme_id.startswith("_"):
                    continue
                if isinstance(theme_data, dict):
                    members = theme_data.get("members", [])
                    tickers.update(m.upper() for m in members if isinstance(m, str))
        except Exception:
            pass

    # 3. latest.json — current day's rs_ranker scored tickers
    latest_file = ROOT / "data" / "rs_universe" / "latest.json"
    if latest_file.exists():
        try:
            data = json.loads(latest_file.read_text(encoding="utf-8"))
            results = data.get("results", data.get("top_10_leaders", []))
            tickers.update(r["ticker"].upper() for r in results if "ticker" in r)
        except Exception:
            pass

    # 4. active_universe.json — paper trading positions / watchlist
    active_file = ROOT / "data" / "universe" / "active_universe.json"
    if active_file.exists():
        try:
            data = json.loads(active_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                tickers.update(t.upper() for t in data if isinstance(t, str))
            elif isinstance(data, dict):
                tickers.update(t.upper() for t in data.keys()
                               if not t.startswith("_"))
        except Exception:
            pass

    # Filter out obvious non-tickers
    tickers = {t for t in tickers if t.isalpha() and 1 <= len(t) <= 5}

    return sorted(tickers)


# ── Staleness check ───────────────────────────────────────────────────────────

def _needs_refresh(ticker: str) -> bool:
    """Return True if analyst cache is missing, stale, or has no count (prev fetch failed)."""
    cache_file = ANALYST_CACHE_DIR / f"{ticker}.json"
    if not cache_file.exists():
        return True
    try:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        # Re-fetch if count is None (previous run used broken endpoint — stubs have null count)
        if cached.get("count") is None and cached.get("source", "").startswith(("no_key", "err:", "fmp_empty")):
            return True
        cached_date = date.fromisoformat(cached.get("cached_date", "2000-01-01"))
        return (date.today() - cached_date).days >= ANALYST_CACHE_DAYS
    except Exception:
        return True


# ── FMP fetch ─────────────────────────────────────────────────────────────────

def _fetch_and_cache(ticker: str) -> dict:
    """Fetch analyst count from FMP and write to cache. Returns result dict."""
    today = date.today()
    cache_file = ANALYST_CACHE_DIR / f"{ticker}.json"
    result: dict = {"count": None, "cached_date": today.isoformat(), "source": "no_key"}

    if not FMP_KEY:
        # Write a stub so we don't retry every run with no key
        try:
            cache_file.write_text(json.dumps(result), encoding="utf-8")
        except Exception:
            pass
        return result

    try:
        # Use /stable/price-target-summary — works on free plan, gives analyst count proxy
        # lastQuarterCount = how many analysts issued price targets in last 90 days
        url = (
            f"https://financialmodelingprep.com/stable/price-target-summary"
            f"?symbol={ticker}&apikey={FMP_KEY}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "AlphaAbsolute/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        if data and isinstance(data, list) and len(data) > 0:
            # lastQuarterCount = analysts active in past 90 days (best proxy for coverage)
            count_val = (data[0].get("lastQuarterCount")
                         or data[0].get("lastMonthCount")
                         or data[0].get("allTimeCount"))
            result = {
                "count":       int(count_val) if count_val is not None else None,
                "cached_date": today.isoformat(),
                "source":      "fmp_price_target",
            }
        else:
            result = {"count": None, "cached_date": today.isoformat(), "source": "fmp_empty"}

    except Exception as e:
        result = {"count": None, "cached_date": today.isoformat(), "source": f"err:{str(e)[:40]}"}

    try:
        cache_file.write_text(json.dumps(result), encoding="utf-8")
    except Exception:
        pass

    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    today = date.today().isoformat()
    print(f"\n{'='*60}")
    print(f"  Discovery Index Pre-Warm  [{today}]")
    print(f"{'='*60}")

    if not FMP_KEY:
        print("  [WARN] FMP_API_KEY not set — skipping analyst fetch")
        print("     Add FMP_API_KEY to .env for analyst coverage data")
        return {"status": "no_key", "fetched": 0, "skipped": 0, "total": 0}

    # ── Connectivity check: test one ticker before burning 200 calls ───────────
    test_url = (
        f"https://financialmodelingprep.com/stable/price-target-summary"
        f"?symbol=AAPL&apikey={FMP_KEY}"
    )
    try:
        req = urllib.request.Request(test_url, headers={"User-Agent": "AlphaAbsolute/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            _ = resp.read()
    except Exception as e:
        err_str = str(e)
        if "403" in err_str or "401" in err_str:
            print(f"  [WARN] FMP analyst endpoint requires premium plan (HTTP {err_str[:3]}).")
            print(f"         Discovery Index DISC component will default to 0 (estimate).")
            print(f"         Upgrade FMP plan to unlock analyst coverage data.")
        else:
            print(f"  [WARN] FMP connectivity test failed: {err_str[:80]}")
        return {"status": "no_access", "fetched": 0, "skipped": 0, "total": 0}

    all_tickers = _collect_tickers()
    print(f"  Universe: {len(all_tickers)} tickers")

    # Filter to only stale ones
    stale = [t for t in all_tickers if _needs_refresh(t)]
    fresh = len(all_tickers) - len(stale)
    print(f"  Cache status: {fresh} fresh, {len(stale)} need refresh")

    if not stale:
        print("  ✅ All caches current — nothing to fetch")
        return {"status": "all_current", "fetched": 0, "skipped": fresh, "total": len(all_tickers)}

    # Respect daily call cap
    to_fetch = stale[:MAX_CALLS_PER_RUN]
    if len(stale) > MAX_CALLS_PER_RUN:
        print(f"  ⚡ Cap {MAX_CALLS_PER_RUN}/run — {len(stale)-MAX_CALLS_PER_RUN} deferred to next run")

    print(f"\n  Fetching {len(to_fetch)} tickers...")
    fetched = 0
    errors  = 0
    ok_list = []
    err_list = []

    for i, ticker in enumerate(to_fetch, 1):
        result = _fetch_and_cache(ticker)
        count  = result.get("count")
        src    = result.get("source", "?")

        if count is not None:
            fetched += 1
            ok_list.append(f"{ticker}={count}")
        else:
            errors += 1
            err_list.append(f"{ticker}({src})")

        # Progress every 50
        if i % 50 == 0 or i == len(to_fetch):
            print(f"  [{i:>3}/{len(to_fetch)}] fetched={fetched} errors={errors}")

        time.sleep(CALL_DELAY_SEC)

    # Summary
    print(f"\n  ─── Results ───────────────────────────────────────────")
    print(f"  Fetched:  {fetched} ({len(ok_list)} with count, {errors} errors)")
    if ok_list[:10]:
        print(f"  Sample:   {', '.join(ok_list[:10])}")
    if err_list:
        print(f"  Errors:   {', '.join(err_list[:5])}{'...' if len(err_list)>5 else ''}")
    print(f"  Cache:    {ANALYST_CACHE_DIR}")
    print(f"  {'✅' if errors==0 else '⚠'} Pre-warm complete")

    return {
        "status":   "done",
        "fetched":  fetched,
        "errors":   errors,
        "skipped":  fresh,
        "total":    len(all_tickers),
        "deferred": max(0, len(stale) - MAX_CALLS_PER_RUN),
    }


if __name__ == "__main__":
    run()
