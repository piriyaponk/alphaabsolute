"""
Targeted fundamentals fetch for all 285 themed tickers.
Uses data_engine (EDGAR -> FMP -> Finnhub fallback chain).
Run this to get fundamental data for all 14 themes.
"""
import sys, os, sqlite3, json, time
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR / "scripts" / "utils"))
import data_engine

DB_PATH = str(BASE_DIR / "data" / "ohlcv.db")
THEMED_TICKERS_PATH = str(BASE_DIR / "data" / "themes" / "themed_tickers.json")
LABELS_PATH = str(BASE_DIR / "data" / "themes" / "ticker_labels.json")
SLEEP = 0.5
CACHE_DAYS = 7

def get_cached(conn):
    # Cache by timestamp only — NOT by gate_eps value.
    # Filtering AND gate_eps != -1 caused all "no data" tickers to be re-fetched every run,
    # burning API quota on tickers that genuinely have no EDGAR data.
    # A ticker is "cached" if it was attempted within CACHE_DAYS, regardless of result.
    cutoff = (datetime.now() - timedelta(days=CACHE_DAYS)).isoformat()
    c = conn.cursor()
    c.execute("SELECT ticker FROM fundamentals_summary WHERE last_fetched > ?", (cutoff,))
    return {r[0] for r in c.fetchall()}

def save_fundamentals(conn, ticker, result):
    now = datetime.now().isoformat()
    gm_hist = result.get('gross_margin_history', [])
    gm_vals = []
    for g in gm_hist:
        if isinstance(g, dict):
            gm_vals.append(g.get('gross_margin'))
        elif isinstance(g, (int, float)):
            gm_vals.append(float(g))

    gm_latest = gm_vals[0] if gm_vals else None
    gm_prev = gm_vals[1] if len(gm_vals) > 1 else None
    gm_2q = gm_vals[2] if len(gm_vals) > 2 else None

    if len(gm_vals) >= 2:
        delta = (gm_vals[0] or 0) - (gm_vals[-1] or 0)
        gm_trend = "EXPANDING" if delta > 1.5 else ("CONTRACTING" if delta < -1.5 else "STABLE")
    else:
        gm_trend = "N/A"

    eps_yoy    = result.get('eps_yoy_pct')
    eps_latest = result.get('latest_eps')
    rev_yoy    = result.get('rev_yoy_pct')
    # gate_eps requires BOTH: YoY growth > 25% AND absolute EPS > 0
    # A company improving from -$1.00 to -$0.65 shows "35% growth" but is still losing money
    gate_eps = (1 if (eps_yoy > 25 and eps_latest is not None and eps_latest > 0) else 0) if eps_yoy is not None else -1
    gate_rev = (1 if rev_yoy > 25 else 0) if rev_yoy is not None else -1
    gate_gm = (1 if gm_trend in ("EXPANDING","STABLE") else 0) if gm_trend != "N/A" else -1

    eps_hist = result.get('eps_history', [])
    latest_period = eps_hist[0].get('quarter') if eps_hist and isinstance(eps_hist[0], dict) else None

    c = conn.cursor()
    # Write gm_1q_ago (REAL column) NOT gm_prev (legacy TEXT column from ALTER TABLE).
    # gm_prev is a zombie column — migration to gm_1q_ago completed 2026-05-22.
    c.execute("""
        INSERT INTO fundamentals_summary
            (ticker, source, eps_latest, eps_yoy_pct, rev_latest, rev_yoy_pct,
             gm_latest, gm_1q_ago, gm_2q_ago, gm_trend,
             eps_acceleration, rev_acceleration,
             gate_eps, gate_rev, gate_gm,
             latest_period, n_quarters, last_fetched, last_updated)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(ticker) DO UPDATE SET
            source=excluded.source, eps_latest=excluded.eps_latest,
            eps_yoy_pct=excluded.eps_yoy_pct, rev_latest=excluded.rev_latest,
            rev_yoy_pct=excluded.rev_yoy_pct, gm_latest=excluded.gm_latest,
            gm_1q_ago=excluded.gm_1q_ago, gm_2q_ago=excluded.gm_2q_ago,
            gm_trend=excluded.gm_trend, eps_acceleration=excluded.eps_acceleration,
            rev_acceleration=excluded.rev_acceleration,
            gate_eps=excluded.gate_eps, gate_rev=excluded.gate_rev, gate_gm=excluded.gate_gm,
            latest_period=excluded.latest_period, n_quarters=excluded.n_quarters,
            last_fetched=excluded.last_fetched, last_updated=excluded.last_updated
    """, (
        ticker, result.get('source','unknown'),
        result.get('latest_eps'), eps_yoy, result.get('latest_revenue'), rev_yoy,
        gm_latest, gm_prev, gm_2q, gm_trend,
        'ACCELERATING' if (eps_yoy or 0) > 25 else 'N/A',
        'ACCELERATING' if (rev_yoy or 0) > 25 else 'N/A',
        gate_eps, gate_rev, gate_gm,
        latest_period, len(eps_hist),
        now, now
    ))
    conn.commit()
    return gate_eps, gate_rev, gate_gm

def main():
    with open(THEMED_TICKERS_PATH, encoding='utf-8') as f:
        themed = json.load(f)
    with open(LABELS_PATH, encoding='utf-8') as f:
        labels = json.load(f)['labels']

    conn = sqlite3.connect(DB_PATH)
    cached = get_cached(conn)
    queue = [t for t in themed if t not in cached]

    print(f"Themed tickers: {len(themed)}")
    print(f"Cached: {len(cached)}")
    print(f"To fetch: {len(queue)}")
    print()

    ok = failed = 0
    theme_results = {}

    for i, ticker in enumerate(queue, 1):
        label = labels.get(ticker, '?')
        result = data_engine.get_fundamentals(ticker, quarters=8)

        if result and result.get('eps_history'):
            g_eps, g_rev, g_gm = save_fundamentals(conn, ticker, result)
            eps_y = result.get('eps_yoy_pct')
            rev_y = result.get('rev_yoy_pct')
            eps_s = f"EPS:{eps_y:+.0f}%" if eps_y is not None else "EPS:?"
            rev_s = f"Rev:{rev_y:+.0f}%" if rev_y is not None else "Rev:?"
            gm_s = f"GM:{'OK' if g_gm == 1 else 'FAIL' if g_gm == 0 else '?'}"
            all_pass = g_eps == 1 and g_rev == 1 and g_gm == 1
            star = " ***" if all_pass else ""
            print(f"  {i:>3}/{len(queue)} {ticker:<8} [{label:<16}] | {eps_s} {rev_s} {gm_s}{star}")
            ok += 1

            if label not in theme_results:
                theme_results[label] = {'ok': 0, 'all_pass': 0}
            theme_results[label]['ok'] += 1
            if all_pass:
                theme_results[label]['all_pass'] += 1
        else:
            print(f"  {i:>3}/{len(queue)} {ticker:<8} [{label:<16}] | NO DATA")
            now = datetime.now().isoformat()
            c = conn.cursor()
            c.execute("""
                INSERT INTO fundamentals_summary (ticker, source, gate_eps, gate_rev, gate_gm, last_fetched, last_updated)
                VALUES (?, 'none', -1, -1, -1, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET source='none', last_fetched=excluded.last_fetched
            """, (ticker, now, now))
            conn.commit()
            failed += 1

        time.sleep(SLEEP)

    print(f"\nDone: {ok} OK, {failed} failed")

    # Update screening results
    c = conn.cursor()
    c.execute("SELECT MAX(SUBSTR(date,1,10)) FROM screening_results")
    screen_date = c.fetchone()[0]
    if screen_date:
        placeholders = ','.join(['?' for _ in themed])
        c.execute(f"""
            UPDATE screening_results
            SET
                gate_eps = COALESCE((SELECT gate_eps FROM fundamentals_summary WHERE ticker = screening_results.ticker), -1),
                gate_rev = COALESCE((SELECT gate_rev FROM fundamentals_summary WHERE ticker = screening_results.ticker), -1),
                gate_gm  = COALESCE((SELECT gate_gm  FROM fundamentals_summary WHERE ticker = screening_results.ticker), -1),
                gates_passed = (
                    COALESCE(gate_rs, 0) + COALESCE(gate_adtv, 0) + COALESCE(gate_52w, 0) +
                    COALESCE((SELECT gate_eps FROM fundamentals_summary WHERE ticker = screening_results.ticker), 0) +
                    COALESCE((SELECT gate_rev FROM fundamentals_summary WHERE ticker = screening_results.ticker), 0) +
                    COALESCE((SELECT gate_gm  FROM fundamentals_summary WHERE ticker = screening_results.ticker), 0)
                )
            WHERE SUBSTR(date, 1, 10) = ? AND ticker IN ({placeholders})
        """, [screen_date] + themed)
        conn.commit()

        # Count full passes
        c.execute(f"""
            SELECT s.ticker, s.label, s.rs_composite, f.eps_yoy_pct, f.rev_yoy_pct, f.gm_trend
            FROM screening_results s
            JOIN fundamentals_summary f ON s.ticker = f.ticker
            WHERE SUBSTR(s.date,1,10) = ? AND s.gates_passed = 6
              AND s.ticker IN ({placeholders})
            ORDER BY s.rs_composite DESC
        """, [screen_date] + themed)
        full_pass = c.fetchall()
        print(f"\nFull Mode A (themed stocks, 6/6 gates): {len(full_pass)}")
        for row in full_pass:
            ticker, label, comp, eps_y, rev_y, gm_t = row
            print(f"  {ticker:<8} [{label:<16}] RS={comp:.0f} EPS={eps_y:+.0f}% Rev={rev_y:+.0f}% GM={gm_t}")

    conn.close()

if __name__ == "__main__":
    main()
