"""
AlphaAbsolute v2 — Forward Testing DB Migration
================================================
One-time migration: adds forward-return columns to screening_history
and creates fwd_test_cohorts table for the weekly learning loop.

Run once: python scripts/pre_compute/fwd_test_migrate.py
Idempotent — safe to re-run.
"""

from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB   = ROOT / "data" / "ohlcv.db"


def migrate() -> None:
    print(f"DB: {DB}")
    conn = sqlite3.connect(DB)
    cur  = conn.cursor()

    # ── 1. Add forward-return columns to screening_history ─────────────────────
    new_cols = [
        ("regime",        "TEXT DEFAULT NULL"),
        ("setup_type",    "TEXT DEFAULT NULL"),
        ("rank_score",    "REAL DEFAULT NULL"),
        ("fwd_ret_4w",    "REAL DEFAULT NULL"),
        ("fwd_ret_8w",    "REAL DEFAULT NULL"),
        ("fwd_ret_13w",   "REAL DEFAULT NULL"),
        ("qqq_ret_4w",    "REAL DEFAULT NULL"),
        ("qqq_ret_8w",    "REAL DEFAULT NULL"),
        ("qqq_ret_13w",   "REAL DEFAULT NULL"),
        ("fwd_filled_at", "TEXT DEFAULT NULL"),
    ]

    # Fetch existing columns once
    cur.execute("PRAGMA table_info(screening_history)")
    existing = {row[1] for row in cur.fetchall()}

    added = []
    for col_name, col_def in new_cols:
        if col_name not in existing:
            cur.execute(f"ALTER TABLE screening_history ADD COLUMN {col_name} {col_def}")
            added.append(col_name)
            print(f"  + Added column: screening_history.{col_name}")
        else:
            print(f"  ~ Already exists: screening_history.{col_name}")

    # ── 2. Create fwd_test_cohorts table ──────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS fwd_test_cohorts (
            cohort_date     TEXT NOT NULL PRIMARY KEY,
            regime          TEXT NOT NULL,
            universe_n      INTEGER,
            n_5gate         INTEGER,
            n_4gate         INTEGER,
            n_3gate         INTEGER,
            n_2gate         INTEGER,
            n_1gate         INTEGER,
            qqq_close       REAL,
            spy_close       REAL,
            fill_4w_date    TEXT DEFAULT NULL,
            fill_8w_date    TEXT DEFAULT NULL,
            fill_13w_date   TEXT DEFAULT NULL,
            created_at      TEXT DEFAULT (date('now'))
        )
    """)
    print("  + Table fwd_test_cohorts: OK")

    # ── 3. Indexes for fast fwd_fill lookups ──────────────────────────────────
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_sh_fwd_fill
        ON screening_history(run_date, fwd_filled_at)
        WHERE fwd_filled_at IS NULL
    """)
    print("  + Index idx_sh_fwd_fill: OK")

    conn.commit()
    conn.close()

    if added:
        print(f"\n  Migration complete. Added {len(added)} columns.")
    else:
        print("\n  Migration complete. Nothing to add (already up-to-date).")


if __name__ == "__main__":
    migrate()
