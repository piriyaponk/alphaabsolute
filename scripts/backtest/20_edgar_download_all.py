"""
20_edgar_download_all.py
Download EDGAR companyfacts for all tickers that ever passed rs_pct >= 75
in signals.parquet. Saves to data/backtest/edgar_cache/{TICKER}_revenue.json.
Already-cached files (< 7 days old) are skipped.
SEC rate limit: max 10 req/sec — we use 3 threads + 0.12s delay.
"""
import sys, io, os, json, time, ssl
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))

import pandas as pd
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ssl._create_default_https_context = ssl._create_unverified_context
CACHE_DIR = Path('data/backtest/edgar_cache')
CACHE_DIR.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.verify = False
SESSION.headers.update({
    'User-Agent': 'AlphaAbsolute Research piriyaponk@gmail.com',
    'Accept-Encoding': 'gzip, deflate',
    'Accept': 'application/json',
})

# ── CIK map ───────────────────────────────────────────────────────────────────
cik_cache = CACHE_DIR / 'company_tickers.json'
with open(cik_cache) as f:
    raw = json.load(f)
ticker_to_cik = {v['ticker'].upper(): v['cik_str'] for v in raw.values()}

# ── Find all candidate tickers from signals ───────────────────────────────────
print("Loading signals.parquet...")
sig = pd.read_parquet('data/backtest/signals.parquet')
# Candidate = ever hit rs_pct >= 75 (realistic pool for screen)
candidates = sig[sig['rs_pct'] >= 75]['ticker'].unique()
exclude = {'QQQ', 'SPY', 'IWM'}
candidates = [t for t in candidates if t not in exclude]
print(f"Candidate tickers (ever rs>=75): {len(candidates)}")

# Filter: only those with CIK (US-listed)
candidates = [t for t in candidates if t in ticker_to_cik]
print(f"With CIK mapping (US-listed): {len(candidates)}")

# Skip already cached
fresh_cutoff = time.time() - 86400 * 7
to_download = []
for t in candidates:
    f = CACHE_DIR / f'{t}_revenue.json'
    if f.exists() and f.stat().st_mtime > fresh_cutoff:
        continue
    to_download.append(t)
print(f"Already cached: {len(candidates) - len(to_download)} | To download: {len(to_download)}")

REVENUE_CONCEPTS = [
    'Revenues',
    'RevenueFromContractWithCustomerExcludingAssessedTax',
    'RevenueFromContractWithCustomerIncludingAssessedTax',
    'SalesRevenueNet',
    'SalesRevenueGoodsNet',
]

def fetch_one(ticker):
    cik = ticker_to_cik.get(ticker)
    if not cik:
        return ticker, None, 'no_cik'
    cik_str = str(cik).zfill(10)
    url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_str}.json'
    try:
        resp = SESSION.get(url, timeout=25)
        if resp.status_code == 404:
            # Save empty marker so we don't retry
            cache_file = CACHE_DIR / f'{ticker}_revenue.json'
            with open(cache_file, 'w') as f:
                json.dump({'ticker': ticker, 'concept': None, 'quarterly': [], 'annual': []}, f)
            return ticker, None, 'HTTP404'
        if resp.status_code != 200:
            return ticker, None, f'HTTP{resp.status_code}'
        facts = resp.json()
        us_gaap = facts.get('facts', {}).get('us-gaap', {})
        revenue_data = None
        used_concept = None
        for concept in REVENUE_CONCEPTS:
            if concept in us_gaap:
                units = us_gaap[concept].get('units', {})
                usd_data = units.get('USD', [])
                # Keep all forms that contain revenue (10-Q quarterly + 10-K annual)
                qtrs   = [x for x in usd_data if x.get('form') in ('10-Q',) and x.get('fp') in ('Q1','Q2','Q3','Q4')]
                annual = [x for x in usd_data if x.get('form') == '10-K']
                if qtrs or annual:
                    revenue_data = {'quarterly': qtrs, 'annual': annual}
                    used_concept = concept
                    break
        if revenue_data is None:
            # Save empty marker
            cache_file = CACHE_DIR / f'{ticker}_revenue.json'
            with open(cache_file, 'w') as f:
                json.dump({'ticker': ticker, 'concept': None, 'quarterly': [], 'annual': []}, f)
            return ticker, None, 'no_concept'
        result = {'ticker': ticker, 'concept': used_concept, **revenue_data}
        cache_file = CACHE_DIR / f'{ticker}_revenue.json'
        with open(cache_file, 'w') as f:
            json.dump(result, f)
        time.sleep(0.12)
        return ticker, result, 'ok'
    except Exception as e:
        return ticker, None, str(e)[:60]


if not to_download:
    print("All tickers already cached. Done.")
else:
    print(f"\nDownloading {len(to_download)} tickers (3 threads)...")
    ok_count = 0
    fail_count = 0
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(fetch_one, t): t for t in to_download}
        for i, fut in enumerate(as_completed(futures), 1):
            ticker, data, status = fut.result()
            if data:
                ok_count += 1
            else:
                fail_count += 1
            if i % 50 == 0 or i <= 3 or i == len(to_download):
                print(f"  [{i}/{len(to_download)}] ok={ok_count} fail={fail_count} | {ticker}:{status}")

    print(f"\nDone: {ok_count} OK, {fail_count} failed/no-data")

print(f"\nCache size: {len(list(CACHE_DIR.glob('*_revenue.json')))} files")
print("Next: run 21_build_revenue_signals.py")
