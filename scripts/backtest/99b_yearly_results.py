"""v4.0 full backtest — year-by-year detailed results."""
import pandas as pd, numpy as np, warnings; warnings.filterwarnings('ignore')
import os; os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))

sig = pd.read_parquet('data/backtest/signals.parquet')
sig['date'] = pd.to_datetime(sig['date'])
sig = sig.sort_values(['date','ticker'])

close_px = sig.pivot_table(index='date', columns='ticker', values='close').sort_index()
log_ret  = np.log(close_px / close_px.shift(1))
vol_63d  = log_ret.rolling(63, min_periods=20).std() * np.sqrt(252)
qqq = close_px['QQQ'].dropna()

beta_lkp = {}
for row in sig[['date','ticker','beta_252']].dropna().itertuples():
    b = float(row.beta_252)
    if not np.isnan(b): beta_lkp[(row.date, row.ticker)] = max(0.3, min(b, 4.0))

all_dates = sig['date'].sort_values().unique()
iwm = close_px['IWM'].dropna()
iwm_ma200 = iwm.rolling(200, min_periods=150).mean()
iwm_bull = (iwm > iwm_ma200).reindex(all_dates, method='ffill').fillna(1.0)

def get_weights(ts, tickers, exposure):
    vols, maxws = [], []
    for tkr in tickers:
        v = float(vol_63d.loc[ts, tkr]) if (ts in vol_63d.index and tkr in vol_63d.columns) else 0.30
        vols.append(max(v if not np.isnan(v) else 0.30, 0.01))
        b = beta_lkp.get((ts, tkr), 1.0)
        maxws.append(min(0.18, 0.12 / max(b, 0.5)))
    raw_w = 1.0 / np.array(vols); raw_w /= raw_w.sum()
    w = raw_w.copy(); mw = np.array(maxws)
    for _ in range(25):
        over = w > mw
        if not over.any(): break
        exc = (w[over] - mw[over]).sum(); w[over] = mw[over]
        recv = ~over
        if not recv.any(): break
        w[recv] += exc * w[recv] / w[recv].sum()
    return w * exposure

months = pd.date_range(start='2017-01-01', end='2026-08-20', freq='ME')

equity = 1.0; peak = 1.0; max_dd_all = 0.0
year_data = {}
prev_h = set()

for i, me in enumerate(months[:-1]):
    ne = months[i+1]
    ts = me
    bull = bool(iwm_bull.get(ts, 1.0) >= 0.5)
    vt   = 0.75 if bull else 0.95
    exp  = 1.0 if bull else 0.5

    day = sig[sig['date'] == ts].copy()
    if day.empty:
        closest = sig[sig['date'] <= ts]['date'].max()
        if pd.isna(closest): continue
        day = sig[sig['date'] == closest].copy()

    mask = ((day['rs_pct'] >= 80) & (day['price_vs_ma200_pct'] >= 0)
            & (day['vol_trend'] >= vt) & (day['adtv_63m'] >= 10))
    df = day[mask].nlargest(15, 'rs_pct')

    if df.empty:
        month_ret = 0.0; n_holds = 0
    else:
        tkrs = df['ticker'].tolist()
        wts  = get_weights(ts, tkrs, exp)
        fwd  = []
        for t, w in zip(tkrs, wts):
            if t not in close_px.columns: fwd.append(0.0); continue
            try:
                p0 = close_px.loc[close_px.index <= ts, t].dropna().iloc[-1]
                p1 = close_px.loc[close_px.index <= ne, t].dropna().iloc[-1]
                fwd.append((p1/p0 - 1) * w)
            except: fwd.append(0.0)
        new_h = set(tkrs)
        turn  = len(prev_h.symmetric_difference(new_h)) / max(len(prev_h | new_h), 1)
        month_ret = sum(fwd) - 0.0015 * turn
        n_holds = len(tkrs)
        prev_h = new_h

    equity *= (1 + month_ret)
    if equity > peak: peak = equity
    dd = (equity - peak) / peak
    if dd < max_dd_all: max_dd_all = dd

    try:
        q0 = qqq.loc[qqq.index <= ts].iloc[-1]
        q1 = qqq.loc[qqq.index <= ne].iloc[-1]
        bm = q1/q0 - 1
    except: bm = 0.0

    yr = ne.year
    year_data.setdefault(yr, {'p': 1.0, 'b': 1.0, 'months': 0, 'holds': [], 'bull_m': 0, 'bear_m': 0, 'mdd': 0.0, 'peak': 1.0, 'eq_start': equity/(1+month_ret)})
    year_data[yr]['p'] *= (1 + month_ret)
    year_data[yr]['b'] *= (1 + bm)
    year_data[yr]['months'] += 1
    year_data[yr]['holds'].append(n_holds)
    if bull: year_data[yr]['bull_m'] += 1
    else:    year_data[yr]['bear_m'] += 1
    # Track intra-year peak/dd
    yr_eq = year_data[yr]['eq_start'] * year_data[yr]['p']
    if yr_eq > year_data[yr]['peak']: year_data[yr]['peak'] = yr_eq
    yr_dd = (yr_eq - year_data[yr]['peak']) / year_data[yr]['peak']
    if yr_dd < year_data[yr]['mdd']: year_data[yr]['mdd'] = yr_dd

# Overall CAGR
n_yrs = (pd.Timestamp('2026-08-20') - pd.Timestamp('2017-01-01')).days / 365.25
cagr_port = (equity ** (1/n_yrs) - 1) * 100
q0 = qqq.loc[qqq.index >= '2017-01-01'].iloc[0]
q1 = qqq.loc[qqq.index <= '2026-08-20'].iloc[-1]
cagr_qqq  = ((q1/q0) ** (1/n_yrs) - 1) * 100

print('='*70)
print('SYSTEM v4.0 — YEAR-BY-YEAR BACKTEST RESULTS (2017-2026)')
print('='*70)
print(f'  {"Year":<6} {"Port":>7} {"QQQ":>7} {"Excess":>8} {"IntraDD":>8} {"Regime":>10} {"AvgH":>5}')
print('-'*65)

all_port, all_qqq, all_exc = [], [], []
for yr in sorted(year_data.keys()):
    d = year_data[yr]
    port_ret = (d['p'] - 1) * 100
    qqq_ret  = (d['b'] - 1) * 100
    excess   = port_ret - qqq_ret
    intra_dd = d['mdd'] * 100
    bull_m   = d['bull_m']; bear_m = d['bear_m']
    avg_h    = np.mean(d['holds']) if d['holds'] else 0
    regime_str = f"B{bull_m}/bear{bear_m}" if bear_m > 0 else f"Bull{bull_m}mo"
    sign = '+' if excess >= 0 else ''
    all_port.append(port_ret); all_qqq.append(qqq_ret); all_exc.append(excess)
    win = 'W' if excess > 0 else 'L'
    print(f"  {yr:<6} {port_ret:>+6.1f}%  {qqq_ret:>+6.1f}%  {sign}{excess:>6.1f}% [{win}]  {intra_dd:>+6.1f}%  {regime_str:>10}  {avg_h:>4.1f}")

print('-'*65)
wins = sum(1 for e in all_exc if e > 0)
print(f"  {'TOTAL':<6}  CAGR={cagr_port:+.1f}%  QQQ={cagr_qqq:+.1f}%  Excess={cagr_port-cagr_qqq:+.1f}%")
print(f"  Max DD (full period): {max_dd_all*100:.1f}%  |  Win rate: {wins}/{len(all_exc)} years")
print(f"  Avg annual excess: {np.mean(all_exc):+.1f}%  |  Std: {np.std(all_exc):.1f}%")
print()
print('  Note: Signal data ends 2026-08-27. 2026 = partial year (Jan-Aug).')
print('  Cost: 0.15% round-trip. Regime: IWM > MA200 = BULL (100%), else BEAR (50%).')
