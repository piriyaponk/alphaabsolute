"""
19_edgar_fundamentals.py
Fetch quarterly revenue from SEC EDGAR XBRL API (free, no auth).
Compute YoY revenue growth + acceleration for the current screen.
Test revenue filters on today's 212 passing stocks.
Cache to data/backtest/edgar_revenue.json for future use.

EDGAR endpoints used:
  CIK map: https://www.sec.gov/files/company_tickers.json
  Facts:   https://data.sec.gov/api/xbrl/companyfacts/CIK{:010d}.json
"""
import sys, io, os, json, time, ssl
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd, numpy as np, requests
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

# ── Step 1: Get CIK mapping ───────────────────────────────────────────────────
cik_cache = CACHE_DIR / 'company_tickers.json'
if cik_cache.exists():
    with open(cik_cache) as f:
        raw = json.load(f)
    print(f"CIK map loaded from cache ({len(raw)} entries)")
else:
    print("Fetching SEC CIK map...")
    resp = SESSION.get('https://www.sec.gov/files/company_tickers.json', timeout=30)
    raw = resp.json()
    with open(cik_cache, 'w') as f:
        json.dump(raw, f)
    print(f"CIK map fetched: {len(raw)} entries")

ticker_to_cik = {v['ticker'].upper(): v['cik_str'] for v in raw.values()}
print(f"Ticker->CIK mapping: {len(ticker_to_cik)} tickers")

# ── Step 2: Load current screen passes ───────────────────────────────────────
sig = pd.read_parquet('data/backtest/signals.parquet')
sig['date'] = pd.to_datetime(sig['date'])
latest = sig['date'].max()
df = sig[sig['date'] == latest].copy()

exclude = {'QQQ', 'SPY', 'IWM'}
df = df[~df['ticker'].isin(exclude)]

# SYSTEM v3.0 + vol_contract=1.5 filters (BULL regime)
spy_bull = bool(df[df['ticker']=='SPY']['spy_above_ma200'].values[0]) if 'SPY' in df['ticker'].values else True
vol_thr = 0.75 if spy_bull else 0.95

passed = df[
    (df['rs_pct'] >= 80) &
    (df['vol_trend'] >= vol_thr) &
    (df['price_vs_ma200_pct'] > 0) &
    (df['adtv_63m'] >= 10) &
    (df['vol_contraction'] <= 1.5)
].copy()
print(f"\nScreen passes (as of {latest.date()}): {len(passed)} tickers")
target_tickers = sorted(passed['ticker'].tolist())

# ── Step 3: Revenue fetch function ───────────────────────────────────────────
REVENUE_CONCEPTS = [
    'Revenues',
    'RevenueFromContractWithCustomerExcludingAssessedTax',
    'RevenueFromContractWithCustomerIncludingAssessedTax',
    'SalesRevenueNet',
    'SalesRevenueGoodsNet',
    'RevenueFromRelatedParties',
]

def fetch_revenue(ticker):
    cache_file = CACHE_DIR / f'{ticker}_revenue.json'
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < 86400 * 7:
        with open(cache_file) as f:
            return ticker, json.load(f), 'cached'

    cik = ticker_to_cik.get(ticker)
    if not cik:
        return ticker, None, 'no_cik'

    cik_str = str(cik).zfill(10)
    url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_str}.json'
    try:
        resp = SESSION.get(url, timeout=20)
        if resp.status_code != 200:
            return ticker, None, f'HTTP{resp.status_code}'
        facts = resp.json()
        us_gaap = facts.get('facts', {}).get('us-gaap', {})

        # Try revenue concepts in order
        revenue_data = None
        used_concept = None
        for concept in REVENUE_CONCEPTS:
            if concept in us_gaap:
                units = us_gaap[concept].get('units', {})
                usd_data = units.get('USD', [])
                # Filter for quarterly (10-Q) and annual (10-K) filings only
                qtrs = [x for x in usd_data if x.get('form') in ('10-Q', '10-K') and x.get('fp') != 'FY']
                annual = [x for x in usd_data if x.get('form') == '10-K' and x.get('fp') == 'FY']
                if qtrs or annual:
                    revenue_data = {'quarterly': qtrs, 'annual': annual}
                    used_concept = concept
                    break

        if revenue_data is None:
            return ticker, None, 'no_revenue_concept'

        result = {'ticker': ticker, 'concept': used_concept, **revenue_data}
        with open(cache_file, 'w') as f:
            json.dump(result, f)
        time.sleep(0.12)  # SEC rate limit: max 10 req/sec
        return ticker, result, 'ok'
    except Exception as e:
        return ticker, None, str(e)[:60]


print(f"\nFetching EDGAR revenue for {len(target_tickers)} tickers (3 threads, rate-limited)...")
results = {}
errors = {}

with ThreadPoolExecutor(max_workers=3) as ex:
    futures = {ex.submit(fetch_revenue, t): t for t in target_tickers}
    for i, fut in enumerate(as_completed(futures), 1):
        ticker, data, status = fut.result()
        if data:
            results[ticker] = data
        else:
            errors[ticker] = status
        if i % 20 == 0 or i <= 3:
            print(f"  [{i}/{len(target_tickers)}] ok={len(results)} fail={len(errors)} | {ticker}:{status}")

print(f"\nFetched: {len(results)} OK, {len(errors)} failed")
if errors:
    print(f"Failed: {list(errors.items())[:10]}")

# ── Step 4: Compute revenue metrics ──────────────────────────────────────────
def parse_revenue_quarters(data):
    """Return a clean quarterly revenue Series (indexed by end date)."""
    rows = data.get('quarterly', [])
    if not rows:
        # Fall back to annual diff if no quarterly
        rows = data.get('annual', [])
    if not rows:
        return pd.Series(dtype=float)

    df_r = pd.DataFrame(rows)
    df_r['end'] = pd.to_datetime(df_r['end'])
    df_r = df_r.dropna(subset=['val'])
    df_r = df_r.sort_values('end')

    # For 10-Q filings, keep only quarterly periods (not cumulative YTD)
    # Use 'fp' field: Q1/Q2/Q3 are quarterly, FY is annual
    if 'fp' in df_r.columns:
        quarterly_mask = df_r['fp'].isin(['Q1','Q2','Q3','Q4'])
        if quarterly_mask.sum() >= 4:
            df_r = df_r[quarterly_mask]

    # Deduplicate by end date (take latest filing)
    df_r = df_r.sort_values(['end', 'filed']).drop_duplicates('end', keep='last')
    return df_r.set_index('end')['val']


def compute_rev_metrics(ticker, data):
    rev = parse_revenue_quarters(data)
    if len(rev) < 5:
        return None

    rev = rev.sort_index()

    # Latest quarter
    latest_q = rev.iloc[-1]
    latest_q_date = rev.index[-1]

    # YoY: compare same quarter 1 year ago
    one_yr_ago = latest_q_date - pd.DateOffset(months=12)
    # Find closest quarter around 1 year ago (within 45 days)
    candidates = rev.index[abs(rev.index - one_yr_ago) < pd.Timedelta(days=45)]
    if len(candidates) == 0:
        return None
    yoy_q = rev.loc[candidates[-1]]
    yoy_growth = (latest_q - yoy_q) / abs(yoy_q) * 100 if yoy_q != 0 else np.nan

    # Prior quarter YoY (for acceleration)
    prior_q = rev.iloc[-2]
    prior_q_date = rev.index[-2]
    two_yr_ago = prior_q_date - pd.DateOffset(months=12)
    candidates2 = rev.index[abs(rev.index - two_yr_ago) < pd.Timedelta(days=45)]
    if len(candidates2) > 0:
        prior_yoy_q = rev.loc[candidates2[-1]]
        prior_yoy_growth = (prior_q - prior_yoy_q) / abs(prior_yoy_q) * 100 if prior_yoy_q != 0 else np.nan
        accel = yoy_growth - prior_yoy_growth
    else:
        prior_yoy_growth = np.nan
        accel = np.nan

    # TTM revenue
    ttm = rev.iloc[-4:].sum() if len(rev) >= 4 else np.nan

    return {
        'ticker': ticker,
        'rev_latest_q': latest_q / 1e9,  # $B
        'rev_ttm': ttm / 1e9,
        'rev_yoy_pct': yoy_growth,
        'rev_prior_yoy_pct': prior_yoy_growth,
        'rev_accel': accel,
        'latest_q_date': str(latest_q_date.date()),
        'concept': data.get('concept', '?'),
    }


metrics = []
for ticker, data in results.items():
    m = compute_rev_metrics(ticker, data)
    if m:
        metrics.append(m)

metrics_df = pd.DataFrame(metrics)
print(f"\nRevenue metrics computed: {len(metrics_df)} tickers")

# ── Step 5: Merge with screen & show results ─────────────────────────────────
screen = passed[['ticker','rs_pct','vol_trend','adtv_63m','vol_contraction','close']].copy()
merged = screen.merge(metrics_df, on='ticker', how='left')

print(f"\n{'='*80}")
print(f"SCREEN + FUNDAMENTALS — {latest.date()}")
print(f"{'='*80}")

# Sort by RS
merged_sorted = merged.sort_values('rs_pct', ascending=False).reset_index(drop=True)

print(f"\n{'#':>2} {'Ticker':<7} {'RS%':>5} {'RevYoY':>8} {'Accel':>7} {'TTM($B)':>8} {'LatestQ':>10}")
print("-"*55)
for i, row in merged_sorted.head(30).iterrows():
    yoy = f"{row['rev_yoy_pct']:+.0f}%" if pd.notna(row['rev_yoy_pct']) else "  N/A"
    acc = f"{row['rev_accel']:+.0f}%" if pd.notna(row['rev_accel']) else "  N/A"
    ttm = f"${row['rev_ttm']:.1f}B" if pd.notna(row['rev_ttm']) else "  N/A"
    qd  = str(row['latest_q_date'])[:7] if pd.notna(row.get('latest_q_date')) else "  N/A"
    print(f"{i+1:>2} {row['ticker']:<7} {row['rs_pct']:>5.1f} {yoy:>8} {acc:>7} {ttm:>8} {qd:>10}")

# ── Step 6: Test filter thresholds ───────────────────────────────────────────
print(f"\n{'='*60}")
print("FILTER THRESHOLD TEST (on today's 212 passing stocks)")
print(f"{'='*60}")

total = len(merged)
has_rev = merged['rev_yoy_pct'].notna().sum()
print(f"\nHave revenue data: {has_rev}/{total} ({has_rev/total*100:.0f}%)")

thresholds = [0, 10, 15, 20, 25, 30]
for thr in thresholds:
    passes = (merged['rev_yoy_pct'] >= thr) | merged['rev_yoy_pct'].isna()  # NA = pass (no penalize missing)
    strict = merged['rev_yoy_pct'] >= thr  # strict: NA fails
    print(f"\n  RevYoY >= {thr:2d}%: {strict.sum():3d} pass (strict) | {passes.sum():3d} pass (NA=pass)")

# Acceleration filter
accel_has = merged['rev_accel'].notna().sum()
accel_pos = (merged['rev_accel'] > 0).sum()
accel_5 = (merged['rev_accel'] > 5).sum()
print(f"\n  Revenue acceleration > 0%:  {accel_pos}/{accel_has} (of those with data)")
print(f"  Revenue acceleration > 5%:  {accel_5}/{accel_has}")

# Combined: YoY>20% AND accel>0
combo = ((merged['rev_yoy_pct'] >= 20) & (merged['rev_accel'] > 0)).sum()
print(f"\n  RevYoY>=20% AND Accel>0: {combo} stocks")

# Show top candidates with strong fundamentals
print(f"\n--- Top candidates: RevYoY>=20% (sorted by accel) ---")
strong = merged[merged['rev_yoy_pct'] >= 20].sort_values('rev_accel', ascending=False)
print(f"{'Ticker':<8} {'RS%':>5} {'RevYoY':>8} {'Accel':>8} {'TTM($B)':>9}")
for _, row in strong.iterrows():
    yoy = f"{row['rev_yoy_pct']:+.0f}%"
    acc = f"{row['rev_accel']:+.0f}%" if pd.notna(row['rev_accel']) else "  N/A"
    ttm = f"${row['rev_ttm']:.1f}B" if pd.notna(row['rev_ttm']) else "  N/A"
    print(f"{row['ticker']:<8} {row['rs_pct']:>5.1f} {yoy:>8} {acc:>8} {ttm:>9}")

# Save merged for further analysis
merged.to_csv('data/backtest/screen_with_fundamentals.csv', index=False)
print(f"\nSaved: data/backtest/screen_with_fundamentals.csv")
