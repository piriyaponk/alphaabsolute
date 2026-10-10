"""
Universe Expansion — Russell 1000 + current S&P500
Step 1: Fetch Russell 1000 tickers from iShares IWB holdings
Step 2: Find which are NOT in current prices.parquet
Step 3: Download missing tickers via Yahoo Finance query2 API
Step 4: Merge into prices.parquet + recompute signals

After this, re-run 03b_backtest_v2 on the full universe.
"""

import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd, numpy as np, requests, time, json, ssl
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO

ssl._create_default_https_context = ssl._create_unverified_context

SESSION = requests.Session()
SESSION.verify = False
SESSION.headers.update({'User-Agent': 'Mozilla/5.0'})


# ============================================================
# STEP 1: GET RUSSELL 1000 TICKERS
# ============================================================
def fetch_russell1000_ishares():
    """Fetch IWB (iShares Russell 1000 ETF) holdings."""
    url = ('https://www.ishares.com/us/products/239707/'
           'ishares-russell-1000-etf/1467271812596.ajax'
           '?tab=holdings&fileType=csv')
    try:
        resp = SESSION.get(url, timeout=30)
        if resp.status_code == 200:
            lines = resp.text.splitlines()
            # skip header rows until we hit the data
            start = 0
            for i, line in enumerate(lines):
                if line.startswith('Ticker,'):
                    start = i
                    break
            df = pd.read_csv(StringIO('\n'.join(lines[start:])), thousands=',')
            tickers = df['Ticker'].dropna().tolist()
            tickers = [t.strip() for t in tickers if isinstance(t, str) and t.strip() and t.strip() != '-']
            print(f'  iShares IWB: {len(tickers)} tickers')
            return tickers
    except Exception as e:
        print(f'  iShares failed: {e}')
    return []

def fetch_qqq_tickers():
    """Fetch QQQ (NASDAQ-100) holdings from Invesco."""
    url = 'https://www.invesco.com/us/financial-products/etfs/holdings/main/holdings/0?audienceType=Investor&action=download&ticker=QQQ'
    try:
        resp = SESSION.get(url, timeout=20)
        if resp.status_code == 200:
            df = pd.read_csv(StringIO(resp.text))
            col = [c for c in df.columns if 'ticker' in c.lower() or 'symbol' in c.lower()]
            if col:
                tickers = df[col[0]].dropna().tolist()
                tickers = [t.strip() for t in tickers if isinstance(t, str) and t.strip()]
                print(f'  Invesco QQQ: {len(tickers)} tickers')
                return tickers
    except Exception as e:
        print(f'  QQQ failed: {e}')
    return []

def fetch_sp500_wikipedia():
    """Fallback: fetch S&P 500 from Wikipedia."""
    try:
        tables = pd.read_html('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies', verify=False)
        df = tables[0]
        tickers = df['Symbol'].tolist()
        tickers = [t.replace('.', '-') for t in tickers]
        print(f'  Wikipedia S&P500: {len(tickers)} tickers')
        return tickers
    except Exception as e:
        print(f'  Wikipedia failed: {e}')
    return []

def fetch_russell1000_alternative():
    """Alternative: get from FTSE Russell / Quandl-style URL."""
    # Try stooq ETF constituents
    # Try SPDR or Vanguard alternative
    urls_to_try = [
        # Vanguard VONE (Russell 1000)
        'https://advisors.vanguard.com/web/c1/fas-investmentproducts/0970/portfolio',
    ]
    # If all else fails, use a hardcoded approach: combine S&P500 + top NASDAQ stocks
    return []


print('='*80)
print('STEP 1: Fetching universe tickers')
print('='*80)

r1000 = fetch_russell1000_ishares()
qqq   = fetch_qqq_tickers()

# Load existing
existing_df = pd.read_parquet('data/backtest/prices.parquet')
existing_tickers = set(existing_df['ticker'].unique())
print(f'\nExisting in prices.parquet: {len(existing_tickers)} tickers')

# Build target universe
universe = set(r1000) | set(qqq) | existing_tickers

# Clean up tickers: remove non-standard
def clean_ticker(t):
    import re
    t = str(t).strip().upper()
    # Remove entries that aren't real tickers
    if re.match(r'^[A-Z]{1,5}(-[A-Z])?$', t):
        return t
    return None

universe = {clean_ticker(t) for t in universe if clean_ticker(t)}
universe.discard(None)

new_tickers = sorted(universe - existing_tickers)
print(f'Target universe: {len(universe)} tickers')
print(f'New tickers to download: {len(new_tickers)}')
if new_tickers:
    print(f'Sample new: {new_tickers[:20]}')


# ============================================================
# STEP 2: DOWNLOAD NEW TICKERS
# ============================================================
START_DATE = '2015-01-01'
END_DATE   = '2026-08-28'

import calendar
START_TS = int(pd.Timestamp(START_DATE).timestamp())
END_TS   = int(pd.Timestamp(END_DATE).timestamp())

def get_crumb_and_cookies():
    """Get Yahoo Finance crumb for authentication."""
    try:
        r = SESSION.get('https://fc.yahoo.com', timeout=10)
        r2 = SESSION.get(
            'https://query2.finance.yahoo.com/v1/test/getcrumb',
            headers={'Accept': 'text/plain'}, timeout=10)
        if r2.status_code == 200:
            return r2.text.strip()
    except Exception:
        pass
    return None

def download_ticker(ticker, crumb=None, retries=2):
    """Download OHLCV for one ticker from Yahoo Finance query2."""
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
                'date':   pd.to_datetime(timestamps, unit='s').normalize(),
                'open':   quotes.get('open', []),
                'high':   quotes.get('high', []),
                'low':    quotes.get('low', []),
                'close':  quotes.get('close', []),
                'volume': quotes.get('volume', []),
                'adj_close': adjclose if adjclose else quotes.get('close', []),
            })
            df['ticker'] = ticker
            df = df.dropna(subset=['close'])
            df = df[df['close'] > 0]
            if len(df) < 100:
                return ticker, None, f'only {len(df)} rows'
            # Apply adjustment ratio to OHLC
            df['adj_ratio'] = df['adj_close'] / df['close']
            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col] * df['adj_ratio']
            df = df[['date','ticker','open','high','low','close','volume','adj_close']]
            return ticker, df, 'ok'
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1)
            else:
                return ticker, None, str(e)

if not new_tickers:
    print('\nNo new tickers to download. Proceeding with existing data.')
else:
    print(f'\nSTEP 2: Downloading {len(new_tickers)} new tickers...')
    crumb = get_crumb_and_cookies()
    print(f'  Yahoo crumb: {"ok" if crumb else "none (will retry without)"}')

    downloaded = []
    failed = []

    def dl_worker(t):
        return download_ticker(t, crumb=crumb)

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(dl_worker, t): t for t in new_tickers}
        for i, fut in enumerate(as_completed(futures), 1):
            ticker, df, status = fut.result()
            if df is not None:
                downloaded.append(df)
                if i % 50 == 0 or i <= 5:
                    print(f'  [{i}/{len(new_tickers)}] {ticker}: {len(df)} rows')
            else:
                failed.append((ticker, status))
                if i % 50 == 0 or len(failed) <= 10:
                    print(f'  [{i}/{len(new_tickers)}] {ticker}: FAIL ({status})')
            time.sleep(0.05)

    print(f'\n  Downloaded: {len(downloaded)} tickers OK, {len(failed)} failed')
    if failed[:20]:
        print(f'  Failed: {[t for t,_ in failed[:20]]}')

    # ============================================================
    # STEP 3: MERGE INTO prices.parquet
    # ============================================================
    if downloaded:
        print('\nSTEP 3: Merging into prices.parquet...')
        new_df = pd.concat(downloaded, ignore_index=True)
        new_df['date'] = pd.to_datetime(new_df['date'])

        combined = pd.concat([existing_df, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=['date', 'ticker'])
        combined = combined.sort_values(['ticker', 'date']).reset_index(drop=True)

        combined.to_parquet('data/backtest/prices.parquet', index=False)
        print(f'  Saved: {combined["ticker"].nunique()} tickers, {len(combined)} rows')
        print(f'  Date range: {combined["date"].min().date()} to {combined["date"].max().date()}')

        # Save ticker list for reference
        ticker_list = sorted(combined['ticker'].unique())
        with open('data/backtest/universe_tickers.json', 'w') as f:
            json.dump({'tickers': ticker_list,
                       'total': len(ticker_list),
                       'sp500': len(existing_tickers),
                       'new': len(downloaded),
                       'failed': [t for t,_ in failed],
                       'updated': pd.Timestamp.now().isoformat()}, f, indent=2)
        print(f'  Saved universe_tickers.json ({len(ticker_list)} tickers)')
    else:
        print('  No new data downloaded.')


# ============================================================
# STEP 4: RECOMPUTE SIGNALS
# ============================================================
print('\nSTEP 4: Recomputing signals.parquet...')
print('  Running 02_compute_signals.py...')
import subprocess
result = subprocess.run(
    [sys.executable, 'scripts/backtest/02_compute_signals.py'],
    capture_output=True, text=True, timeout=600,
    cwd=os.getcwd()
)
if result.returncode == 0:
    print('  Signals computed OK')
    print(result.stdout[-2000:] if result.stdout else '')
else:
    print(f'  ERROR: {result.returncode}')
    print(result.stderr[-2000:] if result.stderr else '')
    print(result.stdout[-1000:] if result.stdout else '')

print('\nDone. Run backtest next.')
