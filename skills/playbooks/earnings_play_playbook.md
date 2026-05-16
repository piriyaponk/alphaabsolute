# Earnings Play Playbook
*Rules for managing positions around earnings events*

Load this playbook when earnings is within 10 trading days for any held or watched position.

---

## The Core Rule (Risk.md — non-negotiable)

> **No new entries within 5 trading days of earnings.**
> **Reduce existing positions to <3% if holding through earnings.**

---

## Pre-Earnings Protocol (T-10 to T-5 days)

**Assessment phase — answer these before T-5:**
- [ ] What is the EPS estimate? What beat % is needed to move the stock?
- [ ] What is the whisper number (vs consensus)?
- [ ] What did the stock do on last 3 earnings? (up/down/flat)
- [ ] Is current base forming BEFORE earnings? (risky — avoid entry)
- [ ] What is the implied move from options? (use to size risk)
- [ ] Is the stock extended? >+20% from 50DMA = high risk of "sell the news"

**Decision at T-5:**
| Scenario | Action |
|----------|--------|
| In profit >+15% | Consider trimming to 6-8%, let remainder ride |
| In profit +5-15% | Hold current size, set mental stop below base low |
| Near breakeven | Reduce to <3% — earnings binary risk not worth it |
| At a loss | Exit BEFORE earnings — do not gamble on recovery |
| Watching (no position) | No entry until earnings are past |

---

## Earnings Tone Analysis (earnings_tone.py)

If transcript is available, run tone analysis immediately:
```python
from scripts.learning.earnings_tone import analyze_earnings_batch
result = analyze_earnings_batch({"TICKER": transcript_text})
```

**Tone score interpretation:**
| Score | Signal | Action |
|-------|--------|--------|
| +20 or higher | Strong beat + guidance raise + inflection language | Add on confirmation |
| +10 to +19 | Beat + positive tone | Hold, watch for base |
| 0 to +9 | In-line, neutral tone | Hold, no action |
| -10 to -1 | Miss or cautious guidance | Reduce |
| Below -10 | Miss + negative tone + lowered guidance | EXIT |

---

## Post-Earnings Protocol (T+1 to T+5)

**Gap-up on volume (strong beat):**
- [ ] Is gap >+5% on volume >150% avg? → Signal of strength
- [ ] Wait for first pullback to support (don't chase the gap)
- [ ] Entry on first constructive pullback to base top or 10EMA
- [ ] This is a "pocket pivot" setup — can initiate/add position

**Gap-down on volume (miss or guidance cut):**
- [ ] Exit immediately if already holding — do not average down
- [ ] If watching: put on 3-month watchlist, wait for new base
- [ ] Check if miss was one-time or structural (use tone score)
- [ ] Structural miss = avoid for 2+ quarters

**Flat/muted reaction:**
- [ ] In-line result, no catalyst
- [ ] Hold existing position if thesis intact
- [ ] No new entry — wait for next setup to form

---

## Earnings Acceleration Screen

After each earnings season, run this filter:
```
Companies reporting EPS acceleration (current Q > prior Q growth rate):
AND revenue QoQ turning positive or accelerating
AND guidance raised
= NRGC Phase 2 candidates for next cycle
```

---

## Earnings Calendar Integration

Check before ANY new entry:
1. Look up next earnings date (yfinance: `ticker.calendar`)
2. If within 5 days → NO NEW ENTRY (Risk.md rule)
3. If within 10 days → reduce planned size by 50%
4. Mark calendar for 1 day after earnings → reassess

---

## Historical Pattern: What Works

**Best entry timing for earnings plays:**
- 4-6 weeks BEFORE earnings (stock bases quietly)
- 1-3 days AFTER a strong earnings gap-and-hold
- **Never** the day before or day of earnings

**Setup that works best post-earnings:**
- Strong earnings gap → consolidates for 1-3 weeks in tight range → breakout = high conviction entry
- This is the "earnings gap base" pattern used by Minervini and Zanger
