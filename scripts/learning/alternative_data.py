"""
AlphaAbsolute — Alternative Data Intelligence Layer
Free/public signals that give edge BEFORE price reflects them.

Sources (all free, zero tokens):
  1. Google Trends (pytrends)  — narrative acceleration by theme
  2. Short Interest (yfinance) — squeeze setup detection
  3. Apple App Store RSS       — consumer adoption proxy
  4. FRED Copper / BDI proxy  — global trade recovery signal
  5. EIA Electricity           — data center power demand proxy

NRGC Integration:
  Google Trends spike  → narrative_acceleration → +4 EMLS boost (Phase 2→3)
  Squeeze setup        → squeeze_forming       → +3 EMLS boost
  Freight rising       → global_trade_recovery → +2 macro boost
  Trends peaking       → late_cycle_warning    → Phase 5/6 flag (−3)

Runs weekly. Data cached monthly to avoid re-scraping.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import requests
import urllib3
urllib3.disable_warnings()

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR  = BASE_DIR / "data" / "agent_memory"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    print(f"  [AltData] {msg}")


def _session() -> requests.Session:
    s = requests.Session()
    s.verify = False
    s.headers.update({"User-Agent": "Mozilla/5.0 AlphaAbsolute"})
    return s


# ═══════════════════════════════════════════════════════════════════════════════
# 1. GOOGLE TRENDS — narrative acceleration per theme
# ═══════════════════════════════════════════════════════════════════════════════

THEME_KEYWORDS = {
    "AI-Related":         "agentic AI",
    "Memory/HBM":         "HBM memory",
    "Space":              "rocket launch",
    "Quantum":            "quantum computing",
    "Photonics":          "silicon photonics",
    "DefenseTech":        "defense AI",
    "Data Center":        "data center power",
    "Nuclear/SMR":        "small modular reactor",
    "NeoCloud":           "GPU cloud",
    "AI Infrastructure":  "AI infrastructure",
    "Drone/UAV":          "drone delivery",
    "Robotics":           "humanoid robot",
    "Connectivity":       "Starlink satellite",
}

# NRGC signal thresholds
TREND_ACCEL_HIGH   = 40    # % WoW → narrative inflection
TREND_ACCEL_MED    = 20    # % WoW → narrative building
TREND_PEAK_WARN    = -25   # % WoW with high baseline → narrative peaking
TREND_PEAK_LEVEL   = 55    # baseline must be above this to flag as peaking


def _get_trends_batch(keywords: list, timeframe: str = "today 3-m") -> dict:
    """Pull Google Trends for up to 5 keywords. Returns {kw: interest_list}."""
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=360, timeout=(10, 30),
                      retries=2, backoff_factor=0.5)
        pt.build_payload(keywords[:5], timeframe=timeframe, geo="US")
        df = pt.interest_over_time()
        if df.empty:
            return {}
        return {kw: df[kw].tolist() for kw in keywords if kw in df.columns}
    except ImportError:
        return {}          # pytrends not installed
    except Exception:
        return {}


def get_theme_trends() -> dict:
    """
    Google Trends for all 14 themes. Returns narrative signal per theme.
    Caches result — runs at most once per week.
    """
    cache_path = DATA_DIR / f"trends_cache_{datetime.utcnow().strftime('%Y-W%W')}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    results = {}
    keywords = list(THEME_KEYWORDS.values())
    themes   = list(THEME_KEYWORDS.keys())

    log(f"Fetching Google Trends for {len(themes)} themes...")

    # Process in batches of 5
    for i in range(0, len(keywords), 5):
        batch_kws    = keywords[i:i+5]
        batch_themes = themes[i:i+5]
        trend_data   = _get_trends_batch(batch_kws)

        for theme, kw in zip(batch_themes, batch_kws):
            if kw not in trend_data or not trend_data[kw]:
                results[theme] = {
                    "keyword": kw, "narrative_heat": 0,
                    "accel_pct": 0, "nrgc_signal": "no_data", "nrgc_boost": 0,
                }
                continue

            vals = [v for v in trend_data[kw] if v is not None]
            if len(vals) < 4:
                continue
            mid     = len(vals) // 2
            current = sum(vals[mid:]) / len(vals[mid:])
            prior   = sum(vals[:mid]) / max(mid, 1)
            accel   = ((current - prior) / max(prior, 1)) * 100

            # NRGC mapping
            if accel >= TREND_ACCEL_HIGH:
                nrgc_signal = "narrative_inflection"
                nrgc_boost  = 4
            elif accel >= TREND_ACCEL_MED:
                nrgc_signal = "narrative_accelerating"
                nrgc_boost  = 2
            elif accel <= TREND_PEAK_WARN and current >= TREND_PEAK_LEVEL:
                nrgc_signal = "narrative_peaking"
                nrgc_boost  = -3      # late-cycle warning
            else:
                nrgc_signal = "neutral"
                nrgc_boost  = 0

            results[theme] = {
                "keyword":        kw,
                "narrative_heat": round(current, 1),
                "accel_pct":      round(accel, 1),
                "nrgc_signal":    nrgc_signal,
                "nrgc_boost":     nrgc_boost,
                "trending":       accel >= TREND_ACCEL_MED,
                "peaking":        nrgc_signal == "narrative_peaking",
            }
            log(f"  {theme}: heat={current:.0f} accel={accel:+.0f}% → {nrgc_signal}")
        time.sleep(2.0)   # respect rate limit

    cache_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SHORT INTEREST SCREEN — squeeze setup detection
# ═══════════════════════════════════════════════════════════════════════════════

SHORT_HIGH_THRESHOLD   = 15.0   # % of float = high short interest
SQUEEZE_DTC_THRESHOLD  =  5.0   # days to cover = painful if forced to cover


def get_short_interest(ticker: str) -> dict:
    """Pull short interest via yfinance. Returns squeeze signal."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info

        short_pct    = (info.get("shortPercentOfFloat") or 0) * 100   # → %
        days_cover   = info.get("shortRatio") or 0
        shares_now   = info.get("sharesShort") or 0
        shares_prior = info.get("sharesShortPriorMonth") or 0
        short_decl   = (shares_now < shares_prior * 0.93) if shares_prior else False

        squeeze = (short_pct >= SHORT_HIGH_THRESHOLD and
                   days_cover >= SQUEEZE_DTC_THRESHOLD and
                   short_decl)
        high_short = short_pct >= SHORT_HIGH_THRESHOLD

        nrgc_boost = 3 if squeeze else (1 if high_short and short_decl else 0)

        return {
            "short_pct_float":  round(short_pct, 1),
            "days_to_cover":    round(days_cover, 1),
            "short_declining":  short_decl,
            "squeeze_setup":    squeeze,
            "high_short":       high_short,
            "nrgc_boost":       nrgc_boost,
        }
    except Exception:
        return {"short_pct_float": 0, "days_to_cover": 0,
                "squeeze_setup": False, "nrgc_boost": 0}


def screen_short_interest(tickers: list, max_tickers: int = 40) -> dict:
    """Screen list of tickers for squeeze setups."""
    results       = {}
    squeeze_list  = []

    for ticker in tickers[:max_tickers]:
        d = get_short_interest(ticker)
        results[ticker] = d
        if d.get("squeeze_setup"):
            squeeze_list.append({
                "ticker":        ticker,
                "short_pct":     d["short_pct_float"],
                "days_to_cover": d["days_to_cover"],
            })
        time.sleep(0.2)

    squeeze_list.sort(key=lambda x: x["days_to_cover"], reverse=True)
    if squeeze_list:
        log(f"Squeeze setups: {[s['ticker'] for s in squeeze_list[:5]]}")

    return {"per_ticker": results, "squeeze_candidates": squeeze_list}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. GLOBAL MACRO PROXY — copper + freight for cycle detection
# ═══════════════════════════════════════════════════════════════════════════════

def get_macro_proxy() -> dict:
    """
    Copper price from FRED as global growth proxy.
    Rising copper = global demand recovering = macro tailwind.
    """
    fred_key = os.environ.get("FRED_API_KEY", "")
    result   = {"signal": "unavailable", "nrgc_boost": 0}
    if not fred_key:
        return result

    try:
        s = _session()
        r = s.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id":  "PCOPPUSDM",   # Global copper price USD/metric ton
                "api_key":    fred_key,
                "limit":      6,
                "sort_order": "desc",
                "file_type":  "json",
            },
            timeout=15, verify=False,
        )
        obs = r.json().get("observations", [])
        if len(obs) >= 3:
            vals    = [float(o["value"]) for o in obs if o["value"] != "."]
            current = vals[0]
            prior_3m = vals[2]
            change  = (current - prior_3m) / prior_3m * 100

            signal = ("recovery"  if change > 8  else
                      "improving" if change > 3  else
                      "declining" if change < -5 else "neutral")
            boost  = 3 if change > 8 else 1 if change > 3 else 0

            result = {
                "copper_usd":      round(current, 0),
                "copper_chg_3m":   round(change, 1),
                "signal":          signal,
                "nrgc_boost":      boost,
                "implication":     (
                    "Global growth recovering — cyclical tailwind"
                    if change > 5 else
                    "Global growth slowing — headwind for cyclicals"
                    if change < -5 else "Neutral"
                ),
            }
            log(f"Copper: ${current:,.0f} ({change:+.1f}% 3M) → {signal}")
    except Exception as e:
        log(f"Macro proxy error: {e}")

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 4. APP STORE — consumer adoption proxy (theme-level)
# ═══════════════════════════════════════════════════════════════════════════════

APP_THEME_KEYWORDS = {
    "AI-Related": ["AI", "ChatGPT", "Claude", "Gemini", "Copilot", "Perplexity"],
    "Robotics":   ["robot", "automation", "Optimus"],
    "Connectivity": ["Starlink", "satellite", "SpaceX"],
    "Drone/UAV":  ["drone", "UAV", "delivery"],
}


def get_app_store_signals() -> dict:
    """Apple App Store top-100 free apps. Detects theme mainstream adoption."""
    try:
        s = _session()
        r = s.get(
            "https://itunes.apple.com/us/rss/topfreeapplications/limit=100/json",
            timeout=15
        )
        apps = r.json().get("feed", {}).get("entry", [])
        app_names = [(i + 1, a.get("im:name", {}).get("label", ""))
                     for i, a in enumerate(apps[:100])]

        results = {}
        for theme, kws in APP_THEME_KEYWORDS.items():
            matches = [(rank, name) for rank, name in app_names
                       if any(kw.lower() in name.lower() for kw in kws)]
            if matches:
                best_rank = matches[0][0]
                results[theme] = {
                    "top_apps":         [n for _, n in matches[:3]],
                    "best_rank":        best_rank,
                    "mainstream":       best_rank <= 20,
                    "nrgc_signal":      "consumer_mainstream" if best_rank <= 20 else "growing",
                    "nrgc_boost":       3 if best_rank <= 10 else 2 if best_rank <= 20 else 1,
                }
                log(f"  App store {theme}: rank #{best_rank} ({matches[0][1]})")

        return results
    except Exception as e:
        log(f"App store error: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_alternative_data(tickers: list = None) -> dict:
    """
    Weekly alternative data pull.
    Returns: {google_trends, short_interest, macro_proxy, app_store, nrgc_theme_boosts}
    """
    log("=== Alternative Data Intelligence Layer ===")
    today = datetime.utcnow().strftime("%Y-%m-%d")

    results = {
        "date":             today,
        "google_trends":    {},
        "short_interest":   {},
        "macro_proxy":      {},
        "app_store":        {},
        "nrgc_theme_boosts": {},    # {theme: net_boost_points}
        "nrgc_ticker_boosts": {},   # {ticker: boost_points}
    }

    # 1. Google Trends
    try:
        results["google_trends"] = get_theme_trends()
        for theme, d in results["google_trends"].items():
            boost = d.get("nrgc_boost", 0)
            if boost != 0:
                results["nrgc_theme_boosts"][theme] = (
                    results["nrgc_theme_boosts"].get(theme, 0) + boost
                )
    except Exception as e:
        log(f"Trends failed: {e}")

    # 2. Short interest
    if tickers:
        try:
            results["short_interest"] = screen_short_interest(tickers)
            for ticker, d in results["short_interest"].get("per_ticker", {}).items():
                boost = d.get("nrgc_boost", 0)
                if boost:
                    results["nrgc_ticker_boosts"][ticker] = (
                        results["nrgc_ticker_boosts"].get(ticker, 0) + boost
                    )
        except Exception as e:
            log(f"Short interest failed: {e}")

    # 3. Macro proxy (copper)
    try:
        results["macro_proxy"] = get_macro_proxy()
    except Exception as e:
        log(f"Macro proxy failed: {e}")

    # 4. App store
    try:
        results["app_store"] = get_app_store_signals()
        for theme, d in results["app_store"].items():
            boost = d.get("nrgc_boost", 0)
            if boost:
                results["nrgc_theme_boosts"][theme] = (
                    results["nrgc_theme_boosts"].get(theme, 0) + boost
                )
    except Exception as e:
        log(f"App store failed: {e}")

    # Save
    out = DATA_DIR / f"alternative_data_{today[:7]}.json"
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    n_trending  = sum(1 for d in results["google_trends"].values() if d.get("trending"))
    n_squeeze   = len(results["short_interest"].get("squeeze_candidates", []))
    macro_sig   = results["macro_proxy"].get("signal", "?")
    log(f"Done: {n_trending} themes trending | {n_squeeze} squeeze setups | Macro: {macro_sig}")

    return results


def get_altdata_nrgc_enrichment(alt_result: dict, ticker: str, theme: str) -> dict:
    """
    Given alt_result and a ticker + theme, return NRGC enrichment for that asset.
    Called by weekly_runner when merging into NRGC assessments.
    """
    boost = 0
    signals = []

    # Theme-level boost from Google Trends
    theme_boost = alt_result.get("nrgc_theme_boosts", {}).get(theme, 0)
    if theme_boost > 0:
        boost += theme_boost
        signals.append(f"trends:{alt_result.get('google_trends',{}).get(theme,{}).get('nrgc_signal','?')}")
    elif theme_boost < 0:
        boost += theme_boost
        signals.append("trends:peaking_warning")

    # Ticker-level boost from short interest
    ticker_boost = alt_result.get("nrgc_ticker_boosts", {}).get(ticker, 0)
    if ticker_boost > 0:
        boost += ticker_boost
        signals.append("short:squeeze_setup")

    # Macro proxy
    macro_boost = alt_result.get("macro_proxy", {}).get("nrgc_boost", 0)
    if macro_boost > 0:
        boost += macro_boost
        signals.append("copper:recovery")

    # App store theme
    app_boost = alt_result.get("app_store", {}).get(theme, {}).get("nrgc_boost", 0)
    if app_boost > 0:
        boost += min(app_boost, 3)
        signals.append("app_store:consumer_adoption")

    return {
        "alt_data_boost":   boost,
        "alt_data_signals": signals,
        "narrative_heat":   alt_result.get("google_trends", {}).get(theme, {}).get("narrative_heat", 0),
    }


def get_altdata_telegram_line(result: dict) -> str:
    """One-liner for Telegram weekly report."""
    if not result:
        return ""
    trending = [t for t, d in result.get("google_trends", {}).items() if d.get("trending")]
    squeezes = result.get("short_interest", {}).get("squeeze_candidates", [])
    macro    = result.get("macro_proxy", {}).get("signal", "?")
    return (f"Alt Data: {len(trending)} themes trending "
            f"| {len(squeezes)} squeeze setups | Macro: {macro}")
