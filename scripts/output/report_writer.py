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
    # Try v2 path first (risk_guardian/daily_report.json), fall back to v1 path
    v2 = ROOT / "data" / "risk_guardian" / "daily_report.json"
    v1 = ROOT / "data" / "risk" / "risk_report.json"
    return _load_json(v2) if v2.exists() else _load_json(v1)


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

def build_telegram_messages(health: dict, setups: list, signals: dict,
                              portfolio: dict, paper: dict,
                              changes: Optional[dict] = None) -> list[str]:
    """
    7-Step Daily Brief for Telegram.
    Sends as multiple messages (one per step) for easy reading on mobile.

    STEP 1: Market Health
    STEP 2: Exposure Recommendation
    STEP 3+4: Top 30 Focus List with Theme Tags
    STEP 5: Top 5 Actionable Today (detailed entry cards)
    STEP 6: New Adders
    STEP 7: Droppers
    """
    messages = []
    today = date.today().strftime("%d %b %Y")

    # Load extra data
    top30       = _load_top30()
    rs_universe = _load_rs_universe()
    theme_data  = _load_theme_detail()
    by_ticker   = theme_data.get("by_ticker", {})
    macro       = _load_macro()

    regime     = health.get("regime", "Unknown")
    cash_floor = int(health.get("cash_floor", 0) * 100)
    max_dep    = int(health.get("max_deployed", 1) * 100)
    qqq_td     = health.get("qqq_td_signal", "Neutral")
    dist_days  = health.get("distribution_days", 0)
    pct_50dma  = health.get("pct_above_50dma")
    pct_200dma = health.get("pct_above_200dma")
    warnings   = health.get("early_warnings", [])
    emoji      = REGIME_EMOJI.get(regime, "⚪")

    macro_note    = macro.get("macro_note", "")
    rate_env      = macro.get("rate_environment", "")
    hy_spread     = macro.get("hy_spread_bps") or macro.get("hy_spread_pct")
    yield_10y     = macro.get("yield_10y")
    macro_mod     = macro.get("macro_modifier", 1.0)

    # ── MSG 1: STEP 1 — Market Health ────────────────────────────────────────
    def _health_verdict(regime, pct_50, dist, td) -> str:
        if regime == "Markup":
            return "Risk-On ✅" if (pct_50 or 0) > 60 else "Risk-On (breadth weak) ⚠️"
        if regime == "Distribution":
            return "Risk-Off ⚠️ — Reduce exposure"
        if regime == "Sideways":
            return "Neutral 🟡 — Selective only"
        return "Risk-Off 🔴 — Mostly Cash"

    def _leaders_verdict(health) -> str:
        leaders_ok = health.get("leaders_ok", True)
        return "Acting well ✅" if leaders_ok else "Showing cracks ⚠️"

    def _breakout_verdict(regime, dist_days) -> str:
        if regime == "Markup" and dist_days < 3:
            return "Working ✅"
        if dist_days >= 4:
            return "Failing — wait ❌"
        return "Mixed ⚠️"

    def _overextended_verdict(health) -> str:
        qqq_ext = health.get("qqq_pct_from_50dma")
        if qqq_ext is None:
            return "—"
        if qqq_ext > 8:
            return f"Extended +{qqq_ext:.1f}% from 50DMA ⚠️"
        return f"+{qqq_ext:.1f}% from 50DMA ✅"

    # Exposure output label
    if regime == "Markup" and (pct_50dma or 0) > 60 and dist_days < 4:
        stance = "🟢 OFFENSIVE"
    elif regime == "Markup" or regime == "Sideways":
        stance = "🟡 NEUTRAL"
    elif regime == "Distribution":
        stance = "🟠 DEFENSIVE"
    else:
        stance = "🔴 MOSTLY CASH"

    step1 = [
        f"{emoji} *STEP 1: MARKET HEALTH* — {today}",
        f"{'━'*32}",
        f"• Risk:        {_health_verdict(regime, pct_50dma, dist_days, qqq_td)}",
        f"• Nasdaq:      *{regime}* phase",
        f"• Breadth:     {'Good ✅' if (pct_50dma or 0) > 60 else 'Weak ⚠️'}"
          + (f" ({pct_50dma:.0f}% above 50DMA)" if pct_50dma else ""),
        f"• Leaders:     {_leaders_verdict(health)}",
        f"• Breakouts:   {_breakout_verdict(regime, dist_days)}",
        f"• TD:          QQQ={qqq_td}" + (" ⚠️" if "Sell" in qqq_td else ""),
        f"• Extension:   {_overextended_verdict(health)}",
        f"• Macro:       {macro_note[:60]}" if macro_note else "• Macro:  —",
        "",
        f"➡️  *{stance}*",
    ]
    if warnings:
        step1.append("")
        step1.append("⚡ *Leading Warnings:*")
        for w in warnings[:3]:
            step1.append(f"  • {w}")
    messages.append("\n".join(step1))

    # ── MSG 2: STEP 2 — Exposure Recommendation ───────────────────────────────
    equity_pct = max_dep
    cash_pct   = 100 - equity_pct
    size_mod   = macro_mod

    if regime == "Markup":
        beta_rec   = "Increase beta — lead with high-RS names"
        stop_rec   = "Normal stops (-8% Leader, -10% Big Shot)"
        cap_rec    = "Large + Mid cap leaders"
        bigshot    = "Yes — up to 30% sleeve ✅"
    elif regime == "Sideways":
        beta_rec   = "Neutral — avoid speculative names"
        stop_rec   = "Tighten to -6% on Leaders"
        cap_rec    = "Large cap only"
        bigshot    = "No — wait for trend ❌"
    elif regime == "Distribution":
        beta_rec   = "Reduce beta — trim extended names"
        stop_rec   = "Tighten to -5%, no new adds"
        cap_rec    = "Large cap defensive only"
        bigshot    = "No — close sleeve ❌"
    else:
        beta_rec   = "Minimal — cash is a position"
        stop_rec   = "Hard stops, no exceptions"
        cap_rec    = "None — 100% cash preferred"
        bigshot    = "No ❌"

    step2 = [
        "📊 *STEP 2: EXPOSURE RECOMMENDATION*",
        f"{'━'*32}",
        f"• Equity:      *{equity_pct}%* max deployed",
        f"• Cash:        *{cash_pct}%* minimum",
        f"• Beta:        {beta_rec}",
        f"• Stops:       {stop_rec}",
        f"• Cap size:    {cap_rec}",
        f"• Big Shot:    {bigshot}",
    ]
    if size_mod != 1.0:
        step2.append(f"• Macro mod:   ×{size_mod:.2f} (reduce all sizes)")
    messages.append("\n".join(step2))

    # ── MSG 3: STEP 3+4 — Top 30 Focus List with Theme Tags ─────────────────
    # Pull from top30_watchlist or rs_universe top by composite
    focus_list = top30[:30] if top30 else []

    # If no top30 file, fall back to rs_universe top 30 by composite
    if not focus_list and rs_universe:
        sorted_rs = sorted(rs_universe.items(),
                           key=lambda x: x[1].get("rs_composite_pct", 0) or 0,
                           reverse=True)
        focus_list = [{"ticker": t, **d} for t, d in sorted_rs[:30]]

    lines30 = [
        "📋 *STEP 3+4: TOP 30 FOCUS LIST*",
        f"{'━'*32}",
        "_RS = 1M/3M/6M percentile vs market_",
        "",
    ]

    for i, stock in enumerate(focus_list[:30], 1):
        ticker = stock.get("ticker", "?")
        rs_e   = rs_universe.get(ticker, stock)
        rs_s   = _rs_str(rs_e)
        thm    = by_ticker.get(ticker, {})
        tag    = THEME_TAG.get(thm.get("primary_theme", ""), "")
        heat   = thm.get("theme_heat", "")
        heat_s = "🔥" if heat == "HOT" else ("🌤" if heat == "WARM" else "")
        # RS momentum
        r1 = rs_e.get("rs_1m_pct") or rs_e.get("rs_pct_1m")
        r3 = rs_e.get("rs_3m_pct") or rs_e.get("rs_pct_3m")
        mom = ""
        if r1 is not None and r3 is not None:
            d = r1 - r3
            mom = f" ↑" if d > 5 else (" ↓" if d < -5 else "")
        lines30.append(f"{i:2}. `${ticker:<6}` RS:{rs_s}{mom}  {heat_s}{tag}")

    messages.append("\n".join(lines30))

    # ── MSG 4: STEP 5 — Top 5 Actionable Today ───────────────────────────────
    # Use setups_today.json Grade A first, then B
    actionable = sorted(setups, key=lambda x: (
        0 if x.get("setup_grade") == "A" else 1,
        -(x.get("rr_ratio") or 0)
    ))[:5]

    lines5 = [
        "🎯 *STEP 5: TOP 5 ACTIONABLE TODAY*",
        f"{'━'*32}",
        "",
    ]

    if not actionable:
        lines5.append("_No Grade A/B setups today — watchlist mode_")
        lines5.append("_Wait for better entry conditions_")
    else:
        for i, s in enumerate(actionable, 1):
            ticker  = s.get("ticker", "?")
            stype   = s.get("setup_type", "?")
            grade   = s.get("setup_grade", "B")
            pivot   = s.get("pivot", 0)
            stop    = s.get("stop", 0)
            t1      = s.get("target_1", 0)
            rr      = s.get("rr_ratio", 0)
            risk    = round(abs(pivot - stop) / pivot * 100, 1) if pivot else 0
            rs_e    = rs_universe.get(ticker, s)
            rs_s    = _rs_str(rs_e)
            r1      = rs_e.get("rs_1m_pct") or s.get("rs_pct_1m")
            r3      = rs_e.get("rs_3m_pct") or s.get("rs_pct_3m")
            rs_chg  = f" Δ{r1-r3:+.0f}" if (r1 and r3) else ""
            thm     = by_ticker.get(ticker, {})
            thm_nm  = thm.get("theme_name", "")
            thm_pct = thm.get("theme_vs_themes_pct")
            thm_s   = f"{thm_nm} {thm_pct:.0f}th" if (thm_nm and thm_pct) else thm_nm
            rev_g   = s.get("rev_yoy") or s.get("rev_growth")
            eps_g   = s.get("eps_yoy") or s.get("eps_growth")
            fund_s  = ""
            if rev_g: fund_s += f"Rev:{rev_g:+.0f}%"
            if eps_g: fund_s += f" EPS:{eps_g:+.0f}%"
            why     = s.get("entry_note", "")[:60]
            invalid = s.get("invalid_if", f"Close below {_dollar(stop)}")
            grade_e = SETUP_EMOJI.get(grade, "✅")

            card = [
                f"{grade_e} *#{i} ${ticker}* — {stype} (Grade {grade})",
                f"  Entry: {_dollar(pivot)} | Stop: {_dollar(stop)} | Target: {_dollar(t1)}",
                f"  RR: {rr:.1f}x | Risk: {risk:.1f}%",
                f"  RS: {rs_s}{rs_chg}",
                f"  Sector: {thm_s}" if thm_s else "",
                f"  Fund: {fund_s}" if fund_s else "",
                f"  📌 Why now: {why}" if why else "",
                f"  ⛔ Invalid if: {invalid}",
                "",
            ]
            lines5.extend([l for l in card if l != ""])

    messages.append("\n".join(lines5))

    # ── MSG 5: STEP 6+7 — New Adders + Droppers ──────────────────────────────
    climbers = (changes or {}).get("climbers", [])
    droppers = (changes or {}).get("droppers", [])
    rev_infl = (changes or {}).get("revenue_inflections", [])

    lines67 = [
        "📈📉 *STEP 6+7: ADDERS & DROPPERS*",
        f"{'━'*32}",
    ]

    # Step 6 — New Adders
    lines67.append("")
    lines67.append("*📈 STEP 6 — New Adders:*")
    adder_label = {
        "new_rs_leader":      "New RS Leader (>70th)",
        "fresh_breakout":     "Fresh Breakout (63D high)",
        "sector_rs_improver": "Sector RS Improving",
        "revenue_inflection": "Revenue Inflection",
        "new_hot_theme":      "Entered HOT Theme",
    }
    added_any = False
    for c in climbers[:8]:
        ticker = c.get("ticker", "?")
        event  = c.get("event", "")
        label  = adder_label.get(event, event)
        rs_s   = f" RS:{c['rs_3m_today']:.0f}" if c.get("rs_3m_today") else ""
        thm    = by_ticker.get(ticker, {})
        tag    = THEME_TAG.get(thm.get("primary_theme", ""), "")
        lines67.append(f"  • ${ticker} — {label}{rs_s} {tag}")
        added_any = True
    for r in rev_infl[:3]:
        ticker  = r.get("ticker", "?")
        rev_now = r.get("rev_q0")
        rev_str = f" Rev→{rev_now:.0f}%" if rev_now else ""
        lines67.append(f"  • ${ticker} — 💰 Revenue Inflection{rev_str}")
        added_any = True
    if not added_any:
        lines67.append("  _No new entries today_")

    # Step 7 — Droppers
    lines67.append("")
    lines67.append("*📉 STEP 7 — Droppers:*")
    dropper_label = {
        "rs_fell_below_70":       "Fell below RS 70th",
        "rs_rank_drop_20":        "RS rank drop >20pts",
        "ma50_break":             "Broke MA50",
        "failed_breakout":        "Failed Breakout",
        "sector_lost_momentum":   "Sector lost momentum",
    }
    action_emoji = {
        "TRIM_IMMEDIATELY": "🚨",
        "STOP_CHECK":       "⚠️",
        "TRIM":             "✂️",
        "REVIEW":           "📋",
        "REMOVE_WATCHLIST": "🗑️",
    }
    dropped_any = False
    for d in sorted(droppers, key=lambda x: (
        0 if x.get("action") == "TRIM_IMMEDIATELY" else
        1 if x.get("action") == "STOP_CHECK" else 2
    ))[:8]:
        ticker = d.get("ticker", "?")
        event  = d.get("event", "")
        action = d.get("action", "REVIEW")
        label  = dropper_label.get(event, event)
        ae     = action_emoji.get(action, "📋")
        rs_s   = f" RS:{d['rs_3m_today']:.0f}" if d.get("rs_3m_today") else ""
        lines67.append(f"  {ae} ${ticker} — {label}{rs_s}")
        dropped_any = True
    if not dropped_any:
        lines67.append("  _No drops today — all clear ✅_")

    # Immediate portfolio signals
    imm_sigs = [s for s in signals.get("signals", []) if s.get("priority") == "IMMEDIATE"]
    if imm_sigs:
        lines67.append("")
        lines67.append("🚨 *Portfolio Actions:*")
        for s in imm_sigs[:4]:
            lines67.append(f"  🚨 ${s['ticker']}: {s['action']} — {s.get('reason','')[:40]}")

    messages.append("\n".join(lines67))

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
