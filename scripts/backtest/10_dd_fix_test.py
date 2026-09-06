"""
Isolated test: Portfolio Vol Targeting + HY Spread + Bond Yield Vol gate
Fixes:
  - screen() returns RangeIndex, tickers in 'ticker' column
  - HY spread reindex using merge on trading dates
  - Add Bond Yield Vol (10Y MOVE proxy) — user suggestion
Key insight: Q4 2018 crash (Sep 11 - Dec 24) happens BETWEEN monthly rebalances.
Any monthly gate only protects the NEXT rebalance after the crash, not the crash itself.
=> Need signals that are LEADING, not coincident.
"""
import sys, io, os, ssl, requests, warnings
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')
ssl._create_default_https_context = ssl._create_unverified_context
warnings.filterwarnings('ignore')

import importlib.util, pandas as pd, numpy as np, dataclasses as _dc
from pathlib import Path

def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

v2     = _load(Path('scripts/backtest/03b_backtest_v2.py'), 'v2')
COST   = v2.CostParams()
loader = v2.BacktestDataloader()

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
trading_days = pd.DatetimeIndex(close_wide.index)

def fetch_fred(series_id, col_name):
    """Fetch FRED daily series, forward-fill to trading days."""
    url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}'
    raw = requests.get(url, verify=False, timeout=30).text.strip().split('\n')
    df  = pd.DataFrame([r.split(',') for r in raw[1:]], columns=['date', col_name])
    df['date'] = pd.to_datetime(df['date'])
    df[col_name] = pd.to_numeric(df[col_name], errors='coerce')
    s = df.dropna().set_index('date')[col_name]
    # reindex: combine trading days with FRED dates, ffill
    all_dates = trading_days.union(s.index).sort_values()
    s = s.reindex(all_dates).ffill()
    return s.reindex(trading_days)

print('Fetching macro data from FRED...', flush=True)
hy_spread = fetch_fred('BAMLH0A0HYM2', 'hy')   # HY OAS spread %
yield_10y = fetch_fred('DGS10', 'y10')          # 10Y Treasury yield %
print(f'  HY spread: latest={hy_spread.iloc[-1]:.2f}%, Q4-2018-Dec={hy_spread.get(pd.Timestamp("2018-12-28"), "N/A")}')
print(f'  10Y yield: latest={yield_10y.iloc[-1]:.2f}%, Q4-2018-Dec={yield_10y.get(pd.Timestamp("2018-12-28"), "N/A")}')

# Derived signals
hy_chg_20d  = hy_spread - hy_spread.shift(20)   # 20-day change in HY spread (stress rising)
y10_chg_20d = yield_10y - yield_10y.shift(20)   # 20-day change in 10Y yield
# Negative y10_chg = yields falling = flight to safety (risk-off signal)
y10_vol_20d = yield_10y.diff().rolling(20).std() * np.sqrt(252)  # annualized yield vol

# Portfolio vol helper
log_ret = np.log(close_wide / close_wide.shift(1))

def port_vol(result_df, date, lookback=20):
    """Compute realized portfolio vol using actual weights and stock returns."""
    tickers = result_df['ticker'].tolist()
    weights = dict(zip(result_df['ticker'], result_df['weight']))
    try:
        idx_pos = close_wide.index.get_loc(date)
        if idx_pos < lookback + 1:
            return None
        ret_slice = log_ret.iloc[idx_pos - lookback: idx_pos][tickers].dropna(axis=1)
        if ret_slice.empty or len(ret_slice) < 5:
            return None
        valid = ret_slice.columns.tolist()
        w = np.array([weights.get(t, 0) for t in valid], dtype=float)
        if w.sum() == 0:
            return None
        w /= w.sum()
        port_ret = (ret_slice.values * w).sum(axis=1)
        return port_ret.std() * np.sqrt(252)
    except Exception:
        return None

# Verify signals on Q4 2018 rebal dates
p0 = v2.StrategyParams(**V3, start_date='2018-01-01', end_date='2019-03-31')
rebal_2018 = loader.get_rebal_dates(p0)
print('\nSignal values on 2018-2019 rebal dates:')
print(f'  {"Date":<14} {"HY%":>6}  {"HY_chg":>8}  {"10Y%":>6}  {"10Y_chg":>8}  {"10Y_vol":>8}')
for d in rebal_2018:
    hy  = hy_spread.get(d, float('nan'))
    hyc = hy_chg_20d.get(d, float('nan'))
    y   = yield_10y.get(d, float('nan'))
    yc  = y10_chg_20d.get(d, float('nan'))
    yv  = y10_vol_20d.get(d, float('nan'))
    print(f'  {str(d.date()):<14} {hy:6.2f}%  {hyc:+8.2f}%  {y:6.2f}%  {yc:+8.2f}%  {yv:8.2f}')
print()


def run_strat(StratClass, **kwargs):
    p = v2.StrategyParams(**V3, start_date='2017-01-01', end_date='2026-08-20')
    strat = StratClass(loader, p, **kwargs)
    port  = v2.BacktestPortfolio(1_000_000, COST)
    if hasattr(strat, '_set_portfolio'):
        strat._set_portfolio(port)
    r  = v2.BacktestModel(loader, strat, port, p).run(verbose=False)
    yr = r.get('yearly', {})
    win = sum(1 for v in yr.values() if v['excess'] > 0)
    return r, yr, win

def fmt(label, r, yr, win, show_years=False):
    y    = {k: v['excess']*100 for k,v in yr.items()}
    ywdd = min(v['dd'] for v in yr.values())*100 if yr else 0
    flag = ''
    if r['port_mdd']*100 > -30: flag = ' <<< DD<30%!'
    elif r['port_mdd']*100 > -35: flag = ' << DD<35%'
    print(f'  {label:<50} CAGR={r["port_cagr"]*100:5.1f}%  Ex={r["excess"]*100:+6.1f}%  '
          f'DD={r["port_mdd"]*100:6.1f}%  Sh={r["sharpe"]:.2f}  Cal={r["calmar"]:.2f}  '
          f'Win={win}/10  WorstYrDD={ywdd:.0f}%{flag}')
    if show_years:
        print('    ' + '  '.join(f'{y}: {y.get(yr_,0):+.0f}%' for yr_ in range(2017,2027)
                                  for y in [y]))


print('='*115)
print('  ISOLATED TEST v2 — all signals verified on actual rebal dates')
print('='*115)

r0, yr0, w0 = run_strat(v2.BacktestStrategy)
fmt('BASELINE v3.0', r0, yr0, w0)
print('-'*115)


# ================================================================
# A: Portfolio vol targeting — FIXED (use result['ticker'])
# ================================================================
print('\n[A] Portfolio vol targeting — proper implementation (target_vol / realized_port_vol)')

class PVTStrategy(v2.BacktestStrategy):
    def __init__(self, loader, params, target_vol=0.20, min_scale=0.20):
        super().__init__(loader, params)
        self.target_vol = target_vol
        self.min_scale  = min_scale

    def screen(self, date):
        result = super().screen(date)
        if result.empty:
            return result
        pvol = port_vol(result, date, lookback=20)
        if pvol and pvol > 0.01:
            scale = min(self.target_vol / pvol, 1.0)
            scale = max(scale, self.min_scale)
            result = result.copy()
            result['weight'] = result['weight'] * scale
        return result

for tv, ms in [(0.15, 0.10), (0.20, 0.15), (0.25, 0.20), (0.30, 0.25)]:
    r, yr, win = run_strat(PVTStrategy, target_vol=tv, min_scale=ms)
    fmt(f'PVT target={tv:.0%} min_scale={ms:.0%}', r, yr, win)


# ================================================================
# B: HY Spread gate — FIXED (use trading-day aligned series)
# ================================================================
print('\n[B] HY spread gate — level + 20d momentum')

class HYStrategy(v2.BacktestStrategy):
    def __init__(self, loader, params, hy_level=5.0, hy_chg=1.5, scale=0.30):
        super().__init__(loader, params)
        self.hy_level = hy_level
        self.hy_chg   = hy_chg
        self.scale    = scale

    def screen(self, date):
        result = super().screen(date)
        if result.empty:
            return result
        lvl = hy_spread.get(date, float('nan'))
        chg = hy_chg_20d.get(date, float('nan'))
        if (not np.isnan(lvl) and lvl > self.hy_level) or \
           (not np.isnan(chg) and chg > self.hy_chg):
            result = result.copy()
            result['weight'] = result['weight'] * self.scale
        return result

for lvl, chg, sc in [(5.0, 1.5, 0.30), (4.5, 1.0, 0.30), (4.0, 0.8, 0.30),
                     (5.0, 1.5, 0.20), (4.5, 1.0, 0.20)]:
    r, yr, win = run_strat(HYStrategy, hy_level=lvl, hy_chg=chg, scale=sc)
    fmt(f'HY>{lvl:.1f}% or chg>{chg:.1f}% -> {sc:.0%}', r, yr, win)


# ================================================================
# C: Bond Yield Vol gate (user suggestion)
# ================================================================
print('\n[C] Bond yield vol gate (10Y yield realized vol + direction signal)')
print('    Idea: rising yield vol = macro uncertainty -> reduce equity exposure')
print('    Risk-off signal: 10Y yield falling fast (flight to safety)')

class YieldVolStrategy(v2.BacktestStrategy):
    def __init__(self, loader, params, yv_thresh=1.5, yc_thresh=-0.40, scale=0.30):
        super().__init__(loader, params)
        self.yv_thresh = yv_thresh   # annualized yield vol > X = stressed
        self.yc_thresh = yc_thresh   # 20d yield change < X% = flight to safety
        self.scale     = scale

    def screen(self, date):
        result = super().screen(date)
        if result.empty:
            return result
        yv = y10_vol_20d.get(date, float('nan'))
        yc = y10_chg_20d.get(date, float('nan'))
        stressed = (not np.isnan(yv) and yv > self.yv_thresh) or \
                   (not np.isnan(yc) and yc < self.yc_thresh)
        if stressed:
            result = result.copy()
            result['weight'] = result['weight'] * self.scale
        return result

for yv, yc, sc in [(1.5, -0.40, 0.30), (1.5, -0.40, 0.20),
                   (2.0, -0.50, 0.30), (1.0, -0.30, 0.30),
                   (1.5, -0.30, 0.25)]:
    r, yr, win = run_strat(YieldVolStrategy, yv_thresh=yv, yc_thresh=yc, scale=sc)
    fmt(f'YieldVol>{yv:.1f} or 10Y_chg<{yc:.2f}% -> {sc:.0%}', r, yr, win)


# ================================================================
# D: COMBO — PVT + best macro signal
# ================================================================
print('\n[D] COMBO: Portfolio vol targeting + best macro gate')

class ComboStrategy(v2.BacktestStrategy):
    def __init__(self, loader, params,
                 target_vol=0.25, min_scale=0.20,
                 hy_level=5.0, hy_chg=1.5,
                 yv_thresh=1.5, yc_thresh=-0.40,
                 stress_scale=0.25):
        super().__init__(loader, params)
        self.target_vol  = target_vol
        self.min_scale   = min_scale
        self.hy_level    = hy_level
        self.hy_chg      = hy_chg
        self.yv_thresh   = yv_thresh
        self.yc_thresh   = yc_thresh
        self.stress_scale = stress_scale

    def screen(self, date):
        result = super().screen(date)
        if result.empty:
            return result

        # Macro stress check
        lvl = hy_spread.get(date, float('nan'))
        chg = hy_chg_20d.get(date, float('nan'))
        yv  = y10_vol_20d.get(date, float('nan'))
        yc  = y10_chg_20d.get(date, float('nan'))
        macro_stressed = (
            (not np.isnan(lvl) and lvl > self.hy_level) or
            (not np.isnan(chg) and chg > self.hy_chg) or
            (not np.isnan(yv)  and yv  > self.yv_thresh) or
            (not np.isnan(yc)  and yc  < self.yc_thresh)
        )
        if macro_stressed:
            result = result.copy()
            result['weight'] = result['weight'] * self.stress_scale
            return result

        # Vol targeting in normal environment
        pvol = port_vol(result, date, lookback=20)
        if pvol and pvol > 0.01:
            scale = min(self.target_vol / pvol, 1.0)
            scale = max(scale, self.min_scale)
            result = result.copy()
            result['weight'] = result['weight'] * scale
        return result

combos = [
    ('PVT25%+HY>5.0+YV>1.5->25%', 0.25, 0.20, 5.0, 1.5, 1.5, -0.40, 0.25),
    ('PVT25%+HY>4.5+YV>1.5->30%', 0.25, 0.20, 4.5, 1.0, 1.5, -0.40, 0.30),
    ('PVT30%+HY>5.0+YV>1.5->30%', 0.30, 0.25, 5.0, 1.5, 1.5, -0.40, 0.30),
    ('PVT25%+HY>5.0+YV>1.5->20%', 0.25, 0.20, 5.0, 1.5, 1.5, -0.40, 0.20),
    ('PVT30%+HY>4.5+YV>1.0->25%', 0.30, 0.25, 4.5, 1.0, 1.0, -0.30, 0.25),
]
for label, tv, ms, hl, hc, yv, yc, ss in combos:
    r, yr, win = run_strat(ComboStrategy,
                           target_vol=tv, min_scale=ms,
                           hy_level=hl, hy_chg=hc,
                           yv_thresh=yv, yc_thresh=yc,
                           stress_scale=ss)
    fmt(label, r, yr, win)

# ================================================================
# FINAL: year-by-year for best candidates
# ================================================================
print('\n' + '='*115)
print('  YEAR-BY-YEAR — best candidates vs baseline')
print('='*115)

candidates = [
    ('BASELINE v3.0',              v2.BacktestStrategy,  {}),
    ('PVT=20% min=15%',            PVTStrategy,          dict(target_vol=0.20, min_scale=0.15)),
    ('PVT=25% min=20%',            PVTStrategy,          dict(target_vol=0.25, min_scale=0.20)),
    ('YieldVol>1.5/chg<-0.40->30%',YieldVolStrategy,     dict(yv_thresh=1.5, yc_thresh=-0.40, scale=0.30)),
    ('YieldVol>1.5/chg<-0.30->25%',YieldVolStrategy,     dict(yv_thresh=1.5, yc_thresh=-0.30, scale=0.25)),
    ('PVT25%+HY>5.0+YV>1.5->25%', ComboStrategy,        dict(target_vol=0.25, min_scale=0.20, hy_level=5.0, hy_chg=1.5, yv_thresh=1.5, yc_thresh=-0.40, stress_scale=0.25)),
    ('PVT30%+HY>4.5+YV>1.0->25%', ComboStrategy,        dict(target_vol=0.30, min_scale=0.25, hy_level=4.5, hy_chg=1.0, yv_thresh=1.0, yc_thresh=-0.30, stress_scale=0.25)),
]

hdr = f'  {"Config":<44} {"CAGR":>6} {"Ex":>7} {"DD":>7} {"Sh":>5} {"Win":>5}  '
hdr += '  '.join(f'{y}' for y in range(2017, 2027))
print(hdr)
print('-'*135)

for label, cls, kwargs in candidates:
    p = v2.StrategyParams(**V3, start_date='2017-01-01', end_date='2026-08-20')
    strat = cls(loader, p, **kwargs)
    port  = v2.BacktestPortfolio(1_000_000, COST)
    if hasattr(strat, '_set_portfolio'):
        strat._set_portfolio(port)
    r  = v2.BacktestModel(loader, strat, port, p).run(verbose=False)
    yr = r.get('yearly', {})
    win = sum(1 for v in yr.values() if v['excess'] > 0)
    yr_str = '  '.join(f'{yr.get(y,{}).get("excess",-99)*100:+5.0f}%' for y in range(2017, 2027))
    dd_flag = ' <<<' if r['port_mdd']*100 > -30 else (' <<' if r['port_mdd']*100 > -35 else '')
    print(f'  {label:<44} {r["port_cagr"]*100:6.1f}%  {r["excess"]*100:+6.1f}%  '
          f'{r["port_mdd"]*100:6.1f}%  {r["sharpe"]:5.2f}  {win:>3}/10  {yr_str}{dd_flag}')
