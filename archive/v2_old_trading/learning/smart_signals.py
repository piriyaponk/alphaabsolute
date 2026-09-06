"""
AlphaAbsolute — Smart Signals (Zero/Minimal Token Intelligence Layer)
Extracts maximum intelligence from structured free data sources BEFORE
any LLM call. Enriches NRGC assessments without spending a single token.

Architecture:
  Numbers → Rules → Signals   (zero tokens)
  Signals → Pre-filter         (zero tokens)
  Pre-filtered signals → LLM  (tokens only on high-value targets)

Sources:
  1. FRED API     — yield curve, HY credit spread, ISM, LEI (macro regime)
  2. SEC Form 4   — insider cluster buys from EDGAR (smart money signal)
  3. EDGAR XBRL   — exact revenue/EPS/margin from SEC filings (zero scrape)
  4. Yahoo OHLCV  — volume anomaly + 52W breakout (from existing data)
  5. SEC 8-K RSS  — earnings/guidance alerts filtered by ticker

All free. All zero-token. All cloud-runnable.
"""
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET

import requests
import urllib3
urllib3.disable_warnings()

BASE_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = BASE_DIR / "data" / "smart_signals"
XBRL_CACHE = CACHE_DIR / "xbrl_cache.json"
CIK_CACHE  = CACHE_DIR / "cik_map.json"

SESSION = requests.Session()
SESSION.verify = False
SESSION.headers.update({
    "User-Agent": "AlphaAbsolute research@alphaabsolute.ai",  # EDGAR requires User-Agent
    "Accept": "application/json",
})


def _get(url: str, params: dict = None, timeout: int = 15) -> Optional[dict]:
    try:
        r = SESSION.get(url, params=params, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _save_cache(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _load_cache(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. FRED MACRO REGIME — Zero Tokens
#    Expanded beyond current fetch_macro.py to include regime-critical series
# ═══════════════════════════════════════════════════════════════════════════════

FRED_SERIES = {
    # Yield curve (most reliable recession predictor)
    "T10Y2Y":       ("Yield Curve 10Y-2Y", "spread"),
    "T10Y3M":       ("Yield Curve 10Y-3M", "spread"),

    # Credit stress (risk-on/off signal)
    "BAMLH0A0HYM2": ("HY Credit Spread", "spread"),   # >5% = stress
    "BAMLC0A0CM":   ("IG Credit Spread", "spread"),    # >1.5% = caution

    # Real economy (cycle position)
    "INDPRO":       ("Industrial Production", "level"),
    "RSAFS":        ("Retail Sales", "level"),

    # Inflation + Fed
    "FEDFUNDS":     ("Fed Funds Rate", "rate"),
    "T5YIE":        ("5Y Breakeven Inflation", "rate"),

    # Leading indicator
    "USALOLITONOSTSAM": ("US Leading Index OECD", "level"),

    # Liquidity
    "M2SL":         ("M2 Money Supply", "level"),
    "DTWEXBGS":     ("USD Trade-Weighted Index", "level"),

    # Commodities (risk appetite)
    "DCOILWTICO":   ("WTI Oil", "level"),
    "GOLDAMGBD228NLBM": ("Gold Price", "level"),
}

# Regime rules (zero token — pure logic)
def _compute_regime(signals: dict) -> dict:
    score = 0
    notes = []

    yc = signals.get("T10Y2Y", {}).get("value")
    if yc is not None:
        if yc > 0.5:
            score += 2; notes.append(f"Yield curve positive {yc:+.2f}% (expansion)")
        elif yc > -0.2:
            score += 1; notes.append(f"Yield curve flat {yc:+.2f}% (late cycle)")
        else:
            score -= 2; notes.append(f"Yield curve inverted {yc:+.2f}% (recession risk)")

    hy = signals.get("BAMLH0A0HYM2", {}).get("value")
    if hy is not None:
        if hy < 3.5:
            score += 2; notes.append(f"HY spread tight {hy:.1f}% (risk-on)")
        elif hy < 5.0:
            score += 0; notes.append(f"HY spread elevated {hy:.1f}% (neutral)")
        else:
            score -= 2; notes.append(f"HY spread wide {hy:.1f}% (stress)")

    ig = signals.get("BAMLC0A0CM", {}).get("value")
    if ig is not None:
        if ig < 1.2:
            score += 1; notes.append(f"IG spread tight {ig:.2f}%")
        elif ig > 2.0:
            score -= 1; notes.append(f"IG spread elevated {ig:.2f}%")

    usd = signals.get("DTWEXBGS", {}).get("change_pct_4w")
    if usd is not None:
        if usd < -2:
            score += 1; notes.append(f"USD weakening {usd:+.1f}% (EM/growth positive)")
        elif usd > 3:
            score -= 1; notes.append(f"USD strengthening {usd:+.1f}% (risk-off)")

    if score >= 3:
        regime = "risk-on"
    elif score <= -2:
        regime = "risk-off"
    else:
        regime = "neutral"

    return {
        "regime":       regime,
        "regime_score": score,
        "notes":        notes,
        "signals":      {k: v.get("value") for k, v in signals.items() if v.get("value")},
        "computed_at":  datetime.utcnow().strftime("%Y-%m-%d"),
    }


def fetch_fred_regime() -> dict:
    """
    Fetch key FRED series and compute macro regime via rules.
    Zero tokens. Returns regime + raw values.
    """
    fred_key = os.environ.get("FRED_API_KEY", "")
    if not fred_key:
        return {"regime": "neutral", "error": "no FRED key"}

    signals = {}
    for series_id, (name, stype) in FRED_SERIES.items():
        data = _get("https://api.stlouisfed.org/fred/series/observations",
                    params={"series_id": series_id, "api_key": fred_key,
                            "sort_order": "desc", "limit": 8,
                            "file_type": "json"})
        if not data:
            continue
        obs = [o for o in data.get("observations", []) if o.get("value") != "."]
        if not obs:
            continue

        current = float(obs[0]["value"])
        prev4w  = float(obs[min(4, len(obs)-1)]["value"]) if len(obs) > 1 else current
        signals[series_id] = {
            "name":          name,
            "value":         round(current, 4),
            "date":          obs[0]["date"],
            "change_pct_4w": round((current - prev4w) / abs(prev4w) * 100, 2) if prev4w else 0,
        }
        time.sleep(0.1)  # FRED rate limit

    regime_data = _compute_regime(signals)
    regime_data["raw"] = signals

    # Cache
    cache = {"date": datetime.utcnow().strftime("%Y-%m-%d"), **regime_data}
    _save_cache(CACHE_DIR / "fred_regime.json", cache)
    return regime_data


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SEC FORM 4 — Insider Cluster Buys (Zero Tokens)
#    Cluster = 2+ insiders buying same stock in 14 days > $500K each
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_insider_signals(watchlist_tickers: list) -> dict:
    """
    Scrape SEC EDGAR Form 4 filings for insider purchases.
    Filter: purchases (not sales), > $500K value, in watchlist.
    Zero tokens — purely structured XML/JSON parsing.
    """
    tickers_upper = set(t.upper() for t in watchlist_tickers)
    end   = datetime.utcnow()
    start = end - timedelta(days=14)

    results = {}

    # EDGAR full-text search for Form 4 (structured endpoint)
    url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q": "",
        "dateRange": "custom",
        "startdt": start.strftime("%Y-%m-%d"),
        "enddt":   end.strftime("%Y-%m-%d"),
        "forms":   "4",
        "_source": "period_of_report,entity_name,file_num,file_date",
        "hits.hits.total.value": 1,
    }

    # Simpler: use OpenInsider RSS (structured CSV, no LLM needed)
    # Filter for: purchases only, value > $500K, last 14 days
    oi_url = (
        "http://openinsider.com/screener?"
        "s=&o=&pl=&ph=&ll=&lh=&fd=14&fdr=&td=0&tdr=&"
        "fdlyl=&fdlyh=&daysago=&xp=1&xs=1"   # purchases only, exclude sales
        "&vl=500000&vh="                        # value > $500K
        "&ocl=&och=&sic1=-1&sicl=100&sich=9999"
        "&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh=&v2l=&v2h="
        "&oc2l=&oc2h=&sortcol=0&cnt=100&Action=1"
        "&csv=1"  # CSV format — no parsing needed
    )
    try:
        r = SESSION.get(oi_url, timeout=15)
        lines = r.text.splitlines()
        if len(lines) < 2:
            return {}

        # Parse CSV header
        header = [h.strip().strip('"') for h in lines[0].split(",")]
        ticker_col = next((i for i, h in enumerate(header)
                           if "ticker" in h.lower()), None)
        value_col  = next((i for i, h in enumerate(header)
                           if "value" in h.lower()), None)
        type_col   = next((i for i, h in enumerate(header)
                           if "type" in h.lower()), None)
        name_col   = next((i for i, h in enumerate(header)
                           if "title" in h.lower() or "insider" in h.lower()), None)

        for line in lines[1:]:
            cols = [c.strip().strip('"') for c in line.split(",")]
            if len(cols) < 4:
                continue
            try:
                ticker = cols[ticker_col].upper() if ticker_col else ""
                if ticker not in tickers_upper:
                    continue

                val_str = cols[value_col].replace("$", "").replace(",", "") if value_col else "0"
                value   = float(val_str or 0)
                t_type  = cols[type_col] if type_col else "P"

                if "P" not in t_type:  # Skip sales
                    continue

                if ticker not in results:
                    results[ticker] = {
                        "signal": "insider_buy",
                        "buys": [],
                        "total_value": 0,
                        "cluster": False,
                    }
                results[ticker]["buys"].append({
                    "value_usd": value,
                    "insider":   cols[name_col] if name_col else "?",
                })
                results[ticker]["total_value"] += value
            except Exception:
                continue

        # Flag cluster buys (2+ insiders)
        for ticker, data in results.items():
            if len(data["buys"]) >= 2:
                data["cluster"] = True
                data["signal"]  = "insider_cluster_buy"

    except Exception as e:
        print(f"  [Insider] fetch error: {e}")

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 3. EDGAR XBRL — Exact Financials (Zero Tokens)
#    Real revenue/EPS/margins from SEC filings — more accurate than Yahoo
# ═══════════════════════════════════════════════════════════════════════════════

def _get_cik(ticker: str) -> Optional[str]:
    """Get SEC CIK number for a ticker. Cached."""
    cache = _load_cache(CIK_CACHE)
    if ticker.upper() in cache:
        return cache[ticker.upper()]

    # Load full ticker→CIK map from SEC (free, no key)
    data = _get("https://www.sec.gov/files/company_tickers.json")
    if data:
        mapping = {v["ticker"].upper(): str(v["cik_str"]).zfill(10)
                   for v in data.values()}
        _save_cache(CIK_CACHE, mapping)
        return mapping.get(ticker.upper())
    return None


def fetch_xbrl_financials(tickers: list) -> dict:
    """
    Fetch actual revenue/EPS from SEC EDGAR XBRL API.
    More accurate than Yahoo Finance scraping. Zero tokens.
    Only fetches for tickers where we need to verify earnings data.
    """
    results = {}
    cache   = _load_cache(XBRL_CACHE)
    today   = datetime.utcnow().strftime("%Y-%m-%d")

    for ticker in tickers[:15]:  # limit to avoid rate limiting
        ticker = ticker.upper()

        # Use cache if < 7 days old
        cached = cache.get(ticker, {})
        if cached.get("date") == today:
            results[ticker] = cached
            continue

        cik = _get_cik(ticker)
        if not cik:
            continue

        url  = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        data = _get(url, timeout=20)
        if not data:
            time.sleep(0.5)
            continue

        facts = data.get("facts", {}).get("us-gaap", {})

        def _latest_quarters(concept_names: list, n: int = 6) -> list:
            """Extract last n quarterly values for a concept."""
            for concept in concept_names:
                cf = facts.get(concept, {})
                units = cf.get("units", {})
                for unit_key, entries in units.items():
                    # Quarterly filings only (form 10-Q or 10-K)
                    qtrs = [e for e in entries
                            if e.get("form") in ("10-Q", "10-K")
                            and e.get("fp", "").startswith("Q")
                            and e.get("val") is not None]
                    if qtrs:
                        qtrs.sort(key=lambda x: x.get("end", ""), reverse=True)
                        return [(q["end"], q["val"]) for q in qtrs[:n]]
            return []

        # Revenue
        rev_q = _latest_quarters([
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomer",
        ])
        # Net income
        ni_q  = _latest_quarters(["NetIncomeLoss", "ProfitLoss"])
        # Gross profit
        gp_q  = _latest_quarters(["GrossProfit"])

        if not rev_q:
            time.sleep(0.3)
            continue

        # Compute QoQ and YoY
        def _qoq(series: list) -> Optional[float]:
            if len(series) >= 2 and series[1][1] and series[1][1] != 0:
                return round((series[0][1] - series[1][1]) / abs(series[1][1]) * 100, 1)
            return None

        def _yoy(series: list) -> Optional[float]:
            if len(series) >= 5 and series[4][1] and series[4][1] != 0:
                return round((series[0][1] - series[4][1]) / abs(series[4][1]) * 100, 1)
            return None

        result = {
            "ticker":        ticker,
            "date":          today,
            "cik":           cik,
            "latest_quarter": rev_q[0][0] if rev_q else None,
            "revenue": {
                "latest":    rev_q[0][1] if rev_q else None,
                "qoq_pct":   _qoq(rev_q),
                "yoy_pct":   _yoy(rev_q),
                "history":   [(d, v) for d, v in rev_q],
                "accel":     (_qoq(rev_q) > _qoq(rev_q[1:])
                              if len(rev_q) >= 3 and _qoq(rev_q) and _qoq(rev_q[1:])
                              else None),
            },
            "net_income": {
                "latest":    ni_q[0][1] if ni_q else None,
                "qoq_pct":   _qoq(ni_q),
                "yoy_pct":   _yoy(ni_q),
            },
            "gross_margin": None,
        }

        # Gross margin
        if gp_q and rev_q and rev_q[0][1]:
            result["gross_margin"] = round(gp_q[0][1] / rev_q[0][1] * 100, 1)

        results[ticker]  = result
        cache[ticker]    = result
        time.sleep(0.4)  # EDGAR rate limit: 10 req/sec max

    _save_cache(XBRL_CACHE, cache)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 4. VOLUME ANOMALY DETECTOR — Zero Tokens (uses existing OHLCV data)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_volume_anomalies(watchlist_tickers: list) -> dict:
    """
    Detect institutional accumulation signals from volume patterns.
    Zero tokens — pure math on OHLCV data.

    Signals:
    - Accumulation: volume > 2.5× avg AND price up > 0.5%
    - Distribution: volume > 2.5× avg AND price down > 0.5%
    - Pocket pivot: volume > highest down-day vol in last 10 days (Minervini)
    - Breakout: new 52W high on 1.5× avg volume
    """
    results = {}
    end   = int(time.time())
    start = end - 120 * 86400  # 120 days

    for ticker in watchlist_tickers:
        try:
            r = SESSION.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                params={"interval": "1d", "period1": start, "period2": end},
                timeout=12,
            )
            res = r.json()["chart"]["result"][0]
            q   = res["indicators"]["quote"][0]
            ts  = res["timestamp"]

            closes  = [c for c in q.get("close", [])  if c]
            volumes = [v for v in q.get("volume", []) if v]
            highs   = [h for h in q.get("high", [])  if h]

            if len(closes) < 25:
                continue

            current     = closes[-1]
            prev_close  = closes[-2]
            today_vol   = volumes[-1]
            avg_vol20   = sum(volumes[-21:-1]) / 20
            price_chg   = (current - prev_close) / prev_close * 100

            # 52W high
            hi_52w = max(highs[-252:]) if len(highs) >= 252 else max(highs)

            signals = []
            strength = 0

            # Accumulation
            if today_vol > avg_vol20 * 2.5 and price_chg > 0.5:
                signals.append(f"ACCUMULATION vol={today_vol/avg_vol20:.1f}×avg price={price_chg:+.1f}%")
                strength += 2

            # Distribution
            elif today_vol > avg_vol20 * 2.5 and price_chg < -0.5:
                signals.append(f"DISTRIBUTION vol={today_vol/avg_vol20:.1f}×avg price={price_chg:+.1f}%")
                strength -= 2

            # Pocket pivot (Minervini): today vol > highest down-day vol in last 10d
            down_vols = [volumes[-i] for i in range(2, 12)
                         if i < len(volumes) and closes[-i] < closes[-(i+1)]]
            if down_vols and today_vol > max(down_vols) and price_chg > 0:
                signals.append(f"POCKET_PIVOT vol exceeds all down-day vols in 10d")
                strength += 2

            # 52W high breakout
            if current >= hi_52w * 0.999 and today_vol > avg_vol20 * 1.5:
                signals.append(f"52W_HIGH_BREAKOUT vol={today_vol/avg_vol20:.1f}×avg")
                strength += 3

            # Volume dry-up (base forming)
            avg_vol5 = sum(volumes[-6:-1]) / 5
            if avg_vol5 < avg_vol20 * 0.6 and abs(price_chg) < 1:
                signals.append(f"VOLUME_DRYUP avg5={avg_vol5/avg_vol20:.0%} of 20d avg")
                strength += 1

            if signals:
                results[ticker] = {
                    "signals":      signals,
                    "strength":     strength,
                    "vol_ratio":    round(today_vol / avg_vol20, 2),
                    "price_chg":    round(price_chg, 2),
                    "current":      round(current, 2),
                    "at_52w_high":  current >= hi_52w * 0.999,
                    "positive":     strength > 0,
                }
            time.sleep(0.2)
        except Exception:
            pass

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SEC 8-K ALERT FILTER — Minimal Token (only send earnings items to LLM)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_8k_alerts(watchlist_tickers: list) -> list:
    """
    Fetch recent 8-K filings from SEC EDGAR RSS.
    Rule-based filter to find earnings/guidance items.
    Only sends MATCHED items to LLM — not all filings.
    """
    tickers_upper = set(t.upper() for t in watchlist_tickers)
    alerts = []

    url = ("https://efts.sec.gov/LATEST/search-index?"
           "q=&forms=8-K&dateRange=custom&"
           f"startdt={(datetime.utcnow()-timedelta(days=7)).strftime('%Y-%m-%d')}&"
           f"enddt={datetime.utcnow().strftime('%Y-%m-%d')}")

    try:
        data = _get(url)
        if not data:
            return alerts

        for hit in data.get("hits", {}).get("hits", [])[:100]:
            src = hit.get("_source", {})
            company = src.get("entity_name", "").upper()
            # Try to match to watchlist ticker
            matched_ticker = next((t for t in tickers_upper if t in company), None)
            if not matched_ticker:
                continue

            # Get filing items — rule-based keyword filter (zero tokens)
            items = src.get("period_of_report", "")
            earnings_keywords = ["results", "earnings", "revenue", "guidance",
                                 "outlook", "quarter", "fiscal", "financial"]
            title = str(src.get("display_date_filed", "")).lower()

            alerts.append({
                "ticker":  matched_ticker,
                "company": src.get("entity_name", ""),
                "date":    src.get("period_of_report", ""),
                "type":    "8-K earnings" if any(k in title for k in earnings_keywords) else "8-K",
                "url":     f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={src.get('file_num','')}&type=8-K",
            })
    except Exception as e:
        print(f"  [8-K] fetch error: {e}")

    return alerts


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER SMART SIGNALS RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_smart_signals(watchlist_tickers: list,
                      nrgc_tickers: list = None) -> dict:
    """
    Run all zero-token signal sources and return structured intelligence.
    nrgc_tickers: tickers in Phase 2-4 (prioritize for XBRL fetch).
    Returns dict to be merged into NRGC assessments before LLM calls.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print("  [SmartSignals] Fetching zero-token intelligence...")

    # 1. FRED macro regime
    print("    FRED macro regime...")
    fred = {}
    try:
        fred = fetch_fred_regime()
        print(f"    Regime: {fred.get('regime','?')} | Score: {fred.get('regime_score','?')}")
    except Exception as e:
        print(f"    FRED error: {e}")

    # 2. Insider signals
    print("    SEC insider signals...")
    insiders = {}
    try:
        insiders = fetch_insider_signals(watchlist_tickers)
        clusters = [t for t, d in insiders.items() if d.get("cluster")]
        if clusters:
            print(f"    Insider clusters: {clusters}")
        else:
            print(f"    Insiders: {len(insiders)} buys found")
    except Exception as e:
        print(f"    Insider error: {e}")

    # 3. EDGAR XBRL (only for Phase 2-4 tickers — prioritize)
    print("    EDGAR XBRL financials...")
    xbrl = {}
    try:
        priority = list(nrgc_tickers or [])[:10] + [t for t in watchlist_tickers
                                                     if t not in (nrgc_tickers or [])]
        xbrl = fetch_xbrl_financials(priority[:15])
        print(f"    XBRL: {len(xbrl)} tickers | "
              f"accel: {[t for t,d in xbrl.items() if d.get('revenue',{}).get('accel')]}")
    except Exception as e:
        print(f"    XBRL error: {e}")

    # 4. Volume anomalies
    print("    Volume anomalies...")
    vol = {}
    try:
        vol = detect_volume_anomalies(watchlist_tickers[:25])
        pos = {t: d for t, d in vol.items() if d.get("positive")}
        print(f"    Volume: {len(pos)} positive signals | {list(pos.keys())[:5]}")
    except Exception as e:
        print(f"    Volume error: {e}")

    # 5. 8-K alerts
    print("    SEC 8-K alerts...")
    alerts_8k = []
    try:
        alerts_8k = fetch_8k_alerts(watchlist_tickers)
        print(f"    8-K alerts: {len(alerts_8k)}")
    except Exception as e:
        print(f"    8-K error: {e}")

    # ── Merge into enrichment dict for NRGC ──────────────────────────────────
    enrichment = {}
    all_tickers = set(watchlist_tickers)
    for ticker in all_tickers:
        enrich = {}

        # XBRL financials override Yahoo if available
        if ticker in xbrl:
            x = xbrl[ticker]
            rev = x.get("revenue", {})
            enrich["xbrl_qoq"]    = rev.get("qoq_pct")
            enrich["xbrl_yoy"]    = rev.get("yoy_pct")
            enrich["xbrl_accel"]  = rev.get("accel")
            enrich["xbrl_margin"] = x.get("gross_margin")
            enrich["xbrl_quarter"]= x.get("latest_quarter")

        # Insider signal
        if ticker in insiders:
            enrich["insider_signal"] = insiders[ticker]["signal"]
            enrich["insider_value"]  = insiders[ticker]["total_value"]
            enrich["insider_cluster"]= insiders[ticker]["cluster"]

        # Volume anomaly
        if ticker in vol:
            enrich["vol_signals"]  = vol[ticker]["signals"]
            enrich["vol_strength"] = vol[ticker]["strength"]
            enrich["at_52w_high"]  = vol[ticker]["at_52w_high"]

        if enrich:
            enrichment[ticker] = enrich

    result = {
        "fred_regime":    fred,
        "insider_signals": insiders,
        "xbrl_financials": xbrl,
        "volume_anomalies": vol,
        "alerts_8k":       alerts_8k,
        "enrichment":      enrichment,
        "summary": {
            "regime":       fred.get("regime", "neutral"),
            "regime_score": fred.get("regime_score", 0),
            "insider_clusters": [t for t, d in insiders.items() if d.get("cluster")],
            "volume_positive":  [t for t, d in vol.items() if d.get("positive")],
            "xbrl_accel":   [t for t, d in xbrl.items() if d.get("revenue", {}).get("accel")],
            "alerts_count": len(alerts_8k),
        }
    }

    # Save full output
    _save_cache(CACHE_DIR / f"smart_signals_{datetime.utcnow().strftime('%Y%m%d')}.json", result)
    return result
