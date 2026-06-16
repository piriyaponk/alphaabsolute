# AlphaAbsolute v2 — Adaptive Secular Momentum Investing System
## Master Configuration for Claude Code
### Version 2.0 | Redesigned 2026-05-17 | CIO: Piriyapon Kongvanich

---

## Philosophy — What This System Is

AlphaAbsolute v2 is built around one thesis:

> **"หุ้นที่วิ่ง 3–10 เด้ง ต้องการ 3 สิ่ง: right market, right stock, right moment."**

The system does NOT try to predict markets. It identifies whether conditions are RIGHT for leaders to run — and when they are, it concentrates capital in the best setups available. When conditions are wrong, it sits in cash. That is it.

**Core principle: Curate, don't score.** A composite score that averages a great story with a weak chart produces a mediocre result. Instead, every stock must PASS specific gates — no averaging, no exceptions.

**Universe:** US stocks only — S&P 500 + Nasdaq (NYSE + NASDAQ listed).
**Mandate:** Beat QQQ over a full market cycle.

---

## Two Investment Modes

Every position falls into exactly one of these modes. Never mix criteria.

### PRISM — Confirmed Leaders (Price · RS · Inflection · Structure · Megatrend)
*"Buy the strongest stocks in the strongest market."*

These are the confirmed leaders — stocks where institutions are already accumulating, fundamentals are accelerating, and the chart is acting right. The highest-probability setup in any bull market.

**PRISM Entry criteria (ALL must pass — no exceptions):**
- RS percentile vs S&P+Nasdaq benchmark: **> 70th** (3M and 6M both)
- Revenue YoY growth (latest quarter): **> 25%**
- EPS YoY growth (latest quarter): **> 25%** (or clear acceleration trend)
- Gross margin trend: **stable or expanding** (not contracting)
- Price structure: Stage 2 only — Minervini SEPA Trend Template (ALL 8 conditions):
  - S1: price > MA200 | S2: MA200 trending up | S3: price > MA150
  - S4: MA150 within **-3%** of MA200 *(stop-hunt tolerance — brief institutional shakeouts)*
  - S5: MA50 > MA150 | S6: price within **-5%** of MA50 *(stop-hunt tolerance)*
  - S7: price within 25% of 52W high (Gate 4 enforces -20%, stricter)
  - S8: price ≥ 20% above 52W low *(BOA-005 DEC-013, 2026-05-24: raised from 10% → 20%. HYPOTHESIS. Minervini exact = 30%.)*
- Chart pattern: must be forming or breaking from a recognized base
- % from 52-week high: **> -20%** (not extended down)
- 6M ADTV: **> $15M USD** (DOCX Section 5.3 — hard minimum liquidity gate)

**Position sizing:**
- Full initial position: **10% of portfolio**
- Maximum 10 Leaders simultaneously → 100% invested at full bull market
- Can pyramid up to **15%** on confirmed leaders (price extended, volume confirmed)
- Reduce to 5% on any Leader that drops from top-quartile RS

**Stop loss:** Regime-calibrated (A12 backtest 2026-05-23, N=23, one Markup period):
- **Markup regime:** -12% from entry (break-even threshold — tighter stops cause excessive false exits)
- **Distribution/Sideways regime:** -8% from entry (capital preservation priority)
- **Markdown regime:** -8% from entry (no new entries in Markdown anyway)
- Hard rule: If Stage 3/4 detected → immediate review regardless of stop level

---

### Monster Scout — 10x Before The Supercycle
*"Find the next 10-bagger before the crowd sees it."*

These are early-stage, narrative-driven, asymmetric opportunities. They may not yet have strong RS (it's early), but they have a structural growth driver and the chart is giving a clear entry signal via breakout.

**Entry criteria (ALL must pass):**
- **Breakout required — no exceptions.** Price must be at a minimum **3-month high** (63-day high). Preferred: 6-month high (126-day high) or all-time high. No buying into consolidations that haven't resolved.
- Strong narrative backing: must fit one of the 14 official themes with clear TAM and catalyst
- Base structure: Base 0 or Base 1 ONLY (no late-stage entries)
- Price structure: Early stage — either pre-discovery or entering early institutional phase
- No RS floor: RS can be anywhere. Revenue can be pre-revenue or early. What matters is trajectory and narrative, not current numbers.
- 6M ADTV: **> $3M USD** (smaller names acceptable, but must have some liquidity)

**Position sizing:**
- Initial position: **5% of portfolio** (always — regardless of conviction)
- Pyramid only after price action confirms (second breakout, pocket pivot): up to **10%**
- Hard cap per stock: **30% of portfolio** (only for super-confirmed monsters after multi-year hold)
- **Total Monster Scout bucket: ≤ 30% of portfolio at any time**
- This means: max ~3-6 Big Shot positions at 5-10% each while keeping Leaders full

**Stop loss:** -10% from breakout pivot (wider because early stage, lower liquidity).

---

## Market Regime & Cash Rules

**Cash is the most important position.** The regime determines how much of the portfolio is deployed. This is non-negotiable.

### 4-State Regime Classification

| State | SPY/QQQ Signal | Required Cash | New Entries |
|-------|---------------|--------------|-------------|
| **Markup** (bull) | Price rising, above 50DMA, breadth strong | 0-10% | Full size, both modes |
| **Distribution** (topping) | Heavy sell volume, breadth weakening, leaders fading | 40-60% | Reduce size, PRISM only |
| **Sideways/Choppy** | No clear direction, range-bound | 20-40% | Small size, high conviction only |
| **Markdown** (bear) | Price below 200DMA, breadth collapsed | 75-100% | ONLY if something passes full screen — otherwise 100% cash |

**Key rule:** In Markdown regime — if no stock passes the full PRISM or Monster Scout screen, the answer is 100% cash. Do not force positions.

**Cash enforcement mechanism:**
- A01 Market Health Engine outputs `cash_floor` and `max_deployed` each morning
- A09 Portfolio Manager reads these BEFORE any buy signal is executed
- If current deployment > `max_deployed` → A09 flags which positions to reduce FIRST

---

## TD Sequential in v2

TD Sequential is **not a hard block** in v2. It is a **size modifier**.

| Signal | Action |
|--------|--------|
| Buy Setup 9 / Countdown 13 | **Normal size** — potential price exhaustion, monitor closely. BOA-005 DEC-014 (2026-05-24): +25% bonus removed. TD Buy Setup 9 scored 8/20 as direction signal (BOA-001, rejected). |
| Neutral (no signal) | Normal size |
| Sell Setup 5-6 | -25% size — scale in slowly |
| Sell Setup 7-8 | -50% size — first tranche only, wait for reset |
| Sell Setup 9 / Countdown 10-13 | -75% size — only if ALL other technicals remain bullish |

**Rationale:** In a strong trend, Sell Setup 9 can fire many times before a reversal. Blocking entry would miss the entire trend. Instead, reduce size and scale in if price continues to confirm.

If TD says Sell Setup 9 but price is making new highs + RS climbing + volume accumulating → buy at normal size (not inflated) and pyramid as it confirms.

---

## 8 Official Buy Setup Types

Every entry must be classified as one of these. No entry without a named setup.

**BOA-005 DEC-012 (2026-05-24): Setup Tier Hierarchy — APPROVED 4/4**

| Tier | Code | Setup | Description | Entry Rule | Grade A Eligible? |
|------|------|-------|-------------|-----------|------------------|
| **Tier 1** | **VCP** | Volatility Contraction Pattern | 3+ contracting swings, volume drying up | Buy on breakout of final tight pivot | ✅ Yes |
| **Tier 1** | **BKT** | Breakout | Price clears resistance on volume ≥ 1.5× 20D average | Buy within 3% above pivot | ✅ Yes |
| **Tier 1** | **CWH** | Cup with Handle | 7+ week cup, handle ≤ 12% depth, volume dry-up | Buy on handle breakout | ✅ Yes |
| **Tier 1** | **SOS** | Sign of Strength (Wyckoff) | Strong up-day expanding volume breaking above prior consolidation high | Buy at close, stop below 50DMA | ✅ Yes |
| **Tier 2** | **PPT** | Pocket Pivot | Strong up-day volume exceeds any down-day in prior 10 days | Buy within 5% of 10DMA | Grade B max |
| **Tier 2** | **SPR** | Wyckoff Spring | Price dips below support then snaps back on volume | Buy the snap-back, stop below spring low | Grade B max |
| **Context** | **EMA** | EMA Pullback | Price pulls back to 10EMA or 21EMA in uptrend | Observe for adds — **not a new-entry signal** | ❌ No entry |
| **Context** | **VPS** | Volume Pocket Support | Price lands in High Volume Node from Volume Profile | Observe for adds — **not a new-entry signal** | ❌ No entry |
| ~~**Context**~~ | ~~**FIB**~~ | ~~Fibonacci Retracement~~ | ~~38.2% or 50% retracement with confluence~~ | **REMOVED as entry signal.** If Fib level coincides with VCP/BKT pivot, label as VCP/BKT instead. | ❌ Removed |

**Grade A requires: Tier 1 setup + all 5 PRISM gates + R:R ≥ 3.5x (extended to target_2)**
**Grade B: Tier 1 with R:R 3.0-3.5x, OR Tier 2 with R:R > 3.0x**
**Context setups (EMA/VPS): recommended_size_pct = 0 — observation only, used for pyramiding decisions**

**Minimum R:R for any actionable entry: 3:1**
- 7% stop requires ≥ 21% expected upside
- 10% stop (Big Shot) requires ≥ 30% expected upside
- If R:R < 3:1 → wait for better entry or skip

---

## Position Management Rules

### Adding to Positions (Pyramiding)
- **Rule:** Only add to WINNING positions. Never average down.
- Add at: +5% from entry (first add), +15% from entry (second add)
- Each add: 50% of original position size
- Total max: initial 10% → add 5% → max 15% for one Leader (A10 hard cap — no exceptions)
- For Big Shot: initial 5% → add 3% → max 8% before full confirmation

### Selling Rules (Priority Order)

**STOP LOSS RULES (BOA-007 DEC-017, 2026-05-24 — APPROVED 4/4):**
- **Closing price rule**: Stop is triggered ONLY when EOD close < stop_price. Intraday dips below stop = stop hunts by institutions — ignore. Only a full-day close below the level is a genuine breakdown.
- Markup regime stop: **-12%** from entry (basis = closing price, not intraday low)
- Distribution regime stop: **-8%** from entry (closing price basis)
- Hard rule: Stage 3/4 detected OR earnings gap-down -10% → IMMEDIATE regardless of stop level

**IMMEDIATE — Exit today, no debate:**
- Hard stop triggered: EOD close < stop_price (see stop levels above)
- EPS guidance cut by management
- Revenue deceleration 3 consecutive quarters (PRISM only)
- Gap down -10% on earnings miss
- Wyckoff Distribution Phase confirmed (Stage 3 or 4)
- Market enters Markdown regime AND stock shows relative weakness
- 10-week MA breach on HIGHEST weekly volume in base (Markup only — see Behavior-Based exits below)

**TODAY — Evaluate for exit:**
- RS line breaks below index line (rs_line today < rs_line 10 days ago, stock up >15%) — first warning
- TD Sell Countdown 13 on daily chart
- RS rank drops from top quartile AND stock breaks 50DMA (dual confirmation)
- RS rank falls below 50th percentile AND breaks 21EMA (dual confirmation)
- Base failure (breakout reversal — close back inside base)

**REVIEW — Monitor, reduce if confirmed:**
- RS line declining for 3 consecutive weeks
- Stock breaks 21EMA after extended run
- Volume pattern shifts (down days > up days, 3+ weeks)

**PROFIT TAKING — DUAL-REGIME SYSTEM (BOA-008 DEC-018, 2026-05-24 — APPROVED 4/4):**

*MARKUP regime — Behavior-Based Exits (hold winners until they ACT wrong):*
1. **RS line break** (first real warning): rs_line breaks below its 10-day MA AND gain > 15% → take 25%
2. **10-week MA breach**: Weekly close < 10-week SMA on volume > 1.5× weekly avg → exit 75%
3. **Climax top**: Weekly gain > 20% in single week AND volume > 2× avg AND total gain > 75% from entry → exit 75%
4. **TD Weekly Countdown 13**: Weekly chart countdown reaches 13 → exit 75%
5. **Trail stop**: Move stop to breakeven after +15% gain (only trigger for stops, not for taking profit)

*DISTRIBUTION regime — Mechanical Ladder (protect gains, priority over running winners):*
- Trail stop to breakeven after +15% gain
- Take 25% off after +25% gain
- Take 50% off after +50% gain
- Let final 25-50% run (trailing stop at 10-week MA)
- TD Countdown 13 on WEEKLY chart → exit 75%

*Monster Scout (Big Shot) — Mechanical Ladder always* (shorter expected hold, earlier stage)

**8-WEEK HOLD RULE EXCEPTION (BOA-005 DEC-011, 2026-05-24 — APPROVED 4/4):**
- If stock gains ≥ 20% in first 3 weeks (21 trading days) from entry → DO NOT take profit
- Hold for full 8 weeks (56 trading days) before first profit taking
- During 8-week hold: suppress ALL profit ladder signals AND behavior-based exits 1-4 above
- Only IMMEDIATE exits (hard stop, gap-down, Stage 3/4) override the hold window
- At end of 8 weeks: resume profit ladder from current price (not entry price)
- PRISM only. Monster Scout: maintain normal exit logic
- Rationale: +20% in 3 weeks = top 1-2% momentum outcomes → these stocks often go 100%+ (Minervini: "Never sell a stock in the first 8 weeks unless it is giving you real trouble")
- HYPOTHESIS until N≥10 qualifying events

**POSITION COUNT (BOA-006 DEC-016, 2026-05-24):**
- Maximum: 10 positions (hard cap, A10 enforces)
- **10 is NOT a target.** Hold as many positions as Grade A setups exist — if 3 pass today, hold 3. Never force entries to fill slots.
- Quality gates (Grade A required in Distribution, Grade A/B in Markup) act as the natural concentration mechanism

---

## The 12-Agent System

### Agent Directory

| ID | Name | Layer | Daily Role |
|----|------|-------|-----------|
| A01 | Market Health Engine | 0 — Foundation | Regime classification + cash floor |
| A02 | Macro Monitor | 0 — Foundation | Rates, credit, Fed direction |
| A03 | RS Universe Ranker | 1 — Intelligence | Daily RS percentile for 500+ stocks |
| A04 | Fundamental Engine | 1 — Intelligence | EDGAR XBRL, EPS/Rev acceleration |
| A05 | Theme Intelligence | 1 — Intelligence | 14 themes, HOT/WARM/WEAK heatmap |
| A06 | Leadership Curator | 2 — Curation | PRISM screen → Top 30 watchlist + Top 10 active |
| A07 | Monster Scout | 2 — Curation | Monster Scout screen → Big Shot candidates |
| A08 | Setup Scanner | 3 — Execution | 8 setup types, entry/stop/RR for each |
| A09 | Portfolio Manager | 3 — Execution | Position monitoring, exits, cash management |
| A10 | Risk Guardian | 3 — Execution | Portfolio risk, concentration, regime enforcement |
| A11 | Report Writer | 4 — Output | Daily brief + Telegram signals |
| A12 | Performance Tracker | 4 — Learning | Post-mortems, Bayesian calibration, monthly reports |

---

### Layer 0: Foundation — Runs Pre-Market, Every Day

#### A01 — Market Health Engine
**Purpose:** Determine the market regime and set cash floor for the day.

**Inputs:** SPY + QQQ daily OHLCV, breadth data (% stocks above 50DMA/200DMA), distribution day count, VIX

**Outputs:** `data/regime/market_health.json`
```json
{
  "date": "YYYY-MM-DD",
  "regime": "Markup | Distribution | Sideways | Markdown",
  "cash_floor": 0.0,
  "max_deployed": 1.0,
  "leaders_ok": true,
  "bigshot_ok": true,
  "spy_td_signal": "Neutral | SellSetup7 | SellSetup9 | BuySetup9",
  "qqq_td_signal": "...",
  "distribution_days": 3,
  "pct_above_50dma": 68.4,
  "pct_above_200dma": 71.2,
  "regime_note": "Short explanation"
}
```

**Source file:** `scripts/pre_compute/market_regime.py` (extend from v1)

**Regime factors (BOA-024 redesign, 2026-06-15):**

| Factor | Measures | Weight |
|--------|---------|--------|
| A — Price Structure | QQQ/SPY vs 50/200DMA | 25 pts |
| B — Volume Quality | Up-vol ratio 15d (Lowry/Wyckoff accumulation signal) | 20 pts |
| C — Breadth | pct_above_50dma (8) + pct_above_200dma (6) + AD line direction (6) | 20 pts |
| E — RS Leaders | Top-tier RS leader count | 10 pts |
| F — Intermarket | VIX + yield curve | 5 pts |
| G — Credit | HY spread | 5 pts |
| **Total** | | **85 pts** |

**Factor B (Volume Quality):** Primary = 15-day up-volume ratio. >= 62% = accumulation = 20 pts. < 42% = distribution = 0 pts. Distribution day count kept as audit context only — not deducted (up-vol ratio already captures the same signal). Bulkowski (N=568, 60yr): distribution day counts have zero predictive value in rising trends. No hard score clamp on dist_days.

**Regime thresholds:**
- Markup: effective_score >= 68
- Sideways: 50–67
- Distribution: 35–49
- Markdown: < 35 OR QQQ below 200DMA

**Threshold provenance (BOA-023 DEC-029 + BOA-024 DEC-031, 2026-06-15):**
- `pct_above_50dma > 60%` = **internally derived**, no published practitioner source. IBD/Minervini/Zweig/O'Neil do not use this specific number. Practitioners use 50% as the basic bull/bear neutral line. 60% is a deliberate conservative choice requiring genuine breadth. **HYPOTHESIS — re-evaluate at N≥200 trading days with forward returns by regime day (est. end-2026).**
- `distribution_days >= 4` = 1 day stricter than IBD's published "4-5 days" trigger. Deliberate conservative override.
- `cash_floor = 40%` in Distribution = **internally derived**. O'Neil/Minervini use binary approach (fully invested or fully cash). The 40% is an operational translation for a fund that must stay partially deployed. Not a published number.
- Distribution day volume comparison = **50-day average volume** (IBD standard, BOA-023 DEC-027). Prior-day volume comparison was the original implementation — changed to 50-day avg to match IBD methodology.
- **Factor B up-volume ratio thresholds (≥62=20pts, ≥57=16pts, ≥52=12pts, ≥47=7pts, ≥42=3pts) = INTERNALLY DERIVED** — NOT canonical Lowry methodology. Lowry's published signal uses discrete 90% Up-Volume day clusters. The 15-day rolling ratio is an engineering approximation of Lowry's Buying Power principle, calibrated on 1 bear episode (April 2025, N=82 days). **HYPOTHESIS — recalibrate at N≥3 distinct bear episodes.** Do not represent these thresholds as "Lowry methodology."
- **Factor B overall Spearman r = +0.015 (near-zero across all conditions)** — predictive content is regime-conditional: strong in bear periods (r=-0.540, p=0.004, N=82) and near-zero in bull/sideways. Expected behavior for a defensive signal. Disclosed per DEC-031.
- **90% Up-Volume day (canonical Lowry signal)**: logged as audit context in market_health.json when index up-volume ≥ 90% of total. Not scored — informational until N≥5 cluster events (clusters of 2+ within 10 sessions = historical bull market confirmation per Desmond/Lowry 1938-2024).
- **Mode B (Monster Scout) in Sideways regime: Grade A setups ONLY** (BOA-024 DEC-031). Grade B Monster Scout entries in Sideways prohibited — maximum damage scenario if regime is actually Distribution. PRISM entries in Sideways remain Grade A+B eligible.

**Cash floor by regime:**
- Markup: cash_floor = 0.00 (max_deployed = 1.00)
- Distribution: cash_floor = 0.40 (max_deployed = 0.60)
- Sideways: cash_floor = 0.20 (max_deployed = 0.80)
- Markdown: cash_floor = 0.75 (max_deployed = 0.25) — only deploy if stocks pass full screen

---

#### A02 — Macro Monitor
**Purpose:** Track macro backdrop — is the environment supportive for growth stocks?

**Inputs:** FRED (10Y yield, 2Y yield, HY spread, DXY)

**Outputs:** `data/regime/macro_state.json`
```json
{
  "date": "YYYY-MM-DD",
  "rate_environment": "Supportive | Neutral | Restrictive",
  "credit_stress": false,
  "yield_curve": "Normal | Flat | Inverted",
  "hy_spread_bps": 280,
  "macro_note": "1-2 sentence summary",
  "macro_modifier": 1.0
}
```

**macro_modifier rules:**
- Restrictive + credit_stress=true → macro_modifier = 0.75 (reduce all sizes 25%)
- Restrictive only → macro_modifier = 0.90
- Neutral → macro_modifier = 1.00
- Supportive → macro_modifier = 1.00 (no leverage — cap at full deployment only)

**Note:** macro_modifier never exceeds 1.0. This system does not use leverage.

**Source file:** `scripts/pre_compute/macro_monitor.py` (new)

---

### Layer 1: Intelligence — Runs Daily, After Market Close

#### A03 — RS Universe Ranker
**Purpose:** Rank every S&P+Nasdaq stock by relative strength. Foundation of PRISM screening.

**Outputs:**
- `data/rs_universe/latest.json` — full ranked list with RS percentile at 1M/3M/6M/12M
- `data/rs_universe/benchmark_distribution.json` — return distribution (written weekly)
- `data/rs_universe/benchmark_tickers.json` — full ticker list (written weekly)

**Source files:** `scripts/pre_compute/rs_benchmark.py` + `scripts/pre_compute/rs_ranker.py` (reuse from v1)

**Key output per ticker:**
```json
{
  "ticker": "NVDA",
  "rs_pct_1m": 91.2,
  "rs_pct_3m": 88.5,
  "rs_pct_6m": 85.1,
  "rs_momentum_1m_3m": 2.7,
  "rs_momentum_3m_6m": 3.4,
  "rs_source": "polygon",
  "phase": "Leader | Emerging | Recovering | Weak"
}
```

---

#### A04 — Fundamental Engine
**Purpose:** Fetch and classify fundamental acceleration — EPS, Revenue, Gross Margin.

**Source file:** `scripts/utils/data_engine.py` (reuse from v1 — EDGAR disk cache already built)

**Key output per ticker (cached 7 days):**
```json
{
  "ticker": "NVDA",
  "eps_yoy_latest": 82.4,
  "rev_yoy_latest": 78.2,
  "gm_trend": "Expanding | Stable | Contracting",
  "eps_5q_trend": [22, 35, 55, 68, 82],
  "rev_5q_trend": [28, 40, 58, 72, 78],
  "acceleration_label": "ACCELERATING | DECELERATING | STABLE | TURNAROUND | EARLY",
  "cached_date": "YYYY-MM-DD"
}
```

**Acceleration labels:**
- ACCELERATING: EPS and Rev YoY both increasing 3+ consecutive quarters
- TURNAROUND: was negative, now positive
- EARLY: first quarter of positive growth
- STABLE: growing but flat YoY rate
- DECELERATING: growth rate slowing 2+ quarters

---

#### A05 — Theme Intelligence
**Purpose:** Classify every stock by theme, rank themes HOT/WARM/WEAK.

**Source file:** `scripts/pre_compute/rs_theme_ranker.py` (reuse from v1 — 148 tickers mapped)

**Theme grading:**
- HOT: theme RS vs all themes > 75th percentile + positive news flow
- WARM: theme RS 50th–75th percentile
- WEAK: theme RS < 50th percentile or deteriorating

**HOT theme bonus in A06:** ~~Lower Mode A RS threshold from 70th to 60th percentile for stocks in HOT themes.~~ **REJECTED by backtest (2026-05-23).** RS >60% group underperformed RS >70% group by 3.9 percentage points in the only testable period (Mar–May 2026, Markup regime). RS threshold stays at 70th percentile. Do NOT implement this rule without N≥50 new data points across at least 3 regimes.

**14 Official Themes:** AI-Related, Memory/HBM, Space, Quantum Computing, Photonics, DefenseTech, Data Center, Nuclear/SMR, NeoCloud, AI Infrastructure, Data Center Infra, Drone/UAV, Robotics, Connectivity

---

### Layer 2: Curation — Runs Daily, After Layer 1

#### A06 — Leadership Curator (Mode A)
**Purpose:** Run the full PRISM screen. Output: Top 30 Watchlist + Top 10 Active Leaders.

**Decision logic:**
```
FOR each ticker in universe:
  [RS Gate]          rs_pct_3m > 70 AND rs_pct_6m > 70
  [Fundamental Gate] eps_yoy_latest > 25 AND rev_yoy_latest > 25
  [Quality Gate]     gm_trend != "Contracting"
  [Technical Gate]   pct_from_52w_high > -20  AND  adtv_6m > $15M (DOCX Section 5.3)
  [Stage Gate]       Minervini SEPA Trend Template — ALL 8 conditions (Gate 5):
                     S1: price > MA200 (strictly)
                     S2: MA200 trending up (exact: ma200_today > ma200_21d_ago)
                     S3: price > MA150 (strictly)
                     S4: MA150 within -3% of MA200 (stop-hunt tolerance)
                     S5: MA50 > MA150 (strictly)
                     S6: price within -5% of MA50 (stop-hunt tolerance)
                     S7: price within -20% of 52W high (Gate 4 enforces, stricter)
                     S8: price ≥ 10% above 52W low (DOCX preferred; Minervini exact=30%)

  [HOT theme bonus REJECTED — RS stays at 70th regardless of theme heat]

  → Top 30 Watchlist if passes 4+ of 5 gates
  → Top 10 Active if passes ALL 5 gates AND has valid setup (A08 confirms)
```

**Outputs:** `data/leadership/top30_watchlist.json` + `data/leadership/top10_active.json`

**Top 10 Active ranking:**
1. RS momentum (acceleration of RS percentile) — most important
2. Fundamental acceleration strength
3. Base quality (VCP > Flat > Cup > other)
4. Theme heat (HOT = +1 rank bonus)

---

#### A07 — Monster Scout (Mode B)
**Purpose:** Find early-stage Big Shot candidates breaking out before institutional discovery.

**Screen logic:**
```
FOR each ticker in thematic watchlist:
  [Hard Gate]      price >= 63-day high (3-month high minimum)
  [Narrative Gate] must be in 1 of 14 themes with clear catalyst
  [Stage Gate]     Base 0 or Base 1 only

  → Flag as BIG_SHOT_CANDIDATE if all gates pass
  → Rank by: breakout strength × narrative freshness × base number
  → Max 5 candidates per day
```

**Outputs:** `data/bigshot/candidates.json`

**Important:** A07 does NOT use RS as a filter. RS is context only (shows where institutional discovery stands).

---

### Layer 3: Execution — Runs Daily

#### A08 — Setup Scanner
**Purpose:** For every A06 + A07 candidate — identify exact entry, stop, target, R:R.

**Output per actionable name:**
```json
{
  "ticker": "COHR",
  "mode": "A",
  "setup_type": "VCP",
  "pivot": 95.50,
  "buy_zone": [95.50, 98.27],
  "stop": 87.86,
  "target_1": 116.0,
  "target_2": 143.0,
  "rr_ratio": 3.4,
  "td_signal": "Neutral",
  "size_modifier": 1.0,
  "recommended_size_pct": 10.0,
  "entry_note": "VCP 3-swing contraction, volume dried to 40% avg",
  "setup_grade": "A | B | C"
}
```

**Setup grading:**
- Grade A: All 5 PRISM gates + setup BKT/VCP/CWH + R:R > 4:1
- Grade B: 4+ gates + any setup + R:R > 3:1
- Grade C: Monitor only — not ready for entry

**R:R < 3:1 → skip, mark as "wait_for_better_entry"**

**TD size modifier applied here:**
- Sell Setup 7-8: recommended_size_pct × 0.5
- Sell Setup 9: recommended_size_pct × 0.25
- Buy Setup 9: recommended_size_pct × 1.25

---

#### A09 — Portfolio Manager
**Purpose:** Monitor all positions daily. Issue action signals. Enforce cash rules.

**Daily checks per position:**
1. Stop hit? → IMMEDIATE exit
2. Stage 3/4 detected? → REVIEW exit
3. RS dropping from top quartile? → REDUCE
4. TD Sell Countdown 10-13? → Take partial profit
5. +25% gain? → Take 25% profit signal
6. Cash floor breach? → Flag positions to reduce

**Cash enforcement:**
```
IF deployed > max_deployed (from A01):
  Reduce lowest-conviction positions first:
    - Leaders below Grade B
    - Positions showing RS deterioration
    - Positions closest to stop
  Issue REDUCE signal for bottom 1-2 positions
```

**Outputs:** Updated `data/portfolio/portfolio_state.json` + `data/portfolio/action_signals.json`

---

#### A10 — Risk Guardian
**Purpose:** Portfolio-level risk check. Hard limits. Devil's advocate.

**Hard limits enforced:**
- Max single position: 15% of portfolio
- Max Monster Scout bucket total: 30% of portfolio
- Max one theme: 40% of portfolio
- Min R:R before entry: 3:1
- No new entry if earnings within 5 trading days
- ADTV rule: position size ≤ 20% of 6M ADTV

**For every proposed entry, A10 answers:**
1. Does this breach any concentration limit?
2. What is worst-case drawdown if correlated positions all hit stop simultaneously?
3. What is the ADTV constraint?
4. Is there an earnings date within 5 days?
5. **"What would make this thesis completely wrong?"** (mandatory — goes into daily brief)

**Outputs:** `data/risk/risk_report.json` + approval/block verdict per entry

---

### Layer 4: Output & Learning

#### A11 — Report Writer + Telegram
**Purpose:** Daily brief + push signals to Telegram before market open.

**Daily brief structure:**
```
ALPHAABSOLUTE DAILY BRIEF [DATE]
=================================
MARKET: [Regime] | Cash Floor: [X%] | SPY TD: [signal] | QQQ TD: [signal]
MACRO: [1-sentence state]
THEMES HOT: [...] | WARM: [...]

TOP SETUPS TODAY:
1. $[TICKER] — Mode [A/B] | [Setup] | Buy: $[pivot] | Stop: $[stop] | RR: [X]x
   → [1-line thesis]
2. ...

PORTFOLIO: [N] positions | Deployed: [X%] | Cash: [Y%]
[Positions with action signals]

RISK FLAGS: [from A10 — including devil's advocate]
```

**Telegram format (mobile-optimized):**
```
🟢 SETUP: $COHR
PRISM | VCP | Pivot $95.50
Stop: $87.86 | Target: $116 | RR: 3.4x
Size: 10% (full) | Grade: A
⚡ Photonics HOT + RS #88
```

**Config in .env:** `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`

**Outputs:**
- `output/daily_brief_YYMMDD.md`
- Telegram push via bot

**Source file:** `scripts/output/report_writer.py` (new)

---

#### A12 — Performance Tracker + Learning System
**Purpose:** Close the learning loop. Every trade becomes a lesson.

**4 Learning Components:**

**1. Auto Post-Mortem (every closed trade)**
- Triggered by any position close
- Captures: entry gate scores, setup quality, what worked, what failed, signal accuracy
- Output: `data/postmortems/[TICKER]_[DATE].json` + `output/postmortems/[TICKER]_[DATE].md`

**2. Signal Calibration — Bayesian (monthly)**
- Calculate hit rate per signal from all closed trades
- Bayesian update: blend prior (theory-based) with posterior (empirical)
- Guardrail: no signal can change by more than 20% in a single month
- Output: `data/calibration/signal_weights.json` (read by A06/A08 for sizing)

**3. Monthly Performance Report**
- Attribution: which positions drove alpha vs QQQ
- Mistake classification: exit too early, stop too tight, bought extended, wrong mode, wrong regime
- Top 3 recurring mistakes with $ impact
- Output: `output/performance_YYMMDD.md`

**4. Backtest New Rules Before Adding**
- When CIO proposes new rule → A12 runs it on historical portfolio data
- If rule improves Sharpe ratio AND win rate → add
- If not → reject with data showing why
- Output: `output/backtest_[RULENAME]_YYMMDD.md`

**Backtest Findings as of 2026-05-23 (da-quant, N=23, 1 Markup regime, 38 trading days):**
- Full PRISM 5-gate screen: +23.6% excess return vs QQQ, 78% hit rate → direction confirmed
- Revenue gate (>25%) is the single most powerful gate (+21% lift on top of RS alone)
- EPS gate (+8.6% lift) — both retained
- RS >70% is near-optimal — DO NOT lower to 60% for HOT themes (tested, rejected)
- Stop calibration: -12% break-even in Markup; tighter stops cause 75% false exits in bull runs
- Stage 2 S4/S6 tolerances: S4=-3% (MA150 within -3% of MA200), S6=-5% (price within -5% of MA50)
  Rationale: institutional stop-hunts temporarily push price below key MAs before resuming uptrend.
  Strict 0% rejects valid Stage 2 stocks during controlled shakeouts. -3%/-5% = meaningful
  wiggle room without accepting genuinely broken structures (Stage 3 breakdown = much deeper).
- Stage 2 S8 threshold: ≥20% above 52W low (BOA-005 DEC-013, 2026-05-24: raised from 10% to 20%).
  HYPOTHESIS — monitor Grade A count for 60 days. 20% = confirmed recovery from low (10% was too permissive).
  Splits Minervini 30% vs old DOCX 10%. Revert to 15% if Grade A count drops >50% vs prior 20 days.
- ADTV hard minimum: $15M USD (DOCX Section 5.3). Raised from $10M to enforce institutional-grade liquidity.
  418 tickers eliminated from 1,292 universe on 2026-05-23. Grade A count unaffected (16 pass).
- Min sample required before any rule change: N≥50 closed trades across ≥3 regimes
- **CRITICAL: Log every screening result daily.** Every week without logged screening = lost backtesting data.

**BOA-022 (2026-06-13) — Pipeline Architecture: OPTION A ACTIVE, OPTION B DEFERRED**
- Active mode: Screening + Setups + Telegram only. No paper trading loop.
- Archived (do NOT run until trigger): `auto_postmortem.py`, `framework_calibrator.py`, `portfolio_manager.py`, `auto_trader.py`, `performance_tracker.py`, `risk_guardian.py`
- **Option B reactivation trigger:** `N_closed_trades >= 20 AND regimes_represented >= 2` (est. Q1–Q2 2027)
- `fwd_fill.py` (EOD) must stay active — only forward-return data collection running
- Storage: PKL files auto-purged on each runner start via `_cleanup_stale_pkl_cache()`

**Source files:**
- `scripts/pre_compute/auto_postmortem.py` (reuse from v1)
- `scripts/pre_compute/framework_calibrator.py` (reuse from v1)
- `scripts/portfolio/performance_tracker.py` (new)

---

## Pipeline Auto-Fix Rules (2026-06-14)

These rules encode bugs found in production. When fixing pipeline errors, apply these patterns before investigating further.

### HEAL_MAP — Lightweight Scripts Only
**Rule:** `health_check.py` HEAL_MAP must ONLY contain lightweight prerequisite scripts (market_regime, macro_monitor, fetch_market_breadth, fetch_earnings_calendar, update_ohlcv_daily, pipeline_metrics). Heavy curation scripts (trend_template_screener, monster_scout, setup_scanner) are NOT in HEAL_MAP — they run as proper pipeline steps a06/a07/a08. Running them via importlib in the heal phase buffers stdout and hangs the pipeline for 20+ minutes with no output.

### Tiingo 429 Sleep — 5 Seconds, Not 60
**Rule:** When Tiingo returns HTTP 429 (rate limit), sleep 5 seconds not 60. Tiingo free plan enforces per-minute limits, not hourly. 60s sleep × 38 tickers = 38-minute A03 runtime. File: `scripts/utils/data_engine.py`.

### RS Ranker — No Live API Fallback for Dead Tickers
**Rule:** `rs_ranker.py` must NOT fall through to live API if a ticker is absent from `ohlcv.db`. Absent = delisted or not in our universe. Live-fetching 30–40 ghost tickers causes Tiingo 429 cascades and adds 30+ minutes to A03 runtime. Return `{}` immediately when SQLite has no data.

### Finnhub None Guards — Revenue/GP Comparison
**Rule:** Finnhub returns `None` for revenue and gross profit on some tickers (CP, CRGY). All comparisons like `rev > 0` crash with `TypeError: '>' not supported between instances of 'NoneType' and 'int'`. Always guard with `is not None` first: `if (rev is not None and rev > 0 and gp is not None and gp > 0)`. File: `scripts/utils/data_engine.py` line ~741.

### Telegram 400 — Retry Without parse_mode
**Rule:** Telegram HTTP 400 Bad Request fires when message text contains unescaped Markdown special characters (`_`, `*`, `[`, `]`, etc.). Fix: catch 400 errors and retry WITHOUT `parse_mode` (plain text). Plain text never fails on special characters. File: `scripts/output/report_writer.py` `send_telegram()`.

### Health Probe — Stop == Entry Is Valid (Breakeven Trailing Stop)
**Rule:** After a position gains +15%, the stop is trailed to entry price (breakeven). `stop_price == entry_price` is CORRECT per CLAUDE.md profit-taking rules. `system_health_probe.py` P7 must NOT flag this as an error. Use `stop > entry * 1.001` (0.1% tolerance), not `stop >= entry`. File: `scripts/pre_compute/system_health_probe.py`.

### Health Check — RR Variance Is Regime-Aware
**Rule:** Setup Scanner floors `rr_ratio` at 3.0x minimum. In Distribution/Sideways/Markdown regimes, stocks near 52W highs have narrow headroom to target — floored RR is EXPECTED behavior, not a bug. `health_check.py` `setup_rr_variance` check must skip the warning when `regime in {"Distribution", "Sideways", "Markdown"}`.

### Health Check — grade_a_sanity Skips When Screening Is Stale
**Rule:** `health_check.py` runs BEFORE A06 (trend_template_screener). When `screening_results` table has a date < today, `grade_a_sanity` must emit SKIP, not FAIL. The check is meaningless on yesterday's stale data. After A06 writes today's data, the next health_check run will see today's date and evaluate correctly.

### Float Volumes — Fix After Every OHLCV Update
**Rule:** Polygon grouped endpoint returns volumes as floats (e.g., `1234567.0`). SQLite stores them as REAL. This causes data_quality `float_volume` FAIL. Fix: `scripts/pre_compute/fix_dates_volumes.py` runs `CAST(ROUND(volume) AS INTEGER)` on the entire ohlcv table. This script runs in the pipeline AFTER `ohlcv_update` step.

### Weekly Picker — day_filter in Runner Steps
**Rule:** Steps with `"day_filter": [6]` run Sunday only, `[5]` runs Saturday only (weekday 5=Sat, 6=Sun). Runner checks `date.today().weekday()` before executing — silently skips wrong days. `weekly_picker.py` (Sunday) saves QQQ entry price at pick time for Saturday comparison. `weekly_scorer.py` (Saturday) reads from ohlcv.db (must run after Friday EOD update). Learning curve: `data/weekly_picks/learning_curve.json`.

### OHLCV — Stale Open Price Bug (open > high by >0.5%)
**Rule:** When `open > high` by more than 0.5%, the open is a stale copy of the previous day's open (Polygon grouped endpoint bug — seen on gap-down days like 2026-05-26). Fix: `UPDATE ohlcv SET open = NULL WHERE open > high AND (open-high)/high > 0.005`. NULL open is handled by setup_scanner which falls back to `open = close`. Run `fix_dates_volumes.py` or a one-off SQL after discovering this.

### OHLCV — Adjusted Close vs Unadjusted OHLC (close < low)
**Rule:** ~41,960 rows have `close < low` because `close` is dividend/split-adjusted but `open/high/low` are unadjusted. This is a known data architecture issue — NOT a data error. MA calculations (MA50/150/200) use `close` and are internally consistent. Pattern detection using `high/low` may have slight price-level discrepancies but does not affect RS or trend template gates. Do NOT delete these rows. Fix requires re-fetching fully adjusted OHLC from Polygon (future work).

### OHLCV — NULL open/high/low from Tiingo Fallback
**Rule:** 673 tickers have rows where `open/high/low = NULL` (Tiingo returns only close+volume for some tickers). `setup_scanner.py` must filter out bars where `high IS NULL OR low IS NULL` — never use `float(r or 0)` which converts NULL to 0. Use `float(r) if r is not None else None` and skip bars missing high/low. Fallback: `open = close` when open is NULL but high/low are present.

### OHLCV Backfill — Auto-Detect Gap, Cap at 30 Days
**Rule:** `update_ohlcv_bulk.py` default `backfill_days` must be `None` (auto-detect), NOT a fixed number like 5. Auto-detect calculates the actual gap between last DB date and today, capped at 30 calendar days. A fixed `backfill_days=5` permanently loses data when the pipeline misses a weekend or holiday.

### Module-Level mkdir — Pre-Create All Dirs at Runner Startup
**Rule:** 50+ scripts call `Path.mkdir(parents=True, exist_ok=True)` at module level (top-level code, not inside `run()`). On Windows with OneDrive, `makedirs` on an already-syncing parent directory can raise `PermissionError` even with `exist_ok=True`. Fix: `run_pipeline()` calls `_ensure_dirs()` FIRST — this creates all 32 required output directories before any step is imported. After that, module-level `mkdir(exist_ok=True)` is always a safe no-op. Do NOT add individual PermissionError retries to each script — fix at the runner level instead. `_REQUIRED_DIRS` list in `scripts/runners/pre_market_runner.py` must be updated whenever a new output directory is added to any script.

### Pipeline Step Retry — Auto-Retry Transient Failures
**Rule:** `run_step()` in `pre_market_runner.py` retries transient failures automatically before counting as FAIL. Config: `MAX_RETRIES=2`, `RETRY_DELAY=5s`. Retryable errors: ConnectionError, Timeout, HTTPError, 429, 503, SSLError, PermissionError/WinError 5, "Output file not written". Non-retryable: ImportError, AttributeError, KeyError, code bugs — these retry immediately without delay and won't help. Diagnostic steps (`health_check`, `data_quality`, `data_quality_eod`) skip retry (not worth re-running). File: `scripts/runners/pre_market_runner.py` `_RETRYABLE`, `_NO_RETRY_STEPS`.

### Pipeline Telegram Alert — Always Fire (ALL PASS + PARTIAL FAILURE)
**Rule:** `_send_pipeline_alert()` fires on EVERY pipeline run, not just on failures. Three states: `[OK] ALL PASS` (all steps pass), `[WARN] PARTIAL FAILURE` (some steps fail after retries), `[ABORT]` (critical step aborted pipeline). Purpose: if no Telegram message arrives → pipeline never ran (GitHub Actions down, cron missed). This is also how CIO confirms the pipeline is healthy without opening logs. File: `scripts/runners/pre_market_runner.py` `_send_pipeline_alert()`.

### Pipeline Exit Code — sys.exit(1) on Failures
**Rule:** `pre_market_runner.py` exits with `sys.exit(1)` when `result["failed"] > 0`. This causes GitHub Actions to mark the workflow run as **failed** (red X), making failures visible in the Actions tab without needing to read logs. File: `scripts/runners/pre_market_runner.py` `__main__` block.

---

## Model Portfolio + Backtesting

The system runs a **Paper Trading Model Portfolio** alongside real account signals.

**Paper portfolio rules:**
- Grade A setups → auto-execute (no human approval)
- Grade B setups → auto-execute if regime = Markup
- All exits triggered automatically by A09 rules
- Separate state: `data/portfolio/paper_portfolio_state.json`

**Benchmark:**
- Primary: Beat QQQ on rolling 12-month basis
- Secondary: Beat QQQ on rolling 3-month basis (faster feedback)
- Stretch: Sharpe ratio > 1.0

---

## Daily Automation Schedule

```
PRE-MARKET (6:00 AM):
  1. market_regime.py           → A01 regime + cash floor
  2. macro_monitor.py           → A02 macro state

MORNING COMPUTATION (7:00 AM):
  3. rs_benchmark.py            → A03 benchmark (Fridays only)
  4. rs_ranker.py               → A03 daily RS ranking
  5. rs_theme_ranker.py         → A05 theme heatmap
  6. prewarm_analyst_cache.py   → A04 support (Tuesdays only)

CURATION (8:00 AM):
  7. trend_template_screener.py → A06 leadership screen
  8. monster_scout.py           → A07 Big Shot candidates

EXECUTION (8:30 AM):
  9. setup_scanner.py           → A08 entry/stop/RR
  10. portfolio_manager.py      → A09 position review + cash check
  11. risk_guardian.py          → A10 risk check + approve/block

OUTPUT (9:00 AM — before market open):
  12. report_writer.py          → A11 daily brief + Telegram push

POST-MARKET (4:30 PM):
  13. auto_postmortem.py        → A12 post-mortem on closed trades
  14. portfolio_manager.py      → A09 EOD price update

MONTHLY (1st of month):
  15. framework_calibrator.py   → A12 Bayesian signal weights
  16. performance_tracker.py    → A12 monthly performance report
```

**Master runner:** `scripts/runners/pre_market_runner.py`

---

## Data Source Architecture

| Priority | Source | Use Case | Cost |
|----------|--------|---------|------|
| 1st | **Polygon.io** | Price OHLCV, quotes | Free EOD / $29/mo real-time |
| 2nd | **Tiingo** | Price backup | Free / $10/mo |
| 3rd | **FMP** | Fundamentals, earnings, analyst | Free 250/day / $15/mo |
| 4th | **Finnhub** | Market cap, quotes | Free 60/min |
| 5th | **query2.finance.yahoo.com** | Emergency fallback | Free |
| Macro | **FRED** | Yields, DXY, macro | Free |

**EDGAR XBRL** — Free. 7-day disk cache in data_engine.py (built in v1).

**Required .env keys:**
```
POLYGON_API_KEY=
TIINGO_API_KEY=
FMP_API_KEY=
FINNHUB_API_KEY=
FRED_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

---

## File & Folder Structure

```
AlphaAbsolute/
├── CLAUDE.md                           ← This file (v2 operating manual)
├── .env                                ← API keys (never commit)
│
├── scripts/
│   ├── utils/
│   │   └── data_engine.py              ← Multi-source data + EDGAR cache (v1 reuse)
│   ├── pre_compute/
│   │   ├── market_regime.py            ← A01 (v1 extend)
│   │   ├── macro_monitor.py            ← A02 (new)
│   │   ├── rs_benchmark.py             ← A03 (v1 reuse)
│   │   ├── rs_ranker.py                ← A03 (v1 reuse)
│   │   ├── rs_theme_ranker.py          ← A05 (v1 reuse)
│   │   ├── trend_template_screener.py  ← A06 (v1 extend to full PRISM screen)
│   │   ├── monster_scout.py            ← A07 (new)
│   │   ├── setup_scanner.py            ← A08 (new)
│   │   ├── risk_guardian.py            ← A10 (v1 extend)
│   │   ├── framework_calibrator.py     ← A12 (v1 reuse)
│   │   ├── auto_postmortem.py          ← A12 (v1 reuse)
│   │   └── prewarm_analyst_cache.py    ← A04 support (v1 reuse)
│   ├── paper_trading/
│   │   └── auto_trader.py              ← Paper portfolio execution (v1 extend)
│   ├── portfolio/
│   │   ├── portfolio_manager.py        ← A09 (new)
│   │   └── performance_tracker.py      ← A12 (new)
│   ├── output/
│   │   └── report_writer.py            ← A11 (new)
│   └── runners/
│       └── pre_market_runner.py        ← Master daily runner (v1 extend)
│
├── data/
│   ├── regime/
│   │   ├── market_health.json          ← A01 output
│   │   └── macro_state.json            ← A02 output
│   ├── rs_universe/                    ← A03 outputs (v1 structure)
│   ├── fundamentals/                   ← A04 per-ticker cache
│   ├── themes/                         ← A05 outputs
│   ├── leadership/
│   │   ├── top30_watchlist.json        ← A06 output
│   │   └── top10_active.json           ← A06 output
│   ├── bigshot/
│   │   └── candidates.json             ← A07 output
│   ├── setups/
│   │   └── setups_today.json           ← A08 output
│   ├── portfolio/
│   │   ├── portfolio_state.json        ← Real signals (human executes)
│   │   ├── paper_portfolio_state.json  ← Auto paper trading
│   │   └── action_signals.json         ← A09 daily signals
│   ├── risk/
│   │   └── risk_report.json            ← A10 output
│   ├── postmortems/                    ← A12 per-trade JSON
│   └── calibration/
│       └── signal_weights.json         ← A12 Bayesian weights
│
├── output/
│   ├── daily_brief_YYMMDD.md           ← A11 daily brief
│   ├── postmortems/                    ← A12 markdown post-mortems
│   └── performance_YYMMDD.md           ← A12 monthly reports
│
├── memory/                             ← Knowledge base (persists across sessions)
│
└── archive/
    └── v1_knowledge_base/              ← Full v1 system archived
        └── CLAUDE_v1.md
```

---

## Bug-Fix Philosophy — Root Cause Only, No Patches

**Core principle: ถ้าแก้แล้วปัญหายังเกิดได้อีก = ยังไม่ได้แก้**

Every bug fix must eliminate the problem class permanently, not just suppress the symptom. A fix that makes the pipeline run today but fail next month under the same conditions is not a fix — it is deferred debt.

### Step 1 — Diagnose Before Touching Code

Before writing any code, answer these 3 questions:

1. **WHERE does it break?** — exact file, line number, call stack. Read the traceback fully. Do not guess.
2. **WHY does it happen?** — the real reason, not the surface error. `PermissionError` is not the cause; "makedirs runs at module import time before the directory exists" is the cause.
3. **How often / under what conditions?** — is this deterministic or intermittent? Local only or also CI? What triggers it?

If you cannot answer all 3, keep reading code until you can. Do not write a fix for a bug you do not fully understand.

### Step 2 — Fix at the Right Layer

Ask: **what is the highest layer where this problem can be prevented for ALL instances at once?**

- If 50 scripts all call `mkdir` at module level → fix in the **runner** (`_ensure_dirs`), not in each script
- If 10 scripts all retry Tiingo 429 inconsistently → fix in the **shared utility** (`data_engine.py`), not each script
- If a check runs on stale data → fix the **data flow** (when the check runs), not the check logic

**Wrong approach:** patch the specific instance that broke today  
**Right approach:** fix the pattern so every current and future instance is covered

### Step 3 — Verify the Fix Eliminates the Problem Class

After fixing, confirm:

1. **Run the full pipeline** — not just the broken step. Confirm `0 failed` end-to-end.
2. **Check that the same error cannot recur** — if the fix requires a precondition (e.g., dir must exist), verify that precondition is now guaranteed unconditionally.
3. **Check related code** — if the pattern exists elsewhere, fix those too or document why they're safe.

### Step 4 — Document in CLAUDE.md

Every root cause fix gets a rule in the **Pipeline Auto-Fix Rules** section above. The rule must state:
- What the symptom was
- What the actual root cause was
- What the fix is and where it lives
- Why the same problem cannot recur

### Anti-Patterns — Never Do These

| Anti-pattern | Why it's wrong | Right approach |
|---|---|---|
| Wrap in `try: ... except: pass` | Silently hides the real failure | Fix the cause; only catch errors you handle explicitly |
| Increase retry count | Treats symptom, not disease | Understand why it fails; retry is valid only for truly transient external failures (429, timeout) |
| Add `skip_if_missing=True` | Pretends the problem doesn't exist | Make sure the dependency exists before the step runs |
| `if error: print warning and continue` | Degrades silently over time | Either fix the error or fail loudly |
| Fix the specific instance | Pattern recurs elsewhere or next version | Fix the class — find all similar code |
| "It passed locally" | CI environment is different | Run in the target environment or mock it faithfully |
| Add a feature flag / bypass | Creates permanent complexity | Remove the bad code; don't route around it |

### The Standard: Pipeline Must Run Clean

The definition of "fixed" is: **the full pipeline runs `0 failed` in both local and CI environments, and the failure mechanism no longer exists in the codebase.**

Not "it passed this time." Not "it should be fine now." Confirmed 0 failures on a real run.

---

## Anti-Bias & No-Sycophancy Rules (ALL Agents)

1. **Position changes only when NEW DATA arrives** — never because CIO prefers a different answer
2. **If CIO proposes a stock that fails gates → state clearly it fails**, with specific gate reason
3. **No cheerleading** — "this stock looks interesting" is not analysis. Numbers only.
4. **A10 must always challenge** — devil's advocate question is mandatory, not optional
5. **CIO overrides are allowed** but must be logged. If override leads to loss → A12 tracks as "override trade"
6. **Before every BUY:** "What is the maximum loss scenario if I am completely wrong?" must be answered

---

## Command Reference

| Command | Action |
|---------|--------|
| `run daily brief` | Full pipeline → brief + Telegram |
| `analyse [TICKER]` | A03 + A04 + A08 → full analysis with entry/stop/RR |
| `screen leaders` | A06 → run full PRISM screen, output top 30 |
| `find big shots` | A07 → run Monster Scout screen, output candidates |
| `update portfolio` | A09 → review all positions, cash check, action signals |
| `risk check` | A10 → full portfolio risk assessment |
| `post-mortem [TICKER]` | A12 → write lesson learned for closed trade |
| `calibrate signals` | A12 → run Bayesian weight update |
| `monthly report` | A12 → full performance vs QQQ |
| `backtest rule: [description]` | A12 → test new rule on historical trades |
| `study [THEME]` | A05 → theme deep dive, top stocks, heatmap |
| `what went wrong with [TICKER]` | A12 → post-mortem + identify which signal failed |

---

## Key Metrics — How to Know If the System Works

**Monthly:**
- Paper portfolio vs QQQ: target > +2%/month in bull, < -5% in bear (capital preservation)
- Win rate: target > 55%
- Average winner / average loser ratio: target > 2.5:1

**Annually:**
- Beat QQQ by > 5% in bull year
- Beat QQQ by > 10% in correction year (preserving capital IS alpha)
- Sharpe ratio > 1.0

**Learning system health:**
- 1+ post-mortem per week
- Signal weights updated monthly
- No single signal weight changes by > 20% in one month (stability guardrail)

---

*v2 built on v1 research — EDGAR cache, RS benchmark, RS theme ranker, data_engine multi-source, framework calibrator, auto_postmortem all carry forward. New in v2: Two-mode investment system (Leader + Monster), Monster Scout, Setup Scanner, Portfolio Manager, Report Writer with Telegram, 4-state regime with hard cash floors, TD as size modifier not gate, 3:1 minimum R:R hard rule.*
