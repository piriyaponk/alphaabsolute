---
ticker: <% tp.system.prompt("Ticker") %>
direction: <% tp.system.prompt("BUY or SELL") %>
entry_date: <% tp.date.now("YYYY-MM-DD") %>
entry_price: <% tp.system.prompt("Entry price") %>
stop_price: <% tp.system.prompt("Stop price") %>
target_price: <% tp.system.prompt("Target price") %>
size_pct: <% tp.system.prompt("Size % of portfolio") %>
setup: <% tp.system.prompt("Setup type (VCP/Spring/Hypergrowth)") %>
nrgc_phase: 
emls_score: 
health_score: /8
status: open
pnl_pct: 
exit_date: 
exit_price: 
tags: [trade, <% tp.frontmatter.ticker %>, <% tp.frontmatter.direction.toLowerCase() %>]
---

# <% tp.frontmatter.direction %> <% tp.frontmatter.ticker %> — <% tp.frontmatter.entry_date %>

| Field | Value |
|-------|-------|
| Entry | $<% tp.frontmatter.entry_price %> |
| Stop | $<% tp.frontmatter.stop_price %> |
| Target | $<% tp.frontmatter.target_price %> |
| Size | <% tp.frontmatter.size_pct %>% |
| Setup | <% tp.frontmatter.setup %> |
| NRGC Phase | |
| EMLS Score | |
| Health Check | /8 |

## Entry Thesis
*(Why now? What is the setup?)*

## Thesis Challenge
*(What would make this wrong?)*

## Risk.md Compliance
- [ ] Size within limits
- [ ] ADTV check
- [ ] No earnings within 5 days
- [ ] Stage 2 gate: GREEN

## Trade Management Log

| Date | Price | Action | Reason |
|------|-------|--------|--------|
| <% tp.date.now("YYYY-MM-DD") %> | $<% tp.frontmatter.entry_price %> | ENTRY | |

## Exit Notes
*(To be filled on close)*

## Post-Mortem
*(To be filled after exit)*
