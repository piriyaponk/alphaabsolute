"""
AlphaAbsolute — Unified Stock Data Fetcher
Pulls fundamentals, estimates, institutional + insider data for US and Thai stocks.

Sources:
  - yfinance:   price, fundamentals, estimates, upgrades, institutional, insider (free)
  - SEC EDGAR:  actual XBRL financials (revenue, EPS, margins) from 10-K/10-Q (free)
  - Finnhub:    EPS surprises (beat/miss), analyst rec counts, company news (free tier, US only)
  - FINNHUB_API_KEY env var required for Finnhub — set via scripts/set_env_keys.ps1

Usage:
  python scripts/fetch_stock_data.py NVDA
  python scripts/fetch_stock_data.py AOT.BK
  python scripts/fetch_stock_data.py NVDA MU PLTR AOT.BK KBANK.BK
"""

import json
import os
import sys
import requests
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
FINNHUB_BASE = "https://finnhub.io/api/v1"

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
DATA_DIR.mkdir(exist_ok=True)

TODAY = datetime.now().strftime("%y%m%d")
HEADERS = {"User-Agent": "AlphaAbsolute research@alphaabsolute.com"}

# ── CIK lookup for SEC EDGAR (expand as needed) ────────────────────────────────
CIK_MAP = {
    "NVDA": "0001045810", "AAPL": "0000320193", "MSFT": "0000789019",
    "AMZN": "0001018724", "GOOGL": "0001652044", "META": "0001326801",
    "TSLA": "0001318605", "AMD": "0000002488",  "MU":   "0000723125",
    "AVGO": "0001730168", "ANET": "0001313925", "PLTR": "0001321655",
    "AXON": "0001069183", "CACI": "0000016058", "LDOS": "0001336920",
    "RKLB": "0001819989", "IONQ": "0001819989", "NNE":  "0001792030",
    "VRT":  "0001091698", "DELL": "0000826083", "SMCI": "0000310764",
    "CRWV": "0001797033", "AMAT": "0000006951", "LITE": "0001041514",
    "COHR": "0000021510",
}

# ── CANSLIM score weights ──────────────────────────────────────────────────────
def canslim_score(info: dict, estimates) -> dict:
    scores = {}
    # C — Current quarterly EPS growth
    eps_q_growth = None
    if estimates is not None and not estimates.empty and "0q" in estimates.index:
        ago = estimates.loc["0q", "yearAgoEps"]
        avg = estimates.loc["0q", "avg"]
        if ago and ago != 0:
            eps_q_growth = (avg - ago) / abs(ago) * 100
    scores["C_current_eps"] = 2 if (eps_q_growth or 0) > 25 else (1 if (eps_q_growth or 0) > 0 else 0)

    # A — Annual EPS trend
    eps_ttm = info.get("trailingEps", 0) or 0
    scores["A_annual_eps"] = 2 if eps_ttm > 0 else 0

    # N — New highs / new product
    high52 = info.get("fiftyTwoWeekHigh", 1) or 1
    price = info.get("currentPrice") or info.get("regularMarketPrice", 0) or 0
    scores["N_near_high"] = 2 if price > high52 * 0.85 else (1 if price > high52 * 0.75 else 0)

    # S — Supply/Demand (float, short interest)
    short_ratio = info.get("shortRatio", 0) or 0
    scores["S_supply"] = 1 if short_ratio < 5 else 0

    # L — Leader (RS — approximated by 52W return)
    change_52w = info.get("52WeekChange", None)
    scores["L_leader"] = 2 if (change_52w or 0) > 0.3 else (1 if (change_52w or 0) > 0 else 0)

    # I — Institutional sponsorship
    inst_own = info.get("institutionalOwnershipPercentage") or info.get("heldPercentInstitutions", 0)
    scores["I_institutional"] = 2 if inst_own > 0.5 else (1 if inst_own > 0.2 else 0)

    # M — Market direction (from portfolio.json)
    portfolio_path = DATA_DIR / "portfolio.json"
    regime = "Cautious"
    if portfolio_path.exists():
        p = json.loads(portfolio_path.read_text())
        regime = p.get("regime", "Cautious")
    scores["M_market"] = 2 if regime == "Bull" else (1 if "Cautious" in regime else 0)

    total = sum(scores.values())
    scores["total"] = total
    scores["max"] = 14
    scores["pass"] = total >= 9
    scores["eps_q_growth_pct"] = round(eps_q_growth, 1) if eps_q_growth else None
    return scores


# ── Finnhub free-tier data (US stocks only) ───────────────────────────────────
def fetch_finnhub(ticker: str) -> dict:
    if not FINNHUB_KEY:
        return {"available": False, "reason": "FINNHUB_API_KEY not set"}
    h = {"X-Finnhub-Token": FINNHUB_KEY}

    def fget(endpoint, params):
        try:
            r = requests.get(f"{FINNHUB_BASE}/{endpoint}", params=params, headers=h, timeout=10)
            return r.json() if r.ok else {}
        except Exception:
            return {}

    # EPS surprise history (last 4 quarters)
    eps_raw = fget("stock/earnings", {"symbol": ticker, "limit": 4})
    eps_surprises = []
    if isinstance(eps_raw, list):
        for q in eps_raw:
            actual = q.get("actual")
            estimate = q.get("estimate")
            surprise_pct = round((actual - estimate) / abs(estimate) * 100, 1) if estimate else None
            eps_surprises.append({
                "period": q.get("period", ""),
                "actual": actual,
                "estimate": estimate,
                "surprise_pct": surprise_pct,
                "beat": (actual > estimate) if (actual is not None and estimate is not None) else None,
            })

    # Analyst recommendation counts (latest period)
    recs_raw = fget("stock/recommendation", {"symbol": ticker})
    rec_counts = {}
    if isinstance(recs_raw, list) and recs_raw:
        latest = recs_raw[0]
        rec_counts = {
            "period": latest.get("period", ""),
            "strong_buy": latest.get("strongBuy", 0),
            "buy": latest.get("buy", 0),
            "hold": latest.get("hold", 0),
            "sell": latest.get("sell", 0),
            "strong_sell": latest.get("strongSell", 0),
        }
        rec_counts["total_bullish"] = rec_counts["strong_buy"] + rec_counts["buy"]
        rec_counts["total_bearish"] = rec_counts["sell"] + rec_counts["strong_sell"]

    # Company news (last 7 days, top 5)
    today_str = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    news_raw = fget("company-news", {"symbol": ticker, "from": week_ago, "to": today_str})
    news = []
    if isinstance(news_raw, list):
        for item in news_raw[:5]:
            news.append({
                "headline": item.get("headline", ""),
                "source": item.get("source", ""),
                "url": item.get("url", ""),
                "date": datetime.fromtimestamp(item["datetime"]).strftime("%Y-%m-%d") if item.get("datetime") else "",
                "sentiment": item.get("sentiment", ""),
            })

    return {
        "available": True,
        "source": "Finnhub (free tier)",
        "eps_surprises": eps_surprises,
        "analyst_recs": rec_counts,
        "recent_news": news,
    }


# ── SEC EDGAR XBRL financials ──────────────────────────────────────────────────
def fetch_edgar_financials(ticker: str) -> dict:
    cik = CIK_MAP.get(ticker.upper())
    if not cik:
        return {"available": False, "reason": "CIK not in map — add to CIK_MAP"}
    try:
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if not r.ok:
            return {"available": False, "reason": f"HTTP {r.status_code}"}
        facts = r.json().get("facts", {}).get("us-gaap", {})

        def get_annual(concept: str, n=4):
            data = facts.get(concept, {})
            units = data.get("units", {}).get("USD", [])
            annual = [u for u in units if u.get("form") in ("10-K", "20-F") and u.get("val")]
            seen = {}
            for u in annual:
                seen[u["end"]] = u["val"]
            return dict(sorted(seen.items())[-n:])

        def get_quarterly(concept: str, n=6):
            data = facts.get(concept, {})
            units = data.get("units", {}).get("USD", [])
            qtrs = [u for u in units if u.get("form") == "10-Q" and u.get("val")]
            seen = {}
            for u in qtrs:
                key = f"{u['start']}_{u['end']}"
                seen[key] = {"start": u["start"], "end": u["end"], "val": u["val"]}
            return list(sorted(seen.values(), key=lambda x: x["end"]))[-n:]

        rev_concept = next((c for c in ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                                         "SalesRevenueNet"] if c in facts), None)
        eps_concept = next((c for c in ["EarningsPerShareDiluted", "EarningsPerShareBasic"] if c in facts), None)

        return {
            "available": True,
            "source": "SEC EDGAR (free, official)",
            "revenue_annual_usd": get_annual(rev_concept) if rev_concept else {},
            "eps_diluted_quarterly": [
                {"period": f"{q['start']} to {q['end']}", "eps": q["val"]}
                for q in get_quarterly(eps_concept)
            ] if eps_concept else [],
        }
    except Exception as e:
        return {"available": False, "reason": str(e)}


# ── Main fetch function ────────────────────────────────────────────────────────
def fetch_stock(ticker: str) -> dict:
    print(f"\n{'='*60}")
    print(f"Fetching: {ticker}")
    print(f"{'='*60}")

    is_thai = ticker.endswith(".BK")
    t = yf.Ticker(ticker)

    try:
        info = t.info
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}

    # Core price data
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    high52 = info.get("fiftyTwoWeekHigh")
    low52 = info.get("fiftyTwoWeekLow")
    pct_from_high = ((price - high52) / high52 * 100) if price and high52 else None
    pct_from_low = ((price - low52) / low52 * 100) if price and low52 else None

    # EPS estimates
    estimates = None
    try:
        estimates = t.earnings_estimate
    except Exception:
        pass

    # Upgrades / downgrades
    upgrades = []
    try:
        ud = t.upgrades_downgrades
        if ud is not None and not ud.empty:
            for idx, row in ud.head(5).iterrows():
                upgrades.append({
                    "date": str(idx.date()),
                    "firm": row.get("Firm", ""),
                    "to": row.get("ToGrade", ""),
                    "action": row.get("priceTargetAction", ""),
                    "target": row.get("currentPriceTarget"),
                })
    except Exception:
        pass

    # Institutional holders
    institutions = []
    try:
        inst = t.institutional_holders
        if inst is not None and not inst.empty:
            for _, row in inst.head(5).iterrows():
                institutions.append({
                    "holder": row.get("Holder", ""),
                    "pct_held": round(row.get("pctHeld", 0) * 100, 2),
                    "shares": int(row.get("Shares", 0)),
                    "pct_change": round(row.get("pctChange", 0) * 100, 2),
                })
    except Exception:
        pass

    # Insider transactions
    insiders = []
    try:
        ins = t.insider_transactions
        if ins is not None and not ins.empty:
            for _, row in ins.head(5).iterrows():
                insiders.append({
                    "insider": row.get("Insider", ""),
                    "action": row.get("Text", ""),
                    "shares": int(row.get("Shares", 0)),
                    "value": int(row.get("Value", 0)),
                })
    except Exception:
        pass

    # CANSLIM
    canslim = canslim_score(info, estimates)

    # EPS estimates formatted
    eps_fwd = {}
    if estimates is not None and not estimates.empty:
        for period in estimates.index:
            eps_fwd[period] = {
                "avg_eps": estimates.loc[period, "avg"],
                "year_ago": estimates.loc[period, "yearAgoEps"],
                "growth_pct": round(estimates.loc[period, "growth"] * 100, 1) if estimates.loc[period, "growth"] else None,
                "analysts": int(estimates.loc[period, "numberOfAnalysts"]),
            }

    # SEC EDGAR + Finnhub for US stocks
    edgar = {}
    finnhub = {}
    if not is_thai:
        edgar = fetch_edgar_financials(ticker.split(".")[0].upper())
        finnhub = fetch_finnhub(ticker.split(".")[0].upper())

    result = {
        "ticker": ticker,
        "fetched": datetime.now().isoformat(),
        "source": "yfinance + SEC EDGAR (free)",
        "market": "TH" if is_thai else "US",
        "name": info.get("longName", ticker),
        "sector": info.get("sector", ""),
        "industry": info.get("industry", ""),

        # Price
        "price": price,
        "currency": info.get("currency", "USD"),
        "market_cap_b": round(info.get("marketCap", 0) / 1e9, 1),
        "52w_high": high52,
        "52w_low": low52,
        "pct_from_52w_high": round(pct_from_high, 1) if pct_from_high else None,
        "pct_above_52w_low": round(pct_from_low, 1) if pct_from_low else None,
        "change_52w_pct": round((info.get("52WeekChange") or 0) * 100, 1),

        # Fundamentals
        "pe_trailing": info.get("trailingPE"),
        "pe_forward": info.get("forwardPE"),
        "ev_ebitda": info.get("enterpriseToEbitda"),
        "ev_revenue": info.get("enterpriseToRevenue"),
        "ps_ratio": info.get("priceToSalesTrailing12Months"),
        "pb_ratio": info.get("priceToBook"),
        "eps_ttm": info.get("trailingEps"),
        "eps_forward": info.get("forwardEps"),
        "revenue_growth_yoy": round((info.get("revenueGrowth") or 0) * 100, 1),
        "earnings_growth_yoy": round((info.get("earningsGrowth") or 0) * 100, 1),
        "gross_margin_pct": round((info.get("grossMargins") or 0) * 100, 1),
        "operating_margin_pct": round((info.get("operatingMargins") or 0) * 100, 1),
        "net_margin_pct": round((info.get("profitMargins") or 0) * 100, 1),
        "roe": round((info.get("returnOnEquity") or 0) * 100, 1),
        "roa": round((info.get("returnOnAssets") or 0) * 100, 1),
        "debt_to_equity": info.get("debtToEquity"),

        # Analyst
        "analyst_target_mean": info.get("targetMeanPrice"),
        "analyst_target_high": info.get("targetHighPrice"),
        "analyst_recommendation": info.get("recommendationKey"),
        "analyst_count": info.get("numberOfAnalystOpinions"),

        # Estimates (forward EPS)
        "eps_estimates": eps_fwd,
        "eps_q_growth_pct": canslim.get("eps_q_growth_pct"),

        # Smart money
        "upgrades_downgrades": upgrades,
        "institutional_top5": institutions,
        "insider_recent": insiders,

        # CANSLIM
        "canslim": canslim,

        # EDGAR
        "sec_edgar": edgar,

        # Finnhub (US only)
        "finnhub": finnhub,
    }

    # Print summary
    currency = result["currency"]
    print(f"  {result['name']} ({ticker})")
    print(f"  Price: {currency} {price} | MCap: {currency} {result['market_cap_b']}B")
    print(f"  P/E: {result['pe_trailing']} | Fwd P/E: {result['pe_forward']} | EV/EBITDA: {result['ev_ebitda']}")
    print(f"  Rev Growth YoY: {result['revenue_growth_yoy']}% | Gross Margin: {result['gross_margin_pct']}%")
    print(f"  52W: {result['pct_from_52w_high']}% from high | {result['pct_above_52w_low']}% from low")
    print(f"  Analyst: {result['analyst_recommendation']} | Target: {currency} {result['analyst_target_mean']}")
    print(f"  EPS Q growth: {result['eps_q_growth_pct']}%")
    print(f"  CANSLIM: {canslim['total']}/{canslim['max']} -- {'PASS' if canslim['pass'] else 'FAIL'}")
    if upgrades:
        print(f"  Latest upgrade: {upgrades[0]['firm']} {upgrades[0]['to']} ({upgrades[0]['date']})")
    if institutions:
        direction = "+" if institutions[0]['pct_change'] > 0 else "-"
        print(f"  Top holder: {institutions[0]['holder']} ({institutions[0]['pct_held']}%, {direction}{abs(institutions[0]['pct_change'])}%)")
    if edgar.get("available"):
        revs = edgar.get("revenue_annual_usd", {})
        if revs:
            print(f"  SEC Revenue: {list(revs.items())[-1]}")
    if finnhub.get("available"):
        surprises = finnhub.get("eps_surprises", [])
        if surprises and surprises[0].get("surprise_pct") is not None:
            s = surprises[0]
            beat_str = "BEAT" if s["beat"] else "MISS"
            print(f"  Finnhub EPS: {s['period']} {beat_str} {s['surprise_pct']:+.1f}% vs est")
        recs = finnhub.get("analyst_recs", {})
        if recs:
            print(f"  Finnhub Recs: Buy={recs.get('total_bullish',0)} Hold={recs.get('hold',0)} Sell={recs.get('total_bearish',0)}")

    return result


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["NVDA", "MU", "AOT.BK"]
    results = {}
    for ticker in tickers:
        results[ticker] = fetch_stock(ticker)

    outfile = DATA_DIR / f"stock_data_{TODAY}.json"
    outfile.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\n\nSaved: {outfile}")


if __name__ == "__main__":
    main()
