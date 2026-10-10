"""
dr_mapper.py — DR Universe mapper for AlphaAbsolute

Maps US tickers → best SET DR ticker (highest liquidity, issuer priority).
Issuer priority: 80 (KTB) > 01 (BLS) > 23 (INVX) > others
Monthly refresh via settrade.com same-origin browser fetch.

Output: data/research/dr/dr_universe.json  (full 551 DRs)
        data/research/dr/dr_map.json        (us_ticker → best DR)
"""
import json
import re
from pathlib import Path
from datetime import datetime, date

ROOT = Path(__file__).resolve().parents[3]
DR_DIR = ROOT / "data/research/dr"
DR_DIR.mkdir(parents=True, exist_ok=True)

UNIVERSE_PATH = DR_DIR / "dr_universe.json"
MAP_PATH = DR_DIR / "dr_map.json"

# Issuer code priority: lower number = higher priority
ISSUER_PRIORITY = {
    "80": 0,   # KTB — first choice
    "01": 1,   # BLS — second choice
    "23": 2,   # INVX — third choice
}

def _issuer_code(set_symbol: str) -> str:
    """Extract numeric issuer code from SET DR symbol e.g. 'NVDA80' -> '80'."""
    m = re.search(r"(\d+)$", set_symbol)
    return m.group(1) if m else "99"


def build_dr_map(rows: list[dict]) -> dict:
    """
    Build us_ticker -> best DR mapping.
    Rules:
      1. Priority: issuer code 80 > 01 > 23 > any other code
      2. Tiebreak within same issuer: pick highest totalValue (most liquid)
      3. Skip ETF-type DRs (underlying contains 'ETF' keyword)
    Returns dict: {us_ticker: {set_symbol, issuer, val, issuer_code}}
    """
    # Dedup rows by symbol (inline seed may contain duplicate entries)
    seen_symbols: set[str] = set()
    deduped = []
    for r in rows:
        sym = r.get("s", "")
        if sym not in seen_symbols:
            seen_symbols.add(sym)
            deduped.append(r)
    rows = deduped

    candidates: dict[str, list] = {}
    for r in rows:
        us = r["u"].strip()
        # Skip ETF-type underlyings
        if "ETF" in us.upper() or "FUND" in us.upper():
            continue
        # Skip non-US (Vietnam VN, HK, Chinese companies listed on HK)
        # We keep them if they're in the watch universe — filter happens at usage
        s = r["s"]
        issuer_code = _issuer_code(s)
        priority = ISSUER_PRIORITY.get(issuer_code, 10)
        val = r.get("val", 0) or 0
        candidates.setdefault(us, []).append({
            "set_symbol": s,
            "issuer": r.get("issuer", ""),
            "issuer_code": issuer_code,
            "priority": priority,
            "val": val,
        })

    dr_map = {}
    for us, cands in candidates.items():
        # Sort: primary key = priority (asc), secondary key = val (desc)
        best = sorted(cands, key=lambda x: (x["priority"], -x["val"]))[0]
        dr_map[us] = {
            "set_symbol": best["set_symbol"],
            "issuer": best["issuer"],
            "issuer_code": best["issuer_code"],
            "val_thb": best["val"],
            "all_dr": [c["set_symbol"] for c in cands],
        }
    return dr_map


def get_dr_for_tickers(us_tickers: list[str]) -> dict:
    """
    Main lookup function. Given list of US tickers, return dict of those
    that have a SET DR, with their best DR info.
    Loads dr_map.json (must exist — run build_from_json first).
    """
    if not MAP_PATH.exists():
        raise FileNotFoundError(f"DR map not found: {MAP_PATH}. Run dr_mapper.py first.")
    dr_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))["map"]
    return {t: dr_map[t] for t in us_tickers if t in dr_map}


def build_from_json(raw_rows: list[dict], fetched_at: str = None) -> None:
    """Save universe JSON and build + save dr_map.json."""
    universe = {
        "fetched_at": fetched_at or datetime.now().isoformat(),
        "total": len(raw_rows),
        "rows": raw_rows,
    }
    UNIVERSE_PATH.write_text(json.dumps(universe, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved universe: {len(raw_rows)} DRs → {UNIVERSE_PATH}")

    dr_map = build_dr_map(raw_rows)
    map_out = {
        "built_at": datetime.now().isoformat(),
        "source_date": fetched_at,
        "us_tickers": len(dr_map),
        "map": dr_map,
    }
    MAP_PATH.write_text(json.dumps(map_out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved DR map: {len(dr_map)} US tickers with SET DRs → {MAP_PATH}")


def needs_refresh() -> bool:
    """Return True if dr_map.json is older than ~30 days or missing."""
    if not MAP_PATH.exists():
        return True
    built_at_str = json.loads(MAP_PATH.read_text(encoding="utf-8")).get("built_at", "")
    if not built_at_str:
        return True
    try:
        built_date = datetime.fromisoformat(built_at_str).date()
        return (date.today() - built_date).days >= 28
    except ValueError:
        return True


# ── Inline data from browser fetch 2026-10-10 ─────────────────────────────────
DR_ROWS_2026_10_10 = json.loads(r"""
[{"s":"AAOI03","u":"AAOI","issuer":"PI","val":4450091.75},{"s":"AAOI23","u":"AAOI","issuer":"INVX","val":2338118.62},{"s":"AAPL01","u":"AAPL","issuer":"BLS","val":201262.75},{"s":"AAPL03","u":"AAPL","issuer":"PI","val":116108.6},{"s":"AAPL19","u":"AAPL","issuer":"YUANTA","val":26869.3},{"s":"AAPL80","u":"AAPL","issuer":"KTB","val":9178527.7},{"s":"ABBV19","u":"ABBV","issuer":"YUANTA","val":26601.15},{"s":"ABBV80","u":"ABBV","issuer":"KTB","val":167540.65},{"s":"ABNB06","u":"ABNB","issuer":"KKPS","val":9408.9},{"s":"ADBE03","u":"ADBE","issuer":"PI","val":35658.36},{"s":"ADBE06","u":"ADBE","issuer":"KKPS","val":764698.1},{"s":"AFRM03","u":"AFRM","issuer":"PI","val":104327.44},{"s":"AIRBUS80","u":"AIRBUS","issuer":"KTB","val":4570.24},{"s":"ALAB01","u":"ALAB","issuer":"BLS","val":10758346.84},{"s":"ALAB23","u":"ALAB","issuer":"INVX","val":4841233.96},{"s":"AMAT01","u":"AMAT","issuer":"BLS","val":863647.44},{"s":"AMAT19","u":"AMAT","issuer":"YUANTA","val":82807.04},{"s":"AMAT23","u":"AMAT","issuer":"INVX","val":568083.9},{"s":"AMAT80","u":"AMAT","issuer":"KTB","val":81165.6},{"s":"AMD03","u":"AMD","issuer":"PI","val":558538.9},{"s":"AMD23","u":"AMD","issuer":"INVX","val":691604.45},{"s":"AMD80","u":"AMD","issuer":"KTB","val":16451854.16},{"s":"AMGN06","u":"AMGN","issuer":"KKPS","val":16949.68},{"s":"AMKR03","u":"AMKR","issuer":"PI","val":396819.07},{"s":"AMKR23","u":"AMKR","issuer":"INVX","val":2876049.74},{"s":"AMPX03","u":"AMPX","issuer":"PI","val":4055.8},{"s":"AMZN01","u":"AMZN","issuer":"BLS","val":974785},{"s":"AMZN03","u":"AMZN","issuer":"PI","val":64453.44},{"s":"AMZN06","u":"AMZN","issuer":"KKPS","val":662421.2},{"s":"AMZN19","u":"AMZN","issuer":"YUANTA","val":2.18},{"s":"AMZN23","u":"AMZN","issuer":"INVX","val":3405226.8},{"s":"AMZN80","u":"AMZN","issuer":"KTB","val":12780132.82},{"s":"ANET23","u":"ANET","issuer":"INVX","val":357049.7},{"s":"ANET80","u":"ANET","issuer":"KTB","val":31472.8},{"s":"APLD03","u":"APLD","issuer":"PI","val":964582.98},{"s":"APPL03","u":"APPL","issuer":"PI","val":658868.31},{"s":"ASML01","u":"ASML","issuer":"BLS","val":853671.25},{"s":"ASTS01","u":"ASTS","issuer":"BLS","val":17580383.58},{"s":"ASTS03","u":"ASTS","issuer":"PI","val":32645680.12},{"s":"ASTS23","u":"ASTS","issuer":"INVX","val":5415609.59},{"s":"AVGO23","u":"AVGO","issuer":"INVX","val":1061184.22},{"s":"AVGO80","u":"AVGO","issuer":"KTB","val":5986310.16},{"s":"AXP06","u":"AXP","issuer":"KKPS","val":204017.92},{"s":"BAC03","u":"BAC","issuer":"PI","val":188096.38},{"s":"BDX06","u":"BDX","issuer":"KKPS","val":295.5},{"s":"BE03","u":"BE","issuer":"PI","val":1538048.48},{"s":"BE80","u":"BE","issuer":"KTB","val":615587.52},{"s":"BKNG03","u":"BKNG","issuer":"PI","val":21681},{"s":"BKNG80","u":"BKNG","issuer":"KTB","val":83586.8},{"s":"BKSY03","u":"BKSY","issuer":"PI","val":17860.3},{"s":"BLK06","u":"BLK","issuer":"KKPS","val":58207.22},{"s":"BOEING80","u":"BOEING","issuer":"KTB","val":33015.75},{"s":"BRKB23","u":"BRKB","issuer":"INVX","val":35177.55},{"s":"BRKB80","u":"BRKB","issuer":"KTB","val":882574.54},{"s":"CAT19","u":"CAT","issuer":"YUANTA","val":24206.65},{"s":"CAT80","u":"CAT","issuer":"KTB","val":22435.44},{"s":"CBRS03","u":"CBRS","issuer":"PI","val":8283467.6},{"s":"CBRS80","u":"CBRS","issuer":"KTB","val":871651.72},{"s":"CCJ23","u":"CCJ","issuer":"INVX","val":183136.67},{"s":"CDNS23","u":"CDNS","issuer":"INVX","val":703.4},{"s":"CEG23","u":"CEG","issuer":"INVX","val":1175663.54},{"s":"CIEN03","u":"CIEN","issuer":"PI","val":585631.95},{"s":"CME03","u":"CME","issuer":"PI","val":1999.92},{"s":"COHR23","u":"COHR","issuer":"INVX","val":4835849.55},{"s":"COHR80","u":"COHR","issuer":"KTB","val":14483767.5},{"s":"COIN01","u":"COIN","issuer":"BLS","val":4494200.28},{"s":"COIN23","u":"COIN","issuer":"INVX","val":1474547.96},{"s":"COIN80","u":"COIN","issuer":"KTB","val":1565136.9},{"s":"COSTCO19","u":"COSTCO","issuer":"YUANTA","val":19523.25},{"s":"COSTCO80","u":"COSTCO","issuer":"KTB","val":2577.6},{"s":"CRDO23","u":"CRDO","issuer":"INVX","val":2281398.45},{"s":"CRDO80","u":"CRDO","issuer":"KTB","val":205686.22},{"s":"CRM01","u":"CRM","issuer":"BLS","val":803668.76},{"s":"CRM06","u":"CRM","issuer":"KKPS","val":30898.9},{"s":"CRM80","u":"CRM","issuer":"KTB","val":102295},{"s":"CRSP03","u":"CRSP","issuer":"PI","val":78041.6},{"s":"CRWD06","u":"CRWD","issuer":"KKPS","val":372391.65},{"s":"CRWD80","u":"CRWD","issuer":"KTB","val":2647626.25},{"s":"CRWV03","u":"CRWV","issuer":"PI","val":1362851.46},{"s":"CRWV23","u":"CRWV","issuer":"INVX","val":1800601.66},{"s":"CSCO06","u":"CSCO","issuer":"KKPS","val":617164.7},{"s":"DASH03","u":"DASH","issuer":"PI","val":262396.72},{"s":"DDOG19","u":"DDOG","issuer":"YUANTA","val":718762.85},{"s":"DELL19","u":"DELL","issuer":"YUANTA","val":17208619.2},{"s":"DELL23","u":"DELL","issuer":"INVX","val":735172.58},{"s":"DELL80","u":"DELL","issuer":"KTB","val":1717134.88},{"s":"DISNEY19","u":"DISNEY","issuer":"YUANTA","val":55419.7},{"s":"EOSE03","u":"EOSE","issuer":"PI","val":15262172.09},{"s":"EOSE23","u":"EOSE","issuer":"INVX","val":2637360.31},{"s":"ESTEE80","u":"ESTEE","issuer":"KTB","val":64898.66},{"s":"ETN03","u":"ETN","issuer":"PI","val":7440},{"s":"ETN23","u":"ETN","issuer":"INVX","val":160380.35},{"s":"FABRINET03","u":"FABRINET","issuer":"PI","val":172986.7},{"s":"FABRINET23","u":"FABRINET","issuer":"INVX","val":2920665.46},{"s":"FABRINET80","u":"FABRINET","issuer":"KTB","val":49868},{"s":"FCX23","u":"FCX","issuer":"INVX","val":62553.38},{"s":"FSLR03","u":"FSLR","issuer":"PI","val":4794.97},{"s":"FTNT03","u":"FTNT","issuer":"PI","val":37097.48},{"s":"GEV23","u":"GEV","issuer":"INVX","val":544741.98},{"s":"GEV80","u":"GEV","issuer":"KTB","val":473373.75},{"s":"GFS03","u":"GFS","issuer":"PI","val":282817.92},{"s":"GLW80","u":"GLW","issuer":"KTB","val":345572.25},{"s":"GOOG06","u":"GOOG","issuer":"KKPS","val":79881.92},{"s":"GOOG23","u":"GOOG","issuer":"INVX","val":1582133.64},{"s":"GOOG80","u":"GOOG","issuer":"KTB","val":47540921.8},{"s":"GOOGL01","u":"GOOGL","issuer":"BLS","val":472010.5},{"s":"GOOGL03","u":"GOOGL","issuer":"PI","val":76188.9},{"s":"GOOGL19","u":"GOOGL","issuer":"YUANTA","val":3504.6},{"s":"GRAB80","u":"GRAB","issuer":"KTB","val":1600069.9},{"s":"GSUS06","u":"GSUS","issuer":"KKPS","val":463122.46},{"s":"HERMES80","u":"HERMES","issuer":"KTB","val":8347},{"s":"HIMS03","u":"HIMS","issuer":"PI","val":1978030.9},{"s":"HOOD03","u":"HOOD","issuer":"PI","val":238557.7},{"s":"HOOD06","u":"HOOD","issuer":"KKPS","val":1617365.9},{"s":"HOOD80","u":"HOOD","issuer":"KTB","val":585503.38},{"s":"HPE80","u":"HPE","issuer":"KTB","val":679093.7},{"s":"IBM06","u":"IBM","issuer":"KKPS","val":481175.54},{"s":"IBM23","u":"IBM","issuer":"INVX","val":762},{"s":"INTEL01","u":"INTEL","issuer":"BLS","val":7654713.98},{"s":"INTEL03","u":"INTEL","issuer":"PI","val":2999118.35},{"s":"INTEL19","u":"INTEL","issuer":"YUANTA","val":3289483.6},{"s":"INTEL23","u":"INTEL","issuer":"INVX","val":1907673.6},{"s":"INTEL80","u":"INTEL","issuer":"KTB","val":3678182.38},{"s":"IONQ03","u":"IONQ","issuer":"PI","val":1112696.22},{"s":"IONQ23","u":"IONQ","issuer":"INVX","val":1637908.24},{"s":"ISRG01","u":"ISRG","issuer":"BLS","val":242592},{"s":"ISRG06","u":"ISRG","issuer":"KKPS","val":41967.4},{"s":"ISRG19","u":"ISRG","issuer":"YUANTA","val":2968.92},{"s":"ISRG80","u":"ISRG","issuer":"KTB","val":124096},{"s":"JNJ03","u":"JNJ","issuer":"PI","val":80886.62},{"s":"JOBY03","u":"JOBY","issuer":"PI","val":121031.84},{"s":"JPMUS06","u":"JPMUS","issuer":"KKPS","val":43663},{"s":"JPMUS19","u":"JPMUS","issuer":"YUANTA","val":251944.5},{"s":"KLAC01","u":"KLAC","issuer":"BLS","val":770338.16},{"s":"KLAC19","u":"KLAC","issuer":"YUANTA","val":37561.6},{"s":"KLAC23","u":"KLAC","issuer":"INVX","val":1170149.35},{"s":"KO80","u":"KO","issuer":"KTB","val":418739.8},{"s":"LITE01","u":"LITE","issuer":"BLS","val":8141304.35},{"s":"LITE23","u":"LITE","issuer":"INVX","val":2771609.05},{"s":"LITE80","u":"LITE","issuer":"KTB","val":29101479.22},{"s":"LLY23","u":"LLY","issuer":"INVX","val":219371},{"s":"LLY80","u":"LLY","issuer":"KTB","val":3238768.14},{"s":"LRCX01","u":"LRCX","issuer":"BLS","val":724037.4},{"s":"LRCX19","u":"LRCX","issuer":"YUANTA","val":14006.9},{"s":"LRCX23","u":"LRCX","issuer":"INVX","val":1813077.18},{"s":"LRCX80","u":"LRCX","issuer":"KTB","val":1163651.4},{"s":"LULU06","u":"LULU","issuer":"KKPS","val":428495.74},{"s":"LVMH01","u":"LVMH","issuer":"BLS","val":170613.25},{"s":"MA80","u":"MA","issuer":"KTB","val":48921.48},{"s":"MELI06","u":"MELI","issuer":"KKPS","val":147367.36},{"s":"MELI23","u":"MELI","issuer":"INVX","val":31.75},{"s":"META01","u":"META","issuer":"BLS","val":242462.95},{"s":"META06","u":"META","issuer":"KKPS","val":88687.5},{"s":"META23","u":"META","issuer":"INVX","val":81833.4},{"s":"META80","u":"META","issuer":"KTB","val":4609089.04},{"s":"MICRON01","u":"MICRON","issuer":"BLS","val":81488161.6},{"s":"MICRON03","u":"MICRON","issuer":"PI","val":371717.9},{"s":"MICRON19","u":"MICRON","issuer":"YUANTA","val":8166222.3},{"s":"MICRON23","u":"MICRON","issuer":"INVX","val":10297963.6},{"s":"MICRON80","u":"MICRON","issuer":"KTB","val":8652070.25},{"s":"MKSI03","u":"MKSI","issuer":"PI","val":47194.16},{"s":"MNST06","u":"MNST","issuer":"KKPS","val":22108.02},{"s":"MP23","u":"MP","issuer":"INVX","val":989588.85},{"s":"MP80","u":"MP","issuer":"KTB","val":201155.1},{"s":"MPWR23","u":"MPWR","issuer":"INVX","val":147937.46},{"s":"MRAM03","u":"MRAM","issuer":"PI","val":44777.51},{"s":"MRVL06","u":"MRVL","issuer":"KKPS","val":317397.05},{"s":"MRVL23","u":"MRVL","issuer":"INVX","val":1391843.45},{"s":"MRVL80","u":"MRVL","issuer":"KTB","val":20478022.95},{"s":"MS06","u":"MS","issuer":"KKPS","val":496158.02},{"s":"MSFT01","u":"MSFT","issuer":"BLS","val":1074664.5},{"s":"MSFT03","u":"MSFT","issuer":"PI","val":11955.22},{"s":"MSFT06","u":"MSFT","issuer":"KKPS","val":714561.72},{"s":"MSFT19","u":"MSFT","issuer":"YUANTA","val":1770},{"s":"MSFT23","u":"MSFT","issuer":"INVX","val":27600.82},{"s":"MSFT80","u":"MSFT","issuer":"KTB","val":3327405.8},{"s":"NBIS01","u":"NBIS","issuer":"BLS","val":3686693.18},{"s":"NBIS03","u":"NBIS","issuer":"PI","val":7159799.15},{"s":"NBIS23","u":"NBIS","issuer":"INVX","val":2199713.35},{"s":"NBIS80","u":"NBIS","issuer":"KTB","val":4365892.8},{"s":"NDAQ06","u":"NDAQ","issuer":"KKPS","val":76190.7},{"s":"NEE80","u":"NEE","issuer":"KTB","val":8604.56},{"s":"NEM06","u":"NEM","issuer":"KKPS","val":39823.06},{"s":"NEM23","u":"NEM","issuer":"INVX","val":373014.34},{"s":"NET03","u":"NET","issuer":"PI","val":1521184.8},{"s":"NFLX06","u":"NFLX","issuer":"KKPS","val":1458214.5},{"s":"NFLX80","u":"NFLX","issuer":"KTB","val":4522781.82},{"s":"NIKE80","u":"NIKE","issuer":"KTB","val":56489.9},{"s":"NOVOB80","u":"NOVOB","issuer":"KTB","val":1352839.96},{"s":"NOW19","u":"NOW","issuer":"YUANTA","val":548550.24},{"s":"NOW23","u":"NOW","issuer":"INVX","val":175893.96},{"s":"NVDA01","u":"NVDA","issuer":"BLS","val":2916082.9},{"s":"NVDA03","u":"NVDA","issuer":"PI","val":47890.25},{"s":"NVDA06","u":"NVDA","issuer":"KKPS","val":1422174.7},{"s":"NVDA19","u":"NVDA","issuer":"YUANTA","val":180360},{"s":"NVDA23","u":"NVDA","issuer":"INVX","val":6983168.7},{"s":"NVDA80","u":"NVDA","issuer":"KTB","val":17101519},{"s":"NVTS03","u":"NVTS","issuer":"PI","val":1266154.38},{"s":"NVTS23","u":"NVTS","issuer":"INVX","val":1011567.08},{"s":"OKLO03","u":"OKLO","issuer":"PI","val":745394.34},{"s":"OKLO23","u":"OKLO","issuer":"INVX","val":1137621.22},{"s":"ON23","u":"ON","issuer":"INVX","val":650338},{"s":"ONDS03","u":"ONDS","issuer":"PI","val":2205652.9},{"s":"ONON03","u":"ONON","issuer":"PI","val":305252.06},{"s":"ORCL01","u":"ORCL","issuer":"BLS","val":848261.4},{"s":"ORCL06","u":"ORCL","issuer":"KKPS","val":1922715.85},{"s":"ORCL19","u":"ORCL","issuer":"YUANTA","val":1034301.22},{"s":"ORCL23","u":"ORCL","issuer":"INVX","val":26496.32},{"s":"ORCL80","u":"ORCL","issuer":"KTB","val":1765159.76},{"s":"OXY03","u":"OXY","issuer":"PI","val":469853.68},{"s":"PANW19","u":"PANW","issuer":"YUANTA","val":480653.7},{"s":"PANW80","u":"PANW","issuer":"KTB","val":3497723.4},{"s":"PEP80","u":"PEP","issuer":"KTB","val":989264.85},{"s":"PFIZER19","u":"PFIZER","issuer":"YUANTA","val":784188.3},{"s":"PLAB03","u":"PLAB","issuer":"PI","val":310046.68},{"s":"PLTR01","u":"PLTR","issuer":"BLS","val":5732367.15},{"s":"PLTR03","u":"PLTR","issuer":"PI","val":167983.58},{"s":"PLTR06","u":"PLTR","issuer":"KKPS","val":5062703.62},{"s":"PLTR23","u":"PLTR","issuer":"INVX","val":1257915.48},{"s":"PLTR80","u":"PLTR","issuer":"KTB","val":491931.9},{"s":"PWR03","u":"PWR","issuer":"PI","val":235702},{"s":"PWR80","u":"PWR","issuer":"KTB","val":23320},{"s":"QBTS03","u":"QBTS","issuer":"PI","val":99651.08},{"s":"QCOM06","u":"QCOM","issuer":"KKPS","val":663379.54},{"s":"QCOM23","u":"QCOM","issuer":"INVX","val":371810.24},{"s":"QCOM80","u":"QCOM","issuer":"KTB","val":45047.3},{"s":"RBLX06","u":"RBLX","issuer":"KKPS","val":3055165.2},{"s":"RGTI03","u":"RGTI","issuer":"PI","val":252320.26},{"s":"RGTI23","u":"RGTI","issuer":"INVX","val":863308.36},{"s":"RKLB01","u":"RKLB","issuer":"BLS","val":2035054.14},{"s":"RKLB03","u":"RKLB","issuer":"PI","val":762655.92},{"s":"RKLB23","u":"RKLB","issuer":"INVX","val":8836240.48},{"s":"RKLB80","u":"RKLB","issuer":"KTB","val":10972467.14},{"s":"SBUX80","u":"SBUX","issuer":"KTB","val":85545.78},{"s":"SEAGATE23","u":"SEAGATE","issuer":"INVX","val":2718106.5},{"s":"SEAGATE80","u":"SEAGATE","issuer":"KTB","val":783747.32},{"s":"SHOP03","u":"SHOP","issuer":"PI","val":1116},{"s":"SHOP06","u":"SHOP","issuer":"KKPS","val":103113.12},{"s":"SIL03","u":"SIL","issuer":"PI","val":119373.96},{"s":"SMCI03","u":"SMCI","issuer":"PI","val":1606553.42},{"s":"SMR03","u":"SMR","issuer":"PI","val":117837.2},{"s":"SMR23","u":"SMR","issuer":"INVX","val":123089.71},{"s":"SNDK03","u":"SNDK","issuer":"PI","val":603241.9},{"s":"SNDK23","u":"SNDK","issuer":"INVX","val":5789768.5},{"s":"SNDK80","u":"SNDK","issuer":"KTB","val":23592241.7},{"s":"SNOW06","u":"SNOW","issuer":"KKPS","val":505859.75},{"s":"SNOW23","u":"SNOW","issuer":"INVX","val":2890454.3},{"s":"SOFI23","u":"SOFI","issuer":"INVX","val":1521388},{"s":"SPACEX01","u":"SPACEX","issuer":"BLS","val":17939235.4},{"s":"SPACEX03","u":"SPACEX","issuer":"PI","val":2154906.48},{"s":"SPACEX06","u":"SPACEX","issuer":"KKPS","val":447836.3},{"s":"SPACEX23","u":"SPACEX","issuer":"INVX","val":1627828.7},{"s":"SPACEX80","u":"SPACEX","issuer":"KTB","val":20426930.2},{"s":"STM03","u":"STM","issuer":"PI","val":8750},{"s":"SYM03","u":"SYM","issuer":"PI","val":1.8},{"s":"SYM23","u":"SYM","issuer":"INVX","val":8098.75},{"s":"SYNP03","u":"SYNP","issuer":"PI","val":305.4},{"s":"SYNP23","u":"SYNP","issuer":"INVX","val":74563.75},{"s":"TER01","u":"TER","issuer":"BLS","val":127744.24},{"s":"TER23","u":"TER","issuer":"INVX","val":71389.1},{"s":"TER80","u":"TER","issuer":"KTB","val":318482.5},{"s":"TSEMI03","u":"TSEMI","issuer":"PI","val":186668.09},{"s":"TSEMI23","u":"TSEMI","issuer":"INVX","val":853892},{"s":"TSEMI80","u":"TSEMI","issuer":"KTB","val":301177.82},{"s":"TSLA01","u":"TSLA","issuer":"BLS","val":1796404.75},{"s":"TSLA03","u":"TSLA","issuer":"PI","val":3141.02},{"s":"TSLA23","u":"TSLA","issuer":"INVX","val":947414.26},{"s":"TSLA80","u":"TSLA","issuer":"KTB","val":13136448.1},{"s":"TTMI80","u":"TTMI","issuer":"KTB","val":30861},{"s":"UBER06","u":"UBER","issuer":"KKPS","val":2278719.72},{"s":"UNH19","u":"UNH","issuer":"YUANTA","val":546642},{"s":"USAR03","u":"USAR","issuer":"PI","val":304303.79},{"s":"VICR80","u":"VICR","issuer":"KTB","val":223085.7},{"s":"VISA06","u":"VISA","issuer":"KKPS","val":1459797.01},{"s":"VISA80","u":"VISA","issuer":"KTB","val":1803250.24},{"s":"VRT01","u":"VRT","issuer":"BLS","val":2057802.86},{"s":"VRT23","u":"VRT","issuer":"INVX","val":5921292.92},{"s":"VRT80","u":"VRT","issuer":"KTB","val":456895.7},{"s":"VST03","u":"VST","issuer":"PI","val":3526732.98},{"s":"WDC03","u":"WDC","issuer":"PI","val":7061978.97},{"s":"WMT06","u":"WMT","issuer":"KKPS","val":175447.16},{"s":"WMT80","u":"WMT","issuer":"KTB","val":13142.24}]
""")

# MICRON special case: MICRON01 (BLS) has highest liquidity (81M THB) despite 80 priority rule
# User said: pick 80 first, then 01, then 23 — so MICRON80 is correct by rule.
# But note: MICRON01 has 81.5M vs MICRON80 8.6M. The rule is issuer code, not liquidity.
# This is as designed per user instruction.


def run():
    """Bootstrap: build dr_map from inline data."""
    print("Building DR map from 2026-10-10 fetch data...")
    build_from_json(DR_ROWS_2026_10_10, fetched_at="2026-10-10T03:20:09+07:00")

    # Quick validation
    dr_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))["map"]
    print(f"\nSample mappings:")
    for us in ["NVDA", "MICRON", "MRVL", "COHR", "LITE", "RKLB", "ALAB", "DELL", "ASTS", "NBIS"]:
        if us in dr_map:
            d = dr_map[us]
            print(f"  {us:10s} -> {d['set_symbol']:15s} ({d['issuer']:6s}) val={d['val_thb']:>12,.0f} THB  alts={d['all_dr']}")

    print(f"\nTotal US tickers with SET DR: {len(dr_map)}")

    # Show MICRON specifically since it was used as example
    if "MICRON" in dr_map:
        d = dr_map["MICRON"]
        print(f"\nMICRON detail: best={d['set_symbol']} (priority 80=KTB, even though 01 has more volume)")
        print(f"  All DRs: {d['all_dr']}")


if __name__ == "__main__":
    run()
