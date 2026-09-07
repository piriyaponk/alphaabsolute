"""
AlphaAbsolute v2 -- Universe Builder
=====================================
Universe = NYSE + NASDAQ — ทุกหุ้นที่จดทะเบียนในสองตลาด ~6,000 ตัว

Source: Polygon reference API
  XNAS (NASDAQ) → ~3,300 active common stocks
  XNYS (NYSE)   → ~2,700 active common stocks
  Total         → ~6,000 unique US stocks (deduplicated, type=CS only)

Filter: letters-only ticker 1-5 chars (ตัด warrants/units/preferreds ออก)
Delay: 13s between pages (Polygon free tier: 5 req/min)
Time:  ~2 minutes for full fetch (runs weekly on Fridays)

Output:
  data/universe/full_universe.json   -- all tickers + metadata
  data/universe/active_universe.json -- legacy compat

Run: Weekly on Fridays (same as rs_benchmark.py)
     Manual: python scripts/pre_compute/universe_builder.py
"""

import sys
import json
import os
import time
import ssl
import urllib.request
import urllib.error
import re
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT    = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "universe"
OUT_DIR.mkdir(parents=True, exist_ok=True)

FULL_FILE    = OUT_DIR / "full_universe.json"
ACTIVE_FILE  = OUT_DIR / "active_universe.json"  # legacy compat
CACHE_FILE   = OUT_DIR / "_constituent_cache.json"
CACHE_DAYS   = 7

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

# ── Load .env ─────────────────────────────────────────────────────────────────
def _load_env() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        for ln in env_path.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
_load_env()

# ── Always-include: AlphaAbsolute thematic watchlist ─────────────────────────
ALWAYS_INCLUDE = sorted({
    "MU","WDC","STX","MRAM","RKLB","LUNR","ASTS","RDW","PL",
    "IONQ","RGTI","QUBT","LITE","COHR","AAOI","IPGP",
    "CRWV","CORZ","CLSK","NNE","OKLO","SMR","BWXT",
    "ACHR","JOBY","RCAT","AVAV","KTOS",
    "ONTO","CAMT","COHU","PLAB","KLIC","ACMR","AEIS",
    "PLTR","AXON","CACI","LDOS","SAIC","BAH",
    "AVGO","MRVL","NVDA","AMD","MPWR","ON","NXPI","CIEN",
    "SOUN","AI","BBAI","VRT","ANET","APH","EME","PWR",
    "MSFT","AAPL","GOOGL","AMZN","META","TSLA",
    # Photonics / microcap monsters
    "LWLG","POET","IIVI","VIAV","FNSR","NPTN",
    # Space emerging
    "KEEL","MNTS","SATL","IRDM","SPIR",
    # AI Infrastructure emerging
    "NBIS","GFAI","PRCT",
    # Memory / bottleneck
    "ACMR","UCTT","FORM","PLAB",
    # NeoCloud
    "BTBT","HUT","IREN","WULF","CLSK","MARA","RIOT",
    # Quantum
    "ARQQ","QBTS","IQM",
    # Defense micro
    "RCAT","BKSY","SPIR","HII",
})

ALWAYS_EXCLUDE = {"SPY","QQQ","IWM","MDY","VOO","VTI","DIA","GLD","SLV",
                  "USO","VIX","^VIX","^GSPC","^IXIC","^RUT","GOOG",""}

# ── Hardcoded fallback lists (last updated 2026-05) ──────────────────────────
# Used when Wikipedia scrape fails (SSL/network issues)

SP500_CORE = [
    # Technology
    "AAPL","MSFT","NVDA","AVGO","ORCL","CRM","CSCO","ADBE","AMD","QCOM",
    "AMAT","KLAC","LRCX","MU","TXN","MRVL","NXPI","ON","STX","WDC",
    "INTC","ANET","FTNT","PANW","CDNS","SNPS","ANSS","GDDY","CTSH","EPAM",
    # Communications
    "META","GOOGL","GOOG","NFLX","DIS","TMUS","VZ","T","CMCSA","CHTR",
    # Consumer
    "AMZN","TSLA","HD","MCD","SBUX","NKE","LOW","TGT","COST","WMT",
    "BKNG","MAR","HLT","MGM","EXPE",
    # Healthcare
    "LLY","UNH","JNJ","ABBV","MRK","PFE","TMO","DHR","ABT","ISRG",
    "VRTX","REGN","BIIB","AMGN","GILD","MRNA","IDXX","SYK","BSX","EW",
    # Financials
    "JPM","V","MA","BAC","WFC","GS","MS","BLK","SCHW","AXP",
    "COF","USB","PNC","TFC","FITB","HBAN","KEY","RF","CFG","MTB",
    # Energy
    "XOM","CVX","COP","EOG","SLB","PSX","MPC","VLO","OXY","DVN",
    "FANG","HES","PXD","APA","HAL","BKR","NOV","MRO",
    # Industrials
    "CAT","DE","HON","RTX","GE","BA","LMT","NOC","GD","L3H",
    "EMR","ETN","PWR","VRT","AXON","PH","ITW","MMM","IR","SWK",
    # Utilities / REIT
    "NEE","DUK","SO","AEP","EXC","PCG","SRE","ED","CEG","VST",
    "EQIX","AMT","PLD","SPG","WELL","DLR","CCI","PSA",
    # Materials
    "APD","LIN","SHW","FCX","NEM","CTVA","CF","MOS","ALB","MP",
]

NASDAQ100_CORE = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","GOOG","TSLA","AVGO","COST",
    "NFLX","ASML","AMD","TMUS","LIN","QCOM","CSCO","AMAT","INTU","ISRG",
    "BKNG","TXN","CMCSA","HON","AMGN","VRTX","PANW","LRCX","ADP","ADI",
    "SBUX","KLAC","REGN","SNPS","CDNS","MDLZ","ORLY","ABNB","CSGP","MNST",
    "PYPL","MELI","NXPI","WDAY","CRWD","FAST","DXCM","TEAM","FTNT","CEG",
    "IDXX","BIIB","MRVL","KDP","ON","PCAR","KHC","MRNA","ROST","GEHC",
    "EXC","FANG","BKR","DDOG","ZS","ANSS","VRSK","CCEP","ODFL","EA",
    "CTSH","CTAS","CPRT","ACGL","XEL","GILD","LULU","DLTR","CDW","CHKP",
    "WBD","TTWO","ILMN","SGEN","DOCU","ZM","SPLK","MDB","SNOW","NET",
    "PLTR","RKLB","LUNR","IONQ","JOBY","ACHR","SOUN","RDW","SMR","NNE",
]

GROWTH_WATCHLIST = [
    # AlphaAbsolute existing watchlist + discovered leaders
    "COHR","LITE","IPGP","MRAM","AAOI","CIEN",          # Photonics
    "RKLB","LUNR","ASTS","PL","RDW",                     # Space
    "IONQ","RGTI","QUBT",                                 # Quantum
    "OKLO","SMR","NNE","CCJ","BWXT",                     # Nuclear
    "CRWV","SMCI","NTAP","CORZ","CLSK",                  # NeoCloud
    "AXON","PLTR","BAH","CACI","LDOS","SAIC","KTOS","AVAV", # Defense
    "VRT","DELL","ANET","APH","EME","PWR","GLDD",        # AI Infra
    "ACHR","JOBY","RCAT",                                 # Drone
    "TER","BRKS","ONTO","FORM","KLIC",                   # Semi equip
    "SOUN","ORCL","CRM","META","MSFT",                   # AI Software
    "MU","WDC","STX","MRVL","ON","NXPI","AMD","NVDA","AVGO","QCOM", # Semis
    "TSLA","ISRG",                                        # Robotics
    "TMUS","ERIC","NOK",                                  # Connectivity
    "CEG","VST","NNE","OKLO",                            # Nuclear power
    "IBM","MSFT","GOOGL","META","ORCL",                  # Enterprise AI
]


def _http_get(url: str, timeout: int = 45) -> str:
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"    [fetch error] {e}")
        return ""


def _fetch_wikipedia_tickers(url: str, table_idx: int = 0,
                              col: str = "Symbol", alt_cols: list = None) -> list[str]:
    """
    Scrape ticker list from Wikipedia table using pandas (reliable, SSL bypass).
    Tries multiple table indices and column names automatically.
    """
    try:
        import pandas as pd
        ssl._create_default_https_context = ssl._create_unverified_context
        tables = pd.read_html(url, attrs=None)

        # Try requested table_idx first, then scan all tables for one with ticker column
        search_cols = [col] + (alt_cols or []) + ["Ticker", "Symbol", "Ticker symbol"]
        indices_to_try = list(dict.fromkeys([table_idx] + list(range(min(len(tables), 8)))))

        for idx in indices_to_try:
            if idx >= len(tables):
                continue
            df = tables[idx]
            for c in search_cols:
                if c in df.columns:
                    tickers = df[c].dropna().astype(str).tolist()
                    cleaned = []
                    for t in tickers:
                        t = t.strip().replace(".", "-")
                        if re.match(r'^[A-Z]{1,5}(-[A-Z])?$', t.upper()):
                            cleaned.append(t.upper())
                    if len(cleaned) > 50:   # sanity: real index has >50 stocks
                        return cleaned
        return []
    except Exception as e:
        print(f"  [Wiki] Could not scrape {url}: {e}")
        return []


def _fetch_polygon_exchange(exchange: str, key: str, ctx) -> list[str]:
    """
    Fetch ALL active common stocks (type=CS) from one Polygon exchange.
    Paginated at 1000/page with 13s delay (free tier: 5 req/min).
    Filters: letters-only tickers 1-5 chars (no warrants W, units U, rights, preferred).
    """
    tickers: list[str] = []
    next_url = (
        f"https://api.polygon.io/v3/reference/tickers"
        f"?market=stocks&exchange={exchange}&active=true&type=CS"
        f"&limit=1000&apiKey={key}"
    )
    page_count = 0
    retry_count = 0

    print(f"  [{exchange}] Fetching from Polygon...")
    while next_url and page_count < 10:
        try:
            req = urllib.request.Request(next_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                data = json.loads(r.read())
            results = data.get("results", [])
            for stock in results:
                t = stock.get("ticker", "").upper()
                if t and re.match(r'^[A-Z]{1,5}$', t):
                    tickers.append(t)
            next_url = data.get("next_url")
            if next_url and "apiKey" not in next_url:
                next_url += f"&apiKey={key}"
            page_count += 1
            retry_count = 0
            print(f"    [{exchange}] Page {page_count}: +{len(results)} | total: {len(tickers)}")
            if next_url:
                time.sleep(13)   # Polygon free tier: 5 req/min → must wait ≥12s
        except urllib.error.HTTPError as e:
            if e.code == 429 and retry_count < 3:
                wait = 20 * (retry_count + 1)
                print(f"    [{exchange}] Rate limited — waiting {wait}s...")
                time.sleep(wait)
                retry_count += 1
            else:
                print(f"    [{exchange}] HTTP error page {page_count}: {e}")
                break
        except Exception as e:
            print(f"    [{exchange}] Error page {page_count}: {e}")
            break

    unique = sorted(set(tickers))
    print(f"  [{exchange}] Done: {len(unique)} unique common stocks")
    return unique


def _fetch_all_nyse_nasdaq_polygon() -> list[str]:
    """
    Fetch ALL active common stocks from NYSE (XNYS) + NASDAQ (XNAS).
    Returns ~6,000 unique tickers (NYSE ~2,700 + NASDAQ ~3,300).
    Runs in ~2 minutes with polite 13s delay between pages.
    """
    import ssl as _ssl
    key = os.environ.get("POLYGON_API_KEY", "")
    if not key:
        print("  [!] POLYGON_API_KEY not set — cannot fetch from Polygon")
        return []

    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE

    nasdaq = _fetch_polygon_exchange("XNAS", key, ctx)

    # Wait between exchanges to reset rate limit window
    if nasdaq:
        print("  Waiting 15s before NYSE fetch...")
        time.sleep(15)

    nyse = _fetch_polygon_exchange("XNYS", key, ctx)

    combined = sorted(set(nasdaq) | set(nyse))
    print(f"  Combined NYSE+NASDAQ: {len(combined)} unique stocks")
    return combined


def _fetch_russell2000_fmp() -> list[str]:
    """Fallback: Russell 2000 from FMP (requires FMP_API_KEY)."""
    key = os.environ.get("FMP_API_KEY", "")
    if not key:
        return []
    url = f"https://financialmodelingprep.com/api/v3/russell2000_constituent?apikey={key}"
    text = _http_get(url)
    if not text:
        return []
    try:
        data = json.loads(text)
        tickers = [d.get("symbol", "").upper() for d in data if d.get("symbol")]
        print(f"    → {len(tickers)} tickers (FMP)")
        return tickers
    except Exception:
        return []


def build_full_universe() -> list[str]:
    """
    Universe = NYSE + NASDAQ ทั้งหมด (~6,000 stocks)

    Primary:  Polygon reference API (XNYS + XNAS), type=CS (common stock only)
              ~2,700 NYSE + ~3,300 NASDAQ = ~6,000 unique after dedup
    Fallback: hardcoded SP500_CORE + NASDAQ100_CORE (~600 stocks)
              Used only if Polygon API key missing or returns <500 stocks.

    Watchlist: ALWAYS_INCLUDE + GROWTH_WATCHLIST always added on top.
    Filter:    letters-only 1-5 chars (ตัด warrants/units/preferreds ออก)
    """
    print("  Building universe: NYSE + NASDAQ all stocks (Polygon)...")

    all_tickers: set[str] = set()

    # ── Primary: Polygon NYSE + NASDAQ ───────────────────────────────────────
    polygon_tickers = _fetch_all_nyse_nasdaq_polygon()

    if len(polygon_tickers) >= 500:
        all_tickers.update(polygon_tickers)
        print(f"  Polygon NYSE+NASDAQ: {len(polygon_tickers)} stocks ✓")
    else:
        print(f"  Polygon returned only {len(polygon_tickers)} — using hardcoded fallback")
        all_tickers.update(polygon_tickers)
        all_tickers.update(SP500_CORE)
        all_tickers.update(NASDAQ100_CORE)

    # ── AlphaAbsolute thematic watchlist (always included) ───────────────────
    pre = len(all_tickers)
    all_tickers.update(ALWAYS_INCLUDE)
    all_tickers.update(GROWTH_WATCHLIST)
    added = len(all_tickers) - pre
    if added:
        print(f"  + {added} extra thematic tickers (watchlist)")

    # ── Clean up ──────────────────────────────────────────────────────────────
    all_tickers -= ALWAYS_EXCLUDE
    all_tickers = {
        t.strip().upper() for t in all_tickers
        if t and re.match(r'^[A-Z]{1,5}$', t.strip().upper())
    }

    result = sorted(all_tickers)
    print(f"  TOTAL UNIQUE: {len(result)} tickers")
    return result


def build_active_universe(full_universe: list[str]) -> list[str]:
    """Legacy: returns full universe (no pre-filter in v2 — ohlcv_prefetch handles all)."""
    return full_universe


def _build_active_universe_v1(full_universe: list[str]) -> list[str]:
    """v1 pre-filter kept for reference."""
    """
    Pre-filter full universe to ~150-200 stocks for deep scoring.

    Priority tiers:
    1. Already in existing watchlist (always include)
    2. In RS leader file (rs_universe/latest.json) with RS > 60th
    3. Growth watchlist override (always include)

    This avoids expensive per-ticker API calls for pre-filtering.
    Uses data we already computed.
    """
    print("  Applying pre-filter for active universe...")

    active = set()

    # Tier 1: AlphaAbsolute growth watchlist (always include, these are targets)
    for t in GROWTH_WATCHLIST:
        active.add(t)
    print(f"  + Growth watchlist: {len(GROWTH_WATCHLIST)} tickers")

    # Tier 2: RS leaders from latest rs_ranker output (RS > 60th percentile)
    rs_file = ROOT / "data" / "rs_universe" / "latest.json"
    if rs_file.exists():
        try:
            rs_data = json.loads(rs_file.read_text(encoding="utf-8"))
            rs_leaders = [
                r["ticker"] for r in rs_data.get("ranked", [])
                if r.get("rs_composite_percentile", 0) >= 60
                and r["ticker"] in full_universe
            ]
            for t in rs_leaders:
                active.add(t)
            print(f"  + RS leaders (>60th): {len(rs_leaders)} tickers")
        except Exception as e:
            print(f"  [WARN] RS file error: {e}")

    # Tier 3: Trend Template passes (any grade) — from screener
    tt_file = ROOT / "data" / "trend_template" / "screener_latest.json"
    if tt_file.exists():
        try:
            tt_data = json.loads(tt_file.read_text(encoding="utf-8"))
            tt_pass = [
                r["ticker"] for r in tt_data.get("results", [])
                if r.get("grade", "F") in ("A", "B", "C")
                and r["ticker"] in full_universe
            ]
            for t in tt_pass:
                active.add(t)
            print(f"  + Trend Template pass (A/B/C): {len(tt_pass)} tickers")
        except Exception as e:
            print(f"  [WARN] TT file error: {e}")

    # Tier 4: Top S&P sectors (always include sector leaders for macro context)
    SECTOR_LEADERS = [
        "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA",  # Mega cap
        "JPM","V","MA","BAC","GS",                           # Financials
        "LLY","UNH","JNJ","ABBV","MRK",                     # Healthcare
        "XOM","CVX","COP","EOG",                             # Energy
        "CAT","DE","HON","RTX","GE","BA","LMT",              # Industrials
        "NEE","DUK","SO","CEG","VST",                        # Utilities/Energy
        "MU","AMD","AVGO","QCOM","AMAT","KLAC","LRCX",      # Semis
    ]
    for t in SECTOR_LEADERS:
        if t in full_universe:
            active.add(t)

    active_list = sorted(active)
    print(f"  Active universe: {len(active_list)} tickers (for deep scoring)")
    return active_list


def get_universe() -> list[str]:
    """
    Public API: return full universe tickers.
    Loads from disk if fresh (<7 days), else rebuilds.
    Called by rs_benchmark.py and ohlcv_prefetch.py.
    """
    if FULL_FILE.exists():
        try:
            d = json.loads(FULL_FILE.read_text(encoding="utf-8"))
            built = d.get("built_date") or d.get("built_at", "")
            # Accept if built today or yesterday (weekend handling)
            from datetime import datetime as dt, timedelta
            try:
                age = (dt.now().date() - date.fromisoformat(d.get("built_date", "2000-01-01"))).days
            except Exception:
                age = 999
            if age < CACHE_DAYS and d.get("tickers"):
                return d["tickers"]
        except Exception:
            pass
    result = run()
    return result.get("tickers", [])


def run(force: bool = False) -> dict:
    """Build and save universe files. Entry point for pre_market_runner.py."""
    today = date.today().isoformat()

    # Check cache
    if FULL_FILE.exists() and not force:
        try:
            existing = json.loads(FULL_FILE.read_text(encoding="utf-8"))
            if existing.get("built_date") == today and len(existing.get("tickers", [])) > 500:
                n = len(existing["tickers"])
                print(f"  Universe already built today ({n} tickers) — skipping")
                return existing
        except Exception:
            pass

    print(f"\n{'='*58}")
    print(f"  Universe Builder  [{today}]")
    print(f"{'='*58}")

    tickers = build_full_universe()

    output = {
        "built_date":  today,
        "updated_at":  datetime.now().isoformat(),
        "total":       len(tickers),
        "sources":     ["NYSE (Polygon XNYS)", "NASDAQ (Polygon XNAS)", "AlphaAbsolute watchlist"],
        "note":        f"NYSE + NASDAQ all common stocks | {len(tickers)} unique tickers",
        "tickers":     tickers,
    }

    FULL_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    ACTIVE_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")  # legacy compat

    print(f"  Saved: {FULL_FILE} ({len(tickers)} tickers)")
    return output


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    run(force=args.force)
