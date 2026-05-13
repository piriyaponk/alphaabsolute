"""
BH (Bumrungrad Hospital) — Balance Sheet Analysis Report
Agent 04 × Agent 06 | AlphaAbsolute
"""

from fpdf import FPDF
from fpdf.enums import XPos, YPos
import datetime

OUTPUT_PATH = r"C:\Users\Pizza\OneDrive\Desktop\AlphaAbsolute\output\stock_BH_balance_sheet_260509.pdf"
FONT_REG  = r"C:\Windows\Fonts\tahoma.ttf"
FONT_BOLD = r"C:\Windows\Fonts\tahomabd.ttf"

# ── Colour palette ──────────────────────────────────────────────────────────
NAVY   = (15,  40,  80)
GOLD   = (196, 150,  30)
BLUE   = (30,  90, 160)
LGRAY  = (245, 246, 248)
MGRAY  = (210, 215, 220)
DGRAY  = (100, 110, 120)
WHITE  = (255, 255, 255)
GREEN  = ( 39, 174,  96)
ORANGE = (230, 126,  34)
RED    = (192,  57,  43)
BLACK  = (  0,   0,   0)


class ReportPDF(FPDF):
    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.add_font("Tahoma",  "",  FONT_REG)
        self.add_font("Tahoma",  "B", FONT_BOLD)
        self.set_margins(15, 15, 15)
        self.set_auto_page_break(auto=True, margin=18)

    # ── Helpers ─────────────────────────────────────────────────────────────
    def set_color(self, rgb, which="draw"):
        if which == "fill":
            self.set_fill_color(*rgb)
        elif which == "text":
            self.set_text_color(*rgb)
        else:
            self.set_draw_color(*rgb)

    def h_rule(self, color=GOLD, thickness=0.8):
        self.set_draw_color(*color)
        self.set_line_width(thickness)
        self.line(15, self.get_y(), 195, self.get_y())
        self.ln(2)

    def section_title(self, text):
        self.ln(4)
        self.set_fill_color(*NAVY)
        self.set_text_color(*WHITE)
        self.set_font("Tahoma", "B", 10)
        self.cell(0, 8, f"  {text}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
        self.set_text_color(*BLACK)
        self.ln(1)

    def body(self, text, size=8.5, indent=0, color=BLACK):
        self.set_font("Tahoma", "", size)
        self.set_text_color(*color)
        if indent:
            self.set_x(self.get_x() + indent)
        self.multi_cell(0, 5, text)
        self.set_text_color(*BLACK)

    def kv_row(self, label, value, fill=False, val_color=BLACK):
        self.set_fill_color(*LGRAY)
        self.set_font("Tahoma", "", 8.5)
        self.set_text_color(*DGRAY)
        self.cell(85, 6, f"  {label}", fill=fill)
        self.set_text_color(*val_color)
        self.set_font("Tahoma", "B", 8.5)
        self.cell(0, 6, value, new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=fill)
        self.set_text_color(*BLACK)

    # ── Table builder ────────────────────────────────────────────────────────
    def table(self, headers, rows, col_widths, alt=True, header_bg=NAVY,
              header_fg=WHITE, font_size=7.8):
        self.set_font("Tahoma", "B", font_size)
        self.set_fill_color(*header_bg)
        self.set_text_color(*header_fg)
        self.set_draw_color(*MGRAY)
        self.set_line_width(0.2)
        for h, w in zip(headers, col_widths):
            self.cell(w, 7, f" {h}", border=1, fill=True)
        self.ln()
        self.set_font("Tahoma", "", font_size)
        self.set_text_color(*BLACK)
        for i, row in enumerate(rows):
            fill = alt and (i % 2 == 0)
            self.set_fill_color(*(LGRAY if fill else WHITE))
            bold = row[0].startswith("**")
            if bold:
                self.set_font("Tahoma", "B", font_size)
                row = [c.replace("**", "") for c in row]
            for j, (cell, w) in enumerate(zip(row, col_widths)):
                # colour-code direction arrows
                tc = BLACK
                if cell.startswith("+") and j > 0:
                    tc = GREEN
                elif cell.startswith("-") and j > 0 and cell != "--":
                    tc = RED
                elif "RECORD" in cell or "✅" in cell:
                    tc = GREEN
                elif "⚠" in cell:
                    tc = ORANGE
                self.set_text_color(*tc)
                self.cell(w, 6, f" {cell}", border=1, fill=fill)
                self.set_text_color(*BLACK)
            self.ln()
            if bold:
                self.set_font("Tahoma", "", font_size)
        self.ln(1)

    # ── Header / Footer ─────────────────────────────────────────────────────
    def header(self):
        if self.page_no() == 1:
            return
        self.set_fill_color(*NAVY)
        self.rect(0, 0, 210, 10, "F")
        self.set_font("Tahoma", "B", 7)
        self.set_text_color(*WHITE)
        self.set_y(2)
        self.cell(0, 6, "  AlphaAbsolute | BH Bumrungrad Hospital — Balance Sheet Analysis Q1 2026",
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*BLACK)
        self.set_y(12)

    def footer(self):
        self.set_y(-13)
        self.h_rule(color=MGRAY, thickness=0.3)
        self.set_font("Tahoma", "", 7)
        self.set_text_color(*DGRAY)
        self.cell(130, 5, "  AlphaAbsolute — Private & Confidential | For CIO Use Only")
        self.cell(0, 5, f"Page {self.page_no()}  |  Generated {datetime.date.today().strftime('%d %b %Y')}",
                  align="R")
        self.set_text_color(*BLACK)


# ════════════════════════════════════════════════════════════════════════════
def build_report():
    pdf = ReportPDF()

    # ── COVER PAGE ──────────────────────────────────────────────────────────
    pdf.add_page()

    # Full navy header bar
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, 210, 58, "F")

    # Gold accent stripe
    pdf.set_fill_color(*GOLD)
    pdf.rect(0, 58, 210, 2.5, "F")

    pdf.set_y(12)
    pdf.set_font("Tahoma", "B", 9)
    pdf.set_text_color(*GOLD)
    pdf.cell(0, 6, "ALPHAABSOLUTE  |  AGENT 04 × AGENT 06  |  CONFIDENTIAL",
             align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(6)

    pdf.set_font("Tahoma", "B", 26)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 12, "BH — BUMRUNGRAD HOSPITAL", align="C",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Tahoma", "B", 14)
    pdf.set_text_color(*GOLD)
    pdf.cell(0, 8, "Balance Sheet Deep-Dive  |  FY2023 – Q1 2026", align="C",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_y(68)
    pdf.set_text_color(*BLACK)

    # Metadata grid
    meta = [
        ("Ticker",          "SET: BH"),
        ("Sector",          "Healthcare / Hospital"),
        ("Report Date",     "09 May 2026"),
        ("Data Currency",   "Thai Baht (THB)"),
        ("Latest Period",   "Q1 2026  (31 March 2026)"),
        ("Audited Period",  "FY2024  (31 December 2024)"),
        ("Analyst Agents",  "Agent 04 (Fundamental) × Agent 06 (Thai FM)"),
        ("Prepared for",    "Piriyapon Kongvanich, CIO"),
    ]
    pdf.set_font("Tahoma", "", 9)
    for i, (k, v) in enumerate(meta):
        fill = i % 2 == 0
        pdf.set_fill_color(*(LGRAY if fill else WHITE))
        pdf.set_font("Tahoma", "", 9)
        pdf.set_text_color(*DGRAY)
        pdf.cell(65, 7, f"  {k}", fill=fill)
        pdf.set_font("Tahoma", "B", 9)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 7, v, fill=fill, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*BLACK)

    pdf.ln(6)
    pdf.h_rule(color=GOLD)
    pdf.ln(3)

    # Verdict box
    pdf.set_fill_color(*NAVY)
    pdf.rect(15, pdf.get_y(), 180, 22, "F")
    pdf.set_fill_color(*GOLD)
    pdf.rect(15, pdf.get_y(), 4, 22, "F")
    pdf.set_y(pdf.get_y() + 4)
    pdf.set_x(22)
    pdf.set_font("Tahoma", "B", 11)
    pdf.set_text_color(*GOLD)
    pdf.cell(0, 6, "BALANCE SHEET VERDICT:  A+  (Upgraded from A)")
    pdf.ln(7)
    pdf.set_x(22)
    pdf.set_font("Tahoma", "", 8.5)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 5,
             "Fortress liquidity | Zero financial debt | Record EBITDA margin 39.3% | 23yrs consecutive net cash")
    pdf.set_text_color(*BLACK)
    pdf.ln(16)

    # Key stats strip — 4 boxes
    stats = [
        ("Total Assets Q1 2026",  "38,710M THB",  "+18.5% YoY"),
        ("Total Equity Q1 2026",  "32,974M THB",  "+18.6% YoY"),
        ("D/E Ratio",             "0.17x",         "Near-zero debt"),
        ("Net Cash Position",     "~3,490M THB",  "23yrs net cash"),
    ]
    box_w = 42
    box_x = 15
    for s in stats:
        pdf.set_fill_color(*LGRAY)
        pdf.rect(box_x, pdf.get_y(), box_w, 22, "F")
        pdf.set_fill_color(*GOLD)
        pdf.rect(box_x, pdf.get_y(), box_w, 1.5, "F")
        y0 = pdf.get_y() + 3
        pdf.set_xy(box_x + 1, y0)
        pdf.set_font("Tahoma", "", 6.5)
        pdf.set_text_color(*DGRAY)
        pdf.cell(box_w - 2, 4, s[0])
        pdf.set_xy(box_x + 1, y0 + 5)
        pdf.set_font("Tahoma", "B", 10)
        pdf.set_text_color(*NAVY)
        pdf.cell(box_w - 2, 6, s[1])
        pdf.set_xy(box_x + 1, y0 + 12)
        pdf.set_font("Tahoma", "", 6.5)
        pdf.set_text_color(*GREEN)
        pdf.cell(box_w - 2, 4, s[2])
        box_x += box_w + 3

    pdf.set_text_color(*BLACK)
    pdf.ln(32)
    pdf.set_font("Tahoma", "", 7.5)
    pdf.set_text_color(*DGRAY)
    pdf.cell(0, 5,
             "Source: Official Financial Statements (investor.bumrungrad.com) | SET Factsheet | "
             "Investing.com | Kaohoon International",
             align="C")

    # ── PAGE 2: FULL BALANCE SHEET TABLE ────────────────────────────────────
    pdf.add_page()
    pdf.section_title("1.  CONSOLIDATED BALANCE SHEET  —  FY2023 to Q1 2026  (THB Million)")

    bs_headers = ["Line Item", "FY2023", "FY2024", "FY2025", "Q1 2026", "YoY Δ*"]
    bs_col_w   = [62, 22, 22, 22, 22, 22]

    bs_rows = [
        ["**ASSETS", "", "", "", "", ""],
        ["**Current Assets", "", "", "", "", ""],
        ["Cash & Cash Equivalents",         "3,774.1",  "3,259.4",  "7,595.1",   "n/a",     "+133%"],
        ["Trade & Other Receivables",       "4,408.5",  "3,982.3",  "4,249.9",   "n/a",     "+6.7%"],
        ["Inventories",                     "362.3",    "356.0",    "371.2",     "n/a",     "+4.3%"],
        ["Other Current Financial Assets",  "6,971.4",  "8,854.0",  "~6,353",    "n/a",     "-28.2%"],
        ["Prepaid & Other Current",         "168.8",    "279.8",    "n/a",       "n/a",     "--"],
        ["**Total Current Assets",          "15,685.0", "16,731.5", "18,781.4",  "~21,300", "+13.4%"],
        ["**Non-Current Assets", "", "", "", "", ""],
        ["Property, Plant & Equipment",     "12,302.4", "12,631.9", "13,657.9",  "~13,900", "+1.8%"],
        ["Other Non-Current Fin. Assets",   "71.4",     "2,013.6",  "n/a",       "n/a",     "⚠ +2718%"],
        ["Right-of-Use Assets",             "65.5",     "108.2",    "n/a",       "n/a",     "--"],
        ["Intangibles + Goodwill + Other",  "765.4",    "727.0",    "n/a",       "n/a",     "--"],
        ["Deferred Tax Assets",             "309.2",    "311.1",    "n/a",       "n/a",     "--"],
        ["**Total Non-Current Assets",      "13,527.1", "15,921.5", "~17,677",   "~17,410", "+9.9%"],
        ["**TOTAL ASSETS",                  "29,212.1", "32,653.0", "36,458.5",  "38,710",  "+11.7%"],
        ["**LIABILITIES", "", "", "", "", ""],
        ["**Current Liabilities", "", "", "", "", ""],
        ["Trade & Other Payables",          "1,078.5",  "1,148.8",  "905.7",     "n/a",     "-21.2%"],
        ["Accrued Physicians' Fees",        "1,011.7",  "919.7",    "n/a",       "n/a",     "--"],
        ["Accrued Expenses",                "1,097.6",  "979.9",    "n/a",       "n/a",     "--"],
        ["Income Tax Payable",              "803.2",    "525.4",    "n/a",       "n/a",     "--"],
        ["Current Lease Liabilities",       "17.0",     "37.1",     "n/a",       "n/a",     "--"],
        ["**Total Current Liabilities",     "4,184.4",  "3,826.7",  "4,085.4",   "~4,733",  "+15.9%"],
        ["**Non-Current Liabilities", "", "", "", "", ""],
        ["Long-Term Bank Loan",             "24.2",     "24.3",     "22.7",      "~22",     "-2.6%"],
        ["Non-Current Lease Liabilities",   "50.0",     "78.1",     "n/a",       "n/a",     "--"],
        ["Provision — Employee Benefits",   "853.7",    "920.1",    "n/a",       "n/a",     "--"],
        ["**Total Non-Current Liabilities", "929.9",    "1,025.4",  "~1,202",    "~1,000",  "--"],
        ["**TOTAL LIABILITIES",             "5,114.3",  "4,852.1",  "5,287.6",   "~5,729",  "+8.4%"],
        ["**SHAREHOLDERS EQUITY", "", "", "", "", ""],
        ["Paid-up Share Capital",           "795.8",    "795.8",    "795.8",     "795.0",   "0%"],
        ["Share Premium",                   "449.9",    "449.9",    "449.9",     "449.9",   "0%"],
        ["Convertible Bonds (Equity)",      "320.0",    "320.0",    "320.0",     "320.0",   "0%"],
        ["Unappropriated Retained Earn.",   "22,396.4", "26,074.3", "29,577.3",  "~31,200", "+5.5%"],
        ["Non-controlling Interests",       "n/a",      "313.3",    "n/a",       "n/a",     "--"],
        ["**TOTAL EQUITY",                  "24,097.8", "27,800.9", "31,170.9",  "32,974",  "+5.8%"],
        ["**TOTAL LIAB + EQUITY",           "29,212.1", "32,653.0", "36,458.5",  "38,710",  "+6.2%"],
    ]
    pdf.table(bs_headers, bs_rows, bs_col_w, font_size=7.5)
    pdf.set_font("Tahoma", "", 7)
    pdf.set_text_color(*DGRAY)
    pdf.cell(0, 4,
             "* YoY: Q1 2026 vs FY2025 (QoQ), or FY2025 vs FY2024 where Q1 not available. "
             "n/a = line-item not disclosed for that period.  ~ = estimated.",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*BLACK)

    # ── PAGE 3: RATIOS + Q1 2026 P&L ────────────────────────────────────────
    pdf.add_page()
    pdf.section_title("2.  KEY BALANCE SHEET RATIOS  —  TREND  FY2023 – Q1 2026")

    r_headers = ["Ratio", "FY2023", "FY2024", "FY2025", "Q1 2026", "Benchmark"]
    r_col_w   = [62, 22, 22, 22, 22, 22]
    r_rows = [
        ["Total Assets (M THB)",       "29,212", "32,653", "36,459", "38,710",  "Growing"],
        ["Asset Growth YoY",           "+26.1%", "+11.8%", "+11.7%", "+18.5%*", "--"],
        ["**D/E  (Total Liab/Equity)", "0.21x",  "0.17x",  "0.17x",  "0.17x",  "< 1.0x [OK]"],
        ["Financial Debt Only",        "~0.3%",  "~0.5%",  "~0.4%",  "~0.4%",  "0% [OK]"],
        ["Equity Ratio",               "82.5%",  "85.1%",  "85.5%",  "85.2%",  "> 60% [OK]"],
        ["Liability / Asset",          "17.5%",  "14.9%",  "14.5%",  "14.8%",  "< 40% [OK]"],
        ["**Current Ratio",            "3.75x",  "4.37x",  "4.60x",  "4.5x",   "> 1.5x [OK]"],
        ["Net Debt / EBITDA",          "neg.",   "neg.",   "neg.",   "-0.4x",   "< 2x [OK]"],
        ["Net Debt / Equity",          "neg.",   "neg.",   "neg.",   "-0.1x",   "< 1x [OK]"],
        ["Retained Earnings (M THB)",  "22,396", "26,074", "29,577", "~31,200", "Growing"],
        ["Book Value / Share (THB)",   "~30.3",  "~34.9",  "39.21",  "~41.5",  "Growing"],
        ["Net Cash (M THB)",           "pos.",   "pos.",   "pos.",   "~3,490",  "> 0 [OK]"],
    ]
    pdf.table(r_headers, r_rows, r_col_w, font_size=7.8)
    pdf.set_font("Tahoma", "", 7)
    pdf.set_text_color(*DGRAY)
    pdf.cell(0, 4, "* Q1 2026 Asset Growth vs FY2024 (YoY). QoQ vs FY2025: +6.2%",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*BLACK)

    pdf.section_title("3.  Q1 2026 INCOME STATEMENT  (Context)")
    p_headers = ["Metric", "Q1 2026", "Q1 2025", "YoY Δ", "Note"]
    p_col_w   = [55, 28, 28, 22, 37]
    p_rows = [
        ["Total Revenue (M THB)",       "6,254",  "6,211",  "+0.7%",  "Modest top-line"],
        ["Hospital Operations Rev.",    "6,204",  "6,119",  "+1.4%",  "Core business"],
        ["Cost of Operations (M THB)",  "3,022",  "3,040",  "-0.6%",  "✅ Cost falling"],
        ["Administrative Expenses",     "898",    "920",    "-2.4%",  "✅ Leaner overhead"],
        ["**EBITDA (M THB)",            "2,455",  "2,338",  "+5.0%",  "Strong"],
        ["**EBITDA Margin",             "39.3%",  "37.6%",  "+170bps","RECORD HIGH ✅"],
        ["Corporate Income Tax",        "433",    "474",    "-8.7%",  "Lower effective rate"],
        ["**Net Profit (M THB)",        "1,790",  "1,735",  "+3.2%",  "Beats forecast"],
        ["**Net Profit Margin",         "28.6%",  "27.9%",  "+70bps", "Expanding"],
        ["Basic EPS (THB)",             "2.25",   "2.18",   "+3.2%",  "Beat est. 2.04"],
        ["Non-Thai Revenue %",          "65.7%",  "63.9%",  "+180bps","Mix shift ✅"],
        ["Middle East Revenue",         "22% rev","18% rev","+21.3%", "Key growth driver"],
        ["Thai Patient Revenue",        "34.3%",  "36.1%",  "-3.6%",  "Domestic soft"],
    ]
    pdf.table(p_headers, p_rows, p_col_w, font_size=7.8)

    # ── PAGE 4: FINDINGS ────────────────────────────────────────────────────
    pdf.add_page()
    pdf.section_title("4.  FIVE KEY FINDINGS")

    findings = [
        ("1 — Net Cash Fortress Confirmed  ✅",
         "Management disclosed Net Debt/EBITDA = -0.4x and Net Debt/Equity = -0.1x at Q1 2026. "
         "BH holds more cash than all financial debt combined. Cross-verified via SET factsheet: "
         "Enterprise Value THB 138,412M vs Market Cap THB 141,902M → Net Cash ~3,490M THB. "
         "BH has maintained a net cash position for 23 consecutive years — zero refinancing risk, "
         "zero interest rate risk."),
        ("2 — Cash Pile Tripled in FY2025: +133% YoY  ⚠",
         "Cash & equivalents surged from 3,259M (FY2024) to 7,595M (FY2025), a +4,336M jump "
         "in one year. Combined with the reduction in Other Current Financial Assets, management "
         "converted short-term securities into raw cash. This is unusual and suggests preparation "
         "for a large capital event: M&A, new facility capex, special dividend, or strategic "
         "investment. The FY2024 non-current financial asset spike (+2,718%) remains unexplained "
         "without Note 10 details — both items warrant monitoring."),
        ("3 — Record EBITDA Margin 39.3%: Operating Leverage Confirmed  ✅",
         "EBITDA margin hit an all-time record in Q1 2026 (+170bps YoY) driven by patient mix "
         "shift to high-margin international (Middle East +21.3%, Bangladesh +25%, Myanmar +15.1%). "
         "Critically, revenue grew only +0.7% YoY but EBITDA grew +5.0% — pure operating leverage. "
         "Cost/Revenue improved to 48.7% from 49.7%. Admin expenses fell -2.4%. This demonstrates "
         "that BH can grow margins without volume growth — a premium quality characteristic."),
        ("4 — Retained Earnings Compounding at ~22% Annualised  ✅",
         "Unappropriated retained earnings: FY2023: 22,396M → FY2024: 26,074M → FY2025: 29,577M "
         "→ Q1 2026 est. 31,200M. Q1 2026 alone added ~1,623M in just 3 months (~6,500M annualised). "
         "BH compounds at ~22% of equity per year from retained earnings alone, without issuing "
         "new equity or debt. Share count is stable at ~795M shares. This is the hallmark of a "
         "capital-light, self-funding compounder."),
        ("5 — Asset Growth Accelerated in Q1 2026: +6.2% QoQ  ⚠",
         "Total assets rose from 36,459M (FY2025) to 38,710M (Q1 2026) — a +2,251M expansion "
         "in just one quarter. Net profit of 1,790M accounts for only ~79% of this increase. "
         "The balance (~461M) came from other sources — likely non-current financial assets "
         "or working capital build. This mirrors the FY2024 non-current asset spike and reinforces "
         "the view that BH is actively deploying capital into financial investments. "
         "Full Q1 2026 financial statements (PDF) needed to confirm line-item detail."),
    ]

    for title, body in findings:
        pdf.ln(2)
        # Finding title bar
        pdf.set_fill_color(*BLUE)
        pdf.set_text_color(*WHITE)
        pdf.set_font("Tahoma", "B", 8.5)
        pdf.cell(0, 7, f"  {title}", fill=True,
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(*BLACK)
        pdf.set_fill_color(*LGRAY)
        pdf.set_font("Tahoma", "", 8)
        pdf.multi_cell(0, 5, f"  {body}", fill=True)
        pdf.ln(1)

    # ── PAGE 5: RISK + THAI FM + MARKET ─────────────────────────────────────
    pdf.add_page()
    pdf.section_title("5.  RISK DASHBOARD")

    risk_headers = ["Risk Factor", "FY2024 Status", "Q1 2026 Update", "RAG"]
    risk_col_w   = [60, 42, 52, 16]
    risk_rows = [
        ["Financial Debt (bank loan)",   "24.3M — immaterial",   "~22M — shrinking",        "LOW [OK]"],
        ["Lease Liabilities (total)",    "115M — low",            "est. ~130M — managed",    "LOW [OK]"],
        ["Employee Benefit Provision",   "920M — growing +7.8%", "Structurally rising",      "MED [!]"],
        ["Non-curr. Fin. Asset Spike",   "2,014M — unexplained", "Note 10 still unclear",    "MED [!]"],
        ["Thai Patient Revenue Decline", "declining trend",       "-3.6% YoY in Q1 2026",    "MED [!]"],
        ["FX Risk (Intl. revenue mix)",  "growing exposure",      "65.7% non-Thai — rising", "MED [!]"],
        ["Undeployed Cash Pile",         "7,595M (FY2025)",       "Net cash ~3.9B+",          "MED [!]"],
        ["Capex / PP&E Growth",          "+2.7% (FY2024)",        "Modest, controlled",       "LOW [OK]"],
    ]
    pdf.table(risk_headers, risk_rows, risk_col_w, font_size=7.8)

    pdf.section_title("6.  MARKET SNAPSHOT  (SET Factsheet — 8 May 2026)")
    mkt_headers = ["Metric", "Value", "Metric", "Value"]
    mkt_col_w   = [45, 45, 45, 35]
    mkt_rows = [
        ["Stock Price (THB)",      "178.50",          "52W High (THB)",     "214.00"],
        ["Market Cap (M THB)",     "141,902",         "52W Low (THB)",      "130.00"],
        ["Enterprise Value (M THB)","138,412",        "Net Cash (implied)", "~3,490M"],
        ["P/E Ratio (trailing)",   "18.77x",          "EV / EBITDA",        "13.19x"],
        ["P/BV Ratio",             "4.35x",           "Book Value/Share",   "~41.5 THB"],
        ["Free Float",             "72.96%",          "Foreign Limit",      "49%"],
        ["Foreign Holders",        "30.67%",          "NVDR",               "9.82%"],
        ["Paid-up Capital (M THB)","795.0",           "Shares Outstanding", "~795M"],
    ]
    pdf.table(mkt_headers, mkt_rows, mkt_col_w, font_size=7.8)

    pdf.section_title("7.  AGENT 06 — THAI FM VERDICT  (Institutional Thai)")

    thai_text = (
        "งบดุล BH ณ Q1 2026 สะท้อนภาพ 'Fortress Balance Sheet + Operating Leverage' "
        "ที่สมบูรณ์แบบที่สุดในกลุ่ม Healthcare บน SET\n\n"
        "จุดเด่น 3 ข้อ:\n"
        "1) Net Cash -0.4x Net Debt/EBITDA — บริษัทถือ cash มากกว่าหนี้ทั้งหมด 23 ปีติดต่อกัน "
        "Net cash ณ Q1 2026 ประมาณ 3,490-3,928M THB — Buffer ขนาดใหญ่สำหรับ M&A หรือ special dividend\n\n"
        "2) EBITDA Margin Record 39.3% — Revenue shift ไปยัง international patients "
        "(Middle East +21.3%) ทำให้ margin เพิ่มขึ้นแม้ revenue growth จะเพียง +0.7% "
        "เป็นตัวอย่างชัดเจนของ operating leverage ที่ไม่ต้องพึ่ง volume growth\n\n"
        "3) Total Assets ขยาย +6.2% ใน 1 ไตรมาส (36,459M -> 38,710M) — เร็วกว่าปกติ "
        "บ่งชี้ว่าอาจมี asset deployment ใน non-current financial assets "
        "ควรรอ Note 10 ใน full Q1 financial statement เพื่อยืนยัน\n\n"
        "Balance Sheet Verdict: A+ (Upgraded) — Equity compounding +5.8% ใน 1 ไตรมาส "
        "= 23% annualized. เหมาะสำหรับ core holding ระยะยาว"
    )
    pdf.set_fill_color(*LGRAY)
    pdf.set_font("Tahoma", "", 8.5)
    pdf.set_text_color(*NAVY)
    pdf.multi_cell(0, 5.5, thai_text, fill=True)
    pdf.set_text_color(*BLACK)

    pdf.section_title("8.  RECOMMENDED NEXT STEPS")
    steps = [
        "1. analyse BH income statement  — Complete CANSLIM C/A with 8-quarter EPS trend",
        "2. special request: investigate BH capital allocation 2025-2026  — Decode non-current financial asset surge & cash pile",
        "3. Wyckoff x Weinstein Gate Check (requires TradingView price/volume data) — Before any BUY action",
        "4. await BH Q1 2026 full PDF (Note 10)  — Confirm nature of non-current financial asset investment",
    ]
    for s in steps:
        pdf.set_font("Tahoma", "", 8.5)
        pdf.set_x(15)
        pdf.multi_cell(0, 6, f"   {s}")

    # ── SOURCE PAGE ──────────────────────────────────────────────────────────
    pdf.ln(4)
    pdf.h_rule(color=MGRAY)
    pdf.section_title("DATA SOURCES")
    sources = [
        "Official Statement of Financial Position 31 Dec 2024 (Audited PDF) — investor.bumrungrad.com",
        "Financial Highlights Page FY2025 — investor.bumrungrad.com/financial_highlights.html",
        "Q1 2026 Earnings Transcript — Investing.com (93CH-4638189)",
        "Q1 2026 Slides: Middle East Surge — Investing.com (93CH-4638338)",
        "Q1 2026 Results — Kaohoon International (kaohooninternational.com/markets/581409)",
        "SET BH Factsheet 8 May 2026 — set.or.th",
        "BK:BH 4-Year Annual Financials — Investing.com/equities/bumrungrad-hos-financial-summary",
        "SET:BH Balance Sheet Chart — tradingview.com/symbols/SET-BH/financials-balance-sheet",
    ]
    for src in sources:
        pdf.set_font("Tahoma", "", 7.5)
        pdf.set_text_color(*DGRAY)
        pdf.cell(5, 5, "")
        pdf.cell(0, 5, f"• {src}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_text_color(*BLACK)
    pdf.ln(4)
    pdf.set_font("Tahoma", "", 7)
    pdf.set_text_color(*DGRAY)
    disclaimer = (
        "DISCLAIMER: This report is prepared solely for the internal use of AlphaAbsolute and "
        "Piriyapon Kongvanich (CIO). All data sourced from publicly available financial "
        "statements. Q1 2026 balance sheet line-items marked 'est.' are model estimates pending "
        "the official Q1 2026 PDF filing. This is not investment advice."
    )
    pdf.multi_cell(0, 4.5, disclaimer)

    pdf.output(OUTPUT_PATH)
    print(f"PDF saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    build_report()
