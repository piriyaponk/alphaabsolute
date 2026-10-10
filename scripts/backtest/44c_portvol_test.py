"""
44c_portvol_test.py — Portfolio Vol Targeting with full v2 engine
Tests PortVol 20% and 15% using IWM vol * beta multiplier as portfolio vol proxy.
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
    if not np.isnan(b): beta_lkp[(r['date'], r['ticker'])] = max(0.3, min(b, 4.0))

all_dates = sig['date'].sort_values().unique()
if 'IWM' in close_px.columns:
    iwm = close_px['IWM'].dropna()
    iwm_ma200 = iwm.rolling(200, min_periods=150).mean()
    iwm_bull_s = (iwm > iwm_ma200).astype(float).reindex(all_dates, method='ffill').fillna(1.0)
    iwm_vol63  = (np.log(iwm / iwm.shift(1)).rolling(63, min_periods=20).std() * np.sqrt(252)).reindex(all_dates, method='ffill').fillna(0.18)
else:
    iwm_bull_s = pd.Series(1.0, index=all_dates)
    iwm_vol63  = pd.Series(0.18, index=all_dates)

_orig = v2.BacktestStrategy.screen
BETA_EST = 1.8  # avg beta of high-RS momentum basket vs IWM

def vp_screen(self, date, exposure_override=None, vol_scale=1.0):
    df   = _orig(self, date)
    if df.empty: return df
    ts   = pd.Timestamp(date)
    bull = bool(iwm_bull_s.get(ts, 1.0) >= 0.5)
    exp  = ((1.0 if bull else 0.5) if exposure_override is None else exposure_override) * vol_scale
    vt   = 0.75 if bull else 0.95
    if 'vol_trend' in df.columns: df = df[df['vol_trend'] >= vt].copy()
    if df.empty: return df
    if len(df) > 15: df = df.nlargest(15, 'rs_pct').copy()
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

def run_bt(label, scr_fn, start=None, end=None):
    p = BASE_PARAMS.copy()
    if start: p['start_date'] = start
    if end:   p['end_date']   = end
    v2.BacktestStrategy.screen = scr_fn
    try:
        ps    = v2.StrategyParams(**p)
        strat = v2.BacktestStrategy(dl, ps)
        port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
        r     = v2.BacktestModel(dl, strat, port, ps).run(verbose=False)
    finally:
        v2.BacktestStrategy.screen = _orig
    sc   = 100 if abs(r.get('port_cagr', 0)) < 5 else 1
    cagr = r.get('port_cagr', 0) * sc
    exc  = r.get('excess', 0) * sc
    dd   = r.get('port_mdd', 0) * (100 if abs(r.get('port_mdd', 0)) < 1 else 1)
    sh   = r.get('sharpe', 0)
    ye   = {int(yr): (d['excess'] if isinstance(d, dict) else d)*100
            for yr, d in r.get('yearly', {}).items()}
    wins = sum(1 for v in ye.values() if v > 0)
    print(f"  {label}: CAGR={cagr:+.1f}% Ex={exc:+.1f}% DD={dd:.1f}% Sh={sh:.2f} Win={wins}/{len(ye)}")
    return dict(label=label, cagr=cagr, excess=exc, dd=dd, sharpe=sh, wins=wins, n=len(ye), ye=ye)

def gov(r):
    if r['excess'] > LOCKED['excess'] and r['dd'] > LOCKED['dd'] and r['sharpe'] > LOCKED['sharpe']: return 'STRICT'
    if r['dd'] > LOCKED['dd'] and (r['excess'] > LOCKED['excess'] or r['sharpe'] > LOCKED['sharpe']): return 'PARTIAL'
    if r['dd'] > LOCKED['dd']: return 'DD_ONLY'
    return 'FAIL'

print(f"\n{'='*65}")
print("44c_portvol_test.py — Portfolio Vol Targeting (Full v2 Engine)")
print(f"{'='*65}")
print(f"  LOCKED: Ex={LOCKED['excess']:+.1f}% DD={LOCKED['dd']:.1f}% Sh={LOCKED['sharpe']:.2f}")
print(f"\n  Approach: scale all portfolio weights by min(1.0, target / IWM_vol_proxy)")
print(f"  Port vol proxy = IWM 63d realized vol * {BETA_EST} (momentum beta estimate)")

results = []
print("\n[Baseline v4.0]")
r = run_bt("BASELINE v4.0", lambda self, d: vp_screen(self, d))
results.append(r); base = r

print("\n[Portfolio Vol Targeting 25%]")
def scr_pvt25(self, date):
    ts = pd.Timestamp(date)
    iv = float(iwm_vol63.get(ts, 0.18))
    scale = min(1.0, 0.25 / max(iv * BETA_EST, 0.01))
    return vp_screen(self, date, vol_scale=scale)
r = run_bt("PortVol 25% target", scr_pvt25)
results.append(r)

print("\n[Portfolio Vol Targeting 20%]")
def scr_pvt20(self, date):
    ts = pd.Timestamp(date)
    iv = float(iwm_vol63.get(ts, 0.18))
    scale = min(1.0, 0.20 / max(iv * BETA_EST, 0.01))
    return vp_screen(self, date, vol_scale=scale)
r = run_bt("PortVol 20% target", scr_pvt20)
results.append(r)

print("\n[Portfolio Vol Targeting 15%]")
def scr_pvt15(self, date):
    ts = pd.Timestamp(date)
    iv = float(iwm_vol63.get(ts, 0.18))
    scale = min(1.0, 0.15 / max(iv * BETA_EST, 0.01))
    return vp_screen(self, date, vol_scale=scale)
r = run_bt("PortVol 15% target", scr_pvt15)
results.append(r)

# Summary
print(f"\n{'='*65}")
print("SUMMARY")
print(f"{'='*65}")
print(f"  {'Config':<30} {'CAGR':>7} {'Excess':>7} {'DD':>7} {'Sh':>5} {'W':>5}  Gov  DDdelta")
print("-"*80)
for r in results:
    g  = gov(r)
    ddd = r['dd'] - base['dd']
    mk = '***' if g in ('STRICT','PARTIAL') else '   '
    print(f"  {mk} {r['label']:<30} {r['cagr']:>+6.1f}% {r['excess']:>+6.1f}% "
          f"{r['dd']:>+6.1f}% {r['sharpe']:>5.2f} {r['wins']:>2}/{r['n']}  [{g}] {ddd:+.1f}%")
print(f"\n  LOCKED: Ex={LOCKED['excess']:+.1f}% DD={LOCKED['dd']:.1f}% Sh={LOCKED['sharpe']:.2f}")

print(f"\n  IWM vol stats (shows when scaling fires):")
print(f"  IWM avg vol (2017-26): {iwm_vol63.mean()*100:.1f}%")
print(f"  Port vol proxy avg:    {iwm_vol63.mean()*BETA_EST*100:.1f}% (IWM*{BETA_EST})")
print(f"  Scale < 1.0 when proxy > target:")
for t in [0.25, 0.20, 0.15]:
    pct_scaled = (iwm_vol63 * BETA_EST > t).mean() * 100
    print(f"    target={int(t*100)}%: scaling fires {pct_scaled:.0f}% of trading days")

# Year-by-year
print(f"\n  YEAR-BY-YEAR EXCESS vs QQQ")
yrs = sorted(base['ye'].keys())
print("  " + " ".join(f"{y:>7}" for y in yrs))
for r in results:
    row = " ".join(f"{r['ye'].get(y,0):>+6.1f}%" for y in yrs)
    print(f"  {r['label'][:30]:<30} {row}")
