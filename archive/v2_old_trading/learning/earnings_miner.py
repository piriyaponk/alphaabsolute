"""
AlphaAbsolute — Earnings Intelligence Miner
Mines SEC EDGAR 8-K filings for revenue acceleration data.

Revenue acceleration is the #1 NRGC signal. This module extracts:
  - Revenue QoQ change (current vs prior quarter)
  - Gross margin trend
  - Guidance tone (raised/maintained/cut)
  - Management language signals (Haiku extraction)

Free sources used:
  - SEC EDGAR full-text search (EFTS API) — for 8-K filings
  - Company IR pages — press releases
  - Yahoo Finance earnings history — for historical QoQ trend

Token cost: ~$0.002 per earnings release (Haiku extraction)
"""
import json, time, re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import requests
import urllib3
import pandas as pd

BASE_DIR     = Path(__file__).parent.parent.parent
EARNINGS_DIR = BASE_DIR / "data" / "earnings"
EARNINGS_DIR.mkdir(parents=True, exist_ok=True)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Shared session (same SSL bypass as portfolio_engine)
_SESSION = requests.Session()
_SESSION.verify = False
_SESSION.headers.update({
    "User-Agent": "AlphaAbsolute Research piriyaponk@gmail.com",
    "Accept": "application/json",
})

# ─── SEC EDGAR 8-K Fetcher ────────────────────────────────────────────────────

def fetch_recent_8k(ticker: str, days_back: int = 90) -> list[dict]:
    """
    Fetch recent 8-K filings for a ticker from SEC EDGAR.
    Returns list of {date, accession, items, url} dicts.
    Item 2.02 = Results of Operations (earnings release).
    """
    results = []
    try:
        # First get CIK from ticker
        cik = _get_cik(ticker)
        if not cik:
            return results

        url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
        r = _SESSION.get(url, timeout=15)
        data = r.json()

        recent = data.get("filings", {}).get("recent", {})
        forms   = recent.get("form", [])
        dates   = recent.get("filingDate", [])
        acc_nos = recent.get("accessionNumber", [])
        items   = recent.get("items", [])

        cutoff = datetime.now() - timedelta(days=days_back)

        for form, date, acc, item in zip(forms, dates, acc_nos, items):
            if form not in ("8-K", "8-K/A"):
                continue
            try:
                filing_date = datetime.strptime(date, "%Y-%m-%d")
                if filing_date < cutoff:
                    continue
            except:
                continue
            acc_clean = acc.replace("-", "")
            results.append({
                "ticker":  ticker,
                "form":    form,
                "date":    date,
                "items":   item,  # e.g. "2.02" or "2.02,9.01"
                "acc":     acc,
                "url":     f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=8-K&dateb=&owner=include&count=40",
                "filing_url": f"https://www.sec.gov/Archives/edgar/full-index/{date[:4]}/{int(date[5:7]):02d}/{date[8:10]}/{acc_clean}",
            })

        time.sleep(0.2)  # SEC rate limit: max 10 req/sec
    except Exception as e:
        print(f"  [8-K fetch error] {ticker}: {e}")
    return results


def fetch_earnings_release_text(acc_number: str, cik: str) -> str:
    """
    Fetch the earnings press release text from an 8-K filing.
    Returns first 3000 chars of the main document.
    """
    try:
        acc_clean = acc_number.replace("-", "")
        idx_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{acc_clean}-index.htm"
        r = _SESSION.get(idx_url, timeout=15)

        # Find .htm or .html press release link
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "lxml")
        links = soup.find_all("a", href=True)
        press_release_url = None
        for link in links:
            href = link.get("href", "")
            text = link.get_text(strip=True).lower()
            if any(x in text for x in ["press release", "earnings", "ex-99", "exhibit 99"]):
                press_release_url = "https://www.sec.gov" + href
                break
        if not press_release_url:
            # Try first .htm file
            for link in links:
                href = link.get("href", "")
                if href.endswith(".htm") and "index" not in href.lower():
                    press_release_url = "https://www.sec.gov" + href
                    break

        if press_release_url:
            r2 = _SESSION.get(press_release_url, timeout=15)
            soup2 = BeautifulSoup(r2.text, "lxml")
            text = soup2.get_text(separator=" ")[:4000]
            return text
    except Exception as e:
        print(f"  [8-K text error]: {e}")
    return ""


# ─── Yahoo Finance Earnings History ───────────────────────────────────────────

_YF_SESSION = requests.Session()
_YF_SESSION.verify = False
_YF_SESSION.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
})

def fetch_earnings_history(ticker: str) -> list[dict]:
    """
    Fetch quarterly earnings history from SEC EDGAR XBRL API (free, no auth).
    Returns list of {quarter, revenue, gross_profit, gross_margin}.
    Used to calculate QoQ revenue acceleration.
    """
    results = []
    try:
        from datetime import datetime as _dt
        # Step 1: Get CIK
        cik = _get_cik(ticker)
        if not cik:
            return []

        # Step 2: Fetch XBRL company facts
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json"
        r = _SESSION.get(url, timeout=20)
        if r.status_code != 200:
            return []
        facts = r.json()
        us_gaap = facts.get("facts", {}).get("us-gaap", {})

        # Step 3: Extract revenue — try multiple GAAP revenue field names
        # Priority: Revenues (broadest) > RevenueFromContractWithCustomer > SalesRevenueNet
        revenue_data = {}
        cutoff_2y = (_dt.now() - timedelta(days=730)).strftime("%Y-%m-%d")

        for rev_key in [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
            "RevenueNet",
        ]:
            if rev_key not in us_gaap:
                continue
            units = us_gaap[rev_key].get("units", {}).get("USD", [])
            candidate = {}
            for u in units:
                start = u.get("start", "")
                end   = u.get("end", "")
                form  = u.get("form", "")
                val   = u.get("val", 0)
                if not (start and end and val):
                    continue
                try:
                    d_start = _dt.strptime(start, "%Y-%m-%d")
                    d_end   = _dt.strptime(end,   "%Y-%m-%d")
                    days    = (d_end - d_start).days
                except:
                    continue
                # Single quarter = 60–105 days, must be recent (within 2 years)
                if 60 <= days <= 105 and form in ("10-Q", "10-K") and end >= cutoff_2y:
                    qkey = end
                    if qkey not in candidate or candidate[qkey]["val"] < val:
                        candidate[qkey] = {"end": end, "val": val, "form": form}

            # Only use this key if it has at least 2 recent quarters
            if len(candidate) >= 2:
                revenue_data = candidate
                break

        # Step 4: Gross profit
        gross_data = {}
        if "GrossProfit" in us_gaap:
            units = us_gaap["GrossProfit"].get("units", {}).get("USD", [])
            for u in units:
                start = u.get("start", "")
                end   = u.get("end", "")
                val   = u.get("val", 0)
                if not (start and end and val):
                    continue
                try:
                    d_start = _dt.strptime(start, "%Y-%m-%d")
                    d_end   = _dt.strptime(end,   "%Y-%m-%d")
                    days    = (d_end - d_start).days
                except:
                    continue
                if 60 <= days <= 105:
                    qkey = end
                    if qkey not in gross_data or gross_data[qkey]["val"] < val:
                        gross_data[qkey] = {"end": end, "val": val}

        # Step 5: Build quarterly list
        all_quarters = sorted(set(list(revenue_data.keys())))
        for q in all_quarters:
            rev = revenue_data.get(q, {}).get("val")
            gross = gross_data.get(q, {}).get("val")
            gross_m = (gross / rev) if (gross and rev and rev > 0) else None
            results.append({
                "quarter":      q,
                "revenue":      rev,
                "gross_profit": gross,
                "gross_margin": round(gross_m * 100, 1) if gross_m else None,
            })

        # Sort ascending
        results.sort(key=lambda x: x["quarter"])

        # Step 6: Calculate QoQ revenue changes
        for i in range(1, len(results)):
            prev_rev = results[i-1].get("revenue")
            curr_rev = results[i].get("revenue")
            if prev_rev and curr_rev and prev_rev > 0:
                results[i]["revenue_qoq_pct"] = round((curr_rev / prev_rev - 1) * 100, 1)
            else:
                results[i]["revenue_qoq_pct"] = None

        # Step 7: YoY (4 quarters ago)
        for i in range(4, len(results)):
            prev_rev = results[i-4].get("revenue")
            curr_rev = results[i].get("revenue")
            if prev_rev and curr_rev and prev_rev > 0:
                results[i]["revenue_yoy_pct"] = round((curr_rev / prev_rev - 1) * 100, 1)
            else:
                results[i]["revenue_yoy_pct"] = None

        time.sleep(0.3)  # SEC rate limit

    except Exception as e:
        print(f"  [SEC XBRL error] {ticker}: {e}")

    return results[-8:] if results else []  # last 8 quarters


def analyze_revenue_acceleration(history: list[dict]) -> dict:
    """
    Detect NRGC revenue acceleration signal from quarterly history.
    Returns:
      qoq_trend: "accelerating" | "decelerating" | "stable" | "negative"
      phase_signal: 2 | 3 | 4 | 5 | 6 | 7 (NRGC phase implied by revenue)
      acceleration_score: float 0-1 (how strong is the signal)
    """
    if len(history) < 3:
        return {"qoq_trend": "insufficient_data", "phase_signal": None}

    recent = [h for h in history if h.get("revenue_qoq_pct") is not None][-4:]
    if len(recent) < 2:
        return {"qoq_trend": "insufficient_data", "phase_signal": None}

    qoq_values = [h["revenue_qoq_pct"] for h in recent]
    latest_qoq = qoq_values[-1]
    prior_qoq  = qoq_values[-2]

    # Revenue trajectory analysis
    positive_quarters = sum(1 for q in qoq_values if q > 0)
    accelerating = latest_qoq > prior_qoq

    if latest_qoq < -10:
        qoq_trend = "strongly_negative"
        phase_signal = 1
    elif latest_qoq < 0:
        qoq_trend = "negative"
        phase_signal = 1 if prior_qoq < latest_qoq else 2  # if decelerating decline = Phase 2
    elif latest_qoq < 5:
        qoq_trend = "flat_to_slightly_positive"
        phase_signal = 2 if accelerating else 5
    elif latest_qoq < 15:
        qoq_trend = "positive"
        phase_signal = 3 if accelerating else 4
    elif latest_qoq < 30:
        qoq_trend = "strong"
        phase_signal = 3 if positive_quarters <= 2 else 4
    else:
        qoq_trend = "accelerating_strongly"
        phase_signal = 3 if positive_quarters <= 3 else 4

    # Acceleration score: how convincingly is revenue accelerating?
    if len(qoq_values) >= 3:
        deltas = [qoq_values[i] - qoq_values[i-1] for i in range(1, len(qoq_values))]
        avg_delta = sum(deltas) / len(deltas)
        acceleration_score = min(max(avg_delta / 20, 0), 1)  # normalize to 0-1
    else:
        acceleration_score = 0.5

    # Gross margin trend
    margins = [h.get("gross_margin") for h in recent if h.get("gross_margin")]
    margin_trend = "unknown"
    if len(margins) >= 2:
        if margins[-1] > margins[0]:
            margin_trend = "expanding"
        elif margins[-1] < margins[0] - 2:
            margin_trend = "compressing"
        else:
            margin_trend = "stable"

    return {
        "qoq_trend":          qoq_trend,
        "phase_signal":       phase_signal,
        "acceleration_score": round(acceleration_score, 2),
        "latest_qoq_pct":     latest_qoq,
        "prior_qoq_pct":      prior_qoq,
        "positive_quarters":  positive_quarters,
        "margin_trend":       margin_trend,
        "latest_margin":      margins[-1] if margins else None,
        "history_summary":    [{"q": h["quarter"], "qoq": h.get("revenue_qoq_pct")} for h in recent],
    }


# ─── LLM-Powered Earnings Intelligence ────────────────────────────────────────

EARNINGS_EXTRACTION_PROMPT = """You are extracting NRGC (Narrative Reflexive Growth Cycle) signals from an earnings press release.

Company: {ticker}
Industry/Theme: {theme}

Press release text (first 3000 chars):
{text}

Extract these specific signals for NRGC phase detection. Return JSON only:
{{
  "revenue_mention": "string — what management says about revenue growth",
  "guidance_action": "raised|maintained|cut|withdrawn|not_provided",
  "guidance_quote": "string — exact quote about guidance (max 20 words)",
  "gross_margin_trend": "expanding|stable|compressing|not_mentioned",
  "management_tone": "very_positive|positive|cautious|negative|very_negative",
  "supply_demand": "supply_constrained|balanced|oversupplied|not_mentioned",
  "demand_signals": ["list of specific positive demand signals mentioned"],
  "risk_signals": ["list of specific risk/negative signals mentioned"],
  "nrgc_phase_implied": 2 or 3 or 4 or 5 or 6 or 7,
  "phase_confidence": 0.0 to 1.0,
  "key_quote": "most important quote for NRGC phase (max 25 words)"
}}"""

def extract_earnings_intelligence(ticker: str, theme: str, press_release_text: str,
                                   client=None) -> Optional[dict]:
    """
    Use Haiku to extract NRGC signals from earnings press release.
    Returns structured intelligence dict.
    """
    if not client or not press_release_text:
        return None
    try:
        prompt = EARNINGS_EXTRACTION_PROMPT.format(
            ticker=ticker, theme=theme, text=press_release_text[:3000]
        )
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        # Clean JSON
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text)
    except Exception as e:
        print(f"  [Earnings LLM error] {ticker}: {e}")
        return None


# ─── CIK Lookup ───────────────────────────────────────────────────────────────

_CIK_CACHE = {}

def _get_cik(ticker: str) -> Optional[str]:
    """Get SEC CIK number for a ticker."""
    if ticker in _CIK_CACHE:
        return _CIK_CACHE[ticker]
    try:
        url = "https://www.sec.gov/cgi-bin/browse-edgar"
        params = {"company": "", "CIK": ticker, "type": "8-K",
                  "dateb": "", "owner": "include", "count": "1",
                  "search_text": "", "action": "getcompany", "output": "atom"}
        r = _SESSION.get(url, params=params, timeout=10)
        # Extract CIK from URL redirect or response
        import re as _re
        match = _re.search(r"CIK=(\d+)", r.url)
        if not match:
            match = _re.search(r"/(\d{10})/", r.url)
        if match:
            cik = match.group(1).lstrip("0")
            _CIK_CACHE[ticker] = cik
            return cik

        # Try EDGAR company search JSON
        url2 = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt=2024-01-01&forms=8-K"
        r2 = _SESSION.get(url2, timeout=10)
        # Just try ticker->CIK mapping endpoint
        url3 = "https://www.sec.gov/files/company_tickers.json"
        r3 = _SESSION.get(url3, timeout=15)
        data = r3.json()
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker.upper():
                cik = str(entry["cik_str"])
                _CIK_CACHE[ticker] = cik
                return cik
    except Exception as e:
        print(f"  [CIK lookup error] {ticker}: {e}")
    return None


# ─── Main Earnings Scan ───────────────────────────────────────────────────────

def run_earnings_scan(tickers: list[str], theme_map: dict,
                       client=None, days_back: int = 90) -> dict:
    """
    Full earnings intelligence scan for a list of tickers.
    Returns {ticker: {revenue_analysis, earnings_intelligence, filings}}
    """
    results = {}
    print(f"  [Earnings Miner] Scanning {len(tickers)} tickers...")

    for ticker in tickers:
        theme = theme_map.get(ticker, "Unknown")
        ticker_result = {"ticker": ticker, "theme": theme}

        # 1. Revenue acceleration from Yahoo Finance history
        history = fetch_earnings_history(ticker)
        if history:
            rev_analysis = analyze_revenue_acceleration(history)
            ticker_result["revenue_analysis"] = rev_analysis
            ticker_result["earnings_history"] = history
            print(f"    {ticker}: QoQ {rev_analysis.get('latest_qoq_pct','?')}% | "
                  f"Trend: {rev_analysis.get('qoq_trend','?')} | "
                  f"Phase signal: {rev_analysis.get('phase_signal','?')}")
        else:
            ticker_result["revenue_analysis"] = {"qoq_trend": "no_data"}
            print(f"    {ticker}: no earnings data")

        # 2. Recent 8-K filings
        filings = fetch_recent_8k(ticker, days_back)
        earnings_filings = [f for f in filings if "2.02" in (f.get("items") or "")]
        ticker_result["recent_8k_count"]     = len(filings)
        ticker_result["earnings_8k_count"]   = len(earnings_filings)
        ticker_result["latest_filing_date"]  = filings[0]["date"] if filings else None

        # 3. LLM extraction from most recent earnings release (if client provided)
        if client and earnings_filings:
            latest = earnings_filings[0]
            cik = _get_cik(ticker)
            if cik:
                text = fetch_earnings_release_text(latest["acc"], cik)
                if text:
                    intelligence = extract_earnings_intelligence(ticker, theme, text, client)
                    ticker_result["earnings_intelligence"] = intelligence
                    if intelligence:
                        print(f"    {ticker} LLM: tone={intelligence.get('management_tone')} | "
                              f"guidance={intelligence.get('guidance_action')} | "
                              f"phase_implied={intelligence.get('nrgc_phase_implied')}")

        results[ticker] = ticker_result
        time.sleep(0.3)  # Be polite to SEC

    # Save to disk
    out_path = BASE_DIR / "data" / "earnings" / f"{datetime.now().strftime('%y%m%d')}_earnings_scan.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"  [Earnings Miner] Saved: {out_path}")

    return results


if __name__ == "__main__":
    # Quick test
    tickers = ["MU", "NVDA", "RKLB"]
    theme_map = {"MU": "Memory/HBM", "NVDA": "AI Infrastructure", "RKLB": "Space"}
    results = run_earnings_scan(tickers, theme_map)
    for t, r in results.items():
        rev = r.get("revenue_analysis", {})
        print(f"\n{t}: {rev.get('qoq_trend','?')} | phase {rev.get('phase_signal','?')}")
