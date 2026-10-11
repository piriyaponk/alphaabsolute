# AlphaModel-TH — Complete System Instructions
## For AI Learning & Onboarding
### Version 1.0 | 2026-10-10 | AlphaAbsolute Research

---

## 1. Overview: What Is AlphaModel-TH?

AlphaModel-TH เป็นระบบ quantitative equity screener และ paper trading สำหรับ **ตลาดหลักทรัพย์แห่งประเทศไทย (SET)** โดยเฉพาะ มีรากฐานมาจาก AlphaAbsolute System 4 (US) แต่ออกแบบใหม่ให้เหมาะกับลักษณะเฉพาะของตลาดไทย

### Core Thesis
> **ตลาดไทยตอบสนองต่อ FVG + Volume + Structure ดีกว่า Pure Momentum**  
> (จาก Thai Q-Signal Research 825 Q-signals, 2026-09-13)

### System Goals
- **รัน daily** หลัง SET ปิด (16:30 ICT)
- **คัด Focus List**: หุ้น High-RS 15 ตัวบน
- **PULSE-TH Overlay**: ให้คะแนน hit-rate บน Focus List
- **Paper Trading**: ทดสอบ COMBINED config อัตโนมัติ
- **Telegram Push**: ส่งผลทุกวันก่อนตลาดเปิดวันถัดไป

---

## 2. Universe & Data

### Stock Universe
- **แหล่งที่มา**: Yahoo Finance (`.BK` tickers) — primary; query2 + query1 endpoint
- **History**: 2015-01-01 จนถึงปัจจุบัน (~10 ปี)
- **Database**: `data/research/thai_ohlcv.db` (SQLite)
  - Table: `thai_ohlcv (ticker, date, open, high, low, close, volume)`
  - Table: `set_index_history (date, close)` — SET index level รายวัน
- **Universe builder**: `scripts/research/thai_universe_builder.py`

### Eligible Universe Filter (ใช้เวลา Screen/Backtest ไม่ใช่ตอน Fetch)
```
ADTV >= 20,000,000 THB   (avg daily turnover 6 เดือน = close × volume)
Price >= 1.00 THB
ไม่รวม: TDEX.BK, ^SET.BK (SET index ETF/proxy — ใช้เป็น benchmark เท่านั้น)
```

### SET Index (Regime Proxy)
ลำดับความสำคัญ (priority order):
1. **TDEX.BK** — iShares SET ETF บน Yahoo Finance (primary; มี history)
2. **Synthetic proxy** — equal-weight ADVANC.BK + PTT.BK + KBANK.BK (ถ้า TDEX stale)
3. **Investing.com** — ผ่าน `fetch_set_direct()` ใน `thai_data_layer.py`

### Data Update Flow
```
Daily (EOD ~17:30 ICT):
  thai_eod_update.yml → scripts/ci/th_eod_set_index.py
    → update_set_index_history() ใน thai_data_layer.py
    → fetch จาก tvremix.xyz (WebSocket, 300 bars)
    → เขียน data/research/thai_ohlcv.db (table: set_index_history)
    → commit + push to GitHub

Paper Trade (~18:00 ICT):
  thai_paper_trade.yml → หลาย scripts ตามลำดับ (ดู Section 7)
```

---

## 3. RS Ranking — How Focus List Is Built

### RS Formula: COMBINED config
```
RS_DAYS = 21      # lookback สำหรับ return calculation
TOP_N   = 10      # top stocks ใน portfolio
rs_type = "vol_weight"   # volume-weighted return
```

**Volume-Weighted RS (vol_weight)**:
```python
# สำหรับแต่ละหุ้น:
close_21d_ago = prices[today - 21 trading days]
return_21d    = close_today / close_21d_ago - 1

# Weight by average volume (6 months):
adtv_6m = avg(close × volume, lookback=126 days)
rs_score_raw = return_21d × adtv_6m

# Rank ทุกหุ้นใน eligible universe → rs_percentile (0-100)
rs_pct_3m = percentile rank ของ 3-month return vs ทุกหุ้นใน universe
```

### Focus List = Top 15 by RS
- คัดจาก eligible universe (ADTV ≥ 20M, Price ≥ 1 THB)
- Rank by RS percentile (3-month หลัก)
- เอา top 15 → **Focus List**
- Focus List นี้คือ input ของ PULSE-TH daily screener

### Regime Gate (MA50)
```
Regime = SET index (TDEX.BK) vs MA50
  BULL  : SET price > MA50 → ลงทุนปกติ
  BEAR  : SET price < MA50 → ถือ cash 100%, ไม่ rebalance
```

---

## 4. PULSE-TH — Signal Scoring System

### คืออะไร
PULSE-TH = ระบบ overlay ที่ให้คะแนน "buy signal quality" ให้แต่ละหุ้นใน Focus List  
**ไม่ได้ filter หุ้น** — แต่บอกว่าหุ้นในรายชื่อตัวไหน "มีสัญญาณ quality สูง" วันนี้

### Signal Library: q_library_th.json
```
Path: data/research/thai_pulse/q_library_th.json
Source: build จาก thai_entry_screen_results.csv (39,000+ rows, 7,659 Q-signals)
Calibration: fwd5 (5-day forward return > 0 = "hit")
Baseline hit rate: 45.7% (random = no skill)
```

**Grade thresholds** (TH calibrated on fwd5):
| Grade | h3 threshold | จำนวน signals |
|-------|-------------|----------------|
| S_BOTH | ≥ 80% | 24 signals |
| S | ≥ 70% | 1,607 signals |
| A | ≥ 63% | 302 signals |
| B | < 63% | 1,092 signals |
| **Quality (A+S+S_BOTH)** | **≥ 63%** | **1,933 signals** |

Quality signals = 1,933 สัญญาณที่ใช้จริงใน PULSE-TH  
Baseline = 45.7% → Quality signals เริ่มที่ 63% = เหนือ baseline อย่างมีนัย

**ตัวอย่าง S_BOTH signal** (h3=84.2%, N=19):
```
Q_CMB_Q670_j3_f1_j6_J8_pb_zone_ma50
→ Combo signal: j3 (CHOCH/structure) + f1 (FVG) + j6 + J8 + pb_zone + ma50 filter
→ ทุก 19 ครั้งที่ fire → 84.2% of the time หุ้นขึ้นใน 5 วันถัดไป
```

### Signal Families
แต่ละ Q-signal มี "family" (กลุ่มสัญญาณ):
- **f1** = FVG (Fair Value Gap) — imbalance zone สำคัญ
- **j3** = CHOCH (Change of Character) — structure break
- **j6, J8** = momentum / structure variants
- **mom** = momentum signals
- **i9, i8** = indicator-based
- **pb_zone** = pullback zone filter
- **ma50** = above MA50 filter
- จำนวน distinct families ใน quality signals = **28 families**

### PULSE Score Formula
```python
# สำหรับแต่ละหุ้นใน Focus List:

fired_signals = [sig for sig in quality_signals if sig fired today for this ticker]

# Breadth: กี่ family ที่ fire (ไม่ใช่จำนวน signals)
breadth     = len(distinct families that fired ≥1 signal)
breadth_pct = breadth / 28 * 100   # % ของทั้งหมด 28 quality families

# avg_h3: ค่าเฉลี่ย h3 ของ top-3 signals ที่ fire (เรียง h3 สูงไปต่ำ)
top3_h3 = sorted([sig.h3 for sig in fired_signals], reverse=True)[:3]
avg_h3  = mean(top3_h3)   # % (0-100)
```

**Ranking logic**:
```python
score = avg_h3 * 1000 + breadth_pct   # h3 primary, breadth tiebreaker
```

**Icons**:
- 🔴 = avg_h3 ≥ 80% (S_BOTH level)
- 🟠 = avg_h3 ≥ 75%
- 🟡 = avg_h3 ≥ 63% (A level)
- ⚪ = avg_h3 = 0 (no quality signal fired)

---

## 5. Daily Outputs: What PULSE-TH Sends

### Message 1 — Focus List (RS-ranked, PULSE overlay)
```
[TH] PULSE-TH Daily | 10 Oct 2026
Quality signals: 1933 | Families: 28 | Tickers hit: 8

📊 Focus List (RS-ranked, PULSE overlay)
hr = avg h3 of top-3 quality signals (fwd5 hit rate)
breadth = % of families fired / 28 quality families

🔴 HANA.BK  RS=92%  hr=84.2%  br=25.0%
⚪ PTT.BK   RS=88%
🟡 KBANK.BK RS=85%  hr=67.1%  br=10.7%
...
```

**ความหมายของแต่ละ column**:
- **RS** = RS percentile ของหุ้น vs ทุกหุ้น SET (0-100%)
- **hr** = hit rate เฉลี่ยของ top-3 quality signals ที่ fire วันนี้ — โอกาสที่หุ้นขึ้นใน 5 วัน
- **br** = % ของ signal families ที่ fire — ยิ่ง breadth สูง = หลาย ๆ มิติยืนยันพร้อมกัน

### Message 2 — Top 5 by PULSE Score (Independent of RS)
```
[TH] PULSE-TH Top 5 | 10 Oct 2026
rank: HR primary, Breadth tiebreaker — independent of RS

1. 🔴 HANA.BK
   hr=84.2%  breadth=25.0% (7/28 fam)  RS=92%
2. 🟠 DELTA.BK
   hr=76.5%  breadth=17.8% (5/28 fam)  RS=71%
...
```

**Top 5 ≠ Focus List rank** — Top 5 rank by PULSE score (hr + breadth), ไม่ใช่ RS  
→ บางครั้งหุ้น RS ต่ำอาจมี signal quality สูงกว่า

---

## 6. PULSE-TH vs Focus List — ความแตกต่าง

| | Focus List | PULSE Top 5 |
|--|-----------|-------------|
| Ranked by | RS percentile | avg_h3 × 1000 + breadth_pct |
| จำนวน | Top 15 RS | Top 5 quality signals |
| Purpose | หุ้นที่ Market Leader ใน SET | หุ้นที่มีสัญญาณ buy quality วันนี้ |
| Independent? | ใช่ | ใช่ (ไม่ขึ้นกับ RS rank) |
| Paper trade ใช้? | ✅ (COMBINED config ใช้ RS rank) | ❌ (PULSE เป็น research overlay) |

---

## 7. Paper Trading System

### Config: COMBINED
```python
rs_days    = 21      # ใช้ 21-day return
top_n      = 10      # hold top 10 หุ้น
rebal_days = 5       # rebalance ทุก 5 วัน (weekly)
regime_ma  = 50      # SET MA50 regime gate
rs_type    = "vol_weight"  # volume-weighted RS
```

**Baseline performance** (Locked 2026-09-12):
- Beat SET 8/8 Walk-Forward Test windows
- Avg Sharpe: 2.37
- Worst DD: -13.3%

### Portfolio Rules
```
Starting NAV    : ฿1,000,000
Position size   : Equal-weight (100% / top_n = 10% per stock)
Transaction cost: 0.15% per side (buy + sell)
Min price       : ฿1.00
ADTV min        : ฿20M/day
```

### Regime Control
```
BULL  (SET > MA50) : ลงทุน top 10 หุ้น, rebalance ทุก 5 วัน
BEAR  (SET < MA50) : ออกทุก position → ถือ cash 100%
```

### Daily Paper Trade Flow
```
Step 1: Update thai_ohlcv.db (OHLCV ล่าสุด)
Step 2: Update PULSE-TH signals (thai_pulse_daily.py → 21 นาที)
Step 3: Fill combo columns (thai_fill_combo_columns.py)
Step 4: Run PULSE-TH screener (thai_entry_screen.py)
Step 5: Run paper trader (thai_paper_trader.py)
  → Compute RS, eligible universe, regime
  → Decide: hold / rebalance / go cash
  → Update state JSON
  → Send Telegram
Step 6: Commit → rebase → push
```

---

## 8. Files & Paths

```
AlphaAbsolute/
├── scripts/research/
│   ├── thai_data_layer.py          ← Data fetch, DB init, SET index update
│   ├── thai_universe_builder.py    ← Scan + download all .BK tickers
│   ├── thai_entry_screen.py        ← Compute Q-signals for all tickers
│   ├── thai_fill_combo_columns.py  ← Compute combo (CMB) signals
│   ├── thai_paper_trader.py        ← Daily paper trade execution
│   ├── thai_paper_log.json         ← (output) trade log
│   ├── thai_paper_portfolio.json   ← (output) current portfolio state
│   └── thai_pulse/
│       ├── build_q_library_th.py   ← Build signal library (run once/periodically)
│       └── thai_pulse_daily.py     ← Daily PULSE-TH screener + Telegram
│
├── data/research/
│   ├── thai_ohlcv.db               ← Main SQLite DB (thai_ohlcv + set_index_history)
│   ├── thai_entry_screen_results.csv   ← Q-signals per ticker per date
│   ├── thai_entry_screen_summary.csv   ← Per-signal hit rates (baseline)
│   ├── thai_paper_portfolio.json   ← Current paper portfolio
│   ├── thai_paper_log.json         ← Trade history
│   └── thai_pulse/
│       ├── q_library_th.json       ← 7,659 scored signals (1,933 quality)
│       └── pulse_th_daily_signals.json  ← Today's PULSE scores per ticker
│
├── scripts/ci/
│   └── th_eod_set_index.py         ← CI script: update SET index only (fast, EOD)
│
└── .github/workflows/
    ├── thai_eod_update.yml          ← EOD SET index update (17:30 ICT)
    └── thai_paper_trade.yml         ← Full paper trade run (~18:00 ICT)
```

---

## 9. Key Research Findings (2026-09-13)

Source: Thai Q-Signal Research, 825 Q-signals tested

### What Works in SET Market
1. **FVG (Fair Value Gap) + Volume + Structure** → ทำงานดีที่สุด
2. **Pure Momentum** → ไม่ทำงานดีใน SET (ต่างจาก US)
3. **Best patterns**:
   - PERFECT: Q872/Q916 (I4+A3+FVG) → h3=100%
   - Q883/Q886 (CHOCH) → h3=88.9%
4. **Best combo**: j3 + f1 + j6 + J8 + pb_zone + ma50 → h3=84.2%

### Signal Quality Baseline
```
Baseline hit rate (ซื้อทุกหุ้น fwd5):  45.7%
Quality signal floor (Grade A):         63.0%
Grade S:                                70.0%
Grade S_BOTH:                           80.0%
Best known signals:                     84.2%
```

### COMBINED Config Rationale
- `rs_days=21` (ไม่ใช่ 63) → เหมาะกับ SET velocity ที่เร็วกว่า S&P
- `vol_weight` → volume-weighted RS จัดการ penny stocks ที่อาจ distort RS
- `top_n=10` → concentration ที่ทดสอบแล้วว่า win

---

## 10. Telegram Message Interpretation Guide

### [TH] Paper Trade Message
```
[TH] AlphaModel Paper Trade — 10 Oct 2026
Regime: BULL (SET 1580 > MA50 1520)
Portfolio: 10 positions | Cash: 0%
NAV: ฿1,082,500 (+8.25%) vs SET +3.1% | Alpha: +5.15%

Rebalanced today:
  EXIT: PTT.BK (RS dropped, -1.2%)
  ENTER: HANA.BK (RS=92%, new entrant)
```

**ความหมาย**:
- **BULL/BEAR** = Regime จาก SET vs MA50
- **NAV** = มูลค่า paper portfolio ปัจจุบัน
- **Alpha** = เหนือ SET index กี่ %
- **EXIT** = หุ้นที่ RS ตกออกจาก top 10
- **ENTER** = หุ้นใหม่ที่เข้า top 10

### [TH] PULSE-TH Messages (2 messages/day)
ดู Section 5 ด้านบน

---

## 11. Rules for AI Agents Working on AlphaModel-TH

### DO
- ✅ ใช้ `thai_data_layer.py` functions เท่านั้น สำหรับ DB access
  - `init_db(conn)` — create tables if not exist
  - `update_set_index_history(conn, backfill=False)` — update SET index
  - `DB_PATH` — absolute path to thai_ohlcv.db
- ✅ ใช้ `vol_weight` RS type เสมอ (ไม่ใช่ simple return)
- ✅ ตรวจ Regime (SET vs MA50) ก่อน trade เสมอ
- ✅ ADTV >= 20M THB เป็น hard filter
- ✅ Ticker format: `.BK` suffix (เช่น `HANA.BK`, `DELTA.BK`)

### DON'T
- ❌ อย่า fetch ^SET.BK เป็น price history (Yahoo ไม่มี historical data)
- ❌ อย่า lower ADTV threshold ต่ำกว่า 20M THB
- ❌ อย่าใช้ simple 3-month return เป็น RS (ต้องใช้ vol_weight)
- ❌ อย่า trade ใน BEAR regime (cash 100%)
- ❌ อย่าเปลี่ยน COMBINED config โดยไม่ทำ Walk-Forward Test ก่อน
- ❌ อย่า average down — RS drop = exit, ไม่ใช่ buy more

### Anti-Patterns ที่พบจาก Thai Market Research
- **Pure momentum** ไม่ทำงานใน SET (ต้องมี structure/FVG เสริม)
- **ลด ADTV threshold** ทำให้ backtest ดีขึ้นแต่ live ล้มเหลว (penny stock trap)
- **ไม่ใช้ Regime gate** → ผลขาดทุนใน bear market รุนแรงมาก
- **Top N > 15** → dilute signal, alpha ลด

---

## 12. System Constraints & Limitations

### Data Constraints
- **tvremix limit**: 300 bars สำหรับ SET index history (~1.2 ปี)
- **Yahoo Finance**: บางครั้ง rate limit สำหรับ .BK tickers
- **No real-time data**: ระบบใช้ EOD data เท่านั้น
- **TDEX.BK staleness**: หาก TDEX.BK stale > 5 วัน → fallback to synthetic proxy

### Known Issues (As of 2026-10-10)
- PULSE-TH signal update ใช้เวลา ~21 นาที (ต้องปรับให้เร็วขึ้น)
- SET index historical data ก่อน 2025 มาจาก tvremix เท่านั้น (ไม่ครบ 10 ปี)

### Backtest vs Live Gap
- Backtest ใช้ OHLCV ที่ผ่านมา — ผลลัพธ์ ideal กว่า live เสมอ
- Live: transaction cost จริง 0.15% ต่อ side (บวก bid-ask spread จริง)
- Walk-Forward Test (8 windows) = วิธีที่ใช้ validate ว่า config ทำงานจริง

---

## 13. Quick Reference Commands

```bash
# Update SET index only (fast, ~5s)
python scripts/ci/th_eod_set_index.py

# Full OHLCV update (all .BK tickers, ~26s)
python -X utf8 scripts/research/thai_data_layer.py --update

# Run PULSE-TH screener (no Telegram)
python -X utf8 scripts/research/thai_pulse/thai_pulse_daily.py --no-telegram

# Run paper trader (no Telegram)
python -X utf8 scripts/research/thai_paper_trader.py

# Check DB status
python -X utf8 scripts/research/thai_data_layer.py --status

# Build signal library (after collecting new data)
python -X utf8 scripts/research/thai_pulse/build_q_library_th.py
```

---

*Document auto-generated from codebase analysis | AlphaAbsolute Research | 2026-10-10*
