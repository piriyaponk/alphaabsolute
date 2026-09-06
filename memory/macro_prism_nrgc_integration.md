# Macro OS × PRISM × NRGC — Unified Investment Framework
## "3 Layers of Intelligence, 1 Investment Decision"

> **Philosophy:** Macro sets the game rules. NRGC finds the right stocks at the right phase.
> PRISM validates and sizes the trade. Three lenses — one verdict.

---

## THE 3-LAYER ARCHITECTURE

```
  ┌─────────────────────────────────────────────────────────────┐
  │  LAYER 3: MACRO OS (Top-Down Game Rules)                    │
  │  "What kind of game are we playing today?"                  │
  │                                                             │
  │  Inputs:  10Y yield, HY spread, PMI, CPI, Fed policy,      │
  │           yield curve, DXY, VIX, ERP                       │
  │  Outputs: Regime (9 types) + Score (X/30)                  │
  │           Which themes are investable                       │
  │           ERP adjustment to EMLS                           │
  │           Macro Gate PASS/FAIL                             │
  └───────────────────────┬─────────────────────────────────────┘
                          │  Regime → adjusts
                          ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  LAYER 2: NRGC (Narrative Reflexivity Growth Cycle)         │
  │  "Where is this stock in its growth journey?"               │
  │                                                             │
  │  Inputs:  EPS trajectory, narrative phase, RS trend,        │
  │           institutional ownership, price structure          │
  │  Outputs: NRGC Phase (0-7)                                 │
  │           Narrative Lifecycle Phase (1-7)                  │
  │           Entry zone (Ph 2-3 ideal)                        │
  │           Conviction multiplier                            │
  └───────────────────────┬─────────────────────────────────────┘
                          │  Phase → validates
                          ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  LAYER 1: PRISM (5-Component Entry System)                  │
  │  "Is this specific stock ready to buy RIGHT NOW?"           │
  │                                                             │
  │  Inputs:  Fundamental, Price Structure, RS, Volatility,     │
  │           Market Regime                                     │
  │  Outputs: PRISM score + 6-gate entry verdict               │
  │           Position size (Macro-adjusted)                   │
  │           Buy zone + Stop level                            │
  └─────────────────────────────────────────────────────────────┘
```

---

## GATE 0: MACRO GATE (NEW — added BEFORE existing 6 gates)

### Updated 7-Gate Entry Chain

```
GATE 0: MACRO GATE           ← NEW (Macro OS verdict)
GATE 1: TD Sequential Gate   — no Sell Setup 7-9
GATE 2: Health Check Gate    — score >= 5/8
GATE 3: Base Count Gate      — Base 4+ = BLOCKED
GATE 4: PRISM Gate           — GREEN or YELLOW
GATE 5: Cap/Score Tier Gate  — tier-specific threshold
GATE 6: Liquidity Gate       — ADTV >= $5M
GATE 7: BUY
```

### Macro Gate Logic

```
MACRO GATE INPUTS:
  - Current Regime (from M0 strategy_clearance.json)
  - Macro Score (X/30)
  - ERP level
  - Narrative Lifecycle Phase of the stock's theme

MACRO GATE VERDICT:

  REGIME                  | VERDICT  | SIZE MULTIPLIER | CONDITION
  ─────────────────────────┼──────────┼─────────────────┼────────────────────────────
  Goldilocks (24-30/30)   | GREEN    | 1.0x (full)     | All themes investable
  Reflation (20-25/30)    | GREEN    | 1.0x (full)     | Cyclical themes preferred
  Early Recovery (18-24)  | YELLOW   | 0.75x           | Leaders only (EMLS > 75)
  Late Overheating (15-20)| YELLOW   | 0.75x           | Watch inflation themes
  Stagflation (10-18)     | YELLOW   | 0.5x            | Defensive/real assets only
  Correction (8-14)       | RED      | 0.0x (wait)     | No new buys
  Bear (< 8)              | RED      | 0.0x (wait)     | Cash + shorts only
  Liquidity Melt-up       | YELLOW   | 0.75x           | Quality leadership only
  Credit Stress           | RED      | 0.0x            | Immediate exit high-beta

OVERRIDE RULE: ERP < 0% → downgrade any YELLOW to RED regardless of regime
OVERRIDE RULE: ERP > 3% → upgrade YELLOW to GREEN if fundamentals are sound
```

### Macro Gate Output (added to strategy_clearance.json)

```json
{
  "macro_gate": {
    "verdict": "YELLOW",
    "size_multiplier": 0.5,
    "reason": "STAGFLATION_LITE: only defensive/real assets investable",
    "erp_pct": 0.08,
    "erp_adjustment": -3,
    "investable_themes": ["Photonics", "DefenseTech", "Memory/HBM"],
    "blocked_themes": ["Speculative Hypergrowth", "Unprofitable Growth"],
    "narrative_window": "Ph2-3 themes with tangible earnings only"
  }
}
```

---

## NARRATIVE LIFECYCLE × NRGC PHASE — ALIGNMENT MATRIX

> **Key Insight:** Narrative Lifecycle (themes) maps 1:1 to NRGC (individual stocks).
> The narrative IS the macro, the NRGC phase IS the stock's position within that narrative.

### Phase Correspondence Table

```
NARRATIVE LIFECYCLE PHASE   │ NRGC PHASE   │ MARKET FEELING        │ IDEAL ACTION
────────────────────────────┼──────────────┼───────────────────────┼──────────────────────
Phase 1: Skepticism         │ NRGC Ph 0-1  │ "This won't work"     │ RESEARCH only
Phase 2: Early Adoption     │ NRGC Ph 1-2  │ "Maybe..."            │ SMALL PILOT (3-4%)
Phase 3: Thesis Building    │ NRGC Ph 2    │ "Here's the evidence" │ FULL ENTRY (10%)
Phase 4: Consensus Forms    │ NRGC Ph 2-3  │ "Everyone knows this" │ HOLD, monitor
Phase 5: Mainstream         │ NRGC Ph 3-4  │ "Obviously bullish"   │ TRAIL STOPS TIGHT
Phase 6: Crowded/Euphoria   │ NRGC Ph 4-5  │ "Can't lose"          │ REDUCE / EXIT
Phase 7: Narrative Collapse │ NRGC Ph 5-7  │ "Was a bubble"        │ SHORT or AVOID
```

### The Power Combination — Maximum Return Zones

```
SCENARIO                               │ RISK/REWARD │ EXAMPLE
───────────────────────────────────────┼─────────────┼────────────────────────────
Narrative Ph3 + NRGC Ph2               │ MAXIMUM     │ AI narrative just building,
                                       │ CONVICTION  │ stock at first breakout
                                       │ (10%+ size) │ → 3-10x potential
───────────────────────────────────────┼─────────────┼────────────────────────────
Narrative Ph2 + NRGC Ph1-2             │ HIGH        │ Narrative starting, earnings
                                       │ ASYMMETRIC  │ just inflecting
                                       │ (5-7% size) │ → early entry, 2-8x
───────────────────────────────────────┼─────────────┼────────────────────────────
Narrative Ph4 + NRGC Ph2               │ GOOD        │ Known thesis, but this stock
                                       │ LATE ENTRY  │ just got institutional attention
                                       │ (5-7% size) │ → stock catching up to theme
───────────────────────────────────────┼─────────────┼────────────────────────────
Narrative Ph5 + NRGC Ph3-4             │ DANGEROUS   │ Crowded theme, stock catching
                                       │ AVOID       │ up late — tourist stock
                                       │ (0%)        │ → arrive late, pay full price
───────────────────────────────────────┼─────────────┼────────────────────────────
Narrative Ph4-5 + NRGC Ph4-5           │ HIGH RISK   │ Both narrative AND stock
                                       │ EXIT NOW    │ fully extended
                                       │ (0%, exit)  │ → climax risk imminent
───────────────────────────────────────┼─────────────┼────────────────────────────
Narrative Ph6-7 + NRGC Ph5-7           │ SHORT SETUP │ Narrative collapsing,
                                       │             │ distribution complete
                                       │             │ → avoid or short (future)
```

### Misalignment Warning (Critical Rule)

```
IF:  Narrative Ph 5-7 (crowded/mainstream/collapsing)
AND: NRGC Ph 2-3 (stock looks like "early entry")
THEN: FALSE EARLY ENTRY TRAP
      → Stock is not early, it's LATE to a dying theme
      → The "base" is actually distribution
      → EMLS score must be penalized -10 pts
      → PRISM size multiplier = 0.25x max

Example: Metaverse stocks (2021) — stocks "breaking out" (NRGC Ph2)
         while narrative was already Phase 6 (collapsing)
```

---

## MACRO REGIME × PRISM COMPONENT WEIGHTS

> In different regimes, different PRISM components should carry more weight.
> This prevents equal-weighting in all environments.

### Base PRISM Weights

| Component | Base Weight | What It Scores |
|-----------|-------------|----------------|
| Fundamental (F) | 25% | EPS/Revenue acceleration |
| Price Structure (P) | 20% | VCP quality, base count |
| Relative Strength (RS) | 20% | RS vs SPX + sector |
| Volatility (V) | 15% | ATR compression → expansion |
| Market Regime (M) | 20% | Breadth, distribution days |

### Regime-Adjusted Weights

```
REGIME           │  F    │  P    │  RS   │  V    │  M    │ RATIONALE
─────────────────┼───────┼───────┼───────┼───────┼───────┼──────────────────────────────
Goldilocks       │  20%  │  20%  │  20%  │  15%  │  25%  │ Market health = key driver
Reflation        │  25%  │  20%  │  20%  │  15%  │  20%  │ Earnings acceleration matters
Early Recovery   │  30%  │  15%  │  20%  │  15%  │  20%  │ Fundamentals lead price early
Late Overheating │  20%  │  25%  │  25%  │  15%  │  15%  │ RS leadership = key survivor
Stagflation      │  35%  │  15%  │  20%  │  15%  │  15%  │ Earnings MUST be real, profitable
Correction       │  35%  │  10%  │  30%  │  10%  │  15%  │ Only true leaders survive
Bear             │  40%  │  10%  │  30%  │  10%  │  10%  │ Earnings + leadership = everything
Liq Melt-up      │  15%  │  25%  │  25%  │  20%  │  15%  │ Momentum and vol expansion rule
Credit Stress    │  40%  │  10%  │  25%  │  15%  │  10%  │ Balance sheet safety first
```

### What Changes in STAGFLATION (Current Regime — May 2026)

```
DEFAULT: F=25%, P=20%, RS=20%, V=15%, M=20%
STAGFLATION: F=35%, P=15%, RS=20%, V=15%, M=15%

KEY IMPLICATIONS:
→ Fundamental weight HIGHEST: stock must show real earnings, positive margins
→ Price structure LOWEST: pretty chart patterns don't survive Stagflation
→ RS still matters: relative strength = surviving in a tough environment
→ Market regime weight DOWN: macro backdrop already accounted for (it's bad)

WHAT PASSES IN STAGFLATION:
  - Profitable companies with pricing power
  - Stocks where CPI benefits them (energy, commodities, defense)
  - RS leaders that are HOLDING ATHs during market decline
  
WHAT FAILS IN STAGFLATION:
  - Hypergrowth unprofitable names (P/E = infinity)
  - Stocks relying on multiple expansion (hurt by high ERP)
  - Any company with variable-rate debt exposure
```

---

## ERP → EMLS SCORE MODIFIER

> Equity Risk Premium adjusts the "floor" for what's worth owning.
> Near-zero ERP means bonds compete with stocks — stocks must be exceptional.

### ERP Calculation

```
ERP = Earnings Yield - 10Y Risk-Free Rate
    = (1 / Forward P/E × 100) - 10Y Yield

Example (May 2026):
  Market P/E = 22x → Earnings Yield = 4.55%
  10Y Yield  = 4.47%
  ERP        = 4.55% - 4.47% = 0.08% (NEAR ZERO)
```

### ERP → EMLS Adjustment Table

```
ERP LEVEL         │ ERP %    │ EMLS MODIFIER │ EFFECT
──────────────────┼──────────┼───────────────┼────────────────────────────────
Very Healthy      │ > 3.5%   │  +5 pts       │ Stocks very cheap vs bonds
Healthy           │ 2-3.5%   │  +3 pts       │ Normal equity premium
Adequate          │ 1-2%     │  +1 pt        │ Slightly compressed
Thin              │ 0.5-1%   │   0 pts       │ No penalty, no bonus
NEAR ZERO (now)   │ 0-0.5%   │  -3 pts       │ Stocks barely beat bonds
Negative          │ < 0%     │  -5 pts       │ Bonds beat stocks risk-adjusted
Very Negative     │ < -1%    │  -8 pts       │ Significant valuation overhang

CURRENT (May 2026): ERP = 0.08% → EMLS MODIFIER = -3 pts
```

### Valuation Threshold Adjustment by ERP

```
ERP ENVIRONMENT    │ ACCEPTABLE P/E │ GROWTH REQUIRED │ P/S MAX
───────────────────┼────────────────┼─────────────────┼────────
ERP > 3% (healthy) │ Up to 35x      │ EPS > 20% YoY   │ 10x
ERP 1-3% (normal)  │ Up to 25x      │ EPS > 30% YoY   │ 7x
ERP 0-1% (thin)    │ Up to 20x      │ EPS > 40% YoY   │ 5x
ERP < 0% (negative)│ Up to 15x      │ EPS > 50% YoY   │ 3x

CURRENT (ERP=0.08%): Only buy stocks with P/E < 20x OR EPS growing > 40% YoY
→ This disqualifies most of Big Tech at current valuations
→ Keeps: High-growth semis, early-phase AI enablers with real earnings
```

---

## UNIFIED STOCK ANALYSIS WORKFLOW

### Step-by-Step: From Idea to Trade

```
STEP 1: MACRO CONTEXT CHECK (Layer 3 — Macro OS)
─────────────────────────────────────────────────
□ Current regime: [REGIME NAME] ([X]/30)
□ Macro Gate verdict: GREEN / YELLOW / RED
□ Size multiplier from macro: [0.5x / 0.75x / 1.0x]
□ ERP level: [X%] → EMLS modifier: [+/-X pts]
□ Is the stock's theme investable in this regime? YES / NO
□ Narrative Lifecycle Phase of theme: [1-7]

If Macro Gate = RED → STOP. Do not proceed.

STEP 2: NARRATIVE × NRGC ALIGNMENT (Layer 2)
─────────────────────────────────────────────
□ What narrative/theme does this stock belong to?
□ Narrative Lifecycle Phase: [1-7] → [Skepticism / Early / Building / Consensus / Mainstream / Crowded / Collapse]
□ NRGC Phase of stock: [0-7]
□ Alignment check:
    - Narrative Ph1-3 + NRGC Ph1-2 → IDEAL ZONE (proceed)
    - Narrative Ph4 + NRGC Ph2-3 → ACCEPTABLE (proceed, smaller size)
    - Narrative Ph5+ + NRGC Ph3-5 → WARNING (reduce size by 50%)
    - Narrative Ph6-7 + any NRGC → AVOID (stop here)
□ Reflexivity check: Is the narrative self-reinforcing or reversing?
□ Non-consensus opportunity? YES / NO

If Narrative/NRGC misaligned (Ph5+ narrative) → reduce or STOP.

STEP 3: PRISM 5-COMPONENT SCORING (Layer 1)
────────────────────────────────────────────
Use regime-adjusted weights (from table above)

□ [F] Fundamental: EPS growth rate [X%], Revenue QoQ [X%], Gross margin [X%]
    Score: [1-5] × weight [adjusted %] = [X]

□ [P] Price Structure: Base type [VCP/Flat/Cup], Base count [1/2/3], Quality [0-100]
    Score: [1-5] × weight [adjusted %] = [X]

□ [RS] Relative Strength: RS percentile [Xth], RS trend [up/flat/down], vs sector
    Score: [1-5] × weight [adjusted %] = [X]

□ [V] Volatility: ATR contracting? VCP compression? Volume dry-up?
    Score: [1-5] × weight [adjusted %] = [X]

□ [M] Market Regime: Breadth [X%>50DMA], Distribution days [X], Regime score
    Score: [1-5] × weight [adjusted %] = [X]

PRISM RAW SCORE: [X/25] → Normalized to [X/100]

STEP 4: EMLS SCORE CALCULATION
────────────────────────────────
EMLS Raw Score (from standard scoring)
+ ERP Modifier: [+/-X pts]
+ NRGC Phase Bonus: Ph2 = +3, Ph3 = 0, Ph4 = -3, Ph5 = -8
+ Narrative Alignment Bonus: Ph1-3 = +3, Ph4 = 0, Ph5 = -5, Ph6-7 = -10
= EMLS ADJUSTED SCORE

Tiers (adjusted):
  90+ = Hyper Leader (max size)
  80-89 = Institutional Leader (full size)
  70-79 = Emerging Leader (standard size)
  60-69 = Watchlist
  < 60 = No position

STEP 5: 7-GATE ENTRY CHAIN
───────────────────────────
GATE 0: Macro Gate          [GREEN/YELLOW/RED] → If RED: STOP
GATE 1: TD Sequential       [PASS/FAIL] → Sell Setup 7-9: FAIL
GATE 2: Health Check        [X/8] → Need >= 5: FAIL if < 5
GATE 3: Base Count          [Base X] → Base 4+: FAIL
GATE 4: PRISM               [GREEN/YELLOW/RED] → RED: FAIL
GATE 5: EMLS Adjusted Score [X] → Threshold by tier
GATE 6: Liquidity           [ADTV $XM] → < $5M: FAIL
GATE 7: BUY

STEP 6: POSITION SIZING
─────────────────────────
Base Size (from conviction tier):
  EMLS 90+ = 10% | EMLS 80-89 = 7-10% | EMLS 70-79 = 5-7%

Adjustments (multiplicative):
  × Macro Size Multiplier: [0.5x Stagflation / 0.75x Pressure / 1.0x Uptrend]
  × NRGC Phase Modifier:   [Ph1=0.5x / Ph2=1.0x / Ph3=0.85x / Ph4=0.5x]
  × Base Count Modifier:   [Base1=1.0x / Base2=1.0x / Base3=0.7x]
  × ERP Modifier:          [ERP>2%=1.0x / ERP0-2%=0.85x / ERP<0%=0.7x]
  × Health Check:          [7-8/8=1.0x / 5-6/8=0.5x]

FINAL POSITION SIZE = Base Size × All Modifiers (floor: 2%, cap: 15%)

STEP 7: TRADE SETUP
─────────────────────
Entry: Within 3% of pivot (VCP high or breakout level)
Stop: -8% from pivot (Base 3: -6%)
Target: 3:1 R/R minimum (more in NRGC Ph2 stocks)
Add: Only on profitable position, after +10%, same setup quality
```

---

## PER-REGIME STOCK SELECTION RULES

### STAGFLATION (Current — May 2026)

```
RULE SET: STAGFLATION STOCK FILTER
────────────────────────────────────
REQUIRED (all must pass):
  □ Earnings Yield > 10Y yield + 1% (minimum ERP buffer)
  □ EPS positive and growing (no "P/E = ∞" tolerated)
  □ Gross margin > 35% (pricing power exists)
  □ Revenue acceleration or at least stable (no decel)
  □ NRGC Phase 2-3 ONLY (not Ph1 hope stocks, not Ph4-5 crowded)
  □ RS > 70th percentile AND held RS during last market decline

PREFERRED CHARACTERISTICS:
  □ Real asset exposure (semis with pricing power, defense)
  □ Recurring revenue / software subscriptions
  □ Dollar-cost advantage (USD-earning in global costs)
  □ Pricing power formula: can raise prices without demand destruction

BLOCKED:
  × Any stock where: Revenue GROWTH needed to justify valuation
  × Long-duration growth (P/E > 40x, no earnings for 3+ yrs)
  × Consumer discretionary / rate-sensitive sectors
  × NRGC Ph1 "lottery tickets"
  × Base 3+ late-stage leaders

THEME RANKING FOR STAGFLATION:
  1st tier (INVEST): Photonics, Memory/HBM (profitable semis), DefenseTech
  2nd tier (SELECTIVE): AI Infrastructure (profitable), Data Center REIT
  3rd tier (AVOID): Space (no earnings), Quantum (no earnings), NeoCloud (leveraged)
```

### GOLDILOCKS

```
RULE SET: GOLDILOCKS STOCK FILTER
──────────────────────────────────
REQUIRED:
  □ EMLS > 70 (any leader qualifies)
  □ Base 1-3 (Base 4 still blocked)
  □ NRGC Phase 2-4 acceptable

ALL 14 THEMES investable
Maximum conviction: EMLS 90+ in NRGC Ph2 with Narrative Ph3
Full size multiplier: 1.0x
```

### CORRECTION (If regime worsens)

```
RULE SET: CORRECTION STOCK FILTER
────────────────────────────────────
REQUIRED:
  □ EMLS adjusted > 80 (only institutional leaders)
  □ RS holding ATH or within 5% while market declines
  □ NRGC Phase 2 ONLY (first breakout, not extended)
  □ Base 1-2 ONLY (no late-stage)
  □ Health Check >= 7/8

BLOCKED:
  × No new buys in existing positions already down > -5%
  × No NRGC Ph3-5 stocks
  × Cash target: 45-55% (current setting correct)
  × Add to losers: PROHIBITED
```

---

## REAL-TIME INTEGRATION EXAMPLE (May 2026)

### Analyzing COHR (Current Held Position)

```
STEP 1: MACRO CONTEXT
  Regime: STAGFLATION LITE (17/30)
  Macro Gate: YELLOW (size multiplier = 0.5x)
  ERP: 0.08% → EMLS modifier = -3 pts
  Theme (Photonics): Narrative Phase 3-4 (thesis building → consensus)
  VERDICT: Investable theme, but monitor for Phase 5 (crowded)

STEP 2: NARRATIVE × NRGC ALIGNMENT
  Photonics Narrative: Phase 3-4 (AI connectivity demand = real, building)
  COHR NRGC Phase: Phase 3 (institutional discovery begun, Base 3)
  Alignment: Ph3 narrative + Ph3 stock → LATE PHASE, not ideal
  Warning: Base 3 = later-stage, requires tighter management
  ADJUSTMENT: -5% size, trail stop tight

STEP 3: PRISM (Stagflation-adjusted weights: F=35%, P=15%, RS=20%, V=15%, M=15%)
  F (35%): EPS growing, profitable, photonics pricing power → 4/5 = 0.28
  P (15%): Base 3 VCP, quality score 56/100 → 3/5 = 0.09
  RS (20%): RS adequate but not leading → 3/5 = 0.12
  V (15%): Contracting base, volume dry-up → 4/5 = 0.12
  M (15%): Correction regime negative → 2/5 = 0.06
  PRISM Raw = 0.67/1.0 → normalized = 67/100 (YELLOW)

STEP 4: EMLS
  EMLS raw estimate: 69/100
  ERP modifier: -3 (ERP near zero)
  NRGC Phase 3 modifier: 0
  Narrative Ph3-4 modifier: 0 (neutral)
  EMLS ADJUSTED: 66/100 → WATCHLIST / HOLD (not add)

STEP 5: 7-GATE (for potential ADD)
  GATE 0: YELLOW (allowed with 0.5x size)
  GATE 1: TD → Sell Setup 6/9 → CAUTION (not blocked yet, watch)
  GATE 2: HC score → check needed
  GATE 3: Base 3 → allowed at 0.7x size
  GATE 4: PRISM YELLOW → allowed
  GATE 5: EMLS 66 → below 70 threshold for add → FAIL
  VERDICT: HOLD existing, do NOT add

STEP 6: CURRENT SIZING AUDIT
  Base EMLS tier: 70-79 → 5-7%
  × Macro 0.5x = 3.5%
  × NRGC Ph3 0.85x = 3.0%
  × Base3 0.7x = 2.1%
  × ERP 0.85x = 1.8%
  APPROPRIATE SIZE: ~2% (current position check)
  ACTION: If position > 3%, REDUCE to 2%. Set alert for TD Sell 9.

SUMMARY FOR COHR:
  HOLD small position (< 3%)
  Alert: TD Sell Setup 9 = immediate flag to exit
  Alert: COHR breaks 21EMA = exit
  Do NOT add until EMLS > 75 and Base resets (Base 4 next)
  Next re-evaluation: After next earnings
```

---

## QUICK REFERENCE: ONE-LINE DECISION RULES

```
Macro Gate RED  → Cash, no new positions, period.
Macro Gate YELLOW + NRGC Ph1 → PASS (only if exceptional)
Macro Gate YELLOW + NRGC Ph2 → PASS at 50% size
Macro Gate GREEN + NRGC Ph2 → FULL size
Narrative Ph5+ + any NRGC → WARN (-5 pts EMLS, halve size)
Narrative Ph6-7 + any NRGC → AVOID (stop analysis)
ERP < 0% → EMLS -5, P/E threshold tightens to 15x
ERP > 3% → EMLS +3, standard valuation thresholds
Base 3 always → -30% position size
TD Sell 9 → GATE 1 FAILS, no new entry on that ticker
NRGC Ph4+ narrative + Ph3 stock → LATE TOURIST TRAP
```

---

## INTEGRATION STATUS IN CODEBASE

```
FILES TO UPDATE (implement this framework):
─────────────────────────────────────────────────────────────
scripts/pre_compute/market_regime.py
  → Add macro_gate{} to strategy_clearance.json output
  → Add erp_modifier, investable_themes, size_multiplier

scripts/pre_compute/watchlist_engine.py
  → Apply narrative_phase_modifier to EMLS score
  → Apply erp_modifier in scoring

scripts/paper_trading/auto_trader.py
  → Check macro_gate before any BUY decision
  → Apply size_multiplier from macro_gate

memory/macro_regime_os.md
  → Reference this file for full integration rules

CLAUDE.md (master config)
  → Update 6-gate to 7-gate chain (Gate 0 = Macro Gate)
  → Add EMLS modifier table (ERP + Narrative)
  → Add per-regime PRISM weight adjustment table
```

---

## HOW TO USE THIS IN DAILY WORKFLOW

```
MORNING WORKFLOW (with Macro-PRISM-NRGC integration):

1. READ regime from session_start output
   → Macro Gate = [verdict] | Size = [Xor%] | ERP = [X%]

2. CHECK investable themes list
   → Only research stocks in approved themes for today's regime

3. FOR EACH CANDIDATE:
   a. Determine narrative phase (from macro_playbook.md Section E)
   b. Determine NRGC phase (from watchlist_engine output)
   c. Check alignment (matrix above)
   d. Run PRISM with regime-adjusted weights
   e. Apply ERP modifier to EMLS
   f. Run 7-gate chain
   g. Calculate position size with all multipliers

4. OUTPUT FORMAT (when presenting stock ideas):
   [MACRO]: STAGFLATION LITE | Gate: YELLOW | ERP: 0.08% | Modifier: -3pts
   [NARRATIVE]: Photonics Ph3 (Building) | NRGC Ph2 (Discovery)
   [ALIGNMENT]: IDEAL — narrative early, stock early → proceed
   [PRISM]: 72/100 YELLOW | Regime-adj: F=35% weight
   [EMLS]: 69 raw → 66 adjusted (ERP -3)
   [7-GATES]: 0:Y 1:P 2:P 3:P 4:Y 5:F 6:P → FAIL at Gate 5
   [SIZE]: 5% base × 0.5 macro × 1.0 NRGC × 0.85 ERP = 2.1%
   [ACTION]: WATCHLIST — need EMLS > 70 to enter
```

---

*Integration Framework v1.0 | Created: 2026-05-16*
*AlphaAbsolute Unified 3-Layer Investment System*
*References: macro_regime_os.md | macro_playbook.md | CLAUDE.md (PRISM/NRGC/EMLS sections)*
