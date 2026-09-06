"""
AlphaAbsolute -- Mode A Screen Excel Builder
=============================================
Pulls from screening_results + fundamentals_summary and builds a
fully-formatted Excel with:
  - RS percentiles (1M/3M/6M/12M + composite)
  - RS time-delta momentum (chg_1w, chg_4w)
  - RS cross-timeframe momentum (1M-3M, 3M-6M, 6M-12M)
  - Price structure (52W/3M/6M high/low proximity)
  - Fundamentals (EPS/Rev/GM)
  - Gates (G1/G6/G7 + status)

Run: python scripts/pre_compute/build_excel.py
"""

import sqlite3
import shutil
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from datetime import datetime

DB   = "data/ohlcv.db"
DATE = "2026-05-19"
OUT  = f"output/AlphaAbsolute_ModeA_Screen_{DATE.replace('-','')}.xlsx"
DESK = r"C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute_ModeA_Screen.xlsx"

# ── Colors ────────────────────────────────────────────────────────────────────
C_NAVY   = "1F3864"
C_BLUE   = "2E75B6"
C_LBLUE  = "D5E8F0"
C_GREEN  = "375623"
C_LGREEN = "E2EFDA"
C_GOLD   = "7F6000"
C_LGOLD  = "FFF2CC"
C_PURPLE = "4B2E83"
C_LPURP  = "EAD6FF"
C_ORANGE = "C55A11"
C_LORNG  = "FCE4D6"
C_RED    = "9C0006"
C_LRED   = "FFC7CE"
C_GRAY   = "F5F5F5"
C_DGRAY  = "BFBFBF"
C_WHITE  = "FFFFFF"

THEMED = {
    "AI_Related","Memory_HBM","Space","Quantum","Photonics","DefenseTech",
    "DataCenter","Nuclear_SMR","NeoCloud","AI_Infra","DataCenter_Infra",
    "Drone_UAV","Robotics","Connectivity"
}

def fill(hex_):
    return PatternFill("solid", fgColor=hex_)

def fnt(bold=False, color="000000", sz=10, italic=False):
    return Font(bold=bold, color=color, size=sz, italic=italic, name="Calibri")

def aln(h="center", v="center", wrap=False):
    return Alignment(horizontal=h, vertical=v, wrap_text=wrap)

def thin_border():
    s = Side(style="thin", color=C_DGRAY)
    return Border(left=s, right=s, top=s, bottom=s)


def pct_fill(val):
    if val is None:
        return fill(C_GRAY)
    try:
        v = float(val)
    except (TypeError, ValueError):
        return fill(C_GRAY)
    if v >= 90: return fill("1F7A4F")
    if v >= 70: return fill(C_GREEN)
    if v >= 50: return fill(C_LGREEN)
    if v >= 30: return fill(C_LGOLD)
    return fill(C_LRED)

def pct_fnt(val):
    try:
        v = float(val)
    except (TypeError, ValueError):
        return fnt()
    if v >= 70: return fnt(bold=True, color=C_WHITE)
    return fnt()

def chg_fill(val):
    if val is None: return fill(C_LBLUE)
    try: v = float(str(val).replace("+",""))
    except: return fill(C_LBLUE)
    if v > 5:   return fill(C_LGREEN)
    if v > 0:   return fill("EBF5EB")
    if v > -5:  return fill(C_LORNG)
    return fill(C_LRED)

def mom_fill(val):
    if val is None: return fill(C_LPURP)
    try: v = float(str(val).replace("+",""))
    except: return fill(C_LPURP)
    if v > 10:  return fill("1F7A4F")
    if v > 3:   return fill(C_LGREEN)
    if v > -3:  return fill(C_GRAY)
    if v > -10: return fill(C_LORNG)
    return fill(C_LRED)

def gate_fill(val):
    if val == 1: return fill(C_LGREEN)
    if val == 0: return fill(C_LRED)
    return fill(C_LGOLD)

def phase_colors(phase):
    m = {
        "Leader":    ("1F7A4F", C_WHITE),
        "Emerging":  (C_BLUE,  C_WHITE),
        "Recovering":(C_LGOLD, C_GOLD),
        "Weak":      (C_LRED,  C_RED),
    }
    return m.get(phase, (C_WHITE, "000000"))


def build():
    print(f"AlphaAbsolute -- Build Excel  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    # Pull everything from screening_results + fundamentals_summary
    c.execute("""
        SELECT
            s.ticker, s.label, s.phase,
            s.rs_1m_pct, s.rs_3m_pct, s.rs_6m_pct, s.rs_12m_pct, s.rs_composite,
            s.rs_comp_chg_1w, s.rs_comp_chg_4w,
            s.rs_momentum_1m_3m, s.rs_momentum_3m_6m, s.rs_momentum_6m_12m,
            s.last_close, s.adtv_6m_usd,
            s.pct_from_52w_high, s.pct_from_52w_low,
            s.pct_from_3m_high,  s.pct_from_3m_low,
            s.pct_from_6m_high,  s.pct_from_6m_low,
            f.eps_yoy_pct, f.rev_yoy_pct, f.gm_latest, f.gm_trend,
            s.gate_rs, s.gate_52w, s.gate_adtv,
            s.gate_eps, s.gate_rev, s.gate_gm,
            s.gates_passed
        FROM screening_results s
        LEFT JOIN fundamentals_summary f ON s.ticker = f.ticker
        WHERE SUBSTR(s.date,1,10) = ?
        ORDER BY
            CASE WHEN s.label IN (
                'AI_Related','Memory_HBM','Space','Quantum','Photonics','DefenseTech',
                'DataCenter','Nuclear_SMR','NeoCloud','AI_Infra','DataCenter_Infra',
                'Drone_UAV','Robotics','Connectivity'
            ) THEN 0 ELSE 1 END,
            s.rs_composite DESC
    """, (DATE,))
    data = c.fetchall()
    conn.close()
    print(f"  Rows: {len(data)}")

    wb = openpyxl.Workbook()

    # ── Sheet 1: Mode A Screen ────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Mode A Screen"
    ws.freeze_panes = "E4"

    # Row 1: title bar
    ws.merge_cells("A1:AF1")
    ws["A1"].value     = f"AlphaAbsolute  Mode A Screen  |  {DATE}  |  {len(data)} tickers"
    ws["A1"].font      = fnt(bold=True, color=C_WHITE, sz=13)
    ws["A1"].fill      = fill(C_NAVY)
    ws["A1"].alignment = aln()
    ws.row_dimensions[1].height = 22

    # Row 2: section headers
    sections = [
        ("A2:D2",  "IDENTITY",                       C_NAVY),
        ("E2:L2",  "RELATIVE STRENGTH (Percentile)", C_BLUE),
        ("M2:O2",  "RS MOMENTUM (cross-timeframe)",  C_PURPLE),
        ("P2:W2",  "PRICE STRUCTURE",                C_GOLD),
        ("X2:AA2", "FUNDAMENTALS",                   C_GREEN),
        ("AB2:AF2","GATES",                           C_ORANGE),
    ]
    for span, label, color in sections:
        ws.merge_cells(span)
        cell = ws[span.split(":")[0]]
        cell.value     = label
        cell.font      = fnt(bold=True, color=C_WHITE, sz=10)
        cell.fill      = fill(color)
        cell.alignment = aln()
    ws.row_dimensions[2].height = 18

    # Row 3: column headers
    # (header_text, bg, fg, col_width)
    HEADERS = [
        ("#",         C_NAVY,   C_WHITE,  4),   # A
        ("Ticker",    C_NAVY,   C_WHITE,  8),   # B
        ("Theme",     C_NAVY,   C_WHITE, 17),   # C
        ("Phase",     C_NAVY,   C_WHITE, 11),   # D
        # RS Percentile block
        ("RS 1M",     C_BLUE,   C_WHITE,  8),   # E
        ("RS 3M",     C_BLUE,   C_WHITE,  8),   # F
        ("RS 6M",     C_BLUE,   C_WHITE,  8),   # G
        ("RS 12M",    C_BLUE,   C_WHITE,  8),   # H
        ("Composite", C_BLUE,   C_WHITE, 10),   # I
        ("Chg 1W",    C_LBLUE,  C_BLUE,   9),   # J (vs 5d ago)
        ("Chg 4W",    C_LBLUE,  C_BLUE,   9),   # K (vs 21d ago)
        ("RS Phase",  C_LBLUE,  C_BLUE,   9),   # L
        # RS Momentum block
        ("1M-3M",     C_PURPLE, C_WHITE,  9),   # M
        ("3M-6M",     C_PURPLE, C_WHITE,  9),   # N
        ("6M-12M",    C_PURPLE, C_WHITE,  9),   # O
        # Price Structure block
        ("Price",     C_GOLD,   C_WHITE,  9),   # P
        ("ADTV $M",   C_GOLD,   C_WHITE, 10),   # Q
        ("52W H%",    C_LGOLD,  C_GOLD,   9),   # R
        ("52W L%",    C_LGOLD,  C_GOLD,   9),   # S
        ("3M H%",     C_LGOLD,  C_GOLD,   9),   # T
        ("3M L%",     C_LGOLD,  C_GOLD,   9),   # U
        ("6M H%",     C_LGOLD,  C_GOLD,   9),   # V
        ("6M L%",     C_LGOLD,  C_GOLD,   9),   # W
        # Fundamentals block
        ("EPS YoY%",  C_GREEN,  C_WHITE,  9),   # X
        ("Rev YoY%",  C_GREEN,  C_WHITE,  9),   # Y
        ("GM%",       C_LGREEN, C_GREEN,  8),   # Z
        ("GM Trend",  C_LGREEN, C_GREEN, 11),   # AA
        # Gates block
        ("G1 RS",     C_ORANGE, C_WHITE,  7),   # AB
        ("G6 52W",    C_ORANGE, C_WHITE,  7),   # AC
        ("G7 ADTV",   C_ORANGE, C_WHITE,  8),   # AD
        ("Gates",     C_ORANGE, C_WHITE,  7),   # AE
        ("Status",    C_ORANGE, C_WHITE, 12),   # AF
    ]

    for col_i, (hdr, bg, fg, wd) in enumerate(HEADERS, 1):
        cell = ws.cell(row=3, column=col_i, value=hdr)
        cell.font      = fnt(bold=True, color=fg, sz=9)
        cell.fill      = fill(bg)
        cell.alignment = aln()
        cell.border    = thin_border()
        ws.column_dimensions[get_column_letter(col_i)].width = wd
    ws.row_dimensions[3].height = 22

    # ── Data rows ─────────────────────────────────────────────────────────────
    for i, row in enumerate(data, 1):
        r = i + 3
        (ticker, label, phase,
         rs1m, rs3m, rs6m, rs12m, rs_comp,
         chg1w, chg4w, mom13, mom36, mom612,
         price, adtv,
         p52h, p52l, p3h, p3l, p6h, p6l,
         eps, rev, gm, gm_trend,
         g_rs, g_52w, g_adtv, g_eps, g_rev, g_gm,
         gates) = row

        is_themed = label in THEMED
        base_bg   = "EEF4FF" if is_themed else (C_WHITE if i % 2 == 0 else C_GRAY)

        def sv(v, fmt="{:.1f}", fallback=""):
            if v is None: return fallback
            try: return fmt.format(float(v))
            except: return str(v) if v else fallback

        def sv_signed(v, fallback=""):
            if v is None: return fallback
            try:
                fv = float(v)
                return f"{fv:+.1f}"
            except: return fallback

        vals = [
            i,                                                  # A: #
            ticker,                                             # B
            label or "",                                        # C
            phase or "",                                        # D
            sv(rs1m, "{:.0f}"),                                 # E
            sv(rs3m, "{:.0f}"),                                 # F
            sv(rs6m, "{:.0f}"),                                 # G
            sv(rs12m, "{:.0f}"),                                # H
            sv(rs_comp, "{:.1f}"),                              # I
            sv_signed(chg1w),                                   # J
            sv_signed(chg4w),                                   # K
            phase or "",                                        # L
            sv_signed(mom13),                                   # M
            sv_signed(mom36),                                   # N
            sv_signed(mom612),                                  # O
            round(price, 2) if price else "",                   # P
            round(adtv / 1e6, 1) if adtv else "",              # Q
            sv(p52h, "{:.1f}"),                                 # R
            sv(p52l, "{:.1f}"),                                 # S
            sv(p3h,  "{:.1f}"),                                 # T
            sv(p3l,  "{:.1f}"),                                 # U
            sv(p6h,  "{:.1f}"),                                 # V
            sv(p6l,  "{:.1f}"),                                 # W
            sv(eps,  "{:.0f}"),                                 # X
            sv(rev,  "{:.0f}"),                                 # Y
            sv(gm,   "{:.1f}"),                                 # Z
            gm_trend or "",                                     # AA
            g_rs,                                               # AB
            g_52w,                                              # AC
            g_adtv,                                             # AD
            gates,                                              # AE
            "PASS" if (g_rs and g_52w and g_adtv) else "WATCH", # AF
        ]

        for col_i, val in enumerate(vals, 1):
            cell = ws.cell(row=r, column=col_i, value=val)
            cell.alignment = aln()
            cell.border    = thin_border()
            cell.font      = fnt()
            cell.fill      = fill(base_bg)

            # Column-specific styling
            if col_i == 1:    # #
                cell.font = fnt(color="888888", sz=8)
            elif col_i == 2:  # Ticker
                cell.font = fnt(bold=True)
            elif col_i == 3:  # Theme
                cell.alignment = aln("left")
            elif col_i == 4:  # Phase
                bg_c, fg_c = phase_colors(phase)
                cell.fill = fill(bg_c)
                cell.font = fnt(bold=True, color=fg_c, sz=9)
            elif col_i in (5, 6, 7, 8):  # RS 1M/3M/6M/12M
                raw_vals = [rs1m, rs3m, rs6m, rs12m]
                rv = raw_vals[col_i - 5]
                cell.fill = pct_fill(rv)
                cell.font = pct_fnt(rv)
            elif col_i == 9:  # Composite
                cell.fill = pct_fill(rs_comp)
                cell.font = pct_fnt(rs_comp)
                try:
                    if rs_comp and float(rs_comp) >= 80:
                        cell.font = fnt(bold=True, color=C_WHITE, sz=10)
                except: pass
            elif col_i in (10, 11):  # Chg 1W / 4W
                raw_chg = [chg1w, chg4w][col_i - 10]
                cell.fill = chg_fill(raw_chg)
            elif col_i == 12:  # RS Phase
                bg_c, fg_c = phase_colors(phase)
                cell.fill = fill(bg_c)
                cell.font = fnt(color=fg_c, sz=8)
            elif col_i in (13, 14, 15):  # Momentum 1M-3M, 3M-6M, 6M-12M
                raw_mom = [mom13, mom36, mom612][col_i - 13]
                cell.fill = mom_fill(raw_mom)
                try:
                    if raw_mom is not None and abs(float(raw_mom)) > 10:
                        cell.font = fnt(bold=True)
                except: pass
            elif col_i in (16, 17):  # Price, ADTV
                cell.fill = fill(C_LGOLD)
            elif col_i in range(18, 24):  # Price structure %
                cell.fill = fill(C_LGOLD)
                # High-proximity columns: 18(52WH), 20(3MH), 22(6MH) — negative bad
                if col_i in (18, 20, 22):
                    try:
                        pv = float(val) if val != "" else None
                        if pv is not None:
                            if pv > -5:   cell.fill = fill(C_LGREEN)
                            elif pv > -15: cell.fill = fill(C_LGOLD)
                            else:          cell.fill = fill(C_LRED)
                    except: pass
            elif col_i in (24, 25):  # EPS, Rev YoY
                try:
                    fv = float(val) if val != "" else None
                    if fv is not None:
                        if fv >= 25:  cell.fill = fill(C_LGREEN)
                        elif fv >= 0: cell.fill = fill(C_LGOLD)
                        else:         cell.fill = fill(C_LRED)
                    else:
                        cell.fill = fill(C_GRAY)
                except:
                    cell.fill = fill(C_GRAY)
            elif col_i == 26:  # GM%
                cell.fill = fill(C_LGREEN)
            elif col_i == 27:  # GM Trend
                if val == "Expanding":   cell.fill = fill(C_LGREEN)
                elif val == "Contracting": cell.fill = fill(C_LRED)
                else:                    cell.fill = fill(C_LGOLD)
            elif col_i in (28, 29, 30):  # Gate flags
                cell.fill = gate_fill(val)
                if   val == 1: cell.value = "Y"; cell.font = fnt(bold=True, color=C_GREEN)
                elif val == 0: cell.value = "N"; cell.font = fnt(bold=True, color=C_RED)
                else:          cell.value = "?"; cell.font = fnt(italic=True, color=C_GOLD)
            elif col_i == 31:  # Gates count
                cell.fill = fill(C_LGOLD)
                cell.font = fnt(bold=True)
            elif col_i == 32:  # Status
                if val == "PASS":
                    cell.fill = fill(C_LGREEN)
                    cell.font = fnt(bold=True, color=C_GREEN)
                else:
                    cell.fill = fill(C_LGOLD)
                    cell.font = fnt(color=C_GOLD)

    # ── Sheet 2: Field Guide ──────────────────────────────────────────────────
    ws2 = wb.create_sheet("Field Guide")
    ws2.column_dimensions["A"].width = 22
    ws2.column_dimensions["B"].width = 20
    ws2.column_dimensions["C"].width = 14
    ws2.column_dimensions["D"].width = 60

    guide = [
        # (label, field_col, unit_col, desc_col)
        ("FIELD GUIDE  AlphaAbsolute Mode A Screen", None, None, None),
        ("", None, None, None),
        ("=== RELATIVE STRENGTH PERCENTILE ===", None, None, None),
        ("RS 1M Percentile",   "rs_1m_pct",   "0-100",     "Return vs QQQ over 21d, reranked vs full 1325-ticker universe"),
        ("RS 3M Percentile",   "rs_3m_pct",   "0-100",     "GATE 1: must be >= 70. Return vs QQQ over 63d"),
        ("RS 6M Percentile",   "rs_6m_pct",   "0-100",     "GATE 1: must be >= 70. Return vs QQQ over 126d"),
        ("RS 12M Percentile",  "rs_12m_pct",  "0-100/NULL","Return vs QQQ over 252d. NULL = < 252 bars of history available"),
        ("RS Composite",       "rs_composite","0-100",     "Weighted: 0.35 x 1M + 0.45 x 3M + 0.20 x 6M"),
        ("RS Chg 1W",          "rs_comp_chg_1w","signed pts","Composite TODAY minus composite 5 trading days ago (time-delta)"),
        ("RS Chg 4W",          "rs_comp_chg_4w","signed pts","Composite TODAY minus composite 21 trading days ago (time-delta)"),
        ("", None, None, None),
        ("=== RS MOMENTUM (cross-timeframe percentile subtraction) ===", None, None, None),
        ("Momentum 1M-3M",     "rs_momentum_1m_3m","signed pts","rs_1m_pct MINUS rs_3m_pct. Positive = 1M window outpacing 3M = SHORT-TERM ACCELERATION"),
        ("Momentum 3M-6M",     "rs_momentum_3m_6m","signed pts","rs_3m_pct MINUS rs_6m_pct. Positive = 3M window outpacing 6M = MID-TERM STRENGTHENING"),
        ("Momentum 6M-12M",    "rs_momentum_6m_12m","signed pts","rs_6m_pct MINUS rs_12m_pct. NULL until 12M history available (need 252 bars of OHLCV)"),
        ("IDEAL PROFILE:",     None, None, "1M-3M > +5 AND 3M-6M > +5 = Full Acceleration (best entry setup)"),
        ("WATCH:",             None, None, "Both negative = RS fading across all timeframes (avoid)"),
        ("", None, None, None),
        ("=== PRICE STRUCTURE ===", None, None, None),
        ("% from 52W High",    "pct_from_52w_high","negative %","GATE 6: must be > -20%. Value 0% = at 52-week high. -30% = 30% below high"),
        ("% from 52W Low",     "pct_from_52w_low", "positive %","% above the 52-week low. High value = stock recovered far from its lows"),
        ("% from 3M High",     "pct_from_3m_high", "negative %","Proximity to 3-month high. -5% to 0% = tight base near highs (Stage 2 signal)"),
        ("% from 3M Low",      "pct_from_3m_low",  "positive %","% above 3M low. Measures base depth (VCP: should be < 20%)"),
        ("% from 6M High",     "pct_from_6m_high", "negative %","Near 6M high = strong uptrend. Far from 6M high = extended correction"),
        ("% from 6M Low",      "pct_from_6m_low",  "positive %","% above 6M low. Very high (100%+) = multi-bagger run"),
        ("", None, None, None),
        ("=== FUNDAMENTALS ===", None, None, None),
        ("EPS YoY%",           "eps_yoy_latest","percent",  "GATE 2: must be > 25%. Source: EDGAR XBRL primary, FMP fallback"),
        ("Rev YoY%",           "rev_yoy_latest","percent",  "GATE 3: must be > 25%. Source: EDGAR XBRL primary, FMP fallback"),
        ("GM%",                "gm_pct_latest", "percent",  "Gross margin latest reported quarter"),
        ("GM Trend",           "gm_trend",      "text",     "GATE 4: Expanding/Stable = PASS. Contracting = FAIL"),
        ("", None, None, None),
        ("=== GATES (Mode A) ===", None, None, None),
        ("G1 RS",              "gate_rs",  "0=Fail/1=Pass","RS 3M >= 70 AND RS 6M >= 70. The core leadership gate"),
        ("G6 52W",             "gate_52w", "0=Fail/1=Pass","pct_from_52w_high > -20%. Not in deep correction"),
        ("G7 ADTV",            "gate_adtv","0=Fail/1=Pass","ADTV > $10M. Enough liquidity to size a 10% position"),
        ("Gates",              "gates_passed","0-3",       "Count of available gates passed. Fundamentals gates pending FMP data"),
        ("Status",             None,        "PASS/WATCH",  "PASS = G1+G6+G7 all pass. WATCH = partial (review manually)"),
        ("", None, None, None),
        ("=== RS FORMULA ===", None, None, None),
        ("Raw RS formula:",    None, None, "((1 + stock_return) / (1 + QQQ_return) - 1) x 100"),
        ("Percentile method:", None, None, "Full 1325-ticker distribution, numpy searchsorted (true percentile rank)"),
        ("Phase thresholds:",  None, None, "Leader: RS3M>=80 AND RS6M>=70 | Emerging: RS3M>=60 | Recovering: RS3M>=40 | Weak: below 40"),
    ]

    for rr, (label, field, unit, desc) in enumerate(guide, 1):
        ws2.row_dimensions[rr].height = 18
        if label.startswith("==="):
            ws2.merge_cells(f"A{rr}:D{rr}")
            cell = ws2.cell(row=rr, column=1, value=label)
            cell.font      = fnt(bold=True, color=C_WHITE, sz=10)
            cell.fill      = fill(C_NAVY)
            cell.alignment = aln("left")
        elif label in ("IDEAL PROFILE:", "WATCH:", "Raw RS formula:", "Percentile method:", "Phase thresholds:"):
            ws2.cell(row=rr, column=1, value=label).font = fnt(bold=True, italic=True)
            dc = ws2.cell(row=rr, column=4, value=desc)
            dc.font      = fnt(italic=True)
            dc.alignment = aln("left", wrap=True)
        elif label == "" and field is None:
            pass
        elif field is None and label:
            ws2.merge_cells(f"A{rr}:D{rr}")
            ws2.cell(row=rr, column=1, value=label).font = fnt(bold=True, sz=11)
        else:
            ws2.cell(row=rr, column=1, value=label).font  = fnt(bold=True)
            ws2.cell(row=rr, column=2, value=field).font  = fnt(color="595959")
            ws2.cell(row=rr, column=3, value=unit).alignment = aln()
            dc = ws2.cell(row=rr, column=4, value=desc)
            dc.alignment = aln("left", wrap=True)

    # ── Sheet 3: RS Momentum Deep Dive ────────────────────────────────────────
    ws3 = wb.create_sheet("RS Momentum Top")

    ws3.merge_cells("A1:J1")
    ws3["A1"].value     = "RS Momentum Leaders  |  Strongest Acceleration Signals  |  " + DATE
    ws3["A1"].font      = fnt(bold=True, color=C_WHITE, sz=12)
    ws3["A1"].fill      = fill(C_PURPLE)
    ws3["A1"].alignment = aln()
    ws3.row_dimensions[1].height = 20

    mom_headers = [
        ("#",          5), ("Ticker", 8), ("Theme", 17), ("Phase", 11),
        ("RS Comp",   10), ("1M-3M",  9), ("3M-6M",  9), ("Chg 1W", 9),
        ("RS 3M",      8), ("RS 1M",  8),
    ]
    for col_i, (hdr, wd) in enumerate(mom_headers, 1):
        cell = ws3.cell(row=2, column=col_i, value=hdr)
        cell.font      = fnt(bold=True, color=C_WHITE, sz=10)
        cell.fill      = fill(C_PURPLE)
        cell.alignment = aln()
        ws3.column_dimensions[get_column_letter(col_i)].width = wd

    # Filter: both 1M-3M and 3M-6M must be valid, sort by mom13 + mom36 combo
    mom_data = [r for r in data if r[10] is not None and r[11] is not None]
    mom_data.sort(key=lambda x: (x[10] + x[11]), reverse=True)

    for i, row in enumerate(mom_data[:60], 1):
        r = i + 2
        (ticker, label, phase,
         rs1m, rs3m, rs6m, rs12m, rs_comp,
         chg1w, chg4w, mom13, mom36, mom612,
         *rest) = row

        def sv(v, fmt="{:.1f}", fallback=""):
            if v is None: return fallback
            try: return fmt.format(float(v))
            except: return ""

        vals = [
            i, ticker, label or "", phase or "",
            sv(rs_comp, "{:.1f}"),
            f"{float(mom13):+.1f}" if mom13 is not None else "",
            f"{float(mom36):+.1f}" if mom36 is not None else "",
            f"{float(chg1w):+.1f}" if chg1w is not None else "",
            sv(rs3m, "{:.0f}"),
            sv(rs1m, "{:.0f}"),
        ]

        for col_i, val in enumerate(vals, 1):
            cell = ws3.cell(row=r, column=col_i, value=val)
            cell.alignment = aln()
            cell.border    = thin_border()
            cell.font      = fnt()

            bg = "EEF4FF" if (label in THEMED) else (C_WHITE if i % 2 == 0 else C_GRAY)
            cell.fill = fill(bg)

            if col_i == 4:  # Phase
                bg_c, fg_c = phase_colors(phase)
                cell.fill = fill(bg_c)
                cell.font = fnt(bold=True, color=fg_c, sz=9)
            elif col_i == 5:  # RS Composite
                cell.fill = pct_fill(rs_comp)
                cell.font = pct_fnt(rs_comp)
            elif col_i == 6:  # 1M-3M momentum
                cell.fill = mom_fill(mom13)
                if mom13 and abs(mom13) > 10: cell.font = fnt(bold=True)
            elif col_i == 7:  # 3M-6M momentum
                cell.fill = mom_fill(mom36)
                if mom36 and abs(mom36) > 10: cell.font = fnt(bold=True)
            elif col_i == 8:  # Chg 1W
                cell.fill = chg_fill(chg1w)
            elif col_i in (9, 10):  # RS 3M, 1M
                rv = [rs3m, rs1m][col_i - 9]
                cell.fill = pct_fill(rv)
                cell.font = pct_fnt(rv)

    ws3.row_dimensions[1].height = 20
    ws3.row_dimensions[2].height = 18

    # Save
    wb.save(OUT)
    print(f"  Saved:   {OUT}")
    shutil.copy(OUT, DESK)
    print(f"  Desktop: {DESK}")
    print("[OK] Excel build complete.")


if __name__ == "__main__":
    build()
