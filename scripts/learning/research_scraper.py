"""
AlphaAbsolute — Research Scraper
Fetches from Tier 1-3 sources. No LLM calls here — pure data collection.
Output: data/raw/YYMMDD_[source]_[type].json

Token cost: $0 (Python-only)
"""
import json, hashlib, os, time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import feedparser
import requests
from bs4 import BeautifulSoup
from requests_cache import CachedSession

from source_config import TIER1_SOURCES, TIER2_SOURCES, TIER3_SOURCES, ALL_TICKERS

# ─── Config ──────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent.parent.parent
RAW_DIR    = BASE_DIR / "data" / "raw"
SEEN_FILE  = BASE_DIR / "data" / "state" / "seen_hashes.json"
TODAY      = datetime.now().strftime("%y%m%d")

RAW_DIR.mkdir(parents=True, exist_ok=True)
SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)

# Cached HTTP session — don't hit same URL twice in 6 hours
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

session = CachedSession(
    cache_name=str(BASE_DIR / "data" / "state" / "http_cache"),
    expire_after=21600,  # 6 hours
)
session.verify = False  # bypass corporate proxy SSL
session.headers.update({
    "User-Agent": "AlphaAbsolute Research Bot/1.0 (investment research; piriyaponk@gmail.com)"
})

# ─── Dedup ────────────────────────────────────────────────────────────────────

def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()

def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen)))

def content_hash(text: str) -> str:
    return hashlib.md5(text[:500].encode()).hexdigest()

# ─── Scrapers ─────────────────────────────────────────────────────────────────

def scrape_rss(source: dict, seen: set) -> list[dict]:
    """Fetch and parse RSS feed. Returns new items only."""
    items = []
    try:
        feed = feedparser.parse(source["url"])
        for entry in feed.entries[:20]:  # max 20 per run
            text = entry.get("summary", "") + entry.get("title", "")
            h = content_hash(text)
            if h in seen:
                continue
            # Pre-filter: must mention a watchlist ticker OR theme keyword
            if not _is_relevant(entry.get("title","") + " " + entry.get("summary","")):
                seen.add(h)  # mark as seen anyway
                continue
            items.append({
                "source":    source["id"],
                "source_name": source["name"],
                "title":     entry.get("title", ""),
                "url":       entry.get("link", ""),
                "published": entry.get("published", ""),
                "text":      entry.get("summary", "")[:2000],  # cap at 2000 chars
                "themes":    source.get("themes", []),
                "hash":      h,
                "fetched":   datetime.now().isoformat(),
            })
            seen.add(h)
    except Exception as e:
        print(f"  [RSS error] {source['id']}: {e}")
    return items


def scrape_openinsider(seen: set) -> list[dict]:
    """Scrape OpenInsider for cluster insider buys (>$1M, multiple insiders)."""
    items = []
    try:
        url = ("http://openinsider.com/screener?s=&o=&pl=&ph=&ll=&lh=&fd=7&fdr=&td=0"
               "&tdr=&fdlyl=&fdlyh=&daysago=&xp=1&xs=1&vl=1000000&vh=&ocl=&och="
               "&sic1=-1&sicl=100&sich=9999&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh="
               "&v2l=&v2h=&oc2l=&oc2h=&sortcol=0&cnt=50&Action=1")
        r = session.get(url, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
        table = soup.find("table", {"class": "tinytable"})
        if not table:
            return items
        rows = table.find_all("tr")[1:]  # skip header
        ticker_counts = {}
        for row in rows:
            cols = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cols) < 13:
                continue
            ticker = cols[3]
            value_str = cols[12].replace("$","").replace(",","").replace("+","")
            try:
                value = float(value_str) if value_str else 0
            except:
                value = 0
            if ticker not in ticker_counts:
                ticker_counts[ticker] = {"count": 0, "total_value": 0, "rows": []}
            ticker_counts[ticker]["count"] += 1
            ticker_counts[ticker]["total_value"] += value
            ticker_counts[ticker]["rows"].append(cols)

        # Only keep CLUSTER buys (2+ insiders) or single >$5M
        for ticker, data in ticker_counts.items():
            if data["count"] >= 2 or data["total_value"] >= 5_000_000:
                text = f"Insider cluster buy: {ticker} | {data['count']} insiders | ${data['total_value']:,.0f} total"
                h = content_hash(text)
                if h in seen:
                    continue
                items.append({
                    "source": "openinsider",
                    "source_name": "OpenInsider",
                    "title": f"CLUSTER BUY: {ticker} — {data['count']} insiders, ${data['total_value']/1e6:.1f}M",
                    "ticker": ticker,
                    "text": text,
                    "insider_count": data["count"],
                    "total_value": data["total_value"],
                    "themes": ["Insider", "Smart Money"],
                    "signal_type": "insider_cluster",
                    "urgency": "this_week",
                    "hash": h,
                    "fetched": datetime.now().isoformat(),
                })
                seen.add(h)
    except Exception as e:
        print(f"  [OpenInsider error]: {e}")
    return items


def scrape_capitoltrades(seen: set) -> list[dict]:
    """Scrape Congress trades from CapitolTrades."""
    items = []
    try:
        url = "https://www.capitoltrades.com/trades?pageSize=50&page=1"
        r = session.get(url, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
        # Find trade rows
        rows = soup.find_all("div", {"class": lambda c: c and "trade-row" in c})
        if not rows:
            # Try table format
            rows = soup.find_all("tr", {"class": lambda c: c and "trade" in str(c).lower()})

        for row in rows[:30]:
            text = row.get_text(strip=True)
            h = content_hash(text)
            if h in seen or not _is_relevant(text):
                seen.add(h)
                continue
            items.append({
                "source": "capitoltrades",
                "source_name": "Capitol Trades",
                "title": f"Congress Trade: {text[:100]}",
                "text": text[:500],
                "themes": ["Policy", "Smart Money"],
                "signal_type": "congress_trade",
                "hash": h,
                "fetched": datetime.now().isoformat(),
            })
            seen.add(h)
    except Exception as e:
        print(f"  [CapitolTrades error]: {e}")
    return items


def scrape_dataroma(seen: set) -> list[dict]:
    """Scrape Dataroma for super investor portfolio changes."""
    items = []
    try:
        url = "https://www.dataroma.com/m/feeds/activity.php"
        r = session.get(url, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
        entries = soup.find_all("item")
        for entry in entries[:20]:
            title = entry.find("title")
            desc  = entry.find("description")
            if not title:
                continue
            text = (title.get_text() + " " + (desc.get_text() if desc else ""))
            h = content_hash(text)
            if h in seen:
                continue
            items.append({
                "source": "dataroma",
                "source_name": "Dataroma Super Investors",
                "title": title.get_text(strip=True),
                "text": text[:800],
                "themes": ["Institutional", "Smart Money"],
                "signal_type": "super_investor_move",
                "urgency": "this_week",
                "hash": h,
                "fetched": datetime.now().isoformat(),
            })
            seen.add(h)
    except Exception as e:
        print(f"  [Dataroma error]: {e}")
    return items


def fetch_sec_13f_recent(seen: set) -> list[dict]:
    """Fetch latest 13F filings from SEC EDGAR for tracked super investors."""
    items = []
    try:
        from source_config import SUPER_INVESTORS
        for inv_id, inv in SUPER_INVESTORS.items():
            url = f"https://data.sec.gov/submissions/CIK{inv['cik'].zfill(10)}.json"
            r = session.get(url, timeout=15,
                           headers={"User-Agent": "AlphaAbsolute piriyaponk@gmail.com"})
            if r.status_code != 200:
                continue
            data = r.json()
            recent = data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            acc_nums = recent.get("accessionNumber", [])

            for form, date, acc in zip(forms, dates, acc_nums):
                if form != "13F-HR":
                    continue
                # Only filings from last 90 days
                try:
                    filing_date = datetime.strptime(date, "%Y-%m-%d")
                    if (datetime.now() - filing_date).days > 90:
                        continue
                except:
                    continue
                h = content_hash(f"13F_{inv_id}_{date}")
                if h in seen:
                    continue
                items.append({
                    "source": "sec_13f",
                    "source_name": f"SEC 13F — {inv['name']}",
                    "title": f"13F Filing: {inv['name']} ({date})",
                    "text": f"{inv['name']} filed 13F on {date}. CIK: {inv['cik']}. Acc: {acc}",
                    "investor": inv["name"],
                    "investor_style": inv["style"],
                    "cik": inv["cik"],
                    "acc_number": acc,
                    "filing_date": date,
                    "themes": ["Institutional", "Smart Money"],
                    "signal_type": "13f_filing",
                    "urgency": "this_week",
                    "hash": h,
                    "fetched": datetime.now().isoformat(),
                })
                seen.add(h)
            time.sleep(0.2)  # SEC rate limit: 10 req/sec
    except Exception as e:
        print(f"  [SEC 13F error]: {e}")
    return items


# ─── Pre-filter (no LLM) ──────────────────────────────────────────────────────

RELEVANT_KEYWORDS = set([
    # Tickers
    *ALL_TICKERS,
    # Themes
    "AI", "artificial intelligence", "machine learning", "GPU", "NVIDIA",
    "memory", "HBM", "DRAM", "NAND", "semiconductor", "chip",
    "photonics", "optical", "laser", "silicon photonics",
    "nuclear", "SMR", "small modular reactor",
    "space", "satellite", "rocket", "launch vehicle",
    "quantum", "qubit",
    "data center", "hyperscaler", "cloud",
    "defense", "drone", "UAV", "autonomous",
    "robotics", "automation",
    # Signals
    "beat", "miss", "accelerat", "guidance", "raised", "record",
    "breakout", "52-week high", "all-time high", "momentum",
    "insider", "cluster", "congress", "senate", "bought",
    "upgrade", "overweight", "price target",
    # Macro
    "Fed", "FOMC", "rate", "inflation", "CPI", "recession", "yield curve",
    "dollar", "DXY", "oil", "energy", "supply chain",
])

def _is_relevant(text: str) -> bool:
    text_lower = text.lower()
    hits = sum(1 for kw in RELEVANT_KEYWORDS if kw.lower() in text_lower)
    return hits >= 2


# ─── Main Scraper ─────────────────────────────────────────────────────────────

def run_scrapers(mode: str = "daily") -> dict:
    """
    mode: 'daily' (fast sources) | 'weekly' (all sources) | 'quarterly' (13F)
    Returns dict of {source_id: [items]}
    """
    seen = load_seen()
    all_items = {}
    total = 0

    print(f"\n[Scraper] Mode: {mode} | {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # Daily sources
    if mode in ("daily", "weekly"):
        print("  Fetching daily sources...")
        items = scrape_openinsider(seen)
        if items: all_items["openinsider"] = items; total += len(items)
        print(f"    OpenInsider: {len(items)} new items")

        items = scrape_capitoltrades(seen)
        if items: all_items["capitoltrades"] = items; total += len(items)
        print(f"    CapitolTrades: {len(items)} new items")

        # Daily RSS (news + IBD)
        for src in TIER1_SOURCES + TIER2_SOURCES:
            if src["type"] == "rss" and src["freq"] == "daily":
                items = scrape_rss(src, seen)
                if items: all_items[src["id"]] = items; total += len(items)
                print(f"    {src['name']}: {len(items)} new items")

    # Weekly sources (run on Sunday)
    if mode == "weekly":
        print("  Fetching weekly sources...")
        for src in TIER1_SOURCES + TIER2_SOURCES:
            if src["type"] == "rss" and src["freq"] == "weekly":
                items = scrape_rss(src, seen)
                if items: all_items[src["id"]] = items; total += len(items)
                print(f"    {src['name']}: {len(items)} new items")

        items = scrape_dataroma(seen)
        if items: all_items["dataroma"] = items; total += len(items)
        print(f"    Dataroma: {len(items)} new items")

    # Quarterly sources
    if mode == "quarterly":
        print("  Fetching quarterly sources (13F)...")
        items = fetch_sec_13f_recent(seen)
        if items: all_items["sec_13f"] = items; total += len(items)
        print(f"    SEC 13F: {len(items)} new filings")

    # Save raw output
    if total > 0:
        out_path = RAW_DIR / f"{TODAY}_{mode}_raw.json"
        out_path.write_text(json.dumps(all_items, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Saved: {out_path} ({total} items total)")
    else:
        print("  No new items found (all already seen or filtered)")

    save_seen(seen)
    return all_items


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    results = run_scrapers(mode)
    print(f"\nDone. {sum(len(v) for v in results.values())} total new items.")
