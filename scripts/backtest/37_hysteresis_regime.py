"""
37_hysteresis_regime.py — 4-State Credit+Market Hysteresis Regime

Ported from template: models/overlays/risk/hysteresis.py + scores.py + exposure.py

REPLACES: Binary IWM > MA200 (100% vs 50% exposure)
WITH: Continuous 4-state regime based on:
  - Market score: vol_ratio (10d/63d realized vol) + MA200 trend  [from our own data]
  - Credit score: HY OAS spread level + momentum               [from FRED API]

States: 0=Normal / 1=Warning / 2=Stress / 3=Crisis
Exposure: Normal=100% / Warning=85% / Stress=65% / Crisis=45%

Key advantage: smooth transitions with confirm_days anti-whipsaw filter.
V-shape recovery: price > MA50 + off 52W low + MA50 rising + credit relief → force 100%

All configs tested via v2.BacktestModel monkey-patch → accurate Sharpe.
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd, numpy as np, importlib.util, pathlib, requests, ssl

ssl._create_default_https_context = ssl._create_unverified_context
_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)

BASE_PARAMS = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=9.9, base_tight_max=9.9, adtv_min_m=10, cap_pct=0.18,
    bear_exposure=0.50, bull_exposure=1.00, softmax_alpha=1.0,
    start_date='2017-01-01', end_date='2026-08-20'
)
TOP_N    = 15
BASELINE = dict(excess=35.8, dd=-40.1, sharpe=1.44)

# ── FRED data fetch ──────────────────────────────────────────────────────────

def fetch_fred(series_id, start='2014-01-01'):
    url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}'
    try:
        r = requests.get(url, verify=False, timeout=20)
        from io import StringIO
        df = pd.read_csv(StringIO(r.text))
        # FRED CSV has columns: DATE, <series_id>  (header row present)
        df.columns = [c.strip() for c in df.columns]
        date_col = [c for c in df.columns if 'DATE' in c.upper() or 'date' in c.lower()]
        if date_col:
            df = df.rename(columns={date_col[0]: 'date'})
        else:
            df.columns = ['date', series_id]
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df = df.dropna(subset=['date'])
        val_col = [c for c in df.columns if c != 'date'][0]
        df[val_col] = pd.to_numeric(df[val_col], errors='coerce')
        df = df[df['date'] >= start].copy()
        return df.set_index('date')[val_col].rename(series_id)
    except Exception as e:
        print(f"  FRED {series_id} failed: {e}")
        return None

print("Fetching FRED macro data...")
hy_oas  = fetch_fred('BAMLH0A0HYM2')     # US HY OAS spread (bps)
nfci    = fetch_fred('NFCI')              # Chicago Fed NFCI (weekly)
vix     = fetch_fred('VIXCLS')            # VIX as vol proxy
print(f"  HY OAS: {len(hy_oas) if hy_oas is not None else 'FAIL'} rows")
print(f"  NFCI:   {len(nfci) if nfci is not None else 'FAIL'} rows")
print(f"  VIX:    {len(vix) if vix is not None else 'FAIL'} rows")

# ── Load price data for market score ────────────────────────────────────────
print("Loading signal data...")
dl  = v2.BacktestDataloader()
sig = dl.signals.copy()
sig['date'] = pd.to_datetime(sig['date'])

spy_rows = sig[sig['ticker']=='SPY'][['date','close']].copy().set_index('date')['close']
iwm_rows = sig[sig['ticker']=='IWM'][['date','close']].copy().set_index('date')['close']

all_dates  = pd.DatetimeIndex(sorted(spy_rows.index))
START = pd.Timestamp('2017-01-01'); END = pd.Timestamp('2026-08-20')

# ── 4-State Hysteresis Engine (ported from template) ────────────────────────

def run_hysteresis(score, enter, exit_thr, confirm_days, exposure_map):
    """Template hysteresis: states 0-3, upgrade immediate, downgrade needs confirm_days."""
    values = score.to_numpy()
    n = len(values)
    exposures = np.empty(n, dtype=float)
    regimes   = np.empty(n, dtype=np.int64)
    state = 0; exit_counter = 0
    for i in range(n):
        s = values[i]
        if np.isnan(s):
            regimes[i] = state; exposures[i] = exposure_map[state]; continue
        target = 3 if s >= enter[2] else 2 if s >= enter[1] else 1 if s >= enter[0] else 0
        if target > state:
            state = target; exit_counter = 0
        elif target < state:
            if s < exit_thr[state-1]:
                exit_counter += 1
                if exit_counter >= confirm_days:
                    state -= 1; exit_counter = 0
            else:
                exit_counter = 0
        else:
            exit_counter = 0
        regimes[i] = state; exposures[i] = exposure_map[state]
    return (pd.Series(exposures, index=score.index),
            pd.Series(regimes,   index=score.index))

def build_market_score(spy, w_vol=0.40, w_trend=0.60,
                       vol_bands=(1.2,1.5,2.0), vol_values=(0.0,0.33,0.67,1.0)):
    """Market score = vol_ratio component + MA200 trend component."""
    log_ret  = np.log(spy / spy.shift(1))
    vol_10d  = log_ret.rolling(10, min_periods=5).std() * np.sqrt(252)
    vol_63d  = log_ret.rolling(63, min_periods=20).std() * np.sqrt(252)
    vr       = (vol_10d / vol_63d).clip(0, 10)

    vol_score = pd.Series(
        np.select([vr < vol_bands[0], vr < vol_bands[1], vr < vol_bands[2]],
                  [vol_values[0], vol_values[1], vol_values[2]], default=vol_values[3]),
        index=spy.index).where(vr.notna())

    ma200 = spy.rolling(200, min_periods=140).mean()
    trend = (spy < ma200).where(spy.notna() & ma200.notna()).astype(float)

    return (w_vol * vol_score + w_trend * trend).rename('market_score')

def build_credit_score(hy_oas_raw, all_dates, w_level=0.50, w_mom=0.50,
                       level_warn=400, level_stress=600, level_crisis=800,
                       mom_window=63):
    """Credit score from HY OAS: level percentile + 63d momentum (rising = bad)."""
    hy = hy_oas_raw.reindex(all_dates, method='ffill')
    # Level signal: normalize to 0-1 (400bps=warning, 600=stress, 800=crisis)
    level_sig = hy.clip(200, 1000).apply(
        lambda x: 0 if x < level_warn else
                  0.33 if x < level_stress else
                  0.67 if x < level_crisis else 1.0
    )
    # Momentum signal: HY OAS rising = bad (0=falling/flat, 1=rising fast)
    hy_mom = (hy - hy.rolling(mom_window, min_periods=30).mean())
    mom_sig = hy_mom.clip(-200, 200).apply(
        lambda x: max(0.0, x / 200.0)  # 0 when flat/falling, 1 when +200bps above rolling avg
    )
    return (w_level * level_sig + w_mom * mom_sig).rename('credit_score')

def build_recovery_signal(spy, hy_oas_raw, all_dates,
                          off_low_thr=0.20, ma50_lookback=15):
    """V-shape override: price > MA50 + off 52W low + MA50 rising + HY relief."""
    hy = hy_oas_raw.reindex(all_dates, method='ffill')
    ma50  = spy.rolling(50, min_periods=35).mean()
    low52 = spy.rolling(252, min_periods=100).min()
    above  = (spy > ma50).astype(float)
    off_low = (spy / low52 - 1)
    rising = (ma50 > ma50.shift(ma50_lookback)).astype(float)
    hy_max60 = hy.rolling(60, min_periods=30).max()
    relief = (hy_max60 - hy).clip(0) >= 50   # HY spread compressed by 50bps from 60d peak
    return ((above==1) & (off_low >= off_low_thr) & (rising==1) & relief).astype(float)

# Build base score series from IWM (more sensitive to credit stress)
print("Building regime scores...")
market_score  = build_market_score(iwm_rows.reindex(all_dates, method='ffill'))
credit_score  = build_credit_score(hy_oas, all_dates)
recovery_sig  = build_recovery_signal(iwm_rows.reindex(all_dates, method='ffill'), hy_oas, all_dates)

# ── Exposure calculator ───────────────────────────────────────────────────────

def get_exposure(dt, credit_exp, market_exp, recovery):
    """Get portfolio exposure for a rebalance date."""
    exp = min(
        float(credit_exp.reindex([dt], method='ffill').iloc[0]),
        float(market_exp.reindex([dt], method='ffill').iloc[0])
    )
    rec = float(recovery.reindex([dt], method='ffill').fillna(0).iloc[0])
    if rec == 1.0:
        exp = max(exp, 1.00)  # V-shape override: force full exposure if recovering
    return exp

_orig_screen = v2.BacktestStrategy.screen

def make_hysteresis_screener(credit_exp, market_exp, recovery, top_n=TOP_N):
    def patched(self, date):
        df = _orig_screen(self, date)
        if df.empty: return df
        if len(df) > top_n:
            df = df.nlargest(top_n, 'rs_pct').copy()
        # Override weight sum to reflect hysteresis exposure
        expo = get_exposure(date, credit_exp, market_exp, recovery)
        total_w = df['weight'].sum()
        if total_w > 1e-9:
            df['weight'] = df['weight'] / total_w * expo
        return df
    return patched

def run_config(label, credit_cfg, market_cfg, recovery_cfg=None, comb_method='min'):
    """Test one hysteresis config via v2.BacktestModel."""
    c_enter, c_exit, c_conf, c_exp_map = credit_cfg
    m_enter, m_exit, m_conf, m_exp_map = market_cfg

    c_exp, c_reg = run_hysteresis(credit_score, c_enter, c_exit, c_conf, c_exp_map)
    m_exp, m_reg = run_hysteresis(market_score,  m_enter, m_exit, m_conf, m_exp_map)

    # Combine: min (conservative) or weighted
    if comb_method == 'min':
        port_exp = pd.concat([c_exp, m_exp], axis=1).min(axis=1)
    else:
        port_exp = 0.5 * c_exp + 0.5 * m_exp

    rec = recovery_sig if recovery_cfg else pd.Series(0.0, index=all_dates)
    # V-shape override
    port_exp = port_exp.where(rec != 1.0, np.maximum(port_exp, 1.00))

    # Override exposure in screener (replaces bear_exposure/bull_exposure)
    params = BASE_PARAMS.copy()
    params['bear_exposure'] = 0.01    # effectively disabled — we override in screener
    params['bull_exposure'] = 1.00

    v2.BacktestStrategy.screen = make_hysteresis_screener(c_exp, m_exp, rec)
    try:
        p     = v2.StrategyParams(**params)
        strat = v2.BacktestStrategy(dl, p)
        port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
        r     = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
    finally:
        v2.BacktestStrategy.screen = _orig_screen

    sc   = 100 if r.get('port_cagr',0) < 5 else 1
    cagr = r.get('port_cagr', 0) * sc
    exc  = r.get('excess', 0) * sc
    dd   = r.get('port_mdd', 0) * (100 if abs(r.get('port_mdd',0)) < 1 else 1)
    sh   = r.get('sharpe', 0)
    holds = r.get('avg_holdings', 0)

    ye = {int(yr): (d['excess'] if isinstance(d,dict) else d)*100
          for yr,d in r.get('yearly',{}).items()}
    wins = sum(1 for v in ye.values() if v > 0)
    wf2  = (ye.get(2022,0) + ye.get(2023,0)) / 2

    ex_ok = exc > BASELINE['excess']; dd_ok = dd > BASELINE['dd']; sh_ok = sh > BASELINE['sharpe']
    gov   = 'STRICT' if (ex_ok and dd_ok and sh_ok) else \
            'PARTIAL' if ((ex_ok or sh_ok) and dd_ok) else 'no'

    # Regime stats
    regime_pct = (c_reg + m_reg).clip(0,3)
    avg_exp = port_exp.reindex(all_dates[(all_dates>=START)&(all_dates<=END)], method='ffill').mean()

    yr_str = ' '.join(f"{yr}:{'+' if v>=0 else ''}{v:.1f}%" for yr,v in sorted(ye.items()))
    print(f"\n{'='*65}")
    print(f"  {label}")
    print(f"  CAGR={cagr:.1f}% | Excess={exc:+.1f}% | DD={dd:.1f}%")
    print(f"  Sharpe={sh:.2f} | Holds={holds:.0f} | Win={wins}/{len(ye)} | WF2={wf2:+.1f}% | Gov={gov}")
    print(f"  Avg portfolio exposure={avg_exp*100:.1f}%")
    print(f"  {yr_str}")
    return dict(label=label, cagr=cagr, excess=exc, dd=dd, sharpe=sh,
                holds=holds, wins=wins, wf2=wf2, gov=gov, ye=ye, avg_exp=avg_exp)

# ── CONFIGS ──────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("4-STATE HYSTERESIS REGIME — Template Port")
print(f"{'='*65}")

# Benchmark: current v3.1 IWM binary (simulate via hysteresis with just market score)
# entry at market score 0.25 (vol moderate, IWM below MA200)
results = []

# A. Baseline (IWM binary approximation via market score only)
r = run_config(
    "BASELINE — IWM binary (current v3.1 approximation)",
    credit_cfg=((9,9,9),(8,8,8), 1, (1.0,1.0,1.0,1.0)),  # credit always Normal
    market_cfg=((0.40,0.60,0.80),(0.25,0.45,0.65), 5, (1.0,0.85,0.65,0.45)),
    comb_method='min'
)
results.append(r)

# B. Full 2-signal: credit (HY OAS) + market (vol+trend)
r = run_config(
    "CREDIT+MARKET (template defaults, min combine)",
    credit_cfg=((0.35,0.55,0.75),(0.20,0.40,0.60), 30, (1.0,0.80,0.55,0.35)),
    market_cfg=((0.25,0.45,0.70),(0.15,0.35,0.60), 10, (1.0,0.85,0.65,0.45)),
    recovery_cfg=True, comb_method='min'
)
results.append(r)

# C. Less aggressive credit (confirm=20 days, softer exposure steps)
r = run_config(
    "CREDIT+MARKET (softer — confirm=20, exposure 100/90/70/50)",
    credit_cfg=((0.40,0.60,0.80),(0.25,0.45,0.65), 20, (1.0,0.90,0.70,0.50)),
    market_cfg=((0.25,0.45,0.70),(0.15,0.35,0.60), 10, (1.0,0.85,0.65,0.45)),
    recovery_cfg=True, comb_method='min'
)
results.append(r)

# D. Market only (no credit), 4 states, faster confirm
r = run_config(
    "MARKET ONLY (4-state, confirm=5, 100/85/65/45)",
    credit_cfg=((9,9,9),(8,8,8), 1, (1.0,1.0,1.0,1.0)),
    market_cfg=((0.25,0.45,0.70),(0.15,0.35,0.60), 5, (1.0,0.85,0.65,0.45)),
    recovery_cfg=True, comb_method='min'
)
results.append(r)

# E. Market only, more aggressive reduction
r = run_config(
    "MARKET ONLY 4-state aggressive (100/80/60/40)",
    credit_cfg=((9,9,9),(8,8,8), 1, (1.0,1.0,1.0,1.0)),
    market_cfg=((0.25,0.45,0.70),(0.15,0.35,0.60), 5, (1.0,0.80,0.60,0.40)),
    recovery_cfg=True, comb_method='min'
)
results.append(r)

# F. Weighted combine: 40% credit + 60% market
r = run_config(
    "CREDIT+MARKET weighted (40%+60%)",
    credit_cfg=((0.35,0.55,0.75),(0.20,0.40,0.60), 30, (1.0,0.80,0.55,0.35)),
    market_cfg=((0.25,0.45,0.70),(0.15,0.35,0.60), 10, (1.0,0.85,0.65,0.45)),
    recovery_cfg=True, comb_method='weighted'
)
results.append(r)

# G. Current v3.1 actual (IWM>MA200: 100% vs 50%) for apples-to-apples
params_v31 = BASE_PARAMS.copy()
v2.BacktestStrategy.screen = _orig_screen
p     = v2.StrategyParams(**params_v31)
strat = v2.BacktestStrategy(dl, p)
port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
rr    = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
sc = 100 if rr.get('port_cagr',0)<5 else 1
ye2 = {int(yr): (d['excess'] if isinstance(d,dict) else d)*100 for yr,d in rr.get('yearly',{}).items()}
exc2=rr.get('excess',0)*sc; dd2=rr.get('port_mdd',0)*(100 if abs(rr.get('port_mdd',0))<1 else 1)
sh2=rr.get('sharpe',0); holds2=rr.get('avg_holdings',0)
wf2_2=(ye2.get(2022,0)+ye2.get(2023,0))/2; wins2=sum(1 for v in ye2.values() if v>0)
ex_ok2=exc2>BASELINE['excess']; dd_ok2=dd2>BASELINE['dd']; sh_ok2=sh2>BASELINE['sharpe']
gov2='STRICT' if (ex_ok2 and dd_ok2 and sh_ok2) else 'PARTIAL' if ((ex_ok2 or sh_ok2) and dd_ok2) else 'no'
yr_s=' '.join(f"{yr}:{'+' if v>=0 else ''}{v:.1f}%" for yr,v in sorted(ye2.items()))
print(f"\n{'='*65}\n  v3.1 ACTUAL (IWM>MA200, 100%/50%)")
print(f"  CAGR={rr.get('port_cagr',0)*sc:.1f}% | Excess={exc2:+.1f}% | DD={dd2:.1f}%")
print(f"  Sharpe={sh2:.2f} | Holds={holds2:.0f} | Win={wins2}/{len(ye2)} | WF2={wf2_2:+.1f}% | Gov={gov2}\n  {yr_s}")
results.append(dict(label="v3.1 ACTUAL (IWM binary)", cagr=rr.get('port_cagr',0)*sc, excess=exc2, dd=dd2, sharpe=sh2, holds=holds2, wins=wins2, wf2=wf2_2, gov=gov2, ye=ye2, avg_exp=0.75))

# ── SUMMARY ──────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("FULL SUMMARY — 4-STATE HYSTERESIS vs v3.1")
print(f"  {'Config':<48}  {'Excess':>7}  {'DD':>7}  {'Sh':>5}  {'Exp':>5}  {'WF2':>6}  Gov")
print("-"*105)
for r in results:
    print(f"  {r['label']:<48}  {r['excess']:>+6.1f}%  {r['dd']:>+6.1f}%  {r['sharpe']:>5.2f}"
          f"  {r.get('avg_exp',0)*100:>4.0f}%  {r['wf2']:>+5.1f}%  {r['gov']}")

baseline_r = results[-1]
print(f"\n  v3.1 LOCKED: Excess={BASELINE['excess']:+.1f}% DD={BASELINE['dd']:.1f}% Sharpe={BASELINE['sharpe']:.2f}")
gov_pass = [(r['label'],r) for r in results if r['gov'] in ('STRICT','PARTIAL')]
if gov_pass:
    print(f"\nGov PASS ({len(gov_pass)}):")
    for lbl,r in gov_pass:
        print(f"  [{r['gov']}] {lbl}: Excess={r['excess']:+.1f}% DD={r['dd']:.1f}% Sh={r['sharpe']:.2f} WF2={r['wf2']:+.1f}%")
else:
    print("\nNo config passed. Delta vs v3.1 actual (same-run):")
    b = baseline_r
    for r in results[:-1]:
        print(f"  {r['label']}: Ex{r['excess']-b['excess']:+.1f}% DD{r['dd']-b['dd']:+.1f}% Sh{r['sharpe']-b['sharpe']:+.2f} WF2{r['wf2']-b['wf2']:+.1f}%")

print(f"\n{'='*65}")
print("YEAR-BY-YEAR: best configs by Sharpe vs v3.1")
top3 = sorted(results[:-1], key=lambda x: x['sharpe'], reverse=True)[:3]
b = results[-1]
print(f"  {'Yr':<6}  {'v3.1':>8}" + "".join(f"  {r['label'][:20]:>22}" for r in top3))
for yr in range(2017,2027):
    bv = b['ye'].get(yr,0)
    row = f"  {yr}  {bv:>+7.1f}%"
    for r in top3:
        v=r['ye'].get(yr,0); flag='*' if abs(v-bv)>5 else ' '
        row += f"  {v:>+21.1f}%{flag}"
    print(row)
