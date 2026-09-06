"""
AlphaAbsolute — AI Fund Manager Brain: Obsidian Vault Initializer v2
Creates full Investment Operating System structure (not a knowledge dump).

Architecture: 6 layers — Principles, Models, Playbooks, Cases, Decisions, Current State
Source: AI Fund Manager Brain research (2026-09-06)

Cost: $0 — pure file I/O. Idempotent — safe to re-run.
"""
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.brain.obsidian_writer_v2 import VAULT, ensure_vault, write, read

TODAY = date.today().isoformat()

# ── Folder structure: Investment Operating System ──────────────────────────────
FOLDERS = [
    "00_System",
    "01_Investment_Philosophy",
    "02_Market_Regime",
    "03_Alpha_Library",
    "03_Alpha_Library/Value",
    "03_Alpha_Library/Quality",
    "03_Alpha_Library/Growth",
    "03_Alpha_Library/Momentum",
    "03_Alpha_Library/Earnings_Revision",
    "03_Alpha_Library/Technical",
    "04_Fundamental",
    "05_Quant",
    "05_Quant/Backtests",
    "05_Quant/Signals",
    "05_Quant/Models",
    "06_Technical",
    "07_Portfolio_Construction",
    "08_Risk_Management",
    "09_Execution",
    "10_Playbooks",
    "11_Stocks",
    "12_Market_Memory",
    "13_Decision_Journal",
    "14_Post_Mortem",
    "14_Post_Mortem/Winners",
    "14_Post_Mortem/Losers",
    "14_Post_Mortem/False_Positive",
    "14_Post_Mortem/False_Negative",
    "14_Post_Mortem/Process_Error",
    "15_Research_Library",
    "15_Research_Library/Papers",
    "15_Research_Library/Books",
    "15_Research_Library/Interviews",
    "99_Current_State",
    # Legacy AlphaAbsolute folders (keep for backward compat)
    "Themes", "Themes/_New Discoveries",
    "Signals", "Knowledge", "Knowledge/Daily",
    "Tickers", "Trades", "Performance", "Performance/Monthly",
]

# ── Note templates ────────────────────────────────────────────────────────────

SYSTEM_README = f"""\
---
title: AI Fund Manager Brain
created: {TODAY}
type: system
---

# AI Fund Manager Brain — Investment Operating System

This vault is NOT a knowledge library. It is an **Investment Operating System**.

## 6-Layer Architecture

| Layer | Folder | Purpose |
|-------|--------|---------|
| Principles | 01_Investment_Philosophy | What the system believes (with evidence) |
| Models | 02–06 | How it reads the market |
| Playbooks | 10_Playbooks | If X happens, do Y |
| Cases | 12_Market_Memory | What history taught it |
| Decisions | 13_Decision_Journal | Every trade with outcome |
| Current State | 99_Current_State | Live belief state |

## YAML Schema Convention

Every note should declare:
```yaml
type: hypothesis | principle | model | playbook | case | decision | signal | rule
status: active | testing | deprecated | confirmed
confidence: 0.0–1.0
evidence_for:
evidence_against:
applicable_regime: [risk_on, earnings_expansion, etc.]
```

## Knowledge Graph Rule

**Never create isolated notes.** Always link causal chains:

```
[[AI CAPEX]] → [[GPU Demand]] → [[HBM Demand]] → [[Advanced Packaging]] → [[Power Demand]]
[[Inflation]] → [[Fed Policy]] → [[US10Y]] → [[Discount Rate]] → [[Growth Valuation]] → [[NVDA]]
```

## Research Distillation Pipeline

Paper/Book → Research Note → Atomic Insights → Principle → Rule/Signal → Playbook → Test → Decision → Lesson

## Critical Rule

**Decision quality ≠ Outcome quality.**
Log ALL decisions — including wrong ones that worked and right ones that didn't.

## Auto-Written Notes

- `Knowledge/Daily/YYYY-MM-DD.md` — every Claude Code session open
- `99_Current_State/state.md` — updated by pipeline daily
- `13_Decision_Journal/*.md` — every paper trade entry/exit
"""

PHILOSOPHY_TEMPLATE = f"""\
---
type: principle
status: active
confidence: 0.85
created: {TODAY}
evidence_for:
evidence_against:
applicable_regime: [all]
---

# Investment Principle: [NAME]

## Statement

[One-sentence principle]

## Rationale

[Why this works — economic/behavioral mechanism]

## Evidence

- Source 1:
- Source 2:

## When It Works

- Regime:
- Market conditions:

## When It Fails

- Counter-conditions:
- Historical failures:

## Exceptions

-

## AlphaAbsolute Application

How this principle maps to our PRISM/Monster Scout gates:
"""

PLAYBOOK_TEMPLATE = f"""\
---
type: playbook
status: active
created: {TODAY}
trigger:
regime: [all]
last_used:
outcome_history: []
---

# Playbook: [SCENARIO NAME]

## Trigger Conditions

- Signal 1:
- Signal 2:

## Expected Transmission

```
Trigger → Channel 1 → Asset Impact
         → Channel 2 → Asset Impact
```

## Prefer (Overweight)

-
-

## Avoid (Underweight)

-
-

## Thesis Breaker

If this happens → playbook invalidated:
-

## Historical Cases

- [[12_Market_Memory/CASE]]

## AlphaAbsolute Position Change

- Cash floor:
- Max deployment:
- Mode A / Mode B:
"""

DECISION_TEMPLATE = f"""\
---
type: decision
status: open
created: {TODAY}
ticker:
direction: BUY
entry_price:
stop_price:
target:
size_pct:
rr_ratio:
confidence:
market_regime:
setup:
---

# Decision: [DIRECTION] [TICKER] — {TODAY}

## Trade Parameters

| Field | Value |
|-------|-------|
| Entry | $ |
| Stop | $ (%) |
| Target | $ (%) |
| R:R | x |
| Size | % |
| Setup | |
| Confidence | % |

## Thesis

**What I believe:**

**What consensus believes:**

**Where I differ from consensus:**

## Catalyst

## Thesis Breaker

If this happens → exit immediately regardless of P&L:

## Risk: What If I Am Completely Wrong?

## Post-Trade Evaluation

Outcome: +/-%
Decision quality: Good / Bad (independent of outcome)
What was right?
What was wrong?
Lesson:
"""

POSTMORTEM_TEMPLATE = f"""\
---
type: post_mortem
category: [winner | loser | false_positive | false_negative | process_error]
created: {TODAY}
ticker:
entry_date:
exit_date:
pnl_pct:
market_regime_at_entry:
market_regime_at_exit:
setup:
---

# Post-Mortem: [TICKER] — [DATE]

## Summary

P&L: +/-% | Holding period: days | Regime at entry: | Regime at exit:

## Original Thesis

## What Actually Happened

## Signal Scorecard

| Signal | Predicted | Actual | Score |
|--------|-----------|--------|-------|
| RS | | | +/- |
| Earnings | | | +/- |
| Stage | | | +/- |
| Regime | | | +/- |
| Setup | | | +/- |

## Error Classification

- [ ] Thesis error — fundamental thesis was wrong
- [ ] Timing error — right thesis, wrong timing
- [ ] Sizing error — wrong position size for conviction
- [ ] Risk error — stop too tight/loose
- [ ] Execution error — entry/exit mechanics
- [ ] Regime error — bought in wrong regime
- [ ] Data error — used bad/stale data

## Decision Quality vs Outcome Quality

Was the decision good given information available at the time?
[ ] Yes — good process, bad luck
[ ] No — bad process, correctable

## Lesson

One actionable rule change:

## Links

[[13_Decision_Journal/TICKER_DATE]]
"""

CURRENT_STATE_TEMPLATE = f"""\
---
type: current_state
updated: {TODAY}
auto_update: true
---

# Current State — {TODAY}

_Auto-updated by pipeline. Manual edits for conviction notes._

## Regime

Market Regime: **[MARKUP / SIDEWAYS / DISTRIBUTION / MARKDOWN]**
Score: /85
Cash floor: %
Max deployed: %

## Belief State

| Factor | Reading | Change |
|--------|---------|--------|
| Trend | | |
| Breadth | | |
| Earnings Revision | | |
| Liquidity | | |
| Volatility | | |
| Macro | | |

## Portfolio

Beta: | Cash: % | Primary theme exposure:

## Primary Risks (Top 3)

1.
2.
3.

## Current Themes HOT

## What I Believe Now (vs Last Week)

"""

RESEARCH_DISTILL_TEMPLATE = f"""\
---
type: research_note
source_type: paper | book | interview | report
source_quality: primary | secondary | anecdotal
status: distilling
created: {TODAY}
distilled_to: []
---

# Research: [TITLE]

Author(s): | Year: | Source:

## Abstract / Core Claim

## Key Findings

1.
2.
3.

## Atomic Insights → Principles

- Insight → [[01_Investment_Philosophy/PRINCIPLE]]

## Alpha Signals Derived

- Signal → [[03_Alpha_Library/SIGNAL]]

## Rules for AlphaAbsolute

- Rule:

## Playbooks Updated

- [[10_Playbooks/PLAYBOOK]]

## Caveats / Weaknesses

- Sample period:
- Data:
- Regime dependency:

## Verdict

Use as: Core signal | Context | Reject
Reason:
"""

ALPHA_SIGNAL_TEMPLATE = f"""\
---
type: signal
family: [value | quality | growth | momentum | earnings_revision | technical]
status: active
confidence: 0.0
created: {TODAY}
backtest_sharpe:
backtest_n:
in_production: false
applicable_regime: []
---

# Signal: [NAME]

## Definition

Formula:
```
signal = ...
```

## Economic Rationale

Why should this predict returns?

## Factor Family

[[03_Alpha_Library/FAMILY/README]]

## Evidence

| Study | Hit Rate | Sharpe | N | Period |
|-------|----------|--------|---|--------|
| | | | | |

## Regime Dependency

Works best in: | Fails in:

## AlphaAbsolute Integration

Current gate it maps to: | Proposed modification:

## Backtest Results (AlphaAbsolute data)

Date run: | N stocks: | Hit rate: | Sharpe: | Additionality vs baseline:

## Deflated Sharpe Check

Strategies tested: | Deflated Sharpe: | Pass/Fail:
"""


def _write_if_new(path: str, content: str) -> str:
    """Write only if note doesn't exist. Returns status string."""
    if read(path) is not None:
        return f"  - {path} already exists -- skipped"
    write(path, content)
    return f"  + {path}"


def init_vault():
    print(f"[VaultInit v2] Initializing AI Fund Manager Brain at: {VAULT}")

    # Create all folders
    for f in FOLDERS:
        (VAULT / f).mkdir(parents=True, exist_ok=True)
    print(f"  + {len(FOLDERS)} folders created")

    # System docs
    print(_write_if_new("00_System/README.md", SYSTEM_README))
    print(_write_if_new("00_System/VAULT_STRUCTURE.md", SYSTEM_README))

    # Templates folder
    (VAULT / "00_System/Templates").mkdir(exist_ok=True)
    for name, content in [
        ("principle.md",   PHILOSOPHY_TEMPLATE),
        ("playbook.md",    PLAYBOOK_TEMPLATE),
        ("decision.md",    DECISION_TEMPLATE),
        ("postmortem.md",  POSTMORTEM_TEMPLATE),
        ("current_state.md", CURRENT_STATE_TEMPLATE),
        ("research.md",    RESEARCH_DISTILL_TEMPLATE),
        ("alpha_signal.md", ALPHA_SIGNAL_TEMPLATE),
    ]:
        print(_write_if_new(f"00_System/Templates/{name}", content))

    # Core principles (seeded from AlphaAbsolute CLAUDE.md)
    print(_write_if_new("01_Investment_Philosophy/Core_Principles.md", f"""\
---
type: principle
status: active
confidence: 0.90
created: {TODAY}
---

# AlphaAbsolute Core Principles

## Primary Thesis
Stocks that run 3–10x need: right market + right stock + right moment.
The system does NOT predict markets — it identifies when conditions are RIGHT for leaders to run.

## Principles

### 1. Curate, Don't Score
A composite score that averages a great story with a weak chart = mediocre result.
Every stock must PASS specific gates — no averaging, no exceptions.
**Evidence:** PRISM backtest N=23, 5-gate screen +23.6% excess vs QQQ, 78% hit rate.

### 2. Revenue Gate is the Strongest Single Gate
Revenue >25% YoY adds +21% lift on top of RS alone.
**Evidence:** A12 backtest 2026-05-23.

### 3. Cash is the Most Important Position
Regime determines deployment. This is non-negotiable.
**When it works:** Markdown, Distribution.
**When it fails:** Extended Markup — but we use it as re-entry capital.

### 4. Decision Quality ≠ Outcome Quality
A stop-out on a well-executed trade is not a mistake.
A winner from a poorly executed trade is not a skill demonstration.

### 5. Stop Losses Are Not Optional
Markup: -12% from entry (EOD close). Distribution: -8%.
Hard stop = position sizing discipline in disguise.

### 6. Never Average Down
Add only to WINNING positions. Never to losers.
"""))

    # Current state (live)
    print(_write_if_new("99_Current_State/state.md", CURRENT_STATE_TEMPLATE))

    # 14 AlphaAbsolute themes — now in 11_Stocks/Themes/
    THEMES_14 = [
        ("AI-Related",        "NVDA, MSFT, PLTR, SOUN, CRWV"),
        ("Memory-HBM",        "MU, WDC, AMAT, MRAM"),
        ("Space",             "RKLB, LUNR, ASTS, RDW"),
        ("Quantum-Computing", "IONQ, RGTI, QUBT"),
        ("Photonics",         "LITE, COHR, AAOI, IPGP"),
        ("DefenseTech",       "PLTR, CACI, AXON, AVAV, LDOS"),
        ("Data-Center",       "EQIX, DLR, VRT, ETN"),
        ("Nuclear-SMR",       "NNE, OKLO, CEG, CCJ"),
        ("NeoCloud",          "CRWV, SMCI, CORZ, NTAP"),
        ("AI-Infrastructure", "VRT, DELL, ANET, APH"),
        ("DC-Infra",          "PWR, EME, GLDD"),
        ("Drone-UAV",         "ACHR, JOBY, RCAT, AVAV"),
        ("Robotics",          "ISRG, TER, TSLA"),
        ("Connectivity",      "TMUS, ASTS, ERIC"),
    ]
    (VAULT / "11_Stocks/Themes").mkdir(parents=True, exist_ok=True)
    for theme, tickers in THEMES_14:
        path = f"11_Stocks/Themes/{theme}.md"
        if read(path) is None:
            content = f"""\
---
theme: {theme}
tickers: {tickers}
status: ACTIVE
last_updated: {TODAY}
---

# Theme: {theme}

**Tickers:** {tickers}

## Bottleneck Owner

Who controls the critical constraint?

## Causal Chain

```
[Demand Driver] → [Bottleneck] → [Beneficiary]
```

## RS Status

| Date | Theme RS Pctl | HOT/WARM/WEAK |
|------|---------------|---------------|
| {TODAY} | TBD | — |

## Weekly Signals

_Auto-appended by daily_insight.py_

## Key Research

[[15_Research_Library/Papers/]]
"""
            print(_write_if_new(path, content))

    # Seed key playbooks
    playbooks = {
        "Stage2_Breakout.md": f"""\
---
type: playbook
status: active
created: {TODAY}
trigger: Stock breaks out from base in Stage 2
regime: [Markup, Sideways]
---

# Playbook: Stage 2 Breakout (Mode A)

## Trigger Conditions

- Minervini SEPA Trend Template: ALL 8 conditions pass
- RS > 70th percentile (3M and 6M)
- Revenue YoY > 25%, EPS > 25%
- Price breaks from VCP/Cup/BKT pattern on volume ≥ 1.5× avg
- Regime = Markup or Sideways (Grade A only in Sideways)

## Expected Behavior

Institutional accumulation confirmed by price + volume. Distribution of gains is right-skewed.

## Position Sizing

Markup: 10% initial → pyramid to 15%
Distribution: 5% initial — Grade A only

## Exit Rules

See: [[08_Risk_Management/Stop_Loss_Rules.md]]
See: [[08_Risk_Management/Profit_Taking_Ladder.md]]

## Historical Base Rate

A12 backtest N=23 (Markup only): 78% hit rate, +23.6% vs QQQ
""",
        "Distribution_Regime.md": f"""\
---
type: playbook
status: active
created: {TODAY}
trigger: Market enters Distribution regime (score 35-49/85)
regime: [Distribution]
---

# Playbook: Distribution Regime

## Trigger Conditions

- A01 score 35-49/85
- Cash floor ≥ 40%
- Early warnings: MOMENTUM_DIVERGENCE or DISTRIBUTION_CLUSTER

## Actions

- New entries: Grade A ONLY (no Grade B)
- No new Monster Scout entries
- Profit ladder: mechanical (not behavior-based)
- Existing positions: trail stop to breakeven after +15%

## Prefer

- Profitable, FCF-positive quality
- Strong RS relative to market (showing relative strength in weakness)
- Defensive: DCInfra, Nuclear/SMR, DefenseTech

## Avoid

- Unprofitable growth
- Extended valuations
- Thematic plays with no near-term catalyst

## Thesis Breaker → Markdown

Score falls below 35 OR QQQ below 200DMA → full defensive, 75-100% cash
""",
    }
    for fname, content in playbooks.items():
        print(_write_if_new(f"10_Playbooks/{fname}", content))

    print(f"\n[VaultInit v2] Done.")
    print(f"  Vault path: {VAULT}")
    print(f"  Open Obsidian → Settings → Vault → point to this folder")
    print(f"  Enable Dataview plugin for live queries across notes")


if __name__ == "__main__":
    init_vault()
