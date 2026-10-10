"""Quick comparison: expanded universe with various vol_contract_max thresholds."""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd, numpy as np, importlib.util, pathlib

_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)

dl = v2.BacktestDataloader()

BASE = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=9.9, base_tight_max=9.9, adtv_min_m=10,
    cap_pct=0.18, bear_exposure=0.50, bull_exposure=1.00,
    softmax_alpha=1.0, start_date='2017-01-01', end_date='2026-08-20'
)

TOP_N = 15
_orig_screen = v2.BacktestStrategy.screen
def _topn_screen(self, date):
    result = _orig_screen(self, date)
    if not result.empty and len(result) > TOP_N:
        result = result.nlargest(TOP_N, 'rs_pct').copy()
        w = result['weight'].clip(upper=self.params.cap_pct)
        result['weight'] = w / w.sum() * result['weight'].sum()
    return result
v2.BacktestStrategy.screen = _topn_screen

def run(label, **kwargs):
    p = v2.StrategyParams(**{**BASE, **kwargs})
    strat = v2.BacktestStrategy(dl, p)
    port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
    r = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
    ye = {yr: d['excess'] for yr, d in r.get('yearly', {}).items()}
    wins = sum(1 for v in ye.values() if v > 0)
    # Detect decimal vs pct scale: if port_cagr < 5, assume decimal
    scale = 100 if r['port_cagr'] < 5 else 1
    pc = r['port_cagr'] * scale
    bc = r['bench_cagr'] * scale
    ex = r['excess'] * scale
    dd = r['port_mdd'] * scale
    sh = r['sharpe']
    ca = r['calmar']
    wf1 = sum(ye.get(yr,0) for yr in [2020,2021])/2 * scale
    wf2 = sum(ye.get(yr,0) for yr in [2022,2023])/2 * scale
    wf3 = sum(ye.get(yr,0) for yr in [2024,2025])/2 * scale
    yr_parts = [f"{yr}:{ye[yr]*scale:+.1f}%" for yr in sorted(ye)]
    print(f"\n{'='*62}")
    print(f"  {label}")
    print(f"  Port CAGR={pc:.1f}% | QQQ={bc:.1f}% | Excess={ex:+.1f}% | DD={dd:.1f}%")
    print(f"  Sharpe={sh:.2f} | Calmar={ca:.2f} | Holds={r['avg_holdings']:.0f} | Win={wins}/{len(ye)}")
    print(f"  WF1={wf1:+.1f}% | WF2={wf2:+.1f}% | WF3={wf3:+.1f}%")
    print(f"  {' | '.join(yr_parts)}")
    return {'label': label, 'excess': ex, 'dd': dd, 'sharpe': sh, 'wf2': wf2, 'wins': wins, 'n': len(ye), 'holds': r['avg_holdings']}

print("vol_contract_max sweep — expanded 1375-ticker universe")
print("v3.0 SP500 reference: Excess=+35.8%, DD=-40.1%, Sh=1.44, WF2=+37.9%, Win=9/10")

results = []
results.append(run("BASELINE (vol_contract=off, 9.9)"))
results.append(run("vol_contract_max=1.5", vol_contract_max=1.5))
results.append(run("vol_contract_max=1.3", vol_contract_max=1.3))
results.append(run("vol_contract_max=1.2", vol_contract_max=1.2))
results.append(run("vol_contract_max=1.5 + adtv>=15", vol_contract_max=1.5, adtv_min_m=15))
results.append(run("vol_contract_max=1.5 + rs=85", vol_contract_max=1.5, rs_threshold=85))

print(f"\n\n{'Config':<40} {'Excess':>8} {'DD':>8} {'Sharpe':>7} {'WF2':>8} {'Win':>6} {'Holds':>6}")
print("-"*80)
for r in results:
    print(f"{r['label']:<40} {r['excess']:>+7.1f}% {r['dd']:>7.1f}% {r['sharpe']:>7.2f} {r['wf2']:>+7.1f}% {r['wins']}/{r['n']} {r['holds']:>6.0f}")
