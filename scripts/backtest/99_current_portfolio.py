import pandas as pd, numpy as np, warnings; warnings.filterwarnings('ignore')
import os; os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))

sig = pd.read_parquet('data/backtest/signals.parquet')
sig['date'] = pd.to_datetime(sig['date'])
latest = sig['date'].max()
print('Latest date:', latest.date())

close_px = sig.pivot_table(index='date', columns='ticker', values='close').sort_index()
log_ret  = np.log(close_px / close_px.shift(1))
vol_63d  = log_ret.rolling(63, min_periods=20).std() * np.sqrt(252)

beta_lkp = {}
for row in sig[['date','ticker','beta_252']].dropna().itertuples():
    b = float(row.beta_252)
    if not np.isnan(b): beta_lkp[(row.date, row.ticker)] = max(0.3, min(b, 4.0))

iwm = close_px['IWM'].dropna()
iwm_ma200 = iwm.rolling(200, min_periods=150).mean()
bull = bool(iwm.iloc[-1] > iwm_ma200.iloc[-1])
regime = 'BULL' if bull else 'BEAR'
print('IWM regime:', regime)
vt = 0.75 if bull else 0.95
exp = 1.0 if bull else 0.5

ts = latest
day = sig[sig['date'] == ts].copy()
mask = ((day['rs_pct'] >= 80) & (day['price_vs_ma200_pct'] >= 0)
        & (day['vol_trend'] >= vt) & (day['adtv_63m'] >= 10))
df = day[mask].nlargest(15, 'rs_pct').copy()
print('Passing screens:', len(df))

vols, maxws = [], []
for tkr in df['ticker']:
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
w = w * exp
df['weight'] = w
df = df.sort_values('weight', ascending=False).reset_index(drop=True)

print()
print('='*62)
print('v4.0 PORTFOLIO  @', latest.date(), '|', regime, '| Cash', round((1-w.sum())*100), '%')
print('='*62)
print('#   Ticker   Weight    RS   Beta   ADTV($M)  vsMA200')
print('-'*55)
for i, r in df.iterrows():
    tkr = r['ticker']
    b   = beta_lkp.get((ts, tkr), float('nan'))
    print(f"{i+1:<3} {tkr:<7} {r['weight']*100:>6.1f}%  {r['rs_pct']:>4.0f}  {b:>5.1f}  {r['adtv_63m']:>8.1f}  {r['price_vs_ma200_pct']:>+7.1f}%")

print('-'*55)
print('Total deployed:', round(w.sum()*100, 1), '%  | Cash:', round((1-w.sum())*100, 1), '%')
print('Avg weight:', round(df['weight'].mean()*100, 1), '%  | Max:', round(df['weight'].max()*100, 1), '%')
