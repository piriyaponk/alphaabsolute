"""
Idea F — FIXED credit arm using Moody's BAA-AAA spread (free, 1919-present)
replaces BAMLC0A4CBBB/BAMLH0A0HYM2 (paywall since Aug 2023)

Credit proxy:
  baa_aaa  = BAA - AAA  (Moody's, monthly, 1919-present)  → corporate credit stress
  nfci     = NFCI       (weekly, 1971-present)             → broad financial conditions
  stlfsi   = STLFSI4    (weekly, 1993-present)             → financial stress index
  t10y2y   = T10Y2Y     (daily,  1976-present)             → yield curve (inverted = risk-off)

credit_score = 0.35×(baa_aaa_norm) + 0.25×(nfci_norm) + 0.20×(stlfsi_norm) + 0.20×(t10y2y_stress_norm)

Each component normalized to 0-1 via rolling 252d percentile rank on TRADING DAY timeline.
"""

import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import importlib.util, pandas as pd, numpy as np, requests, warnings
warnings.filterwarnings('ignore')
from pathlib import Path
from io import StringIO

try:
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
except Exception:
    pass

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
    for k, v in kwargs.items():
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

spy_px      = loader.get_benchmark_close('SPY')
trading_days = loader.close_wide.index

# ─── FETCH FRED ────────────────────────────────────────────────────────
SESSION = requests.Session()
SESSION.verify = False
SESSION.headers.update({'User-Agent': 'Mozilla/5.0'})

def fetch_fred(series_id, label=''):
    url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}'
    try:
        resp = SESSION.get(url, timeout=20)
        if resp.status_code == 200:
            df = pd.read_csv(StringIO(resp.text))
            df.columns = ['date', 'value']
            df['date']  = pd.to_datetime(df['date'])
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
            s = df.dropna().set_index('date')['value']
            print(f'  {label or series_id}: {len(s)} rows, {s.index[0].date()} to {s.index[-1].date()}')
            return s
    except Exception as e:
        print(f'  FAILED {series_id}: {e}')
    return pd.Series(dtype=float)

FRED_CACHE = Path('data/backtest/fred_cache')
FRED_CACHE.mkdir(parents=True, exist_ok=True)

def fetch_fred_cached(series_id, label=''):
    cache_file = FRED_CACHE / f'{series_id}.csv'
    if cache_file.exists():
        df = pd.read_csv(cache_file)
        df['date'] = pd.to_datetime(df['date'])
        df['value'] = pd.to_numeric(df['value'], errors='coerce')
        s = df.dropna().set_index('date')['value']
        print(f'  {label or series_id}: {len(s)} rows (cached)')
        return s
    # Fetch from FRED
    url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}'
    for attempt in range(3):
        try:
            resp = SESSION.get(url, timeout=30)
            if resp.status_code == 200:
                df = pd.read_csv(StringIO(resp.text))
                df.columns = ['date', 'value']
                df['date']  = pd.to_datetime(df['date'])
                df['value'] = pd.to_numeric(df['value'], errors='coerce')
                s = df.dropna().set_index('date')['value']
                df.to_csv(cache_file, index=False)
                print(f'  {label or series_id}: {len(s)} rows, {s.index[0].date()} to {s.index[-1].date()}')
                return s
        except Exception as e:
            print(f'  {series_id} attempt {attempt+1}: {str(e)[:60]}')
            import time; time.sleep(2)
    print(f'  {series_id}: FAILED after 3 attempts')
    return pd.Series(dtype=float)

print('Fetching FRED data (using Moody BAA/AAA as credit proxy)...')
baa    = fetch_fred_cached('BAA',    'Moodys BAA')
aaa    = fetch_fred_cached('AAA',    'Moodys AAA')
nfci   = fetch_fred_cached('NFCI',   'NFCI')
stlfsi = fetch_fred_cached('STLFSI4','STLFSI')
t10y2y = fetch_fred_cached('T10Y2Y', 'T10Y2Y')

# Build BAA-AAA spread (monthly → forward-fill to daily)
if baa.empty or aaa.empty:
    print('\nBAA or AAA data unavailable from FRED. Cannot build credit arm. Exiting.')
    sys.exit(1)

baa_aaa_raw = (baa - aaa).dropna()
print(f'\n  BAA-AAA spread: min={baa_aaa_raw.min():.2f}% max={baa_aaa_raw.max():.2f}% mean={baa_aaa_raw.mean():.2f}%')

# Key crisis check
for d, label in [('2018-10', 'Q4-2018-Oct'), ('2018-12', 'Q4-2018-Dec'),
                 ('2020-03', 'COVID-Mar'),   ('2020-04', 'COVID-Apr'),
                 ('2022-06', '2022-Jun')]:
    dt = pd.Timestamp(d)
    row = baa_aaa_raw[baa_aaa_raw.index <= dt]
    if len(row): print(f'    {label}: BAA-AAA={row.iloc[-1]:.3f}%')

# ─── ALIGN ALL SERIES TO TRADING DAYS ──────────────────────────────────
def align_to_trading(s, trading_days, method='ffill'):
    """Reindex to trading days, forward-fill from prior available data."""
    s_daily = s.reindex(trading_days, method=method)
    return s_daily

baa_aaa  = align_to_trading(baa_aaa_raw, trading_days)
nfci_td  = align_to_trading(nfci,        trading_days)
stlfsi_td= align_to_trading(stlfsi,      trading_days)
t10y2y_td= align_to_trading(t10y2y,      trading_days)

# ─── ROLLING PERCENTILE NORMALIZATION (lookback=252d, no lookahead) ────
def rolling_pct_rank(s, window=252):
    """0=best (tight spreads / loose conditions) → 1=worst (stress)."""
    return s.rolling(window, min_periods=60).apply(
        lambda x: (x[-1] > x[:-1]).mean() if len(x) > 1 else 0.5,
        raw=True
    )

print('\nComputing rolling percentile ranks...')
baa_norm    = rolling_pct_rank(baa_aaa_td)   # high spread = stress = high pct
nfci_norm   = rolling_pct_rank(nfci_td)      # high NFCI = stress
stlfsi_norm = rolling_pct_rank(stlfsi_td)    # high STLFSI = stress
# T10Y2Y: inverted (low/negative) = stress → use 1 - rank
t10y2y_norm = 1 - rolling_pct_rank(t10y2y_td)  # inverted curve = high stress score

credit_score = (
    0.35 * baa_norm +
    0.25 * nfci_norm +
    0.20 * stlfsi_norm +
    0.20 * t10y2y_norm
).clip(0, 1)

# ─── SPY MARKET SCORE (from 13_ideas_def_test.py) ──────────────────────
def make_market_score(spy_px):
    vol_21  = spy_px.pct_change().rolling(21).std() * np.sqrt(252)
    vol_63  = spy_px.pct_change().rolling(63).std() * np.sqrt(252)
    vol_ratio = (vol_21 / vol_63).clip(0.5, 4.0)
    ma200 = spy_px.rolling(200).mean()

    def vol_to_score(vr):
        s = np.zeros_like(vr.values)
        s[vr >= 2.0] = 1.0
        s[(vr >= 1.5) & (vr < 2.0)] = 0.67
        s[(vr >= 1.2) & (vr < 1.5)] = 0.33
        return pd.Series(s, index=vr.index)

    vol_score   = vol_to_score(vol_ratio)
    trend_score = (spy_px < ma200).astype(float)
    market_score = 0.40 * vol_score + 0.60 * trend_score
    return market_score.clip(0, 1), vol_ratio

market_score, vol_ratio = make_market_score(spy_px)

# ─── 4-STATE HYSTERESIS (reuse from 13) ────────────────────────────────
def run_hysteresis(score, enter=(0.25, 0.45, 0.70), exit_thr=(0.15, 0.35, 0.60),
                   confirm_days=10, exposure_map=(1.00, 0.85, 0.65, 0.45)):
    vals = score.to_numpy()
    n = len(vals)
    exposures = np.empty(n)
    states    = np.empty(n, dtype=int)
    state = 0; exit_counter = 0
    for i in range(n):
        s = vals[i]
        if np.isnan(s):
            states[i] = state; exposures[i] = exposure_map[state]; continue
        if   s >= enter[2]: target = 3
        elif s >= enter[1]: target = 2
        elif s >= enter[0]: target = 1
        else:               target = 0
        if target > state:
            state = target; exit_counter = 0
        elif target < state:
            if s < exit_thr[state - 1]:
                exit_counter += 1
                if exit_counter >= confirm_days:
                    state -= 1; exit_counter = 0
            else:
                exit_counter = 0
        states[i] = state; exposures[i] = exposure_map[state]
    return pd.Series(exposures, index=score.index), pd.Series(states, index=score.index)

# ─── VALIDATE CREDIT SIGNAL ON CRISIS DATES ────────────────────────────
print('\n' + '='*80)
print('CREDIT SIGNAL VALIDATION (BAA-AAA based)')
print('='*80)
print(f'{"Date":<14} {"BAA-AAA":>9} {"BAA_norm":>9} {"NFCI_n":>8} {"CreditSc":>9} {"MktSc":>7}')
for d in ['2018-09-28','2018-10-31','2018-11-30','2018-12-31','2019-01-31',
          '2020-02-28','2020-03-31','2020-04-30','2021-12-31',
          '2022-01-31','2022-06-30','2022-09-30']:
    dt = pd.Timestamp(d)
    td = trading_days[trading_days <= dt][-1] if len(trading_days[trading_days <= dt]) > 0 else None
    if td is None: continue
    print(f'{str(td.date()):<14} {baa_aaa[td]:>9.3f} {baa_norm[td]:>9.3f} '
          f'{nfci_norm[td]:>8.3f} {credit_score[td]:>9.3f} {market_score[td]:>7.3f}')

# ─── PRE-COMPUTE MARKET AND CREDIT EXPOSURE SERIES ─────────────────────
def make_recovery_signal(spy_px, vol_ratio, off_low_thr=0.15, vol_rebuy_thr=1.4):
    ma50    = spy_px.rolling(50).mean()
    low_52w = spy_px.rolling(252).min()
    ma50_15 = spy_px.rolling(50).mean().shift(15)
    ma50_rising = ma50 > ma50_15
    recovery = (
        (spy_px > ma50) &
        (spy_px > low_52w * (1 + off_low_thr)) &
        ma50_rising &
        (vol_ratio < vol_rebuy_thr)
    ).astype(float)
    return recovery

mkt_exp_series, mkt_state_series = run_hysteresis(
    market_score,
    enter=(0.25, 0.45, 0.70), exit_thr=(0.15, 0.35, 0.60),
    confirm_days=10, exposure_map=(1.00, 0.85, 0.65, 0.45)
)
crd_exp_series, crd_state_series = run_hysteresis(
    credit_score,
    enter=(0.35, 0.55, 0.75), exit_thr=(0.25, 0.45, 0.65),
    confirm_days=5, exposure_map=(1.00, 0.80, 0.55, 0.35)
)
recovery_series = make_recovery_signal(spy_px, vol_ratio, off_low_thr=0.15)

# combined: min of market & credit (most conservative arm wins)
combined_exp = pd.DataFrame({'m': mkt_exp_series, 'c': crd_exp_series}).min(axis=1)
combined_exp_rec = combined_exp.copy()
combined_exp_rec[recovery_series == 1] = combined_exp_rec[recovery_series == 1].clip(lower=1.0)

print('\nExposure check on crisis dates:')
print(f'{"Date":<14} {"Mkt_exp":>8} {"Crd_exp":>8} {"Comb_exp":>9} {"Rec":>5} {"CombRec":>9}')
for d in ['2018-09-28','2018-10-31','2018-11-30','2018-12-31','2019-01-31',
          '2020-02-28','2020-03-31','2020-04-30','2022-01-31','2022-06-30']:
    dt = pd.Timestamp(d)
    td = trading_days[trading_days <= dt][-1] if len(trading_days[trading_days <= dt]) > 0 else None
    if td is None: continue
    print(f'{str(td.date()):<14} {mkt_exp_series[td]:>8.2f} {crd_exp_series[td]:>8.2f} '
          f'{combined_exp[td]:>9.2f} {int(recovery_series[td]):>5} {combined_exp_rec[td]:>9.2f}')


# ─── STRATEGY CLASS ─────────────────────────────────────────────────────
class CreditFixedStrategy(v2.BacktestStrategy):
    def __init__(self, params, cost_params, use_recovery=True, mode='min'):
        super().__init__(params, cost_params)
        self._mode = mode
        self._use_rec = use_recovery
        self._exp = combined_exp_rec if use_recovery else combined_exp

    def get_market_exposure(self, date):
        closest = trading_days[trading_days <= date]
        if len(closest) == 0: return 1.0
        td = closest[-1]
        exp = float(self._exp.get(td, 1.0))
        # SYSTEM v3 bear floor: if SPY<MA200 never go above bear_exposure
        ma200 = float(spy_px.rolling(200).mean().get(td, spy_px.iloc[-1]))
        spy_close = float(spy_px.get(td, spy_px.iloc[-1]))
        if spy_close < ma200:
            bear_floor = self.params.bear_exposure
            exp = min(exp, bear_floor)
        return exp

    def screen(self, date):
        result = v2.BacktestStrategy.screen(self, date)
        top_n = getattr(self.params, 'top_n', 999)
        if not result.empty and len(result) > top_n:
            result = result.nlargest(top_n, 'rs_pct').copy()
            w = result['weight'].clip(upper=self.params.cap_pct)
            result['weight'] = w / w.sum() * (result['weight'].sum())
        if result.empty: return result
        exp = self.get_market_exposure(date)
        result = result.copy()
        result['weight'] *= exp
        return result


# ─── BENCHMARK RUN ───────────────────────────────────────────────────────
def run_v3():
    params = v2.StrategyParams(**V3)
    strat  = v2.BacktestStrategy(params, COST)
    model  = v2.BacktestModel(strat, loader)
    return model.run(pd.Timestamp('2017-01-01'), pd.Timestamp('2026-08-20'))

def run_credit_fixed(use_recovery=True, mode='min'):
    params = v2.StrategyParams(**V3)
    strat  = CreditFixedStrategy(params, COST, use_recovery=use_recovery, mode=mode)
    model  = v2.BacktestModel(strat, loader)
    return model.run(pd.Timestamp('2017-01-01'), pd.Timestamp('2026-08-20'))

# walk-forward windows
WF_WINDOWS = {
    'WF0 (2017-19)': (pd.Timestamp('2017-01-01'), pd.Timestamp('2019-12-31')),
    'WF1 (2020-21)': (pd.Timestamp('2020-01-01'), pd.Timestamp('2021-12-31')),
    'WF2 (2022-23)': (pd.Timestamp('2022-01-01'), pd.Timestamp('2023-12-31')),
    'WF3 (2024-26)': (pd.Timestamp('2024-01-01'), pd.Timestamp('2026-08-20')),
}

def eval_result(port_nav, bench_nav, label=''):
    from dateutil.relativedelta import relativedelta
    import warnings; warnings.filterwarnings('ignore')
    yrs = (port_nav.index[-1] - port_nav.index[0]).days / 365.25
    if yrs <= 0: return {}
    port_ret = port_nav.pct_change().dropna()
    bench_ret = bench_nav.reindex(port_nav.index).pct_change().dropna()
    common = port_ret.index.intersection(bench_ret.index)
    pr, br = port_ret.loc[common], bench_ret.loc[common]
    cagr  = (port_nav.iloc[-1] / port_nav.iloc[0]) ** (1/yrs) - 1
    bcagr = (bench_nav.reindex(port_nav.index).iloc[-1] / bench_nav.reindex(port_nav.index).iloc[0]) ** (1/yrs) - 1
    excess = cagr - bcagr
    roll_max = port_nav.cummax()
    dd  = ((port_nav / roll_max) - 1).min()
    sharpe = pr.mean() / pr.std() * np.sqrt(252) if pr.std() > 0 else 0
    return dict(cagr=cagr, excess=excess, dd=dd, sharpe=sharpe)

def yearly_excess(port_nav, bench_nav_full):
    years = range(port_nav.index[0].year, port_nav.index[-1].year + 1)
    results = {}
    bench = bench_nav_full.reindex(port_nav.index, method='ffill')
    for yr in years:
        p = port_nav[port_nav.index.year == yr]
        b = bench[bench.index.year == yr]
        if len(p) < 10: continue
        pr = (p.iloc[-1]/p.iloc[0]) - 1
        br = (b.iloc[-1]/b.iloc[0]) - 1
        results[yr] = pr - br
    return results

def summarize(label, port_nav, bench_nav_full):
    m = eval_result(port_nav, bench_nav_full, label)
    ye = yearly_excess(port_nav, bench_nav_full)
    wins = sum(1 for v in ye.values() if v > 0)
    ystr = '  '.join(f'{v*100:+.0f}%' for v in ye.values())
    print(f'  {label:<50} CAGR={m["cagr"]*100:>5.1f}%  Ex={m["excess"]*100:>+6.1f}%  '
          f'DD={m["dd"]*100:>6.1f}%  Sh={m["sharpe"]:.2f}  Win={wins}/{len(ye)}  | {ystr}')
    return m

print('\n' + '='*100)
print('  IDEA F FIXED (Moody BAA-AAA credit arm) vs SYSTEM v3.0 — 0.15% cost, 2017-2026')
print('='*100)

v3_nav   = run_v3()
bench_nav = loader.get_benchmark_nav('QQQ', v3_nav.index[0], v3_nav.index[-1])

print(f'  {"Config":<50} {"CAGR":>6}  {"Excess":>7}  {"DD":>7}  {"Sharpe":>6}  {"Win":>5}  | Years')
print('  ' + '-'*115)
summarize('BASELINE v3.0', v3_nav, bench_nav)
print('  ' + '-'*115)

configs = [
    ('F-fixed: market-only (Idea D)', False, False),
    ('F-fixed: credit+market min, no recovery', False, True),
    ('F-fixed: credit+market min + recovery', True,  True),
]

# Also run market-only reference (no credit)
class MarketOnlyStrategy(v2.BacktestStrategy):
    def get_market_exposure(self, date):
        closest = trading_days[trading_days <= date]
        if len(closest) == 0: return 1.0
        td = closest[-1]
        exp = float(mkt_exp_series.get(td, 1.0))
        ma200 = float(spy_px.rolling(200).mean().get(td, spy_px.iloc[-1]))
        spy_close = float(spy_px.get(td, spy_px.iloc[-1]))
        if spy_close < ma200:
            exp = min(exp, self.params.bear_exposure)
        return exp
    def screen(self, date):
        result = v2.BacktestStrategy.screen(self, date)
        top_n = getattr(self.params, 'top_n', 999)
        if not result.empty and len(result) > top_n:
            result = result.nlargest(top_n, 'rs_pct').copy()
            w = result['weight'].clip(upper=self.params.cap_pct)
            result['weight'] = w / w.sum() * (result['weight'].sum())
        if result.empty: return result
        result = result.copy()
        result['weight'] *= self.get_market_exposure(date)
        return result

class MarketRecoveryStrategy(MarketOnlyStrategy):
    def get_market_exposure(self, date):
        closest = trading_days[trading_days <= date]
        if len(closest) == 0: return 1.0
        td = closest[-1]
        exp = float(mkt_exp_series.get(td, 1.0))
        rec = float(recovery_series.get(td, 0.0))
        if rec: exp = max(exp, 1.0)
        ma200 = float(spy_px.rolling(200).mean().get(td, spy_px.iloc[-1]))
        spy_close = float(spy_px.get(td, spy_px.iloc[-1]))
        if spy_close < ma200:
            exp = min(exp, self.params.bear_exposure)
        return exp

def run_strat(strat_cls, **kwargs):
    params = v2.StrategyParams(**V3)
    strat  = strat_cls(params, COST, **kwargs)
    model  = v2.BacktestModel(strat, loader)
    return model.run(pd.Timestamp('2017-01-01'), pd.Timestamp('2026-08-20'))

mkt_nav     = run_strat(MarketOnlyStrategy)
mkt_rec_nav = run_strat(MarketRecoveryStrategy)
crd_nav     = run_credit_fixed(use_recovery=False)
crd_rec_nav = run_credit_fixed(use_recovery=True)

summarize('F: market hysteresis only (no recovery)', mkt_nav, bench_nav)
summarize('F: market hysteresis + recovery', mkt_rec_nav, bench_nav)
summarize('F: credit+market min (no recovery)', crd_nav, bench_nav)
summarize('F: credit+market min + recovery (FULL)', crd_rec_nav, bench_nav)

print('\n' + '='*100)
print('  WALK-FORWARD BREAKDOWN')
print('='*100)
for wf_name, (wf_start, wf_end) in WF_WINDOWS.items():
    v3_wf  = v3_nav[(v3_nav.index >= wf_start) & (v3_nav.index <= wf_end)]
    fn_wf  = crd_rec_nav[(crd_rec_nav.index >= wf_start) & (crd_rec_nav.index <= wf_end)]
    bch_wf = bench_nav[(bench_nav.index >= wf_start) & (bench_nav.index <= wf_end)]
    if len(v3_wf) < 20 or len(fn_wf) < 20: continue
    m_v3 = eval_result(v3_wf, bch_wf)
    m_fn = eval_result(fn_wf, bch_wf)
    print(f'  {wf_name}:')
    print(f'    v3.0:   Excess={m_v3["excess"]*100:>+.1f}%  DD={m_v3["dd"]*100:>+.1f}%  Sh={m_v3["sharpe"]:.2f}')
    print(f'    F-fix:  Excess={m_fn["excess"]*100:>+.1f}%  DD={m_fn["dd"]*100:>+.1f}%  Sh={m_fn["sharpe"]:.2f}')

print('\n' + '='*100)
print('  GOVERNANCE VERDICT')
print('='*100)
m_v3 = eval_result(v3_nav, bench_nav)
m_fn = eval_result(crd_rec_nav, bench_nav)
beat_excess = m_fn['excess'] > m_v3['excess']
beat_dd     = m_fn['dd']     > m_v3['dd']
beat_sharpe = m_fn['sharpe'] > m_v3['sharpe']
cagr_ok     = m_fn['cagr']   > 0.30

verdict = 'APPROVED' if (beat_excess or beat_dd) and cagr_ok else 'REJECTED'
print(f'  Excess:  v3={m_v3["excess"]*100:+.1f}%  F-fix={m_fn["excess"]*100:+.1f}%  {"BETTER" if beat_excess else "WORSE"}')
print(f'  DD:      v3={m_v3["dd"]*100:.1f}%    F-fix={m_fn["dd"]*100:.1f}%    {"BETTER" if beat_dd else "WORSE"}')
print(f'  Sharpe:  v3={m_v3["sharpe"]:.2f}      F-fix={m_fn["sharpe"]:.2f}       {"BETTER" if beat_sharpe else "WORSE"}')
print(f'\n  VERDICT: {verdict}')
print('='*100)
