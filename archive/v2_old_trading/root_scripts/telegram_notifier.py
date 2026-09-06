"""
AlphaAbsolute — Telegram Notifier
Sends portfolio alerts, NRGC signals, and daily/weekly summaries to Telegram.

Uses: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID from .env
Cost: $0 (Telegram Bot API is free, no rate limits for personal use)
"""
import json, os, time, sys
from datetime import datetime
from pathlib import Path
from typing import Optional
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = Path(__file__).parent.parent

# Stop-loss rule for fallback display
RULES = {"stop_loss_pct": -0.08}

# Load .env → merge into os.environ (works locally + GitHub Actions)
import os as _os
_env_path = BASE_DIR / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8-sig").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _os.environ.setdefault(_k.strip(), _v.strip())

BOT_TOKEN = _os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = _os.environ.get("TELEGRAM_CHAT_ID", "")

# ─── Core Send Function ───────────────────────────────────────────────────────

def send(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the Telegram chat. Returns True if successful."""
    if not BOT_TOKEN or not CHAT_ID:
        print("  [Telegram] No bot token or chat ID configured")
        return False
    try:
        s = requests.Session()
        s.verify = False
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = s.post(url, json={
            "chat_id": CHAT_ID,
            "text": text[:4096],  # Telegram max message length
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }, timeout=15)
        if r.status_code == 200:
            return True
        else:
            print(f"  [Telegram error] {r.status_code}: {r.text[:200]}")
            return False
    except Exception as e:
        print(f"  [Telegram error] {e}")
        return False


def send_chunks(text: str, parse_mode: str = "HTML"):
    """Send long text split into chunks (Telegram 4096 char limit)."""
    for i in range(0, len(text), 4000):
        send(text[i:i+4000], parse_mode)
        if i + 4000 < len(text):
            time.sleep(0.5)  # avoid flood


# ─── Load rich portfolio.json (real portfolio data) ──────────────────────────

def _load_real_portfolio() -> dict:
    """Load the rich portfolio.json with full position detail."""
    try:
        p = BASE_DIR / "data" / "portfolio.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _get_live_price(ticker: str) -> Optional[float]:
    """Quick live price fetch via Yahoo Finance."""
    try:
        import requests as _req, urllib3 as _u3
        _u3.disable_warnings()
        s = _req.Session(); s.verify = False
        s.headers["User-Agent"] = "Mozilla/5.0"
        r = s.get(
            f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={"interval": "1d", "range": "5d"}, timeout=8
        )
        result = r.json().get("chart", {}).get("result")
        if result:
            return float(result[0]["meta"]["regularMarketPrice"])
    except Exception:
        pass
    return None


# ─── Daily Summary ────────────────────────────────────────────────────────────

def send_daily_summary(portfolio: dict, perf: dict, alerts: list,
                        insights: list = None, urgent: list = None):
    """Send daily fund statement — positions, P&L, transactions, benchmark vs QQQ."""
    now      = datetime.now().strftime("%Y-%m-%d %H:%M")
    beating  = perf.get("beating_nasdaq", False)
    capital  = perf.get("capital", 100_000)
    t_value  = perf.get("total_value", capital)
    t_ret    = perf.get("total_return_pct", 0)
    qqq_ret  = perf.get("benchmark_return", 0)
    alpha    = perf.get("alpha", 0)
    realized = perf.get("realized_pnl_usd", 0)
    unreal   = perf.get("unrealized_pnl_usd", 0)

    lines = [
        f"<b>AlphaAbsolute Model Portfolio</b> | {now}",
        "",
        f"<b>NAV:</b> ${t_value:,.0f}  ({t_ret:+.2f}%)",
        f"vs QQQ: {qqq_ret:+.2f}%  |  Alpha: <b>{alpha:+.2f}pp</b>  {'BEATING' if beating else 'LAGGING'}",
        f"Realized P&L: ${realized:+,.0f}  |  Unrealized: ${unreal:+,.0f}",
        f"Cash: {perf.get('cash_pct',0):.1f}%  |  Invested: {perf.get('invested_pct',0):.1f}%"
        f"  |  Positions: {perf.get('num_positions',0)}/10",
    ]

    # ── Position Table ─────────────────────────────────────────────────────────
    positions = portfolio.get("positions", {})
    if positions:
        lines.append("")
        lines.append("<b>Holdings:</b>")
        for ticker, pos in sorted(positions.items(),
                                   key=lambda x: x[1].get("pnl_pct", 0), reverse=True):
            entry   = pos.get("entry_price", 0)
            curr    = pos.get("current_price", entry)
            cost    = pos.get("cost", 0)
            pnl_pct = pos.get("pnl_pct", 0)
            pnl_usd = pos.get("pnl_usd", 0)
            stop    = pos.get("stop", entry * 0.92)
            days    = pos.get("days_held", 0)
            theme   = pos.get("theme", "")
            weight  = round(cost / capital * 100, 1) if capital else 0
            stop_pct = ((stop / entry - 1) * 100) if entry else -8.0
            trail   = pos.get("trail_stop")
            trail_str = f" | Trail ${trail:.2f}" if trail else ""
            pnl_sign = "+" if pnl_pct >= 0 else ""
            lines.append(
                f"  <b>{ticker}</b> [{weight}%] {theme}"
                f"\n    Cost ${entry:.2f} | Now ${curr:.2f} ({pnl_sign}{pnl_pct:.1f}%) | P&L ${pnl_usd:+,.0f}"
                f"\n    Stop ${stop:.2f} ({stop_pct:+.1f}%){trail_str} | {days}d held"
            )

    # ── Transactions Today ────────────────────────────────────────────────────
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        trade_log_path = BASE_DIR / "data" / "paper_trading" / "trade_log.json"
        if trade_log_path.exists():
            import json as _json
            log_entries = _json.loads(trade_log_path.read_text(encoding="utf-8"))
            todays_trades = [e for e in log_entries if e.get("date", "") == today_str]
            if todays_trades:
                lines.append("")
                lines.append("<b>Transactions Today:</b>")
                for t in todays_trades:
                    action = t.get("action", "?")
                    ticker = t.get("ticker", "?")
                    price  = t.get("price", 0)
                    shares = t.get("shares", 0)
                    reason = t.get("reason", "")
                    pnl    = t.get("pnl_pct")
                    pnl_str = f" ({pnl:+.1f}%)" if pnl is not None else ""
                    lines.append(f"  {action} <b>{ticker}</b> @ ${price:.2f} x{shares}{pnl_str}")
                    if reason:
                        lines.append(f"    {reason[:60]}")
    except Exception:
        pass

    # ── Alerts ────────────────────────────────────────────────────────────────
    if alerts:
        lines.append("")
        lines.append("<b>Alerts:</b>")
        for a in alerts[:5]:
            lines.append(f"  ! {a}")

    # ── Urgent signals ────────────────────────────────────────────────────────
    if urgent:
        lines.append("")
        lines.append("<b>URGENT:</b>")
        for sig in urgent[:3]:
            lines.append(f"  {sig.get('headline', '')}")
            if sig.get("ticker"):
                lines.append(f"  -> {sig['ticker']}: {sig.get('action_note', '')}")

    if insights:
        lines.append("")
        lines.append(f"Signals today: {len(insights)}")

    return send("\n".join(lines))


# ─── Weekly Summary ───────────────────────────────────────────────────────────

def send_weekly_summary(perf: dict, nrgc_assessments: dict, synthesis: dict,
                         portfolio: dict, promotions: list,
                         token_cost_usd: float = 0,
                         paper_stats: dict = None,
                         new_lessons_count: int = 0,
                         focus_result: dict = None,
                         agent_memory_result: dict = None,
                         research_memory_result: dict = None,
                         indicator_result: dict = None,
                         lifetime_stats: dict = None,
                         alt_data_result: dict = None,
                         edge_result: dict = None,
                         narrative_result: dict = None):
    """Send full weekly summary to Telegram."""
    now = datetime.now().strftime("%Y-%m-%d")
    beating = perf.get("beating_nasdaq", False)

    capital  = perf.get("capital", 100_000)
    t_value  = perf.get("total_value", capital)
    t_ret    = perf.get("total_return_pct", 0)
    qqq_ret  = perf.get("benchmark_return", 0)
    alpha    = perf.get("alpha", 0)
    realized = perf.get("realized_pnl_usd", 0)
    unreal   = perf.get("unrealized_pnl_usd", 0)

    # ── Header ─────────────────────────────────────────────────────────────────
    lines = [
        f"<b>AlphaAbsolute Weekly Report</b> | {now}",
        "",
        f"<b>NAV:</b> ${t_value:,.0f}  ({t_ret:+.2f}%)",
        f"vs QQQ: {qqq_ret:+.2f}%  |  Alpha: <b>{alpha:+.2f}pp</b>  {'BEATING NASDAQ' if beating else 'Lagging Nasdaq'}",
        f"Realized P&L: ${realized:+,.0f}  |  Unrealized: ${unreal:+,.0f}",
        f"Cash: {perf.get('cash_pct',0):.1f}%  |  Invested: {perf.get('invested_pct',0):.1f}%"
        f"  |  {perf.get('num_positions',0)}/10 positions",
        f"Win rate: {perf.get('win_rate',0):.1f}%  |  Avg win: {perf.get('avg_win_pct',0):+.1f}%"
        f"  |  Avg loss: {perf.get('avg_loss_pct',0):+.1f}%",
        f"Running: {perf.get('days_running',0)}d  |  Closed trades: {perf.get('closed_trades',0)}",
    ]

    # ── NRGC Phase 3 Setups (highest conviction) ───────────────────────────────
    phase3 = [(t, a) for t, a in nrgc_assessments.items() if a.get("phase") == 3]
    phase2 = [(t, a) for t, a in nrgc_assessments.items() if a.get("phase") == 2]

    if phase3:
        lines.append("")
        lines.append(f"<b>Phase 3 - INFLECTION (Full position):</b>")
        for ticker, a in sorted(phase3, key=lambda x: -x[1].get("nrgc_composite_score", 0))[:6]:
            score = a.get("nrgc_composite_score", 0)
            conf = a.get("confidence", 0)
            rev = a.get("revenue_signal", {})
            qoq = rev.get("latest_qoq_pct")
            qoq_str = f" | QoQ {qoq:+.0f}%" if qoq else ""
            lines.append(f"  <b>{ticker}</b> ({a.get('theme','')}) | Score={score}{qoq_str}")

    if phase2:
        lines.append("")
        lines.append(f"<b>Phase 2 - ACCUMULATION (Build 25-30%):</b>")
        for ticker, a in phase2[:4]:
            lines.append(f"  {ticker} ({a.get('theme','')})")

    # ── Synthesis (if available) ───────────────────────────────────────────────
    if synthesis:
        lines.append("")
        regime = synthesis.get("regime_signal", "neutral")
        regime_emoji = {"risk-on": "RISK-ON", "risk-off": "RISK-OFF", "neutral": "NEUTRAL"}.get(regime, regime)
        lines.append(f"<b>Market Regime:</b> {regime_emoji}")
        themes = synthesis.get("top_themes", [])
        if themes:
            lines.append(f"<b>Top Themes:</b> {', '.join(themes[:3])}")
        opps = synthesis.get("top_opportunities", [])
        if opps:
            lines.append(f"<b>Best Opportunity:</b> {opps[0].get('ticker','')} — {opps[0].get('thesis','')[:60]}")

    # ── Promotions ─────────────────────────────────────────────────────────────
    ready = [p for p in promotions if p.get("ready")]
    if ready:
        lines.append("")
        lines.append(f"<b>READY FOR REAL MONEY:</b>")
        for p in ready[:3]:
            lines.append(f"  {p['ticker']}: {p['paper_pnl']:+.1f}% paper | Suggest {p['suggested_real_pct']}% real")

    # ── Closed Trades This Week ───────────────────────────────────────────────
    from datetime import timedelta as _td
    week_ago = (datetime.now() - _td(days=7)).strftime("%Y-%m-%d")
    closed_this_week = [
        t for t in portfolio.get("closed", [])
        if t.get("exit_date", t.get("close_date", "")) >= week_ago
    ]
    if closed_this_week:
        lines.append("")
        lines.append("<b>Closed This Week:</b>")
        for t in closed_this_week:
            outcome = "WIN" if t.get("pnl_pct", 0) >= 0 else "LOSS"
            ticker  = t.get("ticker", "?")
            pnl_pct = t.get("pnl_pct", 0)
            pnl_usd = t.get("pnl_usd", 0)
            days    = t.get("days_held", 0)
            reason  = t.get("exit_reason", t.get("reason", ""))[:40]
            lines.append(
                f"  [{outcome}] <b>{ticker}</b> {pnl_pct:+.1f}% (${pnl_usd:+,.0f}) | {days}d | {reason}"
            )

    # ── Open Positions ─────────────────────────────────────────────────────────
    positions = portfolio.get("positions", {})
    if positions:
        lines.append("")
        lines.append("<b>Open Positions:</b>")
        for ticker, pos in sorted(positions.items(),
                                   key=lambda x: x[1].get("pnl_pct", 0), reverse=True):
            curr  = pos.get("current_price", pos.get("entry_price", 0))
            entry = pos.get("entry_price", 0)
            pnl   = pos.get("pnl_pct", ((curr / entry - 1) * 100) if entry else 0)
            cost  = pos.get("cost", 0)
            weight = round(cost / capital * 100, 1) if capital else 0
            lines.append(
                f"  <b>{ticker}</b> [{weight}%] {pnl:+.1f}%"
                f" | {pos.get('days_held',0)}d | {pos.get('theme','')}"
            )

    # ── Paper Trading P&L ────────────────────────────────────────────────────
    if paper_stats and paper_stats.get("total_trades", 0) > 0:
        lines.append("")
        lines.append("<b>Paper Portfolio Stats:</b>")
        lines.append(
            f"  {paper_stats.get('total_trades',0)} trades | "
            f"Win rate: {paper_stats.get('win_rate_pct',0):.1f}% | "
            f"Expectancy: {paper_stats.get('expectancy',0):+.2f}%"
        )
        lines.append(
            f"  Avg win: {paper_stats.get('avg_win_pct',0):+.1f}% | "
            f"Avg loss: {paper_stats.get('avg_loss_pct',0):+.1f}%"
        )
        if paper_stats.get("nrgc_phase3_accuracy_pct") is not None:
            lines.append(f"  NRGC Phase 3 accuracy: {paper_stats['nrgc_phase3_accuracy_pct']:.1f}%")
        if paper_stats.get("best_trade"):
            lines.append(f"  Best: {paper_stats['best_trade']} | Worst: {paper_stats.get('worst_trade','')}")
        theme_breakdown = paper_stats.get("theme_breakdown", {})
        if theme_breakdown:
            top_themes = sorted(theme_breakdown.items(),
                                key=lambda x: x[1].get("avg_pnl", 0), reverse=True)[:3]
            for theme, ts in top_themes:
                lines.append(f"  {theme}: {ts['wins']}/{ts['trades']} wins | avg {ts['avg_pnl']:+.1f}%")
        if new_lessons_count > 0:
            lines.append(f"  New lessons this week: {new_lessons_count} (saved to memory)")

    # ── Agent Accuracy ────────────────────────────────────────────────────────
    if agent_memory_result and agent_memory_result.get("verified_agents"):
        veri = agent_memory_result.get("verification", {})
        n_lessons = agent_memory_result.get("lessons_generated", 0)
        lines.append("")
        lines.append(f"<b>Agent Accuracy This Week:</b>")
        if "agent_01" in veri:
            d = veri["agent_01"]
            icon = "CORRECT" if d.get("regime_correct") else "WRONG"
            lines.append(f"  01 Macro: Regime {d.get('regime_call','?')} [{icon}]"
                         f" | QQQ {d.get('qqq_return_1w',0):+.1f}%")
        if "agent_03" in veri:
            d = veri["agent_03"]
            lines.append(f"  03 Screener: Phase3 avg {d.get('avg_return',0):+.1f}%"
                         f" | Beat QQQ: {d.get('beat_qqq_count',0)}/{d.get('total_picks',0)}")
        if "agent_05" in veri:
            d = veri["agent_05"]
            edge = d.get("conviction_edge")
            if edge is not None:
                lines.append(f"  05 Thematic: Conviction edge {edge:+.1f}%pp")
        if "agent_09" in veri:
            d = veri["agent_09"]
            lines.append(f"  09 Strategist: Opp hit rate {d.get('hit_rate',0):.0f}%")
        if "agent_12" in veri:
            d = veri["agent_12"]
            lines.append(f"  12 Risk: {d.get('flags_raised',0)} flags | "
                         f"{d.get('accuracy','?')}% accurate")
        if n_lessons > 0:
            updated = agent_memory_result.get("agents_updated", [])
            lines.append(f"  Lessons generated: {n_lessons} | Updated: {', '.join(updated)}")

    # ── Focus List ────────────────────────────────────────────────────────────
    if focus_result and focus_result.get("picks"):
        picks = focus_result["picks"]
        lines.append("")
        lines.append(f"<b>WEEKLY FOCUS LIST ({len(picks)} picks)</b>")
        phase_icon = {2: "ACCUM", 3: "INFLECT", 4: "RECOG"}
        emls_icon  = {"Hyper Leader": "HYPER", "Institutional Leader": "LEADER",
                      "Emerging Leader": "EMERG", "Watchlist": "WATCH"}
        for i, p in enumerate(picks[:8], 1):
            ticker = p["ticker"]
            score  = p.get("emls_score", 0)
            label  = p.get("emls_label", "")
            phase  = p.get("phase", 0)
            theme  = p.get("theme", "")
            cap    = p.get("cap_tier", "")
            trigger= p.get("trigger")
            stop   = p.get("stop")
            target = p.get("target")
            rr     = p.get("rr_ratio", 0)
            zone_l = p.get("zone_low")
            zone_h = p.get("zone_high")
            qoq    = p.get("qoq_pct")
            rs6m   = p.get("rs_6m_pct")
            vol_c  = p.get("vol_contracting", False)
            ext    = p.get("extended", False)

            ph_str = phase_icon.get(phase, f"Ph{phase}")
            el_str = emls_icon.get(label, label)

            row = f"  <b>#{i} {ticker}</b> | EMLS {score} {el_str} | {ph_str} | {cap}cap | {theme}"
            if zone_l and zone_h:
                row += f"\n     Zone: ${zone_l}–${zone_h}"
            if trigger:
                row += f"\n     Trigger: ${trigger}"
                if stop:
                    row += f" | Stop: ${stop} (−8%)"
            if target and rr:
                row += f" | Target: ${target} | R/R {rr}x"
            extras = []
            if qoq:
                extras.append(f"QoQ {qoq:+.0f}%")
            if rs6m:
                extras.append(f"RS6m {rs6m:+.0f}%")
            if vol_c:
                extras.append("Vol dry")
            if ext:
                extras.append("EXTENDED-caution")
            if extras:
                row += f"\n     {' | '.join(extras)}"
            lines.append(row)

    # ── Last Week Focus List Outcomes ─────────────────────────────────────────
    if focus_result and focus_result.get("prev_outcomes"):
        outcomes = focus_result["prev_outcomes"]
        triggered = [o for o in outcomes if o.get("triggered")]
        if triggered:
            lines.append("")
            lines.append("<b>Last Week Focus — Outcomes:</b>")
            for o in triggered:
                label  = o.get("outcome_label", "?")
                pnl    = o.get("outcome_pct")
                ticker = o["ticker"]
                pnl_str = f" {pnl:+.1f}%" if pnl is not None else ""
                icon = {"BIG_WIN":"WIN+", "WIN":"WIN", "SMALL_WIN":"WIN-",
                        "STOP":"STOP", "OPEN_LOSS":"OPEN-"}.get(label, label)
                lines.append(f"  [{icon}] {ticker}{pnl_str}")
        no_trig = [o for o in outcomes if o.get("outcome_label") == "NO_TRIGGER"]
        if no_trig:
            tickers = ", ".join(o["ticker"] for o in no_trig)
            lines.append(f"  Not triggered: {tickers}")

    # ── Focus Lessons ────────────────────────────────────────────────────────
    if focus_result and focus_result.get("new_lessons"):
        lessons = focus_result["new_lessons"]
        lines.append("")
        lines.append(f"<b>Lessons This Week ({len(lessons)}):</b>")
        for l in lessons[:3]:
            lesson  = l.get("lesson", "")[:80]
            action  = l.get("action", "")[:80]
            lines.append(f"  L: {lesson}")
            if action:
                lines.append(f"  A: {action}")
        total_l = focus_result.get("total_lessons", 0)
        if total_l:
            lines.append(f"  Total lessons stored: {total_l}")

    # ── Research Team Learning ────────────────────────────────────────────────
    if research_memory_result:
        lines.append("")
        lines.append("<b>Research Team Learning:</b>")
        top_src   = research_memory_result.get("top_source", "?")
        n_src     = research_memory_result.get("sources_ranked", 0)
        t_ver     = research_memory_result.get("theses_verified", 0)
        t_acc     = research_memory_result.get("thesis_accuracy")
        kb_n      = research_memory_result.get("kb_tickers", 0)
        kb_add    = research_memory_result.get("kb_added", 0)
        t_saved   = research_memory_result.get("theses_saved", 0)

        lines.append(f"  READ: Top source = {top_src} ({n_src} sources ranked)")
        if t_ver > 0:
            acc_str = f"{t_acc:.0f}%" if t_acc else "—"
            lines.append(f"  WRITE: {t_ver} theses verified | Accuracy: {acc_str}")
            # Show top results
            for r in research_memory_result.get("thesis_results", [])[:3]:
                icon = "YES" if r.get("correct") else "NO"
                lines.append(f"    [{icon}] {r['ticker']} {r.get('pct',0):+.1f}%"
                             f" — {r.get('thesis','')[:50]}")
        else:
            lines.append(f"  WRITE: {t_saved} theses saved (verified next week)")
        lines.append(f"  STORE: {kb_n} tickers tracked | +{kb_add} facts added this week")

    # ── Indicator Knowledge Base ──────────────────────────────────────────────
    if indicator_result and indicator_result.get("total_kb_size", 0) > 0:
        lines.append("")
        lines.append("<b>Indicator KB — Lifetime Learning:</b>")
        total   = indicator_result.get("total_kb_size", 0)
        added   = indicator_result.get("new_items_added", 0)
        novel   = indicator_result.get("novel_count", 0)
        sources = indicator_result.get("sources_active", 0)
        lines.append(
            f"  KB: {total} items | +{added} this week | {novel} novel discoveries"
            f" | {sources} sources active"
        )
        # Top new discoveries
        top_new = indicator_result.get("top_new_items", [])
        if top_new:
            for it in top_new[:3]:
                name    = it.get("name", "")[:40]
                src     = it.get("source", "")
                score   = it.get("quality_score", 0)
                insight = it.get("novel_insight", "") or ""
                if insight and insight != "N/A":
                    lines.append(f"  + {name} [{src} | q={score}]: {insight[:60]}")
                else:
                    cats = ", ".join(it.get("categories", [])[:2])
                    lines.append(f"  + {name} [{src} | q={score}] ({cats})")
        # Top categories
        top_cats = indicator_result.get("top_categories", [])
        if top_cats:
            cat_str = " / ".join(f"{c}:{n}" for c, n in top_cats[:4])
            lines.append(f"  Top: {cat_str}")

    # ── Edge Intelligence Summary ─────────────────────────────────────────────
    try:
        if edge_result and edge_result.get("top_signals"):
            lines.append("")
            lines.append("<b>Edge Signals:</b>")
            modules = {
                "semiconductor":     "Semi",
                "ai_capex":          "AI Capex",
                "hbm_memory":        "HBM",
                "defense":           "Defense",
                "nuclear":           "Nuclear",
                "data_center_power": "DC Power",
            }
            for key, label in modules.items():
                m = edge_result.get(key, {})
                sig   = m.get("nrgc_signal", "").replace("_", " ")
                boost = m.get("nrgc_boost", 0)
                if boost != 0:
                    lines.append(f"  {label}: {sig} ({boost:+d})")
        if alt_data_result:
            trending = [t for t, d in alt_data_result.get("google_trends", {}).items()
                        if d.get("trending")]
            squeezes = alt_data_result.get("short_interest", {}).get("squeeze_candidates", [])
            if trending or squeezes:
                parts = []
                if trending:
                    parts.append(f"Trending: {', '.join(trending[:3])}")
                if squeezes:
                    parts.append(f"Squeeze: {', '.join(s['ticker'] for s in squeezes[:3])}")
                lines.append("  " + " | ".join(parts))
        if narrative_result and narrative_result.get("top_accelerating"):
            top = narrative_result["top_accelerating"][:3]
            narr_str = " ".join(f"{t}({a:+.0f}%)" for t, a, _ in top)
            lines.append(f"  Narrative accel: {narr_str}")
        if narrative_result and narrative_result.get("super_narratives"):
            sn = list(narrative_result["super_narratives"].keys())[:2]
            if sn:
                lines.append(f"  Super-narrative: {', '.join(sn)}")
    except Exception:
        pass

    # ── Cost ──────────────────────────────────────────────────────────────────
    if token_cost_usd > 0:
        lines.append("")
        lines.append(f"LLM cost this run: ${token_cost_usd:.4f}")

    # ── Lifetime System Block ─────────────────────────────────────────────────
    if lifetime_stats:
        try:
            from lifetime_tracker import get_lifetime_telegram_block
            lifetime_block = get_lifetime_telegram_block(lifetime_stats)
            if lifetime_block:
                lines.append("")
                lines.append("─" * 30)
                lines.append(lifetime_block)
        except Exception:
            pass

    # Send (split if needed)
    msg = "\n".join(lines)
    if len(msg) > 4000:
        send_chunks(msg)
        return True
    else:
        return send(msg)


# ─── Alert Types ──────────────────────────────────────────────────────────────

def send_alert(ticker: str, alert_type: str, message: str, price: float = None):
    """Send a specific trading alert."""
    emoji = {
        "stop_loss":    "STOP HIT",
        "trailing":     "TRAIL HIT",
        "earnings":     "EARNINGS SOON",
        "stage_warn":   "STAGE WARNING",
        "phase_change": "NRGC PHASE CHANGE",
        "urgent":       "URGENT SIGNAL",
        "promotion":    "READY FOR REAL MONEY",
    }.get(alert_type, "ALERT")

    lines = [
        f"<b>{emoji}: {ticker}</b>",
        message,
    ]
    if price:
        lines.append(f"Current price: ${price:,.2f}")
    lines.append(datetime.now().strftime("%Y-%m-%d %H:%M"))
    send("\n".join(lines))


def send_nrgc_phase_change(ticker: str, old_phase: int, new_phase: int,
                            theme: str, action: str, confidence: float):
    """Alert when NRGC phase changes — especially Phase 2→3 transitions."""
    direction = "UP" if new_phase < old_phase else "DOWN"  # lower phase # = better
    phase_names = {1:"Neglect",2:"Accumulation",3:"Inflection",4:"Recognition",
                   5:"Consensus",6:"Euphoria",7:"Distribution"}

    if new_phase == 3:
        header = f"INFLECTION DETECTED: {ticker}"
    elif new_phase == 2:
        header = f"ACCUMULATION PHASE: {ticker}"
    elif new_phase >= 6:
        header = f"EXIT SIGNAL: {ticker}"
    else:
        header = f"NRGC PHASE {direction}: {ticker}"

    msg = (
        f"<b>{header}</b>\n"
        f"Phase: {phase_names.get(old_phase,'?')} -> <b>{phase_names.get(new_phase,'?')}</b>\n"
        f"Theme: {theme} | Confidence: {confidence:.0%}\n"
        f"Action: <b>{action}</b>\n"
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    send(msg)


def send_cost_summary():
    """Send current cumulative cost summary."""
    cost_file = BASE_DIR / "data" / "state" / "token_cost_log.json"
    if not cost_file.exists():
        send("No LLM costs recorded yet.")
        return
    try:
        log = json.loads(cost_file.read_text(encoding="utf-8"))
        total = log.get("total_usd", 0)
        calls = log.get("calls", [])
        recent = calls[-10:] if calls else []
        this_week_cost = sum(c["cost_usd"] for c in calls
                             if c.get("ts", "") >= datetime.now().strftime("%Y-%m-%d")[:8])
        msg = (
            f"<b>LLM Cost Summary</b>\n"
            f"Total all-time: ${total:.4f}\n"
            f"This run: ${sum(c['cost_usd'] for c in recent):.4f}\n"
            f"Calls logged: {len(calls)}\n"
            f"\nRecent calls:\n"
        )
        for c in recent[-5:]:
            msg += f"  {c['ts'][:10]} {c['type']:12} {c['in']}in/{c['out']}out ${c['cost_usd']:.5f}\n"
        send(msg)
    except Exception as e:
        send(f"Cost log error: {e}")


# ─── Startup Test ─────────────────────────────────────────────────────────────

def test_connection() -> bool:
    """Test Telegram connection and send a ping."""
    return send(
        f"AlphaAbsolute system online | {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"Bot connected. Automation active."
    )


if __name__ == "__main__":
    print("Testing Telegram connection...")
    ok = test_connection()
    print("Sent:", ok)
