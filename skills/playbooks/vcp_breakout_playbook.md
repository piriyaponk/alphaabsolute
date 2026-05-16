# VCP Breakout Playbook
*Setup 1 — Leader / Momentum (Minervini SEPA)*

Load this playbook when evaluating a VCP (Volatility Contraction Pattern) setup.

---

## What is a Valid VCP?

A VCP requires **3 or more tight pullbacks** with **volume contracting each base**.

```
Price:  ----\    /--\  /--\  /------>  BREAKOUT
              \  /    \/    \/
Volume: ||||  ||   ||   |   |        |||  (expansion on breakout)
         (high)  (lower each contraction)
```

### VCP Checklist (all must be YES before entry)

**Structure:**
- [ ] 3+ distinct pullbacks visible (more contractions = tighter base = better)
- [ ] Each pullback shallower than the previous (e.g., -15% → -10% → -6%)
- [ ] Each pullback volume lower than the previous (supply drying up)
- [ ] Base duration: minimum 3 weeks, ideal 6-15 weeks
- [ ] Price at or within -5% of pivot (buy point = top of last base)

**Trend Template (ALL 8 required):**
- [ ] Price > 150D MA (30W MA)
- [ ] Price > 200D MA (40W MA)
- [ ] 150D MA > 200D MA
- [ ] 200D MA trending up for at least 1 month
- [ ] Price within -25% of 52W high
- [ ] Price at least +30% above 52W low
- [ ] RS line at new high OR near 52W high
- [ ] RS percentile > 72nd (all timeframes: 1W/2W/1M/3M/6M/12M)

**Volume confirmation on breakout:**
- [ ] Breakout volume > 150% of 50D avg volume
- [ ] Breakout closes near the TOP of the day's range (not reversing)
- [ ] No heavy distribution in prior 3 weeks

---

## Entry Rules

- **Entry point**: Buy on breakout above the pivot (top of last contraction)
- **Entry trigger**: Price closes above pivot on volume >150% of avg
- **Aggressive entry**: Can buy within -2% of pivot on strong volume (before full breakout)
- **Late entry**: NEVER buy more than +5% above pivot — setup is extended

---

## Stop Loss Placement

- **Initial stop**: Below the lowest low of the last contraction (tightest pullback)
- **Typical stop distance**: 5-10% from entry
- **Hard stop**: -8% from entry = mandatory review (Risk.md rule)
- **Never move stop lower** — if it hits, exit

---

## Position Sizing

Per Risk.md rules:
```
Size = min(15% equity, 20% × 6M_ADTV)
For base 0 hypergrowth VCPs: max 5%
For base 1: max 8%
For base 2+: can size to full conviction limit
```

---

## Scaling Rules

**Adding to winners only:**
- Add at the next valid pivot (if stock bases again after breakout)
- Never add to a losing position
- Never add if stock is more than +10% extended from last add

**Pyramid scaling:**
```
Initial: 50% of planned size at breakout
Add 1:   25% at next base breakout (+15-25% from entry)
Add 2:   25% at next base breakout (+30-40% from entry)
```

---

## Invalidation Conditions (exit or avoid)

- Breakout fails: stock returns below pivot on volume → EXIT immediately
- Volume on breakout weak (<100% of avg) → downgrade to watchlist
- RS line fails to make new high with price → warning sign
- Stage changes to 3 or 4 → EXIT
- Distribution days ≥ 4 in the market → hold, no adds
- Earnings within 5 days → reduce to <3%, no new entry

---

## Scoring a VCP (0-10)

| Factor | Points |
|--------|--------|
| 3+ contractions | +2 |
| Volume perfectly dry on last contraction | +2 |
| RS at new high | +2 |
| Tight final contraction (<5% range) | +2 |
| Clear pivot level (obvious buy point) | +1 |
| CANSLIM C+A+N+I all confirmed | +1 |
| **Total 9-10** | Maximum conviction |
| **Total 7-8** | Good setup |
| **Total <6** | Pass — wait for better setup |
