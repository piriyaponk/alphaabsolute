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

# Pre-compute SPY signals
spy_px    = loader.get_benchmark_close('SPY')
spy_ma50  = spy_px.rolling(50).mean()
spy_ma200 = spy_px.rolling(200).mean()

# Pre-compute breadth: % stocks above MA200
close_all   = loader.close_wide
ma200_all   = close_all.rolling(200).mean()
breadth_pct = (close_all > ma200_all).mean(axis=1)


def run(cfg, StratClass=None):
    p = v2.StrategyParams(**cfg, start_date='2017-01-01', end_date='2026-08-20')
    strat = StratClass(loader, p) if StratClass else v2.BacktestStrategy(loader, p)
    port  = v2.BacktestPortfolio(1_000_000, COST)
    if hasattr(strat, '_set_portfolio'):
        strat._set_portfolio(port)
    r = v2.BacktestModel(loader, strat, port, p).run(verbose=False)
    yr = r.get('yearly', {})
    win = sum(1 for v in yr.values() if v['excess'] > 0)
    return r, yr, win


def fmt(label, r, yr, win):
    y = {k: v['excess']*100 for k,v in yr.items()}
    yw = {k: v['dd']*100 for k,v in yr.items()}
    worst_yr_dd = min(yw.values()) if yw else 0
    dd_flag = ' <<< DD<30%!' if r['port_mdd']*100 > -30 else (' <<' if r['port_mdd']*100 > -35 else '')
    print(f'  {label:<42} CAGR={r["port_cagr"]*100:5.1f}%  Ex={r["excess"]*100:+6.1f}%  '
          f'DD={r["port_mdd"]*100:6.1f}%  Sh={r["sharpe"]:.2f}  Win={win}/10  '
          f'2019={y.get(2019,-99):+.1f}%  2022={y.get(2022,-99):+.1f}%  '
          f'WorstYrDD={worst_yr_dd:.1f}%{dd_flag}')


print('=' * 120)
print('  DD REDUCTION METHODS — ISOLATED TEST vs SYSTEM v3.0 (NOT updating baseline)')
print('=' * 120)

r0, yr0, w0 = run(V3)
fmt('BASELINE v3.0', r0, yr0, w0)
print('-' * 120)


# ================================================================
# METHOD 1: Circuit breaker
# ================================================================
print('\n[METHOD 1] Portfolio circuit breaker (go cash when NAV drops X% from rolling peak)')

class CBStrategy(v2.BacktestStrategy):
    def __init__(self, loader, params, cb_thresh=0.15):
        super().__init__(loader, params)
        self.cb_thresh = cb_thresh
        self._peak_nav = 1_000_000.0
        self._in_cb    = False
        self._port     = None

    def _set_portfolio(self, port):
        self._port = port

    def screen(self, date):
        nav = self._port.nav if self._port else 1_000_000.0
        if nav > self._peak_nav:
            self._peak_nav = nav
        dd = (nav - self._peak_nav) / self._peak_nav
        if dd < -self.cb_thresh:
            self._in_cb = True
        if self._in_cb:
            spy_now   = spy_px.get(date, None)
            ma200_now = spy_ma200.get(date, None)
            if spy_now and ma200_now and spy_now > ma200_now:
                self._in_cb = False
        if self._in_cb:
            return pd.DataFrame(columns=self.dl.signals.columns)
        return super().screen(date)

for thresh in [0.10, 0.15, 0.20]:
    p = v2.StrategyParams(**V3, start_date='2017-01-01', end_date='2026-08-20')
    strat = CBStrategy(loader, p, cb_thresh=thresh)
    port  = v2.BacktestPortfolio(1_000_000, COST)
    strat._set_portfolio(port)
    r = v2.BacktestModel(loader, strat, port, p).run(verbose=False)
    yr = r.get('yearly',{}); win = sum(1 for v in yr.values() if v['excess']>0)
    fmt(f'CB thresh={thresh:.0%}', r, yr, win)


# ================================================================
# METHOD 2: Volatility targeting
# ================================================================
print('\n[METHOD 2] Volatility targeting (scale exposure = target_vol / realized_20d_vol)')

class VTStrategy(v2.BacktestStrategy):
    def __init__(self, loader, params, target_vol=0.20):
        super().__init__(loader, params)
        self.target_vol = target_vol

    def screen(self, date):
        result = super().screen(date)
        if result.empty:
            return result
        spy_ret = spy_px.loc[:date].tail(22).pct_change().dropna()
        if len(spy_ret) < 10:
            return result
        realized = spy_ret.std() * np.sqrt(252)
        if realized < 0.01:
            return result
        scale = min(self.target_vol / realized, 1.0)
        result = result.copy()
        result['weight'] = result['weight'] * scale
        return result

for tv in [0.15, 0.20, 0.25]:
    p = v2.StrategyParams(**V3, start_date='2017-01-01', end_date='2026-08-20')
    r = v2.BacktestModel(loader, VTStrategy(loader, p, target_vol=tv),
                         v2.BacktestPortfolio(1_000_000, COST), p).run(verbose=False)
    yr = r.get('yearly',{}); win = sum(1 for v in yr.values() if v['excess']>0)
    fmt(f'VolTarget={tv:.0%}', r, yr, win)


# ================================================================
# METHOD 3: Death cross early warning
# ================================================================
print('\n[METHOD 3] Death cross (50d<200d but price>200d -> reduce to dc_bear exposure)')

class DCStrategy(v2.BacktestStrategy):
    def __init__(self, loader, params, dc_bear=0.20):
        super().__init__(loader, params)
        self.dc_bear = dc_bear

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
        spy_now   = spy_px.get(date, None)
        ma50_now  = spy_ma50.get(date, None)
        ma200_now = spy_ma200.get(date, None)
        if spy_now and ma50_now and ma200_now:
            if spy_now < ma200_now:
                exp = self.params.bear_exposure
            elif ma50_now < ma200_now:
                exp = self.dc_bear
            else:
                exp = self.params.bull_exposure
        else:
            exp = self.params.bull_exposure
        result = result.copy()
        result['weight'] = result['weight'] * exp
        return result

for dc in [0.30, 0.20, 0.00]:
    p = v2.StrategyParams(**V3, start_date='2017-01-01', end_date='2026-08-20')
    r = v2.BacktestModel(loader, DCStrategy(loader, p, dc_bear=dc),
                         v2.BacktestPortfolio(1_000_000, COST), p).run(verbose=False)
    yr = r.get('yearly',{}); win = sum(1 for v in yr.values() if v['excess']>0)
    fmt(f'DeathCross dc_bear={dc:.0%}', r, yr, win)


# ================================================================
# METHOD 4: Breadth filter
# ================================================================
print('\n[METHOD 4] Breadth filter (scale exposure when pct_stocks_above_MA200 < threshold)')

class BRStrategy(v2.BacktestStrategy):
    def __init__(self, loader, params, br_thresh=0.40):
        super().__init__(loader, params)
        self.br_thresh = br_thresh

    def screen(self, date):
        result = super().screen(date)
        if result.empty:
            return result
        br = breadth_pct.get(date, 0.5)
        if br < self.br_thresh:
            scale = max(br / self.br_thresh, 0.10)
            result = result.copy()
            result['weight'] = result['weight'] * scale
        return result

for bt in [0.30, 0.40, 0.50]:
    p = v2.StrategyParams(**V3, start_date='2017-01-01', end_date='2026-08-20')
    r = v2.BacktestModel(loader, BRStrategy(loader, p, br_thresh=bt),
                         v2.BacktestPortfolio(1_000_000, COST), p).run(verbose=False)
    yr = r.get('yearly',{}); win = sum(1 for v in yr.values() if v['excess']>0)
    fmt(f'Breadth thresh={bt:.0%}', r, yr, win)


# ================================================================
# METHOD 5: COMBO — best of each method stacked
# ================================================================
print('\n[METHOD 5] COMBO — circuit breaker + death cross + breadth (stacked, no baseline change)')

class ComboStrategy(v2.BacktestStrategy):
    def __init__(self, loader, params, cb_thresh=0.15, dc_bear=0.20, br_thresh=0.40):
        super().__init__(loader, params)
        self.cb_thresh = cb_thresh
        self.dc_bear   = dc_bear
        self.br_thresh = br_thresh
        self._peak_nav = 1_000_000.0
        self._in_cb    = False
        self._port     = None

    def _set_portfolio(self, port):
        self._port = port

    def screen(self, date):
        nav = self._port.nav if self._port else 1_000_000.0
        if nav > self._peak_nav:
            self._peak_nav = nav
        dd = (nav - self._peak_nav) / self._peak_nav
        if dd < -self.cb_thresh:
            self._in_cb = True
        if self._in_cb:
            spy_now   = spy_px.get(date, None)
            ma200_now = spy_ma200.get(date, None)
            if spy_now and ma200_now and spy_now > ma200_now:
                self._in_cb = False
        if self._in_cb:
            return pd.DataFrame(columns=self.dl.signals.columns)

        result = _orig_screen(self, date)
        if not result.empty:
            top_n = getattr(self.params, 'top_n', 999)
            if len(result) > top_n:
                result = result.nlargest(top_n, 'rs_pct').copy()
                w = result['weight'].clip(upper=self.params.cap_pct)
                result['weight'] = w / w.sum() * (result['weight'].sum())
        if result.empty:
            return result

        spy_now   = spy_px.get(date, None)
        ma50_now  = spy_ma50.get(date, None)
        ma200_now = spy_ma200.get(date, None)
        if spy_now and ma50_now and ma200_now:
            if spy_now < ma200_now:         exp = self.params.bear_exposure
            elif ma50_now < ma200_now:      exp = self.dc_bear
            else:                           exp = self.params.bull_exposure
        else:
            exp = self.params.bull_exposure

        br = breadth_pct.get(date, 0.5)
        if br < self.br_thresh:
            exp = exp * max(br / self.br_thresh, 0.10)

        result = result.copy()
        result['weight'] = result['weight'] * exp
        return result

combos = [
    ('CB15%+DC20%+BR40%', 0.15, 0.20, 0.40),
    ('CB15%+DC30%+BR50%', 0.15, 0.30, 0.50),
    ('CB10%+DC20%+BR40%', 0.10, 0.20, 0.40),
    ('CB15%+DC20%+BR30%', 0.15, 0.20, 0.30),
    ('CB20%+DC30%+BR50%', 0.20, 0.30, 0.50),
]
for label, cb, dc, br in combos:
    p = v2.StrategyParams(**V3, start_date='2017-01-01', end_date='2026-08-20')
    strat = ComboStrategy(loader, p, cb_thresh=cb, dc_bear=dc, br_thresh=br)
    port  = v2.BacktestPortfolio(1_000_000, COST)
    strat._set_portfolio(port)
    r = v2.BacktestModel(loader, strat, port, p).run(verbose=False)
    yr = r.get('yearly',{}); win = sum(1 for v in yr.values() if v['excess']>0)
    fmt(label, r, yr, win)

print('\n' + '='*120)
print('  RESULT GUIDE: <<< = DD < -30%  | << = DD < -35%')
print('='*120)
