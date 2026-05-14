"""
AlphaAbsolute — Monthly Runner (1st Sunday of each month, 09:00 ICT)
Deep learning cycle: NRGC calibration + theme rotation + framework improvement.
This is the system's self-improvement engine — it learns from 4 weeks of data
and proposes specific rule updates to get better at beating NASDAQ forever.
"""
import sys, json, os, io
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts" / "learning"))
sys.path.insert(0, str(BASE_DIR / "scripts" / "paper_trading"))
sys.path.insert(0, str(BASE_DIR / "scripts"))

import requests
import urllib3
urllib3.disable_warnings()


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ─── LLM (Groq → Gemini) ──────────────────────────────────────────────────────
def _call_llm(prompt: str, max_tokens: int = 1200) -> str:
    groq_key   = os.environ.get("GROQ_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")

    if groq_key:
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}",
                         "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": max_tokens},
                timeout=40, verify=False,
            )
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            log(f"  Groq failed: {e}")

    if gemini_key:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.0-flash:generateContent?key={gemini_key}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=40, verify=False,
            )
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            log(f"  Gemini failed: {e}")

    return ""


# ─── Step 1: NRGC Accuracy Review ────────────────────────────────────────────
def review_nrgc_accuracy(portfolio: dict) -> dict:
    """
    Review closed trades to measure NRGC phase call accuracy.
    Phase 3 (Inflection) entry → was it actually the best entry timing?
    """
    closed = portfolio.get("closed", [])
    month_ago = (datetime.now() - timedelta(days=35)).strftime("%Y-%m-%d")
    recent = [t for t in closed
              if t.get("exit_date", t.get("close_date", "")) >= month_ago]

    if not recent:
        return {"trades": 0, "phase3_accuracy": None}

    phase3_entries = [t for t in recent if t.get("entry_phase") == 3]
    phase3_wins    = [t for t in phase3_entries if t.get("pnl_pct", 0) > 0]
    phase4_entries = [t for t in recent if t.get("entry_phase") == 4]
    phase4_wins    = [t for t in phase4_entries if t.get("pnl_pct", 0) > 0]

    def win_rate(entries, wins):
        return round(len(wins) / len(entries) * 100, 1) if entries else None

    avg_pnl = lambda trades: (
        round(sum(t.get("pnl_pct", 0) for t in trades) / len(trades), 2)
        if trades else 0
    )

    return {
        "trades": len(recent),
        "phase3_entries":  len(phase3_entries),
        "phase3_win_rate": win_rate(phase3_entries, phase3_wins),
        "phase3_avg_pnl":  avg_pnl(phase3_entries),
        "phase4_entries":  len(phase4_entries),
        "phase4_win_rate": win_rate(phase4_entries, phase4_wins),
        "phase4_avg_pnl":  avg_pnl(phase4_entries),
        "overall_avg_pnl": avg_pnl(recent),
        "overall_win_rate": win_rate(recent, [t for t in recent if t.get("pnl_pct",0) > 0]),
    }


# ─── Step 2: Focus List Performance Review ───────────────────────────────────
def review_focus_performance() -> dict:
    """Review last 4 weeks of focus list outcomes."""
    history_file = BASE_DIR / "data" / "paper_trading" / "focus_history.json"
    if not history_file.exists():
        return {}

    try:
        history = json.loads(history_file.read_text(encoding="utf-8"))
    except Exception:
        return {}

    # Last 4 weeks
    recent_weeks = history[-4:] if len(history) >= 4 else history
    if not recent_weeks:
        return {}

    all_picks, triggered, wins, stops = [], [], [], []
    cap_perf, theme_perf = {}, {}

    for week in recent_weeks:
        for p in week.get("picks", []):
            all_picks.append(p)
            if p.get("triggered"):
                triggered.append(p)
                label = p.get("outcome_label", "")
                pnl   = p.get("outcome_pct", 0) or 0

                if label in ("WIN", "BIG_WIN", "SMALL_WIN"):
                    wins.append(p)
                elif label == "STOP":
                    stops.append(p)

                # Cap tier breakdown
                cap = p.get("cap_tier", "unknown")
                if cap not in cap_perf:
                    cap_perf[cap] = {"triggered": 0, "wins": 0, "total_pnl": 0}
                cap_perf[cap]["triggered"] += 1
                cap_perf[cap]["total_pnl"] += pnl
                if label in ("WIN", "BIG_WIN", "SMALL_WIN"):
                    cap_perf[cap]["wins"] += 1

                # Theme breakdown
                theme = p.get("theme", "unknown")
                if theme not in theme_perf:
                    theme_perf[theme] = {"triggered": 0, "wins": 0, "total_pnl": 0}
                theme_perf[theme]["triggered"] += 1
                theme_perf[theme]["total_pnl"] += pnl
                if label in ("WIN", "BIG_WIN", "SMALL_WIN"):
                    theme_perf[theme]["wins"] += 1

    trigger_rate = round(len(triggered) / len(all_picks) * 100, 1) if all_picks else 0
    win_rate     = round(len(wins) / len(triggered) * 100, 1) if triggered else 0
    avg_pnl      = round(sum(p.get("outcome_pct", 0) or 0 for p in triggered)
                         / len(triggered), 2) if triggered else 0

    # Best/worst cap tier
    for cap, data in cap_perf.items():
        data["win_rate"] = round(data["wins"] / data["triggered"] * 100, 1) if data["triggered"] else 0
        data["avg_pnl"]  = round(data["total_pnl"] / data["triggered"], 2) if data["triggered"] else 0
    for theme, data in theme_perf.items():
        data["win_rate"] = round(data["wins"] / data["triggered"] * 100, 1) if data["triggered"] else 0
        data["avg_pnl"]  = round(data["total_pnl"] / data["triggered"], 2) if data["triggered"] else 0

    return {
        "weeks_reviewed":  len(recent_weeks),
        "total_picks":     len(all_picks),
        "trigger_rate":    trigger_rate,
        "win_rate":        win_rate,
        "avg_pnl":         avg_pnl,
        "cap_performance": cap_perf,
        "theme_performance": dict(sorted(theme_perf.items(),
                                         key=lambda x: x[1]["avg_pnl"], reverse=True)),
    }


# ─── Step 3: Theme Health Check ───────────────────────────────────────────────
def review_theme_health(portfolio: dict, focus_perf: dict) -> dict:
    """
    Review which of the 14 themes are working and which are lagging.
    Based on: open position P&L by theme + focus list performance by theme.
    """
    positions = portfolio.get("positions", {})
    closed    = portfolio.get("closed", [])
    month_ago = (datetime.now() - timedelta(days=35)).strftime("%Y-%m-%d")

    theme_live = {}
    for ticker, pos in positions.items():
        theme = pos.get("theme", "Unknown")
        pnl   = pos.get("pnl_pct", 0)
        if theme not in theme_live:
            theme_live[theme] = []
        theme_live[theme].append(pnl)

    theme_closed = {}
    for t in closed:
        if t.get("exit_date", "") >= month_ago:
            theme = t.get("theme", "Unknown")
            pnl   = t.get("pnl_pct", 0)
            if theme not in theme_closed:
                theme_closed[theme] = []
            theme_closed[theme].append(pnl)

    summary = {}
    all_themes = set(list(theme_live.keys()) + list(theme_closed.keys()))
    for theme in all_themes:
        live_pnls   = theme_live.get(theme, [])
        closed_pnls = theme_closed.get(theme, [])
        all_pnls    = live_pnls + closed_pnls
        focus_data  = focus_perf.get("theme_performance", {}).get(theme, {})
        summary[theme] = {
            "live_positions":  len(live_pnls),
            "avg_live_pnl":    round(sum(live_pnls)/len(live_pnls), 1) if live_pnls else 0,
            "closed_this_month": len(closed_pnls),
            "avg_closed_pnl":  round(sum(closed_pnls)/len(closed_pnls), 1) if closed_pnls else 0,
            "focus_win_rate":  focus_data.get("win_rate"),
            "focus_avg_pnl":   focus_data.get("avg_pnl"),
            "overall_score":   round(sum(all_pnls)/len(all_pnls), 1) if all_pnls else 0,
        }

    return dict(sorted(summary.items(),
                       key=lambda x: x[1]["overall_score"], reverse=True))


# ─── Step 4: Framework Improvement Prompt ────────────────────────────────────
FRAMEWORK_PROMPT = """You are AlphaAbsolute's Framework Improvement Agent.
Goal: Make the system a world-class trader that beats NASDAQ consistently with low drawdown.

MONTHLY PERFORMANCE DATA:

NRGC Phase Accuracy (last month):
{nrgc_data}

Focus List Performance (last 4 weeks):
- Weeks reviewed: {weeks}
- Total picks: {total_picks}
- Trigger rate: {trigger_rate:.0f}%
- Win rate (triggered): {win_rate:.0f}%
- Avg P&L (triggered): {avg_pnl:+.1f}%

Cap Tier Performance:
{cap_perf}

Theme Performance (top & bottom):
{theme_perf}

CURRENT SYSTEM RULES:
- NRGC Phase 3 (Inflection) minimum EMLS score: 60 (gets +5 bonus)
- Phase 4 minimum EMLS score: 60
- Phase filter: 2-4 only
- Trigger: pivot_high × 1.01
- Stop: trigger × 0.92 (-8%)
- R/R minimum: 1.5× (waived for Phase 3)
- Cap preference: mid > small > large > mega
- Max positions: 10
- Regime risk-off → 50% cash

EXISTING LESSONS FROM MEMORY:
{lessons_summary}

Generate a Monthly Framework Review with exactly this format:

## NRGC CALIBRATION
[2-3 specific adjustments to phase scoring, confidence thresholds, or phase filters based on accuracy data]

## THEME ROTATION
PROMOTE: [themes to increase weight/conviction] — reason
DEMOTE: [themes to reduce or remove from watchlist] — reason
WATCH: [new themes or setups to monitor next month]

## ENTRY RULE UPDATES
[2-3 specific changes to trigger/stop/EMLS scoring based on what worked]

## SIZING RULE UPDATES
[1-2 changes to position sizing based on cap tier / theme performance]

## FRAMEWORK VERDICT
Win rate trend: [improving/stable/declining]
Biggest edge identified: [specific, measurable]
Biggest weakness to fix: [specific, measurable]
Projected improvement if rules applied: [realistic estimate]"""


def generate_framework_update(nrgc_data: dict, focus_perf: dict,
                               theme_health: dict, lessons: list) -> str:
    """LLM-generate monthly framework improvement proposals."""
    # Cap tier summary
    cap_txt = ""
    for cap, data in focus_perf.get("cap_performance", {}).items():
        cap_txt += (f"  {cap}: triggered={data['triggered']} "
                    f"win_rate={data.get('win_rate','?')}% "
                    f"avg_pnl={data.get('avg_pnl','?')}%\n")

    # Theme summary (top 5 + bottom 3)
    theme_txt = ""
    th = focus_perf.get("theme_performance", {})
    items = list(th.items())
    for theme, data in items[:5]:
        theme_txt += (f"  TOP {theme}: wr={data.get('win_rate','?')}% "
                      f"avg={data.get('avg_pnl','?')}%\n")
    for theme, data in items[-3:]:
        theme_txt += (f"  BOT {theme}: wr={data.get('win_rate','?')}% "
                      f"avg={data.get('avg_pnl','?')}%\n")

    # Recent lessons summary
    lessons_txt = ""
    for l in lessons[-10:]:
        lessons_txt += f"- {l.get('lesson','')}\n"

    nrgc_txt = json.dumps(nrgc_data, indent=2)

    prompt = FRAMEWORK_PROMPT.format(
        nrgc_data=nrgc_txt,
        weeks=focus_perf.get("weeks_reviewed", 0),
        total_picks=focus_perf.get("total_picks", 0),
        trigger_rate=focus_perf.get("trigger_rate", 0),
        win_rate=focus_perf.get("win_rate", 0),
        avg_pnl=focus_perf.get("avg_pnl", 0),
        cap_perf=cap_txt or "No data yet",
        theme_perf=theme_txt or "No data yet",
        lessons_summary=lessons_txt or "No lessons yet — first month",
    )
    return _call_llm(prompt, max_tokens=1500)


# ─── Step 5: Write to Memory ──────────────────────────────────────────────────
def write_framework_update(update_text: str, nrgc_data: dict, focus_perf: dict):
    """Persist monthly framework update to memory files."""
    today = datetime.now().strftime("%Y-%m-%d")
    month = datetime.now().strftime("%Y-%m")

    # Main framework updates file
    fw_file = BASE_DIR / "memory" / "framework_updates.md"
    fw_file.parent.mkdir(parents=True, exist_ok=True)

    section = f"\n\n---\n# Monthly Review — {month}\n\n"
    section += f"**NRGC Accuracy:** {nrgc_data.get('phase3_win_rate','?')}% Phase3 win rate "
    section += f"| {nrgc_data.get('phase3_entries','?')} trades this month\n"
    section += f"**Focus List:** {focus_perf.get('trigger_rate','?')}% trigger rate "
    section += f"| {focus_perf.get('win_rate','?')}% win rate "
    section += f"| {focus_perf.get('avg_pnl','?'):+}% avg P&L\n\n"
    section += update_text

    existing = fw_file.read_text(encoding="utf-8") if fw_file.exists() else ""
    fw_file.write_text(existing + section, encoding="utf-8")

    # Also write a dated snapshot
    snap_file = BASE_DIR / "output" / f"framework_review_{today}.md"
    snap_file.parent.mkdir(parents=True, exist_ok=True)
    snap_file.write_text(
        f"# AlphaAbsolute Framework Review — {today}\n\n" + update_text,
        encoding="utf-8"
    )
    return str(snap_file)


# ─── Step 6: Telegram Monthly Report ─────────────────────────────────────────
def send_monthly_telegram(perf: dict, nrgc_data: dict, focus_perf: dict,
                           theme_health: dict, update_text: str):
    """Send concise monthly review to Telegram."""
    try:
        from telegram_notifier import send_chunks
    except Exception:
        return False

    now = datetime.now().strftime("%Y-%m")
    capital = perf.get("capital", 100_000)
    nav     = perf.get("total_value", capital)
    ret     = perf.get("total_return_pct", 0)
    bench   = perf.get("benchmark_return", 0)
    alpha   = perf.get("alpha", 0)

    lines = [
        f"<b>AlphaAbsolute Monthly Review — {now}</b>",
        "",
        f"<b>Fund NAV:</b> ${nav:,.0f} ({ret:+.2f}%)",
        f"vs QQQ: {bench:+.2f}% | Alpha: <b>{alpha:+.2f}pp</b> "
        f"{'BEATING' if alpha > 0 else 'LAGGING'} NASDAQ",
        f"Running: {perf.get('days_running', 0)} days | Closed: {perf.get('closed_trades', 0)} trades",
        "",
        "<b>NRGC Phase Accuracy (this month):</b>",
        f"  Phase 3 entries: {nrgc_data.get('phase3_entries', 0)} | "
        f"Win rate: {nrgc_data.get('phase3_win_rate', '?')}% | "
        f"Avg P&L: {nrgc_data.get('phase3_avg_pnl', 0):+.1f}%",
        f"  Overall: {nrgc_data.get('overall_win_rate', '?')}% win rate | "
        f"{nrgc_data.get('overall_avg_pnl', 0):+.1f}% avg",
        "",
        "<b>Focus List (4 weeks):</b>",
        f"  Picks: {focus_perf.get('total_picks', 0)} | "
        f"Triggered: {focus_perf.get('trigger_rate', 0):.0f}% | "
        f"Wins: {focus_perf.get('win_rate', 0):.0f}% | "
        f"Avg: {focus_perf.get('avg_pnl', 0):+.1f}%",
    ]

    # Top themes
    th = focus_perf.get("theme_performance", {})
    if th:
        top3 = list(th.items())[:3]
        lines.append("")
        lines.append("<b>Top Themes:</b>")
        for theme, data in top3:
            lines.append(f"  {theme}: {data.get('win_rate','?')}% wr | "
                         f"{data.get('avg_pnl',0):+.1f}% avg")

    # Framework update excerpt (first 400 chars)
    if update_text:
        lines.append("")
        lines.append("<b>Framework Updates:</b>")
        # Extract just the key sections
        for section in ["NRGC CALIBRATION", "THEME ROTATION", "FRAMEWORK VERDICT"]:
            idx = update_text.find(f"## {section}")
            if idx >= 0:
                end = update_text.find("\n## ", idx + 5)
                snippet = update_text[idx:end if end > 0 else idx + 300]
                lines.append(snippet[:250])

    lines.append("")
    lines.append("Full review saved to memory/framework_updates.md")

    msg = "\n".join(lines)
    send_chunks(msg)
    return True


# ─── Main ─────────────────────────────────────────────────────────────────────
def run_monthly():
    start = datetime.now()
    log("=" * 55)
    log(f"AlphaAbsolute Monthly Runner — {start.strftime('%Y-%m')}")
    log("=" * 55)

    # Load portfolio
    portfolio, perf = {}, {}
    try:
        from portfolio_engine import load_portfolio, get_performance
        portfolio = load_portfolio()
        perf      = get_performance(portfolio)
        log(f"Portfolio: ${perf['total_value']:,.0f} | "
            f"Return: {perf['total_return_pct']:+.2f}% | "
            f"Alpha: {perf['alpha']:+.2f}pp")
    except Exception as e:
        log(f"[ERROR] Portfolio load: {e}")

    # Step 1: NRGC accuracy
    log("\n[1/6] NRGC phase accuracy review...")
    nrgc_data = review_nrgc_accuracy(portfolio)
    log(f"  Phase3 win rate: {nrgc_data.get('phase3_win_rate','N/A')}% "
        f"({nrgc_data.get('phase3_entries',0)} trades)")

    # Step 2: Focus list performance
    log("\n[2/6] Focus list 4-week review...")
    focus_perf = review_focus_performance()
    log(f"  Trigger rate: {focus_perf.get('trigger_rate','?')}% | "
        f"Win rate: {focus_perf.get('win_rate','?')}% | "
        f"Avg P&L: {focus_perf.get('avg_pnl','?')}%")

    # Step 3: Theme health
    log("\n[3/6] Theme health check...")
    theme_health = review_theme_health(portfolio, focus_perf)
    top_themes = list(theme_health.items())[:3]
    log(f"  Top: {[(t, d['overall_score']) for t, d in top_themes]}")

    # Step 4: Load accumulated lessons
    log("\n[4/6] Loading accumulated lessons...")
    lessons = []
    try:
        from focus_list import load_focus_lessons
        lessons = load_focus_lessons()
        log(f"  {len(lessons)} lessons in memory")
    except Exception as e:
        log(f"  [skip]: {e}")

    # Step 5: Generate framework update (LLM)
    log("\n[5/6] Generating framework improvement proposals (Groq/Gemini)...")
    update_text = ""
    if focus_perf.get("total_picks", 0) > 0 or nrgc_data.get("trades", 0) > 0:
        update_text = generate_framework_update(
            nrgc_data, focus_perf, theme_health, lessons)
        if update_text:
            snap = write_framework_update(update_text, nrgc_data, focus_perf)
            log(f"  Framework update written: {snap}")
        else:
            log("  LLM returned no output")
    else:
        log("  Skipping — no data yet (first month)")

    # Step 6: Telegram
    log("\n[6/6] Sending monthly Telegram report...")
    try:
        ok = send_monthly_telegram(perf, nrgc_data, focus_perf, theme_health, update_text)
        log(f"  Telegram: {'sent' if ok else 'failed'}")
    except Exception as e:
        log(f"  [Telegram ERROR]: {e}")

    elapsed = (datetime.now() - start).seconds
    log(f"\nMonthly run complete in {elapsed}s")


if __name__ == "__main__":
    run_monthly()
