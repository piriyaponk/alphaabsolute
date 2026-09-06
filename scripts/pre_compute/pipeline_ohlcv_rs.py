"""
AlphaAbsolute — OHLCV + RS Pipeline (SQL-centric)
==================================================
Step 1: Import JSON cache -> SQLite  (instant, no API)
Step 2: Fetch missing tickers via Polygon API  (with SSL fix + rate limit)
Step 3: Compute RS from SQLite  (pure SQL/Python, no API)
Step 4: Save RS -> rs_daily table
Step 5: Theme heatmap + Mode A screening output

Run: python scripts/pre_compute/pipeline_ohlcv_rs.py
"""

import json
import os
import sys
import time
import sqlite3
import urllib3
import requests
from pathlib import Path
from datetime import date, datetime, timedelta
from collections import defaultdict

urllib3.disable_warnings()

BASE  = Path(__file__).resolve().parents[2]
DB    = BASE / "data" / "ohlcv.db"
CACHE = BASE / "data" / "ohlcv_cache"
LABELS_FILE = BASE / "data" / "themes" / "ticker_labels.json"
BENCH_FILE  = BASE / "data" / "rs_universe" / "benchmark_distribution.json"
RS_OUT      = BASE / "data" / "rs_universe" / "latest.json"
TODAY       = date.today().isoformat()

# Load .env
for ln in (BASE / ".env").read_text(encoding="utf-8-sig").splitlines():
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, v = ln.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ─── DB helpers ───────────────────────────────────────────────────────────────

def db_conn():
    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS ohlcv (
        ticker TEXT NOT NULL,
        date   TEXT NOT NULL,
        close  REAL,
        volume REAL,
        PRIMARY KEY (ticker, date)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS company_info (
        ticker       TEXT PRIMARY KEY,
        name         TEXT,
        description  TEXT,
        sic_code     TEXT,
        sector       TEXT,
        industry     TEXT,
        theme        TEXT,
        theme_source TEXT,
        last_updated TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS rs_daily (
        ticker      TEXT NOT NULL,
        date        TEXT NOT NULL,
        rs_1m_pct   REAL,
        rs_3m_pct   REAL,
        rs_6m_pct   REAL,
        rs_composite REAL,
        phase       TEXT,
        PRIMARY KEY (ticker, date)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS ticker_meta (
        ticker       TEXT PRIMARY KEY,
        first_date   TEXT,
        last_date    TEXT,
        n_bars       INTEGER,
        source       TEXT,
        last_updated TEXT,
        adtv_6m_usd  REAL,
        last_close   REAL
    )""")
    conn.commit()


# ─── Step 1: Import JSON cache -> SQLite ──────────────────────────────────────

def step1_import_cache(conn, missing_tickers: list[str]) -> int:
    print(f"\n[Step 1] Import JSON cache -> SQLite ({len(missing_tickers)} tickers)")
    imported = 0
    cache_files = {f.stem: f for f in CACHE.glob("*.json")}

    for ticker in missing_tickers:
        f = cache_files.get(ticker)
        if not f:
            continue
        try:
            d = json.loads(f.read_text())
            dates  = d.get("dates", [])
            closes = d.get("close", [])
            vols   = d.get("volume", [])
            if len(closes) < 10:
                continue
            rows = []
            for i, (dt, cl) in enumerate(zip(dates, closes)):
                vol = vols[i] if i < len(vols) else 0
                rows.append((ticker, str(dt)[:10], cl, vol))
            conn.executemany(
                "INSERT OR IGNORE INTO ohlcv (ticker, date, close, volume) VALUES (?,?,?,?)",
                rows
            )
            # Update ticker_meta
            conn.execute("""INSERT OR REPLACE INTO ticker_meta
                (ticker, first_date, last_date, n_bars, source, last_updated, last_close)
                VALUES (?,?,?,?,?,?,?)""",
                (ticker, str(dates[0])[:10], str(dates[-1])[:10],
                 len(closes), "cache", TODAY, closes[-1]))
            imported += 1
        except Exception as e:
            print(f"  WARN {ticker}: {e}")

    conn.commit()
    print(f"  Imported {imported} tickers from JSON cache")
    return imported


# ─── Step 2: Fetch missing from Polygon ───────────────────────────────────────

def _polygon_fetch(ticker: str, start: str, end: str) -> list[tuple]:
    """Returns list of (date, close, volume) tuples."""
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day"
           f"/{start}/{end}?adjusted=true&sort=asc&limit=300&apiKey={POLYGON_KEY}")
    r = requests.get(url, timeout=15, verify=False)
    if r.status_code == 429:
        print(f"  429 rate limit on {ticker}, sleeping 12s...")
        time.sleep(12)
        r = requests.get(url, timeout=15, verify=False)
    if r.status_code != 200:
        return []
    data = r.json()
    results = data.get("results", [])
    out = []
    for bar in results:
        dt = datetime.fromtimestamp(bar["t"] / 1000).strftime("%Y-%m-%d")
        out.append((dt, bar["c"], bar.get("v", 0)))
    return out


def step2_fetch_api(conn, truly_missing: list[str]) -> int:
    print(f"\n[Step 2] Fetch {len(truly_missing)} tickers from Polygon API")
    if not POLYGON_KEY:
        print("  No POLYGON_API_KEY — skipping")
        return 0

    start = (date.today() - timedelta(days=400)).isoformat()
    end   = date.today().isoformat()
    fetched = 0

    for i, ticker in enumerate(truly_missing, 1):
        try:
            bars = _polygon_fetch(ticker, start, end)
            if len(bars) < 10:
                print(f"  [{i}/{len(truly_missing)}] SKIP {ticker}: {len(bars)} bars")
                continue
            rows = [(ticker, dt, cl, vol) for dt, cl, vol in bars]
            conn.executemany(
                "INSERT OR IGNORE INTO ohlcv (ticker, date, close, volume) VALUES (?,?,?,?)",
                rows
            )
            conn.execute("""INSERT OR REPLACE INTO ticker_meta
                (ticker, first_date, last_date, n_bars, source, last_updated, last_close)
                VALUES (?,?,?,?,?,?,?)""",
                (ticker, bars[0][0], bars[-1][0], len(bars), "polygon", TODAY, bars[-1][1]))
            # Also write JSON cache
            d_out = {"ticker": ticker,
                     "dates": [b[0] for b in bars],
                     "close": [b[1] for b in bars],
                     "volume": [b[2] for b in bars]}
            (CACHE / f"{ticker}.json").write_text(json.dumps(d_out), encoding="utf-8")
            fetched += 1
            print(f"  [{i}/{len(truly_missing)}] OK {ticker}: {len(bars)} bars  last={bars[-1][0]}")
            time.sleep(0.25)  # 4 req/sec — Polygon free = 5/min on some plans
        except Exception as e:
            print(f"  [{i}/{len(truly_missing)}] FAIL {ticker}: {str(e)[:80]}")
            time.sleep(1)

    conn.commit()
    print(f"  Fetched {fetched}/{len(truly_missing)} tickers")
    return fetched


# ─── Step 3: Compute RS from SQLite ───────────────────────────────────────────

def _get_closes(conn, ticker: str, days: int = 280) -> list[float]:
    cur = conn.execute(
        "SELECT close FROM ohlcv WHERE ticker=? ORDER BY date DESC LIMIT ?",
        (ticker, days)
    )
    rows = cur.fetchall()
    return [r[0] for r in reversed(rows)]  # oldest first


def _return(closes: list[float], days: int) -> float | None:
    if len(closes) < days + 1:
        return None
    return closes[-1] / closes[-(days + 1)] - 1


def _rs_ratio(s_ret, i_ret) -> float | None:
    if s_ret is None or i_ret is None:
        return None
    return ((1 + s_ret) / (1 + i_ret) - 1) * 100  # in %


def _pct_rank(val: float, breakpoints: dict) -> float:
    if val is None:
        return 50.0
    sorted_bp = sorted(breakpoints.items(), key=lambda x: float(x[1]))
    for pct_str, threshold in sorted_bp:
        if val <= threshold:
            return float(pct_str)
    return 99.9


def step3_compute_rs(conn, universe: list[str], labels: dict) -> dict:
    print(f"\n[Step 3] Compute RS for {len(universe)} tickers from SQLite")

    # Load benchmark breakpoints
    bench = json.loads(BENCH_FILE.read_text(encoding="utf-8"))
    bp = {w: bench["distributions"][w]["breakpoints"]
          for w in ["rs_1m", "rs_3m", "rs_6m"]}

    # Get QQQ index closes
    idx_closes = _get_closes(conn, "QQQ", 280)
    if len(idx_closes) < 22:
        print("  ERROR: QQQ data insufficient in SQLite")
        return {}
    print(f"  QQQ: {len(idx_closes)} bars in SQLite")

    WINDOWS = [
        ("1m",  21,  "rs_1m"),
        ("3m",  63,  "rs_3m"),
        ("6m",  126, "rs_6m"),
        ("12m", 252, "rs_6m"),   # use rs_6m breakpoints as proxy
    ]

    results = {}
    skipped = 0

    for ticker in universe:
        closes = _get_closes(conn, ticker, 280)
        if len(closes) < 22:
            skipped += 1
            continue

        row = {
            "ticker": ticker,
            "label":  labels.get(ticker, "Unknown"),
            "price":  round(closes[-1], 2),
            "bars":   len(closes),
        }
        for w_name, days, bench_key in WINDOWS:
            s_ret = _return(closes, days)
            i_ret = _return(idx_closes, days)
            raw   = _rs_ratio(s_ret, i_ret)
            pct   = round(_pct_rank(raw, bp[bench_key]), 1) if raw is not None else None
            row[f"rs_raw_{w_name}"]  = round(raw, 2) if raw is not None else None
            row[f"rs_pct_{w_name}"]  = pct

        p1 = row.get("rs_pct_1m") or 0
        p3 = row.get("rs_pct_3m") or 0
        p6 = row.get("rs_pct_6m") or 0
        row["rs_momentum_1m_3m"] = round(p1 - p3, 1)
        row["rs_composite"]      = round((p1 * 0.35 + p3 * 0.45 + p6 * 0.20), 1)

        if p3 >= 80 and p6 >= 70:   row["phase"] = "Leader"
        elif p3 >= 60:               row["phase"] = "Emerging"
        elif p3 >= 40:               row["phase"] = "Recovering"
        else:                        row["phase"] = "Weak"

        results[ticker] = row

    print(f"  Computed: {len(results)} | Skipped: {skipped}")
    return results


# ─── Step 4: Save RS -> rs_daily table ────────────────────────────────────────

def step4_save_rs(conn, results: dict):
    print(f"\n[Step 4] Save {len(results)} RS records -> rs_daily")
    rows = []
    for r in results.values():
        rows.append((
            r["ticker"], TODAY,
            r.get("rs_pct_1m"),
            r.get("rs_pct_3m"),
            r.get("rs_pct_6m"),
            r.get("rs_composite"),
            r.get("phase"),
        ))
    conn.executemany("""INSERT OR REPLACE INTO rs_daily
        (ticker, date, rs_1m_pct, rs_3m_pct, rs_6m_pct, rs_composite, phase)
        VALUES (?,?,?,?,?,?,?)""", rows)
    conn.commit()

    # Also save latest.json for other scripts that read it
    ranked = sorted(results.values(),
                    key=lambda x: (x.get("rs_composite") or 0), reverse=True)
    out = {
        "date": TODAY,
        "generated_at": datetime.now().strftime("%H:%M"),
        "total_ranked": len(ranked),
        "ranked": ranked,
        "universe": results,
    }
    RS_OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved rs_daily + latest.json")


# ─── Step 5: Print screening results ──────────────────────────────────────────

THEMES = [
    "AI_Related", "Memory_HBM", "Space", "Quantum", "Photonics", "DefenseTech",
    "DataCenter", "Nuclear_SMR", "NeoCloud", "AI_Infra", "DataCenter_Infra",
    "Drone_UAV", "Robotics", "Connectivity",
]

def step5_screen(conn, labels: dict):
    print(f"\n[Step 5] Screening from rs_daily (date={TODAY})")

    cur = conn.execute("""
        SELECT r.ticker, c.theme, r.rs_1m_pct, r.rs_3m_pct, r.rs_6m_pct,
               r.rs_composite, r.phase, m.last_close
        FROM rs_daily r
        LEFT JOIN company_info c ON r.ticker = c.ticker
        LEFT JOIN ticker_meta  m ON r.ticker = m.ticker
        WHERE r.date = ?
        ORDER BY r.rs_composite DESC
    """, (TODAY,))
    rows = cur.fetchall()
    print(f"  Total RS records today: {len(rows)}")

    # Theme heatmap
    theme_data = defaultdict(list)
    for ticker, theme, p1, p3, p6, comp, phase, price in rows:
        if theme in THEMES and p3 is not None:
            theme_data[theme].append({"ticker": ticker, "p3": p3, "comp": comp or 0})

    print(f"\n{'Theme':<22} {'AvgRS3M':>8} {'N':>5} {'Leaders':>8} {'Status'}")
    print("-" * 55)
    theme_summary = []
    for theme in THEMES:
        items = theme_data.get(theme, [])
        if not items:
            continue
        avg = sum(x["p3"] for x in items) / len(items)
        ldrs = sum(1 for x in items if x["p3"] >= 70)
        status = "HOT" if avg >= 75 else ("WARM" if avg >= 55 else "WEAK")
        theme_summary.append((theme, avg, len(items), ldrs, status))

    theme_summary.sort(key=lambda x: -x[1])
    for theme, avg, n, ldrs, status in theme_summary:
        icon = "  HOT" if status == "HOT" else (" WARM" if status == "WARM" else " WEAK")
        print(f"  {theme:<22} {avg:>7.1f} {n:>5} {ldrs:>8}    {icon}")

    # Mode A Gate: RS3M >= 70 AND RS6M >= 70, theme only
    mode_a = [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7])
              for r in rows
              if r[1] in THEMES
              and (r[3] or 0) >= 70
              and (r[4] or 0) >= 70]

    print(f"\n{'='*70}")
    print(f"MODE A CANDIDATES (RS3M>=70 AND RS6M>=70): {len(mode_a)} theme stocks")
    print(f"{'Ticker':<8} {'Theme':<22} {'1M':>5} {'3M':>5} {'6M':>5} {'Comp':>6} {'Phase':<12} {'Price':>8}")
    print("-" * 75)

    prev = None
    for ticker, theme, p1, p3, p6, comp, phase, price in mode_a:
        if theme != prev:
            print(f"\n  --- {theme} ---")
            prev = theme
        p1s  = f"{p1:.0f}" if p1 else "-"
        p3s  = f"{p3:.0f}" if p3 else "-"
        p6s  = f"{p6:.0f}" if p6 else "-"
        cs   = f"{comp:.1f}" if comp else "-"
        prc  = f"${price:.2f}" if price else "-"
        print(f"  {ticker:<8} {theme:<22} {p1s:>5} {p3s:>5} {p6s:>5} {cs:>6} {phase or '-':<12} {prc:>8}")


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"AlphaAbsolute — OHLCV + RS Pipeline  [{TODAY}]")
    print("=" * 60)

    # Load universe
    labels_data = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    labels = labels_data["labels"]
    universe = sorted(labels.keys())
    print(f"Universe: {len(universe)} tickers (from ticker_labels.json)")

    conn = db_conn()
    ensure_schema(conn)

    # Find what's missing from SQLite
    placeholders = ",".join("?" * len(universe))
    cur = conn.execute(
        f"SELECT ticker FROM ohlcv WHERE ticker IN ({placeholders}) GROUP BY ticker",
        universe
    )
    in_db = {r[0] for r in cur.fetchall()}
    missing_from_db = [t for t in universe if t not in in_db]

    in_cache  = [t for t in missing_from_db if (CACHE / f"{t}.json").exists()]
    need_api  = [t for t in missing_from_db if t not in in_cache]

    print(f"In SQLite: {len(in_db)} | In cache (to import): {len(in_cache)} | Need API: {len(need_api)}")

    # Step 1: Import cache -> SQLite
    if in_cache:
        step1_import_cache(conn, in_cache)

    # Step 2: Fetch from Polygon
    if need_api:
        step2_fetch_api(conn, need_api)

    # Step 3: Compute RS (from SQLite — no API needed)
    results = step3_compute_rs(conn, universe, labels)

    # Step 4: Save to rs_daily
    if results:
        step4_save_rs(conn, results)

    # Step 5: Screen + display
    step5_screen(conn, labels)

    conn.close()
    print(f"\nDone. {len(results)} tickers ranked and saved to rs_daily + latest.json")


if __name__ == "__main__":
    main()
