# Execution Mode — AlphaAbsolute

Load this context when: evaluating specific entry points, checking open positions, managing stops, paper trading decisions.

## Active Agents in Execution Mode
- Agent 10 (CIO Synthesis) — primary decision maker
- Agent 12 (Risk Devil) — mandatory before any BUY
- Agent 06/07 (Thai/US Fund Manager) — position-specific
- Agent 08 (Asset Allocator) — cash/allocation check

## Execution Protocol (every BUY — no exceptions)

### Step 1: Health Check (8 indicators)
TF Alignment / Market / Rel Strength / Volume / Momentum / Volatility / Extension / Bull Streak
Score 7-8 = Green Light | 5-6 = Yellow (reduced size) | <5 = Red (no entry)

### Step 2: Wyckoff Gate
- Weinstein Stage: must be Stage 2
- Wyckoff Phase: must be Accumulation or Mark-Up
- If Stage 3/4 detected → STOP, no entry

### Step 3: Risk.md Check
- Position size within limits?
- ADTV compliant?
- Earnings within 5 days?
- Theme concentration OK?

### Step 4: Thesis Challenge
Answer: "What would make this thesis wrong?"
If you cannot answer this → do not enter.

## Paper Trade Format
```
PAPER TRADE: [BUY/SELL] [TICKER]
Entry: $XX.XX
Stop: $XX.XX (-X%)
Target: $XX.XX (+X%)
Size: X% of portfolio
Setup: [VCP/Spring/Hypergrowth/etc]
NRGC Phase: X
EMLS Score: XX
Health Check: X/8
Risk.md: PASS/FAIL
Thesis challenge: [answer]
```
