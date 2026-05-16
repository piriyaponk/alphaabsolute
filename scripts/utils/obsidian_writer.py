"""
AlphaAbsolute — Obsidian REST API Writer
Writes investment research directly into Obsidian vault via Local REST API plugin.

Requirements:
  1. Obsidian installed with Local REST API plugin
  2. OBSIDIAN_API_KEY in .env
  3. Plugin running on https://127.0.0.1:27124

NotebookLM is kept for long-form research uploads.
Obsidian is used for: ticker notes, theme notes, trade logs, daily ops.
"""

import os, json, requests
from pathlib import Path
from datetime import date
from typing import Optional
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Load .env from project root (two levels up from scripts/utils/)
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass

OBSIDIAN_URL = os.getenv("OBSIDIAN_URL", "https://127.0.0.1:27124")
OBSIDIAN_KEY = os.getenv("OBSIDIAN_API_KEY", "")

def _headers(content_type: str = "text/markdown") -> dict:
    return {
        "Authorization": f"Bearer {OBSIDIAN_KEY}",
        "Content-Type": content_type,
    }

def _is_available() -> bool:
    """Check if Obsidian REST API is running AND authenticated."""
    if not OBSIDIAN_KEY:
        return False
    try:
        r = requests.get(
            f"{OBSIDIAN_URL}/vault/",
            headers=_headers(),
            timeout=2,
            verify=False,
        )
        return r.status_code == 200
    except Exception:
        return False

def write_note(vault_path: str, content: str) -> bool:
    """
    Write or overwrite a note in the Obsidian vault.
    vault_path: relative path e.g. 'tickers/MU.md'
    """
    if not _is_available():
        return False
    try:
        r = requests.put(
            f"{OBSIDIAN_URL}/vault/{vault_path}",
            data=content.encode("utf-8"),
            headers=_headers(),
            verify=False,
            timeout=10
        )
        return r.status_code in (200, 201, 204)
    except Exception as e:
        print(f"[Obsidian] write_note failed: {e}")
        return False

def append_to_section(vault_path: str, heading: str, content: str) -> bool:
    """Append content under a specific heading in an existing note."""
    if not _is_available():
        return False
    try:
        r = requests.patch(
            f"{OBSIDIAN_URL}/vault/{vault_path}",
            data=content.encode("utf-8"),
            headers={
                **_headers(),
                "Operation": "append",
                "Target-Type": "heading",
                "Target": heading,
            },
            verify=False,
            timeout=10
        )
        return r.status_code in (200, 201, 204)
    except Exception as e:
        print(f"[Obsidian] append_to_section failed: {e}")
        return False

def read_note(vault_path: str) -> Optional[str]:
    """Read content of a note. Returns None if not found."""
    if not _is_available():
        return None
    try:
        r = requests.get(
            f"{OBSIDIAN_URL}/vault/{vault_path}",
            headers=_headers(),
            verify=False,
            timeout=10
        )
        if r.status_code == 200:
            return r.text
        return None
    except Exception:
        return None

def search_vault(query: str) -> list:
    """Full-text search across vault. Returns list of matching notes."""
    if not _is_available():
        return []
    try:
        r = requests.post(
            f"{OBSIDIAN_URL}/search/simple/",
            params={"query": query},
            headers=_headers("application/json"),
            verify=False,
            timeout=10
        )
        if r.status_code == 200:
            return r.json()
        return []
    except Exception:
        return []

# ─────────────────────────────────────────────
# High-level helpers for AlphaAbsolute
# ─────────────────────────────────────────────

def write_ticker_note(ticker: str, assessment: dict) -> bool:
    """
    Write a full ticker assessment note to tickers/{TICKER}.md
    assessment dict expected keys: emls_score, nrgc_phase, theme, setup,
    emls_boost, edge_signals, summary, entry, stop, last_updated
    """
    today = date.today().isoformat()
    emls  = assessment.get("emls_score", 0)
    phase = assessment.get("nrgc_phase", "?")
    theme = assessment.get("theme", "")
    setup = assessment.get("setup", "")
    entry = assessment.get("entry", "")
    stop  = assessment.get("stop", "")
    sig   = ", ".join(assessment.get("edge_signals", []))
    summ  = assessment.get("summary", "")

    content = f"""---
ticker: {ticker}
emls_score: {emls}
nrgc_phase: {phase}
theme: {theme}
setup: {setup}
entry: {entry}
stop: {stop}
last_updated: {today}
---

# {ticker}

**EMLS Score:** {emls} | **NRGC Phase:** {phase} | **Theme:** {theme}

## Setup
{setup or 'No setup identified'}

## Edge Signals
{sig or 'None detected this week'}

## Assessment
{summ or 'No summary available'}

## History
"""
    return write_note(f"tickers/{ticker}.md", content)

def append_ticker_signal(ticker: str, signal_line: str) -> bool:
    """Append a weekly signal line to existing ticker note."""
    today = date.today().isoformat()
    entry = f"\n- {today}: {signal_line}"
    return append_to_section(f"tickers/{ticker}.md", "History", entry)

def write_theme_note(theme: str, content: str) -> bool:
    """Write or overwrite a theme note."""
    fname = theme.replace("/", "-").replace(" ", "_")
    return write_note(f"themes/{fname}.md", content)

def append_theme_signal(theme: str, signal_line: str) -> bool:
    """Append weekly edge signal to theme note."""
    today = date.today().isoformat()
    fname = theme.replace("/", "-").replace(" ", "_")
    entry = f"\n- {today}: {signal_line}"
    return append_to_section(f"themes/{fname}.md", "Weekly Signals", entry)

def write_daily_note(content: str) -> bool:
    """Write today's daily brief note."""
    today = date.today().isoformat()
    return write_note(f"daily/{today}.md", content)

def write_trade_note(ticker: str, trade: dict) -> bool:
    """Log a paper trade as a separate note."""
    today       = date.today().isoformat()
    direction   = trade.get("direction", "BUY")
    entry_price = trade.get("entry_price", 0)
    stop_price  = trade.get("stop_price", 0)
    target      = trade.get("target", 0)
    size_pct    = trade.get("size_pct", 0)
    setup       = trade.get("setup", "")
    nrgc_phase  = trade.get("nrgc_phase", "")
    emls        = trade.get("emls_score", 0)
    health      = trade.get("health_score", "")

    content = f"""---
ticker: {ticker}
direction: {direction}
entry_date: {today}
entry_price: {entry_price}
stop_price: {stop_price}
target: {target}
size_pct: {size_pct}
setup: {setup}
nrgc_phase: {nrgc_phase}
emls_score: {emls}
status: open
---

# {direction} {ticker} — {today}

| Field | Value |
|-------|-------|
| Entry | ${entry_price} |
| Stop | ${stop_price} ({round((stop_price-entry_price)/max(entry_price,0.01)*100,1)}%) |
| Target | ${target} ({round((target-entry_price)/max(entry_price,0.01)*100,1)}%) |
| Size | {size_pct}% |
| Setup | {setup} |
| NRGC Phase | {nrgc_phase} |
| EMLS Score | {emls} |
| Health Check | {health} |

## Thesis
[Fill in thesis here]

## Thesis Challenge
[What would make this wrong?]

## Exit Log
"""
    fname = f"{today}-{ticker}-{direction.lower()}"
    return write_note(f"paper_trades/{fname}.md", content)

if __name__ == "__main__":
    # Quick connectivity test
    if _is_available():
        print("[Obsidian] REST API reachable — integration active")
    else:
        print("[Obsidian] REST API not available (Obsidian not running or plugin not installed)")
        print("  Install: Obsidian -> Community Plugins -> Local REST API")
        print("  Set OBSIDIAN_API_KEY in .env")
