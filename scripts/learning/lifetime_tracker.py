"""
AlphaAbsolute — Lifetime Stats Tracker

Records the full life history of the system from Day 1 forever.
Every run adds to the cumulative record — knowledge, performance, learning velocity.

This is the "system biography" — shows how the AI has grown over months and years.
Updated: daily (performance) + weekly (knowledge) + monthly (framework)
Stored:  data/state/lifetime_stats.json  (committed back to GitHub every run)
"""
import json
from datetime import datetime, date
from pathlib import Path
from typing import Optional

BASE_DIR   = Path(__file__).resolve().parents[2]
STATS_FILE = BASE_DIR / "data" / "state" / "lifetime_stats.json"


# ─── Load / Init ──────────────────────────────────────────────────────────────

def load_stats() -> dict:
    if STATS_FILE.exists():
        try:
            return json.loads(STATS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return {
        "born":            today,
        "system_version":  "1.0",
        "runs": {
            "daily":   0,
            "weekly":  0,
            "monthly": 0,
            "last_daily":   "",
            "last_weekly":  "",
            "last_monthly": "",
        },
        "knowledge": {
            "indicator_kb_size":          0,
            "indicator_novel_count":      0,
            "indicator_adopted":          0,
            "total_lessons":              0,
            "focus_lessons":              0,
            "framework_updates":          0,
            "agent_lessons":              {},
            "thesis_accuracy_history":    [],   # weekly snapshots
            "source_count":               0,
            "kb_weekly_history":          [],   # [(week, size)] for velocity
        },
        "performance": {
            "all_time_return_pct":        0.0,
            "all_time_alpha_pp":          0.0,
            "best_week_return":           None,
            "best_month_return":          None,
            "total_trades":               0,
            "all_time_win_rate":          0.0,
            "biggest_winner":             None,   # {"ticker": ..., "pnl": ...}
            "biggest_loser":              None,
            "total_focus_picks":          0,
            "total_focus_triggered":      0,
            "focus_win_rate_history":     [],   # weekly snapshots [pct]
            "peak_nav":                   100_000,
            "peak_nav_date":              today,
            "current_drawdown_pct":       0.0,
            "max_drawdown_pct":           0.0,
            "months_beating_nasdaq":      0,
            "months_lagging_nasdaq":      0,
        },
        "milestones": [],   # [{date, event, value}]
        "snapshots":  [],   # weekly snapshots — keep last 260 (5 years)
    }


def save_stats(stats: dict):
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATS_FILE.write_text(json.dumps(stats, indent=2, default=str), encoding="utf-8")


# ─── Milestone Logger ─────────────────────────────────────────────────────────

_MILESTONE_THRESHOLDS = {
    "indicator_kb_size":  [50, 100, 250, 500, 1000, 2500],
    "total_lessons":      [25, 50, 100, 250, 500],
    "total_trades":       [10, 25, 50, 100, 250, 500, 1000],
    "framework_updates":  [5, 10, 25, 50],
}

def _check_milestones(stats: dict, field_path: str, new_val) -> Optional[dict]:
    """Return a milestone dict if a threshold is crossed, else None."""
    thresholds = _MILESTONE_THRESHOLDS.get(field_path, [])
    for t in thresholds:
        key = f"milestone_{field_path}_{t}"
        if new_val >= t and not stats.get("_milestone_flags", {}).get(key):
            stats.setdefault("_milestone_flags", {})[key] = True
            return {
                "date":  datetime.utcnow().strftime("%Y-%m-%d"),
                "event": f"{field_path} reached {t}",
                "value": new_val,
            }
    return None


# ─── Daily Update (performance only — fast) ───────────────────────────────────

def update_daily(perf: dict, portfolio: dict) -> dict:
    """
    Called from daily_runner. Updates performance metrics only.
    Fast — no heavy computation.
    """
    stats = load_stats()
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Run counter
    stats["runs"]["daily"] += 1
    stats["runs"]["last_daily"] = today

    # Performance
    p   = stats["performance"]
    nav = perf.get("total_value", 100_000)
    ret = perf.get("total_return_pct", 0)
    qqq = perf.get("benchmark_return", 0)

    p["all_time_return_pct"] = ret
    p["all_time_alpha_pp"]   = perf.get("alpha", 0)

    # Peak NAV tracking
    if nav > p.get("peak_nav", nav):
        p["peak_nav"]      = nav
        p["peak_nav_date"] = today
    p["current_drawdown_pct"] = round((nav / p["peak_nav"] - 1) * 100, 2) if p["peak_nav"] else 0
    if p["current_drawdown_pct"] < p.get("max_drawdown_pct", 0):
        p["max_drawdown_pct"] = p["current_drawdown_pct"]

    # Total trades
    closed = portfolio.get("closed", [])
    p["total_trades"] = len(closed)

    # Win rate
    wins = [t for t in closed if t.get("pnl_pct", 0) > 0]
    p["all_time_win_rate"] = round(len(wins) / len(closed) * 100, 1) if closed else 0

    # Best / worst single trade
    if closed:
        best = max(closed, key=lambda x: x.get("pnl_pct", 0))
        worst = min(closed, key=lambda x: x.get("pnl_pct", 0))
        p["biggest_winner"] = {"ticker": best.get("ticker","?"), "pnl": best.get("pnl_pct",0)}
        p["biggest_loser"]  = {"ticker": worst.get("ticker","?"), "pnl": worst.get("pnl_pct",0)}

    # Milestone: total trades
    m = _check_milestones(stats, "total_trades", p["total_trades"])
    if m:
        stats["milestones"].append(m)

    save_stats(stats)
    return stats


# ─── Weekly Update (knowledge + performance snapshot) ────────────────────────

def update_weekly(
    perf: dict,
    portfolio: dict,
    focus_result: dict      = None,
    indicator_result: dict  = None,
    research_result: dict   = None,
    agent_result: dict      = None,
    lessons_count: int      = 0,
) -> dict:
    """
    Called from weekly_runner. Full update: knowledge + performance + snapshot.
    """
    stats = load_stats()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    week  = datetime.utcnow().strftime("%Y-W%V")

    # Run counter
    stats["runs"]["weekly"] += 1
    stats["runs"]["last_weekly"] = today

    # ── Performance ───────────────────────────────────────────────────────────
    p   = stats["performance"]
    nav = perf.get("total_value", 100_000)
    ret = perf.get("total_return_pct", 0)
    qqq = perf.get("benchmark_return", 0)
    alpha = perf.get("alpha", 0)

    p["all_time_return_pct"] = ret
    p["all_time_alpha_pp"]   = alpha

    # Best week
    if p.get("best_week_return") is None or ret > p["best_week_return"]:
        p["best_week_return"] = ret

    # Beat/lag Nasdaq tracking
    if alpha > 0:
        # Approximate: if we're beating on the run overall
        pass  # tracked in monthly update

    # Peak NAV
    if nav > p.get("peak_nav", nav):
        p["peak_nav"]      = nav
        p["peak_nav_date"] = today
    p["current_drawdown_pct"] = round((nav / p["peak_nav"] - 1) * 100, 2) if p["peak_nav"] else 0
    if p["current_drawdown_pct"] < p.get("max_drawdown_pct", 0):
        p["max_drawdown_pct"] = p["current_drawdown_pct"]

    # Trades
    closed = portfolio.get("closed", [])
    p["total_trades"] = len(closed)
    wins = [t for t in closed if t.get("pnl_pct", 0) > 0]
    p["all_time_win_rate"] = round(len(wins) / len(closed) * 100, 1) if closed else 0
    if closed:
        best  = max(closed, key=lambda x: x.get("pnl_pct", 0))
        worst = min(closed, key=lambda x: x.get("pnl_pct", 0))
        p["biggest_winner"] = {"ticker": best.get("ticker","?"), "pnl": best.get("pnl_pct",0)}
        p["biggest_loser"]  = {"ticker": worst.get("ticker","?"), "pnl": worst.get("pnl_pct",0)}

    # Focus list stats
    if focus_result:
        picks = focus_result.get("picks", [])
        p["total_focus_picks"] += len(picks)
        triggered = [o for o in focus_result.get("prev_outcomes", []) if o.get("triggered")]
        p["total_focus_triggered"] += len(triggered)
        # Win rate snapshot
        if triggered:
            fw = len([o for o in triggered if o.get("outcome_label","") in ("WIN","BIG_WIN","SMALL_WIN")])
            p["focus_win_rate_history"].append(round(fw / len(triggered) * 100, 1))
            p["focus_win_rate_history"] = p["focus_win_rate_history"][-52:]  # 1 year

    # ── Knowledge ─────────────────────────────────────────────────────────────
    k = stats["knowledge"]

    # Indicator KB
    if indicator_result:
        new_kb_size = indicator_result.get("total_kb_size", 0)
        k["indicator_kb_size"]    = new_kb_size
        k["indicator_novel_count"] = k.get("indicator_novel_count",0) + indicator_result.get("novel_count",0)
        # Velocity history
        k.setdefault("kb_weekly_history", []).append((week, new_kb_size))
        k["kb_weekly_history"] = k["kb_weekly_history"][-52:]

    # Lessons
    k["total_lessons"]   = k.get("total_lessons",0) + lessons_count
    if focus_result:
        k["focus_lessons"] = k.get("focus_lessons",0) + len(focus_result.get("new_lessons",[]))

    # Agent lessons
    if agent_result:
        for agent_id in agent_result.get("agents_updated", []):
            k["agent_lessons"][agent_id] = k["agent_lessons"].get(agent_id, 0) + 1

    # Research thesis accuracy
    if research_result and research_result.get("thesis_accuracy"):
        acc = research_result["thesis_accuracy"]
        if isinstance(acc, (int, float)):
            k["thesis_accuracy_history"].append(round(float(acc), 1))
            k["thesis_accuracy_history"] = k["thesis_accuracy_history"][-52:]
        k["source_count"] = research_result.get("sources_ranked", k.get("source_count", 0))

    # Milestones
    for field in ["indicator_kb_size", "total_lessons"]:
        val = k.get(field, 0)
        m = _check_milestones(stats, field, val)
        if m:
            stats["milestones"].append(m)

    for field in ["total_trades"]:
        val = p.get(field, 0)
        m = _check_milestones(stats, field, val)
        if m:
            stats["milestones"].append(m)

    # ── Weekly snapshot ───────────────────────────────────────────────────────
    snapshot = {
        "week":                week,
        "date":                today,
        "nav":                 round(nav, 2),
        "return_pct":          round(ret, 2),
        "alpha_pp":            round(alpha, 2),
        "total_trades":        p["total_trades"],
        "win_rate":            p["all_time_win_rate"],
        "indicator_kb_size":   k["indicator_kb_size"],
        "total_lessons":       k["total_lessons"],
        "drawdown":            p["current_drawdown_pct"],
    }
    stats.setdefault("snapshots", []).append(snapshot)
    stats["snapshots"] = stats["snapshots"][-260:]  # 5 years of weekly snapshots

    save_stats(stats)
    return stats


# ─── Monthly Update ───────────────────────────────────────────────────────────

def update_monthly(perf: dict, beat_nasdaq: bool, framework_updates: int = 0) -> dict:
    """Called from monthly_runner."""
    stats = load_stats()
    today = datetime.utcnow().strftime("%Y-%m-%d")

    stats["runs"]["monthly"] += 1
    stats["runs"]["last_monthly"] = today

    p = stats["performance"]
    if beat_nasdaq:
        p["months_beating_nasdaq"] = p.get("months_beating_nasdaq", 0) + 1
    else:
        p["months_lagging_nasdaq"] = p.get("months_lagging_nasdaq", 0) + 1

    k = stats["knowledge"]
    k["framework_updates"] = k.get("framework_updates", 0) + framework_updates

    m = _check_milestones(stats, "framework_updates", k["framework_updates"])
    if m:
        stats["milestones"].append(m)

    save_stats(stats)
    return stats


# ─── Telegram Summary ─────────────────────────────────────────────────────────

def get_lifetime_telegram_block(stats: dict = None) -> str:
    """
    Returns a compact but meaningful lifetime summary for the Telegram weekly message.
    Shows the system's age, cumulative knowledge, and performance record.
    """
    if stats is None:
        stats = load_stats()

    born_str  = stats.get("born", "?")
    today     = datetime.utcnow().date()
    try:
        born_date = date.fromisoformat(born_str)
        age_days  = (today - born_date).days
        age_str   = (f"{age_days // 365}y {(age_days % 365) // 30}m"
                     if age_days >= 365 else f"{age_days}d")
    except Exception:
        age_days = 0
        age_str  = "?"

    p = stats.get("performance", {})
    k = stats.get("knowledge",   {})
    r = stats.get("runs",        {})

    # Performance line
    ret        = p.get("all_time_return_pct", 0)
    alpha      = p.get("all_time_alpha_pp",   0)
    total_tr   = p.get("total_trades", 0)
    win_rate   = p.get("all_time_win_rate", 0)
    max_dd     = p.get("max_drawdown_pct", 0)
    months_win = p.get("months_beating_nasdaq", 0)
    months_tot = months_win + p.get("months_lagging_nasdaq", 0)
    peak       = p.get("peak_nav", 100_000)
    best_trade = p.get("biggest_winner")

    # Knowledge line
    kb_size   = k.get("indicator_kb_size", 0)
    lessons   = k.get("total_lessons", 0)
    fw_upd    = k.get("framework_updates", 0)
    thesis_h  = k.get("thesis_accuracy_history", [])
    thesis_now = f"{thesis_h[-1]:.0f}%" if thesis_h else "—"
    thesis_start = f"{thesis_h[0]:.0f}%" if len(thesis_h) > 1 else "—"

    # Learning velocity (last 4 weeks indicator growth)
    kb_hist = k.get("kb_weekly_history", [])
    if len(kb_hist) >= 4:
        vel = round((kb_hist[-1][1] - kb_hist[-4][1]) / 4, 1)
        vel_str = f"+{vel:.1f}/wk"
    else:
        vel_str = "building..."

    # Weekly runs
    weekly_runs = r.get("weekly", 0)

    lines = [
        f"<b>System Age: {age_str}</b> | Born {born_str} | {weekly_runs} weekly runs",
        "",
        "<b>All-Time Performance:</b>",
        f"  Return: {ret:+.1f}% | Alpha: {alpha:+.1f}pp vs QQQ"
        + (f" | {months_win}/{months_tot}mo beating NASDAQ" if months_tot > 0 else ""),
        f"  {total_tr} trades | Win rate: {win_rate:.1f}% | Max DD: {max_dd:.1f}%",
    ]

    if best_trade:
        lines.append(f"  Best trade: {best_trade['ticker']} {best_trade['pnl']:+.1f}%")

    lines += [
        "",
        "<b>Cumulative Knowledge:</b>",
        f"  Indicators: {kb_size} | Lessons: {lessons} | Framework updates: {fw_upd}",
        f"  Learning velocity: {vel_str} | Thesis accuracy: {thesis_start} → {thesis_now}",
    ]

    # Recent milestones
    milestones = stats.get("milestones", [])
    recent_ms  = [m for m in milestones if m.get("date","") >= str(today - __import__('datetime').timedelta(days=30))]
    if recent_ms:
        lines.append("")
        lines.append("<b>Recent Milestones:</b>")
        for m in recent_ms[-3:]:
            lines.append(f"  {m['date']}: {m['event']}")

    return "\n".join(lines)


# ─── Compact daily block ──────────────────────────────────────────────────────

def get_lifetime_daily_line(stats: dict = None) -> str:
    """Single-line lifetime summary for daily Telegram (non-intrusive)."""
    if stats is None:
        if not STATS_FILE.exists():
            return ""
        try:
            stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return ""

    born_str = stats.get("born", "")
    try:
        age = (date.today() - date.fromisoformat(born_str)).days
    except Exception:
        age = 0

    p  = stats.get("performance", {})
    k  = stats.get("knowledge",   {})
    ret = p.get("all_time_return_pct", 0)
    kb  = k.get("indicator_kb_size", 0)
    les = k.get("total_lessons", 0)

    return f"System age: {age}d | Return: {ret:+.1f}% | KB: {kb} | Lessons: {les}"
