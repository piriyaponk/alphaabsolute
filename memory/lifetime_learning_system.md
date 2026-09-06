# AlphaAbsolute — Lifetime Learning System
## "ทีม Macro Strategist เก่งขึ้นทุกสัปดาห์ อัตโนมัติ"

---

## THE FLYWHEEL — วงจรการเรียนรู้

```
                    ┌─────────────────────────────────┐
                    │                                 │
                    ▼                                 │
            ┌──────────────┐                         │
            │  DAILY CALL  │                         │
            │ Regime Call  │ ← 7-Step Framework      │
            │ + Score      │ ← Self-Debate Protocol  │
            │ + Non-consen │ ← Cross-Asset Confirm   │
            └──────┬───────┘                         │
                   │ auto-save to                    │
                   ▼                                 │
            ┌──────────────┐                         │
            │   SNAPSHOT   │                         │
            │ JSONL Record │ regime_snapshots.jsonl  │
            │ 7 factors    │                         │
            │ key_data{}   │                         │
            └──────┬───────┘                         │
                   │ 1 week later                    │
                   ▼                                 │
            ┌──────────────┐                         │
            │   OUTCOME    │                         │
            │ VALIDATION   │ What actually happened? │
            │ SPX return   │ Regime correct?         │
            │ Signal fired?│                         │
            └──────┬───────┘                         │
                   │ Friday                          │
                   ▼                                 │
            ┌──────────────┐                         │
            │ CALIBRATION  │                         │
            │ Signal hits/ │ Bayesian weight update  │
            │ misses count │ Dead signals penalized  │
            │ Accuracy %   │ Strong signals promoted │
            └──────┬───────┘                         │
                   │ feeds into                      │
                   ▼                                 │
            ┌──────────────┐                         │
            │   PLAYBOOK   │                         │
            │   UPDATE     │ What worked → record    │
            │ + FINGERPRINT│ What failed → error log │
            │   LIBRARY    │ Pattern matching grows  │
            └──────┬───────┘                         │
                   │ improves                        │
                   └─────────────────────────────────┘
                        NEXT REGIME CALL IS BETTER
```

---

## 5 LAYERS OF LEARNING

### Layer 1: Daily Collection (ทุกวัน — Automatic)
```
SCRIPT: macro_learner.py auto_snapshot_from_clearance()
TRIGGER: ทุกเช้าหลัง market_regime.py รัน
DATA STORED:
  - วันที่ + เวลา
  - Regime name + score (X/30)
  - Factor breakdown (Liquidity/Cycle/Inflation/Credit/Internals/Earnings)
  - Key data (10Y yield, HY spread, yield curve, VIX)
  - Top signals used
  - Non-consensus view ของวันนั้น
OUTPUT: data/macro_learning/regime_snapshots.jsonl (append-only)
```

### Layer 2: Weekly Outcome Check (ทุกวันศุกร์ — Manual → Auto later)
```
ACTION: บันทึก outcome ว่า 1 สัปดาห์หลัง call → ตลาดเป็นยังไง
INPUT:
  - snapshot_date (วันที่ call นั้น)
  - actual_regime (regime ที่เกิดจริง)
  - spx_return_1w (SPX return ในสัปดาห์นั้น)

HOW TO CALL:
  from scripts.pre_compute.macro_learner import record_outcome
  record_outcome("2026-05-16", "STAGFLATION_LITE", -0.8)

WHAT TRIGGERS NEXT:
  → Automatic calibrate_signals() runs
  → Signal weights update
```

### Layer 3: Signal Calibration (ทุกศุกร์ — Automatic)
```
SCRIPT: macro_learner.py calibrate
LOGIC: Bayesian update
  - count hits/misses for each signal
  - blend empirical accuracy with prior weight
  - เยิ่งมี data → เยิ่งเชื่อ empirical
  - dead signal (< 40%) → weight ลด 50%
  - strong signal (> 70%) → weight เพิ่ม

OUTPUT: data/macro_learning/signal_performance.json
```

### Layer 4: Fingerprint Library (Grows over time)
```
WHEN: ทุกครั้งที่ regime เปลี่ยน → save "fingerprint" ของ onset conditions
DATA: Factor scores ณ วันที่ regime เริ่มต้น

USAGE (future): 
  "ตอนนี้หน้าตาเหมือน Stagflation onset Q2 2022"
  match_current_to_fingerprint(current_scores) → [(STAGFLATION, 87%), ...]

GROWS: ทุก regime transition = 1 instance ต่อ regime type
After 5 instances per regime → "typical fingerprint" ที่เชื่อถือได้
```

### Layer 5: Playbook Evolution (Monthly)
```
HUMAN REVIEW: ทุกเดือน CIO อ่าน learning report + update playbook
ADD TO PLAYBOOK:
  - Error log เมื่อ call ผิด + lesson
  - Successful patterns ที่พิสูจน์แล้ว
  - New signals ที่ observe
  - Framework updates

FILE: memory/macro_playbook.md (grows manually)
```

---

## WEEKLY WORKFLOW (ทำทุกวันศุกร์ 10 นาที)

```
FRIDAY MORNING MACRO REVIEW:

Step 1: อ่าน macro_learning report ที่ auto-generate แล้ว
        → output/macro_learning_YYMMDD.md
        → ดู accuracy stats, signal performance

Step 2: บันทึก outcome ของสัปดาห์ที่ผ่านมา (manual)
        ถามตัวเอง:
        - Regime call เมื่อวันจันทร์ถูกต้องไหม?
        - SPX week return เป็นเท่าไหร่?
        - Signal อะไรที่ "fired" ถูกต้องที่สุด?
        
        Call: record_outcome("2026-05-12", "STAGFLATION_LITE", -0.8)

Step 3: Update Error Log (ถ้า call ผิด)
        memory/macro_playbook.md → Section F: Error Log
        - ผิดเรื่องอะไร?
        - เหตุผลที่ผิด (root cause)?
        - Rule ใหม่ที่เพิ่มเข้า framework?

Step 4: Update Narrative Tracker
        memory/macro_playbook.md → Section E
        - Narrative ไหน shift phase?
        - Narrative ใหม่กำลังจะ emerge?

Step 5: Scan Non-Consensus Scorecard
        - Non-consensus view ของสัปดาห์ที่แล้ว validate แล้วไหม?
        - ถ้าถูก → เพิ่ม confidence ใน non-consensus process
        - ถ้าผิด → analyze ว่าทำไม

Total time: ~10 minutes ต่อสัปดาห์
```

---

## MONTHLY DEEP REVIEW (1 ชั่วโมง ต้นเดือน)

```
1. ACCURACY REVIEW
   - Regime accuracy % เดือนที่ผ่านมา
   - Signal ที่ outperform/underperform คาด

2. SIGNAL AUDIT  
   - Dead signals? (accuracy < 40% หลัง ≥5 samples)
   - New signals ที่ควรเพิ่ม?
   - Existing signals ที่ควรปรับ definition?

3. FRAMEWORK UPDATE
   - Decision tree ไหนควรเปลี่ยน threshold?
   - New regime ที่ encounter แล้วยังไม่มีใน classification?

4. PLAYBOOK ENRICHMENT
   - เพิ่ม historical precedent ใน per-regime playbook
   - อัปเดต timing signals ตาม latest data

5. NARRATIVE LIFECYCLE REVIEW
   - ทุก narrative เคลื่อนไป phase ไหน?
   - Phase transition triggers ที่ observe ใน month นี้

python scripts/pre_compute/macro_learner.py meta
→ generates meta-calibration report
```

---

## QUARTERLY MASTER REVIEW

```
Q1/Q2/Q3/Q4 → Full framework audit

1. Backtesting: รัน framework ย้อนหลัง 3 เดือน
   → กี่ % ของ regime transitions ตรวจเจอ?
   → กี่ % ของ non-consensus views validated?

2. Benchmark: เทียบ vs consensus / market
   → ถ้า accuracy ≤ 55% → framework ต้องรีวิวใหญ่
   → ถ้า accuracy > 70% → framework ทำงานดี

3. New Research Integration:
   → อ่าน research ใหม่จาก Goldman, BofA, Bridgewater
   → Incorporate insights ที่ยัง missing

4. Signal Weight Reset (optional):
   → ถ้า market structure เปลี่ยน significantly
   → Reset weights กลับ prior แล้วเริ่ม calibrate ใหม่
```

---

## HOW "เก่งขึ้น" Looks Like

### Timeline ของ Intelligence Growth

```
WEEK 1-4:
  - Framework ทำงานตาม OS spec
  - Signal weights = prior (ยังไม่มี empirical data)
  - Fingerprint library = empty
  - ERROR: อาจ miss regime transitions

MONTH 2-3:
  - 8-12 regime snapshots บันทึกแล้ว
  - 4-6 outcomes validated
  - Signal weights เริ่ม reflect empirical accuracy
  - ระบุ dead signals ได้ครั้งแรก
  - IMPROVEMENT: timing accuracy ดีขึ้น

MONTH 4-6:
  - 24-36 snapshots
  - 10-15 outcomes
  - Calibrated weights significant different จาก prior
  - 2-3 fingerprint instances per regime type
  - Pattern matching เริ่มทำงาน
  - IMPROVEMENT: detect regime onset 1-2 weeks earlier

YEAR 1:
  - ~200 daily snapshots
  - ~50 weekly outcomes validated
  - Strong/dead signals clearly identified
  - Full fingerprint library for all 9 regimes
  - Non-consensus view accuracy > 55%
  - IMPROVEMENT: comparable to junior institutional strategist

YEAR 2-3:
  - Regime call accuracy > 70%
  - Fingerprint matching predicts onset 2-3 weeks early
  - Framework continuously self-updating
  - Error log has 20+ entries = institutional memory rich
  - LEVEL: Comparable to experienced macro strategist
```

---

## FILE STRUCTURE

```
AlphaAbsolute/
├── data/macro_learning/
│   ├── regime_snapshots.jsonl      ← append-only daily log (grows forever)
│   ├── signal_performance.json     ← auto-updated weekly
│   ├── fingerprints.json           ← regime pattern library
│   └── playbook_data.json          ← structured outcome data
│
├── scripts/pre_compute/
│   └── macro_learner.py            ← THE ENGINE
│
├── memory/
│   ├── macro_regime_os.md          ← Operating System (never change structure)
│   ├── macro_regime_log.md         ← Weekly regime calls (human readable)
│   ├── macro_playbook.md           ← Evolving knowledge base
│   ├── macro_cycle_framework.md    ← Foundational concepts
│   └── lifetime_learning_system.md ← This file (architecture)
│
└── output/
    ├── macro_learning_YYMMDD.md    ← Weekly auto-generated reports
    └── macro_meta_cal_YYMMDD.md   ← Monthly meta-calibration
```

---

## COMMANDS REFERENCE

```bash
# DAILY (automatic via pre_market_runner.py)
python scripts/pre_compute/macro_learner.py snapshot

# WEEKLY FRIDAY — Record outcome
python -c "
from scripts.pre_compute.macro_learner import record_outcome
record_outcome('2026-05-16', 'STAGFLATION_LITE', -0.8, notes='Iran war confirmed energy impact')
"

# WEEKLY FRIDAY — Generate report (automatic)
python scripts/pre_compute/macro_learner.py report

# MONTHLY — Meta-calibration
python scripts/pre_compute/macro_learner.py meta

# ANYTIME — Check status
python scripts/pre_compute/macro_learner.py status
```

---

## THE CORE PRINCIPLE

> "ระบบนี้เก่งขึ้นเพราะมัน RECORDS, COMPARES, UPDATES"
> ไม่ใช่เพราะ AI똑똑ขึ้นเอง
>
> ทุก regime call ที่บันทึก = data point
> ทุก outcome ที่ validate = feedback
> ทุก feedback = weight update
> ทุก weight update = better next call
>
> 1 ปีของ daily records = 200+ data points
> 200+ data points = better than most junior analysts
> 3 ปีของ daily records = institutional-grade macro intelligence

---

*Architecture Document v1.0 | Created: 2026-05-16*
*System: AlphaAbsolute Lifetime Learning | Agent: 01 Macro Intelligence*
