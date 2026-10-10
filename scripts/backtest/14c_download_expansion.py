"""
Step 2 of universe expansion: Download tickers from expansion_candidates.json
and merge into prices.parquet. Skips existing tickers.
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd, requests, time, json, ssl
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ssl._create_default_https_context = ssl._create_unverified_context

SESSION = requests.Session()
SESSION.verify = False
SESSION.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

START_TS = int(pd.Timestamp('2015-01-01').timestamp())
END_TS   = int(pd.Timestamp('2026-08-28').timestamp())

# Load candidates
with open('data/backtest/expansion_candidates.json') as f:
    cand_data = json.load(f)

all_candidates = list(cand_data['candidates'].keys())
print(f'Total candidates: {len(all_candidates)} (market cap >= ${cand_data["min_market_cap_b"]:.0f}B)')

# Filter: only US stocks (rough filter — exclude known foreign ADR suffixes)
# Foreign stocks often have 2-letter country codes in their data
# We'll just download all and let the screen filter them
existing_df = pd.read_parquet('data/backtest/prices.parquet')
existing_tickers = set(existing_df['ticker'].unique())
new_tickers = [t for t in all_candidates if t not in existing_tickers]
print(f'Already in prices.parquet: {len(existing_tickers)}')
print(f'New to download: {len(new_tickers)}')

# Get crumb
try:
    SESSION.get('https://fc.yahoo.com', timeout=10)
    crumb = SESSION.get('https://query2.finance.yahoo.com/v1/test/getcrumb',
                        headers={'Accept': 'text/plain'}, timeout=10).text.strip()
    print(f'Yahoo crumb: ok')
except Exception:
    crumb = None
    print('Crumb failed')


def download_ticker(ticker, retries=2):
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
                return ticker, None, str(e)[:60]


print(f'\nDownloading {len(new_tickers)} tickers (5 threads)...')
downloaded = []
failed = []

with ThreadPoolExecutor(max_workers=5) as ex:
    futures = {ex.submit(download_ticker, t): t for t in new_tickers}
    for i, fut in enumerate(as_completed(futures), 1):
        ticker, df, status = fut.result()
        if df is not None:
            downloaded.append(df)
        else:
            failed.append((ticker, status))
        if i % 100 == 0 or i <= 5:
            print(f'  [{i}/{len(new_tickers)}] ok={len(downloaded)} fail={len(failed)} | last={ticker}:{status}')
        time.sleep(0.05)

print(f'\nDownloaded: {len(downloaded)} OK, {len(failed)} failed')

if not downloaded:
    print('Nothing to merge. Exiting.')
    sys.exit(0)

print('\nMerging into prices.parquet...')
new_df = pd.concat(downloaded, ignore_index=True)
# Keep date as string to match existing parquet schema
new_df['date'] = new_df['date'].dt.strftime('%Y-%m-%d')
combined = pd.concat([existing_df, new_df], ignore_index=True)
combined = combined.drop_duplicates(subset=['date', 'ticker'])
combined = combined.sort_values(['ticker', 'date']).reset_index(drop=True)
combined.to_parquet('data/backtest/prices.parquet', index=False)
print(f'Saved: {combined["ticker"].nunique()} tickers, {len(combined):,} rows')

with open('data/backtest/universe_tickers.json', 'w') as f:
    json.dump({'tickers': sorted(combined['ticker'].unique().tolist()),
               'total': combined['ticker'].nunique(),
               'original': len(existing_tickers), 'new_added': len(downloaded),
               'failed_count': len(failed),
               'updated': pd.Timestamp.now().isoformat()}, f, indent=2)

print('\nNow recomputing signals.parquet...')
import subprocess
r = subprocess.run([sys.executable, 'scripts/backtest/02_compute_signals.py'],
    capture_output=True, text=True, timeout=600, cwd=os.getcwd())
if r.returncode == 0:
    print('Signals recomputed OK')
    for l in r.stdout.strip().splitlines()[-6:]: print(f'  {l}')
else:
    print(f'Signal compute ERROR: {r.returncode}')
    print(r.stderr[-1000:])

print('\nExpansion complete. Run 03b_backtest_v2.py next.')
