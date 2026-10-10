"""
41_crisis_test.py — Crisis Period Out-of-Sample Test

Tests SYSTEM v4.0 on historical crisis periods NOT in training data:
  - Dot-com bust:        2000-01-01 → 2003-12-31
  - Financial crisis:   2007-01-01 → 2009-12-31
  - Post-crisis:        2010-01-01 → 2014-12-31  (recovery period)

WARNING — SURVIVORSHIP BIAS IS SEVERE HERE:
  Current S&P 500 universe excludes ~30-40% of 2000-era companies that went bust
  (Enron, WorldCom, Pets.com, etc.) and 2008-era financials (Lehman, Bear Stearns,
  Wachovia, Washington Mutual). Results will be OPTIMISTIC vs a true point-in-time test.
  Treat as a STRESS TEST directional check, not a rigorous backtest.

STEP 1: Downloads prices for 1999-2014 using same Yahoo query2 pattern
STEP 2: Computes signals (RS, vol_trend, adtv, MA200) for extended period
STEP 3: Runs v4.0 backtest on each crisis window
"""
import sys, io, os, time, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd
import numpy as np
import requests
import concurrent.futures
import importlib.util, pathlib
import warnings
warnings.filterwarnings('ignore')

# ── CONFIG ────────────────────────────────────────────────────────────────────
EXTENDED_START = '1999-01-01'
EXTENDED_END   = '2014-12-31'
EXT_PRICES     = 'data/backtest/prices_extended.parquet'
EXT_SIGNALS    = 'data/backtest/signals_extended.parquet'
THREADS        = 5
SLEEP_BTW      = 0.3

CRISIS_WINDOWS = [
    ('Dot-com bust 2000-03',     '2000-01-01', '2003-12-31'),
    ('Financial crisis 2007-09', '2007-01-01', '2009-12-31'),
    ('Post-crisis 2010-14',      '2010-01-01', '2014-12-31'),
]

# v4.0 locked params
BASE_PARAMS_V4 = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=9.9, base_tight_max=9.9, adtv_min_m=10, cap_pct=0.18,
    bear_exposure=0.50, bull_exposure=1.00, softmax_alpha=1.0,
)
LOCKED_V4 = dict(cagr=64.6, excess=43.4, dd=-38.2, sharpe=1.46)

# ── DOWNLOAD ─────────────────────────────────────────────────────────────────
HEADERS = {
    'User-Agent': 'Mozilla/5.0',
    'Accept': 'application/json',
}

def fetch_yahoo(ticker, start='1999-01-01', end='2014-12-31', retries=3):
    """Fetch OHLCV from Yahoo Finance query2 API."""
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context

    period1 = int(pd.Timestamp(start).timestamp())
    period2 = int(pd.Timestamp(end).timestamp())
    url = (f'https://query2.finance.yahoo.com/v8/finance/chart/{ticker}'
           f'?interval=1d&period1={period1}&period2={period2}&events=history')

    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, verify=False, timeout=20)
            if r.status_code != 200:
                return None
            data = r.json()
            result = data['chart']['result']
            if not result:
                return None
            res = result[0]
            timestamps = res['timestamp']
            ohlcv = res['indicators']['quote'][0]
            adjclose = res['indicators'].get('adjclose', [{}])[0].get('adjclose', ohlcv['close'])

            df = pd.DataFrame({
                'date':   pd.to_datetime(timestamps, unit='s').normalize(),
                'open':   ohlcv['open'],
                'high':   ohlcv['high'],
                'low':    ohlcv['low'],
                'close':  ohlcv['close'],
                'volume': ohlcv['volume'],
                'adj_close': adjclose,
            })
            df['ticker'] = ticker
            df = df.dropna(subset=['close'])
            return df
        except Exception as e:
            if attempt == retries - 1:
                return None
            time.sleep(1)
    return None


def download_extended(tickers, start=EXTENDED_START, end=EXTENDED_END):
    """Download extended history with progress reporting."""
    print(f"\nDownloading extended history {start} -> {end} for {len(tickers)} tickers...")
    print("(This takes ~10-15 minutes for full universe)")

    results = []
    failed = []
    done = 0

    def fetch_one(t):
        df = fetch_yahoo(t, start=start, end=end)
        time.sleep(SLEEP_BTW)
        return t, df

    with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as ex:
        futures = {ex.submit(fetch_one, t): t for t in tickers}
        for future in concurrent.futures.as_completed(futures):
            t, df = future.result()
            done += 1
            if df is not None and len(df) > 50:
                results.append(df)
            else:
                failed.append(t)
            if done % 100 == 0 or done == len(tickers):
                print(f"  {done}/{len(tickers)} tickers | {len(results)} OK | {len(failed)} failed")

    if not results:
        raise ValueError("No data downloaded!")

    combined = pd.concat(results, ignore_index=True)
    combined['date'] = pd.to_datetime(combined['date'])
    print(f"  Downloaded: {len(combined):,} rows | {combined['ticker'].nunique()} tickers")
    print(f"  Failed/no history: {len(failed)} ({', '.join(failed[:10])}{'...' if len(failed)>10 else ''})")
    return combined


# ── SIGNAL COMPUTATION ───────────────────────────────────────────────────────
def compute_signals_extended(prices_df, spy_df, iwm_df, qqq_df):
    """Compute RS, vol_trend, adtv, MA200, beta for extended price data."""
    print("\nComputing signals for extended period...")

    prices_df = prices_df.copy()
    prices_df['date'] = pd.to_datetime(prices_df['date'])

    # Use adjusted close if available
    px_col = 'adj_close' if 'adj_close' in prices_df.columns else 'close'

    # Pivot to wide format
    close = prices_df.pivot_table(index='date', columns='ticker', values=px_col).sort_index()
    volume = prices_df.pivot_table(index='date', columns='ticker', values='volume').sort_index()

    spy_close = spy_df.set_index('date')[px_col].reindex(close.index).ffill()
    iwm_close = iwm_df.set_index('date')[px_col].reindex(close.index).ffill()
    qqq_close = qqq_df.set_index('date')[px_col].reindex(close.index).ffill()

    returns = close.pct_change()
    spy_ret = spy_close.pct_change()

    print("  Computing RS percentile...")
    # RS composite: 0.1*20d + 0.2*60d + 0.3*126d + 0.4*252d excess vs SPY
    excess_20  = (returns.rolling(20).sum()  - spy_ret.rolling(20).sum())
    excess_60  = (returns.rolling(60).sum()  - spy_ret.rolling(60).sum())
    excess_126 = (returns.rolling(126).sum() - spy_ret.rolling(126).sum())
    excess_252 = (returns.rolling(252).sum() - spy_ret.rolling(252).sum())
    rs_raw = 0.1*excess_20 + 0.2*excess_60 + 0.3*excess_126 + 0.4*excess_252
    rs_pct = rs_raw.rank(axis=1, pct=True) * 100

    print("  Computing vol_trend, ADTV, MA200...")
    vol_20 = volume.rolling(20, min_periods=10).mean()
    vol_60 = volume.rolling(60, min_periods=30).mean()
    vol_trend = vol_20 / vol_60.replace(0, np.nan)

    adtv_63 = (close * volume).rolling(63, min_periods=30).mean() / 1e6

    ma200 = close.rolling(200, min_periods=150).mean()
    above_ma200 = (close > ma200).astype(float)

    spy_ma200 = spy_close.rolling(200, min_periods=150).mean()
    spy_above_ma200 = (spy_close > spy_ma200).astype(float)

    iwm_ma200 = iwm_close.rolling(200, min_periods=150).mean()
    iwm_above_ma200 = (iwm_close > iwm_ma200).astype(float)

    qqq_above_ma200 = (qqq_close > qqq_close.rolling(200, min_periods=150).mean()).astype(float)

    print("  Computing beta...")
    beta_252 = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    spy_var = spy_ret.rolling(252, min_periods=100).var()
    for tkr in close.columns:
        cov = returns[tkr].rolling(252, min_periods=100).cov(spy_ret)
        beta_252[tkr] = cov / spy_var.replace(0, np.nan)

    print("  Building long-format signals DataFrame...")
    # Melt to long format
    all_rows = []
    dates = close.index

    for date in dates:
        row = {
            'date': date,
            'spy_above_ma200': float(spy_above_ma200.loc[date]) if date in spy_above_ma200.index else np.nan,
            'iwm_above_ma200': float(iwm_above_ma200.loc[date]) if date in iwm_above_ma200.index else np.nan,
            'qqq_above_ma200': float(qqq_above_ma200.loc[date]) if date in qqq_above_ma200.index else np.nan,
        }
        all_rows.append(row)

    regime_df = pd.DataFrame(all_rows)

    # Build per-stock rows
    tickers = close.columns.tolist()
    stock_rows = []
    for date in dates:
        if date not in close.index:
            continue
        for tkr in tickers:
            try:
                c = float(close.loc[date, tkr])
                if np.isnan(c) or c <= 0:
                    continue
                stock_rows.append({
                    'date':           date,
                    'ticker':         tkr,
                    'close':          c,
                    'rs_pct':         float(rs_pct.loc[date, tkr]) if date in rs_pct.index else np.nan,
                    'vol_trend':      float(vol_trend.loc[date, tkr]) if date in vol_trend.index else np.nan,
                    'adtv_63m':       float(adtv_63.loc[date, tkr]) if date in adtv_63.index else np.nan,
                    'above_ma200':    float(above_ma200.loc[date, tkr]) if date in above_ma200.index else np.nan,
                    'beta_252':       float(beta_252.loc[date, tkr]) if date in beta_252.index else np.nan,
                    'spy_above_ma200': float(spy_above_ma200.loc[date]) if date in spy_above_ma200.index else np.nan,
                    'iwm_above_ma200': float(iwm_above_ma200.loc[date]) if date in iwm_above_ma200.index else np.nan,
                })
            except:
                continue

    sig_df = pd.DataFrame(stock_rows)
    sig_df['date'] = pd.to_datetime(sig_df['date'])
    print(f"  Signals: {len(sig_df):,} rows | {sig_df['date'].min()} -> {sig_df['date'].max()}")
    return sig_df


# ── MINI BACKTEST ENGINE ──────────────────────────────────────────────────────
def run_crisis_backtest(sig_df, start, end, label,
                        rs_threshold=80, vol_trend_bull=0.75, vol_trend_bear=0.95,
                        adtv_min_m=10, cap_pct=0.18, bear_exposure=0.50,
                        top_n=15, cost_pct=0.0015,
                        use_vol_parity=True, beta_cap_base=0.12,
                        bench_col='qqq_close'):
    """
    Mini monthly rebalance backtest.
    Uses same logic as v2 engine: screen → weight → hold 1 month.
    Regime: IWM > MA200 (bear_exposure when below)
    """
    sig = sig_df.copy()
    sig = sig[(sig['date'] >= start) & (sig['date'] <= end)].copy()
    if sig.empty:
        return None

    # Get all month-end dates
    dates = sig['date'].sort_values().unique()
    month_ends = pd.DatetimeIndex(dates).to_period('M').unique()

    portfolio_val = 1.0
    bench_val     = 1.0
    port_history  = []
    bench_history = []

    # Build close pivot for return calculation
    close_piv = sig.pivot_table(index='date', columns='ticker', values='close').sort_index()

    # Get benchmark (QQQ) prices
    # We'll use qqq_close from the regime signals — but that's not in sig_df
    # Use a separate column we'll pass in

    # Build vol for vol-parity
    log_ret = np.log(close_piv / close_piv.shift(1))
    vol_63 = log_ret.rolling(63, min_periods=20).std() * np.sqrt(252)

    prev_holdings = {}

    for mp in sorted(month_ends)[:-1]:
        # Month-end rebalance date
        month_dates = [d for d in dates if pd.Timestamp(d).to_period('M') == mp]
        if not month_dates:
            continue
        rebal_date = max(month_dates)

        # Next month dates for return calculation
        next_mp = mp + 1
        next_dates = [d for d in dates if pd.Timestamp(d).to_period('M') == next_mp]
        if not next_dates:
            continue
        next_end = max(next_dates)

        # Screen at rebal_date
        day_sig = sig[sig['date'] == rebal_date].copy()
        if day_sig.empty:
            continue

        # Regime
        iwm_bull = day_sig['iwm_above_ma200'].iloc[0] if 'iwm_above_ma200' in day_sig.columns else 1.0
        if pd.isna(iwm_bull):
            iwm_bull = 1.0
        bull = (iwm_bull >= 0.5)

        # Vol-trend threshold
        vt_thresh = vol_trend_bull if bull else vol_trend_bear

        # Apply screens
        screened = day_sig[
            (day_sig['rs_pct'] >= rs_threshold) &
            (day_sig['vol_trend'] >= vt_thresh) &
            (day_sig['above_ma200'] >= 0.5) &
            (day_sig['adtv_63m'] >= adtv_min_m) &
            day_sig['close'].notna() & day_sig['rs_pct'].notna()
        ].copy()

        # Top N by RS
        if len(screened) > top_n:
            screened = screened.nlargest(top_n, 'rs_pct').copy()

        exposure = 1.0 if bull else bear_exposure

        if screened.empty:
            new_holdings = {}
        else:
            # Vol-parity weights
            ts = pd.Timestamp(rebal_date)
            if use_vol_parity and ts in vol_63.index:
                vols = []
                for tkr in screened['ticker']:
                    if tkr in vol_63.columns:
                        v = float(vol_63.loc[ts, tkr]) if not pd.isna(vol_63.loc[ts, tkr]) else 0.30
                        vols.append(max(v, 0.05))
                    else:
                        vols.append(0.30)
                screened = screened.copy()
                screened['_vol'] = vols

                # Beta cap
                if beta_cap_base is not None and 'beta_252' in screened.columns:
                    screened['_max_w'] = screened['beta_252'].apply(
                        lambda b: min(cap_pct, beta_cap_base / max(float(b) if not pd.isna(b) else 1.0, 0.5))
                    )
                else:
                    screened['_max_w'] = cap_pct

                raw_w = 1.0 / screened['_vol'].values
                raw_w = raw_w / raw_w.sum()
                weights = raw_w.copy()
                max_w = screened['_max_w'].values
                for _ in range(25):
                    over = weights > max_w
                    if not over.any(): break
                    overflow = (weights[over] - max_w[over]).sum()
                    weights[over] = max_w[over]
                    recv = ~over & (weights < max_w)
                    if not recv.any(): break
                    weights[recv] += overflow * weights[recv] / weights[recv].sum()
                screened['weight'] = weights
            else:
                # Equal weight fallback
                screened['weight'] = 1.0 / len(screened)

            new_holdings = dict(zip(screened['ticker'], screened['weight'] * exposure))

        # Calculate portfolio return for the month
        port_ret = 0.0
        for tkr, w in new_holdings.items():
            if tkr in close_piv.columns:
                p0 = close_piv.loc[close_piv.index <= rebal_date, tkr].dropna()
                p1 = close_piv.loc[close_piv.index <= next_end, tkr].dropna()
                if not p0.empty and not p1.empty:
                    r = p1.iloc[-1] / p0.iloc[-1] - 1
                    port_ret += w * r

        # Cash earns 0 (simplification)
        cash_w = 1.0 - sum(new_holdings.values())

        # Transaction cost
        old_tickers = set(prev_holdings.keys())
        new_tickers = set(new_holdings.keys())
        turnover = sum(abs(new_holdings.get(t, 0) - prev_holdings.get(t, 0))
                       for t in old_tickers | new_tickers) / 2
        cost = turnover * cost_pct

        portfolio_val *= (1 + port_ret - cost)
        port_history.append({'date': next_end, 'val': portfolio_val, 'holdings': len(new_holdings),
                             'bull': bull, 'ret': port_ret - cost})
        prev_holdings = new_holdings

    if not port_history:
        return None

    ph = pd.DataFrame(port_history)
    ph = ph.set_index('date').sort_index()

    n_years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25
    cagr = (portfolio_val ** (1/n_years) - 1) * 100 if n_years > 0 else 0

    # MDD
    vals = [1.0] + list(ph['val'])
    peak = 1.0
    max_dd = 0
    for v in vals:
        if v > peak: peak = v
        dd = (v - peak) / peak
        if dd < max_dd: max_dd = dd

    # Sharpe (monthly returns)
    monthly_rets = ph['ret'].values
    sharpe = (np.mean(monthly_rets) / np.std(monthly_rets) * np.sqrt(12)) if np.std(monthly_rets) > 0 else 0

    # Year-by-year
    ph['year'] = pd.DatetimeIndex(ph.index).year
    yearly = {}
    for yr, grp in ph.groupby('year'):
        r_total = np.prod(1 + grp['ret']) - 1
        yearly[yr] = r_total * 100

    win_yrs = sum(1 for v in yearly.values() if v > 0)
    avg_holds = ph['holdings'].mean()

    return dict(label=label, start=start, end=end, cagr=cagr,
                mdd=max_dd*100, sharpe=sharpe, yearly=yearly,
                win_yrs=win_yrs, total_yrs=len(yearly), avg_holds=avg_holds,
                bull_months=ph['bull'].sum(), total_months=len(ph))


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("="*65)
    print("CRISIS PERIOD OUT-OF-SAMPLE TEST — SYSTEM v4.0")
    print("="*65)
    print()
    print("!!! SURVIVORSHIP BIAS WARNING !!!")
    print("    Universe = S&P 500 CURRENT constituents only.")
    print("    Dot-com: Enron, WorldCom, many telecoms EXCLUDED.")
    print("    2008:    Lehman, Bear Stearns, Wachovia EXCLUDED.")
    print("    Results are OPTIMISTIC vs true point-in-time universe.")
    print("    Treat as directional stress test only.")
    print()

    # ── STEP 1: Check / download extended prices ──────────────────────────────
    if os.path.exists(EXT_PRICES):
        print(f"Loading existing extended prices from {EXT_PRICES}...")
        ext_prices = pd.read_parquet(EXT_PRICES)
        print(f"  Loaded: {len(ext_prices):,} rows | {ext_prices['date'].min()} -> {ext_prices['date'].max()}")
    else:
        print("Extended prices not found — downloading now...")
        # Load current tickers from existing prices.parquet
        existing = pd.read_parquet('data/backtest/prices.parquet')
        all_tickers = existing['ticker'].unique().tolist()
        # Add benchmarks
        for b in ['SPY', 'QQQ', 'IWM']:
            if b not in all_tickers:
                all_tickers.append(b)
        print(f"  Will download {len(all_tickers)} tickers from {EXTENDED_START} to {EXTENDED_END}")
        ext_prices = download_extended(all_tickers, EXTENDED_START, EXTENDED_END)
        ext_prices.to_parquet(EXT_PRICES, index=False)
        print(f"  Saved to {EXT_PRICES}")

    ext_prices['date'] = pd.to_datetime(ext_prices['date'])

    # ── STEP 2: Check / compute extended signals ──────────────────────────────
    if os.path.exists(EXT_SIGNALS):
        print(f"\nLoading existing extended signals from {EXT_SIGNALS}...")
        sig_df = pd.read_parquet(EXT_SIGNALS)
        sig_df['date'] = pd.to_datetime(sig_df['date'])
        print(f"  Loaded: {len(sig_df):,} rows | {sig_df['date'].min()} -> {sig_df['date'].max()}")
    else:
        print("\nComputing extended signals...")
        px_col = 'adj_close' if 'adj_close' in ext_prices.columns else 'close'
        spy_df = ext_prices[ext_prices['ticker']=='SPY'][['date', px_col]].copy()
        iwm_df = ext_prices[ext_prices['ticker']=='IWM'][['date', px_col]].copy()
        qqq_df = ext_prices[ext_prices['ticker']=='QQQ'][['date', px_col]].copy()
        stock_tickers = [t for t in ext_prices['ticker'].unique() if t not in ('SPY','IWM','QQQ')]
        stock_prices = ext_prices[ext_prices['ticker'].isin(stock_tickers)].copy()
        sig_df = compute_signals_extended(stock_prices, spy_df, iwm_df, qqq_df)
        sig_df.to_parquet(EXT_SIGNALS, index=False)
        print(f"  Saved to {EXT_SIGNALS}")

    # ── STEP 3: Get QQQ benchmark returns ─────────────────────────────────────
    px_col = 'adj_close' if 'adj_close' in ext_prices.columns else 'close'
    qqq_prices = ext_prices[ext_prices['ticker']=='QQQ'][['date', px_col]].copy()
    qqq_prices = qqq_prices.set_index('date')[px_col].sort_index()

    # ── STEP 4: Run crisis backtests ──────────────────────────────────────────
    print()
    print("="*65)
    print("CRISIS PERIOD BACKTEST RESULTS — v4.0 (Vol-Parity + Beta Cap)")
    print("="*65)

    results = []
    for label, start, end in CRISIS_WINDOWS:
        print(f"\nRunning: {label} ({start} -> {end})")
        r = run_crisis_backtest(
            sig_df, start, end, label,
            rs_threshold=80, vol_trend_bull=0.75, vol_trend_bear=0.95,
            adtv_min_m=10, cap_pct=0.18, bear_exposure=0.50,
            top_n=15, cost_pct=0.0015,
            use_vol_parity=True, beta_cap_base=0.12
        )
        if r is None:
            print(f"  [SKIP] No data for this period")
            continue
        results.append(r)

        # Print result
        bear_pct = (1 - r['bull_months']/r['total_months'])*100 if r['total_months'] > 0 else 0
        yr_str = ' '.join(f"{yr}:{'+' if v>=0 else ''}{v:.1f}%" for yr,v in sorted(r['yearly'].items()))
        print(f"  CAGR={r['cagr']:.1f}% | DD={r['mdd']:.1f}% | Sharpe={r['sharpe']:.2f}")
        print(f"  Win={r['win_yrs']}/{r['total_yrs']}yrs | Avg Holdings={r['avg_holds']:.1f}")
        print(f"  Bear months: {bear_pct:.0f}% of period (IWM < MA200)")
        print(f"  {yr_str}")

    # ── Also compute QQQ CAGR for each window ─────────────────────────────────
    print()
    print("="*65)
    print("COMPARISON TABLE — v4.0 vs QQQ per period")
    print("="*65)
    print(f"  {'Period':<28} {'v4.0 CAGR':>10} {'QQQ CAGR':>10} {'Excess':>8} {'DD':>8} {'Sharpe':>7} {'Win':>5}")
    print("-"*80)

    for r in results:
        # QQQ CAGR for this period
        qqq_sub = qqq_prices[r['start']:r['end']]
        if len(qqq_sub) > 0:
            n_yrs = (pd.Timestamp(r['end']) - pd.Timestamp(r['start'])).days / 365.25
            qqq_cagr = (qqq_sub.iloc[-1] / qqq_sub.iloc[0]) ** (1/n_yrs) * 100 - 100
        else:
            qqq_cagr = float('nan')
        excess = r['cagr'] - qqq_cagr if not np.isnan(qqq_cagr) else float('nan')
        print(f"  {r['label']:<28} {r['cagr']:>+9.1f}% {qqq_cagr:>+9.1f}% {excess:>+7.1f}% {r['mdd']:>+7.1f}% {r['sharpe']:>6.2f}  {r['win_yrs']}/{r['total_yrs']}")

    print()
    print("="*65)
    print("CONTEXT — Production periods (2017-2026 train/test)")
    print("="*65)
    print(f"  v4.0 LOCKED: CAGR=+{LOCKED_V4['cagr']}% | Excess={LOCKED_V4['excess']:+.1f}% | DD={LOCKED_V4['dd']:.1f}% | Sharpe={LOCKED_V4['sharpe']:.2f}")
    print()
    print("SURVIVORSHIP BIAS REMINDER:")
    print("  Dot-com bust: ~30% of Nasdaq constituents went to zero or near-zero.")
    print("  Financial crisis: Major financials (15-20% of S&P 500 weight in 2007) excluded.")
    print("  True out-of-sample CAGR likely 5-15% lower than reported here.")
    print("  Key question: Does the REGIME FILTER protect against multi-year drawdowns?")


if __name__ == '__main__':
    main()
