"""
AlphaAbsolute — Telegram Notifier
Generates a PDF daily brief and sends it to Telegram with a text summary.

Usage (standalone):
  python scripts/send_telegram.py                  # sends latest brief
  python scripts/send_telegram.py --test           # sends test message only

Called automatically by run_daily_brief.py after brief is generated.
"""

import json
import os
import requests
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELE_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

TODAY = datetime.now().strftime("%y%m%d")
TODAY_FULL = datetime.now().strftime("%d %b %Y")


# ── PDF Generator ─────────────────────────────────────────────────────────────
class BriefPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 13)
        self.set_fill_color(15, 23, 42)       # dark navy
        self.set_text_color(255, 255, 255)
        self.cell(0, 10, f"AlphaAbsolute Daily Brief  |  {TODAY_FULL}", fill=True, ln=True, align="C")
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"AlphaAbsolute | {datetime.now().strftime('%Y-%m-%d %H:%M')} | FRED + World Bank + IMF + yfinance + Finnhub", align="C")


def section(pdf: FPDF, title: str):
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(30, 58, 138)    # blue
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 7, f"  {title}", fill=True, ln=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(1)


def row(pdf: FPDF, label: str, value: str, shade: bool = False):
    pdf.set_font("Helvetica", "", 9)
    if shade:
        pdf.set_fill_color(243, 244, 246)
    else:
        pdf.set_fill_color(255, 255, 255)
    pdf.cell(85, 6, f"  {label}", fill=True, border=0)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, value, fill=shade, ln=True)


def table_header(pdf: FPDF, cols: list, widths: list):
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(71, 85, 105)
    pdf.set_text_color(255, 255, 255)
    for col, w in zip(cols, widths):
        pdf.cell(w, 6, f" {col}", fill=True, border=0)
    pdf.ln()
    pdf.set_text_color(0, 0, 0)


def table_row(pdf: FPDF, values: list, widths: list, shade: bool = False):
    pdf.set_font("Helvetica", "", 8)
    if shade:
        pdf.set_fill_color(248, 250, 252)
    else:
        pdf.set_fill_color(255, 255, 255)
    for val, w in zip(values, widths):
        pdf.cell(w, 5, f" {str(val)}", fill=True, border=0)
    pdf.ln()


def regime_color(regime: str) -> tuple:
    if regime == "Bull":
        return (22, 163, 74)    # green
    elif regime == "Bear":
        return (220, 38, 38)    # red
    return (234, 179, 8)        # yellow / cautious


def generate_pdf(macro: dict, stocks: dict, portfolio: dict) -> Path:
    pdf = BriefPDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_margins(10, 10, 10)

    regime = macro.get("regime", "Cautious")
    us = macro.get("us_macro", {})
    thai = macro.get("thai_macro", {})
    imf = macro.get("imf_forecasts", {})
    fx = macro.get("fx_commodities", {})

    def v(d, key, fmt=".2f", suffix="", fallback="N/A"):
        val = d.get(key, {}).get("value")
        return f"{val:{fmt}}{suffix}" if val is not None else fallback

    def fxv(key, fmt=".2f"):
        d = fx.get(key, {})
        p = d.get("price")
        c = d.get("change_pct_1d")
        if p is None:
            return "N/A"
        chg = f" ({c:+.2f}%)" if c is not None else ""
        return f"{p:{fmt}}{chg}"

    # ── Regime Banner ─────────────────────────────────────────────────────────
    r, g, b = regime_color(regime)
    pdf.set_fill_color(r, g, b)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 11)
    regime_labels = {"Bull": "BULL -- Risk On", "Bear": "BEAR -- Risk Off", "Cautious": "CAUTIOUS -- Mixed Signals"}
    pdf.cell(0, 9, f"  Regime: {regime_labels.get(regime, regime)}", fill=True, ln=True, align="L")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    # ── US Macro ──────────────────────────────────────────────────────────────
    section(pdf, "US Macro")
    pairs = [
        ("Fed Funds Rate", v(us, "fed_funds_rate", suffix="%")),
        ("US 10Y Yield", v(us, "us_10y_yield", suffix="%")),
        ("US 2Y Yield", v(us, "us_2y_yield", suffix="%")),
        ("10Y-2Y Spread", v(us, "us_yield_spread_10_2", suffix="%")),
        ("VIX", v(us, "vix")),
        ("PCE YoY", v(us, "pce_yoy", suffix="%")),
        ("Unemployment", v(us, "unemployment", suffix="%")),
        ("M2 YoY", v(us, "m2_yoy", suffix="%")),
    ]
    for i, (label, val) in enumerate(pairs):
        row(pdf, label, val, shade=(i % 2 == 0))
    pdf.ln(3)

    # ── Thai Macro ────────────────────────────────────────────────────────────
    section(pdf, "Thai Macro")
    th_gdp = thai.get("th_gdp_growth", {})
    th_gdp_val = (th_gdp.get("latest") or {}).get("value")
    th_gdp_yr = (th_gdp.get("latest") or {}).get("year", "")
    th_cpi = thai.get("th_cpi_inflation", {})
    th_cpi_val = (th_cpi.get("latest") or {}).get("value")
    bot_rate = thai.get("bot_policy_rate", {}).get("value")
    th_gdp_fcast = imf.get("th_gdp_forecast", {})

    thai_pairs = [
        ("BoT Policy Rate", f"{bot_rate}%" if bot_rate else "N/A"),
        ("THB/USD", fxv("thb_usd", ".4f")),
        ("SET Index", fxv("set_index", ".2f")),
        ("Real GDP Growth", f"{th_gdp_val:.2f}% ({th_gdp_yr})" if th_gdp_val else "N/A"),
        ("CPI Inflation", f"{th_cpi_val:.2f}%" if th_cpi_val else "N/A"),
        ("GDP Forecast 2026 (IMF)", f"{th_gdp_fcast.get('current_year_forecast', 'N/A')}%"),
    ]
    for i, (label, val) in enumerate(thai_pairs):
        row(pdf, label, val, shade=(i % 2 == 0))
    pdf.ln(3)

    # ── Commodities ───────────────────────────────────────────────────────────
    section(pdf, "Commodities & FX")
    comm_pairs = [
        ("Gold (USD/oz)", fxv("gold_usd")),
        ("Brent Oil (USD/bbl)", fxv("oil_brent")),
    ]
    for i, (label, val) in enumerate(comm_pairs):
        row(pdf, label, val, shade=(i % 2 == 0))
    pdf.ln(3)

    # ── Watchlist CANSLIM Table ───────────────────────────────────────────────
    section(pdf, "Watchlist -- CANSLIM Scores")
    cols = ["Ticker", "Price", "CANSLIM", "Rev YoY", "EPS Q", "Analyst", "EPS Beat"]
    widths = [22, 28, 22, 20, 20, 22, 36]
    table_header(pdf, cols, widths)

    for i, (ticker, data) in enumerate(stocks.items()):
        if data.get("error"):
            continue
        cs = data.get("canslim", {})
        score = f"{cs.get('total', 0)}/{cs.get('max', 14)} {'OK' if cs.get('pass') else 'X'}"
        price = data.get("price") or 0
        currency = data.get("currency", "USD")
        price_str = f"{currency} {price:.1f}"
        rev_g = f"{data.get('revenue_growth_yoy', 0):+.1f}%"
        eps_q = f"{data.get('eps_q_growth_pct') or 0:+.1f}%"
        analyst = (data.get("analyst_recommendation") or "").replace("_", " ")
        finnhub = data.get("finnhub", {})
        beat_str = ""
        if finnhub.get("available") and finnhub.get("eps_surprises"):
            s = finnhub["eps_surprises"][0]
            if s.get("surprise_pct") is not None:
                direction = "BEAT" if s["beat"] else "MISS"
                beat_str = f"{direction} {s['surprise_pct']:+.1f}%"
        table_row(pdf, [ticker, price_str, score, rev_g, eps_q, analyst, beat_str], widths, shade=(i % 2 == 0))

    pdf.ln(3)

    # ── Top PULSE Candidates ────────────────────────────────────────────────────
    passed = [(t, d) for t, d in stocks.items()
              if not d.get("error") and d.get("canslim", {}).get("pass")]
    passed.sort(key=lambda x: x[1].get("canslim", {}).get("total", 0), reverse=True)

    if passed:
        section(pdf, "Top PULSE Candidates (CANSLIM PASS)")
        for ticker, data in passed[:5]:
            cs = data.get("canslim", {})
            pct_high = data.get("pct_from_52w_high") or 0
            rev = data.get("revenue_growth_yoy") or 0
            eps_q = data.get("eps_q_growth_pct") or 0
            finnhub = data.get("finnhub", {})
            beat_str = ""
            if finnhub.get("available") and finnhub.get("eps_surprises"):
                s = finnhub["eps_surprises"][0]
                if s.get("surprise_pct") is not None:
                    beat_str = f" | EPS: {'BEAT' if s['beat'] else 'MISS'} {s['surprise_pct']:+.1f}%"

            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(18, 6, ticker)
            pdf.set_font("Helvetica", "", 9)
            line = f"CANSLIM {cs['total']}/14 | {pct_high:.1f}% from high | Rev +{rev:.1f}% | EPS Q +{eps_q:.1f}%{beat_str}"
            pdf.cell(0, 6, line, ln=True)
        pdf.ln(2)

    # ── Portfolio State ───────────────────────────────────────────────────────
    section(pdf, "Portfolio State")
    alloc = portfolio.get("allocation", {})
    holdings = [h for h in portfolio.get("holdings", []) if h.get("ticker") != "EXAMPLE"]
    row(pdf, "Stocks", f"{alloc.get('stocks_total_pct', 0)}%", shade=False)
    row(pdf, "Gold", f"{alloc.get('gold_pct', 0)}%", shade=True)
    row(pdf, "Cash", f"{alloc.get('cash_pct', 100)}%", shade=False)
    if not holdings:
        pdf.set_font("Helvetica", "I", 9)
        pdf.cell(0, 6, "  No open positions -- 100% cash", ln=True)
    pdf.ln(2)

    # ── CIO Guidance ──────────────────────────────────────────────────────────
    section(pdf, "CIO Regime Guidance")
    pdf.set_font("Helvetica", "", 9)
    guidance = {
        "Bull": "Allocation: 80-90% stocks | 10% gold | Bias: Add Leader positions | Action: Full PULSE screen",
        "Bear": "Allocation: 30-40% stocks | 40%+ cash | Bias: Defensive | Action: Cut Stage 3/4 immediately",
        "Cautious": "Allocation: 60-70% stocks | 15-20% gold | Bias: Selective | Action: CANSLIM >= 10, RS > 80th pct only",
    }
    pdf.multi_cell(0, 6, f"  {guidance.get(regime, '')}")

    out_path = OUTPUT_DIR / f"daily_brief_{TODAY}.pdf"
    pdf.output(str(out_path))
    return out_path


# ── Telegram sender ───────────────────────────────────────────────────────────
def send_message(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        return False
    r = requests.post(f"{TELE_BASE}/sendMessage", data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }, timeout=15)
    return r.ok


def send_document(pdf_path: Path, caption: str = "") -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    with open(pdf_path, "rb") as f:
        r = requests.post(f"{TELE_BASE}/sendDocument", data={
            "chat_id": CHAT_ID,
            "caption": caption,
            "parse_mode": "HTML",
        }, files={"document": (pdf_path.name, f, "application/pdf")}, timeout=30)
    return r.ok


def build_summary_text(macro: dict, stocks: dict) -> str:
    """Build Telegram message in KSS Alpha Espresso style."""
    regime = macro.get("regime", "?")
    us = macro.get("us_macro", {})
    fx = macro.get("fx_commodities", {})
    thai = macro.get("thai_macro", {})

    regime_emoji = {"Bull": "🟢", "Bear": "🔴", "Cautious": "🟡"}.get(regime, "⚪")
    regime_label = {"Bull": "BULL — Risk On", "Bear": "BEAR — Risk Off", "Cautious": "CAUTIOUS — Mixed"}.get(regime, regime)

    def fv(d, key, fmt=".2f"):
        v = (d.get(key) or {}).get("value")
        return f"{v:{fmt}}" if v is not None else "N/A"

    def fpx(key, fmt=".2f"):
        d = fx.get(key) or {}
        p = d.get("price")
        c = d.get("change_pct_1d")
        if p is None:
            return "N/A"
        return f"{p:{fmt}} ({c:+.2f}%)" if c is not None else f"{p:{fmt}}"

    sp_chg = (fx.get("sp500") or {}).get("change_pct_1d")
    ndx_chg = (fx.get("nasdaq") or {}).get("change_pct_1d")
    set_p = (fx.get("set_index") or {}).get("price")
    set_chg = (fx.get("set_index") or {}).get("change_pct_1d")
    thb = (fx.get("thb_usd") or {}).get("price")
    vix = (us.get("vix") or {}).get("value")
    spread = (us.get("us_yield_spread_10_2") or {}).get("value")
    oil_p = (fx.get("oil_brent") or {}).get("price")
    oil_chg = (fx.get("oil_brent") or {}).get("change_pct_1d")
    gold_p = (fx.get("gold_usd") or {}).get("price")
    bot_rate = (thai.get("bot_policy_rate") or {}).get("value")

    lines = [
        f"🎯 <b>AlphaAbsolute Daily Brief</b>  |  {TODAY_FULL}",
        f"{regime_emoji} Regime: <b>{regime_label}</b>",
        "",
    ]

    # US overnight
    if sp_chg is not None:
        lines.append(f"📌 <b>US Equities</b>: S&amp;P 500 {sp_chg:+.2f}% | Nasdaq {ndx_chg:+.2f}% | VIX {vix:.1f}" if vix else f"S&amp;P {sp_chg:+.2f}% | Nasdaq {ndx_chg:+.2f}%")

    if oil_p:
        lines.append(f"🛢️ <b>Oil</b>: Brent ${oil_p:.2f} ({oil_chg:+.2f}%) | Gold ${gold_p:.0f}" if gold_p else f"Oil: Brent ${oil_p:.2f}")

    if spread is not None:
        lines.append(f"🏦 <b>Rates</b>: 10Y-2Y {spread:+.2f}% | 10Y {fv(us, 'us_10y_yield')}%")

    lines.append("")

    # Thai market
    if set_p:
        thb_str = f"1 USD = {1/thb:.2f} THB" if thb and thb > 0 else ""
        lines.append(f"🇹🇭 <b>SET {set_p:.2f}</b> ({set_chg:+.2f}%) | {thb_str} | BoT {bot_rate}%")
    lines.append("")

    # Top PULSE picks
    passed = [(t, d) for t, d in stocks.items()
              if not d.get("error") and d.get("canslim", {}).get("pass")]
    passed.sort(key=lambda x: x[1].get("canslim", {}).get("total", 0), reverse=True)

    if passed:
        lines.append("🔆 <b>PULSE Candidates (CANSLIM PASS)</b>")
        for ticker, data in passed[:5]:
            cs = data.get("canslim", {})
            rev = data.get("revenue_growth_yoy") or 0
            eps_q = data.get("eps_q_growth_pct") or 0
            finnhub = data.get("finnhub", {})
            beat_str = ""
            if finnhub.get("available") and finnhub.get("eps_surprises"):
                s = finnhub["eps_surprises"][0]
                if s.get("surprise_pct") is not None:
                    beat_str = f" {'BEAT' if s['beat'] else 'MISS'}{s['surprise_pct']:+.1f}%"
            pct_high = data.get("pct_from_52w_high") or 0
            price = data.get("price") or 0
            currency = data.get("currency", "$")
            setup = SETUP_THEMES_TELE.get(ticker, ("", ""))[0]
            lines.append(f"  <b>{ticker}</b> [{setup}] {currency}{price:.1f} | {cs['total']}/14 | Rev+{rev:.0f}% | EPS Q{eps_q:+.0f}%{beat_str}")
        lines.append("")

    # CIO action
    actions = {
        "Bull":     "Full PULSE screen — add Leader / Hypergrowth",
        "Bear":     "Defensive — cut Stage 3/4, raise cash to 40%+",
        "Cautious": "Selective — CANSLIM >= 10, tight stops, RS > 80th",
    }
    lines += [
        f"🎯 <b>CIO Action:</b> {actions.get(regime, '')}",
        "",
        "<i>Full PDF brief attached below</i>",
    ]
    return "\n".join(lines)


SETUP_THEMES_TELE = {
    "NVDA": ("Leader", "AI"), "MU": ("Misprice", "HBM"),
    "PLTR": ("Leader", "Defense"), "AVGO": ("Leader", "AI Infra"),
    "ANET": ("Leader", "AI Infra"), "LITE": ("Hypergrowth", "Photonics"),
    "COHR": ("Hypergrowth", "Photonics"), "RKLB": ("Hypergrowth", "Space"),
    "AXON": ("Leader", "Defense"), "CACI": ("Leader", "Defense"),
    "NNE": ("Hypergrowth", "Nuclear"), "VRT": ("Leader", "AI Infra"),
    "AMD": ("Leader", "AI"), "CRWV": ("Hypergrowth", "NeoCloud"),
    "IONQ": ("Hypergrowth", "Quantum"), "AOT.BK": ("BotFish", "Tourism"),
    "KBANK.BK": ("BotFish", "Banking"),
}


# ── Main ──────────────────────────────────────────────────────────────────────
def notify(macro: dict, stocks: dict, portfolio: dict, test: bool = False) -> bool:
    if test:
        ok = send_message("<b>AlphaAbsolute Bot</b> — test message. Pipeline connected.")
        print("Test message sent." if ok else "Test message FAILED.")
        return ok

    print("Generating PDF...")
    pdf_path = generate_pdf(macro, stocks, portfolio)
    print(f"PDF saved: {pdf_path}")

    print("Sending Telegram summary...")
    summary = build_summary_text(macro, stocks)
    send_message(summary)

    print("Sending PDF document...")
    ok = send_document(pdf_path, caption=f"AlphaAbsolute Daily Brief {TODAY_FULL}")
    print("Telegram notification sent." if ok else "Telegram send FAILED.")
    return ok


def load_latest_json(prefix: str) -> dict:
    files = sorted(DATA_DIR.glob(f"{prefix}_*.json"), reverse=True)
    return json.loads(files[0].read_text(encoding="utf-8")) if files else {}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    if args.test:
        notify({}, {}, {}, test=True)
    else:
        macro = load_latest_json("macro")
        stocks = load_latest_json("stock_data")
        portfolio = load_latest_json("portfolio") if (DATA_DIR / "portfolio.json").exists() else {}
        if (DATA_DIR / "portfolio.json").exists():
            portfolio = json.loads((DATA_DIR / "portfolio.json").read_text(encoding="utf-8"))
        notify(macro, stocks, portfolio)

