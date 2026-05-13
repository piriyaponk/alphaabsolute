# Earnings–Momentum Leadership System (EMLS)
## Master Framework v1.0 — AI Agent Training Document
*Integrated with NRGC + PULSE | AlphaAbsolute System*
*Created: 2026-05-13 | For upload to NotebookLM: AlphaAbsolute — PULSE Framework*

---

## คำนำ — ทำไมต้องมี EMLS

EMLS (Earnings–Momentum Leadership System) คือ **unified scoring engine** ที่ผสาน 7 ระบบการลงทุนเข้าด้วยกัน:

| ระบบต้นทาง | สิ่งที่นำมาใช้ |
|-----------|-------------|
| **CANSLIM** (O'Neil) | Earnings acceleration filter, quarterly growth, institutional sponsorship |
| **SEPA** (Minervini) | Trend template, VCP, extension risk, stop rules |
| **Wyckoff** | Phase identification, volume analysis, spring/SOS detection |
| **Relative Strength Investing** | RS percentile, three-layer leadership, RS momentum ratio |
| **Earnings Revision Investing** | EPS revision breadth, analyst upgrade tracking, guidance direction |
| **Institutional Momentum** | Volume footprint, pocket pivot, accumulation pattern |
| **Volatility Expansion Theory** | Compression → Expansion cycle, ATR, Bollinger squeeze |

**Core thesis:** "หุ้นที่วิ่ง 3–10 เด้ง มักมี pattern ซ้ำกัน"

ทั้งใน Earnings, Revenue, Price Structure, Relative Strength, Volatility, Institutional Behavior และ Multi-TF Alignment — ซ้ำกันเสมอ

**Mission ของ AI Agent:** Detect early-stage institutional leaders **BEFORE mass recognition** — หาหุ้นที่อยู่ที่ inflection point ก่อนตลาด fully price in

---

## การเชื่อมโยงกับ NRGC + PULSE

```
NRGC = WHY layer  → ทำไมหุ้นตัวนี้ถึงมีโอกาสเป็น multi-bagger (narrative + revenue cycle)
PULSE = WHEN layer → เมื่อไหร่ที่เข้า (price structure + RS + earnings)
EMLS  = HOW layer  → วัดและให้คะแนนความพร้อมแบบ systematic (0–100 score)
```

**Decision rule:** 
- NRGC Phase 2-3 + PULSE ผ่าน + EMLS ≥ 80 = **Maximum conviction — size เต็ม**
- EMLS < 60 = ไม่ act ไม่ว่า narrative จะดีแค่ไหน

---

## I. CORE THEORY — ทฤษฎีหลักของระบบ

### 1.1 Discounting Machine

ตลาดหุ้นเป็น "ระบบ discounting machine" — ราคาจะขึ้นก่อน:
- ข่าว
- EPS จริง
- Analyst upgrade
- คนทั่วไปเห็น narrative

ดังนั้น **Price + Relative Strength + Earnings acceleration** มักเกิดก่อนหุ้นวิ่งใหญ่

AI ต้องอ่านสัญญาณเหล่านี้ ไม่ใช่รอข่าว

### 1.2 Six-Phase Multibagger Cycle

หุ้น multibagger ส่วนใหญ่วิ่งผ่าน 6 phases ที่ซ้ำกัน:

| Phase | ชื่อ | ลักษณะ | NRGC Map | AI ต้อง Detect |
|-------|------|--------|----------|---------------|
| 1 | **Neglect** | Volume เบา, analyst ไม่พูดถึง, valuation ต่ำ, earnings stabilize | Ph 1 | Downside slowing, margin stabilizing, revenue contraction decelerating |
| 2 | **Early Acceleration** | YoY growth เร่ง, QoQ กลับบวก, guidance ดีขึ้น, gross margin improve, breakout from base แรก | Ph 2 | Sequential acceleration, estimate upgrades, first volume expansion |
| 3 | **Institutional Discovery** | Volume expansion, RS surges, ATH breakout, volatility expand | Ph 3 | Abnormal volume, RS percentile jump, new highs, 4/4 TF alignment |
| 4 | **Narrative Expansion** | Analyst upgrade, PE rerating, momentum funds chase, media coverage | Ph 4 | Crowdedness signals, sentiment acceleration, extension checks |
| 5 | **Euphoria** | ทุกคน bullish, RSI > 85, parabolic, retail FOMO | Ph 5-6 | Climax volume, RSI divergence, failed breakouts |
| 6 | **Distribution** | Smart money ออก, earnings ยังดีแต่ราคาลง, RS decline | Ph 6-7 | Momentum decay, distribution days, RS deterioration |

**Entry zones ที่ดีที่สุด:** Phase 2 (first breakout) และ Phase 3 (institutional discovery)
**Exit warning:** Phase 5 signals + Health Check score ลดต่ำกว่า 5/8

---

## II. SYSTEM ARCHITECTURE — 5 Layers

EMLS ประเมิน 5 layers พร้อมกัน — **ทุก layer ต้องผ่านก่อน size เต็ม:**

| Layer | ชื่อ | หน้าที่ | Weight ใน EMLS Score |
|-------|------|--------|---------------------|
| I | **Fundamental** | Earnings acceleration, revenue QoQ/YoY, EPS operating leverage, estimate revision, gross margin | 45% |
| II | **Price Structure** | VCP / Cup & Handle / base quality, volatility contraction → expansion, institutional absorption | 15% |
| III | **Relative Strength** | RS vs index, sector, peers — leadership identification, top percentile | 20% |
| IV | **Volatility** | ATR contraction → expansion cycle, Bollinger squeeze → breakout, compression precedes explosive move | 10% |
| V | **Market Regime** | Breadth (% > 50/200DMA), index trend, distribution days, new highs/lows | 10% |

---

## III. FUNDAMENTAL LAYER — หัวใจของระบบ

หุ้น 10 เด้งแทบทั้งหมดมี **Earnings acceleration ก่อน** เสมอ

### 3.1 Revenue Acceleration

**A) YoY Revenue Growth — Rate of Change**

ไม่ใช่แค่ระดับ revenue แต่คือ **second derivative** — อัตราการเปลี่ยนแปลงของอัตราการเติบโต

ตัวอย่าง bullish sequential curve:
```
Q1: Revenue YoY = +15%
Q2: Revenue YoY = +25%
Q3: Revenue YoY = +42%
Q4: Revenue YoY = +68%
```
นี่คือ **acceleration curve** — สัญญาณ multibagger ที่ชัดเจนที่สุด

**B) QoQ Revenue Growth — สำคัญกว่า YoY ในระยะสั้น**

YoY อาจหลอกได้จาก low base แต่ QoQ บอกว่า "ธุรกิจกำลังเร่งจริงไหมในไตรมาสนี้"

Bullish conditions:
- QoQ > 10% 
- QoQ accelerating 3 quarters ติดกัน
- QoQ กลับมาเป็นบวกหลังจากติดลบ = Phase 2 signal ที่สำคัญมาก

**C) Sequential Acceleration Rule**

AI ต้อง flag เมื่อ:
- Revenue growth rate เร่งขึ้น **อย่างน้อย 2 ไตรมาสติดกัน** (QoQ และ YoY ทั้งคู่)
- Rate ของ acceleration เพิ่มขึ้น (acceleration ของ acceleration)

**D) Gross Margin Trend**

- Expanding = operating leverage กำลังทำงาน ✅
- Contracting = input cost rising หรือ pricing power อ่อนแอ ❌
- AI ต้องดู margin trend ทุกไตรมาส — margin peak = early distribution warning

### 3.2 Earnings Acceleration

**A) EPS Growth vs Revenue Growth**

Bullish pattern:
```
Revenue YoY: +40%
EPS YoY:     +90%
```
Revenue โต 40% แต่ EPS โต 90% = **Operating leverage กำลังทำงาน**
นี่คือ phase สำคัญของ multibagger — earnings เร่งเร็วกว่า revenue

**B) Operating Leverage Signal**

เกิดเมื่อ fixed cost ถูก absorb → incremental revenue แปลงเป็น profit ในอัตราสูง
- Revenue +1% → EPS +2-3% = leverage ratio สูง
- AI ต้องคำนวณ EPS growth ÷ Revenue growth = leverage ratio

**C) EPS Revision — Fuel ของ Momentum**

| Signal | Action |
|--------|--------|
| EPS revision เพิ่งเปลี่ยนจากลบเป็นบวก | Phase 2-3 transition → BUY alert |
| EPS revision บวกต่อเนื่อง magnitude ใหญ่ขึ้น | Phase 3-4 → Hold เต็ม |
| EPS revision ชะลอ แม้ยังบวก | Phase 5 warning → เริ่ม plan exit |
| EPS revision กลับลบ | KILL SWITCH → ออกทันที |

AI ต้อง monitor:
- Analyst upgrade/downgrade count ใน 30 วัน
- Guidance raise/maintain/cut จาก management
- Beat-to-miss ratio ใน 4 ไตรมาสล่าสุด
- Earnings revision breadth (% ของ analyst ที่ revise ขึ้น)

### 3.3 Quality Filter — ตัดหุ้น Garbage

ก่อน score ต้องผ่าน quality gate:
- Gross margin > 30% (หรือ expanding trend)
- Free Cash Flow positive หรือ trajectory positive
- Debt-to-equity ไม่เกิน 2x (ยกเว้น financial sector)
- Share dilution น้อยกว่า 5% ต่อปี
- Insider selling ไม่มากผิดปกติ

---

## IV. PRICE STRUCTURE LAYER

### 4.1 Volatility Contraction Pattern (VCP)

หุ้น leader ก่อนวิ่งแรงมักมี correction ที่ **เล็กลงเรื่อยๆ** พร้อม volume ที่ **ลดลงเรื่อยๆ**

ตัวอย่าง ideal VCP:
```
Pullback 1: -25% จาก high, volume สูง
Pullback 2: -15% จาก high, volume ลด
Pullback 3: -8%  จาก high, volume ลดอีก
Pullback 4: -4%  จาก high, volume แห้ง ← Pivot point
```

ความหมาย: **Institutional absorption** — smart money ค่อยๆ ซื้อ supply ที่หมด

Wyckoff map:
- แต่ละ contraction = Last Point of Supply (LPSY) → supply เหลือน้อยลง
- Volume dry-up = seller หมด → spring ready

AI ต้องวัด:
- จำนวน contractions (3-4 = ideal, < 3 = too early, > 5 = too long)
- % ของแต่ละ contraction (ต้องลดลงเรื่อยๆ)
- Volume ของแต่ละ contraction (ต้องลดลงเรื่อยๆ)
- ระยะเวลา (3-8 สัปดาห์ = ideal)

### 4.2 Base Quality Assessment

| คุณสมบัติ | Bullish | Bearish |
|---------|---------|---------|
| ระยะเวลา base | 3-8 สัปดาห์ | น้อยกว่า 2 สัปดาห์ |
| Volume ใน base | ลดลงเรื่อยๆ | ไม่สม่ำเสมอ หรือสูงผิดปกติ |
| Volatility ใน base | หดลง | กว้างขึ้น |
| Failed breakdown | ไม่มี หรือน้อย | มีหลายครั้ง |
| Depth ของ base | ไม่เกิน 30% | เกิน 50% = fundamental problem |
| Position vs 52W | ใกล้ high สุด (< -15%) | ไกลจาก high มาก |

### 4.3 Breakout Confirmation

Breakout ที่ valid ต้องมี:
- Price ผ่าน pivot point ชัดเจน
- Volume > 150% of 30-day average ณ วันที่ breakout (ยิ่งสูงยิ่งดี)
- Close ใกล้ high ของวัน (ไม่ใช่ close กลับมาต่ำกว่า pivot)
- ไม่ควร gap up เกินไปจาก breakout point (ถ้า gap > 5% = extension risk)

---

## V. RELATIVE STRENGTH LAYER — หัวใจของ Momentum Investing

### 5.1 Three-Layer RS System

| ชั้น | วัดอะไร | เงื่อนไข | Conviction Multiplier |
|-----|---------|---------|----------------------|
| Stock RS | หุ้นรายตัว vs SPX/SET | > 72nd percentile (ทุก TF) | 1.0x base |
| Sector RS | กลุ่มอุตสาหกรรม vs Market | > 70th percentile | +0.5x |
| Theme RS | Megatrend group vs Market | > 70th percentile | +0.5x |

**Maximum conviction:** Stock RS + Sector RS + Theme RS ทั้งสาม = 2.0x multiplier

### 5.2 RS Momentum Ratio — สัญญาณล่วงหน้าก่อน Breakout

```
RS Momentum Ratio = 1M RS percentile ÷ 3M RS percentile
```

| Ratio | ความหมาย | Action |
|-------|---------|--------|
| > 1.2 | Short-term outperforming medium-term อย่างชัดเจน | Phase 2→3 signal — watch for breakout (2-4 สัปดาห์) |
| 1.0–1.2 | Accelerating แต่ยังพอดี | Monitor |
| < 1.0 | Momentum กำลัง peak | Phase 5-6 warning → เริ่ม trim |

**Best combined signal:** 1M RS ข้าม 50th percentile จากด้านล่าง + Ratio > 1.2 = เตรียม buy

### 5.3 Strong Leadership Conditions

หุ้นที่มี institutional sponsorship จริงจะแสดง:
- ขึ้นขณะตลาดลง (relative strength ขณะ market down)
- Correction น้อยกว่า index เมื่อ market dips
- Recover เร็วกว่า market หลัง selloff

---

## VI. MULTI-TIMEFRAME ALIGNMENT (TF Alignment)

### 6.1 4/4 Bull Alignment — เงื่อนไขของ Super Leader

| Timeframe | เช็คอะไร | Bullish Condition |
|-----------|---------|-----------------|
| **Monthly** | 30M MA slope, price position | ราคาเหนือ 12M MA ที่ขึ้น |
| **Weekly** | 30W MA slope, weekly structure | ราคาเหนือ 30W MA, trend up |
| **Daily** | 50DMA, 200DMA, trend | ราคาเหนือ 50DMA, 200DMA, slope บวก |
| **Intraday** | 4H/1H structure | Higher highs, higher lows |

**กฎ:** ถ้า 4/4 Bull = Green Light สำหรับ entry
ถ้า 3/4 = Yellow — ลด size 50%
ถ้า < 3/4 = Red — ไม่เข้า **ไม่ว่า factor อื่นจะดีแค่ไหน**

### 6.2 Alignment Map กับ NRGC

| NRGC Phase | TF Alignment ที่คาดหวัง |
|-----------|----------------------|
| Phase 1 (Neglect) | 0-1/4 — ส่วนใหญ่ bearish |
| Phase 2 (Accumulation) | 2-3/4 — monthly/weekly ยังลบ, daily เริ่มบวก |
| Phase 3 (Inflection) ⭐ | 3-4/4 — daily + weekly เริ่ม align |
| Phase 4 (Recognition) | 4/4 Bull — perfect alignment |
| Phase 5 (Consensus) | 4/4 แต่ intraday เริ่ม choppy |
| Phase 6 (Euphoria) | 4/4 แต่ divergence เริ่มเกิด |
| Phase 7 (Distribution) | 3/4 → 2/4 → ลดลงเรื่อยๆ |

---

## VII. VOLUME ANALYSIS — Footprint ของ Institutions

### 7.1 Accumulation Pattern

AI ต้องหา:
- **Up volume > Down volume** (ช่วง 20 วัน cumulative)
- **Pocket pivot**: volume expansion บน up day ที่ผ่าน down day volume สูงสุด 10 วัน = institutional buying
- **Breakout volume**: ≥ 150% of 30D average = institutional confirmation

### 7.2 Volume Dry-Up = Seller หมด

ก่อน breakout ที่ดี volume มักแห้งมาก (< 50% of average):
- ความหมาย: supply exhausted, seller หมดแล้ว
- เมื่อ breakout เกิด demand overwhelms supply → explosive move

### 7.3 Volume States ที่ต้องเช็ค

| State | ลักษณะ | Implication |
|-------|--------|-------------|
| **Normal** | ปริมาณสม่ำเสมอ, accumulation สังเกตได้ | Healthy — เข้าได้ |
| **Dry-up** | Volume ต่ำมาก (< 50% avg) ก่อน breakout | Ideal pre-breakout condition |
| **Climax** | Volume พุ่งสูงมาก 200-500% avg ขณะขึ้น | Distribution warning — เริ่ม trim |
| **Distribution** | Volume สูงบน down days, ต่ำบน up days | Phase 6-7 — ออก |

---

## VIII. VOLATILITY EXPANSION THEORY

### 8.1 Compression → Expansion Cycle

**Core concept:** Compression precedes Expansion

```
Phase A: Volatility หด → ATR ลดลง
Phase B: Sideway consolidation → range แคบ
Phase C: Bollinger squeeze → bands แคบ
Phase D: Breakout เกิด
Phase E: Volatility expand → ATR เพิ่มขึ้น ← สัญญาณ bullish ที่ดี
```

### 8.2 AI ต้องวัด

| Metric | Bullish Signal | Red Flag |
|--------|--------------|----------|
| **ATR (14)** | ลดลงก่อน breakout, เพิ่มขึ้นหลัง breakout | เพิ่มขึ้นระหว่าง consolidation |
| **Bollinger Band Width** | แคบลง (squeeze) ก่อน breakout | กว้างมากผิดปกติ |
| **Range compression** | วันก่อน breakout range เล็กมาก | Range ไม่ compress เลย |
| **VIX ratio** | VIX ต่ำขณะ base forming | VIX สูง = ตลาดกลัว → momentum break |

### 8.3 Volatility = Expanding ใน Health Check

ถ้า Volatility = Expanding **หลัง breakout** = bullish confirmation
- หมายความว่า demand overwhelms supply
- Institutional buying creating price discovery
- Move มีความ sustainable

---

## IX. MARKET REGIME FILTER

Momentum strategy ใช้ไม่ได้ทุกช่วงตลาด

### 9.1 Regime Indicators ที่ต้องดู

| Indicator | Bull Regime | Bear Regime |
|-----------|-------------|-------------|
| % หุ้นเหนือ 50DMA | > 60% | < 40% |
| % หุ้นเหนือ 200DMA | > 60% | < 40% |
| New Highs vs New Lows | New Highs ชนะ | New Lows ชนะ |
| Distribution Days (SPX/Nasdaq) | < 4 ใน 25 วัน | ≥ 4-5 ใน 25 วัน |
| Index trend | เหนือ 50DMA + 200DMA | ต่ำกว่า 200DMA |

### 9.2 กฎ Market Regime ใน EMLS

| % หุ้นเหนือ 200DMA | Action |
|------------------|--------|
| > 60% | Full size allowed — market supportive |
| 50-60% | Reduce new positions 30% |
| 40-50% | Reduce new positions 50% |
| < 40% | Raise cash ≥ 30%, ไม่เปิด position ใหม่ |
| < 30% | Raise cash ≥ 40%, defensive mode |

Distribution Days ≥ 4: reduce ALL positions ทันที

---

## X. HEALTH CHECK DASHBOARD — Leadership State Score

### 10.1 8 Indicators (แต่ละตัว Yes/No)

| # | Indicator | สิ่งที่เช็ค | Bullish Condition | Red Flag |
|---|-----------|-----------|-----------------|---------|
| 1 | **TF Alignment** | Monthly/Weekly/Daily/Intraday trend direction | 4/4 Bull | < 3/4 |
| 2 | **Market** | Breadth, index trend, distribution days | Healthy — risk-on confirmed | Distribution phase |
| 3 | **Rel Strength** | Outperforming index AND sector | Leading — outperforms both up and down | RS declining |
| 4 | **Volume** | Breakout vol, institutional accumulation, dry-up | Normal to Strong — not climax | Climax/Distribution pattern |
| 5 | **Momentum** | Trend strength + price extension vs MAs | Strong + Ranging (powerful not parabolic) | Parabolic / Extended |
| 6 | **Volatility** | ATR, Bollinger, range compression cycle | Expanding after compression | Still compressing after breakout |
| 7 | **Extension** | Distance from 10EMA/21EMA/50DMA | Normal — RSI < 80, < 10% above 10EMA | RSI > 85, > 15% above 10EMA |
| 8 | **Bull Streak** | Consecutive bullish bars | 4+ bars = sustained buying pressure | < 2 bars |

### 10.2 Details Panel ของแต่ละหุ้น

| Metric | Bullish Signal | Red Flag |
|--------|---------------|----------|
| YTD % | Outperforming SPX/SET YTD | Lagging significantly |
| 30D %Chg vs 90D %Chg | 30D > 90D rate = momentum accelerating | 30D < 90D = decelerating |
| 90D %Chg | +15% หรือมากกว่า | Negative or flat |
| vs ATH | At or making New High | > 20% below ATH |
| MACD | Positive and rising | Negative or below zero |
| RSI | 50–75 zone (bullish healthy) | > 85 (climax) or < 40 (trend break) |
| EPS / PE | EPS accelerating, PE justified by growth | PE > 3x sector avg with decelerating EPS |
| Mkt Cap | Context for sizing | Mega-cap limits explosive upside |

### 10.3 Health Check Score Interpretation

| Score | Color | Entry Decision |
|-------|-------|----------------|
| 7–8 / 8 | 🟢 GREEN | Full size entry allowed |
| 5–6 / 8 | 🟡 YELLOW | Reduced size (50%), watch for remaining conditions |
| < 5 / 8 | 🔴 RED | Do not enter — monitor only |

**กฎเพิ่มเติม:** TF Alignment ต้องเป็น 4/4 เสมอ — ถ้าไม่ผ่าน max score = YELLOW ไม่ว่า 7 indicators อื่นจะผ่านกี่ตัว

---

## XI. EMLS DECISION ENGINE — Scoring System (0–100)

### 11.1 Weighted Scoring

| Factor | Weight | วิธีให้คะแนน |
|--------|--------|------------|
| **Earnings Acceleration** | 25% | 3Q+ accel = 25, 2Q = 17, 1Q = 8, flat = 0 |
| **Revenue Acceleration** | 20% | QoQ + YoY ทั้งคู่ accel = 20, เพียงอย่างใด = 12, flat = 0 |
| **Relative Strength** | 20% | RS > 90th = 20, 80-90th = 16, 72-80th = 12, 60-72nd = 6, < 60th = 0 |
| **Price Structure** | 15% | VCP/CwH 3+ contractions clean = 15, partial base = 8, no pattern = 0 |
| **Volume** | 10% | Vol dry-up + breakout expansion = 10, partial = 5, climax/distribution = 0 |
| **Market Regime** | 10% | Breadth > 60% = 10, 40-60% = 5, < 40% = 0 |

### 11.2 Score Tiers

| Score | Label | Action |
|-------|-------|--------|
| 90–100 | ⚡ **Hyper Leader** | Maximum conviction — size up เต็ม, monitor every session |
| 80–89 | 🏆 **Institutional Leader** | Full position — high priority pick |
| 70–79 | 🔺 **Emerging Leader** | Standard size — watchlist to active |
| 60–69 | 👁 **Watchlist** | Monitor — not yet actionable |
| < 60 | — **Ignore** | No position ไม่ว่า story จะน่าสนใจแค่ไหน |

### 11.3 Override Rules

1. **Health Check < 5/8** → ลด EMLS score ลง 20 points อัตโนมัติ
2. **Market Regime bearish** (% > 200DMA < 40%) → EMLS score × 0.5
3. **Stage 3 หรือ Stage 4 detected** → EMLS = 0 (NO BUY ไม่มีข้อยกเว้น)
4. **Earnings within 5 trading days** → ไม่เปิด position ใหม่ ไม่ว่า EMLS จะสูงแค่ไหน

---

## XII. EARLY DETECTION PROTOCOL

AI ต้องหา **inflection** — ไม่ใช่หุ้นที่ดังแล้ว

### 12.1 สัญญาณ Inflection ที่ต้องหา

| สัญญาณ | ความหมาย | NRGC Phase |
|--------|---------|-----------|
| Earnings เพิ่งเริ่ม accelerate | Fundamental turn เพิ่งเริ่ม | Phase 2 เริ่ม |
| RS เพิ่งข้าม 50th percentile จากด้านล่าง | สถาบันเริ่มสนใจ | Phase 2→3 |
| First breakout from base (Base 0/1) | ยังไม่ extended | Phase 2-3 |
| Volume pattern เปลี่ยน (accumulation เริ่มเห็น) | Institutional quietly building | Phase 2 |
| Narrative มีแต่ยังไม่ crowded | Room for more believers | Phase 2-3 |

### 12.2 สิ่งที่ AI ต้องหลีกเลี่ยง

- หุ้นที่ดังแล้ว ทุกคนรู้แล้ว = Phase 4-5 ความเสี่ยงสูง
- Base 3+ = ผ่าน prime entry ไปแล้ว
- RS สูงมาก 95th+ แต่ RSI > 85 = euphoria risk
- Narrative crowded = upside จำกัด

---

## XIII. IDEAL MULTIBAGGER SIGNATURE

หุ้น 10 เด้งในระบบนี้มักมีครบทั้ง 11 สัญญาณพร้อมกัน:

| # | สัญญาณ | หมายเหตุ |
|---|--------|---------|
| ✅ 1 | **Revenue acceleration** | QoQ + YoY ทั้งคู่เร่งขึ้น ≥ 3 ไตรมาส |
| ✅ 2 | **EPS acceleration** | EPS โตเร็วกว่า revenue = operating leverage ทำงาน |
| ✅ 3 | **New highs** | ทำ ATH หรือใกล้ ATH — market re-rating future growth |
| ✅ 4 | **RS leader** | Top percentile vs SPX + sector — outperforms both ways |
| ✅ 5 | **Tight base** | VCP หรือ Cup with Handle — volatility contraction สมบูรณ์ |
| ✅ 6 | **Volume dry-up** | Pre-breakout: seller exhaustion, supply absorbed |
| ✅ 7 | **Volatility expansion** | Post-breakout: demand overwhelming supply |
| ✅ 8 | **Institutional accumulation** | Pocket pivots, up vol > down vol, 13F building |
| ✅ 9 | **4/4 TF alignment** | Monthly + Weekly + Daily + Intraday ทั้งหมด bullish |
| ✅ 10 | **Narrative tailwind** | Megatrend backing — AI, SMR, Space, Defense, Photonics |
| ✅ 11 | **Earnings revisions upward** | Analyst upgrade, guidance raise, estimate breadth positive |

**7–9 signals = HIGH conviction. 10–11 = Maximum size.**

Historical examples: NVDA (2023), PLTR (2024), RKLB (2024), MU (early cycle 2023), LITE (2013)

---

## XIV. FAILURE SIGNALS — Early Exit Checklist

AI ต้อง detect failure ก่อนที่ losses จะสะสม:

| Signal | ความหมาย | Action |
|--------|---------|--------|
| ❌ Revenue QoQ decelerating | Thesis weakening — growth slowing | Reduce 30% |
| ❌ RS declining from top quartile | สถาบันออก — leadership ถูก challenge | Flag → downgrade priority |
| ❌ Failed breakout on volume | Institutional rejection — sellers ชนะ | Exit หรือ stop |
| ❌ Heavy sell volume on up days | Distribution — smart money exiting | Warning |
| ❌ Momentum divergence (price up, RS down) | Leading indicator ของ top | Trim |
| ❌ RSI > 85 + parabolic + climax volume | Euphoria — retail FOMO | Trim aggressively |
| ❌ Distribution days ≥ 4 in market | Market regime change | Reduce ALL |
| ❌ EPS revision turning negative | Fundamental thesis broken | Exit |
| ❌ Stage 3 detected | Distribution confirmed | Exit immediately |
| ❌ Guidance cut | Management ไม่มั่นใจ | Exit |

---

## XV. INTEGRATION SUMMARY FOR AI AGENTS

### 15.1 Complete Workflow

```
Step 1: Market Regime Check (IX)
   └─ If % > 200DMA < 40% → reduce all new positions

Step 2: NRGC Phase Mapping (I-II)
   └─ Map each candidate to Phase 1-7
   └─ Only Phase 2-3 = full size eligible

Step 3: PULSE Screen (Five Layers)
   ├─ Fundamental (III) — earnings + revenue acceleration
   ├─ Price Structure (IV) — VCP/CwH, base quality
   ├─ Relative Strength (V) — three-layer RS
   ├─ Volatility (VIII) — compression → expansion
   └─ Market Regime (IX) — breadth check

Step 4: Health Check Dashboard (X)
   └─ Score 0-8 on 8 indicators
   └─ < 5/8 = Red = do not enter

Step 5: EMLS Score (XI)
   └─ Score 0-100
   └─ < 70 = watchlist only, < 60 = ignore

Step 6: Multibagger Signature Count (XIII)
   └─ Count 0-11 signals
   └─ 7+ = HIGH conviction, 10-11 = maximum size

Step 7: Entry Decision (NRGC Section 4)
   └─ Type A: Wyckoff Spring + SMC (best risk/reward)
   └─ Type B: Breakout + Volume
   └─ Type C: Pullback to OB/FVG

Step 8: Set Stop + Size (NRGC Section 4.3)
   └─ Hard stop at pivot low
   └─ Size per risk rules (max 15% single position)
```

### 15.2 The Five Master Questions

**1. Is the trend right?** → TF Alignment, MACD, RSI
**2. Is this a leader?** → Relative Strength, ATH proximity, YTD performance
**3. Is momentum accelerating?** → 30D/90D, Bull Streak, Volatility Expansion
**4. Is entry risk justified now?** → Extension, Volume, Market Health
**5. Do fundamentals support it?** → EPS acceleration, Revenue growth, PE vs growth rate

ทั้ง 5 คำถามต้องมีคำตอบ YES ก่อน size เต็ม

### 15.3 Ultimate Philosophy

**"Big winners are not random."**

หุ้นที่กลายเป็น NVDA, TSLA, MU, PLTR ก่อนวิ่งใหญ่ มักมี:
- Earnings acceleration
- Relative strength
- Volatility contraction
- Breakout near ATH
- Institutional accumulation

ซ้ำกันเสมอ

ระบบ EMLS จึงพยายามให้ AI **"detect leadership before consensus"** —
หาหุ้นที่กำลังเข้าสู่ช่วงเร่งตัว ก่อนตลาดส่วนใหญ่จะ realize เต็มตัว

---

*Document version: 1.0*
*Created: 2026-05-13*
*Framework: EMLS integrated with NRGC v2.0 + PULSE v2.0*
*For NotebookLM upload: AlphaAbsolute — PULSE Framework notebook*
*Agents using this document: 03 (Screener), 04 (Fundamental), 06 (Thai FM), 07 (US FM), 10 (CIO)*
