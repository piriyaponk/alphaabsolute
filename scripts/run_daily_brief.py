"""
AlphaAbsolute — Daily Brief Pipeline (run_daily_brief.py)
Chains: Macro -> Stock Data -> Brief Generator -> output/daily_brief_YYMMDD.md + PDF + Telegram

Usage:
  python scripts/run_daily_brief.py
  python scripts/run_daily_brief.py --tickers NVDA MU PLTR AOT.BK
  python scripts/run_daily_brief.py --skip-fetch   (use cached data, fast re-run)
"""

import json
import os
import re
import sys
import argparse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

TODAY = datetime.now().strftime("%y%m%d")
TODAY_ISO = datetime.now().strftime("%Y-%m-%d")
TODAY_FULL = datetime.now().strftime("%d %B %Y")

DEFAULT_WATCHLIST = [
    "NVDA", "MU", "PLTR", "AVGO", "ANET",
    "LITE", "COHR", "RKLB", "AXON", "CACI",
    "NNE", "VRT", "AMD", "CRWV", "IONQ",
    "AOT.BK", "KBANK.BK",
]

SETUP_THEMES = {
    "NVDA": ("Leader", "AI Infrastructure"),
    "MU":   ("Misprice", "Memory / HBM"),
    "PLTR": ("Leader", "DefenseTech"),
    "AVGO": ("Leader", "AI Infrastructure"),
    "ANET": ("Leader", "AI Infrastructure"),
    "LITE": ("Hypergrowth", "Photonics"),
    "COHR": ("Hypergrowth", "Photonics"),
    "RKLB": ("Hypergrowth", "Space"),
    "AXON": ("Leader", "DefenseTech"),
    "CACI": ("Leader", "DefenseTech"),
    "NNE":  ("Hypergrowth", "Nuclear / SMR"),
    "VRT":  ("Leader", "AI Infrastructure"),
    "AMD":  ("Leader", "AI-Related"),
    "CRWV": ("Hypergrowth", "NeoCloud"),
    "IONQ": ("Hypergrowth", "Quantum Computing"),
    "AOT.BK":   ("Bottom Fish", "Tourism"),
    "KBANK.BK": ("Bottom Fish", "Banking"),
}


# ── User input parser ─────────────────────────────────────────────────────────
def parse_user_input() -> dict:
    """Parse data/user_input.txt into sections."""
    path = DATA_DIR / "user_input.txt"
    result = {
        "overnight_news": "",
        "foreign_flow": "",
        "earnings_results": "",
        "macro_news": "",
        "set_outlook": "",
        "watchlist_notes": "",
        "market_wrap": "",
    }
    if not path.exists():
        return result
    text = path.read_text(encoding="utf-8")
    sections = {
        "[OVERNIGHT_NEWS]": "overnight_news",
        "[FOREIGN_FLOW]": "foreign_flow",
        "[EARNINGS_RESULTS]": "earnings_results",
        "[MACRO_NEWS]": "macro_news",
        "[SET_OUTLOOK]": "set_outlook",
        "[WATCHLIST_NOTES]": "watchlist_notes",
        "[MARKET_WRAP]": "market_wrap",
    }
    current = None
    lines_acc = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in sections:
            if current:
                result[current] = "\n".join(l for l in lines_acc if not l.startswith("##")).strip()
            current = sections[stripped]
            lines_acc = []
        elif current:
            lines_acc.append(line)
    if current:
        result[current] = "\n".join(l for l in lines_acc if not l.startswith("##")).strip()
    return result


def load_latest_json(prefix: str) -> dict:
    files = sorted(DATA_DIR.glob(f"{prefix}_*.json"), reverse=True)
    return json.loads(files[0].read_text(encoding="utf-8")) if files else {}


# ── Key Factors auto-generator ────────────────────────────────────────────────
def build_key_factors(macro: dict, user: dict) -> list[dict]:
    """Build 6-8 numbered key factors from macro data + user input."""
    us = macro.get("us_macro", {})
    thai = macro.get("thai_macro", {})
    imf = macro.get("imf_forecasts", {})
    fx = macro.get("fx_commodities", {})
    factors = []

    def v(d, key):
        return (d.get(key) or {}).get("value")

    # Factor: US Equity Markets
    sp = fx.get("sp500", {})
    ndx = fx.get("nasdaq", {})
    sp_chg = sp.get("change_pct_1d")
    ndx_chg = ndx.get("change_pct_1d")
    if sp_chg is not None:
        direction = "ปรับตัวลง" if sp_chg < 0 else "ปรับตัวขึ้น"
        implication = "กดดันสินทรัพย์เสี่ยง โดยเฉพาะกลุ่ม Tech" if sp_chg < -0.5 else ("หนุน Sentiment เชิงบวก" if sp_chg > 0.5 else "ตลาดแกว่งตัว sideways")
        factors.append({
            "label": "US Equity Markets",
            "icon": "📉" if sp_chg < 0 else "📈",
            "body": f"S&P 500 {sp_chg:+.2f}%, Nasdaq {ndx_chg:+.2f}%",
            "implication": implication + " | " + (user.get("overnight_news") or "ติดตาม earnings season ต่อเนื่อง"),
            "tag": "(*)" if abs(sp_chg or 0) > 1 else "(+)" if (sp_chg or 0) > 0.3 else "(*)",
        })

    # Factor: US Rates & Yield Curve
    spread = v(us, "us_yield_spread_10_2")
    y10 = v(us, "us_10y_yield")
    pce = v(us, "pce_yoy")
    if y10 is not None:
        curve_read = "steepening — pro-growth signal" if (spread or 0) > 0.3 else ("inverted — recession risk" if (spread or 0) < -0.2 else "flat — mixed")
        fed_bias = "Fed อาจคงดอกเบี้ย" if (pce or 0) > 2.5 else "เปิดทาง Fed ลดดอกเบี้ย"
        factors.append({
            "label": "US Rates & Yield Curve",
            "icon": "🏦",
            "body": f"10Y yield {y10:.2f}% | 10Y-2Y spread {spread:+.2f}% ({curve_read}) | PCE {pce:.1f}% YoY" if spread is not None and pce is not None else f"10Y yield {y10:.2f}%",
            "implication": f"{fed_bias} — กระทบ EM fund flow และ THB",
            "tag": "(+)" if (spread or 0) > 0.3 else "(*)",
        })

    # Factor: Oil & Energy
    oil = (fx.get("oil_brent") or {})
    oil_p = oil.get("price")
    oil_chg = oil.get("change_pct_1d")
    if oil_p is not None:
        oil_read = "สูงกว่าปกติ — กดดันต้นทุนนำเข้า" if oil_p > 90 else "ทรงตัวในกรอบปกติ"
        sector_impact = "กดดันกลุ่มสายการบิน ขนส่ง และปิโตรเคมี | หนุน PTT, TOP" if oil_p > 90 else "ผลต่อกลุ่มพลังงานจำกัด"
        macro_news = user.get("macro_news", "")
        factors.append({
            "label": "Oil & Energy Security",
            "icon": "🛢️",
            "body": f"Brent ${oil_p:.2f} ({oil_chg:+.2f}%) — {oil_read}" if oil_chg is not None else f"Brent ${oil_p:.2f}",
            "implication": sector_impact + (f" | {macro_news}" if macro_news else ""),
            "tag": "(*)" if oil_p > 100 else "(+)" if oil_chg and oil_chg > 1 else "(-)",
        })

    # Factor: Gold & Risk Sentiment
    gold = (fx.get("gold_usd") or {})
    gold_p = gold.get("price")
    gold_chg = gold.get("change_pct_1d")
    if gold_p is not None:
        gold_read = "safe-haven demand สูง — ตลาดกังวล" if (gold_chg or 0) > 1 else "ทรงตัว"
        factors.append({
            "label": "Gold & Risk Sentiment",
            "icon": "🥇",
            "body": f"Gold ${gold_p:.0f} ({gold_chg:+.2f}%) — {gold_read}",
            "implication": "Risk-off signal — แนะถือ Gold เป็น hedge" if (gold_chg or 0) > 1 else "Neutral — ไม่มีสัญญาณ panic ชัดเจน",
            "tag": "(*)" if (gold_chg or 0) > 1.5 else "(+)",
        })

    # Factor: Thai CPI / Macro
    th_cpi = thai.get("th_cpi_inflation", {})
    th_cpi_val = (th_cpi.get("latest") or {}).get("value")
    bot_rate = (thai.get("bot_policy_rate") or {}).get("value")
    th_gdp_fcast = imf.get("th_gdp_forecast", {})
    if th_cpi_val is not None:
        inflation_read = "เร่งตัวจากพลังงาน — risk BoT ส่งสัญญาณคงดอกเบี้ย" if th_cpi_val > 2 else "ต่ำ — เปิดพื้นที่ BoT ลดดอกเบี้ย"
        factors.append({
            "label": "Thai Inflation & BoT Policy",
            "icon": "🇹🇭",
            "body": f"CPI {th_cpi_val:.2f}% YoY | BoT rate {bot_rate}% | GDP Forecast 2026F: {th_gdp_fcast.get('current_year_forecast','N/A')}% (IMF)",
            "implication": inflation_read,
            "tag": "(*)" if th_cpi_val > 2 else "(+)",
        })

    # Factor: Thai SET & FX
    set_idx = (fx.get("set_index") or {})
    set_p = set_idx.get("price")
    set_chg = set_idx.get("change_pct_1d")
    thb = (fx.get("thb_usd") or {}).get("price")
    set_outlook = user.get("set_outlook", "")
    if set_p is not None:
        thb_usd_str = f"1 USD = {1/thb:.2f} THB" if thb and thb > 0 else ""
        factors.append({
            "label": "SET Index & THB",
            "icon": "📊",
            "body": f"SET {set_p:.2f} ({set_chg:+.2f}%) | {thb_usd_str}",
            "implication": set_outlook or ("ตลาดอ่อนแรง" if (set_chg or 0) < -0.5 else "ตลาดทรงตัว"),
            "tag": "(*)" if (set_chg or 0) < -1 else "(+)" if (set_chg or 0) > 0.5 else "(*)",
        })

    # Factor: US Labor Market
    unemp = v(us, "unemployment")
    if unemp is not None:
        labor_read = "ตลาดแรงงานตึงตัว — กดดัน Fed คงดอกเบี้ย" if unemp < 4 else ("ตลาดแรงงานอ่อนตัว — หนุน Fed ลดดอกเบี้ย" if unemp > 4.5 else "ตลาดแรงงานสมดุล")
        factors.append({
            "label": "US Labor Market",
            "icon": "👷",
            "body": f"Unemployment {unemp:.1f}% | M2 Growth {v(us, 'm2_yoy'):.1f}% YoY" if v(us, "m2_yoy") else f"Unemployment {unemp:.1f}%",
            "implication": labor_read,
            "tag": "(-)" if unemp < 3.8 else "(+)" if unemp > 4.5 else "(*)",
        })

    # Factor: Earnings (from user input)
    earnings = user.get("earnings_results", "")
    if earnings:
        factors.append({
            "label": "Earnings Results",
            "icon": "📋",
            "body": earnings,
            "implication": "ติดตามทิศทาง EPS revision ของนักวิเคราะห์หลังประกาศ",
            "tag": "(+)",
        })

    return factors


# ── Watchlist narrative builder ───────────────────────────────────────────────
def build_watchlist_sections(stocks: dict, user_notes: str) -> dict:
    """Group stocks by setup type with narrative commentary."""
    groups = {"Leader": [], "Hypergrowth": [], "Misprice": [], "Bottom Fish": []}

    for ticker, data in stocks.items():
        if data.get("error"):
            continue
        cs = data.get("canslim", {})
        setup, theme = SETUP_THEMES.get(ticker, ("Leader", "Other"))
        price = data.get("price") or 0
        currency = data.get("currency", "USD")
        rev = data.get("revenue_growth_yoy") or 0
        gross_m = data.get("gross_margin_pct") or 0
        eps_q = data.get("eps_q_growth_pct") or 0
        target = data.get("analyst_target_mean")
        upside = ((target - price) / price * 100) if target and price else None
        pct_high = data.get("pct_from_52w_high") or 0
        finnhub = data.get("finnhub", {})
        beat_str = ""
        if finnhub.get("available") and finnhub.get("eps_surprises"):
            s = finnhub["eps_surprises"][0]
            if s.get("surprise_pct") is not None:
                beat_str = f"{'BEAT' if s['beat'] else 'MISS'} {s['surprise_pct']:+.1f}%"
        recs = finnhub.get("analyst_recs", {})
        buy_count = recs.get("total_bullish", 0)

        entry = {
            "ticker": ticker,
            "theme": theme,
            "setup": setup,
            "price_str": f"{currency} {price:.2f}",
            "canslim": cs.get("total", 0),
            "canslim_pass": cs.get("pass", False),
            "rev_growth": rev,
            "gross_margin": gross_m,
            "eps_q_growth": eps_q,
            "upside_pct": round(upside, 1) if upside else None,
            "pct_from_high": pct_high,
            "beat_str": beat_str,
            "buy_count": buy_count,
            "analyst_rec": data.get("analyst_recommendation", ""),
            "name": (data.get("name") or ticker)[:30],
        }
        groups.get(setup, groups["Leader"]).append(entry)

    # Sort each group by CANSLIM score desc
    for key in groups:
        groups[key].sort(key=lambda x: x["canslim"], reverse=True)

    return groups


def render_stock_block(e: dict, note: str = "") -> list[str]:
    """Render a single stock block in narrative style."""
    lines = []
    verdict = "PASS" if e["canslim_pass"] else "FAIL"
    upside_str = f"+{e['upside_pct']:.1f}% upside" if e.get("upside_pct") and e["upside_pct"] > 0 else ""
    beat_part = f" | EPS: {e['beat_str']}" if e["beat_str"] else ""
    buy_part = f" | {e['buy_count']} analysts Buy" if e["buy_count"] else ""

    lines.append(f"**{e['ticker']}** ({e['theme']}) — {e['price_str']} | CANSLIM {e['canslim']}/14 [{verdict}]")
    lines.append(f"  Rev +{e['rev_growth']:.1f}% | Gross Margin {e['gross_margin']:.1f}% | EPS Q {e['eps_q_growth']:+.1f}%{beat_part}")
    lines.append(f"  {e['pct_from_high']:.1f}% from 52W high | {upside_str}{buy_part}")
    if note:
        lines.append(f"  > {note}")
    lines.append("")
    return lines


# ── Main brief writer ─────────────────────────────────────────────────────────
def write_daily_brief(macro: dict, stocks: dict, portfolio: dict) -> Path:
    user = parse_user_input()
    lines = []
    regime = macro.get("regime", "Cautious")
    fx = macro.get("fx_commodities", {})
    us = macro.get("us_macro", {})
    thai = macro.get("thai_macro", {})

    regime_emoji = {"Bull": "🟢", "Bear": "🔴", "Cautious": "🟡"}.get(regime, "⚪")
    regime_label = {"Bull": "BULL — Risk On", "Bear": "BEAR — Risk Off", "Cautious": "CAUTIOUS — Mixed Signals"}.get(regime, regime)

    def fxv(key, fmt=".2f"):
        d = fx.get(key) or {}
        p = d.get("price")
        c = d.get("change_pct_1d")
        if p is None:
            return "N/A"
        return f"{p:{fmt}} ({c:+.2f}%)" if c is not None else f"{p:{fmt}}"

    def v(d, key):
        return (d.get(key) or {}).get("value")

    # ── Header ────────────────────────────────────────────────────────────────
    lines += [
        f"# AlphaAbsolute Daily Brief | {TODAY_FULL}",
        f"{regime_emoji} **Regime: {regime_label}**  |  Generated {datetime.now().strftime('%H:%M')} TH",
        "",
        "---",
        "",
    ]

    # ── Section 1: Overnight Recap ────────────────────────────────────────────
    sp500 = fx.get("sp500") or {}
    ndx = fx.get("nasdaq") or {}
    dow = fx.get("dow") or {}
    y10 = v(us, "us_10y_yield")
    oil_p = (fx.get("oil_brent") or {}).get("price")
    oil_chg = (fx.get("oil_brent") or {}).get("change_pct_1d")
    perf = macro.get("performance_context", {})

    lines += ["## 🌙 Overnight Recap (US Markets)", ""]

    # Auto: market indices with MTD/YTD (use fx price if available, else fall back to perf context)
    sp_chg = sp500.get("change_pct_1d")
    ndx_chg = ndx.get("change_pct_1d")
    dow_chg = dow.get("change_pct_1d")
    def _parts(*items):
        return " | ".join(p for p in items if p)

    has_equity = sp500.get("price") or perf.get("sp500")
    if has_equity:
        sp_daily = f"{sp_chg:+.2f}% daily" if sp_chg is not None else None
        ndx_daily = f"{ndx_chg:+.2f}% daily" if ndx_chg is not None else None
        dow_daily = f"{dow_chg:+.2f}% daily" if dow_chg is not None else None
        sp_mtd = f"MTD {perf.get('sp500',{}).get('mtd_pct',0):+.1f}%" if perf.get("sp500") else None
        sp_ytd = f"YTD {perf.get('sp500',{}).get('ytd_pct',0):+.1f}%" if perf.get("sp500") else None
        ndx_mtd = f"MTD {perf.get('nasdaq',{}).get('mtd_pct',0):+.1f}%" if perf.get("nasdaq") else None
        ndx_ytd = f"YTD {perf.get('nasdaq',{}).get('ytd_pct',0):+.1f}%" if perf.get("nasdaq") else None
        dow_mtd = f"MTD {perf.get('dow',{}).get('mtd_pct',0):+.1f}%" if perf.get("dow") else None
        dow_ytd = f"YTD {perf.get('dow',{}).get('ytd_pct',0):+.1f}%" if perf.get("dow") else None
        lines.append(f"- **S&P 500**: {_parts(sp_daily, sp_mtd, sp_ytd)}")
        lines.append(f"- **Nasdaq**: {_parts(ndx_daily, ndx_mtd, ndx_ytd)}")
        lines.append(f"- **Dow Jones**: {_parts(dow_daily, dow_mtd, dow_ytd)}")
    if y10:
        spread = v(us, "us_yield_spread_10_2")
        spread_str = f" | Spread 10Y-2Y {spread:+.2f}%" if spread is not None else ""
        lines.append(f"- **Treasuries**: 10Y yield {y10:.3f}%{spread_str}")
    if oil_p:
        gold_p = (fx.get("gold_usd") or {}).get("price", 0)
        oil_daily = f"${oil_p:.2f} ({oil_chg:+.2f}% daily)" if oil_chg is not None else f"${oil_p:.2f}"
        oil_mtd = f"MTD {perf.get('brent',{}).get('mtd_pct',0):+.1f}%" if perf.get("brent") else None
        oil_ytd = f"YTD {perf.get('brent',{}).get('ytd_pct',0):+.1f}%" if perf.get("brent") else None
        gold_mtd = f"MTD {perf.get('gold',{}).get('mtd_pct',0):+.1f}%" if perf.get("gold") else None
        gold_ytd = f"YTD {perf.get('gold',{}).get('ytd_pct',0):+.1f}%" if perf.get("gold") else None
        lines.append(f"- **Brent Crude**: {_parts(oil_daily, oil_mtd, oil_ytd)}")
        if gold_p:
            lines.append(f"- **Gold**: ${gold_p:.0f} {_parts(gold_mtd, gold_ytd)}")

    # SET index with MTD/YTD
    set_p_recv = (fx.get("set_index") or {}).get("price")
    if set_p_recv:
        set_chg_recv = (fx.get("set_index") or {}).get("change_pct_1d", 0) or 0
        thb_r = (fx.get("thb_usd") or {}).get("price")
        thb_str = f"THB {1/thb_r:.2f}/USD" if thb_r and thb_r > 0 else None
        set_daily = f"{set_p_recv:.2f} ({set_chg_recv:+.2f}% daily)"
        set_mtd = f"MTD {perf.get('set',{}).get('mtd_pct',0):+.1f}%" if perf.get("set") else None
        set_ytd = f"YTD {perf.get('set',{}).get('ytd_pct',0):+.1f}%" if perf.get("set") else None
        lines.append(f"- **SET Index**: {_parts(set_daily, set_mtd, set_ytd, thb_str)}")

    lines.append("")

    # Manual: overnight news from user_input
    if user["overnight_news"]:
        lines.append("**Overnight News:**")
        for ln in user["overnight_news"].splitlines():
            if ln.strip():
                lines.append(f"- {ln.strip()}")
        lines.append("")

    # Market Wrap from user paste (Bloomberg/investing.com)
    if user["market_wrap"]:
        lines.append("**Market Wrap:**")
        for ln in user["market_wrap"].splitlines():
            if ln.strip():
                lines.append(f"  {ln.strip()}")
        lines.append("")

    lines += ["---", ""]

    # ── Section 2: Key Factors ────────────────────────────────────────────────
    factors = build_key_factors(macro, user)
    lines += [f"## 🔑 Key Factors ({len(factors)})", ""]

    for i, f in enumerate(factors, 1):
        lines.append(f"{i}) {f['icon']} **{f['label']}** {f['tag']}")
        lines.append(f"   {f['body']}")
        lines.append(f"   → {f['implication']}")
        lines.append("")

    lines += ["---", ""]

    # ── Section 3: Thai Market Pulse ──────────────────────────────────────────
    set_p = (fx.get("set_index") or {}).get("price")
    set_chg = (fx.get("set_index") or {}).get("change_pct_1d")
    thb = (fx.get("thb_usd") or {}).get("price")
    bot_rate = (thai.get("bot_policy_rate") or {}).get("value")
    th_gdp = thai.get("th_gdp_growth", {})
    th_gdp_val = (th_gdp.get("latest") or {}).get("value")
    th_gdp_yr = (th_gdp.get("latest") or {}).get("year", "")
    th_cpi = thai.get("th_cpi_inflation", {})
    th_cpi_val = (th_cpi.get("latest") or {}).get("value")

    lines += ["## 🇹🇭 Thai Market Pulse", ""]

    if set_p:
        thb_display = f"1 USD = {1/thb:.2f} THB" if thb and thb > 0 else ""
        lines.append(f"**SET {set_p:.2f}** ({set_chg:+.2f}%) | THB: {thb_display} | BoT rate: {bot_rate}%")
        lines.append("")

    if user["foreign_flow"]:
        lines.append("**Net Flow:**")
        lines.append(f"{user['foreign_flow']}")
        lines.append("")

    lines += [
        f"**Macro:** GDP {th_gdp_val:.2f}% ({th_gdp_yr}) | CPI {th_cpi_val:.2f}% | IMF GDP Forecast 2026F: {(macro.get('imf_forecasts') or {}).get('th_gdp_forecast', {}).get('current_year_forecast', 'N/A')}%",
        "",
    ]

    if user["set_outlook"]:
        lines.append(f"> {user['set_outlook']}")
        lines.append("")

    # Earnings from user input
    if user["earnings_results"]:
        lines += ["**Earnings Results:**"]
        for ln in user["earnings_results"].splitlines():
            if ln.strip():
                lines.append(f"- {ln.strip()}")
        lines.append("")

    lines += ["---", ""]

    # ── Section 4: Watchlist — PULSE picks ─────────────────────────────────────
    groups = build_watchlist_sections(stocks, user["watchlist_notes"])
    lines += ["## 📊 Watchlist — PULSE picks", ""]

    # Parse watchlist notes for specific tickers
    notes_map = {}
    if user["watchlist_notes"]:
        for ln in user["watchlist_notes"].splitlines():
            ln = ln.strip()
            if not ln:
                continue
            parts = ln.split("—", 1)
            if len(parts) == 2:
                t = parts[0].strip().replace("**", "")
                notes_map[t] = parts[1].strip()

    for setup_label, icon in [
        ("Leader", "🏆 Leader / Momentum"),
        ("Hypergrowth", "🚀 Hypergrowth"),
        ("Misprice", "🔍 Misprice"),
        ("Bottom Fish", "🎣 Bottom Fish"),
    ]:
        entries = groups.get(setup_label, [])
        if not entries:
            continue
        lines.append(f"### {icon}")
        lines.append("")
        for e in entries:
            note = notes_map.get(e["ticker"], "")
            lines += render_stock_block(e, note)

    # Top CANSLIM summary table (all PASS)
    all_passed = [
        e for g in groups.values() for e in g if e["canslim_pass"]
    ]
    all_passed.sort(key=lambda x: x["canslim"], reverse=True)

    if all_passed:
        lines += [
            "### Summary — CANSLIM PASS",
            "",
            "| Ticker | Setup | Price | Score | Rev YoY | EPS Q | EPS Beat | Upside |",
            "|--------|-------|-------|-------|---------|-------|----------|--------|",
        ]
        for e in all_passed:
            up = f"+{e['upside_pct']:.1f}%" if e.get("upside_pct") else "—"
            lines.append(
                f"| **{e['ticker']}** | {e['setup']} | {e['price_str']} "
                f"| {e['canslim']}/14 | +{e['rev_growth']:.1f}% "
                f"| {e['eps_q_growth']:+.1f}% | {e['beat_str'] or '—'} | {up} |"
            )
        lines.append("")

    lines += ["---", ""]

    # ── Section 5: Portfolio State ────────────────────────────────────────────
    alloc = portfolio.get("allocation", {})
    holdings = [h for h in portfolio.get("holdings", []) if h.get("ticker") != "EXAMPLE"]
    lines += ["## 💼 Portfolio State", ""]
    lines.append(f"Stocks **{alloc.get('stocks_total_pct', 0)}%**  |  Gold **{alloc.get('gold_pct', 0)}%**  |  Cash **{alloc.get('cash_pct', 100)}%**")
    lines.append("")

    if holdings:
        lines += [
            "| Ticker | Setup | Weight | P&L | Stage | Gate |",
            "|--------|-------|--------|-----|-------|------|",
        ]
        for h in holdings:
            pnl = h.get("unrealized_pnl_pct")
            pnl_str = f"{pnl:+.1f}%" if pnl is not None else "—"
            gate = h.get("gate_verdict", "—")
            lines.append(f"| {h['ticker']} | {h.get('setup_type','')} | {h.get('weight_pct',0)}% | {pnl_str} | Stage {h.get('stage_weinstein',2)} | {gate} |")
    else:
        lines.append("_No open positions — 100% cash (waiting for setup)_")

    lines += ["", "---", ""]

    # ── Section 6: Factors to Watch This Week ────────────────────────────────
    event_path = DATA_DIR / "event_calendar.json"
    if event_path.exists():
        try:
            cal = json.loads(event_path.read_text(encoding="utf-8"))

            econ_events = [e for e in cal.get("economic_calendar", []) if e.get("date", "") >= TODAY_ISO]
            earn_events = [e for e in cal.get("watchlist_earnings", []) if e.get("date", "") >= TODAY_ISO]

            has_content = econ_events or earn_events
            if has_content:
                lines += ["## 📅 Factors to Watch This Week", ""]

            # Economic data calendar
            if econ_events:
                lines.append("**Economic Data Releases:**")
                lines.append("")
                lines.append("| Date | Event | Consensus | Prior |")
                lines.append("|------|-------|-----------|-------|")
                for e in econ_events[:8]:
                    impact = e.get("impact", "")
                    flag = "🔴" if "high" in impact else "🟡"
                    cons = e.get("consensus") or "—"
                    prior = e.get("prior") or "—"
                    lines.append(f"| {flag} {e['date']} | {e['event']} | {cons} | {prior} |")
                lines.append("")

            # Earnings calendar
            if earn_events:
                lines.append("**Earnings Calendar (Watchlist):**")
                lines.append("")
                lines.append("| Date | Ticker | Time | EPS Est |")
                lines.append("|------|--------|------|---------|")
                for e in earn_events[:10]:
                    hour = "AMC" if e.get("hour") == "amc" else ("BMO" if e.get("hour") == "bmo" else "TBD")
                    eps = f"${e['eps_estimate']:.2f}" if e.get("eps_estimate") is not None else "N/A"
                    lines.append(f"| {e['date']} | **{e['symbol']}** | {hour} | {eps} |")
                lines.append("")

            # General high-impact non-watchlist earnings
            all_earn = [e for e in cal.get("earnings_calendar", [])
                        if e.get("date", "") >= TODAY_ISO and not e.get("in_watchlist")]
            notable = [e for e in all_earn if e.get("symbol") in [
                "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "SMCI", "AMD",
                "NFLX", "CRM", "SNOW", "SHOP", "NET", "DDOG"
            ]]
            if notable:
                lines.append("**Notable Earnings (Market-moving):**")
                for e in notable[:6]:
                    hour = "AMC" if e.get("hour") == "amc" else ("BMO" if e.get("hour") == "bmo" else "")
                    eps = f"${e['eps_estimate']:.2f}" if e.get("eps_estimate") is not None else ""
                    eps_str = f" | EPS est {eps}" if eps else ""
                    lines.append(f"- **{e['symbol']}** {hour} ({e['date']}){eps_str}")
                lines.append("")

            if has_content:
                lines += ["---", ""]
        except Exception:
            pass

    # ── Section 7: CIO View ───────────────────────────────────────────────────
    guidance = {
        "Bull":     ("80-90% stocks | 10% gold | 0-10% cash", "Full PULSE screen — add Leader positions, scale into high RS names"),
        "Bear":     ("30-40% stocks | 20% gold | 40%+ cash", "Defensive — cut Stage 3/4 immediately, build cash buffer"),
        "Cautious": ("60-70% stocks | 15-20% gold | 10-20% cash", "Selective only — CANSLIM >= 10, RS > 80th pct, tight stops"),
    }
    alloc_guide, action_guide = guidance.get(regime, guidance["Cautious"])
    vix_val = v(us, "vix") or 0

    lines += [
        "## 🎯 CIO View",
        "",
        f"{regime_emoji} **{regime_label}**",
        "",
        f"**Allocation:** {alloc_guide}",
        f"**Action:** {action_guide}",
        f"**VIX:** {vix_val:.1f} ({'low fear' if vix_val < 18 else 'elevated' if vix_val < 28 else 'high fear'}) | **Yield Spread:** {v(us, 'us_yield_spread_10_2'):+.2f}%",
        "",
    ]

    lines += [
        "---",
        f"_AlphaAbsolute | Data: FRED + World Bank + IMF + yfinance + Finnhub | {datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        "_Paste today's news in `data/user_input.txt` before running for full context._",
    ]

    out_path = OUTPUT_DIR / f"daily_brief_{TODAY}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ── Pipeline ──────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", default=None)
    parser.add_argument("--skip-fetch", action="store_true")
    args = parser.parse_args()

    tickers = args.tickers or DEFAULT_WATCHLIST

    sys.path.insert(0, str(Path(__file__).parent))

    if not args.skip_fetch:
        print("=" * 60)
        print("STEP 1: Fetching macro data...")
        print("=" * 60)
        import fetch_macro
        macro_data = fetch_macro.main()
    else:
        print("Using cached macro data...")
        macro_data = load_latest_json("macro")

    if not args.skip_fetch:
        print("\n" + "=" * 60)
        print(f"STEP 2: Fetching stock data ({len(tickers)} tickers)...")
        print("=" * 60)
        import fetch_stock_data
        stock_results = {}
        for ticker in tickers:
            stock_results[ticker] = fetch_stock_data.fetch_stock(ticker)
        stock_file = DATA_DIR / f"stock_data_{TODAY}.json"
        stock_file.write_text(json.dumps(stock_results, indent=2, default=str), encoding="utf-8")
    else:
        print("Using cached stock data...")
        stock_results = load_latest_json("stock_data")

    if not args.skip_fetch:
        print("\n" + "=" * 60)
        print("STEP 2b: Fetching calendar (earnings + economic data)...")
        print("=" * 60)
        import fetch_calendar
        fetch_calendar.main()
    else:
        print("Using cached calendar...")

    portfolio = {}
    portfolio_path = DATA_DIR / "portfolio.json"
    if portfolio_path.exists():
        portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))

    print("\n" + "=" * 60)
    print("STEP 3: Generating daily brief...")
    print("=" * 60)
    out_path = write_daily_brief(macro_data or load_latest_json("macro"), stock_results, portfolio)
    print(f"Brief saved: {out_path}")

    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        print("\n" + "=" * 60)
        print("STEP 4: Sending Telegram notification...")
        print("=" * 60)
        import send_telegram
        send_telegram.notify(
            macro_data or load_latest_json("macro"),
            stock_results,
            portfolio,
        )
    else:
        print("\nTelegram not configured — skipping.")

    print(f"\nDone: {out_path}")


if __name__ == "__main__":
    main()

