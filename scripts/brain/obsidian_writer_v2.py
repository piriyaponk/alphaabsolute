"""
AlphaAbsolute — Obsidian Direct File Writer v2
Writes .md files directly to vault folder — no REST API, no plugin required.
Works even when Obsidian is closed. OneDrive syncs automatically.

Vault: C:/Users/Pizza/OneDrive/Documents/Obsidian Vault/
Cost: $0 — pure Python file I/O
"""
import time
from pathlib import Path
from datetime import date
from typing import Optional

VAULT = Path(r"C:\Users\Pizza\OneDrive\Documents\Obsidian Vault")

_FOLDERS = [
    "Themes", "Themes/_New Discoveries",
    "Signals",
    "Knowledge", "Knowledge/Daily",
    "Knowledge/Druckenmiller", "Knowledge/Minervini",
    "Knowledge/Papers", "Knowledge/Interviews",
    "Bottlenecks",
    "Performance", "Performance/Monthly",
    "Tickers", "Trades",
    "System",
]


def ensure_vault():
    """Create all required vault folders if missing."""
    for f in _FOLDERS:
        (VAULT / f).mkdir(parents=True, exist_ok=True)


def is_available() -> bool:
    return VAULT.exists()


# ── Low-level primitives ───────────────────────────────────────────────────────

def write(vault_path: str, content: str) -> bool:
    """Write or overwrite a note. Creates parent dirs automatically."""
    try:
        p = VAULT / vault_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return True
    except Exception as e:
        print(f"[Obsidian] write failed ({vault_path}): {e}")
        return False


def append(vault_path: str, content: str) -> bool:
    """Append content to an existing note (creates if missing).
    Retries up to 3× on PermissionError — OneDrive may hold a lock during sync."""
    try:
        p = VAULT / vault_path
        p.parent.mkdir(parents=True, exist_ok=True)
        existing = p.read_text(encoding="utf-8") if p.exists() else ""
        new_text = existing.rstrip() + "\n" + content
        for attempt in range(3):
            try:
                p.write_text(new_text, encoding="utf-8")
                return True
            except PermissionError:
                if attempt < 2:
                    time.sleep(1)
        print(f"[Obsidian] append failed ({vault_path}): PermissionError after 3 attempts")
        return False
    except Exception as e:
        print(f"[Obsidian] append failed ({vault_path}): {e}")
        return False


def read(vault_path: str) -> Optional[str]:
    """Read a note. Returns None if not found."""
    try:
        p = VAULT / vault_path
        return p.read_text(encoding="utf-8") if p.exists() else None
    except Exception:
        return None


# ── High-level helpers ─────────────────────────────────────────────────────────

def write_daily_note(content: str, label: str = "") -> bool:
    """Write today's research note to Knowledge/Daily/YYYY-MM-DD.md"""
    today = date.today().isoformat()
    fname = f"{today}{'-' + label if label else ''}.md"
    return write(f"Knowledge/Daily/{fname}", content)


def append_daily_note(content: str) -> bool:
    """Append to today's daily note (creates if missing)."""
    today = date.today().isoformat()
    return append(f"Knowledge/Daily/{today}.md", content)


def write_theme_note(theme: str, content: str) -> bool:
    """Write or overwrite a theme page."""
    fname = theme.replace("/", "-").replace(" ", "_") + ".md"
    return write(f"Themes/{fname}", content)


def append_theme_signal(theme: str, signal_line: str) -> bool:
    """Append a dated signal line to a theme note."""
    today = date.today().isoformat()
    fname = theme.replace("/", "-").replace(" ", "_") + ".md"
    return append(f"Themes/{fname}", f"\n- {today}: {signal_line}")


def write_ticker_note(ticker: str, data: dict) -> bool:
    """Write ticker research note with MOAT/TAM/Bottleneck scores."""
    today = date.today().isoformat()
    m = data.get("moat_score", "N/A")
    t = data.get("tam_score", "N/A")
    b = data.get("bottleneck_score", "N/A")
    content = f"""---
ticker: {ticker}
last_updated: {today}
moat_score: {m}
tam_score: {t}
bottleneck_score: {b}
theme: {data.get("theme", "")}
---

# {ticker}

**MOAT:** {m}/10 | **TAM:** {t}/10 | **Bottleneck:** {b}/10 | Updated: {today}

## Why This Stock

{data.get("thesis", "_No thesis yet_")}

## Key Signals

{data.get("signals", "_None identified_")}

## MOAT Evidence

{data.get("moat_evidence", "_Not assessed_")}

## TAM Evidence

{data.get("tam_evidence", "_Not assessed_")}

## Bottleneck Evidence

{data.get("bottleneck_evidence", "_Not assessed_")}

## History

"""
    return write(f"Tickers/{ticker}.md", content)


def write_trade_note(ticker: str, trade: dict) -> bool:
    """Log a paper trade entry."""
    today = date.today().isoformat()
    ep = trade.get("entry_price", 0)
    sp = trade.get("stop_price", 0)
    tg = trade.get("target", 0)
    stop_pct = round((sp - ep) / ep * 100, 1) if ep else 0
    tgt_pct  = round((tg - ep) / ep * 100, 1) if ep else 0
    rr = round(tgt_pct / abs(stop_pct), 1) if stop_pct else 0
    content = f"""---
ticker: {ticker}
direction: {trade.get("direction", "BUY")}
entry_date: {today}
entry_price: {ep}
stop_price: {sp}
target: {tg}
size_pct: {trade.get("size_pct", 0)}
rr_ratio: {rr}
status: open
---

# {trade.get("direction","BUY")} {ticker} — {today}

| Field | Value |
|-------|-------|
| Entry | ${ep} |
| Stop | ${sp} ({stop_pct}%) |
| Target | ${tg} ({tgt_pct}%) |
| R:R | {rr}x |
| Size | {trade.get("size_pct", 0)}% |
| Setup | {trade.get("setup", "")} |

## Thesis

[Fill in thesis]

## What Would Make This Wrong?

[Devil's advocate]

## Exit Log

"""
    fname = f"{today}-{ticker}.md"
    return write(f"Trades/{fname}", content)


def write_signal_finding(signal_name: str, content: str) -> bool:
    """Write a signal backtest finding."""
    today = date.today().isoformat()
    fname = f"{signal_name.replace(' ', '_')}_{today}.md"
    return write(f"Signals/{fname}", content)


def write_performance_note(period: str, content: str) -> bool:
    """Write monthly performance attribution note."""
    return write(f"Performance/Monthly/{period}.md", content)


if __name__ == "__main__":
    ensure_vault()
    if is_available():
        print(f"[Obsidian] Vault ready: {VAULT}")
        print(f"[Obsidian] Folders created: {len(_FOLDERS)}")
    else:
        print(f"[Obsidian] Vault NOT found at: {VAULT}")
