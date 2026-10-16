"""
One-time historical OHLCV backfill — fetch 2019-01-01 to present for full universe.

Why: ohlcv.db only goes back to 2024-01-02. Breadth signal backtesting
requires 5+ years (N>=3 regime transitions). Polygon free EOD has no
historical depth limit — just rate limits (5 req/min free tier).

Runtime: ~1,260 trading days × 1 API call each = ~252 min at 5/min.
Run overnight. Idempotent — already-fetched dates are skipped by
update_ohlcv_bulk.run() (Polygon grouped endpoint returns empty for
already-populated dates? No — ohlcv uses INSERT OR IGNORE, so re-fetching
is safe but wastes API quota. Script skips dates already in DB.)
"""
import sys, os
from pathlib import Path
from datetime import date, timedelta

# ── path setup ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "pre_compute"))
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

import sqlite3
from update_ohlcv_bulk import run as _bulk_run, DB_PATH

START_DATE = date(2019, 1, 1)
CHUNK_SIZE = 30  # process in chunks to allow resume + show progress


def _trading_dates_in_range(start: date, end: date) -> list[str]:
    """Return weekday dates (Mon-Fri) from start to end inclusive."""
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # 0=Mon … 4=Fri
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def _dates_already_in_db(conn, dates: list[str]) -> set[str]:
    """Return set of dates that already have >= 100 rows in ohlcv."""
    placeholders = ",".join("?" * len(dates))
    rows = conn.execute(
        f"SELECT date FROM ohlcv WHERE date IN ({placeholders}) "
        f"GROUP BY date HAVING COUNT(*) >= 100",
        dates,
    ).fetchall()
    return {r[0] for r in rows}


def run():
    today = date.today()
    all_dates = _trading_dates_in_range(START_DATE, today)
    print(f"[backfill] Date range: {START_DATE} → {today} | {len(all_dates)} trading days")

    conn = sqlite3.connect(str(DB_PATH))
    existing = _dates_already_in_db(conn, all_dates)
    conn.close()

    missing = [d for d in all_dates if d not in existing]
    print(f"[backfill] Already populated: {len(existing)} | Missing: {len(missing)}")

    if not missing:
        print("[backfill] Nothing to fetch — all dates already in DB.")
        return

    # Process in chunks for progress visibility
    total_chunks = (len(missing) + CHUNK_SIZE - 1) // CHUNK_SIZE
    for i in range(0, len(missing), CHUNK_SIZE):
        chunk = missing[i: i + CHUNK_SIZE]
        chunk_num = i // CHUNK_SIZE + 1
        print(f"\n[backfill] Chunk {chunk_num}/{total_chunks}: {chunk[0]} → {chunk[-1]}")
        try:
            result = _bulk_run(target_dates=chunk, force=True)
            ok = result.get("ok", 0)
            failed = result.get("failed", 0)
            print(f"[backfill] Chunk {chunk_num} done: {ok} ok, {failed} failed")
        except Exception as e:
            print(f"[backfill] Chunk {chunk_num} ERROR: {e}")
            print("[backfill] Continuing with next chunk...")

    print("\n[backfill] Complete.")


if __name__ == "__main__":
    run()
