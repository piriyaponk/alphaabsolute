"""
thai_fill_combo_columns.py
==========================
Fill Q_CMB / Q_CMB3 / Q_CMB4 columns for rows where they are incorrect
(new rows added by thai_entry_screen.py incremental update).

Root cause that this fixes:
  - entry_screen_db stores all signals as packed booleans.
  - When thai_entry_screen.py writes new rows, Q_CMB* columns are not
    computed by compute_signals() — they are registered in the column
    registry but stored as False (the default for absent columns).
  - In SQLite there are NO NaN values — every column is True or False.
    The old NaN-detection approach was a CSV-era concept and never fired.

New approach (correct for SQLite):
  - For each combo column, recompute the expected value from its source
    columns (AND of all sources, from thai_combo_definitions.json).
  - Find rows where stored == False but correct value == True (missed fill).
  - Overwrite those rows. Idempotent — running twice produces same result.

Reads thai_combo_definitions.json (written by thai_add_combo_signals.py + _r2.py)
to know what AND-combination each combo column represents.

Also invalidates thai_pulse_cache.pkl so thai_paper_trader.py rebuilds scores.

Run AFTER thai_entry_screen.py, BEFORE thai_paper_trader.py.
Typical runtime: <30 seconds (vectorized numpy, full table write only when changes found).
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path
import time

ROOT       = Path(__file__).resolve().parent.parent.parent
DEFS_PATH  = ROOT / "data" / "research" / "thai_combo_definitions.json"
CACHE_PATH = ROOT / "data" / "research" / "thai_pulse_cache.pkl"

import sys as _sys
_sys.path.insert(0, str(ROOT / "scripts" / "research"))
from entry_screen_db import read_entry_screen, write_entry_screen


def main():
    t0 = time.time()

    if not DEFS_PATH.exists():
        print("[SKIP] Combo definitions not found — run thai_add_combo_signals*.py first"); return

    defs = json.loads(DEFS_PATH.read_text())
    print(f"[fill_combo] {len(defs)} combo definitions loaded")

    print(f"[fill_combo] Loading from SQLite...")
    df = read_entry_screen()
    df["date"] = pd.to_datetime(df["date"])
    total_rows = len(df)

    combo_cols_in_df = [c for c in df.columns if c in defs]
    if not combo_cols_in_df:
        print("[SKIP] No combo columns in SQLite yet"); _invalidate_cache(); return

    print(f"  Rows: {total_rows:,} | Combo cols to verify: {len(combo_cols_in_df)}")

    # Build bool array cache for source columns
    def to_bool_arr(col):
        if col not in df.columns:
            return None
        v = df[col]
        if v.dtype == object:
            return v.astype(str).str.lower().isin(["true", "1", "1.0"]).values.astype(np.int8)
        return (pd.to_numeric(v, errors="coerce").fillna(0) == 1).values.astype(np.int8)

    src_cache = {}
    def get_src(col):
        if col not in src_cache:
            src_cache[col] = to_bool_arr(col)
        return src_cache[col]

    # For each combo column: recompute from sources and find rows where
    # stored value is False but computed value is True (missed fill).
    # Root cause: new incremental rows have all combo cols set to False by
    # default (absent-column handling in _unpack), but the source signals
    # may have fired — those rows need correction.
    total_wrong_rows = np.zeros(total_rows, dtype=bool)
    col_corrections = {}   # col_name → corrected int8 array (only cols that changed)
    skipped_missing_src = 0

    for col_name in combo_cols_in_df:
        defn = defs[col_name]
        sources = defn.get("sources", [])
        if len(sources) < 2:
            continue

        srcs = [get_src(s) for s in sources]
        if any(s is None for s in srcs):
            skipped_missing_src += 1
            continue

        # Compute the correct combined value
        correct = srcs[0].copy()
        for s in srcs[1:]:
            correct = correct & s

        # Current stored value (already bool from SQLite)
        current = get_src(col_name)
        if current is None:
            current = np.zeros(total_rows, dtype=np.int8)

        # Rows where stored=False (0) but should be True (1)
        wrong = (correct == 1) & (current == 0)
        if wrong.any():
            # Merge correction into the stored array
            corrected = current.copy()
            corrected[wrong] = 1
            col_corrections[col_name] = corrected
            total_wrong_rows |= wrong

    n_wrong = total_wrong_rows.sum()
    print(f"  Incorrect rows found: {n_wrong:,} | Cols needing correction: {len(col_corrections)} "
          f"| Skipped (missing src): {skipped_missing_src}")

    if n_wrong == 0:
        print("[OK] All combo columns correct — nothing to update")
        _invalidate_cache(); return

    # Apply corrections to the DataFrame
    for col_name, corrected_arr in col_corrections.items():
        df[col_name] = corrected_arr.astype(np.int8)

    n_written = write_entry_screen(df)
    print(f"  Corrected {len(col_corrections)} combo columns across {n_wrong} rows")
    print(f"  Saved {n_written:,} rows to SQLite ({time.time()-t0:.1f}s total)")

    _invalidate_cache()


def _invalidate_cache():
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()
        print(f"  Cache invalidated — will rebuild on next run")
    else:
        print(f"  Cache not present (will build fresh)")


if __name__ == "__main__":
    main()
