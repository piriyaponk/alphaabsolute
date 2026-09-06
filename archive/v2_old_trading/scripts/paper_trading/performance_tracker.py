"""
AlphaAbsolute — Performance Tracker + Lesson Generator
Weekly: calculates P&L, drawdown, alpha attribution → generates lessons → saves to memory.

Goal: continuous improvement — beat Nasdaq with low drawdown.
LLM (Groq free): lesson synthesis per closed trade
Memory: writes to Claude Code memory files for persistent agent learning
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR     = Path(__file__).parent.parent.parent
MEMORY_DIR   = Path.home() / ".claude" / "projects" / "C--Users-Pizza-OneDrive-Desktop-AlphaAbsolute" / "memory"
LESSONS_FILE = BASE_DIR / "data" / "paper_trading" / "lessons.json"
PERF_FILE    = BASE_DIR / "data" / "paper_trading" / "performance_history.json"


# ─── Stats ────────────────────────────────────────────────────────────────────

def calc_stats(portfolio: dict) -> dict:
    """Aggregate stats from all closed trades."""
    closed = portfolio.get("closed", [])
    if not closed:
        return {"total_trades": 0}

    wins   = [t for t in closed if t.get("outcome") == "WIN"]
    losses = [t for t in closed if t.get("outcome") == "LOSS"]

    win_pnls  = [t.get("pnl_pct", 0) for t in wins]
    loss_pnls = [t.get("pnl_pct", 0) for t in losses]

    avg_win   = sum(win_pnls)  / len(wins)   if wins   else 0
    avg_loss  = sum(loss_pnls) / len(losses) if losses else 0
    win_rate  = len(wins) / len(closed) * 100 if closed else 0
    expectancy = (win_rate/100 * avg_win) + ((1 - win_rate/100) * avg_loss)

    # Profit factor
    gross_profit = sum(t.get("pnl_usd", 0) for t in wins)
    gross_loss   = abs(sum(t.get("pnl_usd", 0) for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    # NRGC Phase 3 accuracy
    phase3_entries = [t for t in closed if t.get("nrgc_phase_entry") == 3]
    phase3_wins    = [t for t in phase3_entries if t.get("outcome") == "WIN"]
    nrgc_accuracy  = (len(phase3_wins) / len(phase3_entries) * 100) if phase3_entries else None

    # Cap tier breakdown
    cap_pnl = {}
    for t in closed:
        tier = t.get("cap_tier", "unknown")
        cap_pnl.setdefault(tier, []).append(t.get("pnl_pct", 0))
    cap_stats = {
        tier: {
            "trades": len(pnls),
            "avg_pnl": round(sum(pnls)/len(pnls), 1),
            "wins": sum(1 for p in pnls if p >= 0),
        }
        for tier, pnls in cap_pnl.items()
    }

    # Theme breakdown
    theme_pnl = {}
    for t in closed:
        theme = t.get("theme", "Unknown")
        theme_pnl.setdefault(theme, []).append(t.get("pnl_pct", 0))
    theme_stats = {
        theme: {
            "trades": len(pnls),
            "avg_pnl": round(sum(pnls)/len(pnls), 1),
            "wins": sum(1 for p in pnls if p >= 0),
        }
        for theme, pnls in theme_pnl.items()
    }

    # Best / worst
    best  = max(closed, key=lambda t: t.get("pnl_pct", 0), default={})
    worst = min(closed, key=lambda t: t.get("pnl_pct", 0), default={})

    # Avg hold days
    avg_days = sum(t.get("days_held", 0) for t in closed) / len(closed)

    return {
        "total_trades":              len(closed),
        "wins":                      len(wins),
        "losses":                    len(losses),
        "win_rate_pct":              round(win_rate, 1),
        "avg_win_pct":               round(avg_win, 2),
        "avg_loss_pct":              round(avg_loss, 2),
        "expectancy":                round(expectancy, 2),
        "profit_factor":             round(profit_factor, 2),
        "best_trade":                best.get("ticker", ""),
        "best_pnl_pct":              best.get("pnl_pct", 0),
        "worst_trade":               worst.get("ticker", ""),
        "worst_pnl_pct":             worst.get("pnl_pct", 0),
        "avg_hold_days":             round(avg_days, 1),
        "nrgc_phase3_accuracy_pct":  round(nrgc_accuracy, 1) if nrgc_accuracy is not None else None,
        "cap_tier_breakdown":        cap_stats,
        "theme_breakdown":           theme_stats,
        "realized_pnl_usd":          portfolio.get("realized_pnl_usd", 0),
        "as_of":                     datetime.now().strftime("%Y-%m-%d"),
    }


def calc_drawdown(portfolio: dict) -> dict:
    """Calculate max drawdown from performance history."""
    if not PERF_FILE.exists():
        return {}
    try:
        history = json.loads(PERF_FILE.read_text(encoding="utf-8"))
        if not history:
            return {}
        capital = portfolio.get("capital", 100_000)
        navs = [h.get("total_value", capital) for h in history]
        peak = capital
        max_dd = 0.0
        for nav in navs:
            if nav > peak:
                peak = nav
            dd = (nav / peak - 1) * 100
            if dd < max_dd:
                max_dd = dd
        current_nav = navs[-1] if navs else capital
        current_dd  = (current_nav / max(navs + [capital]) - 1) * 100
        return {
            "max_drawdown_pct": round(max_dd, 2),
            "current_drawdown_pct": round(current_dd, 2),
        }
    except Exception:
        return {}


def get_recent_closed(portfolio: dict, days: int = 7) -> list:
    """Get trades closed in the last N days."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return [t for t in portfolio.get("closed", [])
            if t.get("exit_date", t.get("close_date", "")) >= cutoff]


# ─── Lesson Generator ─────────────────────────────────────────────────────────

LESSON_PROMPT = """You are AlphaAbsolute's Performance Analyst (Agent 13).
Goal: help this fund beat Nasdaq (QQQ) consistently with LOW DRAWDOWN.
Preferred style: mid/small cap, patient holds, only trade on strong NRGC/PULSE signals.

Analyze this closed paper trade and generate an actionable investment lesson.

Trade: {ticker} | Theme: {theme} | Cap tier: {cap_tier} (~${market_cap_b}B)
Entry: ${entry_price:.2f} on {open_date} (NRGC Phase {nrgc_phase_entry}, Score={nrgc_score_entry})
Exit:  ${exit_price:.2f} on {exit_date} | Regime at entry: {entry_regime}
Result: {outcome} {pnl_pct:+.1f}% in {days_held} days
Exit reason: {exit_reason}
NRGC phase at exit: {nrgc_phase_exit}
PULSE gate at entry: {pulse_gate}

Return JSON only:
{{
  "lesson_title": "string (max 8 words — the rule to remember)",
  "what_worked": "string (1 sentence)",
  "what_failed": "string or null (1 sentence)",
  "nrgc_call_correct": true|false,
  "cap_tier_insight": "string (was the mid/small cap preference correct here?)",
  "rule_to_update": "string or null (specific rule change — sizing, entry bar, exit trigger)",
  "regime_insight": "string or null (did the regime call matter?)",
  "confidence_in_lesson": "high|medium|low",
  "applies_to_theme": "{theme}",
  "applies_to_setup": "Phase3|Phase2|General",
  "drawdown_note": "string or null (was position drawdown acceptable?)"
}}"""


def generate_lesson(trade: dict) -> dict:
    """Generate a lesson from one closed trade via Groq LLM."""
    import sys
    sys.path.insert(0, str(BASE_DIR / "scripts" / "learning"))
    try:
        from distill_engine import _llm_call
        prompt = LESSON_PROMPT.format(
            ticker          = trade.get("ticker", "?"),
            theme           = trade.get("theme", "?"),
            cap_tier        = trade.get("cap_tier", "unknown"),
            market_cap_b    = trade.get("market_cap_b", 0) or 0,
            entry_price     = trade.get("entry_price", 0),
            open_date       = trade.get("open_date", "?"),
            nrgc_phase_entry= trade.get("nrgc_phase_entry", 3),
            nrgc_score_entry= trade.get("nrgc_score_entry", 0),
            exit_price      = trade.get("exit_price", 0),
            exit_date       = trade.get("exit_date", trade.get("close_date", "?")),
            entry_regime    = trade.get("entry_regime", "neutral"),
            outcome         = trade.get("outcome", "?"),
            pnl_pct         = trade.get("pnl_pct", 0),
            days_held       = trade.get("days_held", 0),
            exit_reason     = trade.get("exit_reason", trade.get("reason", "?")),
            nrgc_phase_exit = trade.get("nrgc_phase_exit", "?"),
            pulse_gate      = trade.get("pulse_gate", "?"),
        )
        text = _llm_call(prompt, max_tokens=500, call_type="lesson")
        if not text:
            return {}
        # Strip markdown code blocks if present
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        lesson = json.loads(text.strip())
        lesson["ticker"]      = trade.get("ticker")
        lesson["pnl_pct"]     = trade.get("pnl_pct")
        lesson["cap_tier"]    = trade.get("cap_tier")
        lesson["market_cap_b"]= trade.get("market_cap_b")
        lesson["generated"]   = datetime.now().strftime("%Y-%m-%d")
        return lesson
    except Exception as e:
        print(f"  [Lesson error] {trade.get('ticker','?')}: {e}")
        return {}


def save_lesson(lesson: dict):
    """Append lesson to lessons.json."""
    if not lesson:
        return
    LESSONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lessons = []
    if LESSONS_FILE.exists():
        try:
            lessons = json.loads(LESSONS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    lessons.append(lesson)
    LESSONS_FILE.write_text(json.dumps(lessons[-200:], indent=2, ensure_ascii=False))


# ─── Write to Claude Memory ────────────────────────────────────────────────────

def update_trading_memory(stats: dict, lessons: list, drawdown: dict = None,
                           perf: dict = None):
    """Write performance stats + lessons to Claude Code memory files."""
    if not lessons and not stats.get("total_trades"):
        return

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    mem_file = MEMORY_DIR / "paper_trading_lessons.md"

    # Lesson bullets
    lesson_bullets = []
    for l in lessons[-20:]:
        if not l.get("lesson_title"):
            continue
        correct  = "NRGC correct" if l.get("nrgc_call_correct") else "NRGC incorrect"
        pnl_str  = f"{l.get('pnl_pct', 0):+.1f}%"
        cap_str  = f"[{l.get('cap_tier','?')}cap]" if l.get("cap_tier") else ""
        bullet   = (
            f"- **{l['lesson_title']}** ({l.get('ticker','')} {pnl_str} {cap_str}, {correct})"
            f"\n  What worked: {l.get('what_worked','')}"
        )
        if l.get("what_failed"):
            bullet += f"\n  What failed: {l['what_failed']}"
        if l.get("rule_to_update"):
            bullet += f"\n  Rule update: {l['rule_to_update']}"
        if l.get("cap_tier_insight"):
            bullet += f"\n  Cap insight: {l['cap_tier_insight']}"
        lesson_bullets.append(bullet)

    # Cap tier performance
    cap_lines = []
    for tier, ts in stats.get("cap_tier_breakdown", {}).items():
        cap_lines.append(
            f"- {tier}cap: {ts['trades']} trades | avg {ts['avg_pnl']:+.1f}% | {ts['wins']}/{ts['trades']} wins"
        )

    # Theme performance
    theme_lines = []
    for theme, ts in sorted(stats.get("theme_breakdown", {}).items(),
                             key=lambda x: x[1].get("avg_pnl", 0), reverse=True):
        theme_lines.append(
            f"- {theme}: {ts['trades']} trades | avg {ts['avg_pnl']:+.1f}% | {ts['wins']}/{ts['trades']} wins"
        )

    # Drawdown section
    dd = drawdown or {}
    dd_line = (
        f"Max drawdown: {dd.get('max_drawdown_pct', 0):.1f}% | "
        f"Current: {dd.get('current_drawdown_pct', 0):.1f}%"
    )

    # Overall fund performance from perf dict
    nav_line = ""
    if perf:
        nav_line = (
            f"NAV: ${perf.get('total_value', 0):,.0f} ({perf.get('total_return_pct', 0):+.2f}%) | "
            f"vs QQQ: {perf.get('benchmark_return', 0):+.2f}% | "
            f"Alpha: {perf.get('alpha', 0):+.2f}pp"
        )

    content = f"""---
name: Paper Trading Lessons
description: Auto-generated weekly lessons from AlphaAbsolute paper portfolio — P&L, NRGC accuracy, cap tier analysis, drawdown, rule updates
type: feedback
---

## Fund Performance (as of {stats.get('as_of', datetime.now().strftime('%Y-%m-%d'))})

{nav_line}
- Total trades: {stats.get('total_trades', 0)} | Win rate: {stats.get('win_rate_pct', 0):.1f}%
- Avg win: {stats.get('avg_win_pct', 0):+.2f}% | Avg loss: {stats.get('avg_loss_pct', 0):+.2f}%
- Expectancy/trade: {stats.get('expectancy', 0):+.2f}% | Profit factor: {stats.get('profit_factor', 0):.2f}x
- Avg hold: {stats.get('avg_hold_days', 0):.0f} days
- NRGC Phase 3 accuracy: {stats.get('nrgc_phase3_accuracy_pct', 'N/A')}%
- Best: {stats.get('best_trade', '')} ({stats.get('best_pnl_pct', 0):+.1f}%) | Worst: {stats.get('worst_trade', '')} ({stats.get('worst_pnl_pct', 0):+.1f}%)
- {dd_line}

**Why:** Beat Nasdaq (QQQ) consistently with low drawdown. Mid/small cap bias for alpha.
Cash is a position — raise when regime = risk-off or no NRGC Phase 3 setups.
**How to apply:**
- NRGC accuracy > 65% → trust Phase 3 signals, size up
- NRGC accuracy < 45% → raise entry bar, require score ≥ 65
- Win rate < 40% → check if regime gate is working properly
- Max drawdown > 15% → tighten stop rules or raise cash threshold

## Cap Tier Performance

{chr(10).join(cap_lines) if cap_lines else 'No closed trades yet.'}

## Theme Performance

{chr(10).join(theme_lines) if theme_lines else 'No closed trades yet.'}

## Lessons Learned (Latest {len(lesson_bullets)})

{chr(10).join(lesson_bullets) if lesson_bullets else 'No lessons yet — trades still open or no exits.'}
"""

    mem_file.write_text(content, encoding="utf-8")
    print(f"  [Memory] Updated: {mem_file.name}")
    _update_memory_index(mem_file.name)


def _update_memory_index(filename: str):
    """Add/update entry in MEMORY.md index."""
    index_file = MEMORY_DIR / "MEMORY.md"
    if not index_file.exists():
        return
    content = index_file.read_text(encoding="utf-8")
    entry = f"- [Paper Trading Lessons]({filename}) — Auto-updated weekly: P&L, alpha vs QQQ, NRGC accuracy, cap tier analysis, drawdown, rule updates"
    if filename in content:
        lines = content.splitlines()
        new_lines = [entry if filename in line else line for line in lines]
        index_file.write_text("\n".join(new_lines), encoding="utf-8")
    else:
        index_file.write_text(content.rstrip() + "\n" + entry + "\n", encoding="utf-8")


# ─── Weekly Learning Run ───────────────────────────────────────────────────────

def run_weekly_learning(portfolio: dict, perf: dict = None) -> dict:
    """
    Full weekly learning cycle:
    1. Calculate performance stats (win rate, expectancy, profit factor, NRGC accuracy, cap tier)
    2. Calculate drawdown
    3. Generate lessons for recently closed trades (Groq LLM)
    4. Save lessons + update Claude memory
    Returns summary dict.
    """
    stats    = calc_stats(portfolio)
    drawdown = calc_drawdown(portfolio)
    recent   = get_recent_closed(portfolio, days=7)

    print(f"  [Learning] {stats.get('total_trades',0)} total trades"
          f" | Win rate: {stats.get('win_rate_pct',0):.1f}%"
          f" | Expectancy: {stats.get('expectancy',0):+.2f}%"
          f" | PF: {stats.get('profit_factor',0):.2f}x")
    if stats.get("nrgc_phase3_accuracy_pct") is not None:
        print(f"  [Learning] NRGC Phase 3 accuracy: {stats['nrgc_phase3_accuracy_pct']:.1f}%")
    if drawdown:
        print(f"  [Learning] Max drawdown: {drawdown.get('max_drawdown_pct',0):.1f}%"
              f" | Current: {drawdown.get('current_drawdown_pct',0):.1f}%")

    # Generate lessons for trades closed this week
    new_lessons = []
    for trade in recent:
        lesson = generate_lesson(trade)
        if lesson:
            save_lesson(lesson)
            new_lessons.append(lesson)
            print(f"  [Lesson] {trade.get('ticker')}: {lesson.get('lesson_title','')}")

    # Save performance snapshot
    PERF_FILE.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if PERF_FILE.exists():
        try:
            history = json.loads(PERF_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    snapshot = {**stats, **drawdown}
    if perf:
        snapshot["total_value"]    = perf.get("total_value")
        snapshot["total_return"]   = perf.get("total_return_pct")
        snapshot["benchmark"]      = perf.get("benchmark_return")
        snapshot["alpha"]          = perf.get("alpha")
        snapshot["cash_pct"]       = perf.get("cash_pct")
        snapshot["num_positions"]  = perf.get("num_positions")
    history.append(snapshot)
    PERF_FILE.write_text(json.dumps(history[-104:], indent=2, ensure_ascii=False))  # 2 years

    # Load all lessons for memory update
    all_lessons = []
    if LESSONS_FILE.exists():
        try:
            all_lessons = json.loads(LESSONS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    update_trading_memory(stats, all_lessons[-20:], drawdown, perf)

    return {
        "stats":       stats,
        "drawdown":    drawdown,
        "new_lessons": len(new_lessons),
    }
