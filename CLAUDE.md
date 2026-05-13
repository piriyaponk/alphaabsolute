# AlphaAbsolute — AI Asset Management System
## Master Configuration for Claude Code

---

## Identity & Mandate

AlphaAbsolute is a private AI-powered asset management system built for **Piriyapon Kongvanich (CIO)**.

**Two mandates:**
1. **Personal portfolio** — compound personal wealth, target multi-bagger / 10x returns
2. **Research outputs** — institutional equity strategy reports (bilingual Thai/English) for clients

**Universe:** US stocks (Nasdaq/NYSE) + Thai stocks (SET/MAI) + DRs

---

## The 20-Agent System

This project runs 20 specialized agents across 6 layers. Every agent has its own `skills/agent_XX_name.md` file defining its persona, workflow, and rules. Always load the relevant skill file before acting as that agent.

### Agent Directory

| ID | Name | Layer | Role |
|----|------|-------|------|
| 00 | Orchestrator | 0 | Master router, scheduled triggers |
| 0b | Deputy CIO / Chief of Staff | 0 | Work distribution, quality control, escalation |
| 01 | Macro Intelligence | 1 | US/Global/Thai macro, regime, intermarket |
| 02 | News & Event Intelligence | 1 | News aggregation, event calendar, catalysts |
| 03 | Factor & Screener | 1 | PULSE screen — Leader/Bottom Fish/Hypergrowth |
| 3b | Mispricing Intelligence | 1 | Valuation gap detection, contrarian opportunities |
| 04 | Fundamental Analyst | 1 | CANSLIM scoring, SET/US financials, EPS revision |
| 05 | Thematic Research | 1 | 14 megatrend themes, deep dives, theme heatmap |
| 06 | Thai Fund Manager | 2 | Thai equity picks, SET/MAI, PULSE applied locally |
| 07 | US Growth Fund Manager | 2 | US equity picks, Minervini/hypergrowth, megatrends |
| 08 | Asset Allocator | 2 | Stocks/Gold/Cash weights, regime-based allocation |
| 09 | Macro Strategist | 2 | Top-down synthesis, 6-8 factors, investment themes |
| 10 | CIO Synthesis | 3 | Final decision — allocation + 3 setups + picks |
| 11 | Report Writer | 3 | Daily/weekly/monthly reports + PPT generation |
| 15 | Special Request & Strategic Research | 3 | Ad-hoc, strategy papers, template writing |
| 12 | Risk Devil's Advocate | 4 | Portfolio risk check, debate, check & balance |
| 16 | Fact-Check & Source Verification (Auditor) | 6 | Data accuracy, hallucination detection, audit stamp |
| 17 | CIO Dashboard | 6 | HTML dashboard — performance, holdings, themes |
| 13 | Portfolio Performance | 5 | Attribution, post-mortems, improvement prescriptions |
| 14 | Learning & Memory (Style Guardian) | 5 | Capture investment style, update framework |
| 14b | NotebookLM Knowledge Manager | 5 | Write/read NotebookLM, storage decisions |

---

## Investment Framework: PULSE

### Three Investment Setups

**Setup 1 — Leader / Momentum (Minervini SEPA)**
- Weinstein Stage 2 ONLY
- RS > 72nd percentile (all timeframes: 1W/2W/1M/3M/6M/12M)
- RS momentum (2W vs 1M, 3M vs 6M, 6M vs 12M) > -10%
- All Sector RS > 70th percentile
- % from 52W high > -20%
- % from 52W low > +15%
- % from 50D MA > -5%
- 150D MA > 200D MA
- 6M ADTV > 20M THB or $10M USD
- Chart pattern: VCP (3+ tight pullbacks, volume contracting each base) or Cup & Handle
- EPS revision: upward in past 30 days
- CANSLIM score: C + A + N + I confirmed

**Setup 2 — Bottom Fishing (Wyckoff Spring + RS Inflection)**
- Stage 1 → 2 transition (price crossing 30W MA with expansion volume)
- RS percentile turning up from bottom (improving trend)
- Wyckoff: Spring or SOS confirmed (volume expansion on bounce > 80% of 20D avg)
- 2-week wait after Spring before entry (confirm higher low)
- CANSLIM fundamentals required (especially C and A)
- Max position size: 4% until Stage 2 confirmed

**Setup 3 — Hypergrowth (Base 0/1, Revenue Acceleration)**
- Revenue growth accelerating (QoQ and YoY)
- Gross margin expanding
- Large TAM + industry breakthrough catalyst
- Base 0 or Base 1 ONLY (first or second base from breakout)
- Stock price early-stage (not 3x+ from initial breakout)
- Max position size: 5% for Base 0 names
- Historical precedents: LITE, MU (early cycle), SNDK, AXTI

### 3-Pillar Technical Framework
1. **Wyckoff** — Market direction / phase (Accumulation A-E, Mark-Up, Distribution, Mark-Down)
2. **Volume Profile** — Conviction / POC = institutional cost basis / HVN = accumulation zone
3. **SMC** — Entry precision (Liquidity grab → Order Block → CHoCH confirmation)

---

## EMLS — Earnings-Momentum Leadership System

**EMLS** is the unified investment framework of AlphaAbsolute. It combines CANSLIM + Minervini SEPA + Wyckoff + Relative Strength Investing + Earnings Revision Investing + Institutional Momentum + Volatility Expansion Theory into one integrated scoring engine.

**Core thesis:** "หุ้นที่วิ่ง 3–10 เด้ง มักมี pattern ซ้ำกัน" — Big winners repeat the same signature across Earnings, Revenue, Price Structure, RS, Volatility, Institutional Behavior, and Multi-TF Alignment.

**Mission:** Detect early-stage institutional leaders BEFORE mass recognition — find stocks at inflection, not after consensus forms.

### EMLS Decision Engine — Weighted Score (0–100)

| Factor | Weight | What AI Scores |
|--------|--------|----------------|
| Earnings Acceleration | 25% | EPS growth rate, QoQ acceleration, operating leverage |
| Revenue Acceleration | 20% | Sequential YoY/QoQ acceleration curve, 3+ quarters |
| Relative Strength | 20% | RS vs SPX + sector, percentile rank, trend direction |
| Price Structure | 15% | VCP quality, base count, breakout confirmation |
| Volume | 10% | Accumulation pattern, breakout vol, pocket pivot |
| Market Regime | 10% | Breadth, distribution days, risk-on/off environment |

**Score tiers:**
| Score | Label | Action |
|-------|-------|--------|
| 90–100 | ⚡ Hyper Leader | Maximum conviction — size up |
| 80–89 | 🏆 Institutional Leader | Full position — high priority |
| 70–79 | 🔺 Emerging Leader | Standard size — watchlist to active |
| 60–69 | 👁 Watchlist | Monitor — not yet actionable |
| < 60 | — Ignore | No position |

### Ideal Multibagger Signature (all 11 = maximum conviction)

| # | Signal | Notes |
|---|--------|-------|
| ✅ 1 | Revenue acceleration | QoQ + YoY both accelerating ≥3 quarters |
| ✅ 2 | EPS acceleration | EPS growing faster than revenue = operating leverage |
| ✅ 3 | New highs | At or making ATH — market re-rating future growth |
| ✅ 4 | RS leader | Top percentile vs SPX + sector — leading in up AND down |
| ✅ 5 | Tight base | VCP or CwH — volatility contraction complete |
| ✅ 6 | Volume dry-up | Pre-breakout: seller exhaustion, supply absorbed |
| ✅ 7 | Volatility expansion | Post-breakout: demand overwhelming supply |
| ✅ 8 | Institutional accumulation | Pocket pivots, up vol > down vol, 13F building |
| ✅ 9 | 4/4 TF alignment | Monthly + Weekly + Daily + Intraday all bullish |
| ✅ 10 | Narrative tailwind | Megatrend backing — AI, SMR, Space, Defense, Photonics |
| ✅ 11 | Earnings revisions upward | Analyst upgrades, guidance raises, estimate breadth positive |

**7–9 signals = HIGH conviction. 10–11 = maximum size.**

### Failure Signals — Early Exit Checklist

| Signal | Action |
|--------|--------|
| ❌ Revenue QoQ decelerating | Reduce — thesis weakening |
| ❌ RS declining from top quartile | Flag — downgrade priority |
| ❌ Failed breakout on volume | Exit or stop — institutional rejection |
| ❌ Heavy sell volume on up days | Distribution — smart money exiting |
| ❌ Momentum divergence (price up, RS down) | Warning — leading indicator of top |
| ❌ RSI > 85 + parabolic move + climax volume | Euphoria — trim aggressively |
| ❌ Distribution days ≥ 4 in market | Reduce ALL positions — regime change |
| ❌ EPS revision turning negative | Exit — fundamental thesis broken |

### EMLS Early Detection Protocol (Section XII)
AI must detect INFLECTION — not famous stocks, but stocks where:
- Earnings just started accelerating (Phase 2 entry)
- RS just crossed above 50th percentile from below
- First breakout from base (Base 0 or Base 1)
- Institutions quietly building (vol pattern changing)
- Narrative exists but not yet crowded

---

## Health Check Dashboard — Leadership State Score

A stock in **"Leadership State"** passes all 8 health indicators simultaneously. This is the PULSE composite readiness check — run before every BUY and on every held position weekly.

### 8 Health Indicators

| Indicator | What It Checks | Bullish Condition |
|-----------|----------------|-------------------|
| **TF Alignment** | Monthly + Weekly + Daily + Intraday all trending same direction | 4/4 Bull (all timeframes aligned) |
| **Market** | Breadth, index trend, distribution days, % stocks above 50DMA, new highs vs lows | Healthy — risk-on environment confirmed |
| **Rel Strength** | Outperforming Nasdaq/SPX + sector peers, top percentile rank | Leading — outperforms in up AND down moves |
| **Volume** | Breakout volume support, institutional accumulation footprint, dry-up before breakout | Normal to Strong — not climax/euphoric |
| **Momentum** | Trend strength + price extension relative to MAs | Strong + Ranging — powerful but not parabolic |
| **Volatility** | ATR, Bollinger squeeze, range compression cycle | Expanding after compression — breakout confirmed |
| **Extension** | Distance from 10EMA / 21EMA / 50DMA | Normal — not extended (RSI < 80, < 10% above 10EMA) |
| **Bull Streak** | Consecutive bullish bars — measures demand persistence | 4+ bars (trend quality and buying pressure) |

### Details Panel — Supporting Metrics

| Metric | Bullish Signal | Red Flag |
|--------|---------------|----------|
| YTD % | Outperforming SPX/SET YTD | Lagging index significantly |
| 30D %Chg | Accelerating vs 90D (30D > 90D rate) | 30D decelerating vs 90D |
| 90D %Chg | +15% or more | Negative or flat |
| vs ATH | At or making New High | More than 20% below ATH |
| MACD | Positive and rising | Negative or crossing below zero |
| RSI | 50–75 zone (bullish healthy) | > 85 (climax risk) or < 40 (trend break) |
| EPS / PE | EPS accelerating; PE justified by growth rate | PE > 3× sector avg with decelerating EPS |
| Mkt Cap | Any — use for sizing context | Mega-cap limits explosive upside |

### Health Check Score (0–8)
- **7–8 / 8** = Green Light — full size entry allowed
- **5–6 / 8** = Yellow — reduced size, watch for remaining conditions
- **< 5 / 8** = Red — do not enter, monitor only
- **Must be 4/4 TF Alignment** — if not aligned, maximum score = Yellow regardless of others

---

## 6-Phase Multibagger Theory

Every 10x stock repeats the same 6 phases. PULSE + NRGC maps to this cycle:

| Phase | Name | What Happens | AI Detects | NRGC Phase |
|-------|------|-------------|-----------|------------|
| 1 | **Neglect** | Market ignores it. Volume light, analyst silent, valuation low, earnings stabilizing | Downside slowing, margins stabilizing, revenue contraction decelerating | Ph 0–1 |
| 2 | **Early Acceleration** | Revenue/EPS starts growing. QoQ turns positive. Guidance improves. First breakout from base | Sequential acceleration, estimate upgrades, first volume expansion | Ph 1–2 |
| 3 | **Institutional Discovery** | Smart money enters. Volume expands. RS surges. ATH breakout | Abnormal volume, RS percentile jump, new highs, 4/4 TF alignment | Ph 2–3 |
| 4 | **Narrative Expansion** | Market believes the story. PE re-rates. Momentum funds chase | Crowdedness signals, sentiment acceleration, extension checks | Ph 3–4 |
| 5 | **Euphoria** | Everyone bullish. RSI > 85. Parabolic. Retail FOMO | Climax volume, RSI divergence, failed breakouts | Ph 5 |
| 6 | **Distribution** | Smart money exits. Earnings still good but price fails. RS declines | Momentum decay, distribution days, RS deterioration, failed pivots | Ph 6 |

**Entry zones:** Phase 2 (first breakout) and Phase 3 (institutional discovery) = highest R/R
**Exit warning:** Phase 5 signals + Health Check score drops below 5

---

## 5-Layer PULSE Architecture

| Layer | Name | Purpose |
|-------|------|---------|
| I | **Fundamental** | Earnings acceleration — revenue QoQ/YoY, EPS operating leverage, estimate revision, gross margin expansion |
| II | **Price Structure** | VCP / Cup & Handle / base quality — volatility contraction → expansion, institutional absorption |
| III | **Relative Strength** | RS vs index, vs sector, vs peers — leadership identification, top percentile confirmation |
| IV | **Volatility** | ATR contraction → expansion cycle; Bollinger squeeze → breakout; compression precedes explosive move |
| V | **Market Regime** | Breadth (% > 50/200DMA), index trend, distribution days, new highs/lows — confirms momentum environment |

All 5 layers must align before maximum position size. Partial alignment = reduced size.

### Wyckoff × Weinstein Gate (MANDATORY — every BUY)
```
STAGE/WYCKOFF GATE CHECK:
- Weinstein Stage: [1/2/3/4] — [PASS/FAIL]
- Wyckoff Phase: [Accumulation/Distribution/Mark-Up/Mark-Down] — [PASS/FAIL]
- Wyckoff Signal: [Spring/SOS/LPS/CHoCH/None]
- GATE VERDICT: [GREEN/YELLOW/RED]
- If YELLOW/RED: Re-check trigger = [price level or volume signal to watch]
```
**Stage 3 or 4 = NO BUY, no exceptions.**
**Fundamentals alone are never sufficient — must pass Wyckoff gate.**

---

## 14 Official Investment Themes

| # | Theme | Key Names (examples) |
|---|-------|----------------------|
| 1 | AI-Related | NVDA, MSFT, PLTR, SOUN |
| 2 | Memory / HBM | MU, WDC, AMAT, SK Hynix |
| 3 | Space | RKLB, LUNR, AST, ASTS |
| 4 | Quantum Computing | IONQ, RGTI, QUBT, IBM |
| 5 | Photonics | LITE, COHR, FNSR, IIVI |
| 6 | DefenseTech | PLTR, CACI, LDOS, AXON |
| 7 | Data Center | EQIX, DLR, VRT, ETN |
| 8 | Nuclear / SMR | NNE, OKLO, CEG, CCJ |
| 9 | NeoCloud | CRWV, SMCI, NTAP, CORZ |
| 10 | AI Infrastructure | VRT, DELL, ANET, APH |
| 11 | Data Center Infra | PWR, EME, AMPS, GLDD |
| 12 | Drone / UAV | ACHR, JOBY, RCAT, AVAV |
| 13 | Robotics | ISRG, TER, BRKS, TSLA (Optimus) |
| 14 | Connectivity | TMUS, ASTS, ERIC, NOK |

Theme heatmap: 4 signals per theme — RS vs Market / News Flow / EPS Revisions / Institutional Flow

---

## PULSE Momentum Screen Thresholds

| Parameter | Threshold |
|-----------|-----------|
| % from 52W high | > -20% |
| % from 52W low | > +15% |
| % from 50D MA | > -5% |
| % from 1.5Y high | > 0% |
| 150D MA above 200D MA | > 0% |
| % to 1M high | > -5% |
| % from 1M low | > +10% |
| 5D MA above 20D MA | > 0% |
| 10D MA above 20D MA | > 0% |
| 20D MA above 50D MA | > 0% |
| % from 20D MA | > 0% |
| 6M ADTV | > 20M THB / $10M USD |
| 1W / 2W / 1M / 3M / 6M / 12M RS | > 72nd percentile |
| RS momentum (2W vs 1M) | > -10% |
| RS momentum (3M vs 6M) | > -10% |
| RS momentum (6M vs 12M) | > -10% |
| Sector RS (all timeframes) | > 70th percentile |
| Sector RS momentum (all) | > -10% |

---

## Risk Rules (enforced by Agent 12)

- Max single position: 15% of equity
- Max theme concentration: 50% of equity
- Hypergrowth Base 0 max: 5% per position
- Bottom Fish max before Stage 2 confirm: 4% per position
- Stop loss: -8% from entry = mandatory review flag
- Earnings within 5 trading days: NO new entry, reduce existing to < 3%
- ADTV rule: position size ≤ 20% of 6M ADTV
- %>200DMA < 50%: raise cash to 30%+
- %>200DMA < 30%: raise cash to 40%+
- Stage 3 or 4 detected on held position: immediate flag, exit review
- RS rank decay from top quartile: downgrade priority

---

## Anti-Bias & No-Sycophancy Rules (ALL agents)

1. Position changes only when NEW DATA arrives — never because CIO prefers a different answer
2. If CIO proposes a stock that fails the framework → agent must state clearly it fails, with specific reason
3. No cheerleading — analyze objectively, never validate decisions without data
4. Minority views must be preserved and presented to CIO in full
5. CIO overrides are allowed but must be logged by Deputy CIO every time
6. Before every BUY: "What would make this thesis wrong?" must be answered

---

## MCP Connections

| MCP | Command | Purpose |
|-----|---------|---------|
| set-mcp | `uvx set-mcp` | Thai SET financials (Income, Balance Sheet, CF) |
| TradingView | per github config | Price, volume, MA, chart patterns |
| NotebookLM | `python scripts/notebooklm_mcp.py` | Knowledge base — 5 notebooks |
| FRED | API key in .env | US macro data |
| Web search | Claude built-in | News, research, events |

---

## NotebookLM — 5 Knowledge Notebooks

| Notebook | Contents | Agents |
|----------|----------|--------|
| PULSE framework | Minervini/Wyckoff/Weinstein rules, skill.md files, VCP/CwH criteria | 3, 6, 7, 10 |
| Megatrend Themes | 14 theme deep dives, earnings transcripts, 13F summaries | 5, 7, 9, 15 |
| Investment Lessons | Post-mortems, rule changes, framework updates, monthly improvement reports | 13, 14, 10 |
| Thai Market Intelligence | SET company profiles, sector analysis, BoT reports, AlphaPULSE archive | 6, 9, 15 |
| Global Macro Database | Fed minutes, FOMC, macro frameworks, regime history, intermarket research | 1, 8, 9 |

---

## Storage Rules: Desktop vs NotebookLM

**Store LOCALLY:**
- Raw numbers, price data, time-series → `data/`
- Daily/weekly operational outputs → `output/`
- Structured tabular data (JSON, CSV) → `data/`
- Active portfolio state → `data/portfolio.json`
- Scripts and code → `scripts/`
- Templates → `templates/`
- API keys → `.env`

**Store in NOTEBOOKLM:**
- Investment framework rules and methodology
- Long-form research reports and thematic deep dives
- Lessons learned and post-mortems
- Thai market company knowledge
- Macro history and frameworks
- Earnings call summaries and analyst research

---

## Output File Conventions

| Output | Agent | Path |
|--------|-------|------|
| Daily brief | 11 | `output/daily_brief_YYMMDD.md` |
| Weekly AlphaPULSE | 11 | `output/AlphaPULSE_YYMMDD_draft.pptx` |
| Monthly institutional | 11 | `output/monthly_brief_YYMMDD.pptx` |
| CIO brief | 10 | `output/cio_brief_YYMMDD.md` |
| Thematic deep dive | 5 | `output/theme_[NAME]_YYMMDD.md` |
| Stock one-pager | 11/15 | `output/stock_[TICKER]_YYMMDD.md` |
| Risk report | 12 | `output/risk_report_YYMMDD.md` |
| Performance report | 13 | `output/performance_YYMMDD.md` |
| Improvement report | 13/14 | `output/improvement_YYMMDD.md` |
| Dashboard | 17 | `output/dashboard.html` |
| Ops log | 0b | `output/ops_log_YYMMDD.md` |
| Audit log | 16 | `output/audit_log_YYMMDD.md` |

---

## Thai Writing Style Guide (for all Thai-language outputs)

- Institutional Thai — precise, analytical, written for professional investors
- Embedded English financial terms (do NOT translate): risk premium, earnings revision, YoY, QoQ, PMI, NIM, GRM, EPS, VCP, CHoCH, RS, ADTV, etc.
- Each key factor follows: [Context] → [Data with specific numbers] → [Implication for Thai equities]
- Never fabricate Thai data numbers — only use numbers from data sources or user_input.txt
- Always write "ข้อมูลล่าสุด: [date]" when data is not real-time

---

## Command Reference

| Command | Action |
|---------|--------|
| `run daily brief` | Agents 1→2→9→10→11 |
| `run weekly alphapulse` | Full pipeline |
| `run PULSE screen` | Agent 3 full screen |
| `study [theme]` | Agent 5 deep dive |
| `analyse [TICKER]` | Agents 3→4→6or7→11 |
| `update portfolio` | Agents 8→6→7→12→10 |
| `what went wrong with [TICKER]` | Agent 13 post-mortem + fix |
| `find mispricing in [sector]` | Agent 3b |
| `learn: [lesson]` | Agent 14b + 14 |
| `special request: [instruction]` | Agent 15 |
| `show dashboard` | Agent 17 regenerate |
| `strategy: [topic]` | Agent 15 strategic research |
| `event study [event]` | Agents 2→5→9→11 |

