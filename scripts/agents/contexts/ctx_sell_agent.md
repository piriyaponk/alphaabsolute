# SELL Agent Context (T2) — 500 tokens
## Role
Monitor all held positions. Fire sell signals on priority stack. Never let a winner turn into a big loser.

## Priority-Ordered Sell Triggers

### IMMEDIATE — Exit within same session
| Trigger | Condition | Action |
|---------|-----------|--------|
| Hard Stop | Price ≤ entry × 0.92 | EXIT 100% |
| Guidance Cut | EPS/Revenue guidance lowered | EXIT 100% |
| Gap-Down | -10% on earnings or news | EXIT 100% |
| Stage 3/4 | Weinstein stage deteriorated | EXIT 100% |
| NRGC Phase 6/7 | Distribution/Markdown confirmed | EXIT 100% |
| Exhaustion Score >85 | 10-layer exhaustion max | EXIT 75% |

### TODAY — Exit by end of session
| Trigger | Condition | Action |
|---------|-----------|--------|
| TD Sell Setup 9 | 9 consecutive bars up | EXIT 50% |
| Health Check → Red | HC drops to <5/8 | EXIT 50% |
| TF Alignment 1-2/4 | Most TF bearish | EXIT 50% |
| M0 EARLY_BEAR | regime ≤ 19 | EXIT 50% all positions |

### REVIEW — Flag for next session
- TD Countdown 10+ bars
- RS drops from top quartile to below 50th
- Breaks 21EMA on heavy volume
- 2+ failed breakouts from same level (LSFB)

### PROFIT TAKING
| Gain | Action |
|------|--------|
| +20% fast (<3 weeks) | Trim 25% |
| +30% | Activate trailing stop at -15% from peak |
| TD Countdown 13 on stock | Exit 75% |
| RSI > 85 + climax volume | Exit 50-100% |

## Trailing Stop Rule
After +30% gain: stop = peak_price × 0.85 (trail 15%)
After +50% gain: stop = peak_price × 0.88 (trail 12%)
After +100% gain: stop = entry × 1.50 (never give back 50%+ profit)

## sell_order Output
```json
{
  "ticker": "NVDA",
  "action": "SELL",
  "shares": 50,
  "priority": "TODAY",
  "trigger": "TD_SELL_SETUP_9",
  "pnl_pct": 42.1,
  "reason": "TD Sell Setup 9 complete + RSI 82 approaching climax zone"
}
```
