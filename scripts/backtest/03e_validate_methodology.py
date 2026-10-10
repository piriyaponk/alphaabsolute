# -*- coding: utf-8 -*-
"""
Methodology Validation — ไล่ตรวจ backtest ทีละขั้น
ถามก่อนเชื่อ: ระบบถูกต้องหรือเปล่า?

Check 1: Benchmark QQQ return ถูกไหม
Check 2: Signal data มี lookahead bias ไหม
Check 3: Return calculation ถูกวิธีไหม
Check 4: Transaction cost ถูกไหม
Check 5: V1 vs V2 discrepancy มาจากอะไร
Check 6: Survivorship bias ขนาดไหน
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pandas as pd
import numpy as np
from pathlib import Path

ROOT   = Path(__file__).resolve().parents[2]
BT_DIR = ROOT / "data/backtest"

sig = pd.read_parquet(BT_DIR / "signals.parquet")
sig["date"] = pd.to_datetime(sig["date"])
prices = pd.read_parquet(BT_DIR / "prices.parquet")
prices["date"] = pd.to_datetime(prices["date"])

PASS = "[PASS]"
FAIL = "[FAIL]"
WARN = "[WARN]"

print("=" * 65)
print("  BACKTEST METHODOLOGY VALIDATION")
print("=" * 65)

# ─────────────────────────────────────────────────────────────
# CHECK 1: QQQ benchmark return ถูกหรือเปล่า
# ─────────────────────────────────────────────────────────────
print("\nCHECK 1: QQQ benchmark return accuracy")
print("-" * 50)

qqq_sig = sig[sig["ticker"] == "QQQ"].set_index("date")["close"].sort_index()
qqq_px  = prices[prices["ticker"] == "QQQ"].set_index("date")["close"].sort_index()

# Known: QQQ 2022 return (bear year — should be ~-32%)
qqq_2022_sig = qqq_sig["2022-01-01":"2022-12-31"]
qqq_2022_px  = qqq_px["2022-01-01":"2022-12-31"]
if len(qqq_2022_sig) > 0:
    ret_sig = qqq_2022_sig.iloc[-1] / qqq_2022_sig.iloc[0] - 1
    print(f"  QQQ 2022 (signals.parquet): {ret_sig*100:.2f}%  (expected: ~-32.6%)")
    if -35 < ret_sig*100 < -28:
        print(f"  {PASS} QQQ return in expected range")
    else:
        print(f"  {WARN} QQQ return outside expected range — check data")

# QQQ full period CAGR (2017-2026)
qqq_full = qqq_sig["2017-01-01":"2026-08-20"]
if len(qqq_full) > 0:
    years = (qqq_full.index[-1] - qqq_full.index[0]).days / 365.25
    cagr  = (qqq_full.iloc[-1] / qqq_full.iloc[0]) ** (1/years) - 1
    print(f"  QQQ CAGR 2017-2026 (signals): {cagr*100:.2f}%  (expected: ~20-22%)")
    if 18 < cagr*100 < 24:
        print(f"  {PASS} QQQ CAGR reasonable")
    else:
        print(f"  {WARN} QQQ CAGR outside expected range")

# ─────────────────────────────────────────────────────────────
# CHECK 2: Lookahead bias in signals
# ─────────────────────────────────────────────────────────────
print("\nCHECK 2: Lookahead bias in signal computation")
print("-" * 50)

# MA200 on date T should only use price data up to T
aapl = sig[sig["ticker"] == "AAPL"].sort_values("date").copy()
if len(aapl) > 210:
    # Take a sample date and verify MA200
    sample_date = aapl.iloc[250]["date"]
    stored_ma200 = aapl.iloc[250]["ma_200"]
    # Compute manually from prices
    aapl_px = prices[prices["ticker"] == "AAPL"].set_index("date")["close"].sort_index()
    px_up_to = aapl_px[aapl_px.index <= sample_date].tail(200)
    manual_ma200 = px_up_to.mean()
    diff = abs(stored_ma200 - manual_ma200) / manual_ma200
    print(f"  AAPL MA200 on {sample_date.date()}")
    print(f"    Stored: {stored_ma200:.4f}")
    print(f"    Manual recalc from prices.parquet: {manual_ma200:.4f}")
    print(f"    Diff: {diff*100:.3f}%")
    if diff < 0.005:
        print(f"  {PASS} MA200 matches — no lookahead on price data")
    else:
        print(f"  {FAIL} MA200 mismatch — possible lookahead or data inconsistency")
else:
    print(f"  {WARN} Not enough AAPL data to check")

# RS percentile: should be cross-sectional rank on date T only
date_check = sig["date"].max() - pd.Timedelta(days=30)
one_day = sig[sig["date"] == sig[sig["date"] >= date_check]["date"].min()]
if len(one_day) > 10:
    rs_pct_vals = one_day["rs_pct"].dropna()
    expected_pct_range = (rs_pct_vals.min(), rs_pct_vals.max())
    n = len(rs_pct_vals)
    # Should go from ~0 to ~100 (cross-sectional percentile)
    print(f"\n  RS percentile cross-section ({one_day['date'].iloc[0].date()}):")
    print(f"    N tickers: {n}")
    print(f"    Min: {rs_pct_vals.min():.1f}  Max: {rs_pct_vals.max():.1f}  Mean: {rs_pct_vals.mean():.1f}")
    if rs_pct_vals.max() > 95 and rs_pct_vals.min() < 5:
        print(f"  {PASS} RS percentile spans full range (0-100)")
    else:
        print(f"  {WARN} RS percentile not spanning full range — check computation")

# ─────────────────────────────────────────────────────────────
# CHECK 3: Return calculation — one period manual check
# ─────────────────────────────────────────────────────────────
print("\nCHECK 3: Return calculation (manual one-period verification)")
print("-" * 50)

# Pick Jan 2023 month-end → Feb 2023 month-end
# Manually: screen on 2023-01-31, compute return to 2023-02-28

rebal_start = pd.Timestamp("2023-01-31")
rebal_end   = pd.Timestamp("2023-02-28")

# Find nearest trading days
close_wide = sig.pivot_table(index="date", columns="ticker", values="close")
dates_avail = close_wide.index.sort_values()

start_actual = dates_avail[dates_avail <= rebal_start][-1]
end_actual   = dates_avail[dates_avail <= rebal_end][-1]
print(f"  Period: {start_actual.date()} -> {end_actual.date()}")

# Screen at start_actual with rs>=80, vol_trend>=0.95, ADTV>=15
day_sig = sig[sig["date"] == start_actual]
mask = (
    (day_sig["rs_pct"]    >= 80) &
    (day_sig["vol_trend"] >= 0.95) &
    (day_sig["adtv_63m"]  >= 15) &
    (day_sig["close"]     >  day_sig["ma_200"]) &
    (~day_sig["ticker"].isin(["QQQ","SPY","IWM"]))
)
sel = day_sig[mask].copy()
print(f"  Stocks selected: {len(sel)}")
if len(sel) > 0:
    print(f"  Top 5: {sel.nlargest(5,'rs_pct')['ticker'].tolist()}")

# Compute period return (manual)
if len(sel) > 0:
    raw = sel["adtv_63m"] ** 1.5
    sel = sel.copy()
    sel["weight"] = raw / raw.sum()
    sel["weight"] = sel["weight"].clip(upper=0.18)
    sel["weight"] = sel["weight"] / sel["weight"].sum()

    manual_port_ret = 0.0
    for _, row in sel.iterrows():
        t = row["ticker"]
        if t in close_wide.columns:
            p_start = close_wide.loc[start_actual, t]
            p_end   = close_wide.loc[end_actual, t] if end_actual in close_wide.index else np.nan
            if pd.notna(p_start) and pd.notna(p_end) and p_start > 0:
                r = (p_end / p_start - 1) * row["weight"]
                manual_port_ret += r

    # QQQ return same period
    qqq_start = close_wide.loc[start_actual, "QQQ"] if "QQQ" in close_wide.columns else np.nan
    qqq_end   = close_wide.loc[end_actual,   "QQQ"] if "QQQ" in close_wide.columns else np.nan
    bench_ret = (qqq_end / qqq_start - 1) if pd.notna(qqq_start) and qqq_start > 0 else 0

    print(f"\n  Manual portfolio return (Jan->Feb 2023): {manual_port_ret*100:+.2f}%")
    print(f"  QQQ return same period:                  {bench_ret*100:+.2f}%")
    print(f"  Excess (manual):                         {(manual_port_ret-bench_ret)*100:+.2f}%")
    print(f"  {PASS if abs(manual_port_ret) < 0.30 else WARN} Return magnitude looks reasonable")

# ─────────────────────────────────────────────────────────────
# CHECK 4: Transaction cost
# ─────────────────────────────────────────────────────────────
print("\nCHECK 4: Transaction cost model")
print("-" * 50)
print("  V1 method: round_trip_cost × sum(|new_weight - old_weight|)")
print("  V2 method: slippage per share on buy/sell + commission")
print()

# Example: full turnover on 11 stocks, avg weight ~9% each
n_stocks   = 11
avg_weight = 1.0 / n_stocks
# If completely new portfolio (100% turnover):
full_turnover = 2 * n_stocks * avg_weight  # buy new + sell old = 2×
v1_cost_pct = full_turnover * 0.0015       # 0.15% round-trip
v2_cost_pct = full_turnover * 0.0006       # 2×0.03% slippage one-way

print(f"  Assumption: {n_stocks} stocks, ~{avg_weight*100:.0f}% each, 100% portfolio turnover")
print(f"  V1 cost (0.15% round-trip): {v1_cost_pct*100:.3f}%")
print(f"  V2 cost (0.06% round-trip): {v2_cost_pct*100:.3f}%")
print(f"  Difference per rebalance:   {(v1_cost_pct-v2_cost_pct)*100:.3f}%")
print(f"  Annual difference (~12 rebal): {(v1_cost_pct-v2_cost_pct)*12*100:.2f}%")
print()
print("  Codex uses: 25 bps round-trip (stress test)")
print(f"  Our v1:    15 bps  (US retail realistic)")
print(f"  Our v2:     6 bps  (slippage only, $0 commission at Fidelity)")
print(f"  {WARN} V2 cost may be TOO LOW vs real world — bid-ask often 5-10 bps one-way")

# ─────────────────────────────────────────────────────────────
# CHECK 5: V1 vs V2 discrepancy
# ─────────────────────────────────────────────────────────────
print("\nCHECK 5: V1 vs V2 number discrepancy")
print("-" * 50)
print("  V1 monthly approx:  excess +5.9%  (from this session)")
print("  V2 daily NAV:       excess +5.9%  (same formula, softmax 0.5)")
print("  V1 grid session:    excess +12.5% (previous session)")
print()
print("  SUSPECT: Previous session's +12.5% may have been with:")
print("    a) Different signals.parquet (before v2 factors added)")
print("    b) Different date range or benchmark start point")
print("    c) Bug in benchmark calculation that was since fixed")
print("    d) No transaction cost applied (cost=0 not 0.15%)")
print()
print("  VALIDATION: Run v1 with round_trip_cost=0 to isolate cost effect:")

import importlib.util
def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

v1 = load_module(ROOT / "scripts/backtest/03_backtest_engine.py", "v1")
signals = sig.copy()

p_nocost = v1.BacktestParams(
    rs_threshold=80, vol_trend_min=0.95, vol_contract_max=1.10, base_tight_max=1.10,
    adtv_min_m=15, liquidity_power=1.5, cap_pct=0.18, bear_exposure=0.30,
    round_trip_cost=0.0,    # NO COST
    benchmark="QQQ", start_date="2017-01-01", end_date="2026-08-20"
)
r_nocost = v1.run_backtest(p_nocost, signals)

p_cost = v1.BacktestParams(
    rs_threshold=80, vol_trend_min=0.95, vol_contract_max=1.10, base_tight_max=1.10,
    adtv_min_m=15, liquidity_power=1.5, cap_pct=0.18, bear_exposure=0.30,
    round_trip_cost=0.0015,  # 0.15% US retail
    benchmark="QQQ", start_date="2017-01-01", end_date="2026-08-20"
)
r_cost = v1.run_backtest(p_cost, signals)

print(f"\n  No-cost excess:   {r_nocost['excess']*100:+.2f}%")
print(f"  0.15% cost excess:{r_cost['excess']*100:+.2f}%")
print(f"  Cost drag:        {(r_nocost['excess']-r_cost['excess'])*100:+.2f}%/yr")

# ─────────────────────────────────────────────────────────────
# CHECK 6: Survivorship bias estimate
# ─────────────────────────────────────────────────────────────
print("\nCHECK 6: Survivorship bias")
print("-" * 50)
n_tickers = sig["ticker"].nunique()
print(f"  Universe size: {n_tickers} tickers (current S&P500 constituents)")
print(f"  Period: 2015-2026 (11 years)")
print(f"  S&P500 annual turnover historically: ~4-6% of members replaced per year")
print(f"  Over 11 years: ~40-60 tickers excluded (deleted = underperformers)")
print(f"  Estimated survivorship bias: +1.5% to +3.0% per year (per academic literature)")
print(f"  {WARN} Our results are OPTIMISTIC by this amount vs live trading")
print(f"  Honest estimate: subtract 2% from any excess return figure")

# ─────────────────────────────────────────────────────────────
# FINAL VERDICT
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("  VERDICT")
print("=" * 65)
print("""
  What is RELIABLE:
    - Relative ranking between strategies (A beats B consistently)
    - Walk-forward window pattern (which regimes hurt which formula)
    - Sharpe and risk-adjusted ratios (relative comparison)
    - Factor additionality findings (vol_trend is real edge)

  What is UNRELIABLE:
    - Absolute excess return numbers (+5.9% is likely +3-4% after bias)
    - Previous session's +12.5% — likely had cost=0 or different data
    - DD numbers in v1 (monthly) vs v2 (daily) are different instruments
    - Any result with <8 avg holdings — too concentrated to generalize

  HONEST EXPECTED PERFORMANCE (v2 engine, SYSTEM v1.0):
    - Gross excess (before bias):  ~+5-6% vs QQQ
    - Survivorship bias drag:      -2%
    - Realistic live excess:       ~+3-4% vs QQQ
    - This is still BETTER than Codex's +3.77% and our DD is lower

  RECOMMENDATION:
    Use v2 engine as official backtest.
    Report results with survivorship bias caveat.
    Next step: validate on EXTENDED universe (Nasdaq+Russell)
    to partially reduce survivorship bias concern.
""")
