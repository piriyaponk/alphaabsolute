"""
AlphaAbsolute -- Fast OHLCV Prefetch (Polygon-only, parallel)
=============================================================
Downloads 290-day OHLCV for ALL universe tickers using Polygon.io
with ThreadPoolExecutor for speed.

- Source: Polygon only (no Tiingo/Yahoo fallback -- fast fail)
- Workers: 8 concurrent threads (respects Polygon free tier ~5 req/min
           per connection, but multiple connections run in parallel)
- Timeout: 8s per request (fail fast for missing tickers)
- Skips tickers already in cache (< 20h old)
- Writes same format as ohlcv_prefetch.py (compatible)

Expected time: ~15-30 minutes for 5000 tickers vs 8+ hours for ohlcv_prefetch

Usage:
  python scripts/pre_compute/fast_prefetch.py
  python scripts/pre_compute/fast_prefetch.py --workers 12
  python scripts/pre_compute/fast_prefetch.py --force  # re-fetch all
"""

from __future__ import annotations
import json
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR  = Path(__file__).resolve().parents[2]
CACHE_DIR = BASE_DIR / "data" / "ohlcv_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts" / "utils"))

PERIOD_DAYS = 290
MAX_AGE_HRS = 20
DEFAULT_WORKERS = 8

# ── Load .env ──────────────────────────────────────────────────────────────────
def _load_env() -> None:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for ln in env_path.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "")

# ── Thread-safe counters ────────────────────────────────────────────────────────
_lock   = threading.Lock()
_ok     = 0
_failed = 0
_skip   = 0


def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker}.json"


def _is_fresh(ticker: str) -> bool:
    p = _cache_path(ticker)
    if not p.exists():
        return False
    return (time.time() - p.stat().st_mtime) / 3600 < MAX_AGE_HRS


def _write_cache(ticker: str, closes: list, volumes: list, dates: list) -> None:
    data = {
        "ticker":     ticker,
        "fetched_at": datetime.now().isoformat(),
        "period_days": PERIOD_DAYS,
        "source":     "polygon_fast",
        "n_bars":     len(closes),
        "dates":      dates,
        "close":      closes,
        "volume":     volumes,
    }
    _cache_path(ticker).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # Also persist to SQLite (never expires, single-file backup)
    try:
        from scripts.utils.ohlcv_store import OHLCVStore
        _store = OHLCVStore()
        _store.upsert(ticker, dates, closes, volumes, source="polygon_fast")
    except Exception:
        pass   # SQLite write is best-effort — JSON is primary


def _polygon_fetch(ticker: str) -> Optional[tuple]:
    """
    Fetch OHLCV from Polygon only. Returns (dates, closes, volumes) or None.
    Fast-fail: 8s timeout, no retries for 429 (just skip).
    """
    if not POLYGON_KEY:
        return None

    import requests, urllib3
    urllib3.disable_warnings()

    to_date   = date.today().isoformat()
    from_date = (date.today() - timedelta(days=PERIOD_DAYS + 10)).isoformat()

    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}"
    params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": POLYGON_KEY}

    try:
        r = requests.get(url, params=params, timeout=8, verify=False)
        if r.status_code == 429:
            time.sleep(13)   # Rate limit: back off and retry once
            r = requests.get(url, params=params, timeout=8, verify=False)
        if r.status_code != 200:
            return None

        data = r.json()
        if data.get("status") not in ("OK", "DELAYED"):
            return None
        results = data.get("results", [])
        if not results:
            return None   # No data for this ticker on Polygon

        # Parse
        import pandas as pd
        df = pd.DataFrame(results)
        df["Date"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(
            "America/New_York").dt.normalize()
        df = df.set_index("Date").sort_index()

        if len(df) < 20:
            return None
        if df["c"].iloc[-1] <= 0:
            return None

        # Check staleness: last bar must be recent (< 7 calendar days)
        last_date = df.index[-1].date()
        if (date.today() - last_date).days > 7:
            return None

        dates   = [str(d)[:10] for d in df.index]
        closes  = [round(float(v), 4) for v in df["c"]]
        volumes = [int(v) for v in df["v"]]
        return dates, closes, volumes

    except Exception:
        return None


def _fetch_one(ticker: str, force: bool = False) -> str:
    """Fetch one ticker. Returns 'fresh' | 'ok' | 'fail'."""
    global _ok, _failed, _skip

    if not force and _is_fresh(ticker):
        with _lock:
            _skip += 1
        return "fresh"

    result = _polygon_fetch(ticker)
    if result:
        dates, closes, volumes = result
        _write_cache(ticker, closes, volumes, dates)
        with _lock:
            _ok += 1
        return "ok"
    else:
        with _lock:
            _failed += 1
        return "fail"


def run(workers: int = DEFAULT_WORKERS, force: bool = False) -> dict:
    global _ok, _failed, _skip
    _ok = _failed = _skip = 0

    if not POLYGON_KEY:
        print("[!] POLYGON_API_KEY not set — cannot run fast_prefetch")
        return {"error": "no_polygon_key"}

    # Load universe
    try:
        from scripts.pre_compute.universe_builder import get_universe
        universe = get_universe()
    except Exception as e:
        print(f"[!] universe_builder failed: {e} — using OHLCV cache keys as fallback")
        universe = sorted(p.stem for p in CACHE_DIR.glob("[A-Z]*.json"))

    # Remove index / ETFs
    exclude = {"SPY", "QQQ", "IWM", "GOOG", ""}
    universe = [t for t in universe if t not in exclude]

    # ── Load known_fail list (skip tickers confirmed to have no Polygon data) ──
    known_fail: set = set()
    known_good_file = BASE_DIR / "data" / "universe" / "known_good.json"
    if known_good_file.exists() and not force:
        try:
            kg = json.loads(known_good_file.read_text(encoding="utf-8"))
            known_fail = set(kg.get("fail", []))
        except Exception:
            pass

    n_before = len(universe)
    universe = [t for t in universe if t not in known_fail or _is_fresh(t)]
    n_skipped_fail = n_before - len(universe)

    today = date.today().isoformat()
    print(f"\n{'='*58}")
    print(f"  Fast OHLCV Pre-fetch (Polygon-parallel)  [{today}]")
    print(f"{'='*58}")
    print(f"  Universe: {len(universe)} tickers | workers={workers} | timeout=8s")
    print(f"  Period: {PERIOD_DAYS}d | Source: Polygon only (fast-fail)")
    if n_skipped_fail:
        print(f"  Known-fail skipped: {n_skipped_fail} tickers (saved ~{n_skipped_fail*8//60}min)")

    # Pre-check fresh
    fresh_count = sum(1 for t in universe if not force and _is_fresh(t))
    need_count  = len(universe) - fresh_count
    print(f"  Already fresh: {fresh_count} | Need fetch: {need_count}")

    t_start = time.time()
    done    = 0
    total   = len(universe)

    # Progress reporter thread
    stop_event = threading.Event()
    def _progress():
        while not stop_event.is_set():
            elapsed = time.time() - t_start
            with _lock:
                ok_now, fail_now, skip_now = _ok, _failed, _skip
            rate = (ok_now + fail_now) / max(elapsed, 1) * 60
            eta  = (total - ok_now - fail_now - skip_now) / max(rate / 60, 0.01)
            print(f"  [{ok_now+fail_now+skip_now}/{total}] ok={ok_now} fail={fail_now} "
                  f"skip={skip_now} | {rate:.0f}/min | ETA {eta:.0f}s",
                  flush=True)
            time.sleep(30)

    reporter = threading.Thread(target=_progress, daemon=True)
    reporter.start()

    # Parallel fetch with ThreadPoolExecutor
    # Small delay between submissions to avoid overwhelming Polygon
    SUBMIT_DELAY = 0.12   # 1/0.12 ≈ 8 submissions/s × 8 workers = polite rate

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for ticker in universe:
            fut = executor.submit(_fetch_one, ticker, force)
            futures[fut] = ticker
            time.sleep(SUBMIT_DELAY)

        for fut in as_completed(futures):
            pass   # results tracked via globals

    stop_event.set()

    elapsed_total = time.time() - t_start
    total_cached  = _ok + fresh_count

    print(f"\n  DONE: {_ok} fetched | {_failed} not on Polygon | {_skip} already fresh")
    print(f"  Total in cache: {total_cached}/{total} ({total_cached/total*100:.1f}%)")
    print(f"  Time: {elapsed_total:.0f}s ({elapsed_total/60:.1f} min)")

    # Save manifest
    manifest = {
        "date":         today,
        "fetched_at":   datetime.now().isoformat(),
        "source":       "fast_prefetch_polygon_parallel",
        "workers":      workers,
        "total":        total,
        "ok":           total_cached,
        "fetched_now":  _ok,
        "fresh":        _skip,
        "failed":       _failed,
        "coverage_pct": round(total_cached / total * 100, 1),
    }
    (CACHE_DIR / "_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  Manifest: {CACHE_DIR / '_manifest.json'}")

    # ── Update known_good.json after full run ──────────────────────────────────
    # Rebuild from entire cache — captures new successes + removes delisted tickers
    try:
        known_good_out: dict = {}
        known_fail_out: list = []
        cache_tickers  = {p.stem for p in CACHE_DIR.glob("[A-Z]*.json")}
        all_u_set      = set(universe) | set(known_fail)   # full universe tried

        for t in sorted(all_u_set):
            if t in cache_tickers:
                p = CACHE_DIR / f"{t}.json"
                d = json.loads(p.read_text(encoding="utf-8"))
                n = d.get("n_bars", len(d.get("close", [])))
                known_good_out[t] = {
                    "n_bars":   n,
                    "source":   d.get("source", "polygon"),
                    "last_ok":  d.get("fetched_at", ""),
                }
            else:
                known_fail_out.append(t)

        kg_data = {
            "built_at": datetime.now().isoformat(),
            "note":     "Auto-updated by fast_prefetch after full run.",
            "n_good":   len(known_good_out),
            "n_fail":   len(known_fail_out),
            "good":     known_good_out,
            "fail":     known_fail_out,
        }
        known_good_file.write_text(
            json.dumps(kg_data, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  known_good.json: {len(known_good_out)} good | {len(known_fail_out)} fail — updated")
    except Exception as e:
        print(f"  [WARN] known_good update failed: {e}")

    return manifest


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fast parallel OHLCV prefetch via Polygon")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--force",   action="store_true")
    args = parser.parse_args()
    run(workers=args.workers, force=args.force)
