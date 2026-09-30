"""
thai_add_combo_signals_r2.py
============================
Round 2 combo generation — build on top of Round 1's 165 quality signals.

Strategy:
  Round 2A: quality_signal AND feature_col (same as R1 but starting from 165 signals
            including Q_CMB ones). Many R1 combos were already done; skip existing.
  Round 2B: triple combos — quality_signal AND quality_signal AND feature_col
            (3-way intersection). Kept only when N>=12 AND h5 lift >=1pp over best pair.
  Round 2C: quality_signal × feature_col × feature_col (2 features on 1 base).

Name convention:
  Q_CMB3_{base[:20]}_{feat1}_{feat2}  (triple)
  Q_CMB4_{base[:20]}_{feat1}_{feat2}  (base + 2 features)

Strict N floor: 12 (slightly lower than R1 because combos are expected to be rarer,
but still high enough to avoid overfitting).
"""
import pandas as pd
import numpy as np
import shutil
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent.parent
CSV_PATH = ROOT / "data" / "research" / "thai_entry_screen_results.csv"
BACK_PATH= ROOT / "data" / "research" / "thai_entry_screen_results_r2.bak.csv"

MIN_N_QUALITY = 15
MIN_H5_QUALITY = 0.63
MIN_N_COMBO    = 12    # slightly lower for 3-way intersections
MIN_H5_LIFT    = 0.01  # +1pp minimum lift

def main():
    print("=" * 60)
    print("thai_add_combo_signals_r2.py  — Round 2")
    print("=" * 60)

    # ── Load ──────────────────────────────────────────────────────
    print("\n[1] Loading CSV...")
    df = pd.read_csv(CSV_PATH, low_memory=False)
    df["date"] = pd.to_datetime(df["date"])
    n_rows = len(df)

    # All Q columns (original + CMB from R1)
    q_cols = [c for c in df.columns if c.startswith("Q") and len(c) > 1
              and (c[1].isdigit() or c.startswith("Q_CMB"))]
    meta = {"date","ticker","regime","fwd1","fwd2","fwd3","fwd5",
            "rs_pct_3m","rs_pct_6m","rs_pct_1m"}
    feat_cols = [c for c in df.columns if c not in meta
                 and not (c.startswith("Q") and len(c) > 1)
                 and not c.startswith("QB")]
    print(f"  Rows: {n_rows:,} | Q-cols: {len(q_cols)} | Feature cols: {len(feat_cols)}")

    # Convert feature cols to bool arrays
    feat_arrs = {}
    for c in feat_cols:
        v = df[c].astype(str).str.lower().isin(["true","1","1.0"]).values
        if v.sum() >= 12:
            feat_arrs[c] = v
    feat_cols = list(feat_arrs.keys())
    print(f"  Active features (N>=12): {len(feat_cols)}")

    # ── fwd5 hit vector ───────────────────────────────────────────
    fwd  = df["fwd5"].values if "fwd5" in df.columns else df["fwd3"].values
    hit  = (fwd > 0).astype(float)
    hit[np.isnan(fwd)] = np.nan
    baseline = np.nanmean(hit) * 100
    print(f"  fwd5 baseline: {baseline:.1f}%")

    # ── Q arrays + hit rates ─────────────────────────────────────
    print("\n[2] Loading Q arrays + computing hit rates...")
    q_arr = {}
    for c in q_cols:
        v = df[c]
        if hasattr(v, "values"):
            v = v.values
        if v.dtype == object:
            v = v.astype(str).str.lower().isin(["true","1","1.0"]).astype(np.int8)
        else:
            v = (v == 1).astype(np.int8)
        q_arr[c] = v

    q_h5 = {}
    for c, v in q_arr.items():
        mask = v == 1
        n = mask.sum()
        if n >= MIN_N_QUALITY:
            q_h5[c] = (np.nanmean(hit[mask]), n)

    quality_sigs = {c: (h, n) for c, (h, n) in q_h5.items() if h >= MIN_H5_QUALITY}
    print(f"  Quality signals (h5>={MIN_H5_QUALITY*100:.0f}%, N>={MIN_N_QUALITY}): {len(quality_sigs)}")

    top_quality = sorted(quality_sigs.items(), key=lambda x: -x[1][0])
    print("  Top 10:")
    for c, (h, n) in top_quality[:10]:
        print(f"    {c:<50}  h5={h*100:.1f}%  N={n}")

    # Backup
    shutil.copy(CSV_PATH, BACK_PATH)
    print(f"\n  Backup: {BACK_PATH.name}")

    # Track existing columns to skip
    existing_cols = set(df.columns)

    # ── Round 2A: quality_signal AND feature_col (new pairs only) ─
    print("\n[3] Round 2A: quality_signal AND feature_col (new only)...")
    new_cols_a = {}
    skipped = 0

    for q_name, (q_h5_val, q_n) in quality_sigs.items():
        q_vec = q_arr[q_name]
        short = q_name[:30].rstrip("_")
        for f_name, f_vec in feat_arrs.items():
            col_name = f"Q_CMB_{short}_{f_name}"
            if col_name in existing_cols or col_name in new_cols_a:
                skipped += 1
                continue
            combined = (q_vec == 1) & f_vec
            n_combo = combined.sum()
            if n_combo < MIN_N_COMBO:
                continue
            h_combo = np.nanmean(hit[combined]) * 100
            if h_combo / 100 >= q_h5_val + MIN_H5_LIFT:
                new_cols_a[col_name] = (combined.astype(np.int8), round(h_combo,1), n_combo, q_name)

    print(f"  New 2A combos: {len(new_cols_a)} | Skipped existing: {skipped}")

    # ── Round 2B: quality × quality × feature (triple) ───────────
    print("\n[4] Round 2B: quality × quality × feature (triple combos)...")
    new_cols_b = {}
    q_list = list(quality_sigs.items())

    for i, (q1, (h1, _)) in enumerate(q_list):
        v1 = q_arr[q1]
        fam1 = q1.split("_")[1] if "_" in q1 else "X"
        if fam1 in ("CMB", "CMB2", "CMB3", "CMB4"):
            fam1 = q1.split("_")[4] if len(q1.split("_")) >= 5 else fam1

        for j, (q2, (h2, _)) in enumerate(q_list):
            if j <= i:
                continue
            fam2 = q2.split("_")[1] if "_" in q2 else "X"
            if fam2 in ("CMB", "CMB2", "CMB3", "CMB4"):
                fam2 = q2.split("_")[4] if len(q2.split("_")) >= 5 else fam2
            if fam1 == fam2:
                continue  # skip same family

            v12 = (v1 == 1) & (q_arr[q2] == 1)
            n12 = v12.sum()
            if n12 < MIN_N_COMBO + 3:  # need slightly more for triple
                continue
            h12 = np.nanmean(hit[v12]) * 100
            best_pair = max(h1, h2) * 100

            # Only try features if the pair itself has some lift
            if h12 < best_pair:
                continue

            for f_name, f_vec in feat_arrs.items():
                combined = v12 & f_vec
                n_combo = combined.sum()
                if n_combo < MIN_N_COMBO:
                    continue
                h_combo = np.nanmean(hit[combined]) * 100
                if h_combo / 100 >= (max(h1, h2) + MIN_H5_LIFT):
                    short1 = q1[:14].rstrip("_")
                    short2 = q2[:14].rstrip("_")
                    col_name = f"Q_CMB3_{short1}_{short2}_{f_name}"
                    if col_name in existing_cols or col_name in new_cols_b:
                        continue
                    new_cols_b[col_name] = (combined.astype(np.int8), round(h_combo,1), n_combo,
                                            f"{q1}+{q2}+{f_name}")

    print(f"  New 2B triple combos: {len(new_cols_b)}")
    top_b = sorted(new_cols_b.items(), key=lambda x: -x[1][1])
    for col, (_, h, n, base) in top_b[:10]:
        print(f"    {col:<55} h5={h:.1f}%  N={n}")

    # ── Round 2C: quality × 2 features ───────────────────────────
    print("\n[5] Round 2C: quality × 2 features...")
    new_cols_c = {}
    feat_list = list(feat_arrs.items())

    for q_name, (q_h5_val, q_n) in quality_sigs.items():
        q_vec = q_arr[q_name]
        short = q_name[:20].rstrip("_")

        for i, (f1, v1) in enumerate(feat_list):
            v_q_f1 = (q_vec == 1) & v1
            n_q_f1 = v_q_f1.sum()
            if n_q_f1 < MIN_N_COMBO + 3:
                continue
            h_q_f1 = np.nanmean(hit[v_q_f1]) * 100
            # Only extend if base+f1 already shows lift
            if h_q_f1 / 100 < q_h5_val + MIN_H5_LIFT / 2:
                continue

            for j, (f2, v2) in enumerate(feat_list):
                if j <= i:
                    continue
                combined = v_q_f1 & v2
                n_combo = combined.sum()
                if n_combo < MIN_N_COMBO:
                    continue
                h_combo = np.nanmean(hit[combined]) * 100
                if h_combo / 100 >= q_h5_val + MIN_H5_LIFT:
                    col_name = f"Q_CMB4_{short}_{f1[:12]}_{f2[:12]}"
                    if col_name in existing_cols or col_name in new_cols_c:
                        continue
                    new_cols_c[col_name] = (combined.astype(np.int8), round(h_combo,1), n_combo,
                                            f"{q_name}+{f1}+{f2}")

    print(f"  New 2C (base+2feat) combos: {len(new_cols_c)}")
    top_c = sorted(new_cols_c.items(), key=lambda x: -x[1][1])
    for col, (_, h, n, base) in top_c[:10]:
        print(f"    {col:<55} h5={h:.1f}%  N={n}")

    # ── Merge all ─────────────────────────────────────────────────
    all_new = {**new_cols_a, **new_cols_b, **new_cols_c}
    print(f"\n[6] Total new columns: {len(all_new)}")

    if all_new:
        new_df = pd.DataFrame(
            {col: vec for col, (vec, *_) in all_new.items()},
            index=df.index
        )
        df = pd.concat([df, new_df], axis=1)

    # ── Final quality summary ─────────────────────────────────────
    print("\n[7] Final quality summary (h5>=63%, N>=15)...")
    all_q = [c for c in df.columns if c.startswith("Q") and len(c) > 1]
    quality_final = []
    for c in all_q:
        v = df[c]
        if v.dtype == object:
            v = v.astype(str).str.lower().isin(["true","1","1.0"])
        mask = (v == 1)
        n = mask.sum()
        if n < MIN_N_QUALITY:
            continue
        h = np.nanmean(hit[mask]) * 100
        if h >= MIN_H5_QUALITY * 100:
            quality_final.append((c, round(h,1), n))

    quality_final.sort(key=lambda x: -x[1])
    print(f"  Total quality signals: {len(quality_final)}")

    print(f"\n  Top 30:")
    print(f"  {'Signal':<55} {'H5':>5}  {'N':>6}")
    print("  " + "-"*70)
    for c, h, n in quality_final[:30]:
        rnd = ""
        if "CMB3" in c or "CMB4" in c:
            rnd = " [R2]"
        elif "CMB" in c:
            rnd = " [R1]"
        print(f"  {c:<55} {h:>4.1f}%  {n:>6,}{rnd}")

    # Family distribution
    def col_family(col):
        parts = col.split("_")
        if len(parts) < 2:
            return "other"
        t = parts[1]
        if t in ("CMB", "CMB2", "CMB3", "CMB4"):
            return parts[4] if len(parts) >= 5 else t
        return t

    fam_counts = {}
    for c, h, n in quality_final:
        fam = col_family(c)
        fam_counts[fam] = fam_counts.get(fam, 0) + 1
    print(f"\n  Quality signals by family (top 20):")
    for fam, cnt in sorted(fam_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"    {fam:<12} {cnt}")

    df.to_csv(CSV_PATH, index=False)
    print(f"\n  Saved: {CSV_PATH}")
    print(f"  Total columns: {len(df.columns)}")
    print("\nDone.")


if __name__ == "__main__":
    main()
