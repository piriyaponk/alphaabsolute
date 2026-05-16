---
ticker: <% tp.system.prompt("Ticker symbol (e.g. MU)") %>
emls_score: 
nrgc_phase: 
theme: 
setup: 
entry: 
stop: 
target: 
size_pct: 
last_updated: <% tp.date.now("YYYY-MM-DD") %>
status: watchlist
tags: [ticker, <% tp.system.prompt("Theme tag (e.g. Memory-HBM)") %>]
---

# <% tp.frontmatter.ticker %>

**EMLS Score:** | **NRGC Phase:** | **Theme:** <% tp.frontmatter.theme %>

---

## Setup Summary
- **Setup type:** (VCP / Spring / Hypergrowth / Bottom Fish)
- **Entry:** $
- **Stop:** $ (−%)
- **Target:** $ (+%)
- **Size:** % of portfolio

## Health Check (0/8)
- [ ] TF Alignment (Monthly/Weekly/Daily/Intraday all bullish)
- [ ] Market (breadth healthy, risk-on)
- [ ] Rel Strength (top quartile vs SPX + sector)
- [ ] Volume (accumulation footprint)
- [ ] Momentum (strong, not parabolic)
- [ ] Volatility (expanding after compression)
- [ ] Extension (not >10% above 10EMA, RSI < 80)
- [ ] Bull Streak (4+ consecutive bullish bars)

## NRGC Phase Analysis
**Current phase:**
**Phase signal:**
**Next phase catalyst:**

## Bull Case

## Bear Case

## What Would Make This Wrong?

## Wyckoff Gate
- Weinstein Stage: [ ] PASS / [ ] FAIL
- Wyckoff Phase:
- Gate Verdict: GREEN / YELLOW / RED

## Risk.md Check
- [ ] Position size within limit
- [ ] ADTV compliant
- [ ] No earnings within 5 days
- [ ] Stage 2 confirmed

## Weekly Signal Log
*(Auto-populated by weekly_runner.py)*

## Notes
