# Base Watchdog Context (I4) — 400 tokens
## Role
Monitor VCP base development. Alert when base is HOT (breakout imminent). Block entries on LSFB. Read `data/base_watchdog/latest.json`.

## Alert Levels (lifecycle)
| Level | Meaning | Action |
|-------|---------|--------|
| NONE | No pattern yet | Monitor weekly |
| EARLY 🔵 | Possible base forming | Watch — 1st contraction |
| FORMING 🟡 | 2 contractions confirmed | Prepare — add to active watch |
| NEAR 🟠 | 3 contractions, volume drying | Ready — check daily, set price alert |
| HOT 🔴 | ≥3 contractions + dry volume + within 3% of pivot | BUY ZONE — enter on breakout above pivot |
| BROKEN 🔴 | 2+ failed breakouts (LSFB) | AVOID — supply wall confirmed |

## HOT Criteria (all must be true)
- VCP contractions ≥ 3
- Volume dry-up score ≥ 75/100
- Distance from pivot: within -3% to 0%
- Base quality score ≥ 65/100

## Base Quality Scoring (0-100)
| Factor | Weight |
|--------|--------|
| Volatility Contraction (depth shrinking each swing) | 25% |
| Volume Dry-Up (recent vol < 50% of early base vol) | 20% |
| RS During Base (stock outperforming SPY during base) | 20% |
| Tight Closes (70%+ bars < 1.5% daily range) | 15% |
| Depth (< 10% = perfect flat) | 10% |
| Duration (3-10 weeks = sweet spot) | 10% |

## Pivot Rules
- Pivot = highest high within base
- Buy zone = pivot to pivot × 1.05 (up to 5% above pivot)
- NEVER chase more than 3% above pivot
- Stop = -8% from pivot (Base 3: -6%)

## LSFB Rule
2+ failed breakouts from the same level = SUPPLY WALL.
→ Do NOT enter. Wait for base to reset completely (new lower lows, full base rebuild).

## Data Output
`data/base_watchdog/latest.json`
Key: `alerts[TICKER]` → `{alert: "HOT", quality: 78, vcp_contractions: 3, pct_from_pivot: -1.2}`
