# PULSE-US Research Log
**Started:** 2026-09-13
**Script:** `scripts/research/pulse_us_entry_screen.py`
**Data:** `data/ohlcv.db` (US), `data/research/pulse_us_entry_screen_results.csv`
**Status:** Phase 1 complete — initial backtest run

---

## Architecture
- Mirror of PULSE-TH signal library (A–Q groups) applied to US ohlcv.db
- Universe: Top-20 RS stocks (63-day momentum) from ~1,800 US tickers
- ADTV filter: $15M minimum
- Forward windows: fwd1, fwd2, fwd3, fwd5
- Added **R-group**: US-specific RS percentile + Minervini Trend Template signals
- Added **RQ combos**: R × Q combinations (RS filter + TH-validated signal)
- `data/research/pulse_us_entry_screen_results.csv` — raw backtest rows
- `data/research/pulse_us_entry_screen_summary.csv` — scored signal table

---

## Dataset (Phase 1 — 2026-09-13)
- Date range: 2024-08-21 – 2026-09-10
- Records: 10,300 (20 tickers × ~515 tradeable dates)
- Tickers: 342 unique (top-RS rotation)
- Regime: Bull 78.8% | Bear 13.4% | Unknown 7.8%
- Base rate (fwd3 > 0): **49.1%** (vs TH base rate ~50%)

---

## Key Findings — BULL Regime (Phase 1)

### ★ Standout Signals

| Signal | N | h3 | avg3 | h5 | avg5 | Notes |
|--------|---|----|------|----|------|-------|
| **Q32_4b_surge_ma50** | 15 | **66.7%** | **+15.0%** | 60.0% | +7.4% | ★★★ EXTRAORDINARY avg return — 3-day pullback + volume surge + above MA50 |
| **Q16_surge_3down_bounce** | 17 | 58.8% | **+12.2%** | 58.8% | +5.8% | ★★ Very high avg return — volume surge on 3-day pullback bounce |
| **P5_td_countdown_13** | 28 | 64.3% | +5.7% | **75.0%** | **+7.9%** | ★★★ fwd5 stronger than fwd3 — TD Countdown extends momentum |
| **P9_countdown_ma50** | 27 | 63.0% | +5.6% | **74.1%** | **+7.3%** | ★★ Same TD Countdown + above MA50 |
| **J6_dry_near_swing_lo** | 34 | **67.6%** | +3.3% | 59.4% | +5.0% | ★ Highest h3 (large enough N) — dry vol near swing low |
| **QB2_gap_after_cap** | 12 | 66.7% | +2.9% | 58.3% | +2.8% | Valid from TH — gap after capitulation |
| **I5_cap_reversal** | 53 | 58.5% | +3.7% | 60.4% | +2.2% | N=53, decent confidence |
| **Q38_k4_j8_mild_pb** | 16 | 62.5% | +2.1% | 50.0% | +1.4% | 3-day trade only (h5 drops) |

### Key Insight #1: TD Countdown works better on US (fwd5 > fwd3)
- TH: TD signals mainly fwd3 alpha
- US: **P5/P9 fwd5 = 75%** much stronger than fwd3 (64%) — US momentum extends past 3 days
- Implication: hold 5 days not 3 days after TD Countdown 13 fires in bull regime

### Key Insight #2: Volume surge + pullback = massive avg return
- Q32 (+15% avg3) and Q16 (+12.2% avg3) are far above anything else
- Pattern: stock pulls back 3 days then bounces with volume surge
- This is the highest-magnitude signal in US dataset
- US is MORE explosive on volume surge pullbacks than TH

### Key Insight #3: RS percentile alone is not predictive
- R1 (RS>70), R2 (RS>80), R3 (RS>90) all cluster at ~52% hit rate
- RS alone doesn't time entry — need price/volume action confluence
- R-group signals most useful as FILTER not as standalone signals

### Key Insight #4: Base rate = 49.1% (market is random on 3-day window)
- Best signals clear 65%+ (vs 49% base rate) = ~16pp edge
- Similar pattern to TH where base rate was ~50%

---

## Signals Needing More Data (N<15, hypothetical)
- QB2_gap_after_cap: N=12 (promising 66.7% but borderline)
- Q38_k4_j8_mild_pb: N=16 (valid but watch h5=50%)

---

---

## Phase 2 — Compound Search Results (2026-09-13)
**Script:** `scripts/research/pulse_us_compound_search.py`
**Output:** `data/research/pulse_us_pairs.csv` (2,485 pairs) | `data/research/pulse_us_triples.csv` (3,904 triples)
**Filter:** Bull regime only | Pairs: N≥10, h3≥60% | Triples: N≥8, h3≥65%

---

### ★★★★ EXTRAORDINARY — Top Triples by avg3 Return

| Triple | N | h3 | avg3 | h5 | avg5 |
|--------|---|----|------|----|------|
| **P4_td_perfect_setup + RS + F3_3day_dry** | 8 | 87.5% | **+33.25%** | 85.7% | +22.46% |
| **P4_td_perfect_setup + RS + J4_dry_value_area** | 8 | 87.5% | **+32.04%** | 87.5% | +30.22% |
| **P8_td_perfect_ma50 + RS + J4_dry_value_area** | 8 | 87.5% | **+32.04%** | 87.5% | +30.22% |
| P4_td_perfect_setup + RS + F1_vol_dry | 10 | 80.0% | +30.27% | 88.9% | +24.77% |
| P7_td9_bull_ma50 + RS + J4_dry_value_area | 9 | **88.9%** | +29.12% | 88.9% | +27.71% |
| P8_td_perfect_ma50 + RS + P2/P3/P4 | 10 | **90.0%** | +27.76% | 90.0% | +30.98% |
| R3_rs3_gt90 + P7_td9_bull_ma50 + P4/P8 | 10 | **90.0%** | +27.76% | 90.0% | +30.98% |

### ★★★ HIGH HIT RATE — Top Triples by h3

| Triple | N | h3 | avg3 | Notes |
|--------|---|----|------|-------|
| **C1_early_move + RS + I3b_gap_up_strong** | 8 | **100%** | +8.1% | Perfect hit rate |
| P6_td_countdown_8plus + RS + C1_early_move | 12 | 91.7% | +10.0% | N=12 solid |
| J1_dry_near_ma10 + R_acc + F4_dry_near_hi | 11 | 90.9% | +1.3% | High h3 but tiny avg |
| B1_tight_near_hi + RS + F4/F5_dry | 9 | 88.9% | +0.2% | High h3 but negligible return |

**Key observation on tight base family**: B1/B2/D5 + RS + F (vol dry) → h3=88-90% but avg3 near zero. 3-day window too short to capture the move — h5=87.5% but avg5=+0.39%. Hold for 5-10 days post-signal.

---

### ★★★★ Top Pairs (Bull)

| Pair | N | h3 | avg3 | Notes |
|------|---|----|------|-------|
| P7_td9_bull_ma50 + RS (any) | 11 | **90.9%** | **+25.76%** | Core TD9 + RS edge |
| P8_td_perfect_ma50 + RS (any) | 10 | 90.0% | +27.76% | Perfect setup + RS |
| E1_pb_ma20 + I2_power_move | 15 | 80.0% | +6.73% | Pullback + Power move |
| A3_vol_surge + N1_pp_at_ma20 | 15 | 80.0% | +11.56% | Vol surge + Pocket Pivot |
| O2_choch_10d + Q16/Q32 | 11 | 72.7% | +21.03% | CHoCH + Vol surge pullback |

---

### SIGNAL FAMILY TAXONOMY

**Family 1 — TD Countdown × RS (CORE EDGE):**
- Signal: P7_td9_bull_ma50 or P8_td_perfect_ma50 + RS>70%
- Edge: h3=90-91%, avg3=+25-28% (N=10-11)
- Interpretation: TD countdown near completion = stock already exhausted sellers, RS = institutions still accumulating → explosive 3-day move
- Action: Primary target signal for PULSE-US daily screen

**Family 2 — Perfect Setup × Volume Dry × RS (HIGHEST RETURN):**
- Signal: P4_td_perfect_setup + RS + F3_3day_dry or J4_dry_value_area
- Edge: h3=87.5%, avg3=+32-33% (N=8 — borderline)
- Interpretation: TD Perfect Setup (9 closes above prior close) + 3-day volume dry-up + RS > 70% = coiled spring about to explode
- Status: **HYPOTHESIS (N=8)** — confirm at N≥15

**Family 3 — Volume Surge Pullback × RSI/CHoCH:**
- Signal: Q16/Q32 + O2_choch_10d
- Edge: h3=72.7%, avg3=+21.03%, N=11
- Interpretation: 3-day volume surge bounce + market structure break = momentum ignition
- Action: Secondary target for PULSE-US

**Family 4 — Early Move × Gap Up × RS:**
- Signal: C1_early_move + I3b_gap_up_strong + RS
- Edge: h3=100%, avg3=+8.1% (N=8 — borderline)
- Status: **HYPOTHESIS (N=8)** — avg3 below top families

**Family 5 — Tight Base × Volume Dry (WRONG WINDOW):**
- Signal: B1/B2/D5 + RS + F_dry variants
- Edge: h3=88-90% but avg3 near 0%
- Finding: 3-day window too short — correct window is fwd5 or fwd10. Not useful for fwd3 scoring.

---

### KEY INSIGHTS from Phase 2

**Insight #5: TD Perfect Setup is the US-specific edge**
- P4_td_perfect_setup + RS = the highest avg-return pattern found
- avg3=+33% is more than 2× anything seen in TH (max ~+15%)
- US market has MORE explosive 3-day moves post-TD exhaustion than TH
- Mechanism: US has more institutional participation → when they decide to buy, moves are larger

**Insight #6: RS is a multiplier not a predictor**
- RS alone: h3=52% (random)
- RS + any good setup: h3 jumps to 80-90%
- RS = quality filter that ensures the move is institutional, not random

**Insight #7: Volume dry-up is the trigger**
- F1_vol_dry, F3_3day_dry, F4_dry_near_hi all appear in top triples
- Volume dry-up = institutions stop selling → next volume burst is a buy signal
- More predictive in combination (triple) than standalone

**Insight #8: fwd3 vs fwd5 tradeoff by signal family**
- TD Countdown family: fwd3 ≈ fwd5 (both strong)
- Tight Base family: fwd3 ≈ 0%, fwd5 significant — need 5+ days to resolve
- Volume surge family: fwd3 is the sweet spot (move happens fast)

**Insight #9: Autocorrelation problem in tight base + TD signals**
- Same ticker fires multiple consecutive days (e.g. CRNX fires 11 times in B1+RS+F4)
- After 5-day cooldown deduplication: B1+RS+F4 avg3 unchanged at ~0% (not a real signal)
- TD compound signals drop to N=4 after dedup (too few for conclusions)
- **IMPACT**: Q32 and Q16 survive dedup well (N=15/17 → still N=15/17, mostly different tickers)
- Q32 standalone: **15 truly independent observations, h3=67%, avg3=+15%** ← most reliable finding
- Q16 standalone: **17 independent observations, h3=59%, avg3=+12.2%** ← second most reliable

**Insight #10: Bear regime signals**
- Bear base rate = 48.5% (same as bull — market doesn't trend short in 3 days either)
- J7_vcp_zone_ma20 and J_combo_vcp_ma20: h3=65-66% in bear (N=55-59)
- These are valid bear-regime entry signals (VCP near MA20 works in ANY regime)
- M3_vdu_expansion_pb: h3=63.6%, avg3=+1.66% in bear (N=22) — modest but valid

**Insight #11: Seasonality**
- Worst months (bull): March (h3=37%, avg3=-2.02%), July (h3=42%, avg3=-1.34%)
- Best months: April (+4.26%), September (+2.11%), November (+2.34%), December (+2.92%)
- March/July = typical seasonal weakness periods in US markets
- October base rate only 44% despite being "sell in May" end — post-correction bounce starts slowly

---

---

## Phase 3 — Validation & Final Rankings (2026-09-13)

### PULSE-US Final Signal Priority Table (deduped, bull regime)

| Grade | Signal | N_dedup | h3 | avg3 | h5 | avg5 | Notes |
|-------|--------|---------|----|----|-----|------|-------|
| **S** | Q32: 3-pullback + vol surge + above MA50 | **15** | 67% | **+15.0%** | 60% | +7.4% | Best validated signal |
| **S** | Q16: vol surge on 3-day pullback bounce | **17** | 59% | **+12.2%** | 59% | +5.8% | Second best |
| **A** | P6+RS80+C1: countdown + RS + early move | **9** | 89% | +12.0% | 78% | +7.8% | High h3 but N=9 |
| **A** | P5: TD Countdown 13 (hold 5 days) | **11** | 55% | +10.5% | **73%** | **+16.1%** | fwd5>>fwd3 |
| **A** | P9: TD Countdown 13 + above MA50 | **10** | 50% | +11.0% | **70%** | **+15.3%** | fwd5>>fwd3 |
| **A** | J6: vol dry near swing low | **23** | 65% | +3.4% | 65% | +4.0% | Low return but consistent |
| **B** | QB2: gap up after capitulation | **12** | 67% | +2.9% | 58% | +2.8% | Valid |
| **B** | I5: capitulation reversal | **53** | 58% | +3.7% | 60% | +2.2% | Highest N |
| **B** | FVG + close quality + near 20d hi | **9** | 89% | +3.4% | 78% | +4.3% | High h3, low avg |

**Dropped from priority list**: J7_vcp_zone_ma20 (h3=40% after dedup — below base rate), B1/B2/D5 tight base family (avg near 0%), R1/R2/R3 RS alone (52% — random)

---

### Seasonality — Avoid March and July

| Month | Base Rate h3 | avg3 | Verdict |
|-------|-------------|------|---------|
| March | 37% | -2.0% | ❌ Avoid — worst month |
| July | 42% | -1.3% | ❌ Avoid |
| Jan, Oct | 44-48% | ~0% | ⚪ Neutral |
| Feb, Jun | 45-48% | ~0% | ⚪ Neutral |
| **Apr** | 58% | **+4.3%** | ✅ Best |
| Sep | 52% | +2.1% | ✅ Good |
| Nov | 52% | +2.3% | ✅ Good |
| Dec | 54% | +2.9% | ✅ Good |

---

## Phase 4 — Extended Search Session (2026-09-13 continued)

### New Validated Signals (Full-Period WF Testable)

#### I1b + I4b: The New Power Backbone

Discovered that `I1b_close_quality_hi_vol` (high-vol close quality) + `I4b_3down_bounce` (3-day pullback bounce, stronger version) form a powerful pair.

| Signal Combo | N | h3 | avg3 | h5 | h21 | avg21 | WF | Notes |
|---|---|---|---|---|---|---|---|---|
| **I1b + I4b (pair)** | 16 | 75.0% | +7.39% | 62.5% | 66.7% | +9.81% | **PASS** (nv=8, hv=75%) | New backbone |
| **I1b + I4b + E3_mild_pb** | 6 | **100%** | +16.71% | 66.7% | 66.7% | +13.72% | **PASS** (nv=4, hv=100%) | Small N but ALL correct |
| **I1b + I4b + I3b_gap** | 11 | 81.8% | +8.47% | 72.7% | 50% | -4.93% | **PASS** (nv=6, hv=83.3%) | N=11, strong |
| **I1b + I4b + H1_mom_resume** | 10 | 80.0% | +10.38% | 60% | 66.7% | +8.98% | **PASS** (nv=5, hv=80%) | Good return |
| **I1b + I4b + C2_above_ma50** | 15 | 80.0% | +8.23% | 66.7% | 64.3% | +8.56% | **PASS** (nv=8, hv=75%) | N=15, broadest |

Records verified for I1b+I4b+E3: AAOI, APLD, CDNA, FCEL, QUBT, UCTT (diverse, no autocorrelation).
Note: I1b+I4b+A3_vol_surge = same 6 records as I1b+Q32 (Q32 is essentially A3 + filter conditions — not independent).

#### Q32 Extended Pairs
| Signal Combo | N | h3 | avg3 | h21 | WF | Notes |
|---|---|---|---|---|---|---|
| **Q32 + O2_choch_10d** | 11 | 72.7% | +21.03% | 71.4% | **PASS** (nv=5, hv=80%) | New variant — 10d CHoCH works better |
| Q32 + H1_mom_resume | 13 | 69.2% | +17.17% | 66.7% | **PASS** (nv=6, hv=66.7%) | Below 70% threshold |

#### Q32 + O2_choch + I1 — Triple at 100%
| Signal Combo | N | h3 | avg3 | WF | Notes |
|---|---|---|---|---|---|
| **Q32 + O2_choch + I1_close_quality** | 6 | **100%** | +17.69% | **PASS** (nv=3, hv=100%) | Diverse: AMLX, APLD, BLZE, FCEL, QUBT, SMTC |

### Bear Regime — Defensive Signals
| Signal Combo (BEAR) | N | h3 | h5 | avg3 | Notes |
|---|---|---|---|---|---|
| J7_vcp_zone_ma20 (solo) | 41 | 68.3% | 65.9% | +0.63% | Most robust bear signal, N=41 |
| **I4 + K1 + J7 (BEAR)** | 6 | **100%** | **100%** | +3.47% | AMT, DUK, DY, RTX, SBAC, TMUS — all defensive |
| B1_tight + J7 (BEAR) | 9 | 88.9% | 77.8% | +1.45% | Tight base near 52w hi in bear = survival signal |

Bear I4+K1+J7 interpretation: "2-down bounce + first pullback + VCP near MA20 in bear market = defensive/counter-trend survivors" — works on defensives/utilities that hold up during broader market sell-offs.

### RECENT-ONLY Signals (R-signals, no WF possible — all data 2026-03+)
These have NO train period (R1/R2/R3 started 2026-03-31, cutoff is 2026-01-07):
- I3b_gap_up_strong + C1_early_move + R3_rs90: h3=100%, N=8 — ALL IN VAL, no true WF
- P6_td8 + C1_early_move + R2_rs80: h3=88.9%, N=9 — ALL IN VAL
→ Label: HYPOTHESIS (recent-only). Re-evaluate when R-signal history extends to 2025.

### Insight #12: I1b is a Trigger Signal, Not a Quality Filter
- I1b = close_quality + HIGH VOLUME specifically. Unlike I1 (any volume), I1b requires the high-vol confirmation.
- When combined with I4b (3-day bounce), it means: "bouncing after 3 down days, AND doing so on the highest-volume close quality day"
- This is essentially catching the INSTITUTIONAL BUYING DAY in a pullback sequence
- avg3 of I1b+I4b+E3 (+16.71%) is as high as Q32 family, with different timing mechanism

### Updated Final Signal Priority Table

| Grade | Signal | N | h3 | avg3 | h5 | WF | Priority |
|---|---|---|---|---|---|---|---|
| **S** | Q32: vol surge + 3-day pb + MA50 | 15 | 67% | +15.0% | 60% | PASS | Buy 3-day bounce |
| **S** | Q32 + O2_choch_10d | 11 | 73% | +21.0% | 73% | PASS | CHoCH confirmation version |
| **S** | Q32 + O2_choch + I1 | 6 | **100%** | +17.7% | 83% | PASS | Triple — small N |
| **A** | I1b + I4b (pair) | 16 | 75% | +7.4% | 62.5% | PASS | New backbone |
| **A** | I1b + I4b + I3b | 11 | 82% | +8.5% | 73% | PASS | Gap confirmation |
| **A** | I1b + I4b + H1 | 10 | 80% | +10.4% | 60% | PASS | Momentum resume |
| **A** | I1b + I4b + C2 | 15 | 80% | +8.2% | 67% | PASS | Above MA50 (broadest) |
| **A** | I1b + I4b + E3 | 6 | **100%** | +16.7% | 67% | PASS | Mild pullback trigger |
| **A** | N1_pp + I1_close_quality | 30 | 70% | +10.5% | 70% | PASS | Original validated |
| **B** | J6: vol dry near swing low | 23 | 65% | +3.4% | 65% | PASS | Low return |
| **BEAR** | I4 + K1 + J7 | 6 | **100%** | +3.5% | 100% | N/A (bear) | Defensive plays |
| **BEAR** | J7 solo | 41 | 68% | +0.6% | 66% | N/A (bear) | Best N in bear |

---

## Next Steps — PULSE-US Research Agenda
1. ~~**Brute-force compound search**~~ ✅ COMPLETED — 2,485 pairs, 3,904 triples
2. ~~**fwd5/fwd10 tight base analysis**~~ ✅ COMPLETED — avg ≈ 0%, confirmed not useful on fwd3/fwd5
3. ~~**Regime-split analysis**~~ ✅ COMPLETED — bear signals: J7/J_combo (h3=65%) work
4. ~~**Seasonality**~~ ✅ COMPLETED — Avoid March/July, Best: April/Sep/Nov/Dec
5. ~~**Extended compound search**~~ ✅ COMPLETED — I1b+I4b backbone discovered (Phase 4)
6. **Validate N=9 hypotheses** — wait for more data: P6+RS80+C1, FVG+IQ combo
7. **Build daily signal file** — output Q32/Q16/P5/P9/J6 signals daily (equivalent to thai_entry_screen)
8. **RS-combined signals with Trend Template** — R8 data starts 2026-03, run after more history accrues
9. **Update daily_signals.py** — add I1b+I4b family to Grade A signals

---

## Files
- Script: `scripts/research/pulse_us_entry_screen.py`
- Raw results: `data/research/pulse_us_entry_screen_results.csv`
- Summary: `data/research/pulse_us_entry_screen_summary.csv`
- This log: `data/brain/pulse_us_research.md`

---

## Differences from PULSE-TH

| Dimension | PULSE-TH | PULSE-US |
|-----------|----------|----------|
| Universe | Top-15 RS (21-day) from SET | Top-20 RS (63-day) from S&P+Nasdaq |
| ADTV | 20M THB | $15M USD |
| Base rate | ~50% | 49.1% |
| Best h3 found | ~86%+ (Q signals) | 67.6% (J6) |
| TD signals | fwd3 works | **fwd5 >> fwd3** |
| Vol surge signals | Strong | **Much stronger avg return** |
| RS alone | Not tested | NOT predictive (~52%) |
| Data length | 2016–2026 | 2024–2026 (shorter) |

---
*Note: All findings are research-only. PULSE-US is independent from System 4 (AlphaAbsolute-US). No production changes.*
