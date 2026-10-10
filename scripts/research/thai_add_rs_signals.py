"""
thai_add_rs_signals.py
======================
Step 1: Compute RS percentile (3M, 6M) for every (date, ticker) from thai_ohlcv.db
Step 2: Join into thai_entry_screen_results.csv
Step 3: Generate RS-gated Q-signals (F_RS_TH family) — same approach as PULSE-US

New Q-columns added:
  Q_RS_r{rs_n}_{feat}  — rs_pct_3m >= rs_n AND feature fired

Example:
  Q_RS_r70_A3_vol_surge  = rs_pct_3m>=70 AND A3_vol_surge
  Q_RS_r80_I4_2down_bounce = rs_pct_3m>=80 AND I4_2down_bounce

This creates the RS gate that PULSE-US has but TH currently lacks.
Expected: new signals with h3 > 70% (matching top PULSE-US performance)

Output: data/research/thai_entry_screen_results.csv (updated in-place, backup first)
"""
import pandas as pd
import numpy as np
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH   = ROOT / "data" / "research" / "thai_ohlcv.db"

import sys as _sys
_sys.path.insert(0, str(ROOT / "scripts" / "research"))
from entry_screen_db import read_entry_screen, write_entry_screen

# RS thresholds to gate on (3M and 6M)
RS_THRESHOLDS = [60, 70, 75, 80, 85, 90]

# Feature columns to combine with RS gate
# Pick the proven high-quality individual signals (from existing Q-signal inspection)
FEATURES_FOR_RS = [
    # A-group: breakout
    "A1_bkt20_vol", "A2_bkt10", "A3_vol_surge",
    # B-group: tight/near-high structure
    "B1_tight_near_hi", "B2_tight", "B3_near_hi20",
    # C-group: momentum
    "C1_early_move", "C2_above_ma50", "C3_pocket_pivot",
    # I-group: candlestick quality
    "I2_power_move", "I2b_power_move_strong",
    "I4_2down_bounce", "I4b_3down_bounce",
    "I8_3up_days", "I9_inside_close_hi", "I10_top_decile_day",
    # K-group: first pullback (high quality)
    "K1_first_pb_after_bkt", "K4_first_pb_to_ma20",
    # M-group: VDU expansion
    "M1_vdu_expansion", "M2_vdu_deep_expansion", "M5_post_bkt_vdu_expansion",
    # N-group: pocket pivot
    "N1_pp_at_ma20", "N3_pp_after_vdu",
    # O-group: FVG / CHOCH
    "O1_in_fvg_3d", "O1_in_fvg_5d", "O2_choch_10d",
    # D-group: full setups
    "D5_full_setup",
    # X combos
    "X7_pb_dry_ma50",
]

# RS momentum: rs_pct_3m > rs_pct_6m (same as PULSE-US rsmom gate)
ADD_RSMOM_COMBOS = True


def compute_rs_percentile(conn: sqlite3.Connection) -> pd.DataFrame:
    """Compute rs_pct_3m and rs_pct_6m for every (date, ticker).
    RS = (close / close_N_days_ago - 1), percentile-ranked within SET universe.
    """
    print("Loading OHLCV...")
    df = pd.read_sql(
        "SELECT ticker, date, close FROM thai_ohlcv WHERE ticker != '^SET.BK'",
        conn,
        parse_dates=["date"]
    )
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)

    print(f"  {df['ticker'].nunique()} tickers, {df['date'].nunique()} dates")

    # Pivot to (date, ticker) close matrix
    px = df.pivot(index="date", columns="ticker", values="close").sort_index()

    # Returns: 63-day (3M) and 126-day (6M)
    ret_3m  = px / px.shift(63) - 1
    ret_6m  = px / px.shift(126) - 1
    ret_3m_mom = px / px.shift(21) - 1   # 1M for momentum direction

    # Percentile rank row-wise (each date, rank vs SET universe)
    print("Computing percentile ranks (this takes ~30s)...")
    rs_3m = ret_3m.rank(axis=1, pct=True) * 100
    rs_6m = ret_6m.rank(axis=1, pct=True) * 100
    rs_1m = ret_3m_mom.rank(axis=1, pct=True) * 100  # 1M for rsmom signal

    # Stack back to long format
    rs3  = rs_3m.stack().rename("rs_pct_3m").reset_index()
    rs6  = rs_6m.stack().rename("rs_pct_6m").reset_index()
    rs1  = rs_1m.stack().rename("rs_pct_1m").reset_index()
    rs3.columns = ["date", "ticker", "rs_pct_3m"]
    rs6.columns = ["date", "ticker", "rs_pct_6m"]
    rs1.columns = ["date", "ticker", "rs_pct_1m"]

    rs = rs3.merge(rs6, on=["date","ticker"]).merge(rs1, on=["date","ticker"])
    rs["date"] = pd.to_datetime(rs["date"])
    print(f"  RS computed: {len(rs):,} rows")
    return rs


def main():
    print("=" * 60)
    print("thai_add_rs_signals.py")
    print("Adding RS-gated Q-signals to PULSE-TH CSV")
    print("=" * 60)

    # ── 1. Load from SQLite ───────────────────────────────────────
    print(f"\n[1] Loading from SQLite entry_screen_signals...")
    df = read_entry_screen()
    df["date"] = pd.to_datetime(df["date"])
    n_orig = len(df)
    q_orig = [c for c in df.columns if c.startswith("Q") and len(c) > 1 and c[1].isdigit()]
    print(f"  Rows: {n_orig:,} | Existing Q-cols: {len(q_orig)}")

    # Check which features actually exist
    feat_available = [f for f in FEATURES_FOR_RS if f in df.columns]
    feat_missing   = [f for f in FEATURES_FOR_RS if f not in df.columns]
    print(f"  Features available: {len(feat_available)} | Missing: {feat_missing}")

    # ── 2. Compute RS ─────────────────────────────────────────────
    print("\n[2] Computing RS percentiles from thai_ohlcv.db...")
    conn = sqlite3.connect(DB_PATH)
    rs_df = compute_rs_percentile(conn)
    conn.close()

    # ── 3. Join RS into main CSV ──────────────────────────────────
    print("\n[3] Joining RS into CSV...")
    # Drop existing RS cols to avoid _x/_y suffix conflicts on re-run
    df = df.drop(columns=["rs_pct_3m","rs_pct_6m","rs_pct_1m"], errors="ignore")
    df = df.merge(rs_df[["date","ticker","rs_pct_3m","rs_pct_6m","rs_pct_1m"]],
                  on=["date","ticker"], how="left")
    n_with_rs = df["rs_pct_3m"].notna().sum()
    print(f"  Rows with RS: {n_with_rs:,} / {n_orig:,} ({n_with_rs/n_orig*100:.1f}%)")
    # Rows missing RS = early dates before 63-day lookback fills in
    print(f"  RS coverage by year:")
    df["year"] = df["date"].dt.year
    cov = df.groupby("year")["rs_pct_3m"].apply(lambda x: x.notna().mean() * 100).round(1)
    print(cov.to_string())
    df = df.drop(columns=["year"])

    # rsmom gate: rs_pct_3m > rs_pct_6m (trending up in RS)
    df["_rsmom"] = df["rs_pct_3m"] > df["rs_pct_6m"]

    # ── 4. Generate RS-gated Q-signals ───────────────────────────
    print("\n[4] Generating RS-gated Q-signals...")
    new_q_cols = []
    skipped_exist = 0

    # Convert feature cols to bool
    for feat in feat_available:
        df[feat] = df[feat].astype(str).str.lower().isin(["true", "1", "1.0"])

    for rs_n in RS_THRESHOLDS:
        rs_gate = df["rs_pct_3m"] >= rs_n
        rs_gate_filled = rs_gate.fillna(False)

        for feat in feat_available:
            col_name = f"Q_RS_r{rs_n}_{feat}"
            if col_name in df.columns:
                skipped_exist += 1
                continue
            df[col_name] = (rs_gate_filled & df[feat]).astype(int)
            new_q_cols.append(col_name)

        if ADD_RSMOM_COMBOS:
            # Add rsmom AND feature (RS momentum filter, mirrors PULSE-US)
            for feat in feat_available:
                col_name = f"Q_RS_r{rs_n}_mom_{feat}"
                if col_name in df.columns:
                    skipped_exist += 1
                    continue
                combined = rs_gate_filled & df["_rsmom"] & df[feat]
                df[col_name] = combined.astype(int)
                new_q_cols.append(col_name)

    print(f"  New Q-cols added: {len(new_q_cols)} | Skipped existing: {skipped_exist}")

    # ── 5. Quick hit-rate check ───────────────────────────────────
    print("\n[5] Quick hit-rate check on new signals (fwd3)...")
    if "fwd3" in df.columns:
        hit = (df["fwd3"] > 0).astype(float)
        hit[df["fwd3"].isna()] = np.nan
        baseline = np.nanmean(hit) * 100
        print(f"  Baseline fwd3 hit rate: {baseline:.1f}%")

        results = []
        for col in new_q_cols:
            mask = df[col] == 1
            n = mask.sum()
            if n < 20:
                continue
            h3 = np.nanmean(hit[mask]) * 100
            results.append({"col": col, "n": n, "h3": round(h3, 1)})

        results.sort(key=lambda x: -x["h3"])
        print(f"\n  Top 30 new signals by h3 (N>=20):")
        print(f"  {'Signal':<45} {'N':>6}  {'H3':>6}")
        print("  " + "-" * 60)
        for r in results[:30]:
            marker = " <-- NEW QUALITY" if r["h3"] >= 63 else ""
            print(f"  {r['col']:<45} {r['n']:>6,}  {r['h3']:>5.1f}%{marker}")

        quality_new = [r for r in results if r["h3"] >= 63]
        print(f"\n  NEW quality signals (h3>=63%): {len(quality_new)}")
        high_new    = [r for r in results if r["h3"] >= 70]
        print(f"  HIGH quality signals (h3>=70%): {len(high_new)}")
        if high_new:
            print("  Best:", high_new[0])
    else:
        print("  fwd3 column not found — skipping hit-rate check")

    # ── 6. Save to SQLite ───────────────────────────────────────
    df = df.drop(columns=["_rsmom"], errors="ignore")
    print(f"\n[6] Writing to SQLite ({len(df.columns)} columns)...")
    n_written = write_entry_screen(df)
    print(f"  Written: {n_written:,} rows")
    print(f"  Total Q-cols now: {len(q_orig) + len(new_q_cols)}")
    print("\nDone. Re-run thai_paper_trader.py to pick up new signals.")


if __name__ == "__main__":
    main()
