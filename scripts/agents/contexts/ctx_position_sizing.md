# Position Sizing Context — 400 tokens
## Framework
Size = Capital × Base% × [5 multipliers stacked]

## Base Position Size by Setup
| Setup | Base % | Notes |
|-------|--------|-------|
| Leader (Base 1-2, VCP) | 10% | Full size when all gates green |
| Leader (Base 3) | 7% | Reduced — late stage risk |
| Hypergrowth (Base 0-1) | 5% | High growth but early = volatile |
| Bottom Fish (pre-Stage 2) | 4% | Wyckoff Spring only, wait confirm |

## 5 Multipliers (all applied simultaneously)
```
Final = Base × cap_mult × score_mult × hc_mult × base_mult × regime_mult
```

| Multiplier | Source | Range |
|-----------|--------|-------|
| cap_mult | Market cap tier | mega=0.6, large=0.8, mid=1.0, small=0.8 |
| score_mult | NRGC composite | <70=0.6, 70-89=0.8, ≥90=1.0 |
| hc_mult | Health Check | red=0 (skip), yellow=0.5, green=1.0 |
| base_mult | Base Counter | Base4+=0 (block), Base3=0.7, Base1-2=1.0 |
| regime_mult | M0 clearance | BEAR=0, CORRECTION=0.3, CHOPPY=0.6, PRESSURE=0.7, CONFIRMED=0.9, POWER=1.0 |

## Hard Limits
- Max single position: 15% of equity (no exceptions)
- Max theme concentration: 50% of equity
- Max Hypergrowth Base 0: 5% per position
- Max Bottom Fish (pre-Stage 2): 4% per position
- ADTV rule: position size ≤ 20% of 6-month ADTV

## Sizing Example
COHR: capital=$100K, Base 2 VCP, score=72, HC=7/8, regime=CONFIRMED
- Base% = 10% = $10,000
- cap_mult = 0.8 (large)
- score_mult = 0.8 (score 72-89)
- hc_mult = 1.0 (7/8 green)
- base_mult = 1.0 (Base 2)
- regime_mult = 0.9 (CONFIRMED)
- Final = $10,000 × 0.8 × 0.8 × 1.0 × 1.0 × 0.9 = **$5,760** (5.76%)

## Pyramiding Rules
- Add at 1st pullback to 10EMA if stock up 10-15% and HC still green
- Max add = 50% of original position
- Stop on add = same hard stop as original entry
- Never pyramid into extended stock (RSI > 80)
