"""
fix_dates_volumes.py — Fix float volumes in ohlcv.db
=====================================================
Converts volume columns stored as REAL (float) to INTEGER.
Polygon grouped endpoint returns volumes as floats; SQLite stores them
as REAL unless the schema enforces INTEGER. This causes:
  - data_quality check: "17568 float volumes" FAIL
  - Downstream code expecting int volumes may get 1234567.0 instead of 1234567

Runs as EOD step after ohlcv_update.
"""
import sqlite3
from pathlib import Path
from datetime import date

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH  = BASE_DIR / "data" / "ohlcv.db"


def _fix_float_volumes() -> dict:  # renamed — unified run() wraps both fixes
    if not DB_PATH.exists():
        print("  [fix_volumes] ohlcv.db not found — skipping")
        return {"fixed": 0}

    conn = sqlite3.connect(str(DB_PATH))
    try:
        # Count float volumes (stored as REAL that are not whole numbers)
        # SQLite: typeof(volume) = 'real' AND volume != CAST(volume AS INTEGER)
        before = conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE volume IS NOT NULL AND typeof(volume)='real' AND volume != CAST(volume AS INTEGER)"
        ).fetchone()[0]

        if before == 0:
            print(f"  [fix_volumes] No float volumes found — already clean")
            conn.close()
            return {"fixed": 0}

        # Fix: round to nearest integer in-place
        conn.execute(
            "UPDATE ohlcv SET volume = CAST(ROUND(volume) AS INTEGER) WHERE volume IS NOT NULL AND typeof(volume)='real'"
        )
        conn.commit()

        after = conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE volume IS NOT NULL AND typeof(volume)='real' AND volume != CAST(volume AS INTEGER)"
        ).fetchone()[0]

        fixed = before - after
        print(f"  [fix_volumes] Fixed {fixed:,} float volumes → INTEGER (before={before:,} after={after:,})")
        return {"fixed": fixed}
    finally:
        conn.close()



def _fix_stale_opens() -> dict:
    """Null out stale open prices where open > high by >0.5%.
    Polygon grouped endpoint bug on gap-down days (e.g. 2026-05-26).
    setup_scanner falls back to open = close when open IS NULL.
    Per CLAUDE.md: OHLCV -- Stale Open Price Bug.
    """
    if not DB_PATH.exists():
        print("  [fix_stale_open] ohlcv.db not found -- skipping")
        return {"fixed": 0}
    conn = sqlite3.connect(str(DB_PATH))
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM ohlcv WHERE open IS NOT NULL AND high IS NOT NULL "
            "AND open > high AND (open - high) / CAST(high AS REAL) > 0.005"
        ).fetchone()[0]
        if before == 0:
            print("  [fix_stale_open] No stale opens found -- already clean")
            return {"fixed": 0}
        conn.execute(
            "UPDATE ohlcv SET open = NULL WHERE open IS NOT NULL AND high IS NOT NULL "
            "AND open > high AND (open - high) / CAST(high AS REAL) > 0.005"
        )
        conn.commit()
        print(f"  [fix_stale_open] Nulled {before:,} stale open prices (open > high by >0.5%)")
        return {"fixed": before}
    finally:
        conn.close()


def run() -> dict:
    """Run both fixes: float volumes + stale opens. Called by pipeline runner."""
    r1 = _fix_float_volumes()
    r2 = _fix_stale_opens()
    return {"float_volumes_fixed": r1.get("fixed", 0), "stale_opens_fixed": r2.get("fixed", 0)}


if __name__ == "__main__":
    run()
