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


def run() -> dict:
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


if __name__ == "__main__":
    run()
