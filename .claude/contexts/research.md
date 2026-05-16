# Research Mode — AlphaAbsolute

Load this context when: doing thematic deep dives, macro analysis, earnings research, sector studies, finding new stocks.

## Active Agents in Research Mode
- Agent 01 (Macro Intelligence) — primary
- Agent 02 (News & Event) — primary
- Agent 05 (Thematic Research) — primary
- Agent 04 (Fundamental) — on demand
- Agent 3b (Mispricing) — on demand

## Research Protocol
1. Start with NRGC framework — what phase is this theme/stock?
2. Identify the bottleneck — where is pricing power?
3. Check narrative acceleration — keyword trend vs last week
4. Gather primary data first (EDGAR, FRED, yfinance) before LLM synthesis
5. Cite every number with source and date
6. End with: bull case / bear case / NRGC phase signal

## Data Sources (prioritized)
- SemiAnalysis → semiconductor / AI capex
- EDGAR XBRL → hyperscaler capex (MSFT/GOOG/META/AMZN/ORCL)
- FRED → US macro (ISM, copper, yields)
- yfinance → price, short interest, float
- TrendForce → DRAM/HBM supply/demand

## Output Format
Every research output must end with:
```
NRGC Phase Signal: [Phase X] — [reason]
EMLS Boost: [+/- N points]
Action: [Monitor / Watchlist / Entry Zone / Avoid]
```
