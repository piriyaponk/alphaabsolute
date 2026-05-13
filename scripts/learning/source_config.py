"""
AlphaAbsolute — Research Source Stack
Tier 1: Primary alpha sources (what hedge funds actually read)
Tier 2: Macro & strategy
Tier 3: Market data feeds

Token budget per source: scrape FREE, distill CHEAP (Haiku), synthesize WEEKLY (Sonnet)
"""

# ─── TIER 1: ALPHA SOURCES ────────────────────────────────────────────────────
# Primary edge — what institutional PMs read BEFORE consensus forms

TIER1_SOURCES = [

    # ── Semiconductor / AI Infrastructure (best in class) ──────────────────
    {
        "id": "semianalysis",
        "name": "SemiAnalysis (Dylan Patel)",
        "url": "https://www.semianalysis.com/feed",
        "type": "rss",
        "themes": ["AI", "Memory/HBM", "Photonics", "AI Infrastructure"],
        "freq": "weekly",
        "priority": 1,
        "why": "Best primary source on AI chip supply chain — moves stock prices",
    },
    {
        "id": "stratechery",
        "name": "Stratechery (Ben Thompson)",
        "url": "https://stratechery.com/feed/",
        "type": "rss",
        "themes": ["AI", "Tech Strategy", "Platform"],
        "freq": "weekly",
        "priority": 1,
        "why": "Platform strategy + AI economics — ahead of consensus",
    },
    {
        "id": "diff",
        "name": "The Diff (Byrne Hobart)",
        "url": "https://www.thediff.co/feed",
        "type": "rss",
        "themes": ["Finance", "Tech", "Macro"],
        "freq": "weekly",
        "priority": 1,
        "why": "Unique angle on finance/tech intersection — HF favorite",
    },

    # ── Institutional Research (Free Tier) ──────────────────────────────────
    {
        "id": "gs_insights",
        "name": "Goldman Sachs Insights",
        "url": "https://www.goldmansachs.com/insights/rss.xml",
        "type": "rss",
        "themes": ["Macro", "Markets", "Themes"],
        "freq": "weekly",
        "priority": 1,
        "why": "GS macro views shape consensus — read it first",
    },
    {
        "id": "jpm_insights",
        "name": "JPMorgan Asset Management",
        "url": "https://am.jpmorgan.com/us/en/asset-management/adv/insights/rss/",
        "type": "rss",
        "themes": ["Macro", "Asset Allocation", "Markets"],
        "freq": "weekly",
        "priority": 1,
        "why": "JPM allocates trillions — their views move markets",
    },
    {
        "id": "blackrock_insights",
        "name": "BlackRock Investment Institute",
        "url": "https://www.blackrock.com/us/individual/insights/blackrock-investment-institute/rss",
        "type": "rss",
        "themes": ["Macro", "Asset Allocation", "Regime"],
        "freq": "weekly",
        "priority": 1,
        "why": "Largest asset manager — regime views essential",
    },
    {
        "id": "oaktree_marks",
        "name": "Howard Marks Memos (Oaktree)",
        "url": "https://www.oaktreecapital.com/insights/howard-marks-memos",
        "type": "web_scrape",
        "themes": ["Market Cycle", "Risk", "Value"],
        "freq": "monthly",
        "priority": 1,
        "why": "Legendary cycle awareness — Buffett reads every memo",
    },
    {
        "id": "aqr_research",
        "name": "AQR Capital Research",
        "url": "https://www.aqr.com/Insights/Research",
        "type": "web_scrape",
        "themes": ["Factor Investing", "Momentum", "Value"],
        "freq": "monthly",
        "priority": 1,
        "why": "Academic-grade quantitative research — momentum/factor papers",
    },

    # ── Smart Money Tracking ────────────────────────────────────────────────
    {
        "id": "openinsider",
        "name": "OpenInsider — Cluster Buys",
        "url": "http://openinsider.com/screener?s=&o=&pl=&ph=&ll=&lh=&fd=14&fdr=&td=0&tdr=&fdlyl=&fdlyh=&daysago=&xp=1&xs=1&vl=1000000&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh=&v2l=&v2h=&oc2l=&oc2h=&sortcol=0&cnt=100&Action=1",
        "type": "web_scrape",
        "themes": ["Insider", "Smart Money"],
        "freq": "daily",
        "priority": 1,
        "why": "Cluster insider buys (3+ insiders >$1M) = highest quality signal",
    },
    {
        "id": "capitoltrades",
        "name": "Capitol Trades — Congress",
        "url": "https://www.capitoltrades.com/trades?pageSize=50",
        "type": "web_scrape",
        "themes": ["Policy", "Defense", "Healthcare", "Smart Money"],
        "freq": "daily",
        "priority": 1,
        "why": "Congress trades outperform market by 12%/yr — information edge",
    },
    {
        "id": "dataroma",
        "name": "Dataroma — Super Investor 13F",
        "url": "https://www.dataroma.com/m/feeds/activity.php",
        "type": "web_scrape",
        "themes": ["Institutional", "Smart Money", "Portfolio"],
        "freq": "quarterly",
        "priority": 1,
        "why": "Tracks Ackman, Tepper, Einhorn, Burry — what smart money buys",
    },
    {
        "id": "sec_13f",
        "name": "SEC EDGAR 13F Filings",
        "url": "https://efts.sec.gov/LATEST/search-index?q=%22Form+13F%22&dateRange=custom&startdt={start}&enddt={end}&forms=13F-HR",
        "type": "sec_api",
        "themes": ["Institutional Holdings"],
        "freq": "quarterly",
        "priority": 1,
        "why": "Primary source — every $100M+ fund must file. Real positions.",
    },
    {
        "id": "unusual_whales",
        "name": "Unusual Whales — Options Flow",
        "url": "https://unusualwhales.com/flow",
        "type": "web_scrape",
        "themes": ["Options Flow", "Smart Money"],
        "freq": "daily",
        "priority": 2,
        "why": "Unusual options = informed buying before catalyst",
    },
]

# ─── TIER 2: MACRO & STRATEGY ─────────────────────────────────────────────────

TIER2_SOURCES = [
    {
        "id": "fred_macro",
        "name": "FRED Economic Data",
        "url": "https://api.stlouisfed.org/fred/series/observations",
        "type": "fred_api",
        "series": {
            "FEDFUNDS": "Fed Funds Rate",
            "T10Y2Y":   "10Y-2Y Yield Curve",
            "UMCSENT":  "Consumer Sentiment",
            "INDPRO":   "Industrial Production",
            "DCOILWTICO": "WTI Oil Price",
            "DTWEXBGS": "USD Index",
        },
        "freq": "weekly",
        "priority": 1,
        "why": "Primary macro data — free, official, regime-setting",
    },
    {
        "id": "seeking_alpha_rss",
        "name": "Seeking Alpha — Market News",
        "url": "https://seekingalpha.com/feed.xml",
        "type": "rss",
        "themes": ["Earnings", "Markets", "Analysis"],
        "freq": "daily",
        "priority": 2,
        "why": "Broad coverage — filter for watchlist tickers only",
    },
    {
        "id": "ibd_news",
        "name": "Investor's Business Daily",
        "url": "https://www.investors.com/feed/",
        "type": "rss",
        "themes": ["Leadership Stocks", "IBD50", "Market Pulse"],
        "freq": "daily",
        "priority": 1,
        "why": "IBD Market Pulse + IBD50 = Minervini/SEPA-aligned picks",
    },
    {
        "id": "reuters_markets",
        "name": "Reuters Markets",
        "url": "https://feeds.reuters.com/reuters/businessNews",
        "type": "rss",
        "themes": ["Macro", "Earnings", "Markets"],
        "freq": "daily",
        "priority": 2,
        "why": "Fast news — macro events, earnings surprises",
    },
    {
        "id": "ft_markets",
        "name": "Financial Times Markets",
        "url": "https://www.ft.com/markets?format=rss",
        "type": "rss",
        "themes": ["Global Macro", "Markets"],
        "freq": "daily",
        "priority": 2,
        "why": "European/global perspective — USD, rates, geopolitics",
    },
    {
        "id": "mckinsey_insights",
        "name": "McKinsey Global Institute",
        "url": "https://www.mckinsey.com/mgi/rss",
        "type": "rss",
        "themes": ["Themes", "Industry", "Technology"],
        "freq": "monthly",
        "priority": 2,
        "why": "10-year thematic trends — AI, automation, energy transition",
    },
    {
        "id": "bridgewater",
        "name": "Bridgewater Daily Observations",
        "url": "https://www.bridgewater.com/research-and-insights/",
        "type": "web_scrape",
        "themes": ["Macro", "All Weather", "Regime"],
        "freq": "monthly",
        "priority": 1,
        "why": "Dalio's framework — All Weather, debt cycles, macro regime",
    },
]

# ─── TIER 3: EARNINGS & COMPANY DATA ─────────────────────────────────────────

TIER3_SOURCES = [
    {
        "id": "quartr",
        "name": "Quartr — Earnings Transcripts",
        "url": "https://quartr.com",
        "type": "quartr_api",
        "freq": "event-driven",
        "priority": 1,
        "why": "Earnings transcripts — EPS acceleration, guidance, management tone",
    },
    {
        "id": "sec_filings",
        "name": "SEC EDGAR — 10-K/10-Q/8-K",
        "url": "https://efts.sec.gov/LATEST/search-index",
        "type": "sec_api",
        "freq": "quarterly",
        "priority": 1,
        "why": "Primary source — forward guidance, risks, financial statements",
    },
    {
        "id": "finviz_screen",
        "name": "Finviz — Pre-Screen",
        "url": "https://finviz.com/screener.ashx",
        "type": "web_scrape",
        "freq": "weekly",
        "priority": 1,
        "why": "Quick pass/fail filter before deep analysis",
    },
]

# ─── WATCHLIST (core tickers to monitor) ─────────────────────────────────────

DEFAULT_WATCHLIST = {
    "AI":           ["NVDA", "MSFT", "PLTR", "SOUN", "CRWV"],
    "Memory/HBM":   ["MU", "WDC", "AMAT", "MRAM"],
    "Space":        ["RKLB", "LUNR", "ASTS", "RDW"],
    "Quantum":      ["IONQ", "RGTI", "QUBT"],
    "Photonics":    ["LITE", "COHR", "AAOI", "IPGP"],
    "DefenseTech":  ["PLTR", "CACI", "AXON", "AVAV"],
    "NeoCloud":     ["CRWV", "SMCI", "CORZ"],
    "Nuclear/SMR":  ["NNE", "OKLO", "CEG", "CCJ"],
    "Robotics":     ["ISRG", "TER", "TSLA"],
}

ALL_TICKERS = list({t for tickers in DEFAULT_WATCHLIST.values() for t in tickers})

# ─── SUPER INVESTORS to track (Dataroma / 13F) ───────────────────────────────

SUPER_INVESTORS = {
    "ackman":      {"name": "Bill Ackman",       "cik": "0001336528", "style": "concentration"},
    "tepper":      {"name": "David Tepper",      "cik": "0001262418", "style": "macro/growth"},
    "druckenmiller":{"name":"Stan Druckenmiller","cik": "0001536411", "style": "macro/trend"},
    "burry":       {"name": "Michael Burry",     "cik": "0001649339", "style": "deep value"},
    "chase_coleman":{"name":"Chase Coleman",     "cik": "0001273087", "style": "growth/tech"},
    "coatue":      {"name": "Philippe Laffont",  "cik": "0001159165", "style": "tech growth"},
    "lone_pine":   {"name": "Stephen Mandel",    "cik": "0001061165", "style": "quality growth"},
}
