"""
Universe Expansion v2 — Real Russell 1000 approach
1. Fetch all 6764 US tickers from GitHub rreichel3 dataset
2. Remove existing 506 in prices.parquet
3. Batch-query Yahoo Finance for market cap (100/call)
4. Keep those with market cap >= MIN_MARKET_CAP ($5B ~ Russell 1000 cutoff)
5. Download OHLCV for selected tickers
6. Merge into prices.parquet + recompute signals
"""

import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd, numpy as np, requests, time, json, ssl
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ssl._create_default_https_context = ssl._create_unverified_context

SESSION = requests.Session()
SESSION.verify = False
SESSION.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

MIN_MARKET_CAP = 5_000_000_000   # $5B — approx Russell 1000 cutoff

START_DATE = '2015-01-01'
END_DATE   = '2026-08-28'
START_TS   = int(pd.Timestamp(START_DATE).timestamp())
END_TS     = int(pd.Timestamp(END_DATE).timestamp())


# ─── STEP 1: ALL US TICKERS FROM GITHUB ──────────────────────────────────
print('='*80)
print('STEP 1: Fetching all US stock tickers from GitHub...')
resp = SESSION.get(
    'https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/nyse/nyse_tickers.txt',
    timeout=20)
resp2 = SESSION.get(
    'https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/nasdaq/nasdaq_tickers.txt',
    timeout=20)
resp3 = SESSION.get(
    'https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/amex/amex_tickers.txt',
    timeout=20)

all_tickers = set()
for r in [resp, resp2, resp3]:
    if r.status_code == 200:
        all_tickers.update([t.strip() for t in r.text.splitlines() if t.strip()])

import re
def is_valid(t):
    return bool(re.match(r'^[A-Z]{1,5}$', t)) and '-' not in t and '.' not in t

all_tickers = {t for t in all_tickers if is_valid(t)}
print(f'  Total valid US tickers: {len(all_tickers)}')

# ─── STEP 2: FIND NEW ONES ───────────────────────────────────────────────
existing_df = pd.read_parquet('data/backtest/prices.parquet')
existing_tickers = set(existing_df['ticker'].unique())
print(f'  Existing in prices.parquet: {len(existing_tickers)}')

candidate_tickers = sorted(all_tickers - existing_tickers)
print(f'  Candidates to check: {len(candidate_tickers)}')

# ─── STEP 3: GET MARKET CAP VIA YAHOO FINANCE ────────────────────────────
print(f'\nSTEP 2: Querying Yahoo Finance market cap (batch of 100)...')

# Get crumb
try:
    SESSION.get('https://fc.yahoo.com', timeout=10)
    crumb = SESSION.get('https://query2.finance.yahoo.com/v1/test/getcrumb',
                        headers={'Accept': 'text/plain'}, timeout=10).text.strip()
    print(f'  Yahoo crumb: ok ({crumb[:15]}...)')
except Exception as e:
    crumb = None
    print(f'  Crumb failed: {e}')


def get_market_caps_batch(tickers, crumb=None):
    """Query Yahoo Finance quote endpoint for market cap. Returns dict ticker->market_cap."""
    joined = ','.join(tickers)
    url = (f'https://query2.finance.yahoo.com/v7/finance/quote'
           f'?symbols={joined}&fields=marketCap,longName,quoteType')
    if crumb:
        url += f'&crumb={crumb}'
    try:
        resp = SESSION.get(url, timeout=20)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        quotes = data.get('quoteResponse', {}).get('result', [])
        result = {}
        for q in quotes:
            t = q.get('symbol', '')
            mc = q.get('marketCap', 0) or 0
            qt = q.get('quoteType', '')
            if qt == 'EQUITY' and t:
                result[t] = mc
        return result
    except Exception:
        return {}


batch_size = 100
qualified = {}   # ticker -> market_cap
checked = 0
for i in range(0, len(candidate_tickers), batch_size):
    batch = candidate_tickers[i:i + batch_size]
    mc_map = get_market_caps_batch(batch, crumb)
    for t, mc in mc_map.items():
        if mc >= MIN_MARKET_CAP:
            qualified[t] = mc
    checked += len(batch)
    if checked % 500 == 0 or i == 0:
        print(f'  Checked {checked}/{len(candidate_tickers)}, qualified so far: {len(qualified)}')
    time.sleep(0.2)

qualified_sorted = sorted(qualified.items(), key=lambda x: -x[1])
print(f'\n  Qualified (market cap >= ${MIN_MARKET_CAP/1e9:.0f}B): {len(qualified_sorted)} tickers')
if qualified_sorted[:30]:
    print('  Top 30:')
    for t, mc in qualified_sorted[:30]:
        print(f'    {t:<10} ${mc/1e9:.1f}B')

# Save qualified list
with open('data/backtest/expansion_candidates.json', 'w') as f:
    json.dump({'candidates': {t: mc for t, mc in qualified_sorted},
               'total': len(qualified_sorted),
               'min_market_cap_b': MIN_MARKET_CAP/1e9,
               'updated': pd.Timestamp.now().isoformat()}, f, indent=2)
print(f'  Saved expansion_candidates.json')

new_tickers = [t for t, _ in qualified_sorted]

if not new_tickers:
    print('\nNo new qualified tickers. Exiting.')
    sys.exit(0)

# ─── STEP 4: DOWNLOAD NEW TICKERS ────────────────────────────────────────
print(f'\nSTEP 3: Downloading {len(new_tickers)} new tickers...')


def download_ticker(ticker, crumb=None, retries=2):
    for attempt in range(retries):
        try:
            url = (f'https://query2.finance.yahoo.com/v8/finance/chart/{ticker}'
                   f'?period1={START_TS}&period2={END_TS}&interval=1d'
                   f'&events=history&includeAdjustedClose=true')
            if crumb:
                url += f'&crumb={crumb}'
            resp = SESSION.get(url, timeout=15)
            if resp.status_code != 200:
                return ticker, None, f'HTTP {resp.status_code}'
            data = resp.json()
            result = data.get('chart', {}).get('result', [])
            if not result:
                return ticker, None, 'no data'
            r = result[0]
            timestamps = r.get('timestamp', [])
            quotes = r.get('indicators', {}).get('quote', [{}])[0]
            adjclose = r.get('indicators', {}).get('adjclose', [{}])[0].get('adjclose', [])
            if not timestamps:
                return ticker, None, 'empty'
            df = pd.DataFrame({
                'date':      pd.to_datetime(timestamps, unit='s').normalize(),
                'open':      quotes.get('open', []),
                'high':      quotes.get('high', []),
                'low':       quotes.get('low', []),
                'close':     quotes.get('close', []),
                'volume':    quotes.get('volume', []),
                'adj_close': adjclose if adjclose else quotes.get('close', []),
            })
            df['ticker'] = ticker
            df = df.dropna(subset=['close'])
            df = df[df['close'] > 0]
            if len(df) < 100:
                return ticker, None, f'only {len(df)} rows'
            adj_ratio = df['adj_close'] / df['close']
            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col] * adj_ratio
            df = df[['date', 'ticker', 'open', 'high', 'low', 'close', 'volume', 'adj_close']]
            return ticker, df, 'ok'
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1)
            else:
                return ticker, None, str(e)


downloaded = []
failed = []

with ThreadPoolExecutor(max_workers=5) as ex:
    futures = {ex.submit(download_ticker, t, crumb): t for t in new_tickers}
    for i, fut in enumerate(as_completed(futures), 1):
        ticker, df, status = fut.result()
        if df is not None:
            downloaded.append(df)
        else:
            failed.append((ticker, status))
        if i % 50 == 0 or i <= 3:
            print(f'  [{i}/{len(new_tickers)}] {ticker}: {status} ({len(downloaded)} ok)')
        time.sleep(0.05)

print(f'\n  Downloaded: {len(downloaded)} OK, {len(failed)} failed')

if not downloaded:
    print('No data downloaded. Exiting.')
    sys.exit(0)

# ─── STEP 5: MERGE INTO prices.parquet ───────────────────────────────────
print('\nSTEP 4: Merging into prices.parquet...')
new_df = pd.concat(downloaded, ignore_index=True)
new_df['date'] = pd.to_datetime(new_df['date'])

combined = pd.concat([existing_df, new_df], ignore_index=True)
combined = combined.drop_duplicates(subset=['date', 'ticker'])
combined = combined.sort_values(['ticker', 'date']).reset_index(drop=True)
combined.to_parquet('data/backtest/prices.parquet', index=False)
print(f'  Saved: {combined["ticker"].nunique()} tickers, {len(combined):,} rows')
print(f'  Date range: {combined["date"].min().date()} to {combined["date"].max().date()}')

ticker_list = sorted(combined['ticker'].unique())
with open('data/backtest/universe_tickers.json', 'w') as f:
    json.dump({'tickers': ticker_list, 'total': len(ticker_list),
               'original_sp500': len(existing_tickers),
               'new_added': len(downloaded), 'failed': [t for t, _ in failed[:50]],
               'updated': pd.Timestamp.now().isoformat()}, f, indent=2)

# ─── STEP 6: RECOMPUTE SIGNALS ───────────────────────────────────────────
print('\nSTEP 5: Recomputing signals.parquet...')
import subprocess
result = subprocess.run([sys.executable, 'scripts/backtest/02_compute_signals.py'],
    capture_output=True, text=True, timeout=600, cwd=os.getcwd())
if result.returncode == 0:
    print('  Signals recomputed OK')
    last_lines = result.stdout.strip().splitlines()[-5:]
    for l in last_lines: print(f'  {l}')
else:
    print(f'  ERROR: {result.returncode}')
    print(result.stderr[-2000:])

print('\nDone. Run 03b_backtest_v2.py next to test expanded universe.')
