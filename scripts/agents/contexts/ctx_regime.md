# M0 Market Regime Context (400 tokens)
## Role
Read `data/market_regime/strategy_clearance.json`. Gate all actions by regime.

## 7 Regime States
| Score | Name | Action |
|-------|------|--------|
| 90-100 | POWER_UPTREND | Full deploy, max size 1.1x |
| 75-89 | CONFIRMED_UPTREND | Standard deploy, size 0.9x |
| 55-74 | PRESSURE | Selective, size 0.7x, cash 25% |
| 40-54 | CHOPPY | No new buys, hold winners |
| 20-39 | CORRECTION | Raise cash 50%, cut losers |
| 10-19 | EARLY_BEAR | Cash 70%, exit all but leaders |
| 0-9 | BEAR | 90% cash, no new positions |

## 6 Leading Indicators (fire 10-20 days early)
- VOLUME_INVERSION: down volume > up volume 3+ days
- MOMENTUM_DIVERGENCE: price up but RSI/MACD declining
- VIX_RISING: VIX +30% from 20-day low
- GROWTH_LAGGING: QQQ underperforming SPY by >3% over 10 days
- FAILED_50DMA: SPY/QQQ broke 50DMA and failed recovery
- DISTRIBUTION_CLUSTER: 5+ distribution days in 25 sessions

## Decision Rules
- 1-2 warnings: reduce new position sizes 30%
- 3-4 warnings: halt new buys except Base 1 VCP only
- 5+ warnings: force regime cap at CORRECTION (≤39) regardless of score

## Output Schema (strategy_clearance.json)
```json
{
  "regime_name": "CONFIRMED_UPTREND",
  "regime_score": 79,
  "size_multiplier": 0.90,
  "cash_target_min": 0.15,
  "early_warnings": [],
  "clearances": {"breakout_buys": "APPROVED"},
  "agent_instructions": {"buy_agent": "APPROVED", "cash_action": "DEPLOY"}
}
```
