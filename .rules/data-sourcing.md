# Data Sourcing Rules — AlphaAbsolute

Always loaded. Governs how agents gather and cite data.

## Source Hierarchy

| Tier | Source | Use for |
|------|--------|---------|
| 1 | yfinance, FRED API, EDGAR XBRL | Price, macro, financials — primary |
| 2 | Reuters, Investing.com, SEC filings | News, events, earnings |
| 3 | SemiAnalysis, TrendForce | Semiconductor / AI capex sector intelligence |
| 4 | IEA, Payload Space, Lightwave | Power demand, space, optical |
| 5 | Seeking Alpha, earnings transcripts | Narrative, tone, management signals |

## Fabrication Rules (CRITICAL)

- **NEVER fabricate Thai stock numbers** — only use numbers from SETSMART or user-provided data
- **NEVER invent analyst price targets** — only cite if from a named analyst with date
- **Always write ข้อมูลล่าสุด: [date]** when data is not real-time
- If unsure of a number: state "unverified — needs confirmation" rather than guessing

## Citation Format

Every data point in reports must trace to a source:
```
[Fact] (Source: [name], [date])
Example: EPS Q1 2026: $1.62 beat estimate $1.48 (Source: EDGAR 10-Q, 2026-04-30)
```

## Caching Rules

- Price data: cache max 1 day
- EDGAR XBRL capex: cache by week (%Y-W%W)
- Google Trends: cache by week
- FRED macro series: cache max 1 week
- News: no cache — always fresh
