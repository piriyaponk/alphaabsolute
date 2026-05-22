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

### Mode A — Momentum Leadership
*"Buy the strongest stocks in the strongest market."*

These are the confirmed leaders — stocks where institutions are already accumulating, fundamentals are accelerating, and the chart is acting right. The highest-probability setup in any bull market.

**Entry criteria (ALL must pass — no exceptions):**
- RS percentile vs S&P+Nasdaq benchmark: **> 70th** (3M and 6M both)
- Revenue YoY growth (latest quarter): **> 25%**
- EPS YoY growth (latest quarter): **> 25%** (or clear acceleration trend)
- Gross margin trend: **stable or expanding** (not contracting)
- Price structure: Stage 2 only (Wyckoff accumulation or mark-up confirmed)
- Chart pattern: must be forming or breaking from a recognized base
- % from 52-week high: **> -20%** (not extended down)
- 6M ADTV: **> $10M USD** (enough liquidity to size)

**Position sizing:**
- Full initial position: **10% of portfolio**
- Maximum 10 Leaders simultaneously → 100% invested at full bull market
- Can pyramid up to **15%** on confirmed leaders (price extended, volume confirmed)
- Reduce to 5% on any Leader that drops from top-quartile RS

**Stop loss:** -8% from entry (hard). If Stage 3/4 detected → immediate review.

---

### Mode B — Monster Stock / Big Shot
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
- **Total Mode B bucket: ≤ 30% of portfolio at any time**
- This means: max ~3-6 Big Shot positions at 5-10% each while keeping Leaders full

**Stop loss:** -10% from breakout pivot (wider because early stage, lower liquidity).

---

## Market Regime & Cash Rules

**Cash is the most important position.** The regime determines how much of the portfolio is deployed. This is non-negotiable.

### 4-State Regime Classification

| State | SPY/QQQ Signal | Required Cash | New Entries |
|-------|---------------|--------------|-------------|
| **Markup** (bull) | Price rising, above 50DMA, breadth strong | 0-10% | Full size, both modes |
| **Distribution** (topping) | Heavy sell volume, breadth weakening, leaders fading | 40-60% | Reduce size, Mode A only |
| **Sideways/Choppy** | No clear direction, range-bound | 20-40% | Small size, high conviction only |
| **Markdown** (bear) | Price below 200DMA, breadth collapsed | 75-100% | ONLY if something passes full screen — otherwise 100% cash |

**Key rule:** In Markdown regime — if no stock passes the full Mode A or Mode B screen, the answer is 100% cash. Do not force positions.

**Cash enforcement mechanism:**
- A01 Market Health Engine outputs `cash_floor` and `max_deployed` each morning
- A09 Portfolio Manager reads these BEFORE any buy signal is executed
- If current deployment > `max_deployed` → A09 flags which positions to reduce FIRST

---

## TD Sequential in v2

TD Sequential is **not a hard block** in v2. It is a **size modifier**.

| Signal | Action |
|--------|--------|
| Buy Setup 9 / Countdown 13 | +25% to normal size — priority entry window |
| Neutral (no signal) | Normal size |
| Sell Setup 5-6 | -25% size — scale in slowly |
| Sell Setup 7-8 | -50% size — first tranche only, wait for reset |
| Sell Setup 9 / Countdown 10-13 | -75% size — only if ALL other technicals remain bullish |

**Rationale:** In a strong trend, Sell Setup 9 can fire many times before a reversal. Blocking entry would miss the entire trend. Instead, reduce size and scale in if price continues to confirm.

If TD says Sell Setup 9 but price is making new highs + RS climbing + volume accumulating → buy at 25% normal size and pyramid as it confirms.

---

## 8 Official Buy Setup Types

Every entry must be classified as one of these. No entry without a named setup.

| Code | Setup | Description | Entry Rule |
|------|-------|-------------|-----------|
| **BKT** | Breakout | Price clears resistance on volume ≥ 1.5× 20D average | Buy within 3% above pivot |
| **VCP** | Volatility Contraction Pattern | 3+ contracting swings, volume drying up | Buy on breakout of final tight pivot |
| **CWH** | Cup with Handle | 7+ week cup, handle ≤ 12% depth, volume dry-up | Buy on handle breakout |
| **SPR** | Wyckoff Spring | Price dips below support then snaps back on volume | Buy the snap-back, stop below spring low |
| **PPT** | Pocket Pivot | Strong up-day volume exceeds any down-day in prior 10 days | Buy within 5% of 10DMA |
| **EMA** | EMA Pullback | Price pulls back to 10EMA or 21EMA in uptrend | Buy the touch, stop below 50DMA |
| **VPS** | Volume Pocket Support | Price lands in High Volume Node (HVN) from Volume Profile | Buy at HVN midpoint |
| **FIB** | Fibonacci Retracement | Price retraces to 38.2% or 50% Fibonacci level with confluence | Buy at Fibonacci level |

**Minimum R:R for any entry: 3:1**
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

**IMMEDIATE — Exit today, no debate:**
- Hard stop: -8% from entry (Leader) or -10% (Big Shot) triggered
- EPS guidance cut by management
- Revenue deceleration 3 consecutive quarters (Mode A only)
- Gap down -10% on earnings miss
- Wyckoff Distribution Phase confirmed (Stage 3 or 4)
- Market enters Markdown regime AND stock shows relative weakness

**TODAY — Evaluate for exit:**
- TD Sell Countdown 13 on daily chart
- RS drops from top quartile AND stock breaks 50DMA
- RS rank falls below 50th percentile AND breaks 21EMA (dual confirmation)
- Base failure (breakout reversal closing back in base)

**REVIEW — Monitor, reduce if confirmed:**
- Stock breaks 21EMA after extended run
- RS declining for 3 consecutive weeks
- Volume pattern changes (down days > up days consistently)

**PROFIT TAKING:**
- Trail stop to breakeven after +15% gain
- Take 25% off after +25% gain (lock profit, let rest run)
- Take 50% off after +50% gain
- Let final 25-50% run (use wide trailing stop at 10-week MA)
- TD Countdown 13 on WEEKLY chart → exit 75% of position

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
| A06 | Leadership Curator | 2 — Curation | Mode A screen → Top 30 watchlist + Top 10 active |
| A07 | Monster Scout | 2 — Curation | Mode B screen → Big Shot candidates |
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

**Regime logic:**
- Markup: QQQ above 50DMA + 200DMA, distribution_days < 4, pct_above_50dma > 60%
- Distribution: 4+ distribution days in 25 sessions OR pct_above_50dma dropping fast below 55%
- Sideways: price between MAs, no clear direction, VIX > 18
- Markdown: QQQ below 200DMA, breadth collapsed (pct_above_50dma < 40%)

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
**Purpose:** Rank every S&P+Nasdaq stock by relative strength. Foundation of Mode A screening.

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

**HOT theme bonus in A06:** Lower Mode A RS threshold from 70th to 60th percentile for stocks in HOT themes. *(Threshold pending backtest validation — do not adjust without running A12 backtest first)*

**14 Official Themes:** AI-Related, Memory/HBM, Space, Quantum Computing, Photonics, DefenseTech, Data Center, Nuclear/SMR, NeoCloud, AI Infrastructure, Data Center Infra, Drone/UAV, Robotics, Connectivity

---

### Layer 2: Curation — Runs Daily, After Layer 1

#### A06 — Leadership Curator (Mode A)
**Purpose:** Run the full Mode A screen. Output: Top 30 Watchlist + Top 10 Active Leaders.

**Decision logic:**
```
FOR each ticker in universe:
  [RS Gate]          rs_pct_3m > 70 AND rs_pct_6m > 70
  [Fundamental Gate] eps_yoy_latest > 25 AND rev_yoy_latest > 25
  [Quality Gate]     gm_trend != "Contracting"
  [Technical Gate]   pct_from_52w_high > -20
  [Stage Gate]       stage == "Stage 2"

  HOT theme bonus: if in HOT theme → relax RS gate to 60th (pending backtest)

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
- Grade A: All 5 Mode A gates + setup BKT/VCP/CWH + R:R > 4:1
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
- Max Mode B bucket total: 30% of portfolio
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
Mode A | VCP | Pivot $95.50
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

**Source files:**
- `scripts/pre_compute/auto_postmortem.py` (reuse from v1)
- `scripts/pre_compute/framework_calibrator.py` (reuse from v1)
- `scripts/portfolio/performance_tracker.py` (new)

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
│   │   ├── trend_template_screener.py  ← A06 (v1 extend to full Mode A screen)
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
| `screen leaders` | A06 → run full Mode A screen, output top 30 |
| `find big shots` | A07 → run Mode B screen, output candidates |
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
