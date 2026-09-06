"""
36_position_sizing.py — Position Sizing / Cap Study

Goal: Reduce drawdown by varying how much weight each stock can have.
All configs run through v2.BacktestModel (full engine) → accurate Sharpe numbers.

Philosophy: heavy concentration in high-beta/high-vol momentum stocks drives DD.
Test whether smarter position sizing can improve Sharpe + DD without killing excess.

METHODS:
  A. Baseline (fixed 18% cap, ADTV^1.5 softmax — current v3.1)
  B. Hard cap tighter: 10%, 12%, 15%
  C. Beta cap: max_w = base_cap / beta (high beta = capped tighter)
  D. Vol cap: max_w = base_cap × (target_vol / stock_vol) — volatility parity
  E. ADTV tier cap: large liquidity = allowed bigger position
  F. Vol-parity weighting: weights = 1/realized_vol, normalized (replaces ADTV^1.5)
  G. Equal weight: no size tilt
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd, numpy as np, importlib.util, pathlib

_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)

BASE_PARAMS = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=9.9, base_tight_max=9.9, adtv_min_m=10, cap_pct=0.18,
    bear_exposure=0.50, bull_exposure=1.00, softmax_alpha=1.0,
    start_date='2017-01-01', end_date='2026-08-20'
)
TOP_N   = 15
BASELINE = dict(excess=35.8, dd=-40.1, sharpe=1.44)

print("Loading data...")
dl  = v2.BacktestDataloader()
sig = dl.signals.copy()
sig['date'] = pd.to_datetime(sig['date'])

# Pre-build signal lookups
close_px = sig.pivot_table(index='date', columns='ticker', values='close').sort_index()
log_ret  = np.log(close_px / close_px.shift(1))
vol_63d  = log_ret.rolling(63, min_periods=20).std() * np.sqrt(252)   # annualized realized vol

# Beta from signals (precomputed in signals.parquet)
beta_sig = sig[['date','ticker','beta_252']].dropna().copy()
beta_map  = {}  # (date, ticker) → beta
for _, r in beta_sig.iterrows():
    beta_map[(r['date'], r['ticker'])] = r['beta_252']

# ADTV from signals
adtv_sig = sig[['date','ticker','adtv_63m']].dropna().copy()
adtv_map  = {}
for _, r in adtv_sig.iterrows():
    adtv_map[(r['date'], r['ticker'])] = r['adtv_63m']

print(f"  Data loaded. Building sizing lookups...")

_orig_screen = v2.BacktestStrategy.screen

def make_screener(cap_fn=None, weight_fn=None, top_n=TOP_N):
    """
    cap_fn(tkr, date, current_w) -> max_w for this stock
    weight_fn(screen_df, date) -> screen_df with adjusted 'weight' column
    Returns a patched screen() method.
    """
    def patched_screen(self, date):
        df = _orig_screen(self, date)
        if df.empty: return df

        # Top-N trim first
        if len(df) > top_n:
            df = df.nlargest(top_n, 'rs_pct').copy()

        # Custom weighting (replaces softmax if provided)
        if weight_fn is not None:
            df = weight_fn(df, date)

        # Apply per-stock cap
        if cap_fn is not None:
            caps = pd.Series({
                r['ticker']: cap_fn(r['ticker'], date, r['weight'])
                for _, r in df.iterrows()
            }, name='cap')
            df = df.set_index('ticker')
            df['cap'] = caps
            # Iterative normalization: clip, renorm until stable
            for _ in range(20):
                df['weight'] = df['weight'].clip(upper=df['cap'])
                s = df['weight'].sum()
                if s < 1e-9: break
                df['weight'] = df['weight'] / s
                if (df['weight'] <= df['cap'] + 1e-9).all():
                    break
            df = df.reset_index()

        # Normalize to sum = original exposure
        total_w = df['weight'].sum()
        if total_w > 1e-9:
            df['weight'] = df['weight'] / total_w * total_w  # already normalized
        return df
    return patched_screen

def run_config(label, cap_fn=None, weight_fn=None, cap_pct_override=None):
    """Run full v2.BacktestModel with patched screener."""
    params = BASE_PARAMS.copy()
    if cap_pct_override is not None:
        params['cap_pct'] = cap_pct_override

    v2.BacktestStrategy.screen = make_screener(cap_fn, weight_fn)
    try:
        p     = v2.StrategyParams(**params)
        strat = v2.BacktestStrategy(dl, p)
        port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
        r     = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
    finally:
        v2.BacktestStrategy.screen = _orig_screen

    sc   = 100 if r.get('port_cagr', 0) < 5 else 1
    cagr = r.get('port_cagr', 0) * sc
    exc  = r.get('excess', 0) * sc
    dd   = r.get('port_mdd', 0) * (100 if abs(r.get('port_mdd', 0)) < 1 else 1)
    sh   = r.get('sharpe', 0)
    holds = r.get('avg_holdings', 0)

    ye = {}
    for yr, d in r.get('yearly', {}).items():
        v_exc = (d['excess'] if isinstance(d, dict) else d) * 100
        ye[int(yr)] = v_exc

    wins = sum(1 for v in ye.values() if v > 0)
    wf2  = (ye.get(2022, 0) + ye.get(2023, 0)) / 2

    ex_ok = exc > BASELINE['excess']
    dd_ok = dd  > BASELINE['dd']
    sh_ok = sh  > BASELINE['sharpe']
    gov   = 'STRICT'  if (ex_ok and dd_ok and sh_ok) else \
            'PARTIAL' if ((ex_ok or sh_ok) and dd_ok) else 'no'

    yr_str = ' '.join(f"{yr}:{'+' if v>=0 else ''}{v:.1f}%" for yr, v in sorted(ye.items()))

    print(f"\n{'='*65}")
    print(f"  {label}")
    print(f"  CAGR={cagr:.1f}% | Excess={exc:+.1f}% | DD={dd:.1f}%")
    print(f"  Sharpe={sh:.2f} | Holds={holds:.0f} | Win={wins}/{len(ye)} | WF2={wf2:+.1f}% | Gov={gov}")
    print(f"  {yr_str}")

    return dict(label=label, cagr=cagr, excess=exc, dd=dd, sharpe=sh,
                holds=holds, wins=wins, wf2=wf2, gov=gov, ye=ye)

# ── Sizing helper functions ───────────────────────────────────────────────────

def get_vol(tkr, date):
    """63d realized annualized vol for ticker at date. Returns 0.30 if unknown."""
    if tkr in vol_63d.columns and date in vol_63d.index:
        v = float(vol_63d.loc[date, tkr])
        if not np.isnan(v) and v > 0.05: return v
    return 0.30

def get_beta(tkr, date):
    """Beta(252d) for ticker at date. Returns 1.0 if unknown."""
    b = beta_map.get((pd.Timestamp(date), tkr))
    if b is None or np.isnan(b) or b <= 0:
        return 1.0
    return max(0.3, min(b, 3.0))  # clip extreme betas

def get_adtv(tkr, date):
    """ADTV in $M for ticker at date."""
    a = adtv_map.get((pd.Timestamp(date), tkr))
    return float(a) if (a is not None and not np.isnan(a)) else 30.0

# ── Vol-parity weighter (replaces ADTV^1.5 softmax) ─────────────────────────

def vol_parity_weight(df, date):
    """Assign weights inversely proportional to 63d realized vol."""
    vols = []
    for _, r in df.iterrows():
        vols.append(get_vol(r['ticker'], date))
    inv_vol = [1.0/v for v in vols]
    total   = sum(inv_vol)
    df = df.copy()
    df['weight'] = [iv/total for iv in inv_vol]
    return df

def equal_weight(df, date):
    n = len(df)
    df = df.copy()
    df['weight'] = 1.0 / n
    return df

# ── CONFIGS ──────────────────────────────────────────────────────────────────

print(f"\n{'='*65}")
print("POSITION SIZING STUDY — v2 engine (accurate Sharpe)")
print(f"{'='*65}")

results = []

# A. Baseline (current v3.1)
r = run_config("BASELINE — 18% cap, ADTV^1.5 softmax")
results.append(r)

# B. Hard cap variants
for cap in [0.10, 0.12, 0.15]:
    def cap_fn(tkr, date, w, _c=cap): return _c
    r = run_config(f"HARD CAP {int(cap*100)}% (vs 18%)", cap_fn=cap_fn, cap_pct_override=cap)
    results.append(r)

# C. Beta-adjusted cap: max = base_cap / beta (high beta gets less room)
def beta_cap(tkr, date, w):
    b = get_beta(tkr, date)
    return min(0.18, 0.18 / b)   # beta=1→18%, beta=2→9%, beta=0.5→18% (capped at 18%)

r = run_config("BETA CAP (18%/beta — high beta = smaller max)", cap_fn=beta_cap)
results.append(r)

# C2. Beta cap with base = 15%
def beta_cap_15(tkr, date, w):
    b = get_beta(tkr, date)
    return min(0.15, 0.15 / b)

r = run_config("BETA CAP 15% base (15%/beta)", cap_fn=beta_cap_15)
results.append(r)

# D. Vol cap: target_port_vol = 20% annualized; per-stock max = target * base_cap / stock_vol
TARGET_VOL = 0.20
def vol_cap(tkr, date, w):
    sv = get_vol(tkr, date)
    return min(0.18, TARGET_VOL / sv * 0.18)  # high vol stock → smaller cap

r = run_config("VOL CAP (target_vol=20%, 18%×20%/stock_vol)", cap_fn=vol_cap)
results.append(r)

# D2. More aggressive vol cap (target 15%)
def vol_cap_15(tkr, date, w):
    sv = get_vol(tkr, date)
    return min(0.15, 0.15 * 0.15 / sv)

r = run_config("VOL CAP (target_vol=15%)", cap_fn=vol_cap_15)
results.append(r)

# E. ADTV tier cap: bigger company = allowed more weight
def adtv_tier_cap(tkr, date, w):
    adtv = get_adtv(tkr, date)
    if   adtv >= 500:  return 0.20  # mega liquidity
    elif adtv >= 100:  return 0.18  # large
    elif adtv >= 30:   return 0.12  # mid
    else:              return 0.08  # small ($10-30M ADTV — adtv_min=10 so possible)

r = run_config("ADTV TIER CAP (mega=20%, lrg=18%, mid=12%, sm=8%)", cap_fn=adtv_tier_cap)
results.append(r)

# F. Vol-parity weighting (replaces ADTV^1.5 softmax with 1/vol)
r = run_config("VOL-PARITY WEIGHT (1/vol, 18% cap)", weight_fn=vol_parity_weight)
results.append(r)

# F2. Vol-parity + tighter 12% cap
def cap12(tkr, date, w): return 0.12
r = run_config("VOL-PARITY WEIGHT + 12% cap", weight_fn=vol_parity_weight, cap_fn=cap12)
results.append(r)

# G. Equal weight (same size for all stocks)
r = run_config("EQUAL WEIGHT (1/N, 18% cap)", weight_fn=equal_weight)
results.append(r)

# H. Beta cap + vol-parity (combined: vol-parity weighting, beta-adjusted cap)
r = run_config("VOL-PARITY + BETA CAP", weight_fn=vol_parity_weight, cap_fn=beta_cap)
results.append(r)

# I. Top 10 only (concentration reduction via fewer stocks)
def cap18(tkr, date, w): return 0.18
r = run_config("TOP 10 STOCKS ONLY (cap 18%)", cap_fn=cap18, weight_fn=None)
# Override top_n=10
v2.BacktestStrategy.screen = make_screener(cap18, None, top_n=10)
try:
    p     = v2.StrategyParams(**BASE_PARAMS)
    strat = v2.BacktestStrategy(dl, p)
    port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
    rr    = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
    results.pop()  # remove the incorrect one
    sc = 100 if rr.get('port_cagr',0)<5 else 1
    ye2 = {int(yr): (d['excess'] if isinstance(d,dict) else d)*100 for yr,d in rr.get('yearly',{}).items()}
    exc2 = rr.get('excess',0)*sc; dd2 = rr.get('port_mdd',0)*(100 if abs(rr.get('port_mdd',0))<1 else 1)
    sh2 = rr.get('sharpe',0); holds2 = rr.get('avg_holdings',0)
    wf2_2 = (ye2.get(2022,0)+ye2.get(2023,0))/2
    wins2 = sum(1 for v in ye2.values() if v>0)
    ex_ok2 = exc2>BASELINE['excess']; dd_ok2 = dd2>BASELINE['dd']; sh_ok2 = sh2>BASELINE['sharpe']
    gov2 = 'STRICT' if (ex_ok2 and dd_ok2 and sh_ok2) else 'PARTIAL' if ((ex_ok2 or sh_ok2) and dd_ok2) else 'no'
    yr_s = ' '.join(f"{yr}:{'+' if v>=0 else ''}{v:.1f}%" for yr,v in sorted(ye2.items()))
    print(f"\n{'='*65}\n  TOP 10 STOCKS ONLY (cap 18%)")
    print(f"  CAGR={rr.get('port_cagr',0)*sc:.1f}% | Excess={exc2:+.1f}% | DD={dd2:.1f}%")
    print(f"  Sharpe={sh2:.2f} | Holds={holds2:.0f} | Win={wins2}/{len(ye2)} | WF2={wf2_2:+.1f}% | Gov={gov2}")
    print(f"  {yr_s}")
    results.append(dict(label="TOP 10 STOCKS ONLY (18% cap)", cagr=rr.get('port_cagr',0)*sc, excess=exc2, dd=dd2, sharpe=sh2, holds=holds2, wins=wins2, wf2=wf2_2, gov=gov2, ye=ye2))
finally:
    v2.BacktestStrategy.screen = _orig_screen

# ── SUMMARY ──────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("FULL SUMMARY vs v3.1 LOCKED BASELINE")
print(f"  {'Config':<44}  {'Excess':>7}  {'DD':>7}  {'Sh':>5}  {'Holds':>5}  {'WF2':>6}  Gov")
print("-"*100)
for r in results:
    print(f"  {r['label']:<44}  {r['excess']:>+6.1f}%  {r['dd']:>+6.1f}%  {r['sharpe']:>5.2f}"
          f"  {r['holds']:>5.0f}  {r['wf2']:>+5.1f}%  {r['gov']}")

b = results[0]
print(f"\n  v3.1 LOCKED:     Excess={BASELINE['excess']:+.1f}% DD={BASELINE['dd']:.1f}% Sharpe={BASELINE['sharpe']:.2f}")
print(f"  Same-run BASE:   Excess={b['excess']:+.1f}% DD={b['dd']:.1f}% Sharpe={b['sharpe']:.2f}")

gov_pass = [(r['label'], r) for r in results if r['gov'] in ('STRICT','PARTIAL')]
if gov_pass:
    print(f"\nGov PASS ({len(gov_pass)} configs):")
    for lbl, r in gov_pass:
        print(f"  [{r['gov']}] {lbl}")
        print(f"    Excess={r['excess']:+.1f}% DD={r['dd']:.1f}% Sh={r['sharpe']:.2f} WF2={r['wf2']:+.1f}%")
else:
    print(f"\nNo config passed v3.1 governance (absolute).")
    print(f"  Delta vs same-run baseline:")
    for r in results[1:]:
        dd_d  = r['dd']     - b['dd']
        sh_d  = r['sharpe'] - b['sharpe']
        ex_d  = r['excess'] - b['excess']
        print(f"    {r['label']:<44}  Ex{ex_d:+.1f}%  DD{dd_d:+.1f}%  Sh{sh_d:+.2f}")

# Year-by-year comparison for top 4 by Sharpe
print(f"\n{'='*65}")
print("YEAR-BY-YEAR vs BASELINE (top 4 by Sharpe delta)")
sorted_by_sh = sorted(results[1:], key=lambda x: x['sharpe'], reverse=True)[:4]
hdrs = [r['label'][:18] for r in sorted_by_sh]
print(f"  {'Yr':<6}  {'BASE':>8}" + "".join(f"  {h:>20}" for h in hdrs))
print("-"*100)
for yr in range(2017, 2027):
    bv  = b['ye'].get(yr, 0)
    row = f"  {yr}  {bv:>+7.1f}%"
    for r in sorted_by_sh:
        v = r['ye'].get(yr, 0); flag = '*' if abs(v-bv) > 5 else ' '
        row += f"  {v:>+19.1f}%{flag}"
    print(row)

# Key insight
print(f"\n{'='*65}")
print("KEY INSIGHT: DD driver analysis")
best_dd = min(results[1:], key=lambda x: x['dd'])
best_sh = max(results[1:], key=lambda x: x['sharpe'])
print(f"  Best DD reduction: {best_dd['label']}")
print(f"    DD {b['dd']:.1f}% -> {best_dd['dd']:.1f}% ({best_dd['dd']-b['dd']:+.1f}%)")
print(f"    Excess tradeoff: {b['excess']:+.1f}% -> {best_dd['excess']:+.1f}% ({best_dd['excess']-b['excess']:+.1f}%)")
print(f"  Best Sharpe: {best_sh['label']}")
print(f"    Sharpe {b['sharpe']:.2f} -> {best_sh['sharpe']:.2f} ({best_sh['sharpe']-b['sharpe']:+.2f})")
print(f"    DD tradeoff: {b['dd']:.1f}% -> {best_sh['dd']:.1f}% ({best_sh['dd']-b['dd']:+.1f}%)")
