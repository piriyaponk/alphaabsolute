"""Screen the latest date in signals.parquet with SYSTEM v3.0 + vol_contract_max=1.5."""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd, numpy as np

sig = pd.read_parquet('data/backtest/signals.parquet')
sig['date'] = pd.to_datetime(sig['date'])

latest = sig['date'].max()
print(f"Latest signal date: {latest.date()}")

df = sig[sig['date'] == latest].copy()
print(f"Tickers on latest date: {len(df)}")

# ── SYSTEM v3.0 + vol_contract_max=1.5 filters ───────────────────────────────
# Regime: check SPY vs MA200
spy = sig[sig['ticker'] == 'SPY'].set_index('date')
spy_today = spy.loc[latest] if latest in spy.index else None
spy_above_ma200 = bool(spy_today['spy_above_ma200']) if spy_today is not None else True
regime = "BULL" if spy_above_ma200 else "BEAR"

vol_trend_thr = 0.75 if spy_above_ma200 else 0.95
bear_exposure = 1.00 if spy_above_ma200 else 0.50

print(f"\nMarket regime: {regime} (SPY {'>' if spy_above_ma200 else '<'} MA200)")
print(f"vol_trend threshold: {vol_trend_thr} | Exposure: {bear_exposure*100:.0f}%")

# Exclude benchmarks/ETFs
exclude = {'QQQ', 'SPY', 'IWM'}
df = df[~df['ticker'].isin(exclude)].copy()

# Gate 1: RS >= 80
gate1 = df['rs_pct'] >= 80
print(f"\nGate 1 (rs_pct>=80):       {gate1.sum():3d} pass / {len(df)} total")

# Gate 2: vol_trend >= threshold (regime-conditional)
gate2 = df['vol_trend'] >= vol_trend_thr
print(f"Gate 2 (vol_trend>={vol_trend_thr}):  {(gate1 & gate2).sum():3d} pass")

# Gate 3: price > MA200
gate3 = df['price_vs_ma200_pct'] > 0
print(f"Gate 3 (price>MA200):       {(gate1 & gate2 & gate3).sum():3d} pass")

# Gate 4: ADTV >= $10M
gate4 = df['adtv_63m'] >= 10
print(f"Gate 4 (adtv>=10M):         {(gate1 & gate2 & gate3 & gate4).sum():3d} pass")

# Gate 5: vol_contract <= 1.5
gate5 = df['vol_contraction'] <= 1.5
print(f"Gate 5 (vol_contract<=1.5): {(gate1 & gate2 & gate3 & gate4 & gate5).sum():3d} pass")

passed = df[gate1 & gate2 & gate3 & gate4 & gate5].copy()

# Top 15 by RS
top15 = passed.nlargest(15, 'rs_pct').copy()

# Softmax weights on ADTV^1.5
adtv_pow = top15['adtv_63m'] ** 1.5
top15['raw_weight'] = adtv_pow / adtv_pow.sum()

# Cap at 18%
cap = 0.18
for _ in range(20):
    capped = top15['raw_weight'].clip(upper=cap)
    overflow = top15['raw_weight'].sum() - capped.sum()
    uncapped_mask = top15['raw_weight'] < cap
    if uncapped_mask.sum() == 0 or overflow < 1e-8:
        break
    top15['raw_weight'] = capped
    top15.loc[uncapped_mask, 'raw_weight'] += overflow * (
        top15.loc[uncapped_mask, 'raw_weight'] / top15.loc[uncapped_mask, 'raw_weight'].sum()
    )

top15['weight'] = top15['raw_weight'] * bear_exposure
top15 = top15.sort_values('weight', ascending=False).reset_index(drop=True)

print(f"\n{'='*75}")
print(f"PORTFOLIO — {latest.date()} | Regime={regime} | Deployed={bear_exposure*100:.0f}%")
print(f"{'='*75}")
print(f"{'#':>2} {'Ticker':<8} {'RS%':>5} {'VolTrend':>9} {'VolCont':>8} {'ADTV($M)':>9} {'Weight':>7} {'Price':>8}")
print(f"{'-'*75}")
for i, row in top15.iterrows():
    print(f"{i+1:>2} {row['ticker']:<8} {row['rs_pct']:>5.1f} {row['vol_trend']:>9.2f} {row['vol_contraction']:>8.2f} {row['adtv_63m']:>9.1f} {row['weight']*100:>6.1f}% ${row['close']:>7.2f}")

print(f"\nTotal deployed: {top15['weight'].sum()*100:.1f}% | Cash: {(1-top15['weight'].sum())*100:.1f}%")
print(f"Avg RS: {top15['rs_pct'].mean():.1f} | Avg VolTrend: {top15['vol_trend'].mean():.2f} | Avg ADTV: ${top15['adtv_63m'].mean():.0f}M")
