# RS Inflection Context (I1) — 400 tokens
## Role
Identify stocks crossing from RS laggard → RS leader. This is the #1 early signal for Phase 2→3 institutional discovery. Read `data/rs_universe/latest.json`.

## RS Inflection Definition
Stock crosses from BELOW 50th percentile → ABOVE 70th percentile in ≤4 weeks.

## 3 Inflection Types (strongest to weakest)
| Type | Condition | Strength |
|------|-----------|----------|
| RS_CROSS_50_TO_70 | Composite RS: prev<50, curr≥70 | STRONG if ≥80th |
| RS_SURGE_TO_TOP_DECILE | curr≥90th, prev<80th | STRONG |
| RS_MULTI_TF_INFLECTION | 1M≥70th AND 3M≥60th AND prev 1M<50th | MODERATE |

## PRISM RS Gate (hard requirement for Leader Setup)
ALL 6 timeframes must be ≥72nd percentile:
- rs_1w_pct, rs_2w_pct, rs_1m_pct, rs_3m_pct, rs_6m_pct, rs_12m_pct ≥ 72
- RS momentum (2W vs 1M, 3M vs 6M, 6M vs 12M) > -10%
- ANY fail = PRISM RS gate RED for Leader Setup

## Action on Inflection
1. Add to watchlist immediately
2. Cross-check NRGC phase (should be Phase 2 or early 3)
3. Run base_watchdog — is a VCP forming?
4. If Base 1-2 + RS inflection + NRGC Phase 2-3 = HIGHEST priority entry

## RS Laggard Warning
Portfolio position in bottom quintile of RS universe for 2+ weeks:
→ Flag for exit review. Smart money moving elsewhere.
→ Rule: never hold an RS laggard through a correction.

## Data Source
`data/rs_universe/latest.json`
Key fields:
- `top_10_leaders`: ranked by composite RS percentile
- `inflection_count`: number of inflection signals today
- `rs_laggards`: held positions with deteriorating RS
- `universe[TICKER].pulse_rs_gate`: true/false PRISM gate status
