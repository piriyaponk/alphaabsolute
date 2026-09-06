# Bear Market Detection Context — 450 tokens
## Mandate
Detect bear markets 10-20 days BEFORE price confirms. Preserve cash. Minimize drawdown.
Primary source: `data/market_regime/strategy_clearance.json` → `early_warnings[]`

## The 6 Leading Indicators (M0 pre-computes these)

### 1. VOLUME_INVERSION
Down-volume days exceed up-volume days for 3+ consecutive sessions.
Smart money distributing while price holds or grinds up. Classic topping signal.

### 2. MOMENTUM_DIVERGENCE  
Price making higher highs BUT RSI/MACD making lower highs.
Hidden selling pressure. Most reliable of all 6 — fires 2-3 weeks before breakdown.

### 3. VIX_RISING_FROM_LOW
VIX rises >30% from its 20-day low while SPY is still near highs.
Options market pricing in fear before equity prices reflect it.

### 4. GROWTH_LAGGING_VALUE
QQQ underperforms SPY by >3% over 10 trading days.
Rotation out of growth = institutional risk-off positioning.

### 5. FAILED_50DMA_RECOVERY
SPY or QQQ broke below 50DMA, bounced, then failed to recover and closed back below.
Distribution confirmed — buyers exhausted at resistance.

### 6. DISTRIBUTION_CLUSTER
5+ distribution days (index down ≥0.2% on higher volume) in 25-session window.
Stalling days (price barely up, close in lower half, higher volume) also count.

## Response Protocol by Warning Count
| Warnings | Regime Cap | Action |
|----------|-----------|--------|
| 0 | Normal | Trade freely per regime |
| 1-2 | -1 level | Reduce new position size 30% |
| 3-4 | PRESSURE max | Halt breakout buys, hold winners only |
| 5-6 | CORRECTION forced | Raise cash to 50%+, cut all losers |

## Cash Raising Order (when ≥3 warnings)
1. Exit positions with loss (stop or near-stop)
2. Trim late-stage positions (Base 3+, Phase 4)
3. Reduce largest positions to 7% max
4. Raise cash to regime target minimum
5. Keep ONLY: Base 1-2, Phase 2-3, CANSLIM A+ grade

## Recovery Signal
Return to normal trading when:
- All 6 warning conditions clear simultaneously
- M0 regime score returns to ≥55 (PRESSURE or better)
- SPY/QQQ reclaim 50DMA with expanding up-volume
