"""
AlphaAbsolute -- A11 Report Writer + Telegram
=============================================
Generates daily brief + pushes to Telegram before market open.

Brief format:
  MARKET: Regime | Cash Floor | TD signals
  MACRO: 1-sentence state
  THEMES HOT/WARM
  SYSTEM 4 PORTFOLIO: NAV | positions | deployed%
  RS CLIMBERS / DROPPERS

Output:
  output/daily_brief_YYMMDD.md
  Telegram push via bot
"""

from __future__ import annotations
import json
import os
import ssl
import sys
import urllib.request
import urllib.parse
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# FIX: self-signed cert in corporate proxy — disable verification for Telegram calls
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

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
    return []  # setup_scanner removed — System 4 does not use PRISM setups


def _load_signals() -> dict:
    return {}  # action_signals removed — System 4 manages exits internally


def _load_portfolio() -> dict:
    return {}  # old portfolio_state removed — use System 4 state.json


def _load_paper_portfolio() -> dict:
    return _load_json(ROOT / "data" / "paper_trading" / "state.json")


def _load_risk() -> dict:
    return {}  # risk_guardian removed — System 4 enforces its own limits


def _load_changes() -> dict:
    return _load_json(ROOT / "data" / "rs_universe" / "changes_today.json")


def _load_top30() -> list:
    return []  # trend_template_screener removed — System 4 does not use PRISM watchlist


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


# ── Build markdown brief ──────────────────────────────────────────────────────

def build_brief(today: str) -> str:
    health    = _load_health()
    macro     = _load_macro()
    paper     = _load_paper_portfolio()
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

    # System 4 Portfolio
    s4_nav   = paper.get("nav", 0)
    s4_inc   = paper.get("inception_nav", s4_nav)
    s4_ret   = (s4_nav / max(s4_inc, 1) - 1) * 100
    s4_pos   = paper.get("positions", {})
    s4_cash  = paper.get("cash", 0)
    s4_dep   = round((1 - s4_cash / max(s4_nav, 1)) * 100, 1) if s4_nav else 0

    lines.append("## SYSTEM 4 PORTFOLIO")
    lines.append(
        f"**{len(s4_pos)} positions** | Deployed: {s4_dep:.0f}% | "
        f"NAV: ${s4_nav:,.0f} ({s4_ret:+.1f}%)"
    )

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


# ── Setup name mapping ────────────────────────────────────────────────────────

SETUP_FULL_NAME = {
    "BKT": "Breakout",
    "VCP": "VCP",
    "CWH": "Cup w/Handle",
    "SOS": "Sign of Strength",
    "PPT": "Pocket Pivot",
    "SPR": "Spring",
    "EMA": "EMA Pullback",
    "VPS": "Vol Pocket Support",
}


def _setup_full(code: str) -> str:
    return SETUP_FULL_NAME.get(code, code)


# ── Breadth context string ────────────────────────────────────────────────────

def _breadth_context(pct) -> str:
    """Return breadth % with explanation of what it means."""
    if pct is None:
        return "breadth N/A"
    pct = float(pct)
    if pct >= 70:
        icon, note = "✅", "strong market internals"
    elif pct >= 60:
        icon, note = "🟡", "healthy, watch for change"
    elif pct >= 50:
        icon, note = "⚠️", "weakening — distribution risk"
    elif pct >= 40:
        icon, note = "🔴", "deteriorating — reduce exposure"
    else:
        icon, note = "🔴", "collapsed — cash priority"
    return f"{pct:.0f}% above 50DMA {icon} ({note})"


# ── HOT theme string with RS + momentum ──────────────────────────────────────

def _hot_themes_str() -> str:
    """Format HOT themes with RS percentile and momentum arrow (1M vs 3M)."""
    data   = _load_json(ROOT / "data" / "rs_universe" / "theme_rs_latest.json")
    themes = data.get("themes", data)
    hot    = []
    for tid, tv in themes.items():
        if not isinstance(tv, dict):
            continue
        phase = tv.get("phase", tv.get("grade", ""))
        if phase != "HOT":
            continue
        name   = tv.get("name", tid.replace("_", " "))
        rs1m   = tv.get("theme_vs_themes_pct") or tv.get("theme_1m_pct") or 0
        rs3m   = tv.get("theme_3m_pct") or 0
        # Momentum arrow: compare 1M vs 3M (if 1M > 3M = accelerating)
        if rs1m > rs3m + 3:
            arrow = "↑"
        elif rs1m < rs3m - 3:
            arrow = "↓"
        else:
            arrow = "→"
        hot.append((rs1m, f"{name} ({rs1m:.0f}{arrow})"))
    hot.sort(reverse=True)
    return " | ".join(h[1] for h in hot[:5]) if hot else "—"


def build_telegram_messages(health: dict, setups: list, signals: dict,
                              portfolio: dict, paper: dict,
                              changes: Optional[dict] = None) -> list[str]:
    """
    AlphaAbsolute v2 Telegram format.

    MSG 1: Regime header — regime, cash, breadth (with explanation),
           dist days, TD signals, macro (brief), HOT themes (RS + momentum)
    MSG 2+: Grade A setup cards (max 5), full verbose format
    MSG after setups: Grade B compact list (if any + Markup/Distribution)
    Last MSG: Portfolio summary
    """
    rs_universe = _load_rs_universe()
    macro       = _load_macro()
    changes     = changes or _load_changes()

    regime    = health.get("regime", "Unknown")
    cash_floor = int(health.get("cash_floor", 0) * 100)
    max_dep   = int(health.get("max_deployed", 1) * 100)
    spy_td    = health.get("spy_td_signal", "Neutral")
    qqq_td    = health.get("qqq_td_signal", "Neutral")
    dist_days = health.get("distribution_days", 0)
    pct_50dma = health.get("pct_above_50dma")
    emoji     = REGIME_EMOJI.get(regime, "⚪")

    macro_mod  = macro.get("macro_modifier", 1.0)
    rate_env   = macro.get("rate_environment", "")
    yield_10y  = macro.get("yield_10y")
    hy_spread  = macro.get("hy_spread_pct") or macro.get("hy_spread_bps")

    messages  = []
    today_str = date.today().strftime("%d %b %Y")

    # ── MSG 1: Regime header ──────────────────────────────────────────────────
    # Entry rule line
    if regime == "Markup":
        entry_rule = "System 4 fully deployed ✅"
    elif regime == "Sideways":
        entry_rule = "System 4 selective | 20-40% cash"
    elif regime == "Distribution":
        entry_rule = "System 4 defensive | 40-60% cash"
    else:
        entry_rule = "System 4 cash priority | 75-100% cash"

    # Breadth with explanation
    breadth_s = _breadth_context(pct_50dma)

    # TD signals (only show non-Neutral)
    td_parts = []
    if spy_td and spy_td != "Neutral": td_parts.append(f"SPY: {spy_td}")
    if qqq_td and qqq_td != "Neutral": td_parts.append(f"QQQ: {qqq_td}")
    td_str = " | ".join(td_parts) if td_parts else "TD: Neutral"

    # Macro: 1 line only (still relevant — A02 drives macro_modifier)
    macro_parts = []
    if rate_env:
        macro_parts.append(f"Rates: {rate_env}")
    if yield_10y:
        macro_parts.append(f"10Y {yield_10y:.2f}%")
    if hy_spread:
        macro_parts.append(f"HY {hy_spread:.2f}%")
    macro_line = "📊 " + " | ".join(macro_parts) if macro_parts else ""
    # FIX: macro_modifier was demoted to INFORMATIONAL in BOA-005 DEC-014.
    # It no longer reduces position sizes — A01 cash_floor handles risk at portfolio level.
    # Old message "all sizes reduced" was wrong. Show as context note only.
    macro_mod_line = f"[Macro] {rate_env} — A02 informational (sizes not reduced)" if macro_mod < 1.0 else ""

    # HOT themes with RS percentile + momentum
    hot_str = _hot_themes_str()

    header_lines = [
        f"{emoji} *AlphaAbsolute | {regime} | {today_str}*",
        f"Cash floor: *{cash_floor}%* | Deploy max: *{max_dep}%*",
        entry_rule,
        f"Breadth: {breadth_s} | Dist days: {dist_days}",
        td_str,
    ]
    if macro_line:
        header_lines.append(macro_line)
    if macro_mod_line:
        header_lines.append(macro_mod_line)
    if hot_str and hot_str != "—":
        header_lines.append(f"🔥 HOT: {hot_str}")

    messages.append("\n".join(header_lines))

    # ── System 4 Portfolio ────────────────────────────────────────────────────
    s4 = _load_paper_portfolio()
    if s4:
        s4_nav   = s4.get("nav", 0)
        s4_inc   = s4.get("inception_nav", s4_nav)
        s4_ret   = (s4_nav / max(s4_inc, 1) - 1) * 100
        s4_pos   = s4.get("positions", {})
        s4_cash  = s4.get("cash", 0)
        s4_dep   = round((1 - s4_cash / max(s4_nav, 1)) * 100, 1)
        s4_reg   = s4.get("regime", "?")
        s4_lines = [
            f"📈 *System 4 Portfolio* — {len(s4_pos)} pos | Deployed {s4_dep:.0f}% | "
            f"NAV ${s4_nav:,.0f} ({s4_ret:+.1f}%) | S4 Regime: {s4_reg}"
        ]
        top5 = sorted(s4_pos.items(), key=lambda x: -x[1].get("weight_target", 0))[:5]
        for tkr, p in top5:
            s4_lines.append(f"  *{tkr}* {p['weight_target']*100:.1f}% | RS={p.get('rs_pct',0):.0f}")
        messages.append("\n".join(s4_lines))

    # (PRISM setups removed — System 4 manages its own monthly rebalance)

    # ── Portfolio summary (always last) ───────────────────────────────────────
    # System 4 is the ONE active portfolio — read from state.json loaded above as `s4`
    s4_held_tickers = set(s4_pos.keys()) if s4 else set()
    droppers        = (changes or {}).get("droppers", [])
    held_drops      = [d for d in droppers if d.get("ticker") in s4_held_tickers]

    port_lines = ["💼 *System 4 Portfolio*"]
    if not s4 or not s4_pos:
        port_lines.append("No open positions | fully in cash")
    else:
        for tkr, p in sorted(s4_pos.items(), key=lambda x: -x[1].get("weight_target", 0))[:5]:
            pnl   = p.get("unrealized_pct", 0) or 0
            pnl_s = f"+{pnl:.1f}%" if pnl >= 0 else f"{pnl:.1f}%"
            stp   = p.get("stop_price", 0)
            port_lines.append(f"  *{tkr}* {pnl_s} | stop {_dollar(stp)}")

    if held_drops:
        port_lines.append("📉 *RS drops (held):*")
        for d in held_drops[:3]:
            port_lines.append(f"  • *{d['ticker']}*: {d.get('event','').replace('_',' ')}")

    messages.append("\n".join(port_lines))

    return messages


# ── Send Telegram ─────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("  [WARN] Telegram not configured (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env)")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    def _post(payload: dict) -> bool:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req  = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode()).get("ok", False)

    try:
        return _post({"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except Exception as e:
        # 400 Bad Request = Markdown parse error (unescaped special chars).
        # Retry as plain text — always succeeds.
        if "400" in str(e):
            try:
                return _post({"chat_id": TELEGRAM_CHAT_ID, "text": message})
            except Exception as e2:
                print(f"  [ERROR] Telegram send failed (plain fallback): {e2}")
                return False
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
    paper     = _load_paper_portfolio()
    changes   = _load_changes()

    messages = build_telegram_messages(health, [], {}, {}, paper, changes)

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
        "setups_in_brief": 0,
        "status":       "ok",
    }


if __name__ == "__main__":
    run()
