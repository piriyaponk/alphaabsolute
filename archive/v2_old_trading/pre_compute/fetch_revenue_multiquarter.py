"""
AlphaAbsolute — Revenue Multi-Quarter Fetcher
==============================================
Populates fundamentals_summary: rev_yoy_q1, rev_yoy_q2, rev_yoy_q3 for all theme tickers.
Also updates rev_yoy_pct (q0) and rev_inflection_label with precise multi-quarter values.

Source: data_engine.get_fundamentals() → EDGAR XBRL (primary) → FMP / Finnhub (fallback)
        EDGAR is FREE and already cached (7-day disk cache).
        No paid API calls needed.

Revenue YoY convention (revenue_history sorted newest-first):
  rev_yoy_pct  (q0) = history[0].yoy_growth  <- latest quarter
  rev_yoy_q1        = history[1].yoy_growth  <- 1Q ago
  rev_yoy_q2        = history[2].yoy_growth  <- 2Q ago
  rev_yoy_q3        = history[3].yoy_growth  <- 3Q ago

rev_inflection_label logic:
  ACCELERATING_STRONG  : q0 > q1 > q2  AND  q0 > 25%
  ACCELERATING         : q0 > q1  AND  q0 > 0%
  TURNAROUND           : q1 <= 0  AND  q0 > 0
  FIRST_INFLECTION     : q2 <= 0  AND  q1 > 0  AND  q0 > 0
  SUSTAINED_GROWTH     : all available quarters positive, not accelerating
  DECELERATING         : q0 < q1 OR q0 < 0
  STABLE               : default
  INSUFFICIENT_DATA    : < 2 quarters with yoy data

two_q_accel (bool): True if q0 > q1 (latest improved vs prior quarter)
                    OR q1 > q2 (sustained momentum building)

Run: weekly (EDGAR cache TTL 7 days — re-fetches automatically when stale)
Args:
  --force     : force re-fetch for all tickers (bypasses EDGAR cache)
  --limit N   : only process first N tickers (test mode)
  --ticker X  : process single ticker
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

DB_PATH = ROOT / "data" / "ohlcv.db"
CACHE_FILE = ROOT / "data" / "themes" / "revenue_multiquarter_cache.json"
CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)


def _get_theme_tickers() -> list[str]:
    """Load all tickers from themed_tickers.json (primary) or theme_rs_latest.json (fallback)."""
    themed = ROOT / "data" / "themes" / "themed_tickers.json"
    if themed.exists():
        data = json.loads(themed.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return sorted(set(t.upper() for t in data if isinstance(t, str)))
        if isinstance(data, dict):
            tickers: set[str] = set()
            for v in data.values():
                if isinstance(v, list):
                    tickers.update(t.upper() for t in v if isinstance(t, str))
            return sorted(tickers)

    theme_file = ROOT / "data" / "rs_universe" / "theme_rs_latest.json"
    if theme_file.exists():
        data = json.loads(theme_file.read_text(encoding="utf-8"))
        themes = data.get("themes", data)
        tickers = set()
        for theme_id, td in themes.items():
            if theme_id.startswith("_") or not isinstance(td, dict):
                continue
            members = td.get("members", {})
            iter_m = members.keys() if isinstance(members, dict) else members
            for t in iter_m:
                if isinstance(t, str):
                    tickers.add(t.upper())
        return sorted(tickers)

    return []


def _classify_label(
    q0: Optional[float],
    q1: Optional[float],
    q2: Optional[float],
    q3: Optional[float],
) -> str:
    """Classify revenue trend from up to 4 consecutive YoY values (newest=q0)."""
    # Need at least q0 for any classification
    if q0 is None:
        return "INSUFFICIENT_DATA"

    # Only q0 available
    if q1 is None:
        if q0 > 0:
            return "FIRST_INFLECTION"
        return "INSUFFICIENT_DATA"

    # TURNAROUND: was negative, now positive
    if q1 <= 0 and q0 > 0:
        return "TURNAROUND"

    # FIRST_INFLECTION: two consecutive positive after negative
    if q2 is not None and q2 <= 0 and q1 > 0 and q0 > 0:
        return "FIRST_INFLECTION"

    # ACCELERATING_STRONG: q0 > q1 > q2 and current > 25%
    if q2 is not None and q0 > q1 and q1 > q2 and q0 > 25:
        return "ACCELERATING_STRONG"

    # ACCELERATING: q0 > q1 and positive
    if q0 > q1 and q0 > 0:
        return "ACCELERATING"

    # SUSTAINED_GROWTH: all available positive but flat/declining rate
    available = [x for x in [q0, q1, q2, q3] if x is not None]
    if all(x > 0 for x in available):
        return "SUSTAINED_GROWTH"

    # DECELERATING: slowing or negative
    if q0 < q1 or q0 < 0:
        return "DECELERATING"

    return "STABLE"


def _two_q_accel(
    q0: Optional[float],
    q1: Optional[float],
    q2: Optional[float],
) -> bool:
    """True if revenue momentum is building over 2+ consecutive quarters."""
    if q0 is None:
        return False
    if q1 is None:
        return q0 > 0  # only q0 available, positive = directional pass

    # Latest quarter improved vs prior
    if q0 > q1:
        return True
    # Momentum building from prior period even if q0 dipped slightly
    if q2 is not None and q1 > q2 and q1 > 0 and q0 > 0:
        return True
    # Turnaround counts as acceleration
    if q1 <= 0 and q0 > 0:
        return True
    return False


def process_tickers(tickers: list[str]) -> dict:
    """Process all tickers using get_fundamentals(). Returns summary stats."""
    from data_engine import get_fundamentals  # type: ignore

    conn = sqlite3.connect(DB_PATH)
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Load existing cache
    cache: dict = {"tickers": {}}
    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    cache.setdefault("tickers", {})

    stats = {
        "total": len(tickers),
        "updated": 0,
        "no_data": 0,
        "insufficient": 0,
        "labels": {},
    }

    for i, ticker in enumerate(tickers):
        try:
            fund = get_fundamentals(ticker)
        except Exception as e:
            print(f"  [{i+1}/{len(tickers)}] {ticker} ERROR: {e}")
            stats["no_data"] += 1
            continue

        if not fund:
            stats["no_data"] += 1
            if (i + 1) % 30 == 0:
                print(f"  [{i+1}/{len(tickers)}] ... {stats['updated']} updated")
            continue

        rev_history = fund.get("revenue_history", [])

        # Extract YoY growth from each quarter (history sorted newest-first)
        def _yoy(idx: int) -> Optional[float]:
            if idx >= len(rev_history):
                return None
            val = rev_history[idx].get("yoy_growth")
            return float(val) if val is not None else None

        q0 = _yoy(0)
        q1 = _yoy(1)
        q2 = _yoy(2)
        q3 = _yoy(3)

        label = _classify_label(q0, q1, q2, q3)
        two_q = _two_q_accel(q0, q1, q2)
        latest_period = rev_history[0].get("quarter", "") if rev_history else ""

        if label == "INSUFFICIENT_DATA":
            stats["insufficient"] += 1

        cache["tickers"][ticker] = {
            "fetched": today_str,
            "q0": q0, "q1": q1, "q2": q2, "q3": q3,
            "label": label,
            "two_q_accel": two_q,
            "latest_period": latest_period,
        }
        stats["labels"][label] = stats["labels"].get(label, 0) + 1
        stats["updated"] += 1

        # Upsert fundamentals_summary
        existing = conn.execute(
            "SELECT ticker FROM fundamentals_summary WHERE ticker=?", (ticker,)
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE fundamentals_summary
                SET rev_yoy_pct         = COALESCE(?, rev_yoy_pct),
                    rev_yoy_q1          = ?,
                    rev_yoy_q2          = ?,
                    rev_yoy_q3          = ?,
                    rev_inflection_label = ?,
                    last_updated        = ?
                WHERE ticker = ?
                """,
                (q0, q1, q2, q3, label, today_str, ticker),
            )
        else:
            conn.execute(
                """
                INSERT INTO fundamentals_summary
                (ticker, rev_yoy_pct, rev_yoy_q1, rev_yoy_q2, rev_yoy_q3,
                 rev_inflection_label, last_updated, source)
                VALUES (?,?,?,?,?,?,?,'edgar_multiq')
                """,
                (ticker, q0, q1, q2, q3, label, today_str),
            )

        if (i + 1) % 10 == 0:
            conn.commit()
            _save_cache(cache, today_str)
            print(
                f"  [{i+1}/{len(tickers)}] {ticker:<8} "
                f"q0={f'{q0:+.1f}%' if q0 is not None else 'N/A':>8}  "
                f"q1={f'{q1:+.1f}%' if q1 is not None else 'N/A':>8}  "
                f"{label}"
            )

    conn.commit()
    _save_cache(cache, today_str)
    conn.close()
    return stats


def _completeness_report(all_tickers: list[str]) -> None:
    """
    Check which theme tickers still lack 2Q revenue data (rev_yoy_q1).
    Missing tickers are EXCLUDED from Monster Scout universe.
    Writes data/themes/rev_coverage.json and prints a summary.
    """
    # Load market cap to separate mega (already excluded from universe)
    mc_cache: dict = {}
    mc_file = ROOT / "data" / "themes" / "market_cap_cache.json"
    if mc_file.exists():
        try:
            mc_data = json.loads(mc_file.read_text(encoding="utf-8"))
            mc_cache = mc_data.get("tickers", {})
        except Exception:
            pass

    # Query DB for 2Q coverage
    conn = sqlite3.connect(DB_PATH)
    has_q1 = set(
        r[0] for r in conn.execute(
            "SELECT ticker FROM fundamentals_summary WHERE rev_yoy_q1 IS NOT NULL"
        ).fetchall()
    )
    conn.close()

    mega      = [t for t in all_tickers if mc_cache.get(t, {}).get("market_cap", 0) >= 20_000_000_000]
    non_mega  = [t for t in all_tickers if t not in mega]
    covered   = [t for t in non_mega if t in has_q1]
    missing   = [t for t in non_mega if t not in has_q1]

    coverage_pct = round(len(covered) / len(non_mega) * 100, 1) if non_mega else 0

    print("\n" + "─" * 70)
    print("REVENUE COMPLETENESS REPORT (Monster Scout universe impact)")
    print(f"  Theme universe    : {len(all_tickers)} tickers")
    print(f"  Mega (>$20B, excl): {len(mega)}")
    print(f"  Non-mega universe : {len(non_mega)}")
    print(f"  Have 2Q data      : {len(covered)} ({coverage_pct}%)")
    print(f"  MISSING 2Q data   : {len(missing)}  ← excluded from Monster Scout universe")
    if missing:
        print(f"  Missing tickers: {', '.join(sorted(missing))}")

    # Write coverage JSON
    coverage = {
        "date":           datetime.now().strftime("%Y-%m-%d"),
        "total_theme":    len(all_tickers),
        "mega_excluded":  len(mega),
        "non_mega":       len(non_mega),
        "covered_2q":     len(covered),
        "missing_2q":     len(missing),
        "coverage_pct":   coverage_pct,
        "covered_tickers": sorted(covered),
        "missing_tickers": sorted(missing),
        "mega_tickers":   sorted(mega),
    }
    cov_file = ROOT / "data" / "themes" / "rev_coverage.json"
    cov_file.write_text(json.dumps(coverage, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Coverage report  → {cov_file}")


def _save_cache(cache: dict, today_str: str) -> None:
    cache["last_updated"] = today_str
    CACHE_FILE.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Populate revenue multi-quarter in fundamentals_summary")
    parser.add_argument("--limit", type=int, default=0, help="Process only first N tickers")
    parser.add_argument("--ticker", type=str, default="", help="Process single ticker")
    args = parser.parse_args()

    if args.ticker:
        tickers = [args.ticker.upper()]
    else:
        tickers = _get_theme_tickers()
        if not tickers:
            print("[ERROR] No theme tickers found in data/themes/themed_tickers.json")
            sys.exit(1)

    if args.limit > 0:
        tickers = tickers[:args.limit]

    print(f"\nRevenue Multi-Quarter Fetch — {len(tickers)} tickers")
    print(f"Source: EDGAR XBRL (via data_engine, free)")
    print(f"DB: {DB_PATH}")
    print("-" * 70)

    t0 = time.time()
    stats = process_tickers(tickers)
    elapsed = time.time() - t0

    print("\n" + "=" * 70)
    print(f"DONE in {elapsed:.1f}s")
    print(f"  Updated : {stats['updated']}/{stats['total']}")
    print(f"  No data : {stats['no_data']}")
    print(f"  Insufficient: {stats['insufficient']}")
    print("\nLabel distribution:")
    for label, count in sorted(stats["labels"].items(), key=lambda x: -x[1]):
        bar = "█" * count
        print(f"  {label:<30} {count:>4}  {bar}")

    # ── Completeness report: which non-mega tickers still lack 2Q data ────────
    # Monster Scout v2 universe requires rev_yoy_q1. Missing = excluded from universe.
    if not args.ticker and args.limit == 0:
        _completeness_report(tickers)


if __name__ == "__main__":
    main()
