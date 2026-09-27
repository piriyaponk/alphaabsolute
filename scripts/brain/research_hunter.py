"""
Research Hunter — weekly auto-scraper for quant trading research.

Sources: arXiv q-fin, SSRN search, AQR Insights, Alpha Architect, Verdad, Elm Wealth
Output:
  - data/research/queue.json          → all fetched papers (CIO reviews MEDIUM)
  - data/research/auto_notes.json     → HIGH relevance → auto-written to Obsidian
  - Obsidian notes in 15_Research_Library/Auto/

Runs: Sunday pipeline (sunday_research.yml)
"""

from __future__ import annotations
import json
import re
import time
import hashlib
import logging
from datetime import datetime, date
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "research"
DATA_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_FILE = DATA_DIR / "queue.json"
AUTO_NOTES_FILE = DATA_DIR / "auto_notes.json"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Relevance keywords — scored against title + abstract
# ---------------------------------------------------------------------------
HIGH_KEYWORDS = [
    "cross-sectional momentum", "time-series momentum", "factor momentum",
    "relative strength", "volatility targeting", "volatility scaling",
    "hierarchical risk parity", "risk parity", "vol parity",
    "market regime", "regime detection", "regime classification",
    "drawdown control", "momentum crash", "momentum factor",
    "skip-period momentum", "residual momentum", "industry momentum",
    "52-week high", "52 week high", "breadth momentum",
    "turnover momentum", "liquidity momentum",
]

MED_KEYWORDS = [
    "momentum", "trend following", "systematic", "quantitative equity",
    "factor investing", "alpha", "anomaly", "return predictability",
    "portfolio construction", "position sizing", "rebalancing",
    "equity risk premium", "earnings momentum", "price momentum",
    "short interest", "institutional ownership", "PEAD",
]

EXCLUDE_KEYWORDS = [
    "crypto", "bitcoin", "cryptocurrency", "forex", "currency",
    "bond", "fixed income", "option pricing", "interest rate model",
    "neural network architecture", "NLP", "large language model",
]

HEADERS = {"User-Agent": "AlphaAbsolute-ResearchBot/1.0 (research@alphaabsolute.ai)"}
TODAY = date.today().isoformat()


def _score(title: str, abstract: str) -> tuple[str, int]:
    """Return (tier, score). Tier: HIGH / MEDIUM / LOW / SKIP."""
    text = (title + " " + abstract).lower()

    # Hard exclude
    if any(k in text for k in EXCLUDE_KEYWORDS):
        return "SKIP", 0

    score = 0
    for kw in HIGH_KEYWORDS:
        if kw in text:
            score += 3
    for kw in MED_KEYWORDS:
        if kw in text:
            score += 1

    if score >= 6:
        return "HIGH", score
    if score >= 2:
        return "MEDIUM", score
    return "LOW", score


def _paper_id(title: str, url: str) -> str:
    return hashlib.md5((title + url).encode()).hexdigest()[:12]


def _load_seen() -> set[str]:
    if QUEUE_FILE.exists():
        papers = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        return {p["id"] for p in papers}
    return set()


def _append_queue(papers: list[dict]) -> None:
    existing = json.loads(QUEUE_FILE.read_text(encoding="utf-8")) if QUEUE_FILE.exists() else []
    seen = {p["id"] for p in existing}
    new_papers = [p for p in papers if p["id"] not in seen]
    all_papers = existing + new_papers
    # Keep last 500
    all_papers = sorted(all_papers, key=lambda x: x.get("fetched", ""), reverse=True)[:500]
    QUEUE_FILE.write_text(json.dumps(all_papers, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"Queue: +{len(new_papers)} new papers (total {len(all_papers)})")


# ---------------------------------------------------------------------------
# Source 1: arXiv q-fin
# ---------------------------------------------------------------------------
ARXIV_CATEGORIES = ["q-fin.PM", "q-fin.TR", "q-fin.ST"]
ARXIV_API = "http://export.arxiv.org/api/query"


def fetch_arxiv() -> list[dict]:
    papers = []
    for cat in ARXIV_CATEGORIES:
        try:
            params = {
                "search_query": f"cat:{cat}",
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": 30,
            }
            r = requests.get(ARXIV_API, params=params, headers=HEADERS, timeout=20)
            feed = feedparser.parse(r.text)
            for entry in feed.entries:
                title = entry.get("title", "").replace("\n", " ").strip()
                abstract = entry.get("summary", "")[:600]
                url = entry.get("link", "")
                tier, score = _score(title, abstract)
                if tier == "SKIP" or tier == "LOW":
                    continue
                papers.append({
                    "id": _paper_id(title, url),
                    "source": "arxiv",
                    "tier": tier,
                    "score": score,
                    "title": title,
                    "abstract": abstract[:400],
                    "url": url,
                    "fetched": TODAY,
                    "status": "new",
                    "hypotheses": [],
                })
            time.sleep(1)
        except Exception as e:
            log.warning(f"arXiv {cat}: {e}")
    log.info(f"arXiv: {len(papers)} relevant papers")
    return papers


# ---------------------------------------------------------------------------
# Source 2: SSRN search (econ + finance working papers)
# ---------------------------------------------------------------------------
SSRN_SEARCHES = [
    "cross-sectional momentum equity",
    "volatility targeting momentum",
    "market regime momentum",
    "momentum crash protection",
]


def fetch_ssrn() -> list[dict]:
    papers = []
    seen_ids = set()
    for query in SSRN_SEARCHES:
        try:
            url = f"https://papers.ssrn.com/sol3/results.cfm?RequestTimeout=50000&txtkey={requests.utils.quote(query)}&sortby=sub_date&lstAbs=1"
            r = requests.get(url, headers=HEADERS, timeout=25)
            soup = BeautifulSoup(r.text, "html.parser")
            for item in soup.select("div.title")[:8]:
                a_tag = item.find("a")
                if not a_tag:
                    continue
                title = a_tag.get_text(strip=True)
                paper_url = "https://papers.ssrn.com" + a_tag.get("href", "")
                # Abstract from sibling div
                parent = item.find_parent("div", class_="list-group-item")
                abstract = ""
                if parent:
                    abs_div = parent.find("div", class_="abstract-text")
                    if abs_div:
                        abstract = abs_div.get_text(strip=True)[:400]
                tier, score = _score(title, abstract)
                if tier in ("SKIP", "LOW"):
                    continue
                pid = _paper_id(title, paper_url)
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                papers.append({
                    "id": pid,
                    "source": "ssrn",
                    "tier": tier,
                    "score": score,
                    "title": title,
                    "abstract": abstract,
                    "url": paper_url,
                    "fetched": TODAY,
                    "status": "new",
                    "hypotheses": [],
                })
            time.sleep(2)
        except Exception as e:
            log.warning(f"SSRN '{query}': {e}")
    log.info(f"SSRN: {len(papers)} relevant papers")
    return papers


# ---------------------------------------------------------------------------
# Source 3: AQR Insights (RSS / sitemap)
# ---------------------------------------------------------------------------
AQR_RSS = "https://www.aqr.com/feed/insights"


def fetch_aqr() -> list[dict]:
    papers = []
    try:
        feed = feedparser.parse(AQR_RSS)
        for entry in feed.entries[:20]:
            title = entry.get("title", "")
            abstract = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()[:400]
            url = entry.get("link", "")
            tier, score = _score(title, abstract)
            if tier in ("SKIP", "LOW"):
                continue
            papers.append({
                "id": _paper_id(title, url),
                "source": "aqr",
                "tier": tier,
                "score": score,
                "title": title,
                "abstract": abstract,
                "url": url,
                "fetched": TODAY,
                "status": "new",
                "hypotheses": [],
            })
    except Exception as e:
        log.warning(f"AQR RSS: {e}")
    log.info(f"AQR: {len(papers)} relevant papers")
    return papers


# ---------------------------------------------------------------------------
# Source 4: Alpha Architect (RSS)
# ---------------------------------------------------------------------------
AA_RSS = "https://alphaarchitect.com/feed/"


def fetch_alpha_architect() -> list[dict]:
    papers = []
    try:
        feed = feedparser.parse(AA_RSS)
        for entry in feed.entries[:20]:
            title = entry.get("title", "")
            abstract = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()[:400]
            url = entry.get("link", "")
            tier, score = _score(title, abstract)
            if tier in ("SKIP", "LOW"):
                continue
            papers.append({
                "id": _paper_id(title, url),
                "source": "alpha_architect",
                "tier": tier,
                "score": score,
                "title": title,
                "abstract": abstract,
                "url": url,
                "fetched": TODAY,
                "status": "new",
                "hypotheses": [],
            })
    except Exception as e:
        log.warning(f"Alpha Architect RSS: {e}")
    log.info(f"Alpha Architect: {len(papers)} relevant papers")
    return papers


# ---------------------------------------------------------------------------
# Source 5: Verdad Research (weekly posts)
# ---------------------------------------------------------------------------
VERDAD_RSS = "https://verdadcap.com/feed"


def fetch_verdad() -> list[dict]:
    papers = []
    try:
        feed = feedparser.parse(VERDAD_RSS)
        for entry in feed.entries[:10]:
            title = entry.get("title", "")
            abstract = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()[:400]
            url = entry.get("link", "")
            tier, score = _score(title, abstract)
            if tier in ("SKIP", "LOW"):
                continue
            papers.append({
                "id": _paper_id(title, url),
                "source": "verdad",
                "tier": tier,
                "score": score,
                "title": title,
                "abstract": abstract,
                "url": url,
                "fetched": TODAY,
                "status": "new",
                "hypotheses": [],
            })
    except Exception as e:
        log.warning(f"Verdad RSS: {e}")
    log.info(f"Verdad: {len(papers)} relevant papers")
    return papers


# ---------------------------------------------------------------------------
# Source 6: Elm Wealth
# ---------------------------------------------------------------------------
ELM_RSS = "https://elmwealth.com/feed/"


def fetch_elm() -> list[dict]:
    papers = []
    try:
        feed = feedparser.parse(ELM_RSS)
        for entry in feed.entries[:10]:
            title = entry.get("title", "")
            abstract = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()[:400]
            url = entry.get("link", "")
            tier, score = _score(title, abstract)
            if tier in ("SKIP", "LOW"):
                continue
            papers.append({
                "id": _paper_id(title, url),
                "source": "elm_wealth",
                "tier": tier,
                "score": score,
                "title": title,
                "abstract": abstract,
                "url": url,
                "fetched": TODAY,
                "status": "new",
                "hypotheses": [],
            })
    except Exception as e:
        log.warning(f"Elm Wealth RSS: {e}")
    log.info(f"Elm Wealth: {len(papers)} relevant papers")
    return papers


# ---------------------------------------------------------------------------
# Auto-write Obsidian notes for HIGH papers
# ---------------------------------------------------------------------------
def _write_obsidian_note(paper: dict) -> bool:
    try:
        sys_path_root = str(ROOT)
        import sys
        if sys_path_root not in sys.path:
            sys.path.insert(0, sys_path_root)
        from scripts.brain.obsidian_writer_v2 import write as obs_write

        safe_title = re.sub(r'[\\/:*?"<>|]', "_", paper["title"])[:80]
        filename = f"15_Research_Library/Auto/{TODAY}_{safe_title}.md"

        content = f"""---
type: research_paper
title: "{paper['title']}"
source: {paper['source']}
url: {paper['url']}
tier: {paper['tier']}
relevance_score: {paper['score']}
fetched: {paper['fetched']}
tags: [auto-fetched, quant, research]
status: unread
---

# {paper['title']}

**Source:** {paper['source'].upper()} | **Relevance:** {paper['tier']} (score={paper['score']})
**URL:** {paper['url']}

## Abstract
{paper['abstract']}

---

## Signal Hypotheses
*(Fill in after reading)*

- [ ] Hypothesis 1:
- [ ] Hypothesis 2:

## AlphaAbsolute S4 Relevance
*(How does this apply to S4 cross-sectional momentum + vol-parity?)*

## Links
[[15_Research_Library/Diary_of_a_Quant_Notebook_II]]
[[15_Research_Library/151_Trading_Strategies]]
"""
        return obs_write(filename, content)
    except Exception as e:
        log.warning(f"Obsidian write failed for '{paper['title']}': {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run() -> dict:
    log.info("=== Research Hunter START ===")
    seen = _load_seen()

    all_papers: list[dict] = []
    all_papers += fetch_arxiv()
    all_papers += fetch_ssrn()
    all_papers += fetch_aqr()
    all_papers += fetch_alpha_architect()
    all_papers += fetch_verdad()
    all_papers += fetch_elm()

    # Deduplicate vs already-seen
    new_papers = [p for p in all_papers if p["id"] not in seen]
    log.info(f"New (not in queue): {len(new_papers)}")

    # Write to queue
    _append_queue(new_papers)

    # Auto-write Obsidian for HIGH papers
    high_papers = [p for p in new_papers if p["tier"] == "HIGH"]
    obsidian_written = 0
    for p in high_papers:
        if _write_obsidian_note(p):
            obsidian_written += 1

    # Summary for Telegram
    med_papers = [p for p in new_papers if p["tier"] == "MEDIUM"]
    summary = {
        "date": TODAY,
        "new_total": len(new_papers),
        "high": len(high_papers),
        "medium": len(med_papers),
        "obsidian_written": obsidian_written,
        "top_papers": [
            {"title": p["title"][:80], "source": p["source"], "score": p["score"]}
            for p in sorted(new_papers, key=lambda x: x["score"], reverse=True)[:5]
        ],
    }

    AUTO_NOTES_FILE.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    # Telegram push — only when there's something new to report
    if summary["new_total"] > 0:
        _send_telegram(summary)

    log.info("=== Research Hunter DONE ===")
    return summary


def _send_telegram(summary: dict) -> None:
    import os
    bot = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not bot or not chat:
        return

    lines = [
        f"📚 RESEARCH HUNTER [{summary['date']}]",
        f"NEW: {summary['new_total']} papers | HIGH: {summary['high']} | MED: {summary['medium']}",
        f"Auto-written to Obsidian: {summary['obsidian_written']}",
        "",
        "TOP PICKS:",
    ]
    for i, p in enumerate(summary["top_papers"][:3], 1):
        lines.append(f"{i}. [{p['source'].upper()}] {p['title'][:60]} (score={p['score']})")

    if summary["high"] > 0:
        lines.append("")
        lines.append("→ HIGH papers auto-saved to 15_Research_Library/Auto/")
    if summary["medium"] > 0:
        lines.append(f"→ {summary['medium']} MEDIUM papers in data/research/queue.json — review needed")

    text = "\n".join(lines)
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot}/sendMessage",
            json={"chat_id": chat, "text": text},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Telegram: {e}")


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
