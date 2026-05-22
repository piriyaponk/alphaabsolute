"""
AlphaAbsolute -- Fundamentals Pipeline (SQL-centric v2)
=======================================================
Fetches quarterly EPS / Revenue / Gross Margin using data_engine.get_fundamentals().
Source priority: EDGAR (free, authoritative) -> FMP -> Finnhub -> Yahoo.
Stores raw + computed metrics in SQLite. Updates Mode A gate flags.

Priority: Mode A 3-gate candidates first -> rest of universe

Run: python scripts/pre_compute/pipeline_fundamentals.py
     python scripts/pre_compute/pipeline_fundamentals.py --all   (fetch all 1325)

Tables created/updated:
  fundamentals_summary -- computed YoY/GM metrics per ticker
"""

import sys
import os
import sqlite3
import json
import time
import argparse
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'utils'))

import warnings
warnings.filterwarnings('ignore')

try:
    import data_engine
    DATA_ENGINE_OK = True
except Exception as e:
    print(f"WARNING: data_engine import failed: {e}")
    DATA_ENGINE_OK = False

DB_PATH = "data/ohlcv.db"
LABELS_PATH = "data/themes/ticker_labels.json"

CACHE_DAYS = 7          # re-fetch if older than this
SLEEP_BETWEEN = 0.5     # seconds between API calls
MAX_PER_RUN = 300       # safety cap (EDGAR is free, so can go higher)


def setup_tables(conn):
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals_summary (
            ticker TEXT PRIMARY KEY,
            source TEXT,
            -- Latest quarter metrics
            eps_latest REAL,
            eps_yoy_pct REAL,
            rev_latest REAL,
            rev_yoy_pct REAL,
            -- Gross margin trend
            gm_latest REAL,
            gm_prev REAL,
            gm_2q_ago REAL,
            gm_trend TEXT,           -- EXPANDING | STABLE | CONTRACTING | N/A
            -- Trend labels
            eps_acceleration TEXT,   -- ACCELERATING | DECELERATING | STABLE | TURNAROUND | EARLY | N/A
            rev_acceleration TEXT,
            -- Mode A gate results
            gate_eps INTEGER DEFAULT -1,   -- 1=pass(>25%), 0=fail, -1=no data
            gate_rev INTEGER DEFAULT -1,
            gate_gm INTEGER DEFAULT -1,    -- 1=pass(stable/expanding), 0=fail
            -- Meta
            latest_period TEXT,
            n_quarters INTEGER,
            last_fetched TEXT,
            last_updated TEXT
        )
    """)
    conn.commit()


def get_cached_tickers(conn):
    cutoff = (datetime.now() - timedelta(days=CACHE_DAYS)).isoformat()
    c = conn.cursor()
    c.execute("SELECT ticker FROM fundamentals_summary WHERE last_fetched > ?", (cutoff,))
    return {r[0] for r in c.fetchall()}


def compute_gm_trend(gm_history):
    """Compute GM trend from list of gross margin % values (newest first)."""
    valid = [g for g in gm_history if g is not None]
    if len(valid) < 2:
        return "N/A", valid[0] if valid else None, None, None

    gm_latest = valid[0]
    gm_prev = valid[1] if len(valid) > 1 else None
    gm_2q = valid[2] if len(valid) > 2 else None

    # Compare latest vs oldest available
    gm_ref = valid[-1] if len(valid) >= 3 else valid[-1]
    delta = gm_latest - gm_ref

    if delta > 1.5:
        trend = "EXPANDING"
    elif delta < -1.5:
        trend = "CONTRACTING"
    else:
        trend = "STABLE"

    return trend, gm_latest, gm_prev, gm_2q


def eps_label(eps_yoy, eps_latest, eps_hist):
    """Classify EPS acceleration."""
    if eps_yoy is None:
        return "N/A"
    # Check turnaround
    if eps_hist and len(eps_hist) >= 2:
        prev = eps_hist[1].get('eps', 0) if isinstance(eps_hist[1], dict) else eps_hist[1]
        if prev is not None and prev < 0 and eps_latest is not None and eps_latest > 0:
            return "TURNAROUND"
    if eps_yoy > 50:
        return "ACCELERATING"
    if eps_yoy > 25:
        return "ACCELERATING"
    if eps_yoy > 0:
        return "EARLY"
    if eps_yoy < 0:
        return "DECELERATING"
    return "STABLE"


def process_ticker(conn, ticker):
    """Fetch fundamentals for one ticker and store in SQLite.
    Returns (success, gate_eps, gate_rev, gate_gm).
    """
    result = data_engine.get_fundamentals(ticker, quarters=8)

    if not result or not result.get('eps_history'):
        return False, -1, -1, -1

    now = datetime.now().isoformat()

    # EPS YoY
    eps_yoy = result.get('eps_yoy_pct')
    eps_latest = result.get('latest_eps')
    eps_hist = result.get('eps_history', [])

    # Rev YoY
    rev_yoy = result.get('rev_yoy_pct')
    rev_latest = result.get('latest_revenue')

    # Gross margin
    gm_hist = result.get('gross_margin_history', [])
    gm_values = []
    for gm in gm_hist:
        if isinstance(gm, dict):
            gm_values.append(gm.get('gross_margin'))
        elif isinstance(gm, (int, float)):
            gm_values.append(float(gm))

    gm_trend, gm_latest, gm_prev, gm_2q = compute_gm_trend(gm_values)

    # Labels
    eps_acc = eps_label(eps_yoy, eps_latest, eps_hist)
    rev_acc = eps_label(rev_yoy, rev_latest, result.get('revenue_history', []))

    # Gate results
    gate_eps = -1
    if eps_yoy is not None:
        gate_eps = 1 if eps_yoy > 25 else 0

    gate_rev = -1
    if rev_yoy is not None:
        gate_rev = 1 if rev_yoy > 25 else 0

    gate_gm = -1
    if gm_trend in ("EXPANDING", "STABLE"):
        gate_gm = 1
    elif gm_trend == "CONTRACTING":
        gate_gm = 0

    # Latest period
    latest_period = None
    if eps_hist and isinstance(eps_hist[0], dict):
        latest_period = eps_hist[0].get('quarter')

    c = conn.cursor()
    c.execute("""
        INSERT INTO fundamentals_summary (
            ticker, source,
            eps_latest, eps_yoy_pct, rev_latest, rev_yoy_pct,
            gm_latest, gm_prev, gm_2q_ago, gm_trend,
            eps_acceleration, rev_acceleration,
            gate_eps, gate_rev, gate_gm,
            latest_period, n_quarters,
            last_fetched, last_updated
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(ticker) DO UPDATE SET
            source = excluded.source,
            eps_latest = excluded.eps_latest,
            eps_yoy_pct = excluded.eps_yoy_pct,
            rev_latest = excluded.rev_latest,
            rev_yoy_pct = excluded.rev_yoy_pct,
            gm_latest = excluded.gm_latest,
            gm_prev = excluded.gm_prev,
            gm_2q_ago = excluded.gm_2q_ago,
            gm_trend = excluded.gm_trend,
            eps_acceleration = excluded.eps_acceleration,
            rev_acceleration = excluded.rev_acceleration,
            gate_eps = excluded.gate_eps,
            gate_rev = excluded.gate_rev,
            gate_gm = excluded.gate_gm,
            latest_period = excluded.latest_period,
            n_quarters = excluded.n_quarters,
            last_fetched = excluded.last_fetched,
            last_updated = excluded.last_updated
    """, (
        ticker, result.get('source', 'unknown'),
        eps_latest, eps_yoy, rev_latest, rev_yoy,
        gm_latest, gm_prev, gm_2q, gm_trend,
        eps_acc, rev_acc,
        gate_eps, gate_rev, gate_gm,
        latest_period, len(eps_hist),
        now, now
    ))
    conn.commit()
    return True, gate_eps, gate_rev, gate_gm


def mark_no_data(conn, ticker):
    """Save empty record so we don't retry until cache expires."""
    now = datetime.now().isoformat()
    c = conn.cursor()
    c.execute("""
        INSERT INTO fundamentals_summary (ticker, source, gate_eps, gate_rev, gate_gm, last_fetched, last_updated)
        VALUES (?, 'none', -1, -1, -1, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            source = 'none',
            last_fetched = excluded.last_fetched,
            last_updated = excluded.last_updated
    """, (ticker, now, now))
    conn.commit()


def update_screening_gates(conn, universe, screen_date):
    """Propagate fundamental gate results into screening_results table."""
    placeholders = ','.join(['?' for _ in universe])
    c = conn.cursor()

    c.execute(f"""
        UPDATE screening_results
        SET
            gate_eps = COALESCE((SELECT gate_eps FROM fundamentals_summary WHERE ticker = screening_results.ticker), -1),
            gate_rev = COALESCE((SELECT gate_rev FROM fundamentals_summary WHERE ticker = screening_results.ticker), -1),
            gate_gm  = COALESCE((SELECT gate_gm  FROM fundamentals_summary WHERE ticker = screening_results.ticker), -1),
            gates_passed = (
                COALESCE(gate_rs, 0) +
                COALESCE(gate_adtv, 0) +
                COALESCE(gate_52w, 0) +
                COALESCE((SELECT gate_eps FROM fundamentals_summary WHERE ticker = screening_results.ticker), 0) +
                COALESCE((SELECT gate_rev FROM fundamentals_summary WHERE ticker = screening_results.ticker), 0) +
                COALESCE((SELECT gate_gm  FROM fundamentals_summary WHERE ticker = screening_results.ticker), 0)
            )
        WHERE SUBSTR(date, 1, 10) = ? AND ticker IN ({placeholders})
    """, [screen_date] + universe)
    conn.commit()

    # Count passes
    c.execute(f"""
        SELECT COUNT(*) FROM screening_results
        WHERE SUBSTR(date,1,10) = ? AND gates_passed = 6
          AND ticker IN ({placeholders})
    """, [screen_date] + universe)
    return c.fetchone()[0]


def print_full_mode_a(conn, universe, screen_date):
    """Print and save full Mode A screening results (all 6 gates)."""
    placeholders = ','.join(['?' for _ in universe])
    c = conn.cursor()

    c.execute(f"""
        SELECT s.ticker, s.label, s.rs_3m_pct, s.rs_6m_pct, s.rs_composite, s.phase,
               s.adtv_6m_usd, s.pct_from_52w_high, s.last_close,
               f.eps_yoy_pct, f.rev_yoy_pct, f.gm_trend, f.gm_latest,
               f.source, s.gates_passed
        FROM screening_results s
        LEFT JOIN fundamentals_summary f ON s.ticker = f.ticker
        WHERE SUBSTR(s.date,1,10) = ? AND s.gates_passed = 6
          AND s.ticker IN ({placeholders})
        ORDER BY s.rs_composite DESC
    """, [screen_date] + universe)

    rows = c.fetchall()

    print(f"\n{'='*80}")
    print(f"FULL MODE A -- ALL 6 GATES PASS -- {screen_date}")
    print(f"{'='*80}")
    if rows:
        print(f"{'#':<3} {'Ticker':<7} {'Label':<18} {'RS3M':>5} {'RS6M':>5} {'Comp':>5} "
              f"{'EPS%':>6} {'Rev%':>6} {'GM%':>5} {'GMT':>4} {'52W%':>7} {'ADTV':>7}")
        print(f"  {'-'*82}")
        for i, row in enumerate(rows, 1):
            ticker, label, rs3, rs6, comp, phase, adtv, pct52, close, eps_y, rev_y, gm_t, gm_l, src, gates = row
            label_s = (label or "?")[:18]
            rs3_s = f"{rs3:.0f}" if rs3 else "?"
            rs6_s = f"{rs6:.0f}" if rs6 else "?"
            comp_s = f"{comp:.0f}" if comp else "?"
            eps_s = f"{eps_y:+.0f}%" if eps_y is not None else "N/A"
            rev_s = f"{rev_y:+.0f}%" if rev_y is not None else "N/A"
            gm_s = f"{gm_l:.0f}%" if gm_l is not None else "N/A"
            gm_t_s = (gm_t or "?")[:4]
            p52_s = f"{pct52:.1f}%" if pct52 is not None else "N/A"
            adtv_s = f"${adtv/1e6:.0f}M" if adtv else "N/A"
            print(f"  {i:<3} {ticker:<7} {label_s:<18} {rs3_s:>5} {rs6_s:>5} {comp_s:>5} "
                  f"{eps_s:>6} {rev_s:>6} {gm_s:>5} {gm_t_s:>4} {p52_s:>7} {adtv_s:>7}")
    else:
        print("  No tickers pass all 6 gates yet.")

    # Also show 5-gate (need only 1 more)
    c.execute(f"""
        SELECT s.ticker, s.label, s.rs_composite,
               s.gate_rs, s.gate_adtv, s.gate_52w,
               COALESCE(f.gate_eps,-1) as geps,
               COALESCE(f.gate_rev,-1) as grev,
               COALESCE(f.gate_gm,-1) as ggm,
               f.eps_yoy_pct, f.rev_yoy_pct, f.gm_trend
        FROM screening_results s
        LEFT JOIN fundamentals_summary f ON s.ticker = f.ticker
        WHERE SUBSTR(s.date,1,10) = ? AND s.gates_passed = 5
          AND s.ticker IN ({placeholders})
        ORDER BY s.rs_composite DESC
        LIMIT 20
    """, [screen_date] + universe)

    rows5 = c.fetchall()
    print(f"\n--- 5/6 Gates (one gate missing) --- top 20")
    if rows5:
        for row in rows5:
            ticker, label, comp, g_rs, g_adtv, g_52w, g_eps, g_rev, g_gm, eps_y, rev_y, gm_t = row
            missing = []
            if not g_rs: missing.append("RS")
            if not g_adtv: missing.append("ADTV")
            if not g_52w: missing.append("52W")
            if g_eps != 1: missing.append(f"EPS({eps_y:+.0f}%)" if eps_y else "EPS=NoData")
            if g_rev != 1: missing.append(f"Rev({rev_y:+.0f}%)" if rev_y else "Rev=NoData")
            if g_gm != 1: missing.append(f"GM({gm_t})")
            label_s = (label or "?")[:16]
            comp_s = f"{comp:.0f}" if comp else "?"
            print(f"  {ticker:<7} {label_s:<16} RS={comp_s} | Missing: {', '.join(missing)}")

    # Save JSON
    os.makedirs("data/screening", exist_ok=True)
    output = {
        "date": screen_date,
        "generated_at": datetime.now().isoformat(),
        "full_mode_a_count": len(rows),
        "gates": {
            "1_rs": "RS3M>=70 AND RS6M>=70",
            "2_eps": "EPS YoY > 25%",
            "3_rev": "Revenue YoY > 25%",
            "4_gm": "Gross margin stable or expanding",
            "6_52w": "pct_from_52w_high > -20%",
            "7_adtv": "ADTV > $10M"
        },
        "candidates": [
            {
                "rank": i + 1,
                "ticker": r[0],
                "label": r[1],
                "rs_3m": round(r[2], 1) if r[2] else None,
                "rs_6m": round(r[3], 1) if r[3] else None,
                "rs_composite": round(r[4], 1) if r[4] else None,
                "phase": r[5],
                "adtv_m": round(r[6] / 1e6, 1) if r[6] else None,
                "pct_52w_high": round(r[7], 1) if r[7] is not None else None,
                "last_close": round(r[8], 2) if r[8] else None,
                "eps_yoy_pct": round(r[9], 1) if r[9] is not None else None,
                "rev_yoy_pct": round(r[10], 1) if r[10] is not None else None,
                "gm_trend": r[11],
                "gm_pct": round(r[12], 1) if r[12] is not None else None,
                "data_source": r[13],
            }
            for i, r in enumerate(rows)
        ]
    }
    with open("data/screening/mode_a_full_latest.json", 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved to data/screening/mode_a_full_latest.json")
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--all', action='store_true', help='Fetch all universe tickers (not just P1)')
    parser.add_argument('--force', action='store_true', help='Ignore cache, re-fetch everything')
    parser.add_argument('--max', type=int, default=MAX_PER_RUN, help=f'Max tickers to fetch (default {MAX_PER_RUN})')
    args = parser.parse_args()

    print("AlphaAbsolute -- Fundamentals Pipeline v2")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if not DATA_ENGINE_OK:
        print("ERROR: data_engine not available")
        return

    with open(LABELS_PATH) as f:
        labels_data = json.load(f)
    labels = labels_data['labels']
    universe = list(labels.keys())
    print(f"Universe: {len(universe)} tickers")

    conn = sqlite3.connect(DB_PATH)
    setup_tables(conn)

    cached = set() if args.force else get_cached_tickers(conn)
    print(f"Cached (< {CACHE_DAYS}d): {len(cached)}")

    # Get latest screening date
    c = conn.cursor()
    c.execute("SELECT MAX(SUBSTR(date,1,10)) FROM screening_results")
    screen_date = c.fetchone()[0]
    if not screen_date:
        print("ERROR: No screening results. Run pipeline_metrics.py first.")
        conn.close()
        return
    print(f"Screening date: {screen_date}")

    # Build priority queue
    placeholders = ','.join(['?' for _ in universe])

    # P1: tickers that pass RS+ADTV+52W gates but have no fundamentals yet
    c.execute(f"""
        SELECT ticker FROM screening_results
        WHERE SUBSTR(date,1,10) = ?
          AND gate_rs = 1 AND gate_adtv = 1 AND gate_52w = 1
          AND ticker IN ({placeholders})
        ORDER BY rs_composite DESC
    """, [screen_date] + universe)
    p1 = [r[0] for r in c.fetchall() if r[0] not in cached]

    # P2: everything else (for full coverage)
    if args.all:
        p2 = [t for t in universe if t not in cached and t not in set(p1)]
    else:
        p2 = []

    queue = (p1 + p2)[:args.max]
    print(f"Queue: {len(queue)} tickers (P1={len(p1)}, P2={len(p2[:args.max-len(p1)])})")

    # Fetch loop
    success = 0
    failed = 0

    for i, ticker in enumerate(queue, 1):
        is_p1 = ticker in set(p1)
        tag = "[P1]" if is_p1 else "    "

        ok, g_eps, g_rev, g_gm = process_ticker(conn, ticker)

        if ok:
            eps_s = f"EPS:{g_eps}" if g_eps != -1 else "EPS:?"
            rev_s = f"Rev:{g_rev}" if g_rev != -1 else "Rev:?"
            gm_s = f"GM:{g_gm}" if g_gm != -1 else "GM:?"
            print(f"  {i:>3}/{len(queue)} {tag} {ticker:<8} | {eps_s} {rev_s} {gm_s}")
            success += 1
        else:
            print(f"  {i:>3}/{len(queue)} {tag} {ticker:<8} | NO DATA")
            mark_no_data(conn, ticker)
            failed += 1

        time.sleep(SLEEP_BETWEEN)

    print(f"\nFetch: {success} OK, {failed} failed")

    # Update screening gates
    print("Updating screening gates...")
    full_pass = update_screening_gates(conn, universe, screen_date)
    print(f"Full Mode A (6/6 gates): {full_pass}")

    # Print results
    full_pass = print_full_mode_a(conn, universe, screen_date)

    # Coverage stats
    c.execute(f"""
        SELECT
            COUNT(*) as has_data,
            SUM(CASE WHEN gate_eps = 1 THEN 1 ELSE 0 END) as eps_pass,
            SUM(CASE WHEN gate_rev = 1 THEN 1 ELSE 0 END) as rev_pass,
            SUM(CASE WHEN gate_gm = 1 THEN 1 ELSE 0 END) as gm_pass,
            SUM(CASE WHEN gate_eps = -1 THEN 1 ELSE 0 END) as no_data
        FROM fundamentals_summary WHERE ticker IN ({placeholders})
    """, universe)
    r = c.fetchone()
    print(f"\nFundamentals: {r[0]} with data, {r[4]} no data")
    print(f"Gate pass rates: EPS>25%={r[1]}, Rev>25%={r[2]}, GM stable/exp={r[3]}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
