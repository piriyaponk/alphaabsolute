# Data Source Architecture — AlphaAbsolute Real Fund
## Definitive Source Map + Failover Plan
**Version:** 2.1 | **Updated:** 2026-05-16 | **Status:** Live in production

---

## 1. Data Type → Best Source Map

| Data Type | Primary | Secondary | Tertiary | Emergency |
|-----------|---------|-----------|----------|-----------|
| **EPS / Revenue (fundamentals)** | SEC EDGAR | Finnhub | FMP | Yahoo quoteSummary |
| **Gross Margin** | SEC EDGAR | Finnhub | FMP | — |
| **Price OHLCV (daily bars)** | Polygon.io | Tiingo | Stooq (if key) | Yahoo v8 API |
| **Current Quote (real-time)** | Polygon.io | Finnhub | Yahoo crumb | — |
| **Market Cap** | Finnhub | FMP | Yahoo quoteSummary | — |
| **Macro (yields, DXY, GDP)** | FRED | — | — | — |
| **Thai stocks** | SET MCP | — | — | — |

### Why this ranking?

**SEC EDGAR = primary for fundamentals:**
- Free, no API key, no rate limits (10 req/sec OK per SEC policy)
- Authoritative: direct from 10-Q/10-K XBRL filings
- Updated same day as filing (vs Finnhub/FMP which lag 1-3 days)
- 20+ years of history for all US public companies
- Works on Cloudflare WARP (HTTPS, proper TLS)
- User-Agent required: "AlphaAbsolute piriyaponk@gmail.com"
- CIK lookup: www.sec.gov/files/company_tickers.json (cached per session)
- Data API: data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json

**Finnhub = secondary fundamentals:**
- Have key in .env, free tier works
- Faster than EDGAR (~0.6s vs 2.5s per ticker)
- Uses XBRL data too but may label quarters differently from calendar convention
- Known issue: Apple (AAPL) uses fiscal quarter labels → "2026Q1" = Oct-Dec 2025 (not Jan-Mar)
- Use as backup or cross-check, not primary

**FMP = tertiary fundamentals:**
- Best structured format, 250 free calls/day
- If FMP_API_KEY set in .env, used automatically
- Falls to 0 if key missing

**Yahoo = emergency fundamentals:**
- quoteSummary endpoint now requires crumb token
- Blocked by Cloudflare WARP frequently (returns 401)
- Keep as last resort only

---

## 2. Known Issues and Workarounds

### Issue 1: Quarter Label Mismatch (EDGAR vs Finnhub)
- EDGAR labels by CALENDAR quarter: "2026Q1" = Jan-Mar 2026
- Finnhub labels by company FISCAL quarter: "2026Q1" = Apple's Q1 FY2026 = Oct-Dec 2025
- For Apple specifically: EDGAR "2026Q1" EPS=$2.01, Finnhub "2026Q1" EPS=$2.85
- **Both are correct** — different quarters, not data error
- **Action:** When comparing across sources, align by fiscal period end date, not by label
- **D3 Verifier** flags this automatically every Friday

### Issue 2: EDGAR Revenue Tag Switches
- Companies change their XBRL revenue tag over time as ASC 606 took effect (2019)
- Apple switched: pre-2019 used "Revenues", post-2019 uses "RevenueFromContractWithCustomerExcludingAssessedTax"
- **Fix implemented:** `_extract_quarterly()` now picks the tag with MOST RECENT data (not first available)
- Handles all known switches automatically

### Issue 3: EDGAR Cumulative YTD Reporting
- 10-Q filings report Q2 as 6-month YTD, Q3 as 9-month YTD
- Q1 standalone is always correct in EDGAR
- **Fix implemented:** De-cumulation logic: Q2_standalone = Q2_YTD - Q1; Q3_standalone = Q3_YTD - Q2_YTD
- Revenue data verified: AAPL Jan-Mar 2026 = $111.2B ✓

### Issue 4: Cloudflare WARP (SSL Interception)
- WARP intercepts TLS on the user's machine (Thai network)
- `verify=False` is set globally in `_SESSION` and `_YF_SESSION`
- EDGAR uses its own `_EDGAR_SESSION` with proper SEC User-Agent
- Yahoo uses crumb authentication to bypass cookie-based blocking

### Issue 5: Stooq Now Requires Paid API Key
- Stooq.com changed from free to gated (paid API key required)
- `_stooq_ohlcv()` silently returns None if STOOQ_API_KEY not in .env
- Yahoo v8 API is now the primary free OHLCV source

### Issue 6: newest-first indexing
- ALL outputs from `data_engine.get_fundamentals()` are newest-first
- `eps_hist[0]` = most recent quarter, `eps_hist[1]` = one quarter prior
- `eps_hist[-1]` = OLDEST quarter — never use [-1] for "latest"
- Fixed across: earnings_inflection.py, all agent scripts

---

## 3. Auto-Failover System (v2.1 — Live)

Three files work together:

### source_registry.py (`scripts/utils/source_registry.py`)
- Central definition of all 8 sources (edgar/finnhub/fmp/polygon/tiingo/yahoo/fred/set_mcp)
- Each source: cost, key_required, disruption_risk, reliability, data_types, warp_safe
- `PRIORITY_MATRIX`: 14 data types → ordered source lists
- `get_priority_list(data_type, available_keys)` → filtered list

### source_health.py (`scripts/utils/source_health.py`)
- **Persistent health scores** per source (0-100), stored in `data/source_health.json`
- `record_success(source, latency)` → score +8 per call
- `record_failure(source, error)` → score -25 per call
- **Auto-recovery:** score recovers toward DEFAULT over 24 hours
- **SKIP_THRESHOLD = 30:** sources below this are automatically excluded
- `get_ranked(data_type)` → health-weighted dynamic order
- CLI: `python scripts/utils/source_health.py` → status table
- CLI: `python scripts/utils/source_health.py reset` → reset to defaults

### data_engine.py (integration)
- Imports `source_health.get_health()` at module load
- Every source call wrapped in `try/except` with `record_success`/`record_failure`
- `get_ohlcv()`, `get_quote()`, `get_fundamentals()` all use `health.get_ranked(data_type)`
- Graceful degradation: if source_health not found, falls back to static order
- Failed sources automatically move to end of ranked list until they recover

### Default Health Scores (starting points)
| Source | Default | SKIP threshold |
|--------|---------|---------------|
| edgar | 90 | > 30 → Active |
| fred | 88 | > 30 → Active |
| polygon | 80 | > 30 → Active |
| finnhub | 75 | > 30 → Active |
| fmp | 75 | > 30 → Active |
| tiingo | 70 | > 30 → Active |
| set_mcp | 70 | > 30 → Active |
| yahoo | 50 | > 30 → Active |
| stooq | 20 | < 30 → **SKIP** (no free key) |

---

## 4. D3 Data Verifier — Weekly Sanity Check

**File:** `scripts/pre_compute/data_verifier.py`  
**Schedule:** Every Friday via `pre_market_runner.py` (weekly_only=True)  
**Output:** `data/data_verifier/latest.json`, `data/data_verifier/source_ranking.json`  
**Report:** `output/data_verify_YYMMDD.md`

### What it checks:
- EPS from EDGAR vs Finnhub vs FMP vs Yahoo (flags >15% deviation from median)
- Revenue from EDGAR vs Finnhub vs FMP vs Yahoo (flags >10% deviation)
- Price OHLCV from Polygon vs Tiingo vs Yahoo (flags >0.5% deviation)
- Ranks each source by: accuracy (deviation from median) + availability % + avg latency

---

## 5. D4 Source Monitor — Weekly Ping Sweep

**File:** `scripts/pre_compute/source_monitor.py`  
**Schedule:** Every Friday via `pre_market_runner.py` (weekly_only=True)  
**Output:** `data/source_monitor/latest.json`  
**Report:** `output/source_status_YYMMDD.md`

### What it checks:
- Pings each source with test tickers (AAPL for fundamentals, SPY for price)
- Records success/failure into health tracker
- Generates status table with health scores, availability, avg latency
- Lists missing API keys with signup links
- Does NOT penalize sources that are missing API keys (marks as NO KEY, not FAIL)

### Trigger manual run:
```bash
python scripts/pre_compute/source_monitor.py              # full ping sweep
python scripts/runners/pre_market_runner.py --step monitor  # via runner
python scripts/runners/pre_market_runner.py --step verify   # D3 verifier
```

---

## 6. Pipeline Run Order (Fridays)

On Fridays, `pre_market_runner.py` runs all 10 daily steps PLUS:
1. **verify** — D3 cross-source data accuracy check
2. **monitor** — D4 source health ping sweep

These two together ensure that every Friday:
- Data accuracy is verified (D3 flags numbers that deviate >15% across sources)
- Source health is updated (D4 pings each endpoint, records latency)
- Auto-failover rankings are refreshed (health tracker re-ranks based on actual performance)

---

## 7. Backup Plan When Primary Source Fails

### Auto-failover (silent, automatic):
```
EPS/Revenue:  EDGAR → Finnhub → FMP → Yahoo
Price OHLCV:  Polygon → Tiingo → Yahoo → (stooq skipped)
Quote:        Finnhub → Polygon → Yahoo
```

### When source is degraded (health < 30):
- Source automatically excluded from `get_ranked()` output
- Next source in list takes over
- Health recovers over 24 hours
- D4 monitor will ping it next Friday to update score

### Manual failover override:
```python
from scripts.utils.source_health import SourceHealth
h = SourceHealth()
h.reset("edgar")    # reset single source
h.reset()           # reset ALL sources to defaults
```

---

## 8. API Keys Status

| Key | Status | Location | Notes |
|-----|--------|----------|-------|
| FINNHUB_API_KEY | [OK] Active | .env | Free tier, 60 req/min |
| POLYGON_API_KEY | Missing | .env | Free signup at polygon.io — no CC needed |
| TIINGO_API_KEY | Missing | .env | Free at api.tiingo.com — 500 req/day |
| FMP_API_KEY | Missing | .env | Free at financialmodelingprep.com — 250/day |
| STOOQ_API_KEY | Missing | .env | Stooq now requires paid key — skip |
| SEC EDGAR | No key | — | Free, just User-Agent required |

**Adding keys = better reliability.** With only Finnhub key:
- Fundamentals: EDGAR (primary) + Finnhub (backup) — GOOD
- OHLCV: Yahoo only — WORKS but single source
- Quote: Finnhub only — WORKS

With Polygon key added:
- OHLCV: Polygon (fast, no WARP issues) + Tiingo + Yahoo — EXCELLENT
- Quote: Polygon (fastest) + Finnhub + Yahoo — EXCELLENT

---

## 9. Implementation Files

| File | Role |
|------|------|
| `scripts/utils/data_engine.py` | Core data layer — all sources, health-tracked failover, caching |
| `scripts/utils/source_registry.py` | Source definitions + priority matrix (static config) |
| `scripts/utils/source_health.py` | Persistent health tracker — dynamic failover rankings |
| `scripts/pre_compute/data_verifier.py` | D3 weekly cross-source sanity check |
| `scripts/pre_compute/source_monitor.py` | D4 weekly ping sweep — updates health scores |
| `scripts/runners/pre_market_runner.py` | Pipeline runner — runs verifier + monitor on Fridays |
| `.env` | API keys |
| `data/source_health.json` | Live health scores (auto-updated every API call) |

**Key functions in data_engine.py:**
- `get_ohlcv(ticker, period)` → DataFrame with OHLCV + ADTV
- `get_quote(ticker)` → float (current price)
- `get_fundamentals(ticker, quarters=8)` → dict with eps_history, revenue_history, gross_margin_history
- `get_market_cap(ticker)` → float in USD
- `_edgar_fundamentals(ticker)` → direct call to SEC EDGAR (use via get_fundamentals())

---

## 10. Verified Numbers (as of 2026-05-16)

| Ticker | EPS Latest | YoY % | Revenue | YoY % | Source | Verified |
|--------|-----------|--------|---------|--------|--------|---------|
| AAPL | $2.01 (2026Q1) | +21.8% | $111.2B | +16.6% | EDGAR 10-Q | [OK] |
| MU | $12.07 (2026Q1) | +756% | $23.9B | +196.3% | EDGAR 10-Q | [OK] |
| NVDA | $1.30 (2025Q4) | +66.7% | $57.0B | +62.5% | EDGAR 10-Q | [OK] |

All numbers match officially reported SEC 10-Q/10-K filings. Cross-checked against Finnhub.
