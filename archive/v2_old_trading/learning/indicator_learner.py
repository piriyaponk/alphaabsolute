"""
AlphaAbsolute — Indicator Learner (Lifetime Trading Knowledge System)

Discovers new indicators, strategies, and trading concepts from community sources.
Builds a persistent, ever-growing knowledge base that the system reads every week.

Philosophy:
  Community wisdom is crowd-sourced R&D. The best indicators float to the top
  by likes/stars/upvotes. We harvest that signal, extract the logic, and test
  whether it improves our edge in the paper portfolio.

Sources:
  1. TradingView Scripts  — community scripts sorted by likes (crowd wisdom)
  2. GitHub              — open-source trading strategies (code quality)
  3. Reddit r/algotrading — practitioner Q&A + backtesting (real-world)
  4. Reddit r/stocks / r/investing — sentiment + idea flow (broader market)
  5. QuantConnect Forum  — professional quant strategies (institutional quality)

Architecture:
  Scrape (zero token) → Extract Pine functions + keywords (zero token)
  → Deduplicate vs KB (zero token)
  → LLM batch classify 10 new items per call (minimal token)
  → Store in knowledge base (forever)
  → Track accuracy via paper trades (continuous)
  → Monthly: propose 3 framework additions (1 LLM call)

Knowledge Base: data/agent_memory/indicator_kb.json
Memory:         memory/indicator_learnings.md
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

BASE_DIR  = Path(__file__).resolve().parents[2]
KB_FILE   = BASE_DIR / "data" / "agent_memory" / "indicator_kb.json"
MEM_FILE  = BASE_DIR / "memory" / "indicator_learnings.md"
LOG_FILE  = BASE_DIR / "data" / "agent_memory" / "indicator_discovery_log.json"

SESSION = requests.Session()
SESSION.verify = False
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
})


# ══════════════════════════════════════════════════════════════════════════════
# ZERO-TOKEN: Pine Script function detector
# ══════════════════════════════════════════════════════════════════════════════

PINE_FUNCTIONS = {
    "ta.rsi":           ("RSI",             "momentum"),
    "ta.macd":          ("MACD",            "momentum"),
    "ta.bb":            ("Bollinger Bands", "volatility"),
    "ta.vwap":          ("VWAP",            "volume_price"),
    "ta.ema":           ("EMA",             "trend"),
    "ta.sma":           ("SMA",             "trend"),
    "ta.wma":           ("WMA",             "trend"),
    "ta.atr":           ("ATR",             "volatility"),
    "ta.stoch":         ("Stochastic",      "momentum"),
    "ta.cci":           ("CCI",             "momentum"),
    "ta.obv":           ("OBV",             "volume"),
    "ta.mom":           ("Momentum",        "momentum"),
    "ta.wpr":           ("Williams %R",     "momentum"),
    "ta.pivothigh":     ("Pivot High",      "structure"),
    "ta.pivotlow":      ("Pivot Low",       "structure"),
    "ta.crossover":     ("Crossover",       "signal"),
    "ta.crossunder":    ("Crossunder",      "signal"),
    "ta.highest":       ("Highest",         "range"),
    "ta.lowest":        ("Lowest",          "range"),
    "ta.percentrank":   ("Percent Rank",    "relative_strength"),
    "ta.linreg":        ("Linear Reg",      "trend"),
    "ta.correlation":   ("Correlation",     "statistical"),
    "ta.variance":      ("Variance",        "statistical"),
    "ta.stdev":         ("Std Dev",         "volatility"),
    "ta.cum":           ("Cumulative",      "volume"),
    "request.security": ("Multi-TF",        "multi_timeframe"),
    "strategy.entry":   ("Strategy Entry",  "strategy"),
    "strategy.exit":    ("Strategy Exit",   "strategy"),
}

KEYWORD_CATEGORIES = {
    "momentum":     ["momentum", "rsi", "macd", "stochastic", "oscillator", "divergence"],
    "trend":        ["trend", "moving average", "ema", "sma", "ribbon", "slope", "direction"],
    "volume":       ["volume", "vwap", "obv", "accumulation", "distribution", "flow"],
    "volatility":   ["volatility", "atr", "bollinger", "squeeze", "range", "breakout"],
    "structure":    ["support", "resistance", "pivot", "supply", "demand", "orderblock",
                     "fvg", "fair value gap", "liquidity", "sweep", "bos", "choch"],
    "pattern":      ["pattern", "cup", "handle", "vcp", "wedge", "flag", "pennant",
                     "base", "consolidation", "stage"],
    "relative_strength": ["relative strength", "rs", "leader", "outperform", "sector"],
    "multi_timeframe": ["multi-timeframe", "htf", "ltf", "1w", "weekly", "monthly"],
    "wyckoff":      ["wyckoff", "accumulation", "distribution", "spring", "sos", "lps"],
    "smc":          ["smc", "smart money", "order block", "inducement", "liquidity",
                     "imbalance", "breaker"],
    "canslim":      ["canslim", "can slim", "minervini", "weinstein", "stage 2",
                     "earnings acceleration"],
}

PULSE_LAYER_MAP = {
    "momentum":          "Fundamental (Earnings momentum proxy)",
    "trend":             "Price Structure",
    "volume":            "Volume",
    "volatility":        "Volatility",
    "structure":         "Price Structure",
    "pattern":           "Price Structure",
    "relative_strength": "Relative Strength",
    "multi_timeframe":   "Price Structure",
    "wyckoff":           "Price Structure",
    "smc":               "Price Structure",
    "canslim":           "Fundamental",
}


def detect_pine_functions(text: str) -> list:
    """Zero-token: detect Pine Script built-in functions in any text."""
    found = []
    text_lower = text.lower()
    for fn, (name, cat) in PINE_FUNCTIONS.items():
        if fn in text_lower:
            found.append({"function": fn, "name": name, "category": cat})
    return found


def extract_categories(text: str) -> list:
    """Zero-token: keyword-based category detection."""
    text_lower = text.lower()
    found = []
    for cat, keywords in KEYWORD_CATEGORIES.items():
        if any(kw in text_lower for kw in keywords):
            found.append(cat)
    return found


def compute_quality_score(item: dict) -> int:
    """
    Zero-token: estimate quality from popularity signals.
    Score 0-100. Likes/stars/upvotes → log-scaled quality.
    """
    score = 30  # base

    # Popularity signal (log-scaled to avoid mega-viral bias)
    popularity = item.get("likes", 0) or item.get("stars", 0) or item.get("upvotes", 0)
    if popularity > 10000:  score = min(score + 40, 70)
    elif popularity > 1000: score = min(score + 30, 65)
    elif popularity > 100:  score = min(score + 20, 60)
    elif popularity > 10:   score = min(score + 10, 50)

    # Pine functions detected = shows actual technical implementation
    pine_count = len(item.get("pine_functions", []))
    score += min(pine_count * 3, 15)

    # PULSE-aligned categories = immediately useful
    categories = item.get("categories", [])
    pulse_aligned = ["volume", "relative_strength", "volatility", "wyckoff", "smc", "canslim"]
    score += sum(5 for c in categories if c in pulse_aligned)

    # Multi-source validation: seen from multiple sources
    if item.get("source_count", 1) > 1:
        score += 5

    return min(score, 100)


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 1: TradingView Community Scripts
# ══════════════════════════════════════════════════════════════════════════════

def scrape_tradingview_scripts(max_pages: int = 3) -> list:
    """
    Scrape TradingView public scripts sorted by likes.
    Tries JSON embedded in page (__NEXT_DATA__) first, then falls back to
    their internal API endpoint known to be used by the web client.
    Returns list of raw items.
    """
    items = []

    # Method A: TradingView's internal pine script listing (no auth for public)
    tv_endpoints = [
        "https://www.tradingview.com/pubscripts-get/?client=web&type=study&per_page=20&page={page}&sort=likes_count",
        "https://www.tradingview.com/pubscripts-get/?client=web&type=strategy&per_page=20&page={page}&sort=likes_count",
    ]

    for endpoint_tmpl in tv_endpoints:
        for page in range(1, max_pages + 1):
            url = endpoint_tmpl.format(page=page)
            try:
                r = SESSION.get(url, timeout=15)
                if r.status_code != 200:
                    break
                data = r.json()
                # API may return a list directly or a dict with 'scripts'/'results' key
                if isinstance(data, list):
                    scripts = data
                else:
                    scripts = data.get("scripts", data.get("results", []))
                if not scripts:
                    break
                for s in scripts:
                    name  = s.get("scriptName", s.get("name", ""))
                    desc  = s.get("description", "")
                    likes = s.get("likes", s.get("agree", 0))
                    tags  = s.get("tags", [])
                    code  = s.get("source", "")

                    pine_fns = detect_pine_functions(code + " " + desc + " " + str(tags))
                    cats     = extract_categories(desc + " " + name + " " + str(tags))

                    item = {
                        "id":             f"tv_{name[:40].replace(' ','_').lower()}",
                        "name":           name,
                        "description":    desc[:500],
                        "source":         "tradingview",
                        "source_url":     f"https://www.tradingview.com/scripts/{s.get('scriptIdPart', '')}",
                        "author":         s.get("authorLogin", ""),
                        "likes":          likes,
                        "pine_functions": pine_fns,
                        "categories":     cats,
                        "tags":           tags[:10],
                        "type":           "strategy" if "strategy" in endpoint_tmpl else "indicator",
                        "added_date":     datetime.utcnow().strftime("%Y-%m-%d"),
                    }
                    item["quality_score"] = compute_quality_score(item)
                    items.append(item)
                time.sleep(0.5)
            except Exception as e:
                print(f"    [TV] page {page} error: {e}")
                break

    # Method B: Try HTML scrape + __NEXT_DATA__ JSON extraction
    if not items:
        try:
            for script_type in ["indicators", "strategies"]:
                r = SESSION.get(
                    f"https://www.tradingview.com/scripts/?sort=likes_total&script_type={script_type}",
                    timeout=20
                )
                html = r.text

                # Try to extract embedded JSON (TradingView uses Next.js __NEXT_DATA__)
                match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                                  html, re.DOTALL)
                if match:
                    next_data = json.loads(match.group(1))
                    # Navigate the React state tree
                    scripts_list = _extract_scripts_from_next_data(next_data)
                    for s in scripts_list[:20]:
                        name = s.get("scriptName", s.get("name", ""))
                        desc = s.get("description", "")
                        pine_fns = detect_pine_functions(desc + " " + name)
                        cats     = extract_categories(desc + " " + name)
                        item = {
                            "id":             f"tv_{name[:40].replace(' ','_').lower()}",
                            "name":           name,
                            "description":    desc[:500],
                            "source":         "tradingview",
                            "source_url":     "",
                            "likes":          s.get("likes", s.get("agree", 0)),
                            "pine_functions": pine_fns,
                            "categories":     cats,
                            "tags":           [],
                            "type":           script_type[:-1],  # "indicator" or "strateg"
                            "added_date":     datetime.utcnow().strftime("%Y-%m-%d"),
                        }
                        item["quality_score"] = compute_quality_score(item)
                        items.append(item)

                # Method C: regex-based HTML extraction
                else:
                    # Extract from HTML script cards (regex, no BS4 needed)
                    title_matches = re.findall(
                        r'"scriptName"\s*:\s*"([^"]+)".*?"likes"\s*:\s*(\d+)', html)
                    for name, likes_str in title_matches[:15]:
                        pine_fns = detect_pine_functions(name)
                        cats     = extract_categories(name)
                        item = {
                            "id":             f"tv_{name[:40].replace(' ','_').lower()}",
                            "name":           name,
                            "description":    "",
                            "source":         "tradingview",
                            "likes":          int(likes_str),
                            "pine_functions": pine_fns,
                            "categories":     cats,
                            "tags":           [],
                            "type":           script_type[:-1],
                            "added_date":     datetime.utcnow().strftime("%Y-%m-%d"),
                        }
                        item["quality_score"] = compute_quality_score(item)
                        items.append(item)
                time.sleep(1)
        except Exception as e:
            print(f"    [TV HTML] error: {e}")

    print(f"    TradingView: {len(items)} scripts")
    return items


def _extract_scripts_from_next_data(data: dict) -> list:
    """Recursively find script arrays in Next.js page data."""
    if isinstance(data, dict):
        for key, val in data.items():
            if key in ("scripts", "pine_scripts", "results") and isinstance(val, list):
                return val
            result = _extract_scripts_from_next_data(val)
            if result:
                return result
    elif isinstance(data, list) and len(data) > 0:
        if isinstance(data[0], dict) and ("scriptName" in data[0] or "name" in data[0]):
            return data
    return []


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 2: GitHub Trading Repositories
# ══════════════════════════════════════════════════════════════════════════════

GITHUB_QUERIES = [
    "trading strategy python stars:>50",
    "pine script indicator stars:>100",
    "algorithmic trading momentum python stars:>30",
    "backtesting breakout strategy python stars:>30",
    "relative strength stock screener python",
]


def scrape_github_strategies(max_per_query: int = 10) -> list:
    """
    GitHub Search API — no auth needed (60 req/hr free).
    Finds trending trading strategy repos with real code quality signals.
    """
    items = []
    seen  = set()

    for query in GITHUB_QUERIES:
        try:
            r = SESSION.get(
                "https://api.github.com/search/repositories",
                params={"q": query, "sort": "stars", "per_page": max_per_query},
                headers={**SESSION.headers, "Accept": "application/vnd.github.v3+json"},
                timeout=15,
            )
            if r.status_code == 403:
                print("    [GitHub] Rate limited — skip")
                break
            data = r.json()

            for repo in data.get("items", []):
                repo_id = repo.get("full_name", "")
                if repo_id in seen:
                    continue
                seen.add(repo_id)

                name    = repo.get("name", "")
                desc    = repo.get("description", "") or ""
                topics  = repo.get("topics", [])
                stars   = repo.get("stargazers_count", 0)
                lang    = repo.get("language", "")
                updated = repo.get("updated_at", "")[:10]

                # Skip if not updated in > 3 years (stale)
                if updated < "2021-01-01":
                    continue

                full_text = f"{name} {desc} {' '.join(topics)}"
                pine_fns  = detect_pine_functions(full_text)
                cats      = extract_categories(full_text)

                # Extra category signals from topics
                for topic in topics:
                    t_lower = topic.lower()
                    for cat, keywords in KEYWORD_CATEGORIES.items():
                        if any(kw in t_lower for kw in keywords):
                            if cat not in cats:
                                cats.append(cat)

                item = {
                    "id":             f"gh_{repo_id.replace('/','_').lower()[:50]}",
                    "name":           name,
                    "description":    desc[:500],
                    "source":         "github",
                    "source_url":     repo.get("html_url", ""),
                    "author":         repo.get("owner", {}).get("login", ""),
                    "stars":          stars,
                    "language":       lang,
                    "topics":         topics[:8],
                    "pine_functions": pine_fns,
                    "categories":     cats,
                    "type":           "strategy" if "strategy" in full_text.lower() else "indicator",
                    "last_updated":   updated,
                    "added_date":     datetime.utcnow().strftime("%Y-%m-%d"),
                }
                item["quality_score"] = compute_quality_score(item)
                items.append(item)
            time.sleep(2)  # GitHub rate limit: 60/hr = 1/minute safe
        except Exception as e:
            print(f"    [GitHub] query '{query[:30]}' error: {e}")

    print(f"    GitHub: {len(items)} repos")
    return items


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 3: Reddit — r/algotrading + r/stocks + r/investing
# ══════════════════════════════════════════════════════════════════════════════

REDDIT_SUBS = [
    ("algotrading",   "top",  "week",  50),
    ("algotrading",   "top",  "month", 20),
    ("stocks",        "top",  "week",  25),
    ("investing",     "top",  "week",  20),
]

REDDIT_FILTER_KEYWORDS = [
    "indicator", "strategy", "backtest", "signal", "momentum", "breakout",
    "mean reversion", "factor", "screen", "filter", "watchlist", "setup",
    "vcp", "wyckoff", "minervini", "relative strength", "volume profile",
    "SMC", "order block", "liquidity", "supply demand", "VWAP",
    "earnings acceleration", "revenue growth", "eps",
]


def scrape_reddit_trading(min_upvotes: int = 50) -> list:
    """
    Reddit public JSON API — no auth needed.
    Filter for trading-relevant posts with meaningful engagement.
    """
    items = []
    seen  = set()
    rh    = {**SESSION.headers, "Accept": "application/json"}

    for subreddit, sort, timeframe, limit in REDDIT_SUBS:
        url = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
        try:
            r = SESSION.get(url, params={"t": timeframe, "limit": limit}, headers=rh, timeout=15)
            if r.status_code != 200:
                continue
            posts = r.json().get("data", {}).get("children", [])

            for post in posts:
                p = post.get("data", {})
                pid = p.get("id", "")
                if pid in seen:
                    continue
                seen.add(pid)

                title     = p.get("title", "")
                selftext  = p.get("selftext", "")[:600]
                upvotes   = p.get("ups", 0)
                url_post  = "https://reddit.com" + p.get("permalink", "")
                flair     = p.get("link_flair_text", "") or ""

                # Filter: min upvotes + trading-relevant
                if upvotes < min_upvotes:
                    continue
                full_text = f"{title} {selftext} {flair}".lower()
                if not any(kw.lower() in full_text for kw in REDDIT_FILTER_KEYWORDS):
                    continue

                pine_fns = detect_pine_functions(full_text)
                cats     = extract_categories(full_text)

                item = {
                    "id":             f"reddit_{pid}",
                    "name":           title[:100],
                    "description":    selftext[:500],
                    "source":         f"reddit_r_{subreddit}",
                    "source_url":     url_post,
                    "author":         p.get("author", ""),
                    "upvotes":        upvotes,
                    "num_comments":   p.get("num_comments", 0),
                    "pine_functions": pine_fns,
                    "categories":     cats,
                    "type":           "discussion",
                    "added_date":     datetime.utcnow().strftime("%Y-%m-%d"),
                }
                item["quality_score"] = compute_quality_score(item)
                items.append(item)
            time.sleep(1)
        except Exception as e:
            print(f"    [Reddit r/{subreddit}] error: {e}")

    print(f"    Reddit: {len(items)} posts")
    return items


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 4: QuantConnect Community (public forum posts)
# ══════════════════════════════════════════════════════════════════════════════

def scrape_quantconnect_forum() -> list:
    """
    QuantConnect public forum — institutional-grade strategy discussions.
    Targets threads about new alpha signals and strategy improvements.
    """
    items = []
    try:
        r = SESSION.get(
            "https://www.quantconnect.com/forum/discussions/1",
            timeout=15,
        )
        html = r.text

        # Extract thread titles (look for anchor tags in thread listing)
        threads = re.findall(
            r'<a[^>]+href="/forum/discussion/(\d+)/[^"]*"[^>]*>([^<]{10,200})</a>',
            html
        )
        for thread_id, title in threads[:15]:
            title = title.strip()
            if len(title) < 10:
                continue
            cats = extract_categories(title)
            pine_fns = detect_pine_functions(title)
            if not cats and not pine_fns:
                continue  # skip irrelevant threads

            item = {
                "id":             f"qc_{thread_id}",
                "name":           title[:100],
                "description":    "",
                "source":         "quantconnect",
                "source_url":     f"https://www.quantconnect.com/forum/discussion/{thread_id}",
                "pine_functions": pine_fns,
                "categories":     cats,
                "type":           "discussion",
                "added_date":     datetime.utcnow().strftime("%Y-%m-%d"),
            }
            item["quality_score"] = compute_quality_score(item)
            items.append(item)
        time.sleep(1)
    except Exception as e:
        print(f"    [QC Forum] error: {e}")

    print(f"    QuantConnect: {len(items)} threads")
    return items


# ══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def load_indicator_kb() -> dict:
    """Load the persistent knowledge base."""
    if KB_FILE.exists():
        try:
            return json.loads(KB_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "indicators": {},
        "strategies": {},
        "discussions": {},
        "kb_stats": {
            "total_items": 0,
            "by_category": {},
            "by_source": {},
            "by_pulse_layer": {},
            "weekly_added": 0,
            "total_weeks": 0,
            "last_updated": "",
        },
        "accuracy_tracking": {},
        "framework_adoptions": [],
    }


def save_indicator_kb(kb: dict):
    """Save knowledge base to disk."""
    KB_FILE.parent.mkdir(parents=True, exist_ok=True)
    KB_FILE.write_text(json.dumps(kb, indent=2, default=str), encoding="utf-8")


def is_duplicate(item: dict, kb: dict) -> bool:
    """Check if item already exists in KB (by ID or similar name)."""
    item_id = item.get("id", "")
    item_name_lower = item.get("name", "").lower()

    # Check all sections
    for section in ("indicators", "strategies", "discussions"):
        if item_id in kb.get(section, {}):
            return True
        # Fuzzy name match
        for existing_id, existing in kb.get(section, {}).items():
            if existing.get("name", "").lower() == item_name_lower:
                # Update last_seen_date and increment source_count
                existing["last_seen_date"] = datetime.utcnow().strftime("%Y-%m-%d")
                existing["source_count"]   = existing.get("source_count", 1) + 1
                # Update quality score with new popularity data
                if item.get("likes", 0) > existing.get("likes", 0):
                    existing["likes"] = item["likes"]
                if item.get("stars", 0) > existing.get("stars", 0):
                    existing["stars"] = item["stars"]
                return True
    return False


def add_to_kb(item: dict, kb: dict, enriched: dict = None) -> bool:
    """
    Add a new item to the knowledge base.
    enriched: LLM-enriched fields (entry_rule, exit_rule, pulse_layer, etc.)
    Returns True if added successfully.
    """
    item_type = item.get("type", "indicator")
    section   = "discussions" if item_type == "discussion" else \
                "strategies"  if item_type == "strategy"   else "indicators"

    kb_entry = {
        **item,
        "last_seen_date":  datetime.utcnow().strftime("%Y-%m-%d"),
        "source_count":    1,
        "our_accuracy":    None,
        "uses_in_trades":  0,
        "adoption_status": "discovered",
        "pulse_layer":     None,
        "entry_rule":      None,
        "exit_rule":       None,
        "adds_novel_value": None,
        "novel_insight":   None,
    }

    # Merge LLM enrichment if available
    if enriched:
        kb_entry.update(enriched)

    # Auto-assign PULSE layer from categories
    if not kb_entry.get("pulse_layer") and item.get("categories"):
        for cat in item["categories"]:
            if cat in PULSE_LAYER_MAP:
                kb_entry["pulse_layer"] = PULSE_LAYER_MAP[cat]
                break

    kb.setdefault(section, {})[item["id"]] = kb_entry

    # Update stats
    stats = kb.setdefault("kb_stats", {})
    stats["total_items"] = (
        len(kb.get("indicators", {}))
        + len(kb.get("strategies", {}))
        + len(kb.get("discussions", {}))
    )
    stats["weekly_added"] = stats.get("weekly_added", 0) + 1
    stats["last_updated"] = datetime.utcnow().strftime("%Y-%m-%d")

    for cat in item.get("categories", []):
        by_cat = stats.setdefault("by_category", {})
        by_cat[cat] = by_cat.get(cat, 0) + 1

    src = item.get("source", "unknown")
    by_src = stats.setdefault("by_source", {})
    by_src[src] = by_src.get(src, 0) + 1

    if kb_entry.get("pulse_layer"):
        by_layer = stats.setdefault("by_pulse_layer", {})
        by_layer[kb_entry["pulse_layer"]] = by_layer.get(kb_entry["pulse_layer"], 0) + 1

    return True


# ══════════════════════════════════════════════════════════════════════════════
# LLM ENRICHMENT — Minimal tokens (batch 10 items per call)
# ══════════════════════════════════════════════════════════════════════════════

ENRICH_PROMPT = """You are AlphaAbsolute's indicator classification expert.
Our framework: PULSE (Fundamental / Price Structure / Relative Strength / Volume / Volatility)
Our methods: Wyckoff + Weinstein Stage 2 + Minervini SEPA + CANSLIM + SMC

For each item below, return a JSON array. Each object must have these exact keys:
  id, entry_rule, exit_rule, pulse_layer, adds_novel_value (true/false), novel_insight, category_primary

pulse_layer must be one of: "Fundamental" / "Price Structure" / "Relative Strength" / "Volume" / "Volatility" / "Market Regime" / "Multiple"

adds_novel_value = true if it adds something NOT already in PULSE/Wyckoff/SEPA. Otherwise false.

novel_insight: one sentence explaining what NEW insight it provides (or "N/A" if not novel).

Be concise. entry_rule max 25 words. exit_rule max 20 words. novel_insight max 30 words.

ITEMS TO CLASSIFY:
{items_json}

Return ONLY the JSON array. No markdown, no explanation."""


def _call_llm(prompt: str, max_tokens: int = 1500) -> str:
    groq_key   = os.environ.get("GROQ_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")

    if groq_key:
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}",
                         "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": max_tokens, "temperature": 0.3},
                timeout=40, verify=False,
            )
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"    Groq error: {e}")

    if gemini_key:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.0-flash:generateContent?key={gemini_key}",
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"maxOutputTokens": max_tokens,
                                           "temperature": 0.3}},
                timeout=40, verify=False,
            )
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            print(f"    Gemini error: {e}")
    return ""


def enrich_batch_with_llm(items: list) -> dict:
    """
    Batch-enrich up to 10 items per LLM call.
    Returns dict: item_id → enrichment fields.
    Only call for high-quality items (quality_score > 45) to save tokens.
    """
    enriched = {}
    if not items:
        return enriched

    # Filter to quality items worth spending tokens on
    quality_items = [i for i in items if i.get("quality_score", 0) > 45]
    if not quality_items:
        return enriched

    # Process in batches of 10
    for i in range(0, len(quality_items), 10):
        batch = quality_items[i:i+10]
        items_json = json.dumps([{
            "id":          it["id"],
            "name":        it["name"],
            "description": it.get("description", "")[:300],
            "pine_functions": [f.get("name") for f in it.get("pine_functions", [])],
            "categories":  it.get("categories", []),
            "source":      it.get("source", ""),
        } for it in batch], ensure_ascii=False)

        prompt  = ENRICH_PROMPT.format(items_json=items_json)
        raw_out = _call_llm(prompt, max_tokens=1200)

        if not raw_out:
            continue

        # Parse JSON response
        try:
            # Strip markdown code blocks if present
            raw_out = re.sub(r"```json\s*|\s*```", "", raw_out).strip()
            result  = json.loads(raw_out)
            if isinstance(result, list):
                for entry in result:
                    if isinstance(entry, dict) and "id" in entry:
                        enriched[entry["id"]] = {
                            "entry_rule":        entry.get("entry_rule"),
                            "exit_rule":         entry.get("exit_rule"),
                            "pulse_layer":       entry.get("pulse_layer"),
                            "adds_novel_value":  entry.get("adds_novel_value"),
                            "novel_insight":     entry.get("novel_insight"),
                            "category_primary":  entry.get("category_primary"),
                        }
        except Exception as e:
            print(f"    [LLM parse error]: {e}")
            print(f"    Raw: {raw_out[:200]}")

    return enriched


# ══════════════════════════════════════════════════════════════════════════════
# ACCURACY TRACKING — link paper trades to indicator usage
# ══════════════════════════════════════════════════════════════════════════════

def track_indicator_usage_in_trades(portfolio: dict, kb: dict):
    """
    Zero-token: scan closed trades for indicator mentions in their metadata.
    Updates KB accuracy tracking so we know which indicators actually work.
    """
    closed = portfolio.get("closed", [])
    tracking = kb.setdefault("accuracy_tracking", {})

    for trade in closed:
        # Check trade notes / signals for indicator references
        trade_text = json.dumps(trade).lower()
        for section in ("indicators", "strategies"):
            for item_id, item in kb.get(section, {}).items():
                item_name = item.get("name", "").lower()
                # If this indicator is mentioned in the trade data
                if item_name and len(item_name) > 3 and item_name in trade_text:
                    t_id = tracking.setdefault(item_id, {
                        "uses": 0, "wins": 0, "losses": 0,
                        "total_pnl": 0, "avg_pnl": 0, "win_rate": 0
                    })
                    pnl = trade.get("pnl_pct", 0)
                    t_id["uses"]      += 1
                    t_id["total_pnl"] += pnl
                    if pnl > 0:
                        t_id["wins"]  += 1
                    else:
                        t_id["losses"] += 1

                    if t_id["uses"] > 0:
                        t_id["avg_pnl"]   = round(t_id["total_pnl"] / t_id["uses"], 2)
                        t_id["win_rate"]  = round(t_id["wins"] / t_id["uses"] * 100, 1)

                    # Update KB item's accuracy
                    item["our_accuracy"]   = t_id["win_rate"]
                    item["uses_in_trades"] = t_id["uses"]


# ══════════════════════════════════════════════════════════════════════════════
# MEMORY WRITER
# ══════════════════════════════════════════════════════════════════════════════

def write_indicator_memory(kb: dict, weekly_result: dict):
    """
    Write discovery summary and high-value KB items to memory file.
    Agents read this at session start to know what indicators are available.
    """
    MEM_FILE.parent.mkdir(parents=True, exist_ok=True)
    today     = datetime.utcnow().strftime("%Y-%m-%d")
    stats     = kb.get("kb_stats", {})

    # ── Knowledge Base Summary header ─────────────────────────────────────────
    header = f"""# AlphaAbsolute — Indicator Knowledge Base
Last updated: {today} | Total items: {stats.get('total_items', 0)}
Sources: TradingView, GitHub, Reddit, QuantConnect

## KB Coverage by PULSE Layer
"""
    for layer, count in sorted(stats.get("by_pulse_layer", {}).items(),
                                key=lambda x: x[1], reverse=True):
        header += f"- {layer}: {count} items\n"

    header += "\n## KB Coverage by Category\n"
    for cat, count in sorted(stats.get("by_category", {}).items(),
                              key=lambda x: x[1], reverse=True)[:10]:
        header += f"- {cat}: {count} items\n"

    # ── Top 20 highest-quality items ──────────────────────────────────────────
    all_items = {
        **kb.get("indicators", {}),
        **kb.get("strategies", {}),
    }
    top_items = sorted(all_items.values(),
                       key=lambda x: x.get("quality_score", 0), reverse=True)[:20]

    top_section = "\n## Top 20 Items by Quality Score\n"
    for item in top_items:
        acc = item.get("our_accuracy")
        acc_str = f" | Accuracy: {acc:.0f}%" if acc is not None else ""
        novel   = item.get("novel_insight", "")
        pulse   = item.get("pulse_layer", "?")
        top_section += (
            f"\n### {item['name']} (score={item.get('quality_score',0)})\n"
            f"- Source: {item.get('source','?')} | Type: {item.get('type','?')}"
            f" | PULSE: {pulse}{acc_str}\n"
        )
        if item.get("entry_rule"):
            top_section += f"- Entry: {item['entry_rule']}\n"
        if item.get("exit_rule"):
            top_section += f"- Exit: {item['exit_rule']}\n"
        if novel and novel != "N/A":
            top_section += f"- Novel: {novel}\n"
        cats = item.get("categories", [])
        if cats:
            top_section += f"- Categories: {', '.join(cats[:4])}\n"

    # ── Novel items (adds_novel_value = True) ─────────────────────────────────
    novel_items = [i for i in all_items.values() if i.get("adds_novel_value") is True]
    novel_section = f"\n## Novel Discoveries ({len(novel_items)} items adding NEW value)\n"
    for item in sorted(novel_items, key=lambda x: x.get("quality_score", 0), reverse=True)[:10]:
        novel_section += (
            f"- **{item['name']}** ({item.get('source','?')}): "
            f"{item.get('novel_insight','')}\n"
            f"  Entry: {item.get('entry_rule','')}\n"
        )

    # ── This week's discoveries ───────────────────────────────────────────────
    new_count    = weekly_result.get("new_items_added", 0)
    top_new      = weekly_result.get("top_new_items", [])
    weekly_section = f"\n## Latest Weekly Discovery ({today})\n"
    weekly_section += f"New items added this week: {new_count}\n"
    if top_new:
        weekly_section += "Top discoveries:\n"
        for item in top_new[:5]:
            weekly_section += (
                f"- {item.get('name','')} "
                f"[{item.get('source','')} | score={item.get('quality_score',0)}]"
                f" — {item.get('novel_insight','')}\n"
            )

    # ── Adopted indicators (promoted to framework) ────────────────────────────
    adoptions = kb.get("framework_adoptions", [])
    if adoptions:
        adopt_section = "\n## Framework Adoptions (approved by Agent 14)\n"
        for a in adoptions[-10:]:
            adopt_section += (
                f"- **{a.get('name','')}** adopted {a.get('date','')} | "
                f"Reason: {a.get('reason','')}\n"
            )
    else:
        adopt_section = "\n## Framework Adoptions\nNone yet — building KB first.\n"

    full_content = (header + top_section + novel_section + weekly_section + adopt_section)
    MEM_FILE.write_text(full_content, encoding="utf-8")
    print(f"    Memory written: {MEM_FILE.name} ({len(full_content)} chars)")


# ══════════════════════════════════════════════════════════════════════════════
# MONTHLY: Propose framework additions from KB
# ══════════════════════════════════════════════════════════════════════════════

PROPOSAL_PROMPT = """You are AlphaAbsolute's Style Guardian (Agent 14).
Your job: propose 3 concrete improvements to our trading framework based on discoveries.

CURRENT FRAMEWORK:
- PULSE: Fundamental / Price Structure / Relative Strength / Volume / Volatility
- Methods: Wyckoff, Weinstein Stage 2, Minervini SEPA/VCP, CANSLIM, SMC
- Entry: EMLS score ≥ 60, Phase 3 NRGC, Trigger = pivot_high × 1.01
- Stop: 8% from trigger
- Indicators used: RSI, MACD, ATR, Volume profile, 52W high/low, RS percentile

TOP NOVEL DISCOVERIES FROM KB THIS MONTH:
{novel_items}

HIGH-ACCURACY ITEMS (used in our trades, positive outcome):
{accuracy_items}

FRAMEWORK GAPS (PULSE layers with fewest KB items):
{coverage_gaps}

Generate exactly 3 proposals in this format:

## PROPOSAL 1: [Name]
Category: [PULSE layer it improves]
Current gap: [what the framework lacks]
New rule: [specific, quantified rule to add — max 40 words]
Implementation: [exactly where in weekly_runner or EMLS scoring to add it]
Evidence: [why this works — cite the source item from KB]
Adoption risk: [LOW/MEDIUM/HIGH] — [one sentence why]

## PROPOSAL 2: [Name]
[same format]

## PROPOSAL 3: [Name]
[same format]

## AGENT 14 VERDICT
Recommend adopting: [list proposal numbers]
Reason: [1-2 sentences]"""


def generate_framework_proposals(kb: dict) -> str:
    """Monthly: generate 3 concrete framework improvement proposals from KB."""
    all_items = {**kb.get("indicators", {}), **kb.get("strategies", {})}

    # Novel items
    novel = sorted(
        [i for i in all_items.values() if i.get("adds_novel_value") is True],
        key=lambda x: x.get("quality_score", 0), reverse=True
    )[:8]
    novel_txt = ""
    for i in novel:
        novel_txt += (
            f"- {i['name']} ({i.get('source','?')} | score={i.get('quality_score',0)}) | "
            f"PULSE: {i.get('pulse_layer','?')}\n"
            f"  Entry: {i.get('entry_rule','')}\n"
            f"  Insight: {i.get('novel_insight','')}\n"
        )

    # High-accuracy items
    accuracy_items = sorted(
        [i for i in all_items.values() if i.get("our_accuracy") is not None],
        key=lambda x: x.get("our_accuracy", 0), reverse=True
    )[:5]
    acc_txt = ""
    for i in accuracy_items:
        acc_txt += (
            f"- {i['name']}: {i.get('our_accuracy','?')}% win rate "
            f"in {i.get('uses_in_trades',0)} uses\n"
        )

    # Coverage gaps
    by_layer = kb.get("kb_stats", {}).get("by_pulse_layer", {})
    all_layers = ["Fundamental", "Price Structure", "Relative Strength",
                  "Volume", "Volatility", "Market Regime"]
    gaps = [(layer, by_layer.get(layer, 0)) for layer in all_layers]
    gaps.sort(key=lambda x: x[1])
    gaps_txt = "\n".join(f"- {layer}: {count} items" for layer, count in gaps[:3])

    prompt = PROPOSAL_PROMPT.format(
        novel_items=novel_txt or "No novel items yet",
        accuracy_items=acc_txt or "Not enough trades yet",
        coverage_gaps=gaps_txt,
    )
    return _call_llm(prompt, max_tokens=1500)


def save_framework_proposals(proposals_text: str, kb: dict):
    """Save proposals to memory and output."""
    if not proposals_text:
        return
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Append to framework updates
    fw_file = BASE_DIR / "memory" / "framework_updates.md"
    fw_file.parent.mkdir(parents=True, exist_ok=True)
    section = (
        f"\n\n---\n# Indicator KB Proposals — {today}\n\n"
        + proposals_text
    )
    existing = fw_file.read_text(encoding="utf-8") if fw_file.exists() else ""
    fw_file.write_text(existing + section, encoding="utf-8")

    # Snapshot output
    snap = BASE_DIR / "output" / f"indicator_proposals_{today}.md"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(
        f"# Indicator Framework Proposals — {today}\n\n" + proposals_text,
        encoding="utf-8"
    )
    print(f"    Proposals saved: {snap.name}")


# ══════════════════════════════════════════════════════════════════════════════
# MASTER RUNNER — Weekly discovery cycle
# ══════════════════════════════════════════════════════════════════════════════

def run_indicator_discovery(portfolio: dict = None) -> dict:
    """
    Weekly: scrape all sources → deduplicate → enrich → store → write memory.
    Returns summary dict for Telegram and weekly_runner log.
    """
    print("  [IndicatorLearner] Starting weekly discovery...")
    kb = load_indicator_kb()
    kb["kb_stats"]["weekly_added"] = 0  # reset weekly counter

    # ── Scrape all sources ────────────────────────────────────────────────────
    all_raw = []
    print("    TradingView scripts...")
    all_raw.extend(scrape_tradingview_scripts(max_pages=3))
    print("    GitHub strategies...")
    all_raw.extend(scrape_github_strategies(max_per_query=8))
    print("    Reddit trading communities...")
    all_raw.extend(scrape_reddit_trading(min_upvotes=50))
    print("    QuantConnect forum...")
    all_raw.extend(scrape_quantconnect_forum())

    print(f"    Total raw items: {len(all_raw)}")

    # ── Deduplicate and filter ────────────────────────────────────────────────
    new_items = []
    for item in all_raw:
        if not item.get("id") or not item.get("name"):
            continue
        if is_duplicate(item, kb):
            continue
        # Minimum quality threshold
        if item.get("quality_score", 0) < 25:
            continue
        new_items.append(item)

    print(f"    New items after dedup: {len(new_items)}")

    # ── LLM enrichment (batch, minimal tokens) ────────────────────────────────
    enriched_map = {}
    if new_items:
        print(f"    LLM enrichment ({min(len(new_items),30)} items, batched)...")
        # Only send top quality items to LLM
        to_enrich = sorted(new_items, key=lambda x: x.get("quality_score", 0),
                           reverse=True)[:30]
        enriched_map = enrich_batch_with_llm(to_enrich)
        print(f"    Enriched: {len(enriched_map)} items")

    # ── Add to KB ─────────────────────────────────────────────────────────────
    added_items = []
    for item in new_items:
        enriched = enriched_map.get(item["id"])
        if add_to_kb(item, kb, enriched):
            added_items.append({**item, **(enriched or {})})

    # ── Accuracy tracking ─────────────────────────────────────────────────────
    if portfolio:
        track_indicator_usage_in_trades(portfolio, kb)

    # ── Update KB totals ──────────────────────────────────────────────────────
    kb["kb_stats"]["total_weeks"] = kb["kb_stats"].get("total_weeks", 0) + 1

    # ── Save KB and write memory ──────────────────────────────────────────────
    save_indicator_kb(kb)

    # Sort added items by quality for Telegram
    top_new = sorted(added_items, key=lambda x: x.get("quality_score", 0),
                     reverse=True)[:5]

    weekly_result = {
        "raw_scraped":     len(all_raw),
        "new_items_added": len(added_items),
        "total_kb_size":   kb["kb_stats"].get("total_items", 0),
        "top_new_items":   top_new,
        "novel_count":     len([i for i in added_items if i.get("adds_novel_value")]),
        "sources_active":  len(set(i.get("source","") for i in all_raw)),
        "top_categories":  sorted(kb["kb_stats"].get("by_category", {}).items(),
                                  key=lambda x: x[1], reverse=True)[:5],
    }

    write_indicator_memory(kb, weekly_result)

    # Log
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log_data = []
    if LOG_FILE.exists():
        try: log_data = json.loads(LOG_FILE.read_text(encoding="utf-8"))
        except: pass
    log_data.append({
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        **{k: v for k, v in weekly_result.items() if k != "top_new_items"},
        "top_names": [i.get("name","") for i in top_new],
    })
    LOG_FILE.write_text(json.dumps(log_data[-52:], indent=2), encoding="utf-8")

    print(f"    Done: {len(added_items)} new items | KB total: {kb['kb_stats'].get('total_items',0)}")
    return weekly_result


def run_indicator_proposals() -> str:
    """Monthly: load KB and generate 3 framework proposals (Agent 14 style)."""
    kb = load_indicator_kb()
    total = kb.get("kb_stats", {}).get("total_items", 0)
    if total < 10:
        print(f"  [IndicatorLearner] Only {total} items — skip proposals (need ≥10)")
        return ""
    print("  [IndicatorLearner] Generating framework proposals...")
    proposals = generate_framework_proposals(kb)
    save_framework_proposals(proposals, kb)
    return proposals


def get_kb_telegram_summary() -> str:
    """Return 3-line KB summary for Telegram."""
    if not KB_FILE.exists():
        return ""
    try:
        kb    = json.loads(KB_FILE.read_text(encoding="utf-8"))
        stats = kb.get("kb_stats", {})
        total = stats.get("total_items", 0)
        log   = []
        if LOG_FILE.exists():
            try:
                log_data = json.loads(LOG_FILE.read_text(encoding="utf-8"))
                if log_data:
                    log = log_data[-1]
            except: pass
        added   = log.get("new_items_added", 0) if isinstance(log, dict) else 0
        novel   = log.get("novel_count", 0)     if isinstance(log, dict) else 0
        top_names = log.get("top_names", [])    if isinstance(log, dict) else []
        lines = [
            f"<b>Indicator KB:</b> {total} items | +{added} this week | {novel} novel",
        ]
        if top_names:
            lines.append(f"  Top: {', '.join(top_names[:3])}")
        top_cats = sorted(stats.get("by_category", {}).items(),
                          key=lambda x: x[1], reverse=True)[:3]
        if top_cats:
            lines.append(f"  Categories: {', '.join(f'{c}({n})' for c,n in top_cats)}")
        return "\n".join(lines)
    except Exception:
        return ""
