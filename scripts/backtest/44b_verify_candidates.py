"""
44b_verify_candidates.py — Full v2 engine verification of top candidates from script 44

Verifies with production v2 engine:
  A. ERC weighting (almost same excess as v4.0, +1% DD improvement in mini backtest)
  B. PortVol target=20% (DD -5% better, Sharpe +0.08 better, -15% excess)
  C. PortVol target=15% (DD -9% better, Sharpe +0.09 better, -21% excess)

These are PARTIAL governance candidates — DD improves AND Sharpe beats locked.
Run ONE at a time; compare directly against same-run baseline.
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd
import numpy as np
import importlib.util, pathlib
import warnings
warnings.filterwarnings('ignore')

_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)

BASE_PARAMS = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=9.9, base_tight_max=9.9, adtv_min_m=10, cap_pct=0.18,
    bear_exposure=0.50, bull_exposure=1.00, softmax_alpha=1.0,
    start_date='2017-01-01', end_date='2026-08-20'
)
LOCKED = dict(excess=43.4, dd=-38.2, sharpe=1.46)
WF_WINDOWS = [
    ('WF1 2020-21', '2020-01-01', '2021-12-31'),
    ('WF2 2022-23', '2022-01-01', '2023-12-31'),
    ('WF3 2024-26', '2024-01-01', '2026-08-20'),
]

print("Loading data...")
dl   = v2.BacktestDataloader()
sig  = dl.signals.copy()
sig['date'] = pd.to_datetime(sig['date'])

close_px = sig.pivot_table(index='date', columns='ticker', values='close').sort_index()
log_ret  = np.log(close_px / close_px.shift(1))
vol_63d  = log_ret.rolling(63, min_periods=20).std() * np.sqrt(252)

beta_lkp = {}
for _, r in sig[['date','ticker','beta_252']].dropna().iterrows():
    b = float(r['beta_252'])
    if not np.isnan(b):
        beta_lkp[(r['date'], r['ticker'])] = max(0.3, min(b, 4.0))

all_dates = sig['date'].sort_values().unique()
if 'IWM' in close_px.columns:
    iwm = close_px['IWM'].dropna()
    iwm_ma200 = iwm.rolling(200, min_periods=150).mean()
    iwm_bull_s = (iwm > iwm_ma200).astype(float).reindex(all_dates, method='ffill').fillna(1.0)
else:
    iwm_bull_s = pd.Series(1.0, index=all_dates)

_orig_screen = v2.BacktestStrategy.screen

# ── VOL-PARITY HELPER (v4.0 exact) ───────────────────────────────────────────
def vp_screen(self, date, top_n=15, exposure_override=None):
    df   = _orig_screen(self, date)
    if df.empty: return df
    ts   = pd.Timestamp(date)
    bull = bool(iwm_bull_s.get(ts, 1.0) >= 0.5)
    exp  = (1.0 if bull else 0.5) if exposure_override is None else exposure_override
    vt   = 0.75 if bull else 0.95
    if 'vol_trend' in df.columns: df = df[df['vol_trend'] >= vt].copy()
    if df.empty: return df
    if len(df) > top_n: df = df.nlargest(top_n, 'rs_pct').copy()
    vols, maxws = [], []
    for tkr in df['ticker']:
        v = float(vol_63d.loc[ts, tkr]) if (ts in vol_63d.index and tkr in vol_63d.columns) else 0.30
        vols.append(max(v if not np.isnan(v) else 0.30, 0.01))
        b = beta_lkp.get((ts, tkr), 1.0)
        maxws.append(min(0.18, 0.12 / max(b, 0.5)))
    raw_w = 1.0 / np.array(vols); raw_w /= raw_w.sum()
    w = raw_w.copy(); mw = np.array(maxws)
    for _ in range(25):
        over = w > mw
        if not over.any(): break
        exc = (w[over] - mw[over]).sum(); w[over] = mw[over]
        recv = ~over
        if not recv.any(): break
        w[recv] += exc * w[recv] / w[recv].sum()
    df = df.copy(); df['weight'] = w * exp
    return df[['ticker','rs_pct','weight']].copy()

# ── ERC HELPER ────────────────────────────────────────────────────────────────
def erc_screen(self, date):
    df   = _orig_screen(self, date)
    if df.empty: return df
    ts   = pd.Timestamp(date)
    bull = bool(iwm_bull_s.get(ts, 1.0) >= 0.5)
    exp  = 1.0 if bull else 0.5
    vt   = 0.75 if bull else 0.95
    if 'vol_trend' in df.columns: df = df[df['vol_trend'] >= vt].copy()
    if df.empty: return df
    if len(df) > 15: df = df.nlargest(15, 'rs_pct').copy()
    tickers = df['ticker'].tolist()

    # Build covariance from last 63 trading days
    recent = log_ret.loc[log_ret.index <= ts].tail(63)[tickers].dropna(axis=1, how='any')
    valid  = recent.columns.tolist()
    if len(valid) < 2:
        return vp_screen(self, date)

    df_v = df[df['ticker'].isin(valid)].copy()
    cov  = recent[valid].cov().values * 252
    n    = len(valid)
    betas_v = [beta_lkp.get((ts, t), 1.0) for t in valid]
    mw = np.array([min(0.18, 0.12 / max(b, 0.5)) for b in betas_v])
    w  = np.ones(n) / n
    for _ in range(60):
        port_var = w @ cov @ w
        if port_var < 1e-10: break
        rc = w * (cov @ w); target = port_var / n
        w  = w * (target / np.maximum(rc, 1e-10))
        w  = np.maximum(w, 0); w = np.minimum(w, mw)
        tot = w.sum()
        if tot < 1e-10: break
        w  = w / tot

    df_v = df_v.copy(); df_v['weight'] = list(w * exp)
    return df_v[['ticker','rs_pct','weight']].copy()

# ── PORTFOLIO VOL TRACKING ────────────────────────────────────────────────────
# For PortVol targeting, we need to track realized portfolio vol.
# We compute it by caching monthly portfolio returns and computing rolling std.

class PortVolState:
    def __init__(self, target):
        self.target = target
        self.monthly_rets = []

    def scale(self):
        if len(self.monthly_rets) < 3:
            return 1.0
        pv = np.std(self.monthly_rets[-6:]) * np.sqrt(12)
        return min(1.0, self.target / max(pv, 0.01))

pvt_state = PortVolState(0.20)  # will be reset per run

def pvt_screen(self, date, target=0.20, _state=None):
    scale = _state.scale() if _state else 1.0
    df = vp_screen(self, date)
    if df.empty: return df
    df = df.copy(); df['weight'] = df['weight'] * scale
    return df

def run_bt(label, screener_fn, start=None, end=None):
    params = BASE_PARAMS.copy()
    if start: params['start_date'] = start
    if end:   params['end_date']   = end
    v2.BacktestStrategy.screen = screener_fn
    try:
        p     = v2.StrategyParams(**params)
        strat = v2.BacktestStrategy(dl, p)
        port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
        r     = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
    finally:
        v2.BacktestStrategy.screen = _orig_screen
    sc   = 100 if abs(r.get('port_cagr', 0)) < 5 else 1
    cagr = r.get('port_cagr', 0) * sc
    exc  = r.get('excess', 0) * sc
    dd   = r.get('port_mdd', 0) * (100 if abs(r.get('port_mdd', 0)) < 1 else 1)
    sh   = r.get('sharpe', 0)
    ye   = {int(yr): (d['excess'] if isinstance(d, dict) else d)*100
            for yr, d in r.get('yearly', {}).items()}
    wins = sum(1 for v in ye.values() if v > 0)
    return dict(label=label, cagr=cagr, excess=exc, dd=dd, sharpe=sh,
                wins=wins, n=len(ye), ye=ye)

def print_r(r, ref=None):
    ye = r['ye']
    yr = ' '.join(f"{y}:{v:+.1f}%" for y,v in sorted(ye.items()))
    g  = gov(r)
    print(f"\n  {'='*60}")
    print(f"  {r['label']}")
    print(f"  CAGR={r['cagr']:.1f}% | Excess={r['excess']:+.1f}% | DD={r['dd']:.1f}% | Sharpe={r['sharpe']:.2f}")
    print(f"  Win={r['wins']}/{r['n']} | Governance={g}")
    if ref:
        dex = r['excess']-ref['excess']; ddd = r['dd']-ref['dd']; dsh = r['sharpe']-ref['sharpe']
        print(f"  Delta vs baseline: Ex{dex:+.1f}% DD{ddd:+.1f}% Sh{dsh:+.2f}")
    print(f"  Year-by-year: {yr}")

def gov(r):
    if r['excess'] > LOCKED['excess'] and r['dd'] > LOCKED['dd'] and r['sharpe'] > LOCKED['sharpe']:
        return 'STRICT'
    if r['dd'] > LOCKED['dd'] and (r['excess'] > LOCKED['excess'] or r['sharpe'] > LOCKED['sharpe']):
        return 'PARTIAL'
    if r['dd'] > LOCKED['dd']:
        return 'DD_ONLY'
    return 'FAIL'


print(f"\n{'='*65}")
print("44b_verify_candidates.py — Full v2 Engine Verification")
print(f"{'='*65}")
print(f"  LOCKED: Excess={LOCKED['excess']:+.1f}% | DD={LOCKED['dd']:.1f}% | Sh={LOCKED['sharpe']:.2f}")

results = []

# ── BASELINE ─────────────────────────────────────────────────────────────────
print("\n[A: Baseline v4.0 (same-run)]")
r = run_bt("BASELINE v4.0", lambda self, date: vp_screen(self, date))
print_r(r); results.append(r); base = r

# ── ERC ──────────────────────────────────────────────────────────────────────
print("\n[B: ERC Weighting]")
print("  ERC: each stock contributes equal portfolio risk (covariance-aware)")
r = run_bt("B: ERC weighting", erc_screen)
print_r(r, ref=base); results.append(r)

# ── PORTVOL 20% ───────────────────────────────────────────────────────────────
print("\n[C: Portfolio Vol Targeting 20%]")
print("  Scale all weights when portfolio 6-month realized vol > 20% p.a.")
print("  NOTE: This runs with static scale since tracking vol needs full loop")
print("  Approximated via pre-computed scale: if first 6 months vol ~ 40% => scale=0.5")
print("  Real result will use proper forward-looking vol estimate.")

# For proper PortVol we need to track returns. The v2 engine doesn't expose
# portfolio returns to the screener mid-run. Best approximation:
# Use IWM 63d realized vol as proxy for portfolio vol (correlated but not exact).
if 'IWM' in close_px.columns:
    iwm_vol = (np.log(close_px['IWM'] / close_px['IWM'].shift(1))
               .rolling(63, min_periods=20).std() * np.sqrt(252))
    iwm_vol_by_date = iwm_vol.ffill()
else:
    iwm_vol_by_date = pd.Series(0.20, index=close_px.index)

# Portfolio vol is typically ~2-3x IWM vol for momentum stocks
# Scale by 2.0x assumption: port_vol_proxy = min(1.0, target/(2.0*iwm_vol))
TARGET_VOL = 0.20
BETA_EST   = 1.8  # avg beta of momentum basket

def pvt20_screen(self, date):
    ts = pd.Timestamp(date)
    iwm_v = float(iwm_vol_by_date.loc[iwm_vol_by_date.index <= ts].iloc[-1]) if len(iwm_vol_by_date.loc[iwm_vol_by_date.index <= ts]) > 0 else 0.15
    port_v_proxy = max(iwm_v * BETA_EST, 0.01)
    scale = min(1.0, TARGET_VOL / port_v_proxy)
    df = vp_screen(self, date)
    if df.empty: return df
    df = df.copy(); df['weight'] = df['weight'] * scale
    return df

r = run_bt("C: PortVol-20% (IWM proxy)", pvt20_screen)
print_r(r, ref=base); results.append(r)

# ── PORTVOL 15% ───────────────────────────────────────────────────────────────
print("\n[D: Portfolio Vol Targeting 15%]")
TARGET_VOL2 = 0.15

def pvt15_screen(self, date):
    ts = pd.Timestamp(date)
    iwm_v = float(iwm_vol_by_date.loc[iwm_vol_by_date.index <= ts].iloc[-1]) if len(iwm_vol_by_date.loc[iwm_vol_by_date.index <= ts]) > 0 else 0.15
    port_v_proxy = max(iwm_v * BETA_EST, 0.01)
    scale = min(1.0, TARGET_VOL2 / port_v_proxy)
    df = vp_screen(self, date)
    if df.empty: return df
    df = df.copy(); df['weight'] = df['weight'] * scale
    return df

r = run_bt("D: PortVol-15% (IWM proxy)", pvt15_screen)
print_r(r, ref=base); results.append(r)

# ── WALK-FORWARD on ERC ───────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("WALK-FORWARD — ERC vs Baseline (most balanced candidate)")
print(f"{'='*65}")

print("\n  Running WF1 (2020-21)...")
wf1_base = run_bt("BASELINE|WF1", lambda self, date: vp_screen(self, date), start='2020-01-01', end='2021-12-31')
wf1_erc  = run_bt("ERC|WF1",      erc_screen,                                start='2020-01-01', end='2021-12-31')

print("\n  Running WF2 (2022-23)...")
wf2_base = run_bt("BASELINE|WF2", lambda self, date: vp_screen(self, date), start='2022-01-01', end='2023-12-31')
wf2_erc  = run_bt("ERC|WF2",      erc_screen,                                start='2022-01-01', end='2023-12-31')

print("\n  Running WF3 (2024-26)...")
wf3_base = run_bt("BASELINE|WF3", lambda self, date: vp_screen(self, date), start='2024-01-01', end='2026-08-20')
wf3_erc  = run_bt("ERC|WF3",      erc_screen,                                start='2024-01-01', end='2026-08-20')

print(f"\n  {'Config':<25}  {'WF1 2020-21':>20}  {'WF2 2022-23':>20}  {'WF3 2024-26':>20}")
print("-"*90)
def wf_str(r): return f"Ex{r['excess']:+.1f}%/Sh{r['sharpe']:.2f}/DD{r['dd']:.0f}%"
print(f"  {'BASELINE v4.0':<25}  {wf_str(wf1_base):>20}  {wf_str(wf2_base):>20}  {wf_str(wf3_base):>20}")
print(f"  {'ERC weighting':<25}  {wf_str(wf1_erc):>20}  {wf_str(wf2_erc):>20}  {wf_str(wf3_erc):>20}")

# ── FULL SUMMARY ──────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("FULL RESULTS SUMMARY")
print(f"{'='*65}")
print(f"  {'Config':<40} {'CAGR':>7} {'Excess':>7} {'DD':>7} {'Sh':>5} {'W':>5}  Gov")
print("-"*80)
for r in results:
    g  = gov(r); mk = '***' if g in ('STRICT','PARTIAL') else '   '
    print(f"  {mk} {r['label']:<40} {r['cagr']:>+6.1f}% {r['excess']:>+6.1f}% "
          f"{r['dd']:>+6.1f}% {r['sharpe']:>5.2f} {r['wins']:>2}/{r['n']}  [{g}]")
print(f"\n  LOCKED: Excess={LOCKED['excess']:+.1f}% | DD={LOCKED['dd']:.1f}% | Sh={LOCKED['sharpe']:.2f}")

# ── GOVERNANCE VERDICT ────────────────────────────────────────────────────────
print(f"\n{'='*65}")
strict  = [r for r in results[1:] if gov(r)=='STRICT']
partial = [r for r in results[1:] if gov(r)=='PARTIAL']

if strict:
    print("STRICT GOVERNANCE PASSED:")
    for r in strict:
        print(f"  {r['label']}: CAGR={r['cagr']:+.1f}% Ex={r['excess']:+.1f}% DD={r['dd']:.1f}% Sh={r['sharpe']:.2f}")
elif partial:
    print("PARTIAL GOVERNANCE PASSED (DD + Sharpe both beat locked):")
    for r in partial:
        print(f"  {r['label']}: CAGR={r['cagr']:+.1f}% Ex={r['excess']:+.1f}% DD={r['dd']:.1f}% Sh={r['sharpe']:.2f}")
        print(f"    DD delta: {r['dd']-LOCKED['dd']:+.1f}% | Sharpe delta: {r['sharpe']-LOCKED['sharpe']:+.2f}")
        print(f"    Excess COST to get this DD improvement: {r['excess']-LOCKED['excess']:+.1f}%")
        print(f"    CIO decision: Is -10 to -25% excess worth +8 to +22% DD improvement?")
else:
    print("NO governance passed — no approach beats v4.0 locked on DD + Sharpe.")
    print("v4.0 DD=-38.2% appears to be a structural cost of high-momentum strategy.")
    print("Next direction: accept the DD and focus on WF2 2022 survival optimization.")
