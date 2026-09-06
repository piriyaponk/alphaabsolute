# CANSLIM Scoring Context — 500 tokens
## Role
Score stocks C+A+N+S+L+I (each 0-100). M is a GATE not a score. Read `data/canslim_scores/{TICKER}.json`.

## Scoring Weights
`Composite = C×0.25 + A×0.15 + N×0.20 + S×0.10 + L×0.20 + I×0.10`

## C — Current EPS (25%)
- QoQ EPS growth ≥25% = 70pts base
- Acceleration (this Q rate > last Q rate) = +20pts
- Revenue growth ≥20% QoQ = +10pts
- Operating leverage (EPS grows faster than revenue) = +bonus

## A — Annual EPS (15%)
- 3-year EPS CAGR ≥25% = 70pts
- ROE ≥17% = +15pts
- 3+ years of growth = +15pts

## N — New Product/High (20%)
- Within 5% of 52-week high = 50pts base
- NRGC Phase 2-3 = +20pts (narrative traction active)
- Catalyst confirmed (earnings beat, product launch, contract) = +20pts
- IPO within 15 years = +10pts

## S — Supply/Demand Volume (10%)
- Recent 5D vol > 1.5× 20D avg = 60pts (demand spike)
- Up-volume% > 60% (institutional accumulation) = +20pts
- Pocket pivot detected (up-vol > highest down-vol in 10 days) = +20pts

## L — Leadership RS (20%)
- RS excess vs SPY: 1M, 3M, 6M, 12M averaged
- Rank vs universe: mapped to 0-100
- RS acceleration (recent stronger than longer) = bonus

## I — Institutional Sponsorship (10%)
- NRGC institutional_signal = strong/moderate = 40-70pts
- Big up-days vs big down-days ratio > 1.5 = +20pts
- 13F institutional ownership growing = +10pts

## M — Market Direction (GATE)
- Read strategy_clearance.json → regime ≥ CORRECTION → M_GATE = BLOCK
- M is NOT scored — it's a binary pass/fail gate
- Failed M gate = no action regardless of composite score

## Grade Tiers
| Score | Grade | Action |
|-------|-------|--------|
| 85-100 | A+ | Maximum conviction — size up |
| 70-84 | A | Full position |
| 55-69 | B | Watch closely — half size only |
| <55 | C/D | Skip — wait for improvement |
