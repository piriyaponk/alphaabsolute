"""
Template Ideas Test — 3 concepts from the portfolio template applied to SYSTEM v3.0

Idea A: Intramonth vol-triggered trim (EGARCH-lite)
  - After each monthly rebalance, monitor daily portfolio vol
  - If 5d realized port vol > trim_mult × 21d baseline → reduce exposure to trim_ratio until next rebalance
  - Rebuy at full when vol drops back below rebuy_mult threshold
  - Implements daily monitoring loop on top of monthly weights (no lookahead)

Idea B: Hysteresis regime switch
  - Instead of binary SPY > MA200 → bull, require N consecutive days to confirm flip
  - Prevents rapid bull/bear oscillation near MA200 boundary

Idea C: NFCI credit composite gate
  - NFCI (National Financial Conditions Index) + STLFSI from FRED
  - Build composite: NFCI + STLFSI → risk-off when both elevated
  - More robust than single HY OAS (no NaN pre-2019 issue)
"""

import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import importlib.util, pandas as pd, numpy as np, requests
from pathlib import Path

ssl_ctx = None
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
spy_px     = loader.get_benchmark_close('SPY')
spy_ma200  = spy_px.rolling(200).mean()
spy_ma50   = spy_px.rolling(50).mean()
trading_days = close_wide.index


# ============================================================
# Baseline
# ============================================================
def run_baseline():
    p = v2.StrategyParams(**V3, start_date='2017-01-01', end_date='2026-08-20')
    strat = v2.BacktestStrategy(loader, p)
    port  = v2.BacktestPortfolio(1_000_000, COST)
    return v2.BacktestModel(loader, strat, port, p).run(verbose=False)

# ============================================================
# Helper: get monthly rebalance weights from baseline
# ============================================================
def get_monthly_weights():
    """Return dict of {rebalance_date: {ticker: weight}} from baseline run."""
    p = v2.StrategyParams(**V3, start_date='2017-01-01', end_date='2026-08-20')
    strat = v2.BacktestStrategy(loader, p)
    rebal_dates = loader.get_rebal_dates(p)
    weights = {}
    for d in rebal_dates:
        result = strat.screen(d)
        if not result.empty and len(result) > 15:
            result = result.nlargest(15, 'rs_pct').copy()
            w = result['weight'].clip(upper=0.18)
            result['weight'] = w / w.sum() * w.sum()
        if not result.empty:
            weights[d] = dict(zip(result['ticker'], result['weight']))
        else:
            weights[d] = {}
    return weights, rebal_dates


# ============================================================
# IDEA A: Intramonth vol-triggered trim (EGARCH-lite)
# ============================================================
def run_intramonth_trim(trim_mult=2.0, trim_ratio=0.30, rebuy_mult=1.3,
                        vol_lookback=21, vol_fast=5):
    """
    Simulate daily portfolio NAV using monthly weights.
    When 5d port vol > trim_mult * 21d baseline vol: trim to trim_ratio exposure for rest of month.
    Rebuy when 5d vol < rebuy_mult * baseline.
    """
    weights_by_date, rebal_dates = get_monthly_weights()

    # Build daily portfolio return series
    all_dates = trading_days[(trading_days >= pd.Timestamp('2017-01-01')) &
                             (trading_days <= pd.Timestamp('2026-08-20'))]

    nav = 1_000_000.0
    peak_nav = nav
    nav_series = {}
    bench_series = {}

    # Build SPY return series for benchmark
    spy_rets = spy_px.pct_change()

    # State
    current_weights = {}
    current_rebal_idx = 0
    is_trimmed = False
    cost_rate = 0.0015  # 0.15% round trip / 2 for daily approximation of turnover cost

    for i, date in enumerate(all_dates):
        # Check if today is a rebalance date
        if current_rebal_idx < len(rebal_dates) and date >= rebal_dates[current_rebal_idx]:
            # Apply transaction cost on rebalance (approximate)
            if current_weights:
                nav *= (1 - cost_rate)
            current_weights = weights_by_date.get(rebal_dates[current_rebal_idx], {})
            # Advance to next rebalance
            while current_rebal_idx < len(rebal_dates) and rebal_dates[current_rebal_idx] <= date:
                current_rebal_idx += 1
            is_trimmed = False  # reset trim state at each rebalance

        # Compute portfolio return for today
        if not current_weights:
            nav_series[date] = nav
            bench_series[date] = spy_px.get(date, np.nan)
            continue

        tickers = [t for t in current_weights if t in close_wide.columns]
        port_ret = 0.0
        if tickers:
            today_rets = {}
            for t in tickers:
                px_series = close_wide[t]
                if date in px_series.index and i > 0:
                    prev_dates = all_dates[:i]
                    prev_avail = prev_dates[prev_dates.isin(px_series.index)]
                    if len(prev_avail) > 0:
                        prev_px = px_series[prev_avail[-1]]
                        cur_px  = px_series.get(date, np.nan)
                        if not np.isnan(prev_px) and not np.isnan(cur_px) and prev_px > 0:
                            today_rets[t] = cur_px / prev_px - 1

            if today_rets:
                total_w = sum(current_weights[t] for t in today_rets)
                if total_w > 0:
                    for t, r in today_rets.items():
                        port_ret += (current_weights[t] / total_w) * r

        # Intramonth vol check
        # Compute portfolio realized vol using last fast/lookback days
        lookback_dates = all_dates[max(0, i-vol_lookback):i]
        fast_dates     = all_dates[max(0, i-vol_fast):i]

        def compute_port_vol_for_window(window_dates):
            if len(window_dates) < 3 or not current_weights:
                return None
            tks = [t for t in current_weights if t in close_wide.columns]
            if not tks: return None
            px = close_wide[tks].reindex(window_dates).dropna(how='all')
            rets = px.pct_change().dropna()
            if len(rets) < 2: return None
            w_arr = np.array([current_weights[t] for t in tks])
            w_arr = w_arr / w_arr.sum()
            pr = rets.values @ w_arr
            return pr.std() * np.sqrt(252)

        baseline_vol = compute_port_vol_for_window(lookback_dates)
        fast_vol     = compute_port_vol_for_window(fast_dates)

        exposure = 1.0
        if baseline_vol and fast_vol and baseline_vol > 0.01:
            if not is_trimmed and fast_vol > trim_mult * baseline_vol:
                is_trimmed = True
            elif is_trimmed and fast_vol < rebuy_mult * baseline_vol:
                is_trimmed = False

        if is_trimmed:
            exposure = trim_ratio

        # Apply exposure scaling to port return
        # Cash portion earns ~0% (risk-free ~0 for simplicity, or we could add 4%)
        effective_ret = port_ret * exposure

        nav = nav * (1 + effective_ret)
        if nav > peak_nav:
            peak_nav = nav
        nav_series[date] = nav
        bench_series[date] = spy_px.get(date, np.nan)

    # Compute metrics from nav_series
    nav_s = pd.Series(nav_series).sort_index()
    spy_s = pd.Series(bench_series).sort_index()

    if nav_s.empty:
        return {}

    # CAGR
    years = (nav_s.index[-1] - nav_s.index[0]).days / 365.25
    cagr = (nav_s.iloc[-1] / nav_s.iloc[0]) ** (1/years) - 1

    # SPY CAGR for excess computation — use QQQ instead
    qqq_px  = loader.get_benchmark_close('QQQ')
    qqq_s   = qqq_px.reindex(nav_s.index).ffill()
    qqq_cagr = (qqq_s.iloc[-1] / qqq_s.iloc[0]) ** (1/years) - 1
    excess = cagr - qqq_cagr

    # Max DD
    roll_max = nav_s.expanding().max()
    dd_s = (nav_s - roll_max) / roll_max
    max_dd = dd_s.min()

    # Sharpe (daily)
    rets = nav_s.pct_change().dropna()
    sharpe = rets.mean() / rets.std() * np.sqrt(252)

    # Yearly excess
    yearly = {}
    for yr in range(2017, 2027):
        yr_nav = nav_s[nav_s.index.year == yr]
        yr_qqq = qqq_s[qqq_s.index.year == yr]
        if len(yr_nav) > 1 and len(yr_qqq) > 1:
            pf_ret = yr_nav.iloc[-1] / yr_nav.iloc[0] - 1
            bm_ret = yr_qqq.iloc[-1] / yr_qqq.iloc[0] - 1
            yearly[yr] = {'excess': pf_ret - bm_ret, 'port': pf_ret}

    win = sum(1 for v in yearly.values() if v['excess'] > 0)

    return {'port_cagr': cagr, 'excess': excess, 'port_mdd': max_dd,
            'sharpe': sharpe, 'yearly': yearly, 'win': win,
            'bench_cagr': qqq_cagr}


# ============================================================
# IDEA B: Hysteresis Regime Switch
# ============================================================
class HysteresisRegimeStrategy(v2.BacktestStrategy):
    """
    Instead of binary SPY > MA200 = bull, require N consecutive days
    above MA200 to flip to bull, N days below to flip to bear.
    Uses the current regime state from the last rebalance.
    """
    def __init__(self, loader, params, confirm_days=10):
        super().__init__(loader, params)
        self.confirm_days = confirm_days
        self._regime = 'bull'  # start bull

    def _get_regime(self, date):
        # Look at last confirm_days trading days
        idx = trading_days.searchsorted(date)
        window = trading_days[max(0, idx-self.confirm_days):idx+1]
        spy_window = spy_px.reindex(window).ffill()
        ma200_window = spy_ma200.reindex(window).ffill()
        if spy_window.empty or ma200_window.empty:
            return self._regime
        above = (spy_window > ma200_window).sum()
        below = (spy_window <= ma200_window).sum()
        if above == len(window):  # all days above → confirmed bull
            self._regime = 'bull'
        elif below == len(window):  # all days below → confirmed bear
            self._regime = 'bear'
        # else: mixed → hold current regime (hysteresis)
        return self._regime

    def screen(self, date):
        result = _orig_screen(self, date)
        if not result.empty and len(result) > 15:
            result = result.nlargest(15, 'rs_pct').copy()
            w = result['weight'].clip(upper=0.18)
            result['weight'] = w / w.sum() * w.sum()
        if result.empty:
            return result
        regime = self._get_regime(date)
        exp = self.params.bull_exposure if regime == 'bull' else self.params.bear_exposure
        result = result.copy()
        result['weight'] = result['weight'] * exp
        return result


# ============================================================
# IDEA C: NFCI Credit Composite Gate
# ============================================================
def fetch_fred_series(series_id):
    """Fetch a FRED series as pandas Series."""
    url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}'
    try:
        resp = requests.get(url, verify=False, timeout=15)
        if resp.status_code == 200:
            from io import StringIO
            df = pd.read_csv(StringIO(resp.text))
            df.columns = ['date', 'value']
            df['date'] = pd.to_datetime(df['date'])
            df = df.dropna()
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
            return df.set_index('date')['value']
    except Exception as e:
        print(f'  FRED fetch {series_id} failed: {e}')
    return pd.Series(dtype=float)

print('Fetching NFCI and STLFSI from FRED...')
nfci   = fetch_fred_series('NFCI')    # Chicago Fed NFCI (weekly, negative = loose, positive = tight)
stlfsi = fetch_fred_series('STLFSI4') # St Louis Fed Financial Stress (weekly)
print(f'  NFCI: {len(nfci)} rows, latest={nfci.index[-1].date() if len(nfci)>0 else "n/a"}')
print(f'  STLFSI: {len(stlfsi)} rows, latest={stlfsi.index[-1].date() if len(stlfsi)>0 else "n/a"}')

# Show values around Q4 2018 crisis
if len(nfci) > 0:
    print('\n  NFCI on Q4 2018 rebal dates (positive = financial stress):')
    for d in ['2018-09-28','2018-10-31','2018-11-30','2018-12-31','2019-01-31']:
        dt = pd.Timestamp(d)
        # get nearest NFCI value
        past = nfci[nfci.index <= dt]
        val = past.iloc[-1] if len(past) > 0 else np.nan
        print(f'    {d}: NFCI={val:.3f}')

class NFCIGateStrategy(v2.BacktestStrategy):
    """
    Reduce exposure when NFCI + STLFSI composite is in risk-off zone.
    Uses hysteresis: enter risk-off at composite > enter_thresh, exit at < exit_thresh.
    """
    def __init__(self, loader, params, nfci_series, stlfsi_series,
                 enter_thresh=0.3, exit_thresh=0.0, risk_off_exposure=0.50):
        super().__init__(loader, params)
        self.nfci   = nfci_series
        self.stlfsi = stlfsi_series
        self.enter_thresh     = enter_thresh
        self.exit_thresh      = exit_thresh
        self.risk_off_exposure = risk_off_exposure
        self._in_risk_off = False

        # Pre-align to trading days
        all_td = pd.date_range('2015-01-01', '2026-12-31', freq='B')
        if len(nfci_series) > 0:
            self._nfci_daily = nfci_series.reindex(
                nfci_series.index.union(all_td)).ffill().reindex(all_td)
        else:
            self._nfci_daily = pd.Series(dtype=float)

        if len(stlfsi_series) > 0:
            self._stlfsi_daily = stlfsi_series.reindex(
                stlfsi_series.index.union(all_td)).ffill().reindex(all_td)
        else:
            self._stlfsi_daily = pd.Series(dtype=float)

    def _get_credit_score(self, date):
        n = self._nfci_daily.get(date, np.nan)
        s = self._stlfsi_daily.get(date, np.nan)
        if np.isnan(n) and np.isnan(s):
            return np.nan
        n = n if not np.isnan(n) else 0.0
        s = s if not np.isnan(s) else 0.0
        return 0.5 * n + 0.5 * s  # equal weight composite

    def screen(self, date):
        result = _orig_screen(self, date)
        if not result.empty and len(result) > 15:
            result = result.nlargest(15, 'rs_pct').copy()
            w = result['weight'].clip(upper=0.18)
            result['weight'] = w / w.sum() * w.sum()
        if result.empty:
            return result

        credit = self._get_credit_score(date)
        if not np.isnan(credit):
            if not self._in_risk_off and credit > self.enter_thresh:
                self._in_risk_off = True
            elif self._in_risk_off and credit < self.exit_thresh:
                self._in_risk_off = False

        # Apply standard SPY regime, then further reduce if credit risk-off
        spy_now   = spy_px.get(date, None)
        ma200_now = spy_ma200.get(date, None)
        if spy_now and ma200_now:
            regime_exp = self.params.bull_exposure if spy_now > ma200_now else self.params.bear_exposure
        else:
            regime_exp = self.params.bull_exposure

        credit_exp = self.risk_off_exposure if self._in_risk_off else 1.0
        total_exp = regime_exp * credit_exp

        result = result.copy()
        result['weight'] = result['weight'] * total_exp
        return result


# ============================================================
# Run all tests
# ============================================================
def run_strategy(StratClass, **skw):
    p = v2.StrategyParams(**V3, start_date='2017-01-01', end_date='2026-08-20')
    strat = StratClass(loader, p, **skw)
    port  = v2.BacktestPortfolio(1_000_000, COST)
    r = v2.BacktestModel(loader, strat, port, p).run(verbose=False)
    yr = r.get('yearly', {})
    win = sum(1 for v in yr.values() if v['excess'] > 0)
    return r, yr, win


def fmt_row(label, r, yr, win):
    y = {k: v['excess']*100 for k,v in yr.items()}
    yw = {k: v['dd']*100 for k,v in yr.items()}
    worst_yr_dd = min(yw.values()) if yw else 0
    dd_flag = ' <<<' if r['port_mdd']*100 > -30 else (' <<' if r['port_mdd']*100 > -35 else '')
    yr_str = ' '.join(f'{y.get(yr_n,-99):+4.0f}%' for yr_n in range(2017,2027))
    print(f'  {label:<38} CAGR={r["port_cagr"]*100:5.1f}%  Ex={r["excess"]*100:+6.1f}%  '
          f'DD={r["port_mdd"]*100:6.1f}%  Sh={r["sharpe"]:.2f}  Win={win}/10  |  {yr_str}{dd_flag}')


print('\n' + '='*180)
print('  TEMPLATE IDEAS TEST vs SYSTEM v3.0 (FULL 2017-2026, 0.15% cost)')
print('='*180)
print(f'  {"Config":<38} {"CAGR":>7} {"Excess":>8} {"DD":>8} {"Sharpe":>7} {"Win":>6}  |  Years 2017-2026')
print('  ' + '-'*170)

# Baseline
r0, yr0, w0 = run_strategy(v2.BacktestStrategy)
fmt_row('BASELINE v3.0', r0, yr0, w0)
print('  ' + '-'*170)

# Idea B: Hysteresis regime
print('\n  [IDEA B] Hysteresis Regime (N-day confirmation before bull/bear flip)')
for cd in [5, 10, 15, 20]:
    r, yr, w = run_strategy(HysteresisRegimeStrategy, confirm_days=cd)
    fmt_row(f'Hysteresis confirm={cd}d', r, yr, w)

# Idea C: NFCI credit composite
print('\n  [IDEA C] NFCI + STLFSI Credit Composite Gate')
if len(nfci) > 0 and len(stlfsi) > 0:
    for enter, exit_, roff in [(0.3, 0.0, 0.5), (0.2, -0.1, 0.5), (0.3, 0.0, 0.3),
                                (0.5, 0.1, 0.5), (0.1, -0.2, 0.5)]:
        r, yr, w = run_strategy(NFCIGateStrategy,
                                nfci_series=nfci, stlfsi_series=stlfsi,
                                enter_thresh=enter, exit_thresh=exit_,
                                risk_off_exposure=roff)
        fmt_row(f'NFCI enter={enter:.1f} exit={exit_:.1f} roff={roff:.0%}', r, yr, w)
else:
    print('  NFCI/STLFSI data not available — skipping')

# Idea A: Intramonth trim (computationally intensive — runs last)
print('\n  [IDEA A] Intramonth Vol-Triggered Trim (daily monitoring, EGARCH-lite)')
print('  Note: approximate — uses monthly weights, daily vol check, no lookahead')
for trim_mult, trim_ratio, rebuy_mult in [(2.0, 0.30, 1.3), (1.5, 0.30, 1.2),
                                          (2.0, 0.50, 1.3), (2.5, 0.30, 1.5)]:
    print(f'  Running IntramonthTrim mult={trim_mult:.1f} trim={trim_ratio:.0%} rebuy={rebuy_mult:.1f}...')
    try:
        res = run_intramonth_trim(trim_mult=trim_mult, trim_ratio=trim_ratio, rebuy_mult=rebuy_mult)
        if res:
            yr_str = ' '.join(f'{res["yearly"].get(y,{}).get("excess",0)*100:+4.0f}%' for y in range(2017,2027))
            win = res['win']
            dd_flag = ' <<<' if res['port_mdd']*100 > -30 else (' <<' if res['port_mdd']*100 > -35 else '')
            print(f'  {"IntramonthTrim mult="+str(trim_mult)+" trim="+str(trim_ratio):<38} '
                  f'CAGR={res["port_cagr"]*100:5.1f}%  Ex={res["excess"]*100:+6.1f}%  '
                  f'DD={res["port_mdd"]*100:6.1f}%  Sh={res["sharpe"]:.2f}  Win={win}/10  |  {yr_str}{dd_flag}')
    except Exception as e:
        print(f'  ERROR: {e}')

print('\n' + '='*180)
print('  Summary: Which ideas improve FULL excess + DD simultaneously vs baseline?')
print('  Governance gate: CAGR>30%, Win>=8/10, DD better, FULL excess not worse by >5%')
print('='*180)
