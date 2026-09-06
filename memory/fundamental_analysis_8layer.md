# Fundamental Analysis — 8-Layer Stock Evaluation Framework
## AlphaAbsolute × Earthh Evans System

> "ถ้า 6 ชั้นแรกไม่ผ่าน ต่อให้ราคาถูกแค่ไหน มันก็ยังเสี่ยง"
> "ถ้า 6 ชั้นแรกผ่าน เรื่องราคาจะกลายเป็นเรื่อง MOS กับจังหวะมากกว่า"

---

## Overview: 8 Layers ก่อนซื้อหุ้น 1 ตัว

```
Layer 1: ธุรกิจโตจริงไหม          (Revenue Growth)
Layer 2: โตแล้วเหลือกำไรไหม       (Margin Quality)
Layer 3: โตแบบมีคุณภาพไหม         (FCF & ROIC)
Layer 4: Moat ของมันแข็งแค่ไหน    (Competitive Advantage)
Layer 5: งบดุลรอดไหม              (Balance Sheet Survival)
Layer 6: ผู้บริหารใช้เงินเป็นไหม   (Capital Allocation)
Layer 7: ราคาที่จ่ายคุ้มไหม        (Valuation: DCF+PEG+EV/Sales)
Layer 8: ซื้อแล้ว ขายเมื่อไหร่     (Exit + Position Sizing)
```

**ลำดับความสำคัญ:** ต้องผ่าน Layer 1-6 ก่อน แล้วค่อยคุยเรื่อง 7-8

---

## Layer 1 — ธุรกิจโตจริงไหม (Revenue Growth)

### กี่ปีที่ต้องดู
- ธุรกิจปกติ: **5 ปี** ย้อนหลัง
- ธุรกิจวัฏจักร (น้ำมัน/เหล็ก/เรือ/ชิป): **7-10 ปี** (5 ปีอาจแค่ช่วงขาขึ้น)

### Revenue CAGR Benchmark by Sector
| อุตสาหกรรม | CAGR ดี |
|------------|---------|
| Utility, Telecom, Consumer Staples | 3-8% (ช้าแต่ชัวร์) |
| Healthcare, Payments, Mature Software | 8-15% |
| Tech Platform, High-growth Software | 15-30% |
| Hypergrowth (Base 0/1) | > 30% แต่ตรวจ FCF ด้วยทันที |

### ❗ เจาะลึก: โตเพราะอะไร?
1. **Volume** — ขายได้เยอะขึ้นจริง ✅
2. **Price** — ขึ้นราคาได้ = มี Pricing Power ✅✅
3. **M&A** — ซื้อกิจการมาร่วม → ตรวจ ROIC ก่อน/หลัง M&A
4. **Currency** — เงินเฟ้อ/FX ช่วย → ระวัง หักออกแล้วโตจริงไหม

### ❗ คุณภาพรายได้
- **Recurring > One-time** (Subscription > Project)
- **ลูกค้ากระจาย** vs. ลูกค้าเดียว 30-40% → ถ้าหายคนเดียว บริษัทสั่น
- **Contract length** → ยาวกว่า = ความแน่นอนของรายได้สูงกว่า

### 🔗 เชื่อมกับ PRISM
- CANSLIM C (Current Earnings): ต้องการ EPS growth ≥ 25% QoQ
- CANSLIM A (Annual): Revenue CAGR ≥ 25% (3 ปีย้อนหลัง) สำหรับ Leader setup
- Bottom Fish: Revenue CAGR ≥ 10% ก็พอ ถ้า Wyckoff Spring ยืนยัน

---

## Layer 2 — โตแล้วเหลือกำไรไหม (Margin Quality)

### Gross Margin — Pricing Power Check
| ประเภท | Gross Margin ปกติ |
|--------|------------------|
| SaaS/Software | 65-85% |
| Semiconductors | 45-65% |
| Healthcare/Pharma | 55-75% |
| Consumer Tech | 35-55% |
| Retail | 20-40% |
| Manufacturing | 15-35% |

> **Gross Margin ลดลง YoY** = สัญญาณเตือน — คู่แข่งกดราคา หรือ input cost สูง

### Operating Margin — Scalability Check
- **เพิ่มขึ้น** ทุกปี = บริษัทมีสเกล, ควบคุมต้นทุนเก่งขึ้น → Positive leverage
- **ลดลง** แม้ revenue โต = ปัญหาเชิงโครงสร้าง หรือ pricing power หาย

### อย่าหลงแค่ Net Margin
- Net profit อาจสวยจาก One-time item (ขายสินทรัพย์, tax benefit)
- ดู **Operating Margin + EBITDA Margin** เป็นหลัก
- ดู **Adjusted EPS vs GAAP EPS** — ห่างกันมาก = ระวัง

---

## Layer 3 — โตแบบมีคุณภาพไหม (FCF & ROIC > WACC)

### คำถามสำคัญ 2 ข้อ

#### ข้อ 1: ROIC > WACC ต่อเนื่องไหม?
```
ROIC = Net Operating Profit After Tax / Invested Capital
WACC = Weighted Average Cost of Capital

ROIC > WACC → ยิ่งโตยิ่งสร้างมูลค่า ✅
ROIC < WACC → โตแล้วทำลายมูลค่า ❌ (วิ่งบนสายพาน)
```
- Good ROIC benchmark: Tech SaaS = 20-50%, Semiconductor = 15-30%
- Cyclical average: 10-15%
- ถ้า ROIC < 8% = weak business

#### ข้อ 2: โตต้องใช้เงินเยอะแค่ไหน (Capital Intensity)?
- **Low CAPEX business** (Software, Marketplace, Platform) = เงินสดไหลดี
- **High CAPEX business** (Semiconductor fab, Data Center, Airlines) = กำไรมีจริงแต่เงินสดน้อย

### FCF Yield Check
```
FCF = Operating Cash Flow - CAPEX
FCF Yield = FCF / Market Cap

> 5% = ถูก/สมเหตุสมผล
2-5% = neutral
< 2% = แพง (ต้อง justify ด้วย growth)
```

### ⚠️ สัญญาณอันตราย
- กำไรโตทุกปี แต่ FCF ไม่โตตาม → "Paper profit" → ระวัง
- Inventory Days พุ่ง + Receivable Days พุ่ง **ก่อน** กำไรพัง → leading indicator
- Cash Conversion Cycle ยาวขึ้น = ธุรกิจเริ่มมีปัญหา

### 🔗 เชื่อมกับ AlphaAbsolute
- EMLS EPS Acceleration = ดู Operating Leverage (กำไรโตเร็วกว่า Revenue)
- Hypergrowth check: ต้องมี Gross Margin expanding (ไม่ใช่แค่ Revenue)

---

## Layer 4 — Moat ของมันแข็งแค่ไหน (Competitive Advantage)

### 4 ประเภท Moat

#### 1. Switching Cost — "ย้ายแล้วเจ็บ"
- **ตัวอย่าง:** SAP, Oracle (ERP), Salesforce, Visa/Mastercard
- **Test:** ถ้าลูกค้าจะย้ายไปคู่แข่ง เจ็บปวดแค่ไหน?
- **Strength:** สูงมาก ถ้าฝังลึกใน workflow ของลูกค้า

#### 2. Network Effect — "ยิ่งคนมาก ยิ่งมีค่า"
- **ตัวอย่าง:** Meta, YouTube, Visa, Airbnb, LinkedIn
- **Test:** ถ้าผู้ใช้ลดลง 50% ค่าของ platform ลดแค่ไหน?
- **Strength:** แข็งที่สุด แต่พัง fast ถ้า network shift

#### 3. Scale Advantage — "ใหญ่กว่า ถูกกว่า"
- **ตัวอย่าง:** Amazon, Costco, TSMC, Walmart
- **Test:** คู่แข่งที่เล็กกว่าแข่งราคาได้ไหม?
- **Strength:** แข็งในตลาดที่มี high fixed cost

#### 4. Intangible Asset — "มีสิ่งที่คนอื่นไม่มี"
- **Brand:** Hermes, Ferrari, Apple → คนยอมจ่ายแพงกว่า
- **Patent:** Pharma, Semiconductor IP
- **License:** สนามบิน, ธนาคาร, Telecom spectrum
- **Regulation:** กฎหมายกัน competition เข้า

### 🔑 Moat Test (ถามทุกครั้งก่อนซื้อ)
> "ถ้ามีคู่แข่งใหม่เข้ามาด้วยเงินทุนเยอะกว่า เก่งกว่า เร็วกว่า อะไรจะกันบริษัทนี้จากการโดนกิน market share?"

### Moat Life Cycle — Moat ไม่ใช่ของถาวร
- **กว้างขึ้น** (Widening) = ลงทุนเพิ่ม ✅
- **คงที่** (Stable) = OK ถ้ายังแข็ง
- **แคบลง** (Narrowing) = สัญญาณอันตราย → พิจารณาขาย
- **หาย** (Gone) = ออกเลย ไม่รอดูราคา

> Nokia, Kodak, Blockbuster ล้วนเคยมี Moat ที่คิดว่าแข็ง

---

## Layer 5 — งบดุลรอดไหม (Balance Sheet Survival)

### 3 ตัวเลขหลัก

#### 1. Net Debt / EBITDA
```
< 1x    = แทบไม่มีหนี้ → แข็งแกร่งมาก
1-2x   = ปลอดภัย
2-3x   = จัดการได้ ถ้ากระแสเงินสดสม่ำเสมอ
3-4x   = เริ่มระวัง โดยเฉพาะถ้าดอกเบี้ยสูง
> 4x    = อันตราย (ยกเว้น Utility/Infrastructure ที่กระแสเงินสดชัดเจน)
```

#### 2. Interest Coverage (EBIT / Interest Expense)
```
> 5x   = ปลอดภัย
3-5x  = OK
< 3x   = ดอกเบี้ยกินกำไรเยอะ → อ่อนไหวต่อ recession
< 1.5x = อันตราย → บริษัทอาจผิดนัดชำระหนี้
```

#### 3. Debt Maturity Profile
- **หนี้ครบกำหนดใน 1-2 ปี** มีเยอะไหม?
- ถ้าต้อง Refinance ตอนดอกเบี้ยสูง → ต้นทุนทางการเงินพุ่ง
- ดูว่า บริษัทมี Cash + Credit facility พอ Rollover ไหม

### ⚠️ Dilution Check — หุ้นเพิ่มแบบเงียบๆ
```
Diluted Share Count ปี 2019 vs 2024:
+10% → OK (buyback program อาจชดเชยได้)
+30-40% → หุ้นเดิมถูก dilute หนัก → EPS ต่อหุ้นโตช้ากว่าที่ควร
```

**Stock-Based Compensation (SBC):**
- SBC ไม่ใช่ค่าใช้จ่ายเงินสด แต่คือต้นทุนที่ผู้ถือหุ้นแบกรับ
- สูตรที่ถูกต้อง: **FCF adj = FCF - SBC**
- SBC > 15% of Revenue = สงสัย (มักเป็น startup ที่ยังขาดทุน)

### ❌ บริษัทที่ตายเพราะงบดุล ไม่ใช่งบกำไรขาดทุน
- SVB 2023: กำไรดีทุกปี แต่ duration mismatch ใน balance sheet
- อสังหาจีน Evergrande: กำไรสวย แต่ hidden debt enormous
- ธุรกิจ PE-backed: leveraged buyout ทำให้ Net Debt/EBITDA > 6x

---

## Layer 6 — ผู้บริหารใช้เงินเป็นไหม (Capital Allocation)

### 5 ทางเลือกใช้เงินสด
```
1. Reinvest ในธุรกิจเดิม (CAPEX, R&D)
   → ดี: ถ้า ROIC > WACC (ยิ่งลงทุน ยิ่งรวย)
   → แย่: ถ้า ROIC < WACC (เหนื่อยแต่ไม่รวย)

2. M&A — ซื้อกิจการ
   → ดี: ซื้อถูก + Synergy จริง + ROIC ดีขึ้นหลัง integrate
   → แย่: ซื้อแพง + ใช้หุ้นจ่าย + ROIC ลดลงหลัง M&A
   → ประวัติ: M&A ส่วนใหญ่ทำลายมูลค่า ไม่ได้สร้าง

3. จ่ายปันผล
   → เหมาะ: Mature business (Utility, Telecom, Consumer)
   → ไม่เหมาะ: Growth company ที่ยังมีโอกาส Reinvest ดีๆ

4. ซื้อหุ้นคืน (Buyback)
   → ดี: ซื้อตอนหุ้น undervalued (เพิ่ม EPS ต่อหุ้น)
   → แย่: ซื้อตอนหุ้น overvalued หรือซื้อเพื่อ offset SBC เฉยๆ
   → Test: ดูว่าซื้อคืนช่วงไหน เทียบราคาหุ้นตอนนั้น

5. เก็บเงินสด / ลดหนี้
   → ดี: ช่วงดอกเบี้ยสูง, uncertainty สูง
   → แย่: เก็บนานเกินไปแล้วไม่ทำอะไร
```

### วิธีประเมิน CEO/CFO Quality
- ดู ROIC ย้อนหลัง 5-10 ปี: เพิ่มหรือลด?
- ดู M&A track record: กำไรหรือขาดทุนหลัง M&A?
- ดูจังหวะ Buyback vs ราคาหุ้น: ซื้อตอนถูกหรือแพง?
- อ่าน Letter to Shareholders: ตรงไปตรงมาหรือแก้ตัว?

---

## Layer 7 — ราคาที่จ่ายคุ้มไหม (Valuation)

### ใช้ 3 มุมพร้อมกัน (ห้ามใช้มุมเดียว)

#### มุมที่ 1: DCF (Discounted Cash Flow)
**คำถามที่ DCF ตอบ:**
> "ธุรกิจนี้จะส่งเงินสดกลับมาให้เจ้าของได้เท่าไหร่ในอนาคต คิดเป็นมูลค่าวันนี้?"

**3 Input ที่กำหนด DCF Value:**
1. Revenue growth rate (ปีที่ 1-5, Terminal)
2. Margin profile (Operating margin, FCF conversion)
3. Discount rate (WACC = risk level)

**Margin of Safety (MOS):**
```
MOS = (Intrinsic Value - Current Price) / Intrinsic Value

> 30% → ปลอดภัย (ผิดได้เยอะ)
20-30% → ดี
10-20% → ระวัง (ผิดนิดเดียวหุ้นร่วง)
< 10% → Risky (ต้องสมบูรณ์แบบทุกอย่าง)
< 0% → แพงกว่า Intrinsic Value → หลีกเลี่ยง
```

**⚠️ ข้อจำกัด DCF:**
- Terminal Value = 60-80% ของมูลค่าทั้งหมด
- ต้อง run Sensitivity Analysis: WACC +1% / Terminal Growth -1% → มูลค่าเปลี่ยนแค่ไหน?
- ถ้าเปลี่ยนเยอะ = fragile valuation

#### มุมที่ 2: PEG Ratio
```
PEG = TTM P/E ÷ EPS 3-5Y Forward CAGR

< 0.8   → ถูกมาก (ตลาดไม่เชื่อ growth หรือ growth underestimated)
0.8-1.0 → น่าสนใจ
1.0-1.5 → สมเหตุสมผล
1.5-2.0 → เริ่มแพง ต้องมั่นใจ growth
> 2.0   → ซื้อความหวัง → ถ้า growth สะดุดนิดเดียว valuation ยุบแรง
```

**ข้อควรระวัง PEG:**
- ใช้ไม่ได้กับบริษัทขาดทุน (P/E ไม่มีความหมาย)
- ใช้ไม่ได้กับ Cyclical (growth ไม่เสถียร)
- EPS Growth ต้องเป็น "organic" ไม่ใช่จาก Buyback เทียม

#### มุมที่ 3: EV/Sales
**ประโยชน์:** ใช้ได้แม้กับบริษัทที่ยังขาดทุน, ดูว่าตลาดจ่ายต่อรายได้ $1 แค่ไหน

```
ยิ่ง EV/Sales สูง → ยิ่งต้องการ Margin สูงในอนาคตมาจัด
```

**Rule of Thumb:**
- SaaS ที่ Gross Margin 75%+ อาจ justify EV/Sales 10-15x ได้
- SaaS ที่ Gross Margin 55% → EV/Sales 10x แพงเกิน
- Industrial/Manufacturing → EV/Sales ปกติ 1-3x

**เปรียบเทียบ 3 มิติ:**
1. ตัวเองในอดีต 5 ปี (Percentile ที่เท่าไหร่?)
2. Peer ที่ Business Model ใกล้เคียง (Premium/Discount ยังสมเหตุสมผลไหม?)
3. ตลาดโดยรวม (Premium อธิบายด้วยอะไร?)

---

## Layer 8 — ซื้อแล้ว ขายเมื่อไหร่ + ขนาดเท่าไหร่

### 4 เหตุผลขาย ✅
```
1. Thesis Broken  → เหตุผลที่ซื้อหายไป → ออกเลย ไม่รอราคา
2. Valuation Reached → MOS หาย ราคาเต็ม → Trim ลง
3. Better Opportunity → หุ้นอื่น Risk/Reward ดีกว่าชัดเจน → Rotate
4. Position Size ใหญ่เกิน → Trim เพื่อจัดการ Risk พอร์ต
```

### 3 เหตุผล "ไม่ใช่" เหตุผลขาย ❌
```
1. ราคาตก แต่ Thesis ยังอยู่ → อย่าขาย (เพิ่มได้ถ้า MOS ดีกว่าเดิม)
2. ตลาดขาลงทั้งตลาด → อย่าขาย Thesis-intact stocks ตาม macro panic
3. ข่าวลือที่ไม่กระทบ Business จริง → อย่า let noise drive decision
```

### Position Sizing Framework (เชื่อมกับ PRISM)
```
Conviction Level      │ MOS  │ Max Size │ Rule
──────────────────────┼──────┼──────────┼─────────────────────────────
High (Base 1-2, EMLS≥80) │ >30% │ 10-12%   │ Full size authorized
Medium (Base 2-3, EMLS 70-79) │ >20% │ 5-7%    │ Standard size
Low/Speculative       │ >15% │ 3-5%     │ Small size only
Base 0 Hypergrowth    │ any  │ max 5%   │ Fixed cap regardless of conviction
Bottom Fish           │ any  │ max 4%   │ Until Stage 2 confirmed
```

> **Rule:** ถ้าหุ้นตัวเดียวลง 50% แล้วพอร์ตเราสั่น แปลว่าซื้อเยอะเกินไป

---

## 20-Point Checklist ก่อนซื้อทุกครั้ง

```
LAYER 1: REVENUE GROWTH
☐ 1. รายได้โตไหม ดู 5 ปี (วัฏจักรดู 7-10 ปี)
☐ 2. โตสม่ำเสมอ หรือโตแค่ปีเดียว?
☐ 3. โตเพราะ Volume, Price หรือ M&A?
☐ 4. รายได้ Recurring มากแค่ไหน? ลูกค้ากระจุกไหม?

LAYER 2: MARGIN
☐ 5. Gross Margin อยู่ระดับสมเหตุสมผลกับอุตสาหกรรมไหม?
☐ 6. Operating Margin แนวโน้มดีขึ้นหรือแย่ลง?
☐ 7. Net Margin สวยจาก One-time หรือ Operating จริง?

LAYER 3: QUALITY OF GROWTH
☐ 8. FCF มาตามกำไรไหม สม่ำเสมอไหม?
☐ 9. ROIC > WACC ต่อเนื่องไหม?
☐ 10. Cash Conversion Cycle (Inventory+Receivable Days) ดีขึ้นหรือแย่ลง?

LAYER 4: MOAT
☐ 11. Moat คือแบบไหน? Switching/Network/Scale/Intangible?
☐ 12. Moat กำลังกว้างขึ้นหรือแคบลง?

LAYER 5: BALANCE SHEET
☐ 13. Net Debt/EBITDA อยู่ระดับไหน? Interest Coverage พอไหม?
☐ 14. Debt Maturity — มีหนี้ครบกำหนดใน 1-2 ปีเยอะไหม?
☐ 15. Diluted Share Count เพิ่มหรือลด 5 ปีย้อนหลัง? SBC เยอะไหม?

LAYER 6: CAPITAL ALLOCATION
☐ 16. ผู้บริหารใช้เงินสด 5 ทางยังไง? Track record ดีไหม?

LAYER 7: VALUATION
☐ 17. DCF Intrinsic Value คือเท่าไหร่? MOS เหลือเท่าไหร่?
☐ 18. DCF Sensitivity: WACC+1% หรือ Terminal Growth-1% ทนได้ไหม?
☐ 19. PEG อยู่ที่ไหน? เทียบ Peer และตัวเองในอดีต?
☐ 20. EV/Sales สมเหตุสมผลกับ Margin structure ไหม?

LAYER 8: EXIT & SIZING
☐ 21. รู้แล้วว่าจะขายเมื่อไหร่? Thesis Break = อะไร?
☐ 22. ขนาดไม้ตรงกับ Conviction + MOS ไหม?
```

---

## AI Prompt Template — Stock Analysis (ใช้กับ Claude/GPT ได้เลย)

### Master Prompt (ครบทุก Layer)

```
คุณคือ Fundamental Analyst ผู้เชี่ยวชาญ วิเคราะห์ [TICKER] ให้ครบ 8 ชั้น:

บริษัท: [Company Name]
Sector: [Sector]
Market Cap: $[X]B
ราคาปัจจุบัน: $[X]

ข้อมูลที่มี:
- Revenue 5 ปีย้อนหลัง: [ใส่ตัวเลข]
- EPS/Net Income 5 ปีย้อนหลัง: [ใส่ตัวเลข]
- Gross Margin Trend: [ใส่ตัวเลข]
- FCF 3 ปีย้อนหลัง: [ใส่ตัวเลข]
- Net Debt: $[X]B, EBITDA: $[X]B
- Diluted Shares trend: [เพิ่ม/ลด X%]
- Forward P/E: [X]x, Forward Revenue: $[X]B

วิเคราะห์ตามลำดับ:

1. REVENUE: CAGR กี่ %? โตเพราะอะไร? คุณภาพรายได้ดีไหม?
2. MARGIN: Gross/Operating Margin trend? มี Operating Leverage ไหม?
3. FCF QUALITY: ROIC > WACC ไหม? Cash Conversion ดีไหม?
4. MOAT: ประเภทไหน? แข็งแค่ไหน? กำลังกว้างหรือแคบลง?
5. BALANCE SHEET: Net Debt/EBITDA, Interest Coverage, Dilution trend
6. MANAGEMENT: Capital allocation track record อย่างไร?
7. VALUATION: DCF range, PEG, EV/Sales vs Peer — แพงหรือถูก?
8. VERDICT: Pass/Fail กี่ Layer? ควรซื้อ/ดูเพิ่ม/หลีกเลี่ยง? Position Size?

สรุปเป็น Investment Thesis 3-5 ประโยค และ Bull/Bear Case
```

### Quick Scan Prompt (5 นาที)

```
วิเคราะห์ [TICKER] แบบ quick fundamental check:

1. Revenue CAGR 3 ปี = ?% — ดีพอไหม vs อุตสาหกรรม?
2. Gross Margin trend = ขึ้นหรือลง?
3. FCF มาตามกำไรไหม? (FCF vs Net Income)
4. Net Debt/EBITDA = ?x — รอดไหม?
5. ROIC vs WACC — ยิ่งโตยิ่งรวยไหม?
6. Moat ประเภทใด? แข็งแค่ไหน?
7. Forward P/E vs EPS growth rate (PEG)
8. MOS % จาก DCF fair value

ให้ผล Pass/Fail แต่ละ Layer และ Overall Rating: BUY/WATCH/AVOID
```

---

## Integration กับ AlphaAbsolute PRISM

### Fundamental Layer → PRISM Mapping

| 8-Layer Item | PRISM Component | Gate |
|-------------|-----------------|------|
| Revenue Growth (Layer 1) | CANSLIM C+A | EMLS Earnings Acceleration |
| Margin + ROIC (Layer 2-3) | CANSLIM C | EPS Acceleration Score |
| Moat (Layer 4) | NRGC Narrative Layer | Phase 2-3 Timing |
| Balance Sheet (Layer 5) | Risk Guardian T3 | ADTV + Liquidity Gate |
| Valuation (Layer 7) | EMLS Score | Cap/Score Gate |
| Position Size (Layer 8) | Base Count Gate | Size Rules |

### เมื่อไหร่ต้องใช้ 8-Layer vs PRISM

**ใช้ 8-Layer ก่อน เมื่อ:**
- Research หุ้นใหม่ที่ยังไม่รู้จัก
- Validate thesis ก่อนเข้า Phase 2-3
- ประเมิน Management quality
- Long-term holding (3-5 ปี) ต้องการ Moat Analysis

**ใช้ PRISM เป็นหลัก เมื่อ:**
- Timing จังหวะซื้อ (ไม่ใช่ว่าบริษัทดี แต่ breakout timing ถูกไหม)
- Phase 2→3 transition detection
- Stop loss / Sell decision
- Daily portfolio management

> "Fundamental บอกว่า "อะไร" ควรถือ — PRISM บอกว่า "เมื่อไหร่" ควรซื้อ"

---

*Last updated: 2026-05-16 | Source: Earthh Evans Framework + AlphaAbsolute Integration*
*Google Drive: ชุดคู่มือการลงทุน by Earth*
