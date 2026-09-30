"""
thai_add_combo_signals.py
=========================
Build new Q-signal combinations on top of existing fwd5-quality signals.

Strategy:
  1. Find all Q-signals with fwd5 h5 >= 63% AND N >= 15 (quality universe)
  2. For each quality signal, try AND-ing with every other feature column
     → keep when: h5 improves AND N >= 10
  3. Also try AND-ing pairs of quality signals together (same family preferred)
  4. Name new columns: Q_CMB_{base}_{addon}

These are targeted combos that START from proven quality signals —
the opposite approach from RS-gating which starts from RS and adds features.

Output: updated thai_entry_screen_results.csv (backup first)
"""
import pandas as pd
import numpy as np
import shutil
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent.parent
CSV_PATH = ROOT / "data" / "research" / "thai_entry_screen_results.csv"
BACK_PATH= ROOT / "data" / "research" / "thai_entry_screen_results_precombo.bak.csv"

MIN_N_QUALITY = 15    # minimum N for a signal to be called "quality"
MIN_H5_QUALITY = 0.63 # quality threshold
MIN_N_COMBO    = 10   # minimum N for a new combo to keep
MIN_H5_LIFT    = 0.01 # new combo must be at least +1pp better than base

def main():
    print("=" * 60)
    print("thai_add_combo_signals.py")
    print("Building combinations on fwd5-quality signals")
    print("=" * 60)

    # ── Load ──────────────────────────────────────────────────────
    print("\n[1] Loading CSV...")
    df = pd.read_csv(CSV_PATH, low_memory=False)
    df["date"] = pd.to_datetime(df["date"])
    n_rows = len(df)

    q_cols = [c for c in df.columns if c.startswith("Q") and len(c) > 1 and c[1].isdigit()]
    # Feature columns: non-Q, non-meta boolean columns
    meta = {"date","ticker","regime","fwd1","fwd2","fwd3","fwd5",
            "rs_pct_3m","rs_pct_6m","rs_pct_1m"}
    feat_cols = [c for c in df.columns if c not in meta
                 and not (c.startswith("Q") and len(c) > 1)
                 and not c.startswith("QB")]

    print(f"  Rows: {n_rows:,} | Q-cols: {len(q_cols)} | Feature cols: {len(feat_cols)}")

    # Convert feature cols to bool arrays up front
    feat_arrs = {}
    for c in feat_cols:
        v = df[c].astype(str).str.lower().isin(["true","1","1.0"]).values
        if v.sum() >= 10:  # skip dead features
            feat_arrs[c] = v

    feat_cols = list(feat_arrs.keys())
    print(f"  Active features (N>=10): {len(feat_cols)}")

    # ── fwd5 hit vector ───────────────────────────────────────────
    fwd  = df["fwd5"].values if "fwd5" in df.columns else df["fwd3"].values
    hit  = (fwd > 0).astype(float)
    hit[np.isnan(fwd)] = np.nan
    baseline = np.nanmean(hit) * 100
    print(f"  fwd5 baseline: {baseline:.1f}%")

    # ── Q arrays ─────────────────────────────────────────────────
    print("\n[2] Computing per-Q hit rates on fwd5...")
    q_arr = {}
    for c in q_cols:
        v = df[c]
        if hasattr(v, "values"):
            v = v.values
        # handle bool or int stored as string
        if v.dtype == object:
            v = v.astype(str).str.lower().isin(["true","1","1.0"]).astype(np.int8)
        else:
            v = (v == 1).astype(np.int8)
        q_arr[c] = v

    # Per-signal h5
    q_h5 = {}
    for c, v in q_arr.items():
        mask = v == 1
        n = mask.sum()
        if n >= MIN_N_QUALITY:
            q_h5[c] = (np.nanmean(hit[mask]), n)

    quality_sigs = {c: (h, n) for c, (h, n) in q_h5.items() if h >= MIN_H5_QUALITY}
    print(f"  Quality signals (h5>={MIN_H5_QUALITY*100:.0f}%, N>={MIN_N_QUALITY}): {len(quality_sigs)}")

    # Sort by h5 desc for display
    top_quality = sorted(quality_sigs.items(), key=lambda x: -x[1][0])
    print("  Top 10 quality signals:")
    for c, (h, n) in top_quality[:10]:
        print(f"    {c:<40}  h5={h*100:.1f}%  N={n}")

    # Backup
    shutil.copy(CSV_PATH, BACK_PATH)
    print(f"\n  Backup: {BACK_PATH.name}")

    # ── Strategy A: quality_signal AND feature_col ────────────────
    print("\n[3] Strategy A: quality_signal AND feature_col...")
    new_cols_a = {}
    skipped_exist = 0

    for q_name, (q_h5_val, q_n) in quality_sigs.items():
        q_vec = q_arr[q_name]
        short = q_name[:30].rstrip("_")
        for f_name, f_vec in feat_arrs.items():
            col_name = f"Q_CMB_{short}_{f_name}"
            if col_name in df.columns:
                skipped_exist += 1
                continue
            combined = (q_vec == 1) & f_vec
            n_combo = combined.sum()
            if n_combo < MIN_N_COMBO:
                continue
            h_combo = np.nanmean(hit[combined]) * 100
            if h_combo / 100 >= q_h5_val + MIN_H5_LIFT:
                new_cols_a[col_name] = (combined.astype(np.int8), round(h_combo, 1), n_combo, q_name)

    print(f"  New combos (h5 lift >={MIN_H5_LIFT*100:.0f}pp): {len(new_cols_a)} | skipped existing: {skipped_exist}")

    # Show top 20 new combos
    top_a = sorted(new_cols_a.items(), key=lambda x: -x[1][1])
    print(f"  Top 20 new combos:")
    print(f"  {'Column':<55} {'H5':>5}  {'N':>6}  {'Base'}")
    print("  " + "-"*80)
    for col, (_, h, n, base) in top_a[:20]:
        print(f"  {col:<55} {h:>4.1f}%  {n:>6,}  {base}")

    # ── Strategy B: quality_signal AND quality_signal (cross-family) ──
    print("\n[4] Strategy B: quality_signal × quality_signal (cross combos)...")
    new_cols_b = {}
    q_list = list(quality_sigs.items())

    for i, (q1, (h1, n1)) in enumerate(q_list):
        v1 = q_arr[q1]
        fam1 = q1.split("_")[1] if "_" in q1 else "X"
        for j, (q2, (h2, n2)) in enumerate(q_list):
            if j <= i:
                continue
            fam2 = q2.split("_")[1] if "_" in q2 else "X"
            if fam1 == fam2:
                continue  # skip same-family (too correlated)
            combined = (v1 == 1) & (q_arr[q2] == 1)
            n_combo = combined.sum()
            if n_combo < MIN_N_COMBO:
                continue
            h_combo = np.nanmean(hit[combined]) * 100
            best_base = max(h1, h2)
            if h_combo / 100 >= best_base + MIN_H5_LIFT:
                short1 = q1[:20].rstrip("_")
                short2 = q2[:20].rstrip("_")
                col_name = f"Q_CMB2_{short1}_{short2}"
                if col_name in df.columns:
                    continue
                new_cols_b[col_name] = (combined.astype(np.int8), round(h_combo, 1), n_combo,
                                        f"{q1}+{q2}")

    print(f"  Cross-family combos: {len(new_cols_b)}")
    top_b = sorted(new_cols_b.items(), key=lambda x: -x[1][1])
    print(f"  Top 10 cross-family combos:")
    for col, (_, h, n, base) in top_b[:10]:
        print(f"  {col:<55} {h:>4.1f}%  N={n}")

    # ── Merge all new columns ─────────────────────────────────────
    print("\n[5] Writing new columns to CSV...")
    all_new = {**new_cols_a, **new_cols_b}
    print(f"  Total new Q-columns: {len(all_new)}")

    if all_new:
        new_df_parts = {}
        for col_name, (vec, h, n, base) in all_new.items():
            new_df_parts[col_name] = vec
        new_df = pd.DataFrame(new_df_parts, index=df.index)
        df = pd.concat([df, new_df], axis=1)

    # ── Final quality summary ─────────────────────────────────────
    print("\n[6] Final quality summary on fwd5...")
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
    print(f"  Total quality signals (h5>=63%, N>=15): {len(quality_final)}")

    print(f"\n  Top 30:")
    print(f"  {'Signal':<55} {'H5':>5}  {'N':>6}")
    print("  " + "-"*70)
    for c, h, n in quality_final[:30]:
        tag = " [NEW]" if c.startswith("Q_CMB") else ""
        print(f"  {c:<55} {h:>4.1f}%  {n:>6,}{tag}")

    # Family distribution
    fam_counts = {}
    for c, h, n in quality_final:
        parts = c.split("_")
        fam = parts[1] if len(parts) >= 2 else "other"
        if fam not in ("CMB", "CMB2", "RS"):
            fam_counts[fam] = fam_counts.get(fam, 0) + 1
    print(f"\n  Quality signals by family (existing Q-names):")
    for fam, cnt in sorted(fam_counts.items(), key=lambda x: -x[1])[:15]:
        print(f"    {fam:<12} {cnt}")

    df.to_csv(CSV_PATH, index=False)
    print(f"\n  Saved: {CSV_PATH}")
    print(f"  Total columns: {len(df.columns)}")
    print("\nDone.")


if __name__ == "__main__":
    main()
