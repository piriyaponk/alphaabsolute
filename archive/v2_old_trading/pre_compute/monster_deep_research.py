"""
AlphaAbsolute v2 — Monster Deep Research + Telegram Formatter
=============================================================
Reads motw_selection.json + pipeline data, generates plain-language
"why 10x" explanation via Claude API (Haiku), formats 3-message Telegram thread.

Output: data/bigshot/motw_research_YYMMDD.json
        Sends 3 Telegram messages (if configured)

MSG 1: Setup card — breakout, base, RS, earnings flag
MSG 2: Thesis card — company description, TAM, moat, path to 10x milestones
MSG 3: Risk card  — bear case with specific triggers, regime note

Plain-language philosophy (user spec):
  "อธิบายเปนภาษาคน ว่าทำไม จะ 10 เด้ง"
  Explain WHY 10x in human terms, not financial jargon.
  - No: "Revenue acceleration inflection signal detected"
  - Yes: "บริษัทนี้เพิ่งเริ่มมีรายได้พุ่ง ตอนนี้ยังราคาไม่แพง แต่ถ้าเทรนด์นี้ยาว อาจ x10 ใน 3-5 ปี"

Cost: ~$0.002/run (Claude Haiku: 3 short prompts)
Run: Sundays + whenever monster_of_week_selector.py selects a new candidate
"""

from __future__ import annotations
import json
import os
import sqlite3
import ssl
import sys
import urllib.request
import urllib.parse
from datetime import date, datetime
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# FIX: self-signed cert in corporate proxy — disable SSL verification for all outbound calls
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

ROOT     = Path(__file__).resolve().parents[2]
BIGSHOT  = ROOT / "data" / "bigshot"
OUT_DIR  = ROOT / "data" / "bigshot"
OUT_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT / "scripts" / "utils"))


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


_ENV             = _load_env()
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", _ENV.get("TELEGRAM_BOT_TOKEN", ""))
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID",   _ENV.get("TELEGRAM_CHAT_ID",   ""))
ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY",  _ENV.get("ANTHROPIC_API_KEY",  ""))


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _load_motw() -> dict:
    """Load the selected Monster of the Week."""
    data = _load_json(BIGSHOT / "motw_selection.json")
    return data.get("selected", {})


def _load_fundamentals_db(ticker: str) -> dict:
    """Pull enriched fundamentals from SQLite."""
    db = ROOT / "data" / "ohlcv.db"
    if not db.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db))
        row = conn.execute("""
            SELECT eps_yoy_pct, rev_yoy_pct, gross_margin, gm_trend,
                   rev_inflection_label, rev_yoy_q1, rev_yoy_q2, rev_yoy_q3,
                   eps_yoy_q1, latest_period
            FROM fundamentals_summary
            WHERE ticker = ?
            LIMIT 1
        """, (ticker,)).fetchone()
        # Company name from company_info if available
        name_row = conn.execute(
            "SELECT company_name, sector, description FROM company_info WHERE ticker = ? LIMIT 1",
            (ticker,)
        ).fetchone()
        conn.close()
        result = {}
        if row:
            result.update({
                "eps_yoy_pct":    row[0], "rev_yoy_pct":   row[1],
                "gross_margin":   row[2], "gm_trend":      row[3],
                "rev_inflection": row[4], "rev_yoy_q1":    row[5],
                "rev_yoy_q2":     row[6], "rev_yoy_q3":    row[7],
                "eps_yoy_q1":     row[8], "latest_period": row[9],
            })
        if name_row:
            result.update({
                "company_name": name_row[0] or ticker,
                "sector":       name_row[1] or "",
                "description":  name_row[2] or "",
            })
        return result
    except Exception:
        return {}


def _load_theme_info(ticker: str) -> dict:
    """Get theme name and heat for ticker."""
    data   = _load_json(ROOT / "data" / "rs_universe" / "theme_rs_latest.json")
    themes = data.get("themes", {})
    for tid, tv in themes.items():
        if not isinstance(tv, dict):
            continue
        members = tv.get("members", {})
        if ticker in (members if isinstance(members, dict) else {}):
            return {
                "theme_id":   tid,
                "theme_name": tv.get("name", tid.replace("_", " ")),
                "theme_heat": tv.get("phase", ""),
            }
    return {}


def _load_rs(ticker: str) -> dict:
    data = _load_json(ROOT / "data" / "rs_universe" / "latest.json")
    universe = data.get("universe", {})
    return universe.get(ticker, {})


# ── Claude Haiku call ─────────────────────────────────────────────────────────

def _call_claude_haiku(prompt: str, max_tokens: int = 400) -> str:
    """
    Call Claude Haiku via Anthropic Messages API.
    Falls back to rule-based text if API key not set.
    """
    if not ANTHROPIC_KEY:
        return ""   # handled by fallback generator below

    try:
        payload = json.dumps({
            "model":      "claude-haiku-4-5",
            "max_tokens": max_tokens,
            "messages":   [{"role": "user", "content": prompt}],
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key":         ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            result = json.loads(resp.read().decode())
            content = result.get("content", [])
            if content and isinstance(content, list):
                return content[0].get("text", "").strip()
    except Exception as e:
        print(f"  [WARN] Claude API call failed: {e}")

    return ""


# ── Plain-language generators ─────────────────────────────────────────────────

def _generate_thesis_ai(ticker: str, candidate: dict, fund: dict, theme: dict) -> dict:
    """
    Generate plain-language "why 10x" thesis via Claude Haiku (Lynch/Fisher/Shay persona).
    Returns a dict with keys:
      thesis_text      - plain-language main thesis (str)
      lynch_60s        - 60-second customer explanation (str or None)
      fisher_5yr       - 5-year competitive moat outlook (str or None)
      shay_old_narrative - market's current narrative (str or None)
      shay_new_narrative - new narrative that will re-price the stock (str or None)
      shay_bottleneck  - specific supply constraint this company owns (str or None)
    If API unavailable, falls back to rule-based thesis_text and None for persona fields.
    """
    company_name = fund.get("company_name", ticker)
    theme_name   = theme.get("theme_name", "")
    rev_yoy      = fund.get("rev_yoy_pct") or candidate.get("rev_yoy_resolved")
    eps_yoy      = fund.get("eps_yoy_pct") or candidate.get("eps_yoy_resolved")
    inflection   = fund.get("rev_inflection") or candidate.get("inflection_label_resolved", "")
    description  = fund.get("description", "")
    base_num     = candidate.get("base_num_resolved") or candidate.get("base_number", "")
    entry_note   = candidate.get("entry_note") or candidate.get("bottleneck_note", "")

    def _empty_result(thesis: str) -> dict:
        return {
            "thesis_text":       thesis,
            "lynch_60s":         None,
            "fisher_5yr":        None,
            "shay_old_narrative": None,
            "shay_new_narrative": None,
            "shay_bottleneck":   None,
        }

    prompt = f"""You are AlphaAbsolute's Monster Scout team — three analysts working together.

PETER LYNCH lens: Explain in 60 seconds WHO buys this product and WHY it sells.
Name the actual customer. Use plain language any investor understands. No jargon.

PHILIP FISHER lens: Will this company's competitive position be STRONGER in 5 years?
What does the gross margin trend tell you about pricing power?

SHAY BOLOOR lens: What is the OLD narrative the market currently uses to price this stock?
What is the NEW narrative that will re-price it higher?
Where is the SPECIFIC bottleneck or chokepoint this company owns in its value chain?

Stock: {ticker} ({company_name})
Theme: {theme_name}
Revenue growth: {f'{rev_yoy:.0f}%' if rev_yoy else 'N/A'} YoY | Signal: {inflection}
EPS growth: {f'{eps_yoy:.0f}%' if eps_yoy else 'N/A'} YoY
Base number: {base_num} (early stage = more room to run)
Company description: {description[:200] if description else 'N/A'}
Setup note: {entry_note[:150] if entry_note else 'N/A'}

Return a JSON object with EXACTLY these fields (no markdown, no extra text):
{{
  "thesis_text": "3-4 sentence plain-language thesis explaining why 10x is possible. Mix of English and Thai is OK. Be honest — if data is thin, say early stage, speculative.",
  "lynch_60s": "1-2 sentences: who buys this product and why it sells. Name the actual customer.",
  "fisher_5yr": "1-2 sentences: will competitive position be stronger in 5 years? What does gross margin say about pricing power?",
  "shay_old_narrative": "1 sentence: what the market thinks today about this stock.",
  "shay_new_narrative": "1 sentence: what the market will think in 12-18 months that drives re-pricing.",
  "shay_bottleneck": "1 sentence or null: the specific supply constraint or chokepoint this company owns."
}}"""

    ai_raw = _call_claude_haiku(prompt, max_tokens=500)

    if ai_raw:
        # Try to parse as JSON
        try:
            # Strip any markdown fences if present
            clean = ai_raw.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            parsed = json.loads(clean)
            result = {
                "thesis_text":       parsed.get("thesis_text") or ai_raw,
                "lynch_60s":         parsed.get("lynch_60s") or None,
                "fisher_5yr":        parsed.get("fisher_5yr") or None,
                "shay_old_narrative": parsed.get("shay_old_narrative") or None,
                "shay_new_narrative": parsed.get("shay_new_narrative") or None,
                "shay_bottleneck":   parsed.get("shay_bottleneck") or None,
            }
            return result
        except (json.JSONDecodeError, ValueError):
            # Haiku returned plain text — use as thesis_text, set others to None
            return _empty_result(ai_raw)

    # ── Rule-based fallback (no API) ──────────────────────────────────────────────────
    parts = []

    if company_name and company_name != ticker:
        parts.append(f"*{ticker}* ({company_name})")
    else:
        parts.append(f"*{ticker}*")

    if theme_name:
        parts.append(f"เป็นหุ้นใน theme {theme_name}")

    if rev_yoy and rev_yoy >= 25:
        if inflection in ("ACCELERATING_STRONG", "FIRST_INFLECTION"):
            parts.append(f"รายได้โต {rev_yoy:.0f}% YoY และกำลัง accelerate — สัญญาณว่าธุรกิจกำลัง take off")
        elif inflection == "TURNAROUND":
            parts.append(f"รายได้กลับมาเป็นบวก +{rev_yoy:.0f}% หลังจากขาดทุน — turnaround play")
        else:
            parts.append(f"รายได้โต {rev_yoy:.0f}% YoY")

    if entry_note:
        parts.append(entry_note[:100])

    thesis = " | ".join(parts) if parts else f"{ticker} — Early stage monster scout candidate"
    return _empty_result(thesis)


def _generate_risk_text(ticker: str, candidate: dict, fund: dict, regime: str) -> str:
    """Generate plain-language risk/bear case."""
    inflection   = fund.get("rev_inflection") or candidate.get("inflection_label_resolved", "")
    mkt_cap      = candidate.get("mkt_cap_resolved") or candidate.get("market_cap") or 0
    base_num     = candidate.get("base_num_resolved") or candidate.get("base_number", "")
    stop         = candidate.get("stop", 0)
    pivot        = candidate.get("pivot", 0)
    risk_pct     = abs(pivot - stop) / pivot * 100 if pivot else 10.0

    risks = []

    # Base risk
    try:
        bn = int(base_num) if base_num else 0
        if bn == 0:
            risks.append("Base 0 = ยังไม่มี track record — ออกตัวแรง fail ได้ง่าย")
        elif bn == 1:
            risks.append("Base 1 = เพิ่ง emerge — ยัง thin liquidity ช่วงแรก")
    except (ValueError, TypeError):
        pass

    # Small cap risk
    if mkt_cap and mkt_cap < 300e6:
        risks.append("Market cap น้อย — bid/ask spread กว้าง, stop ต้องเคร่ง")

    # Revenue risk
    if inflection in ("TURNAROUND",):
        risks.append("Turnaround อาจ revert — ต้องดู Q ถัดไปยืนยัน")

    # Regime risk
    if regime in ("Distribution", "Markdown"):
        risks.append(f"Regime {regime} — ห้ามซื้อ WATCH ONLY รอ regime เปลี่ยน")
    elif regime == "Sideways":
        risks.append("Sideways regime — ขนาดเล็ก 2.5% เท่านั้น")

    # Hard stop
    if stop and pivot:
        risks.append(f"Hard stop: EOD close < ${stop:.2f} ({risk_pct:.1f}% จาก entry) — ออกทันที ไม่ถัวะ")

    if not risks:
        risks.append(f"Hard stop: ${stop:.2f} | Regime: {regime}")

    return " | ".join(risks[:4])


# ── Format Telegram messages ──────────────────────────────────────────────────

def format_motw_messages(candidate: dict, fund: dict, theme: dict, regime: str,
                         entry_status: str, score: int) -> list[str]:
    """
    Format 3-message Telegram thread for Monster of the Week.

    MSG 1: Setup — breakout stats, RS, earnings
    MSG 2: Thesis — why 10x in plain language
    MSG 3: Risk  — bear case, stop, regime note
    """
    ticker    = candidate.get("ticker", "?")
    stype     = candidate.get("setup_type", "BKT")
    pivot     = candidate.get("pivot", 0)
    stop      = candidate.get("stop", 0)
    size      = candidate.get("recommended_size_pct", 5)
    grade     = candidate.get("setup_grade", "B")

    rev_yoy   = fund.get("rev_yoy_pct") or candidate.get("rev_yoy_resolved")
    eps_yoy   = fund.get("eps_yoy_pct") or candidate.get("eps_yoy_resolved")
    inflection = (fund.get("rev_inflection") or candidate.get("inflection_label_resolved") or "").replace("_", " ")
    rs_1m     = candidate.get("rs_1m_resolved") or 0
    rs_3m     = candidate.get("rs_3m_resolved") or 0
    mkt_cap   = candidate.get("mkt_cap_resolved") or candidate.get("market_cap") or 0
    base_num  = candidate.get("base_num_resolved") or candidate.get("base_number", "?")
    theme_name = theme.get("theme_name", "")
    theme_heat = theme.get("theme_heat", "")
    trend_str  = candidate.get("trend_str") or ""
    pct_52wh   = candidate.get("pct_from_52w_high") or 0
    is_ath     = candidate.get("is_ath") or candidate.get("at_ath") or False
    is_6m      = candidate.get("is_6m_high") or candidate.get("is_126d_high") or False

    risk_pct  = abs(pivot - stop) / pivot * 100 if pivot else 10.0
    cap_str   = (f"${mkt_cap/1e6:.0f}M" if mkt_cap and mkt_cap < 1e9 else
                 f"${mkt_cap/1e9:.1f}B" if mkt_cap else "")

    bkout_label = "ATH 🏔️" if is_ath else ("6M High 🔝" if is_6m else "3M High")
    theme_emoji = "🔥" if theme_heat == "HOT" else ("🌤" if theme_heat == "WARM" else "")
    action_str  = "✅ ACTIONABLE" if entry_status == "ACTIVE" else "👁 WATCH ONLY"
    grade_e     = "🚀" if grade == "A" else "🌱"
    week_str    = date.today().strftime("W%V %Y")

    # ── MSG 1: Setup ─────────────────────────────────────────────────────────
    setup_lines = [
        f"🎯 *Monster of the Week — {week_str}*",
        f"{grade_e} *{ticker}* | Monster Scout | Grade {grade} | Score {score}/100",
        f"{action_str}",
        "",
        f"📍 *Setup*",
        f"Entry: *${pivot:.2f}* | Stop: ${stop:.2f} ({risk_pct:.1f}% risk) | Size: {size:.0f}%",
        f"Breakout: {bkout_label} | {pct_52wh:+.1f}% from 52Wh",
    ]
    rs_parts = []
    if rs_1m: rs_parts.append(f"1M:{rs_1m:.0f}")
    if rs_3m: rs_parts.append(f"3M:{rs_3m:.0f}")
    if rs_parts:
        setup_lines.append(f"RS: {'/'.join(rs_parts)} | Base {base_num}")
    if theme_name:
        setup_lines.append(f"{theme_emoji} Theme: {theme_name}")
    rev_parts = []
    if inflection:
        rev_parts.append(inflection)
    if trend_str and trend_str != "N/A":
        rev_parts.append(trend_str)
    if rev_parts:
        setup_lines.append(f"📈 Rev: {' | '.join(rev_parts)}")
    if eps_yoy is not None:
        setup_lines.append(f"EPS: {eps_yoy:+.0f}% YoY")
    setup_lines.append(f"⛔ Invalid if EOD close < ${stop:.2f}")

    # ── MSG 2: Thesis ─────────────────────────────────────────────────────────
    thesis_result = _generate_thesis_ai(ticker, candidate, fund, theme)
    thesis_text       = thesis_result.get("thesis_text", "")
    shay_old          = thesis_result.get("shay_old_narrative")
    shay_new          = thesis_result.get("shay_new_narrative")
    shay_bottleneck   = thesis_result.get("shay_bottleneck")
    thesis_lines = [
        f"💡 *{ticker} — ทำไมอาจ 10 เด้ง?*",
        "",
        thesis_text,
        "",
    ]
    # Re-rating section (REQUIRED — BOA-017 DEC-023 A2)
    if shay_old and shay_new:
        thesis_lines.append(f"🔄 Re-rating: {shay_old} → {shay_new}")
    else:
        thesis_lines.append("🔄 Re-rating: [Narrative analysis pending — manual review]")
    if shay_bottleneck:
        thesis_lines.append(f"⛓ Bottleneck: {shay_bottleneck}")
    thesis_lines.append("")
    # Add milestones if we have revenue data
    if rev_yoy and rev_yoy >= 25:
        if mkt_cap and mkt_cap < 1e9:
            thesis_lines.append("📊 *Path to 10x:*")
            thesis_lines.append(f"  ตอนนี้: {cap_str} | ถ้าโตเท่ากัน 3-4 ปี → อาจแตะ $5-10B")
        elif mkt_cap and mkt_cap < 5e9:
            thesis_lines.append("📊 *Path to 10x:*")
            thesis_lines.append(f"  ตอนนี้: {cap_str} | ต้องใช้เวลา 5-7 ปีถ้าเทรนด์ยาว")
    thesis_lines.append(f"_Regime: {regime} | {action_str}_")

    # ── MSG 3: Risk ───────────────────────────────────────────────────────────
    risk_text = _generate_risk_text(ticker, candidate, fund, regime)
    risk_lines = [
        f"⚠️ *{ticker} — ความเสี่ยง*",
        "",
        risk_text,
        "",
        "_Monster Scout = เก็งกำไร ขนาดเล็ก 5% เท่านั้น_",
        "_ถ้า stop hit = ออกทันที ไม่ถัวะ ไม่ถาม_",
    ]

    return [
        "\n".join(setup_lines),
        "\n".join(thesis_lines),
        "\n".join(risk_lines),
    ]


# ── Telegram sender ───────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "Markdown",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            result = json.loads(resp.read().decode())
            return result.get("ok", False)
    except Exception as e:
        print(f"  [ERROR] Telegram: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    today     = date.today().isoformat()
    today_fmt = date.today().strftime("%y%m%d")

    print(f"\n{'='*55}")
    print(f"  Monster Deep Research  [{today}]")
    print(f"{'='*55}")

    # Load selection
    motw_data = _load_json(BIGSHOT / "motw_selection.json")
    candidate = motw_data.get("selected")

    if not candidate:
        print("  [SKIP] No Monster of the Week selected — run monster_of_week_selector.py first")
        return {"date": today, "status": "no_selection"}

    ticker      = candidate.get("ticker", "?")
    regime      = motw_data.get("regime", "Unknown")
    entry_status = motw_data.get("entry_status", "WATCH_ONLY")
    score       = candidate.get("motw_score", 0)

    print(f"  Researching: {ticker} | Score: {score}/100 | {entry_status}")

    # Pull enriched data
    fund  = _load_fundamentals_db(ticker)
    theme = _load_theme_info(ticker)
    rs    = _load_rs(ticker)

    # Merge RS into candidate for display
    for k, v in rs.items():
        if "rs_" in k and candidate.get(k) is None:
            candidate[k] = v

    print(f"  Fund data: rev={fund.get('rev_yoy_pct')} eps={fund.get('eps_yoy_pct')} gm={fund.get('gm_trend')}")
    print(f"  Theme: {theme.get('theme_name','?')} | Heat: {theme.get('theme_heat','?')}")

    # Build Telegram messages
    msgs = format_motw_messages(candidate, fund, theme, regime, entry_status, score)

    # Save research output
    research = {
        "date":         today,
        "ticker":       ticker,
        "regime":       regime,
        "entry_status": entry_status,
        "score":        score,
        "fund":         fund,
        "theme":        theme,
        "telegram_messages": msgs,
        "status":       "ok",
    }
    out_file = OUT_DIR / f"motw_research_{today_fmt}.json"
    out_file.write_text(json.dumps(research, indent=2, default=str), encoding="utf-8")
    print(f"  -> Written: {out_file}")

    # Send Telegram
    sent = 0
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        import time
        # Send divider first
        send_telegram(f"{'─'*30}")
        time.sleep(0.5)
        for i, msg in enumerate(msgs, 1):
            print(f"  Sending MSG {i}/{len(msgs)}...")
            if send_telegram(msg):
                sent += 1
            time.sleep(0.6)
        print(f"  Sent {sent}/{len(msgs)} messages")
    else:
        print("  [SKIP] Telegram not configured — preview:")
        for i, msg in enumerate(msgs, 1):
            print(f"\n  ─── MSG {i} ───────────────────────────────────────")
            for line in msg.split("\n"):
                print(f"  {line}")

    return {
        "date":         today,
        "ticker":       ticker,
        "score":        score,
        "telegram_sent": sent,
        "research_file": str(out_file),
        "status":       "ok",
    }


if __name__ == "__main__":
    run()
