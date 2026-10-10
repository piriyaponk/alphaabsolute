"""
24_vshape_reentry_test.py
V-Shape Re-entry Override — capitulation breadth signals

Problem: current system (SPY < MA200 -> 50%) stays at 50% until MA200 re-cross.
This means we MISS the recovery phase (first 2-3 months off the low = biggest gains).

Template solution (risk_overlay/exposure.py): apply_vshape_override()
  "if recovery_signal == 1: portfolio_exposure = max(portfolio_exposure, 1.00)"
  → only ever RAISES exposure, never lowers it.

Signals computed monthly (at each rebalance date, looking back 20-30 days):
  1. pct_below_lower_bb  : % S&P500 stocks below 20d lower Bollinger Band (20d SMA - 2σ)
  2. pct_at_4w_low       : % stocks where close = 20-day rolling low
  3. pct_at_52w_low      : % stocks within 5% of 252-day rolling low
  4. selling_climax      : any day in last 30d with SPY volume 1.5x+ AND drop >2%
  5. follow_through_day  : in last 15d, after >=3 down days, a day with +1.5% on high volume
  6. avg_dd_pct          : current bear drawdown from peak as % of historical avg bear DD

Governance: tested in ISOLATION vs baseline (bear=50%, no override).
Must beat baseline on: excess + WF2 + DD simultaneously.
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd
import numpy as np
import importlib.util, pathlib

_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)

# ── Load data ─────────────────────────────────────────────────────────────────
print("Loading prices.parquet and signals.parquet...")
prices = pd.read_parquet('data/backtest/prices.parquet')
prices['date'] = pd.to_datetime(prices['date'])
dl = v2.BacktestDataloader()
sig = dl.signals.copy()
sig['date'] = pd.to_datetime(sig['date'])

# ── Build breadth panel (daily, SPY 500 universe) ─────────────────────────────
print("Building breadth panel...")

all_tickers = sig['ticker'].unique()
# Use tickers that have good price history (exclude recent IPOs)
# Work with prices directly for breadth computation
price_pivot = prices.pivot_table(index='date', columns='ticker', values='close')
volume_pivot = prices.pivot_table(index='date', columns='ticker', values='volume')

# Only use common tickers (exclude SPY itself from stock breadth)
broad_tickers = [t for t in price_pivot.columns if t != 'SPY']
price_pivot = price_pivot[broad_tickers]
print(f"Breadth universe: {len(broad_tickers)} tickers")

# Rolling 20d stats for each stock
roll20_mean  = price_pivot.rolling(20, min_periods=15).mean()
roll20_std   = price_pivot.rolling(20, min_periods=15).std()
roll20_min   = price_pivot.rolling(20, min_periods=15).min()   # 4-week low
roll252_min  = price_pivot.rolling(252, min_periods=180).min() # 52-week low
lower_bb     = roll20_mean - 2 * roll20_std

# Daily breadth signals
pct_below_bb   = (price_pivot < lower_bb).sum(axis=1) / price_pivot.notna().sum(axis=1)
pct_at_4w_low  = (price_pivot <= roll20_min * 1.01).sum(axis=1) / price_pivot.notna().sum(axis=1)
pct_at_52w_low = (price_pivot <= roll252_min * 1.05).sum(axis=1) / price_pivot.notna().sum(axis=1)

breadth_daily = pd.DataFrame({
    'pct_below_bb':   pct_below_bb,
    'pct_at_4w_low':  pct_at_4w_low,
    'pct_at_52w_low': pct_at_52w_low,
}).sort_index()

# SPY daily data for climax / follow-through
spy_px = prices[prices['ticker']=='SPY'][['date','close','volume','high','low']].set_index('date').sort_index()
spy_px['ret'] = spy_px['close'].pct_change()
spy_px['vol_21d_avg'] = spy_px['volume'].rolling(21, min_periods=15).mean()
spy_px['vol_ratio'] = spy_px['volume'] / spy_px['vol_21d_avg']

# SPY drawdown from rolling 252d peak
spy_px['peak'] = spy_px['close'].rolling(252, min_periods=180).max()
spy_px['drawdown'] = (spy_px['close'] / spy_px['peak'] - 1)

print("Breadth panel built. Computing monthly snapshots...")

# ── Compute monthly breadth snapshots at each rebalance date ─────────────────
# Month-end rebalance dates: last trading day of each month
# (engine rebalances at month-end only, daily dates are noise)
all_spy_dates = pd.DatetimeIndex(sorted(sig[sig['ticker']=='SPY']['date'].unique()))
rebal_dates = pd.DatetimeIndex(
    all_spy_dates[all_spy_dates.to_series().groupby(
        all_spy_dates.to_period('M')).transform('max') == all_spy_dates]
)

# Historical average bear drawdown (use SPY bear periods: MA200 crossunder)
spy_ma200 = spy_px['close'].rolling(200, min_periods=140).mean()
spy_bear = spy_px['close'] < spy_ma200
# Compute historical average max drawdown during bear periods
bear_episodes = []
in_bear = False
ep_start = None
peak_in_ep = None
for dt, row in spy_px.iterrows():
    is_bear = bool(spy_bear.get(dt, False))
    if is_bear and not in_bear:
        in_bear = True
        ep_start = dt
        peak_in_ep = row['close']
    elif in_bear:
        peak_in_ep = max(peak_in_ep, row['close'])
        if not is_bear:
            trough = spy_px.loc[ep_start:dt, 'close'].min()
            bear_episodes.append((peak_in_ep - trough) / peak_in_ep)
            in_bear = False

avg_bear_dd = np.mean(bear_episodes) if bear_episodes else 0.20
print(f"Historical avg bear episode DD: {avg_bear_dd:.1%} (from {len(bear_episodes)} episodes)")

monthly_breadth = []
for dt in rebal_dates:
    row = {'date': dt}

    # Breadth: 30-day window ending at this date (looking back, monthly snapshot)
    b30_start = dt - pd.Timedelta(days=35)
    b30 = breadth_daily.loc[b30_start:dt]

    if len(b30) == 0:
        for k in ['pct_below_bb','pct_at_4w_low','pct_at_52w_low']:
            row[k] = np.nan
    else:
        # Use max over the 30-day window (captures worst day even if today is better)
        row['pct_below_bb_max']   = float(b30['pct_below_bb'].max())
        row['pct_at_4w_low_max']  = float(b30['pct_at_4w_low'].max())
        row['pct_at_52w_low_max'] = float(b30['pct_at_52w_low'].max())
        # Also current (at month end)
        row['pct_below_bb']   = float(b30['pct_below_bb'].iloc[-1]) if len(b30) > 0 else np.nan
        row['pct_at_4w_low']  = float(b30['pct_at_4w_low'].iloc[-1]) if len(b30) > 0 else np.nan
        row['pct_at_52w_low'] = float(b30['pct_at_52w_low'].iloc[-1]) if len(b30) > 0 else np.nan

    # Selling climax in last 30 days
    s30 = spy_px.loc[b30_start:dt] if b30_start in spy_px.index or dt in spy_px.index else spy_px.loc[:dt].tail(30)
    if len(s30) > 0:
        # Selling climax: vol_ratio >= 1.5 AND daily return < -2%
        climax_days = ((s30['vol_ratio'] >= 1.5) & (s30['ret'] < -0.02))
        row['selling_climax'] = int(climax_days.any())
        row['climax_days_count'] = int(climax_days.sum())

        # Follow-through day (IBD): 4th+ day of rally, close up ≥1.5% on volume > 21d avg
        # Simplified: look for a day in last 15d with +1.5%+ on above-avg volume, after downturn
        s15 = s30.tail(15)
        fth_candidates = ((s15['ret'] >= 0.015) & (s15['vol_ratio'] >= 1.0))
        row['follow_through'] = int(fth_candidates.any())

        # Current SPY drawdown relative to historical avg bear DD
        spy_row = s30.iloc[-1] if len(s30) > 0 else None
        if spy_row is not None:
            current_dd = abs(float(spy_row['drawdown']))
            row['dd_vs_avg'] = current_dd / avg_bear_dd  # >1.0 = worse than avg
        else:
            row['dd_vs_avg'] = 0.5
    else:
        row['selling_climax'] = 0
        row['climax_days_count'] = 0
        row['follow_through'] = 0
        row['dd_vs_avg'] = 0.5

    monthly_breadth.append(row)

breadth_monthly = pd.DataFrame(monthly_breadth).set_index('date')
print(f"Monthly breadth computed: {len(breadth_monthly)} dates")

# Show sample stats during known bear periods
for label, start, end in [('COVID crash 2020', '2020-02', '2020-06'),
                           ('2022 bear', '2022-01', '2022-12')]:
    period = breadth_monthly.loc[start:end]
    if len(period) > 0:
        print(f"\n{label}:")
        print(f"  Max pct_below_bb:  {period['pct_below_bb_max'].max():.0%}")
        print(f"  Max pct_at_4w_low: {period['pct_at_4w_low_max'].max():.0%}")
        print(f"  Selling climax months: {period['selling_climax'].sum()}")
        print(f"  Follow-through months: {period['follow_through'].sum()}")
        print(f"  Max dd_vs_avg: {period['dd_vs_avg'].max():.1f}x")

# ── Backtest machinery ────────────────────────────────────────────────────────
BASE = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=1.5, base_tight_max=9.9, adtv_min_m=10,
    cap_pct=0.18, bear_exposure=0.50, bull_exposure=1.00,
    softmax_alpha=1.0, start_date='2017-01-01', end_date='2026-08-20'
)
TOP_N = 15
_orig_screen = v2.BacktestStrategy.screen

def base_screen(self, date):
    result = _orig_screen(self, date)
    if result.empty: return result
    if len(result) > TOP_N:
        result = result.nlargest(TOP_N, 'rs_pct').copy()
        w = result['weight'].clip(upper=self.params.cap_pct)
        result['weight'] = w / w.sum() * result['weight'].sum()
    return result

def make_vshape_override(
    bb_threshold=0.40,         # % stocks below BB -> capitulation
    low4w_threshold=0.40,      # % stocks at 4w low
    low52w_threshold=0.15,     # % stocks at 52w low
    require_climax=False,      # must also have selling climax
    require_fth=False,         # must also have follow-through day
    dd_threshold=0.80,         # current DD >= X * avg bear DD
    override_exposure=1.00,    # what to set when override fires
    min_signals=1,             # how many signals must fire for override
):
    """
    Returns a per-date exposure function.
    Returns (bear_exp, bull_exp) where bear_exp may be overridden.
    """
    def get_exposure(dt, spy_above):
        # If bull: always 100%
        if spy_above:
            return 1.00

        # Bear: check if V-shape override fires
        ts = pd.Timestamp(dt)
        if ts not in breadth_monthly.index:
            return 0.50  # no data -> use default bear

        b = breadth_monthly.loc[ts]
        signals_fired = 0

        # 1. Breadth capitulation: % below BB
        if pd.notna(b.get('pct_below_bb_max')) and b['pct_below_bb_max'] >= bb_threshold:
            signals_fired += 1

        # 2. % at 4-week low
        if pd.notna(b.get('pct_at_4w_low_max')) and b['pct_at_4w_low_max'] >= low4w_threshold:
            signals_fired += 1

        # 3. % at 52-week low
        if pd.notna(b.get('pct_at_52w_low_max')) and b['pct_at_52w_low_max'] >= low52w_threshold:
            signals_fired += 1

        # 4. Selling climax
        if require_climax or not require_climax:
            if b.get('selling_climax', 0) == 1:
                signals_fired += 1 if not require_climax else signals_fired

        # 5. Follow-through day
        if b.get('follow_through', 0) == 1:
            signals_fired += 1

        # 6. DD vs historical avg
        if b.get('dd_vs_avg', 0) >= dd_threshold:
            signals_fired += 1

        if signals_fired >= min_signals:
            return override_exposure  # V-shape override
        return 0.50  # normal bear

    return get_exposure


def run_with_override(label, exposure_fn=None, bear_exp_default=0.50):
    """Run backtest with per-date exposure override via signal patching."""
    # We implement the override by modifying spy_above_ma200 in signals
    # Override months: if V-shape fires in bear, set exposure to override_exposure
    # by treating those months as "bull" (spy_above=1) with bull_exposure=override_exp

    if exposure_fn is None:
        p = v2.StrategyParams(**BASE)
        v2.BacktestStrategy.screen = base_screen
        strat = v2.BacktestStrategy(dl, p)
        port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
        r = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
    else:
        # Build per-date exposure mapping
        spy_sig = sig[sig['ticker']=='SPY'].set_index('date')
        orig_signals = dl.signals.copy()
        patched = dl.signals.copy()

        override_count = 0
        for dt in rebal_dates:
            ts = pd.Timestamp(dt)
            if ts not in spy_sig.index: continue
            spy_above_orig = int(spy_sig.loc[ts, 'spy_above_ma200'])
            effective_exp = exposure_fn(ts, bool(spy_above_orig))

            if not spy_above_orig and effective_exp > 0.50:
                # V-shape override: force spy_above=True by setting ma_200 < close
                # The engine computes: spy_above = close > ma_200
                # So we set ma_200 = close * 0.99 (just below close) to force bull mode
                spy_mask = (patched['ticker'] == 'SPY') & (patched['date'] == ts)
                spy_close = patched.loc[spy_mask, 'close'].values
                if len(spy_close) > 0:
                    patched.loc[spy_mask, 'ma_200'] = spy_close[0] * 0.99
                    override_count += 1

        dl.signals = patched
        print(f"    [override] {override_count} rebalance dates flipped to bull via V-shape")

        p = v2.StrategyParams(**{**BASE, 'bear_exposure': bear_exp_default})
        v2.BacktestStrategy.screen = base_screen
        strat = v2.BacktestStrategy(dl, p)
        port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
        r = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
        dl.signals = orig_signals

    ye = {yr: d['excess'] for yr, d in r.get('yearly', {}).items()}
    wins = sum(1 for v in ye.values() if v > 0)
    scale = 100 if r['port_cagr'] < 5 else 1
    pc = r['port_cagr'] * scale; ex = r['excess'] * scale; dd = r['port_mdd'] * scale
    sh = r['sharpe']; ca = r['calmar']; holds = r['avg_holdings']
    wf1 = sum(ye.get(yr, 0) for yr in [2020, 2021]) / 2 * scale
    wf2 = sum(ye.get(yr, 0) for yr in [2022, 2023]) / 2 * scale
    wf3 = sum(ye.get(yr, 0) for yr in [2024, 2025]) / 2 * scale
    yr_str = ' '.join(f"{yr}:{ye[yr]*scale:+.1f}%" for yr in sorted(ye))

    print(f"\n{'='*65}")
    print(f"  {label}")
    print(f"  Port CAGR={pc:.1f}% | Excess={ex:+.1f}% | DD={dd:.1f}%")
    print(f"  Sharpe={sh:.2f} | Calmar={ca:.2f} | Holds={holds:.0f} | Win={wins}/{len(ye)}")
    print(f"  WF1={wf1:+.1f}% | WF2={wf2:+.1f}% | WF3={wf3:+.1f}%")
    print(f"  {yr_str}")
    return {'label': label, 'excess': ex, 'dd': dd, 'sharpe': sh, 'calmar': ca,
            'wf2': wf2, 'wins': wins, 'n': len(ye), 'holds': holds,
            'wf1': wf1, 'wf3': wf3, 'cagr': pc}


# ── Show how often each signal fires in bear months ──────────────────────────
bear_months = breadth_monthly[breadth_monthly.index.isin(
    sig[(sig['ticker']=='SPY') & (sig['spy_above_ma200']==0)]['date'].values
)]
print(f"\n{'='*65}")
print(f"SIGNAL FREQUENCY IN BEAR MONTHS ({len(bear_months)} total)")
print(f"{'='*65}")
if len(bear_months) > 0:
    print(f"  pct_below_bb_max >= 40%: {(bear_months['pct_below_bb_max']>=0.40).sum()} months ({(bear_months['pct_below_bb_max']>=0.40).mean():.0%})")
    print(f"  pct_below_bb_max >= 30%: {(bear_months['pct_below_bb_max']>=0.30).sum()} months ({(bear_months['pct_below_bb_max']>=0.30).mean():.0%})")
    print(f"  pct_at_4w_low_max >= 40%: {(bear_months['pct_at_4w_low_max']>=0.40).sum()} months ({(bear_months['pct_at_4w_low_max']>=0.40).mean():.0%})")
    print(f"  pct_at_52w_low_max >= 15%: {(bear_months['pct_at_52w_low_max']>=0.15).sum()} months ({(bear_months['pct_at_52w_low_max']>=0.15).mean():.0%})")
    print(f"  selling_climax fired: {bear_months['selling_climax'].sum()} months ({bear_months['selling_climax'].mean():.0%})")
    print(f"  follow_through fired: {bear_months['follow_through'].sum()} months ({bear_months['follow_through'].mean():.0%})")
    print(f"  dd_vs_avg >= 0.8: {(bear_months['dd_vs_avg']>=0.80).sum()} months ({(bear_months['dd_vs_avg']>=0.80).mean():.0%})")


# ── Run all configs ───────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("V-SHAPE RE-ENTRY — Isolation Test")
print("Base: v3.0 + vol_contract=1.5 | Baseline = no override (bear=50%)")
print(f"{'='*65}")

results = []

results.append(run_with_override("BASELINE — bear=50%, no override"))

# Single signal overrides (each signal alone)
results.append(run_with_override("BB40 — bear->100% when 40%+ stocks below BB",
    make_vshape_override(bb_threshold=0.40, low4w_threshold=9, low52w_threshold=9, dd_threshold=9, min_signals=1)))

results.append(run_with_override("BB30 — bear->100% when 30%+ stocks below BB",
    make_vshape_override(bb_threshold=0.30, low4w_threshold=9, low52w_threshold=9, dd_threshold=9, min_signals=1)))

results.append(run_with_override("4WK_LOW40 — bear->100% when 40%+ stocks at 4w low",
    make_vshape_override(bb_threshold=9, low4w_threshold=0.40, low52w_threshold=9, dd_threshold=9, min_signals=1)))

results.append(run_with_override("52W_LOW15 — bear->100% when 15%+ stocks at 52w low",
    make_vshape_override(bb_threshold=9, low4w_threshold=9, low52w_threshold=0.15, dd_threshold=9, min_signals=1)))

results.append(run_with_override("CLIMAX — bear->100% on selling climax",
    make_vshape_override(bb_threshold=9, low4w_threshold=9, low52w_threshold=9, dd_threshold=9,
                         require_climax=True, min_signals=1)))

results.append(run_with_override("FTH — bear->100% on follow-through day",
    make_vshape_override(bb_threshold=9, low4w_threshold=9, low52w_threshold=9, dd_threshold=9,
                         require_fth=True, min_signals=1)))

results.append(run_with_override("DD_DEPTH — bear->100% when DD >= 80% of historical avg",
    make_vshape_override(bb_threshold=9, low4w_threshold=9, low52w_threshold=9, dd_threshold=0.80, min_signals=1)))

# Composite: 2+ signals must fire
results.append(run_with_override("COMBO2 — 2 of (BB30, 4wLow30, Climax, FTH) -> 100%",
    make_vshape_override(bb_threshold=0.30, low4w_threshold=0.30, low52w_threshold=9,
                         require_climax=True, require_fth=True, dd_threshold=9, min_signals=2)))

results.append(run_with_override("COMBO3 — 3+ signals -> 100%",
    make_vshape_override(bb_threshold=0.30, low4w_threshold=0.30, low52w_threshold=0.10,
                         require_climax=True, require_fth=True, dd_threshold=0.70, min_signals=3)))

# Partial override: don't go to 100%, go to 75%
results.append(run_with_override("BB30_75 — BB signal -> 75% (not 100%)",
    make_vshape_override(bb_threshold=0.30, low4w_threshold=9, low52w_threshold=9, dd_threshold=9,
                         override_exposure=0.75, min_signals=1)))

results.append(run_with_override("CLIMAX_75 — selling climax -> 75%",
    make_vshape_override(bb_threshold=9, low4w_threshold=9, low52w_threshold=9, dd_threshold=9,
                         require_climax=True, override_exposure=0.75, min_signals=1)))

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n\n{'Config':<52} {'CAGR':>6} {'Excess':>8} {'DD':>8} {'Sharpe':>7} {'WF1':>8} {'WF2':>8} {'Win':>6}")
print("-"*108)
b = results[0]
for r in results:
    flag = " <<" if (r['excess'] > b['excess'] and r['wf2'] >= b['wf2'] and r['dd'] >= b['dd']) else ""
    print(f"{r['label']:<52} {r['cagr']:>5.1f}% {r['excess']:>+7.1f}% {r['dd']:>7.1f}% {r['sharpe']:>7.2f} "
          f"{r['wf1']:>+7.1f}% {r['wf2']:>+7.1f}% {r['wins']}/{r['n']}{flag}")

print(f"\n[GOVERNANCE] Must beat baseline on ALL 3: excess + WF2 + DD")
for r in results[1:]:
    e = r['excess'] > b['excess']
    w = r['wf2'] >= b['wf2']
    d = r['dd'] >= b['dd']
    if e and w and d:
        print(f"  APPROVED: {r['label']}")
    else:
        reasons = []
        if not e: reasons.append(f"excess {r['excess']:+.1f}%<{b['excess']:+.1f}%")
        if not w: reasons.append(f"WF2 {r['wf2']:+.1f}%<{b['wf2']:+.1f}%")
        if not d: reasons.append(f"DD {r['dd']:.1f}%>{b['dd']:.1f}%")
        print(f"  REJECTED: {r['label'][:45]} ({', '.join(reasons)})")
