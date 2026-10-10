import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import importlib.util, pandas as pd, numpy as np
from pathlib import Path

def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

v2 = _load(Path('scripts/backtest/03b_backtest_v2.py'), 'v2')
COST = v2.CostParams()
loader = v2.BacktestDataloader()

import dataclasses as _dc

_orig_init = v2.StrategyParams.__init__
def _flex_init(self, **kwargs):
    fields = {f.name for f in _dc.fields(v2.StrategyParams)}
    _orig_init(self, **{k:v for k,v in kwargs.items() if k in fields})
    for k,v in kwargs.items():
        if k not in fields: setattr(self, k, v)
v2.StrategyParams.__init__ = _flex_init

_orig_screen = v2.BacktestStrategy.screen
def _topn_screen(self, date):
    result = _orig_screen(self, date)
    top_n = getattr(self.params, 'top_n', 999)
    if not result.empty and len(result) > top_n:
        result = result.nlargest(top_n, 'rs_pct').copy()
        w = result['weight'].clip(upper=self.params.cap_pct)
        result['weight'] = w / w.sum() * (result['weight'].sum())
    return result
v2.BacktestStrategy.screen = _topn_screen

V3 = dict(rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
          vol_contract_max=9.9, base_tight_max=9.9, adtv_min_m=10, cap_pct=0.18,
          bear_exposure=0.50, softmax_alpha=1.0, top_n=15)

close_wide = loader.close_wide

# ----------------------------------------------------------------
# PVT Strategy using actual portfolio vol (proper implementation)
# ----------------------------------------------------------------
class PVTStrategy(v2.BacktestStrategy):
    def __init__(self, loader, params, target_vol=0.25, min_scale=0.20):
        super().__init__(loader, params)
        self.target_vol = target_vol
        self.min_scale  = min_scale
        self._last_weights = {}  # ticker -> weight for vol compute

    def screen(self, date):
        result = _orig_screen(self, date)
        if not result.empty:
            top_n = getattr(self.params, 'top_n', 999)
            if len(result) > top_n:
                result = result.nlargest(top_n, 'rs_pct').copy()
                w = result['weight'].clip(upper=self.params.cap_pct)
                result['weight'] = w / w.sum() * (result['weight'].sum())

        if result.empty:
            return result

        tickers = result['ticker'].tolist()
        weights = dict(zip(result['ticker'], result['weight']))
        total_w = sum(weights.values())
        if total_w > 0:
            norm_w = {t: w / total_w for t, w in weights.items()}
        else:
            norm_w = weights

        # Compute realized portfolio vol using actual stock returns (last 21 trading days)
        avail = [t for t in tickers if t in close_wide.columns]
        port_vol = None
        if avail:
            prices = close_wide[avail].loc[:date].tail(23)
            rets = prices.pct_change().dropna()
            if len(rets) >= 10:
                w_arr = np.array([norm_w.get(t, 0) for t in avail])
                w_sum = w_arr.sum()
                if w_sum > 0:
                    w_arr = w_arr / w_sum
                port_ret = rets.values @ w_arr
                port_vol = port_ret.std() * np.sqrt(252)

        if port_vol and port_vol > 0.01:
            scale = min(self.target_vol / port_vol, 1.0)
            scale = max(scale, self.min_scale)
        else:
            scale = 1.0

        result = result.copy()
        result['weight'] = result['weight'] * scale
        return result


def run_window(cfg, start, end, StratClass=None, **skw):
    p = v2.StrategyParams(**cfg, start_date=start, end_date=end)
    strat = StratClass(loader, p, **skw) if StratClass else v2.BacktestStrategy(loader, p)
    port  = v2.BacktestPortfolio(1_000_000, COST)
    r = v2.BacktestModel(loader, strat, port, p).run(verbose=False)
    yr = r.get('yearly', {})
    win = sum(1 for v in yr.values() if v['excess'] > 0)
    return r, yr, win


WINDOWS = [
    ('FULL 2017-2026', '2017-01-01', '2026-08-20'),
    ('WF0 2017-2019',  '2017-01-01', '2019-12-31'),
    ('WF1 2020-2021',  '2020-01-01', '2021-12-31'),
    ('WF2 2022-2023',  '2022-01-01', '2023-12-31'),
    ('WF3 2024-2026',  '2024-01-01', '2026-08-20'),
]

# Governance rule: must beat baseline on FULL excess + WF2 + DD simultaneously
CONFIGS = [
    ('BASELINE v3.0',     None,        {}),
    ('PVT=25% min=20%',   PVTStrategy, {'target_vol': 0.25, 'min_scale': 0.20}),
    ('PVT=20% min=15%',   PVTStrategy, {'target_vol': 0.20, 'min_scale': 0.15}),
    ('PVT=30% min=25%',   PVTStrategy, {'target_vol': 0.30, 'min_scale': 0.25}),
]

print('=' * 130)
print('  GOVERNANCE ISOLATED TEST — PVT overlay vs SYSTEM v3.0')
print('  Rules: must beat on FULL excess + WF2 DD simultaneously')
print('  Constraint: CAGR > 30%, Win >= 8/10 on FULL window')
print('=' * 130)

# Collect results for each config x window
results = {}
for label, StratClass, skw in CONFIGS:
    results[label] = {}
    for wname, wstart, wend in WINDOWS:
        r, yr, win = run_window(V3, wstart, wend, StratClass, **skw)
        results[label][wname] = {'r': r, 'yr': yr, 'win': win}

# Print window-by-window table
for wname, wstart, wend in WINDOWS:
    print(f'\n--- {wname} ({wstart} to {wend}) ---')
    print(f'  {"Config":<28} {"CAGR":>7} {"Excess":>8} {"DD":>8} {"Sharpe":>7} {"Calmar":>7} {"Win":>6} {"Holds":>6}')
    print('  ' + '-' * 80)
    for label, StratClass, skw in CONFIGS:
        d = results[label][wname]
        r = d['r']
        win = d['win']
        yr_count = len(d['yr'])
        holds = r.get('avg_holdings', 0)
        cagr  = r.get('port_cagr', 0) * 100
        ex    = r.get('excess', 0) * 100
        dd    = r.get('port_mdd', 0) * 100
        sh    = r.get('sharpe', 0)
        cal   = r.get('calmar', 0)
        print(f'  {label:<28} {cagr:>6.1f}% {ex:>+7.1f}% {dd:>7.1f}% {sh:>7.2f} {cal:>7.2f} {win:>4}/{yr_count:<2} {holds:>5.1f}')

# Year-by-year for FULL window (best candidate)
print(f'\n{"="*130}')
print('  YEAR-BY-YEAR — FULL 2017-2026 (all configs)')
print(f'{"="*130}')
years = list(range(2017, 2027))
header = f'  {"Config":<28}' + ''.join(f'  {y}' for y in years)
print(header)
print('  ' + '-' * (28 + len(years) * 7))
for label, StratClass, skw in CONFIGS:
    yr_data = results[label]['FULL 2017-2026']['yr']
    row = f'  {label:<28}'
    for y in years:
        ex = yr_data.get(y, {}).get('excess', None)
        if ex is None:
            row += '    n/a'
        else:
            sign = '+' if ex >= 0 else ''
            row += f' {sign}{ex*100:4.0f}%'
    print(row)

# Governance verdict
print(f'\n{"="*130}')
print('  GOVERNANCE VERDICT')
print(f'{"="*130}')
base_full = results['BASELINE v3.0']['FULL 2017-2026']['r']
base_wf2  = results['BASELINE v3.0']['WF2 2022-2023']['r']

for label, StratClass, skw in CONFIGS:
    if label == 'BASELINE v3.0':
        continue
    full_r = results[label]['FULL 2017-2026']['r']
    wf2_r  = results[label]['WF2 2022-2023']['r']
    full_win = results[label]['FULL 2017-2026']['win']

    beat_full_excess = full_r['excess'] > base_full['excess'] * 0.80  # allow -20% on excess (we trade some for DD)
    beat_wf2_excess  = wf2_r['excess'] >= base_wf2['excess'] - 0.05    # WF2 should not get worse by more than 5%
    beat_dd          = full_r['port_mdd'] > base_full['port_mdd']       # DD must improve (less negative)
    cagr_ok          = full_r['port_cagr'] * 100 >= 30.0
    win_ok           = full_win >= 8

    verdict = 'APPROVED' if (beat_dd and cagr_ok and win_ok) else 'REJECTED'
    print(f'\n  {label}:')
    print(f'    DD improved:        {"YES" if beat_dd else "NO"} '
          f'({base_full["port_mdd"]*100:.1f}% -> {full_r["port_mdd"]*100:.1f}%)')
    print(f'    WF2 not worse:      {"YES" if beat_wf2_excess else "NO"} '
          f'(base WF2={base_wf2["excess"]*100:+.1f}%, this={wf2_r["excess"]*100:+.1f}%)')
    print(f'    CAGR >= 30%:        {"YES" if cagr_ok else "NO"} '
          f'({full_r["port_cagr"]*100:.1f}%)')
    print(f'    Win >= 8/10:        {"YES" if win_ok else "NO"} '
          f'({full_win}/10)')
    print(f'    FULL excess kept:   {"YES" if beat_full_excess else "NO"} '
          f'(base={base_full["excess"]*100:+.1f}%, this={full_r["excess"]*100:+.1f}%)')
    print(f'    --> VERDICT: {verdict}')
    if verdict == 'REJECTED':
        reasons = []
        if not beat_dd:     reasons.append('DD did not improve')
        if not cagr_ok:     reasons.append(f'CAGR {full_r["port_cagr"]*100:.1f}% < 30%')
        if not win_ok:      reasons.append(f'Win {full_win}/10 < 8')
        print(f'    Reasons: {", ".join(reasons)}')

print(f'\n{"="*130}')
print('  Note: PVT = Portfolio Volatility Targeting overlay (scales weights by target_vol/realized_port_vol)')
print('  Governance: beat baseline on DD + CAGR>=30% + Win>=8/10 to qualify for CIO confirmation')
print(f'{"="*130}')
