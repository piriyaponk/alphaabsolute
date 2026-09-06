"""
AlphaAbsolute -- Data Source Registry
======================================
The single source of truth for ALL data sources in the system.

Every source is documented with:
  - What data types it provides
  - Cost / key requirements
  - Disruption risk (how likely free tier changes or goes down)
  - Reliability class (A = authoritative, B = good, C = fragile)
  - Priority rank per data type (1 = try first)

This file is READ by:
  - data_engine.py         (to get the ordered source list per data type)
  - source_health.py       (to know which sources exist and their defaults)
  - source_monitor.py      (weekly health sweep)
  - data_verifier.py       (cross-check agent)

HOW FAILOVER WORKS:
  1. source_health.py reads data/source_health.json at startup
  2. data_engine.get_ohlcv() / get_fundamentals() etc. call
     source_health.get_ranked(data_type) -> ordered list of sources
  3. Each call records success (latency) or failure (error) in health state
  4. Health score = weighted avg of last 20 calls: success(+10) / failure(-25)
  5. If health < 40 -> source is skipped until recovery (auto-recovery 24h)
  6. D3 verifier runs weekly cross-check and updates health independently
"""

from __future__ import annotations
from typing import Any


# =============================================================================
# SOURCE DEFINITIONS
# =============================================================================

SOURCES: dict[str, dict[str, Any]] = {

    # ── SEC EDGAR ─────────────────────────────────────────────────────────────
    "edgar": {
        "name":             "SEC EDGAR XBRL",
        "url":              "https://data.sec.gov/api/xbrl/companyfacts/",
        "cost":             "FREE",
        "key_required":     False,
        "key_env":          None,
        "rate_limit":       "10 req/sec",
        "disruption_risk":  "VERY_LOW",    # US Government API
        "reliability":      "A+",
        "data_types": [
            "eps",          # EarningsPerShareDiluted / EarningsPerShareBasic
            "revenue",      # Revenues / RevenueFromContractWithCustomer...
            "gross_margin", # GrossProfit
            "shares_out",   # CommonStockSharesOutstanding
            "net_income",   # NetIncomeLoss
        ],
        "notes": (
            "Authoritative 10-Q/10-K filings. Same-day data after filing. "
            "US-listed companies only. Requires User-Agent: 'AlphaAbsolute piriyaponk@gmail.com'. "
            "Quarter labels = calendar quarters (not fiscal). "
            "Possible delay if company files late (SMCI 2024 example). "
            "CIK lookup: www.sec.gov/files/company_tickers.json (cached per session)."
        ),
        "warp_safe":        True,
        "us_only":          True,
    },

    # ── Finnhub ───────────────────────────────────────────────────────────────
    "finnhub": {
        "name":             "Finnhub",
        "url":              "https://finnhub.io/api/v1/",
        "cost":             "FREE (60 req/min)",
        "key_required":     True,
        "key_env":          "FINNHUB_API_KEY",
        "rate_limit":       "60/min free tier",
        "disruption_risk":  "LOW",         # Stable company, free tier consistent
        "reliability":      "B+",
        "data_types": [
            "eps",          # stock/metric XBRL EPS
            "revenue",      # stock/metric XBRL revenue
            "gross_margin", # derived from XBRL gross profit
            "quote",        # quote endpoint (real-time)
            "market_cap",   # stock/metric marketCapitalization
            "earnings_date", # calendar/earnings
        ],
        "notes": (
            "Uses XBRL from SEC filings. Fast (~0.6s). "
            "Quarter labels = FISCAL quarters (Apple Q1 = Oct-Dec, not Jan-Mar). "
            "EPS may differ from EDGAR by ~5% for split-adjusted edge cases. "
            "Rate limit: 60/min free -- enough for 21-ticker daily run."
        ),
        "warp_safe":        True,
        "us_only":          False,
    },

    # ── FMP (Financial Modeling Prep) ─────────────────────────────────────────
    "fmp": {
        "name":             "Financial Modeling Prep",
        "url":              "https://financialmodelingprep.com/api/v3/",
        "cost":             "FREE (250 req/day) / $15/mo unlimited",
        "key_required":     True,
        "key_env":          "FMP_API_KEY",
        "rate_limit":       "250/day free",
        "disruption_risk":  "MEDIUM",      # Private, free tier has changed before
        "reliability":      "B+",
        "data_types": [
            "eps",              # income-statement quarterly
            "revenue",          # income-statement quarterly
            "gross_margin",     # grossProfit / revenue
            "market_cap",       # profile endpoint
            "float",            # profile floatShares
            "shares_out",       # profile sharesOutstanding
            "earnings_date",    # earnings calendar
            "analyst_estimates", # analyst-estimates endpoint
            "price_target",     # analyst price targets
        ],
        "notes": (
            "Best for float, analyst estimates, earnings calendar. "
            "250 free calls/day -- watchlist of 30 tickers uses ~120/day. "
            "Income statement is clearly labeled fiscal quarters. "
            "Rate limit resets midnight ET."
        ),
        "warp_safe":        True,
        "us_only":          False,
    },

    # ── Polygon.io ────────────────────────────────────────────────────────────
    "polygon": {
        "name":             "Polygon.io",
        "url":              "https://api.polygon.io/v2/",
        "cost":             "FREE (5 req/min EOD) / $29+/mo real-time",
        "key_required":     True,
        "key_env":          "POLYGON_API_KEY",
        "rate_limit":       "5 req/min free tier (15min delayed data)",
        "disruption_risk":  "LOW",
        "reliability":      "A",
        "data_types": [
            "ohlcv",        # /aggs/ticker (bars)
            "quote",        # /last/trade (last trade price)
            "market_cap",   # /v3/reference/tickers details
            "float",        # /v3/reference/tickers details
            "shares_out",   # /v3/reference/tickers details
            "earnings_date", # /v3/reference/tickers details
            "dividends",
            "splits",
        ],
        "notes": (
            "Best-in-class for price data. No SSL issues, no WARP problems. "
            "Free tier = 5 req/min with 15min delay. "
            "Upgrade to $29/mo for real-time + unlimited calls. "
            "Sign up: polygon.io -- no credit card for free tier."
        ),
        "warp_safe":        True,
        "us_only":          False,
    },

    # ── Tiingo ────────────────────────────────────────────────────────────────
    "tiingo": {
        "name":             "Tiingo",
        "url":              "https://api.tiingo.com/tiingo/daily/",
        "cost":             "FREE (500 req/day)",
        "key_required":     True,
        "key_env":          "TIINGO_API_KEY",
        "rate_limit":       "500/day free",
        "disruption_risk":  "LOW",
        "reliability":      "B+",
        "data_types": [
            "ohlcv",        # /tiingo/daily/{ticker}/prices
        ],
        "notes": (
            "Excellent OHLCV backup. Clean JSON, adjusted for splits/dividends. "
            "500 free calls/day = 30 tickers x 16 days = fine for weekly refresh. "
            "Sign up: api.tiingo.com -- free."
        ),
        "warp_safe":        True,
        "us_only":          False,
    },

    # ── Yahoo Finance (unofficial) ────────────────────────────────────────────
    "yahoo": {
        "name":             "Yahoo Finance (unofficial)",
        "url":              "https://query2.finance.yahoo.com/",
        "cost":             "FREE (unofficial, no SLA)",
        "key_required":     False,
        "key_env":          None,
        "rate_limit":       "~2000/day (unofficial, not guaranteed)",
        "disruption_risk":  "HIGH",        # Unofficial, changes without notice
        "reliability":      "C+",
        "data_types": [
            "ohlcv",        # v8/finance/chart
            "quote",        # /v8/finance/chart (last close)
            "market_cap",   # quoteSummary summaryDetail
        ],
        "notes": (
            "Works on WARP with verify=False + crumb token. "
            "Crumb expires daily -- re-fetch on 401. "
            "FRAGILE: Yahoo has changed API endpoints 3x since 2022. "
            "quoteSummary (fundamentals) returns 401 frequently on WARP. "
            "Price OHLCV via v8 chart API is most stable path. "
            "Use only as last-resort for what other sources can't provide."
        ),
        "warp_safe":        True,    # With verify=False + crumb
        "us_only":          False,
    },

    # ── FRED (Federal Reserve Economic Data) ──────────────────────────────────
    "fred": {
        "name":             "FRED (Federal Reserve)",
        "url":              "https://api.stlouisfed.org/fred/series/observations",
        "cost":             "FREE",
        "key_required":     True,
        "key_env":          "FRED_API_KEY",
        "rate_limit":       "120 req/min",
        "disruption_risk":  "VERY_LOW",   # US Federal Reserve
        "reliability":      "A+",
        "data_types": [
            "macro_yields",     # DGS10 (10Y), DGS2 (2Y), DGS3MO (3M)
            "dxy",              # DTWEXBGS (Trade-weighted USD)
            "fed_funds",        # FEDFUNDS
            "inflation",        # CPIAUCSL, PCE
            "unemployment",     # UNRATE
            "ism",              # ISM manufacturing PMI proxy
            "vix_alt",          # VIXCLS (VIX via FRED)
        ],
        "notes": (
            "Gold standard for macro data. Free API key at fred.stlouisfed.org. "
            "Current data for most series has 1-day lag. "
            "Use for: yield curve (2Y-10Y spread), DXY trend, macro regime inputs."
        ),
        "warp_safe":        True,
        "us_only":          False,
    },

    # ── SET MCP (Thai stocks) ─────────────────────────────────────────────────
    "set_mcp": {
        "name":             "SET MCP (Thai Exchange)",
        "url":              "uvx set-mcp",
        "cost":             "FREE (configured)",
        "key_required":     False,
        "key_env":          None,
        "rate_limit":       "N/A (local MCP)",
        "disruption_risk":  "LOW",
        "reliability":      "A",
        "data_types": [
            "thai_ohlcv",       # SET/MAI price bars
            "thai_fundamentals", # Income statement, balance sheet, CF
            "thai_dividends",
        ],
        "notes": "Thai stocks only. Local MCP process. Requires uvx set-mcp running.",
        "warp_safe":        True,
        "us_only":          False,
    },

    # ── Stooq ─────────────────────────────────────────────────────────────────
    "stooq": {
        "name":             "Stooq",
        "url":              "https://stooq.com/q/d/l/",
        "cost":             "PAID (key required as of 2025)",
        "key_required":     True,
        "key_env":          "STOOQ_API_KEY",
        "rate_limit":       "Unknown",
        "disruption_risk":  "VERY_HIGH",  # Changed pricing model without notice
        "reliability":      "D",
        "data_types": ["ohlcv"],
        "notes": (
            "Previously free CSV download. Changed to paid API without announcement. "
            "DO NOT rely on this. Skip silently if STOOQ_API_KEY missing."
        ),
        "warp_safe":        True,
        "us_only":          False,
    },
}


# =============================================================================
# PRIORITY MATRIX — DATA TYPE -> ORDERED SOURCE LIST
# =============================================================================
# Each entry: [source_id, ...] in priority order (try #1 first, #2 if fails, etc.)
# Rules:
#   - Free sources without keys go last (Yahoo) or are conditional (EDGAR first since no key)
#   - Keys present in .env = automatically promoted
#   - Disruption risk HIGH sources are never #1

PRIORITY_MATRIX: dict[str, list[str]] = {

    # ── Fundamentals ──────────────────────────────────────────────────────────
    "eps": [
        "edgar",     # #1: Free, authoritative, no key, calendar quarters
        "fmp",       # #2: Good if key exists, fiscal quarter labeled clearly
        "finnhub",   # #3: Have key, fast, fiscal quarter labels
        "yahoo",     # #4: Last resort (frequently blocked by WARP)
    ],
    "revenue": [
        "edgar",     # #1: Same as EPS -- direct from 10-Q
        "fmp",       # #2: Clean structure
        "finnhub",   # #3: XBRL-derived, fast
        "yahoo",     # #4: Last resort
    ],
    "gross_margin": [
        "edgar",     # #1: GrossProfit tag directly from XBRL
        "fmp",       # #2: grossProfit field in income statement
        "finnhub",   # #3: XBRL derived
        "yahoo",     # #4: Last resort
    ],
    "market_cap": [
        "finnhub",   # #1: Have key, real-time market cap metric
        "fmp",       # #2: Profile endpoint, good
        "yahoo",     # #3: summaryDetail (sometimes blocked)
    ],
    "float": [
        "fmp",       # #1: floatShares in profile endpoint
        "polygon",   # #2: reference/tickers details
        "edgar",     # #3: CommonStockSharesOutstanding (approximate)
    ],
    "shares_out": [
        "edgar",     # #1: CommonStockSharesOutstanding from 10-Q
        "fmp",       # #2: sharesOutstanding in profile
        "polygon",   # #3: reference/tickers details
    ],
    "earnings_date": [
        "fmp",       # #1: earnings calendar (if key)
        "finnhub",   # #2: calendar/earnings
        "polygon",   # #3: reference/tickers details
    ],
    "analyst_estimates": [
        "fmp",       # #1: analyst-estimates endpoint (if key)
        "finnhub",   # #2: recommendation-trends
        # edgar/yahoo: no analyst estimates
    ],

    # ── Price Data ────────────────────────────────────────────────────────────
    "ohlcv": [
        "polygon",   # #1: Best data quality, no SSL issues (if key)
        "tiingo",    # #2: Clean, adjusted, 500/day free (if key)
        "yahoo",     # #3: v8 chart API works on WARP with crumb
        "stooq",     # #4: Only if STOOQ_API_KEY present (DEPRECATED)
    ],
    "quote": [
        "finnhub",   # #1: Have key, real-time quote
        "polygon",   # #2: last trade (if key)
        "yahoo",     # #3: crumb-based quote
    ],

    # ── Macro ─────────────────────────────────────────────────────────────────
    "macro_yields": [
        "fred",      # #1: Authoritative. FRED_API_KEY (free signup)
        "yahoo",     # #2: ^TNX (10Y), ^IRX (3M) via OHLCV
    ],
    "vix": [
        "yahoo",     # #1: ^VIX ticker via OHLCV (most reliable path)
        "fred",      # #2: VIXCLS series (1-day lag)
    ],
    "dxy": [
        "yahoo",     # #1: DX-Y.NYB ticker
        "fred",      # #2: DTWEXBGS series (1-day lag)
    ],

    # ── Thai stocks ───────────────────────────────────────────────────────────
    "thai_ohlcv": [
        "set_mcp",   # #1: Only source for Thai SET/MAI data
    ],
    "thai_fundamentals": [
        "set_mcp",   # #1: Only source for Thai company financials
    ],
}


# =============================================================================
# DATA TYPE METADATA
# =============================================================================

DATA_TYPE_META: dict[str, dict] = {
    "eps": {
        "label":        "EPS Quarterly",
        "unit":         "USD/share",
        "frequency":    "quarterly (8 quarters)",
        "freshness":    "same-day after 10-Q filing",
        "critical":     True,    # Required for CANSLIM C-score, I2 inflection
    },
    "revenue": {
        "label":        "Revenue Quarterly",
        "unit":         "USD",
        "frequency":    "quarterly (8 quarters)",
        "freshness":    "same-day after 10-Q filing",
        "critical":     True,
    },
    "gross_margin": {
        "label":        "Gross Margin %",
        "unit":         "percent",
        "frequency":    "quarterly",
        "freshness":    "same-day after 10-Q filing",
        "critical":     False,
    },
    "ohlcv": {
        "label":        "Daily Price Bars (OHLCV + ADTV)",
        "unit":         "USD / shares",
        "frequency":    "daily, 1y history needed",
        "freshness":    "T+0 (EOD after market close)",
        "critical":     True,    # Required by ALL scripts
    },
    "quote": {
        "label":        "Current Quote (real-time or 15min delay)",
        "unit":         "USD",
        "frequency":    "intraday",
        "freshness":    "real-time or 15min delayed",
        "critical":     True,    # Portfolio NAV calculation
    },
    "market_cap": {
        "label":        "Market Capitalization",
        "unit":         "USD",
        "frequency":    "daily snapshot",
        "freshness":    "T+0",
        "critical":     False,
    },
    "float": {
        "label":        "Float Shares",
        "unit":         "shares",
        "frequency":    "static (update quarterly)",
        "freshness":    "T+0 (updated after 10-Q)",
        "critical":     False,
    },
    "earnings_date": {
        "label":        "Next Earnings Date",
        "unit":         "date",
        "frequency":    "per quarter",
        "freshness":    "updated when company announces",
        "critical":     True,    # Risk rule: no entry within 5 days of earnings
    },
    "macro_yields": {
        "label":        "Bond Yields (10Y, 2Y, 3M)",
        "unit":         "percent",
        "frequency":    "daily",
        "freshness":    "T+1 (FRED lag)",
        "critical":     False,
    },
    "vix": {
        "label":        "VIX Volatility Index",
        "unit":         "index",
        "frequency":    "daily",
        "freshness":    "T+0 EOD",
        "critical":     True,    # M0 market regime core input
    },
    "dxy": {
        "label":        "US Dollar Index",
        "unit":         "index",
        "frequency":    "daily",
        "freshness":    "T+0 EOD",
        "critical":     False,
    },
    "analyst_estimates": {
        "label":        "Analyst EPS/Rev Estimates",
        "unit":         "USD / percent",
        "frequency":    "updated after each earnings",
        "freshness":    "T+1 to T+3 after analyst updates",
        "critical":     False,   # CANSLIM A-score uses NRGC proxy if missing
    },
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_priority_list(data_type: str, available_keys: dict[str, bool] = None) -> list[str]:
    """
    Return ordered source list for a data type, filtered by key availability.

    available_keys = {"fmp": True, "polygon": False, "finnhub": True, ...}
    Sources that require a key but the key is missing are moved to end of list.
    """
    base_order = PRIORITY_MATRIX.get(data_type, [])
    if not available_keys:
        return base_order

    # Sources with keys available first, then keyless required
    with_key    = [s for s in base_order
                   if not SOURCES[s]["key_required"] or available_keys.get(s, False)]
    without_key = [s for s in base_order
                   if SOURCES[s]["key_required"] and not available_keys.get(s, False)]
    return with_key + without_key


def get_source_info(source_id: str) -> dict:
    """Return full metadata for a source."""
    return SOURCES.get(source_id, {})


def print_priority_table():
    """Print the full priority matrix for human review."""
    print("\n" + "=" * 70)
    print("  AlphaAbsolute -- Data Source Priority Matrix")
    print("=" * 70)
    for dtype, sources in PRIORITY_MATRIX.items():
        meta = DATA_TYPE_META.get(dtype, {})
        critical = "[!] CRITICAL" if meta.get("critical") else "[.]"
        print(f"\n  {dtype:20s} {critical}")
        for i, src in enumerate(sources, 1):
            s = SOURCES[src]
            key_status = f"(key: {s['key_env']})" if s["key_required"] else "(no key needed)"
            print(f"    #{i} {src:12s} | {s['cost']:35s} | risk={s['disruption_risk']:10s} | {key_status}")
    print("\n" + "=" * 70)


if __name__ == "__main__":
    print_priority_table()
