"""
laggard_backtest.py — PULSE-TH_LAGGARD gate ablation backtest
==============================================================
Read-only. Does NOT connect to any live screener or pipeline module.
Run: python -I scripts/research/thai_pulse/laggard_backtest.py

Architecture: fully vectorized — NO per-ticker-per-day Python loop.
All gate values are pre-computed as DataFrame columns before any
filtering. Gate ablation = boolean mask on pre-computed columns.

Gate combinations (A–F, no macro gate):
  A: rs_pct_6m < 40
  B: A + rs_pct_3m < 40
  C: B + inflection (rs_pct_1m > rs_pct_3m)
  D: C + close > ma50
  E: D + consolidation range < 8%
  F: E + vol_today / vol20_active < 0.50  (full 5-gate)

Promotion threshold: h20 >= 58% on N >= 50 events.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")  # Windows cp874 crash fix

import argparse
import json
import sqlite3
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

ROOT    = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "data" / "research" / "thai_ohlcv.db"
OUT_DIR = ROOT / "data" / "research" / "thai_pulse"


# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="PULSE-TH_LAGGARD gate ablation backtest")
    p.add_argument("--start",    default="2015-01-01", help="Backtest start date (YYYY-MM-DD)")
    p.add_argument("--end",      default="2026-10-09", help="Backtest end date (YYYY-MM-DD)")
    p.add_argument("--dry-run",  action="store_true",
                   help="Run 2015-2016 only, print summary, do not write output files")
    return p.parse_args()


# ── LOAD ─────────────────────────────────────────────────────────────────────

def load_data() -> tuple[pd.DataFrame, pd.Series]:
    """Load full thai_ohlcv table into memory. One query, no per-day calls."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        "SELECT ticker, date, open, high, low, close, volume "
        "FROM thai_ohlcv ORDER BY ticker, date",
        conn,
    )
    conn.close()

    df["date"] = pd.to_datetime(df["date"])

    # ^SET.BK used as benchmark only — exclude from equity universe
    set_df = (
        df[df["ticker"] == "^SET.BK"]
        .set_index("date")["close"]
        .sort_index()
    )
    eq_df = df[df["ticker"] != "^SET.BK"].copy()
    return eq_df, set_df


# ── PRE-COMPUTE ALL GATE COLUMNS ─────────────────────────────────────────────

def precompute_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    All gate values computed once as columns. No per-ticker-per-day loops.
    Missing data → NaN (output maps to -1, not 0).
    """
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    g = df.groupby("ticker", sort=False)

    # Suspension gate — prerequisite for all others
    df["is_active"] = (df["volume"] > 0)

    # Rolling returns for RS (only on active days — zero-volume → NaN before rolling)
    active_close = df["close"].where(df["volume"] > 0)
    for window, col in [(126, "ret_6m"), (63, "ret_3m"), (21, "ret_1m")]:
        df[col] = g["close"].transform(
            lambda x: x / x.shift(window) - 1
        )
        # Exclude tickers with insufficient bar history
        bar_count = g["close"].transform("cumcount")
        df.loc[bar_count < window, col] = np.nan

    # RS percentile (cross-sectional rank per date, NaN excluded from denominator)
    for ret_col, pct_col in [
        ("ret_6m", "rs_pct_6m"),
        ("ret_3m", "rs_pct_3m"),
        ("ret_1m", "rs_pct_1m"),
    ]:
        df[pct_col] = (
            df.groupby("date")[ret_col]
            .rank(pct=True, na_option="keep")
            * 100
        )

    # MA50 (all days; active-day approximation via calendar days is standard)
    df["ma50"] = g["close"].transform(
        lambda x: x.rolling(50, min_periods=50).mean()
    )

    # Consolidation: 10-day H-L range / min_low — guard low > 0
    df["range_10d_low"] = g["low"].transform(
        lambda x: x.where(x > 0).rolling(10, min_periods=10).min()
    )
    df["range_10d_high"] = g["high"].transform(
        lambda x: x.where(x > 0).rolling(10, min_periods=10).max()
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        df["consolidation_range"] = np.where(
            df["range_10d_low"] > 0,
            (df["range_10d_high"] - df["range_10d_low"]) / df["range_10d_low"],
            np.nan,
        )

    # vol20: rolling mean over last 20 ACTIVE (volume > 0) trading days
    df["vol20"] = g["volume"].transform(
        lambda x: x.where(x > 0).rolling(20, min_periods=20).mean()
    )

    # Stochastic %K (14-period): (close - low14) / (high14 - low14) * 100
    low14  = g["low"].transform(lambda x: x.where(x > 0).rolling(14, min_periods=14).min())
    high14 = g["high"].transform(lambda x: x.where(x > 0).rolling(14, min_periods=14).max())
    stoch_range = high14 - low14
    with np.errstate(divide="ignore", invalid="ignore"):
        df["stoch_k"] = np.where(stoch_range > 0, (df["close"] - low14) / stoch_range * 100, np.nan)

    # Bollinger Band lower (20-period, 2 std)
    bb_ma20  = g["close"].transform(lambda x: x.rolling(20, min_periods=20).mean())
    bb_std20 = g["close"].transform(lambda x: x.rolling(20, min_periods=20).std())
    bb_lower = bb_ma20 - 2 * bb_std20
    with np.errstate(divide="ignore", invalid="ignore"):
        df["pct_to_lower_bb"] = np.where(
            bb_lower > 0, (df["close"] - bb_lower) / bb_lower * 100, np.nan
        )

    # Forward returns: entry = T+1 open (no lookahead)
    df["entry_open_t1"] = g["open"].transform(lambda x: x.shift(-1))
    for offset in [5, 10, 20, 40]:
        future_close = g["close"].transform(lambda x: x.shift(-offset))
        df[f"fwd_{offset}d"] = (
            (future_close - df["entry_open_t1"]) / df["entry_open_t1"]
        )

    return df


# ── SET BENCHMARK FORWARD RETURN ─────────────────────────────────────────────

def attach_set_benchmark(set_series: pd.Series, df: pd.DataFrame) -> pd.DataFrame:
    """
    Forward-fill SET close for holidays, then join 20d forward return.
    Excess = stock_fwd_20d - set_fwd_20d (same calendar window).
    """
    all_dates = df["date"].drop_duplicates().sort_values()
    set_ffill  = set_series.reindex(all_dates).sort_index().ffill()
    set_fwd20  = (set_ffill.shift(-20) / set_ffill - 1).rename("set_fwd_20d")

    df = df.merge(
        set_fwd20.reset_index().rename(columns={"index": "date", "date": "date"}),
        on="date",
        how="left",
    )
    df["excess_20d"] = df["fwd_20d"] - df["set_fwd_20d"]
    return df


# ── GATE COLUMNS ─────────────────────────────────────────────────────────────

def apply_gates(df: pd.DataFrame) -> pd.DataFrame:
    """Convert pre-computed features to binary gate columns (1=pass, 0=fail, NaN=no-data)."""
    mask = df["is_active"]

    def gate(condition, valid_mask):
        return np.where(
            mask & valid_mask,
            condition.astype(float),
            np.nan,
        )

    df["gate_rs_6m"] = gate(
        df["rs_pct_6m"] < 40,
        df["rs_pct_6m"].notna(),
    )
    df["gate_rs_3m"] = gate(
        df["rs_pct_3m"] < 40,
        df["rs_pct_3m"].notna(),
    )
    # Inflection: 1M RS percentile > 3M RS percentile (direction signal)
    df["gate_inflection"] = gate(
        df["rs_pct_1m"] > df["rs_pct_3m"],
        df["rs_pct_1m"].notna() & df["rs_pct_3m"].notna(),
    )
    # --- Test G (RS dip in strong stock): 6M good, 3M dip, 1M recovering ---
    # 6M strong leader (>= 60th pct), 3M dipped below 6M, 1M > 3M (recovering)
    df["gate_h_stoch_os"] = gate(
        df["stoch_k"] < 20,
        df["stoch_k"].notna(),
    )
    # "near or below lower BB" = within 3% above lower band, or below
    df["gate_h_bb_near"] = gate(
        df["pct_to_lower_bb"] <= 3.0,
        df["pct_to_lower_bb"].notna(),
    )

    df["gate_g_6m_strong"] = gate(
        df["rs_pct_6m"] >= 60,
        df["rs_pct_6m"].notna(),
    )
    df["gate_g_3m_dip"] = gate(
        df["rs_pct_3m"] < df["rs_pct_6m"],
        df["rs_pct_3m"].notna() & df["rs_pct_6m"].notna(),
    )
    df["gate_g_1m_recover"] = gate(
        df["rs_pct_1m"] > df["rs_pct_3m"],
        df["rs_pct_1m"].notna() & df["rs_pct_3m"].notna(),
    )
    df["gate_ma50"] = gate(
        df["close"] > df["ma50"],
        df["ma50"].notna(),
    )
    df["gate_consolidation"] = gate(
        df["consolidation_range"] < 0.08,
        df["consolidation_range"].notna(),
    )
    df["gate_vol_dryup"] = gate(
        df["volume"] < df["vol20"] * 0.5,
        df["vol20"].notna() & (df["volume"] > 0),
    )
    return df


# ── GATE COMBINATIONS ────────────────────────────────────────────────────────

GATE_COMBOS = {
    "A": ["gate_rs_6m"],
    "B": ["gate_rs_6m", "gate_rs_3m"],
    "C": ["gate_rs_6m", "gate_rs_3m", "gate_inflection"],
    "D": ["gate_rs_6m", "gate_rs_3m", "gate_inflection", "gate_ma50"],
    "E": ["gate_rs_6m", "gate_rs_3m", "gate_inflection", "gate_ma50",
          "gate_consolidation"],
    "F": ["gate_rs_6m", "gate_rs_3m", "gate_inflection", "gate_ma50",
          "gate_consolidation", "gate_vol_dryup"],
    # Test G: RS dip in strong stock (6M good, 3M dipped, 1M recovering)
    "G_base":     ["gate_g_6m_strong", "gate_g_3m_dip", "gate_g_1m_recover"],
    "G_ma50":     ["gate_g_6m_strong", "gate_g_3m_dip", "gate_g_1m_recover", "gate_ma50"],
    "G_full":     ["gate_g_6m_strong", "gate_g_3m_dip", "gate_g_1m_recover", "gate_ma50",
                   "gate_consolidation", "gate_vol_dryup"],
    # Test H: RS6M leader + Stochastic oversold + near/below lower Bollinger Band
    "H_base":     ["gate_g_6m_strong", "gate_h_stoch_os", "gate_h_bb_near"],
    "H_rs3m":     ["gate_g_6m_strong", "gate_g_3m_dip", "gate_h_stoch_os", "gate_h_bb_near"],
    "H_ma50":     ["gate_g_6m_strong", "gate_h_stoch_os", "gate_h_bb_near", "gate_ma50"],
    "H_full":     ["gate_g_6m_strong", "gate_g_3m_dip", "gate_h_stoch_os", "gate_h_bb_near",
                   "gate_ma50"],
}

GATE_COLS = [
    "gate_rs_6m", "gate_rs_3m", "gate_inflection",
    "gate_ma50", "gate_consolidation", "gate_vol_dryup",
    "gate_g_6m_strong", "gate_g_3m_dip", "gate_g_1m_recover",
    "gate_h_stoch_os", "gate_h_bb_near",
]


# ── DEDUPLICATION ─────────────────────────────────────────────────────────────

def deduplicate(signals: pd.DataFrame, min_gap_days: int = 20) -> pd.DataFrame:
    """Keep first occurrence per ticker; skip same ticker within min_gap_days calendar days."""
    signals = signals.sort_values("date").copy()
    last_date: dict = {}
    keep = []
    for idx, row in signals.iterrows():
        t = row["ticker"]
        d = row["date"]
        if t not in last_date or (d - last_date[t]).days >= min_gap_days:
            keep.append(idx)
            last_date[t] = d
    return signals.loc[keep]


# ── AGGREGATE RESULTS ─────────────────────────────────────────────────────────

def aggregate(signals: pd.DataFrame, test_id: str, gates: list) -> dict:
    valid = signals.dropna(subset=["fwd_20d", "set_fwd_20d"])
    n = len(valid)
    if n == 0:
        return {"test_id": test_id, "gates": gates, "n_events": 0}
    return {
        "test_id":               test_id,
        "gates":                 gates,
        "n_events":              n,
        "h5":                    round(float((valid["fwd_5d"]  > 0).mean()), 3),
        "h10":                   round(float((valid["fwd_10d"] > 0).mean()), 3),
        "h20":                   round(float((valid["fwd_20d"] > 0).mean()), 3),
        "h40":                   round(float((valid["fwd_40d"] > 0).mean()), 3),
        "avg_fwd_20d":           round(float(valid["fwd_20d"].mean()), 4),
        "median_fwd_20d":        round(float(valid["fwd_20d"].median()), 4),
        "avg_excess_20d":        round(float(valid["excess_20d"].mean()), 4),
        "pct_beat_set_20d":      round(float((valid["excess_20d"] > 0).mean()), 3),
        "production_ready":      bool(n >= 50 and (valid["fwd_20d"] > 0).mean() >= 0.58),
    }


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    start, end = args.start, args.end
    if args.dry_run:
        start, end = "2015-01-01", "2016-12-31"
        print("[DRY RUN] Limiting to 2015-2016")

    t0 = datetime.now()

    print("[1/5] Loading thai_ohlcv.db ...")
    eq_df, set_series = load_data()
    print(f"      {len(eq_df):,} rows | {eq_df['ticker'].nunique()} tickers")

    print("[2/5] Pre-computing gate features (vectorized) ...")
    eq_df = precompute_features(eq_df)
    eq_df = attach_set_benchmark(set_series, eq_df)
    print(f"      Features ready in {(datetime.now()-t0).seconds}s")

    print("[3/5] Applying gate columns ...")
    eq_df = apply_gates(eq_df)

    # Filter to backtest date range
    date_str = eq_df["date"].dt.strftime("%Y-%m-%d")
    bt = eq_df[
        (date_str >= start) &
        (date_str <= end) &
        eq_df["entry_open_t1"].notna()
    ].copy()
    print(f"      Backtest window: {start} to {end} | {len(bt):,} rows")

    print("[4/5] Running gate ablation (Tests A–F) ...")
    summary = []
    prev_h20 = None
    for test_id, gates in GATE_COMBOS.items():
        combo_mask = pd.Series(True, index=bt.index)
        for gc in gates:
            combo_mask &= (bt[gc] == 1.0)
        signals = deduplicate(bt[combo_mask])
        result  = aggregate(signals, test_id, gates)
        h20     = result.get("h20", "N/A")
        delta   = f"  Δ{(h20 - prev_h20)*100:+.1f}pp" if prev_h20 is not None and isinstance(h20, float) else ""
        print(f"  Test {test_id}: N={result.get('n_events',0):5d} | "
              f"h20={h20} | excess={result.get('avg_excess_20d','N/A')}{delta}")
        summary.append(result)
        if isinstance(h20, float):
            prev_h20 = h20

    print("[5/5] Writing outputs ...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not args.dry_run:
        out_json = OUT_DIR / "laggard_backtest_results.json"
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump({
                "run_date":    datetime.today().strftime("%Y-%m-%d"),
                "date_range":  {"start": start, "end": end},
                "notes": {
                    "survivorship_bias": (
                        "Universe = current 226 tickers in DB. "
                        "Delisted stocks absent — results overstate win rate."
                    ),
                    "corporate_actions": (
                        "Consolidation gate uses unadjusted high/low. "
                        "Post-split signals may be biased."
                    ),
                    "macro_gate": "Gate 0 (macro trigger) removed per CIO decision 2026-10-12.",
                },
                "promotion_threshold": "h20 >= 0.58 on N >= 50 events",
                "results": summary,
            }, f, indent=2, ensure_ascii=False)
        print(f"  -> {out_json}")

        # Events detail CSV — all rows where gate_rs_6m == 1 (full audit trail)
        events_cols = (
            ["ticker", "date"]
            + GATE_COLS
            + ["fwd_5d", "fwd_10d", "fwd_20d", "fwd_40d",
               "set_fwd_20d", "excess_20d", "entry_open_t1",
               "rs_pct_6m", "rs_pct_3m", "rs_pct_1m"]
        )
        events_df = bt[bt["gate_rs_6m"] == 1.0][events_cols].copy()
        for gc in GATE_COLS:
            events_df[gc] = events_df[gc].fillna(-1).astype(int)
        out_csv = OUT_DIR / "laggard_events_detail.csv"
        events_df.to_csv(out_csv, index=False, encoding="utf-8")
        print(f"  -> {out_csv}")

    elapsed = (datetime.now() - t0).seconds
    print(f"[DONE] {elapsed}s total")


if __name__ == "__main__":
    main()
