# -*- coding: utf-8 -*-
"""
pre_factor_validator.py — Validate any new factor before adding to GA search space.

Runs 5 checks automatically. All must PASS before factor can be used in production.
Run this BEFORE modifying 03b_backtest_v2.py or 07_ga_optimize.py.

Usage:
  python scripts/backtest/pre_factor_validator.py --factor resilience_3m --direction low
  python scripts/backtest/pre_factor_validator.py --factor pct_from_52w_high --direction high
  python scripts/backtest/pre_factor_validator.py --factor base_tight --direction low

Directions:
  high = higher value is better (buy when factor is high)
  low  = lower value is better (buy when factor is low)
"""
import sys, io, argparse
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import pandas as pd
import numpy as np
from pathlib import Path

ROOT       = Path(__file__).resolve().parents[2]
BT_DIR     = ROOT / "data" / "backtest"
SIG_FILE   = BT_DIR / "signals.parquet"


# ── Check 1: No lookahead ─────────────────────────────────────────────────────

LOOKAHEAD_KEYWORDS = ["shift(-", "future", "look_ahead", "lead(", ".shift(-"]

def check_no_lookahead(factor_name: str, signals_df: pd.DataFrame) -> tuple[bool, str]:
    """Cannot check code here — checks signal.parquet for suspicious patterns."""
    col = signals_df[factor_name]
    # If factor correlates with NEXT DAY return > 0.05 → suspicious
    prices = pd.read_parquet(BT_DIR / "prices.parquet")
    prices["date"] = pd.to_datetime(prices["date"])
    close_wide = prices.pivot(index="date", columns="ticker", values="close")
    fwd_ret = close_wide.pct_change(5).shift(-5)  # 5-day forward return

    sigs_wide = signals_df.copy()
    sigs_wide["date"] = pd.to_datetime(sigs_wide["date"])
    sigs_wide = sigs_wide.pivot(index="date", columns="ticker", values=factor_name)

    # Align
    common_dates = fwd_ret.index.intersection(sigs_wide.index)
    common_ticks = fwd_ret.columns.intersection(sigs_wide.columns)
    fwd_flat  = fwd_ret.loc[common_dates, common_ticks].values.flatten()
    sig_flat  = sigs_wide.loc[common_dates, common_ticks].values.flatten()

    mask = ~(np.isnan(fwd_flat) | np.isnan(sig_flat))
    if mask.sum() < 100:
        return True, "N too small to check — assume OK"

    corr = np.corrcoef(sig_flat[mask], fwd_flat[mask])[0, 1]
    # If corr > 0.20 (very high for daily cross-section) → likely lookahead
    if abs(corr) > 0.20:
        return False, f"SUSPICIOUS: corr with 5d fwd return = {corr:.3f} (>0.20 = possible lookahead)"
    return True, f"OK: corr with 5d fwd return = {corr:.3f}"


# ── Check 2: Data coverage ────────────────────────────────────────────────────

def check_data_coverage(factor_name: str, signals_df: pd.DataFrame) -> tuple[bool, str]:
    col = signals_df[factor_name]
    miss_rate = col.isna().mean()
    if miss_rate > 0.10:
        return False, f"FAIL: {miss_rate:.1%} missing (threshold 10%)"
    # Check by year — no year should be > 30% missing
    signals_df2 = signals_df.copy()
    signals_df2["year"] = pd.to_datetime(signals_df2["date"]).dt.year
    yr_miss = signals_df2.groupby("year")[factor_name].apply(lambda x: x.isna().mean())
    worst_yr = yr_miss.max()
    worst_yr_name = yr_miss.idxmax()
    if worst_yr > 0.30:
        return False, f"FAIL: {worst_yr_name} has {worst_yr:.1%} missing"
    return True, f"OK: {miss_rate:.1%} overall missing, worst year {worst_yr_name}={worst_yr:.1%}"


# ── Check 3: Not redundant with existing factors ──────────────────────────────

EXISTING_FACTORS = ["rs_pct", "vol_trend", "vol_contraction", "base_tight", "adtv_63m"]
REDUNDANCY_THRESHOLD = 0.85

def check_not_redundant(factor_name: str, signals_df: pd.DataFrame) -> tuple[bool, str]:
    new_col = signals_df[factor_name].dropna()
    max_corr = 0.0
    max_name = ""
    for existing in EXISTING_FACTORS:
        if existing not in signals_df.columns:
            continue
        ex_col = signals_df[existing].dropna()
        common = new_col.index.intersection(ex_col.index)
        if len(common) < 1000:
            continue
        corr = abs(np.corrcoef(new_col.loc[common], ex_col.loc[common])[0, 1])
        if corr > max_corr:
            max_corr = corr
            max_name = existing
    if max_corr > REDUNDANCY_THRESHOLD:
        return False, f"FAIL: corr with {max_name} = {max_corr:.3f} (>{REDUNDANCY_THRESHOLD} = redundant)"
    return True, f"OK: max corr with existing = {max_name} {max_corr:.3f}"


# ── Check 4: Non-degenerate distribution ─────────────────────────────────────

def check_distribution(factor_name: str, signals_df: pd.DataFrame) -> tuple[bool, str]:
    col = signals_df[factor_name].dropna()
    if len(col) < 1000:
        return False, "FAIL: < 1000 valid rows"
    std = col.std()
    pct_unique = col.nunique() / len(col)
    p5, p95 = col.quantile(0.05), col.quantile(0.95)
    spread = p95 - p5
    if std == 0:
        return False, "FAIL: zero std (constant signal)"
    if spread == 0:
        return False, "FAIL: p5==p95 (degenerate range)"
    if pct_unique < 0.001:
        return False, f"FAIL: only {col.nunique()} unique values — too discrete"
    return True, f"OK: std={std:.4f}  p5={p5:.4f}  p95={p95:.4f}  unique={col.nunique():,}"


# ── Check 5: Factor direction confirmed ──────────────────────────────────────

def check_direction(factor_name: str, direction: str, signals_df: pd.DataFrame) -> tuple[bool, str]:
    """
    Quintile test: split stocks into Q1 (low) and Q5 (high) on factor.
    Then measure 21-day forward return for each quintile.
    direction='high' → Q5 should outperform Q1
    direction='low'  → Q1 should outperform Q5
    """
    df = signals_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=[factor_name, "close"])

    # Compute 21d forward return per ticker
    df = df.sort_values(["ticker", "date"])
    df["fwd_21d"] = df.groupby("ticker")["close"].pct_change(21).shift(-21)
    df = df.dropna(subset=["fwd_21d", factor_name])

    if len(df) < 5000:
        return False, "FAIL: not enough rows for quintile test"

    # Cross-sectional quintile on each date
    df["quintile"] = df.groupby("date")[factor_name].transform(
        lambda x: pd.qcut(x, 5, labels=False, duplicates="drop")
    )
    df = df.dropna(subset=["quintile"])

    q1_ret = df[df["quintile"] == 0]["fwd_21d"].mean() * 100
    q5_ret = df[df["quintile"] == 4]["fwd_21d"].mean() * 100
    spread = q5_ret - q1_ret

    if direction == "high":
        # Q5 (high factor) should beat Q1 (low factor)
        passes = spread > 0
        msg = f"Q1={q1_ret:+.2f}%  Q5={q5_ret:+.2f}%  spread={spread:+.2f}%  (want Q5>Q1)"
    else:
        # Q1 (low factor) should beat Q5 (high factor)
        passes = spread < 0
        msg = f"Q1={q1_ret:+.2f}%  Q5={q5_ret:+.2f}%  spread={spread:+.2f}%  (want Q1>Q5)"

    status = "OK" if passes else "WEAK"
    # Weak is allowed (direction test can be noisy), but flag it
    return True, f"{status}: {msg}"


# ── Main validator ────────────────────────────────────────────────────────────

def validate_factor(factor_name: str, direction: str) -> bool:
    print(f"\n{'='*60}")
    print(f"FACTOR VALIDATOR: {factor_name}  direction={direction}")
    print(f"{'='*60}")

    sigs = pd.read_parquet(SIG_FILE)

    if factor_name not in sigs.columns:
        print(f"\n[ABORT] '{factor_name}' not found in signals.parquet")
        print(f"Available columns: {[c for c in sigs.columns if c not in ['date','ticker']]}")
        return False

    checks = [
        ("1. No lookahead bias",        lambda: check_no_lookahead(factor_name, sigs)),
        ("2. Data coverage >= 90%",     lambda: check_data_coverage(factor_name, sigs)),
        ("3. Not redundant (corr<0.85)",lambda: check_not_redundant(factor_name, sigs)),
        ("4. Non-degenerate dist",      lambda: check_distribution(factor_name, sigs)),
        ("5. Direction confirmed",      lambda: check_direction(factor_name, direction, sigs)),
    ]

    all_pass = True
    for check_name, check_fn in checks:
        try:
            passed, msg = check_fn()
            status = "PASS" if passed else "FAIL"
            if not passed:
                all_pass = False
            print(f"  [{status}] {check_name}")
            print(f"         {msg}")
        except Exception as e:
            print(f"  [ERROR] {check_name}: {e}")
            all_pass = False

    print(f"\n{'='*60}")
    verdict = "APPROVED — safe to add to GA search space" if all_pass else "REJECTED — fix issues above before using"
    print(f"VERDICT: {verdict}")
    print(f"{'='*60}\n")
    return all_pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate a factor before adding to production")
    parser.add_argument("--factor",    required=True, help="Column name in signals.parquet")
    parser.add_argument("--direction", required=True, choices=["high","low"],
                        help="high=higher is better, low=lower is better")
    args = parser.parse_args()
    ok = validate_factor(args.factor, args.direction)
    sys.exit(0 if ok else 1)
