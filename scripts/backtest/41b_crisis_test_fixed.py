"""
41b_crisis_test_fixed.py — Crisis Test with Fixed RS Computation

Bug in 41: rs_pct = NaN throughout extended signals because SPY date alignment failed
Fix: recompute RS properly from cached prices_extended.parquet

Also adds a 2010-2014 post-crisis sanity check to confirm the system
can deploy in mature bull markets vs being permanently stuck in cash.
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

EXT_PRICES  = 'data/backtest/prices_extended.parquet'
LOCKED_V4   = dict(cagr=64.6, excess=43.4, dd=-38.2, sharpe=1.46)

CRISIS_WINDOWS = [
    ('Dot-com bust   2000-03', '2000-01-01', '2003-12-31'),
    ('Financial crisis 07-09', '2007-01-01', '2009-12-31'),
    ('Post-crisis    2010-14', '2010-01-01', '2014-12-31'),
    ('Pre-train      2015-16', '2015-01-01', '2016-12-31'),   # already in prices.parquet
]

# ── LOAD PRICES ───────────────────────────────────────────────────────────────
print("Loading extended prices...")
ext = pd.read_parquet(EXT_PRICES)
ext['date'] = pd.to_datetime(ext['date'])
print(f"  {len(ext):,} rows | {ext['ticker'].nunique()} tickers | {ext['date'].min().date()} -> {ext['date'].max().date()}")

# Use adj_close if available, else close
px_col = 'adj_close' if 'adj_close' in ext.columns else 'close'
print(f"  Price column: {px_col}")

# Check SPY/IWM/QQQ availability
for b in ['SPY', 'IWM', 'QQQ']:
    rows = ext[ext['ticker']==b]
    print(f"  {b}: {len(rows)} rows | {rows['date'].min().date() if len(rows)>0 else 'MISSING'} -> {rows['date'].max().date() if len(rows)>0 else ''}")

# ── BUILD CLEAN PRICE PIVOTS ──────────────────────────────────────────────────
print("\nBuilding price pivots...")

all_tickers = ext['ticker'].unique().tolist()
benchmarks  = ['SPY', 'IWM', 'QQQ']
stocks      = [t for t in all_tickers if t not in benchmarks]

# Pivot close prices — stocks only
close = ext[ext['ticker'].isin(stocks)].pivot_table(
    index='date', columns='ticker', values=px_col, aggfunc='last'
).sort_index()

# Benchmark price series
def get_bench(ticker):
    df = ext[ext['ticker']==ticker][['date', px_col]].set_index('date')[px_col].sort_index()
    return df.reindex(close.index, method='ffill')

spy = get_bench('SPY')
iwm = get_bench('IWM')
qqq = get_bench('QQQ')

print(f"  Stock close pivot: {close.shape}")
print(f"  SPY valid: {spy.notna().sum()} / {len(spy)} dates")
print(f"  IWM valid: {iwm.notna().sum()} / {len(iwm)} dates")

# ── COMPUTE SIGNALS ───────────────────────────────────────────────────────────
print("\nComputing signals (this takes ~2-3 min)...")

returns     = close.pct_change()
spy_ret     = spy.pct_change()
log_ret     = np.log(close / close.shift(1))

# RS composite: 0.1*20d + 0.2*60d + 0.3*126d + 0.4*252d excess vs SPY (cross-sectional rank)
print("  RS percentile...")
def excess_rank(lb, w):
    stock_cum = returns.rolling(lb, min_periods=int(lb*0.6)).sum()
    spy_cum   = spy_ret.rolling(lb, min_periods=int(lb*0.6)).sum()
    # broadcast spy_cum (Series) against stock_cum (DataFrame)
    return (stock_cum.subtract(spy_cum, axis=0)) * w

rs_raw  = (excess_rank(20, 0.1)
         + excess_rank(60, 0.2)
         + excess_rank(126, 0.3)
         + excess_rank(252, 0.4))

# Cross-sectional percentile rank (each row = one date across all stocks)
rs_pct = rs_raw.rank(axis=1, pct=True, na_option='keep') * 100

# Verify
sample_date = pd.Timestamp('2012-06-29')
if sample_date in rs_pct.index:
    valid_rs = rs_pct.loc[sample_date].dropna()
    print(f"  RS check @ 2012-06-29: {len(valid_rs)} valid | mean={valid_rs.mean():.1f} | RS>=80: {(valid_rs>=80).sum()}")

print("  Vol trend, ADTV, MA200...")
vol_20   = ext.groupby('ticker').apply(lambda x: x.set_index('date')['volume'].rolling(20, min_periods=10).mean()).T if False else None
# Faster: use pivot
volume   = ext[ext['ticker'].isin(stocks)].pivot_table(index='date', columns='ticker', values='volume', aggfunc='last').sort_index()
vol_20p  = volume.rolling(20, min_periods=10).mean()
vol_60p  = volume.rolling(60, min_periods=30).mean()
vol_trend = vol_20p / vol_60p.replace(0, np.nan)

adtv_63  = (close * volume).rolling(63, min_periods=30).mean() / 1e6

ma200    = close.rolling(200, min_periods=150).mean()
above_ma200 = (close > ma200).astype(float)

# IWM MA200 for regime
iwm_ma200    = iwm.rolling(200, min_periods=150).mean()
iwm_bull     = (iwm > iwm_ma200).astype(float)

# Beta vs SPY
print("  Beta (252d)...")
spy_var  = spy_ret.rolling(252, min_periods=100).var()
spy_cov  = returns.apply(lambda col: col.rolling(252, min_periods=100).cov(spy_ret))
beta_252 = spy_cov.div(spy_var, axis=0)

# Vol for vol-parity weights
vol_63d  = log_ret.rolling(63, min_periods=20).std() * np.sqrt(252)

print("  Signals computed.")
# Quick validation
for check_yr in [2000, 2008, 2012]:
    check_date = pd.Timestamp(f'{check_yr}-12-31')
    # find nearest date
    avail = rs_pct.index[rs_pct.index <= check_date]
    if len(avail) == 0:
        print(f"  [{check_yr}] No data")
        continue
    d = avail[-1]
    valid = rs_pct.loc[d].dropna()
    rs80  = (valid >= 80).sum()
    vt_ok = (vol_trend.loc[d].dropna() >= 0.75).sum() if d in vol_trend.index else 0
    iwm_b = float(iwm_bull.loc[d]) if d in iwm_bull.index else float('nan')
    print(f"  [{check_yr}] {str(d)[:10]} | RS>=80: {rs80:3d} | vol_trend>=0.75: {vt_ok:3d} | IWM_BULL={iwm_b:.0f}")

# ── MINI BACKTEST ─────────────────────────────────────────────────────────────
def run_period(label, start, end,
               rs_thresh=80, vt_bull=0.75, vt_bear=0.95,
               adtv_min=10, cap=0.18, beta_cap=0.12,
               bear_exp=0.50, bull_exp=1.00, top_n=15, cost=0.0015):

    start_ts = pd.Timestamp(start)
    end_ts   = pd.Timestamp(end)

    # Get month-end rebalance dates within period
    dates_in_period = rs_pct.index[(rs_pct.index >= start_ts) & (rs_pct.index <= end_ts)]
    if len(dates_in_period) == 0:
        return None

    months = pd.DatetimeIndex(dates_in_period).to_period('M').unique()
    rebal  = [(mp, max(d for d in dates_in_period if pd.Timestamp(d).to_period('M')==mp))
              for mp in months]
    rebal  = sorted(rebal, key=lambda x: x[1])

    portfolio_val = 1.0
    prev_h        = {}
    records       = []

    for i, (mp, rd) in enumerate(rebal[:-1]):
        _, next_rd = rebal[i+1]

        # Regime
        bull = bool(iwm_bull.loc[rd] >= 0.5) if rd in iwm_bull.index else True
        vt   = vt_bull if bull else vt_bear
        exp  = bull_exp if bull else bear_exp

        # Screen
        rs_row  = rs_pct.loc[rd]    if rd in rs_pct.index    else pd.Series(dtype=float)
        vt_row  = vol_trend.loc[rd] if rd in vol_trend.index else pd.Series(dtype=float)
        ad_row  = adtv_63.loc[rd]   if rd in adtv_63.index   else pd.Series(dtype=float)
        ma_row  = above_ma200.loc[rd] if rd in above_ma200.index else pd.Series(dtype=float)
        cl_row  = close.loc[rd]     if rd in close.index     else pd.Series(dtype=float)

        candidates = pd.DataFrame({'rs': rs_row, 'vt': vt_row, 'adtv': ad_row,
                                    'above': ma_row, 'close': cl_row}).dropna()
        passed = candidates[
            (candidates['rs']    >= rs_thresh) &
            (candidates['vt']    >= vt) &
            (candidates['adtv']  >= adtv_min) &
            (candidates['above'] >= 0.5)
        ]
        if len(passed) > top_n:
            passed = passed.nlargest(top_n, 'rs')

        if passed.empty:
            new_h = {}
        else:
            tickers = passed.index.tolist()
            # Vol-parity
            vols = []
            for t in tickers:
                v = float(vol_63d.loc[rd, t]) if (rd in vol_63d.index and t in vol_63d.columns) else 0.30
                vols.append(v if not np.isnan(v) and v > 0.01 else 0.30)

            betas = []
            for t in tickers:
                b = float(beta_252.loc[rd, t]) if (rd in beta_252.index and t in beta_252.columns) else 1.0
                betas.append(max(0.3, min(b if not np.isnan(b) else 1.0, 4.0)))

            max_w = np.array([min(cap, beta_cap / max(b, 0.5)) for b in betas])
            raw_w = 1.0 / np.array(vols)
            raw_w /= raw_w.sum()
            w = raw_w.copy()
            for _ in range(25):
                over = w > max_w
                if not over.any(): break
                ov = (w[over] - max_w[over]).sum()
                w[over] = max_w[over]
                recv = ~over
                if not recv.any(): break
                w[recv] += ov * w[recv] / w[recv].sum()

            new_h = {t: float(wt) * exp for t, wt in zip(tickers, w)}

        # Monthly return
        port_ret = 0.0
        for t, wt in new_h.items():
            if t in close.columns:
                p0 = close.loc[close.index <= rd,       t].dropna()
                p1 = close.loc[close.index <= next_rd,  t].dropna()
                if not p0.empty and not p1.empty:
                    port_ret += wt * (p1.iloc[-1]/p0.iloc[-1] - 1)

        # Turnover cost
        all_t    = set(prev_h) | set(new_h)
        turnover = sum(abs(new_h.get(t,0) - prev_h.get(t,0)) for t in all_t) / 2
        portfolio_val *= (1 + port_ret - turnover * cost)

        records.append({'date': next_rd, 'val': portfolio_val, 'ret': port_ret - turnover*cost,
                        'bull': bull, 'n': len(new_h)})
        prev_h = new_h

    if not records:
        return None

    df = pd.DataFrame(records).set_index('date')
    n_yrs = (end_ts - start_ts).days / 365.25
    cagr  = (portfolio_val**(1/n_yrs) - 1)*100

    vals = [1.0] + list(df['val'])
    peak = 1.0; mdd = 0
    for v in vals:
        if v > peak: peak = v
        mdd = min(mdd, (v-peak)/peak)

    mo_rets = df['ret'].values
    sharpe  = np.mean(mo_rets)/np.std(mo_rets)*np.sqrt(12) if np.std(mo_rets)>0 else 0

    df['year'] = pd.DatetimeIndex(df.index).year
    yearly = {yr: (np.prod(1+g['ret'])-1)*100 for yr, g in df.groupby('year')}
    wins   = sum(1 for v in yearly.values() if v > 0)

    # Benchmark CAGR (QQQ)
    qqq_sub = qqq[(qqq.index >= start_ts) & (qqq.index <= end_ts)].dropna()
    bench_cagr = (qqq_sub.iloc[-1]/qqq_sub.iloc[0])**(1/n_yrs)*100-100 if len(qqq_sub)>10 else float('nan')

    return dict(label=label, cagr=cagr, mdd=mdd*100, sharpe=sharpe,
                bench_cagr=bench_cagr, excess=cagr-bench_cagr,
                yearly=yearly, wins=wins, n_yrs=len(yearly),
                avg_n=df['n'].mean(), bear_pct=(~df['bull']).mean()*100)


# ── RUN ALL WINDOWS ───────────────────────────────────────────────────────────
print()
print("="*70)
print("CRISIS PERIOD BACKTEST — v4.0 (FIXED RS + vol-parity + beta-cap)")
print("="*70)
print("  [!] SURVIVORSHIP BIAS: ~25% of S&P500 tickers missing pre-2003 history")

results = []
for label, start, end in CRISIS_WINDOWS:
    print(f"\n  Running: {label}  ({start} -> {end})")
    r = run_period(label, start, end)
    if r is None:
        print(f"    [SKIP] No data")
        continue
    results.append(r)

    yr_str = ' '.join(f"{yr}:{v:+.1f}%" for yr,v in sorted(r['yearly'].items()))
    print(f"    CAGR={r['cagr']:+.1f}% | QQQ={r['bench_cagr']:+.1f}% | Excess={r['excess']:+.1f}%")
    print(f"    DD={r['mdd']:+.1f}% | Sharpe={r['sharpe']:.2f} | Win={r['wins']}/{r['n_yrs']} | AvgHoldings={r['avg_n']:.1f}")
    print(f"    Bear months: {r['bear_pct']:.0f}%")
    print(f"    {yr_str}")

# ── COMPARISON TABLE ──────────────────────────────────────────────────────────
print()
print("="*70)
print(f"  {'Period':<26} {'Sys CAGR':>9} {'QQQ CAGR':>9} {'Excess':>8} {'DD':>8} {'Sharpe':>7} {'Win':>5} {'Holds':>6}")
print("-"*75)

for r in results:
    print(f"  {r['label']:<26} {r['cagr']:>+8.1f}% {r['bench_cagr']:>+8.1f}% "
          f"{r['excess']:>+7.1f}% {r['mdd']:>+7.1f}% {r['sharpe']:>6.2f}  "
          f"{r['wins']:>2}/{r['n_yrs']}  {r['avg_n']:>5.1f}")

print()
print(f"  v4.0 LOCKED (2017-26): CAGR=+{LOCKED_V4['cagr']}% | Excess=+{LOCKED_V4['excess']}% | DD={LOCKED_V4['dd']}% | Sh={LOCKED_V4['sharpe']}")

print()
print("="*70)
print("KEY QUESTIONS THIS ANSWERS")
print("="*70)
print("""
  1. Dot-com (2000-03):
     - Did IWM regime filter protect? (bear_pct high = mostly cash = protected)
     - Were there ANY stocks passing RS+vol_trend even in a crash? (avg_n)

  2. Financial crisis (2007-09):
     - 2007 BULL phase: did we hold momentum stocks before crash?
     - 2008 BEAR phase: regime switch speed — cash when it mattered?

  3. Post-crisis (2010-14):
     - This is the CRITICAL test: can system re-enter after a major bear?
     - A healthy system should start deploying capital by 2010-2011
     - If avg_n = 0 through 2014 → threshold is too strict to re-enter

  4. Pre-train (2015-16):
     - System uses 2017 as train start
     - 2015-16 is true out-of-sample (same universe, different time)
     - Should show similar quality to 2017+ results
""")

print("="*70)
print("SURVIVORSHIP BIAS IMPACT ESTIMATE")
print("="*70)
print("""
  358 / 1395 tickers (25.7%) have NO pre-2003 price history on Yahoo.
  These include:
    - Recent IPOs (ABNB, AFRM = post-2020)
    - Companies that failed/were acquired before 2003 → TRUE survivorship bias
    - The failed group is the important one — these would have HURT performance

  Rough adjustment:
    - In dot-com bust: ~15% of S&P500 stocks dropped >90% and eventually left.
      If we included them, they'd occasionally pass RS (as laggards) and hurt.
    - Expected impact: -2 to -5% CAGR per year during crisis, minimal in bull
    - Our crisis numbers are OPTIMISTIC by roughly that amount.
""")
