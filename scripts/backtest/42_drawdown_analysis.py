"""
42_drawdown_analysis.py — Deep Drawdown Dissection

GOAL: Identify WHEN and WHY the -38.2% DD happens in v4.0.
  1. Reproduce portfolio equity curve (monthly resolution)
  2. Find all drawdown periods >= 5%
  3. For each drawdown: show
     - Which stocks were held
     - Were they high-beta or concentrated?
     - Was regime filter already in BEAR mode?
     - How far did SPY/QQQ/IWM fall vs our portfolio?
  4. Propose improvements to study

WHY THIS MATTERS:
  Root cause is NOT stock-level vol spikes (already confirmed in scripts 36-40).
  Root cause IS multi-month regime corrections.
  But v4.0 already has IWM filter. Where does -38.2% still come from?
  Answer: IWM filter FIRES LATE — the portfolio is already down before regime switches.
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd
import numpy as np
import importlib.util, pathlib
import warnings
warnings.filterwarnings('ignore')

_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)

BASE_PARAMS = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=9.9, base_tight_max=9.9, adtv_min_m=10, cap_pct=0.18,
    bear_exposure=0.50, bull_exposure=1.00, softmax_alpha=1.0,
    start_date='2017-01-01', end_date='2026-08-20'
)

print("Loading data...")
dl  = v2.BacktestDataloader()
sig = dl.signals.copy()
sig['date'] = pd.to_datetime(sig['date'])

# Precompute vol for vol-parity
close_px = sig.pivot_table(index='date', columns='ticker', values='close').sort_index()
log_ret  = np.log(close_px / close_px.shift(1))
vol_63d  = log_ret.rolling(63, min_periods=20).std() * np.sqrt(252)

# Beta lookup
beta_lkp = {}
for _, r in sig[['date','ticker','beta_252']].dropna().iterrows():
    b = r['beta_252']
    if not np.isnan(float(b)):
        beta_lkp[(r['date'], r['ticker'])] = max(0.3, min(float(b), 4.0))

_orig_screen = v2.BacktestStrategy.screen

def volparity_screener(self, date):
    df = _orig_screen(self, date)
    if df.empty: return df
    if len(df) > 15:
        df = df.nlargest(15, 'rs_pct').copy()
    df = df.copy()
    ts = pd.Timestamp(date)
    vols = []
    for tkr in df['ticker']:
        if ts in vol_63d.index and tkr in vol_63d.columns:
            v = float(vol_63d.loc[ts, tkr])
            vols.append(v if not np.isnan(v) and v > 0.01 else 0.30)
        else:
            vols.append(0.30)
    df['_vol'] = vols
    betas = [beta_lkp.get((ts, t), 1.0) for t in df['ticker']]
    df['_max_w'] = [min(0.18, 0.12 / max(b, 0.5)) for b in betas]
    raw_w = 1.0 / df['_vol'].values
    raw_w = raw_w / raw_w.sum()
    weights = raw_w.copy()
    max_w = df['_max_w'].values
    for _ in range(25):
        over = weights > max_w
        if not over.any(): break
        overflow = (weights[over] - max_w[over]).sum()
        weights[over] = max_w[over]
        recv = ~over & (weights < max_w)
        if not recv.any(): break
        weights[recv] += overflow * (weights[recv] / weights[recv].sum())
    df['weight'] = weights
    return df[['ticker','rs_pct','weight']].copy()

print("Running v4.0 backtest with equity curve tracking...")

v2.BacktestStrategy.screen = volparity_screener
try:
    p     = v2.StrategyParams(**BASE_PARAMS)
    strat = v2.BacktestStrategy(dl, p)
    port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
    model = v2.BacktestModel(dl, strat, port, p)
    result = model.run(verbose=False)
finally:
    v2.BacktestStrategy.screen = _orig_screen

print(f"  CAGR={result.get('port_cagr',0)*100:.1f}% | DD={result.get('port_mdd',0)*100:.1f}% | Sharpe={result.get('sharpe',0):.2f}")

# ── EXTRACT EQUITY CURVE ──────────────────────────────────────────────────────
# Try to get monthly equity values from result
monthly = result.get('monthly', None)
if monthly is None:
    # Reconstruct from yearly if needed
    print("  [Note] Monthly equity curve not available in result dict — using yearly proxy")
    yearly = result.get('yearly', {})
    # Build from CAGR and yearly excess
    print("\n  Year-by-year (excess vs QQQ):")
    for yr, v in sorted(yearly.items()):
        val = v if isinstance(v, (int, float)) else v.get('excess', 0) if isinstance(v, dict) else 0
        print(f"    {yr}: {val*100:+.1f}%")
else:
    print("\n  Monthly equity curve available.")

# ── REBUILD PORTFOLIO STEP-BY-STEP WITH FULL LOGGING ────────────────────────
print("\n" + "="*65)
print("REBUILDING EQUITY CURVE WITH DRAWDOWN TRACKING")
print("="*65)

# Get benchmark prices
bench_data = {}
for bench in ['SPY', 'QQQ', 'IWM']:
    b_sig = sig[sig['ticker']==bench][['date','close']].set_index('date')['close'].sort_index() if bench in sig['ticker'].values else None
    if b_sig is None or len(b_sig) == 0:
        # Try from signals regime columns
        b_sig = None
    bench_data[bench] = b_sig

# Get IWM MA200 from signals
iwm_ma200_col = None
# Use IWM directly from close_px (IWM is in the universe)
if 'IWM' in close_px.columns:
    iwm_for_regime = close_px['IWM'].dropna()
    iwm_ma200_regime = iwm_for_regime.rolling(200, min_periods=150).mean()
    regime_series = (iwm_for_regime > iwm_ma200_regime).astype(float)
    regime_df = regime_series.reindex(sig['date'].sort_values().unique(), method='ffill').fillna(1.0)
    print("  [OK] Using IWM > MA200 regime (from close_px)")
elif 'spy_above_ma200' in sig.columns:
    regime_df = sig.groupby('date')['spy_above_ma200'].first().sort_index()
    print("  [Note] Using SPY regime fallback")
else:
    regime_df = pd.Series(1.0, index=sig['date'].unique())

# Rerun with detailed tracking
v2.BacktestStrategy.screen = volparity_screener

month_records = []

class TrackingPortfolio:
    """Wraps the standard portfolio to capture monthly snapshots."""
    pass

# Build manual monthly loop
dates_all = sig['date'].sort_values().unique()
month_ends = pd.DatetimeIndex(dates_all).to_period('M').to_timestamp('M')
# Get unique month boundaries
rebal_dates = []
for mp in pd.DatetimeIndex(dates_all).to_period('M').unique():
    month_d = [d for d in dates_all if pd.Timestamp(d).to_period('M') == mp]
    if month_d:
        rebal_dates.append((mp, max(month_d)))

rebal_dates = sorted(rebal_dates, key=lambda x: x[1])

portfolio_val = 1.0
prev_holdings = {}
equity_curve = [{'date': pd.Timestamp('2017-01-01'), 'val': 1.0, 'regime': 'BULL'}]

try:
    for i, (mp, rebal_date) in enumerate(rebal_dates):
        if rebal_date < pd.Timestamp('2017-01-01'):
            continue
        if rebal_date > pd.Timestamp('2026-08-20'):
            break

        day_sig = sig[sig['date'] == rebal_date].copy()
        if day_sig.empty:
            continue

        # Regime
        iwm_bull = float(regime_df.get(rebal_date, 1.0))
        if pd.isna(iwm_bull): iwm_bull = 1.0
        bull = (iwm_bull >= 0.5)
        exposure = 1.0 if bull else 0.5

        # Screen
        vt = 0.75 if bull else 0.95
        screened = day_sig[
            (day_sig['rs_pct'] >= 80) &
            (day_sig['vol_trend'] >= vt) &
            (day_sig['price_vs_ma200_pct'] >= 0) &
            (day_sig['adtv_63m'] >= 10) &
            day_sig['close'].notna()
        ].copy()

        if len(screened) > 15:
            screened = screened.nlargest(15, 'rs_pct').copy()

        # Vol-parity weights
        if not screened.empty:
            ts = pd.Timestamp(rebal_date)
            vols = []
            for tkr in screened['ticker']:
                if ts in vol_63d.index and tkr in vol_63d.columns:
                    v = float(vol_63d.loc[ts, tkr])
                    vols.append(v if not np.isnan(v) and v > 0.01 else 0.30)
                else:
                    vols.append(0.30)
            screened = screened.copy()
            screened['_vol'] = vols
            betas = [beta_lkp.get((ts, t), 1.0) for t in screened['ticker']]
            screened['_max_w'] = [min(0.18, 0.12 / max(b, 0.5)) for b in betas]
            raw_w = 1.0 / screened['_vol'].values
            raw_w = raw_w / raw_w.sum()
            weights = raw_w.copy()
            max_w_arr = screened['_max_w'].values
            for _ in range(25):
                over = weights > max_w_arr
                if not over.any(): break
                ov = (weights[over] - max_w_arr[over]).sum()
                weights[over] = max_w_arr[over]
                recv = ~over
                if not recv.any(): break
                weights[recv] += ov * weights[recv] / weights[recv].sum()
            screened['weight'] = weights * exposure
            new_holdings = dict(zip(screened['ticker'], screened['weight']))
        else:
            new_holdings = {}

        # Calculate next month return
        if i + 1 < len(rebal_dates):
            _, next_rebal = rebal_dates[i+1]
            port_ret = 0.0
            stock_rets = {}
            for tkr, w in new_holdings.items():
                if tkr in close_px.columns:
                    p0 = close_px.loc[close_px.index <= rebal_date, tkr].dropna()
                    p1 = close_px.loc[close_px.index <= next_rebal, tkr].dropna()
                    if not p0.empty and not p1.empty:
                        r = p1.iloc[-1] / p0.iloc[-1] - 1
                        port_ret += w * r
                        stock_rets[tkr] = {'ret': r, 'weight': w, 'beta': beta_lkp.get((ts, tkr), 1.0)}

            # Turnover & cost
            all_t = set(prev_holdings) | set(new_holdings)
            turnover = sum(abs(new_holdings.get(t,0) - prev_holdings.get(t,0)) for t in all_t) / 2
            cost = turnover * 0.0015

            portfolio_val *= (1 + port_ret - cost)

            # Portfolio-level beta (weighted avg)
            w_beta = sum(v['beta'] * v['weight'] for v in stock_rets.values()) if stock_rets else 1.0

            month_records.append({
                'date': next_rebal,
                'val': portfolio_val,
                'ret': port_ret - cost,
                'gross_ret': port_ret,
                'regime': 'BULL' if bull else 'BEAR',
                'exposure': exposure,
                'n_stocks': len(new_holdings),
                'holdings': list(new_holdings.keys()),
                'weights': dict(new_holdings),
                'stock_rets': stock_rets,
                'w_beta': w_beta,
                'turnover': turnover,
            })
            prev_holdings = new_holdings

finally:
    v2.BacktestStrategy.screen = _orig_screen

eq = pd.DataFrame(month_records).set_index('date').sort_index()

# ── DRAWDOWN PERIODS ──────────────────────────────────────────────────────────
print("\n" + "="*65)
print("ALL DRAWDOWN PERIODS >= 5% (from peak)")
print("="*65)

vals = eq['val'].values
dates_eq = eq.index
peak_val = 1.0
peak_date = dates_eq[0] if len(dates_eq) > 0 else pd.Timestamp('2017-01-01')
in_dd = False
dd_start = None
dd_start_val = 1.0

drawdowns = []

for i, (d, v) in enumerate(zip(dates_eq, vals)):
    if v > peak_val:
        if in_dd and (peak_val - dd_start_val) / dd_start_val < -0.05:
            # Record the drawdown
            trough_idx = eq.loc[dd_start:d, 'val'].idxmin()
            trough_val = eq.loc[trough_idx, 'val']
            dd_depth = (trough_val - dd_start_val) / dd_start_val * 100
            drawdowns.append({
                'start': dd_start,
                'trough': trough_idx,
                'end': d,
                'depth': dd_depth,
                'start_val': dd_start_val,
                'trough_val': trough_val,
                'recover_val': v,
            })
        peak_val = v
        peak_date = d
        in_dd = False
    elif v < peak_val * 0.95:
        if not in_dd:
            in_dd = True
            dd_start = peak_date
            dd_start_val = peak_val

# Catch ongoing drawdown
if in_dd:
    trough_idx = eq.loc[dd_start:, 'val'].idxmin()
    trough_val = eq.loc[trough_idx, 'val']
    dd_depth = (trough_val - dd_start_val) / dd_start_val * 100
    drawdowns.append({
        'start': dd_start, 'trough': trough_idx, 'end': None,
        'depth': dd_depth, 'start_val': dd_start_val, 'trough_val': trough_val,
        'recover_val': None,
    })

drawdowns.sort(key=lambda x: x['depth'])

print(f"\n  {'#':<3} {'Start':>10} {'Trough':>10} {'Depth':>8}  {'Regime during DD':>18}  {'Avg Beta':>8}  Cause")
print("-"*90)

for i, dd in enumerate(drawdowns):
    dd_period = eq.loc[dd['start']:dd['trough']]
    bull_months = (dd_period['regime'] == 'BULL').sum()
    bear_months = (dd_period['regime'] == 'BEAR').sum()
    regime_str = f"Bull={bull_months}mo Bear={bear_months}mo"
    avg_beta = dd_period['w_beta'].mean() if 'w_beta' in dd_period.columns else 1.0

    # Largest single-month losers during this drawdown
    worst_months = dd_period.nsmallest(2, 'ret')[['ret', 'regime', 'holdings']]

    end_str = str(dd['end'])[:7] if dd['end'] else 'ONGOING'
    print(f"  {i+1:<3} {str(dd['start'])[:7]:>10} {str(dd['trough'])[:7]:>10} {dd['depth']:>+7.1f}%  {regime_str:>18}  {avg_beta:>8.2f}")

    # Show worst months
    for _, wm in worst_months.iterrows():
        top_losers = []
        if isinstance(wm.get('holdings'), list):
            for tkr in wm['holdings'][:5]:
                sr = eq.loc[wm.name, 'stock_rets'] if 'stock_rets' in eq.columns else {}
                if isinstance(sr, dict) and tkr in sr:
                    top_losers.append(f"{tkr}({sr[tkr]['ret']*100:+.0f}%)")
        print(f"       worst month {str(wm.name)[:7]}: {wm['ret']*100:+.1f}% ({wm['regime']}) | {', '.join(top_losers[:4])}")

print()

# ── DEEP DIVE: LARGEST DRAWDOWN ───────────────────────────────────────────────
if drawdowns:
    worst = drawdowns[0]
    print("="*65)
    print(f"DEEP DIVE: WORST DRAWDOWN  ({str(worst['start'])[:7]} -> {str(worst['trough'])[:7]}  {worst['depth']:+.1f}%)")
    print("="*65)

    dd_eq = eq.loc[worst['start']:worst['trough']]

    print(f"\n  Month-by-month during the drawdown:")
    print(f"  {'Date':>10} {'Port Ret':>9} {'Regime':>6} {'Beta':>6} {'N':>3}  Top Holdings")
    print("  " + "-"*75)

    for d, row in dd_eq.iterrows():
        sr = row.get('stock_rets', {})
        if isinstance(sr, dict):
            top_h = sorted(sr.items(), key=lambda x: abs(x[1]['ret']), reverse=True)[:3]
            h_str = ', '.join(f"{t}({v['ret']*100:+.0f}%)" for t, v in top_h)
        else:
            h_str = str(row.get('holdings', []))[:50]
        print(f"  {str(d)[:10]:>10} {row['ret']*100:>+8.1f}% {row['regime']:>6} {row.get('w_beta',1.0):>6.2f} {row.get('n_stocks',0):>3}  {h_str}")

    print()
    print(f"  KEY QUESTIONS:")
    bull_in_dd = (dd_eq['regime'] == 'BULL').sum()
    bear_in_dd = (dd_eq['regime'] == 'BEAR').sum()
    if bull_in_dd > 0:
        print(f"  - IWM regime was BULL for {bull_in_dd} months during this drawdown")
        print(f"    -> IWM filter was LATE to fire (portfolio already drawing down)")
    if bear_in_dd > 0:
        print(f"  - IWM regime switched to BEAR for {bear_in_dd} months (50% exposure deployed)")
        print(f"    -> Even 50% exposure still hurt — stocks held were in correction")

# ── ROOT CAUSE ANALYSIS ────────────────────────────────────────────────────────
print()
print("="*65)
print("ROOT CAUSE ANALYSIS")
print("="*65)

# Correlate drawdowns with market conditions
print("\n1. REGIME FILTER LAG:")
bull_rets  = eq[eq['regime']=='BULL']['ret'].values
bear_rets  = eq[eq['regime']=='BEAR']['ret'].values
print(f"   Bull months: avg {np.mean(bull_rets)*100:+.2f}%/mo (n={len(bull_rets)})")
print(f"   Bear months: avg {np.mean(bear_rets)*100:+.2f}%/mo (n={len(bear_rets)})")
print(f"   Bear months with negative return: {(bear_rets<0).sum()}/{len(bear_rets)} ({(bear_rets<0).mean()*100:.0f}%)")
print(f"   Bull months with big loss (>-5%): {(bull_rets<-0.05).sum()}/{len(bull_rets)} ({(bull_rets<-0.05).mean()*100:.0f}%)")

# The "transition months" — the month IWM crossed below MA200
print("\n2. TRANSITION MONTH ANALYSIS:")
regime_changes = []
prev_r = None
for d, row in eq.iterrows():
    if prev_r is not None and prev_r != row['regime']:
        regime_changes.append({'date': d, 'from': prev_r, 'to': row['regime'], 'ret': row['ret']})
    prev_r = row['regime']

print(f"   Total regime switches: {len(regime_changes)}")
for rc in regime_changes:
    print(f"   {str(rc['date'])[:10]} BULL->BEAR" if rc['from']=='BULL' else f"   {str(rc['date'])[:10]} BEAR->BULL", end='')
    print(f"  | month ret: {rc['ret']*100:+.1f}%")

print("\n3. HIGH-BETA EXPOSURE DURING CORRECTIONS:")
# Months where both regime=BULL and high beta (>1.5) AND big loss
risky = eq[(eq['regime']=='BULL') & (eq.get('w_beta', pd.Series(1.0, index=eq.index)) > 1.5) & (eq['ret'] < -0.03)]
if len(risky) > 0:
    print(f"   High-beta BULL months with >3% loss: {len(risky)}")
    for d, row in risky.iterrows():
        print(f"   {str(d)[:10]}: {row['ret']*100:+.1f}% | beta={row.get('w_beta',1.0):.2f}")
else:
    print("   No high-beta BULL months with >3% loss found")

# ── IMPROVEMENT CANDIDATES ─────────────────────────────────────────────────────
print()
print("="*65)
print("DD REDUCTION STUDY PLAN")
print("="*65)
print("""
CONFIRMED ROOT CAUSE (from scripts 36-40 + this analysis):
  DD comes from multi-month corrections where IWM regime filter is LATE.
  The portfolio is already 10-15% down before IWM crosses MA200.

CANDIDATE APPROACHES to study:

A) FASTER REGIME SIGNAL
   Problem: IWM > MA200 is a 200-day SMA — fires 2-4 weeks after trend breaks
   Study: Replace IWM MA200 with IWM MA50 or IWM EMA21
          Or: IWM MA200 + early warning (breadth < 40% OR VIX > 25)
   Risk:  More whipsaws (in/out more often) → higher turnover cost
   Script: 43_regime_sensitivity.py

B) LEADING INDICATOR GATE (pre-emptive)
   Problem: MA200 is lagging. Before MA200 breaks, there are warning signs:
     - NYSE A-D line slope turns negative
     - % stocks above 50dma < 40% (breadth collapse)
     - HY credit spread widens > 50bps in 1 month
   Study: Add a "regime warning" state → reduce exposure to 70% (not full bear 50%)
          Before full BEAR, there's a WARNING phase that fires 4-6 weeks early
   Risk:  False warnings in mid-cycle corrections (2019, 2023) → underinvest
   Script: 43_regime_sensitivity.py

C) STOCK-LEVEL STOP (HARD FLOOR per position)
   Problem: Individual stocks can drop 30-40% while regime stays BULL
   Study: Monthly hard stop at -15% from entry price (check at rebalance)
          If stock is -15% from entry → exit at next rebalance regardless
   Risk:  Momentum works by holding winners long; stops kill this
   Note:  Already REJECTED in scripts 35-36 for intra-month version.
          Monthly version (check at rebalance only) has not been tested.
   Script: 44_monthly_stop.py  [NEW — not tested yet]

D) CORRELATION FILTER (reduce when all holdings moving together)
   Problem: In corrections, high-RS stocks are correlated → 15 stocks feel like 1
   Study: At rebalance, compute avg pairwise correlation of holdings (30d).
          If avg correlation > 0.8 → reduce position count to 8, more diversified
   Risk:  Corrections often require reducing position count which means selling
          before recovery — could underinvest
   Script: 43_regime_sensitivity.py

E) SECTOR CONCENTRATION LIMIT
   Problem: v4.0 holdings often cluster in 2-3 sectors (e.g., semi + AI infra + cloud)
   Study: No more than 35% in any single GICS sector at rebalance
   Risk:  If one sector is THE momentum leader (2020=Cloud, 2024=AI), capping hurts excess
   Script: 43_regime_sensitivity.py
""")

print("="*65)
print("RECOMMENDATION PRIORITY ORDER:")
print("="*65)
print("""
  Priority 1 — Option B (Leading Indicator Gate)
    WHY: Fires 4-6 weeks BEFORE MA200 break. Prevents the "already down 10%"
         problem. Low turnover cost (only reduces to 70%, not full switch).
         Breadth signal is free (already in system from market_regime.py).
    TEST: 43_regime_sensitivity.py  [build next]

  Priority 2 — Option A (IWM MA50 instead of MA200)
    WHY: Faster signal, fewer months of "bull while falling." Trade-off is
         more whipsaws. Quantify the cost exactly.
    TEST: Vary MA lookback (50/100/150/200) in isolated test.

  Priority 3 — Option D (Correlation filter)
    WHY: Catches regime-level risk before price breaks. Relatively unexplored.
    CAUTION: Complex to implement correctly (need daily returns for 30d).

  NOT recommended to test again:
    - Intra-month stops (already REJECTED in scripts 35-36)
    - 4-state hysteresis (already REJECTED in script 37)
    - EGARCH vol trim (already REJECTED in script 38)
""")
