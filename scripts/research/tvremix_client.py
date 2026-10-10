"""
tvremix MCP client for AlphaModel-TH
=====================================
Wraps tvremix.xyz/api/mcp/v1 as a simple Python function library.
Used by thai_data_layer.py as primary OHLCV source (Yahoo Finance = fallback).

Ticker mapping:
  .BK tickers  →  SET:TICKER   (e.g. DELTA.BK → SET:DELTA)
  ^SET.BK      →  SET:SET      (real SET composite ~1594)
  TDEX.BK      →  SET:SET      (replace ETF proxy with real index)

Rate limits (free beta): 20/min · 200/hr · 1,500/day
Concurrency: serialize calls with _SLEEP_SEC between requests.

Usage:
  from tvremix_client import fetch_tvremix, fetch_set_index_tvremix, TvremixError
"""

import json, os, ssl, time, urllib.request, urllib.error, warnings
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────────────────
_BASE_URL  = "https://tvremix.xyz/api/mcp/v1"
_SESSION   = "alphaabsolute-th"
_SLEEP_SEC = 0.0   # caller controls sleep; set >3.0 only in bulk loops
_TIMEOUT   = 25

# API key: loaded from .env or environment variable TVREMIX_API_KEY
def _load_key() -> str:
    key = os.environ.get("TVREMIX_API_KEY", "")
    if not key:
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("TVREMIX_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        raise TvremixError("TVREMIX_API_KEY not set in .env or environment")
    return key


class TvremixError(Exception):
    pass


# ── SSL context (bypass Windows certificate chain issues) ─────────────────────
def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ── Core MCP call ─────────────────────────────────────────────────────────────
def _call_tool(name: str, args: dict, api_key: str) -> dict:
    """Send one MCP tools/call request. Returns parsed result dict."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": args}
    }).encode("utf-8")
    req = urllib.request.Request(
        _BASE_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Mcp-Session-Id": _SESSION,
        },
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=_TIMEOUT) as resp:
                raw = json.loads(resp.read())
            if "result" not in raw:
                raise TvremixError(f"No result in response: {raw}")
            content = raw["result"].get("content", [])
            if not content:
                raise TvremixError("Empty content in response")
            return json.loads(content[0]["text"])
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # tvremix free plan: 20/min limit (not hourly) — 10s enough to reset
                wait = 10 * (attempt + 1)
                print(f"  [tvremix] 429 rate-limit — sleeping {wait}s")
                time.sleep(wait)
            elif e.code in (400, 403, 404):
                raise TvremixError(f"HTTP {e.code} for {name}({args})")
            else:
                time.sleep(5 * (attempt + 1))
        except (json.JSONDecodeError, KeyError) as e:
            raise TvremixError(f"Parse error: {e}")
        except Exception as e:
            if attempt == 2:
                raise TvremixError(f"Network error after 3 attempts: {e}")
            time.sleep(5)
    raise TvremixError(f"Failed after 3 attempts: {name}({args})")


# ── Ticker mapping ────────────────────────────────────────────────────────────
def _to_tv_symbol(bk_ticker: str) -> str:
    """Convert Yahoo .BK ticker to TradingView SET: symbol.

    Examples:
      DELTA.BK  → SET:DELTA
      ^SET.BK   → SET:SET      (real SET composite index)
      TDEX.BK   → SET:SET      (replace ETF proxy with real index)
    """
    if bk_ticker in ("^SET.BK", "TDEX.BK"):
        return "SET:SET"
    # Remove .BK suffix
    base = bk_ticker.replace(".BK", "").lstrip("^")
    return f"SET:{base}"


# ── OHLCV fetch ───────────────────────────────────────────────────────────────
def fetch_tvremix(bk_ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch OHLCV from tvremix for a .BK ticker.

    Returns DataFrame with index=datetime (ICT, tz-naive), columns=open/high/low/close/volume.
    Same format as thai_data_layer.fetch_yahoo() — drop-in replacement.

    Args:
        bk_ticker: Yahoo .BK format, e.g. 'DELTA.BK' or '^SET.BK'
        start:     'YYYY-MM-DD'
        end:       'YYYY-MM-DD'

    Raises:
        TvremixError: if symbol not found or data unavailable
    """
    api_key = _load_key()
    tv_sym = _to_tv_symbol(bk_ticker)

    # Request 300 bars (max for daily) — filter by date after
    result = _call_tool("get_ohlcv", {
        "symbol": tv_sym,
        "interval": "1D",
        "bars": 300,
    }, api_key)

    if not result.get("success"):
        raise TvremixError(f"tvremix get_ohlcv failed for {tv_sym}: {result.get('error')}")

    bars = result.get("bars", [])
    if not bars:
        return pd.DataFrame()

    records = []
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt   = datetime.strptime(end, "%Y-%m-%d")

    for b in bars:
        # TradingView timestamps are Unix seconds (exchange local time)
        # SET bars are ICT (UTC+7) — convert accordingly
        # Convert Unix timestamp to Bangkok date (ICT = UTC+7, no DST)
        # utcfromtimestamp is deprecated; use timezone-aware fromtimestamp instead
        _ICT = timezone(timedelta(hours=7))
        dt = datetime.fromtimestamp(b["t"], tz=_ICT).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        if dt < start_dt or dt > end_dt:
            continue
        records.append({
            "date":   dt,
            "open":   b.get("o"),
            "high":   b.get("h"),
            "low":    b.get("l"),
            "close":  b.get("c"),
            "volume": int(b["v"]) if b.get("v") else None,
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records).set_index("date").sort_index()
    df = df.dropna(subset=["close"])
    return df


def fetch_set_index_tvremix(lookback_days: int = 300) -> pd.DataFrame:
    """Fetch SET composite index OHLCV from tvremix (SET:SET).

    Returns DataFrame indexed by date with columns: open, high, low, close, volume.
    SET:SET = real SET composite ~1594 (matches Settrade API).
    """
    api_key = _load_key()
    result = _call_tool("get_ohlcv", {
        "symbol": "SET:SET",
        "interval": "1D",
        "bars": lookback_days,
    }, api_key)

    if not result.get("success"):
        raise TvremixError(f"tvremix SET:SET failed: {result.get('error')}")

    bars = result.get("bars", [])
    if not bars:
        return pd.DataFrame()

    records = []
    for b in bars:
        _ICT = timezone(timedelta(hours=7))
        dt = datetime.fromtimestamp(b["t"], tz=_ICT).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        records.append({
            "date":   dt,
            "open":   b.get("o"),
            "high":   b.get("h"),
            "low":    b.get("l"),
            "close":  b.get("c"),
            "volume": int(b["v"]) if b.get("v") else 0,
        })

    df = pd.DataFrame(records).set_index("date").sort_index()
    return df.dropna(subset=["close"])


def get_quote_tvremix(bk_ticker: str) -> dict:
    """Get live quote for a .BK ticker. Returns dict with price, volume, etc."""
    api_key = _load_key()
    tv_sym = _to_tv_symbol(bk_ticker)
    result = _call_tool("get_quote", {"symbol": tv_sym}, api_key)
    if not result.get("success"):
        raise TvremixError(f"get_quote failed for {tv_sym}: {result.get('error')}")
    return result.get("data", {})


def get_set_screener(min_volume_thb: float = 20_000_000, limit: int = 200) -> pd.DataFrame:
    """Run SET screener and return top stocks by 3M performance.

    Returns DataFrame with columns: symbol, name, close, perf_3m, perf_1m, rsi, volume
    """
    api_key = _load_key()
    result = _call_tool("run_screener", {
        "market": "thailand",
        "sort_by": "Perf.3M",
        "sort_order": "desc",
        "limit": limit,
        "filters": [
            {"left": "type", "operation": "equal", "right": "stock"},
        ],
    }, api_key)

    if not result.get("success"):
        raise TvremixError(f"Screener failed: {result.get('error')}")

    rows = result.get("data", {}).get("results", [])
    if not rows:
        return pd.DataFrame()

    records = []
    for r in rows:
        vol = r.get("average_volume_10d_calc", 0) or 0
        close = r.get("close", 0) or 0
        # Estimate daily value in THB (close × avg_volume)
        adtv_thb = close * vol
        records.append({
            "symbol":   r.get("symbol", ""),
            "name":     r.get("name", ""),
            "close":    close,
            "perf_3m":  r.get("Perf.3M", 0) or 0,
            "perf_1m":  r.get("Perf.1M", 0) or 0,
            "perf_1y":  r.get("Perf.Y", 0) or 0,
            "rsi":      r.get("RSI", 50) or 50,
            "volume_10d": vol,
            "adtv_thb": adtv_thb,
            "ema50":    r.get("EMA50"),
            "ema200":   r.get("EMA200"),
        })

    df = pd.DataFrame(records)
    return df[df["adtv_thb"] >= min_volume_thb].reset_index(drop=True)


# ── Stability check ───────────────────────────────────────────────────────────
def ping() -> bool:
    """Quick connectivity check. Returns True if tvremix API is reachable."""
    try:
        api_key = _load_key()
        result = _call_tool("get_quote", {"symbol": "SET:PTT"}, api_key)
        return bool(result.get("success"))
    except Exception:
        return False


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("=== tvremix_client self-test ===")
    print(f"Ping: {ping()}")

    print("\n--- SET index (SET:SET) last 5 bars ---")
    df = fetch_set_index_tvremix(lookback_days=10)
    print(df.tail(5).to_string())

    print("\n--- DELTA.BK last 5 bars ---")
    from datetime import date, timedelta
    end = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    df2 = fetch_tvremix("DELTA.BK", start, end)
    print(df2.tail(5).to_string())
    time.sleep(_SLEEP_SEC)

    print("\n--- Quote: AOT.BK ---")
    q = get_quote_tvremix("AOT.BK")
    print(q)
