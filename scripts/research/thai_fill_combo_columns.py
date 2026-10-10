"""
thai_fill_combo_columns.py
==========================
Fill Q_CMB / Q_CMB3 / Q_CMB4 columns for rows where they are NaN (new rows
added by thai_entry_screen.py incremental update).

Reads thai_combo_definitions.json (written by thai_add_combo_signals.py + _r2.py)
to know what AND-combination each combo column represents.

Also invalidates thai_pulse_cache.pkl so thai_paper_trader.py rebuilds scores.

Run AFTER thai_entry_screen.py, BEFORE thai_paper_trader.py.
Typical runtime: <10 seconds (only processes NaN rows).
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

    # Find rows where ANY combo column is NaN
    combo_cols_in_df = [c for c in df.columns if c in defs]
    if not combo_cols_in_df:
        print("[SKIP] No combo columns in CSV yet"); _invalidate_cache(); return

    combo_arr = df[combo_cols_in_df].apply(pd.to_numeric, errors="coerce").values
    needs_fill_mask = np.isnan(combo_arr).any(axis=1)
    n_fill = needs_fill_mask.sum()
    print(f"  Rows: {total_rows:,} | Combo cols: {len(combo_cols_in_df)} | Needs fill: {n_fill:,}")

    if n_fill == 0:
        print("[OK] All combo columns filled — nothing to do")
        _invalidate_cache(); return

    fill_idx = np.where(needs_fill_mask)[0]

    # Build bool array cache for source columns
    def to_bool_full(col):
        if col not in df.columns:
            return None
        v = df[col]
        if v.dtype == object:
            return v.astype(str).str.lower().isin(["true","1","1.0"]).values.astype(np.int8)
        return (pd.to_numeric(v, errors="coerce").fillna(0) == 1).values.astype(np.int8)

    src_cache = {}
    def get_src(col):
        if col not in src_cache:
            src_cache[col] = to_bool_full(col)
        return src_cache[col]

    filled_cols = 0
    skipped_missing_src = 0

    for col_name in combo_cols_in_df:
        defn = defs[col_name]
        sources = defn.get("sources", [])
        if len(sources) < 2:
            continue

        # Check all sources exist
        if any(get_src(s) is None for s in sources):
            skipped_missing_src += 1
            continue

        # Compute combined bool for fill rows only
        combined = get_src(sources[0])[fill_idx].copy()
        for s in sources[1:]:
            combined = combined & get_src(s)[fill_idx]

        # Only update NaN cells in this column
        col_vals = pd.to_numeric(df[col_name], errors="coerce").values.copy()
        nan_in_col = np.isnan(col_vals)
        write_where = fill_idx[nan_in_col[fill_idx]]
        if len(write_where) == 0:
            continue

        # Map fill_idx positions to write_where positions
        fill_idx_set = {v: i for i, v in enumerate(fill_idx)}
        for row_i in write_where:
            col_vals[row_i] = combined[fill_idx_set[row_i]]

        df[col_name] = col_vals.astype(np.int8)
        filled_cols += 1

    print(f"  Filled {filled_cols} columns | Skipped (missing src): {skipped_missing_src}")
    n_written = write_entry_screen(df)
    print(f"  Saved {n_written:,} rows to SQLite ({time.time()-t0:.1f}s total)")

    _invalidate_cache()


def _invalidate_cache():
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()
        print(f"  Cache invalidated → will rebuild on next run")
    else:
        print(f"  Cache not present (will build fresh)")


if __name__ == "__main__":
    main()
