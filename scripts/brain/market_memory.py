"""
AlphaAbsolute -- Market Memory Seeder
Creates 12_Market_Memory/ historical case studies in Obsidian.

Each case = what happened, transmission chain, what worked, what failed,
and crucially: what signals fired BEFORE price confirmed.

These are training data for the CIO agent -- every regime crisis
leaves pattern fingerprints that repeat across cycles.

Cost: $0 -- pure file I/O. Idempotent.
"""
import sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.brain.obsidian_writer_v2 import VAULT, write, read

TODAY = date.today().isoformat()

CASES = {
    "2020_COVID_Crash.md": """\
---
type: case
event: COVID-19 Market Crash
period: 2020-02 to 2020-04
regime_at_peak: Markup
regime_at_trough: Markdown
max_drawdown_spy: -34%
recovery_days: 149
status: closed
lessons_applied: true
---

# Case: COVID-19 Crash (Feb-Apr 2020)

## Summary

The fastest -34% drawdown in S&P history (33 days). Followed by the fastest recovery.
V-shaped recovery driven by Fed's unlimited QE + fiscal stimulus (CARES Act $2.2T).

## Timeline

| Date | Event |
|------|-------|
| 2020-02-19 | S&P ATH at 3,386 |
| 2020-02-24 | -3.4% single day -- first real crack |
| 2020-03-09 | Oil war (Saudi/Russia) + COVID -- -7.6% in one day |
| 2020-03-12 | -9.5% -- circuit breaker day |
| 2020-03-16 | -12% -- worst day since 1987 |
| 2020-03-23 | Trough: SPY 218 (-34% from ATH) |
| 2020-03-23 | Fed: unlimited QE announced |
| 2020-04-09 | +12% weekly -- strongest week since 1938 |
| 2020-08-18 | SPY back at ATH |

## Causal Chain

```
COVID lockdowns
  -> Earnings void (no visibility)
  -> Forced selling (margin calls, risk parity deleveraging)
  -> Liquidity crisis (credit seized: HY spread +800bps in 3 weeks)
  -> Fed: unlimited QE + SMCCF (bought HY ETFs -- first ever)
  -> Liquidity restored
  -> Growth stocks re-rated UP (zero rates = higher multiples)
  -> Tech/Growth massive outperformance 2020-2021
```

## Early Warning Signals (Fired Before Price Confirmed)

- HY spread: started widening Feb 24 -- 2 weeks before price low
- VIX: spiked above 80 (highest ever) -- capitulation signal
- % above 50DMA: crashed from 70% -> 5% in 3 weeks
- RSI(14) on SPY: reached 18 -- extreme oversold (historically <20 = bottom within 2 weeks)
- Up-volume ratio: near 0% for 3 consecutive days = maximum distribution

## What the A01 Regime System Would Have Said

Score: ~8/85 at trough (Markdown confirmed by day 3 of drawdown)
Cash floor: 75% minimum
New entries: BLOCKED (no stock passes full PRISM in Markdown)

## Survivors / Winners

Leaders that held up and led the recovery:
- AMZN: -21% vs SPY -34% (relative strength preserved)
- ZM: actually RALLIED (thesis: remote work beneficiary)
- NVDA: -27%, recovered 100% in 5 months
- Key insight: stocks that made new 52W highs in April 2020 (during bear market) = PRISM Grade A candidates

## Mistakes This Case Teaches

1. **Don't fight the Fed.** Unlimited QE = don't short. Exit. Wait. Re-enter.
2. **HY spread is the real signal.** Credit leads equity by 2 weeks.
3. **Recovery is fastest when drawdown is policy-driven (not structural).** Structural bear markets (2008) take years; policy crises take months.
4. **The best buys are in the crash.** Not 6 months after. This requires sitting in 75%+ cash in Markdown.

## False Negatives (Stocks Missed That Ran)

- ROKU: +300% from March 2020 lows -- breakout in early April was the signal
- DKNG: +400% from trough
- PTON: +500%

## Links

[[10_Playbooks/Distribution_Regime.md]]
[[02_Market_Regime/Regime_Classification.md]]
""",

    "2022_Rate_Hike_Bear.md": """\
---
type: case
event: Fed Rate Hike Bear Market
period: 2022-01 to 2022-10
regime_at_peak: Distribution (signals fired Nov 2021)
regime_at_trough: Markdown
max_drawdown_spy: -25%
max_drawdown_qqq: -35%
max_drawdown_growth: -60% to -90% (ARKK)
recovery_days: 280 (to SPY ATH)
status: closed
lessons_applied: true
---

# Case: 2022 Rate Hike Bear Market

## Summary

The destruction of the 2020-2021 bubble. Zero interest rates had inflated growth multiples
to 20-50x revenue. When Fed pivoted from "transitory" to "40-year-high inflation" reality,
the multiple compression was violent. High-growth / no-profit stocks fell 60-90%.
PRISM would have been 100% cash by January 2022.

## Timeline

| Date | Event |
|------|-------|
| 2021-11-08 | QQQ ATH 407 (SPY ATH Nov 2021) |
| 2021-11-15 | CPI print 6.2% -- "transitory" narrative dies |
| 2021-12-15 | Fed: accelerated taper + signals 3 hikes in 2022 |
| 2022-01-03 | Market opens -- QQQ -5% in January alone |
| 2022-03-16 | First hike: +25bps |
| 2022-05-04 | +50bps hike -- QQQ -5% in ONE day |
| 2022-06-15 | +75bps (first 75bps hike since 1994) |
| 2022-10-13 | CPI surprise DOWN -> reversal day: SPY +5.5% in one hour |
| 2022-10-13 | Trough: QQQ 254 (-38% from ATH) |
| 2023-07-31 | Last hike: 5.25-5.50% |

## Causal Chain

```
Inflation (CPI 9.1% peak June 2022)
  -> Fed pivot: "transitory" abandoned
  -> Rate hikes: 0% -> 5.25% in 16 months (fastest in history)
  -> Discount rate spiked
  -> High-multiple growth stocks crushed (P/E = 1/(r-g), r went up dramatically)
  -> No-profit growth stocks most vulnerable (terminal value = 0 when discounted at 5%)
  -> Credit tightened
  -> IPO market closed
  -> VC winter
  -> Tech layoffs
```

## Early Warning Signals (Nov-Dec 2021, Before Price Peaked)

- % above 50DMA: peaked Oct 2021, started declining while SPY still making ATH (divergence)
- Junk bond spreads: started widening Nov 2021
- Advance-Decline line: peaked Sept 2021 -- 2 months before SPY ATH (classic divergence)
- IPO market euphoria (RIVN raised at $80B, Bumble at 100x revenue) = classic late-cycle
- ARKK already -40% from peak while SPY making ATH -- leadership rotation into defensives

## What the A01 Regime System Would Have Said

Nov 2021: Score falling from 75 -> 55 (entering Sideways)
Dec 2021: Score 45 (Distribution) -- cash floor 40%
Jan 2022: Score <35 (Markdown) -- cash floor 75%, no new entries

A PRISM screener would find ZERO new Grade A setups in January 2022.
The right answer was: stay in cash, wait for Stage 2 to re-emerge.

## Winners in 2022 (What PRISM Would Have Found)

Energy stocks: XOM +80%, CVX +60%, OXY +120% (Stage 2 breakouts in Jan 2022)
Defense: LMT +30%, NOC +40%
Healthcare: UNH +20%

Key insight: These all had RS >90 in January 2022 while market fell.
PRISM would have found them -- PRISM gate is RS >70 + Stage 2.

## Mistakes This Case Teaches

1. **Valuation matters when rates normalize.** 50x revenue works at 0% rates. Not at 5%.
2. **Stage 2 exit is non-negotiable.** ARKK entered Stage 3 Nov 2021 -- Weinstein says exit immediately.
3. **Multiple expansion is not earnings growth.** Know which drove your position's P&L.
4. **Breadth divergence (A-D line) leads by 2-3 months.** This is Factor C in A01.
5. **No-profit growth in late-cycle = AVOID.** Gross margin and FCF matter again.

## False Negatives (We Should Have Held Through)

Energy stocks were the exception -- these passed PRISM in January 2022.
If held, +80% while market fell 25%.

## Links

[[10_Playbooks/Distribution_Regime.md]]
[[01_Investment_Philosophy/Core_Principles.md]]
""",

    "2023_AI_Boom.md": """\
---
type: case
event: AI Supercycle Breakout (GPT-4 Era)
period: 2023-01 to 2024-12
regime_at_start: Sideways (recovering from 2022)
regime_at_peak: Markup (strong)
spy_return: +26% (2023), +23% (2024)
qqq_return: +54% (2023), +29% (2024)
nvda_return: +239% (2023), +170% (2024)
status: ongoing (2026: entering Distribution?)
lessons_applied: true
---

# Case: AI Supercycle Breakout (2023-Present)

## Summary

ChatGPT launched Nov 2022. The AI narrative ignited one of the fastest and most concentrated
bull markets in history. NVDA went from $140 (Jan 2023) to $950 (June 2024) = +580% in 18 months.
The "Magnificent 7" drove 60%+ of S&P returns in 2023.

## Timeline

| Date | Event |
|------|-------|
| 2022-11-30 | ChatGPT launched -- 1M users in 5 days |
| 2023-01-03 | NVDA: RS starts inflecting (bottoming after -65% in 2022) |
| 2023-02-22 | NVDA earnings: data center BLOWOUT -- stock +14% in one day |
| 2023-03 | NVDA clears Stage 2 Trend Template -- PRISM Gate 5 = PASS |
| 2023-04 | NVDA breakout from 6-month base at $280 -- VCP setup |
| 2023-05-25 | NVDA earnings: +19% revenue YoY guidance RAISED -> stock +24% |
| 2023-06 | NVDA enters $1T market cap club |
| 2024-06 | NVDA peaks near $130 (post-split) -- ~$3.2T market cap |
| 2025-01 | DeepSeek shock: NVDA -17% in one day |
| 2025-03 | Recovery: AI capex reaffirmed by hyperscalers |
| 2026-09 | Current: Distribution regime -- NVDA Phase 4 (NRGC) |

## Causal Chain

```
LLM scaling laws proven (GPT-4, 2023)
  -> Hyperscaler AI capex explosion (MSFT, GOOG, META, AMZN)
  -> GPU demand >> supply (NVDA H100 waitlist 12+ months)
  -> HBM demand explosion (MU, Samsung, SK Hynix)
  -> Power demand explosion (nuclear, natgas, grid infra)
  -> Inference boom: cloud AI services = new revenue streams
  -> Model competition: MSFT/Copilot, Google/Gemini, Anthropic/Claude, Meta/Llama
  -> Edge AI: smartphones, PCs, cars
  -> Robotics: physical AI next wave (2025-2027)
```

## What PRISM Would Have Found

Jan 2023: NVDA RS percentile = 45th (still low -- recovering from bear)
March 2023: NVDA RS = 75th (passes Gate 1!)
April 2023: All 5 PRISM gates pass -- Grade A setup at $280 breakout
May-June 2023: +80% from PRISM entry = 8-week hold rule triggered (20% in 3 weeks)

The system would have HELD through the entire 2023 run.
The mistake would be selling too early (behavior-based exit too conservative).

## Key Alpha Signals That Fired Early

1. **RS inflection (Phase 2 entry):** NVDA RS crossed 70th percentile in March 2023 -- 2 months before the May earnings blowout
2. **Revenue acceleration:** Feb 2023 earnings = first quarter of acceleration after 2 quarters of decline
3. **Institutional accumulation:** Large-cap RS lines started breaking out BEFORE retail noticed
4. **Theme RS (AI-Related):** AI theme RS crossed 75th percentile in Feb 2023 = HOT signal

## The Sectors That Won (Theme Cascade)

| Wave | Theme | Leader |
|------|-------|--------|
| Wave 1 (2023) | AI chips | NVDA, AMD |
| Wave 2 (2023-24) | AI infrastructure | MSFT, GOOG |
| Wave 3 (2024) | Power/cooling | VRT, ETN, EQIX |
| Wave 4 (2024-25) | Robotics/physical AI | TSLA, ISRG, TER |
| Wave 5 (2025-26) | Edge AI, memory | MU, ALAB, CRDO |

## Current State (2026-09)

- NVDA: NRGC Phase 4 (nearing exhaustion)
- A01: Distribution regime (score 45/85)
- Multiple warning signs: MOMENTUM_DIVERGENCE active
- Question: Is this the top of Wave 5, or consolidation before Wave 6?

## Lessons

1. **Theme cascades have 3-5 waves.** Don't sell Wave 1 leader thinking it's done.
2. **RS inflection at Phase 2 entry is the BEST risk/reward.** March 2023 NVDA was the signal.
3. **8-week hold rule preserves the most alpha.** Systems that took profit early missed 80% of the move.
4. **Concentration works in secular themes.** 20% in NVDA would have been the right call.

## Links

[[11_Stocks/Themes/AI-Related.md]]
[[11_Stocks/Themes/Memory-HBM.md]]
[[10_Playbooks/Stage2_Breakout.md]]
""",

    "2024_JapanYenCarry.md": """\
---
type: case
event: Japan Yen Carry Trade Unwind
period: 2024-08-05
regime_at_event: Markup (interrupted)
max_drawdown_spy: -9% in 3 days
max_drawdown_vix: VIX spike to 65 (pandemic high was 80)
recovery_days: 14
status: closed
lessons_applied: true
---

# Case: Japan Yen Carry Trade Unwind (Aug 5, 2024)

## Summary

Bank of Japan raised rates 25bps (July 31, 2024) -- a policy shock for a country at 0% for 30 years.
Yen carry traders (borrow yen at 0%, invest in US stocks) were forced to unwind simultaneously.
SPY fell 9% in 3 trading days. The FASTEST recovery from a -9% drawdown in history: fully recovered in 14 days.

## Why This Matters for AlphaAbsolute

This is the MODEL for how to handle MACRO-DRIVEN flash crashes:
- Fundamental thesis didn't change
- Regime didn't change (Markup regime correctly classified even on Aug 5)
- Stops should NOT have triggered (EOD close basis -- intraday dip to stop, not EOD close)
- The right action: DO NOTHING, let it pass

## Causal Chain

```
BoJ rate hike (July 31, 2024)
  -> Yen appreciation (USD/JPY 161 -> 142 in 3 weeks)
  -> Carry traders: yen loans got more expensive
  -> Forced unwind: sell US stocks/carry assets, buy back yen
  -> Simultaneous selling: SPY -9%, Nikkei -12%
  -> VIX spike to 65 (fear index at near-pandemic levels)
  -> Zero fundamental change to US corporate earnings
  -> Recovery: carry trade fully unwound in 2 weeks
  -> SPY at ATH again within 3 weeks of the low
```

## What A01 Regime Said on Aug 5

Score: ~60/85 (Sideways -- brief reclassification due to breadth data)
But by Aug 12: back to 70+ (Markup)

The regime reading was TEMPORARILY distorted by the breadth collapse.
This is a known limitation of breadth-based systems for macro flash crashes.

## Stop Loss Analysis

- COHR example: entry $100, stop -8% at $92 (Distribution regime)
- On Aug 5: COHR intraday low might have touched $92 briefly
- EOD close: $96 (well above stop)
- PRISM rule: stops trigger on EOD CLOSE only
- Correct action: HOLD -- stop not triggered

This is exactly why the CLOSING PRICE RULE exists in CLAUDE.md.
Intraday stop hits in flash crashes = stop hunts by institutions.

## Lessons

1. **Macro flash crashes are NOT regime changes.** Check: did fundamentals change? Usually no.
2. **EOD closing price rule is critical.** Intraday stops would have exited profitable positions.
3. **VIX spike to 60+ = capitulation, not bear market.** Historical: VIX >60 = buy, not sell.
4. **Recovery speed inversely proportional to fundamental damage.** Yen carry = no fundamental damage = fast recovery.
5. **First, identify WHY.** If "policy shock with no earnings impact" -> hold. If "credit crisis" -> exit.

## False Negatives

Stocks that broke out of the crash (Aug 5 low -> Sept 2024):
- Any stock that held above its 21EMA on the Aug 5 crash = relative strength = BUY signal
- These were Phase 2/3 stocks showing institutional accumulation despite macro noise

## Links

[[02_Market_Regime/Regime_Classification.md]]
[[08_Risk_Management/Stop_Loss_Rules.md]]
""",

    "2025_DeepSeek_Shock.md": """\
---
type: case
event: DeepSeek R1 Shock (AI Efficiency Narrative)
period: 2025-01-27
regime_at_event: Markup (briefly interrupted)
max_drawdown_nvda: -17% in one day
max_drawdown_spy: -2%
recovery_days: 45 (NVDA)
status: closed
lessons_applied: true
---

# Case: DeepSeek R1 Shock (Jan 27, 2025)

## Summary

Chinese AI lab DeepSeek released R1 model claiming GPT-4-level performance at fraction of compute cost.
NVDA fell -17% in one day ($600B market cap wiped). This was the largest single-day market cap loss
in stock market history. SPY was largely unaffected (-2%).

## Was the Thesis Broken?

NO -- and this is the key lesson.

- NVDA stop was at -12% from entry (Markup regime)
- EOD close: NVDA closed -17%... but the THESIS evaluation came first
- A10 devil's advocate: "Does DeepSeek prove GPU demand collapses?" -> NO
- Hyperscalers immediately reaffirmed AI capex (MSFT, GOOG earnings 1 week later)
- NVDA thesis: GPU demand >> supply. DeepSeek efficiency = MORE inference demand (Jevons Paradox)

## Causal Chain

```
DeepSeek R1 released (open source)
  -> Narrative: "AI compute is commoditized"
  -> Fear: NVDA H100/H200 won't be needed
  -> Forced selling: momentum funds de-risk
  -> -17% in one day

REALITY CHECK:
  -> Jevons Paradox: cheaper AI = more AI use = MORE compute demand
  -> Hyperscalers confirmed: capex INCREASING not decreasing
  -> NVDA recovery: +45% from Jan 27 low by March 2025
```

## What AlphaAbsolute Should Have Done

1. Hard stop was -12% from entry (Markup)
2. DeepSeek: EOD close was -17% (exceeds stop mathematically)
3. HOWEVER: A10 devil's advocate check shows thesis is INTACT
4. This is a gray area: stop triggered by EOD rule, but thesis unchanged

**Resolution:** Follow the stop. Thesis-intact or not, -17% in one day = something changed.
Re-enter when Stage 2 recovers (and it did, within 2 weeks).

## Lesson: What to do when a gap-down exceeds stop?

1. Stop is mechanical -- exit.
2. IMMEDIATELY run devil's advocate: "Is the thesis broken?"
3. If thesis intact AND stock recovers within 5 days -> consider re-entry at new breakout
4. If thesis broken -> stay out

## False Negatives

- Re-entry signal fired Feb 12, 2025: NVDA broke above 50DMA with volume
- PRISM passed again in February
- From Feb entry: +30% by April 2025

## Links

[[11_Stocks/Themes/AI-Related.md]]
[[08_Risk_Management/Stop_Loss_Rules.md]]
[[01_Investment_Philosophy/Core_Principles.md]]
""",
}


PLAYBOOKS_EXTRA = {
    "Fed_Rehike_Scenario.md": """\
---
type: playbook
status: active
created: {TODAY}
trigger: Fed signals rate hike cycle resumption after pause
regime: [Distribution, Sideways]
last_fired: 2022-03
---

# Playbook: Fed Rehike Scenario

## Trigger Conditions

- CPI re-acceleration (2 consecutive months above target after cooling)
- Fed language shift: "patient" -> "additional firming may be appropriate"
- 2Y yield: rising faster than 10Y (bear flattener or inversion deepening)
- HY spread: starting to widen (credit leads equity)

## Expected Transmission (6-12 month lag to earnings impact)

```
Fed rehike signal
  -> Short-term rates rise
  -> Long-term rates rise (if inflationary) OR fall (if recessionary)
  -> Dollar strengthens (USD/JPY, USD/EM)
  -> High-multiple growth stocks repriced (P/E compression)
  -> Leveraged companies: higher interest expense -> EPS pressure
  -> Housing: mortgage rates rise -> housing slowdown -> construction decline
```

## Prefer (Most Resilient)

- Energy: benefits from inflation + pricing power
- Financials: banks earn more on float
- Defense: non-discretionary spending, no rate sensitivity
- Value: low multiples = low discount rate sensitivity
- International ex-US: may outperform USD on rate differentials

## Avoid (Most Vulnerable)

- Unprofitable growth: no FCF to service debt
- High-multiple tech (PE > 50x): massive discount rate sensitivity
- Real estate (REITs): bond proxy
- Consumer discretionary: rates + inflation = double pressure on consumer

## Position Changes

- Cash floor: 40% (Distribution regime minimum)
- Mode B (Monster Scout): STOP all entries
- Mode A: Grade A only, smaller size
- Profit ladder: mechanical (not behavior-based)

## Thesis Breaker -> Rehike Cancelled

Fed language pivots back to "on hold" OR CPI drops 2 consecutive months -> remove hedges

## Historical Cases

- [[12_Market_Memory/2022_Rate_Hike_Bear.md]]
""".format(TODAY=TODAY),

    "Risk_Off_Scenario.md": """\
---
type: playbook
status: active
created: {TODAY}
trigger: Credit stress + liquidity freeze + forced selling
regime: [Markdown, Distribution]
last_fired: 2020-03 (COVID), 2023-03 (SVB)
---

# Playbook: Risk Off / Credit Stress

## Trigger Conditions

- HY spread: >500bps AND widening fast (>50bps/week)
- VIX: >40 (fear zone) -- >60 = capitulation (potential reversal)
- Credit markets seizing: LIBOR/OIS spread, TED spread rising
- Forced selling: margin calls, ETF redemptions overwhelming market makers
- Banks in trouble: CDS on major banks widening

## Expected Transmission

```
Credit event (bank failure, fund blowup, sovereign stress)
  -> Liquidity crisis: forced selling of ALL assets
  -> Correlation spike: everything falls together
  -> VIX spike: options cost explodes
  -> Fed emergency action within days (historically)
  -> Recovery: V-shaped if Fed acts fast, L-shaped if structural
```

## Immediate Actions

1. Cash floor: 75-100% (no new entries)
2. Review EVERY position for thesis validity
3. Exit any position with Stage 3/4 breakdown
4. Exit any leveraged or low-quality position

## Watch For Turn Signal

- VIX >60 = capitulation zone (historically: buying in next 5-10 trading days rewarded)
- Fed emergency action announced = inflection signal within 1-5 days
- HY spread peaks and turns = credit leads equity by 2 weeks
- % above 50DMA: <10% (extreme oversold) = reversal typically within 20 days

## Re-Entry Protocol

DO NOT re-enter until:
1. A01 score > 50 (Sideways at minimum)
2. At least 1 strong RS inflection from Markdown bottom
3. 10-week MA on SPY/QQQ trending up for 2 weeks

## Historical Cases

- [[12_Market_Memory/2020_COVID_Crash.md]]
- [[12_Market_Memory/2022_Rate_Hike_Bear.md]]
""".format(TODAY=TODAY),

    "Earnings_Upgrade_Cycle.md": """\
---
type: playbook
status: active
created: {TODAY}
trigger: Analysts begin upward EPS revision cycle for a sector/stock
regime: [Markup, Sideways]
last_fired: 2023-01 (AI cycle), 2024-05 (memory cycle)
---

# Playbook: Earnings Upgrade Cycle

## Why This Works

EPS revision momentum has the strongest published Sharpe of any factor in academic literature
(Jegadeesh, 1990; Chan, Jegadeesh, Lakonishok, 1996). Analysts systematically under-react
to positive earnings surprises -- they raise estimates slowly, creating a multi-quarter
window of positive surprise and price appreciation.

## Trigger Conditions

- EPS estimate for FY1: upgraded by >= 5% in a single quarter
- Multiple analysts revising UP (breadth of revision > 60%)
- Revenue revision UP alongside EPS (not just margin improvement)
- Gross margin trend: stable or expanding (A04 output)

## NRGC Phase Signal

This playbook activates at NRGC Phase 2 entry:
- First quarter of EPS acceleration after DECELERATING period
- Fundamental gate passes (>25% YoY growth suddenly)
- RS begins inflecting upward

## Expected Return Profile (A12 backtest N=23)

Phase 2 entry (first acceleration quarter): hold 6-18 months
Average return: +45-80% over holding period
Win rate: 72% (Markup regime)

## Position Sizing

Markup: 10% initial
Sideways: 5% initial (Grade A only)

## Exit Triggers

- EPS revision turns DOWN for 2 consecutive quarters
- Revenue deceleration 3 consecutive quarters
- NRGC Phase 5 (score >85 = exhaustion)
- Stock enters Stage 3/4

## Key Risk: Pre-Revenue Companies

This playbook requires ACTUAL EPS. Do NOT apply to pre-revenue or EBITDA-only companies.
They fail the PRISM fundamental gate and should only be in Monster Scout bucket.

## Historical Cases

- NVDA: EPS revision cycle started Feb 2023 (Feb earnings print) -> +580% in 18 months
- MU: Memory cycle started Q2 2024 -> +90% from Phase 2 entry
- ALAB: Revenue inflection Q3 2024 -> +200% in 9 months

## Links

[[12_Market_Memory/2023_AI_Boom.md]]
[[11_Stocks/Themes/Memory-HBM.md]]
""",
}


def seed_market_memory():
    print(f"[MarketMemory] Seeding 12_Market_Memory/ historical cases...")
    (VAULT / "12_Market_Memory").mkdir(parents=True, exist_ok=True)

    for fname, content in CASES.items():
        path = f"12_Market_Memory/{fname}"
        if read(path) is None:
            from scripts.brain.obsidian_writer_v2 import write as obs_write
            obs_write(path, content)
            print(f"  + {path}")
        else:
            print(f"  - {path} already exists -- skipped")

    print(f"\n[MarketMemory] Seeding 10_Playbooks/ additional playbooks...")
    (VAULT / "10_Playbooks").mkdir(parents=True, exist_ok=True)

    for fname, content in PLAYBOOKS_EXTRA.items():
        path = f"10_Playbooks/{fname}"
        if read(path) is None:
            from scripts.brain.obsidian_writer_v2 import write as obs_write
            obs_write(path, content)
            print(f"  + {path}")
        else:
            print(f"  - {path} already exists -- skipped")

    # Create knowledge graph index
    kg_path = "00_System/Knowledge_Graph.md"
    if read(kg_path) is None:
        from scripts.brain.obsidian_writer_v2 import write as obs_write
        obs_write(kg_path, f"""\
---
type: system
created: {TODAY}
---

# Knowledge Graph -- Causal Chain Index

Every note must link into a causal chain. Isolated notes decay into trivia.

## Master Chain: AI Supercycle

```
[[AI CAPEX Explosion]]
  -> [[GPU Demand]] ([[11_Stocks/Themes/AI-Related]])
  -> [[HBM Memory Demand]] ([[11_Stocks/Themes/Memory-HBM]])
  -> [[Advanced Packaging]] (TSMC CoWoS, Amkor)
  -> [[Power Demand]] ([[11_Stocks/Themes/Nuclear-SMR]], [[11_Stocks/Themes/Data-Center]])
  -> [[Cooling Solutions]] (VRT, LIQT)
  -> [[Data Center Infra]] ([[11_Stocks/Themes/DC-Infra]])
```

## Master Chain: Macro -> Stocks

```
[[Fed Policy]]
  -> [[US10Y Yield]]
  -> [[Discount Rate]]
  -> [[Growth Valuation]] (P/E compression or expansion)
  -> [[PRISM Stage 2]] (Trend Template)
  -> [[Position Sizing]]
```

## Master Chain: Credit -> Markets

```
[[HY Spread]]
  -> [[Credit Conditions]] (A02 output)
  -> [[Liquidity]] (macro_modifier)
  -> [[Risk Appetite]] (A01 regime score)
  -> [[Cash Floor]] (regime -> deployment %)
```

## Theme Cascade Sequence (AI Supercycle 2023-2026)

```
Wave 1: NVDA/AMD (2023) -> GPU demand proven
Wave 2: Cloud infra MSFT/GOOG (2023-24) -> inference revenue
Wave 3: Power/cooling VRT/ETN (2024) -> physical infrastructure
Wave 4: Memory MU/SNDK (2024-25) -> data storage explosion
Wave 5: Edge AI / Robotics (2025-26) -> physical AI
Wave 6: ??? (watch: Connectivity ASTS, Photonics AEVA)
```

## Research Pipeline (how notes connect)

```
[[15_Research_Library/Papers/]]
  -> Atomic insights
  -> [[01_Investment_Philosophy/]] (principles)
  -> [[03_Alpha_Library/]] (signals)
  -> [[10_Playbooks/]] (IF signal -> THEN action)
  -> [[13_Decision_Journal/]] (every trade)
  -> [[14_Post_Mortem/]] (what we learned)
  -> [[05_Quant/]] (backtest: does it actually work?)
```
""")
        print(f"  + {kg_path}")

    print(f"\n[MarketMemory] Done.")


if __name__ == "__main__":
    seed_market_memory()
