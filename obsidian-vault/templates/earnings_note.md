---
ticker: <% tp.system.prompt("Ticker symbol") %>
quarter: <% tp.system.prompt("Quarter (e.g. Q1 2026)") %>
report_date: <% tp.date.now("YYYY-MM-DD") %>
eps_actual: 
eps_estimate: 
eps_beat_miss: 
revenue_actual: 
revenue_qoq_pct: 
revenue_yoy_pct: 
guidance: raise / maintain / lower
tone_score: 
nrgc_signal: 
tags: [earnings, <% tp.frontmatter.ticker %>]
---

# <% tp.frontmatter.ticker %> Earnings — <% tp.frontmatter.quarter %>

## Quick Verdict
- **EPS:** $ actual vs $ estimate → beat/miss by %
- **Revenue:** $B (QoQ: +/−% | YoY: +/−%)
- **Guidance:** Raised / Maintained / Lowered
- **Tone:** +/− (score from earnings_tone.py)
- **NRGC Signal:**

## Key Numbers

| Metric | Q Actual | Q Prior | Change |
|--------|---------|---------|--------|
| EPS | | | |
| Revenue | | | |
| Gross Margin | | | |
| Operating Margin | | | |

## Management Commentary (key quotes)

## Guidance Detail

## Thesis Impact
Does this change the NRGC phase? **YES / NO**
New phase assessment:

## Earnings Tone Analysis
Run: `python scripts/learning/earnings_tone.py`
- Positive signals:
- Negative signals:
- Inflection language:
- QoQ tone shift:

## Action
- [ ] No change — hold
- [ ] Add — thesis strengthened
- [ ] Reduce — concern
- [ ] Exit — thesis broken
