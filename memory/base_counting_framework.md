# AI Base Counting Framework (Leadership Base Structure Engine)
## AlphaAbsolute Memory | Created: 2026-05-16

---

## Core Philosophy

> "The best gains come from Base 1 and Base 2. Base 3 is selective. Base 4+ is dangerous. Never buy a late-stage base."

Base counting is mandatory in PRISM + NRGC. Every stock entry must include a base count check. The base number is the single biggest predictor of whether a breakout will succeed or fail.

**Historical evidence:**
- MU (2016): Base 1 breakout → 9x in 3 years
- LITE (2017): Base 1 breakout → 12x in 2 years  
- NVDA (2023): Base 1 from AI narrative start → 10x
- Stocks that fail: typically Base 4+ or no pattern (random chasing)

**Implementation:** `scripts/learning/base_counter.py`  
**Cache location:** `data/base_counts/{TICKER}.json`  
**Integrated into:** auto_trader.py (entry gate), health_check.py (details), session_start.py (portfolio display)

---

## Section 1: The Base Count System

### What Is a Base?

A base (consolidation period) forms AFTER a significant run-up. It represents institutional accumulation — smart money building positions in a controlled, range-bound pattern before the next leg higher.

**Required run-up:** >= 12% gain before a base "counts"  
**Minimum duration:** 3 weeks (15 trading days)  
**Maximum duration:** 1 year (250 trading days)  
**Maximum depth:** 60% from high to low (deeper = not a base, it's a breakdown)

### Base Count Rules

| Base | Entry Signal | Conviction | Max Position | Typical Setup |
|------|-------------|-----------|-------------|---------------|
| **Base 1** | First consolidation after initial run | **Maximum** | **10%** | Early institutional discovery — best R/R |
| **Base 2** | Second consolidation, breakout from B1 | **High** | **10%** | Sweet spot — super leaders extend here |
| **Base 3** | Third consolidation | **Selective** | **7%** | Reduce size, tighten stops to -6% |
| **Base 4+** | Late stage | **AVOID** | **3%** | Distribution risk — institutions exiting |

### Key Rule: Later = More Failed Breakouts
- Base 1: ~70% success rate on breakouts (institutional conviction, low crowding)
- Base 2: ~60% success rate (still institutional, some followers)
- Base 3: ~40% success rate (retail entering, institutional scaling out)
- Base 4+: ~20% success rate (distribution, failed breakouts, exhaustion)

---

## Section 2: The 5 Base Types

Ranked by quality and reliability:

### 1. VCP — Volatility Contraction Pattern (BEST)
**Creator:** Mark Minervini (SEPA framework)  
**Structure:** 3+ pullbacks within the base, each one shallower than the last  
**Example:** -20% swing → -12% → -7% → -4% → breakout  
**Key signals:**
- Each successive pullback contracts in depth AND volume
- Final "handle" < 10% deep (tight = institutional support)
- Volume dries up to lowest point just before breakout
- Pivot = highest point in entire base  

**Quality score:** 80-100 (A grade)

### 2. Flat Base
**Structure:** Stock moves sideways in < 15% range for 3+ weeks  
**Signal:** No selling pressure — institutions are NOT distributing  
**Quality score:** 70-90  
**Best after:** Big run-up (stock "digesting" gains on low volume)

### 3. Cup & Handle
**Structure:** Rounded U-shaped correction (15-35% depth), then a tight handle  
**Handle requirements:** < 12% deep, on declining volume, 1-4 weeks  
**Quality score:** 60-85  
**Classic formation:** 7-65 weeks total, handle in upper 15% of prior base

### 4. IPO Base
**Structure:** First consolidation within 6 weeks of IPO or first major run  
**Characteristics:** Very few price bars available, depth varies  
**Risk:** Limited historical data = harder to evaluate  
**Quality score:** Variable (0-80)

### 5. High Tight Flag
**Structure:** Stock gains 100%+ in 8 weeks or less, then consolidates < 25% deep for < 5 weeks  
**Rarity:** Extremely rare — reserved for true monster stocks  
**Action:** Maximum size if confirmed — this is the most powerful pattern  
**Quality score:** 85-100 when confirmed

---

## Section 3: Base Quality Score (0–100)

The Base Quality Score quantifies how well-formed a base is across 6 weighted factors:

| Factor | Weight | What It Measures | Ideal Signal |
|--------|--------|-----------------|-------------|
| **Volatility Contraction** | 25% | VCP quality — contracting swings | Classic VCP = 100 |
| **Volume Dry-Up** | 20% | Volume declining into base lows | Late vol < 50% of early vol = 100 |
| **Relative Strength** | 20% | Stock holding vs SPY during base | Outperforming SPY = 100 |
| **Tight Closes** | 15% | % of bars with < 1.5% close change | 70%+ tight bars = 100 |
| **Depth** | 10% | Max drawdown within base | < 10% depth = 100 |
| **Duration** | 10% | Duration in weeks | 3-10 weeks = 100 |

**Grade Scale:**
- 85-100 → A+ (Ideal) — enter at pivot with full size
- 70-84  → A  (Strong) — enter at pivot  
- 55-69  → B  (Good)  — monitor, enter only on clean breakout
- 40-54  → C  (Marginal) — skip unless other signals very strong
- < 40   → D  (Avoid) — do not enter

---

## Section 4: Pivot Detection Rules

The **pivot** = highest point within the base = the breakout trigger price.

| Level | Price | Meaning |
|-------|-------|---------|
| **Pivot** | Base high | Breakout trigger |
| **Breakout price** | Pivot × 1.03 | Buy zone entry (3% above pivot, confirmed) |
| **Buy zone** | Pivot × 0.95 to Pivot × 1.03 | Acceptable entry range |
| **Stop loss** | Pivot × 0.92 | Hard stop (-8% from pivot) |
| **Stop (Base 3)** | Pivot × 0.94 | Tighter stop (-6%) |

**Risk/Reward calculation:**
- Risk = Entry - Stop = 8-11% typically  
- Target = 20% minimum (first target), 30-50% for leaders  
- R/R ratio target: > 2:1 (preferably 3:1)

**"At pivot" zone:** Within 5% below to 3% above pivot = actionable entry window.  
Do NOT chase more than 3% above pivot — wait for next base or pullback to 10EMA.

---

## Section 5: Base Count Integration in NRGC Framework

### How Base Count Maps to NRGC Phases

| NRGC Phase | Typical Base Count | Action |
|-----------|------------------|--------|
| Phase 1 (Neglect) | Pre-Base (accumulation) | Watch only |
| Phase 2 (Early Acceleration) | Base 0 → Base 1 forming | Build watchlist, paper trade |
| Phase 3 (Institutional Discovery) | Base 1 → Base 2 | **PRIMARY ENTRY ZONE** |
| Phase 4 (Narrative Expansion) | Base 2 → Base 3 | Selective entry, smaller size |
| Phase 5 (Euphoria) | Base 3 → Base 4 | EXIT, no new entries |
| Phase 6 (Distribution) | Base 4+ | SHORT or avoid |

### Conviction Matrix

| NRGC Phase + Base Count | Conviction | Max Position |
|------------------------|-----------|-------------|
| Phase 3 + Base 1 | **MAXIMUM** | 10% |
| Phase 3 + Base 2 | **HIGH** | 10% |
| Phase 3 + Base 3 | **SELECTIVE** | 7% |
| Phase 3 + Base 4+ | **BLOCKED** | 0% |
| Phase 4 + Base 2 | **MODERATE** | 7% |
| Phase 4 + Base 3+ | **AVOID** | 0% |

---

## Section 6: VCP Detection Algorithm

**3-step algorithm:**
1. Find local price swing highs and lows (5-bar look-around)
2. Extract high→low pullback pairs
3. Check depth contraction: |depth_n| < |depth_n-1| for ALL n

**VCP quality tiers:**
- **Classic VCP**: 3+ swings, all contracting, final depth < 10% = score 100
- **Valid VCP**: 3+ swings, all contracting, final depth > 10% = score 80
- **Forming VCP**: 2 swings contracting = score 55
- **Not VCP**: < 2 swings or not contracting = score 20

**Volume contraction check:** Volume on each pullback low should be lower than the previous pullback low (drying up = no sellers willing to sell). This is scored separately in Volume Dry-Up factor.

---

## Section 7: Entry Gate Rules (auto_trader.py)

The base count gate runs AFTER the Health Check gate and BEFORE PRISM gate confirmation:

```
NRGC Phase 3 + score >= 50
  → TD Sequential Gate
  → Health Check Gate (score >= 5/8)
  → BASE COUNT GATE (new)
     → Base 4+ → BLOCK
     → Base 3 + quality < 40 → BLOCK
     → Base 3 → ALLOW, 70% size, tighter stops
     → Base 1-2 → ALLOW, full size
  → PRISM Gate
  → Cap/Score Gate
  → BUY
```

**Position size modifier from base count:**
- Base 1: 1.00x (full)
- Base 2: 1.00x (full)
- Base 3: 0.70x (reduced)
- Base 4+: 0.00x (blocked)

---

## Section 8: Health Check Integration

Base count appears as a **detail metric** in the Health Check dashboard:
- `base_count`: integer  
- `base_label`: "Base 1", "Base 2", etc.
- `base_quality`: 0-100 score
- `vcp_quality`: "Classic VCP" / "Valid VCP" / "Forming" / "Not VCP"
- `pivot`: breakout trigger price
- `at_pivot`: True/False (in buy zone right now)

This gives the CIO an instant visual of whether a stock is at the optimal buy zone.

---

## Section 9: Session Start Integration

For held positions, the session start hook shows:
```
[COHR] Base 2 | VCP Forming | Quality 72/100 | Pivot $392 | At Pivot: NO (-4.2%)
[MU]   Base 3 | Flat Base   | Quality 58/100 | Pivot $128 | SELECTIVE (-2.1%)
```

---

## Section 10: Weekly Base Count Review

Run every Monday morning before the trading week opens:

```bash
python scripts/learning/base_counter.py COHR MU NVDA LITE WDC
```

Output for each ticker:
- Base label + conviction
- Quality score + grade
- VCP status (swings, depths)
- Pivot price + distance
- Recommendation (STRONG BUY SETUP / WATCHLIST / SELECTIVE / AVOID)

---

## Section 11: The "Base Failure" Warning System

If a position breaks below its base (closes below stop price):
1. Immediately flag as "Base Failure"
2. Exit within 1-2 days (no averaging down)
3. Post-mortem: was this Base 3 or 4? Poor quality score?
4. Log to Agent 13 (Performance Review) for pattern learning

**Most common base failure causes:**
- Late-stage base (Base 3+)
- Low quality score (< 40) — depth too great, volume not contracting
- Market regime turning risk-off during base formation
- Earnings surprise (negative) during base
- RS declining during base (market knows before you do)

---

## Section 12: Quick Reference Decision Tree

```
Stock at potential buy point?
├── Check NRGC Phase
│   ├── Phase 1 or 2 → Watch only (paper trade max)
│   └── Phase 3 → Continue
├── Check Base Count
│   ├── Base 4+ → SKIP (hard block)
│   ├── Base 3 → SELECTIVE (70% size, -6% stop)
│   └── Base 1-2 → Continue
├── Check Base Quality Score
│   ├── < 40 (Base 3) → SKIP
│   ├── 40-69 → Watchlist until quality improves
│   └── 70+ → Continue
├── Check VCP
│   ├── Not VCP → Continue (other patterns OK)
│   └── Classic/Valid VCP → PREFERRED, add to priority list
├── At Pivot?
│   ├── No (> 3% above pivot) → Wait for base #2 or 10EMA pullback
│   └── Yes (within buy zone) → ENTER
└── Size = Cap_mult × Score_mult × HC_mult × Base_mult
```

---

## Implementation Files

| File | Role |
|------|------|
| `scripts/learning/base_counter.py` | Core engine — all 12 sections |
| `data/base_counts/{TICKER}.json` | Daily cache per ticker |
| `scripts/paper_trading/auto_trader.py` | Base count entry gate embedded |
| `scripts/paper_trading/health_check.py` | Base count in details panel |
| `scripts/hooks/session_start.py` | Base count for held positions |
| `memory/base_counting_framework.md` | This file |
