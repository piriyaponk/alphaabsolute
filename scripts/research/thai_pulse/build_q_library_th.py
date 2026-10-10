"""
build_q_library_th.py — Build PULSE-TH Signal Library
=======================================================
Reads thai_entry_screen_results.csv (39k rows, 7659 Q-signals, fwd1/3/5)
Computes per-signal hit rates (h3 = fwd5 > 0, same as PULSE-TH calibration)
Outputs q_library_th.json in identical schema to q_library_1000.json

Grade thresholds (TH uses fwd5, lower threshold than US fwd3):
  S_BOTH : h3 >= 80%
  S      : h3 >= 70%
  A      : h3 >= 63%
  (quality = A and above)

Family = first non-numeric token in Q-column name:
  Q813_i9_i8_fvg8  → "i9"
  Q5_mom_pp_fvg    → "mom"
  Q_CMB_Q670_j3... → "j3"

Run:
  python scripts/research/thai_pulse/build_q_library_th.py
  python scripts/research/thai_pulse/build_q_library_th.py --min-n 10 --fwd fwd3
"""

import pandas as pd
import numpy as np
import json
import argparse
from pathlib import Path

ROOT     = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "research" / "thai_pulse" / "q_library_th.json"

import sys as _sys
_sys.path.insert(0, str(ROOT / "scripts" / "research"))
from entry_screen_db import read_entry_screen

# Standard thresholds (mirror q_library_1000.json grading)
H3_S_BOTH = 80.0
H3_S      = 70.0
H3_A      = 63.0
MIN_N     = 15      # min occurrences to score a signal
FWD_COL   = "fwd5"  # TH calibrated on 5-day forward return


def _col_family(col: str) -> str:
    """Extract signal family from Q-column name.
    Q813_i9_i8_fvg8   → 'i9'   (first token after Q-number)
    Q5_mom_pp_fvg      → 'mom'
    Q_CMB_Q670_j3_...  → 'j3'  (first token of base signal)
    """
    parts = col.split("_")
    if len(parts) < 2:
        return "other"
    if parts[0].startswith("Q") and parts[0][1:].isdigit():
        # Q{num}_family_... → parts[1]
        return parts[1]
    if len(parts) >= 2 and parts[1] in ("CMB", "CMB2"):
        # Q_CMB_Q670_j3_... → parts[2]=Q670, parts[3]=j3 (family)
        return parts[3] if len(parts) >= 4 else "cmb"
    # Fallback: first token after Q prefix
    return parts[1] if len(parts) > 1 else "other"


def build_library(fwd_col: str, min_n: int) -> dict:
    print("Loading from SQLite entry_screen_signals ...")
    df = read_entry_screen()
    print(f"  Rows: {len(df)}  |  Columns: {len(df.columns)}")

    if fwd_col not in df.columns:
        raise ValueError(f"Column '{fwd_col}' not found. Available: {[c for c in df.columns if c.startswith('fwd')]}")

    fwd = pd.to_numeric(df[fwd_col], errors="coerce")
    hit = (fwd > 0).astype(float)  # 1.0 = up, 0.0 = down, NaN kept
    hit_valid = fwd.notna()         # rows with forward return available

    q_cols = [c for c in df.columns if c.startswith("Q")]
    print(f"  Q-signals to score: {len(q_cols)}")

    results = []
    for col in q_cols:
        sig = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(bool)
        mask = sig & hit_valid
        n = int(mask.sum())
        if n < min_n:
            continue

        h3_frac = float(hit[mask].mean())  # fraction 0.0–1.0
        h3_pct  = round(h3_frac * 100, 1)  # % for storage (mirror US)
        avg_fwd = round(float(fwd[mask].mean()), 4)

        # Grade
        if h3_pct >= H3_S_BOTH:
            grade = "S_BOTH"
        elif h3_pct >= H3_S:
            grade = "S"
        elif h3_pct >= H3_A:
            grade = "A"
        else:
            grade = "B"

        results.append({
            "label":  col,
            "h3":     h3_pct,
            "h5":     h3_pct,   # TH uses fwd5 as primary — alias h5=h3 for clarity
            "avg":    avg_fwd,
            "N":      n,
            "grade":  grade,
            "family": _col_family(col),
        })

    results.sort(key=lambda x: x["h3"], reverse=True)

    grade_s_both = sum(1 for r in results if r["grade"] == "S_BOTH")
    grade_s      = sum(1 for r in results if r["grade"] == "S")
    grade_a      = sum(1 for r in results if r["grade"] == "A")
    quality      = sum(1 for r in results if r["grade"] in ("S_BOTH", "S", "A"))

    library = {
        "total_scored":  len(q_cols),
        "total_results": len(results),
        "grade_s_both":  grade_s_both,
        "grade_s":       grade_s,
        "grade_a":       grade_a,
        "quality_total": quality,
        "fwd_col":       fwd_col,
        "min_n":         min_n,
        "h3_threshold":  H3_A,
        "results":       results,
    }

    print(f"\n  Scored: {len(results)} signals (with N>={min_n})")
    print(f"  S_BOTH (h3>={H3_S_BOTH}%): {grade_s_both}")
    print(f"  S      (h3>={H3_S}%):  {grade_s}")
    print(f"  A      (h3>={H3_A}%):  {grade_a}")
    print(f"  Quality total: {quality}")
    return library


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fwd",   default=FWD_COL, help="Forward return column (fwd1/fwd3/fwd5)")
    ap.add_argument("--min-n", type=int, default=MIN_N, help="Min occurrences per signal")
    ap.add_argument("--out",   default=str(OUT_PATH))
    args = ap.parse_args()

    library = build_library(args.fwd, args.min_n)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(library, f, indent=2)
    print(f"\nSaved → {out_path}")


if __name__ == "__main__":
    run()
