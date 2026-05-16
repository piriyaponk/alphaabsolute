# Data Sourcing Rules — AlphaAbsolute
*Rules for data quality, source hierarchy, hallucination prevention, and citation standards.*

## Source Hierarchy (use in priority order)

Tier 1 (MUST USE -- primary alpha):
- FRED API: US macro regime (Fed funds, yield curve, industrial production, sentiment)
- SEC EDGAR: 10-K/10-Q/8-K filings (primary financial statements -- never estimate)
- Insider trades: OpenInsider cluster buys (3+ insiders > $1M within 2 weeks)
- Earnings transcripts: Quartr / SEC (management tone, guidance, EPS acceleration)

Tier 2 (SHOULD USE -- confirmation):
- yfinance: price, volume, MA, RS percentile (free, reliable for US equities)
- SET MCP (uvx set-mcp): Thai SET financial statements (Income, Balance Sheet, CF)
- IBD RSS: Market Pulse, IBD 50 leadership stocks (Minervini-aligned filter)
- SemiAnalysis: semiconductor supply chain, AI chip data (moves prices)

Tier 3 (CONTEXT ONLY -- never sole source):
- Reuters, FT, Seeking Alpha RSS: news context -- never trade on alone
- Google Trends: narrative acceleration proxy -- use with Tier 1 confirmation
- Reddit / social: sentiment signal only -- never fundamental data

## Hallucination Prevention Rules

1. NEVER fabricate financial numbers -- only use numbers from actual data sources
2. If a data source is unavailable, state explicitly: "Data unavailable -- [source] not reachable"
3. Every financial figure must have a source citation: {value} [{source}, {date}]
4. For Thai stocks: always write "ข้อมูลล่าสุด: [date]" when data is not real-time
5. EPS/Revenue figures: use SEC filings or yfinance -- never estimate from memory
6. If you cite a number from memory and cannot verify it, flag it: [UNVERIFIED]

## Data Freshness Requirements

Price data: must be current-day or prior day close -- never use prices > 2 days old
Earnings data: use most recent completed quarter (not TTM estimates)
Macro data: FRED updates weekly -- use within 7 days of publication
Insider data: cluster within 14-day window -- older clusters lose signal value
13F filings: filed 45 days after quarter end -- always note the filing lag

## Allowed Data Operations (Bash tool)

python scripts/fetch_macro.py -- FRED macro update
python scripts/fetch_stock_data.py -- yfinance price data
uvx set-mcp -- Thai SET financial data
python scripts/run_screener.py -- PULSE screen
python scripts/runners/daily_runner.py -- daily data pipeline
python scripts/runners/weekly_runner.py -- full weekly pipeline

## Data Storage Rules

Raw numbers, time-series --> data/
Processed outputs --> output/
Portfolio state --> data/paper_trading/portfolio_state.json
Trade log --> data/paper_trading/trade_log.json
NRGC state per ticker --> data/nrgc/state/{TICKER}.json
Smart signals latest --> data/smart_signals/latest.json
Weekly run log --> data/state/weekly_run_log.json

## Source Quality Tracking

Track source accuracy in: data/agent_memory/research_kb.json
- Each source gets hit rate: correct signals / total signals
- Sources below 40% hit rate get deprioritized automatically
- Top sources by accuracy are cited first in synthesis reports
