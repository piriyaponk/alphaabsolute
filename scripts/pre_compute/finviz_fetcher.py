"""
AlphaAbsolute — Finviz Market Cap + Short Interest Fetcher
===========================================================
Fills ticker_meta.market_cap and ticker_meta.short_interest_pct for tickers
where those fields are NULL. Both values come from the same HTTP request —
zero additional latency for the short interest fill.

Source: https://finviz.com/quote.ashx?t={TICKER}
Robots.txt: /quote.ashx?t=* NOT in Disallow list. Screener bulk IS blocked — per-ticker only.
            Assessed 2026-06-01 by DE Team.

Cache: 7-day TTL.
Rate:  0.5s delay (no explicit crawl-delay in robots.txt, conservative).
Run:   Weekly in pre_market_runner.py, after stockanalysis_fetcher.py.

Fields fetched per page request:
  - market_cap      → ticker_meta.market_cap      (USD float, e.g. 5.1e12)
  - short_float_pct → ticker_meta.short_interest_pct (%, e.g. 4.52)

Usage:
  python scripts/pre_compute/finviz_fetcher.py
  python scripts/pre_compute/finviz_fetcher.py --tickers NVDA AAPL MSFT
"""

import sqlite3
import json
import time
import sys
import argparse
import re
from datetime import date
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

import urllib3
urllib3.disable_warnings()

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE  = Path(__file__).resolve()
ROOT   = _HERE.parent.parent.parent
DB_PATH    = ROOT / "data" / "ohlcv.db"
CKPT_FILE  = ROOT / "data" / "finviz_fetcher_checkpoint.json"

CACHE_TTL_DAYS = 7
DELAY_S        = 0.5
MAX_RETRIES    = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finviz.com/",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.verify = False  # WARP-hardened


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_market_cap(s: str) -> Optional[float]:
    """
    Parse Finviz market cap string to USD float.
    Examples: "5109.59B" → 5.11e12, "283.11M" → 2.83e8, "1.23T" → 1.23e12
    """
    if not s or s.strip() in ("-", "N/A", ""):
        return None
    s = s.strip()
    multiplier = 1.0
    if s.endswith("T"):
        multiplier = 1e12
        s = s[:-1]
    elif s.endswith("B"):
        multiplier = 1e9
        s = s[:-1]
    elif s.endswith("M"):
        multiplier = 1e6
        s = s[:-1]
    elif s.endswith("K"):
        multiplier = 1e3
        s = s[:-1]
    try:
        return float(s.replace(",", "")) * multiplier
    except ValueError:
        return None


def _parse_short_float(s: str) -> Optional[float]:
    """
    Parse Finviz "Short Float" string to percentage float.
    Examples: "4.52%" → 4.52, "12.3%" → 12.3, "-" → None
    """
    if not s or s.strip() in ("-", "N/A", ""):
        return None
    s = s.strip().replace("%", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def fetch_finviz(ticker: str) -> Optional[dict]:
    """
    Fetch market cap and short float from Finviz quote page.
    Returns dict {"market_cap": float|None, "short_float_pct": float|None}
    or None on complete failure.

    Both fields are extracted from the same HTTP request — zero extra latency.
    "Market Cap" → ticker_meta.market_cap (USD float)
    "Short Float" → ticker_meta.short_interest_pct (%)
    """
    url = f"https://finviz.com/quote.ashx?t={ticker.upper()}"
    backoff = 1.0

    for attempt in range(MAX_RETRIES):
        try:
            r = SESSION.get(url, timeout=12)
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                wait = backoff * (2 ** attempt)
                print(f"    [{ticker}] 429 rate limit — sleeping {wait:.0f}s")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                return None

            soup = BeautifulSoup(r.text, "html.parser")

            # Finviz stores stats in the snapshot-table2 element
            tables = soup.find_all("table", {"class": re.compile(r"snapshot")})
            target_table = tables[0] if tables else None

            # Collect all td cells — scan once for all target labels
            all_tds = (target_table.find_all("td") if target_table
                       else soup.find_all("td"))

            if not all_tds:
                print(f"    [{ticker}] snapshot-table2 not found — PARSER_BROKEN")
                return None

            market_cap: Optional[float] = None
            short_float_pct: Optional[float] = None

            for i, td in enumerate(all_tds):
                label = td.get_text(strip=True)
                if label == "Market Cap" and i + 1 < len(all_tds):
                    market_cap = _parse_market_cap(all_tds[i + 1].get_text(strip=True))
                elif label == "Short Float" and i + 1 < len(all_tds):
                    short_float_pct = _parse_short_float(all_tds[i + 1].get_text(strip=True))

            if market_cap is None and short_float_pct is None:
                if not target_table:
                    print(f"    [{ticker}] Market Cap / Short Float cells not found")
                return None

            return {"market_cap": market_cap, "short_float_pct": short_float_pct}

        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(backoff)
            else:
                print(f"    [{ticker}] Request failed: {e}")
                return None

    return None


def write_finviz_data(con: sqlite3.Connection, ticker: str, data: dict) -> None:
    """
    Upsert market_cap and short_interest_pct into ticker_meta.
    Only writes non-None values — never overwrites with NULL.
    """
    cur = con.cursor()
    today = date.today().isoformat()
    mktcap = data.get("market_cap")
    short_pct = data.get("short_float_pct")

    # Check if row exists
    cur.execute("SELECT 1 FROM ticker_meta WHERE ticker = ?", (ticker,))
    exists = cur.fetchone()

    if exists:
        sets = []
        vals = []
        if mktcap is not None:
            sets.append("market_cap = ?")
            vals.append(mktcap)
        if short_pct is not None:
            sets.append("short_interest_pct = ?")
            vals.append(short_pct)
        if sets:
            sets.append("last_updated = ?")
            vals.append(today)
            vals.append(ticker)
            cur.execute(
                f"UPDATE ticker_meta SET {', '.join(sets)} WHERE ticker = ?",
                vals
            )
    else:
        # Insert minimal row — only include columns with values
        cols = ["ticker", "last_updated"]
        phs  = ["?", "?"]
        vals = [ticker, today]
        if mktcap is not None:
            cols.append("market_cap"); phs.append("?"); vals.append(mktcap)
        if short_pct is not None:
            cols.append("short_interest_pct"); phs.append("?"); vals.append(short_pct)
        cur.execute(
            f"INSERT OR IGNORE INTO ticker_meta ({', '.join(cols)}) VALUES ({', '.join(phs)})",
            vals
        )
    con.commit()


# ── Backward-compat alias used by pre_market_runner.py if referenced directly ──
def write_market_cap(con: sqlite3.Connection, ticker: str, mktcap: float) -> None:
    write_finviz_data(con, ticker, {"market_cap": mktcap})


def _load_checkpoint() -> set:
    if CKPT_FILE.exists():
        try:
            return set(json.loads(CKPT_FILE.read_text()))
        except Exception:
            pass
    return set()


def _save_checkpoint(done: set) -> None:
    CKPT_FILE.write_text(json.dumps(sorted(done)))


# ── Main ──────────────────────────────────────────────────────────────────────

def run(tickers: list = None) -> dict:
    print(f"\n{'='*58}")
    print(f"  Finviz Market Cap + Short Interest  [{date.today().isoformat()}]")
    print(f"{'='*58}")

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    if tickers:
        targets = [t.upper() for t in tickers]
        print(f"  Mode: explicit list — {len(targets)} tickers")
    else:
        # Only tickers missing market_cap in ticker_meta
        cur.execute("""
            SELECT ticker FROM ticker_meta
            WHERE market_cap IS NULL OR market_cap = 0
            ORDER BY ticker
        """)
        targets = [r[0] for r in cur.fetchall()]
        print(f"  Mode: missing-only — {len(targets)} tickers without market_cap")

    if not targets:
        print("  Nothing to fetch — all market caps populated.")
        con.close()
        return {"fetched": 0, "filled": 0, "failed": 0}

    # Resume from checkpoint
    done = _load_checkpoint()
    remaining = [t for t in targets if t not in done]
    print(f"  Remaining (checkpoint): {len(remaining)}/{len(targets)}")
    eta_total = len(remaining) * (1.1 + DELAY_S) / 60
    print(f"  Estimated time: ~{eta_total:.0f} min")

    fetched = filled = failed = 0
    t0 = time.time()

    for i, ticker in enumerate(remaining, 1):
        elapsed = time.time() - t0
        eta_s = (elapsed / i) * (len(remaining) - i) if i > 1 else 0
        eta_m = f"{eta_s/60:.0f}m" if eta_s > 60 else f"{eta_s:.0f}s"
        print(f"  [{i:4d}/{len(remaining)}] {ticker:8} | ETA {eta_m}", end="  ")

        result = fetch_finviz(ticker)
        fetched += 1

        if result and (result.get("market_cap") or result.get("short_float_pct")):
            write_finviz_data(con, ticker, result)
            filled += 1
            mc = result.get("market_cap")
            sf = result.get("short_float_pct")
            mc_str = f"${mc/1e9:.2f}B" if mc else "MC=--"
            sf_str = f"Short={sf:.1f}%" if sf is not None else "Short=--"
            print(f"[OK] {mc_str} {sf_str}")
        else:
            failed += 1
            print("[--] no data")

        done.add(ticker)

        if i % 50 == 0:
            _save_checkpoint(done)
            print(f"  [Checkpoint saved — {i}/{len(remaining)} done]")

        time.sleep(DELAY_S)

    _save_checkpoint(done)
    con.close()

    print(f"\n{'='*58}")
    print(f"  Done: fetched={fetched} | filled={filled} | failed={failed}")
    print(f"  Runtime: {(time.time()-t0)/60:.1f} min")
    print(f"{'='*58}")

    return {"fetched": fetched, "filled": filled, "failed": failed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Finviz market cap fetcher")
    parser.add_argument("--tickers", nargs="+", help="Explicit list of tickers")
    args = parser.parse_args()
    run(tickers=args.tickers)
