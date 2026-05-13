"""
AlphaPULSE Research Brief — Prompt Builder
Gathers FRED macro data + user Bloomberg input, then builds a ready-to-use
prompt for Claude Code. No API key needed.

Run: python scripts\research_brief.py
Then: copy the prompt from output\prompt_YYMMDD.txt and paste into Claude Code.

Claude Code will write the full Thai research brief for you.
"""

import sys
import os
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import glob
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import pandas as pd
from datetime import datetime, timedelta
from io import StringIO

ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR   = os.path.join(ROOT, "data")
OUTPUT_DIR = os.path.join(ROOT, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="

SERIES = [
    ("Fed Funds Rate (%)",        "FEDFUNDS",     "%"),
    ("US 10Y Treasury Yield (%)", "DGS10",        "%"),
    ("US 2Y Treasury Yield (%)",  "DGS2",         "%"),
    ("Yield Curve 10Y-2Y (bps)",  "T10Y2Y",       "bps"),
    ("US CPI Index",              "CPIAUCSL",     "index"),
    ("USD Index (DXY)",           "DTWEXBGS",     "index"),
    ("Brent Crude Oil ($/bbl)",   "DCOILBRENTEU", "$/bbl"),
    ("US Unemployment Rate (%)",  "UNRATE",       "%"),
    ("US M2 Money Supply ($B)",   "M2SL",         "$B"),
]

def fetch_fred(fred_id, months_back=6):
    try:
        resp = requests.get(FRED_BASE + fred_id, timeout=15, verify=False)
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text))
        df.columns = ["DATE", fred_id]
        df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
        df = df.dropna(subset=["DATE"]).set_index("DATE")
        df[fred_id] = pd.to_numeric(df[fred_id].replace(".", pd.NA), errors="coerce")
        cutoff = datetime.today() - timedelta(days=months_back * 31)
        return df[df.index >= cutoff][fred_id].dropna()
    except Exception as e:
        print(f"  WARNING: {fred_id}: {e}")
        return pd.Series(dtype=float)

def load_user_input():
    path = os.path.join(DATA_DIR, "user_input.txt")
    if not os.path.exists(path):
        return "(ไม่มีข้อมูลจาก Bloomberg/BLS)"
    with open(path, encoding="utf-8") as f:
        content = f.read().strip()
    # Check if user has actually filled it in (not just the template)
    filled_lines = [l for l in content.splitlines()
                    if ":" in l and not l.startswith("#") and l.split(":")[-1].strip()]
    if len(filled_lines) < 3:
        return "(user_input.txt ยังไม่ได้ใส่ข้อมูล Bloomberg/BLS)"
    return content

def main():
    today     = datetime.today()
    date_str  = today.strftime("%y%m%d")
    date_eng  = today.strftime("%d %B %Y")
    thai_months = ["ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.",
                   "ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]
    date_thai = f"{today.day} {thai_months[today.month-1]} {today.year+543}"

    print("=" * 60)
    print("AlphaPULSE Research Brief — Prompt Builder")
    print(f"Date: {date_eng}")
    print("=" * 60)

    # Fetch FRED data
    print("\nFetching FRED macro data...")
    macro_lines = []
    for name, fred_id, unit in SERIES:
        s = fetch_fred(fred_id)
        if s.empty:
            macro_lines.append(f"- {name}: N/A")
        else:
            latest = round(s.iloc[-1], 3)
            as_of  = s.index[-1].strftime("%Y-%m-%d")
            # MoM change
            mom = round(s.iloc[-1] - s.iloc[-2], 3) if len(s) >= 2 else None
            mom_str = f"  (MoM: {mom:+.3f})" if mom is not None else ""
            macro_lines.append(f"- {name}: {latest} {unit}{mom_str}  [as of {as_of}]")
            print(f"  {name}: {latest} {unit}")

    macro_block = "\n".join(macro_lines)

    # User Bloomberg data
    print("\nLoading Bloomberg/BLS data...")
    user_block = load_user_input()

    # Build prompt
    prompt = f"""กรุณาเขียน AlphaPULSE Research Brief ภาษาไทย สำหรับสัปดาห์ {date_thai}

โปรดใช้ข้อมูลต่อไปนี้เท่านั้น อย่าแต่งตัวเลขขึ้นมาเอง:

=== FRED MACRO DATA (ล่าสุด ณ {date_eng}) ===
{macro_block}

=== Bloomberg / BLS Internal Data ===
{user_block}

=== รูปแบบที่ต้องการ ===

# AlphaPULSE Research Brief — {date_eng}

## ภาพรวม Macro Regime
[2-3 ประโยค: ระบุโหมดตลาด Risk-on/Risk-off, Bull/Bear/Accumulation พร้อมอ้างอิงตัวเลข FRED ข้างต้น]

## ปัจจัยขับเคลื่อนตลาด (Key Factors)
[6-8 ข้อ — แต่ละข้อมีรูปแบบ: หมายเลข) บริบท + ตัวเลขเฉพาะเจาะจง + นัยการลงทุน]
[ใช้ภาษาไทย + คำศัพท์การเงินอังกฤษในวงเล็บ เช่น risk premium, NIM, YoY, QoQ]
[ตัวอย่างสไตล์: "ความไม่แน่นอนจากสงครามยังอยู่ในระดับสูง...ทำให้ risk premium ยังไม่หาย ราคาน้ำมัน Brent เคลื่อนไหวเหนือ $100/bbl"]

## กลยุทธ์การลงทุน
[1-2 investment themes พร้อมเหตุผล]

## กลุ่มหุ้นที่น่าสนใจ
[กลุ่ม sector ที่ได้ประโยชน์จาก themes ข้างต้น]

## ความเสี่ยงที่ต้องติดตาม
[3-4 ข้อ downside risks]
"""

    # Save prompt to file
    prompt_path = os.path.join(OUTPUT_DIR, f"prompt_{date_str}.txt")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)

    print(f"\nPrompt saved: {prompt_path}")
    print()
    print("=" * 60)
    print("NEXT STEP — Copy and paste the prompt into Claude Code:")
    print("=" * 60)
    print()
    print(prompt)
    print()
    print("=" * 60)
    print("Copy everything between the lines above and paste into")
    print("your Claude Code chat window. Claude will write the brief.")
    print("Save the output as: output\\research_brief_" + date_str + ".md")
    print("Then run: python scripts\\generate_alphapulse.py")

if __name__ == "__main__":
    main()
