"""
AlphaAbsolute — Weekly Research Push (Team K)
Sunday deep-dive: fetch all RSS sources → format → send to Telegram.
No Obsidian write (GitHub Actions has no vault access).

Cost: $0 — pure Python HTTP fetches
"""
import sys, os, json, time
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

import xml.etree.ElementTree as ET  # stdlib — always available

TODAY = date.today().isoformat()

SOURCES = [
    {"name": "SemiAnalysis",   "url": "https://www.semianalysis.com/feed",         "n": 2},
    {"name": "IBD",            "url": "https://www.investors.com/feed/",            "n": 3},
    {"name": "Reuters Mkt",    "url": "https://feeds.reuters.com/reuters/businessNews", "n": 3},
    {"name": "The Diff",       "url": "https://www.thediff.co/feed",               "n": 2},
    {"name": "GS Insights",    "url": "https://www.goldmansachs.com/insights/rss.xml", "n": 2},
]

KEYWORDS = [
    "nvda", "nvidia", "ai ", "hbm", "memory", "dram", "photonics",
    "datacenter", "data center", "nuclear", "smr", "quantum", "space",
    "semiconductor", "gpu", "interconnect", "mu ", "micron",
    "earnings", "guidance", "breakout", "momentum", "insider",
    "fed", "yields", "regime", "correction", "bull",
]


_ATOM_NS = "http://www.w3.org/2005/Atom"


def _fetch_rss(url, n, timeout=10):
    """Fetch RSS or Atom feed. Returns list of title strings."""
    try:
        r = requests.get(url, timeout=timeout,
                         headers={"User-Agent": "AlphaAbsolute/2.0"})
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
        items = []
        is_atom = root.tag in (f"{{{_ATOM_NS}}}feed", "feed")
        if is_atom:
            for entry in root.iter(f"{{{_ATOM_NS}}}entry"):
                t = entry.find(f"{{{_ATOM_NS}}}title")
                if t is not None and t.text:
                    items.append(t.text.strip())
                if len(items) >= n:
                    break
        else:
            for item in root.iter("item"):
                t = item.find("title")
                if t is not None and t.text:
                    items.append(t.text.strip())
                if len(items) >= n:
                    break
        return items
    except Exception:
        return []


def _relevant(title):
    t = title.lower()
    return any(k in t for k in KEYWORDS)


def _send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("[Telegram] No credentials — printing only")
        print(text)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat, "text": text}, timeout=10)
        if r.status_code == 200:
            return True
        # Retry as plain text (omit parse_mode entirely — null value still 400s)
        r2 = requests.post(url, json={"chat_id": chat, "text": text}, timeout=10)
        return r2.status_code == 200
    except Exception as e:
        print(f"[Telegram] send failed: {e}")
        return False


def run():
    if not HAS_REQUESTS:
        print("[WeeklyResearch] requests not installed")
        return

    flagged = []
    total   = 0
    for src in SOURCES:
        items = _fetch_rss(src["url"], src["n"])
        total += len(items)
        for item in items:
            if _relevant(item):
                flagged.append(f"[{src['name']}] {item[:65]}")
        time.sleep(0.5)

    # Build Telegram message (max ~400 chars)
    lines = [
        f"ALPHAABSOLUTE — SUNDAY RESEARCH",
        f"Week of {TODAY} | Team K Deep Dive",
        f"Sources scanned: {total} headlines",
        f"",
    ]

    if flagged:
        lines.append(f"FLAGGED ({len(flagged)} relevant):")
        for f in flagged[:6]:
            lines.append(f"• {f}")
        if len(flagged) > 6:
            lines.append(f"• ...+{len(flagged)-6} more")
    else:
        lines.append("No headlines matched AlphaAbsolute themes this week.")
        lines.append("Market quiet — regime discipline: stay in cash.")

    lines += [
        "",
        "Open Obsidian for full notes.",
        "Have a great week.",
    ]

    msg = "\n".join(lines)
    print(msg)
    ok = _send_telegram(msg)
    print(f"[WeeklyResearch] Telegram: {'sent' if ok else 'FAILED'}")


if __name__ == "__main__":
    run()
