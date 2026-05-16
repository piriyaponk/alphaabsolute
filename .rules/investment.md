# Investment Rules — AlphaAbsolute
*Modular rule file. Loaded by agents that make BUY/SELL/HOLD decisions.*

## Core Philosophy
- Never enter without confirming all framework gates
- Never move a stop loss against the trade
- Never add to a losing position
- Never hold through earnings without explicit thesis re-evaluation

## The 3 Valid Setups

### Setup 1 — Leader / VCP (Minervini SEPA)
- Weinstein Stage 2 ONLY
- RS percentile > 72nd (all timeframes: 1W/2W/1M/3M/6M/12M)
- Trend Template: 8/8 criteria required
- VCP: 3+ contractions, volume dry-up, breakout volume > 150% of 50D avg
- Entry: at pivot +/-2%, never more than +5% extended

### Setup 2 — Bottom Fish (Wyckoff Spring)
- Stage 1 to 2 transition: price crossing 30W MA on expanding volume
- RS improving from below 50th percentile
- Wyckoff Spring or SOS confirmed (volume expansion > 80% of 20D avg)
- Wait 2 weeks after Spring before entry
- Max size: 4% until Stage 2 confirmed (Risk.md)

### Setup 3 — Hypergrowth (Base 0 / 1)
- Revenue accelerating QoQ AND YoY for 3+ quarters
- Gross margin expanding
- Large TAM + industry breakthrough catalyst
- Base 0 or Base 1 ONLY
- Max size: 5% for Base 0, 8% for Base 1 (Risk.md)

## Mandatory Gates (all must PASS before entry)

Gate 1: Wyckoff x Weinstein -- Stage must be 2; Phase must be Accumulation or Mark-Up. Stage 3/4 = HARD STOP.
Gate 2: Health Check -- 7-8/8 = Green (full size); 5-6/8 = Yellow (half size); <5/8 = Red (no entry). 4/4 TF Alignment required.
Gate 3: Risk.md Compliance -- confirm no position, size, or timing violations.
Gate 4: Thesis Challenge -- answer "What would make this wrong RIGHT NOW?" If no specific answer, do not enter.

## NRGC Phase Entry Map

Phase 1-2 = Bottom Fish only (small size, confirm Stage transition)
Phase 2-3 = VCP / Hypergrowth (standard entry if Wyckoff gate passes)
Phase 3   = Leader (full size -- max conviction zone)
Phase 4   = Leader extended (reduce size, tighter stop)
Phase 5   = NO ENTRY -- euphoria
Phase 6   = NO ENTRY -- distribution

## Exit Triggers

-8% from entry = mandatory stop review
Stage 3/4 detected on held position = immediate exit review
RS decays from top quartile = downgrade, reduce size
Revenue QoQ decelerating = reduce
EPS revision negative = exit -- thesis broken
RSI > 85 + climax volume = trim aggressively
Distribution days >= 4 = no new entries, reduce all

## Anti-Bias Rules

1. Position changes only on NEW DATA -- not on CIO preference
2. Minority views must be preserved and presented fully
3. Never validate a thesis failing the framework
4. Before every BUY: "What would make this wrong?" must be answered specifically
