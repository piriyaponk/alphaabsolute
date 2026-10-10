"""
21_build_revenue_signals.py
Build point-in-time revenue table from cached EDGAR data.

For each (ticker, rebalance_date):
  - Use only quarterly reports with filed_date <= rebalance_date  (no lookahead)
  - Find most recent quarter available
  - Find same quarter 1 year prior (YoY)
  - Find prior quarter's YoY (acceleration)
  - Compute rev_yoy_pct and rev_accel

Output: data/backtest/revenue_signals.parquet
  columns: date, ticker, rev_yoy_pct, rev_accel, rev_ttm, rev_latest_q, rev_data_lag_days
"""
import sys, io, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))

import pandas as pd
import numpy as np
from pathlib import Path

CACHE_DIR = Path('data/backtest/edgar_cache')

# ── Load rebalance dates from signals ────────────────────────────────────────
print("Loading signals.parquet...")
sig = pd.read_parquet('data/backtest/signals.parquet')
sig['date'] = pd.to_datetime(sig['date'])

# Monthly rebalance dates (month-end trading days) from 2016-01 to 2026-08
# We go back to 2016 to have YoY lookback for 2017 start
rebal_dates = pd.date_range('2016-01-01', '2026-08-31', freq='ME')
# Snap to actual trading days in signals
all_dates = sig['date'].unique()
all_dates_sorted = np.sort(all_dates)

def snap_to_trading_day(dt):
    mask = all_dates_sorted <= dt
    if mask.any():
        return pd.Timestamp(all_dates_sorted[mask][-1])
    return None

rebal_dates_snapped = []
for d in rebal_dates:
    snapped = snap_to_trading_day(d)
    if snapped is not None:
        rebal_dates_snapped.append(snapped)
rebal_dates_snapped = sorted(set(rebal_dates_snapped))
print(f"Rebalance dates: {len(rebal_dates_snapped)} ({rebal_dates_snapped[0].date()} to {rebal_dates_snapped[-1].date()})")

# ── Parse EDGAR cache ─────────────────────────────────────────────────────────
def parse_quarters(data):
    """Return DataFrame of (end_date, filed_date, val) for quarterly reports."""
    rows = []
    for item in data.get('quarterly', []):
        end = item.get('end')
        filed = item.get('filed')
        val = item.get('val')
        if end and filed and val is not None:
            rows.append({'end': pd.Timestamp(end), 'filed': pd.Timestamp(filed), 'val': float(val)})
    if not rows:
        # Fall back to annual data, compute implied quarters if available
        return pd.DataFrame(columns=['end', 'filed', 'val'])
    df = pd.DataFrame(rows)
    # Dedup: for same end date, keep latest filing
    df = df.sort_values(['end', 'filed']).drop_duplicates('end', keep='last')
    return df.sort_values('end').reset_index(drop=True)


def compute_rev_at_date(ticker, qdf, rebal_dt):
    """Compute revenue metrics available at rebal_dt (no lookahead)."""
    # Filter: only data that was filed before this rebalance date
    available = qdf[qdf['filed'] <= rebal_dt]
    if len(available) < 2:
        return None

    # Latest available quarter
    latest = available.iloc[-1]
    latest_end = latest['end']
    latest_val = latest['val']

    # YoY: same quarter 1 year ago (within 46 days)
    one_yr_ago = latest_end - pd.DateOffset(months=12)
    mask_yoy = (abs(available['end'] - one_yr_ago) <= pd.Timedelta(days=46))
    if not mask_yoy.any():
        return None
    yoy_val = available[mask_yoy].iloc[-1]['val']
    rev_yoy = (latest_val - yoy_val) / abs(yoy_val) * 100 if yoy_val != 0 else np.nan

    # Prior quarter YoY (for acceleration = rev_yoy - prior_rev_yoy)
    if len(available) >= 2:
        prior = available.iloc[-2]
        prior_end = prior['end']
        prior_val = prior['val']
        prior_one_yr = prior_end - pd.DateOffset(months=12)
        mask_prior_yoy = (abs(available['end'] - prior_one_yr) <= pd.Timedelta(days=46))
        if mask_prior_yoy.any():
            prior_yoy_val = available[mask_prior_yoy].iloc[-1]['val']
            prior_rev_yoy = (prior_val - prior_yoy_val) / abs(prior_yoy_val) * 100 if prior_yoy_val != 0 else np.nan
            rev_accel = rev_yoy - prior_rev_yoy if pd.notna(rev_yoy) and pd.notna(prior_rev_yoy) else np.nan
        else:
            rev_accel = np.nan
    else:
        rev_accel = np.nan

    # TTM
    last4 = available.tail(4)
    rev_ttm = last4['val'].sum() / 1e9 if len(last4) >= 4 else np.nan

    # Data lag: how many days old is the latest filing relative to rebal_dt
    lag_days = (rebal_dt - latest['filed']).days

    return {
        'rev_yoy_pct': rev_yoy,
        'rev_accel': rev_accel,
        'rev_ttm_b': rev_ttm,
        'rev_latest_q_b': latest_val / 1e9,
        'rev_data_lag_days': lag_days,
    }


# ── Build point-in-time table ─────────────────────────────────────────────────
cache_files = list(CACHE_DIR.glob('*_revenue.json'))
print(f"\nParsing {len(cache_files)} cached EDGAR files...")

all_records = []
skipped_no_data = 0
skipped_no_quarters = 0

for i, f in enumerate(cache_files, 1):
    ticker = f.stem.replace('_revenue', '')
    with open(f) as fh:
        data = json.load(fh)

    if not data.get('concept'):
        skipped_no_data += 1
        continue

    qdf = parse_quarters(data)
    if len(qdf) < 4:
        skipped_no_quarters += 1
        continue

    # Compute metrics at each rebalance date
    for rebal_dt in rebal_dates_snapped:
        metrics = compute_rev_at_date(ticker, qdf, rebal_dt)
        if metrics:
            all_records.append({'date': rebal_dt, 'ticker': ticker, **metrics})

    if i % 100 == 0:
        print(f"  [{i}/{len(cache_files)}] records so far: {len(all_records):,}")

print(f"\nTotal records: {len(all_records):,}")
print(f"Skipped (no concept): {skipped_no_data} | Skipped (< 4 quarters): {skipped_no_quarters}")

if not all_records:
    print("ERROR: No records generated. Check cache.")
    sys.exit(1)

rev_df = pd.DataFrame(all_records)
rev_df['date'] = pd.to_datetime(rev_df['date'])

print(f"\nRevenue signals shape: {rev_df.shape}")
print(f"Tickers covered: {rev_df['ticker'].nunique()}")
print(f"Date range: {rev_df['date'].min().date()} to {rev_df['date'].max().date()}")
print(f"\nrev_yoy_pct stats:")
print(rev_df['rev_yoy_pct'].describe().round(1))
print(f"\nrev_accel stats:")
print(rev_df['rev_accel'].describe().round(1))

# Save
out_path = 'data/backtest/revenue_signals.parquet'
rev_df.to_parquet(out_path, index=False)
print(f"\nSaved: {out_path} ({len(rev_df):,} rows)")
print("Next: run 22_backtest_revenue_gate.py")
