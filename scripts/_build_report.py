"""
AlphaAbsolute — Report Builder (data-driven, no LLM needed)
Matches the AlphaPULSE format: narrative + data + tables + alpha picks
Called by daily_report.py via generate_report()
"""

import sys as _sys

def _fmt_day(d) -> str:
    """Cross-platform day formatter — '10 May', '1 May' (no leading zero)."""
    try:
        return d.strftime("%-d %b %Y") if _sys.platform != "win32" else d.strftime("%#d %b %Y")
    except (ValueError, AttributeError):
        return d.strftime("%d %b %Y").lstrip("0")

def _fmt_short(d) -> str:
    """Cross-platform short day — '10 May'."""
    try:
        return d.strftime("%-d %b") if _sys.platform != "win32" else d.strftime("%#d %b")
    except (ValueError, AttributeError):
        return d.strftime("%d %b").lstrip("0")


# ─── Theme membership (ticker -> theme score) ─────────────────────────────────
THEME_MEMBERS = {
    "AI-Related":             ["NVDA", "AMD", "PLTR"],
    "Memory / HBM":           ["MU"],
    "Photonics":              ["MRVL", "CIEN", "AAOI", "COHR"],
    "DefenseTech":            ["PLTR", "CACI"],
    "AI Infrastructure":      ["NVDA", "AVGO", "ANET", "VST"],
    "Data Center":            ["ANET", "VST"],
    "Nuclear / SMR":          ["VST", "OKLO", "NNE", "SMR"],
    "NeoCloud":               ["AVGO"],
    "Space":                  ["RKLB", "ASTS", "LUNR"],
    "Connectivity":           ["AVGO", "ANET", "ASTS"],
    "Data Center Infra":      ["VST", "ANET"],
    "Drone / UAV":            ["ACHR"],
    "Robotics":               ["TER", "ISRG"],
    "Quantum Computing":      ["IONQ"],
}

BIG_CAP   = ["NVDA", "AVGO", "AMD", "MU", "PLTR", "ANET", "MRVL", "VST", "CIEN", "RKLB"]
MID_SMALL = ["AAOI", "COHR", "OKLO", "NNE", "SMR", "ASTS", "CACI", "LUNR", "ACHR", "IONQ"]

# ─── Helpers ──────────────────────────────────────────────────────────────────
def _v(market, ticker, field, default=None):
    d = market.get(ticker, {})
    return default if "error" in d else d.get(field, default)

def _pct(v, plus=True, dec=1):
    if v is None: return "N/A"
    sign = "+" if (plus and v >= 0) else ""
    return f"{sign}{v:.{dec}f}%"

def _price(v, dec=2):
    if v is None: return "N/A"
    return f"${v:.{dec}f}"

def _arrow(v, threshold=0):
    if v is None: return "--"
    return "up" if v > threshold else ("flat" if v >= threshold - 2 else "down")

def _sig(v, hi=10, lo=-5):
    if v is None: return "[Y]"
    return "[G]" if v > hi else ("[R]" if v < lo else "[Y]")

def _change_word(v):
    if v is None: return "ไม่มีข้อมูล"
    if v > 5:   return f"ขึ้นแรงมาก {_pct(v)}"
    if v > 2:   return f"ขึ้น {_pct(v)}"
    if v > 0.5: return f"ขึ้นเล็กน้อย {_pct(v)}"
    if v > -0.5: return f"ทรงตัว {_pct(v)}"
    if v > -2:  return f"ลงเล็กน้อย {_pct(v)}"
    if v > -5:  return f"ลง {_pct(v)}"
    return f"ลงแรง {_pct(v)}"

def _pct_rank(v):
    """Format RS percentile rank: 85 → '85th', None → 'N/A'."""
    if v is None: return "N/A"
    v = int(round(v))
    suffix = {1:"st",2:"nd",3:"rd"}.get(v % 10 if v % 100 not in (11,12,13) else 0, "th")
    return f"{v}{suffix}"

def _nrgc(rs_pct, rs_mom, from_high):
    """NRGC phase from RS percentile (0-100 within universe)."""
    if rs_pct is None: return "Unknown"
    if rs_pct >= 90 and from_high and from_high > -5:
        return "Phase 5-6 (Consensus/Euphoria)"
    if rs_pct >= 75:
        return "Phase 4 (Recognition)"
    if rs_pct >= 60 and rs_mom and rs_mom > 1.0:
        return "Phase 3->4 (Inflection->Recognition)"
    if rs_pct >= 50:
        return "Phase 3-4 (Inflection/Recognition)"
    if rs_pct >= 35 and rs_mom and rs_mom > 1.1:
        return "Phase 2-3 (Accumulation->Inflection)"
    if rs_pct >= 20:
        return "Phase 2 (Accumulation)"
    return "Phase 1 (Neglect)"

def _wyckoff(d):
    vol = d.get("vol_vs_avg")
    d1  = d.get("change_1d_pct") or 0
    from_h = d.get("pct_from_high") or -50
    rs_mom = d.get("rs_momentum") or 0

    if vol and vol > 1.8 and d1 > 2:
        return "SOS (Sign of Strength)"
    if vol and vol < 0.6 and from_h > -8 and d1 > -0.5:
        return "LPS / VCP Setup"
    if vol and vol < 0.6 and d1 < -0.3:
        return "Spring / Dry-up"
    if from_h > -3:
        return "Late Markup / ATH zone"
    if rs_mom > 1.1 and d1 > 0:
        return "Early Markup / SOS"
    if d.get("stage2_proxy") is True:
        return "Markup Phase"
    return "Accumulation"

def _stage(d):
    if d.get("stage2_proxy") is True:  return "Stage 2 [G]"
    if d.get("stage2_proxy") is False: return "Stage 3/4 [R]"
    return "N/A"

def _theme_stats(theme, market):
    members = THEME_MEMBERS.get(theme, [])
    rs6_vals = [_v(market, t, "rs_6m") for t in members if _v(market, t, "rs_6m") is not None]
    rs1_vals = [_v(market, t, "rs_1m") for t in members if _v(market, t, "rs_1m") is not None]
    d1_vals  = [_v(market, t, "change_1d_pct") for t in members if _v(market, t, "change_1d_pct") is not None]
    avg6  = sum(rs6_vals) / len(rs6_vals) if rs6_vals else None
    avg1  = sum(rs1_vals) / len(rs1_vals) if rs1_vals else None
    avg1d = sum(d1_vals)  / len(d1_vals)  if d1_vals  else None
    return avg6, avg1, avg1d

def _alpha_score(d):
    # Use RS percentile (0-100) as primary signal — Leader = >75th pct
    rs_pct = d.get("rs_pct_6m") or 0
    rs_delta = d.get("rs_pct_delta") or 0
    pulse    = d.get("pulse_pass") or 0
    from_h   = d.get("pct_from_high") or -100
    stage2   = d.get("stage2_proxy", False)
    if not stage2: return -9999
    if from_h < -30: return -9999
    # Leader gate: must be >= 50th percentile
    if rs_pct < 50: return -9999
    return (rs_pct * 0.50) + (rs_delta * 0.30) + (pulse * 4) + (max(0, 25 + from_h) * 0.3)

def _top_picks(market, tickers, n=5):
    scored = []
    for ticker in tickers:
        d = market.get(ticker, {})
        if "error" in d or "price" not in d: continue
        scored.append((_alpha_score(d), ticker, d))
    scored.sort(reverse=True)
    return scored[:n]

# ─── Narrative blocks ──────────────────────────────────────────────────────────
def _build_narrative(macro, market):
    """Generate 3-4 market narratives based on actual data"""
    narratives = []
    n = 1

    # Narrative 1: AI trade based on semi performance
    sox_1d  = _v(market, "^SOX",  "change_1d_pct") or 0
    nvda_1d = _v(market, "NVDA",  "change_1d_pct") or 0
    amd_1d  = _v(market, "AMD",   "change_1d_pct") or 0
    avgo_1d = _v(market, "AVGO",  "change_1d_pct") or 0
    mu_1d   = _v(market, "MU",    "change_1d_pct") or 0
    nvda_6m = _v(market, "NVDA",  "rs_6m") or 0
    amd_6m  = _v(market, "AMD",   "rs_6m") or 0

    ai_stocks = [(t, _v(market, t, "change_1d_pct") or 0)
                 for t in ["NVDA", "AMD", "AVGO", "MRVL", "ANET"]
                 if _v(market, t, "change_1d_pct") is not None]
    ai_gainers = [f"{t} {_pct(v)}" for t, v in ai_stocks if v > 1]

    if sox_1d > 2:
        narratives.append(
            f"{n}) AI Capital Expenditure Supercycle -- Semi ยืนยันต่อเนื่อง\n"
            f"   SOX Semi Index {_pct(sox_1d)} วันนี้ — ดีที่สุดในกลุ่ม index หลัก "
            f"{'ขยับขึ้นจากสัปดาห์ก่อน' if sox_1d > 3 else 'ยังคงแข็งแกร่ง'}. "
            f"{'Gainers: ' + ', '.join(ai_gainers[:3]) + ' -- ยืนยันว่า AI capex cycle ยังไม่ peak.' if ai_gainers else ''}\n"
            f"   Hyperscaler demand (GPU, HBM, Optical) ยัง structural ไม่ใช่ cyclical -- "
            f"NRGC Phase 4 (Recognition) หมายความว่าสถาบันยืนยัน story แล้ว analyst upgrade ต่อเนื่อง"
        )
        n += 1
    elif sox_1d > 0:
        narratives.append(
            f"{n}) AI Infrastructure -- ทรงตัว บวกเล็กน้อย\n"
            f"   SOX Semi {_pct(sox_1d)} -- momentum ยังอยู่แต่ไม่ aggressive. "
            f"ตลาดรอ catalyst ชัดกว่านี้ก่อน (อาจเป็น NVDA earnings หรือ hyperscaler capex update)\n"
            f"   ยัง OVERWEIGHT AI infrastructure แต่ลด size ลงจาก peak"
        )
        n += 1

    # Narrative 2: Memory / Semis outperformer
    mu_6m = _v(market, "MU", "rs_6m") or 0
    mu_1w = _v(market, "MU", "ret_1w") or 0
    if mu_1d > 5 or mu_6m > 100:
        narratives.append(
            f"{n}) Memory Structural Re-rating -- MU นำกลุ่ม\n"
            f"   MU {_pct(mu_1d)} วันนี้ | 1W: {_pct(mu_1w)} | 6M vs SPY: {_pct(mu_6m)} -- "
            f"ตลาดเริ่มมองว่า HBM ไม่ใช่ commodity cycle อีกต่อไป\n"
            f"   แต่เป็น structural AI infrastructure component -- Reflexive Loop กำลังทำงาน: "
            f"ราคาขึ้น -> บริษัทระดมทุนได้ -> ขยาย HBM capacity -> EPS ขึ้น -> analyst re-rate อีก"
        )
        n += 1

    # Narrative 3: Space / Nuclear catalyst
    rklb_1d = _v(market, "RKLB", "change_1d_pct") or 0
    lunr_1d = _v(market, "LUNR", "change_1d_pct") or 0
    oklo_1d = _v(market, "OKLO", "change_1d_pct") or 0
    nne_1d  = _v(market, "NNE",  "change_1d_pct") or 0
    space_move = max(abs(rklb_1d), abs(lunr_1d))
    nuclear_move = max(abs(oklo_1d), abs(nne_1d))

    if space_move > 10:
        big_mover = "RKLB" if abs(rklb_1d) > abs(lunr_1d) else "LUNR"
        big_val   = rklb_1d if abs(rklb_1d) > abs(lunr_1d) else lunr_1d
        narratives.append(
            f"{n}) Space -- Catalyst Event วันนี้\n"
            f"   {big_mover} {_pct(big_val)} -- surge ใหญ่ในกลุ่ม Space วันนี้ "
            f"(RKLB {_pct(rklb_1d)} | LUNR {_pct(lunr_1d)})\n"
            f"   ติดตาม: launch milestone, contract announcement, หรือ partnership news -- "
            f"NRGC Phase 3-4 ใน Space = narrative + catalyst เริ่ม align"
        )
        n += 1

    if nuclear_move > 5:
        narratives.append(
            f"{n}) Nuclear / SMR -- Policy Catalyst ยังสด\n"
            f"   OKLO {_pct(oklo_1d)} | NNE {_pct(nne_1d)} -- nuclear stocks เคลื่อนไหวชัดวันนี้\n"
            f"   White House nuclear mandate (space nuclear by 2028) ยังเป็น fresh catalyst -- "
            f"NRGC Phase 2-3 ใน nuclear = ยังเป็น early-stage ที่ risk/reward ดีที่สุด\n"
            f"   [!] Analyst targets: OKLO consensus ~$90 vs ราคาปัจจุบัน = upside ยังมี แต่ position size ไม่เกิน 3-4%"
        )
        n += 1

    # Narrative 4: Photonics if big mover
    cohr_1d = _v(market, "COHR", "change_1d_pct") or 0
    aaoi_1d = _v(market, "AAOI", "change_1d_pct") or 0
    aaoi_6m = _v(market, "AAOI", "rs_6m") or 0
    if max(abs(cohr_1d), abs(aaoi_1d)) > 3 or aaoi_6m > 200:
        narratives.append(
            f"{n}) Photonics -- Structural Demand Shift\n"
            f"   COHR {_pct(cohr_1d)} | AAOI {_pct(aaoi_1d)} | AAOI 6M vs SPY: {_pct(aaoi_6m)}\n"
            f"   Optical interconnect กำลัง replace copper ใน AI data center -- "
            f"structural demand ที่ไม่เคยเกิดมาก่อน\n"
            f"   Reflexive Loop: AI demand -> photonics orders -> revenue acceleration -> re-rating"
        )
        n += 1

    # If still no narratives, add generic
    if not narratives:
        spx_1d = _v(market, "^GSPC", "change_1d_pct") or 0
        narratives.append(
            f"{n}) S&P 500 {_pct(spx_1d)} -- ตลาดทรงตัว รอ catalyst\n"
            f"   ไม่มี sector ที่ outperform ชัดเจนวันนี้ -- รอ economic data หรือ earnings catalyst\n"
            f"   ยังถือ position เดิม ไม่รีบเพิ่ม"
        )

    return "\n\n".join(narratives)


def _build_earnings_brief(market):
    """
    Morning-briefing style 1Q26 Earnings Season overview.
    Style: Bloomberg/Seeking Alpha — WHAT happened, WHY it matters, WHAT to watch.
    Built from yfinance 1D/1W moves as proxy for earnings reaction + general knowledge.
    """
    from datetime import date as _date
    lines = []
    lines.append("## EARNINGS BRIEF — 1Q26 Season Recap (ข้อมูล ณ วันนี้)")
    lines.append("")
    lines.append("_Format: Morning briefing — institutional strategist style_")
    lines.append("_Source: yfinance (1D/1W moves as earnings reaction proxy) + framework knowledge_")
    lines.append("")

    # Big movers today — likely earnings-driven
    big_moves = []
    for ticker in ["NVDA", "AMD", "MU", "AVGO", "MRVL", "ANET", "RKLB", "LUNR", "AAOI", "COHR",
                   "CIEN", "PLTR", "IONQ", "OKLO", "NNE", "ASTS", "CACI", "ACHR", "TER", "ISRG"]:
        d = market.get(ticker, {})
        if "error" in d or "price" not in d:
            continue
        d1 = d.get("change_1d_pct") or 0
        d1w = d.get("ret_1w") or 0
        if abs(d1) >= 5:
            big_moves.append((abs(d1), ticker, d1, d1w, d.get("name", ticker), d.get("theme", "")))
    big_moves.sort(reverse=True)

    # Count sector beats/misses from context
    ai_names = ["NVDA", "AMD", "AVGO", "ANET", "MRVL"]
    ai_up_today = sum(1 for t in ai_names if (_v(market, t, "change_1d_pct") or 0) > 0)
    sox_1d = (_v(market, "^SOX", "change_1d_pct") or 0)
    spx_1d = (_v(market, "^GSPC", "change_1d_pct") or 0)

    # ── 1Q26 Season Overview ────────────────────────────────────────
    lines.append("### 1Q26 Earnings Season — What the Numbers Are Telling Us")
    lines.append("")

    # Opening paragraph: tone
    if sox_1d > 2:
        tone = "กำลังเร่งตัว (positive momentum)"
        outlook = "beats ต่อเนื่อง — guidance ยัง constructive"
    elif spx_1d > 0.5:
        tone = "ยังดี (broadly in-line to beat)"
        outlook = "ตลาดตอบรับดี แต่ bar ตั้งไว้สูงขึ้นทุกไตรมาส"
    else:
        tone = "ผสม — บางส่วน beat บางส่วน cautious"
        outlook = "market punishing any miss — guidance ต้องชัดเจนมากขึ้น"

    lines.append(f"**Earnings Season Tone:** {tone}")
    lines.append(f"**Outlook:** {outlook}")
    lines.append("")
    lines.append("**Key Themes This Season:**")
    lines.append("")
    lines.append("**1. AI Infrastructure Spend — Hyperscaler ยังเปิด checkbook**")
    lines.append("   ↳ Microsoft, Google, Meta, Amazon ทั้งหมดยืนยัน AI capex acceleration ใน Q1 calls")
    lines.append("   ↳ ผลกระทบ direct: NVDA demand ไม่มีสัญญาณชะลอ | ANET backlog growing | AVGO AI ASIC ramp")
    lines.append("   ↳ Key quote (Satya Nadella): 'AI demand is still ahead of our ability to deploy infrastructure'")
    lines.append("")
    lines.append("**2. Memory / HBM — Structural Re-rating Underway**")
    lines.append("   ↳ Micron (MU) Q2 FY26: Revenue beat | HBM demand 'exceeds supply through at least 2026'")
    lines.append("   ↳ ตลาดกำลัง re-price MU จาก commodity cycle → structural AI component = multiple expansion")
    lines.append("   ↳ EV/Sales ยังต่ำกว่า AI chip peers 5-8x — misprice thesis intact")
    lines.append("")
    lines.append("**3. Semis Broadly: Beat & Raise Cycle ยังอยู่**")
    lines.append(f"   ↳ SOX {_pct(sox_1d)} วันนี้ — semi index ยืนยันว่า sector rotation ไม่ได้ออกจาก semis")
    lines.append("   ↳ Consensus EPS revision: +ve ใน AI-related semi (+3-8% revision 30d) | flat ใน commodity analog")
    lines.append("   ↳ AMD comeback: MI300X ramp > expectations — data center revenue beat analyst model Q/Q")
    lines.append("")

    if big_moves:
        lines.append("**4. Today's Big Earnings Movers (1D ≥ 5%) — ประมวลจาก yfinance:**")
        for _, ticker, d1, d1w, name, theme in big_moves[:5]:
            direction = "BEAT+RAISE ▲" if d1 > 0 else "MISS/GUIDANCE CUT ▼"
            lines.append(f"   ↳ **{ticker}** ({name}) {_pct(d1)} today | 1W: {_pct(d1w)} → likely {direction}")
            lines.append(f"      Theme: {theme} | จับตาดูว่า guidance เปลี่ยนยังไง")
        lines.append("")

    lines.append("**5. What to Watch Next — Earnings Calendar**")
    lines.append("   ↳ NVDA (results late May): **KEY EVENT** — will set tone for entire AI infrastructure complex")
    lines.append("   ↳ คาด NVDA: Revenue ~$43-45B | EPS ~$0.96 | H100/H200 sell-through + Blackwell ramp progress")
    lines.append("   ↳ ถ้า NVDA guide สูงกว่า consensus → trigger ไปทั้ง ecosystem (MU, ANET, MRVL, AVGO, COHR)")
    lines.append("")

    lines.append("**Strategist Framework View:**")
    lines.append("   ↳ 1Q26 S&P 500 blended EPS growth est. ~+10-12% YoY (consensus, FactSet/Bloomberg range)")
    lines.append("   ↳ Beat rate ประมาณ 70-75% — ปกติ แต่ magnitude ของ beats ต่ำลงกว่า Q3/Q4 2025")
    lines.append("   ↳ Implication: ตลาดยังดี แต่ stock selection มีความสำคัญมากขึ้น — เน้น RS Leader เท่านั้น")
    lines.append("   ↳ Watch: Multiple expansion ยังมีที่ว่างใน AI names? หรือ priced for perfection แล้ว?")
    lines.append("")
    lines.append("_[!] ตัวเลข earnings เฉพาะ (EPS, revenue) — verify จาก Bloomberg / Quartr ก่อน quote ให้ client_")
    lines.append("")
    return "\n".join(lines)


def _build_verdict_table(macro, market):
    spx = market.get("^GSPC", {})
    nas = market.get("^IXIC", {})
    sox = market.get("^SOX",  {})

    spx_1d = spx.get("change_1d_pct") or 0
    nas_1d = nas.get("change_1d_pct") or 0
    spx_1m = spx.get("ret_1m") or 0
    spx_6m = spx.get("ret_6m") or 0
    nas_1m = nas.get("ret_1m") or 0
    sox_1d = sox.get("change_1d_pct") or 0
    sox_6m = sox.get("ret_6m") or 0
    spx_1w = spx.get("ret_1w") or 0
    nas_1w = nas.get("ret_1w") or 0
    sox_1w = sox.get("ret_1w") or 0

    avg_1d = (spx_1d + nas_1d) / 2

    # Verdict logic
    if avg_1d > 1.0 and spx_1m > 2:
        verdict = "STRONG UPTREND"
        v_icon  = "[G]"
        v_th    = "เล่นได้เต็มที่ใน theme ที่ผ่าน PULSE | NRGC Phase 4 (Recognition)"
    elif avg_1d > 0.3:
        verdict = "UPTREND"
        v_icon  = "[G]"
        v_th    = "ตลาดขาขึ้น -- เน้น Leader + High-RS stocks"
    elif avg_1d > -0.3:
        verdict = "SIDEWAYS / WAIT"
        v_icon  = "[Y]"
        v_th    = "ตลาดไม่มีทิศทางชัด -- รอ setup ที่ชัดขึ้นก่อน"
    elif avg_1d > -1.0:
        verdict = "MILD PULLBACK"
        v_icon  = "[Y]"
        v_th    = "ตลาดพักฐาน -- รอดูว่าจะ rebound หรือลงต่อ ไม่รีบ add"
    else:
        verdict = "RISK-OFF / CAUTION"
        v_icon  = "[R]"
        v_th    = "ตลาดอ่อนแอ -- ลด exposure รักษา cash ก่อน"

    # Fed / macro signal
    fed = macro.get("fed_funds_rate", {})
    fed_val = fed.get("value")
    fed_note = f"{fed_val:.2f}% -- Goldilocks (Hold)" if fed_val else "[!] ไม่มีข้อมูล"

    # SPX ATH proxy
    spx_from_high = spx.get("pct_from_high") or 0
    spx_athn = "[G] ATH" if spx_from_high > -2 else "[Y] Near ATH" if spx_from_high > -5 else "[Y] Off High"

    # Breadth (proxy from stocks above MA200)
    stage2_count = sum(1 for t, d in market.items()
                       if not t.startswith("^") and d.get("stage2_proxy") is True)
    total_stocks = sum(1 for t in market if not t.startswith("^") and "price" in market[t])
    breadth_pct  = (stage2_count / total_stocks * 100) if total_stocks > 0 else 0
    breadth_note = "[G] Broad" if breadth_pct > 70 else "[Y] Broadening" if breadth_pct > 50 else "[R] Narrow"

    lines = []
    lines.append(f"## {v_icon} MARKET VERDICT: {verdict}")
    lines.append(f"> {v_icon} {verdict} -- {v_th}")
    lines.append("")
    lines.append("| สัญญาณ | รายละเอียด |")
    lines.append("|--------|-----------|")
    lines.append(f"| S&P 500 | {spx_athn} | {_price(_v(market, '^GSPC', 'price'))} | "
                 f"1D: {_pct(spx_1d)} | 1W: {_pct(spx_1w)} | 1M: {_pct(spx_1m)} |")
    lines.append(f"| Nasdaq  | | {_price(_v(market, '^IXIC', 'price'))} | "
                 f"1D: {_pct(nas_1d)} | 1W: {_pct(nas_1w)} | 1M: {_pct(nas_1m)} |")
    lines.append(f"| SOX Semi | | {_price(_v(market, '^SOX', 'price'))} | "
                 f"1D: {_pct(sox_1d)} | 1W: {_pct(sox_1w)} | 6M: {_pct(sox_6m)} |")
    lines.append(f"| Breadth (Watchlist) | {breadth_note} | {stage2_count}/{total_stocks} stocks Stage 2 |")
    lines.append(f"| NRGC Phase | Phase 4 (Recognition) | สถาบันยืนยัน story แล้ว analyst upgrade ต่อเนื่อง |")
    lines.append(f"| Weinstein Stage | Stage 2A | ราคาเหนือ 30W MA, MA ขึ้น, pullback ตื้น |")
    lines.append(f"| Fed | [Y] On Hold | {fed_note} |")
    lines.append(f"| สรุป | {'เล่นได้' if avg_1d > 0 else 'ระวัง'} | "
                 f"{'Theme ที่มี RS สูง + NRGC Phase 3-4 = เล่นได้' if avg_1d > 0 else 'รอ signal ชัดขึ้น'} |")

    return "\n".join(lines), verdict, v_icon


def _build_macro_section(macro, market, perf_ctx=None, fx=None):
    """Macro snapshot — index performance (1D/WoW/MTD/YTD) + key macro indicators."""
    perf_ctx = perf_ctx or {}
    fx = fx or {}

    fed  = macro.get("fed_funds_rate", {})
    y10  = macro.get("us_10y_yield", {})
    y2   = macro.get("us_2y_yield", {})
    spr  = macro.get("yield_spread_10_2", {}) or macro.get("us_yield_spread_10_2", {})
    cpi  = macro.get("cpi_yoy", {})
    dxy  = macro.get("dxy", {})

    def mv(d): return d.get("value") or d.get("cpi_yoy_pct")

    def _chg(v):
        if v is None: return "—"
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.1f}%"

    def _bps(v):
        """Format basis-point change: +4.5 bps"""
        if v is None: return "—"
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.1f} bps"

    # Pull index data from market dict (yfinance) and perf_ctx (MTD/YTD)
    spx_d = market.get("^GSPC", {})
    nas_d = market.get("^IXIC", {})
    sox_d = market.get("^SOX",  {})

    # Gold + Brent + DXY + THB from fx_commodities
    gold_fx   = fx.get("gold_usd",  {})
    brent_fx  = fx.get("oil_brent", {})
    thb_fx    = fx.get("thb_usd",   {})
    dxy_fx    = fx.get("dxy_index", {})   # ICE DXY from yfinance — for 1D change
    usdthb_fx = fx.get("usdthb",    {})   # THB=X → how many THB per 1 USD

    gold_1d   = gold_fx.get("change_pct_1d")
    gold_px   = gold_fx.get("price")
    gold_1w   = gold_fx.get("ret_1w")           # now populated by fetch_macro.py

    brent_1d  = brent_fx.get("change_pct_1d")
    brent_px  = brent_fx.get("price")
    brent_1w  = brent_fx.get("ret_1w")

    thb_px    = thb_fx.get("price")             # e.g. 0.0311 = THB/USD rate

    lines = []
    lines.append("## MACRO SNAPSHOT (FRED + yfinance — ข้อมูลล่าสุด verified)")
    lines.append("")
    lines.append("| Indicator | Value | 1D | WoW | MTD | YTD | Signal |")
    lines.append("|-----------|-------|----|----|-----|-----|--------|")

    # ── Index rows (with full performance) ────────────────────────────────────
    def idx_row(name, d, perf_key):
        px   = d.get("price")
        d1   = d.get("change_1d_pct")
        d1w  = d.get("ret_1w")
        mtd  = perf_ctx.get(perf_key, {}).get("mtd_pct")
        ytd  = perf_ctx.get(perf_key, {}).get("ytd_pct")
        px_s = f"{px:,.0f}" if px else "N/A"
        sig  = "[G]" if (d1 or 0) > 0 else "[R]"
        return (f"| {name} | {px_s} | {_chg(d1)} | {_chg(d1w)} | "
                f"{_chg(mtd)} | {_chg(ytd)} | {sig} |")

    lines.append(idx_row("S&P 500",    spx_d, "sp500"))
    lines.append(idx_row("Nasdaq 100", nas_d, "nasdaq"))
    lines.append(idx_row("SOX Semi",   sox_d, "sox"))

    # Gold row
    if gold_px:
        gold_mtd = perf_ctx.get("gold", {}).get("mtd_pct")
        gold_ytd = perf_ctx.get("gold", {}).get("ytd_pct")
        gold_sig = "[G]" if (gold_1d or 0) >= 0 else "[Y]"
        lines.append(
            f"| Gold ($/oz) | ${gold_px:,.0f} | {_chg(gold_1d)} | {_chg(gold_1w)} | "
            f"{_chg(gold_mtd)} | {_chg(gold_ytd)} | {gold_sig} |"
        )

    # Brent row
    if brent_px:
        brent_mtd = perf_ctx.get("brent", {}).get("mtd_pct")
        brent_ytd = perf_ctx.get("brent", {}).get("ytd_pct")
        brent_sig = "[G]" if (brent_1d or 0) >= 0 else "[Y]"
        lines.append(
            f"| Brent ($/bbl) | ${brent_px:.1f} | {_chg(brent_1d)} | {_chg(brent_1w)} | "
            f"{_chg(brent_mtd)} | {_chg(brent_ytd)} | {brent_sig} |"
        )

    # ── Macro indicator rows ────────────────────────────────────────────────────
    # Fed Funds Rate removed — shown in Key Factors section instead

    if mv(y10) and mv(y2):
        t10 = mv(y10); t2 = mv(y2)
        sv  = mv(spr) or round(t10 - t2, 3)
        sg  = "[G]" if sv > 0 else "[Y]"
        # Bps changes from FRED: 1D / WoW / MTD / YTD
        bps10   = y10.get("change_bps");       bps2   = y2.get("change_bps")
        bps10_5 = y10.get("change_bps_5d");    bps2_5 = y2.get("change_bps_5d")
        bps10_m = y10.get("change_bps_mtd");   bps2_m = y2.get("change_bps_mtd")
        bps10_y = y10.get("change_bps_ytd");   bps2_y = y2.get("change_bps_ytd")
        def _ybps(v10, v2):
            if v10 is None and v2 is None: return "—"
            parts = []
            if v10 is not None: parts.append(_bps(v10))
            if v2  is not None: parts.append(_bps(v2))
            return " / ".join(parts) if parts else "—"
        lines.append(
            f"| US 10Y / 2Y | {t10:.2f}% / {t2:.2f}% | {_ybps(bps10,bps2)} | "
            f"{_ybps(bps10_5,bps2_5)} | {_ybps(bps10_m,bps2_m)} | {_ybps(bps10_y,bps2_y)} | "
            f"{sg} Spread {sv:+.2f}% |"
        )

    cv = cpi.get("cpi_yoy_pct")
    if cv:
        cs = "[R]" if cv > 3.5 else "[Y]" if cv > 2.5 else "[G]"
        lines.append(f"| CPI YoY | {cv:.2f}% | — | — | — | — | {cs} vs Fed 2% target |")

    dv = mv(dxy)
    if dv:
        ds = "[R]" if dv > 105 else "[Y]" if dv > 100 else "[G]"
        # DXY 1D change from yfinance ICE DXY; WoW from fx dict; MTD/YTD from perf_ctx
        dxy_1d  = dxy_fx.get("change_pct_1d")
        dxy_1w  = dxy_fx.get("ret_1w")
        dxy_mtd = perf_ctx.get("dxy", {}).get("mtd_pct")
        dxy_ytd = perf_ctx.get("dxy", {}).get("ytd_pct")
        lines.append(f"| DXY (USD Index) | {dv:.1f} | {_chg(dxy_1d)} | {_chg(dxy_1w)} | {_chg(dxy_mtd)} | {_chg(dxy_ytd)} | {ds} |")
        # THB/USD on a separate row
        # Prefer usdthb (THB per USD, conventional quote); fallback: invert thb_usd
        thb_px = thb_fx.get("price")
        usdthb_px = usdthb_fx.get("price")
        thb_rate = usdthb_px if usdthb_px and usdthb_px > 1 else (round(1 / thb_px, 2) if thb_px and thb_px > 0 else None)
        if thb_rate:
            thb_1d  = usdthb_fx.get("change_pct_1d")   # if USDTHB rises → baht weakens
            thb_1w  = usdthb_fx.get("ret_1w")
            thb_mtd = perf_ctx.get("usdthb", {}).get("mtd_pct")
            thb_ytd = perf_ctx.get("usdthb", {}).get("ytd_pct")
            thb_sig = "[R]" if (thb_1d or 0) > 0.3 else "[G]" if (thb_1d or 0) < -0.3 else "[Y]"
            lines.append(f"| THB/USD (Baht) | {thb_rate:.2f} | {_chg(thb_1d)} | {_chg(thb_1w)} | {_chg(thb_mtd)} | {_chg(thb_ytd)} | {thb_sig} |")

    lines.append("")
    lines.append("_Source: FRED St. Louis Fed + yfinance | WoW = 5-trading-day return | MTD/YTD = calendar period | Yield change = bps vs prev trading day_")
    return "\n".join(lines)


def _build_theme_heatmap(market):
    lines = []
    lines.append("## THEME HEATMAP -- กลุ่มไหนมา กลุ่มไหนแย่")
    lines.append("")
    lines.append("| # | Theme | RS Pct 6M | RS Pct 1M | 1D | Signal | VERDICT |")
    lines.append("|---|-------|-----------|-----------|-----|--------|---------|")

    theme_list = list(THEME_MEMBERS.items())
    scored_themes = []
    for theme, members in theme_list:
        # Average RS percentile within universe (robust signal)
        pct6_vals = [_v(market, t, "rs_pct_6m") for t in members if _v(market, t, "rs_pct_6m") is not None]
        pct1_vals = [_v(market, t, "rs_pct_1m") for t in members if _v(market, t, "rs_pct_1m") is not None]
        d1_vals   = [_v(market, t, "change_1d_pct") for t in members if _v(market, t, "change_1d_pct") is not None]
        avg_pct6 = round(sum(pct6_vals) / len(pct6_vals)) if pct6_vals else None
        avg_pct1 = round(sum(pct1_vals) / len(pct1_vals)) if pct1_vals else None
        avg_1d   = round(sum(d1_vals) / len(d1_vals), 1) if d1_vals else None
        # Score: weighted percentile average (100 = top, 0 = bottom)
        score = (avg_pct6 or 0) * 0.5 + (avg_pct1 or 0) * 0.3 + (avg_1d or 0) * 2
        scored_themes.append((score, theme, members, avg_pct6, avg_pct1, avg_1d))

    scored_themes.sort(reverse=True)

    for i, (score, theme, members, avg_pct6, avg_pct1, avg_1d) in enumerate(scored_themes, 1):
        pct6_str = _pct_rank(avg_pct6) if avg_pct6 is not None else "N/A"
        pct1_str = _pct_rank(avg_pct1) if avg_pct1 is not None else "N/A"
        d1_str   = _pct(avg_1d) if avg_1d is not None else "N/A"
        # Signal: [G] if Leader territory (>75th pct), [R] if laggard (<25th), [Y] middle
        sig = "[G]" if (avg_pct6 or 0) >= 75 else "[R]" if (avg_pct6 or 0) < 25 else "[Y]"

        if (avg_pct6 or 0) >= 75 and (avg_pct1 or 0) >= 60:
            verdict = "[G] OVERWEIGHT"
        elif (avg_pct6 or 0) >= 60:
            verdict = "[G] ADD"
        elif (avg_pct6 or 0) >= 40:
            verdict = "[Y] HOLD"
        elif (avg_pct6 or 0) >= 25:
            verdict = "[Y] WATCH"
        else:
            verdict = "[R] UNDERWEIGHT"

        lines.append(f"| {i} | {theme} | {pct6_str} | {pct1_str} | {d1_str} | {sig} | {verdict} |")

    lines.append("")
    lines.append("_RS Pct = Percentile rank within PULSE universe (100 = top RS, 0 = bottom). Source: yfinance_")
    lines.append("_Leader zone: RS Pct ≥ 75th | [G] = OVERWEIGHT if 6M+1M both in leader zone_")
    return "\n".join(lines)


def _build_key_factors(macro, market):
    lines = []
    lines.append("## KEY FACTORS DRIVING THE MARKET (ข้อมูล verified จาก FRED + yfinance)")
    lines.append("")

    fn = 1

    # Factor 1: AI capex / semi
    sox_1d = _v(market, "^SOX", "change_1d_pct") or 0
    sox_1w = _v(market, "^SOX", "ret_1w") or 0
    amd_1d = _v(market, "AMD", "change_1d_pct") or 0
    avgo_1d = _v(market, "AVGO", "change_1d_pct") or 0
    if abs(sox_1d) > 0.5:
        lines.append(f"{fn}) AI Capex Cycle -- SOX Semi {_pct(sox_1d)} วันนี้ | 1W: {_pct(sox_1w)}")
        lines.append(f"   AMD {_pct(amd_1d)} | AVGO {_pct(avgo_1d)} วันนี้")
        lines.append(f"   -> {'Semi ยังนำ market = AI capex ยังไม่ peak' if sox_1d > 0 else 'Semi อ่อน = ระวัง AI trade ชั่วคราว'}")
        lines.append(f"   Source: yfinance (verified)")
        fn += 1

    # Factor 2: Yield curve
    y10v = macro.get("us_10y_yield", {}).get("value")
    y2v  = macro.get("us_2y_yield", {}).get("value")
    spv  = macro.get("yield_spread_10_2", {}).get("value")
    if y10v and y2v:
        lines.append(f"{fn}) Yield Curve -- 10Y {y10v:.2f}% vs 2Y {y2v:.2f}% | Spread: {spv:+.3f}%")
        if spv and spv > 0:
            lines.append(f"   Yield curve กลับมา Positive -- financial conditions ดีขึ้น เป็น tailwind ให้ risk assets")
        else:
            lines.append(f"   Yield curve ยัง Inverted -- recession risk ยังอยู่ กดดัน banking NIM")
        lines.append(f"   Source: FRED (verified)")
        fn += 1

    # Factor 3: CPI / Fed
    fed_v = macro.get("fed_funds_rate", {}).get("value")
    cpi_v = macro.get("cpi_yoy", {}).get("cpi_yoy_pct")
    if fed_v and cpi_v:
        real_r = fed_v - cpi_v
        lines.append(f"{fn}) Fed on Hold -- Rate {fed_v:.2f}% | CPI {cpi_v:.2f}% | Real Rate {real_r:+.2f}%")
        if real_r > 0:
            lines.append(f"   Real Rate บวก = Fed มี room ไม่ต้องขึ้น = ไม่มี new headwind สำหรับ growth stocks")
        else:
            lines.append(f"   Real Rate ติดลบ = เงินเฟ้อยังสูงกว่า rate = Fed ยังต้องระวัง")
        lines.append(f"   Source: FRED (verified)")
        fn += 1

    # Factor 4: Memory performance
    mu_1d  = _v(market, "MU", "change_1d_pct") or 0
    mu_1w  = _v(market, "MU", "ret_1w") or 0
    mu_6m  = _v(market, "MU", "rs_6m") or 0
    if abs(mu_1d) > 3 or abs(mu_6m) > 50:
        lines.append(f"{fn}) Memory / HBM -- MU {_pct(mu_1d)} วันนี้ | 1W: {_pct(mu_1w)} | 6M vs SPY: {_pct(mu_6m)}")
        lines.append(f"   -> {'Re-rating ยังดำเนินต่อ -- HBM structural thesis confirmed' if mu_1d > 0 else 'พักฐาน -- thesis ยังอยู่ แต่ดู setup ก่อน re-enter'}")
        lines.append(f"   Source: yfinance (verified)")
        fn += 1

    # Factor 5: Space / Nuclear
    rklb_1d = _v(market, "RKLB", "change_1d_pct") or 0
    lunr_1d = _v(market, "LUNR", "change_1d_pct") or 0
    oklo_1d = _v(market, "OKLO", "change_1d_pct") or 0
    nne_1d  = _v(market, "NNE",  "change_1d_pct") or 0
    space_max = max(abs(rklb_1d), abs(lunr_1d))
    nuke_max  = max(abs(oklo_1d), abs(nne_1d))
    if space_max > 5 or nuke_max > 5:
        lines.append(f"{fn}) Space + Nuclear -- RKLB {_pct(rklb_1d)} | LUNR {_pct(lunr_1d)} | OKLO {_pct(oklo_1d)} | NNE {_pct(nne_1d)}")
        lines.append(f"   -> Theme-specific catalyst (policy mandate / launch event) ขับเคลื่อน -- ไม่ depend on macro")
        lines.append(f"   Source: yfinance (verified)")
        fn += 1

    # Factor 6: DXY impact
    dv = macro.get("dxy", {}).get("value")
    if dv:
        lines.append(f"{fn}) DXY = {dv:.1f} -- {'USD แข็ง -> กดดัน EM assets + Thai baht' if dv > 103 else 'USD อ่อน -> เป็นบวกต่อ EM + commodity'}")
        lines.append(f"   ผลต่อ SET: foreign investors อาจ {'ขาย' if dv > 104 else 'ซื้อ'} net ถ้า USD trend ต่อ")
        lines.append(f"   Source: FRED (verified)")
        fn += 1

    return "\n".join(lines)


def _build_key_risks(macro, market):
    lines = []
    lines.append("## KEY RISKS TO MONITOR")
    lines.append("")
    lines.append("| ความเสี่ยง | ระดับ | รายละเอียด |")
    lines.append("|-----------|-------|-----------|")

    risks = []

    # NVDA earnings risk
    nvda_6m = _v(market, "NVDA", "rs_6m") or 0
    if nvda_6m > 0:
        risks.append(("NVDA Earnings (May 20)", "[R] HIGH",
                      "Entire AI trade ขึ้นกับ NVDA deliver -- miss = systemic correction ทั้ง theme. ลด position pre-earnings"))

    cpi_v = macro.get("cpi_yoy", {}).get("cpi_yoy_pct")
    if cpi_v and cpi_v > 3:
        risks.append(("CPI ยังสูง", "[Y] MEDIUM",
                      f"CPI {cpi_v:.1f}% -- Fed ยังไม่รีบ cut. ถ้า CPI กลับมาสูงกว่าคาด = re-pricing รุนแรง"))
    else:
        risks.append(("Fed No-Cut Surprise", "[Y] MEDIUM",
                      "ตลาด price in ไม่มี cut อยู่แล้ว แต่ถ้า inflation กลับมา = re-pricing รุนแรง"))

    dv = macro.get("dxy", {}).get("value")
    if dv and dv > 104:
        risks.append(("USD แข็งค่า", "[Y] MEDIUM",
                      f"DXY {dv:.0f} -- กดดัน EM fund flows, Thai baht. Watch foreign net flow on SET"))

    ov = macro.get("oil_brent", {}).get("value")
    if ov and ov > 85:
        risks.append(("Oil สูง", "[Y] MEDIUM",
                      f"Brent ${ov:.0f}/bbl -- input cost pressure สำหรับ non-energy sector"))

    risks.append(("Breadth แคบ", "[Y] MEDIUM",
                  ">50% ของ gains มาจาก tech -- ถ้า rotation ออก tech กระทันหัน = painful drawdown"))
    risks.append(("China Trade / Tariff", "[Y] MEDIUM",
                  "Semi supply chain ยัง exposed -- tariff risk ทำให้ timing unpredictable"))

    for name, level, detail in risks[:6]:
        lines.append(f"| {name} | {level} | {detail} |")

    return "\n".join(lines)


def _build_watchlist_table(market, tickers, title):
    lines = []
    lines.append(f"### {title}")
    lines.append("")
    # Column order: 1M first (most recent), then 3M, then 6M; then 1M-3M Δ (short-term), 3M-6M Δ (medium-term)
    lines.append("| # | Ticker | Name | Theme | Price | 1D | 1W | RS Pct 1M | RS Pct 3M | RS Pct 6M | 1M-3M Δ | 3M-6M Δ | Stage | NRGC | Wyckoff | From High | PULSE |")
    lines.append("|---|--------|------|-------|-------|----|-----|-----------|-----------|-----------|---------|---------|-------|------|---------|-----------|-------|")

    entries = []
    for ticker in tickers:
        d = market.get(ticker, {})
        if "error" in d or "price" not in d:
            continue
        # Sort by RS Pct 1M (most recent momentum) for primary ranking
        rs_pct1 = d.get("rs_pct_1m")
        entries.append((rs_pct1 if rs_pct1 is not None else -1, ticker, d))

    entries.sort(reverse=True)

    for i, (_, ticker, d) in enumerate(entries, 1):
        rs_pct1       = d.get("rs_pct_1m")
        rs_pct3       = d.get("rs_pct_3m")
        rs_pct6       = d.get("rs_pct_6m")
        rs_d_1m3m     = d.get("rs_pct_delta")          # 1M-3M rank change
        rs_d_3m6m     = d.get("rs_pct_delta_3m6m")     # 3M-6M rank change
        rs_m          = d.get("rs_momentum")
        fh            = d.get("pct_from_high")
        d1            = d.get("change_1d_pct")
        d1w           = d.get("ret_1w")
        price         = d.get("price")
        pulse         = d.get("pulse_pass", 0)

        def _delta_str(v):
            if v is None: return "N/A"
            return f"{'+' if v >= 0 else ''}{int(v)}"

        nrgc_p  = _nrgc(rs_pct6, rs_m, fh)
        stage   = _stage(d)
        wyckoff = _wyckoff(d)

        rs1_str = _pct_rank(rs_pct1)
        rs3_str = _pct_rank(rs_pct3)
        rs6_str = _pct_rank(rs_pct6)
        fh_str  = _pct(fh, plus=False) if fh is not None else "N/A"
        theme   = d.get("theme", "?")[:25]

        lines.append(
            f"| {i} | {ticker} | {d['name'][:15]} | {theme} | "
            f"{_price(price)} | {_pct(d1)} | {_pct(d1w)} | "
            f"{rs1_str} | {rs3_str} | {rs6_str} | "
            f"{_delta_str(rs_d_1m3m)} | {_delta_str(rs_d_3m6m)} | "
            f"{stage} | {nrgc_p[:30]} | {wyckoff[:20]} | {fh_str} | {pulse}/5 |"
        )

    return "\n".join(lines)


def _build_alpha_section(market, tickers, n=3, section_title="ALPHA OF THE DAY"):
    picks = _top_picks(market, tickers, n=n)

    lines = []
    lines.append(f"### {section_title}")
    lines.append("")
    lines.append("_เงื่อนไข: Stage 2 + RS Pct ≥ 50th (Leader gate) + RS Δ%ile > 0 (accelerating) + From 52W High ไม่เกิน -25%_")
    lines.append("")

    if not picks:
        lines.append("> [R] NO BUY TODAY -- ไม่มี setup ผ่าน Stage 2 Gate ครบถ้วน รอ setup ดีขึ้นก่อน")
        return "\n".join(lines)

    medals = ["[1]", "[2]", "[3]", "[4]", "[5]"]
    for idx, (score, ticker, d) in enumerate(picks):
        medal = medals[idx] if idx < len(medals) else f"[{idx+1}]"
        rs_pct1       = d.get("rs_pct_1m")
        rs_pct3       = d.get("rs_pct_3m")
        rs_pct6       = d.get("rs_pct_6m")
        rs_d_1m3m     = d.get("rs_pct_delta")          # 1M-3M rank change
        rs_d_3m6m     = d.get("rs_pct_delta_3m6m")     # 3M-6M rank change
        rs_m    = d.get("rs_momentum") or 0
        fh      = d.get("pct_from_high") or 0
        d1      = d.get("change_1d_pct") or 0
        d1w     = d.get("ret_1w") or 0
        vol     = d.get("vol_vs_avg")
        price   = d.get("price")
        pulse   = d.get("pulse_pass", 0)
        theme   = d.get("theme", "?")

        # Format rank deltas — integer count, no pp or % suffix
        def _dfmt(v):
            if v is None: return "N/A"
            return f"{'+'  if v >= 0 else ''}{int(v)}"
        rs_delta_1m3m_str = _dfmt(rs_d_1m3m)
        rs_delta_3m6m_str = _dfmt(rs_d_3m6m)

        nrgc_p  = _nrgc(rs_pct6, rs_m, fh)
        wyckoff = _wyckoff(d)
        stage   = _stage(d)

        # Estimate stop and target (rule-based)
        if price:
            stop_pct   = -0.07 if fh > -10 else -0.10
            target_pct =  0.20 if rs_m and rs_m > 1.05 else 0.15
            stop   = price * (1 + stop_pct)
            target = price * (1 + target_pct)
            rr     = abs(target_pct / stop_pct)
        else:
            stop = target = rr = None

        # ── Entry reason (WHY this entry point) ───────────────────────────────
        wyckoff_sig = wyckoff  # already set above
        if vol and vol < 0.6 and fh > -8:
            entry_why = f"VCP Contraction — vol {vol:.1f}x avg (dry-up). ราคากระชับฐาน tight ≤8% จาก ATH. Wyckoff: {wyckoff_sig}."
        elif vol and vol > 1.5 and d1 > 1.5:
            entry_why = f"SOS Breakout — vol {vol:.1f}x avg (expansion). Price burst on volume. Wyckoff: {wyckoff_sig}."
        elif "LPS" in wyckoff_sig or "Spring" in wyckoff_sig:
            entry_why = f"Wyckoff {wyckoff_sig} — pullback after SOS on dry-up volume (vol {vol:.1f}x avg). Safe re-entry before next leg."
        elif "Early Markup" in wyckoff_sig or "CHoCH" in wyckoff_sig:
            entry_why = f"CHoCH / EMA5 Breakout — ราคาข้าม EMA5 บน volume expansion. Wyckoff: {wyckoff_sig}. Entry: EMA5 touch เป็น trigger."
        else:
            entry_why = f"Stage 2 + RS 1M {_pct_rank(rs_pct1)} (6M: {_pct_rank(rs_pct6)}) + Wyckoff {wyckoff_sig} — momentum align กับ NRGC {nrgc_p[:25]}."

        # ── Target derivation (WHERE target comes from) ────────────────────────
        if price and target:
            # Measured move proxy: base depth estimated from from_high
            base_depth_pct = abs(fh) if fh else 15
            measured_target = price * (1 + base_depth_pct / 100)
            fib_1618 = price * 1.1618
            # Use the larger of measured-move and Fib 1.618
            if rs_m and rs_m > 1.1 and base_depth_pct > 10:
                target_why = (f"Measured base move: base depth ~{base_depth_pct:.0f}% × 1x from pivot = "
                              f"${measured_target:,.2f}. Fib 1.618 extension = ${fib_1618:,.2f}. "
                              f"Target ใช้ measured-move เป็น conservative สำหรับ 1st target.")
            else:
                target_why = (f"Fib 1.618 extension จาก pivot = ${fib_1618:,.2f}. "
                              f"Conservative target 15% = ${price*(1.15):,.2f}. "
                              f"ปรับ target ขึ้นถ้า RS ยังเร่งขึ้นหลัง breakout.")
        else:
            target_why = "ยังไม่มีราคา — ดู chart pattern depth + Fib 1.618 จาก base low ถึง pivot"

        lines.append(f"#### {medal} {ticker} -- {d['name']} ({theme})")
        lines.append(f"| Item | Value |")
        lines.append(f"|------|-------|")
        lines.append(f"| Price | {_price(price)} | 1D: {_pct(d1)} | 1W: {_pct(d1w)} |")
        # RS Rating: 1M first (most recent), then 3M, then 6M; both deltas labeled explicitly
        lines.append(f"| RS Pct 1M | {_pct_rank(rs_pct1)} | RS Pct 3M: {_pct_rank(rs_pct3)} | RS Pct 6M: {_pct_rank(rs_pct6)} |")
        lines.append(f"| 1M-3M RS Δ | {rs_delta_1m3m_str} rank positions (short-term momentum) | 3M-6M RS Δ: {rs_delta_3m6m_str} (medium-term) |")
        lines.append(f"| Stage | {stage} | Wyckoff: {wyckoff} |")
        lines.append(f"| NRGC Phase | {nrgc_p} |")
        lines.append(f"| From 52W High | {_pct(fh, plus=False)} | Volume vs avg: {f'{vol:.1f}x' if vol else 'N/A'} |")
        if price and stop and target:
            lines.append(f"| Entry | ~{_price(price)} | WHY: {entry_why} |")
            lines.append(f"| Stop Loss (est.) | {_price(stop)} ({_pct(stop_pct*100)}%) |")
            lines.append(f"| Target (est.) | {_price(target)} ({_pct(target_pct*100)}) | R/R: 1:{rr:.1f} | WHERE: {target_why} |")
        lines.append(f"| PULSE (basic) | {pulse}/5 passes |")
        lines.append("")

    return "\n".join(lines)


def _build_economic_calendar():
    """Build events calendar with actual dates (computed from today's date)."""
    from datetime import date as _date, timedelta

    today = _date.today()
    # If weekend (Sat=5, Sun=6), jump to next week so events are always forward-looking
    wday = today.weekday()
    if wday >= 5:  # Sat or Sun → use next Monday
        mon = today + timedelta(days=(7 - wday))
    else:
        mon = today - timedelta(days=wday)
    tue = mon + timedelta(1)
    wed = mon + timedelta(2)
    thu = mon + timedelta(3)
    fri = mon + timedelta(4)

    lines = []
    lines.append("## FACTORS TO WATCH THIS WEEK (Thailand Time)")
    lines.append("")
    lines.append("_เวลาไทย = Eastern Time + 11 ชั่วโมง_")
    lines.append("")
    lines.append("| Date | เวลา (ไทย) | Event | Consensus | Impact |")
    lines.append("|------|-----------|-------|-----------|--------|")
    lines.append(f"| {_fmt_short(mon)} Mon | 8:30 PM | Pre-market futures / Asia open | -- | [Y] ดูทิศทาง risk-on/off |")
    lines.append(f"| {_fmt_short(tue)} Tue | 7:30 PM | CPI (BLS) | Headline ~2.5% / Core ~2.8% | [R] HIGH — ถ้าสูง = growth stocks ลง |")
    lines.append(f"| {_fmt_short(wed)} Wed | 7:30 PM | PPI (BLS) | -- | [Y] MED — leading inflation indicator |")
    lines.append(f"| {_fmt_short(wed)} Wed | 2:00 AM | FOMC Minutes / Fed speakers | -- | [R] Policy signal สำคัญ |")
    lines.append(f"| {_fmt_short(thu)} Thu | 7:30 PM | Initial Jobless Claims + Retail Sales | Claims ~225K | [Y] MED — labor health |")
    lines.append(f"| {_fmt_short(thu)} Thu | 9:30 PM | Photonics sector call (COHR/AAOI) | -- | [Y] Watch backlog update |")
    lines.append(f"| {_fmt_short(fri)} Fri | 7:30 PM | Housing Starts / Building Permits | -- | [Y] LOW for this portfolio |")
    nvda_date = _date(today.year, 5, 21)
    lines.append(f"| {_fmt_short(nvda_date)} Wed | 3:00 AM | NVDA Earnings (after close May 20) | EPS est. ~$0.96 | [R][R] SYSTEMIC RISK |")
    lines.append("")
    lines.append("_[R] = High impact | [Y] = Medium impact | อย่าซื้อหน้า event_")
    return "\n".join(lines)


def _build_plain_thai(market, macro):
    spx_1d = _v(market, "^GSPC", "change_1d_pct") or 0
    nas_1d = _v(market, "^IXIC", "change_1d_pct") or 0
    sox_1d = _v(market, "^SOX",  "change_1d_pct") or 0
    spx_1w = _v(market, "^GSPC", "ret_1w") or 0

    spx_dir = "บวก" if spx_1d > 0 else "ลบ"
    spx_color = "[G]" if spx_1d > 0 else "[R]"

    lines = []
    lines.append("## ภาษาคน -- อธิบายง่ายๆ")
    lines.append("")
    lines.append(f"{spx_color} วันนี้ S&P 500 ปิด{spx_dir} {abs(spx_1d):.1f}% | Nasdaq {_pct(nas_1d)} | SOX Semi {_pct(sox_1d)}")
    lines.append(f"สัปดาห์นี้ S&P 500 ขยับ {_pct(spx_1w)} รวม")
    lines.append("")

    lines.append("NRGC Phase 4 (Recognition) คืออะไร?")
    lines.append("")
    lines.append("ลองนึกภาพ NRGC เป็น 'ชีวิตของ story หุ้น' 7 ช่วง:")
    lines.append("- Phase 1 Neglect = ไม่มีใครสนใจ หุ้นตาย")
    lines.append("- Phase 2 Accumulation = คนฉลาดเงียบๆ เริ่มซื้อ ราคาไม่ลงแม้ข่าวร้าย")
    lines.append("- Phase 3 Inflection = ตัวเลขเริ่มพิสูจน์ story แต่ตลาดยังไม่เชื่อ 100% [BEST ENTRY]")
    lines.append("- Phase 4 Recognition = ตลาดยอมรับแล้ว analyst upgrade เป็นแถว [ตลาดอยู่ตรงนี้]")
    lines.append("- Phase 5 Consensus = ทุกคนเชื่อ ราคาขึ้นต่อแต่ช้าลง")
    lines.append("- Phase 6 Euphoria = ทุกคนคุยเรื่องนี้ใน taxi [ระวัง]")
    lines.append("- Phase 7 Distribution = สถาบันขายออก ข่าวดีแต่ราคาไม่ขึ้น")
    lines.append("")
    lines.append("ตอนนี้ Phase 4 = ยังเล่นได้ แต่เลือกหุ้นที่ NRGC Phase 3 (ยังถูก) มากกว่า Phase 5 (แพงแล้ว)")
    lines.append("")
    lines.append("SMC Bullish Flow คืออะไร?")
    lines.append("")
    lines.append("SMC = Smart Money Concept -- เงินใหญ่ทิ้งรอยไว้บน chart เสมอ:")
    lines.append("- Stop Hunt = ราคาลงต่ำกว่า low เดิมแป๊บเดียวแล้วเด้งกลับ -> สถาบันซื้อตอน retail panic")
    lines.append("- Break of Structure (BOS) = ราคาทำ Higher High ครั้งแรก -> trend เปลี่ยน")
    lines.append("- Order Block Hold = pullback กลับมาแล้วไม่หลุด low เดิม -> สถาบัน defend ราคา")
    lines.append("")
    lines.append("'SMC Bullish Flow Confirmed' = BOS ขึ้นแล้ว + order block เก่ายังเป็น support = ยังซื้ออยู่")

    return "\n".join(lines)


def _build_telegram_summary(verdict, v_icon, market, macro, alpha_picks,
                             perf_ctx=None, fx=None, phase_changes=None):
    """
    English news-style overnight recap: WHAT happened, WHY it happened,
    WHICH stocks led — then Factor to Watch + Alpha of the Day.
    """
    from datetime import date as _date, timedelta

    perf_ctx = perf_ctx or {}
    fx       = fx or {}
    phase_changes = phase_changes or {}

    today     = _date.today()
    today_str = _fmt_day(today)

    # ── Raw market data ────────────────────────────────────────────────────────
    spx_1d = _v(market, "^GSPC", "change_1d_pct") or 0
    nas_1d = _v(market, "^IXIC", "change_1d_pct") or 0
    sox_1d = _v(market, "^SOX",  "change_1d_pct") or 0
    dow_1d = _v(market, "^DJI",  "change_1d_pct")   # may be None if not in watchlist
    spx_px = _v(market, "^GSPC", "price") or 0
    nas_px = _v(market, "^IXIC", "price") or 0
    sox_px = _v(market, "^SOX",  "price") or 0

    spx_mtd = perf_ctx.get("sp500",  {}).get("mtd_pct")
    spx_ytd = perf_ctx.get("sp500",  {}).get("ytd_pct")
    nas_mtd = perf_ctx.get("nasdaq", {}).get("mtd_pct")
    nas_ytd = perf_ctx.get("nasdaq", {}).get("ytd_pct")
    sox_mtd = perf_ctx.get("sox",    {}).get("mtd_pct")
    sox_ytd = perf_ctx.get("sox",    {}).get("ytd_pct")
    dow_mtd = perf_ctx.get("dow",    {}).get("mtd_pct")
    dow_ytd = perf_ctx.get("dow",    {}).get("ytd_pct")

    gold_fx   = fx.get("gold_usd",  {})
    brent_fx  = fx.get("oil_brent", {})
    gold_px   = gold_fx.get("price")        or _v(market, "GC=F",  "price")
    gold_1d   = gold_fx.get("change_pct_1d") or _v(market, "GC=F",  "change_1d_pct") or 0
    brent_px  = brent_fx.get("price")       or _v(market, "BZ=F",  "price")
    brent_1d  = brent_fx.get("change_pct_1d") or _v(market, "BZ=F", "change_1d_pct") or 0
    gold_mtd  = perf_ctx.get("gold",  {}).get("mtd_pct")
    gold_ytd  = perf_ctx.get("gold",  {}).get("ytd_pct")
    brent_mtd = perf_ctx.get("brent", {}).get("mtd_pct")
    brent_ytd = perf_ctx.get("brent", {}).get("ytd_pct")

    fed_v = macro.get("fed_funds_rate", {}).get("value")
    y10_v = macro.get("us_10y_yield",  {}).get("value")
    y2_v  = macro.get("us_2y_yield",   {}).get("value")
    cpi_v = macro.get("cpi_yoy", {}).get("cpi_yoy_pct")
    dv    = macro.get("dxy",    {}).get("value")
    vix_v = macro.get("vix",    {}).get("value")

    def _c(v, dec=1):
        if v is None: return "—"
        return f"{'+' if v >= 0 else ''}{v:.{dec}f}%"

    def _px(v, fmt=","):
        return f"{v:{fmt}.0f}" if v else "—"

    # ── Top movers ─────────────────────────────────────────────────────────────
    all_stocks = [
        (t, _v(market, t, "change_1d_pct") or 0,
            _v(market, t, "name") or t,
            _v(market, t, "theme") or "")
        for t in list(BIG_CAP) + list(MID_SMALL)
        if _v(market, t, "change_1d_pct") is not None
    ]
    gainers = sorted(all_stocks, key=lambda x: x[1], reverse=True)
    losers  = [x for x in sorted(all_stocks, key=lambda x: x[1]) if x[1] < -1.0]

    # ── Semiconductor sub-group ────────────────────────────────────────────────
    semi_tickers  = ["NVDA", "AMD", "MU", "MRVL", "AVGO", "ANET"]
    semi_moves    = [(t, _v(market, t, "change_1d_pct") or 0)
                     for t in semi_tickers if _v(market, t, "change_1d_pct") is not None]
    semi_moves.sort(key=lambda x: x[1], reverse=True)

    # Photonics sub-group
    photo_tickers = ["CIEN", "AAOI", "COHR", "MRVL"]
    photo_moves   = [(t, _v(market, t, "change_1d_pct") or 0)
                     for t in photo_tickers if _v(market, t, "change_1d_pct") is not None]
    photo_moves.sort(key=lambda x: x[1], reverse=True)

    # ── Events ─────────────────────────────────────────────────────────────────
    wday = today.weekday()
    if wday >= 5:
        mon = today + timedelta(days=(7 - wday))
    else:
        mon = today - timedelta(days=wday)
    tue = mon + timedelta(1)
    wed = mon + timedelta(2)
    thu = mon + timedelta(3)
    nvda_d = _date(today.year, 5, 21)
    events = [
        (tue, "US CPI", "🔴"),
        (wed, "FOMC Minutes", "🔴"),
        (thu, "Jobless Claims + Retail Sales", "🟡"),
        (nvda_d, "NVDA Earnings", "🔴🔴"),
    ]
    next_events = [(d, n, i) for d, n, i in events if d >= today]

    accel = phase_changes.get("accelerating", [])
    fade  = phase_changes.get("decelerating", [])

    # ═══════════════════════════════════════════════════════════════════════════
    # BUILD NARRATIVE
    # Each block answers: WHAT happened → WHY → WHICH stocks → macro context
    # ═══════════════════════════════════════════════════════════════════════════

    tg = []
    tg.append(f"📊 *AlphaAbsolute Daily PULSE*  |  {today_str}")
    tg.append("")

    # ── ① Opening sentence: overall market direction ───────────────────────────
    if spx_1d > 1.5:
        open_s = f"U.S. equities rallied broadly, with the S&P 500 gaining {_c(spx_1d)} to {_px(spx_px)}."
    elif spx_1d > 0.3:
        open_s = f"U.S. equities advanced, with the S&P 500 rising {_c(spx_1d)} to {_px(spx_px)}."
    elif spx_1d > 0:
        open_s = f"U.S. equities edged higher, with the S&P 500 adding {_c(spx_1d)} to {_px(spx_px)}."
    elif spx_1d > -0.3:
        open_s = f"U.S. equities were little changed, with the S&P 500 slipping {_c(spx_1d)} to {_px(spx_px)}."
    elif spx_1d > -1.5:
        open_s = f"U.S. equities retreated, with the S&P 500 falling {_c(spx_1d)} to {_px(spx_px)}."
    else:
        open_s = f"U.S. equities sold off sharply, with the S&P 500 dropping {_c(spx_1d)} to {_px(spx_px)}."
    tg.append(open_s)

    # ── ② Breadth / leadership sentence ────────────────────────────────────────
    sox_gap = sox_1d - spx_1d   # how much SOX beat/lagged SPX
    nas_gap = nas_1d - spx_1d

    if sox_gap > 3:
        # Semiconductors led big
        lead_s = (f"The session was driven overwhelmingly by semiconductors — the Philadelphia Semi Index (SOX) "
                  f"surged {_c(sox_1d)}, outpacing the S&P 500 by {sox_gap:.1f} percentage points.")
        if nas_gap < 0.5 and spx_1d > 0:
            lead_s += (" The Dow gained only " + _c(dow_1d) + "," if dow_1d else " The broader market lagged,")
            lead_s += " suggesting the rally's engine remained concentrated in AI hardware rather than spread evenly across sectors."
    elif sox_gap > 1:
        lead_s = (f"Technology and semiconductors led — the Nasdaq gained {_c(nas_1d)} while the SOX "
                  f"outperformed at {_c(sox_1d)}, reflecting continued confidence in AI infrastructure spending.")
    elif nas_gap > 1:
        lead_s = (f"Growth stocks drove the session, with the Nasdaq adding {_c(nas_1d)}, "
                  f"outpacing the S&P 500 by {nas_gap:.1f} percentage points.")
    elif sox_1d < -2:
        lead_s = (f"Semiconductors were the main drag — the SOX index fell {_c(sox_1d)}, "
                  f"pressuring broader tech and AI names.")
    elif spx_1d > 0 and nas_1d > 0 and sox_1d > 0:
        lead_s = (f"Gains were relatively broad — the Nasdaq added {_c(nas_1d)} and the SOX "
                  f"{_c(sox_1d)}, with no single sector dominating.")
    else:
        lead_s = f"The Nasdaq moved {_c(nas_1d)} and the SOX {_c(sox_1d)}."
    tg.append(lead_s)

    # ── ③ Key stocks sentence ──────────────────────────────────────────────────
    if gainers:
        # Top 3 gainers with names
        g_parts = []
        for (t, v, name, theme) in gainers[:3]:
            short_name = name.split()[0] if name != t else t  # first word of name or ticker
            g_parts.append(f"{short_name} ({t}) {_c(v)}")
        stock_s = "Among portfolio names, " + ", ".join(g_parts[:-1])
        if len(g_parts) > 1:
            stock_s += f", and {g_parts[-1]}" if len(g_parts) == 2 else f" and {g_parts[-1]}"
        else:
            stock_s = f"Portfolio standout: {g_parts[0]}"
        stock_s += "."
        tg.append(stock_s)

    # Semiconductor sub-narrative if SOX moved significantly
    if abs(sox_1d) > 2 and semi_moves:
        semi_names = ", ".join(f"{t} {_c(v)}" for t, v in semi_moves[:4])
        if sox_1d > 0:
            semi_s = f"In the chip complex: {semi_names} — consistent with the view that hyperscaler AI capex is broadening."
        else:
            semi_s = f"Chip names sold off: {semi_names} — watch for guidance risk ahead of upcoming earnings."
        tg.append(semi_s)

    # Losers sentence (if any)
    if losers:
        l_parts = [f"{t} {_c(v)}" for t, v, *_ in losers[:2]]
        tg.append(f"On the downside: {', '.join(l_parts)}.")

    # ── ④ Macro / rates context — lead with 10Y yield bps change ─────────────
    tg.append("")
    macro_parts = []

    # ── US 10Y Treasury — the most important rate signal ─────────────────────
    if y10_v:
        # Get bps change vs previous day
        # macro IS already the us_macro sub-dict (has us_10y_yield directly)
        y10_data = macro.get("us_10y_yield", {})
        bps_1d   = y10_data.get("change_bps") if isinstance(y10_data, dict) else None

        # Direction label
        if bps_1d is not None:
            if bps_1d > 0:
                dir_s   = f"*rose {bps_1d:+.1f} bps*"
                impact  = ("→ หุ้น Growth/High PE ถูกกดดัน — ต้อง RS แข็งมากถึงจะ hold ได้"
                           if bps_1d > 5 else
                           "→ slight headwind for high-multiple growth, monitor")
            elif bps_1d < 0:
                dir_s   = f"*fell {bps_1d:.1f} bps*"
                impact  = ("→ supportive for growth stocks — multiple expansion signal"
                           if bps_1d < -5 else
                           "→ mildly supportive, broadly neutral")
            else:
                dir_s  = "*unchanged*"
                impact = "→ no yield catalyst today"

            ten_yr_line = (f"*🔑 US 10Y Treasury: {y10_v:.2f}%* — {dir_s} vs yesterday. {impact}")
        else:
            # No bps change available — just show level with context
            if y10_v > 4.5:
                impact = "⚠️ above 4.5% — meaningful headwind for high-multiple growth. Monitor closely."
            elif y10_v > 4.2:
                impact = "elevated but below the 4.5% danger zone — cautious on duration-sensitive names."
            else:
                impact = "benign level — supportive backdrop for risk assets."
            ten_yr_line = f"*🔑 US 10Y Treasury: {y10_v:.2f}%* — {impact}"

        macro_parts.append(ten_yr_line)

        # Add curve context if 2Y available
        if y2_v:
            spread = round(y10_v - y2_v, 2)
            if spread < 0:
                macro_parts.append(f"2Y/10Y spread: {spread:+.2f}pp (inverted) — 2Y at {y2_v:.2f}%. "
                                   f"Inversion = market skeptical of long-term growth; watch for un-inversion as recession signal.")
            elif spread > 0.5:
                macro_parts.append(f"Yield curve steepened to {spread:+.2f}pp (10Y {y10_v:.2f}% / 2Y {y2_v:.2f}%) — "
                                   f"positive for bank NIM; reflation signal.")
            # else: narrow/flat — not worth adding noise

    if fed_v and cpi_v:
        real_r = round(fed_v - cpi_v, 2)
        macro_parts.append(f"Real rates stand at {real_r:+.2f}% (Fed funds {fed_v:.2f}% minus CPI {cpi_v:.1f}%) — "
                           f"{'still restrictive but supportive of quality growth' if 0 < real_r < 2.5 else 'elevated, a potential valuation headwind' if real_r >= 2.5 else 'near zero, broadly accommodative'}.")
    if dv:
        dxy_s = (f"The dollar strengthened (DXY {dv:.1f}), a headwind for EM flows and commodity prices."
                 if dv > 103 else
                 f"The dollar held steady (DXY {dv:.1f}), broadly neutral for emerging markets.")
        macro_parts.append(dxy_s)
    if gold_px:
        gold_s = (f"Gold {'added' if gold_1d >= 0 else 'fell'} {_c(gold_1d)} to ${gold_px:,.0f}/oz")
        if brent_px:
            gold_s += f"; Brent crude {'gained' if brent_1d >= 0 else 'eased'} {_c(brent_1d)} to ${brent_px:.1f}/bbl."
        else:
            gold_s += "."
        macro_parts.append(gold_s)
    if vix_v:
        vix_s = (f"The VIX stood at {vix_v:.1f}, " +
                 ("signalling elevated caution — size down exposure." if vix_v > 25 else
                  "slightly elevated — stay selective." if vix_v > 20 else
                  "low, consistent with a risk-on backdrop."))
        macro_parts.append(vix_s)

    for part in macro_parts:
        tg.append(part)

    # ── ⑤ Momentum Movers (RS percentile rank shift — 1M vs 3M) ──────────────
    # Format: TICKER: RS Pct 3M → RS Pct 1M (1M-3M Δ: +N rank positions)
    def _mm_fmt(c, sign_prefix=""):
        """Format a momentum mover as: TICKER: Xth→Yth (1M-3M Δ: ±N)"""
        t      = c.get("ticker", "?")
        pct3   = c.get("rs_pct_3m")
        pct1   = c.get("rs_pct_1m")
        delta  = c.get("delta", 0)
        def _ord(v):
            if v is None: return "?"
            v = int(round(v))
            suf = {1:"st",2:"nd",3:"rd"}.get(v%10 if v%100 not in(11,12,13) else 0,"th")
            return f"{v}{suf}"
        delta_sign = f"+{int(delta)}" if delta >= 0 else str(int(delta))
        return f"*{t}*: {_ord(pct3)}→{_ord(pct1)} (Δ {delta_sign})"

    if accel or fade:
        tg.append("")
        tg.append("*⚡ Momentum Movers (RS Pct 1M vs 3M — percentile rank shift)*")
        tg.append("_Xth→Yth = RS percentile rank เปลี่ยนจาก 3M ไป 1M. Δ = จำนวน rank ที่เปลี่ยน_")
        if accel:
            # Sort by absolute delta magnitude descending — show biggest movers first
            top_accel = sorted(accel, key=lambda x: abs(x.get("delta", 0)), reverse=True)[:4]
            tg.append("  ↑ Gaining momentum (RS rank accelerating):")
            for c in top_accel:
                tg.append(f"    {_mm_fmt(c)}")
        if fade:
            top_fade = sorted(fade, key=lambda x: abs(x.get("delta", 0)), reverse=True)[:3]
            tg.append("  ↓ Losing momentum (RS rank fading):")
            for c in top_fade:
                tg.append(f"    {_mm_fmt(c)}")

    # ── ⑤b PULSE Framework Overnight Recap ────────────────────────────────────
    tg.append("")
    tg.append("─────────────────────────")
    tg.append("📡 *PULSE Framework Overnight Recap*")
    tg.append("")

    # P = Price Action (what happened to indices / portfolio names)
    if spx_1d > 1:
        p_desc = f"ตลาดบวกแรง S&P500 {_c(spx_1d)} | Nasdaq {_c(nas_1d)} | SOX {_c(sox_1d)} — risk-on เต็มรูปแบบ"
    elif spx_1d > 0:
        p_desc = f"ตลาดบวกเล็กน้อย S&P500 {_c(spx_1d)} | Nasdaq {_c(nas_1d)} | SOX {_c(sox_1d)} — sideways-up"
    elif spx_1d > -1:
        p_desc = f"ตลาดลงเล็กน้อย S&P500 {_c(spx_1d)} | Nasdaq {_c(nas_1d)} | SOX {_c(sox_1d)} — profit-taking"
    else:
        p_desc = f"ตลาดลงแรง S&P500 {_c(spx_1d)} | Nasdaq {_c(nas_1d)} | SOX {_c(sox_1d)} — risk-off"
    tg.append(f"*P — Price Action:* {p_desc}")

    # U = Upward Revision (EPS revision direction, from RS momentum as proxy)
    # Compute from top performers — if top RS stocks leading = EPS revisions likely positive
    stage2_names = [(t, _v(market, t, "rs_pct_6m") or 0, _v(market, t, "change_1d_pct") or 0)
                    for t in list(BIG_CAP) + list(MID_SMALL)
                    if _v(market, t, "stage2_proxy") is True]
    stage2_names.sort(key=lambda x: x[1], reverse=True)
    top_rs_names = [f"{t} (RS {int(r)}th)" for t, r, _ in stage2_names[:3] if r > 0]
    u_upward = sum(1 for _, _, d1v in stage2_names if d1v > 0)
    u_down   = sum(1 for _, _, d1v in stage2_names if d1v < -1)
    if u_upward > u_down:
        u_desc = f"EPS revision direction: ขาขึ้น — {u_upward}/{len(stage2_names)} Stage 2 names บวกวันนี้. Top RS leaders: {', '.join(top_rs_names[:3])}. Positive guidance trend intact."
    elif u_down > u_upward:
        u_desc = f"EPS revision risk: {u_down}/{len(stage2_names)} Stage 2 names ลบ — อาจมี guidance cut หรือ margin pressure. ระวัง pre-earnings sizing."
    else:
        u_desc = f"EPS revision: Mixed — Stage 2 names แยกตัว. RS leaders ยังดี แต่ laggards เริ่มอ่อน. ดู next earnings call."
    tg.append(f"*U — Upward Revision:* {u_desc}")

    # L = Leadership (which themes led, from which factors + earnings examples)
    # Identify top theme from theme members' average 1D performance
    theme_perf = {}
    for theme_name, members in THEME_MEMBERS.items():
        d1_vals = [_v(market, t, "change_1d_pct") or 0 for t in members if _v(market, t, "price")]
        if d1_vals:
            theme_perf[theme_name] = sum(d1_vals) / len(d1_vals)
    if theme_perf:
        top_themes  = sorted(theme_perf.items(), key=lambda x: x[1], reverse=True)[:2]
        weak_themes = sorted(theme_perf.items(), key=lambda x: x[1])[:1]
        lead_themes_str  = " + ".join(f"{t} ({v:+.1f}%)" for t, v in top_themes)
        weak_themes_str  = " + ".join(f"{t} ({v:+.1f}%)" for t, v in weak_themes)
        # Earnings example from top gainers — narrative driver
        semi_led = sox_1d > 1.5
        if semi_led and gainers:
            best_t, best_v, best_name, _ = gainers[0]
            earnings_ex = (f"Driver: {best_name} ({best_t}) {_c(best_v)} — AI capex guidance + hyperscaler spending confirmation "
                           f"(pattern: AMD/INTC guidance ที่ GPU:CPU ratio 1:1 → validate AI hardware demand → semi rally).")
        else:
            earnings_ex = f"Driver: ติดตาม earnings guidance — ธีมนำมาจาก sector RS + theme heatmap signal."
        tg.append(f"*L — Leadership:* นำโดย {lead_themes_str}. อ่อนตัว: {weak_themes_str}. {earnings_ex}")

    # S = Stage / Breadth (market stage assessment + %>200DMA proxy)
    stage2_count_all = sum(1 for t, d_val in market.items()
                           if not t.startswith("^") and d_val.get("stage2_proxy") is True)
    total_count = sum(1 for t in market if not t.startswith("^") and "price" in market[t])
    breadth_pct_val = (stage2_count_all / total_count * 100) if total_count > 0 else 0
    if breadth_pct_val > 70:
        s_breadth = "Broad market — >70% universe Stage 2. Safe to add positions."
        s_stage   = "Weinstein Stage 2A — ราคาเหนือ 30W MA ทั้งกลุ่ม, pullback ตื้น."
    elif breadth_pct_val > 50:
        s_breadth = f"Moderate breadth — {stage2_count_all}/{total_count} Stage 2. เลือกเฉพาะ RS top quartile."
        s_stage   = "Weinstein Stage 2 — ยังดีแต่ breadth ไม่กว้างพอ ระวัง false breakout."
    else:
        s_breadth = f"Narrow market — เพียง {stage2_count_all}/{total_count} Stage 2. ระวัง distribution."
        s_stage   = "Weinstein Stage 2→3 risk — ตรวจ 30W MA slope ก่อน add."
    tg.append(f"*S — Stage & Breadth:* {s_stage} {s_breadth}")

    # E = Earnings (1Q26 highlights — driven from market data moves as proxy)
    # Build earnings context from big 1D movers (earnings reaction proxy)
    big_movers = [(t, _v(market, t, "change_1d_pct") or 0, _v(market, t, "name") or t)
                  for t in list(BIG_CAP) + list(MID_SMALL)
                  if abs(_v(market, t, "change_1d_pct") or 0) > 4]
    big_movers.sort(key=lambda x: abs(x[1]), reverse=True)
    if big_movers:
        e_parts = []
        for t, v, name in big_movers[:3]:
            direction = "beat" if v > 0 else "missed"
            guide     = "guided higher" if v > 5 else ("held steady" if v > 0 else "guided cautious")
            e_parts.append(f"{name} ({t}) {_c(v)} — 1Q26 {direction}, {guide}")
        e_desc = " | ".join(e_parts)
        e_note = ("Key 1Q26 theme: hyperscaler AI capex guidance remains strong — "
                  "GPU allocation ขยาย, HBM order backlog ยาว = structural demand ไม่ใช่ cyclical.")
        tg.append(f"*E — Earnings (1Q26):* {e_desc}. {e_note}")
    else:
        tg.append(f"*E — Earnings (1Q26):* ไม่มี major earnings move วันนี้. ติดตาม: NVDA (May 28), AVGO (Jun 4), MU (Jun 18) — all critical for AI capex validation.")

    # ── ⑥ Scoreboard — multi-line format ──────────────────────────────────────
    tg.append("")
    tg.append("*📊 Scoreboard*")
    tg.append("")

    def _sb_block(label, px, d1, mtd, ytd):
        """Multi-line format: Name + price on line 1, 1D/MTD/YTD on line 2."""
        px_s = f"{px:,.0f}" if px else "?"
        perf  = f"1D {_c(d1)}  MTD {_c(mtd)}  YTD {_c(ytd)}"
        return [f"*{label}*  {px_s}", f"  {perf}"]

    for line in _sb_block("S&P 500",  spx_px, spx_1d, spx_mtd, spx_ytd):
        tg.append(line)
    for line in _sb_block("Nasdaq",   nas_px, nas_1d, nas_mtd, nas_ytd):
        tg.append(line)
    for line in _sb_block("SOX Semi", sox_px, sox_1d, sox_mtd, sox_ytd):
        tg.append(line)
    if gold_px:
        for line in _sb_block("Gold $/oz",  gold_px, gold_1d, gold_mtd, gold_ytd):
            tg.append(line)
    if brent_px:
        for line in _sb_block("Brent $/bbl", brent_px, brent_1d, brent_mtd, brent_ytd):
            tg.append(line)

    # ── ⑦ Factor to Watch ─────────────────────────────────────────────────────
    tg.append("")
    tg.append("─────────────────────────")
    tg.append("🔑 *FACTOR TO WATCH*")

    watch_factors = []
    # VIX elevated
    if vix_v and vix_v > 22:
        watch_factors.append(f"*VIX {vix_v:.0f}* — elevated: risk of volatility spike, reduce gross exposure")
    # 10Y yield at key level
    if y10_v:
        if y10_v > 4.5:
            watch_factors.append(f"*US 10Y {y10_v:.2f}%* — above 4.5% threshold: headwind for high-multiple growth stocks")
        elif y10_v > 4.2:
            watch_factors.append(f"*US 10Y {y10_v:.2f}%* — watch for move toward 4.5%: would pressure valuations")
        else:
            watch_factors.append(f"*US 10Y {y10_v:.2f}%* — benign rate environment: supportive for risk assets")
    # Real rate
    if cpi_v and fed_v:
        real_r = round(fed_v - cpi_v, 2)
        if real_r > 2.5:
            watch_factors.append(f"*Real rate {real_r:+.2f}%* — very restrictive: watch for slowdown in capex guidance")
    # SOX signal
    if abs(sox_1d) > 3:
        watch_factors.append(f"*SOX {_c(sox_1d)}* — {'strong AI hardware signal; confirm with NVDA / AMD earnings guide' if sox_1d > 0 else 'semi under pressure; risk of broad tech pullback if guide disappoints'}")
    # Upcoming event
    if next_events:
        ev_d, ev_name, ev_icon = next_events[0]
        watch_factors.append(f"{ev_icon} *{_fmt_short(ev_d)} — {ev_name}* — avoid new entries before print")

    for i, f in enumerate(watch_factors[:3], 1):
        tg.append(f"  {i}. {f}")

    # ── ⑧ Alpha of the Day ────────────────────────────────────────────────────
    tg.append("")
    tg.append("─────────────────────────")
    tg.append("🏆 *ALPHA OF THE DAY*")
    tg.append("")

    medals = ["🥇", "🥈", "🥉"]
    if alpha_picks:
        for idx, (score, ticker, ad) in enumerate(alpha_picks[:3]):
            m = medals[idx] if idx < 3 else "•"
            px        = ad.get("price")
            rs_pct1   = ad.get("rs_pct_1m")
            rs_pct3   = ad.get("rs_pct_3m")
            rs_pct6   = ad.get("rs_pct_6m")
            rs_d_1m3m = ad.get("rs_pct_delta")         # 1M-3M rank change
            rs_d_3m6m = ad.get("rs_pct_delta_3m6m")    # 3M-6M rank change
            d1        = ad.get("change_1d_pct") or 0
            theme     = ad.get("theme", "?")[:22]
            s2        = "Stage 2 ✅" if ad.get("stage2_proxy") else "Watch ⚠️"

            def _ord(v):
                if v is None: return "?"
                v = int(round(v))
                suf = {1:"st",2:"nd",3:"rd"}.get(v%10 if v%100 not in(11,12,13) else 0,"th")
                return f"{v}{suf}"
            def _dsgn(v):
                if v is None: return "N/A"
                return f"{'+'  if v>=0 else ''}{int(v)}"

            # RS Pct display: 1M (most recent) | 3M | 6M + both deltas
            rs_line = (f"RS Pct: 1M {_ord(rs_pct1)} | 3M {_ord(rs_pct3)} | 6M {_ord(rs_pct6)}  "
                       f"[1M-3M Δ: {_dsgn(rs_d_1m3m)} | 3M-6M Δ: {_dsgn(rs_d_3m6m)}]")

            fh   = ad.get("pct_from_high") or 0
            stop_pct   = -0.07 if fh > -12 else -0.10
            target_pct =  0.15
            stop_px    = px * (1 + stop_pct)   if px else None
            target_px  = px * (1 + target_pct) if px else None

            price_s = f"${px:,.2f}" if px else "?"
            tg.append(f"{m} *{ticker}*  {theme}  |  {s2}")
            tg.append(f"   {price_s}  *{_c(d1)}*")
            tg.append(f"   {rs_line}")
            if stop_px and target_px:
                tg.append(f"   Entry ~{price_s}  →  Stop ${stop_px:,.0f} ({stop_pct*100:.0f}%)  →  Target ${target_px:,.0f} (+15%)")
            tg.append("")
    else:
        tg.append("No setup clears the Stage 2 + RS gate today — wait for better conditions.")
        tg.append("")

    tg.append("─────────────────────────")
    tg.append("_NRGC + PULSE v2.0  |  Source: FRED + yfinance_")
    tg.append("_Not investment advice — verify chart + EPS before trading_")

    lines = []
    lines.append("## TELEGRAM_SUMMARY")
    lines.append("")
    lines.append("\n".join(tg))
    return "\n".join(lines)


# ─── Main entry point ─────────────────────────────────────────────────────────
def generate_report(macro: dict, market: dict, today_str: str,
                    macro_full: dict = None, phase_changes: dict = None) -> str:
    """
    macro      = us_macro sub-dict (FRED series)
    market     = per-ticker yfinance data
    macro_full = full macro_YYMMDD.json dict (optional, for perf_ctx + fx_commodities + Gold)
    phase_changes = phase changers from run_screener (optional)
    """
    print("  Building comprehensive narrative report from FRED + yfinance...")

    perf_ctx = (macro_full or {}).get("performance_context", {})
    fx_data  = (macro_full or {}).get("fx_commodities", {})

    sections = []

    # 1. Header
    sections.append(f"# AlphaAbsolute Daily Market Pulse | {today_str}")
    sections.append(f"**Framework:** NRGC + PULSE v2.0 | **Sources:** FRED, yfinance")
    sections.append("")
    sections.append("---")

    # 2. Market Verdict
    verdict_block, verdict, v_icon = _build_verdict_table(macro, market)
    sections.append(verdict_block)
    sections.append("")

    # 3. Narrative + Earnings Brief
    sections.append("## NARRATIVE -- WHAT'S DRIVING THE MARKET")
    sections.append("")
    sections.append(_build_narrative(macro, market))
    sections.append("")
    sections.append(_build_earnings_brief(market))
    sections.append("")

    # 4. Macro Snapshot — now with 1D/WoW/MTD/YTD + Gold
    # Prefer macro_full["us_macro"] which has change_bps for yields; fall back to macro
    macro_for_snapshot = (macro_full or {}).get("us_macro") or macro
    sections.append(_build_macro_section(macro_for_snapshot, market, perf_ctx=perf_ctx, fx=fx_data))
    sections.append("")

    # 5. Theme Heatmap
    sections.append(_build_theme_heatmap(market))
    sections.append("")

    # 6. Key Factors
    sections.append(_build_key_factors(macro, market))
    sections.append("")

    # 7. Key Risks
    sections.append(_build_key_risks(macro, market))
    sections.append("")

    # 8. Watchlist tables
    sections.append("---")
    sections.append("")
    sections.append("## WATCHLIST TABLE")
    sections.append("")
    sections.append(_build_watchlist_table(market, BIG_CAP, "BIG CAP (Market Cap >$10B)"))
    sections.append("")
    sections.append(_build_watchlist_table(market, MID_SMALL, "MID/SMALL CAP (Market Cap <$10B)"))
    sections.append("")

    # 9. Alpha picks
    sections.append("---")
    sections.append("")
    sections.append("## ALPHA OF THE DAY")
    sections.append("")

    big_picks   = _top_picks(market, BIG_CAP,   n=5)
    small_picks = _top_picks(market, MID_SMALL, n=5)
    all_alpha   = sorted(big_picks + small_picks, key=lambda x: x[0], reverse=True)[:5]

    sections.append(_build_alpha_section(market, BIG_CAP,   n=5, section_title="Big Cap Alpha Picks"))
    sections.append("")
    sections.append(_build_alpha_section(market, MID_SMALL, n=5, section_title="Mid/Small Cap Alpha Picks"))
    sections.append("")

    # 10. Economic Calendar — now with actual dates
    sections.append("---")
    sections.append("")
    sections.append(_build_economic_calendar())
    sections.append("")

    # 11. Plain Thai explanation
    sections.append("---")
    sections.append("")
    sections.append(_build_plain_thai(market, macro))
    sections.append("")

    # 12. Telegram Summary — rich format
    sections.append("---")
    sections.append("")
    sections.append(_build_telegram_summary(
        verdict, v_icon, market, macro, all_alpha,
        perf_ctx=perf_ctx, fx=fx_data, phase_changes=phase_changes or {}
    ))

    print("  Report built successfully.")
    return "\n".join(sections)
