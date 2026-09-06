# NRGC Phase Context — 400 tokens
## Role
Classify stocks into the 7-phase Narrative Reflexive Growth Cycle. Entry zones: Phase 2-3. Exit: Phase 5-6.

## 7 Phases
| Phase | Name | Signal | AI Action |
|-------|------|--------|-----------|
| 0 | Neglect | Silent, low vol, no coverage | IGNORE |
| 1 | Early Acceleration | Revenue turns positive, quiet breakout | WATCH |
| 2 | Institutional Discovery | RS surge, vol expansion, ATH break | ENTRY (best R/R) |
| 3 | Narrative Expansion | PE re-rate, analysts upgrade, momentum | ENTRY to HOLD |
| 4 | Consensus | Widely known, crowded, growth priced in | HOLD only |
| 5 | Euphoria | RSI>85, climax vol, parabolic | TRIM aggressively |
| 6 | Distribution | Failed pivots, RS decay, smart money exits | EXIT |
| 7 | Markdown | Stage 4 confirmed, downtrend | AVOID |

## Phase 2→3 Detection Checklist
- [ ] RS crossed 50th → 70th+ percentile (see rs_universe/latest.json)
- [ ] Volume expanded 40%+ above 20D avg on up-day
- [ ] First breakout from Base 0 or Base 1
- [ ] Analyst estimate revised upward
- [ ] NRGC narrative_traction > 50 (new product/contract/catalyst)

## Phase 5 Exhaustion Signals (SELL)
- RSI > 85 + climax volume (highest in 3+ months)
- Price gaps up 10%+ on earnings and immediately fades
- RS starting to decline from top decile
- TD Sell Countdown 13 complete
- 3+ distribution days in 10 sessions

## State File
`data/nrgc/state/{TICKER}.json`
Key fields: `phase` (0-7), `nrgc_composite_score` (0-100), `confidence` (0-1), `narrative_traction`

## Phase Rules
- Phase 2-3: Entry zone — full 8-gate chain applies
- Phase 4: Hold existing — no new adds (crowded)
- Phase 5+: Exit trigger — check sell agent immediately
- Phase 3→4 transition: reduce size to 7% max
