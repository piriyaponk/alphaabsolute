"""
AlphaAbsolute v2 -- OHLCV Pre-fetch  (runs after market close, 4:30 PM)
========================================================================
Downloads 290 days of OHLCV for ALL 600+ benchmark tickers and caches to disk.
rs_ranker.py reads from this cache the next morning — no real-time fetch needed.

Without this:  rs_ranker fetches ~73/600 tickers (rate-limited) → incomplete universe
With this:     rs_ranker reads all 600 from disk in <5 seconds → full universe ranked

Schedule:  Daily at 4:30 PM (after market close), BEFORE auto_postmortem.py
Run time:  ~11 minutes (1,325 tickers × 0.3s each via Yahoo direct)

Source priority (DE fix 2026-05-22):
  1. Yahoo query2 — direct call, no Polygon rate-limit sleep, ~11 min for 1,325 tickers
  2. data_engine.get_ohlcv() — Polygon/Tiingo fallback for tickers Yahoo fails on
  OLD: get_ohlcv() called Polygon first → 12s sleep per rate-limit → 4+ hours total

Output:    data/ohlcv_cache/<TICKER>.json   (290 days OHLCV per ticker)
           data/ohlcv_cache/_manifest.json  (status: ok/failed per ticker)

Cost: $0 — Yahoo query2 (unofficial, stable since 2020), Polygon free as fallback
"""

from __future__ import annotations
import json
import sys
import time
import os
import requests
import urllib3
from datetime import date, datetime, timezone
from pathlib import Path

urllib3.disable_warnings()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# WARP/Cloudflare SSL bypass — must run before any HTTPS call
try:
    from scripts.utils.ssl_patch import apply as _ssl_apply
    _ssl_apply()
except Exception:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "utils"))
        from ssl_patch import apply as _ssl_apply
        _ssl_apply()
    except Exception:
        pass

BASE_DIR   = Path(__file__).resolve().parents[2]
CACHE_DIR  = BASE_DIR / "data" / "ohlcv_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts" / "utils"))

PERIOD_DAYS = 290   # enough for 12M RS + 200DMA + ADTV
DELAY_SEC   = 0.25  # Yahoo handles this comfortably (was 0.5s when Polygon was primary)
MAX_AGE_HRS = 20    # re-fetch if cache older than this (covers weekends: 72h ok too)

_YF_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


# ── Load .env ─────────────────────────────────────────────────────────────────
def _load_env() -> None:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for ln in env_path.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()


# ── Universe ──────────────────────────────────────────────────────────────────
def _get_universe() -> list[str]:
    """
    Full universe: S&P 500 + Nasdaq 100 + S&P 400 + Russell 2000 (~3,000 tickers).
    Sourced from universe_builder.py — auto-refreshes weekly from live constituent lists.
    """
    tickers: set[str] = set()

    # Primary: universe_builder (S&P+NDX+SP400+R2000, auto-updated weekly)
    try:
        from scripts.pre_compute.universe_builder import get_universe
        universe_list = get_universe()
        tickers.update(universe_list)
        print(f"  [Universe] Loaded {len(tickers)} tickers from universe_builder")
    except Exception as e:
        print(f"  [Universe] universe_builder failed ({e}) — using benchmark fallback")
        try:
            from scripts.pre_compute.rs_benchmark import BENCHMARK_TICKERS
            tickers.update(BENCHMARK_TICKERS)
        except Exception:
            pass

    # Always include index (for QQQ cache)
    tickers.add("QQQ")

    # Remove ETFs / non-equities
    for excl in {"SPY", "IWM", "VIX", "^VIX", "^GSPC", "^IXIC", "GOOG", ""}:
        tickers.discard(excl)

    return sorted(tickers)


# ── Cache I/O ─────────────────────────────────────────────────────────────────
def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker}.json"


def _is_fresh(ticker: str) -> bool:
    """Return True if cache file exists and is < MAX_AGE_HRS old."""
    p = _cache_path(ticker)
    if not p.exists():
        return False
    age_hours = (time.time() - p.stat().st_mtime) / 3600
    return age_hours < MAX_AGE_HRS


def _write_cache(ticker: str, closes: list, volumes: list, dates: list) -> None:
    data = {
        "ticker":     ticker,
        "fetched_at": datetime.now().isoformat(),
        "period_days": PERIOD_DAYS,
        "n_bars":     len(closes),
        "dates":      dates,
        "close":      closes,
        "volume":     volumes,
    }
    _cache_path(ticker).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def read_cache(ticker: str) -> dict:
    """
    Public API: read cached OHLCV for a ticker.
    Returns {"close": [...], "volume": [...], "dates": [...]} or {} if not cached.
    Called by rs_ranker.py fetch_ohlcv() as primary source.
    """
    p = _cache_path(ticker)
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {
            "close":  d.get("close", []),
            "volume": d.get("volume", []),
            "dates":  d.get("dates", []),
        }
    except Exception:
        return {}


# ── Yahoo direct fetch (fast path — no Polygon rate-limit sleep) ──────────────
def _fetch_yahoo_direct(ticker: str) -> tuple[list, list, list] | None:
    """
    Fetch 290d OHLCV from Yahoo Finance query2 directly.
    Returns (closes, volumes, dates) or None on failure.

    Why direct instead of data_engine.get_ohlcv():
      get_ohlcv() puts Polygon first. Polygon free = 5 req/min → 12s sleep per
      rate-limit hit → 1,325 tickers × 12s = 4+ hour wall time.
      Yahoo has no documented rate limit at 0.25s between calls.
    """
    end_ts   = int(datetime.now().timestamp()) + 86400
    start_ts = end_ts - (PERIOD_DAYS + 15) * 86400   # 15-day buffer

    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1d&period1={start_ts}&period2={end_ts}")
    try:
        r = requests.get(url, headers=_YF_HEADERS, verify=False, timeout=15)
        if r.status_code != 200:
            return None
        data   = r.json()
        chart  = data.get("chart", {}).get("result", [{}])[0]
        tss    = chart.get("timestamp", [])
        quotes = chart.get("indicators", {}).get("quote", [{}])[0]
        closes  = quotes.get("close",  [])
        volumes = quotes.get("volume", [])

        if not tss or not closes:
            return None

        out_dates, out_closes, out_vols = [], [], []
        for i, ts in enumerate(tss):
            d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            c = closes[i]  if closes  and i < len(closes)  else None
            v = volumes[i] if volumes and i < len(volumes) else None
            if c is not None:
                out_dates.append(d)
                out_closes.append(round(float(c), 4))
                out_vols.append(int(v) if v else 0)

        return (out_closes, out_vols, out_dates) if len(out_closes) >= 20 else None
    except Exception:
        return None


# ── Themed universe set (loaded once for fast lookup) ────────────────────────
_THEMED_UNIVERSE: set[str] | None = None

def _get_themed_universe() -> set[str]:
    """Load themed universe tickers once (1,321 tickers in ticker_labels.json)."""
    global _THEMED_UNIVERSE
    if _THEMED_UNIVERSE is None:
        try:
            labels_path = BASE_DIR / "data" / "themes" / "ticker_labels.json"
            data = json.loads(labels_path.read_text(encoding="utf-8"))
            _THEMED_UNIVERSE = set(data.get("labels", data).keys())
        except Exception:
            _THEMED_UNIVERSE = set()
    return _THEMED_UNIVERSE


# ── Fetch one ticker ──────────────────────────────────────────────────────────
def _fetch_one(ticker: str) -> bool:
    """
    Fetch OHLCV and write to cache. Returns True on success.

    Source priority:
      1. Yahoo query2 direct  — fast, no rate-limit sleep (~0.25s/ticker)
      2. data_engine.get_ohlcv() — Polygon/Tiingo fallback ONLY for themed universe
         tickers (avoids 60s Tiingo 429 sleep for obscure benchmark micro-caps
         that Yahoo doesn't cover and aren't in our 1,321 investment universe).
    """
    # ── Fast path: Yahoo direct ───────────────────────────────────────────────
    result = _fetch_yahoo_direct(ticker)
    if result is not None:
        closes, volumes, dates = result
        _write_cache(ticker, closes, volumes, dates)
        return True

    # ── Fallback: data_engine — only for tickers in themed universe ───────────
    # Benchmark micro-caps (Russell 2000 tail) that Yahoo can't fetch also won't
    # be in Polygon/Tiingo — skipping saves the 60s Tiingo-429 sleep per ticker.
    if ticker not in _get_themed_universe():
        return False

    try:
        from data_engine import get_ohlcv
        df = get_ohlcv(ticker, period=f"{PERIOD_DAYS}d")
        if df is None or len(df) < 20:
            return False
        closes  = [round(float(v), 4) for v in df["Close"].tolist()]
        volumes = [int(v) for v in df["Volume"].tolist()]
        dates   = [str(d) for d in df.index]
        _write_cache(ticker, closes, volumes, dates)
        return True
    except Exception:
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def run(force: bool = False) -> dict:
    """
    Pre-fetch OHLCV for all 600+ tickers.
    force=True re-fetches even if cache is fresh (used for manual refresh).
    """
    today = date.today().isoformat()
    print(f"\n{'='*58}")
    print(f"  OHLCV Pre-fetch  [{today}]")
    print(f"{'='*58}")

    universe = _get_universe()
    print(f"  Universe: {len(universe)} tickers | period={PERIOD_DAYS}d | delay={DELAY_SEC}s")

    # Separate: already fresh vs needs fetch
    fresh_tickers  = [t for t in universe if not force and _is_fresh(t)]
    to_fetch       = [t for t in universe if force or not _is_fresh(t)]

    print(f"  Already cached:  {len(fresh_tickers)}")
    print(f"  Need fetch:      {len(to_fetch)}")

    if not to_fetch:
        print("  All tickers fresh — nothing to do.")
        return {"status": "ok", "fetched": 0, "fresh": len(fresh_tickers),
                "total": len(universe), "date": today}

    ok_count  = 0
    err_count = 0
    errors    = []
    t_start   = time.time()

    for i, ticker in enumerate(to_fetch):
        success = _fetch_one(ticker)
        if success:
            ok_count += 1
        else:
            err_count += 1
            errors.append(ticker)

        # Progress every 50 tickers
        if (i + 1) % 50 == 0 or (i + 1) == len(to_fetch):
            elapsed = time.time() - t_start
            eta     = (elapsed / (i + 1)) * (len(to_fetch) - i - 1)
            print(f"  [{i+1:3d}/{len(to_fetch)}] ok={ok_count} err={err_count} "
                  f"| elapsed={elapsed:.0f}s ETA={eta:.0f}s")

        time.sleep(DELAY_SEC)

    total_cached = ok_count + len(fresh_tickers)
    elapsed_total = time.time() - t_start

    print(f"\n  DONE: {ok_count} fetched | {err_count} failed | {len(fresh_tickers)} already fresh")
    print(f"  Total cached: {total_cached}/{len(universe)} tickers")
    print(f"  Time: {elapsed_total:.0f}s")

    if errors[:10]:
        print(f"  Failed: {', '.join(errors[:10])}" + (" ..." if len(errors) > 10 else ""))

    # Save manifest
    manifest = {
        "date":         today,
        "fetched_at":   datetime.now().isoformat(),
        "total":        len(universe),
        "ok":           total_cached,
        "fetched_now":  ok_count,
        "fresh":        len(fresh_tickers),
        "failed":       err_count,
        "failed_list":  errors,
        "coverage_pct": round(total_cached / len(universe) * 100, 1),
    }
    (CACHE_DIR / "_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return manifest


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Re-fetch all even if fresh")
    args = parser.parse_args()
    run(force=args.force)
