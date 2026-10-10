# -*- coding: utf-8 -*-
"""
REGIME DETECTION STUDY — ทดสอบทุก method อย่างจริงจัง
=======================================================
คำถาม: Market regime gate ควรใช้อะไร?

Research:
  - Codex:    Bull/Sideway/Bear 3-state (100/100/40%)
              SPY > MA200 = Bull, close to MA200 = Sideway, clearly below = Bear
  - Template: EGARCH individual stock stop-loss (ไม่ใช่ market regime)
              — ตัด exposure ทีละหุ้นเมื่อ vol spike, ไม่ตัด market-wide
  - Academic: Faber (2007) — 10-month MA timing ชนะ buy-hold risk-adjusted
              Clare et al. (2017) — trend filter ลด DD ไม่เพิ่ม excess มาก
              Antonacci (2013) — dual momentum ใช้ absolute momentum as regime gate

Tests (ทีละ method บน SYSTEM v1.0 criteria):
  R0: No regime gate (100% always)          ← baseline, upper bound
  R1: Simple MA200 (current)                ← our baseline
  R2: Buffer ±2%                            ← fix whipsaw
  R3: N-day confirm (5 consecutive days)    ← lag-based filter
  R4: Death cross MA50<MA200                ← slower signal
  R5: MA200 slope (rising/falling)          ← direction matters
  R6: Dual confirm (price>MA200 + MA50>MA200)
  R7: Codex 3-state (Bull/Sideway/Bear)     ← replicate Codex
  R8: Trend score composite                 ← combine R1+R4+R5
"""
import sys, io, importlib.util
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field

ROOT = Path(__file__).resolve().parents[2]

def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

v2 = load_module(ROOT / "scripts/backtest/03b_backtest_v2.py", "v2")

# ── Load SPY signals for regime computation ──────────────────
sig = pd.read_parquet(ROOT / "data/backtest/signals.parquet")
sig["date"] = pd.to_datetime(sig["date"])

spy = sig[sig["ticker"] == "SPY"].set_index("date").sort_index()
spy["ma_50"]  = spy["close"].rolling(50,  min_periods=40).mean()
spy["ma_200"] = spy["close"].rolling(200, min_periods=180).mean()

# Pre-compute regime signals for SPY
spy["above_ma200"]      = spy["close"] > spy["ma_200"]
spy["above_ma50"]       = spy["close"] > spy["ma_50"]
spy["death_cross"]      = spy["ma_50"] < spy["ma_200"]   # True = bearish

# MA200 slope: positive if MA200 is rising (20-day change in MA200)
spy["ma200_slope"]      = spy["ma_200"].diff(20)
spy["ma200_rising"]     = spy["ma200_slope"] > 0

# Buffer: distance from MA200 in %
spy["dist_from_ma200"]  = (spy["close"] - spy["ma_200"]) / spy["ma_200"]

# N-day consecutive below MA200
def n_day_below(series, n=5):
    """True if below MA200 for n consecutive days."""
    below = (~series).astype(int)
    return below.rolling(n).sum() >= n

spy["below_5days"]  = n_day_below(spy["above_ma200"], n=5)
spy["below_3days"]  = n_day_below(spy["above_ma200"], n=3)

print("SPY regime signals computed")
print(f"  Date range: {spy.index.min().date()} to {spy.index.max().date()}")
print(f"  % days above MA200: {spy['above_ma200'].mean()*100:.1f}%")
print(f"  % days in death cross: {spy['death_cross'].mean()*100:.1f}%")
print(f"  % days MA200 rising: {spy['ma200_rising'].mean()*100:.1f}%")

# ── Regime functions: return exposure (0.0-1.0) for a date ──

BEAR_EXP = 0.30   # our bear exposure (tested best)
SIDE_EXP = 0.75   # codex sideway (between bull+bear)

def get_exposure(date: pd.Timestamp, method: str) -> float:
    """Return portfolio exposure 0.0-1.0 for given date and regime method."""
    if date not in spy.index:
        # Find nearest prior date
        prior = spy.index[spy.index <= date]
        if len(prior) == 0:
            return 1.0
        date = prior[-1]

    row = spy.loc[date]

    if method == "R0_no_regime":
        return 1.0

    elif method == "R1_ma200_simple":
        return 1.0 if row["above_ma200"] else BEAR_EXP

    elif method == "R2_buffer_2pct":
        dist = row["dist_from_ma200"]
        if dist > 0.02:    return 1.0    # clearly bull: >2% above
        elif dist < -0.01: return BEAR_EXP  # clearly bear: >1% below
        else:              return SIDE_EXP  # sideway: within band

    elif method == "R3_nday5":
        # Bear only after 5 consecutive days below MA200
        if row["below_5days"]:  return BEAR_EXP
        else:                   return 1.0

    elif method == "R3_nday3":
        if row["below_3days"]:  return BEAR_EXP
        else:                   return 1.0

    elif method == "R4_death_cross":
        return BEAR_EXP if row["death_cross"] else 1.0

    elif method == "R5_ma200_slope":
        # Bull only when MA200 is rising (trend has momentum)
        if row["above_ma200"] and row["ma200_rising"]:  return 1.0
        elif not row["above_ma200"]:                    return BEAR_EXP
        else:                                           return SIDE_EXP  # above but MA200 falling

    elif method == "R6_dual_confirm":
        # Bull: price above MA200 AND no death cross
        if row["above_ma200"] and not row["death_cross"]: return 1.0
        elif not row["above_ma200"]:                      return BEAR_EXP
        else:                                             return SIDE_EXP

    elif method == "R7_codex_3state":
        # Replicate Codex: Bull=100%, Sideway=100%, Bear=40%
        # Sideway = within ±1.5% of MA200
        dist = row["dist_from_ma200"]
        if dist > 0.015:    return 1.0    # Bull
        elif dist > -0.015: return 1.0    # Sideway = still 100% (Codex behavior)
        else:               return 0.40   # Bear = 40% (Codex default)

    elif method == "R8_composite":
        # Score 0-3: above_ma200 + not death_cross + ma200_rising
        score = int(row["above_ma200"]) + int(not row["death_cross"]) + int(row["ma200_rising"])
        if score == 3:   return 1.0
        elif score == 2: return SIDE_EXP
        elif score == 1: return 0.50
        else:            return BEAR_EXP

    return 1.0


# ── Extend BacktestStrategy to use custom regime ─────────────
class RegimeStrategy(v2.BacktestStrategy):
    def __init__(self, dataloader, params, regime_method: str):
        super().__init__(dataloader, params)
        self.regime_method = regime_method

    def screen(self, date: pd.Timestamp):
        """Override: use custom regime instead of SPY MA200 simple."""
        day_sig = self.dl.get_signals_at(date, self.params)
        if day_sig.empty:
            return pd.DataFrame(columns=["ticker", "weight"])

        # Hard filters (same as always)
        mask = (
            (day_sig["rs_pct"]          >= self.params.rs_threshold) &
            (day_sig["vol_trend"]       >= self.params.vol_trend_min) &
            (day_sig["vol_contraction"] <= self.params.vol_contract_max) &
            (day_sig["base_tight"]      <= self.params.base_tight_max) &
            (day_sig["adtv_63m"]        >= self.params.adtv_min_m) &
            (day_sig["close"]           >  day_sig["ma_200"])
        )
        sel = day_sig[mask].copy()
        if sel.empty:
            return pd.DataFrame(columns=["ticker", "weight"])

        # Get exposure from regime method
        exposure = get_exposure(date, self.regime_method)

        # Softmax weighting on RS
        scores  = sel["rs_pct"].values
        shifted = 0.5 * (scores - scores.max())
        exp_s   = np.exp(shifted)
        weights = exp_s / exp_s.sum()

        # Cap + normalize + regime scale
        weights = np.minimum(weights, self.params.cap_pct)
        total   = weights.sum()
        if total > 0:
            weights = weights / total * exposure

        return pd.DataFrame({
            "ticker": sel["ticker"].values,
            "weight": weights,
            "rs_pct": sel["rs_pct"].values,
            "adtv":   sel["adtv_63m"].values,
        })


# ── Run all regime methods × all windows ─────────────────────
dl = v2.BacktestDataloader()
us = v2.CostParams()   # 14 bps round-trip

WINDOWS = [
    ("WF1 COVID(2020-21)", "2020-01-01", "2021-12-31"),
    ("WF2 Bear(2022-23) ", "2022-01-01", "2023-12-31"),
    ("WF3 Bull(2024-26) ", "2024-01-01", "2026-08-20"),
    ("FULL(2017-2026)   ", "2017-01-01", "2026-08-20"),
]

REGIMES = [
    ("R0  No regime gate      ", "R0_no_regime"),
    ("R1  Simple MA200 (now)  ", "R1_ma200_simple"),
    ("R2  Buffer +-2%/1%      ", "R2_buffer_2pct"),
    ("R3a N-day confirm 3d    ", "R3_nday3"),
    ("R3b N-day confirm 5d    ", "R3_nday5"),
    ("R4  Death cross 50<200  ", "R4_death_cross"),
    ("R5  MA200 slope         ", "R5_ma200_slope"),
    ("R6  Dual confirm        ", "R6_dual_confirm"),
    ("R7  Codex 3-state       ", "R7_codex_3state"),
    ("R8  Composite score     ", "R8_composite"),
]

BASE_PARAMS = dict(
    rs_threshold=80, vol_trend_min=0.95,
    vol_contract_max=1.10, base_tight_max=1.10,
    adtv_min_m=15, cap_pct=0.18,
    # Regime handled externally — set bull/bear both to 1.0 (regime overrides)
    bull_exposure=1.0, bear_exposure=1.0,
    weighting="softmax", softmax_alpha=0.5,
)

results = {}
total = len(REGIMES) * len(WINDOWS)
done  = 0
print(f"\nRunning {len(REGIMES)} regime methods x {len(WINDOWS)} windows = {total} backtests...\n")

for rlabel, rmethod in REGIMES:
    results[rlabel] = {}
    for wlabel, start, end in WINDOWS:
        p     = v2.StrategyParams(**BASE_PARAMS, start_date=start, end_date=end)
        strat = RegimeStrategy(dl, p, regime_method=rmethod)
        port  = v2.BacktestPortfolio(1_000_000, us)
        model = v2.BacktestModel(dl, strat, port, p)
        r     = model.run(verbose=False)
        results[rlabel][wlabel] = r
        done += 1
        sys.stdout.write(f"\r  {done}/{total}")
        sys.stdout.flush()

print("\n")

# ── Print results ─────────────────────────────────────────────
W = [w[0] for w in WINDOWS]

def tbl(title, metric_fn, fmt):
    print(f"=== {title} ===")
    header = f"{'Regime':30s}"
    for w in W: header += f" | {w.strip()[:17]:17s}"
    print(header)
    print("-" * (30 + 20 * len(W)))
    for rlabel, wins in results.items():
        row = f"{rlabel:30s}"
        for wk in W:
            val = metric_fn(wins[wk])
            row += f" | {fmt(val):17s}"
        print(row)
    print()

tbl("EXCESS RETURN vs QQQ",
    lambda r: r["excess"]*100,
    lambda v: f"{v:+.1f}%")

tbl("MAX DRAWDOWN",
    lambda r: r["port_mdd"]*100,
    lambda v: f"{v:.1f}%")

tbl("SHARPE RATIO",
    lambda r: r["sharpe"],
    lambda v: f"{v:.2f}")

tbl("CALMAR RATIO",
    lambda r: r["calmar"],
    lambda v: f"{v:.2f}")

# ── Regime switch frequency (cost driver) ────────────────────
print("=== REGIME SWITCH ANALYSIS (bear days %) ===")
print("How often each method puts portfolio in bear/reduced mode:")
print()

date_range = pd.date_range("2017-01-01", "2026-08-20", freq="ME")
for rlabel, rmethod in REGIMES:
    exposures = []
    for d in date_range:
        prior = spy.index[spy.index <= d]
        if len(prior) > 0:
            exposures.append(get_exposure(prior[-1], rmethod))
    arr = np.array(exposures)
    pct_full = (arr >= 0.99).mean() * 100
    pct_bear = (arr <= BEAR_EXP + 0.01).mean() * 100
    pct_side = 100 - pct_full - pct_bear
    switches = sum(1 for i in range(1, len(arr)) if abs(arr[i]-arr[i-1]) > 0.1)
    print(f"  {rlabel:30s}: Bull={pct_full:4.0f}% Side={pct_side:4.0f}% Bear={pct_bear:4.0f}%  Switches/yr={switches/9:.1f}")

# ── Composite score + ranking ─────────────────────────────────
print()
print("=== COMPOSITE SCORE (WF1x1 + WF2x3 + WF3x1 + FULLx2) ===")
print(f"{'Regime':30s} | Score  | WF2    | FULL   | DD     | Sharpe | Calmar")
print("-" * 100)

scored = []
for rlabel, wins in results.items():
    wf1  = wins["WF1 COVID(2020-21)"]["excess"] * 100
    wf2  = wins["WF2 Bear(2022-23) "]["excess"] * 100
    wf3  = wins["WF3 Bull(2024-26) "]["excess"] * 100
    full = wins["FULL(2017-2026)   "]["excess"] * 100
    dd   = wins["FULL(2017-2026)   "]["port_mdd"] * 100
    sh   = wins["FULL(2017-2026)   "]["sharpe"]
    cal  = wins["FULL(2017-2026)   "]["calmar"]
    score = wf1*1 + wf2*3 + wf3*1 + full*2
    scored.append((score, rlabel, wf2, full, dd, sh, cal))

scored.sort(reverse=True)
for i, (score, rl, wf2, full, dd, sh, cal) in enumerate(scored):
    m = " <-- WINNER" if i==0 else (" <-- 2nd" if i==1 else "")
    print(f"{rl:30s} | {score:+6.1f} | {wf2:+5.1f}% | {full:+5.1f}% | {dd:6.1f}% | {sh:.2f}   | {cal:.2f}{m}")

print()
w = scored[0]
print(f"WINNER: {w[1].strip()}")
print(f"  Score={w[0]:+.1f}  WF2={w[2]:+.1f}%  FULL={w[3]:+.1f}%  DD={w[4]:.1f}%  Sharpe={w[5]:.2f}  Calmar={w[6]:.2f}")

print("""
KEY INSIGHTS TO READ:
  - R0 (no gate) shows theoretical max return — regime gate costs excess in bull
  - R7 Codex 3-state: sideway=100% means FEWER cuts → less whipsaw, less cost
  - WF2 2022 is the KEY test: good regime gate should hold excess positive there
  - Switch frequency = turnover cost from regime changes alone
  - Lower switches = less regime-driven cost drag
""")
