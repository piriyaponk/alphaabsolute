"""
28_regime_filter_test.py — Regime Filter Comparison

Test which index best captures the regime of our actual universe:
  A. SPY > MA200     (current baseline)
  B. QQQ > MA200     (Nasdaq-100, closest to our high-RS growth universe)
  C. IWM > MA200     (Russell 2000, small-cap leg of our universe)
  D. SPY AND QQQ     (both must be bull — stricter entry, earlier exit)
  E. SPY OR QQQ      (either bull — more permissive)
  F. 2-of-3 (SPY+QQQ+IWM) majority vote
  G. QQQ AND IWM     (growth + breadth confirmation)
  H. Equal-weight composite MA200 score (0/3, 1/3, 2/3, 3/3)

Key question: does switching from SPY to QQQ reduce DD in 2018 and 2025
              without killing the bull-market excess?
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd, numpy as np, importlib.util, pathlib

_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)

print("Loading data...")
dl  = v2.BacktestDataloader()
sig = dl.signals.copy()
sig['date'] = pd.to_datetime(sig['date'])

BASE_PARAMS = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=1.5, base_tight_max=9.9, adtv_min_m=10,
    cap_pct=0.18, bear_exposure=0.50, bull_exposure=1.00,
    softmax_alpha=1.0, start_date='2017-01-01', end_date='2026-08-20'
)
TOP_N = 15
_orig_screen = v2.BacktestStrategy.screen

# ── Pull regime signals for SPY / QQQ / IWM ──────────────────────────────────
def get_regime_series(ticker):
    """Return date -> is_bull (True/False) from signals.parquet ma_200."""
    rows = sig[sig['ticker'] == ticker][['date','close','ma_200']].copy()
    rows = rows.dropna(subset=['close','ma_200'])
    rows['bull'] = rows['close'] > rows['ma_200']
    return rows.set_index('date')['bull']

spy_bull = get_regime_series('SPY')
qqq_bull = get_regime_series('QQQ')
iwm_bull = get_regime_series('IWM')

print(f"SPY regime points: {len(spy_bull)}")
print(f"QQQ regime points: {len(qqq_bull)}")
print(f"IWM regime points: {len(iwm_bull)}")

# Show divergence: days when SPY=bull but QQQ=bear (the dangerous gap)
common_dates = spy_bull.index.intersection(qqq_bull.index)
diverge_spy_bull_qqq_bear = (spy_bull.loc[common_dates] & ~qqq_bull.loc[common_dates]).sum()
diverge_qqq_bull_spy_bear = (~spy_bull.loc[common_dates] & qqq_bull.loc[common_dates]).sum()
print(f"\nDivergence analysis ({len(common_dates)} common dates):")
print(f"  SPY=BULL but QQQ=BEAR: {diverge_spy_bull_qqq_bear} days ({diverge_spy_bull_qqq_bear/len(common_dates)*100:.1f}%)")
print(f"  QQQ=BULL but SPY=BEAR: {diverge_qqq_bull_spy_bear} days ({diverge_qqq_bull_spy_bear/len(common_dates)*100:.1f}%)")

# Show which periods had SPY=bull but QQQ=bear (most dangerous for us)
print("\nPeriods where SPY=BULL but QQQ=BEAR (we stay exposed while Nasdaq is broken):")
div = spy_bull.loc[common_dates] & ~qqq_bull.loc[common_dates]
div_periods = []
in_period = False
start = None
for dt in sorted(common_dates):
    if div.loc[dt] and not in_period:
        start = dt; in_period = True
    elif not div.loc[dt] and in_period:
        div_periods.append((start, dt, (dt-start).days))
        in_period = False
if in_period:
    div_periods.append((start, common_dates[-1], (common_dates[-1]-start).days))

for s, e, d in sorted(div_periods, key=lambda x: -x[2])[:10]:
    print(f"  {s.strftime('%Y-%m-%d')} -> {e.strftime('%Y-%m-%d')} ({d}d)")

# ── Build regime map per config ───────────────────────────────────────────────
all_dates = pd.DatetimeIndex(sorted(sig[sig['ticker']=='SPY']['date'].unique()))
rebal_dates = pd.DatetimeIndex(
    all_dates[all_dates.to_series().groupby(
        all_dates.to_period('M')).transform('max') == all_dates]
)

def build_regime_map(name):
    """Return date -> is_bull for each rebalance date."""
    result = {}
    for dt in rebal_dates:
        s = spy_bull.get(dt, True)
        q = qqq_bull.get(dt, True)
        i = iwm_bull.get(dt, True)
        if name == 'SPY':
            result[dt] = bool(s)
        elif name == 'QQQ':
            result[dt] = bool(q)
        elif name == 'IWM':
            result[dt] = bool(i)
        elif name == 'SPY_AND_QQQ':
            result[dt] = bool(s and q)
        elif name == 'SPY_OR_QQQ':
            result[dt] = bool(s or q)
        elif name == 'MAJORITY':
            result[dt] = bool(sum([s, q, i]) >= 2)
        elif name == 'QQQ_AND_IWM':
            result[dt] = bool(q and i)
        elif name == 'QQQ_AND_SPY_AND_IWM':
            result[dt] = bool(s and q and i)
    return result

def run_regime_backtest(label, regime_name, verbose=True):
    regime_map = build_regime_map(regime_name)

    # Patch: override ma_200 in signals to enforce regime_map
    patched = dl.signals.copy()
    for dt, is_bull in regime_map.items():
        spy_mask = (patched['ticker'] == 'SPY') & (patched['date'] == dt)
        spy_close = patched.loc[spy_mask, 'close'].values
        if len(spy_close) > 0:
            if is_bull:
                patched.loc[spy_mask, 'ma_200'] = spy_close[0] * 0.99  # force bull
            else:
                patched.loc[spy_mask, 'ma_200'] = spy_close[0] * 1.01  # force bear
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
    p     = v2.StrategyParams(**BASE_PARAMS)
    strat = v2.BacktestStrategy(dl, p)
    port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
    r     = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
    dl.signals = dl.signals  # reset (patched already; reload below)
    v2.BacktestStrategy.screen = _orig_screen

    ye    = {yr: (d['excess'] if isinstance(d, dict) else d) * 100
             for yr, d in r.get('yearly', {}).items()}
    wins  = sum(1 for v in ye.values() if v > 0)
    sc    = 100 if r['port_cagr'] < 5 else 1
    pc = r['port_cagr']*sc; ex = r['excess']*sc; dd = r['port_mdd']*sc
    sh = r['sharpe']; ca = r['calmar']; holds = r['avg_holdings']
    wf1 = (ye.get(2020,0)+ye.get(2021,0))/2
    wf2 = (ye.get(2022,0)+ye.get(2023,0))/2
    wf3 = (ye.get(2024,0)+ye.get(2025,0))/2

    # Bear months per regime
    bear_months = sum(1 for v in regime_map.values() if not v)

    if verbose:
        print(f"\n{'='*65}")
        print(f"  {label}  [bear months={bear_months}]")
        print(f"  CAGR={pc:.1f}% | Excess={ex:+.1f}% | DD={dd:.1f}%")
        print(f"  Sharpe={sh:.2f} | Calmar={ca:.2f} | Holds={holds:.0f} | Win={wins}/{len(ye)}")
        print(f"  WF1={wf1:+.1f}% WF2={wf2:+.1f}% WF3={wf3:+.1f}%")
        print(f"  {' '.join(f'{yr}:{ye[yr]:+.1f}%' for yr in sorted(ye))}")
    return {'label':label,'cagr':pc,'excess':ex,'dd':dd,'sharpe':sh,
            'calmar':ca,'wins':wins,'n':len(ye),'wf2':wf2,'wf1':wf1,'wf3':wf3,
            'holds':holds,'bear_months':bear_months}

# Need to reload signals after patching — reload dl for each run
import importlib
def fresh_run(label, regime_name, verbose=True):
    global dl
    dl = v2.BacktestDataloader()  # fresh signals each time
    dl.signals['date'] = pd.to_datetime(dl.signals['date'])
    return run_regime_backtest(label, regime_name, verbose)

results = []
results.append(fresh_run("A. SPY > MA200      (current baseline)", 'SPY'))
b = results[0]

results.append(fresh_run("B. QQQ > MA200      (Nasdaq-100)", 'QQQ'))
results.append(fresh_run("C. IWM > MA200      (Russell 2000)", 'IWM'))
results.append(fresh_run("D. SPY AND QQQ      (both bull)", 'SPY_AND_QQQ'))
results.append(fresh_run("E. SPY OR QQQ       (either bull)", 'SPY_OR_QQQ'))
results.append(fresh_run("F. Majority (2/3)   SPY+QQQ+IWM", 'MAJORITY'))
results.append(fresh_run("G. QQQ AND IWM      (growth+breadth)", 'QQQ_AND_IWM'))
results.append(fresh_run("H. ALL 3 > MA200    (strictest)", 'QQQ_AND_SPY_AND_IWM'))

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n\n{'='*80}")
print("REGIME FILTER COMPARISON — FULL SUMMARY")
print(f"Baseline = SPY > MA200 | CAGR={b['cagr']:.1f}% Excess={b['excess']:+.1f}% DD={b['dd']:.1f}% Sh={b['sharpe']:.2f}")
print(f"{'='*80}")
print(f"{'Config':<35} {'CAGR':>7} {'Excess':>8} {'DD':>9} {'Sharpe':>7} {'WF2':>8} {'Bear':>6} {'Win':>5} {'Gov':>8}")
print("─"*95)
for r in results:
    dd_d  = r['dd'] - b['dd']
    ex_d  = r['excess'] - b['excess']
    sh_d  = r['sharpe'] - b['sharpe']
    # Governance: STRICT = all 3 improve; RELAXED = excess+Sharpe improve, DD within 2%
    strict  = r['excess'] > b['excess'] and r['dd'] > b['dd'] and r['sharpe'] > b['sharpe']
    relaxed = r['excess'] > b['excess'] and r['sharpe'] > b['sharpe'] and r['dd'] >= b['dd'] - 2.0
    gov = "STRICT" if strict else ("RELAXED" if relaxed else "no")
    print(f"{r['label']:<35} {r['cagr']:>6.1f}% {r['excess']:>+7.1f}% "
          f"{r['dd']:>+7.1f}%({dd_d:>+.1f}) {r['sharpe']:>6.2f}({sh_d:>+.2f}) "
          f"{r['wf2']:>+7.1f}% {r['bear_months']:>6} {r['wins']}/{r['n']} {gov:>8}")

# ── Detailed year-by-year for best configs ────────────────────────────────────
print(f"\n\n{'='*80}")
print("YEAR-BY-YEAR: SPY vs QQQ vs MAJORITY (key comparison)")
print(f"{'='*80}")
key_configs = [r for r in results if any(x in r['label'] for x in ['SPY >', 'QQQ >', 'Majority'])]

years = sorted(set(yr for r in key_configs for yr in range(2017, 2027)))
print(f"{'Year':<6}", end="")
for r in key_configs:
    lbl = r['label'].split()[0] + r['label'].split()[1]
    print(f"{lbl:>16}", end="")
print()
print("─"*70)

# Need year data — re-run and capture
def fresh_run_with_yearly(label, regime_name):
    global dl
    dl = v2.BacktestDataloader()
    dl.signals['date'] = pd.to_datetime(dl.signals['date'])
    regime_map = build_regime_map(regime_name)
    patched = dl.signals.copy()
    for dt, is_bull in regime_map.items():
        spy_mask = (patched['ticker'] == 'SPY') & (patched['date'] == dt)
        spy_close = patched.loc[spy_mask, 'close'].values
        if len(spy_close) > 0:
            patched.loc[spy_mask, 'ma_200'] = spy_close[0] * (0.99 if is_bull else 1.01)
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
    p     = v2.StrategyParams(**BASE_PARAMS)
    strat = v2.BacktestStrategy(dl, p)
    port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
    r     = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
    v2.BacktestStrategy.screen = _orig_screen
    ye = {yr: (d['excess'] if isinstance(d, dict) else d)*100
          for yr, d in r.get('yearly', {}).items()}
    sc = 100 if r['port_cagr'] < 5 else 1
    return ye, r['port_mdd']*sc, r['sharpe'], r['excess']*sc

configs_to_compare = [
    ("SPY",          'SPY'),
    ("QQQ",          'QQQ'),
    ("SPY_AND_QQQ",  'SPY_AND_QQQ'),
    ("MAJORITY_2/3", 'MAJORITY'),
]
all_yearly = {}
all_meta   = {}
for lbl, name in configs_to_compare:
    ye, dd, sh, ex = fresh_run_with_yearly(lbl, name)
    all_yearly[lbl] = ye
    all_meta[lbl]   = (dd, sh, ex)

headers = list(all_yearly.keys())
print(f"{'Year':<6} " + " ".join(f"{h:>14}" for h in headers))
print("─"*75)
for yr in sorted(range(2017, 2027)):
    row = f"{yr:<6} "
    for h in headers:
        v = all_yearly[h].get(yr, np.nan)
        marker = " [!]" if pd.notna(v) and v < -10 else ("  * " if pd.notna(v) and v > 50 else "    ")
        row += f"{v:>+9.1f}%{marker} " if pd.notna(v) else f"{'N/A':>14} "
    print(row)
print("─"*75)
print(f"{'DD':6} " + " ".join(f"{all_meta[h][0]:>+13.1f}%" for h in headers))
print(f"{'Sharpe':<6} " + " ".join(f"{all_meta[h][1]:>14.2f}" for h in headers))
print(f"{'Excess':<6} " + " ".join(f"{all_meta[h][2]:>+13.1f}%" for h in headers))

# ── Worst episode analysis ────────────────────────────────────────────────────
print(f"\n\n{'='*80}")
print("REGIME SWITCH TIMING — when did each filter cut to bear?")
print("(Focus on 2018 and 2022 — the two worst drawdown periods)")
print(f"{'='*80}")

spy_regime_monthly = {}
qqq_regime_monthly = {}
maj_regime_monthly = {}
for dt in rebal_dates:
    spy_regime_monthly[dt] = spy_bull.get(dt, True)
    qqq_regime_monthly[dt] = qqq_bull.get(dt, True)
    q = qqq_bull.get(dt, True); s = spy_bull.get(dt, True); i = iwm_bull.get(dt, True)
    maj_regime_monthly[dt] = sum([s, q, i]) >= 2

for period, yr_range in [("2018 Bear", range(2018,2020)), ("2022 Bear", range(2021,2024))]:
    print(f"\n{period}:")
    print(f"  {'Month':>8} {'SPY_bull':>10} {'QQQ_bull':>10} {'MAJ_bull':>10} {'Note':>30}")
    for dt in [d for d in rebal_dates if d.year in yr_range]:
        s = spy_regime_monthly.get(dt, True)
        q = qqq_regime_monthly.get(dt, True)
        m = maj_regime_monthly.get(dt, True)
        note = ""
        if s and not q:
            note = "<-- SPY=BULL but QQQ=BEAR (danger!)"
        elif not s and q:
            note = "<-- QQQ=BULL but SPY=BEAR"
        elif not s and not q and not m:
            note = "all BEAR"
        print(f"  {dt.strftime('%Y-%m'):>8}  {'BULL' if s else 'bear':>10}  "
              f"{'BULL' if q else 'bear':>10}  {'BULL' if m else 'bear':>10}  {note:>30}")

# ── Recommendation ────────────────────────────────────────────────────────────
print(f"\n\n{'='*80}")
print("VERDICT — Which regime filter fits our universe?")
print(f"{'='*80}")

best = max(results[1:], key=lambda r: r['sharpe'])
print(f"\nBest Sharpe:  {best['label']} -> {best['sharpe']:.2f}")
best_dd = max(results[1:], key=lambda r: r['dd'])
print(f"Best DD (least negative): {best_dd['label']} -> {best_dd['dd']:.1f}%")
best_ex = max(results[1:], key=lambda r: r['excess'])
print(f"Best Excess:  {best_ex['label']} -> {best_ex['excess']:+.1f}%")

print("""
Structural argument:
  - Our universe = S&P500 + Nasdaq100 + Russell1000 (growth-heavy mix)
  - QQQ tracks Nasdaq-100 which is the dominant driver of our high-RS stocks
  - When QQQ breaks MA200 BEFORE SPY (common in rate-hike cycles):
    using SPY filter leaves us 100% exposed to growth stocks in a broken market
  - QQQ > MA200 is a tighter and more relevant gate for our specific portfolio

Recommendation: If QQQ or SPY_AND_QQQ shows better or equal DD with similar/better excess
  -> switch from SPY to QQQ (or SPY_AND_QQQ) as primary regime filter
  -> document in CLAUDE.md under SYSTEM v3.0 notes
""")
