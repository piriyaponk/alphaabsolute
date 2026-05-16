# Risk.md — AlphaAbsolute Hard Risk Rules

**CRITICAL: These rules are NEVER bypassed. Every agent references this file before any BUY/SELL recommendation.**

---

## Position Sizing Rules

| Rule | Limit | Exception |
|------|-------|-----------|
| Max single position | 15% of equity | NONE |
| Hypergrowth Base 0 | 5% max | NONE |
| Hypergrowth Base 1 | 8% max | NONE |
| Bottom Fish (pre-Stage 2) | 4% max | NONE |
| ADTV rule | Position ≤ 20% of 6M ADTV | NONE |
| Theme concentration | 50% of equity max | NONE |

## Stop Loss Rules

- **-8% from entry** = mandatory review flag — reassess thesis immediately
- Stops are **never moved against the position**
- After 3 consecutive losing trades: reduce size by 50% until 2 wins
- Max daily drawdown: 3% of total portfolio NAV = trading halt for the day

## Entry Restriction Rules

- **Earnings within 5 trading days**: NO new entry. Reduce existing to <3% if holding
- **Stage 3 or Stage 4 detected**: NO BUY — no exceptions, exit review triggered immediately
- **%>200DMA < 50%**: Raise cash to 30%+ minimum
- **%>200DMA < 30%**: Raise cash to 40%+ minimum
- **Distribution days ≥ 4**: Reduce ALL positions, no new entries
- **Failed breakout on volume**: Exit or stop — do not average down

## Framework Gate (MANDATORY before every BUY)

```
STAGE/WYCKOFF GATE CHECK:
- Weinstein Stage: [1/2/3/4] — [PASS/FAIL]
- Wyckoff Phase: [Accumulation/Distribution/Mark-Up/Mark-Down] — [PASS/FAIL]
- GATE VERDICT: [GREEN / YELLOW / RED]
```

**Stage 3 or 4 = RED. No override allowed.**

## Exit Triggers (immediate review required)

- RS rank decays from top quartile → downgrade priority
- Revenue QoQ decelerating → reduce position
- EPS revision turning negative → exit — thesis broken
- RSI > 85 + parabolic + climax volume → trim aggressively
- Heavy sell volume on up days → distribution signal — smart money exiting

## ADTV Liquidity Check

```
Max position size ($) = min(
    15% × portfolio_equity,
    20% × 6M_ADTV_in_dollars
)
```

For Thai stocks: ADTV must be > 20M THB
For US stocks: ADTV must be > $10M USD

---

*This file is the single source of truth for risk. All agents must reference it.*
*Risk.local.md = private account overrides (gitignored)*
