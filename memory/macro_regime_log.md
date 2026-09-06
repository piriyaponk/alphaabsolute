# Macro Regime Learning Log
## Agent 01 Weekly Regime Calls + Signal Performance

> นี่คือ "Memory ของ Macro Strategist" — บันทึกทุก regime call พร้อม evidence
> ทบทวนทุกวันศุกร์เพื่อ calibrate signal weights

---

## Signal Weight Registry (Updated from Empirical Evidence)

```
SIGNAL                  │ WEIGHT │ VALIDATION COUNT │ LAST UPDATED
────────────────────────┼────────┼──────────────────┼─────────────
HY Spread Direction     │  20%   │ 0                │ 2026-05-16
PMI New Orders          │  18%   │ 0                │ 2026-05-16
Real Rate Direction     │  17%   │ 0                │ 2026-05-16
Yield Curve Slope       │  15%   │ 0                │ 2026-05-16
A/D Line vs Price       │  12%   │ 0                │ 2026-05-16
EPS Revision Breadth    │  10%   │ 0                │ 2026-05-16
SLOOS Net Tightening    │   8%   │ 0                │ 2026-05-16
```

*Weights update when signal hits 10 validation instances*

---

## Regime Call History

### 2026-05-16 — INITIAL BASELINE

```
REGIME: STAGFLATION LITE (Transitioning)
SCORE: 17/30
CONFIDENCE: Medium

MACRO SCORECARD:
  Liquidity:  2/5 — Real rate 1.7%, Fed QT, DXY elevated
  Econ Cycle: 3/5 — PMI ~50, flat, not recession
  Inflation:  2/5 — Iran war energy shock, re-accelerating
  Credit:     4/5 — HY 2.76%, tight, no stress
  Internals:  3/5 — Narrow breadth, small cap lagging
  Earnings:   3/5 — Mixed, AI capex strong, consumer weak
  TOTAL: 17/30

KEY DATA:
  10Y Yield:    4.47% (rising from 4.1% in March)
  Yield Curve:  +0.50% (non-inverted, positive signal)
  HY Spread:    2.76% (very tight, best signal in the board)
  VIX:          ~19 (moderate)
  Fed pricing:  0 cuts in 2026, some traders pricing hike

SELF-DEBATE SUMMARY:
  Bull: Credit calm = no imminent crisis. AI capex secular.
  Bear: Stagflation building. Fed stuck. ERP near zero.
  Non-consensus: Iran war = underpriced inflation re-acceleration risk
  Pain trade: Long growth/tech if Fed hikes instead of cuts

NARRATIVE LIFECYCLE TRACKING:
  AI: Phase 3-4 (earnings confirming, but crowding concern)
  Memory/HBM: Phase 2-3 (thesis building, not yet consensus)
  Space: Phase 1-2 (early, asymmetric)

ALPHAABSOLUTE ACTION:
  M0 Regime: 5 (CORRECTION)
  Cash Target: 45-55%
  New Position Max: 5%
  Cleared: Best-of-best only (EMLS > 80, RS > 85th)
  Blocked: New speculative buys, hypergrowth, bottom fish
  Held: COHR (monitor closely, Base 3, TD Setup approaching)

WHAT WOULD CHANGE THIS CALL:
  ↑ Bull: Iran ceasefire → energy prices collapse → inflation falls → 
          market re-prices cuts → risk-on resumes
  ↓ Bear: CPI print shows acceleration to 4%+ →
          Fed signals hike → HY spreads widen → Credit stress begins
  
NEXT REVIEW: Next CPI release + Friday 2026-05-23
```

---

## Signal Performance Tracker

```
DATE       │ SIGNAL            │ PREDICTION        │ OUTCOME │ CORRECT?
───────────┼───────────────────┼───────────────────┼─────────┼─────────
2026-05-16 │ [Baseline — no prediction yet to validate]
```

---

## Regime Transition Log

```
DATE       │ FROM REGIME      │ TO REGIME        │ TRIGGER          │ SIGNALS THAT FIRED FIRST
───────────┼──────────────────┼──────────────────┼──────────────────┼──────────────────────────
(no transitions yet — baseline established 2026-05-16)
```

---

## Lessons Learned Archive

```
[Empty — will fill as regime calls are validated/invalidated]

Format:
DATE: [date]
LESSON: [what I learned]
SIGNAL UPDATED: [which signal weight changed]
FRAMEWORK UPDATE: [what changed in decision tree]
```

---

## Non-Consensus Calls Tracker

```
DATE       │ NON-CONSENSUS VIEW                    │ VALIDATED? │ NOTES
───────────┼───────────────────────────────────────┼────────────┼──────
2026-05-16 │ Iran war = underpriced inflation risk  │ PENDING    │ Next CPI will tell
           │ Market complacent on rate hike risk    │            │
```

---

*Start Date: 2026-05-16 | Agent 01 Global Macro Intelligence*
*Review: Every Friday | OS Reference: memory/macro_regime_os.md*
