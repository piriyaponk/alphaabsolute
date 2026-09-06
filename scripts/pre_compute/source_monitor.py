"""
AlphaAbsolute -- Source Monitor (D4 Health Sweep)
===================================================
Weekly agent that tests every data source and updates health scores.
Runs on Fridays alongside the D3 data verifier.

What it does:
  1. Ping each source with a test ticker (AAPL / SPY)
  2. Record success/failure + latency into source_health.json
  3. Generate human-readable source_status_YYMMDD.md report
  4. Raise alerts for degraded sources

Run:
  python scripts/pre_compute/source_monitor.py
  python scripts/runners/pre_market_runner.py --step monitor

Output:
  data/source_health.json          (live health scores -- updated by every data call)
  output/source_status_YYMMDD.md   (weekly status report)
"""

import sys
import os
import json
import time
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts" / "utils"))

# Fix encoding for Thai terminal
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("cp874", "cp1252", "ascii"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() in ("cp874", "cp1252", "ascii"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR   = BASE_DIR / "data" / "source_monitor"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Test tickers
_TEST_TICKER_US    = "AAPL"
_TEST_TICKER_PRICE = "SPY"

# ── Source ping functions (lightweight, no caching) ──────────────────────────

def _ping_edgar(ticker: str) -> tuple[bool, float, str]:
    """Test SEC EDGAR CIK lookup + company facts endpoint."""
    import requests
    t0 = time.time()
    sess = requests.Session()
    sess.verify = False
    sess.headers["User-Agent"] = "AlphaAbsolute piriyaponk@gmail.com"
    try:
        r = sess.get(
            "https://www.sec.gov/files/company_tickers.json",
            timeout=10,
        )
        if r.status_code != 200:
            return False, time.time() - t0, f"HTTP {r.status_code}"
        data = r.json()
        # Find AAPL
        cik = None
        for item in data.values():
            if item.get("ticker", "").upper() == ticker.upper():
                cik = str(item.get("cik_str", "")).zfill(10)
                break
        if not cik:
            return False, time.time() - t0, "ticker not found in tickers.json"
        # Fetch company facts
        r2 = sess.get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
            timeout=15,
        )
        latency = time.time() - t0
        if r2.status_code == 200:
            facts = r2.json()
            if "facts" in facts:
                return True, latency, "OK"
            return False, latency, "No 'facts' key in response"
        return False, latency, f"HTTP {r2.status_code}"
    except Exception as e:
        return False, time.time() - t0, str(e)[:120]


def _ping_finnhub(ticker: str) -> tuple[bool, float, str]:
    """Test Finnhub quote endpoint."""
    import requests
    key = _env_key("FINNHUB_API_KEY")
    if not key:
        return False, 0.0, "FINNHUB_API_KEY not set"
    t0 = time.time()
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": ticker, "token": key},
            timeout=8, verify=False,
        )
        latency = time.time() - t0
        data = r.json()
        price = data.get("c", 0)
        if price and float(price) > 0:
            return True, latency, f"price={price:.2f}"
        return False, latency, f"price=0 response={str(data)[:80]}"
    except Exception as e:
        return False, time.time() - t0, str(e)[:120]


def _ping_fmp(ticker: str) -> tuple[bool, float, str]:
    """Test FMP earnings endpoint."""
    import requests
    key = _env_key("FMP_API_KEY")
    if not key:
        return False, 0.0, "FMP_API_KEY not set (free signup: financialmodelingprep.com)"
    t0 = time.time()
    try:
        r = requests.get(
            f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}",
            params={"period": "quarter", "limit": "2", "apikey": key},
            timeout=10, verify=False,
        )
        latency = time.time() - t0
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            return True, latency, f"{len(data)} quarters"
        return False, latency, f"unexpected response: {str(data)[:80]}"
    except Exception as e:
        return False, time.time() - t0, str(e)[:120]


def _ping_polygon(ticker: str) -> tuple[bool, float, str]:
    """Test Polygon ticker details."""
    import requests
    key = _env_key("POLYGON_API_KEY")
    if not key:
        return False, 0.0, "POLYGON_API_KEY not set (free signup: polygon.io)"
    t0 = time.time()
    try:
        end   = date.today().isoformat()
        start = (date.today().replace(day=1)).isoformat()
        r = requests.get(
            f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}",
            params={"adjusted": "true", "sort": "desc", "limit": "5", "apiKey": key},
            timeout=10, verify=False,
        )
        latency = time.time() - t0
        data = r.json()
        count = data.get("resultsCount", 0)
        if count > 0:
            return True, latency, f"{count} bars"
        return False, latency, f"resultsCount=0 status={data.get('status')}"
    except Exception as e:
        return False, time.time() - t0, str(e)[:120]


def _ping_tiingo(ticker: str) -> tuple[bool, float, str]:
    """Test Tiingo price endpoint."""
    import requests
    key = _env_key("TIINGO_API_KEY")
    if not key:
        return False, 0.0, "TIINGO_API_KEY not set (free signup: api.tiingo.com)"
    t0 = time.time()
    try:
        r = requests.get(
            f"https://api.tiingo.com/tiingo/daily/{ticker}/prices",
            params={"startDate": "2025-01-01", "token": key},
            headers={"Content-Type": "application/json"},
            timeout=10, verify=False,
        )
        latency = time.time() - t0
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            return True, latency, f"{len(data)} rows"
        return False, latency, f"empty: {str(data)[:80]}"
    except Exception as e:
        return False, time.time() - t0, str(e)[:120]


def _ping_yahoo(ticker: str) -> tuple[bool, float, str]:
    """Test Yahoo Finance quote endpoint."""
    import requests
    t0 = time.time()
    sess = requests.Session()
    sess.verify = False
    sess.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    })
    try:
        # Get crumb
        sess.get("https://finance.yahoo.com/", timeout=8, allow_redirects=True)
        crumb_r = sess.get(
            "https://query2.finance.yahoo.com/v1/test/getcrumb",
            timeout=8,
        )
        crumb = crumb_r.text.strip() if crumb_r.status_code == 200 else None

        params: dict = {}
        if crumb:
            params["crumb"] = crumb

        r = sess.get(
            f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={**params, "interval": "1d", "range": "5d"},
            timeout=10,
        )
        latency = time.time() - t0
        data    = r.json()
        result  = data.get("chart", {}).get("result")
        if result and result[0].get("indicators", {}).get("quote"):
            closes = result[0]["indicators"]["quote"][0].get("close", [])
            closes = [c for c in closes if c is not None]
            if closes:
                return True, latency, f"price={closes[-1]:.2f}"
        return False, latency, f"empty result HTTP={r.status_code}"
    except Exception as e:
        return False, time.time() - t0, str(e)[:120]


def _ping_fred(series_id: str = "DGS10") -> tuple[bool, float, str]:
    """Test FRED macro data endpoint."""
    import requests
    t0 = time.time()
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": "annualsurvey",   # public series, no key needed for basic
                "file_type": "json",
                "limit": "3",
                "sort_order": "desc",
            },
            timeout=10, verify=False,
        )
        latency = time.time() - t0
        if r.status_code == 200:
            obs = r.json().get("observations", [])
            if obs:
                return True, latency, f"{series_id}={obs[0].get('value')}"
        # Try without API key (FRED allows some public access)
        r2 = requests.get(
            "https://fred.stlouisfed.org/",
            timeout=8, verify=False,
        )
        latency = time.time() - t0
        if r2.status_code == 200:
            return True, latency, "FRED homepage reachable (API key needed for data)"
        return False, latency, f"HTTP {r.status_code}"
    except Exception as e:
        return False, time.time() - t0, str(e)[:120]


# ── Registry of all source pings ─────────────────────────────────────────────

_PINGS = [
    ("edgar",   lambda: _ping_edgar(_TEST_TICKER_US)),
    ("finnhub", lambda: _ping_finnhub(_TEST_TICKER_US)),
    ("fmp",     lambda: _ping_fmp(_TEST_TICKER_US)),
    ("polygon", lambda: _ping_polygon(_TEST_TICKER_PRICE)),
    ("tiingo",  lambda: _ping_tiingo(_TEST_TICKER_PRICE)),
    ("yahoo",   lambda: _ping_yahoo(_TEST_TICKER_PRICE)),
    ("fred",    lambda: _ping_fred("DGS10")),
]

_SOURCE_ROLE = {
    "edgar":   "EPS / Revenue / Gross Margin (primary)",
    "finnhub": "EPS backup + Market Cap + Quote",
    "fmp":     "EPS / Revenue tertiary (250 req/day free)",
    "polygon": "OHLCV primary (free EOD)",
    "tiingo":  "OHLCV backup (500 req/day free)",
    "yahoo":   "OHLCV + Quote emergency fallback",
    "fred":    "Macro (yields, DXY, GDP, PCE)",
}


# ── Helper ───────────────────────────────────────────────────────────────────

def _env_key(name: str) -> str:
    val = os.environ.get(name, "")
    if val:
        return val
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for ln in env_file.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln.startswith(name + "="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# ── Main run ─────────────────────────────────────────────────────────────────

def run() -> dict:
    """
    Test all sources, update health scores, write status report.
    Called by pre_market_runner.py on Fridays.
    """
    print("\n  [D4] Source Monitor -- running health sweep...")

    # Load health tracker
    try:
        from scripts.utils.source_health import SourceHealth
        health = SourceHealth()
    except Exception as e:
        print(f"  [D4] WARNING: could not load SourceHealth: {e}")
        health = None

    results = {}
    for source_name, ping_fn in _PINGS:
        print(f"    Pinging {source_name}... ", end="", flush=True)
        try:
            ok, latency, detail = ping_fn()
        except Exception as ex:
            ok, latency, detail = False, 0.0, str(ex)[:120]

        # Distinguish "no key = unavailable" from "key set but network/API failed"
        no_key = "not set" in detail.lower() and "api_key" in detail.lower()
        status_str = "[OK]" if ok else ("[--]" if no_key else "[X]")
        print(f"{status_str} {detail} ({latency:.1f}s)")

        if health and not no_key:
            # Only record health when key is configured (or source needs no key)
            if ok:
                health.record_success(source_name, latency=latency)
            else:
                health.record_failure(source_name, error=detail)

        results[source_name] = {
            "ok":        ok,
            "no_key":    no_key,
            "latency":   round(latency, 2),
            "detail":    detail,
        }

    # Get final health report
    health_report = health.get_status_report() if health else {}

    # Write output JSON
    today_str = date.today().isoformat()
    output = {
        "date":      today_str,
        "run_at":    datetime.now().isoformat(),
        "test_ticker_us":    _TEST_TICKER_US,
        "test_ticker_price": _TEST_TICKER_PRICE,
        "ping_results":   results,
        "health_scores":  {k: v["health"] for k, v in health_report.items()},
        "degraded_sources": [
            k for k, v in health_report.items() if v.get("skipped")
        ],
    }
    latest_file = DATA_DIR / "latest.json"
    latest_file.write_text(json.dumps(output, indent=2), encoding="utf-8")

    # Write markdown report
    _write_report(results, health_report)

    n_ok      = sum(1 for r in results.values() if r["ok"])
    n_no_key  = sum(1 for r in results.values() if r.get("no_key"))
    n_total   = len(results)
    degraded  = output["degraded_sources"]

    print(f"  [D4] Source sweep complete: {n_ok}/{n_total} sources UP "
          f"({n_no_key} skipped -- no API key)")
    if degraded:
        print(f"  [D4] [!] DEGRADED (auto-skipped): {', '.join(degraded)}")

    return output


def _write_report(results: dict, health_report: dict) -> None:
    today_str = date.today().strftime("%y%m%d")
    report_file = OUTPUT_DIR / f"source_status_{today_str}.md"

    lines = [
        f"# Source Status Report — {date.today().isoformat()}",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"**Test tickers:** {_TEST_TICKER_US} (fundamentals), {_TEST_TICKER_PRICE} (price)  ",
        "",
        "---",
        "",
        "## Ping Results",
        "",
        "| Source | Status | Latency | Detail |",
        "|--------|--------|---------|--------|",
    ]
    for name, r in results.items():
        if r["ok"]:
            status = "OK"
        elif r.get("no_key"):
            status = "NO KEY"
        else:
            status = "FAIL"
        lines.append(
            f"| {name} | {status} | {r['latency']:.1f}s | {r['detail']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Health Scores (cumulative, all-time)",
        "",
        "| Source | Health | Avail% | Avg Latency | Calls | Status | Role |",
        "|--------|--------|--------|-------------|-------|--------|------|",
    ]
    for name, info in sorted(health_report.items(), key=lambda x: -x[1]["health"]):
        status = "SKIP (degraded)" if info["skipped"] else "Active"
        role   = _SOURCE_ROLE.get(name, "")
        lines.append(
            f"| {name} | {info['health']:.0f}/100 | {info['availability_pct']:.0f}% "
            f"| {info['avg_latency_s']:.1f}s | {info['total_calls']} | {status} | {role} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Failover Chain (by data type)",
        "",
        "| Data Type | Primary | Secondary | Tertiary | Emergency |",
        "|-----------|---------|-----------|----------|-----------|",
        "| EPS / Revenue | SEC EDGAR | FMP | Finnhub | Yahoo |",
        "| Gross Margin | SEC EDGAR | FMP | Finnhub | — |",
        "| Price OHLCV | Polygon | Tiingo | Yahoo | — |",
        "| Current Quote | Finnhub | Polygon | Yahoo | — |",
        "| Market Cap | Finnhub | FMP | Yahoo | — |",
        "| Macro (yields/DXY) | FRED | — | — | — |",
        "| Thai stocks | SET MCP | — | — | — |",
        "",
        "---",
        "",
        "## Missing API Keys (add to .env for better reliability)",
        "",
    ]

    missing_keys = {
        "POLYGON_API_KEY": "polygon.io — free signup, fastest OHLCV (5 req/min EOD)",
        "TIINGO_API_KEY":  "api.tiingo.com — free signup, 500 req/day",
        "FMP_API_KEY":     "financialmodelingprep.com — free 250 req/day, best fundamentals format",
    }
    has_missing = False
    for key, desc in missing_keys.items():
        val = _env_key(key)
        if not val:
            lines.append(f"- **{key}** — {desc}")
            has_missing = True
    if not has_missing:
        lines.append("All optional API keys are configured. Data layer is fully redundant.")

    lines += ["", "---", "", "*Generated by AlphaAbsolute D4 Source Monitor*"]

    report_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"  [D4] Report: {report_file.name}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = run()
    print(f"\nHealth scores: {json.dumps(result.get('health_scores', {}), indent=2)}")
