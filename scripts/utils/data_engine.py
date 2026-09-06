"""
AlphaAbsolute -- Production Data Engine v3  (WARP-Hardened)
===========================================================
Multi-source market data with automatic failover. Works EVEN when
Cloudflare WARP intercepts SSL (verify=False + crumb token fix).

Source Priority for OHLCV:
  1. Polygon.io     -- best (if key in .env) -- no SSL issues
  2. Tiingo         -- reliable backup (if key in .env)
  3. Stooq          -- FREE, no auth, CSV, no Cloudflare issue [OK]
  4. Yahoo query2   -- crumb-authenticated + verify=False (WARP-safe) [OK]
  5. Yahoo query1   -- alternate endpoint fallback [OK]
  6. yfinance lib   -- last resort with ssl_patch

Source Priority for Quotes:
  1. Polygon        -- if key exists
  2. Finnhub        -- already have key [OK]
  3. Yahoo query2   -- crumb + verify=False

Source Priority for Fundamentals:
  1. SEC EDGAR      -- FREE, no key, authoritative 10-Q/10-K XBRL data [OK]
  2. FMP            -- if key exists (structured, fast)
  3. Finnhub        -- already have key (EPS, revenue) [OK]
  4. Yahoo quoteSummary -- free fallback [OK]

FREE SOURCES (no keys needed, work on WARP):
  Stooq: stooq.com/q/d/l/?s={TICKER}.US&i=d   -- CSV, no auth
  Yahoo quoteSummary: query1.finance.yahoo.com  -- with crumb
  Finnhub: already configured in .env

OPTIONAL keys (add to .env for better reliability):
  POLYGON_API_KEY  -- polygon.io (free signup, 5 calls/min)
  TIINGO_API_KEY   -- api.tiingo.com (free, 500/day)
  FMP_API_KEY      -- financialmodelingprep.com (free, 250/day)

NO extra cost. All free tiers.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import urllib3

# ── Source Health Tracker (auto-failover) ────────────────────────────────────
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.utils.source_health import get_health as _get_health
    _HEALTH = _get_health()
except Exception:
    _HEALTH = None   # graceful degradation if source_health not found

# ── Data Accuracy Shield (D5 validator) ──────────────────────────────────────
try:
    from scripts.utils.data_validator import validate_fundamentals as _validate_fund
    from scripts.utils.data_validator import validate_ohlcv as _validate_ohlcv
    _D5_VALIDATOR = True
except Exception:
    _D5_VALIDATOR = False
    def _validate_fund(ticker, data, source, log=True): return data, []   # no-op
    def _validate_ohlcv(ticker, df, source): return df, []

# ── Silence SSL warnings globally ────────────────────────────────────────────
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Apply SSL patch immediately (WARP bypass) ─────────────────────────────────
import ssl as _ssl
_ssl._create_default_https_context = _ssl._create_unverified_context  # type: ignore

# ── Paths & env ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]

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

_ENV = _load_env()

def _key(name: str) -> str:
    return os.environ.get(name, _ENV.get(name, ""))

POLYGON_KEY  = _key("POLYGON_API_KEY")
TIINGO_KEY   = _key("TIINGO_API_KEY")
FMP_KEY      = _key("FMP_API_KEY")
FINNHUB_KEY  = _key("FINNHUB_API_KEY")
FRED_KEY     = _key("FRED_API_KEY")

# ── OHLCV Disk Cache (24h) ─────────────────────────────────────────────────────
# Shared across all scripts in the same day — avoids 5 scripts fetching same ticker
_CACHE_DIR = ROOT / "data" / "ohlcv_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_OHLCV_MEM: dict = {}   # in-process cache (same interpreter session)

# ── EDGAR Disk Cache ──────────────────────────────────────────────────────────
# companyfacts is 200-500KB per ticker; we cache the processed result (small)
# CIK map (~1MB) refreshed weekly; per-ticker fund result refreshed every 7 days
# (10-Qs file every 90 days — daily re-fetch is wasteful; weekly is more than enough)
_EDGAR_CACHE_DIR = ROOT / "data" / "edgar_cache"
_EDGAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_EDGAR_FUND_CACHE_DAYS  = 7     # re-fetch fundamentals weekly (10-Q = 90d cycle)
_EDGAR_CIK_MAP_DAYS     = 7     # CIK map refreshed weekly


# ── HTTP Sessions ──────────────────────────────────────────────────────────────

# Standard session (Polygon, Tiingo, FMP, Finnhub -- all proper TLS)
_SESSION = requests.Session()
_SESSION.verify = False   # WARP-safe
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
})

# SEC EDGAR session -- requires company name + email per SEC fair access policy
# see: https://www.sec.gov/os/accessing-edgar-data
_EDGAR_SESSION = requests.Session()
_EDGAR_SESSION.verify = False
_EDGAR_SESSION.headers.update({
    "User-Agent": "AlphaAbsolute piriyaponk@gmail.com",
    "Accept-Encoding": "gzip, deflate",
})

# Yahoo-specific session with browser-like headers for crumb auth
_YF_SESSION = requests.Session()
_YF_SESSION.verify = False
_YF_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
})

# Yahoo-specific session with browser-like headers for crumb auth
_YF_SESSION = requests.Session()
_YF_SESSION.verify = False
_YF_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://finance.yahoo.com/",
    "Origin":          "https://finance.yahoo.com",
})

# ── SEC EDGAR CIK Cache (module-level, survives multiple calls per session) ───
_EDGAR_CIK_CACHE: dict = {}   # {TICKER: "0000320193"} -- avoids repeated tickers.json fetches
_EDGAR_TICKERS_MAP: dict = {} # Full ticker->CIK map, populated once per session from tickers.json

# ── Yahoo Crumb Manager ───────────────────────────────────────────────────────
_CRUMB_LOCK  = threading.Lock()
_CRUMB_VALUE = None
_CRUMB_DATE  = None

def _get_yahoo_crumb() -> Optional[str]:
    """
    Get Yahoo Finance crumb token. Required since 2023 for all Yahoo API calls.
    Caches per session. Thread-safe.
    """
    global _CRUMB_VALUE, _CRUMB_DATE
    with _CRUMB_LOCK:
        today = date.today().isoformat()
        if _CRUMB_VALUE and _CRUMB_DATE == today:
            return _CRUMB_VALUE

        # Step 1: Visit Yahoo Finance to get cookies
        try:
            _YF_SESSION.get(
                "https://finance.yahoo.com/",
                timeout=10,
                allow_redirects=True,
            )
        except Exception:
            pass

        # Step 2: Get crumb from API
        CRUMB_URLS = [
            "https://query2.finance.yahoo.com/v1/test/getcrumb",
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
        ]
        for url in CRUMB_URLS:
            try:
                r = _YF_SESSION.get(url, timeout=10)
                if r.status_code == 200 and r.text and len(r.text) < 50:
                    _CRUMB_VALUE = r.text.strip()
                    _CRUMB_DATE  = today
                    return _CRUMB_VALUE
            except Exception:
                continue

        return None


# ── Period helpers ─────────────────────────────────────────────────────────────
_PERIOD_DAYS = {
    "1mo": 35, "3mo": 95, "6mo": 185,
    "1y": 370, "2y": 740, "5y": 1830, "14mo": 430,
}

def _period_to_dates(period: str) -> tuple:
    """Convert period string to (from_date, to_date) ISO."""
    if period.endswith("d"):
        try:
            days = int(period[:-1])
        except ValueError:
            days = 370
    else:
        days = _PERIOD_DAYS.get(period, 370)
    end   = date.today()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()

def _stooq_date(period: str) -> tuple:
    """Returns (d1, d2) in YYYYMMDD format for Stooq."""
    s, e = _period_to_dates(period)
    return s.replace("-", ""), e.replace("-", "")


# ── Indicators ────────────────────────────────────────────────────────────────

def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add standard technical indicators to OHLCV DataFrame."""
    c = df["Close"]
    v = df.get("Volume", pd.Series(0, index=df.index))
    df["MA20"]  = c.rolling(20, min_periods=5).mean()
    df["MA50"]  = c.rolling(50, min_periods=10).mean()
    df["MA150"] = c.rolling(150, min_periods=30).mean()
    df["MA200"] = c.rolling(200, min_periods=40).mean()
    df["MA30W"] = c.rolling(150, min_periods=30).mean()
    df["Vol20"] = v.rolling(20, min_periods=5).mean()
    df["ADTV"]  = (c * v).rolling(63, min_periods=20).mean()
    return df


def _validate(df: Optional[pd.DataFrame], min_rows: int = 20) -> bool:
    """True if DataFrame passes quality checks."""
    if df is None or df.empty or len(df) < min_rows:
        return False
    if "Close" not in df.columns:
        return False
    closes = df["Close"].dropna()
    if closes.empty:
        return False
    last = float(closes.iloc[-1])
    if not (0.01 < last < 1_000_000):
        return False
    if df["Close"].isna().mean() > 0.05:
        return False
    # Staleness: last bar <= 7 days old
    last_date = df.index[-1]
    if hasattr(last_date, "date"):
        last_date = last_date.date()
    if isinstance(last_date, pd.Timestamp):
        last_date = last_date.date()
    if (date.today() - last_date).days > 7:
        return False
    return True


def _make_df(index, opens, highs, lows, closes, volumes) -> pd.DataFrame:
    """Helper to build a clean DataFrame."""
    df = pd.DataFrame({
        "Open": opens, "High": highs, "Low": lows,
        "Close": closes, "Volume": volumes,
    }, index=index)
    df.index.name = "Date"
    df = df.dropna(subset=["Close"])
    return _add_indicators(df) if not df.empty else df


# =============================================================================
# SOURCE 1 -- POLYGON.IO  (best, no WARP issues, free API key at polygon.io)
# =============================================================================

def _polygon_ohlcv(ticker: str, period: str = "1y") -> Optional[pd.DataFrame]:
    if not POLYGON_KEY:
        return None
    from_date, to_date = _period_to_dates(period)
    try:
        r = _SESSION.get(
            f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}",
            params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": POLYGON_KEY},
            timeout=15,
        )
        if r.status_code == 429:
            time.sleep(12)
            r = _SESSION.get(r.url, timeout=15)
        data = r.json()
        if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
            return None
        rows = data["results"]
        df = pd.DataFrame(rows)
        df["Date"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(
            "America/New_York").dt.normalize()
        df = df.set_index("Date").sort_index()
        df = df.rename(columns={"o":"Open","h":"High","l":"Low","c":"Close","v":"Volume"})
        df = df[["Open","High","Low","Close","Volume"]].dropna(subset=["Close"])
        df.index.name = "Date"
        return _add_indicators(df)
    except Exception as e:
        print(f"  [Polygon] {ticker}: {e}")
        return None

def _polygon_quote(ticker: str) -> Optional[float]:
    if not POLYGON_KEY:
        return None
    try:
        yesterday = (date.today() - timedelta(days=3)).isoformat()
        r = _SESSION.get(
            f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{yesterday}/{date.today().isoformat()}",
            params={"adjusted":"true","sort":"desc","limit":1,"apiKey":POLYGON_KEY}, timeout=8)
        results = r.json().get("results", [])
        if results:
            return float(results[0]["c"])
    except Exception:
        pass
    return None


# =============================================================================
# SOURCE 2 -- TIINGO  (reliable backup, free at api.tiingo.com)
# =============================================================================

def _tiingo_ohlcv(ticker: str, period: str = "1y") -> Optional[pd.DataFrame]:
    if not TIINGO_KEY:
        return None
    from_date, _ = _period_to_dates(period)
    try:
        r = _SESSION.get(
            f"https://api.tiingo.com/tiingo/daily/{ticker}/prices",
            params={"startDate": from_date, "token": TIINGO_KEY},
            headers={"Content-Type": "application/json"}, timeout=15)
        # Retry once on 429 — short sleep only (free plan = per-minute limit, not hourly)
        if r.status_code == 429:
            import time
            print(f"  [Tiingo] 429 rate limit for {ticker} — sleeping 5s then retrying")
            time.sleep(5)
            r = _SESSION.get(
                f"https://api.tiingo.com/tiingo/daily/{ticker}/prices",
                params={"startDate": from_date, "token": TIINGO_KEY},
                headers={"Content-Type": "application/json"}, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        df = pd.DataFrame(data)
        df["Date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.set_index("Date").sort_index()
        close_col = "adjClose" if "adjClose" in df.columns else "close"
        df = df.rename(columns={"open":"Open","high":"High","low":"Low",
                                  close_col:"Close","volume":"Volume"})
        df = df[["Open","High","Low","Close","Volume"]].dropna(subset=["Close"])
        df.index.name = "Date"
        return _add_indicators(df)
    except Exception as e:
        print(f"  [Tiingo] {ticker}: {e}")
        return None

def _tiingo_quote(ticker: str) -> Optional[float]:
    if not TIINGO_KEY:
        return None
    try:
        r = _SESSION.get(
            f"https://api.tiingo.com/iex/{ticker.upper()}",
            params={"token": TIINGO_KEY},
            headers={"Content-Type": "application/json"}, timeout=8)
        data = r.json()
        if isinstance(data, list) and data:
            return float(data[0].get("last", data[0].get("tngoLast", 0)) or 0)
    except Exception:
        pass
    return None


# =============================================================================
# SOURCE 3 -- STOOQ  (FREE signup required at stooq.com, add STOOQ_API_KEY to .env)
# NOTE: docstring previously said "no auth" — INCORRECT. Stooq now requires a free API key.
# If STOOQ_API_KEY not set, this source is silently skipped (falls through to Yahoo).
# =============================================================================

def _stooq_ohlcv(ticker: str, period: str = "1y") -> Optional[pd.DataFrame]:
    """
    Stooq CSV download. NOTE: Stooq now requires an API key (free signup at stooq.com).
    If STOOQ_API_KEY is set in .env, use it. Otherwise skip silently.
    """
    stooq_key = _key("STOOQ_API_KEY")
    if not stooq_key:
        return None   # Skip silently -- no key, no Stooq

    d1, d2 = _stooq_date(period)
    stooq_sym = f"{ticker}.US"

    try:
        r = _SESSION.get(
            "https://stooq.com/q/d/l/",
            params={"s": stooq_sym, "d1": d1, "d2": d2, "i": "d", "apikey": stooq_key},
            timeout=20,
        )
        if r.status_code != 200 or "apikey" in r.text.lower() or len(r.text) < 50:
            return None

        df = pd.read_csv(io.StringIO(r.text))
        if df.empty or "Close" not in df.columns:
            return None

        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()

        if "Volume" not in df.columns:
            df["Volume"] = 0

        df = df[["Open","High","Low","Close","Volume"]].dropna(subset=["Close"])
        df.index.name = "Date"
        return _add_indicators(df)
    except Exception as e:
        print(f"  [Stooq] {ticker}: {e}")
        return None


# =============================================================================
# SOURCE 4 -- YAHOO FINANCE (crumb-authenticated, WARP-safe via verify=False)
# =============================================================================

def _yahoo_ohlcv_v8(ticker: str, period: str = "1y",
                     host: str = "query2") -> Optional[pd.DataFrame]:
    """
    Yahoo Finance v8 API with crumb token (required since 2023).
    Falls back to non-crumb if crumb fetch fails.
    """
    # Try with crumb first
    crumb = _get_yahoo_crumb()
    params = {"interval": "1d", "range": period}
    if crumb:
        params["crumb"] = crumb

    # Map period to Yahoo format
    yahoo_periods = {"14mo": "2y"}
    yperiod = yahoo_periods.get(period, period)
    if yperiod.endswith("d"):
        # Yahoo doesn't support Nd format -- convert to range
        days = int(yperiod[:-1])
        yperiod = "2y" if days > 365 else "1y" if days > 185 else "6mo"
    params["range"] = yperiod

    for host in [f"query2.finance.yahoo.com", "query1.finance.yahoo.com"]:
        try:
            url = f"https://{host}/v8/finance/chart/{ticker}"
            r = _YF_SESSION.get(url, params=params, timeout=20)
            if r.status_code == 401 and crumb:
                # Crumb expired -- reset and retry once
                global _CRUMB_VALUE
                _CRUMB_VALUE = None
                crumb = _get_yahoo_crumb()
                if crumb:
                    params["crumb"] = crumb
                r = _YF_SESSION.get(url, params=params, timeout=20)
            if r.status_code != 200:
                continue
            data = r.json()
            chart_result = data.get("chart", {}).get("result")
            if not chart_result:
                continue
            result     = chart_result[0]
            timestamps = result.get("timestamp", [])
            if not timestamps:
                continue
            quote = result["indicators"]["quote"][0]
            adj_list = result["indicators"].get("adjclose", [{}])
            adj = (adj_list[0].get("adjclose") if adj_list else None) or quote["close"]

            idx = (pd.to_datetime(timestamps, unit="s")
                     .tz_localize("UTC")
                     .tz_convert("America/New_York")
                     .normalize())
            df = pd.DataFrame({
                "Open":   quote.get("open",   [None]*len(idx)),
                "High":   quote.get("high",   [None]*len(idx)),
                "Low":    quote.get("low",    [None]*len(idx)),
                "Close":  adj,
                "Volume": quote.get("volume", [0]*len(idx)),
            }, index=idx)
            df.index.name = "Date"
            df = df.dropna(subset=["Close"])
            if not df.empty:
                return _add_indicators(df)
        except Exception as e:
            print(f"  [Yahoo {host}] {ticker}: {e}")
            continue

    return None

def _yahoo_quote(ticker: str) -> Optional[float]:
    """Get Yahoo price via quoteSummary (more reliable than v8 for quotes)."""
    crumb = _get_yahoo_crumb()
    for host in ["query2.finance.yahoo.com", "query1.finance.yahoo.com"]:
        try:
            url = f"https://{host}/v8/finance/chart/{ticker}"
            params = {"interval": "1d", "range": "5d"}
            if crumb:
                params["crumb"] = crumb
            r = _YF_SESSION.get(url, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                chart_result = data.get("chart", {}).get("result")
                if chart_result:
                    return float(chart_result[0]["meta"]["regularMarketPrice"])
        except Exception:
            continue
    return None


# =============================================================================
# SOURCE 5 -- FINNHUB  (quotes, already have key [OK])
# =============================================================================

def _finnhub_quote(ticker: str) -> Optional[float]:
    """Get real-time quote from Finnhub (free, 60/min)."""
    if not FINNHUB_KEY:
        return None
    try:
        r = _SESSION.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": ticker, "token": FINNHUB_KEY},
            timeout=8,
        )
        data = r.json()
        price = data.get("c", 0)   # 'c' = current price
        if price and price > 0:
            return float(price)
    except Exception:
        pass
    return None

def _first_yoy(history: list, yoy_key: str = "yoy_growth") -> Optional[float]:
    """Return the first non-None yoy value from a fundamentals history list.

    The most recent quarter's yoy_growth may be None when the quarter was just
    reported and no year-ago equivalent exists yet (e.g. a new fiscal period).
    This function falls back to the next available quarter to avoid silently
    dropping valid acceleration signals.
    """
    for q in history:
        v = q.get(yoy_key)
        if v is not None:
            return float(v)
    return None


def _first_yoy_with_val(history: list, yoy_key: str = "yoy_growth",
                        val_key: str = "eps") -> tuple:
    """Return (yoy_value, metric_value) from the SAME quarter — the first quarter
    with a non-None yoy value.

    Fixes the spinoff artifact where eps_latest and eps_yoy_pct come from
    different periods (e.g. SNDK Q2 has no yoy baseline because Q2 2025 was
    pre-spinoff, so _first_yoy skips to Q1 but eps_history[0] still returns Q2).

    Returns (yoy_float, metric_value) or (None, history[0][val_key]) if no yoy
    is found anywhere.
    """
    for q in history:
        v = q.get(yoy_key)
        if v is not None:
            return float(v), q.get(val_key)
    # No quarter has a valid yoy — return None yoy + most recent metric
    return None, history[0].get(val_key) if history else None


def _finnhub_fundamentals(ticker: str, quarters: int = 8) -> Optional[dict]:
    """
    Get financials from Finnhub (free, 60/min, have key).
    FIXES:
    - Concept names use 'us-gaap_' prefix -> match by suffix
    - Q2/Q3 10-Q reports are YTD cumulative -> de-cumulate to quarterly
    - Returns standardized schema matching earnings_inflection.py expectations
    """
    if not FINNHUB_KEY:
        return None
    try:
        r = _SESSION.get(
            "https://finnhub.io/api/v1/stock/financials-reported",
            params={"symbol": ticker, "freq": "quarterly", "token": FINNHUB_KEY},
            timeout=12,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        # Get more reports than needed (de-cumulation needs prior period)
        reports = data.get("data", [])[:quarters + 4]
        if not reports:
            return None

        def _extract_ic(ic: list) -> dict:
            """Extract key income statement items, matching by concept suffix."""
            result = {"eps": None, "revenue": None, "gross_profit": None, "cogs": None}
            # Revenue concept variants (match by suffix to handle us-gaap_ prefix)
            REV_CONCEPTS = {"RevenueFromContractWithCustomerExcludingAssessedTax",
                            "Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomer",
                            "SalesRevenueGoodsNet", "RevenueFromContractWithCustomerIncludingAssessedTax"}
            # COGS concept variants — many tech/photonics companies don't tag GrossProfit
            # but do tag CostOfRevenue or CostOfGoodsSold. Compute GP = Rev - COGS.
            COGS_CONCEPTS = {"CostOfGoodsAndServicesSold", "CostOfRevenue",
                             "CostOfGoodsSold", "CostOfSales", "CostOfGoodsAndServices",
                             "OperatingCostsAndExpenses"}
            for item in ic:
                concept = item.get("concept", "")
                val     = item.get("value")
                if val is None:
                    continue
                # Strip namespace prefix (us-gaap_, dei_, etc.)
                bare = concept.split("_", 1)[-1] if "_" in concept else concept
                if bare in ("EarningsPerShareBasic", "EarningsPerShareDiluted") and result["eps"] is None:
                    result["eps"] = float(val)
                elif bare in REV_CONCEPTS and result["revenue"] is None:
                    result["revenue"] = float(val)
                elif bare == "GrossProfit" and result["gross_profit"] is None:
                    result["gross_profit"] = float(val)
                elif bare in COGS_CONCEPTS and result["cogs"] is None:
                    result["cogs"] = float(val)
            # Fallback: compute GP from Rev - COGS if GrossProfit not tagged
            if result["gross_profit"] is None and result["cogs"] is not None and result["revenue"] is not None:
                result["gross_profit"] = result["revenue"] - result["cogs"]
            return result

        # Build raw quarterly series (may still be cumulative for Q2/Q3)
        raw = []
        for rep in reports:
            ic       = rep.get("report", {}).get("ic", [])
            date_str = rep.get("endDate", "")[:10]
            quarter  = rep.get("quarter", 0)   # 1,2,3,4 = fiscal Q
            year     = rep.get("year", 0)
            vals     = _extract_ic(ic)
            raw.append({
                "date":    date_str,
                "quarter": quarter,
                "year":    year,
                "eps_raw": vals["eps"],
                "rev_raw": vals["revenue"],
                "gp_raw":  vals["gross_profit"],
            })

        # De-cumulate: Q1=standalone, Q2=YTD−Q1, Q3=YTD−Q2(6m), Q4=annual(skip)
        # Finnhub fiscal quarter numbering: 1=Q1(3m), 2=Q2(6m YTD), 3=Q3(9m YTD)
        quarterly = []
        for i, r_cur in enumerate(raw):
            q = r_cur["quarter"]
            eps = r_cur["eps_raw"]
            rev = r_cur["rev_raw"]
            gp  = r_cur["gp_raw"]

            if q == 1:
                # Q1 is always standalone 3-month -- use as-is
                pass
            elif q in (2, 3):
                # Q2/Q3 are YTD -- subtract prior period (next item in list = older)
                # Look for immediate prior 10-Q in the same fiscal year
                prior = None
                for r_prev in raw[i+1:i+4]:
                    if r_prev["year"] == r_cur["year"] and r_prev["quarter"] == q - 1:
                        prior = r_prev
                        break
                if prior:
                    if eps is not None and prior["eps_raw"] is not None:
                        eps = round(eps - prior["eps_raw"], 4)
                    if rev is not None and prior["rev_raw"] is not None:
                        rev = rev - prior["rev_raw"]
                    if gp is not None and prior["gp_raw"] is not None:
                        gp = gp - prior["gp_raw"]
            elif q == 0 or q == 4:
                # Annual or unknown -- skip
                continue

            if eps is None and rev is None:
                continue   # Empty period

            quarterly.append({
                "date":         r_cur["date"],
                "quarter":      f"{r_cur['year']}Q{q}",
                "eps":          round(eps, 4) if eps is not None else None,
                "revenue":      round(rev, 0) if rev is not None else None,
                "gross_profit": round(gp, 0) if gp is not None else None,
                # None = no data (gate_gm = -1 = unknown)
                # Previously used 0.0 which caused 0% gross margin on missing data,
                # triggering gate_gm = 0 (FAIL) instead of -1 (UNKNOWN).
            })

        # Keep only requested quarters, oldest first for YoY calc
        quarterly = quarterly[:quarters]

        if len(quarterly) < 2:
            return None

        # Build YoY lookup: (quarter_num, year-1) -> index in quarterly
        # Finnhub skips Q4 (annual), so "same quarter 1 year ago" is NOT a fixed offset
        # Match by fiscal quarter label: e.g. 2026Q1 -> look for 2025Q1
        q_lookup = {}
        for idx, q in enumerate(quarterly):
            q_lookup[q["quarter"]] = idx  # e.g. {"2026Q1": 0, "2025Q3": 1, ...}

        def _prior_year_label(qlabel: str) -> str:
            """Convert '2026Q1' -> '2025Q1'"""
            try:
                yr, qn = qlabel.split("Q")
                return f"{int(yr)-1}Q{qn}"
            except Exception:
                return ""

        # Compute YoY and QoQ
        eps_history          = []
        revenue_history      = []
        gross_margin_history = []

        for i, q in enumerate(quarterly):
            rev    = q["revenue"]
            gp     = q["gross_profit"]
            gm_pct = round(gp / max(rev, 1) * 100, 2) if (rev is not None and rev > 0 and gp is not None and gp > 0) else None

            # YoY: match same fiscal quarter, prior year
            eps_yoy = rev_yoy = None
            prior_label = _prior_year_label(q["quarter"])
            if prior_label in q_lookup:
                j = q_lookup[prior_label]
                prev_eps = quarterly[j]["eps"]
                prev_rev = quarterly[j]["revenue"]
                if prev_eps and abs(prev_eps) > 0.001:
                    eps_yoy = round((q["eps"] - prev_eps) / abs(prev_eps) * 100, 1)
                if prev_rev > 0:
                    rev_yoy = round((rev - prev_rev) / prev_rev * 100, 1)

            # QoQ: compare to prior quarter (index i+1 = one period older)
            rev_qoq = None
            if i + 1 < len(quarterly):
                prev_rev = quarterly[i + 1]["revenue"]
                if prev_rev > 0:
                    rev_qoq = round((rev - prev_rev) / prev_rev * 100, 1)

            eps_history.append({
                "quarter":    q["quarter"],
                "eps":        q["eps"],
                "yoy_growth": eps_yoy,
            })
            revenue_history.append({
                "quarter":    q["quarter"],
                "revenue":    rev,
                "yoy_growth": rev_yoy,
                "qoq_growth": rev_qoq,
            })
            gross_margin_history.append({
                "quarter":     q["quarter"],
                "gross_margin": gm_pct,
            })

        # Acceleration flags
        eps_yoys = [e["yoy_growth"] for e in eps_history if e["yoy_growth"] is not None]
        rev_yoys = [r["yoy_growth"] for r in revenue_history if r["yoy_growth"] is not None]
        eps_accel = len(eps_yoys) >= 2 and eps_yoys[0] > eps_yoys[1]
        rev_accel = len(rev_yoys) >= 2 and rev_yoys[0] > rev_yoys[1]

        return {
            "ticker":               ticker,
            "source":               "finnhub",
            "eps_history":          eps_history,
            "revenue_history":      revenue_history,
            "gross_margin_history": gross_margin_history,
            "latest_eps":           _first_yoy_with_val(eps_history, val_key="eps")[1] if eps_history else None,
            "latest_revenue":       _first_yoy_with_val(revenue_history, val_key="revenue")[1] if revenue_history else None,
            "eps_yoy_pct":          _first_yoy_with_val(eps_history)[0] if eps_history else None,
            "rev_yoy_pct":          _first_yoy_with_val(revenue_history, val_key="revenue")[0] if revenue_history else None,
            "eps_accel":            eps_accel,
            "rev_accel":            rev_accel,
            "quarters":             len(quarterly),
            "as_of":                date.today().isoformat(),
        }
    except Exception as e:
        print(f"  [Finnhub fundamentals] {ticker}: {e}")
        return None


def _yahoo_fundamentals(ticker: str, quarters: int = 8) -> Optional[dict]:
    """
    Yahoo quoteSummary fallback for fundamentals (free, no key).
    Uses incomeStatementHistoryQuarterly module.
    """
    crumb = _get_yahoo_crumb()
    modules = "incomeStatementHistoryQuarterly,defaultKeyStatistics,financialData"
    for host in ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]:
        try:
            params = {"modules": modules, "formatted": "false"}
            if crumb:
                params["crumb"] = crumb
            r = _YF_SESSION.get(
                f"https://{host}/v10/finance/quoteSummary/{ticker}",
                params=params, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            result = data.get("quoteSummary", {}).get("result")
            if not result:
                continue
            result = result[0]

            qis = (result.get("incomeStatementHistoryQuarterly", {})
                         .get("incomeStatementHistory", []))[:quarters]
            if not qis:
                continue

            eps_history     = []
            revenue_history = []
            gross_margin    = []

            for q in qis:
                date_str = q.get("endDate", {}).get("fmt", "")
                eps    = float(q.get("dilutedEPS",       {}).get("raw", 0) or 0)
                rev    = float(q.get("totalRevenue",     {}).get("raw", 0) or 0)
                gp     = float(q.get("grossProfit",      {}).get("raw", 0) or 0)
                eps_history.append({"date": date_str, "eps": round(eps, 4), "yoy_pct": None})
                revenue_history.append({"date": date_str, "revenue": rev, "yoy_pct": None})
                gross_margin.append({"date": date_str,
                                      "gm_pct": round(gp/max(rev,1)*100, 2) if rev else None})

            # YoY
            for i in range(len(eps_history)):
                if i+4 < len(eps_history):
                    cur = eps_history[i]["eps"]; prev = eps_history[i+4]["eps"]
                    if prev != 0: eps_history[i]["yoy_pct"] = round((cur-prev)/abs(prev)*100, 1)
                if i+4 < len(revenue_history):
                    cur = revenue_history[i]["revenue"]; prev = revenue_history[i+4]["revenue"]
                    if prev > 0: revenue_history[i]["yoy_pct"] = round((cur-prev)/prev*100, 1)

            eps_accel = rev_accel = False
            ey = [e["yoy_pct"] for e in eps_history[:3] if e["yoy_pct"] is not None]
            ry = [r["yoy_pct"] for r in revenue_history[:3] if r["yoy_pct"] is not None]
            if len(ey) >= 2: eps_accel = ey[0] > ey[1]
            if len(ry) >= 2: rev_accel = ry[0] > ry[1]

            return {
                "ticker":          ticker,
                "source":          "yahoo",
                "eps_history":     eps_history,
                "revenue_history": revenue_history,
                "gross_margin":    gross_margin,
                "latest_eps":      _first_yoy_with_val(eps_history, "yoy_pct", "eps")[1] if eps_history else None,
                "latest_revenue":  _first_yoy_with_val(revenue_history, "yoy_pct", "revenue")[1] if revenue_history else None,
                "eps_yoy_pct":     _first_yoy_with_val(eps_history, "yoy_pct", "eps")[0] if eps_history else None,
                "rev_yoy_pct":     _first_yoy_with_val(revenue_history, "yoy_pct", "revenue")[0] if revenue_history else None,
                "eps_accel":       eps_accel,
                "rev_accel":       rev_accel,
                "as_of":           date.today().isoformat(),
            }
        except Exception as e:
            print(f"  [Yahoo fundamentals {host}] {ticker}: {e}")
            continue
    return None


def _edgar_fundamentals(ticker: str, quarters: int = 8) -> Optional[dict]:
    """
    SEC EDGAR XBRL API -- FREE, no key, authoritative 10-Q/10-K data.
    Direct from the source: data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json
    Covers ALL US public companies, ~20 years of history, updated same day as filing.

    Flow:
      1. Resolve ticker -> CIK via EDGAR company search API
      2. Fetch companyfacts JSON (all XBRL tags)
      3. Extract EPS (EarningsPerShareDiluted), Revenue (Revenues/SalesRevenueNet),
         GrossProfit -- all from us-gaap namespace
      4. De-cumulate 6M/9M YTD values into standalone quarterly figures
      5. Return same schema as Finnhub (eps_history, revenue_history, gross_margin_history)
    """
    tkr = ticker.upper()

    # ── Step 0: Check processed-result disk cache (7-day TTL) ────────────────
    # Earnings-aware: if latest_quarter > 75 days old, a new 10-Q may have filed.
    # In that case, bypass cache even within 7 days to capture fresh earnings.
    _fund_cache_file = _EDGAR_CACHE_DIR / f"{tkr}_fund.json"
    if _fund_cache_file.exists():
        try:
            cached = json.loads(_fund_cache_file.read_text(encoding="utf-8"))
            cached_ts = cached.get("_cache_ts", "")
            if cached_ts:
                age_days = (datetime.now() - datetime.fromisoformat(cached_ts)).days
                if age_days < _EDGAR_FUND_CACHE_DAYS:
                    # Negative cache hit: EDGAR returned nothing for this ticker — stop re-fetching
                    if cached.get("_negative_cache"):
                        return None

                    # Earnings-aware staleness: check if latest quarter is > 75 days old
                    # (10-Q filing window is 40-45 days after quarter end, so by day 75 it should exist)
                    _stale_earnings = False
                    eps_hist = cached.get("eps_history", [])
                    if eps_hist:
                        latest_q = eps_hist[0].get("quarter", "")  # e.g. "2026Q1"
                        if latest_q and len(latest_q) == 6:
                            try:
                                yr, qn = int(latest_q[:4]), int(latest_q[5])
                                # Exact quarter end: Q1=Mar31, Q2=Jun30, Q3=Sep30, Q4=Dec31
                                q_end_month = qn * 3
                                if q_end_month == 12:
                                    q_end = datetime(yr, 12, 31)
                                else:
                                    # First day of next month minus 1 day = last day of current month
                                    q_end = datetime(yr, q_end_month + 1, 1) - timedelta(days=1)
                                days_since_q_end = (datetime.now() - q_end).days
                                if days_since_q_end > 75:
                                    _stale_earnings = True   # 10-Q filing window has passed
                            except Exception:
                                pass

                    if not _stale_earnings:
                        # Positive cache hit: return stored fundamentals
                        result = {k: v for k, v in cached.items() if not k.startswith("_")}
                        if result.get("eps_history"):
                            # Recompute paired fields to ensure same-quarter alignment.
                            # Fixes spinoff artifacts where eps_history[0] has null yoy_growth
                            # (e.g. SNDK Q2 2026 has no prior-year comparison) — the cached
                            # latest_eps and eps_yoy_pct would come from different quarters.
                            _ey, _el = _first_yoy_with_val(result["eps_history"], val_key="eps")
                            _ry, _rl = _first_yoy_with_val(
                                result.get("revenue_history") or [], val_key="revenue")
                            result["latest_eps"] = _el
                            result["eps_yoy_pct"] = _ey
                            if _ry is not None:
                                result["latest_revenue"] = _rl
                                result["rev_yoy_pct"] = _ry
                        return result if result.get("eps_history") else None
                    # else: fall through to re-fetch (earnings window has passed)
        except Exception:
            pass   # corrupted cache — re-fetch

    # ── Step 1: CIK lookup ────────────────────────────────────────────────────
    # _EDGAR_CIK_CACHE is defined at module level (below sessions block)
    cik = _EDGAR_CIK_CACHE.get(tkr)
    if not cik:
        # tickers.json: try disk cache first (weekly refresh), then network
        _cik_map_file = _EDGAR_CACHE_DIR / "cik_map.json"
        if not _EDGAR_TICKERS_MAP:
            # 1a. Try disk-cached CIK map (refresh weekly)
            if _cik_map_file.exists():
                try:
                    disk_map = json.loads(_cik_map_file.read_text(encoding="utf-8"))
                    # Extract timestamp BEFORE mutating the dict
                    cache_ts_str = disk_map.get("_cache_ts", "")
                    if cache_ts_str:
                        age_days = (datetime.now() - datetime.fromisoformat(cache_ts_str)).days
                        if age_days < _EDGAR_CIK_MAP_DAYS:
                            # Load all real ticker entries (skip internal fields)
                            _EDGAR_TICKERS_MAP.update(
                                {k: v for k, v in disk_map.items() if not k.startswith("_")}
                            )
                except Exception:
                    pass   # parse error → fetch fresh from SEC

            # 1b. Still empty → fetch from SEC
            if not _EDGAR_TICKERS_MAP:
                try:
                    r2 = _EDGAR_SESSION.get(
                        "https://www.sec.gov/files/company_tickers.json",
                        timeout=15)
                    if r2.status_code == 200:
                        raw = r2.json()   # {0: {cik_str, ticker, title}, ...}
                        for entry in raw.values():
                            t = entry.get("ticker", "").upper()
                            if t:
                                _EDGAR_TICKERS_MAP[t] = str(entry["cik_str"]).zfill(10)
                        # 1c. Save to disk with timestamp
                        try:
                            save_map = dict(_EDGAR_TICKERS_MAP)
                            save_map["_cache_ts"] = datetime.now().isoformat()
                            _cik_map_file.write_text(
                                json.dumps(save_map, separators=(",", ":")),
                                encoding="utf-8")
                        except Exception:
                            pass
                except Exception:
                    pass

        cik = _EDGAR_TICKERS_MAP.get(tkr)
        if cik:
            _EDGAR_CIK_CACHE[tkr] = cik

    if not cik:
        return None

    # ── Step 2: Fetch companyfacts ────────────────────────────────────────────
    try:
        r = _EDGAR_SESSION.get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
            timeout=20)
        if r.status_code != 200:
            return None
        facts = r.json()
    except Exception:
        return None

    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        # Write negative cache: EDGAR responded but no us-gaap data (SPAC, foreign issuer, etc.)
        try:
            _fund_cache_file.write_text(
                json.dumps({"_negative_cache": True, "_cache_ts": datetime.now().isoformat(),
                            "_reason": "no_us_gaap", "ticker": tkr}),
                encoding="utf-8")
        except Exception:
            pass
        return None

    # ── Step 3: Extract quarterly data ───────────────────────────────────────
    def _get_units(tag: str, prefer_per_share: bool = False) -> list:
        """Get USD or shares/usd units for a tag, return list of period dicts.
        prefer_per_share=True: check USD/shares first (for EPS tags, avoiding
        inadvertently reading net-income totals stored under the USD key).
        """
        tag_data = us_gaap.get(tag, {}).get("units", {})
        if prefer_per_share:
            return (tag_data.get("USD/shares") or tag_data.get("shares/USD") or
                    tag_data.get("USD") or [])
        return (tag_data.get("USD") or tag_data.get("shares/USD") or
                tag_data.get("USD/shares") or [])

    def _is_quarterly(item: dict) -> bool:
        """True if item covers exactly one quarter (not annual or YTD)."""
        start = item.get("start", "")
        end   = item.get("end",   "")
        if not start or not end:
            return False
        try:
            s = datetime.strptime(start, "%Y-%m-%d")
            e = datetime.strptime(end,   "%Y-%m-%d")
            days = (e - s).days
            return 75 <= days <= 100   # ~90 days = one quarter
        except Exception:
            return False

    def _to_quarter_label(end_date: str) -> str:
        """Convert end date to 'YYYYQn' label."""
        try:
            d = datetime.strptime(end_date, "%Y-%m-%d")
            month = d.month
            q = (month - 1) // 3 + 1
            return f"{d.year}Q{q}"
        except Exception:
            return end_date

    def _extract_quarterly(tag_names: list, prefer_per_share: bool = False) -> dict:
        """
        Try each tag name, extract standalone quarterly values.
        Returns {quarter_label: value} dict.
        Picks the tag with the MOST RECENT data (companies switch XBRL tags over time).
        prefer_per_share: pass True for EPS tags to ensure USD/shares takes priority
        over USD (which may contain net income totals, not per-share values).
        """
        this_year = date.today().year
        best_result: dict = {}
        best_recency: str = ""   # highest quarter label seen = most recent data

        def _build_from_standalone(standalone_list: list) -> dict:
            """Build {qlabel: val} from pre-filtered standalone list."""
            r: dict = {}
            for u in standalone_list:
                qlabel = _to_quarter_label(u["end"])
                accn   = u.get("accn", "")
                val    = u.get("val", 0)
                if qlabel not in r or accn > r[qlabel]["accn"]:
                    r[qlabel] = {"val": val, "accn": accn}
            return {k: v["val"] for k, v in r.items()}

        for tag in tag_names:
            units = _get_units(tag, prefer_per_share=prefer_per_share)
            if not units:
                continue

            # Filter 10-Q/10-K standalone quarters only (75-100 day range)
            standalone = [u for u in units if _is_quarterly(u) and
                          u.get("form") in ("10-Q", "10-K")]

            if standalone:
                result = _build_from_standalone(standalone)
            else:
                # Try de-cumulating YTD figures: Q1(75-100d), Q2(170-195d), Q3(260-290d)
                ytd_map: dict = {}
                for u in units:
                    if u.get("form") != "10-Q":
                        continue
                    start = u.get("start", "")
                    end   = u.get("end",   "")
                    if not start or not end:
                        continue
                    try:
                        s_d  = datetime.strptime(start, "%Y-%m-%d")
                        e_d  = datetime.strptime(end,   "%Y-%m-%d")
                        days = (e_d - s_d).days
                        yr   = e_d.year
                    except Exception:
                        continue
                    if 75 <= days <= 100:
                        qtype = "Q1"
                    elif 170 <= days <= 195:
                        qtype = "Q2"
                    elif 260 <= days <= 290:
                        qtype = "Q3"
                    else:
                        continue
                    key = f"{yr}_{qtype}"
                    if key not in ytd_map or u.get("accn", "") > ytd_map[key]["accn"]:
                        ytd_map[key] = {"val": u.get("val", 0), "end": end, "accn": u.get("accn", "")}

                result = {}
                for key, data in ytd_map.items():
                    yr_s, qtype = key.split("_")
                    val_ytd = data["val"]
                    if qtype == "Q1":
                        sa_val = val_ytd
                    elif qtype == "Q2":
                        q1_key = f"{yr_s}_Q1"
                        if q1_key in ytd_map:
                            sa_val = val_ytd - ytd_map[q1_key]["val"]
                        else:
                            continue
                    elif qtype == "Q3":
                        q2_key = f"{yr_s}_Q2"
                        if q2_key in ytd_map:
                            sa_val = val_ytd - ytd_map[q2_key]["val"]
                        else:
                            continue
                    else:
                        continue
                    qlabel = _to_quarter_label(data["end"])
                    result[qlabel] = sa_val

            if not result:
                continue

            # Pick the tag whose data is most recent (handles companies switching XBRL tags)
            most_recent_q = max(result.keys())
            if most_recent_q > best_recency:
                best_recency = most_recent_q
                best_result  = result

        return best_result

    # Extract EPS (per-share diluted), Revenue, GrossProfit
    # prefer_per_share=True: EPS must come from USD/shares unit, not USD (net income totals)
    eps_map = _extract_quarterly([
        "EarningsPerShareDiluted",
        "EarningsPerShareBasic",
    ], prefer_per_share=True)
    rev_map = _extract_quarterly([
        "RevenueFromContractWithCustomerExcludingAssessedTax",   # most common modern XBRL tag
        "RevenueFromContractWithCustomerIncludingAssessedTax",   # gross-revenue filers (telecom etc)
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "RevenueFromContractWithCustomer",                        # generic ASC 606 tag
    ])
    gp_map = _extract_quarterly([
        "GrossProfit",            # preferred: direct gross profit tag
    ])
    # Fallback: compute GP = Revenue - COGS (many tech/photonics companies use COGS, not GrossProfit)
    if not gp_map:
        cogs_map = _extract_quarterly([
            "CostOfGoodsAndServicesSold",   # COHR, many tech companies
            "CostOfRevenue",
            "CostOfGoodsSold",
            "CostOfSales",
            "OperatingCostsAndExpenses",    # some service companies (matches Path A tag list)
            "CostOfGoodsAndServices",       # variant used by some issuers
        ])
        if cogs_map and rev_map:
            gp_map = {
                qlabel: rev_map[qlabel] - cogs_val
                for qlabel, cogs_val in cogs_map.items()
                if qlabel in rev_map and rev_map[qlabel] > 0
            }

    if len(eps_map) < 3 and len(rev_map) < 3:
        # Write negative cache to avoid repeated EDGAR re-fetches for recent IPOs.
        # Cache is 7-day TTL (same as positive cache) — re-check after next quarter.
        try:
            _fund_cache_file.write_text(
                json.dumps({"_negative_cache": True,
                            "_cache_ts": datetime.now().isoformat(),
                            "_reason": "insufficient_history",
                            "_detail": f"eps_quarters={len(eps_map)} rev_quarters={len(rev_map)} (need 3+)",
                            "ticker": tkr}),
                encoding="utf-8")
        except Exception:
            pass
        return None

    # ── Step 4: Build sorted (newest-first) history with YoY/QoQ ─────────────
    all_qtrs = sorted(set(list(eps_map) + list(rev_map)), reverse=True)[:quarters]

    def _prior_year(qlabel: str) -> str:
        yr, qn = qlabel.split("Q")
        return f"{int(yr)-1}Q{qn}"

    def _prior_quarter(qlabel: str, all_q: list) -> Optional[str]:
        idx = all_q.index(qlabel) if qlabel in all_q else -1
        if idx >= 0 and idx + 1 < len(all_q):
            return all_q[idx + 1]
        return None

    eps_history = []
    revenue_history = []
    gross_margin_history = []

    for qlabel in all_qtrs:
        eps = eps_map.get(qlabel)
        rev = rev_map.get(qlabel, 0) or 0
        gp  = gp_map.get(qlabel)
        gm_pct = round(gp / max(rev, 1) * 100, 2) if gp and rev else None

        # EPS YoY
        py = _prior_year(qlabel)
        eps_yoy = None
        if eps is not None and py in eps_map and eps_map[py] != 0:
            eps_yoy = round((eps - eps_map[py]) / abs(eps_map[py]) * 100, 1)

        # Revenue YoY + QoQ
        rev_yoy = None
        if rev and py in rev_map and rev_map[py] > 0:
            rev_yoy = round((rev - rev_map[py]) / rev_map[py] * 100, 1)

        rev_qoq = None
        pq = _prior_quarter(qlabel, list(all_qtrs))
        if rev and pq and pq in rev_map and rev_map[pq] > 0:
            rev_qoq = round((rev - rev_map[pq]) / rev_map[pq] * 100, 1)

        if eps is not None:
            eps_history.append({"quarter": qlabel, "eps": round(eps, 4), "yoy_growth": eps_yoy})
        revenue_history.append({"quarter": qlabel, "revenue": rev, "yoy_growth": rev_yoy, "qoq_growth": rev_qoq})
        if gm_pct is not None:
            gross_margin_history.append({"quarter": qlabel, "gross_margin": gm_pct})

    # ── EPS outlier scrub ─────────────────────────────────────────────────────
    # XBRL de-cumulation artifacts (e.g. Bloom Energy Q3 2024/2025 = -100/-60)
    # appear when EDGAR's standalone-quarter extraction subtracts mismatched YTD
    # periods. Detect: |eps| > 20× median(|eps|) of all other quarters.
    # Scrubbed quarters get eps=None so _first_yoy skips them safely.
    if len(eps_history) >= 3:
        import statistics
        abs_vals = [abs(q["eps"]) for q in eps_history if q["eps"] is not None and q["eps"] != 0]
        if abs_vals:
            try:
                med = statistics.median(abs_vals)
                if med > 0:
                    scrub_threshold = max(50.0, med * 20)   # never scrub < $50 EPS (BRK-A etc safe)
                    for q in eps_history:
                        if q["eps"] is not None and abs(q["eps"]) > scrub_threshold:
                            # Don't scrub if ALL quarters are large (legitimate high-EPS co)
                            frac_large = sum(1 for v in abs_vals if v > scrub_threshold) / len(abs_vals)
                            if frac_large < 0.4:  # outlier only if < 40% quarters are large
                                print(f"  [EDGAR] {tkr} {q['quarter']}: EPS={q['eps']:.1f} "
                                      f"scrubbed (outlier vs median={med:.2f})")
                                q["eps"] = None
                                q["yoy_growth"] = None
            except Exception:
                pass

    if not eps_history:
        # Write negative cache: EDGAR had data but couldn't extract EPS (e.g. pre-revenue biotech)
        try:
            _fund_cache_file.write_text(
                json.dumps({"_negative_cache": True, "_cache_ts": datetime.now().isoformat(),
                            "_reason": "no_eps_history", "ticker": tkr}),
                encoding="utf-8")
        except Exception:
            pass
        return None

    eps_yoys = [e["yoy_growth"] for e in eps_history if e["yoy_growth"] is not None]
    rev_yoys = [r["yoy_growth"] for r in revenue_history if r["yoy_growth"] is not None]

    result = {
        "ticker":               tkr,
        "source":               "edgar",
        "cik":                  cik,
        "eps_history":          eps_history,
        "revenue_history":      revenue_history,
        "gross_margin_history": gross_margin_history,
        "latest_eps":           _first_yoy_with_val(eps_history, val_key="eps")[1] if eps_history else None,
        "latest_revenue":       _first_yoy_with_val(revenue_history, val_key="revenue")[1] if revenue_history else None,
        "eps_yoy_pct":          _first_yoy_with_val(eps_history)[0] if eps_history else None,
        "rev_yoy_pct":          _first_yoy_with_val(revenue_history, val_key="revenue")[0] if revenue_history else None,
        "eps_accel":            len(eps_yoys) >= 2 and eps_yoys[0] is not None and eps_yoys[0] > eps_yoys[1],
        "rev_accel":            len(rev_yoys) >= 2 and rev_yoys[0] is not None and rev_yoys[0] > rev_yoys[1],
        "quarters":             len(eps_history),
        "as_of":                date.today().isoformat(),
    }

    # ── Write processed result to disk cache ──────────────────────────────────
    try:
        cached_out = dict(result)
        cached_out["_cache_ts"] = datetime.now().isoformat()
        _fund_cache_file.write_text(
            json.dumps(cached_out, separators=(",", ":")),
            encoding="utf-8")
    except Exception:
        pass   # cache write failure is non-fatal

    return result


# ── EDGAR 6-K Fundamentals (foreign filers filing 6-K quarterly reports) ──────
# Foreign private issuers (ARM, TSM, ASML, STM, UMC) don't file 10-Q.
# They file 6-K quarterly interim reports which ARE tagged in XBRL and
# available via the companyfacts API using us-gaap tags (even for IFRS companies).
# We filter to ~90-day period entries (standalone quarters, not cumulative YTD).
#
# CIK lookup: company_tickers.json includes some but not all foreign filers.
# FOREIGN_6K_CIK overrides for known foreign NASDAQ/NYSE listed companies.
FOREIGN_6K_CIK: dict[str, str] = {
    # ticker → 10-digit CIK string (must be zero-padded to 10 digits for API)
    "ARM":   "0001973239",  # ARM Holdings PLC /UK
    "TSM":   "0001046179",  # Taiwan Semiconductor Manufacturing
    "ASML":  "0000937966",  # ASML Holding NV (Netherlands) — DE Pipeline fix: was "0937556" (wrong, causes 404)
    "STM":   "0000932787",  # STMicroelectronics NV
    "UMC":   "0001033767",  # United Microelectronics Corp
    "GFS":   "0001709164",  # GlobalFoundries Inc
    "NICE":  "0001073349",  # NICE Systems Ltd (Israel)
    "INFY":  "0001067491",  # Infosys Ltd (India) — zero-padded to 10 digits
}

def _edgar_6k_fundamentals(ticker: str, quarters: int = 8) -> Optional[dict]:
    """
    EDGAR 6-K fundamentals for foreign private issuers (ARM, TSM, ASML etc.).

    These companies don't file 10-Q (US quarterly) — they file 6-K interim reports.
    The XBRL companyfacts API contains their financials tagged with us-gaap concepts.

    Key insight: Each 6-K filing contains BOTH cumulative YTD and standalone quarter
    entries. We filter standalone quarters by period length (~80-100 days) to get
    clean quarter-by-quarter comparables.

    Revenue concept: RevenueFromContractWithCustomerExcludingAssessedTax (primary)
                     Revenues (fallback)
    Net income:      NetIncomeLoss
    EPS:             EarningsPerShareBasic → EarningsPerShareDiluted → computed from NI/shares
    """
    tkr = ticker.upper().strip()
    cik = FOREIGN_6K_CIK.get(tkr)
    if not cik:
        # Also try the main CIK map for companies not in our hardcoded list
        if _EDGAR_TICKERS_MAP:
            cik = _EDGAR_TICKERS_MAP.get(tkr)
        if not cik:
            return None

    cik_str = cik.zfill(10)

    # Check disk cache (7 days)
    cache_file = _EDGAR_CACHE_DIR / f"{tkr}_6k_fund.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("_negative_cache"):
                return None
            age_h = (datetime.now() - datetime.fromisoformat(
                cached.get("_cache_ts", "2000-01-01"))).total_seconds() / 3600
            if age_h < 168 and cached.get("eps_history"):  # 7-day cache
                # Recompute paired yoy/val fields from history on cache hit
                if cached.get("eps_history"):
                    _ey, _el = _first_yoy_with_val(cached["eps_history"], val_key="eps")
                    cached["eps_yoy_pct"] = _ey
                    cached["latest_eps"]  = _el
                if cached.get("revenue_history"):
                    _ry, _rl = _first_yoy_with_val(
                        cached["revenue_history"], val_key="revenue")
                    if _ry is not None:
                        cached["rev_yoy_pct"]     = _ry
                        cached["latest_revenue"]  = _rl
                return cached
        except Exception:
            cache_file.unlink(missing_ok=True)

    # Fetch XBRL company facts (DE Pipeline: throttle to stay under SEC 10 req/sec limit)
    try:
        import time as _time
        _time.sleep(0.12)   # ~8 req/sec max — safe margin under SEC fair access policy
        r = _EDGAR_SESSION.get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_str}.json",
            timeout=20)
        if r.status_code != 200:
            # Retry once on transient errors (503, 429)
            if r.status_code in (429, 503):
                _time.sleep(2.0)
                r = _EDGAR_SESSION.get(
                    f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_str}.json",
                    timeout=20)
            if r.status_code != 200:
                return None
        facts_json = r.json()
    except Exception:
        return None

    # DE Pipeline fix: try us-gaap first (ARM), then ifrs-full (GFS, UMC, STM file under IFRS)
    facts = facts_json.get("facts", {})
    us_gaap = facts.get("us-gaap") or facts.get("ifrs-full") or {}
    if not us_gaap:
        return None  # No financial data in any known namespace

    def _extract_quarterly(concept: str, unit_key: str = "USD") -> list[dict]:
        """
        Extract standalone quarterly entries for a concept.
        Filter: form in 6-K or 20-F, period length 75-105 days (single quarter).
        Returns list of {start, end, val, filed} sorted newest-first.
        """
        entries = us_gaap.get(concept, {}).get("units", {}).get(unit_key, [])
        out = []
        seen = set()  # dedup by (start, end)
        for e in entries:
            if e.get("form") not in ("6-K", "20-F", "6-K/A", "20-F/A"):
                continue
            start = e.get("start")
            end   = e.get("end")
            if not start or not end:
                continue
            try:
                d1 = date.fromisoformat(start)
                d2 = date.fromisoformat(end)
                days = (d2 - d1).days
            except ValueError:
                continue
            # Only standalone quarter entries (75-105 days)
            if not (75 <= days <= 105):
                continue
            key = (start, end)
            if key in seen:
                continue
            seen.add(key)
            # Convert end date to approximate quarter label
            month = d2.month
            year  = d2.year
            q_num = (month - 1) // 3 + 1
            qlabel = f"{year}Q{q_num}"
            out.append({
                "quarter": qlabel,
                "start":   start,
                "end":     end,
                "val":     e["val"],
                "filed":   e.get("filed", ""),
            })
        # Sort newest first (by end date)
        out.sort(key=lambda x: x["end"], reverse=True)
        return out[:quarters]

    # Extract revenue
    rev_entries = _extract_quarterly("RevenueFromContractWithCustomerExcludingAssessedTax")
    if not rev_entries:
        rev_entries = _extract_quarterly("Revenues")
    if not rev_entries:
        rev_entries = _extract_quarterly("RevenueFromContractWithCustomerIncludingAssessedTaxValue")

    # Extract net income
    ni_entries = _extract_quarterly("NetIncomeLoss")

    # Extract EPS (try basic first, then diluted, then compute from NI/shares)
    eps_entries = _extract_quarterly("EarningsPerShareBasic", "USD/shares")
    if not eps_entries:
        eps_entries = _extract_quarterly("EarningsPerShareDiluted", "USD/shares")

    if not rev_entries and not ni_entries:
        # Write negative cache
        cache_file.write_text(json.dumps({
            "_negative_cache": True,
            "_cache_ts": datetime.now().isoformat(),
            "_reason": "no_quarterly_6k_data",
            "ticker": tkr
        }), encoding="utf-8")
        return None

    # ── Build histories with YoY ──────────────────────────────────────────────
    def _build_history_with_yoy(
        entries: list[dict], val_key: str, label_key: str = "quarter"
    ) -> list[dict]:
        """Build history list and compute YoY by matching same quarter prior year."""
        if not entries:
            return []
        # Build quarter → val map for YoY lookup
        by_quarter: dict[str, float] = {}
        for e in entries:
            by_quarter[e["quarter"]] = float(e["val"])

        hist = []
        for e in entries:
            cur_val = float(e["val"])
            # Find prior year same quarter
            q_label = e["quarter"]  # e.g. "2025Q3"
            try:
                yr, qn = int(q_label[:4]), q_label[4:]   # "2025Q4" → yr=2025, qn="Q4"
                prior_label = f"{yr-1}{qn}"               # → "2024Q4" (correct match)
            except (ValueError, IndexError):
                prior_label = None

            prior_val  = by_quarter.get(prior_label) if prior_label else None
            yoy_growth = None
            if prior_val is not None and prior_val != 0:
                yoy_growth = round((cur_val - prior_val) / abs(prior_val) * 100, 1)

            hist.append({
                "quarter":    q_label,
                val_key:      round(cur_val, 4) if val_key == "eps" else int(cur_val),
                "yoy_growth": yoy_growth,
            })
        return hist

    eps_history = []
    if eps_entries:
        eps_history = _build_history_with_yoy(eps_entries, "eps")
    elif ni_entries:
        # Compute approximate per-share EPS from net income (no shares data → use NI trend)
        eps_history = _build_history_with_yoy(ni_entries, "eps")
        # Scale to roughly per-share using a fixed divisor (net income in USD)
        # Note: this will show NI in USD, not per-share EPS — flag accordingly
        for h in eps_history:
            h["eps_unit"] = "net_income_usd"

    rev_history = _build_history_with_yoy(rev_entries, "revenue") if rev_entries else []

    # Gross margin: approximate from cost of revenue if available
    cogs_entries = _extract_quarterly("CostOfRevenue")
    gm_history   = []
    if cogs_entries and rev_entries:
        cogs_map = {e["quarter"]: float(e["val"]) for e in cogs_entries}
        rev_map  = {e["quarter"]: float(e["val"]) for e in rev_entries}
        for q in sorted(rev_map.keys(), reverse=True)[:quarters]:
            if q in cogs_map and rev_map[q] > 0:
                gm_pct = (rev_map[q] - cogs_map[q]) / rev_map[q] * 100
                gm_history.append({"quarter": q, "gross_margin": round(gm_pct, 2)})

    # GM trend
    gm_trend = "Unknown"
    if len(gm_history) >= 3:
        gm_vals = [g["gross_margin"] for g in gm_history[:4]]
        if gm_vals[0] > gm_vals[-1] + 1:
            gm_trend = "Expanding"
        elif gm_vals[0] < gm_vals[-1] - 1:
            gm_trend = "Contracting"
        else:
            gm_trend = "Stable"

    # Paired EPS/rev YoY
    _ey, _el = _first_yoy_with_val(eps_history, val_key="eps") if eps_history else (None, None)
    _ry, _rl = _first_yoy_with_val(rev_history, val_key="revenue") if rev_history else (None, None)

    result = {
        "ticker":           tkr,
        "source":           "edgar_6k",
        "eps_yoy_pct":      _ey,
        "rev_yoy_pct":      _ry,
        "latest_eps":       _el,
        "latest_revenue":   _rl,
        "gm_trend":         gm_trend,
        "eps_history":      eps_history,
        "revenue_history":  rev_history,
        "gm_history":       gm_history,
        "eps_5q_trend":     [h.get("yoy_growth") for h in eps_history[:5]],
        "rev_5q_trend":     [h.get("yoy_growth") for h in rev_history[:5]],
        "acceleration_label": "UNKNOWN",  # computed below
        "_cache_ts":         datetime.now().isoformat(),
        "_filing_type":      "6-K",
        "_cik":              cik_str,
    }

    # Acceleration label
    eps_yoys = [h.get("yoy_growth") for h in eps_history[:4] if h.get("yoy_growth") is not None]
    rev_yoys = [h.get("yoy_growth") for h in rev_history[:4] if h.get("yoy_growth") is not None]
    if eps_yoys and rev_yoys:
        if eps_yoys[0] is not None and eps_yoys[0] > 25 and rev_yoys[0] is not None and rev_yoys[0] > 25:
            if len(eps_yoys) >= 2 and eps_yoys[0] > eps_yoys[1]:
                result["acceleration_label"] = "ACCELERATING"
            else:
                result["acceleration_label"] = "STABLE"
        elif eps_yoys[0] is not None and eps_yoys[0] < 0:
            result["acceleration_label"] = "DECELERATING"

    # Write to cache
    try:
        cache_file.write_text(
            json.dumps(result, separators=(",", ":"), default=str),
            encoding="utf-8")
    except Exception:
        pass

    return result



# Foreign filers that file ONLY annual 20-F (no quarterly 6-K).
# These companies cannot use _edgar_6k_fundamentals (75-105 day filter fails).
# ASML: us-gaap + EUR. TSM/STM/UMC: ifrs-full + USD (or TWD/EUR).
FOREIGN_20F_CIK: dict[str, str] = {
    "ASML":  "0000937966",   # ASML Holding NV (Netherlands, us-gaap, EUR, 20-F annual)
    "TSM":   "0001046179",   # Taiwan Semiconductor (ifrs-full, USD/TWD, 20-F annual)
    "STM":   "0000932787",   # STMicroelectronics (ifrs-full, USD, 20-F annual)
    "UMC":   "0001033767",   # United Microelectronics (ifrs-full, USD, 20-F annual)
}

def _edgar_20f_fundamentals(ticker: str, quarters: int = 8) -> Optional[dict]:
    """
    Annual 20-F fundamentals for foreign private issuers that file only annual reports.
    `quarters` parameter accepted for API consistency with other fund sources (unused — 20-F is annual).

    Handles:
    - ASML (Netherlands): us-gaap namespace, EUR currency, 20-F annual
    - TSM (Taiwan): ifrs-full namespace, USD currency, 20-F annual
    - STM, UMC: ifrs-full namespace, USD currency, 20-F annual

    Annual period filter: 350–380 days (captures fiscal years of 364/365 days).
    Returns annual YoY growth — less granular than quarterly but accurate and free.
    Quarter labels use "FY" suffix: "2024FY" to distinguish from "2024Q3".

    Currency priority: USD first, then EUR. YoY % growth is currency-neutral.
    """
    tkr = ticker.upper().strip()
    cik_str = FOREIGN_20F_CIK.get(tkr, "").zfill(10)
    if not cik_str or cik_str == "0000000000":
        return None

    # 7-day disk cache
    cache_file = _EDGAR_CACHE_DIR / f"{tkr}_20f_fund.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("_negative_cache"):
                return None
            age_h = (datetime.now() - datetime.fromisoformat(
                cached.get("_cache_ts", "2000-01-01"))).total_seconds() / 3600
            if age_h < 168 and cached.get("eps_history"):
                if cached.get("eps_history"):
                    _ey, _el = _first_yoy_with_val(cached["eps_history"], val_key="eps")
                    cached["eps_yoy_pct"]  = _ey
                    cached["latest_eps"]   = _el
                if cached.get("revenue_history"):
                    _ry, _rl = _first_yoy_with_val(cached["revenue_history"], val_key="revenue")
                    if _ry is not None:
                        cached["rev_yoy_pct"]    = _ry
                        cached["latest_revenue"] = _rl
                return cached
        except Exception:
            cache_file.unlink(missing_ok=True)

    # Fetch EDGAR company facts
    import time as _time
    _time.sleep(0.12)
    try:
        r = _EDGAR_SESSION.get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_str}.json",
            timeout=20)
        if r.status_code in (429, 503):
            _time.sleep(2.0)
            r = _EDGAR_SESSION.get(
                f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_str}.json",
                timeout=20)
        if r.status_code != 200:
            return None
        facts_json = r.json()
    except Exception:
        return None

    facts = facts_json.get("facts", {})
    # Try us-gaap first (ASML), then ifrs-full (TSM, STM, UMC)
    ns_data = facts.get("us-gaap") or facts.get("ifrs-full") or {}
    if not ns_data:
        return None

    def _extract_annual(concept: str, unit_priority: list = None) -> list[dict]:
        """Extract annual entries (350–380 day periods) from 20-F and 20-F/A forms."""
        unit_priority = unit_priority or ["USD", "EUR"]
        best: list[dict] = []
        for unit_key in unit_priority:
            entries = ns_data.get(concept, {}).get("units", {}).get(unit_key, [])
            if not entries:
                continue
            out = []
            seen: set[tuple] = set()
            for e in entries:
                if e.get("form") not in ("20-F", "20-F/A"):
                    continue
                start = e.get("start")
                end   = e.get("end")
                if not start or not end:
                    continue
                try:
                    d1 = date.fromisoformat(start)
                    d2 = date.fromisoformat(end)
                    days = (d2 - d1).days
                except ValueError:
                    continue
                if not (350 <= days <= 380):
                    continue
                key = (start, end)
                if key in seen:
                    continue
                seen.add(key)
                fy_label = f"{d2.year}FY"
                out.append({
                    "quarter": fy_label,
                    "start":   start,
                    "end":     end,
                    "val":     float(e["val"]),
                    "filed":   e.get("filed", ""),
                    "currency": unit_key,
                })
            if out:
                # Keep latest 6 fiscal years; use this unit (don't mix currencies)
                out.sort(key=lambda x: x["end"], reverse=True)
                best = out[:6]
                break  # found data in this currency — stop
        return best

    def _build_annual_yoy(entries: list[dict], val_key: str) -> list[dict]:
        """Compute YoY for annual entries (current year vs prior year)."""
        by_fy: dict[str, float] = {e["quarter"]: e["val"] for e in entries}
        hist = []
        for e in entries:
            cur_val = e["val"]
            fy_label = e["quarter"]  # e.g. "2024FY"
            try:
                yr = int(fy_label[:4])
                prior_label = f"{yr-1}FY"
            except (ValueError, IndexError):
                prior_label = None
            prior_val = by_fy.get(prior_label) if prior_label else None
            yoy = None
            if prior_val is not None and prior_val != 0:
                yoy = round((cur_val - prior_val) / abs(prior_val) * 100, 1)
            hist.append({
                "quarter":    fy_label,
                val_key:      round(cur_val, 4) if val_key == "eps" else int(cur_val),
                "yoy_growth": yoy,
            })
        return hist

    # ── Revenue ──────────────────────────────────────────────────────────────────
    # Try us-gaap tags first, then IFRS
    rev_entries = (
        _extract_annual("RevenueFromContractWithCustomerExcludingAssessedTax") or
        _extract_annual("SalesRevenueNet") or
        _extract_annual("Revenues") or
        _extract_annual("Revenue") or            # IFRS concept (TSM)
        _extract_annual("SalesRevenueGoodsNet")
    )

    # ── EPS (per-share) ───────────────────────────────────────────────────────────
    eps_entries = (
        _extract_annual("EarningsPerShareDiluted",      ["EUR/shares", "USD/shares"]) or
        _extract_annual("EarningsPerShareBasic",        ["EUR/shares", "USD/shares"]) or
        _extract_annual("DilutedEarningsLossPerShare",  ["USD/shares", "TWD/shares"]) or
        _extract_annual("BasicEarningsLossPerShare",    ["USD/shares", "TWD/shares"])
    )

    # ── Net income (fallback if no per-share EPS) ────────────────────────────────
    ni_entries = [] if eps_entries else (
        _extract_annual("NetIncomeLoss") or
        _extract_annual("ProfitLossAttributableToOwnersOfParent") or
        _extract_annual("ProfitLoss")
    )

    if not rev_entries and not eps_entries and not ni_entries:
        cache_file.write_text(json.dumps({
            "_negative_cache": True,
            "_cache_ts": datetime.now().isoformat(),
            "_reason": "no_annual_20f_data",
            "ticker": tkr,
        }), encoding="utf-8")
        return None

    # ── Gross Profit ──────────────────────────────────────────────────────────────
    gp_entries = _extract_annual("GrossProfit")
    cogs_entries = [] if gp_entries else (
        # Mirror the quarterly COGS_CONCEPTS list — covers ORCL, KLAC, CRWD, CEG, CAT, GE, RTX
        # "CostOfGoodsAndServicesSold" is the most common alternate tag for tech/industrial filers
        _extract_annual("CostOfGoodsAndServicesSold") or
        _extract_annual("CostOfRevenue") or
        _extract_annual("CostOfGoodsSold") or
        _extract_annual("CostOfSales") or
        _extract_annual("CostOfGoodsAndServices")
    )
    gp_history: list[dict] = []
    if gp_entries and rev_entries:
        rev_map = {e["quarter"]: e["val"] for e in rev_entries}
        for e in gp_entries:
            rv = rev_map.get(e["quarter"], 0)
            if rv > 0:
                gm_pct = e["val"] / rv * 100
                gp_history.append({"quarter": e["quarter"], "gross_margin": round(gm_pct, 2)})
    elif cogs_entries and rev_entries:
        cogs_map = {e["quarter"]: e["val"] for e in cogs_entries}
        rev_map  = {e["quarter"]: e["val"] for e in rev_entries}
        for q, rv in rev_map.items():
            if q in cogs_map and rv > 0:
                gm_pct = (rv - cogs_map[q]) / rv * 100
                gp_history.append({"quarter": q, "gross_margin": round(gm_pct, 2)})
        gp_history.sort(key=lambda x: x["quarter"], reverse=True)

    # GM trend (annual — use 2 years for meaningful trend)
    gm_trend = "Unknown"
    if len(gp_history) >= 2:
        gm_vals = [g["gross_margin"] for g in gp_history[:3]]
        if gm_vals[0] > gm_vals[-1] + 1:
            gm_trend = "Expanding"
        elif gm_vals[0] < gm_vals[-1] - 1:
            gm_trend = "Contracting"
        else:
            gm_trend = "Stable"

    # ── Build histories ───────────────────────────────────────────────────────────
    eps_history = _build_annual_yoy(eps_entries or ni_entries, "eps")
    if ni_entries and not eps_entries:
        for h in eps_history:
            h["eps_unit"] = "net_income_usd"
    rev_history = _build_annual_yoy(rev_entries, "revenue") if rev_entries else []

    _ey, _el = _first_yoy_with_val(eps_history, val_key="eps") if eps_history else (None, None)
    _ry, _rl = _first_yoy_with_val(rev_history, val_key="revenue") if rev_history else (None, None)

    # Acceleration label (annual cadence — less granular)
    accel = "UNKNOWN"
    eps_yoys = [h.get("yoy_growth") for h in eps_history[:3] if h.get("yoy_growth") is not None]
    rev_yoys = [h.get("yoy_growth") for h in rev_history[:3] if h.get("yoy_growth") is not None]
    if eps_yoys and rev_yoys:
        if eps_yoys[0] and eps_yoys[0] > 0 and rev_yoys[0] and rev_yoys[0] > 0:
            if len(eps_yoys) >= 2 and eps_yoys[0] > eps_yoys[1]:
                accel = "ACCELERATING"
            else:
                accel = "STABLE"
        elif eps_yoys[0] and eps_yoys[0] < 0:
            accel = "DECELERATING"

    result = {
        "ticker":             tkr,
        "source":             "edgar_20f",
        "eps_yoy_pct":        _ey,
        "rev_yoy_pct":        _ry,
        "latest_eps":         _el,
        "latest_revenue":     _rl,
        "gm_trend":           gm_trend,
        "eps_history":        eps_history,
        "revenue_history":    rev_history,
        "gm_history":         gp_history,
        "eps_5q_trend":       [h.get("yoy_growth") for h in eps_history[:5]],
        "rev_5q_trend":       [h.get("yoy_growth") for h in rev_history[:5]],
        "acceleration_label": accel,
        "_cache_ts":          datetime.now().isoformat(),
        "_filing_type":       "20-F (annual)",
        "_cik":               cik_str,
        "_note": ("Annual data only — YoY is fiscal year vs prior fiscal year. "
                  "Less granular than quarterly 6-K."),
    }

    try:
        cache_file.write_text(
            json.dumps(result, separators=(",", ":"), default=str),
            encoding="utf-8")
    except Exception:
        pass

    return result


def _fmp_fundamentals(ticker: str, quarters: int = 8) -> Optional[dict]:
    """
    FMP fundamentals -- 250 free calls/day.
    NOTE: Uses /stable/income-statement (new FMP API post-Aug 2025).
    Old /api/v3/ endpoints return 403 for new accounts.
    Also tries /stable/profile for market cap.
    """
    if not FMP_KEY:
        return None
    try:
        r = _SESSION.get(
            "https://financialmodelingprep.com/stable/income-statement",
            params={"symbol": ticker, "period": "quarter",
                    "limit": quarters, "apikey": FMP_KEY},
            timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data or not isinstance(data, list) or "Error" in str(data[0] if data else ""):
            return None

        # Newest-first order (FMP stable already newest-first)
        eps_history = []; revenue_history = []; gross_margin = []
        for q in data:
            eps = float(q.get("eps", 0) or q.get("epsDiluted", 0) or 0)
            rev = float(q.get("revenue", 0) or 0)
            gp  = float(q.get("grossProfit", 0) or 0)
            gm_pct = round(gp / max(rev, 1) * 100, 2) if rev > 0 else None
            # Convert FMP date (2026-03-28) to quarter label (2026Q1)
            date_str = q.get("date", "")
            period_label = q.get("period", "")  # "Q1", "Q2", etc
            fiscal_year  = q.get("fiscalYear", "")
            qlabel = f"{fiscal_year}{period_label}" if fiscal_year and period_label else date_str

            eps_history.append({"quarter": qlabel, "date": date_str,
                                 "eps": round(eps, 4), "yoy_growth": None})
            revenue_history.append({"quarter": qlabel, "date": date_str,
                                     "revenue": rev, "yoy_growth": None, "qoq_growth": None})
            if gm_pct is not None:
                gross_margin.append({"quarter": qlabel, "date": date_str, "gross_margin": gm_pct})

        # Compute YoY (i vs i+4)
        for i in range(len(eps_history)):
            if i + 4 < len(eps_history):
                cur = eps_history[i]["eps"]; prev = eps_history[i+4]["eps"]
                if prev != 0:
                    eps_history[i]["yoy_growth"] = round((cur - prev) / abs(prev) * 100, 1)
            if i + 4 < len(revenue_history):
                cur = revenue_history[i]["revenue"]; prev = revenue_history[i+4]["revenue"]
                if prev > 0:
                    revenue_history[i]["yoy_growth"] = round((cur - prev) / prev * 100, 1)
            if i + 1 < len(revenue_history):
                cur = revenue_history[i]["revenue"]; prev = revenue_history[i+1]["revenue"]
                if prev > 0:
                    revenue_history[i]["qoq_growth"] = round((cur - prev) / prev * 100, 1)

        if not eps_history:
            return None

        ey = [e["yoy_growth"] for e in eps_history[:3] if e["yoy_growth"] is not None]
        ry = [r["yoy_growth"] for r in revenue_history[:3] if r["yoy_growth"] is not None]
        return {
            "ticker":               ticker,
            "source":               "fmp",
            "eps_history":          eps_history,
            "revenue_history":      revenue_history,
            "gross_margin_history": gross_margin,
            "latest_eps":           _first_yoy_with_val(eps_history, val_key="eps")[1] if eps_history else None,
            "latest_revenue":       _first_yoy_with_val(revenue_history, val_key="revenue")[1] if revenue_history else None,
            "eps_yoy_pct":          _first_yoy_with_val(eps_history)[0] if eps_history else None,
            "rev_yoy_pct":          _first_yoy_with_val(revenue_history, val_key="revenue")[0] if revenue_history else None,
            "eps_accel":            len(ey) >= 2 and ey[0] > ey[1],
            "rev_accel":            len(ry) >= 2 and ry[0] > ry[1],
            "as_of":                date.today().isoformat(),
        }
    except Exception as e:
        print(f"  [FMP] {ticker}: {e}")
        return None


# =============================================================================
# SOURCE 6 -- YFINANCE  (absolute last resort -- centralized here so no script
#             needs its own yfinance fallback)
# =============================================================================

def _yfinance_ohlcv(ticker: str, period: str = "1y") -> Optional[pd.DataFrame]:
    """
    yfinance absolute last resort. WARP-safe via ssl patch already applied globally.
    Scripts should NOT import yfinance directly -- use get_ohlcv() which calls this.
    """
    try:
        import yfinance as yf
        # WARP bypass: the ssl patch at module load already handles this,
        # but also patch yf session just in case
        try:
            import requests as _req
            _s = _req.Session(); _s.verify = False
            yf.base.requests = _s  # type: ignore
        except Exception:
            pass
        df = yf.download(ticker, period=period, progress=False,
                         auto_adjust=True, timeout=20)
        if df is not None and not df.empty and len(df) >= 20:
            # yfinance returns MultiIndex columns when downloading -- flatten
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
            df.index.name = "Date"
            return _add_indicators(df)
    except Exception as e:
        print(f"  [yfinance] {ticker}: {e}")
    return None


# =============================================================================
# SOURCE 7 -- FRED  (macro data: yields, credit spreads, GDP, PCE)
# Key: FRED_API_KEY in .env  |  Free, no CC, extremely reliable
# Series examples: DGS10 (10Y yield), DGS2 (2Y), T10Y2Y (yield curve),
#                  BAMLH0A0HYM2 (HY spread), DEXUSEU (EUR/USD), DFII10 (TIPS)
# =============================================================================

def _fred_series(series_id: str, limit: int = 10) -> Optional[list[dict]]:
    """
    Fetch FRED series observations. Returns list of {date, value} newest-first.
    Uses FRED_API_KEY from .env. Falls back to Yahoo Finance if key missing.
    """
    if not FRED_KEY:
        return None
    try:
        r = _SESSION.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id":  series_id,
                "api_key":    FRED_KEY,
                "file_type":  "json",
                "sort_order": "desc",
                "limit":      str(limit),
            },
            timeout=10,
        )
        if r.status_code != 200:
            return None
        obs = r.json().get("observations", [])
        result = []
        for o in obs:
            try:
                val = float(o["value"])
                result.append({"date": o["date"], "value": val})
            except (ValueError, KeyError):
                continue   # skip "." (missing data) entries
        return result if result else None
    except Exception as e:
        print(f"  [FRED] {series_id}: {e}")
        return None


def get_macro(series_id: str, limit: int = 10) -> Optional[list[dict]]:
    """
    Get macro data from FRED. Returns list of {date, value} newest-first.

    Common series:
      DGS10       -- 10-Year Treasury Yield (%)
      DGS2        -- 2-Year Treasury Yield (%)
      T10Y2Y      -- 10Y-2Y Yield Curve Spread (%)
      BAMLH0A0HYM2 -- ICE BofA High Yield Spread (%)
      VIXCLS      -- VIX Closing Level
      DEXUSEU     -- EUR/USD exchange rate
      DFII10      -- 10-Year TIPS yield (real rate)
      UMCSENT     -- U of Michigan Consumer Sentiment
      UNRATE      -- Unemployment Rate
      CPIAUCSL    -- CPI All Urban Consumers
    """
    t0 = time.time()
    result = _fred_series(series_id, limit=limit)
    if result:
        if _HEALTH:
            _HEALTH.record_success("fred", latency=time.time() - t0)
        return result
    if _HEALTH:
        _HEALTH.record_failure("fred", error=f"series {series_id} returned None")
    # Yahoo Finance fallback for some macro series
    _yahoo_series = {
        "DGS10":  "^TNX",   # 10Y yield × 10 on Yahoo
        "DGS2":   "^IRX",   # 13-week T-bill (not ideal but available)
        "VIXCLS": "^VIX",
    }
    if series_id in _yahoo_series:
        try:
            yahoo_sym = _yahoo_series[series_id]
            crumb = _get_yahoo_crumb()
            params = {"interval": "1d", "range": "5d"}
            if crumb:
                params["crumb"] = crumb
            r = _YF_SESSION.get(
                f"https://query2.finance.yahoo.com/v8/finance/chart/{yahoo_sym}",
                params=params, timeout=10,
            )
            if r.status_code == 200:
                result_data = r.json().get("chart", {}).get("result")
                if result_data:
                    closes = result_data[0]["indicators"]["quote"][0].get("close", [])
                    timestamps = result_data[0].get("timestamp", [])
                    pairs = [(t, c) for t, c in zip(timestamps, closes) if c is not None]
                    pairs.sort(reverse=True)
                    scale = 0.1 if series_id == "DGS10" else 1.0  # TNX is yield×10
                    return [
                        {"date": str(datetime.fromtimestamp(t).date()),
                         "value": round(v * scale, 4)}
                        for t, v in pairs[:limit]
                    ]
        except Exception:
            pass
    return None


# =============================================================================
# PUBLIC INTERFACE
# =============================================================================

# Source list -- tried in priority order until one works
_OHLCV_SOURCES = [
    ("polygon",  _polygon_ohlcv),
    ("tiingo",   _tiingo_ohlcv),
    ("stooq",    _stooq_ohlcv),       # FREE [OK] -- works on WARP
    ("yahoo",    _yahoo_ohlcv_v8),    # crumb-fixed [OK] -- works on WARP
    ("yfinance", _yfinance_ohlcv),    # absolute last resort -- centralized here
]

_QUOTE_SOURCES = [
    ("polygon",  _polygon_quote),
    ("finnhub",  _finnhub_quote),     # already have key [OK]
    ("yahoo",    _yahoo_quote),       # crumb-fixed [OK]
]

_FUND_SOURCES = [
    ("edgar",     _edgar_fundamentals),      # FREE, no key, authoritative 10-Q/10-K [OK]
    ("edgar_6k",  _edgar_6k_fundamentals),   # FREE, no key, foreign filers 6-K quarterly
    ("edgar_20f", _edgar_20f_fundamentals),  # FREE, no key, foreign filers 20-F annual (ASML/TSM/STM/UMC)
    ("fmp",       _fmp_fundamentals),        # best quality if key available
    ("finnhub",   _finnhub_fundamentals),    # free key [OK]
    ("yahoo",     _yahoo_fundamentals),      # free fallback [OK]
]


def get_ohlcv(ticker: str, period: str = "1y",
              min_rows: int = 20) -> Optional[pd.DataFrame]:
    """
    Get OHLCV DataFrame with indicators. Tries sources in health-ranked order.
    Compatible with portfolio_engine.get_price_data() -- drop-in replacement.
    Returns DataFrame: Open,High,Low,Close,Volume,MA20,MA50,MA150,MA200,ADTV

    24-hour disk cache: avoids multiple scripts re-fetching the same ticker
    in the same pipeline run. Cache stored in data/ohlcv_cache/.
    """
    ticker = ticker.upper().strip()
    today_str = date.today().isoformat()

    # 1. In-process memory cache (within same Python session)
    mem_key = f"{ticker}_{period}_{today_str}"
    if mem_key in _OHLCV_MEM:
        return _OHLCV_MEM[mem_key]

    # 2. Disk cache (shared across scripts in same day)
    cache_file = _CACHE_DIR / f"{ticker}_{period}_{today_str}.pkl"
    if cache_file.exists():
        try:
            df = pd.read_pickle(cache_file)
            if _validate(df, min_rows):
                _OHLCV_MEM[mem_key] = df
                return df
        except Exception:
            cache_file.unlink(missing_ok=True)   # corrupt cache → delete

    # Build lookup map for dynamic dispatch
    _ohlcv_map = {name: fn for name, fn in _OHLCV_SOURCES}

    # Key-configured sources always take priority (premium API > free fallback)
    # Then fill with health-ranked non-key sources for redundancy
    key_sources   = [("polygon", _polygon_ohlcv)] if POLYGON_KEY else []
    key_sources  += [("tiingo",  _tiingo_ohlcv)]  if TIINGO_KEY  else []
    key_set       = {n for n, _ in key_sources}

    if _HEALTH:
        ranked = _HEALTH.get_ranked("ohlcv")
        fallback = [(n, _ohlcv_map[n]) for n in ranked
                    if n in _ohlcv_map and n not in key_set]
        ranked_set = set(ranked)
        for n, fn in _OHLCV_SOURCES:
            if n not in ranked_set and n not in key_set:
                fallback.append((n, fn))
    else:
        fallback = [(n, fn) for n, fn in _OHLCV_SOURCES if n not in key_set]

    ordered = key_sources + fallback

    for source_name, fn in ordered:
        t0 = time.time()
        try:
            df = fn(ticker, period)
            if _validate(df, min_rows):
                df.attrs["source"] = source_name
                if _HEALTH:
                    _HEALTH.record_success(source_name, latency=time.time() - t0)
                # Write to cache
                try:
                    df.to_pickle(cache_file)
                    _OHLCV_MEM[mem_key] = df
                except Exception:
                    pass
                return df
        except Exception as e:
            if _HEALTH:
                _HEALTH.record_failure(source_name, error=str(e)[:120])
            print(f"  [DataEngine] {ticker} {source_name}: {e}")
            continue
    print(f"  [DataEngine] [X] {ticker}: ALL ohlcv sources failed "
          f"(polygon={bool(POLYGON_KEY)}, tiingo={bool(TIINGO_KEY)})")
    return None


def get_quote(ticker: str) -> Optional[float]:
    """
    Get current/latest price. Tries sources in health-ranked order.
    Compatible with portfolio_engine.get_current_price() -- drop-in replacement.
    """
    ticker = ticker.upper().strip()
    _quote_map = {name: fn for name, fn in _QUOTE_SOURCES}

    # Key-configured quote sources first
    key_sources  = [("polygon", _polygon_quote)] if POLYGON_KEY else []
    key_sources += [("finnhub", _finnhub_quote)] if FINNHUB_KEY else []
    key_set = {n for n, _ in key_sources}

    if _HEALTH:
        ranked   = _HEALTH.get_ranked("quote")
        fallback = [(n, _quote_map[n]) for n in ranked
                    if n in _quote_map and n not in key_set]
        ranked_set = set(ranked)
        for n, fn in _QUOTE_SOURCES:
            if n not in ranked_set and n not in key_set:
                fallback.append((n, fn))
    else:
        fallback = [(n, fn) for n, fn in _QUOTE_SOURCES if n not in key_set]

    ordered = key_sources + fallback

    for source_name, fn in ordered:
        t0 = time.time()
        try:
            price = fn(ticker)
            if price and price > 0:
                if _HEALTH:
                    _HEALTH.record_success(source_name, latency=time.time() - t0)
                return float(price)
        except Exception as e:
            if _HEALTH:
                _HEALTH.record_failure(source_name, error=str(e)[:120])
            continue
    return None


def _manual_fundamentals_override(ticker: str) -> Optional[dict]:
    """
    CIO manual override for foreign private issuers (20-F filers) and other
    non-EDGAR stocks where automated fetch consistently fails.
    Reads from data/manual_fundamentals.json.
    Source of truth: CIO updates this file from company IR pages.
    """
    try:
        manual_file = ROOT / "data" / "manual_fundamentals.json"
        if not manual_file.exists():
            return None
        overrides = json.loads(manual_file.read_text(encoding="utf-8"))
        entry = overrides.get(ticker)
        if not entry:
            return None
        # Ensure required fields exist before returning
        if entry.get("eps_history") or entry.get("eps_yoy_pct") is not None:
            print(f"  [Manual Override] {ticker}: using CIO-entered fundamentals ({entry.get('_verify_date', 'unknown date')})")
            return entry
    except Exception as e:
        print(f"  [Manual Override] {ticker}: read failed — {e}")
    return None


def get_fundamentals(ticker: str, quarters: int = 8) -> Optional[dict]:
    """
    Get quarterly earnings + revenue. Tries sources in health-ranked order.
    Returns standard dict with eps_history, revenue_history, eps_accel, rev_accel.

    Source priority:
      1. EDGAR — authoritative 10-Q/10-K (free, no key)
      2. FMP   — structured, fast (if key available)
      3. Finnhub — basic (if key available)
      4. Yahoo   — fallback
      5. Manual override — CIO-entered data for foreign filers (20-F)
         File: data/manual_fundamentals.json
    """
    ticker = ticker.upper().strip()
    _fund_map = {name: fn for name, fn in _FUND_SOURCES}

    # EDGAR always first (authoritative ground truth, no key needed)
    # edgar_6k second for foreign filers (6-K quarterly reports — ARM, TSM, ASML etc.)
    # FMP with key third, then Finnhub with key, then Yahoo as fallback
    key_sources  = [("edgar",    _edgar_fundamentals)]
    key_sources += [("edgar_6k", _edgar_6k_fundamentals)]  # no key needed, always try
    key_sources += [("fmp",      _fmp_fundamentals)]   if FMP_KEY     else []
    key_sources += [("finnhub",  _finnhub_fundamentals)] if FINNHUB_KEY else []
    key_set = {n for n, _ in key_sources}

    if _HEALTH:
        ranked   = _HEALTH.get_ranked("eps")
        fallback = [(n, _fund_map[n]) for n in ranked
                    if n in _fund_map and n not in key_set]
        ranked_set = set(ranked)
        for n, fn in _FUND_SOURCES:
            if n not in ranked_set and n not in key_set:
                fallback.append((n, fn))
    else:
        fallback = [(n, fn) for n, fn in _FUND_SOURCES if n not in key_set]

    ordered = key_sources + fallback

    for source_name, fn in ordered:
        t0 = time.time()
        try:
            result = fn(ticker, quarters)
            if result and result.get("eps_history"):
                # D5: validate before returning
                validated, warns = _validate_fund(ticker, result, source_name)
                if validated is None:
                    # BLOCK-level violation — skip this source, try next.
                    # NOTE: D5 blocks are DATA QUALITY issues, not API failures.
                    # Record as a partial success (API responded) to avoid tanking
                    # source health scores for connectivity-healthy sources.
                    if _HEALTH:
                        _HEALTH.record_success(source_name, latency=time.time() - t0)
                    continue
                if _HEALTH:
                    _HEALTH.record_success(source_name, latency=time.time() - t0)
                # Post-process: fix eps_yoy_pct/rev_yoy_pct if None but history has data
                # (Old cached files may have eps_yoy_pct=None when latest quarter just filed
                # and has no year-ago comparison yet — fall back to next available quarter)
                _patched = False
                if validated.get("eps_yoy_pct") is None and validated.get("eps_history"):
                    fallback = _first_yoy(validated["eps_history"])
                    if fallback is not None:
                        validated["eps_yoy_pct"] = fallback
                        # Track how many quarters we skipped (0 = current, 1+ = stale)
                        offset = next(
                            (i for i, q in enumerate(validated["eps_history"])
                             if q.get("yoy_growth") is not None), None)
                        validated["yoy_quarters_offset"] = offset if offset else 0
                        _patched = True
                if validated.get("rev_yoy_pct") is None and validated.get("revenue_history"):
                    fallback = _first_yoy(validated["revenue_history"])
                    if fallback is not None:
                        validated["rev_yoy_pct"] = fallback
                        _patched = True
                # Write patched values back to EDGAR disk cache so all readers see
                # the same corrected data (trend_template, mode_a, session_start etc.)
                if _patched and source_name == "edgar":
                    try:
                        _fund_cache_file = _EDGAR_CACHE_DIR / f"{ticker}_fund.json"
                        if _fund_cache_file.exists():
                            cached_raw = json.loads(_fund_cache_file.read_text(encoding="utf-8"))
                            cached_raw["eps_yoy_pct"] = validated.get("eps_yoy_pct")
                            cached_raw["rev_yoy_pct"] = validated.get("rev_yoy_pct")
                            if "yoy_quarters_offset" in validated:
                                cached_raw["yoy_quarters_offset"] = validated["yoy_quarters_offset"]
                            _fund_cache_file.write_text(
                                json.dumps(cached_raw, separators=(",", ":")),
                                encoding="utf-8")
                    except Exception:
                        pass  # write-back failure is non-fatal
                return validated
        except Exception as e:
            if _HEALTH:
                _HEALTH.record_failure(source_name, error=str(e)[:120])
            print(f"  [Fundamentals] {ticker} {source_name}: {e}")
            continue

    # All automated sources failed — try CIO manual override
    # (foreign filers: ARM, STM, TSM, UMC, GFS that don't file US 10-Q/10-K)
    manual = _manual_fundamentals_override(ticker)
    if manual:
        return manual

    return None


def get_market_cap(ticker: str) -> Optional[float]:
    """Get market cap in USD. Finnhub primary (have key), FMP backup, Yahoo fallback."""
    ticker = ticker.upper().strip()
    # 1. Finnhub
    if FINNHUB_KEY:
        t0 = time.time()
        try:
            r = _SESSION.get(
                "https://finnhub.io/api/v1/stock/metric",
                params={"symbol": ticker, "metric": "all", "token": FINNHUB_KEY},
                timeout=8)
            mc_m = r.json().get("metric", {}).get("marketCapitalization", 0)
            if mc_m:
                if _HEALTH:
                    _HEALTH.record_success("finnhub", latency=time.time() - t0)
                return float(mc_m) * 1_000_000
        except Exception as e:
            if _HEALTH:
                _HEALTH.record_failure("finnhub", error=str(e)[:120])
    # 2. FMP (new stable endpoint)
    if FMP_KEY:
        t0 = time.time()
        try:
            r = _SESSION.get(
                "https://financialmodelingprep.com/stable/profile",
                params={"symbol": ticker, "apikey": FMP_KEY}, timeout=8)
            data = r.json()
            if data and isinstance(data, list):
                mc = data[0].get("mktCap", 0)
                if mc:
                    if _HEALTH:
                        _HEALTH.record_success("fmp", latency=time.time() - t0)
                    return float(mc)
        except Exception as e:
            if _HEALTH:
                _HEALTH.record_failure("fmp", error=str(e)[:120])
    # 3. Yahoo quoteSummary
    t0 = time.time()
    crumb = _get_yahoo_crumb()
    try:
        params = {"modules": "summaryDetail", "formatted": "false"}
        if crumb:
            params["crumb"] = crumb
        r = _YF_SESSION.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
            params=params, timeout=10)
        if r.status_code == 200:
            result = r.json().get("quoteSummary", {}).get("result")
            if result:
                mc = result[0].get("summaryDetail", {}).get("marketCap", {}).get("raw", 0)
                if mc:
                    if _HEALTH:
                        _HEALTH.record_success("yahoo", latency=time.time() - t0)
                    return float(mc)
    except Exception as e:
        if _HEALTH:
            _HEALTH.record_failure("yahoo", error=str(e)[:120])
    return None


# ── Compatibility aliases (drop-in for portfolio_engine) ─────────────────────
get_price_data    = get_ohlcv
get_current_price = get_quote


# ── Diagnostics ───────────────────────────────────────────────────────────────

def test_all_sources(ticker: str = "AAPL") -> dict:
    """
    Test all data sources. Run this to diagnose data issues.
    Usage: python -c "from utils.data_engine import test_all_sources; test_all_sources()"
    """
    print(f"\n{'='*55}")
    print(f"  AlphaAbsolute Data Engine -- Source Diagnostics")
    print(f"  Ticker: {ticker} | {date.today()}")
    print(f"  Keys: Polygon={bool(POLYGON_KEY)} | Tiingo={bool(TIINGO_KEY)}")
    print(f"        FMP={bool(FMP_KEY)} | Finnhub={bool(FINNHUB_KEY)}")
    print(f"{'='*55}")

    results = {}

    # OHLCV tests
    print("\n[OHLCV Sources]")
    for name, fn in _OHLCV_SOURCES:
        try:
            df = fn(ticker, "6mo")
            if _validate(df, 5):
                last_price = df["Close"].iloc[-1]
                last_date  = df.index[-1].date() if hasattr(df.index[-1], 'date') else df.index[-1]
                print(f"  [OK] {name:<10} ${last_price:.2f} | {len(df)} bars | last={last_date}")
                results[f"ohlcv_{name}"] = True
            else:
                print(f"  [X] {name:<10} returned invalid data")
                results[f"ohlcv_{name}"] = False
        except Exception as e:
            print(f"  [X] {name:<10} error: {e}")
            results[f"ohlcv_{name}"] = False

    # Quote tests
    print("\n[Quote Sources]")
    for name, fn in _QUOTE_SOURCES:
        try:
            price = fn(ticker)
            if price and price > 0:
                print(f"  [OK] {name:<10} ${price:.2f}")
                results[f"quote_{name}"] = price
            else:
                print(f"  [X] {name:<10} no price returned")
                results[f"quote_{name}"] = None
        except Exception as e:
            print(f"  [X] {name:<10} error: {e}")
            results[f"quote_{name}"] = None

    # Fundamentals test
    print("\n[Fundamentals Sources]")
    for name, fn in _FUND_SOURCES:
        try:
            fund = fn(ticker, 4)
            if fund and fund.get("eps_history"):
                latest_eps = fund["eps_history"][0]["eps"]
                print(f"  [OK] {name:<10} latest EPS=${latest_eps:.2f} | "
                      f"accel={fund.get('eps_accel')} | "
                      f"{len(fund['eps_history'])} quarters")
                results[f"fund_{name}"] = True
            else:
                print(f"  [X] {name:<10} no data")
                results[f"fund_{name}"] = False
        except Exception as e:
            print(f"  [X] {name:<10} error: {e}")
            results[f"fund_{name}"] = False

    # Final verdict
    print(f"\n{'─'*55}")
    working_ohlcv = [n for n in ["polygon","tiingo","stooq","yahoo"] if results.get(f"ohlcv_{n}")]
    if working_ohlcv:
        print(f"  [OK] OHLCV will use: {working_ohlcv[0]} (primary)")
    else:
        print(f"  [X] NO OHLCV source working! Check network / WARP settings.")
    print(f"{'='*55}\n")
    return results


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    test_all_sources(ticker)
