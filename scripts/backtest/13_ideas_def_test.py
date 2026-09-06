"""
IDEAS D / E / F — Advanced template concepts, proper BacktestModel engine

Idea D: 4-State Hysteresis State Machine (from template hysteresis.py)
  States: 0=Normal(100%) → 1=Warning(85%) → 2=Stress(65%) → 3=Crisis(45%)
  Market score = vol_ratio_score × 0.40 + below_MA200 × 0.60
  vol_ratio = SPY 21d vol / SPY 63d vol (VIX proxy)
  Upgrade: immediate. Downgrade: confirm N consecutive days below exit threshold.

Idea E: V-Shape Recovery Override (added on top of D)
  After de-risking, force back to 100% when ALL true:
    SPY > MA50, SPY > 52W_low × 1.20, MA50 rising (> MA50 15d ago), vol_ratio < 1.4
  Template logic: recovery_signal=1 → exposure = max(exposure, 1.0)

Idea F: Full Credit Composite (5-signal, with BBB spread replacing HY level)
  credit_score = 0.30×HY_mom + 0.30×BBB_stress_norm + 0.20×NFCI + 0.20×STLFSI
  BBB stress = BAMLC0A4CBBB spread (FRED, available from 1996 — no NaN issue)
  Combined with market score: exposure = min(credit_exposure, market_exposure)
  Hysteresis on both arms separately.
"""

import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import importlib.util, pandas as pd, numpy as np, requests, warnings
warnings.filterwarnings('ignore')
from pathlib import Path

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

spy_px   = loader.get_benchmark_close('SPY')
trading_days = loader.close_wide.index


# ============================================================
# FETCH FRED DATA
# ============================================================
def fetch_fred(series_id, name=''):
    url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}'
    try:
        resp = requests.get(url, verify=False, timeout=20)
        if resp.status_code == 200:
            from io import StringIO
            df = pd.read_csv(StringIO(resp.text))
            df.columns = ['date', 'value']
            df['date'] = pd.to_datetime(df['date'])
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
            s = df.dropna().set_index('date')['value']
            print(f'  {name or series_id}: {len(s)} rows, {s.index[0].date()} to {s.index[-1].date()}')
            return s
    except Exception as e:
        print(f'  FAILED {series_id}: {e}')
    return pd.Series(dtype=float)

print('Fetching FRED data...')
nfci    = fetch_fred('NFCI',          'NFCI')
stlfsi  = fetch_fred('STLFSI4',       'STLFSI')
bbb_oas = fetch_fred('BAMLC0A4CBBB',  'BBB_OAS')   # BBB option-adjusted spread (% bp? check)
hy_oas  = fetch_fred('BAMLH0A0HYM2',  'HY_OAS')    # HY OAS


# ============================================================
# PRE-COMPUTE DAILY HYSTERESIS SERIES (no lookahead)
# ============================================================

def run_hysteresis_series(score: pd.Series,
                          enter=(0.25, 0.45, 0.70),
                          exit_thr=(0.15, 0.35, 0.60),
                          confirm_days=10,
                          exposure_map=(1.00, 0.85, 0.65, 0.45)):
    """
    4-state machine. State 0=Normal,1=Warning,2=Stress,3=Crisis.
    Upgrade: immediate. Downgrade: confirm_days consecutive below exit threshold.
    Returns: exposure Series, state Series (both aligned to score.index).
    """
    vals = score.to_numpy()
    n = len(vals)
    exposures = np.empty(n)
    states    = np.empty(n, dtype=int)
    state = 0
    exit_counter = 0
    for i in range(n):
        s = vals[i]
        if np.isnan(s):
            states[i]    = state
            exposures[i] = exposure_map[state]
            continue
        # determine target state
        if s >= enter[2]:   target = 3
        elif s >= enter[1]: target = 2
        elif s >= enter[0]: target = 1
        else:               target = 0
        if target > state:
            state = target
            exit_counter = 0
        elif target < state:
            if s < exit_thr[state - 1]:
                exit_counter += 1
                if exit_counter >= confirm_days:
                    state -= 1
                    exit_counter = 0
            else:
                exit_counter = 0
        else:
            exit_counter = 0
        states[i]    = state
        exposures[i] = exposure_map[state]
    return (pd.Series(exposures, index=score.index, name='exposure'),
            pd.Series(states,    index=score.index, name='state'))


def make_market_score(spy_px, w_vol=0.40, w_trend=0.60,
                      vol_fast=21, vol_slow=63, ma_window=200,
                      vol_bands=(1.2, 1.5, 2.0)):
    """
    market_score = w_vol × vol_score + w_trend × trend_score
    vol_ratio    = 21d realized vol / 63d realized vol
    vol_score    = piecewise: 0 / 0.33 / 0.67 / 1.0
    trend_score  = 1 if SPY < MA200 else 0
    """
    log_ret = np.log(spy_px / spy_px.shift(1))
    vol21 = log_ret.rolling(vol_fast).std() * np.sqrt(252)
    vol63 = log_ret.rolling(vol_slow).std() * np.sqrt(252)
    vol_ratio = (vol21 / vol63).clip(lower=0.5, upper=4.0)

    b = vol_bands
    vol_score = pd.Series(
        np.select([vol_ratio < b[0], vol_ratio < b[1], vol_ratio < b[2]],
                  [0.0, 0.33, 0.67], default=1.0),
        index=spy_px.index
    ).where(vol_ratio.notna())

    ma200      = spy_px.rolling(ma_window).mean()
    trend_score = (spy_px < ma200).astype(float).where(spy_px.notna() & ma200.notna())

    return w_vol * vol_score + w_trend * trend_score, vol_ratio


def make_recovery_signal(spy_px, vol_ratio,
                         ma50_window=50, low52_window=252,
                         ma50_lookback=15, vol_rebuy_thr=1.4, off_low_thr=0.15):
    """
    Recovery = True when ALL:
      SPY > MA50
      SPY > 52W_low × (1 + off_low_thr)
      MA50 rising (> MA50 15d ago)
      vol_ratio < vol_rebuy_thr
    """
    ma50  = spy_px.rolling(ma50_window).mean()
    low52 = spy_px.rolling(low52_window).min()
    above_ma50    = (spy_px > ma50)
    above_low52   = (spy_px > low52 * (1 + off_low_thr))
    ma50_rising   = (ma50 > ma50.shift(ma50_lookback))
    vol_declining = (vol_ratio < vol_rebuy_thr)
    recovery = (above_ma50 & above_low52 & ma50_rising & vol_declining)
    return recovery.astype(float)


def make_credit_score(nfci, stlfsi, bbb_oas, hy_oas, trading_days,
                      w_hy_mom=0.30, w_bbb=0.30, w_nfci=0.20, w_stlfsi=0.20,
                      bbb_roll=63, hy_mom_roll=21):
    """
    credit_score = w_hy_mom×HY_mom_norm + w_bbb×BBB_stress_norm + w_nfci×NFCI_norm + w_stlfsi×STLFSI_norm
    All components normalized to [0, 1] via rolling percentile (252d)
    """
    all_td = pd.date_range('2014-01-01', '2027-12-31', freq='B')

    def align(s):
        if len(s) == 0: return pd.Series(dtype=float)
        return s.reindex(s.index.union(all_td)).ffill().reindex(trading_days)

    nfci_d   = align(nfci)
    stlfsi_d = align(stlfsi)
    bbb_d    = align(bbb_oas)
    hy_d     = align(hy_oas)

    def rolling_pct(s, window=252):
        return s.rolling(window, min_periods=int(window*0.5)).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)

    # HY momentum (rate of change)
    hy_mom = hy_d.diff(hy_mom_roll)
    hy_mom_norm = rolling_pct(hy_mom)

    # BBB stress: level normalized
    bbb_norm = rolling_pct(bbb_d)

    # NFCI: positive = tight, normalized
    nfci_norm   = rolling_pct(nfci_d)
    stlfsi_norm = rolling_pct(stlfsi_d)

    score = pd.Series(np.nan, index=trading_days)
    mask = (~hy_mom_norm.isna() | ~bbb_norm.isna())
    if mask.any():
        score = (w_hy_mom   * hy_mom_norm.fillna(0.5) +
                 w_bbb      * bbb_norm.fillna(0.5) +
                 w_nfci     * nfci_norm.fillna(0.5) +
                 w_stlfsi   * stlfsi_norm.fillna(0.5))
        score = score.where(mask)

    return score


# Pre-compute all series
print('\nPre-computing market scores and hysteresis series...')
market_score, vol_ratio = make_market_score(spy_px)
recovery_signal = make_recovery_signal(spy_px, vol_ratio)

credit_score = make_credit_score(nfci, stlfsi, bbb_oas, hy_oas, trading_days)
credit_score_aligned = credit_score.reindex(trading_days).ffill()

# Show key dates
print('\n  Market score on critical dates:')
for d_str in ['2018-09-28','2018-10-31','2018-11-30','2018-12-31',
              '2019-01-31','2020-02-28','2020-03-31','2020-04-30',
              '2022-01-31','2022-06-30']:
    d = pd.Timestamp(d_str)
    ms = market_score.get(d, np.nan)
    cs = credit_score_aligned.get(d, np.nan)
    vr = vol_ratio.get(d, np.nan)
    rc = recovery_signal.get(d, np.nan)
    print(f'    {d_str}: market={ms:.3f}  credit={cs:.3f}  vol_ratio={vr:.2f}  recovery={rc:.0f}')


# ============================================================
# BASE: compute exposure series from hysteresis
# ============================================================
def build_market_exposure(market_score,
                          enter=(0.25, 0.45, 0.70), exit_thr=(0.15, 0.35, 0.60),
                          confirm_days=10,
                          exposure_map=(1.00, 0.85, 0.65, 0.45)):
    exp, state = run_hysteresis_series(market_score, enter, exit_thr, confirm_days, exposure_map)
    return exp, state


def build_credit_exposure(credit_score,
                          enter=(0.55, 0.70, 0.85), exit_thr=(0.40, 0.60, 0.75),
                          confirm_days=30,
                          exposure_map=(1.00, 0.80, 0.60, 0.40)):
    exp, state = run_hysteresis_series(credit_score, enter, exit_thr, confirm_days, exposure_map)
    return exp, state


# ============================================================
# IDEA D: 4-State Hysteresis Strategy (market only)
# ============================================================
class HysteresisD_Strategy(v2.BacktestStrategy):
    def __init__(self, loader, params, market_exp_series, recovery_series=None):
        super().__init__(loader, params)
        self._market_exp = market_exp_series
        self._recovery   = recovery_series  # optional V-shape override

    def screen(self, date):
        result = _orig_screen(self, date)
        if not result.empty and len(result) > 15:
            result = result.nlargest(15, 'rs_pct').copy()
            w = result['weight'].clip(upper=0.18)
            result['weight'] = w / w.sum() * w.sum()
        if result.empty:
            return result

        exp = self._market_exp.get(date, 1.0)

        # V-shape recovery override: if recovery fires → force full exposure
        if self._recovery is not None:
            rec = self._recovery.get(date, 0.0)
            if rec == 1.0:
                exp = max(exp, 1.0)

        result = result.copy()
        result['weight'] = result['weight'] * exp
        return result


# ============================================================
# IDEA F: Full Credit + Market Composite
# ============================================================
class HysteresisF_Strategy(v2.BacktestStrategy):
    def __init__(self, loader, params, market_exp_series, credit_exp_series, recovery_series=None):
        super().__init__(loader, params)
        self._market_exp = market_exp_series
        self._credit_exp = credit_exp_series
        self._recovery   = recovery_series

    def screen(self, date):
        result = _orig_screen(self, date)
        if not result.empty and len(result) > 15:
            result = result.nlargest(15, 'rs_pct').copy()
            w = result['weight'].clip(upper=0.18)
            result['weight'] = w / w.sum() * w.sum()
        if result.empty:
            return result

        m_exp = self._market_exp.get(date, 1.0)
        c_exp = self._credit_exp.get(date, 1.0)
        exp = min(m_exp, c_exp)  # conservative: take the lower of two

        if self._recovery is not None:
            rec = self._recovery.get(date, 0.0)
            if rec == 1.0:
                exp = max(exp, 1.0)

        result = result.copy()
        result['weight'] = result['weight'] * exp
        return result


def run_strategy(StratClass, **skw):
    p = v2.StrategyParams(**V3, start_date='2017-01-01', end_date='2026-08-20')
    strat = StratClass(loader, p, **skw)
    port  = v2.BacktestPortfolio(1_000_000, COST)
    r = v2.BacktestModel(loader, strat, port, p).run(verbose=False)
    yr = r.get('yearly', {})
    win = sum(1 for v in yr.values() if v['excess'] > 0)
    return r, yr, win


def fmt(label, r, yr, win):
    y  = {k: v['excess']*100 for k,v in yr.items()}
    dd_flag = ' <<<' if r['port_mdd']*100 > -30 else (' <<' if r['port_mdd']*100 > -35 else '')
    yr_str = ' '.join(f'{y.get(n,-99):+4.0f}%' for n in range(2017, 2027))
    print(f'  {label:<45} CAGR={r["port_cagr"]*100:5.1f}%  Ex={r["excess"]*100:+6.1f}%  '
          f'DD={r["port_mdd"]*100:6.1f}%  Sh={r["sharpe"]:.2f}  Win={win}/10  |  {yr_str}{dd_flag}')


print('\n' + '='*200)
print('  IDEAS D / E / F vs SYSTEM v3.0 — proper BacktestModel engine, 0.15% cost')
print('='*200)
print(f'  {"Config":<45} {"CAGR":>7} {"Excess":>8} {"DD":>8} {"Sharpe":>7} {"Win":>6}  |  2017-2026')
print('  ' + '-'*190)

r0, yr0, w0 = run_strategy(v2.BacktestStrategy)
fmt('BASELINE v3.0', r0, yr0, w0)
print('  ' + '-'*190)

# ── IDEA D: Market hysteresis only, various params ──────────────────────────
print('\n  [IDEA D] 4-State Market Hysteresis (vol_ratio + MA200 → 4 exposure levels)')
for conf_d, exp_map in [
    (10, (1.00, 0.85, 0.65, 0.45)),
    (10, (1.00, 0.90, 0.70, 0.50)),
    (5,  (1.00, 0.85, 0.65, 0.45)),
    (15, (1.00, 0.85, 0.65, 0.45)),
    (10, (1.00, 0.80, 0.55, 0.35)),  # template default
]:
    m_exp, m_state = build_market_exposure(
        market_score,
        enter=(0.25, 0.45, 0.70), exit_thr=(0.15, 0.35, 0.60),
        confirm_days=conf_d, exposure_map=exp_map)

    r, yr, w = run_strategy(HysteresisD_Strategy,
                             market_exp_series=m_exp, recovery_series=None)
    label = f'D: confirm={conf_d}d map={exp_map[1]:.0%}/{exp_map[2]:.0%}/{exp_map[3]:.0%}'
    fmt(label, r, yr, w)

# ── IDEA E: D + V-shape recovery ────────────────────────────────────────────
print('\n  [IDEA E] D + V-Shape Recovery Override (force 100% when market recovers)')
for conf_d, exp_map, off_low, vol_thr in [
    (10, (1.00, 0.85, 0.65, 0.45), 0.15, 1.4),
    (10, (1.00, 0.85, 0.65, 0.45), 0.10, 1.5),
    (10, (1.00, 0.80, 0.55, 0.35), 0.15, 1.4),
    (5,  (1.00, 0.85, 0.65, 0.45), 0.15, 1.4),
]:
    m_exp, _ = build_market_exposure(
        market_score,
        enter=(0.25, 0.45, 0.70), exit_thr=(0.15, 0.35, 0.60),
        confirm_days=conf_d, exposure_map=exp_map)
    rec = make_recovery_signal(spy_px, vol_ratio, off_low_thr=off_low, vol_rebuy_thr=vol_thr)

    r, yr, w = run_strategy(HysteresisD_Strategy,
                             market_exp_series=m_exp, recovery_series=rec)
    label = f'E: confirm={conf_d}d map={exp_map[2]:.0%}/{exp_map[3]:.0%} off_low={off_low:.0%}'
    fmt(label, r, yr, w)

# ── IDEA F: Market + Credit composite ───────────────────────────────────────
print('\n  [IDEA F] Credit+Market Composite (NFCI+STLFSI+BBB+HY_mom, 4-state each)')
for m_conf, c_conf, m_map, c_map, with_rec in [
    (10, 30, (1.00, 0.85, 0.65, 0.45), (1.00, 0.80, 0.60, 0.40), False),
    (10, 30, (1.00, 0.85, 0.65, 0.45), (1.00, 0.80, 0.60, 0.40), True),
    (10, 30, (1.00, 0.90, 0.75, 0.55), (1.00, 0.90, 0.75, 0.55), False),
    (5,  20, (1.00, 0.85, 0.65, 0.45), (1.00, 0.80, 0.60, 0.40), True),
]:
    m_exp, _ = build_market_exposure(
        market_score, confirm_days=m_conf, exposure_map=m_map)
    c_exp, _ = build_credit_exposure(
        credit_score_aligned, confirm_days=c_conf, exposure_map=c_map)
    rec = make_recovery_signal(spy_px, vol_ratio) if with_rec else None

    r, yr, w = run_strategy(HysteresisF_Strategy,
                             market_exp_series=m_exp,
                             credit_exp_series=c_exp,
                             recovery_series=rec)
    rec_tag = '+Rec' if with_rec else ''
    label = f'F: m={m_map[2]:.0%}/{m_map[3]:.0%} c={c_map[2]:.0%}/{c_map[3]:.0%}{rec_tag}'
    fmt(label, r, yr, w)

# ── BONUS: D + E + tune for DD reduction specifically ───────────────────────
print('\n  [BONUS] D+E tuned for max DD reduction (aggressive de-risk)')
for conf_d, enter, exp_map in [
    (5,  (0.20, 0.35, 0.55), (1.00, 0.80, 0.50, 0.30)),
    (5,  (0.20, 0.35, 0.55), (1.00, 0.70, 0.40, 0.20)),
    (10, (0.20, 0.35, 0.55), (1.00, 0.80, 0.50, 0.30)),
]:
    m_exp, _ = build_market_exposure(
        market_score, enter=enter, exit_thr=(0.10, 0.25, 0.45),
        confirm_days=conf_d, exposure_map=exp_map)
    rec = make_recovery_signal(spy_px, vol_ratio, off_low_thr=0.15, vol_rebuy_thr=1.4)
    r, yr, w = run_strategy(HysteresisD_Strategy,
                             market_exp_series=m_exp, recovery_series=rec)
    label = f'Bonus: conf={conf_d}d enter={enter[0]:.2f} map={exp_map[2]:.0%}/{exp_map[3]:.0%}+Rec'
    fmt(label, r, yr, w)

print('\n' + '='*200)
print('  GOVERNANCE GATE: DD must improve AND CAGR>30% AND Win>=8/10 AND excess not worse by >5%')
print('='*200)

# Print hysteresis state diagnostics on key dates
print('\n  Hysteresis state diagnostics on key crisis dates:')
m_exp_d, m_state_d = build_market_exposure(market_score)
print(f'  {"Date":<14} {"MarketScore":>12} {"State":>7} {"Exposure":>9} {"VolRatio":>9} {"Recovery":>10}')
for d_str in ['2018-09-28','2018-10-31','2018-11-30','2018-12-31',
              '2019-01-31','2020-02-28','2020-03-31','2020-04-30',
              '2022-01-31','2022-06-30','2022-09-30']:
    d = pd.Timestamp(d_str)
    ms  = market_score.get(d, np.nan)
    st  = m_state_d.get(d, -1)
    ex  = m_exp_d.get(d, np.nan)
    vr  = vol_ratio.get(d, np.nan)
    rec = recovery_signal.get(d, np.nan)
    state_name = ['Normal','Warning','Stress','Crisis'][int(st)] if st >= 0 else '?'
    print(f'  {d_str:<14} {ms:>12.3f} {state_name:>7} {ex:>8.0%} {vr:>9.2f} {rec:>10.0f}')
