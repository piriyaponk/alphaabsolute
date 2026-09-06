"""
AlphaAbsolute -- Slow OHLCV Prefetch (Polygon-only, sequential, rate-safe)
===========================================================================
Downloads 290-day OHLCV for ALL universe tickers one at a time.

WHY: fast_prefetch.py uses 8 parallel workers and hits Polygon free tier
     rate limit (5 req/min) → ~83% fail rate.
     1 request every 13s = 4.6 req/min = safely under the 5 req/min cap.

Strategy:
  Priority 1 — known_good tickers with stale cache (>20h) — fetch first
  Priority 2 — known_fail tickers not retried in >7 days  — slow retry
  Priority 3 — tickers not in known_good at all (new)
  Skip       — tickers with fresh cache (<20h old)

On 429: sleep 60s, retry once. If still 429: sleep 60s more, retry.
        If still 429 → log and skip.

Progress: printed every 50 tickers with ETA.
Log file: data/runner_logs/slow_prefetch_{YYMMDD}.log
Checkpoint every 100 tickers → saves known_good.json if interrupted.
SIGINT (Ctrl+C) → checkpoint before exit.

Usage:
  python scripts/pre_compute/slow_prefetch.py
  python scripts/pre_compute/slow_prefetch.py --force
  python scripts/pre_compute/slow_prefetch.py --retry-fails
  python scripts/pre_compute/slow_prefetch.py --workers 1   # kept for compat
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Paths ───────────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).resolve().parents[2]
CACHE_DIR = BASE_DIR / "data" / "ohlcv_cache"
LOG_DIR   = BASE_DIR / "data" / "runner_logs"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts" / "utils"))

# ── Constants ───────────────────────────────────────────────────────────────────
PERIOD_DAYS       = 290
MAX_AGE_HRS       = 20
DELAY_SEC         = 13.0     # 4.6 req/min — safely under 5 req/min free tier
RETRY_FAIL_DAYS   = 7        # re-attempt known_fail after this many days
PROGRESS_EVERY    = 50       # print progress every N tickers
CHECKPOINT_EVERY  = 100      # save known_good.json every N tickers

KNOWN_GOOD_FILE   = BASE_DIR / "data" / "universe" / "known_good.json"

# ── Load .env ───────────────────────────────────────────────────────────────────
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

# ── Logging ─────────────────────────────────────────────────────────────────────
def _setup_logger() -> logging.Logger:
    today_str = date.today().strftime("%y%m%d")
    log_file  = LOG_DIR / f"slow_prefetch_{today_str}.log"
    logger    = logging.getLogger("slow_prefetch")
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    return logger

logger = _setup_logger()

# ── Cache helpers ────────────────────────────────────────────────────────────────
def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker}.json"


def _cache_age_hrs(ticker: str) -> float:
    """Return cache age in hours. Returns 9999 if no cache file."""
    p = _cache_path(ticker)
    if not p.exists():
        return 9999.0
    return (time.time() - p.stat().st_mtime) / 3600


def _is_fresh(ticker: str) -> bool:
    return _cache_age_hrs(ticker) < MAX_AGE_HRS


def _write_cache(ticker: str, closes: list, volumes: list, dates: list) -> None:
    data = {
        "ticker":      ticker,
        "fetched_at":  datetime.now().isoformat(),
        "period_days": PERIOD_DAYS,
        "source":      "polygon_slow",
        "n_bars":      len(closes),
        "dates":       dates,
        "close":       closes,
        "volume":      volumes,
    }
    _cache_path(ticker).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # Also persist to SQLite (never expires, single-file backup)
    try:
        from scripts.utils.ohlcv_store import OHLCVStore
        _store = OHLCVStore()
        _store.upsert(ticker, dates, closes, volumes, source="polygon_slow")
    except Exception:
        pass   # SQLite write is best-effort — JSON is primary


# ── Polygon fetch (sequential, with 429 retry) ───────────────────────────────────
def _polygon_fetch(ticker: str) -> Optional[tuple]:
    """
    Fetch OHLCV from Polygon. Returns (dates, closes, volumes) or None.
    On 429: sleep 60s and retry up to 2 more times before giving up.
    """
    if not POLYGON_KEY:
        return None

    import requests
    import urllib3
    urllib3.disable_warnings()

    to_date   = date.today().isoformat()
    from_date = (date.today() - timedelta(days=PERIOD_DAYS + 10)).isoformat()

    url    = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}"
    params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": POLYGON_KEY}

    max_429_retries = 2
    for attempt in range(1 + max_429_retries):
        try:
            r = requests.get(url, params=params, timeout=15, verify=False)
        except Exception as exc:
            logger.debug(f"  {ticker}: request exception attempt {attempt+1}: {exc}")
            return None

        if r.status_code == 429:
            if attempt < max_429_retries:
                logger.warning(f"  {ticker}: 429 rate-limit (attempt {attempt+1}) — sleeping 60s")
                time.sleep(60)
                continue
            else:
                logger.warning(f"  {ticker}: 429 on final attempt — skipping")
                return None

        if r.status_code != 200:
            logger.debug(f"  {ticker}: HTTP {r.status_code} — skipping")
            return None

        # Parse response
        try:
            data = r.json()
        except Exception as exc:
            logger.debug(f"  {ticker}: JSON parse error: {exc}")
            return None

        if data.get("status") not in ("OK", "DELAYED"):
            logger.debug(f"  {ticker}: status={data.get('status')} — not OK/DELAYED")
            return None

        results = data.get("results", [])
        if not results:
            logger.debug(f"  {ticker}: no results (not listed on Polygon?)")
            return None

        # Build arrays
        try:
            import pandas as pd
            df = pd.DataFrame(results)
            df["Date"] = (
                pd.to_datetime(df["t"], unit="ms", utc=True)
                  .dt.tz_convert("America/New_York")
                  .dt.normalize()
            )
            df = df.set_index("Date").sort_index()
        except Exception as exc:
            logger.debug(f"  {ticker}: DataFrame build error: {exc}")
            return None

        if len(df) < 20:
            logger.debug(f"  {ticker}: only {len(df)} bars — too few")
            return None

        if df["c"].iloc[-1] <= 0:
            logger.debug(f"  {ticker}: last close <= 0")
            return None

        last_date = df.index[-1].date()
        if (date.today() - last_date).days > 7:
            logger.debug(f"  {ticker}: last bar {last_date} is stale (>7 calendar days)")
            return None

        dates   = [str(d)[:10] for d in df.index]
        closes  = [round(float(v), 4) for v in df["c"]]
        volumes = [int(v) for v in df["v"]]
        return dates, closes, volumes

    return None  # exhausted retries


# ── known_good.json helpers ──────────────────────────────────────────────────────
def _load_known_good() -> dict:
    """Load known_good.json. Returns {'good': {}, 'fail': [], ...}"""
    if not KNOWN_GOOD_FILE.exists():
        return {"good": {}, "fail": []}
    try:
        return json.loads(KNOWN_GOOD_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"  [WARN] known_good.json read error: {exc}")
        return {"good": {}, "fail": []}


def _save_known_good(kg: dict) -> None:
    """Write known_good.json atomically (write tmp then replace)."""
    try:
        tmp = KNOWN_GOOD_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(kg, ensure_ascii=False), encoding="utf-8")
        tmp.replace(KNOWN_GOOD_FILE)
    except Exception as exc:
        logger.warning(f"  [WARN] known_good.json write error: {exc}")


def _rebuild_known_good(universe_set: set, tried_tickers: set) -> dict:
    """Rebuild known_good from the OHLCV cache directory after a run."""
    known_good_out: dict  = {}
    known_fail_out: list  = []
    cache_tickers         = {p.stem for p in CACHE_DIR.glob("[A-Z]*.json")}
    all_tried             = universe_set | tried_tickers

    for t in sorted(all_tried):
        if t in cache_tickers:
            p = CACHE_DIR / f"{t}.json"
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                known_good_out[t] = {
                    "n_bars":  d.get("n_bars", len(d.get("close", []))),
                    "source":  d.get("source", "polygon"),
                    "last_ok": d.get("fetched_at", ""),
                }
            except Exception:
                known_fail_out.append(t)
        else:
            known_fail_out.append(t)

    return {
        "built_at": datetime.now().isoformat(),
        "note":     "Auto-updated by slow_prefetch after run.",
        "n_good":   len(known_good_out),
        "n_fail":   len(known_fail_out),
        "good":     known_good_out,
        "fail":     known_fail_out,
    }


# ── Priority ordering ────────────────────────────────────────────────────────────
def _build_fetch_order(
    universe:     list[str],
    kg:           dict,
    force:        bool,
    retry_fails:  bool,
) -> tuple[list[str], int]:
    """
    Returns (ordered_list_to_fetch, n_skipped).

    Priority 1: known_good with stale cache
    Priority 2: known_fail not retried in >7 days (or --retry-fails)
    Priority 3: not in known_good at all (unknown / new)
    Skip: fresh cache (<20h) unless --force
    """
    good_set = set(kg.get("good", {}).keys())
    fail_set = set(kg.get("fail", []))
    good_map = kg.get("good", {})

    p1, p2, p3, skipped = [], [], [], []

    # Parse last_ok timestamps for fail entries (stored in good_map only for good)
    # For fail entries, we track the last attempt via a separate field (if present)
    # known_good.json fail list is just a list of tickers — no timestamps.
    # We use the cache file mtime as a proxy for "last attempted" (if it exists).
    # If no cache file, treat as never retried.
    cutoff_retry = time.time() - (RETRY_FAIL_DAYS * 86400)

    for t in universe:
        if not force and _is_fresh(t):
            skipped.append(t)
            continue

        if t in good_set:
            p1.append(t)
        elif t in fail_set:
            # Only retry if enough time has passed (or --retry-fails / --force)
            if force or retry_fails:
                p2.append(t)
            else:
                # Check when the cache file was last touched (last attempt)
                p = _cache_path(t)
                last_attempt = p.stat().st_mtime if p.exists() else 0
                if last_attempt < cutoff_retry:
                    p2.append(t)
                else:
                    skipped.append(t)
        else:
            p3.append(t)

    ordered = p1 + p2 + p3
    return ordered, len(skipped)


# ── Main run ─────────────────────────────────────────────────────────────────────
def run(force: bool = False, retry_fails: bool = False) -> dict:
    if not POLYGON_KEY:
        logger.error("[!] POLYGON_API_KEY not set — cannot run slow_prefetch")
        return {"error": "no_polygon_key"}

    # Load universe
    try:
        from scripts.pre_compute.universe_builder import get_universe
        universe = get_universe()
    except Exception as exc:
        logger.warning(f"[!] universe_builder failed: {exc} — using OHLCV cache keys as fallback")
        universe = sorted(p.stem for p in CACHE_DIR.glob("[A-Z]*.json"))

    # Exclude index / non-fetchable
    exclude  = {"SPY", "QQQ", "IWM", "GOOG", ""}
    universe = [t for t in universe if t not in exclude]

    kg = _load_known_good()

    # Build fetch order
    to_fetch, n_skipped = _build_fetch_order(universe, kg, force, retry_fails)
    total = len(to_fetch)

    today_str = date.today().isoformat()
    logger.info(f"\n{'='*62}")
    logger.info(f"  Slow OHLCV Pre-fetch (Polygon sequential)  [{today_str}]")
    logger.info(f"{'='*62}")
    logger.info(f"  Universe: {len(universe)} tickers | workers=1 | delay={DELAY_SEC}s")
    logger.info(f"  Rate: {60/DELAY_SEC:.1f} req/min (Polygon free cap: 5 req/min)")
    logger.info(f"  Period: {PERIOD_DAYS}d | Source: Polygon only")
    logger.info(f"  Already fresh (skip): {n_skipped} | To fetch: {total}")
    if total > 0:
        eta_hrs = total * DELAY_SEC / 3600
        logger.info(f"  Estimated time: {eta_hrs:.1f}h ({total * DELAY_SEC / 60:.0f} min)")
    logger.info("")

    # ── State counters ──────────────────────────────────────────────────────────
    n_ok   = 0
    n_fail = 0
    t_start = time.time()

    # Track which tickers we attempted (for known_good rebuild)
    tried_tickers: set[str] = set()

    # Working copy of known_good for incremental checkpoint updates
    kg_working = {
        "built_at": datetime.now().isoformat(),
        "note":     "Auto-updated by slow_prefetch (in progress).",
        "n_good":   kg.get("n_good", 0),
        "n_fail":   kg.get("n_fail", 0),
        "good":     dict(kg.get("good", {})),
        "fail":     list(kg.get("fail", [])),
    }

    # ── SIGINT handler — checkpoint before exit ──────────────────────────────────
    interrupted = False

    def _on_sigint(sig, frame):
        nonlocal interrupted
        interrupted = True
        logger.info("\n[!] Interrupted — saving checkpoint...")

    try:
        signal.signal(signal.SIGINT, _on_sigint)
    except (OSError, ValueError):
        pass  # May fail if not in main thread

    def _checkpoint(label: str = "") -> None:
        """Write current known_good state to disk."""
        kg_working["built_at"] = datetime.now().isoformat()
        kg_working["n_good"]   = len(kg_working["good"])
        kg_working["n_fail"]   = len(set(kg_working["fail"]))
        if label:
            kg_working["note"] = f"Auto-updated by slow_prefetch ({label})."
        _save_known_good(kg_working)

    # ── Fetch loop ───────────────────────────────────────────────────────────────
    for idx, ticker in enumerate(to_fetch):
        if interrupted:
            break

        tried_tickers.add(ticker)

        result = _polygon_fetch(ticker)
        if result:
            dates, closes, volumes = result
            _write_cache(ticker, closes, volumes, dates)
            n_ok += 1
            logger.debug(f"  OK  {ticker} ({len(closes)} bars)")

            # Update working known_good — move from fail to good if needed
            fail_list = kg_working["fail"]
            if ticker in fail_list:
                fail_list.remove(ticker)
            kg_working["good"][ticker] = {
                "n_bars":  len(closes),
                "source":  "polygon_slow",
                "last_ok": datetime.now().isoformat(),
            }
        else:
            n_fail += 1
            logger.debug(f"  FAIL {ticker}")

            # Update working known_good — add to fail list if not already known good
            if ticker not in kg_working["good"]:
                fail_list = kg_working["fail"]
                if ticker not in fail_list:
                    fail_list.append(ticker)

        done = idx + 1

        # ── Progress every 50 tickers ────────────────────────────────────────────
        if done % PROGRESS_EVERY == 0 or done == total:
            elapsed  = time.time() - t_start
            rate_sec = elapsed / done  # seconds per ticker
            remaining = total - done
            eta_sec  = remaining * rate_sec
            eta_str  = (
                f"{eta_sec/3600:.1f}h" if eta_sec > 3600
                else f"{eta_sec/60:.0f}min"
            )
            elapsed_str = (
                f"{elapsed/3600:.1f}h" if elapsed > 3600
                else f"{elapsed/60:.0f}min"
            )
            logger.info(
                f"  [{done}/{total}] ok={n_ok} fail={n_fail} skip={n_skipped} | "
                f"elapsed={elapsed_str} | ETA={eta_str}"
            )

        # ── Checkpoint every 100 tickers ─────────────────────────────────────────
        if done % CHECKPOINT_EVERY == 0:
            _checkpoint(f"checkpoint at {done}/{total}")
            logger.debug(f"  [checkpoint] saved at {done}/{total}")

        # ── Rate delay ────────────────────────────────────────────────────────────
        if done < total and not interrupted:
            time.sleep(DELAY_SEC)

    # ── Final known_good rebuild ─────────────────────────────────────────────────
    logger.info("")
    universe_set = set(universe)

    if interrupted:
        # Save whatever we have
        _checkpoint("interrupted — partial run")
        logger.info(f"  [!] Interrupted at {done}/{total}. Checkpoint saved.")
    else:
        # Full rebuild from cache directory for maximum accuracy
        logger.info("  Rebuilding known_good.json from full cache scan...")
        kg_final = _rebuild_known_good(universe_set, tried_tickers)
        # Merge in any good entries from the old kg that we didn't touch this run
        # (other scripts may have added entries)
        for t, v in kg.get("good", {}).items():
            if t not in kg_final["good"] and _cache_path(t).exists():
                kg_final["good"][t] = v
        kg_final["n_good"] = len(kg_final["good"])
        kg_final["n_fail"] = len(set(kg_final["fail"]))
        _save_known_good(kg_final)
        logger.info(
            f"  known_good.json: {kg_final['n_good']} good | "
            f"{kg_final['n_fail']} fail — updated"
        )

    # ── Summary ──────────────────────────────────────────────────────────────────
    elapsed_total    = time.time() - t_start
    total_in_cache   = sum(1 for p in CACHE_DIR.glob("[A-Z]*.json") if p.stem)
    pct              = round(n_ok / max(total, 1) * 100, 1)

    logger.info("")
    logger.info(f"{'='*62}")
    logger.info(f"  DONE — fetched={n_ok} | failed={n_fail} | fresh_skip={n_skipped}")
    logger.info(f"  Success rate this run: {pct}%")
    logger.info(f"  Total tickers in cache: {total_in_cache}")
    logger.info(
        f"  Time: {elapsed_total:.0f}s "
        f"({elapsed_total/3600:.1f}h | {elapsed_total/60:.1f}min)"
    )
    logger.info(f"{'='*62}")

    # ── Save manifest ────────────────────────────────────────────────────────────
    manifest = {
        "date":           today_str,
        "fetched_at":     datetime.now().isoformat(),
        "source":         "slow_prefetch_polygon_sequential",
        "workers":        1,
        "delay_sec":      DELAY_SEC,
        "total_tried":    total,
        "ok":             n_ok,
        "failed":         n_fail,
        "skipped_fresh":  n_skipped,
        "total_in_cache": total_in_cache,
        "coverage_pct":   round(total_in_cache / max(len(universe), 1) * 100, 1),
        "interrupted":    interrupted,
    }
    manifest_path = CACHE_DIR / "_manifest_slow.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"  Manifest: {manifest_path}")

    return manifest


# ── CLI ──────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sequential rate-safe OHLCV prefetch via Polygon (1 req/13s)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch all tickers including those with fresh cache",
    )
    parser.add_argument(
        "--retry-fails",
        action="store_true",
        help="Only retry tickers in known_fail list regardless of last-attempt age",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Kept for CLI compatibility — always 1 (sequential)",
    )
    args = parser.parse_args()

    if args.workers != 1:
        logger.info(f"[i] --workers={args.workers} ignored — slow_prefetch is always sequential (workers=1)")

    run(force=args.force, retry_fails=args.retry_fails)
