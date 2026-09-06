"""
26_drawdown_autopsy.py — Drawdown Dissection Tool

วิเคราะห์ว่า drawdown ใหญ่ๆ เกิดตอนไหน สาเหตุจากอะไร และแก้ยังไงได้บ้าง

Output:
1. Peak-to-trough episodes ทุกอัน (>3%) + timeline
2. Per-episode autopsy: regime / holdings / which stocks killed it
3. Attribution: ส่วนไหนของพอร์ตโดนมากที่สุด
4. Hypothesis: fix candidate สำหรับแต่ละ episode
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd, numpy as np, importlib.util, pathlib

_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)

# ── Run baseline and capture detailed trade/NAV data ──────────────────────────
print("Loading baseline (SYSTEM v3.0 + expanded universe)...")
BASE_PARAMS = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=1.5, base_tight_max=9.9, adtv_min_m=10,
    cap_pct=0.18, bear_exposure=0.50, bull_exposure=1.00,
    softmax_alpha=1.0, start_date='2017-01-01', end_date='2026-08-20'
)
TOP_N = 15

dl   = v2.BacktestDataloader()
sig  = dl.signals.copy()
sig['date'] = pd.to_datetime(sig['date'])
prices = pd.read_parquet('data/backtest/prices.parquet')
prices['date'] = pd.to_datetime(prices['date'])

_orig_screen = v2.BacktestStrategy.screen
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
model = v2.BacktestModel(dl, strat, port, p)
result = model.run(verbose=False)
v2.BacktestStrategy.screen = _orig_screen

scale = 100 if result['port_cagr'] < 5 else 1
print(f"Baseline: CAGR={result['port_cagr']*scale:.1f}% | Excess={result['excess']*scale:+.1f}% | "
      f"DD={result['port_mdd']*scale:.1f}% | Sharpe={result['sharpe']:.2f}")

# ── Get NAV series ─────────────────────────────────────────────────────────────
nav_raw   = result.get('nav_series')
bench_raw = result.get('bench_series')

if nav_raw is not None:
    nav = pd.Series(nav_raw) if isinstance(nav_raw, dict) else nav_raw
    nav.index = pd.to_datetime(nav.index)
    bench = None
    if bench_raw is not None:
        bench = pd.Series(bench_raw) if isinstance(bench_raw, dict) else bench_raw
        bench.index = pd.to_datetime(bench.index)
else:
    # Build yearly NAV from per-year excess dict
    ye = result.get('yearly', {})
    print("Note: nav_series not available, building from yearly data")
    # yearly values: dict of {yr: {'excess': x, 'dd': y}} in fractional form
    # Get bench_cagr per year from bench_series if available
    dates, port_vals, bench_vals = [], [], []
    pv = bv = 1.0
    bench_yr_cagr = (1 + result['bench_cagr']) ** (1/max(len(ye),1)) - 1 if ye else 0
    for yr in sorted(ye):
        yr_data = ye[yr]
        yr_excess = yr_data['excess'] if isinstance(yr_data, dict) else yr_data
        yr_bench_ret = bench_yr_cagr  # approximate
        yr_port_ret  = yr_excess + yr_bench_ret
        pv *= (1 + yr_port_ret); bv *= (1 + yr_bench_ret)
        dates.append(pd.Timestamp(f'{yr}-12-31'))
        port_vals.append(pv); bench_vals.append(bv)
    nav   = pd.Series(port_vals, index=dates)
    bench = pd.Series(bench_vals, index=dates)

print(f"NAV series: {len(nav)} points, {nav.index[0].date()} to {nav.index[-1].date()}")

# ── Find all drawdown episodes ────────────────────────────────────────────────
def find_episodes(nav_series, min_dd=0.05):
    """Find peak-to-trough episodes >= min_dd (5% default)."""
    episodes = []
    peak_val = nav_series.iloc[0]
    peak_dt  = nav_series.index[0]
    in_dd    = False
    trough_val = peak_val
    trough_dt  = peak_dt

    for dt, val in nav_series.items():
        if val > peak_val:
            if in_dd:
                dd_pct = (trough_val / peak_val - 1) * 100
                episodes.append({
                    'peak_dt': peak_dt, 'trough_dt': trough_dt, 'recover_dt': dt,
                    'peak_nav': peak_val, 'trough_nav': trough_val,
                    'dd_pct': dd_pct,
                    'duration_days': (trough_dt - peak_dt).days,
                    'recovery_days': (dt - trough_dt).days,
                })
            peak_val = val; peak_dt = dt
            trough_val = val; trough_dt = dt; in_dd = False
        elif val < trough_val:
            trough_val = val; trough_dt = dt
            dd_now = (trough_val / peak_val - 1) * 100
            if dd_now <= -min_dd * 100:
                in_dd = True

    if in_dd:
        dd_pct = (trough_val / peak_val - 1) * 100
        episodes.append({
            'peak_dt': peak_dt, 'trough_dt': trough_dt, 'recover_dt': None,
            'peak_nav': peak_val, 'trough_nav': trough_val,
            'dd_pct': dd_pct,
            'duration_days': (trough_dt - peak_dt).days,
            'recovery_days': None,
        })
    return sorted(episodes, key=lambda x: x['dd_pct'])

episodes = find_episodes(nav, min_dd=0.05)

print(f"\n{'='*70}")
print(f"DRAWDOWN EPISODES >= 5%  (total: {len(episodes)})")
print(f"{'='*70}")
print(f"{'Peak':>12} {'Trough':>12} {'Recover':>12} {'DD%':>8} {'Dur(d)':>8} {'Rec(d)':>8}")
print("-"*70)
for ep in episodes:
    rec = ep['recover_dt'].strftime('%Y-%m') if ep['recover_dt'] else 'ongoing'
    print(f"{ep['peak_dt'].strftime('%Y-%m-%d'):>12} "
          f"{ep['trough_dt'].strftime('%Y-%m-%d'):>12} "
          f"{rec:>12} "
          f"{ep['dd_pct']:>7.1f}% "
          f"{ep['duration_days']:>8} "
          f"{str(ep['recovery_days'] or 'N/A'):>8}")

# ── Per-episode autopsy ───────────────────────────────────────────────────────
print(f"\n{'='*70}")
print("EPISODE AUTOPSY — Top 5 worst drawdowns")
print(f"{'='*70}")

# Get month-end rebalance dates
spy_sig = sig[sig['ticker']=='SPY'].set_index('date').sort_index()
all_spy_dates = pd.DatetimeIndex(sorted(spy_sig.index))
rebal_dates = pd.DatetimeIndex(
    all_spy_dates[all_spy_dates.to_series().groupby(
        all_spy_dates.to_period('M')).transform('max') == all_spy_dates]
)

# Price pivot for return attribution
price_pivot = prices.pivot_table(index='date', columns='ticker', values='close')

for ep in episodes[:5]:
    peak = ep['peak_dt']; trough = ep['trough_dt']
    print(f"\n--- Episode: {peak.strftime('%Y-%m-%d')} -> {trough.strftime('%Y-%m-%d')} "
          f"| DD={ep['dd_pct']:.1f}% | Duration={ep['duration_days']}d ---")

    # Rebalance dates inside this episode
    ep_rebals = [d for d in rebal_dates if peak <= d <= trough]
    print(f"  Rebalance dates in episode: {[d.strftime('%Y-%m') for d in ep_rebals]}")

    # For each rebalance in episode: what did the screen pick + regime
    for rb in ep_rebals:
        if rb not in spy_sig.index:
            continue
        spy_close = float(spy_sig.loc[rb, 'close'])
        spy_ma200 = float(spy_sig.loc[rb, 'ma_200'])
        regime = 'BULL' if spy_close > spy_ma200 else 'BEAR'

        # Stocks held (reconstruct screen)
        rb_sig = sig[sig['date'] == rb].copy()
        if rb_sig.empty:
            continue

        # Apply basic filters
        cands = rb_sig[
            (rb_sig['rs_pct'] >= 80) &
            (rb_sig['ticker'] != 'SPY') &
            (rb_sig['adtv_63m'] >= 10)
        ].copy()

        # vol_trend filter (regime-conditional)
        vt_thr = 0.75 if regime == 'BULL' else 0.95
        if 'vol_trend' in cands.columns:
            cands = cands[cands['vol_trend'] >= vt_thr]

        # ma200
        if 'close' in cands.columns and 'ma_200' in cands.columns:
            cands = cands[cands['close'] > cands['ma_200']]

        if len(cands) > TOP_N:
            cands = cands.nlargest(TOP_N, 'rs_pct')

        # Calculate next-month return for each stock
        next_rb_idx = list(rebal_dates).index(rb) + 1 if rb in rebal_dates else None
        next_rb = rebal_dates[next_rb_idx] if next_rb_idx and next_rb_idx < len(rebal_dates) else None

        print(f"\n  [{rb.strftime('%Y-%m')}] Regime={regime} | SPY={spy_close:.0f} vs MA200={spy_ma200:.0f} | "
              f"Stocks held={len(cands)}")

        if len(cands) > 0:
            stock_info = []
            for _, row in cands.iterrows():
                tk = row['ticker']
                ret = np.nan
                if next_rb is not None and tk in price_pivot.columns:
                    p_now  = price_pivot.loc[rb, tk]   if rb     in price_pivot.index else np.nan
                    p_next = price_pivot.loc[next_rb, tk] if next_rb in price_pivot.index else np.nan
                    if pd.notna(p_now) and pd.notna(p_next) and p_now > 0:
                        ret = (p_next / p_now - 1) * 100
                stock_info.append({
                    'ticker': tk,
                    'rs_pct': row.get('rs_pct', np.nan),
                    'vol_trend': row.get('vol_trend', np.nan),
                    'next_ret': ret
                })

            df_s = pd.DataFrame(stock_info).sort_values('next_ret')
            print(f"  {'Ticker':>8} {'RS':>6} {'VT':>6} {'NxtRet':>8}")
            for _, r in df_s.iterrows():
                flag = " <-- KILLER" if r['next_ret'] < -15 else (" <-- BAD" if r['next_ret'] < -8 else "")
                print(f"  {r['ticker']:>8} {r['rs_pct']:>6.0f} {r['vol_trend']:>6.2f} "
                      f"{r['next_ret']:>+7.1f}%{flag}")

            avg_ret = df_s['next_ret'].mean()
            worst5  = df_s['next_ret'].nsmallest(5).mean()
            print(f"  Avg return: {avg_ret:+.1f}% | Worst 5 avg: {worst5:+.1f}%")

    # Benchmark comparison
    if bench is not None:
        bench_slice = bench.loc[peak:trough]
        if len(bench_slice) > 0:
            bench_dd = (bench_slice.min() / bench_slice.iloc[0] - 1) * 100
            print(f"\n  QQQ DD same period: {bench_dd:.1f}%  (our DD: {ep['dd_pct']:.1f}%)")
            excess_dd = ep['dd_pct'] - bench_dd
            print(f"  Excess DD vs QQQ: {excess_dd:+.1f}%  "
                  f"({'we lost MORE than benchmark' if excess_dd < 0 else 'we lost LESS than benchmark'})")

# ── Attribution: what caused each year's biggest DD ──────────────────────────
print(f"\n{'='*70}")
print("YEAR-BY-YEAR DRAWDOWN ATTRIBUTION")
print(f"{'='*70}")

yearly = result.get('yearly', {})
for yr in sorted(yearly):
    yr_data   = yearly[yr]
    yr_excess = (yr_data['excess'] if isinstance(yr_data, dict) else yr_data) * scale
    yr_ep = [e for e in episodes
             if e['peak_dt'].year == yr or e['trough_dt'].year == yr]
    if not yr_ep:
        continue
    worst = min(yr_ep, key=lambda x: x['dd_pct'])

    yr_rebals = [d for d in rebal_dates if d.year == yr]
    bear_months = sum(1 for d in yr_rebals
                      if d in spy_sig.index and
                      float(spy_sig.loc[d, 'close']) <= float(spy_sig.loc[d, 'ma_200']))
    bull_months = len(yr_rebals) - bear_months

    print(f"\n{yr}: excess={yr_excess:+.1f}% | worst_dd={worst['dd_pct']:.1f}% "
          f"| bull_months={bull_months} bear_months={bear_months}")
    print(f"  Episode: {worst['peak_dt'].strftime('%Y-%m-%d')} -> {worst['trough_dt'].strftime('%Y-%m-%d')} "
          f"({worst['duration_days']}d)")

# ── Hypothesis table ──────────────────────────────────────────────────────────
print(f"\n{'='*70}")
print("FIX HYPOTHESIS — per drawdown type")
print(f"{'='*70}")

hypotheses = []

# Analyze regime pattern during DD episodes
bear_dd_eps = []
bull_dd_eps = []
for ep in episodes:
    ep_rebals = [d for d in rebal_dates
                 if ep['peak_dt'] <= d <= ep['trough_dt'] and d in spy_sig.index]
    if not ep_rebals:
        continue
    bear_in_ep = sum(1 for d in ep_rebals
                     if float(spy_sig.loc[d,'close']) <= float(spy_sig.loc[d,'ma_200']))
    if bear_in_ep > len(ep_rebals) // 2:
        bear_dd_eps.append(ep)
    else:
        bull_dd_eps.append(ep)

print(f"\nDD episodes in BEAR regime: {len(bear_dd_eps)}")
for ep in bear_dd_eps:
    print(f"  {ep['peak_dt'].strftime('%Y-%m')} -> {ep['trough_dt'].strftime('%Y-%m')} | DD={ep['dd_pct']:.1f}%")

print(f"\nDD episodes in BULL regime: {len(bull_dd_eps)}")
for ep in bull_dd_eps:
    print(f"  {ep['peak_dt'].strftime('%Y-%m')} -> {ep['trough_dt'].strftime('%Y-%m')} | DD={ep['dd_pct']:.1f}%")

print(f"\n--- HYPOTHESES ---")
if bear_dd_eps:
    print(f"\n[BEAR DD] {len(bear_dd_eps)} episodes — Regime switch fires LATE")
    print(f"  Cause:  SPY crosses below MA200 but we're still 100% deployed from prior month rebalance")
    print(f"  Fix A:  Use 50d<200d death cross as EARLY WARNING -> cut to 35% before full bear")
    print(f"  Fix B:  Intra-month stop: if SPY drops >8% from last rebal, cut to bear exposure NOW")
    print(f"  Fix C:  Bear exposure 30% instead of 50% (tested: -3% excess, likely not worth it)")

if bull_dd_eps:
    print(f"\n[BULL DD] {len(bull_dd_eps)} episodes — High-beta stocks crash mid-bull")
    print(f"  Cause:  High RS stocks = high beta. Corrections of 10-20% in bull = -15-25% on portfolio")
    print(f"  Fix A:  Beta cap — require beta_252 < 1.8 (soft filter, penalize in scoring)")
    print(f"  Fix B:  Volatility targeting — scale down position size when rolling 20d vol spikes")
    print(f"  Fix C:  Concentration limit tighter: cap 12% instead of 18%, top_n=20 instead of 15")
    print(f"  Fix D:  Profit-lock trailing stop: if stock up 40%+, move stop to +15% from entry")

# ── Specific big DD months: what was held ─────────────────────────────────────
print(f"\n{'='*70}")
print("MONTHLY DD > 10% — what exactly was held")
print(f"{'='*70}")

# Find months with big drops by looking at NAV month-over-month
if len(nav) > 1:
    nav_monthly = nav.resample('ME').last().ffill()
    mom_ret = nav_monthly.pct_change() * 100
    bad_months = mom_ret[mom_ret < -8].sort_values()

    print(f"\nMonths with portfolio return < -8%: {len(bad_months)}")
    for dt, ret in bad_months.items():
        print(f"\n  {dt.strftime('%Y-%m')} | Return={ret:+.1f}%")
        rb = max([d for d in rebal_dates if d <= dt], default=None)
        if rb is None or rb not in spy_sig.index:
            continue

        spy_close = float(spy_sig.loc[rb, 'close'])
        spy_ma200 = float(spy_sig.loc[rb, 'ma_200'])
        regime = 'BULL' if spy_close > spy_ma200 else 'BEAR'

        rb_sig = sig[sig['date'] == rb]
        cands = rb_sig[
            (rb_sig['rs_pct'] >= 80) &
            (rb_sig['ticker'] != 'SPY') &
            (rb_sig['adtv_63m'] >= 10)
        ].copy()
        vt_thr = 0.75 if regime == 'BULL' else 0.95
        if 'vol_trend' in cands.columns:
            cands = cands[cands['vol_trend'] >= vt_thr]
        if 'close' in cands.columns and 'ma_200' in cands.columns:
            cands = cands[cands['close'] > cands['ma_200']]
        if len(cands) > TOP_N:
            cands = cands.nlargest(TOP_N, 'rs_pct')

        # Actual month return for each stock
        dt_start = dt - pd.offsets.MonthBegin(1)
        dt_end   = dt
        stock_rets = {}
        for tk in cands['ticker'].tolist():
            if tk in price_pivot.columns:
                sl = price_pivot.loc[dt_start:dt_end, tk].dropna()
                if len(sl) >= 2:
                    stock_rets[tk] = (sl.iloc[-1] / sl.iloc[0] - 1) * 100

        if stock_rets:
            # SPY return same month
            spy_sl = price_pivot.loc[dt_start:dt_end, 'SPY'].dropna() if 'SPY' in price_pivot.columns else pd.Series()
            spy_ret = (spy_sl.iloc[-1]/spy_sl.iloc[0]-1)*100 if len(spy_sl)>=2 else np.nan

            print(f"    Regime={regime} | Stocks={len(cands)} | SPY that month: {spy_ret:+.1f}%")
            sorted_rets = sorted(stock_rets.items(), key=lambda x: x[1])
            for tk, r in sorted_rets[:5]:
                rs_val = cands[cands['ticker']==tk]['rs_pct'].values
                rs_str = f"RS={rs_val[0]:.0f}" if len(rs_val) else ""
                print(f"    {tk:>8}: {r:>+7.1f}%  {rs_str}")
            if len(sorted_rets) > 5:
                avg_rest = np.mean([r for _, r in sorted_rets[5:]])
                print(f"    (rest {len(sorted_rets)-5} stocks avg: {avg_rest:+.1f}%)")

print(f"\n{'='*70}")
print("SUMMARY — Fix Priority")
print(f"{'='*70}")
print("""
Priority 1 — SECTOR/THEME CONCENTRATION
  Problem: All high-RS stocks often cluster in 1-2 themes (e.g. AI semis in 2022)
  When theme corrects -> entire portfolio corrects together
  Fix: max 40% per theme/sector (soft: penalize in weighting, not hard reject)
  Impact: Est. -3% to -8% on DD, minimal excess impact

Priority 2 — LATE REGIME SWITCH
  Problem: Month-end rebalance means we hold full bull into bear for up to 30 days
  Fix: Weekly regime check (not monthly) -- if SPY drops >7% intra-month -> cut exposure
  Impact: Est. -3% to -6% on worst bear DD episodes

Priority 3 — INDIVIDUAL STOCK STOP-LOSS
  Problem: Single stock drops 30-50% while still passing RS screen at last rebalance
  Fix: If any single holding down >20% since entry -> reduce to half at next check
  Impact: Est. -2% to -4% on DD, very low excess impact

Priority 4 — VOLATILITY TARGETING
  Problem: High-vol bull runs increase risk exactly when we're most exposed
  Fix: Scale total exposure = min(100%, 15% target_vol / realized_20d_vol)
  Impact: DD -36% vs baseline -40% (tested in DD reduction research), Sharpe 1.45
""")
