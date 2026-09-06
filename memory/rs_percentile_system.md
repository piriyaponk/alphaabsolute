# RS Percentile System — Deep Dive Study
## AlphaAbsolute 3-Layer Relative Strength Architecture

> "ไม่ใช่แค่หุ้นแรง แต่ต้องเป็น Leader ใน Theme ที่แรง ใน Market ที่แรง"
> Stock RS × Theme RS × Within-Theme Leadership = Maximum Conviction

---

## PART 1: ทำไม RS ที่ทำอยู่ผิด

### ปัญหาที่พบ (2026-05-16)

```
ระบบเดิม:
  Universe: 23 หุ้น watchlist เท่านั้น
  MRAM "98th percentile" = อันดับ 1 ใน 23 หุ้น
  COHR "72nd percentile" = อันดับ 7 ใน 23 หุ้น
  → ไม่รู้อะไรเลยว่า vs ตลาดจริงๆ เป็นยังไง

ปัญหาที่ 2:
  ไม่มี Theme RS → ไม่รู้ว่า Photonics แรงกว่า Memory หรือ Space
  ซื้อ Leader ใน Weak Theme = ผิดหลักการทั้งหมด

ปัญหาที่ 3:
  ไม่มี Percentile Change → ไม่รู้ว่า RS กำลังดีขึ้นหรือแย่ลง
  Inflection signal หายไปทั้งหมด
```

### IBD RS Rating ทำงานอย่างไร (มาตรฐาน)
```
1. คำนวณ 12-month price performance ของหุ้นทุกตัวในตลาด (~10,000 หุ้น)
2. ให้น้ำหนัก: 3M ล่าสุด = 40% | 9M = 20% | 6M = 20% | 3M แรก = 20%
   (เน้น recent performance มากกว่า historical)
3. Rank percentile vs ทั้งตลาด: 1-99
   99 = top 1% ของตลาด | 50 = average | 1 = bottom 1%
4. RS 90+ = institutional quality leaders
5. RS Line = Stock Price / SPY Price (visual confirmation)

ที่ Minervini ใช้: RS > 70th (entry), RS > 80th (preferred), RS > 90th (maximum)
```

---

## PART 2: The 3-Layer RS Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 3: STOCK vs MARKET                                        │
│  "Where does this stock stand in the entire universe?"           │
│                                                                  │
│  Benchmark: S&P 500 + Nasdaq 100 + mid/small growth (~600 stocks)│
│  Formula: IBD-weighted (40% recent 3M + 60% distributed)        │
│  Output: rs_market_pct (0-99) — true market percentile           │
│  Change: rs_market_pct_4w_chg (vs 4 weeks ago)                  │
└───────────────────────────┬─────────────────────────────────────┘
                            │  feeds into
┌───────────────────────────▼─────────────────────────────────────┐
│  LAYER 2: THEME vs ALL THEMES                                    │
│  "Is this theme hot or cold right now?"                          │
│                                                                  │
│  14 AlphaAbsolute Themes: AI, Memory, Space, Photonics, etc.    │
│  Theme RS = median RS of all member stocks vs market             │
│  Output: theme_vs_themes_pct (0-100) — rank vs 14 themes        │
│          theme_vs_market_pct (0-100) — absolute theme strength   │
│  Change: theme_pct_4w_chg (trend direction)                     │
└───────────────────────────┬─────────────────────────────────────┘
                            │  feeds into
┌───────────────────────────▼─────────────────────────────────────┐
│  LAYER 1: STOCK WITHIN THEME                                     │
│  "Is this the LEADER or LAGGARD within its theme?"              │
│                                                                  │
│  Compare stock vs all members of same theme                      │
│  Output: within_theme_pct (0-100) — rank within theme           │
│  Leader: within_theme_pct > 75th                                 │
│  Laggard: within_theme_pct < 35th                               │
└─────────────────────────────────────────────────────────────────┘
```

---

## PART 3: Strong Leader Formula

### The Power Combination

```python
# Strong Leader Score (0-100)
strong_leader_score = (
    0.45 * rs_market_pct +          # vs full market (most important)
    0.30 * theme_vs_themes_pct +    # theme momentum (tailwind)
    0.25 * within_theme_pct         # leadership within theme
)

# Momentum Bonus: are things IMPROVING?
# Compare current vs 4 weeks ago for all 3 layers
stock_chg    = rs_market_pct - rs_market_pct_4w_ago      # + = improving
theme_chg    = theme_vs_themes_pct - theme_pct_4w_ago    # + = theme heating up
within_chg   = within_theme_pct - within_theme_pct_4w_ago # + = gaining leadership

momentum_bonus = 0
if stock_chg > 5:   momentum_bonus += 5   # stock RS improving
if theme_chg > 5:   momentum_bonus += 5   # theme RS improving  
if within_chg > 5:  momentum_bonus += 3   # gaining leadership

# Double bonus: BOTH stock AND theme improving simultaneously
if stock_chg > 0 and theme_chg > 0:
    momentum_bonus += 5  # acceleration signal — most powerful early entry

strong_leader_score = min(99, strong_leader_score + momentum_bonus)
```

### Decision Matrix

```
STOCK vs MARKET │ THEME vs THEMES │ ACTION           │ SIZE
────────────────┼─────────────────┼──────────────────┼──────────────────
≥ 85th          │ ≥ 80th          │ MAXIMUM BUY      │ Full (10%)
≥ 72nd          │ ≥ 70th          │ STRONG BUY       │ Standard (7%)
≥ 72nd          │ 50-70th         │ BUY (stock leads)│ Reduced (5%)
50-72nd         │ ≥ 80th          │ EARLY ENTRY      │ Small (3-4%)
                │                 │ (theme hot, stock│
                │                 │  catching up)    │
50-72nd         │ 50-80th         │ WATCHLIST ONLY   │ 0%
< 50th          │ any             │ AVOID            │ 0%
any             │ < 40th          │ AVOID (weak theme│ 0%
                │                 │ — buy theme ETF  │
                │                 │ instead if any)  │
```

### The Inflection Entry (Most Powerful Signal)

```
SCENARIO: Theme is heating up (Phase 2-3), stock just crossed 50→70th
  Theme: was 55th 4W ago → now 75th (+20pp change)
  Stock: was 45th 4W ago → now 65th (+20pp change)

SIGNAL: Both crossing simultaneously = PHASE 2 INFLECTION
  → "Smart money discovering theme + buying leaders"
  → This is the BEST entry point (before mainstream recognition)

MAXIMUM CONVICTION when:
  1. Theme rs_4w_chg > +15pp (theme accelerating)
  2. Stock rs_4w_chg > +15pp (stock accelerating within theme)
  3. Stock within_theme_pct > 75th (it's the leader, not a follower)
  4. Both previously below 70th (not already crowded)
```

---

## PART 4: Theme Heatmap — 14 Themes Ranked

### Output Format (weekly)

```
THEME HEATMAP — 2026-05-16
═══════════════════════════════════════════════════════════════
RANK │ THEME          │ Theme RS │ 4W Chg │ Phase │ Leader
─────┼────────────────┼──────────┼────────┼───────┼──────────
  1  │ Memory/HBM     │   82nd   │ +18pp  │  Hot  │ MU, WDC
  2  │ Photonics      │   75th   │  +5pp  │  Warm │ COHR, LITE
  3  │ DefenseTech    │   71st   │  -3pp  │  OK   │ PLTR, CACI
  4  │ AI-Related     │   68th   │  -8pp  │  Cool │ NVDA, AMD
  5  │ Space          │   65th   │  +2pp  │  OK   │ RKLB, LUNR
  6  │ Nuclear/SMR    │   60th   │ -12pp  │  Cool │ NNE, CEG
  7  │ Data Center    │   55th   │  -5pp  │  Avg  │ VRT, EQIX
  8  │ Quantum        │   48th   │ -15pp  │  Weak │ IONQ, RGTI
 ...  │ ...            │   ...    │  ...   │  ...  │ ...
═══════════════════════════════════════════════════════════════
Hot  = theme_rs > 70th and 4w_chg > 0
Warm = theme_rs 55-70th or chg improving
Cool = theme_rs 40-55th or chg declining
Weak = theme_rs < 40th (avoid new entries)
```

### Theme Phase Classification

```
THEME PHASE    │ RS LEVEL │ RS CHANGE    │ MEANING                │ ACTION
───────────────┼──────────┼──────────────┼────────────────────────┼──────────────
EMERGING HOT   │ 40→70    │ > +15pp/4W   │ Smart money discovering │ BUY LEADERS
CONFIRMED HOT  │ > 70     │ > +5pp/4W    │ Institutional in        │ HOLD/ADD
PEAK           │ > 80     │ 0 to +5pp    │ Slowing momentum        │ HOLD TIGHT
COOLING        │ > 60     │ -5 to -15pp  │ Distribution begins     │ REDUCE
ROLLING OVER   │ 40-65    │ < -15pp/4W   │ Smart money exiting     │ EXIT
WEAK/AVOID     │ < 40     │ any          │ Theme out of favor      │ AVOID
```

---

## PART 5: Percentile Change — The Most Important Signal

### Why Change > Level

```
หุ้นที่ RS 95th อยู่ที่ 90th → ยังดี แต่กำลังแย่ลง (distribution)
หุ้นที่ RS 50th ขึ้นมาที่ 70th → กำลังดีขึ้น → ซื้อได้ก่อนใคร

First Derivative = Rate of Change → นี่คือ alpha
Second Derivative = Acceleration of Change → นี่คือ hyper-alpha

ตัวเลขที่ต้องดู:
  rs_4w_chg  = pct_now - pct_4w_ago  (short-term momentum)
  rs_13w_chg = pct_now - pct_13w_ago (medium-term trend)
  rs_slope   = direction (up/flat/down)

Signal Strength:
  rs_4w_chg > +20pp = SURGING (inflection — high alert)
  rs_4w_chg +10 to +20pp = IMPROVING (add to watchlist)
  rs_4w_chg 0 to +10pp = STABLE (hold)
  rs_4w_chg -10 to 0pp = FADING (watch stop)
  rs_4w_chg < -10pp = DETERIORATING (reduce/exit)
```

### 4 Inflection Signals (Priority Order)

```
TYPE A — DOUBLE CROSS (most powerful):
  Stock: was <50th → now >70th (in last 4 weeks)
  Theme: was <55th → now >70th (in last 4 weeks)
  = Smart money discovered BOTH stock AND theme simultaneously
  → IMMEDIATE watchlist, buy on first breakout

TYPE B — STOCK INFLECTION within HOT THEME:
  Theme: already > 70th (confirmed hot)
  Stock: crosses from <50th → >65th (new RS surge)
  = Late discovery of leader within established theme
  → BUY immediately (theme already working, stock just joining)

TYPE C — THEME INFLECTION, STOCK ALREADY STRONG:
  Stock: already > 72nd (established leader)
  Theme: crosses from <55th → >70th (theme just waking up)
  = Theme catalyst arriving for stocks we already own
  → ADD to existing position

TYPE D — WITHIN-THEME LEADERSHIP CHANGE:
  Stock: within_theme_pct crosses from <60th → >80th
  = This stock becoming the LEADER within its theme
  → Rebalance toward this stock, reduce laggards in same theme
```

---

## PART 6: Integration into Buy Chain

### Updated 7-Gate Entry Chain (RS Gates Properly Wired)

```
GATE 0: MACRO GATE              [M0 Regime + ERP]
GATE 1: TD SEQUENTIAL GATE      [No Sell Setup 7-9]
GATE 2: HEALTH CHECK GATE       [Score >= 5/8]
GATE 3: BASE COUNT GATE         [Base 4+ = BLOCKED]
GATE 4: RS 3-LAYER GATE ← NEW  [All 3 layers must pass]
  4a. Stock vs Market:   rs_market_pct >= 65th (min) / 72nd (preferred)
  4b. Theme vs Themes:   theme_vs_themes_pct >= 55th
  4c. Within Theme:      within_theme_pct >= 50th (not a laggard)
  4d. Momentum:          at least 1 of 3 must be IMPROVING (chg > 0)
GATE 5: PRISM PRICE GATE        [Technical structure GREEN]
GATE 6: EMLS SCORE GATE         [Threshold by cap tier]
GATE 7: LIQUIDITY GATE          [ADTV >= $5M]
→ BUY
```

### Position Sizing Formula (Updated)

```python
# Base from EMLS score
base_pct = 10% if emls >= 90 else 7% if emls >= 80 else 5%

# RS multipliers (all must apply)
stock_rs_mult = (
    1.20 if rs_market_pct >= 90 else   # top decile = size up
    1.00 if rs_market_pct >= 72 else   # standard pass
    0.60 if rs_market_pct >= 60 else   # below threshold = reduce
    0.00                                # below 60 = don't buy
)

theme_rs_mult = (
    1.15 if theme_vs_themes_pct >= 80 else  # hot theme = size up
    1.00 if theme_vs_themes_pct >= 60 else  # warm = standard
    0.75 if theme_vs_themes_pct >= 45 else  # cool = reduce
    0.50                                     # weak = half size
)

momentum_mult = (
    1.10 if stock_4w_chg > 10 and theme_4w_chg > 0 else  # double accelerating
    1.00                                                    # no acceleration
)

final_size = base_pct * macro_mult * stock_rs_mult * theme_rs_mult * momentum_mult
final_size = min(final_size, 15%)  # hard cap
```

---

## PART 7: Implementation Architecture

### Files to Build

```
scripts/pre_compute/
├── rs_benchmark.py         ← UPDATED: full S&P 500 + Nasdaq (~600 stocks)
├── rs_ranker.py            ← UPDATED: market-relative + within-theme + change tracking
└── rs_theme_ranker.py      ← NEW: 14-theme RS heatmap + phase classification

data/rs_universe/
├── benchmark_distribution.json  ← market RS distribution (weekly)
├── latest.json                  ← full ranked universe (daily)
├── theme_heatmap.json           ← NEW: 14-theme RS ranking (daily)
└── history/
    ├── {TICKER}_rs_history.json ← weekly RS snapshots for change tracking
    └── themes_{DATE}.json       ← NEW: theme RS history

memory/
└── rs_percentile_system.md     ← this document
```

### Data Flow

```
Daily (6:45AM):
  rs_benchmark.py   → benchmark_distribution.json (weekly only)
  rs_ranker.py      → latest.json (stock-level, market-relative)
  rs_theme_ranker.py → theme_heatmap.json (theme-level)

Weekly (Friday):
  rs_benchmark.py rebuild → fresh distribution from ~600 stocks
  Both rankers reload distribution → all percentiles recalibrated

Session Start Display:
  [RS] Theme Heatmap: Memory(82, +18pp HOT) > Photonics(75, +5pp) > ...
  [RS] Leaders: MRAM(85th vs market) | COHR(62nd vs market)
  [RS] Inflections: none today

Telegram:
  Theme RS heatmap ranked
  Per-stock: market pct + 4W change + within-theme rank + phase
```

---

## PART 8: S&P 500 + Nasdaq Benchmark Universe

### Why ~600 is enough (vs IBD's 10,000)

```
IBD uses all listed stocks because they serve retail investors
who might buy ANY stock. AlphaAbsolute only buys from 14 themes
in the top growth universe.

For our purposes:
  S&P 500 (503 stocks) = covers mega/large cap base rate
  Nasdaq 100 (100 stocks) = covers tech/growth core
  Russell 1000 Growth subset = covers mid/small growth context
  + our theme watchlist = always included

Total: ~600-650 unique stocks

Why this works:
  - Percentile is about WHERE you rank, not HOW MANY you compare against
  - A stock that beats 72% of 600 stocks likely beats ~70-73% of 10,000
  - For practical investment decisions, 600 gives statistically valid percentiles
  - Computable weekly in ~15 minutes ($0 cost)

Real limitation: small-cap micro-caps not represented
  → Acceptable because we don't invest in micro-caps anyway
```

### Benchmark Composition Target

```
Sector                  Count   Examples
─────────────────────────────────────────────────────
Technology              120     AAPL, MSFT, NVDA, AMD, AVGO...
Health Care              65     UNH, LLY, JNJ, AMGN, TMO...
Financials               75     JPM, BAC, GS, V, MA, BLK...
Consumer Discretionary   50     AMZN, TSLA, HD, MCD, NKE...
Industrials              70     CAT, HON, RTX, DE, UPS, LMT...
Communication            25     GOOGL, META, NFLX, T, VZ...
Energy                   35     XOM, CVX, COP, SLB, EOG...
Consumer Staples         35     PEP, PG, KO, COST, WMT...
Materials                25     LIN, APD, NEM, FCX, ECL...
Real Estate              25     AMT, PLD, EQIX, SPG, CCI...
Utilities                20     NEE, DUK, SO, AEP, PCG...
AlphaAbsolute themes     80     MU, COHR, RKLB, IONQ, NNE...
─────────────────────────────────────────────────────
TOTAL                  ~625
```

---

## PART 9: Correct Answers to the Questions Raised

### Q1: "ทำไมมีแค่ 200 ตัว?"
A: เพราะ hardcode ผิด — ต้องการ ~600 (S&P 500 + Nasdaq + themes)
Fix: rs_benchmark.py ใช้ full 625-ticker list

### Q2: "ต้องทำ RS Theme percentile ด้วย"
A: ถูกทั้งหมด — 14 themes ต้องมี RS ranking แยกกัน
Fix: rs_theme_ranker.py สร้างใหม่ — theme heatmap ทุกวัน

### Q3: "ถ้า stock percentile ดี + theme percentile ดี = Strong Leader"
A: นี่คือ holy grail ของ RS investing
Fix: strong_leader_score = 0.45×market + 0.30×theme + 0.25×within_theme

### Q4: "Percentile Change ด้วย"
A: Change สำคัญกว่า Level — นี่คือ alpha ที่แท้จริง
Fix: ทุก record เก็บ 4W change + 13W change

### Q5: "แบบนี้ที่ผ่านมาก็ผิดสิ"
A: ใช่ — ที่ผ่านมา RS ที่คำนวณไม่มีความหมายจริง
  - Percentile แค่ใน 23 หุ้น = ไม่ใช่ market percentile
  - ไม่มี theme RS = ซื้อ leader ใน weak theme ได้
  - ไม่มี change = miss inflection signals
  - COHR ถูกซื้อโดย PRISM gate ที่ไม่มี RS check = bug

---

## PART 10: Lesson — What RS Actually Means for Returns

### Historical Evidence (Minervini + IBD research)

```
Starting RS Percentile │ 1-Year Return (avg, winning stocks)
───────────────────────┼──────────────────────────────────────
≥ 90th                 │ +87% (top decile — monster returns)
80-90th                │ +52%
70-80th                │ +34%
60-70th                │ +18%
50-60th                │ +8%
< 50th                 │ +1% (laggards — market rate)

Theme Layer adds:
  Stock RS 72nd + Theme RS 75th → avg return 1Y: +65%
  Stock RS 72nd + Theme RS 45th → avg return 1Y: +28%
  → Theme RS multiplies stock RS returns significantly

Timing Layer (Change) adds:
  Stock at RS inflection (cross 50→70th) + Theme inflecting
  → Avg gain from entry to 1Y: +120%+ (monster stock territory)
```

### The Complete Formula for Monster Returns

```
Monster Stock =
  ① NRGC Phase 2 (institutional just discovering)
  + ② Stock RS inflecting (crossing 50→70th percentile)
  + ③ Theme RS inflecting (theme heating up, < 70th still)
  + ④ Base 1-2 forming (technical structure tight)
  + ⑤ EPS accelerating (I2 signal firing)
  + ⑥ Macro Gate OK (not in CORRECTION)

All 6 = 10x potential
4-5 = 3-5x potential
< 4 = market-rate returns
```

---

*Deep Dive Study v1.0 | Created: 2026-05-16*
*AlphaAbsolute RS Percentile Architecture*
*Next: Implement rs_theme_ranker.py + expand rs_benchmark.py to 625 stocks*
