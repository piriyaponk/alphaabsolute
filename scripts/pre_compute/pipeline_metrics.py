"""
AlphaAbsolute — Metrics Pipeline (SQL-centric)
============================================
Computes all derived metrics from existing SQLite data.
No API calls — pure SQL + Python math.

Steps:
  A. ADTV (6-month average daily trading volume) -> ticker_meta.adtv_6m_usd
  B. 52-week high metrics -> ticker_meta (high_52w, low_52w, pct_from_52w_high, pct_from_52w_low)
  C. Better RS percentiles -> recompute rs_daily using full universe distribution (numpy)
  D. Mode A pre-screen -> screening_results table (RS Gate only for now, + ADTV + 52w)
  E. Update rs_daily.rs_composite and phase from better percentiles

Run: python scripts/pre_compute/pipeline_metrics.py
"""

import sqlite3
import json
import os
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

DB_PATH = "data/ohlcv.db"
LABELS_PATH = "data/themes/ticker_labels.json"
OUTPUT_DIR = "data/screening"
os.makedirs(OUTPUT_DIR, exist_ok=True)

THEMES = {
    "AI_Related", "Memory_HBM", "Space", "Quantum", "Photonics",
    "DefenseTech", "DataCenter", "Nuclear_SMR", "NeoCloud", "AI_Infra",
    "DataCenter_Infra", "Drone_UAV", "Robotics", "Connectivity"
}

def get_trading_days_ago(conn, n_days):
    """Get the date N trading days back from the latest date in ohlcv.
    Returns a plain YYYY-MM-DD string, stripping any timezone info.
    """
    c = conn.cursor()
    # Use SUBSTR to normalize dates to YYYY-MM-DD in case of timezone suffixes
    c.execute("""
        SELECT DISTINCT SUBSTR(date, 1, 10) as d
        FROM ohlcv
        ORDER BY d DESC
        LIMIT ?
    """, (n_days + 10,))
    dates = [r[0] for r in c.fetchall()]
    if len(dates) >= n_days:
        return dates[n_days - 1]
    return dates[-1]


def step_a_adtv(conn, universe):
    """Compute 6M ADTV from close×volume for all universe tickers."""
    print("\n=== Step A: ADTV Computation ===")
    c = conn.cursor()

    # Add columns if missing
    try:
        c.execute("ALTER TABLE ticker_meta ADD COLUMN adtv_6m_usd REAL")
        print("  Added adtv_6m_usd column")
    except:
        pass

    # Get cutoff date for 6M (approx 126 trading days)
    cutoff = get_trading_days_ago(conn, 126)
    latest = get_trading_days_ago(conn, 1)
    print(f"  6M window: {cutoff} to {latest}")

    placeholders = ','.join(['?' for _ in universe])
    c.execute(f"""
        SELECT ticker, AVG(close * volume) as adtv, COUNT(*) as n_bars
        FROM ohlcv
        WHERE ticker IN ({placeholders}) AND SUBSTR(date,1,10) >= ?
        GROUP BY ticker
    """, universe + [cutoff])

    results = c.fetchall()
    print(f"  Computed ADTV for {len(results)} tickers")

    # Upsert into ticker_meta
    for ticker, adtv, n in results:
        c.execute("""
            INSERT INTO ticker_meta (ticker, adtv_6m_usd) VALUES (?, ?)
            ON CONFLICT(ticker) DO UPDATE SET adtv_6m_usd = excluded.adtv_6m_usd
        """, (ticker, adtv))

    conn.commit()

    # Stats
    c.execute(f"""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN adtv_6m_usd > 10000000 THEN 1 ELSE 0 END) as above_10m,
            SUM(CASE WHEN adtv_6m_usd > 1000000 THEN 1 ELSE 0 END) as above_1m,
            AVG(adtv_6m_usd) as avg_adtv
        FROM ticker_meta WHERE ticker IN ({placeholders})
    """, universe)
    r = c.fetchone()
    print(f"  Total: {r[0]} | >$10M ADTV: {r[1]} | >$1M: {r[2]} | Avg: ${r[3]/1e6:.1f}M")
    return r[1]  # count above $10M


def step_b_52week(conn, universe):
    """Compute 52-week high/low metrics from close data."""
    print("\n=== Step B: 52-Week High/Low Metrics ===")
    c = conn.cursor()

    # Add columns if missing
    for col, typ in [
        ("high_52w", "REAL"),
        ("low_52w", "REAL"),
        ("pct_from_52w_high", "REAL"),
        ("pct_from_52w_low", "REAL"),
        ("last_close", "REAL"),
    ]:
        try:
            c.execute(f"ALTER TABLE ticker_meta ADD COLUMN {col} {typ}")
            print(f"  Added {col} column")
        except:
            pass

    # Get cutoff: 252 trading days = 1 year
    cutoff = get_trading_days_ago(conn, 252)
    latest_date = get_trading_days_ago(conn, 1)
    print(f"  52W window: {cutoff} to {latest_date}")

    placeholders = ','.join(['?' for _ in universe])

    # Compute 52w high/low and latest close (normalize dates with SUBSTR)
    c.execute(f"""
        SELECT
            o.ticker,
            MAX(o.close) as high_52w,
            MIN(o.close) as low_52w,
            (SELECT close FROM ohlcv o2
             WHERE o2.ticker = o.ticker
             ORDER BY SUBSTR(o2.date,1,10) DESC LIMIT 1) as last_close
        FROM ohlcv o
        WHERE o.ticker IN ({placeholders}) AND SUBSTR(o.date,1,10) >= ?
        GROUP BY o.ticker
    """, universe + [cutoff])

    results = c.fetchall()
    print(f"  Got 52W data for {len(results)} tickers")

    updated = 0
    for ticker, high_52w, low_52w, last_close in results:
        if not high_52w or not last_close:
            continue
        pct_from_high = (last_close / high_52w - 1) * 100  # negative = below high
        pct_from_low = (last_close / low_52w - 1) * 100 if low_52w else None

        c.execute("""
            INSERT INTO ticker_meta (ticker, high_52w, low_52w, pct_from_52w_high, pct_from_52w_low, last_close)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                high_52w = excluded.high_52w,
                low_52w = excluded.low_52w,
                pct_from_52w_high = excluded.pct_from_52w_high,
                pct_from_52w_low = excluded.pct_from_52w_low,
                last_close = excluded.last_close
        """, (ticker, high_52w, low_52w, pct_from_high, pct_from_low, last_close))
        updated += 1

    conn.commit()

    # Stats: Mode A Gate 6 requires pct_from_52w_high > -20
    c.execute(f"""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN pct_from_52w_high > -20 THEN 1 ELSE 0 END) as within_20pct,
            SUM(CASE WHEN pct_from_52w_high > -10 THEN 1 ELSE 0 END) as within_10pct,
            AVG(pct_from_52w_high) as avg_pct
        FROM ticker_meta WHERE ticker IN ({placeholders}) AND pct_from_52w_high IS NOT NULL
    """, universe)
    r = c.fetchone()
    print(f"  Updated: {updated} | Within -20% of 52W high: {r[1]}/{r[0]} | Within -10%: {r[2]} | Avg: {r[3]:.1f}%")
    return r[1]


def step_b2_price_structure(conn, universe):
    """Compute 3M and 6M high/low price structure metrics.

    Adds to ticker_meta:
      high_3m, low_3m, pct_from_3m_high, pct_from_3m_low
      high_6m, low_6m, pct_from_6m_high, pct_from_6m_low
    """
    print("\n=== Step B2: 3M / 6M Price Structure ===")
    c = conn.cursor()

    # Add columns if missing
    new_cols = [
        ("high_3m",          "REAL"),
        ("low_3m",           "REAL"),
        ("pct_from_3m_high", "REAL"),
        ("pct_from_3m_low",  "REAL"),
        ("high_6m",          "REAL"),
        ("low_6m",           "REAL"),
        ("pct_from_6m_high", "REAL"),
        ("pct_from_6m_low",  "REAL"),
    ]
    for col, typ in new_cols:
        try:
            c.execute(f"ALTER TABLE ticker_meta ADD COLUMN {col} {typ}")
            print(f"  Added ticker_meta.{col}")
        except:
            pass

    cutoff_3m = get_trading_days_ago(conn, 63)
    cutoff_6m = get_trading_days_ago(conn, 126)
    print(f"  3M window: {cutoff_3m} onward | 6M window: {cutoff_6m} onward")

    placeholders = ','.join(['?' for _ in universe])

    # Compute 3M high/low
    c.execute(f"""
        SELECT o.ticker,
               MAX(o.close) as high_3m,
               MIN(o.close) as low_3m
        FROM ohlcv o
        WHERE o.ticker IN ({placeholders}) AND SUBSTR(o.date,1,10) >= ?
        GROUP BY o.ticker
    """, universe + [cutoff_3m])
    data_3m = {r[0]: (r[1], r[2]) for r in c.fetchall()}

    # Compute 6M high/low
    c.execute(f"""
        SELECT o.ticker,
               MAX(o.close) as high_6m,
               MIN(o.close) as low_6m
        FROM ohlcv o
        WHERE o.ticker IN ({placeholders}) AND SUBSTR(o.date,1,10) >= ?
        GROUP BY o.ticker
    """, universe + [cutoff_6m])
    data_6m = {r[0]: (r[1], r[2]) for r in c.fetchall()}

    # Get last_close for each ticker (already in ticker_meta, reuse)
    c.execute(f"""
        SELECT ticker, last_close FROM ticker_meta WHERE ticker IN ({placeholders})
    """, universe)
    last_closes = {r[0]: r[1] for r in c.fetchall()}

    updated = 0
    for ticker in universe:
        lc = last_closes.get(ticker)
        if not lc:
            continue

        h3, lo3 = data_3m.get(ticker, (None, None))
        h6, lo6 = data_6m.get(ticker, (None, None))

        pct_3m_high = (lc / h3 - 1) * 100 if h3 else None
        pct_3m_low  = (lc / lo3 - 1) * 100 if lo3 else None
        pct_6m_high = (lc / h6 - 1) * 100 if h6 else None
        pct_6m_low  = (lc / lo6 - 1) * 100 if lo6 else None

        c.execute("""
            UPDATE ticker_meta SET
                high_3m = ?, low_3m = ?, pct_from_3m_high = ?, pct_from_3m_low = ?,
                high_6m = ?, low_6m = ?, pct_from_6m_high = ?, pct_from_6m_low = ?
            WHERE ticker = ?
        """, (h3, lo3, pct_3m_high, pct_3m_low, h6, lo6, pct_6m_high, pct_6m_low, ticker))
        updated += 1

    conn.commit()
    print(f"  Updated {updated} tickers with 3M/6M price structure")

    # Quick stats
    c.execute(f"""
        SELECT
            AVG(pct_from_3m_high), AVG(pct_from_6m_high),
            SUM(CASE WHEN pct_from_3m_high > -10 THEN 1 ELSE 0 END),
            SUM(CASE WHEN pct_from_6m_high > -20 THEN 1 ELSE 0 END)
        FROM ticker_meta WHERE ticker IN ({placeholders}) AND pct_from_3m_high IS NOT NULL
    """, universe)
    r = c.fetchone()
    if r[0] is not None:
        print(f"  Avg from 3M high: {r[0]:.1f}% | Avg from 6M high: {r[1]:.1f}%")
        print(f"  Within -10% of 3M high: {r[2]} | Within -20% of 6M high: {r[3]}")


def step_b3_moving_averages(conn, universe):
    """Compute moving averages from close prices.

    Adds to ticker_meta:
      ma_5d, ma_10d, ma_20d, ma_50d, ma_150d, ma_200d, ma_300d
      pct_above_ma50, pct_above_ma150, pct_above_ma200
      stage2_flag: 1 if close > MA50 > MA150 > MA200 (Minervini Trend Template core)
    """
    print("\n=== Step B3: Moving Averages & Stage 2 Detection ===")
    c = conn.cursor()

    new_cols = [
        ("ma_5d",           "REAL"),
        ("ma_10d",          "REAL"),
        ("ma_20d",          "REAL"),
        ("ma_50d",          "REAL"),
        ("ma_150d",         "REAL"),
        ("ma_200d",         "REAL"),
        ("ma_300d",         "REAL"),
        ("pct_above_ma50",  "REAL"),
        ("pct_above_ma150", "REAL"),
        ("pct_above_ma200", "REAL"),
        ("stage2_flag",     "INTEGER"),
    ]
    for col, typ in new_cols:
        try:
            c.execute(f"ALTER TABLE ticker_meta ADD COLUMN {col} {typ}")
            print(f"  Added ticker_meta.{col}")
        except:
            pass

    # Load last 310 closes per ticker in one batch (need 300 for MA300)
    c.execute("""
        SELECT DISTINCT SUBSTR(date,1,10) as d FROM ohlcv
        ORDER BY d DESC LIMIT 310
    """)
    last_310 = [r[0] for r in c.fetchall()]
    if not last_310:
        print("  ERROR: No dates in ohlcv")
        return 0
    cutoff_date = last_310[-1]

    placeholders = ','.join(['?' for _ in universe])
    print(f"  Batch-loading closes for {len(universe)} tickers (cutoff {cutoff_date})...")
    c.execute(f"""
        SELECT ticker, SUBSTR(date,1,10) as d, close
        FROM ohlcv
        WHERE ticker IN ({placeholders}) AND SUBSTR(date,1,10) >= ?
        ORDER BY ticker, SUBSTR(date,1,10) ASC
    """, universe + [cutoff_date])

    closes_by_ticker = {}
    for ticker, d, cl in c.fetchall():
        if ticker not in closes_by_ticker:
            closes_by_ticker[ticker] = []
        if cl is not None:
            closes_by_ticker[ticker].append(float(cl))

    def safe_ma(closes, n):
        if len(closes) >= n:
            return float(np.mean(closes[-n:]))
        return None

    updates = []
    stage2_count = 0
    ma50_cov = ma150_cov = ma200_cov = 0

    for ticker in universe:
        closes = closes_by_ticker.get(ticker, [])
        if len(closes) < 5:
            continue

        ma5   = safe_ma(closes, 5)
        ma10  = safe_ma(closes, 10)
        ma20  = safe_ma(closes, 20)
        ma50  = safe_ma(closes, 50)
        ma150 = safe_ma(closes, 150)
        ma200 = safe_ma(closes, 200)
        ma300 = safe_ma(closes, 300)

        last_close = closes[-1]

        pct_above_ma50  = (last_close / ma50  - 1) * 100 if ma50  else None
        pct_above_ma150 = (last_close / ma150 - 1) * 100 if ma150 else None
        pct_above_ma200 = (last_close / ma200 - 1) * 100 if ma200 else None

        if ma50:  ma50_cov  += 1
        if ma150: ma150_cov += 1
        if ma200: ma200_cov += 1

        # Stage 2 Wyckoff / Minervini Trend Template (core):
        # close > MA50 > MA150 > MA200
        stage2 = 0
        if ma50 and ma150 and ma200:
            if last_close > ma50 and ma50 > ma150 and ma150 > ma200:
                stage2 = 1
                stage2_count += 1

        updates.append((
            ma5, ma10, ma20, ma50, ma150, ma200, ma300,
            pct_above_ma50, pct_above_ma150, pct_above_ma200,
            stage2, ticker
        ))

    c.executemany("""
        UPDATE ticker_meta SET
            ma_5d = ?, ma_10d = ?, ma_20d = ?, ma_50d = ?,
            ma_150d = ?, ma_200d = ?, ma_300d = ?,
            pct_above_ma50 = ?, pct_above_ma150 = ?, pct_above_ma200 = ?,
            stage2_flag = ?
        WHERE ticker = ?
    """, updates)
    conn.commit()

    print(f"  Updated {len(updates)} tickers | MA50 coverage: {ma50_cov} | MA150: {ma150_cov} | MA200: {ma200_cov}")
    print(f"  Stage 2 (close>MA50>MA150>MA200): {stage2_count} / {len(updates)} tickers")
    return stage2_count


def step_b4_volume_ma(conn, universe):
    """Compute volume moving averages for VCP/PPT pattern recognition.

    Adds to ticker_meta:
      vol_ma_20d    — 20-day average volume
      vol_ma_50d    — 50-day average volume
      rel_vol_today — today's volume / vol_ma_20d (relative volume)
      mode_b_eligible — 1 if within 3% of 3M high (breakout zone)
    """
    print("\n=== Step B4: Volume MAs & Mode B Eligibility ===")
    c = conn.cursor()

    new_cols = [
        ("vol_ma_20d",      "REAL"),
        ("vol_ma_50d",      "REAL"),
        ("rel_vol_today",   "REAL"),
        ("mode_b_eligible", "INTEGER"),
    ]
    for col, typ in new_cols:
        try:
            c.execute(f"ALTER TABLE ticker_meta ADD COLUMN {col} {typ}")
            print(f"  Added ticker_meta.{col}")
        except:
            pass

    # Load last 55 (close, volume) rows per ticker
    c.execute("""
        SELECT DISTINCT SUBSTR(date,1,10) as d FROM ohlcv
        ORDER BY d DESC LIMIT 55
    """)
    last_55 = [r[0] for r in c.fetchall()]
    cutoff_55 = last_55[-1]

    placeholders = ','.join(['?' for _ in universe])
    c.execute(f"""
        SELECT ticker, SUBSTR(date,1,10) as d, volume, close
        FROM ohlcv
        WHERE ticker IN ({placeholders}) AND SUBSTR(date,1,10) >= ?
        ORDER BY ticker, SUBSTR(date,1,10) ASC
    """, universe + [cutoff_55])

    vol_by_ticker = {}
    for ticker, d, vol, cl in c.fetchall():
        if ticker not in vol_by_ticker:
            vol_by_ticker[ticker] = []
        if vol is not None:
            vol_by_ticker[ticker].append(float(vol))

    # Get pct_from_3m_high from ticker_meta for Mode B flag
    c.execute(f"SELECT ticker, pct_from_3m_high FROM ticker_meta WHERE ticker IN ({placeholders})", universe)
    pct_3m = {r[0]: r[1] for r in c.fetchall()}

    updates = []
    mode_b_count = 0

    for ticker in universe:
        vols = vol_by_ticker.get(ticker, [])
        if not vols:
            continue

        vol_ma_20 = float(np.mean(vols[-20:])) if len(vols) >= 20 else None
        vol_ma_50 = float(np.mean(vols[-50:])) if len(vols) >= 50 else None
        today_vol = vols[-1]
        rel_vol = (today_vol / vol_ma_20) if (vol_ma_20 and vol_ma_20 > 0) else None

        # Mode B: within 3% of 63-day high = near 3-month breakout zone
        p3m = pct_3m.get(ticker)
        mode_b = 1 if (p3m is not None and p3m >= -3) else 0
        if mode_b:
            mode_b_count += 1

        updates.append((vol_ma_20, vol_ma_50, rel_vol, mode_b, ticker))

    c.executemany("""
        UPDATE ticker_meta SET
            vol_ma_20d = ?, vol_ma_50d = ?, rel_vol_today = ?, mode_b_eligible = ?
        WHERE ticker = ?
    """, updates)
    conn.commit()

    print(f"  Updated {len(updates)} tickers with volume MAs")
    print(f"  Mode B eligible (within 3% of 3M high): {mode_b_count}")
    return mode_b_count


def step_c_better_rs(conn, universe, labels):
    """Compute RS percentiles from RAW ohlcv returns (not breakpoint-based values).

    Bug fixed (2026-05-21): previous version read rs_daily which only had 17
    breakpoint-level values from pipeline_ohlcv_rs.py.  Re-ranking percentiles
    of percentiles gives only 17 distinct output values (62+ tickers tied each).
    Correct approach: compute raw RS ratio from ohlcv, rank those raw values.

    Computes:
      rs_1m_pct, rs_3m_pct, rs_6m_pct  -- from raw ohlcv (1,325 unique values)
      rs_12m_pct                         -- 252d RS vs QQQ (NULL if QQQ < 252 bars)
      rs_momentum_1m_3m = rs_1m_pct - rs_3m_pct
      rs_momentum_3m_6m = rs_3m_pct - rs_6m_pct
      rs_momentum_6m_12m = rs_6m_pct - rs_12m_pct
    """
    print("\n=== Step C: RS from Raw OHLCV + Full Distribution Rerank ===")
    c = conn.cursor()

    # Add new columns to rs_daily if missing
    for col in ["rs_12m_pct", "rs_momentum_1m_3m", "rs_momentum_3m_6m", "rs_momentum_6m_12m"]:
        try:
            c.execute(f"ALTER TABLE rs_daily ADD COLUMN {col} REAL")
            print(f"  Added rs_daily.{col}")
        except:
            pass
    conn.commit()

    # ── Use QQQ's last date as the effective benchmark date ──────────────────
    # pipeline_ohlcv_rs.py uses today's date as label but QQQ data ends earlier.
    # We always compute vs QQQ's last available close to stay consistent.
    c.execute("SELECT MAX(SUBSTR(date,1,10)) FROM ohlcv WHERE ticker='QQQ'")
    qqq_last_date = c.fetchone()[0]

    # The rs_daily records are labeled with latest_date (which may be today).
    # We need to know what label was used so we can UPDATE the right rows.
    latest_date = get_trading_days_ago(conn, 1)
    print(f"  rs_daily label date:  {latest_date}")
    print(f"  QQQ last data date:   {qqq_last_date}")

    # Build QQQ trading-day list from ohlcv (for window lookback)
    c.execute("SELECT SUBSTR(date,1,10) FROM ohlcv WHERE ticker='QQQ' ORDER BY SUBSTR(date,1,10) ASC")
    qqq_dates = [r[0] for r in c.fetchall()]
    qqq_dict  = {}
    c.execute("SELECT SUBSTR(date,1,10), close FROM ohlcv WHERE ticker='QQQ'")
    for d, cl in c.fetchall():
        qqq_dict[d] = cl
    print(f"  QQQ bars available:   {len(qqq_dates)} ({qqq_dates[0]} to {qqq_dates[-1]})")

    def qqq_n_days_before(n):
        """Get QQQ close N trading days before qqq_last_date."""
        idx = len(qqq_dates) - 1  # last index
        past_idx = idx - n
        if past_idx < 0:
            return None, None
        return qqq_dates[past_idx], qqq_dict.get(qqq_dates[past_idx])

    qqq_now = qqq_dict.get(qqq_last_date)
    WINDOWS = [(21, '1m'), (63, '3m'), (126, '6m'), (252, '12m')]
    qqq_cuts = {}
    qqq_rets = {}
    for w_days, w_name in WINDOWS:
        cut_date, cut_close = qqq_n_days_before(w_days)
        qqq_cuts[w_name] = (cut_date, cut_close)
        qqq_rets[w_name] = (qqq_now / cut_close - 1) if (qqq_now and cut_close) else None
        avail = "OK" if cut_close else "MISSING"
        print(f"  QQQ {w_name:>3}: cutoff={cut_date} ({avail}) ret={qqq_rets[w_name]*100:.1f}%" if qqq_rets[w_name] else f"  QQQ {w_name:>3}: cutoff={cut_date} ({avail})")

    # ── Load all tickers' closes at needed dates in ONE batch query ──────────
    needed_dates = set()
    needed_dates.add(qqq_last_date)
    for w_name in ('1m', '3m', '6m', '12m'):
        d = qqq_cuts[w_name][0]
        if d:
            needed_dates.add(d)
    needed_dates_list = sorted(needed_dates)

    placeholders = ','.join(['?' for _ in universe])
    date_ph = ','.join(['?' for _ in needed_dates_list])

    print(f"  Batch-loading closes for {len(universe)} tickers on {len(needed_dates_list)} dates...")
    c.execute(f"""
        SELECT ticker, SUBSTR(date,1,10) as d, close
        FROM ohlcv
        WHERE ticker IN ({placeholders}) AND SUBSTR(date,1,10) IN ({date_ph})
    """, universe + needed_dates_list)

    # closes_map: ticker -> {date -> close}
    closes_map = {}
    for ticker, d, cl in c.fetchall():
        if ticker not in closes_map:
            closes_map[ticker] = {}
        closes_map[ticker][d] = cl

    # ── Compute raw RS for all tickers × all windows ────────────────────────
    def raw_rs(s_ret, i_ret):
        if s_ret is None or i_ret is None:
            return np.nan
        return ((1 + s_ret) / (1 + i_ret) - 1) * 100

    all_tickers = [t for t in universe if t in closes_map]
    n = len(all_tickers)

    rs1m_raw  = np.full(n, np.nan)
    rs3m_raw  = np.full(n, np.nan)
    rs6m_raw  = np.full(n, np.nan)
    rs12m_raw = np.full(n, np.nan)

    for i, ticker in enumerate(all_tickers):
        tc = closes_map[ticker]
        now_close = tc.get(qqq_last_date)
        if not now_close:
            continue
        for j, (w_days, w_name) in enumerate(WINDOWS):
            cut_date, _ = qqq_cuts[w_name]
            if not cut_date or qqq_rets[w_name] is None:
                continue
            past_close = tc.get(cut_date)
            if not past_close:
                continue
            s_ret = now_close / past_close - 1
            rs = raw_rs(s_ret, qqq_rets[w_name])
            if w_name == '1m':  rs1m_raw[i]  = rs
            elif w_name == '3m': rs3m_raw[i]  = rs
            elif w_name == '6m': rs6m_raw[i]  = rs
            elif w_name == '12m': rs12m_raw[i] = rs

    n_valid = int(np.sum(~np.isnan(rs3m_raw)))
    print(f"  Raw RS computed: {n_valid}/{n} tickers have 3M data")

    # ── True percentile rerank (searchsorted method) ─────────────────────────
    def rerank(arr):
        result = np.full(len(arr), np.nan)
        valid_mask = ~np.isnan(arr)
        valid_vals = arr[valid_mask]
        if len(valid_vals) < 10:
            return result
        sorted_vals = np.sort(valid_vals)
        n_v = len(sorted_vals)
        for idx in np.where(valid_mask)[0]:
            v = arr[idx]
            below = np.searchsorted(sorted_vals, v, side='left')
            ties  = np.searchsorted(sorted_vals, v, side='right') - below
            result[idx] = (below + 0.5 * ties) / n_v * 100
        return result

    print("  Reranking from raw RS ratios (true percentile, full distribution)...")
    rs1m_pct  = rerank(rs1m_raw)
    rs3m_pct  = rerank(rs3m_raw)
    rs6m_pct  = rerank(rs6m_raw)
    rs12m_pct = rerank(rs12m_raw)

    n_dist = int(np.sum(~np.isnan(rs3m_pct)))
    n_unique = len(np.unique(rs3m_pct[~np.isnan(rs3m_pct)]))
    print(f"  rs_3m_pct: {n_dist} values, {n_unique} unique (should be ~{n_dist})")

    # ── Composite + Phase ────────────────────────────────────────────────────
    def safe_composite(p1, p3, p6):
        vals, weights = [], []
        if not np.isnan(p1): vals.append(p1 * 0.35); weights.append(0.35)
        if not np.isnan(p3): vals.append(p3 * 0.45); weights.append(0.45)
        if not np.isnan(p6): vals.append(p6 * 0.20); weights.append(0.20)
        return sum(vals) / sum(weights) if vals else None

    def get_phase(p3, p6):
        if np.isnan(p3): return "Weak"
        if p3 >= 80 and (np.isnan(p6) or p6 >= 70): return "Leader"
        if p3 >= 60: return "Emerging"
        if p3 >= 40: return "Recovering"
        return "Weak"

    # ── Build updates ─────────────────────────────────────────────────────────
    # rs_daily rows are labeled with latest_date (may be 2026-05-19).
    # We update those rows with correctly-computed values from QQQ's last date.
    updates = []
    for i, ticker in enumerate(all_tickers):
        p1  = float(rs1m_pct[i])  if not np.isnan(rs1m_pct[i])  else None
        p3  = float(rs3m_pct[i])  if not np.isnan(rs3m_pct[i])  else None
        p6  = float(rs6m_pct[i])  if not np.isnan(rs6m_pct[i])  else None
        p12 = float(rs12m_pct[i]) if not np.isnan(rs12m_pct[i]) else None

        p3_v = rs3m_pct[i] if not np.isnan(rs3m_pct[i]) else np.nan
        p6_v = rs6m_pct[i] if not np.isnan(rs6m_pct[i]) else np.nan
        composite = safe_composite(
            rs1m_pct[i],  rs3m_pct[i],  rs6m_pct[i]
        )
        phase = get_phase(p3_v, p6_v)

        mom_1m_3m  = (p1  - p3)  if (p1  is not None and p3  is not None) else None
        mom_3m_6m  = (p3  - p6)  if (p3  is not None and p6  is not None) else None
        mom_6m_12m = (p6  - p12) if (p6  is not None and p12 is not None) else None

        updates.append((p1, p3, p6, p12, composite, phase,
                        mom_1m_3m, mom_3m_6m, mom_6m_12m,
                        ticker, latest_date))

    # Also insert for qqq_last_date if different from latest_date
    # (so the historically-correct date exists in rs_daily too)
    if qqq_last_date != latest_date:
        for row in list(updates):
            updates.append((*row[:-1], qqq_last_date))  # same data, different date label

    c.executemany("""
        INSERT INTO rs_daily
            (ticker, date, rs_1m_pct, rs_3m_pct, rs_6m_pct, rs_12m_pct,
             rs_composite, phase,
             rs_momentum_1m_3m, rs_momentum_3m_6m, rs_momentum_6m_12m)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(ticker, date) DO UPDATE SET
            rs_1m_pct          = excluded.rs_1m_pct,
            rs_3m_pct          = excluded.rs_3m_pct,
            rs_6m_pct          = excluded.rs_6m_pct,
            rs_12m_pct         = excluded.rs_12m_pct,
            rs_composite       = excluded.rs_composite,
            phase              = excluded.phase,
            rs_momentum_1m_3m  = excluded.rs_momentum_1m_3m,
            rs_momentum_3m_6m  = excluded.rs_momentum_3m_6m,
            rs_momentum_6m_12m = excluded.rs_momentum_6m_12m
    """, [(t, date, p1, p3, p6, p12, comp, ph, m13, m36, m612)
          for (p1, p3, p6, p12, comp, ph, m13, m36, m612, t, date) in updates])
    conn.commit()
    print(f"  Upserted {len(updates)} RS records into rs_daily")

    # Distribution check
    rs3m_valid = rs3m_pct[~np.isnan(rs3m_pct)]
    leaders   = int(np.sum(rs3m_valid >= 80))
    emerging  = int(np.sum((rs3m_valid >= 60) & (rs3m_valid < 80)))
    recovering= int(np.sum((rs3m_valid >= 40) & (rs3m_valid < 60)))
    weak      = int(np.sum(rs3m_valid < 40))
    print(f"  Phase distribution: Leader={leaders} | Emerging={emerging} | Recovering={recovering} | Weak={weak}")

    mom_valid = rs1m_pct - rs3m_pct
    accel = int(np.sum(mom_valid[~np.isnan(mom_valid)] > 0))
    print(f"  RS momentum (1M>3M): {accel}/{np.sum(~np.isnan(mom_valid))} tickers accelerating short-term")

    return {t: {'rs_1m': float(rs1m_pct[i]) if not np.isnan(rs1m_pct[i]) else None,
                'rs_3m': float(rs3m_pct[i]) if not np.isnan(rs3m_pct[i]) else None,
                'rs_6m': float(rs6m_pct[i]) if not np.isnan(rs6m_pct[i]) else None,
                'rs_12m': float(rs12m_pct[i]) if not np.isnan(rs12m_pct[i]) else None}
            for i, t in enumerate(all_tickers)}


def step_d_screening(conn, universe, labels):
    """Run Mode A screening with all gates that have data.

    Gates implemented:
      Gate 1: RS3M >= 70 AND RS6M >= 70 (rs_daily)
      Gate 6: pct_from_52w_high > -20 (ticker_meta)
      Gate 7: adtv_6m_usd > 10,000,000 (ticker_meta)

    Gates pending (need FMP fundamental data):
      Gate 2: EPS YoY > 25%
      Gate 3: Revenue YoY > 25%
      Gate 4: Gross margin stable/expanding
      Gate 5: Stage 2
    """
    print("\n=== Step D: Mode A Screening (Available Gates) ===")
    c = conn.cursor()

    latest_date = get_trading_days_ago(conn, 1)
    placeholders = ','.join(['?' for _ in universe])

    # Create screening_results table (with all price structure + RS change columns)
    c.execute("""
        CREATE TABLE IF NOT EXISTS screening_results (
            ticker TEXT,
            date TEXT,
            mode TEXT DEFAULT 'A',
            label TEXT,
            rs_1m_pct REAL,
            rs_3m_pct REAL,
            rs_6m_pct REAL,
            rs_composite REAL,
            rs_comp_chg_1w REAL,
            rs_comp_chg_4w REAL,
            phase TEXT,
            adtv_6m_usd REAL,
            last_close REAL,
            pct_from_52w_high REAL,
            pct_from_52w_low REAL,
            pct_from_3m_high REAL,
            pct_from_3m_low REAL,
            pct_from_6m_high REAL,
            pct_from_6m_low REAL,
            gate_rs INTEGER DEFAULT 0,
            gate_adtv INTEGER DEFAULT 0,
            gate_52w INTEGER DEFAULT 0,
            gate_eps INTEGER DEFAULT -1,
            gate_rev INTEGER DEFAULT -1,
            gate_gm INTEGER DEFAULT -1,
            gates_passed INTEGER DEFAULT 0,
            screen_note TEXT,
            updated_at TEXT,
            PRIMARY KEY (ticker, date)
        )
    """)

    # Add missing columns to existing table (safe ALTER TABLE)
    new_sr_cols = [
        ("rs_1m_pct",           "REAL"),
        ("rs_12m_pct",          "REAL"),
        ("rs_comp_chg_1w",      "REAL"),
        ("rs_comp_chg_4w",      "REAL"),
        ("rs_momentum_1m_3m",   "REAL"),
        ("rs_momentum_3m_6m",   "REAL"),
        ("rs_momentum_6m_12m",  "REAL"),
        ("pct_from_52w_low",    "REAL"),
        ("high_52w",            "REAL"),
        ("low_52w",             "REAL"),
        ("pct_from_3m_high",    "REAL"),
        ("pct_from_3m_low",     "REAL"),
        ("pct_from_6m_high",    "REAL"),
        ("pct_from_6m_low",     "REAL"),
        ("ma_50d",              "REAL"),
        ("ma_150d",             "REAL"),
        ("ma_200d",             "REAL"),
        ("pct_above_ma50",      "REAL"),
        ("pct_above_ma150",     "REAL"),
        ("pct_above_ma200",     "REAL"),
        ("stage2_flag",         "INTEGER"),
        ("gate_stage2",         "INTEGER"),
        ("vol_ma_20d",          "REAL"),
        ("rel_vol_today",       "REAL"),
        ("mode_b_eligible",     "INTEGER"),
        ("gate_earnings",       "INTEGER"),
    ]
    for col, typ in new_sr_cols:
        try:
            c.execute(f"ALTER TABLE screening_results ADD COLUMN {col} {typ}")
        except:
            pass

    # Join rs_daily + ticker_meta — pull all RS + momentum + price structure + MAs
    c.execute(f"""
        SELECT
            r.ticker,
            r.rs_1m_pct,
            r.rs_3m_pct,
            r.rs_6m_pct,
            r.rs_12m_pct,
            r.rs_composite,
            r.rs_comp_chg_1w,
            r.rs_comp_chg_4w,
            r.rs_momentum_1m_3m,
            r.rs_momentum_3m_6m,
            r.rs_momentum_6m_12m,
            r.phase,
            m.adtv_6m_usd,
            m.last_close,
            m.high_52w,
            m.low_52w,
            m.pct_from_52w_high,
            m.pct_from_52w_low,
            m.pct_from_3m_high,
            m.pct_from_3m_low,
            m.pct_from_6m_high,
            m.pct_from_6m_low,
            m.ma_50d,
            m.ma_150d,
            m.ma_200d,
            m.pct_above_ma50,
            m.pct_above_ma150,
            m.pct_above_ma200,
            m.stage2_flag,
            m.vol_ma_20d,
            m.rel_vol_today,
            m.mode_b_eligible
        FROM rs_daily r
        LEFT JOIN ticker_meta m ON r.ticker = m.ticker
        WHERE SUBSTR(r.date,1,10) = ? AND r.ticker IN ({placeholders})
    """, [latest_date] + universe)

    rows = c.fetchall()
    print(f"  Processing {len(rows)} tickers for {latest_date}")

    # Load earnings gate file (built by fetch_earnings_calendar.py)
    import json as _json
    earnings_block = set()
    _earnings_file = os.path.join(os.path.dirname(DB_PATH), "regime", "earnings_next30.json")
    if os.path.exists(_earnings_file):
        try:
            with open(_earnings_file) as _f:
                _edata = _json.load(_f)
            earnings_block = set(_edata.get("within_5_trading_days", []))
            if earnings_block:
                print(f"  Earnings gate active: {len(earnings_block)} tickers blocked (within 5td)")
        except Exception:
            pass

    # Load fundamentals gates from fundamentals_summary (populated by pipeline_fundamentals.py)
    fund_gates = {}
    try:
        c.execute("SELECT ticker, gate_eps, gate_rev, gate_gm FROM fundamentals_summary")
        for ft, ge, gr, gg in c.fetchall():
            fund_gates[ft] = (
                ge if ge is not None else -1,
                gr if gr is not None else -1,
                gg if gg is not None else -1,
            )
        print(f"  Fundamentals gates loaded: {len(fund_gates)} tickers from fundamentals_summary")
    except Exception as _fe:
        print(f"  [WARN] Could not load fundamentals_summary: {_fe}")

    screening = []
    gate1_pass = gate5_pass = gate6_pass = gate7_pass = all4_pass = 0
    gate_earnings_blocked = 0

    now = datetime.now().isoformat()

    for row in rows:
        (ticker, rs1m, rs3m, rs6m, rs12m, rs_comp, chg_1w, chg_4w,
         mom_1m_3m, mom_3m_6m, mom_6m_12m, phase,
         adtv, last_close,
         high_52w, low_52w, pct_52w_high, pct_52w_low,
         pct_3m_high, pct_3m_low, pct_6m_high, pct_6m_low,
         ma50, ma150, ma200,
         pct_ma50, pct_ma150, pct_ma200,
         stage2, vol_ma_20, rel_vol, mode_b) = row
        label = labels.get(ticker, "Unknown")

        # Gate 1: RS (3M AND 6M >= 70)
        g_rs = 1 if (rs3m is not None and rs6m is not None and rs3m >= 70 and rs6m >= 70) else 0
        # Gate 5: Stage 2 (close > MA50 > MA150 > MA200)
        g_stage2 = int(stage2) if stage2 is not None else -1
        # Gate 6: 52W high proximity
        g_52w = 1 if (pct_52w_high is not None and pct_52w_high > -20) else 0
        # Gate 7: ADTV
        g_adtv = 1 if (adtv is not None and adtv >= 10_000_000) else 0
        # Earnings gate: 1=clear, 0=blocked, -1=unknown (no calendar data)
        if earnings_block or os.path.exists(_earnings_file):
            g_earnings = 0 if ticker in earnings_block else 1
        else:
            g_earnings = -1   # calendar not fetched yet
        if ticker in earnings_block:
            gate_earnings_blocked += 1

        # Count technical gates that pass (max 4: RS + Stage2 + 52W + ADTV)
        tech_gates = g_rs + (g_stage2 if g_stage2 >= 0 else 0) + g_52w + g_adtv
        all_4_tech = (g_rs and g_stage2 == 1 and g_adtv and g_52w)

        if g_rs:    gate1_pass += 1
        if g_stage2 == 1: gate5_pass += 1
        if g_52w:   gate6_pass += 1
        if g_adtv:  gate7_pass += 1
        if all_4_tech: all4_pass += 1

        note = f"RS:{rs3m:.0f}/{rs6m:.0f}" if rs3m and rs6m else "RS:N/A"
        if adtv:
            note += f" | ADTV:${adtv/1e6:.1f}M"
        if pct_52w_high is not None:
            note += f" | 52W:{pct_52w_high:.1f}%"
        if stage2 is not None:
            note += f" | S2:{'YES' if stage2 else 'NO'}"
        if chg_1w is not None:
            note += f" | RS1W:{chg_1w:+.1f}"
        if mom_1m_3m is not None:
            note += f" | MOM:{mom_1m_3m:+.1f}"
        if g_earnings == 0:
            note += " | EARNINGS_BLOCK"

        screening.append((
            ticker, latest_date, 'A', label,
            rs1m, rs3m, rs6m, rs12m, rs_comp, chg_1w, chg_4w,
            mom_1m_3m, mom_3m_6m, mom_6m_12m, phase,
            adtv, last_close,
            high_52w, low_52w, pct_52w_high, pct_52w_low,
            pct_3m_high, pct_3m_low, pct_6m_high, pct_6m_low,
            ma50, ma150, ma200,
            pct_ma50, pct_ma150, pct_ma200,
            stage2, g_rs, g_adtv, g_52w, g_stage2,
            fund_gates.get(ticker, (-1, -1, -1))[0],   # gate_eps
            fund_gates.get(ticker, (-1, -1, -1))[1],   # gate_rev
            fund_gates.get(ticker, (-1, -1, -1))[2],   # gate_gm
            g_earnings,
            tech_gates,
            vol_ma_20, rel_vol, int(mode_b) if mode_b is not None else 0,
            note, now
        ))

    # Upsert (full column set — 46 fields)
    c.executemany("""
        INSERT INTO screening_results (
            ticker, date, mode, label,
            rs_1m_pct, rs_3m_pct, rs_6m_pct, rs_12m_pct,
            rs_composite, rs_comp_chg_1w, rs_comp_chg_4w,
            rs_momentum_1m_3m, rs_momentum_3m_6m, rs_momentum_6m_12m,
            phase, adtv_6m_usd, last_close,
            high_52w, low_52w, pct_from_52w_high, pct_from_52w_low,
            pct_from_3m_high, pct_from_3m_low, pct_from_6m_high, pct_from_6m_low,
            ma_50d, ma_150d, ma_200d,
            pct_above_ma50, pct_above_ma150, pct_above_ma200,
            stage2_flag, gate_rs, gate_adtv, gate_52w, gate_stage2,
            gate_eps, gate_rev, gate_gm,
            gate_earnings,
            gates_passed,
            vol_ma_20d, rel_vol_today, mode_b_eligible,
            screen_note, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(ticker, date) DO UPDATE SET
            rs_1m_pct           = excluded.rs_1m_pct,
            rs_3m_pct           = excluded.rs_3m_pct,
            rs_6m_pct           = excluded.rs_6m_pct,
            rs_12m_pct          = excluded.rs_12m_pct,
            rs_composite        = excluded.rs_composite,
            rs_comp_chg_1w      = excluded.rs_comp_chg_1w,
            rs_comp_chg_4w      = excluded.rs_comp_chg_4w,
            rs_momentum_1m_3m   = excluded.rs_momentum_1m_3m,
            rs_momentum_3m_6m   = excluded.rs_momentum_3m_6m,
            rs_momentum_6m_12m  = excluded.rs_momentum_6m_12m,
            phase               = excluded.phase,
            adtv_6m_usd         = excluded.adtv_6m_usd,
            last_close          = excluded.last_close,
            high_52w            = excluded.high_52w,
            low_52w             = excluded.low_52w,
            pct_from_52w_high   = excluded.pct_from_52w_high,
            pct_from_52w_low    = excluded.pct_from_52w_low,
            pct_from_3m_high    = excluded.pct_from_3m_high,
            pct_from_3m_low     = excluded.pct_from_3m_low,
            pct_from_6m_high    = excluded.pct_from_6m_high,
            pct_from_6m_low     = excluded.pct_from_6m_low,
            ma_50d              = excluded.ma_50d,
            ma_150d             = excluded.ma_150d,
            ma_200d             = excluded.ma_200d,
            pct_above_ma50      = excluded.pct_above_ma50,
            pct_above_ma150     = excluded.pct_above_ma150,
            pct_above_ma200     = excluded.pct_above_ma200,
            stage2_flag         = excluded.stage2_flag,
            gate_rs             = excluded.gate_rs,
            gate_adtv           = excluded.gate_adtv,
            gate_52w            = excluded.gate_52w,
            gate_stage2         = excluded.gate_stage2,
            gate_earnings       = excluded.gate_earnings,
            gates_passed        = excluded.gates_passed,
            vol_ma_20d          = excluded.vol_ma_20d,
            rel_vol_today       = excluded.rel_vol_today,
            mode_b_eligible     = excluded.mode_b_eligible,
            screen_note         = excluded.screen_note,
            updated_at          = excluded.updated_at
    """, screening)
    conn.commit()

    print(f"  Gate 1 (RS>=70/70): {gate1_pass} | Gate 5 (Stage2): {gate5_pass} | Gate 6 (52W>-20%): {gate6_pass} | Gate 7 (ADTV>$10M): {gate7_pass}")
    print(f"  All 4 tech gates (RS+S2+52W+ADTV): {all4_pass}")
    if gate_earnings_blocked:
        print(f"  Earnings gate blocked: {gate_earnings_blocked} tickers (within 5 trading days)")

    # ── Compute RS change for LATEST date (fill any NULLs from backfill gaps) ──
    # Get the 5th and 21st trading days before latest_date using ohlcv dates
    c.execute("""
        SELECT DISTINCT SUBSTR(date,1,10) as d FROM ohlcv
        WHERE SUBSTR(date,1,10) <= ? ORDER BY d DESC LIMIT 25
    """, (latest_date,))
    recent_dates = [r[0] for r in c.fetchall()]  # newest first

    past_1w = recent_dates[5]  if len(recent_dates) > 5  else None
    past_4w = recent_dates[21] if len(recent_dates) > 21 else None

    if past_1w:
        c.execute("""
            UPDATE rs_daily
            SET rs_comp_chg_1w = (
                rs_composite - COALESCE(
                    (SELECT p.rs_composite FROM rs_daily p
                     WHERE p.ticker = rs_daily.ticker AND SUBSTR(p.date,1,10) = ?),
                    rs_composite)
            )
            WHERE SUBSTR(date,1,10) = ? AND rs_composite IS NOT NULL
        """, (past_1w, latest_date))
        n1w = c.rowcount
        print(f"  rs_comp_chg_1w updated for {n1w} tickers (vs {past_1w})")

    if past_4w:
        c.execute("""
            UPDATE rs_daily
            SET rs_comp_chg_4w = (
                rs_composite - COALESCE(
                    (SELECT p.rs_composite FROM rs_daily p
                     WHERE p.ticker = rs_daily.ticker AND SUBSTR(p.date,1,10) = ?),
                    rs_composite)
            )
            WHERE SUBSTR(date,1,10) = ? AND rs_composite IS NOT NULL
        """, (past_4w, latest_date))
        n4w = c.rowcount
        print(f"  rs_comp_chg_4w updated for {n4w} tickers (vs {past_4w})")

    conn.commit()

    # Now re-pull chg values into screening_results for the latest date
    if past_1w or past_4w:
        c.execute(f"""
            UPDATE screening_results AS sr
            SET rs_comp_chg_1w = (SELECT r.rs_comp_chg_1w FROM rs_daily r
                                   WHERE r.ticker = sr.ticker AND SUBSTR(r.date,1,10) = ?),
                rs_comp_chg_4w = (SELECT r.rs_comp_chg_4w FROM rs_daily r
                                   WHERE r.ticker = sr.ticker AND SUBSTR(r.date,1,10) = ?)
            WHERE SUBSTR(sr.date,1,10) = ? AND sr.ticker IN ({placeholders})
        """, [latest_date, latest_date, latest_date] + universe)
        conn.commit()
        print(f"  screening_results RS change columns refreshed")

    # Top candidates (all 4 tech gates pass, sorted by RS composite, themed stocks first)
    # Also pull stage2, vol metrics, mode_b for downstream reporting
    c.execute(f"""
        SELECT ticker, label, rs_3m_pct, rs_6m_pct, rs_composite, phase,
               adtv_6m_usd, pct_from_52w_high, last_close,
               stage2_flag, pct_from_3m_high, vol_ma_20d, rel_vol_today,
               mode_b_eligible, pct_above_ma50, pct_above_ma200
        FROM screening_results
        WHERE SUBSTR(date,1,10) = ? AND gate_rs = 1 AND gate_adtv = 1 AND gate_52w = 1
          AND ticker IN ({placeholders})
        ORDER BY
            CASE WHEN label IN ('AI_Related','Memory_HBM','Space','Quantum','Photonics',
                                'DefenseTech','DataCenter','Nuclear_SMR','NeoCloud','AI_Infra',
                                'DataCenter_Infra','Drone_UAV','Robotics','Connectivity')
                 THEN 0 ELSE 1 END,
            CASE WHEN stage2_flag = 1 THEN 0 ELSE 1 END,
            rs_composite DESC
        LIMIT 50
    """, [latest_date] + universe)

    top = c.fetchall()
    return top, all4_pass


def step_e_export(conn, universe, labels, top_candidates, date):
    """Export screening results to JSON for downstream agents."""
    print("\n=== Step E: Export Screening Results ===")
    c = conn.cursor()

    placeholders = ','.join(['?' for _ in universe])

    # Full screening table export
    c.execute(f"""
        SELECT ticker, label, rs_3m_pct, rs_6m_pct, rs_composite, phase,
               adtv_6m_usd, pct_from_52w_high, last_close,
               gate_rs, gate_adtv, gate_52w, gates_passed
        FROM screening_results
        WHERE SUBSTR(date,1,10) = ? AND ticker IN ({placeholders})
        ORDER BY rs_composite DESC NULLS LAST
    """, [date] + universe)

    all_rows = c.fetchall()

    # Theme stats
    theme_stats = {}
    for row in all_rows:
        label = row[1]
        if label not in theme_stats:
            theme_stats[label] = {'total': 0, 'rs_pass': 0, 'all_pass': 0, 'rs_values': []}
        theme_stats[label]['total'] += 1
        if row[9]:  # gate_rs
            theme_stats[label]['rs_pass'] += 1
        if row[12] >= 3:  # all 3 gates pass
            theme_stats[label]['all_pass'] += 1
        if row[4]:
            theme_stats[label]['rs_values'].append(row[4])

    # Compute theme median RS
    for label, stats in theme_stats.items():
        if stats['rs_values']:
            stats['median_rs_composite'] = round(float(np.median(stats['rs_values'])), 1)
        else:
            stats['median_rs_composite'] = None
        del stats['rs_values']

    # Sort themes by median RS
    sorted_themes = sorted(theme_stats.items(),
                          key=lambda x: x[1].get('median_rs_composite') or 0,
                          reverse=True)

    # Build output
    output = {
        "date": date,
        "generated_at": datetime.now().isoformat(),
        "universe_total": len(all_rows),
        "mode_a_candidates": {
            "rs_gate_only": sum(1 for r in all_rows if r[9]),
            "adtv_gate_only": sum(1 for r in all_rows if r[10]),
            "52w_gate_only": sum(1 for r in all_rows if r[11]),
            "all_3_gates": sum(1 for r in all_rows if r[12] >= 3),
            "note": "Fundamental gates (EPS/Rev/GM) pending FMP data fetch"
        },
        "theme_heatmap": {
            label: {
                "total": stats['total'],
                "rs_pass": stats['rs_pass'],
                "all_pass": stats['all_pass'],
                "pct_rs_pass": round(stats['rs_pass'] / stats['total'] * 100, 1) if stats['total'] else 0,
                "median_rs": stats['median_rs_composite'],
                "heat": "HOT" if (stats['median_rs_composite'] or 0) >= 65 else
                        "WARM" if (stats['median_rs_composite'] or 0) >= 45 else "WEAK"
            }
            for label, stats in sorted_themes
        },
        "top_candidates": [
            {
                "ticker": row[0],
                "label": row[1],
                "rs_3m": round(row[2], 1) if row[2] else None,
                "rs_6m": round(row[3], 1) if row[3] else None,
                "rs_composite": round(row[4], 1) if row[4] else None,
                "phase": row[5],
                "adtv_m": round(row[6] / 1e6, 1) if row[6] else None,
                "pct_52w_high": round(row[7], 1) if row[7] else None,
                "last_close": round(row[8], 2) if row[8] else None,
                "gates_passed": "RS=1,ADTV=1,52W=1",
                "pending_gates": "EPS/Rev/GM/Stage2"
            }
            for row in top_candidates
        ]
    }

    out_path = f"{OUTPUT_DIR}/mode_a_screen_{date}.json"
    latest_path = f"{OUTPUT_DIR}/mode_a_screen_latest.json"
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2)
    with open(latest_path, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"  Saved to {out_path}")
    print(f"  Also saved as {latest_path}")

    return output


def print_summary(output):
    """Print human-readable summary."""
    print("\n" + "="*60)
    print("MODE A SCREENING SUMMARY")
    print("="*60)
    print(f"Date: {output['date']}")
    print(f"Universe: {output['universe_total']} tickers")
    print()
    print("GATE RESULTS:")
    m = output['mode_a_candidates']
    print(f"  RS Gate (3M>=70 & 6M>=70): {m['rs_gate_only']}")
    print(f"  ADTV Gate (>$10M):         {m['adtv_gate_only']}")
    print(f"  52W Gate (>-20% from high): {m['52w_gate_only']}")
    print(f"  All 3 Gates PASS:           {m['all_3_gates']}")
    print(f"  [Pending: EPS/Rev/GM/Stage2 gates]")
    print()
    print("THEME HEATMAP (by median RS composite):")
    print(f"  {'Theme':<20} {'Total':>6} {'RS%':>6} {'AllP':>5} {'MedRS':>7} {'Heat':>6}")
    print(f"  {'-'*55}")
    for label, stats in output["theme_heatmap"].items():
        if label in THEMES:
            med = str(round(stats["median_rs"])) if stats["median_rs"] else "N/A"
            pct = str(round(stats["pct_rs_pass"])) + "%"
            heat = stats["heat"]
            print("  " + label.ljust(20) + str(stats["total"]).rjust(6) + pct.rjust(6) + str(stats["all_pass"]).rjust(5) + med.rjust(7) + heat.rjust(6))
    print()
    print(f"  {'#':<3} {'Ticker':<8} {'Label':<20} {'RS3M':>6} {'RS6M':>6} {'Comp':>6} {'ADTV':>8} {'52W%':>7} {'Price':>8}")
    print(f"  {'-'*80}")
    for i, c in enumerate(output['top_candidates'][:30], 1):
        label = c['label'] if c['label'] else "?"
        adtv = f"${c['adtv_m']:.0f}M" if c['adtv_m'] else "N/A"
        price = f"${c['last_close']:.2f}" if c['last_close'] else "N/A"
        rs3 = f"{c['rs_3m']:.0f}" if c['rs_3m'] else "N/A"
        rs6 = f"{c['rs_6m']:.0f}" if c['rs_6m'] else "N/A"
        comp = f"{c['rs_composite']:.0f}" if c['rs_composite'] else "N/A"
        pct52 = f"{c['pct_52w_high']:.1f}%" if c['pct_52w_high'] else "N/A"
        print(f"  {i:<3} {c['ticker']:<8} {label:<20} {rs3:>6} {rs6:>6} {comp:>6} {adtv:>8} {pct52:>7} {price:>8}")


def step_d2_screening_history(conn, universe, latest_date):
    """Append today's screening_results to screening_history (append-only audit log).

    screening_history differs from screening_results:
      - screening_results: upsert (latest state only, overwritten each run)
      - screening_history: append-only (PRIMARY KEY = run_date + ticker, one row per day)
    """
    print("\n=== Step D2: Append to screening_history ===")
    c = conn.cursor()

    run_date = datetime.now().strftime("%Y-%m-%d")
    placeholders = ",".join("?" * len(universe))

    # Pull current screening_results for today
    c.execute(f"""
        SELECT
            ticker, label,
            rs_1m_pct, rs_3m_pct, rs_6m_pct, rs_composite,
            rs_momentum_1m_3m, rs_momentum_3m_6m,
            stage2_flag, gate_rs, gate_stage2, gate_52w, gate_adtv,
            gate_eps, gate_rev, gate_gm, gate_earnings,
            gates_passed, adtv_6m_usd, last_close,
            pct_from_52w_high, pct_from_3m_high, mode_b_eligible
        FROM screening_results
        WHERE SUBSTR(date,1,10) = ? AND ticker IN ({placeholders})
    """, [latest_date] + universe)

    rows = c.fetchall()
    if not rows:
        print(f"  No screening_results for {latest_date} — skipping history append")
        return 0

    history_rows = [
        (run_date, latest_date, ticker, label,
         rs1m, rs3m, rs6m, rs_comp,
         mom_1m_3m, mom_3m_6m,
         stage2, g_rs, g_stage2, g_52w, g_adtv,
         g_eps, g_rev, g_gm, g_earn,
         gates, adtv, last_close,
         pct_52w, pct_3m, mode_b)
        for (ticker, label,
             rs1m, rs3m, rs6m, rs_comp,
             mom_1m_3m, mom_3m_6m,
             stage2, g_rs, g_stage2, g_52w, g_adtv,
             g_eps, g_rev, g_gm, g_earn,
             gates, adtv, last_close,
             pct_52w, pct_3m, mode_b) in rows
    ]

    c.executemany("""
        INSERT OR REPLACE INTO screening_history (
            run_date, market_date, ticker, label,
            rs_1m_pct, rs_3m_pct, rs_6m_pct, rs_composite,
            rs_momentum_1m_3m, rs_momentum_3m_6m,
            stage2_flag, gate_rs, gate_stage2, gate_52w, gate_adtv,
            gate_eps, gate_rev, gate_gm, gate_earnings,
            gates_passed, adtv_6m_usd, last_close,
            pct_from_52w_high, pct_from_3m_high, mode_b_eligible
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, history_rows)
    conn.commit()

    print(f"  Appended {len(history_rows)} rows to screening_history for run_date={run_date}")

    # Show history depth
    c.execute("SELECT COUNT(DISTINCT run_date), COUNT(*) FROM screening_history")
    n_dates, n_total = c.fetchone()
    print(f"  History: {n_total:,} rows across {n_dates} trading dates")
    return len(history_rows)


def _log_pipeline_run(conn, step: str, status: str, duration_s: float,
                      rows_written: int = 0, error_msg: str = "") -> None:
    """Write one row to pipeline_runs for observability."""
    import uuid
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO pipeline_runs
                (run_id, run_date, step, status, duration_s, rows_written,
                 error_msg, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4())[:16],
            datetime.now().strftime("%Y-%m-%d"),
            step, status, round(duration_s, 2), rows_written,
            error_msg[:500] if error_msg else "",
            datetime.now().isoformat(),
            datetime.now().isoformat(),
        ))
        conn.commit()
    except Exception:
        pass   # never let logging crash the pipeline


def main():
    print("AlphaAbsolute — Metrics Pipeline")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"DB: {DB_PATH}")

    import time as _time

    # Load universe
    with open(LABELS_PATH) as f:
        data = json.load(f)
    labels = data['labels']
    universe = list(labels.keys())
    print(f"Universe: {len(universe)} tickers")

    conn = sqlite3.connect(DB_PATH)
    _t0 = _time.time()

    try:
        # Step A: ADTV
        step_a_adtv(conn, universe)

        # Step B: 52-week metrics
        step_b_52week(conn, universe)

        # Step B2: 3M / 6M price structure (high/low proximity)
        step_b2_price_structure(conn, universe)

        # Step B3: Moving averages (MA5/10/20/50/150/200/300) + Stage 2 flag
        step_b3_moving_averages(conn, universe)

        # Step B4: Volume MAs (20d/50d) + relative volume + Mode B eligibility
        step_b4_volume_ma(conn, universe)

        # Step C: Better RS percentiles (rerank using full distribution)
        rs_data = step_c_better_rs(conn, universe, labels)

        # Step D: Mode A screening
        latest_date = get_trading_days_ago(conn, 1)
        top_candidates, n_pass = step_d_screening(conn, universe, labels)

        # Step D2: Append to screening_history (audit log)
        n_hist = step_d2_screening_history(conn, universe, latest_date)

        # Step E: Export
        output = step_e_export(conn, universe, labels, top_candidates, latest_date)

        # Print summary
        print_summary(output)

        total_s = _time.time() - _t0
        _log_pipeline_run(conn, "pipeline_metrics", "ok", total_s, rows_written=n_hist)
        print(f"\n[OK] Pipeline complete in {total_s:.1f}s. "
              f"{n_pass} tickers pass all 4 technical Mode A gates (RS+Stage2+52W+ADTV).")
        print(f"  Next: Run pipeline_fundamentals.py to fetch EPS/Rev/GM from FMP API")

    except Exception as exc:
        import traceback
        total_s = _time.time() - _t0
        _log_pipeline_run(conn, "pipeline_metrics", "fail", total_s, error_msg=str(exc))
        traceback.print_exc()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
