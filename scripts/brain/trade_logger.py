"""
AlphaAbsolute -- Trade Logger (Learning Loop Core)
Writes Decision Journal entries and Post-Mortems to Obsidian.

This is the most important learning mechanism in the system.
Every trade generates two notes:
  1. ENTRY: Decision Journal -- WHY did we buy? What did we see?
  2. EXIT: Post-Mortem -- Were we right? What signal failed/worked?

The post-mortem asks the 4 questions that matter:
  Q1: Which gates signaled correctly?
  Q2: Which gates missed or misled?
  Q3: Error class (if loss)?
  Q4: What rule should change?

Called by: auto_trader.py (on entry) + portfolio_manager.py (on exit)
Also callable manually: python trade_logger.py --ticker NVDA --action postmortem

Cost: $0 -- pure file I/O
"""
import sys, json
from pathlib import Path
from datetime import date, datetime
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.brain.obsidian_writer_v2 import write, read, is_available, VAULT

TODAY = date.today().isoformat()

# ── Error classes for post-mortem taxonomy ────────────────────────────────────
ERROR_CLASSES = {
    "thesis":    "Fundamental thesis was wrong (company, not market)",
    "timing":    "Right thesis, wrong timing (too early or too late in cycle)",
    "sizing":    "Wrong position size for conviction level",
    "risk":      "Stop too tight/loose for the setup type",
    "execution": "Entry/exit mechanics were suboptimal",
    "regime":    "Bought in wrong regime (e.g. Distribution when should wait)",
    "data":      "Used stale or incorrect data for gate evaluation",
    "none":      "No error -- good process, bad outcome (variance)",
}

# ── Signal scorecard template ─────────────────────────────────────────────────
SIGNALS = ["RS_gate", "Fundamental_gate", "Stage_gate", "Pattern_setup", "Regime", "TD_signal"]


def _load_state() -> dict:
    for fname in ("paper_portfolio_state.json", "state.json"):
        p = ROOT / "data" / "paper_trading" / fname
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def _get_trade_from_state(ticker: str) -> Optional[dict]:
    """Find a position (open or closed) in paper trading state."""
    state = _load_state()
    # Check open positions
    for key in ("positions", "holdings"):
        if key in state and isinstance(state[key], dict):
            if ticker in state[key]:
                return state[key][ticker]
    # Check closed trades
    for key in ("closed_trades", "trade_history"):
        if key in state and isinstance(state[key], list):
            for t in reversed(state[key]):  # most recent first
                if t.get("ticker") == ticker:
                    return t
    return None


# ── ENTRY: Decision Journal ───────────────────────────────────────────────────

def log_entry(
    ticker: str,
    mode: str,          # "A" or "B"
    setup: str,         # "VCP", "BKT", etc.
    entry_price: float,
    stop_price: float,
    target: float,
    size_pct: float,
    grade: str,         # "A", "B"
    regime: str,
    rs_pct: float = 0,
    eps_yoy: float = 0,
    rev_yoy: float = 0,
    theme: str = "",
    thesis: str = "",
    devil_advocate: str = "",
    extra: dict = None,
) -> bool:
    """Write Decision Journal entry on trade open."""
    if not is_available():
        return False

    stop_pct = (stop_price - entry_price) / entry_price * 100
    tgt_pct  = (target - entry_price) / entry_price * 100
    rr = abs(tgt_pct / stop_pct) if stop_pct else 0

    fname = f"{TODAY}-{ticker}-ENTRY.md"
    path  = f"13_Decision_Journal/{fname}"

    content = f"""\
---
type: decision
ticker: {ticker}
mode: {"A (PRISM Leader)" if mode == "A" else "B (Monster Scout)"}
setup: {setup}
grade: {grade}
entry_date: {TODAY}
entry_price: {entry_price}
stop_price: {stop_price}
target: {target}
size_pct: {size_pct}
rr_ratio: {rr:.1f}
regime: {regime}
theme: {theme}
status: open
---

# Decision Journal: BUY {ticker} -- {TODAY}

## Trade Parameters

| Field | Value | Notes |
|-------|-------|-------|
| Mode | {mode} ({("PRISM Leader" if mode=="A" else "Monster Scout")}) | |
| Setup | {setup} | |
| Grade | {grade} | |
| Entry | ${entry_price} | |
| Stop | ${stop_price} ({stop_pct:.1f}%) | EOD close trigger only |
| Target | ${target} ({tgt_pct:.1f}%) | |
| R:R | {rr:.1f}x | Min 3.0x required |
| Size | {size_pct}% | |
| Regime | {regime} | |

## Gate Scores at Entry

| Gate | Value | Pass? |
|------|-------|-------|
| RS percentile | {rs_pct:.0f}th | {'PASS' if rs_pct >= 70 else 'FAIL'} |
| EPS YoY | {eps_yoy:+.0f}% | {'PASS' if eps_yoy >= 25 else 'FAIL' if mode == 'A' else 'N/A'} |
| Revenue YoY | {rev_yoy:+.0f}% | {'PASS' if rev_yoy >= 25 else 'FAIL' if mode == 'A' else 'N/A'} |
| Theme | {theme} | {'HOT' if theme else 'N/A'} |
| Regime | {regime} | {'PASS' if regime in ('Markup','Sideways') else 'CAUTION'} |

## Thesis (What I See That Consensus Doesn't)

{thesis if thesis else "_Fill in: What is my edge here? Why will this work?_"}

## Devil's Advocate (What Would Make This Completely Wrong?)

{devil_advocate if devil_advocate else "_Fill in: Maximum damage scenario?_"}

## Why Now?

_What specifically triggered entry today vs last week?_

## Exit Plan

- Stop: ${stop_price} (close below = exit next morning open)
- First profit: +15% -> trail stop to breakeven
- Partial exit: +25% -> take 25%
- Full review: if RS drops from top quartile + breaks 50DMA

## Post-Trade (fill after closing)

Exit date:
Exit price:
P&L:
What happened:
Was the decision good given info at the time?

[[13_Decision_Journal/{TODAY}-{ticker}-EXIT.md]]
"""
    ok = write(path, content)
    if ok:
        print(f"[TradeLogger] ENTRY logged: {path}")
    return ok


# ── EXIT: Post-Mortem ─────────────────────────────────────────────────────────

def log_exit(
    ticker: str,
    entry_date: str,
    entry_price: float,
    exit_price: float,
    exit_reason: str,        # "stop_loss", "profit_ladder", "rs_deterioration", "manual"
    regime_at_exit: str,
    rs_at_exit: float = 0,
    signal_scores: dict = None,  # {signal_name: "correct"|"missed"|"noise"}
    error_class: str = "none",
    lesson: str = "",
    what_worked: str = "",
    what_failed: str = "",
) -> bool:
    """Write Post-Mortem on trade close. Most important learning document."""
    if not is_available():
        return False

    pnl_pct = (exit_price - entry_price) / entry_price * 100
    win = pnl_pct > 0
    pnl_str = f"+{pnl_pct:.1f}%" if win else f"{pnl_pct:.1f}%"
    hold_days = (date.today() - date.fromisoformat(entry_date)).days if entry_date else "?"

    # Determine post-mortem folder
    folder_map = {
        "stop_loss":    "14_Post_Mortem/Losers",
        "profit_ladder":"14_Post_Mortem/Winners",
        "rs_deterioration": "14_Post_Mortem/Losers",
        "manual":       "14_Post_Mortem/Losers" if not win else "14_Post_Mortem/Winners",
    }
    if win:
        folder = "14_Post_Mortem/Winners"
    elif error_class == "none" and not win:
        folder = "14_Post_Mortem/Losers"
    else:
        folder = folder_map.get(exit_reason, "14_Post_Mortem/Losers")

    (VAULT / folder).mkdir(parents=True, exist_ok=True)

    fname = f"{TODAY}-{ticker}-EXIT.md"
    path  = f"{folder}/{fname}"

    # Signal scorecard rows
    scores = signal_scores or {}
    sig_rows = ""
    for sig in SIGNALS:
        score = scores.get(sig, "?")
        icon = {"correct": "+1", "missed": "-1", "noise": "0", "?": "?"}.get(score, score)
        sig_rows += f"| {sig} | {score} | {icon} |\n"

    # Error class description
    err_desc = ERROR_CLASSES.get(error_class, error_class)

    # Decision quality vs outcome quality
    if win:
        if error_class != "none":
            dq = "BAD PROCESS, GOOD OUTCOME -- lucky win. Don't repeat the process."
        else:
            dq = "GOOD PROCESS, GOOD OUTCOME -- skill. Reinforce."
    else:
        if error_class == "none":
            dq = "GOOD PROCESS, BAD OUTCOME -- variance. Accept and continue."
        else:
            dq = f"BAD PROCESS, BAD OUTCOME -- fixable. Error: {err_desc}"

    content = f"""\
---
type: post_mortem
ticker: {ticker}
entry_date: {entry_date}
exit_date: {TODAY}
hold_days: {hold_days}
pnl_pct: {pnl_pct:.1f}
exit_reason: {exit_reason}
regime_at_exit: {regime_at_exit}
error_class: {error_class}
decision_quality: {"good" if error_class == "none" else "bad"}
outcome_quality: {"good" if win else "bad"}
---

# Post-Mortem: {ticker} {pnl_str} -- {TODAY}

**Hold:** {hold_days} days | **Entry:** ${entry_price} | **Exit:** ${exit_price} | **P&L:** {pnl_str}
**Exit reason:** {exit_reason} | **Regime at exit:** {regime_at_exit}

## Signal Scorecard

Which signals predicted the outcome correctly?

| Signal | Verdict | Score |
|--------|---------|-------|
{sig_rows}

## What Worked

{what_worked if what_worked else "_Fill in: Which signal/gate predicted this correctly?_"}

## What Failed

{what_failed if what_failed else "_Fill in: Which signal/gate gave wrong signal or was missing?_"}

## Error Classification

**Error class:** {error_class.upper()} -- {err_desc}

- [ ] Thesis error -- fundamental thesis was wrong
- [ ] Timing error -- right thesis, wrong timing
- [ ] Sizing error -- wrong position size
- [ ] Risk error -- stop too tight/loose
- [ ] Execution error -- entry/exit mechanics
- [ ] Regime error -- wrong regime for entry
- [ ] Data error -- bad/stale data used
- [{'x' if error_class == 'none' else ' '}] No error -- good process, variance outcome

## Decision Quality vs Outcome Quality

**{dq}**

Why this separation matters: A stop-out on a well-executed trade is not a mistake.
A winner from a poorly-executed trade is not skill to repeat.

## Root Cause (if error)

_If error_class != none: what SPECIFICALLY caused the error?_
_Not "timing was wrong" -- WHICH signal was wrong, and WHY?_

## One Rule Change Proposed

{lesson if lesson else "_Fill in: What one specific rule change would prevent this error class?_"}

## Backtest Gate

Before implementing proposed rule change:
- Needs N >= 50 similar trades before changing production rule
- Submit to /boa-review if systemic change proposed

## Links

[[13_Decision_Journal/{entry_date}-{ticker}-ENTRY.md]]
"""

    ok = write(path, content)
    if ok:
        print(f"[TradeLogger] POST-MORTEM logged: {path} | {pnl_str}")
    return ok


# ── False Negative Logger ─────────────────────────────────────────────────────

def log_false_negative(
    ticker: str,
    run_from: float,      # price at which it broke out
    run_to: float,        # price it reached
    why_rejected: str,    # which gate failed at the time of breakout
    theme: str = "",
    lesson: str = "",
) -> bool:
    """Log a stock we DID NOT buy that ran +30%+.
    False negatives are as important as losses -- they reveal where gates are too strict."""
    if not is_available():
        return False

    gain = (run_to - run_from) / run_from * 100
    path = f"14_Post_Mortem/False_Negative/{TODAY}-{ticker}.md"

    content = f"""\
---
type: false_negative
ticker: {ticker}
date_noticed: {TODAY}
price_at_breakout: {run_from}
price_now: {run_to}
missed_gain_pct: {gain:.1f}
theme: {theme}
why_rejected: {why_rejected}
---

# False Negative: {ticker} (+{gain:.0f}% missed) -- {TODAY}

We did NOT buy {ticker}. It ran +{gain:.0f}%.

## Why We Rejected It

{why_rejected}

Which specific gate failed:
- [ ] RS gate (RS < 70th at breakout time)
- [ ] Fundamental gate (EPS/Rev < 25%)
- [ ] Stage gate (not Stage 2 Trend Template)
- [ ] Setup gate (no clean pattern)
- [ ] Regime (cash floor prevented entry)
- [ ] ADTV (insufficient liquidity)
- [ ] Other:

## Was the Rejection Correct Given Info at the Time?

_Decision quality check: was it right to reject based on available data?_

## What Would Have Made Us Buy?

_Which gate value would have needed to be different?_

## Lesson / Gate Calibration Proposal

{lesson if lesson else "_Fill in: Does this suggest a gate threshold needs adjustment?_"}

**Backtest gate:** Requires N >= 50 similar rejections before changing thresholds.

## Links

[[11_Stocks/Themes/{theme}.md]]
"""
    ok = write(path, content)
    if ok:
        print(f"[TradeLogger] FALSE NEGATIVE logged: {ticker} +{gain:.0f}% missed")
    return ok


# ── Summary query ─────────────────────────────────────────────────────────────

def get_learning_stats() -> dict:
    """Count post-mortems by type. Quick health check of the learning loop."""
    stats = {"winners": 0, "losers": 0, "false_neg": 0, "false_pos": 0, "process_error": 0}
    folder_map = {
        "14_Post_Mortem/Winners":       "winners",
        "14_Post_Mortem/Losers":        "losers",
        "14_Post_Mortem/False_Negative":"false_neg",
        "14_Post_Mortem/False_Positive":"false_pos",
        "14_Post_Mortem/Process_Error": "process_error",
    }
    for folder, key in folder_map.items():
        p = VAULT / folder
        if p.exists():
            stats[key] = len(list(p.glob("*.md")))
    total = sum(stats.values())
    stats["total"] = total
    return stats


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="AlphaAbsolute Trade Logger")
    p.add_argument("--action", choices=["stats", "false-negative"], default="stats")
    p.add_argument("--ticker", default="")
    p.add_argument("--from-price", type=float, default=0)
    p.add_argument("--to-price", type=float, default=0)
    p.add_argument("--why", default="")
    p.add_argument("--theme", default="")
    args = p.parse_args()

    if args.action == "stats":
        stats = get_learning_stats()
        print(f"[Learning Loop Stats]")
        print(f"  Winners:      {stats['winners']}")
        print(f"  Losers:       {stats['losers']}")
        print(f"  False Neg:    {stats['false_neg']} (missed runs)")
        print(f"  Process Err:  {stats['process_error']}")
        print(f"  Total logged: {stats['total']}")
        if stats['total'] < 5:
            print(f"  -> Need {5 - stats['total']} more before calibration is meaningful")

    elif args.action == "false-negative":
        if not args.ticker or not args.from_price or not args.to_price:
            print("ERROR: --ticker, --from-price, --to-price required")
        else:
            log_false_negative(
                ticker=args.ticker,
                run_from=args.from_price,
                run_to=args.to_price,
                why_rejected=args.why or "Not specified",
                theme=args.theme,
            )
