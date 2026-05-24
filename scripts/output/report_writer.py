"""
AlphaAbsolute v2 -- A11 Report Writer + Telegram
==================================================
Generates daily brief + pushes signals to Telegram before market open.

Daily brief format:
  ALPHAABSOLUTE DAILY BRIEF [DATE]
  =================================
  MARKET: [Regime] | Cash Floor: [X%] | TD: SPY [signal] QQQ [signal]
  MACRO: [1-sentence state]
  THEMES HOT: [...] | WARM: [...]

  TOP SETUPS TODAY:
  1. $TICKER — Mode A | VCP | Buy: $95.50 | Stop: $87.86 | RR: 3.4x
     -> 1-line thesis

  PORTFOLIO: [N] positions | Deployed: X% | Cash: Y%
  [Positions with action signals]

  RISK FLAGS: [from A10]

Telegram format (mobile-optimized):
  🟢 SETUP: $COHR
  Mode A | VCP | Pivot $95.50
  Stop: $87.86 | Target: $116 | RR: 3.4x
  Size: 10% (full) | Grade: A
  ⚡ Photonics HOT + RS #88

Output:
  output/daily_brief_YYMMDD.md
  Telegram push via bot

Config in .env:
  TELEGRAM_BOT_TOKEN=...
  TELEGRAM_CHAT_ID=...

Cost: $0 (Python only, Telegram free)
"""

from __future__ import annotations
import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import date, datetime
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ── Env loader ────────────────────────────────────────────────────────────────

def _load_env() -> dict:
    env = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_ENV = _load_env()
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", _ENV.get("TELEGRAM_BOT_TOKEN", ""))
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID",   _ENV.get("TELEGRAM_CHAT_ID",   ""))


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _load_health() -> dict:
    return _load_json(ROOT / "data" / "regime" / "market_health.json")


def _load_macro() -> dict:
    return _load_json(ROOT / "data" / "regime" / "macro_state.json")


def _load_setups() -> list[dict]:
    data = _load_json(ROOT / "data" / "setups" / "setups_today.json")
    return data.get("setups", [])


def _load_signals() -> dict:
    return _load_json(ROOT / "data" / "portfolio" / "action_signals.json")


def _load_portfolio() -> dict:
    return _load_json(ROOT / "data" / "portfolio" / "portfolio_state.json")


def _load_paper_portfolio() -> dict:
    return _load_json(ROOT / "data" / "portfolio" / "paper_portfolio_state.json")


def _load_risk() -> dict:
    # v2 canonical path: data/risk/risk_report.json (written by risk_guardian.py)
    # data/risk_guardian/daily_report.json was a v1 path that no longer exists — removed
    return _load_json(ROOT / "data" / "risk" / "risk_report.json")


def _load_changes() -> dict:
    return _load_json(ROOT / "data" / "rs_universe" / "changes_today.json")


def _load_top30() -> list:
    data = _load_json(ROOT / "data" / "leadership" / "top30_watchlist.json")
    return data.get("watchlist", data.get("stocks", []))


def _load_rs_universe() -> dict:
    data = _load_json(ROOT / "data" / "rs_universe" / "latest.json")
    return data.get("universe", {})


def _load_theme_detail() -> dict:
    """
    Returns theme data + a synthetic `by_ticker` reverse mapping.

    theme_rs_latest.json structure:
      { "themes": { "AI_Related": { "members": { "AMD": {...}, ... }, "phase": "HOT", ... } } }

    We add:
      { "by_ticker": { "AMD": { "primary_theme": "AI_Related", "theme_name": "...",
                                "theme_heat": "HOT", "theme_vs_themes_pct": 92.0 } } }
    """
    data   = _load_json(ROOT / "data" / "rs_universe" / "theme_rs_latest.json")
    themes = data.get("themes", {})
    by_ticker: dict = {}
    for theme_id, td in themes.items():
        if not isinstance(td, dict):
            continue
        phase     = td.get("phase", "")
        heat      = phase if phase in ("HOT", "WARM", "WEAK", "EMERGING", "COOLING") else ""
        thm_name  = td.get("name", theme_id.replace("_", " "))
        thm_pct   = td.get("theme_vs_themes_pct") or td.get("theme_1m_pct")
        members   = td.get("members", {})
        if isinstance(members, dict):
            for ticker in members:
                if ticker not in by_ticker:
                    by_ticker[ticker] = {
                        "primary_theme":      theme_id,
                        "theme_name":         thm_name,
                        "theme_heat":         heat,
                        "theme_vs_themes_pct": thm_pct,
                    }
    data["by_ticker"] = by_ticker
    return data


def _load_themes() -> dict:
    data   = _load_json(ROOT / "data" / "rs_universe" / "theme_rs_latest.json")
    themes = data.get("themes", data)   # v2 nests under "themes" key; v1 stored at top level
    hot  = [v.get("name", t) for t, v in themes.items()
            if isinstance(v, dict) and v.get("phase", v.get("grade", "")) == "HOT"]
    warm = [v.get("name", t) for t, v in themes.items()
            if isinstance(v, dict) and v.get("phase", v.get("grade", "")) in ("WARM", "NEUTRAL")]
    return {"hot": hot, "warm": warm}


# ── Climbers / Droppers formatting ───────────────────────────────────────────

_CLIMBER_EVENT_LABEL = {
    "new_rs_leader":      "📈 New RS Leader (crossed 70th)",
    "fresh_breakout":     "🚀 Fresh Breakout (63D high)",
    "sector_rs_improver": "🔥 Sector RS Improving",
    "revenue_inflection": "💰 Revenue Inflection",
    "new_hot_theme":      "⭐ Entered HOT Theme",
}

_DROPPER_EVENT_LABEL = {
    "rs_fell_below_70":       "📉 RS Fell Below 70th",
    "rs_rank_drop_20":        "⬇️ RS Rank Drop >20pts",
    "ma50_break":             "❌ Broke MA50",
    "failed_breakout":        "💔 Failed Breakout",
    "sector_lost_momentum":   "🌧 Sector Lost Momentum",
}

_DROPPER_ACTION_EMOJI = {
    "TRIM_IMMEDIATELY": "🚨",
    "STOP_CHECK":       "⚠️",
    "TRIM":             "✂️",
    "REVIEW":           "📋",
    "REMOVE_WATCHLIST": "🗑️",
}


def _format_climbers_section(changes: dict) -> list[str]:
    """Format the CLIMBERS (Add to Focus List) section."""
    climbers = changes.get("climbers", [])
    if not climbers:
        return []

    lines = ["## 📈 CLIMBERS — Add to Focus List"]
    for c in climbers[:12]:
        ticker = c.get("ticker", "?")
        event  = c.get("event", "")
        label  = _CLIMBER_EVENT_LABEL.get(event, event)
        details = []
        if c.get("rs_3m_today") is not None:
            details.append(f"RS3M:{c['rs_3m_today']:.0f}")
        if c.get("rs_3m_prev") is not None and c.get("rs_3m_today") is not None:
            delta = c["rs_3m_today"] - c["rs_3m_prev"]
            if abs(delta) > 1:
                details.append(f"Δ{delta:+.0f}")
        if c.get("theme"):
            details.append(c["theme"])
        if c.get("note"):
            details.append(c["note"])
        detail_str = " | ".join(details)
        lines.append(f"  ${ticker:<6} {label}" + (f"  [{detail_str}]" if detail_str else ""))

    # Revenue inflections in sub-section
    rev_inflections = changes.get("revenue_inflections", [])
    if rev_inflections:
        lines.append("")
        lines.append("  *Revenue Inflections:*")
        for r in rev_inflections[:5]:
            ticker  = r.get("ticker", "?")
            rev_now = r.get("rev_q0")
            rev_old = r.get("rev_q1")
            rev_str = f"Rev: {rev_old:.0f}%→{rev_now:.0f}%" if rev_now and rev_old else ""
            lines.append(f"  ${ticker:<6} 💰 First >25% growth quarter  {rev_str}")
    lines.append("")
    return lines


def _format_droppers_section(changes: dict) -> list[str]:
    """Format the DROPPERS (Trim / Remove) section."""
    droppers = changes.get("droppers", [])
    if not droppers:
        return []

    lines = ["## 📉 DROPPERS — Trim / Remove from Focus List"]

    # Group by action priority
    immediate = [d for d in droppers if d.get("action") == "TRIM_IMMEDIATELY"]
    stop_check = [d for d in droppers if d.get("action") == "STOP_CHECK"]
    others     = [d for d in droppers if d.get("action") not in ("TRIM_IMMEDIATELY", "STOP_CHECK")]

    for group, label in [
        (immediate,  "🚨 IMMEDIATE"),
        (stop_check, "⚠️ STOP CHECK"),
        (others,     "📋 REVIEW"),
    ]:
        if not group:
            continue
        lines.append(f"  *{label}:*")
        for d in group[:8]:
            ticker  = d.get("ticker", "?")
            event   = d.get("event", "")
            elabel  = _DROPPER_EVENT_LABEL.get(event, event)
            action  = d.get("action", "REVIEW")
            aemoji  = _DROPPER_ACTION_EMOJI.get(action, "📋")
            details = []
            if d.get("rs_3m_today") is not None:
                details.append(f"RS3M:{d['rs_3m_today']:.0f}")
            if d.get("rs_3m_prev") is not None and d.get("rs_3m_today") is not None:
                delta = d["rs_3m_today"] - d["rs_3m_prev"]
                if abs(delta) > 1:
                    details.append(f"Δ{delta:+.0f}")
            if d.get("note"):
                details.append(d["note"])
            detail_str = " | ".join(details)
            lines.append(
                f"  {aemoji} ${ticker:<6} {elabel}" + (f"  [{detail_str}]" if detail_str else "")
            )

    lines.append("")
    return lines


# ── Regime display ────────────────────────────────────────────────────────────

REGIME_EMOJI = {
    "Markup":       "🟢",
    "Sideways":     "🟡",
    "Distribution": "🟠",
    "Markdown":     "🔴",
}

SETUP_EMOJI = {"A": "⭐", "B": "✅", "C": "📋"}


# ── Format setup line ─────────────────────────────────────────────────────────

def format_setup_line(setup: dict, index: int) -> str:
    """One-line setup for the daily brief."""
    ticker = setup.get("ticker", "?")
    mode   = setup.get("mode", "A")
    stype  = setup.get("setup_type", "?")
    pivot  = setup.get("pivot", 0)
    stop   = setup.get("stop", 0)
    rr     = setup.get("rr_ratio", 0)
    size   = setup.get("recommended_size_pct", 0)
    grade  = setup.get("setup_grade", "B")
    theme  = setup.get("theme", "")
    td_sig = setup.get("td_signal", "Neutral")

    td_note = f" [TD:{td_sig}]" if td_sig != "Neutral" else ""
    theme_note = f" | {theme}" if theme else ""

    return (
        f"{index}. ${ticker} — Mode {mode} | {stype} | "
        f"Buy: ${pivot:.2f} | Stop: ${stop:.2f} | RR: {rr:.1f}x | "
        f"Size: {size:.0f}%{td_note}{theme_note} | Grade: {grade}"
    )


def format_setup_thesis(setup: dict) -> str:
    """One-line thesis for the setup."""
    entry_note = setup.get("entry_note", "")
    theme      = setup.get("theme", "")
    rs_pct     = setup.get("rs_pct_3m")
    mode       = setup.get("mode", "A")

    parts = []
    if entry_note:
        parts.append(entry_note[:80])
    if theme:
        parts.append(f"Theme: {theme}")
    if rs_pct and mode == "A":
        parts.append(f"RS: {rs_pct:.0f}th")

    return " | ".join(parts) if parts else "Setup confirmed by price action"


# ── Build markdown brief ──────────────────────────────────────────────────────

def build_brief(today: str) -> str:
    health    = _load_health()
    macro     = _load_macro()
    setups    = _load_setups()
    signals   = _load_signals()
    portfolio = _load_portfolio()
    paper     = _load_paper_portfolio()
    risk      = _load_risk()
    themes    = _load_themes()
    changes   = _load_changes()

    regime    = health.get("regime", "Unknown")
    cash_floor = int(health.get("cash_floor", 0) * 100)
    max_dep    = int(health.get("max_deployed", 1) * 100)
    spy_td    = health.get("spy_td_signal", "Neutral")
    qqq_td    = health.get("qqq_td_signal", "Neutral")
    dist_days = health.get("distribution_days", 0)
    liq_gate  = health.get("liquidity_gate", "")

    macro_note  = macro.get("macro_note", "No macro data available.")
    macro_mod   = macro.get("macro_modifier", 1.0)
    mod_note    = f" [size ×{macro_mod:.2f}]" if macro_mod != 1.0 else ""

    emoji = REGIME_EMOJI.get(regime, "⚪")

    lines = []
    lines.append(f"# ALPHAABSOLUTE DAILY BRIEF — {today}")
    lines.append("=" * 50)
    lines.append("")

    # Market header
    lines.append(f"## {emoji} MARKET: {regime}")
    lines.append(
        f"Cash Floor: **{cash_floor}%** | Max Deployed: **{max_dep}%** | "
        f"Dist Days: {dist_days}"
    )
    lines.append(f"TD: SPY={spy_td} | QQQ={qqq_td}")
    if liq_gate and liq_gate != "SUPPORTIVE":
        lines.append(f"⚠ Liquidity Gate: **{liq_gate}**{mod_note}")
    lines.append("")

    # Macro
    lines.append("## MACRO")
    lines.append(macro_note)
    if macro.get("rate_environment"):
        rate_env = macro.get("rate_environment")
        yield_10y = macro.get("yield_10y")
        hy = macro.get("hy_spread_pct")
        details = []
        if yield_10y: details.append(f"10Y={yield_10y:.2f}%")
        if hy:        details.append(f"HY={hy:.2f}%")
        if details:   lines.append(f"Rate: {rate_env} | {' | '.join(details)}")
    lines.append("")

    # Themes
    hot_str  = ", ".join(themes["hot"][:5])  if themes["hot"]  else "—"
    warm_str = ", ".join(themes["warm"][:5]) if themes["warm"] else "—"
    lines.append("## THEMES")
    lines.append(f"🔥 HOT:  {hot_str}")
    lines.append(f"🌤 WARM: {warm_str}")
    lines.append("")

    # Top Setups
    lines.append("## TOP SETUPS TODAY")
    if not setups:
        lines.append("No Grade A/B setups today — wait for better conditions.")
    else:
        for i, setup in enumerate(setups[:8], 1):
            lines.append(format_setup_line(setup, i))
            lines.append(f"   → {format_setup_thesis(setup)}")
            lines.append("")

    # Portfolio
    positions  = portfolio.get("positions", {})
    deployed   = int(portfolio.get("deployed_pct", 0) * 100)
    cash_pct   = int(portfolio.get("cash_pct", 1) * 100)
    n_pos      = len(positions)

    paper_val  = paper.get("portfolio_value", 100_000)
    paper_cash = int(paper.get("cash_pct", 1) * 100)
    paper_pos  = paper.get("total_positions", 0)

    lines.append("## PORTFOLIO")
    lines.append(f"**Real:** {n_pos} positions | Deployed: {deployed}% | Cash: {cash_pct}%")
    lines.append(f"**Paper:** {paper_pos} positions | Value: ${paper_val:,.0f} | Cash: {paper_cash}%")

    # Expectancy / edge metrics (Minervini Lesson 7)
    exp = paper.get("expectancy", {})
    n_trades = exp.get("total_closed_trades", 0)
    if n_trades >= 3:   # need at least 3 trades for meaningful stats
        win_rate   = exp.get("win_rate", 0)
        avg_win    = exp.get("avg_winner_pct", 0)
        avg_loss   = exp.get("avg_loser_pct", 0)
        edge       = exp.get("expectancy_pct", 0)
        pf         = exp.get("profit_factor")
        pf_str     = f"{pf:.2f}" if pf is not None else "N/A"
        lines.append(
            f"**Edge (N={n_trades}):** Win {win_rate*100:.0f}% | "
            f"Avg +{avg_win:.1f}% / Avg {avg_loss:.1f}% | "
            f"Expectancy {edge:+.1f}% | PF {pf_str}"
        )
    lines.append("")

    # Action signals
    sig_list = signals.get("signals", [])
    imm  = [s for s in sig_list if s.get("priority") == "IMMEDIATE"]
    today_sigs = [s for s in sig_list if s.get("priority") == "TODAY"]
    rev  = [s for s in sig_list if s.get("priority") == "REVIEW"]

    if imm or today_sigs:
        lines.append("### Action Signals")
        for s in imm:
            lines.append(f"🚨 **IMMEDIATE** ${s['ticker']}: {s['action']} — {s['reason']}")
        for s in today_sigs:
            lines.append(f"⚠️ **TODAY** ${s['ticker']}: {s['action']} — {s['reason']}")
        for s in rev:
            lines.append(f"📋 **REVIEW** ${s['ticker']}: {s['action']}")
        lines.append("")

    # Climbers & Droppers (from A03c RS Change Detector)
    climbers_section = _format_climbers_section(changes)
    droppers_section = _format_droppers_section(changes)
    if climbers_section:
        lines.extend(climbers_section)
    if droppers_section:
        lines.extend(droppers_section)
    if not climbers_section and not droppers_section and changes:
        lines.append("## FOCUS LIST CHANGES")
        lines.append("No significant RS movement vs yesterday.")
        lines.append("")

    # Risk flags
    risk_flags = risk.get("flags", [])
    devil = risk.get("devil_advocate", "")
    if risk_flags or devil:
        lines.append("## RISK FLAGS")
        for flag in risk_flags[:5]:
            lines.append(f"⛔ {flag}")
        if devil:
            lines.append(f"🔍 Devil's advocate: *{devil}*")
        lines.append("")

    # Early warnings
    warnings = health.get("early_warnings", [])
    if warnings:
        lines.append("## LEADING WARNINGS")
        for w in warnings:
            lines.append(f"⚡ {w}")
        lines.append("")

    # Challenger
    challenge = health.get("regime_challenge", {})
    if challenge.get("active"):
        lines.append("## REGIME CHALLENGE")
        lines.append(f"> {challenge.get('message', '')}")
        lines.append("")

    lines.append(f"---")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} | AlphaAbsolute v2*")

    return "\n".join(lines)


# ── Telegram helpers ──────────────────────────────────────────────────────────

THEME_TAG = {
    "AI_Related":       "🤖AI",
    "Memory_HBM":       "💾Mem",
    "Space":            "🚀Space",
    "Quantum":          "⚛️QC",
    "Photonics":        "💡Photo",
    "DefenseTech":      "🛡️Def",
    "DataCenter":       "🏢DC",
    "Nuclear_SMR":      "☢️Nuke",
    "NeoCloud":         "☁️Cloud",
    "AI_Infra":         "⚡AIInfra",
    "DataCenter_Infra": "🔧DCInfra",
    "Drone_UAV":        "🚁Drone",
    "Robotics":         "🤖Robot",
    "Connectivity":     "📡Conn",
}

def _rs_str(entry: dict) -> str:
    """Compact RS display: 1M/3M/6M"""
    r1 = entry.get("rs_1m_pct") or entry.get("rs_pct_1m")
    r3 = entry.get("rs_3m_pct") or entry.get("rs_pct_3m")
    r6 = entry.get("rs_6m_pct") or entry.get("rs_pct_6m")
    parts = []
    if r1 is not None: parts.append(f"{r1:.0f}")
    if r3 is not None: parts.append(f"{r3:.0f}")
    if r6 is not None: parts.append(f"{r6:.0f}")
    return "/".join(parts) if parts else "—"

def _pct(v) -> str:
    return f"{v:.0f}%" if v is not None else "—"

def _dollar(v) -> str:
    return f"${v:.2f}" if v else "—"


# ── Build Telegram messages — 7-Step Flow ─────────────────────────────────────

def _setup_card(s: dict, by_ticker: dict, rs_universe: dict, grade: str) -> str:
    """
    4-line setup card per Fund Manager spec.
    Line 1: Ticker | Grade | Setup type | base week if known
    Line 2: Buy / Stop / Target (T2 only) | RR
    Line 3: RS 3M | Size
    Line 4: entry_note (pattern observation, 1 line)
    No EPS/Rev, no theme, no "invalid if" — already passed gates.
    """
    ticker  = s.get("ticker", "?")
    stype   = s.get("setup_type", "?")
    pivot   = s.get("pivot", 0)
    stop    = s.get("stop", 0)
    t2      = s.get("target_2") or s.get("target_1", 0)   # T2 preferred
    rr      = s.get("rr_ratio", 0)
    size    = s.get("recommended_size_pct", 10)
    why     = (s.get("entry_note", "") or "")[:65]

    # RS — 3M only (most relevant for entry timing)
    rs_e = rs_universe.get(ticker, s)
    r3   = rs_e.get("rs_3m_pct") or rs_e.get("rs_pct_3m") or s.get("rs_pct_3m")
    rs_s = f"RS {int(r3)}" if r3 is not None else ""

    # Base week context (Minervini timing — weeks since base started)
    base_wk = s.get("base_week") or s.get("base_weeks")
    base_s  = f" w{base_wk}" if base_wk else ""

    grade_e = "⭐" if grade == "A" else "✅"

    return "\n".join([
        f"{grade_e} *{ticker}* | {stype}{base_s}",
        f"Buy *{_dollar(pivot)}* | Stop {_dollar(stop)} | Target {_dollar(t2)} | RR {rr:.1f}x",
        f"{rs_s} | Size {size:.0f}%",
        why,
    ])


def build_telegram_messages(health: dict, setups: list, signals: dict,
                              portfolio: dict, paper: dict,
                              changes: Optional[dict] = None) -> list[str]:
    """
    AlphaAbsolute v2 Telegram format — practitioner spec (Fund Manager review 2026-05-24).

    Order (per O'Neil/Minervini priority):
    MSG 1: Regime — one screen, no scroll, single line verdict
    MSG 2: Portfolio actions FIRST — stops triggered, RS drops on held names
    MSG 3-7: Grade A setups only, 4 lines each (max 5)
    MSG 8: Grade B exception — only if Markup + zero Grade A (1 line per)
    [NO themes heatmap, NO EPS/Rev on cards, NO watchlist new-adders,
     NO "invalid if" boilerplate, NO Grade B when Grade A exists]
    """
    # ── Load data ─────────────────────────────────────────────────────────────
    rs_universe = _load_rs_universe()
    macro       = _load_macro()
    changes     = changes or _load_changes()

    regime     = health.get("regime", "Unknown")
    cash_floor = int(health.get("cash_floor", 0) * 100)
    max_dep    = int(health.get("max_deployed", 1) * 100)
    spy_td     = health.get("spy_td_signal", "Neutral")
    qqq_td     = health.get("qqq_td_signal", "Neutral")
    dist_days  = health.get("distribution_days", 0)
    pct_50dma  = health.get("pct_above_50dma")
    emoji      = REGIME_EMOJI.get(regime, "⚪")

    macro_note = macro.get("macro_note", "")
    macro_mod  = macro.get("macro_modifier", 1.0)
    hy_bps     = macro.get("hy_spread_bps") or macro.get("hy_spread_pct")
    yield_10y  = macro.get("yield_10y")

    messages = []
    today_str = date.today().strftime("%d %b %Y")

    # ── MSG 1: Regime — one screen, no scroll ─────────────────────────────────
    if regime == "Markup":
        verdict = "RISK-ON ✅  Full size, both modes"
    elif regime == "Sideways":
        verdict = "NEUTRAL 🟡  Mode A only, selective"
    elif regime == "Distribution":
        verdict = "RISK-OFF ⚠️  Mode A only, reduced size"
    else:
        verdict = "CASH 🔴  No new entries"

    td_parts = []
    if spy_td != "Neutral": td_parts.append(f"SPY {spy_td}")
    if qqq_td != "Neutral": td_parts.append(f"QQQ {qqq_td}")
    td_str = "  ·  ".join(td_parts) if td_parts else "TD Neutral"

    breadth_icon = "✅" if (pct_50dma or 0) > 60 else ("⚠️" if (pct_50dma or 0) > 45 else "🔴")
    breadth_str  = f"{pct_50dma:.0f}% above 50DMA {breadth_icon}" if pct_50dma else "breadth N/A"

    # Macro: only if something changed (yield/spread)
    macro_str = ""
    if macro_note:
        macro_str = f"\n{macro_note[:80]}"
    elif yield_10y:
        macro_str = f"\n10Y {yield_10y:.2f}%"
        if hy_bps: macro_str += f"  |  HY {hy_bps:.2f}%"
    macro_mod_str = f"\n⚠️ Macro ×{macro_mod:.2f} — cut all sizes" if macro_mod < 1.0 else ""

    messages.append(
        f"{emoji} *AlphaAbsolute  |  {today_str}*\n"
        f"{verdict}\n"
        f"Cash floor *{cash_floor}%*  |  Deploy max *{max_dep}%*\n"
        f"Breadth: {breadth_str}  |  Dist days: {dist_days}\n"
        f"{td_str}"
        f"{macro_str}"
        f"{macro_mod_str}"
    )

    # ── MSG 2: Portfolio FIRST — stops, RS drops on held names ────────────────
    real_pos   = portfolio.get("positions", {})
    paper_pos  = paper.get("positions", {})
    real_cash  = portfolio.get("cash_pct", 100)
    paper_val  = paper.get("total_value", 100000)
    paper_cash = paper.get("cash_pct", 100)
    n_real     = len(real_pos)

    imm = [s for s in signals.get("signals", []) if s.get("priority") == "IMMEDIATE"]
    today_sigs = [s for s in signals.get("signals", []) if s.get("priority") == "TODAY"]

    # RS drops on HELD positions only (not new watchlist entries)
    held_tickers = set(real_pos.keys()) | set(paper_pos.keys())
    droppers     = (changes or {}).get("droppers", [])
    held_drops   = [d for d in droppers if d.get("ticker") in held_tickers]

    port_lines = [f"💼 *Portfolio*"]
    if n_real == 0:
        deployed_paper = 100 - paper_cash
        port_lines.append(
            f"Real: Cash only\n"
            f"Paper: {len(paper_pos)} pos  |  ${paper_val:,.0f}  |  Deployed {deployed_paper:.0f}%"
        )
    else:
        deployed = 100 - real_cash
        port_lines.append(f"Real: {n_real} pos  |  Deployed {deployed:.0f}%  |  Cash {real_cash:.0f}%")
        for ticker, pos in list(real_pos.items())[:5]:
            pnl   = pos.get("unrealized_pct", 0) or 0
            pnl_s = f"+{pnl:.1f}%" if pnl >= 0 else f"{pnl:.1f}%"
            stp   = pos.get("stop_price", 0)
            port_lines.append(f"  *{ticker}* {pnl_s}  stop {_dollar(stp)}")

    if imm:
        port_lines.append("")
        port_lines.append("🚨 *IMMEDIATE:*")
        for s in imm[:4]:
            port_lines.append(f"  🚨 *{s['ticker']}*: {s['action']} — {s.get('reason','')[:50]}")
    if today_sigs:
        port_lines.append("⚠️ *Today:*")
        for s in today_sigs[:3]:
            port_lines.append(f"  • {s['ticker']}: {s.get('reason','')[:50]}")
    if held_drops:
        port_lines.append("📉 *RS drops on held positions:*")
        for d in held_drops[:3]:
            port_lines.append(f"  • *{d['ticker']}*: {d.get('event','').replace('_',' ')}")

    messages.append("\n".join(port_lines))

    # ── MSG 3-7: Grade A setups — one card each (max 5) ──────────────────────
    context_types = {"EMA", "VPS", "FIB"}
    grade_a = [s for s in setups
               if s.get("setup_grade") == "A"
               and s.get("setup_type", "") not in context_types][:5]
    grade_b = [s for s in setups
               if s.get("setup_grade") == "B"
               and s.get("setup_type", "") not in context_types]

    if not grade_a:
        # No Grade A — show exception message + one Grade B only in Markup
        if regime == "Markup" and grade_b:
            best_b = grade_b[0]
            ticker = best_b.get("ticker", "?")
            pivot  = best_b.get("pivot", 0)
            stype  = best_b.get("setup_type", "?")
            size   = best_b.get("recommended_size_pct", 5)
            messages.append(
                f"🎯 *No Grade A today*\n"
                f"Best B: *{ticker}* {stype} | Buy {_dollar(pivot)} | *{size:.0f}% max, no pyramid*"
            )
        else:
            messages.append("🎯 *No actionable setups today — watchlist mode*")
    else:
        for s in grade_a:
            messages.append(_setup_card(s, {}, rs_universe, "A"))

    return messages


# ── Send Telegram ─────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("  [WARN] Telegram not configured (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env)")
        return False

    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "Markdown",
        }).encode("utf-8")
        req  = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            return result.get("ok", False)
    except Exception as e:
        print(f"  [ERROR] Telegram send failed: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    today     = date.today().isoformat()
    today_fmt = date.today().strftime("%y%m%d")

    print(f"\n{'='*55}")
    print(f"  A11 Report Writer  [{today}]")
    print(f"{'='*55}")

    # Build markdown brief
    print("  Building daily brief...")
    brief = build_brief(today)

    # Save to output/
    brief_file = OUTPUT_DIR / f"daily_brief_{today_fmt}.md"
    brief_file.write_text(brief, encoding="utf-8")
    print(f"  -> Written: {brief_file}")

    # Build Telegram messages
    health    = _load_health()
    setups    = _load_setups()
    signals   = _load_signals()
    portfolio = _load_portfolio()
    paper     = _load_paper_portfolio()
    changes   = _load_changes()

    messages = build_telegram_messages(health, setups, signals, portfolio, paper, changes)

    # Send to Telegram
    sent = 0
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        print(f"  Sending {len(messages)} Telegram messages...")
        for msg in messages:
            if send_telegram(msg):
                sent += 1
            # Small delay between messages
            import time; time.sleep(0.5)
        print(f"  Sent {sent}/{len(messages)} messages to Telegram")
    else:
        print("  [SKIP] Telegram not configured")
        # Print preview to console instead
        print("\n  ── TELEGRAM PREVIEW ──────────────────────────────────")
        for i, msg in enumerate(messages[:3], 1):
            print(f"  Message {i}:")
            for line in msg.split("\n"):
                print(f"    {line}")
            print()

    return {
        "date":         today,
        "brief_file":   str(brief_file),
        "telegram_sent": sent,
        "messages":     len(messages),
        "setups_in_brief": len(setups),
        "status":       "ok",
    }


if __name__ == "__main__":
    run()
