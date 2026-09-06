"""
AlphaAbsolute — StockAnalysis.com Fundamentals Fetcher
=======================================================
Fills gaps in fundamentals_summary for tickers where EDGAR XBRL failed:
  - gm_latest, gm_1q_ago ... gm_4q_ago, gm_trend  (Gross Margin quarterly)
  - rev_yoy_q1, rev_yoy_q2, rev_yoy_q3            (quarterly revenue YoY %)
  - rev_yoy_pct                                    (TTM / most-recent quarter)
  - eps_yoy_pct, eps_latest                        (EPS quarterly)

Source: https://stockanalysis.com/stocks/{ticker}/financials/?p=quarterly
Robots.txt: Fully open (User-agent: * → Disallow: blank). Assessed 2026-06-01 by DE Team.

Cache: 7-day TTL matching data_engine.py pattern.
Rate:  0.5s delay + exponential backoff on 429.
Run:   Weekly in pre_market_runner.py (MORNING COMPUTATION block).

Usage:
  python scripts/pre_compute/stockanalysis_fetcher.py
  python scripts/pre_compute/stockanalysis_fetcher.py --missing-only
  python scripts/pre_compute/stockanalysis_fetcher.py --tickers NVDA AAPL MSFT
"""

import sqlite3
import json
import time
import re
import sys
import argparse
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE  = Path(__file__).resolve()
ROOT   = _HERE.parent.parent.parent
DB_PATH = ROOT / "data" / "ohlcv.db"
CKPT_FILE = ROOT / "data" / "sa_fetcher_checkpoint.json"

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
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.verify = False  # WARP-hardened (consistent with data_engine.py)

import urllib3
urllib3.disable_warnings()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_pct(s: str) -> Optional[float]:
    """Parse '85.23%' or '-3.12%' or 'N/A' → float or None."""
    if not s or s.strip() in ("—", "N/A", "-", ""):
        return None
    s = s.strip().replace("%", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_value(s: str) -> Optional[float]:
    """Parse '81,615' (millions) or 'N/A' → float in millions."""
    if not s or s.strip() in ("—", "N/A", "-", ""):
        return None
    s = s.strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _gm_trend(vals: list) -> str:
    """Compute GM trend from up to 5 quarterly values (most-recent first)."""
    clean = [v for v in vals if v is not None]
    if len(clean) < 2:
        return "N/A"
    # Use first 3 values if available
    last = clean[0]
    prev = clean[1]
    if len(clean) >= 3:
        oldest = clean[2]
        if last >= prev >= oldest:
            return "Expanding"
        elif last <= prev <= oldest:
            return "Contracting"
        else:
            return "Stable"
    else:
        if last > prev + 0.5:
            return "Expanding"
        elif last < prev - 0.5:
            return "Contracting"
        return "Stable"


def fetch_stockanalysis(ticker: str) -> Optional[dict]:
    """
    Fetch quarterly income statement from StockAnalysis.com.
    Returns dict with parsed fundamentals or None on failure.
    """
    url = f"https://stockanalysis.com/stocks/{ticker.lower()}/financials/?p=quarterly"
    backoff = 1.0

    for attempt in range(MAX_RETRIES):
        try:
            r = SESSION.get(url, timeout=12)
            if r.status_code == 404:
                return None  # ticker not on StockAnalysis
            if r.status_code == 429:
                wait = backoff * (2 ** attempt)
                print(f"    [{ticker}] 429 rate limit — sleeping {wait:.0f}s")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                print(f"    [{ticker}] HTTP {r.status_code} — skip")
                return None

            soup = BeautifulSoup(r.text, "html.parser")
            tables = soup.find_all("table")
            if not tables:
                print(f"    [{ticker}] No tables found — parser may be broken")
                return None

            # Find the income statement table (first large table with financials)
            tbl = None
            for t in tables:
                rows = t.find_all("tr")
                if len(rows) >= 10:
                    tbl = t
                    break

            if tbl is None:
                print(f"    [{ticker}] Income statement table not found")
                return None

            # Guard: if table has fewer than 10 rows, likely parser broken
            rows = tbl.find_all("tr")
            if len(rows) < 10:
                print(f"    [{ticker}] Table too small ({len(rows)} rows) — PARSER_BROKEN")
                return None

            # Build row-label → [col1, col2, col3, col4, col5] map
            row_map: dict[str, list] = {}
            for row in rows:
                cells = row.find_all(["td", "th"])
                if not cells:
                    continue
                label = cells[0].get_text(strip=True)
                vals  = [c.get_text(strip=True) for c in cells[1:]]
                if label:
                    row_map[label] = vals

            def get_col(label: str, col: int = 0) -> Optional[str]:
                row = row_map.get(label, [])
                if col < len(row):
                    return row[col]
                return None

            # ── Extract fields ────────────────────────────────────────────────
            # Revenue Growth (YoY) → rev_yoy_q1 (col 0 = most recent quarter)
            rev_yoy_q1  = _parse_pct(get_col("Revenue Growth (YoY)", 0))
            rev_yoy_q2  = _parse_pct(get_col("Revenue Growth (YoY)", 1))
            rev_yoy_q3  = _parse_pct(get_col("Revenue Growth (YoY)", 2))
            rev_yoy_pct = rev_yoy_q1  # use most-recent quarter as primary

            # Gross Margin % (quarterly)
            gm_latest  = _parse_pct(get_col("Gross Margin", 0))
            gm_1q_ago  = _parse_pct(get_col("Gross Margin", 1))
            gm_2q_ago  = _parse_pct(get_col("Gross Margin", 2))
            gm_3q_ago  = _parse_pct(get_col("Gross Margin", 3))
            gm_4q_ago  = _parse_pct(get_col("Gross Margin", 4))
            gm_trend   = _gm_trend([gm_latest, gm_1q_ago, gm_2q_ago, gm_3q_ago, gm_4q_ago])

            # EPS Growth (YoY) + EPS (Diluted)
            eps_yoy_pct = _parse_pct(get_col("EPS Growth", 0))
            eps_latest  = _parse_value(get_col("EPS (Diluted)", 0))

            # Revenue (most recent quarter, in millions)
            rev_latest = _parse_value(get_col("Revenue", 0))

            # Rev acceleration label
            if rev_yoy_q1 is not None and rev_yoy_q2 is not None:
                if rev_yoy_q1 > rev_yoy_q2:
                    rev_inflection = "ACCELERATING"
                elif rev_yoy_q1 < rev_yoy_q2 - 5:
                    rev_inflection = "DECELERATING"
                else:
                    rev_inflection = "STABLE"
            else:
                rev_inflection = None

            # Gate flags (same logic as data_engine.py)
            gate_eps = 1 if (eps_yoy_pct is not None and eps_yoy_pct >= 25) else \
                       (0 if eps_yoy_pct is not None else -1)
            gate_rev = 1 if (rev_yoy_q1 is not None and rev_yoy_q1 >= 25) else \
                       (0 if rev_yoy_q1 is not None else -1)
            gate_gm  = 1 if gm_trend in ("Expanding", "Stable") else \
                       (0 if gm_trend == "Contracting" else -1)

            # Skip if we got nothing useful
            if gm_latest is None and rev_yoy_q1 is None and eps_yoy_pct is None:
                return None

            return {
                "rev_latest":         rev_latest,
                "rev_yoy_pct":        rev_yoy_pct,
                "rev_yoy_q1":         rev_yoy_q1,
                "rev_yoy_q2":         rev_yoy_q2,
                "rev_yoy_q3":         rev_yoy_q3,
                "gm_latest":          gm_latest,
                "gm_1q_ago":          gm_1q_ago,
                "gm_2q_ago":          gm_2q_ago,
                "gm_3q_ago":          gm_3q_ago,
                "gm_4q_ago":          gm_4q_ago,
                "gm_trend":           gm_trend,
                "gm_prev":            gm_1q_ago,
                "eps_latest":         eps_latest,
                "eps_yoy_pct":        eps_yoy_pct,
                "eps_acceleration":   None,
                "rev_acceleration":   None,
                "rev_inflection_label": rev_inflection,
                "gate_eps":           gate_eps,
                "gate_rev":           gate_rev,
                "gate_gm":            gate_gm,
                "source":             "stockanalysis",
                "last_fetched":       date.today().isoformat(),
                "last_updated":       date.today().isoformat(),
                "latest_period":      None,
                "n_quarters":         sum(1 for v in [gm_latest, gm_1q_ago, gm_2q_ago, gm_3q_ago]
                                         if v is not None),
            }

        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(backoff)
            else:
                print(f"    [{ticker}] Request failed: {e}")
                return None

    return None


def write_to_db(con: sqlite3.Connection, ticker: str, data: dict) -> None:
    """Upsert fundamentals_summary — only overwrite NULL fields unless source=stockanalysis."""
    cur = con.cursor()

    # Check if row exists
    cur.execute("SELECT source FROM fundamentals_summary WHERE ticker = ?", (ticker,))
    existing = cur.fetchone()

    if existing is None:
        # Insert new row
        fields = ["ticker"] + list(data.keys())
        placeholders = ",".join("?" * len(fields))
        values = [ticker] + list(data.values())
        cur.execute(
            f"INSERT OR IGNORE INTO fundamentals_summary ({','.join(fields)}) "
            f"VALUES ({placeholders})",
            values
        )
    else:
        # Update: only write fields that are currently NULL (don't overwrite good EDGAR data)
        # Exception: if existing source is 'none' or 'finnhub', overwrite everything
        existing_source = existing[0] or "none"
        if existing_source in ("none", "finnhub", "stockanalysis"):
            # Overwrite all
            sets = ", ".join(f"{k} = ?" for k in data.keys())
            vals = list(data.values()) + [ticker]
            cur.execute(f"UPDATE fundamentals_summary SET {sets} WHERE ticker = ?", vals)
        else:
            # EDGAR data exists — only fill NULL columns
            null_updates = {}
            for col, val in data.items():
                if val is None:
                    continue
                cur.execute(f"SELECT {col} FROM fundamentals_summary WHERE ticker = ?", (ticker,))
                row = cur.fetchone()
                if row and row[0] is None:
                    null_updates[col] = val
            if null_updates:
                sets = ", ".join(f"{k} = ?" for k in null_updates.keys())
                vals = list(null_updates.values()) + [ticker]
                cur.execute(f"UPDATE fundamentals_summary SET {sets} WHERE ticker = ?", vals)

    con.commit()


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

def run(missing_only: bool = True, tickers: list = None) -> dict:
    print(f"\n{'='*58}")
    print(f"  StockAnalysis Fetcher  [{date.today().isoformat()}]")
    print(f"{'='*58}")

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    if tickers:
        # Explicit list
        targets = [t.upper() for t in tickers]
        print(f"  Mode: explicit list — {len(targets)} tickers")
    elif missing_only:
        # Only tickers missing gm_latest OR rev_yoy_q1
        # Also skip tickers already fetched within TTL
        cutoff = date.today().toordinal() - CACHE_TTL_DAYS
        cur.execute("""
            SELECT ticker FROM fundamentals_summary
            WHERE (gm_latest IS NULL OR rev_yoy_q1 IS NULL)
              AND (last_fetched IS NULL
                   OR julianday(last_fetched) < julianday(date('now', ?)))
            ORDER BY ticker
        """, (f"-{CACHE_TTL_DAYS} days",))
        targets = [r[0] for r in cur.fetchall()]
        print(f"  Mode: missing-only — {len(targets)} tickers to fill")
    else:
        # All tickers in RS universe
        cur.execute("SELECT DISTINCT ticker FROM fundamentals_summary ORDER BY ticker")
        targets = [r[0] for r in cur.fetchall()]
        print(f"  Mode: full universe — {len(targets)} tickers")

    if not targets:
        print("  Nothing to fetch — all data fresh within TTL.")
        con.close()
        return {"fetched": 0, "filled": 0, "failed": 0}

    # Resume from checkpoint
    done = _load_checkpoint()
    remaining = [t for t in targets if t not in done]
    print(f"  Remaining (checkpoint): {len(remaining)}/{len(targets)}")

    fetched = filled = failed = 0
    t0 = time.time()

    for i, ticker in enumerate(remaining, 1):
        elapsed = time.time() - t0
        eta_s = (elapsed / i) * (len(remaining) - i) if i > 1 else 0
        eta_m = f"{eta_s/60:.0f}m" if eta_s > 60 else f"{eta_s:.0f}s"
        print(f"  [{i:4d}/{len(remaining)}] {ticker:8} | ETA {eta_m}", end="  ")

        data = fetch_stockanalysis(ticker)
        fetched += 1

        if data:
            write_to_db(con, ticker, data)
            filled += 1
            gm_str = f"GM={data['gm_latest']:.1f}%" if data['gm_latest'] else "GM=—"
            rev_str = f"Rev={data['rev_yoy_q1']:.1f}%" if data['rev_yoy_q1'] else "Rev=—"
            print(f"[OK] {gm_str} {rev_str}")
        else:
            failed += 1
            print("[--] no data")

        done.add(ticker)

        # Checkpoint every 50 tickers
        if i % 50 == 0:
            _save_checkpoint(done)
            print(f"  [Checkpoint saved — {i}/{len(remaining)} done]")

        time.sleep(DELAY_S)

    _save_checkpoint(done)
    con.close()

    # Summary
    print(f"\n{'='*58}")
    print(f"  Done: fetched={fetched} | filled={filled} | failed={failed}")
    print(f"  Runtime: {(time.time()-t0)/60:.1f} min")
    print(f"{'='*58}")

    return {"fetched": fetched, "filled": filled, "failed": failed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="StockAnalysis.com fundamentals fetcher")
    parser.add_argument("--all", action="store_true", help="Fetch all tickers (not just missing)")
    parser.add_argument("--missing-only", action="store_true", default=True,
                        help="Only fetch tickers with missing gm_latest or rev_yoy_q1 (default)")
    parser.add_argument("--tickers", nargs="+", help="Explicit list of tickers")
    args = parser.parse_args()

    run(
        missing_only=(not args.all),
        tickers=args.tickers,
    )
