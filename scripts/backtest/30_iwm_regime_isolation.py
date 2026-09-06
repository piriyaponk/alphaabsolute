"""
30_iwm_regime_isolation.py — IWM Regime Filter Isolation Test

ISOLATION RULE: one change only vs v3.0 locked baseline.
Change: SPY > MA200  →  IWM > MA200 as regime filter.
All other v3.0 params unchanged.

v3.0 locked (CLAUDE.md):
  CAGR=57.0%, Excess=+35.8%, DD=-40.1%, Sharpe=1.44, Win=9/10
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd, numpy as np, importlib.util, pathlib

_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)

BASE_PARAMS = dict(
    rs_threshold=80,
    vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=9.9, base_tight_max=9.9,
    adtv_min_m=10, cap_pct=0.18,
    bear_exposure=0.50, bull_exposure=1.00,
    softmax_alpha=1.0,
    start_date='2017-01-01', end_date='2026-08-20'
)
TOP_N = 15

# v3.0 confirmed baseline (CLAUDE.md)
B_EXCESS = 35.8; B_DD = -40.1; B_SHARPE = 1.44; B_CAGR = 57.0

print("Loading data...")
dl  = v2.BacktestDataloader()
sig = dl.signals.copy()   # pristine copy for restore
sig['date'] = pd.to_datetime(sig['date'])

_orig_screen = v2.BacktestStrategy.screen

# ── Regime series ─────────────────────────────────────────────────────────────
def regime_series(ticker):
    rows = sig[sig['ticker'] == ticker][['date','close','ma_200']].dropna()
    rows = rows.copy(); rows['bull'] = rows['close'] > rows['ma_200']
    return rows.set_index('date')['bull']

spy_bull = regime_series('SPY')
iwm_bull = regime_series('IWM')

all_dates   = pd.DatetimeIndex(sorted(sig[sig['ticker']=='SPY']['date'].unique()))
rebal_dates = pd.DatetimeIndex(
    all_dates[all_dates.to_series().groupby(
        all_dates.to_period('M')).transform('max') == all_dates]
)
print(f"Rebalance dates: {len(rebal_dates)}")

def build_regime_map(use_iwm):
    src = iwm_bull if use_iwm else spy_bull
    return {dt: bool(src.get(dt, True)) for dt in rebal_dates}

def run_bt(label, use_iwm, s_date=None, e_date=None, verbose=True):
    regime_map = build_regime_map(use_iwm)

    # Patch SPY ma_200 in signals to force regime
    patched = sig.copy()
    for dt, is_bull in regime_map.items():
        mask = (patched['ticker'] == 'SPY') & (patched['date'] == dt)
        sc = patched.loc[mask, 'close'].values
        if len(sc):
            patched.loc[mask, 'ma_200'] = sc[0] * (0.99 if is_bull else 1.01)
    dl.signals = patched

    def capped_screen(self, date):
        result = _orig_screen(self, date)
        if result.empty: return result
        if len(result) > TOP_N:
            result = result.nlargest(TOP_N, 'rs_pct').copy()
            w = result['weight'].clip(upper=self.params.cap_pct)
            result['weight'] = w / w.sum() * result['weight'].sum()
        return result
    v2.BacktestStrategy.screen = capped_screen

    params = dict(**BASE_PARAMS)
    if s_date: params['start_date'] = s_date
    if e_date: params['end_date']   = e_date

    p     = v2.StrategyParams(**params)
    strat = v2.BacktestStrategy(dl, p)
    port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
    r     = v2.BacktestModel(dl, strat, port, p).run(verbose=False)

    dl.signals = sig.copy()
    v2.BacktestStrategy.screen = _orig_screen

    # Parse results
    ye   = {yr: (d['excess'] if isinstance(d, dict) else d)*100
            for yr, d in r.get('yearly', {}).items()}
    wins = sum(1 for v in ye.values() if v > 0)
    sc   = 100 if r['port_cagr'] < 5 else 1
    cagr = r['port_cagr'] * sc
    exc  = r['excess']    * sc
    dd   = r['port_mdd']  * sc
    sh   = r['sharpe']
    holds= r['avg_holdings']
    bear_mo = sum(1 for v in regime_map.values() if not v)

    yr_str = ' '.join(f"{yr}:{'+' if v>=0 else ''}{v:.1f}%" for yr, v in sorted(ye.items()))

    if verbose:
        print(f"\n{'='*65}")
        print(f"  {label}  [bear={bear_mo}mo]")
        print(f"  CAGR={cagr:.1f}% | Excess={exc:+.1f}% | DD={dd:.1f}%")
        print(f"  Sharpe={sh:.2f} | Holds={holds:.0f} | Win={wins}/{len(ye)}")
        print(f"  {yr_str}")

    return dict(cagr=cagr, excess=exc, dd=dd, sharpe=sh,
                wins=wins, n=len(ye), holds=holds, bear_mo=bear_mo, ye=ye)

# ── Run both configs full period ──────────────────────────────────────────────
print("\n" + "="*65)
print("ISOLATION TEST: SPY vs IWM as Regime Filter (v3.0 params)")
print("="*65)
print("\n--- FULL PERIOD 2017-2026 ---")
r_spy = run_bt("v3.0 BASELINE  (SPY > MA200)", use_iwm=False)
r_iwm = run_bt("v3.0 + IWM     (IWM > MA200)", use_iwm=True)

# ── WF windows ───────────────────────────────────────────────────────────────
WF = [('WF1 2020-21 COVID', '2020-01-01','2021-12-31'),
      ('WF2 2022-23 bear',  '2022-01-01','2023-12-31'),
      ('WF3 2024-26 bull',  '2024-01-01','2026-08-20')]

print("\n--- WALK-FORWARD WINDOWS ---")
wf_spy, wf_iwm = [], []
for name, s, e in WF:
    rs = run_bt(f"  SPY | {name}", False, s, e, verbose=False)
    ri = run_bt(f"  IWM | {name}", True,  s, e, verbose=False)
    wf_spy.append((name, rs)); wf_iwm.append((name, ri))
    w = 'IWM' if ri['excess'] > rs['excess'] else 'SPY'
    print(f"  {name:<22} SPY={rs['excess']:>+6.1f}%  IWM={ri['excess']:>+6.1f}%  -> {w}")

# ── Year-by-year ──────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("YEAR-BY-YEAR EXCESS vs QQQ")
print(f"  {'Year':<6}  {'SPY':>8}  {'IWM':>8}  {'Delta':>7}  Note")
print("-"*50)
for yr in sorted(set(r_spy['ye']) | set(r_iwm['ye'])):
    es = r_spy['ye'].get(yr, 0)
    ei = r_iwm['ye'].get(yr, 0)
    d  = ei - es
    note = ' [IWM+]' if d > 2 else (' [SPY+]' if d < -2 else '')
    print(f"  {yr}  {es:>+7.1f}%  {ei:>+7.1f}%  {d:>+6.1f}%{note}")

# ── Governance ────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("GOVERNANCE CHECK: IWM vs v3.0 LOCKED BASELINE")
print(f"  Metric      Baseline    IWM       Delta      Result")
print("-"*58)

ex_ok = r_iwm['excess'] > B_EXCESS
dd_ok = r_iwm['dd']     > B_DD
sh_ok = r_iwm['sharpe'] > B_SHARPE

checks = [
    ('Excess',  B_EXCESS,  r_iwm['excess'], ex_ok, '%'),
    ('Max DD',  B_DD,      r_iwm['dd'],     dd_ok, '%'),
    ('Sharpe',  B_SHARPE,  r_iwm['sharpe'], sh_ok, ''),
]
for name, base, val, ok, unit in checks:
    d = val - base
    print(f"  {name:<10}  {base:>+8.2f}{unit}  {val:>+8.2f}{unit}  {d:>+7.2f}{unit}  {'PASS' if ok else 'FAIL'}")

wf2_spy = wf_spy[1][1]['excess']
wf2_iwm = wf_iwm[1][1]['excess']
wf2_ok  = wf2_iwm >= wf2_spy * 0.8
print(f"  {'WF2 bear':<10}  {wf2_spy:>+8.1f}%  {wf2_iwm:>+8.1f}%  {wf2_iwm-wf2_spy:>+7.1f}%  {'OK' if wf2_ok else 'WARN'}")

print()
if ex_ok and dd_ok and sh_ok:
    verdict = 'STRICT PASS — all 3 metrics beat baseline'
elif ex_ok and dd_ok:
    verdict = 'PARTIAL — excess+DD pass, Sharpe miss'
elif dd_ok:
    verdict = 'DD only — excess miss'
else:
    verdict = 'FAIL — does not beat baseline'

print(f"  VERDICT: {verdict}")

print(f"\n{'='*65}")
if 'STRICT' in verdict:
    print("RECOMMENDATION: PROPOSE IWM as SYSTEM v4.0 regime filter")
    print("  -> Awaiting CIO confirmation before locking into CLAUDE.md")
elif 'FAIL' not in verdict:
    print("RECOMMENDATION: Partial improvement — further review needed")
else:
    print("RECOMMENDATION: Keep SPY as regime filter")
