#!/usr/bin/env python3
"""One-time patch: replaces STEP 3 (Claude API) with template-based generator"""

content = open(r'C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute\scripts\daily_report.py', encoding='utf-8').read()

# Find the two boundaries
i_step3 = content.find('# STEP 3:')
i_step4 = content.find('# STEP 4:')

# Walk backward to find the '# ===' separator line before STEP 3
i_sep3 = content.rfind('\n# ', 0, i_step3)
# Walk backward to find the '# ===' separator line before STEP 4
i_sep4 = content.rfind('\n# ', 0, i_step4)

part_before = content[:i_sep3]
part_after  = content[i_sep4:]

print(f"Replacing chars {i_sep3} to {i_sep4} ({i_sep4 - i_sep3} chars)")

NEW_STEP3 = r'''

# =================================================================
# STEP 3: GENERATE REPORT (data-driven, no API key needed)
# =================================================================

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
    "Robotics":               [],
    "Quantum Computing":      ["IONQ"],
}

def _v(market, ticker, field, default=None):
    d = market.get(ticker, {})
    if "error" in d:
        return default
    return d.get(field, default)

def _fmt_pct(v, plus=True):
    if v is None:
        return "N/A"
    sign = "+" if plus and v >= 0 else ""
    return f"{sign}{v:.1f}%"

def _fmt_price(v):
    if v is None:
        return "N/A"
    return f"${v:.2f}"

def _market_verdict(market):
    spx_1d = _v(market, "^GSPC", "change_1d_pct", 0) or 0
    nas_1d = _v(market, "^IXIC", "change_1d_pct", 0) or 0
    spx_1m = _v(market, "^GSPC", "ret_1m") or 0
    avg_1d = (spx_1d + nas_1d) / 2

    if avg_1d > 1.0 and spx_1m > 2:
        return "STRONG UPTREND", "[G]", "ตลาดแข็งแกร่งมาก -- เทรนด์ขาขึ้นชัดเจน เล่นได้เต็มที่ตาม setup"
    elif avg_1d > 0.3:
        return "UPTREND", "[G]", "ตลาดขาขึ้น -- เน้น Leader และ High-RS stocks"
    elif avg_1d > -0.3:
        return "SIDEWAYS / WAIT", "[Y]", "ตลาดไม่มีทิศทางชัด -- รอ setup ก่อน ถือ cash บางส่วน"
    elif avg_1d > -1.0:
        return "MILD PULLBACK", "[Y]", "ตลาดพักฐาน -- รอดูว่าจะ rebound หรือลงต่อ ไม่รีบ add"
    else:
        return "RISK-OFF / CAUTION", "[R]", "ตลาดอ่อนแอ -- ลด exposure รักษา cash ก่อน"

def _nrgc_phase(rs_6m, rs_momentum, pct_from_high):
    if rs_6m is None:
        return "Unknown"
    if rs_6m > 30 and pct_from_high and pct_from_high > -5:
        return "Phase 5-6 (Consensus/Euphoria)"
    elif rs_6m > 15:
        return "Phase 4 (Recognition) [G]"
    elif rs_6m > 0:
        return "Phase 3-4 (Inflection/Recognition) [G]"
    elif rs_6m > -10 and rs_momentum and rs_momentum > 1.0:
        return "Phase 3 (Inflection) [G]"
    elif rs_6m > -20:
        return "Phase 2 (Accumulation) [Y]"
    else:
        return "Phase 1 (Neglect) [R]"

def _stage(d):
    if d.get("stage2_proxy") is True:
        return "Stage 2 [G]"
    elif d.get("stage2_proxy") is False:
        return "Stage 3/4 [R]"
    return "N/A"

def _theme_score(theme, market):
    members = THEME_MEMBERS.get(theme, [])
    if not members:
        return "[Y]"
    rs_vals = [_v(market, t, "rs_6m") for t in members if _v(market, t, "rs_6m") is not None]
    if not rs_vals:
        return "[Y]"
    avg_rs = sum(rs_vals) / len(rs_vals)
    if avg_rs > 10:
        return "[G]"
    elif avg_rs > -5:
        return "[Y]"
    return "[R]"

def _top_alpha_picks(market, n=3):
    candidates = []
    for ticker, d in market.items():
        if ticker.startswith("^") or "error" in d or "price" not in d:
            continue
        rs_6m  = d.get("rs_6m") or -999
        rs_mom = d.get("rs_momentum") or 0
        pulse  = d.get("pulse_pass") or 0
        from_h = d.get("pct_from_high") or -100
        if not d.get("stage2_proxy"):
            continue
        if from_h < -25:
            continue
        score = (rs_6m * 0.4) + (rs_mom * 10) + (pulse * 5) + (max(0, 20 + from_h) * 0.5)
        candidates.append((score, ticker, d))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[:n]


def generate_report(macro, market, today_str):
    """Generate full daily report from real data -- no API key required"""
    print("  Building data-driven report from FRED + yfinance...")

    verdict, vcolor, th_verdict = _market_verdict(market)

    spx_1d = _v(market, "^GSPC", "change_1d_pct") or 0
    nas_1d = _v(market, "^IXIC", "change_1d_pct") or 0
    sox_1d = _v(market, "^SOX",  "change_1d_pct") or 0
    spx_1m = _v(market, "^GSPC", "ret_1m") or 0
    spx_6m = _v(market, "^GSPC", "ret_6m") or 0
    nas_1m = _v(market, "^IXIC", "ret_1m") or 0
    sox_6m = _v(market, "^SOX",  "ret_6m") or 0

    fed  = macro.get("fed_funds_rate", {})
    y10  = macro.get("us_10y_yield", {})
    y2   = macro.get("us_2y_yield", {})
    spr  = macro.get("yield_spread_10_2", {})
    cpi  = macro.get("cpi_yoy", {})
    dxy  = macro.get("dxy", {})
    oil  = macro.get("oil_brent", {})
    gold = macro.get("gold", {})

    def mv(d):
        return d.get("value") or d.get("cpi_yoy_pct")

    def ms(d):
        return d.get("date", "?")

    L = []

    # -- HEADER + VERDICT --
    L.append(f"# AlphaAbsolute Daily Market Pulse | {today_str}")
    L.append("")
    L.append("## MARKET VERDICT")
    L.append(f"> {vcolor} {verdict} -- {th_verdict}")
    L.append("")
    L.append("| Index | 1D | 1M | 6M |")
    L.append("|-------|-----|-----|-----|")
    L.append(f"| S&P 500  | {_fmt_pct(spx_1d)} | {_fmt_pct(spx_1m)} | {_fmt_pct(spx_6m)} |")
    L.append(f"| Nasdaq   | {_fmt_pct(nas_1d)} | {_fmt_pct(nas_1m)} | N/A |")
    L.append(f"| SOX Semi | {_fmt_pct(sox_1d)} | N/A | {_fmt_pct(sox_6m)} |")
    L.append("")

    # -- MACRO SNAPSHOT --
    L.append("## MACRO SNAPSHOT (FRED API -- verified)")
    L.append("")
    L.append("| Indicator | Value | Date | Signal |")
    L.append("|-----------|-------|------|--------|")
    if mv(fed):
        L.append(f"| Fed Funds Rate | {mv(fed):.2f}% | {ms(fed)} | [Y] Holding high |")
    if mv(y10):
        L.append(f"| US 10Y Yield | {mv(y10):.2f}% | {ms(y10)} | [Y] Watch |")
    if mv(y2):
        L.append(f"| US 2Y Yield | {mv(y2):.2f}% | {ms(y2)} | [Y] Watch |")
    sv = mv(spr)
    if sv is not None:
        sg = "[G]" if sv > 0 else "[Y]"
        L.append(f"| 10Y-2Y Spread | {sv:+.3f}% | calculated | {sg} |")
    cv = cpi.get("cpi_yoy_pct")
    if cv:
        cs = "[R]" if cv > 3 else "[Y]" if cv > 2 else "[G]"
        L.append(f"| CPI YoY | {cv:.2f}% | {ms(cpi)} | {cs} |")
    dv = mv(dxy)
    if dv:
        ds = "[R]" if dv > 105 else "[Y]" if dv > 100 else "[G]"
        L.append(f"| DXY (USD Index) | {dv:.1f} | {ms(dxy)} | {ds} |")
    ov = mv(oil)
    if ov:
        os_ = "[R]" if ov > 85 else "[Y]"
        L.append(f"| Brent Oil | ${ov:.1f}/bbl | {ms(oil)} | {os_} |")
    gv = mv(gold)
    if gv:
        L.append(f"| Gold | ${gv:.0f}/oz | {ms(gold)} | [G] |")
    L.append("")

    # -- THEME HEATMAP --
    L.append("## THEME HEATMAP (14 Megatrends)")
    L.append("")
    L.append("| Theme | RS vs SPY 6M | Signal | Key Names |")
    L.append("|-------|-------------|--------|-----------|")
    for theme, members in THEME_MEMBERS.items():
        sig = _theme_score(theme, market)
        rs_vals = [_v(market, t, "rs_6m") for t in members if _v(market, t, "rs_6m") is not None]
        avg_rs_str = f"{sum(rs_vals)/len(rs_vals):+.1f}%" if rs_vals else "N/A"
        names = ", ".join(members[:3]) if members else "--"
        L.append(f"| {theme} | {avg_rs_str} | {sig} | {names} |")
    L.append("")
    L.append("_[G] Outperforming | [Y] Neutral | [R] Underperforming vs SPY 6M (source: yfinance)_")
    L.append("")

    # -- KEY FACTORS --
    L.append("## KEY FACTORS (ข้อมูล verified จาก FRED + yfinance)")
    L.append("")
    fn = 1
    if mv(y10) and mv(y2) and sv is not None:
        if sv < 0:
            L.append(f"{fn}) Yield Curve ยัง Inverted ({sv:+.3f}%) -- 10Y {mv(y10):.2f}% vs 2Y {mv(y2):.2f}% -- "
                     "สัญญาณเศรษฐกิจชะลอตัว กดดัน banking NIM [SOURCE: FRED]")
        else:
            L.append(f"{fn}) Yield Curve กลับ Positive ({sv:+.3f}%) -- 10Y {mv(y10):.2f}% vs 2Y {mv(y2):.2f}% -- "
                     "financial stress ลดลง เป็น tailwind ให้ risk assets [SOURCE: FRED]")
        fn += 1
    if mv(fed) and cv:
        real = mv(fed) - cv
        L.append(f"{fn}) Fed Funds {mv(fed):.2f}% | CPI {cv:.2f}% -- Real Rate {real:+.2f}% -- "
                 f"{'สภาพดอกเบี้ยสูง กดดัน valuation' if real > 0 else 'Real Rate ติดลบ tailwind ให้ Risk Assets'} "
                 "[SOURCE: FRED]")
        fn += 1
    if dv:
        L.append(f"{fn}) DXY = {dv:.1f} ({'USD แข็ง' if dv > 103 else 'USD อ่อน'}) -- "
                 f"{'กดดัน EM flows Thai baht' if dv > 103 else 'เป็นบวกต่อ EM commodity'} [SOURCE: FRED]")
        fn += 1
    L.append(f"{fn}) SOX Semi Index {_fmt_pct(sox_1d)} วันนี้ -- "
             f"{'AI capex cycle ยังแข็งแกร่ง' if sox_1d > 0 else 'Semi พักฐาน -- ติดตาม rebound'} [SOURCE: yfinance]")
    fn += 1
    if gv:
        L.append(f"{fn}) Gold ${gv:.0f}/oz -- "
                 f"{'Safe haven demand สูง' if gv > 2500 else 'ทรงตัว'} [SOURCE: FRED]")
        fn += 1
    if ov:
        L.append(f"{fn}) Brent ${ov:.1f}/bbl -- "
                 f"{'ต้นทุนพลังงานสูง กดดัน airlines/transport' if ov > 80 else 'ราคาน้ำมันปกติ'} [SOURCE: FRED]")
        fn += 1
    L.append("")

    # -- KEY RISKS --
    L.append("## KEY RISKS TO MONITOR")
    L.append("")
    risks = []
    if sv is not None and sv < 0:
        risks.append("Yield curve inversion -- ถ้า spread แย่ลงต่อ = recession risk เพิ่ม")
    if dv and dv > 104:
        risks.append(f"USD แข็ง DXY {dv:.0f} -- กดดัน EM flows และ Thai baht")
    if cv and cv > 3:
        risks.append(f"CPI {cv:.1f}% ยังสูง -- Fed ยังไม่รีบ cut ดอกเบี้ย")
    if ov and ov > 85:
        risks.append(f"Oil ${ov:.0f}/bbl สูง -- inflation risk กลับมา")
    if not risks:
        risks = [
            "Geopolitical risk -- war premium ใน oil และ safe haven demand",
            "Earnings miss risk -- ถ้า Q2 miss consensus จะกด multiple",
            "Fed higher-for-longer -- ถ้า CPI สูงกว่าคาด delay cut",
        ]
    for i, r in enumerate(risks[:4], 1):
        L.append(f"- Risk {i}: {r}")
    L.append("")

    # -- WATCHLIST TABLE --
    L.append("## WATCHLIST TABLE (Source: yfinance)")
    L.append("")
    L.append("| Ticker | Name | Price | 1D | RS 6M vs SPY | RS 1M vs SPY | Stage | NRGC Phase | From 52W High |")
    L.append("|--------|------|-------|----|-------------|-------------|-------|------------|---------------|")
    stock_list = [(t, d) for t, d in market.items()
                  if not t.startswith("^") and "error" not in d and "price" in d]
    stock_list.sort(key=lambda x: x[1].get("rs_6m") or -999, reverse=True)
    for ticker, d in stock_list[:15]:
        nrgc  = _nrgc_phase(d.get("rs_6m"), d.get("rs_momentum"), d.get("pct_from_high"))
        stage = _stage(d)
        L.append(f"| {ticker} | {d['name']} | {_fmt_price(d.get('price'))} | "
                 f"{_fmt_pct(d.get('change_1d_pct'))} | "
                 f"{_fmt_pct(d.get('rs_6m'))} | "
                 f"{_fmt_pct(d.get('rs_1m'))} | "
                 f"{stage} | {nrgc} | {_fmt_pct(d.get('pct_from_high'), plus=False)} |")
    L.append("")

    # -- ALPHA OF THE DAY --
    L.append("## ALPHA OF THE DAY -- Top Setup (PULSE Screen)")
    L.append("")
    alpha = _top_alpha_picks(market, n=3)
    if not alpha:
        L.append("> [R] NO BUY TODAY -- ไม่มี setup ผ่าน Stage 2 Gate วันนี้ รอ setup ดีขึ้น")
    else:
        for score, ticker, d in alpha:
            rs6  = d.get("rs_6m")
            rs_s = "[G]" if rs6 and rs6 > 10 else "[Y]" if rs6 and rs6 > 0 else "[R]"
            fromh = d.get("pct_from_high")
            volr  = d.get("vol_vs_avg")
            L.append(f"### {ticker} -- {d['name']} ({d.get('theme','?')})")
            L.append(f"- Price: {_fmt_price(d.get('price'))} | 1D: {_fmt_pct(d.get('change_1d_pct'))}")
            L.append(f"- RS vs SPY: 6M {_fmt_pct(rs6)} | 1M {_fmt_pct(d.get('rs_1m'))} {rs_s}")
            L.append(f"- From 52W High: {_fmt_pct(fromh, plus=False)} | Volume: {f'{volr:.1f}x avg' if volr else 'N/A'}")
            L.append(f"- Stage: {_stage(d)} | PULSE basic checks: {d.get('pulse_pass',0)}/5")
            L.append(f"- NRGC Phase: {_nrgc_phase(rs6, d.get('rs_momentum'), fromh)}")
            L.append("- [!] EPS revision + chart pattern: verify จาก Bloomberg/TradingView ก่อน trade")
            L.append("")

    # -- ECONOMIC CALENDAR --
    L.append("## FACTORS TO WATCH THIS WEEK (Thailand Time)")
    L.append("")
    L.append("| Day | Event | TH Time | Consensus | Impact |")
    L.append("|-----|-------|---------|-----------|--------|")
    L.append("| Mon | US Futures / Pre-market | 8:30 PM | -- | ดูทิศทาง |")
    L.append("| Tue | Consumer Confidence | 10:00 PM | -- | [Y] Sentiment |")
    L.append("| Wed | FOMC Minutes / Fed speakers | 1:00 AM TH | -- | [R] Policy signal |")
    L.append("| Thu | Initial Jobless Claims | 7:30 PM TH | ~225K | [Y] Labor market |")
    L.append("| Fri | PCE / Core PCE | 7:30 PM TH | -- | [R] Fed inflation gauge |")
    L.append("| Daily | Earnings season | -- | -- | EPS beat/miss AI names |")
    L.append("")
    L.append("_เวลาไทย = ET + 11 ชั่วโมง (DST) หรือ +12 ชั่วโมง (ฤดูหนาว)_")
    L.append("")

    # -- PLAIN THAI EXPLANATION --
    L.append("## ภาษาคน -- อธิบายง่ายๆ")
    L.append("")
    spx_dir = "บวก" if spx_1d > 0 else "ลบ"
    L.append(f"วันนี้ S&P 500 ปิด{spx_dir} {abs(spx_1d):.1f}% "
             f"{'AI stocks นำตลาด ขาขึ้นยังชัด' if spx_1d > 0 else 'ตลาดพักฐาน ไม่ต้องตกใจ'}")
    L.append("")
    L.append("NRGC Phase 4 (Recognition) คืออะไร?")
    L.append("Phase 4 = ช่วงที่นักลงทุนทั่วไปเริ่มรู้แล้วว่า AI เป็น megatrend จริง "
             "ราคาขึ้นแรงเพราะ institutional money ทยอยเข้า แต่ยังไม่ถึง euphoria "
             "ยังซื้อได้ถ้าหา setup ดี แต่ต้อง size เล็กลงกว่า Phase 2-3")
    L.append("")
    L.append("SMC Bullish Flow คืออะไร?")
    L.append("SMC = Smart Money Concept -- ดูว่าเงินใหญ่ซื้อตรงไหน "
             "Bullish Flow = เห็น BOS ขาขึ้น + Order Block hold + FVG ถูก fill "
             "สัญญาณว่า smart money ยังซื้ออยู่ ไม่ใช่แค่ retail push")
    L.append("")

    # -- TELEGRAM SUMMARY --
    L.append("## TELEGRAM_SUMMARY")
    L.append("")
    tg = [
        f"AlphaAbsolute Daily Pulse | {today_str}",
        "Framework: NRGC + PULSE v2.0",
        "",
        f"{vcolor} ตลาดวันนี้: {verdict}",
        f"S&P 500: {_fmt_pct(spx_1d)} | Nasdaq: {_fmt_pct(nas_1d)} | SOX: {_fmt_pct(sox_1d)}",
        "",
        "Macro:",
    ]
    if mv(fed): tg.append(f"- Fed Rate: {mv(fed):.2f}%")
    if mv(y10): tg.append(f"- US 10Y: {mv(y10):.2f}%")
    if cv: tg.append(f"- CPI YoY: {cv:.2f}%")
    if dv: tg.append(f"- DXY: {dv:.1f}")
    if gv: tg.append(f"- Gold: ${gv:.0f}/oz")
    tg.append("")
    top = alpha[0] if alpha else None
    if top:
        _, atick, ad = top
        tg.append(f"Alpha Pick: {atick} ({ad['name']})")
        tg.append(f"Theme: {ad.get('theme','?')}")
        tg.append(f"Price: {_fmt_price(ad.get('price'))} | RS 6M: {_fmt_pct(ad.get('rs_6m'))}")
        tg.append(f"Stage 2: {'Pass [G]' if ad.get('stage2_proxy') else 'Fail [R]'}")
        tg.append("")
        tg.append("[!] EPS + chart pattern: verify จาก Bloomberg/TradingView")
    else:
        tg.append("Alpha: ไม่มี setup ผ่านเกณฑ์วันนี้ -- รอ")
    tg.append("")
    tg.append("ดู full report ใน PDF")
    L.append("\n".join(tg))
    L.append("")

    print("  Report built successfully.")
    return "\n".join(L)

'''

new_content = part_before + NEW_STEP3 + "\n" + part_after
open(r'C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute\scripts\daily_report.py', 'w', encoding='utf-8').write(new_content)
print(f"Patch applied. File is {len(new_content)} chars.")
