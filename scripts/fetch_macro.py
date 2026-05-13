"""
AlphaAbsolute — Macro Data Fetcher (Agent 1 support)
Pulls US macro (FRED) + Thai macro (World Bank, IMF, yfinance FX) → data/macro_YYMMDD.json

Sources (all FREE, no key required except FRED):
  - FRED API:       US rates, yields, DXY, CPI, PCE, unemployment, M2, VIX
  - World Bank API: Thailand GDP growth, CPI, exports, current account (annual, no key)
  - IMF DataMapper: US + Thailand real GDP growth forecasts, WEO (no key)
  - yfinance:       THB/USD spot rate, Gold price proxy

Usage:
  python scripts/fetch_macro.py

Requires: FRED_API_KEY in environment variables (scripts/set_env_keys.ps1)
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def _load_env():
    """Load API keys: .env file first, then Windows User env (registry), then machine env."""
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and val and key not in os.environ:
                    os.environ[key] = val
    # Fallback: read User-level env vars from Windows registry
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(reg, i)
                    if name not in os.environ:
                        os.environ[name] = value
                    i += 1
                except OSError:
                    break
    except Exception:
        pass


_load_env()

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

TODAY = datetime.now().strftime("%y%m%d")
OUTPUT_FILE = DATA_DIR / f"macro_{TODAY}.json"

FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"

FRED_SERIES = {
    "fed_funds_rate":       {"id": "FEDFUNDS",   "label": "Fed Funds Rate (%)",        "transform": "last"},
    "us_10y_yield":         {"id": "DGS10",       "label": "US 10Y Yield (%)",          "transform": "last"},
    "us_2y_yield":          {"id": "DGS2",        "label": "US 2Y Yield (%)",           "transform": "last"},
    "us_yield_spread_10_2": {"id": "T10Y2Y",      "label": "10Y-2Y Spread (%)",         "transform": "last"},
    "dxy":                  {"id": "DTWEXBGS",    "label": "USD Index (DXY proxy)",      "transform": "last"},
    "cpi_yoy":              {"id": "CPIAUCSL",    "label": "US CPI YoY (%)",            "transform": "yoy_pct"},
    "pce_yoy":              {"id": "PCEPI",       "label": "US PCE YoY (%)",            "transform": "yoy_pct"},
    "unemployment":         {"id": "UNRATE",      "label": "US Unemployment (%)",       "transform": "last"},
    "m2_yoy":               {"id": "M2SL",        "label": "M2 YoY (%)",               "transform": "yoy_pct"},
    "vix":                  {"id": "VIXCLS",      "label": "VIX",                       "transform": "last"},
}

# World Bank indicators for Thailand (TH) and US
WB_INDICATORS = {
    "th_gdp_growth":        {"country": "TH", "id": "NY.GDP.MKTP.KD.ZG",    "label": "Thailand Real GDP Growth YoY (%)"},
    "th_cpi_inflation":     {"country": "TH", "id": "FP.CPI.TOTL.ZG",       "label": "Thailand CPI Inflation YoY (%)"},
    "th_exports_growth":    {"country": "TH", "id": "NE.EXP.GNFS.ZG",       "label": "Thailand Exports Growth YoY (%)"},
    "th_current_account":   {"country": "TH", "id": "BN.CAB.XOKA.GD.ZS",   "label": "Thailand Current Account (% of GDP)"},
    "th_fdi_net":           {"country": "TH", "id": "BX.KLT.DINV.CD.WD",   "label": "Thailand Net FDI Inflows (USD bn)"},
    "us_gdp_growth":        {"country": "US", "id": "NY.GDP.MKTP.KD.ZG",    "label": "US Real GDP Growth YoY (%)"},
}

# IMF DataMapper indicators (WEO forecasts)
IMF_INDICATORS = {
    "th_gdp_forecast":  {"country": "THA", "id": "NGDP_RPCH",  "label": "Thailand Real GDP Growth Forecast (IMF WEO, %)"},
    "us_gdp_forecast":  {"country": "USA", "id": "NGDP_RPCH",  "label": "US Real GDP Growth Forecast (IMF WEO, %)"},
    "th_cpi_forecast":  {"country": "THA", "id": "PCPIPCH",    "label": "Thailand CPI Forecast (IMF WEO, %)"},
}


# ── FRED ──────────────────────────────────────────────────────────────────────
def fetch_fred_series(series_id: str, api_key: str, limit: int = 13) -> list:
    try:
        import urllib.request
        url = (f"{FRED_API_BASE}?series_id={series_id}"
               f"&api_key={api_key}&file_type=json&sort_order=desc&limit={limit}")
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return [o for o in data.get("observations", []) if o.get("value") != "."]
    except Exception:
        return []


def compute_fred_value(obs: list, transform: str):
    if not obs:
        return None, None
    try:
        latest_val = float(obs[0]["value"])
        latest_date = obs[0]["date"]
        if transform == "last":
            return latest_val, latest_date
        elif transform == "yoy_pct":
            if len(obs) >= 13:
                year_ago = float(obs[12]["value"])
                return round((latest_val - year_ago) / year_ago * 100, 2), latest_date
    except (ValueError, ZeroDivisionError):
        pass
    return None, None


# ── World Bank ─────────────────────────────────────────────────────────────────
def fetch_worldbank(country: str, indicator: str, n: int = 5) -> list:
    try:
        import urllib.request
        url = (f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"
               f"?format=json&mrv={n}&per_page=10")
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        if isinstance(data, list) and len(data) > 1:
            return [{"year": d["date"], "value": round(d["value"], 3)}
                    for d in data[1] if d.get("value") is not None]
    except Exception:
        pass
    return []


# ── IMF DataMapper ─────────────────────────────────────────────────────────────
def fetch_imf_forecast(country_code: str, indicator_id: str) -> dict:
    try:
        import urllib.request
        url = f"https://www.imf.org/external/datamapper/api/v1/{indicator_id}/{country_code}"
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        values = (data.get("values", {}).get(indicator_id, {}).get(country_code, {}))
        if not values:
            return {}
        current_year = str(datetime.now().year)
        next_year = str(datetime.now().year + 1)
        return {
            "current_year": current_year,
            "current_year_forecast": round(values.get(current_year, 0), 2) if values.get(current_year) else None,
            "next_year_forecast": round(values.get(next_year, 0), 2) if values.get(next_year) else None,
            "as_of": "IMF WEO latest",
        }
    except Exception:
        return {}


# ── yfinance SSL patch (corporate proxy with self-signed cert) ────────────────
_YF_SSL_PATCHED = False

def _patch_yfinance_ssl():
    """Patch yfinance to skip SSL verification — same fix as daily_report.py."""
    global _YF_SSL_PATCHED
    if _YF_SSL_PATCHED:
        return
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass
    try:
        from curl_cffi import requests as _cffi_req
        import yfinance.data as _yfd
        _orig_init = _yfd.YfData.__init__

        def _patched_init(self, session=None):
            _orig_init(self, session=_cffi_req.Session(impersonate="chrome", verify=False))

        _yfd.YfData.__init__ = _patched_init
        _YF_SSL_PATCHED = True
        print("  [OK] yfinance SSL patch applied (corporate proxy mode)")
    except Exception as _e:
        print(f"  [!] yfinance SSL patch skipped: {_e}")


# ── yfinance FX + Gold ────────────────────────────────────────────────────────
def fetch_fx_and_commodities() -> dict:
    result = {}
    try:
        import yfinance as yf
        _patch_yfinance_ssl()
        symbols = {
            "thb_usd":    "THBUSD=X",
            "usdthb":     "THB=X",       # USD/THB conventional quote (32.xx baht per dollar)
            "dxy_index":  "DX-Y.NYB",    # ICE DXY index — for 1D change
            "gold_usd":   "GC=F",
            "oil_brent":  "BZ=F",
            "set_index":  "^SET.BK",
            "sp500":      "^GSPC",
            "nasdaq":     "^IXIC",
            "dow":        "^DJI",
        }
        import pandas as _pd
        for key, sym in symbols.items():
            try:
                t = yf.Ticker(sym)
                info = t.info
                price = info.get("regularMarketPrice") or info.get("currentPrice")
                prev  = info.get("regularMarketPreviousClose")
                chg   = round((price - prev) / prev * 100, 2) if price and prev else None

                # WoW (5-trading-day) return via history
                ret_1w = None
                try:
                    hist = t.history(period="1mo", auto_adjust=True)
                    if not hist.empty and len(hist) >= 5:
                        hist.index = _pd.to_datetime(hist.index).tz_localize(None)
                        close = hist["Close"].dropna()
                        if len(close) >= 6:
                            ret_1w = round((float(close.iloc[-1]) - float(close.iloc[-6])) / float(close.iloc[-6]) * 100, 2)
                except Exception:
                    pass

                result[key] = {"symbol": sym, "price": price, "change_pct_1d": chg, "ret_1w": ret_1w}
            except Exception:
                result[key] = {"symbol": sym, "price": None, "change_pct_1d": None, "ret_1w": None}
    except ImportError:
        result["error"] = "yfinance not installed"
    return result


# ── MTD / YTD Performance ─────────────────────────────────────────────────────
def fetch_performance_context() -> dict:
    """Fetch MTD and YTD returns for key indices via yfinance history download."""
    try:
        import yfinance as yf
        _patch_yfinance_ssl()
        from datetime import date as date_cls

        today = date_cls.today()
        ytd_start = date_cls(today.year, 1, 1).strftime("%Y-%m-%d")
        # Fetch from start of year — covers both MTD and YTD
        symbols = {
            "sp500":   "^GSPC",
            "nasdaq":  "^IXIC",
            "sox":     "^SOX",
            "dow":     "^DJI",
            "set":     "^SET.BK",
            "gold":    "GC=F",
            "brent":   "BZ=F",
            "dxy":     "DX-Y.NYB",
            "usdthb":  "THB=X",
        }
        mtd_start = date_cls(today.year, today.month, 1).strftime("%Y-%m-%d")

        from datetime import timezone
        import pandas as _pd

        result = {}
        for key, sym in symbols.items():
            try:
                # Use Ticker.history() — same path as daily_report.py (SSL-patched)
                t = yf.Ticker(sym)
                hist = t.history(period="ytd", auto_adjust=True)
                if hist.empty:
                    # Fallback: try period="1y" and slice manually
                    hist = t.history(period="1y", auto_adjust=True)
                if hist.empty:
                    result[key] = {}
                    continue

                # Normalise index to date (drop tz info for string comparison)
                hist.index = _pd.to_datetime(hist.index).tz_localize(None)
                close = hist["Close"].dropna()
                if close.empty:
                    result[key] = {}
                    continue

                current = float(close.iloc[-1])

                ytd_slice = close[close.index >= ytd_start]
                ytd_base = float(ytd_slice.iloc[0]) if not ytd_slice.empty else float(close.iloc[0])
                ytd_pct = (current - ytd_base) / ytd_base * 100

                mtd_slice = close[close.index >= mtd_start]
                mtd_base = float(mtd_slice.iloc[0]) if not mtd_slice.empty else current
                mtd_pct = (current - mtd_base) / mtd_base * 100

                result[key] = {
                    "mtd_pct": round(mtd_pct, 2),
                    "ytd_pct": round(ytd_pct, 2),
                }
                print(f"  [perf] {key}: MTD {mtd_pct:+.2f}%  YTD {ytd_pct:+.2f}%")
            except Exception as _ex:
                print(f"  [!] perf {key}: {_ex}")
                result[key] = {}

        return result
    except ImportError:
        return {}


# ── Regime determination ──────────────────────────────────────────────────────
def determine_regime(fred: dict, fx: dict, wb: dict) -> str:
    yield_spread = fred.get("us_yield_spread_10_2", {}).get("value")
    vix = fred.get("vix", {}).get("value")
    cpi = fred.get("cpi_yoy", {}).get("value")

    signals = []
    if yield_spread is not None:
        signals.append("bull" if yield_spread > 0.5 else ("bear" if yield_spread < -0.5 else "cautious"))
    if vix is not None:
        signals.append("bull" if vix < 18 else ("bear" if vix > 28 else "cautious"))
    if cpi is not None:
        if cpi > 4.0:
            signals.append("cautious")

    # Gold rising + DXY falling = risk-off signal
    gold_chg = (fx.get("gold_usd") or {}).get("change_pct_1d")
    dxy_val = fred.get("dxy", {}).get("value")
    if gold_chg and gold_chg > 1.0:
        signals.append("cautious")

    if not signals:
        return "Pending"
    bear = signals.count("bear")
    bull = signals.count("bull")
    if bear >= 2:
        return "Bear"
    if bull >= 2:
        return "Bull"
    return "Cautious"


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    api_key = os.environ.get("FRED_API_KEY", "").strip()

    output = {
        "generated": datetime.now().isoformat(),
        "data_date": TODAY,
        "regime": "",
        "us_macro": {},
        "thai_macro": {},
        "imf_forecasts": {},
        "fx_commodities": {},
        "performance_context": {},
    }

    # ── 1. FRED (US macro) ────────────────────────────────────────────────────
    if not api_key:
        print("WARNING: FRED_API_KEY not set — skipping US macro series.")
        output["us_macro"]["error"] = "FRED_API_KEY not configured"
    else:
        print(f"\nFetching FRED ({len(FRED_SERIES)} series)...")
        # Yield series: pull 300 obs for WoW / MTD / YTD bps-change computation
        YIELD_KEYS = {"us_10y_yield", "us_2y_yield"}
        for key, meta in FRED_SERIES.items():
            limit = 300 if key in YIELD_KEYS else 13
            obs = fetch_fred_series(meta["id"], api_key, limit=limit)
            value, date = compute_fred_value(obs, meta["transform"])
            row = {"label": meta["label"], "value": value, "as_of": date}
            # Compute bps changes for yield series (1D / WoW / MTD / YTD)
            if key in YIELD_KEYS and obs:
                try:
                    from datetime import datetime as _dt
                    v0 = float(obs[0]["value"])
                    today_d = _dt.strptime(obs[0]["date"], "%Y-%m-%d")

                    # 1D change
                    if len(obs) >= 2:
                        row["change_bps"] = round((v0 - float(obs[1]["value"])) * 100, 1)
                    # WoW (~5 trading days)
                    if len(obs) >= 6:
                        row["change_bps_5d"] = round((v0 - float(obs[5]["value"])) * 100, 1)
                    # MTD: first trading day of current month
                    mtd_cut = today_d.replace(day=1)
                    for ob in reversed(obs):
                        ob_d = _dt.strptime(ob["date"], "%Y-%m-%d")
                        if ob_d >= mtd_cut:
                            row["change_bps_mtd"] = round((v0 - float(ob["value"])) * 100, 1)
                            break
                    # YTD: first trading day of current year
                    ytd_cut = today_d.replace(month=1, day=1)
                    for ob in reversed(obs):
                        ob_d = _dt.strptime(ob["date"], "%Y-%m-%d")
                        if ob_d >= ytd_cut:
                            row["change_bps_ytd"] = round((v0 - float(ob["value"])) * 100, 1)
                            break
                except Exception:
                    pass
            output["us_macro"][key] = row
            status = f"{value:.2f} (as of {date})" if value is not None else "unavailable"
            if key in YIELD_KEYS and row.get("change_bps") is not None:
                status += f"  |  1D {row['change_bps']:+.1f}bps  WoW {row.get('change_bps_5d',0):+.1f}bps  MTD {row.get('change_bps_mtd',0):+.1f}bps  YTD {row.get('change_bps_ytd',0):+.1f}bps"
            print(f"  {meta['label']:45s} {status}")

    # ── 2. World Bank (Thai + US historical) ──────────────────────────────────
    print("\nFetching World Bank (Thai macro, annual)...")
    for key, meta in WB_INDICATORS.items():
        rows = fetch_worldbank(meta["country"], meta["id"], n=4)
        output["thai_macro" if meta["country"] == "TH" else "us_macro"][key] = {
            "label": meta["label"],
            "history": rows,
            "latest": rows[0] if rows else None,
            "source": "World Bank API (free)",
        }
        latest = rows[0] if rows else None
        if latest:
            # FDI is absolute USD — convert to billions for display
            if "FDI" in meta["label"] or "USD bn" in meta["label"]:
                status = f"USD {latest['value']/1e9:.1f}bn ({latest['year']})"
            else:
                status = f"{latest['value']:.2f}% ({latest['year']})"
        else:
            status = "unavailable"
        print(f"  {meta['label']:55s} {status}")

    # ── 3. IMF WEO Forecasts ──────────────────────────────────────────────────
    print("\nFetching IMF DataMapper forecasts...")
    for key, meta in IMF_INDICATORS.items():
        forecast = fetch_imf_forecast(meta["country"], meta["id"])
        output["imf_forecasts"][key] = {"label": meta["label"], **forecast}
        if forecast:
            cy = forecast.get("current_year_forecast")
            ny = forecast.get("next_year_forecast")
            print(f"  {meta['label']:55s} {forecast.get('current_year')}: {cy}% | {int(forecast.get('current_year','0'))+1}: {ny}%")
        else:
            print(f"  {meta['label']:55s} unavailable")

    # ── 4. FX + Commodities (yfinance) ────────────────────────────────────────
    print("\nFetching FX + Commodities (yfinance)...")
    fx = fetch_fx_and_commodities()
    output["fx_commodities"] = fx
    for key, data in fx.items():
        if isinstance(data, dict) and data.get("price"):
            print(f"  {key:20s} {data['price']:.4f}  ({data.get('change_pct_1d', 0):+.2f}%)")

    # ── 4b. MTD / YTD Performance ─────────────────────────────────────────────
    print("\nFetching MTD/YTD performance (yfinance history)...")
    perf = fetch_performance_context()
    output["performance_context"] = perf
    for key, data in perf.items():
        if data:
            print(f"  {key:10s} MTD {data.get('mtd_pct', 0):+.2f}%  |  YTD {data.get('ytd_pct', 0):+.2f}%")

    # ── 5. BoT policy rate (manual placeholder) ───────────────────────────────
    existing_file = DATA_DIR / f"macro_{TODAY}.json"
    bot_rate = None
    if existing_file.exists():
        try:
            old = json.loads(existing_file.read_text(encoding="utf-8"))
            bot_rate = old.get("thai_macro", {}).get("bot_policy_rate", {}).get("value")
        except Exception:
            pass
    output["thai_macro"]["bot_policy_rate"] = {
        "label": "Bank of Thailand Policy Rate (%)",
        "value": bot_rate or 2.0,
        "as_of": "2025-10-16",
        "note": "BoT MPC rate — update manually after each MPC meeting. Source: bot.or.th",
        "source": "Manual (BoT MPC)",
    }
    print(f"\n  {'BoT Policy Rate':55s} {output['thai_macro']['bot_policy_rate']['value']}%")

    # ── 6. Regime ─────────────────────────────────────────────────────────────
    output["regime"] = determine_regime(output["us_macro"], output["fx_commodities"], output["thai_macro"])
    print(f"\nRegime assessment: {output['regime']}")

    # ── Save ──────────────────────────────────────────────────────────────────
    OUTPUT_FILE.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(f"Saved: {OUTPUT_FILE}")

    # Update portfolio.json regime field
    portfolio_path = DATA_DIR / "portfolio.json"
    if portfolio_path.exists():
        portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
        portfolio["regime"] = output["regime"]
        portfolio["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        portfolio_path.write_text(json.dumps(portfolio, indent=2), encoding="utf-8")
        print("Updated portfolio.json with regime.")

    return output


if __name__ == "__main__":
    main()
