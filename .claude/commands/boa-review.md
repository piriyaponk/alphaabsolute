# /boa-review — Board of Alpha Full Committee Review

## When to use
Type `/boa-review [topic]` whenever you want to run a full BoA committee review on:
- A proposed new signal or indicator
- A rule change to existing screens/gates
- A threshold that needs validation
- Any system improvement that touches A01–A10

## What happens

You are now the **Board Secretary (Chairman)**. Run the full BoA process:

### Step 1 — Open Agenda Item
Write a new item to `data/board/agenda.json` with status `IN_PROGRESS`.
Assign a new `BOA-XXX` ID (increment from last closed item).

### Step 2 — Spawn BOTH wings IN PARALLEL

**Investment Wing** — spawn TWO agents simultaneously:

**Agent A: Fund Manager** (`fund-manager` subagent)
- Practitioner precedent: who uses this? Minervini, O'Neil, Druckenmiller, Weinstein?
- P&L question: how often does it fire? What is the position size impact?
- Cycle test: would this have helped in 2000, 2008, 2020, 2022?
- Maximum damage scenario: what if this signal is completely wrong?
- USE CASE proposal: what are ALL the ways this indicator can be used? Lead with the most valuable.
- Draft APPROVE / REJECT / CONDITIONAL with one-paragraph rationale

**Agent B: Research** (general-purpose subagent, research mode)
- Find published practitioner documentation (IBD, Ned Davis, Lowry, academic papers)
- Document hit rates from external studies (not our backtest — literature only)
- Identify which top practitioners use this and HOW (mechanically vs context)
- Check if there's a simpler signal that captures the same edge

**Evidence Wing** — spawn TWO agents simultaneously:

**Agent C: Quant** (`da-quant` subagent)
- Math validation FIRST: is the formula asking the right question? (Ask before running any backtest)
- Run backtest with correct formula on available data
- Report: hit rate, N, Pearson correlation, Spearman correlation, additionality vs existing signals
- Score on BoA 20-point scale (Lead Time Quality 0-10 + Evidence Strength 0-10)
- Minimum threshold: 12/20 for APPROVED

**Agent D: Data Analyst** (`da-analyst` subagent)
- Data quality: is the data clean? Any structural breaks? Any contamination?
- Formula feasibility: can this be computed from existing data sources?
- Complexity: Easy / Medium / Hard to implement
- Edge cases: what breaks this formula?

### Step 3 — Synthesize and Vote

After all 4 agents complete:

**VETO rules:**
- Investment Wing can BLOCK if: fires <6x/year, zero practitioner precedent, would underinvest in major bull runs
- Evidence Wing can BLOCK if: math is wrong, data contaminated, N too small to conclude anything

**Standard vote: 3 of 4 members → APPROVED**
**Regime-critical (cash floor, stop loss, position sizing): 4 of 4 → APPROVED**

### Step 4 — Record and Assign

Write vote result to:
- `data/board/agenda.json` (update status → APPROVED / REJECTED / CONDITIONAL)
- `data/board/decisions.json` (add DEC-XXX)
- `data/board/action_items.json` (if approved → add DE tasks)

If APPROVED: assign DE (`de-team` subagent) to design implementation plan.
If REJECTED: write revisit_condition — what specific data/evidence would change the verdict?

### Step 5 — Proactive Signal Surface (run this without being asked)

After any review, scan for adjacent signals that should also be reviewed:
- If reviewing breadth → also flag A-D line, McClellan Oscillator
- If reviewing credit → also flag HY spread term structure
- If reviewing sentiment → also flag NAAIM acceleration, put-call skew

File any adjacent signals as new agenda items automatically.

## Output format

```
BOARD OF ALPHA — BOA-XXX: [Topic]
Date: YYYY-MM-DD | Status: VOTE

INVESTMENT WING:
  Fund Manager: [APPROVE / REJECT / CONDITIONAL]
  → [Key rationale — P&L focused, one paragraph]
  → USE CASES: [list all identified uses, ranked by value]

  Research: [APPROVE / REJECT / CONDITIONAL]
  → [Practitioner documentation found / not found]
  → [External hit rate from literature]

EVIDENCE WING:
  Quant: [APPROVE / REJECT / CONDITIONAL] — Score: X/20
  → Formula: [confirmed correct / corrected to: ...]
  → Hit rate: X% (N=Y) | Correlation: Z | Additionality: yes/no

  DA: [APPROVE / REJECT / CONDITIONAL]
  → Data quality: [clean / issues: ...]
  → Complexity: Easy / Medium / Hard

VOTE: Fund Manager [Y/N] | Research [Y/N] | Quant [Y/N] | DA [Y/N]
→ RESULT: APPROVED / REJECTED / CONDITIONAL

IF APPROVED:
  DE Task: [what to build]
  Backtest gate: [specific threshold before mechanical application]
  HYPOTHESIS status until: [N=X observations]

IF REJECTED:
  Reason: [specific]
  Revisit when: [specific condition — not "when we have more data"]

ADJACENT SIGNALS FLAGGED: [any related items opened automatically]
```
