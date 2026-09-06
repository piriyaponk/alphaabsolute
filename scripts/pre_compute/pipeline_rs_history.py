"""
AlphaAbsolute -- RS History Backfill
=====================================
Backfills rs_daily for the last N trading dates so we can compute
RS percentile change (1W and 4W momentum).

For each historical date:
  1. Compute raw RS_1M/3M/6M returns vs QQQ for all tickers
  2. Rerank to true percentile using full-universe distribution
  3. Store in rs_daily (INSERT OR IGNORE -- won't overwrite existing)

Adds two new columns to rs_daily if missing:
  rs_comp_chg_1w  -- composite today minus composite 5 trading days ago
  rs_comp_chg_4w  -- composite today minus composite 21 trading days ago

Run: python scripts/pre_compute/pipeline_rs_history.py
After running, pipeline_metrics.py step D will have RS change data.
"""

import sqlite3
import numpy as np
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

DB_PATH    = "data/ohlcv.db"
BACKFILL_DAYS = 30   # how many historical trading days to backfill

def main():
    print("AlphaAbsolute -- RS History Backfill")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()

    # Add RS change columns to rs_daily if missing
    for col in ["rs_comp_chg_1w", "rs_comp_chg_4w"]:
        try:
            c.execute(f"ALTER TABLE rs_daily ADD COLUMN {col} REAL")
            print(f"  Added column rs_daily.{col}")
        except:
            pass
    conn.commit()

    # ── Load ALL ohlcv into memory (1,325 tickers × ~250 bars) ──────────────
    print("\nLoading OHLCV data into memory...")
    c.execute("""
        SELECT ticker, SUBSTR(date,1,10) as d, close
        FROM ohlcv
        WHERE close IS NOT NULL
        ORDER BY ticker, d
    """)
    raw = c.fetchall()
    print(f"  Loaded {len(raw):,} rows")

    # Build dict: ticker -> [(date_str, close), ...]  sorted ascending
    ticker_data = {}
    for ticker, d, close in raw:
        if ticker not in ticker_data:
            ticker_data[ticker] = []
        ticker_data[ticker].append((d, close))

    # Get QQQ closes
    qqq = ticker_data.get("QQQ", [])
    if len(qqq) < 130:
        print("ERROR: QQQ data insufficient")
        conn.close()
        return
    print(f"  QQQ bars: {len(qqq)}")

    # All trading days (from QQQ which has most complete data)
    all_trading_days = [row[0] for row in qqq]  # sorted ascending
    all_trading_days_set = set(all_trading_days)

    # Which dates do we need to backfill?
    c.execute("SELECT DISTINCT SUBSTR(date,1,10) FROM rs_daily ORDER BY date DESC")
    existing_rs_dates = {r[0] for r in c.fetchall()}

    # Take last BACKFILL_DAYS trading days from QQQ history
    target_dates = all_trading_days[-(BACKFILL_DAYS + 5):]  # grab a few extra
    missing_dates = [d for d in target_dates if d not in existing_rs_dates]
    latest_date   = all_trading_days[-1]

    print(f"\n  Trading days in QQQ: {len(all_trading_days)}")
    print(f"  Dates already in rs_daily: {len(existing_rs_dates)}")
    print(f"  Dates to backfill: {len(missing_dates)}")
    if missing_dates:
        print(f"  Range: {missing_dates[0]} to {missing_dates[-1]}")

    # ── Build QQQ index dict: date -> close ─────────────────────────────────
    qqq_dict = {d: close for d, close in qqq}

    def get_close_at(data_list, target_date):
        """Binary search for exact date, return close or None."""
        lo, hi = 0, len(data_list) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if data_list[mid][0] == target_date:
                return data_list[mid][1]
            elif data_list[mid][0] < target_date:
                lo = mid + 1
            else:
                hi = mid - 1
        return None

    def get_close_n_days_before(data_list, target_date, n_trading_days):
        """Get close N trading days before target_date using the QQQ trading day list."""
        if target_date not in all_trading_days_set:
            return None
        idx = all_trading_days.index(target_date)
        past_idx = idx - n_trading_days
        if past_idx < 0:
            return None
        past_date = all_trading_days[past_idx]
        return get_close_at(data_list, past_date)

    def raw_rs(s_ret, i_ret):
        if s_ret is None or i_ret is None:
            return None
        return ((1 + s_ret) / (1 + i_ret) - 1) * 100

    def rerank_percentile(arr):
        """Convert raw RS values array to true percentile ranks 0-100."""
        result = np.full(len(arr), np.nan)
        valid_mask = ~np.isnan(arr)
        valid_vals = arr[valid_mask]
        if len(valid_vals) < 10:
            return result
        sorted_vals = np.sort(valid_vals)
        n = len(sorted_vals)
        valid_indices = np.where(valid_mask)[0]
        for idx in valid_indices:
            v = arr[idx]
            below = np.searchsorted(sorted_vals, v, side='left')
            ties  = np.searchsorted(sorted_vals, v, side='right') - below
            result[idx] = (below + 0.5 * ties) / n * 100
        return result

    def safe_composite(p1, p3, p6):
        vals, wts = [], []
        if not np.isnan(p1): vals.append(p1 * 0.35); wts.append(0.35)
        if not np.isnan(p3): vals.append(p3 * 0.45); wts.append(0.45)
        if not np.isnan(p6): vals.append(p6 * 0.20); wts.append(0.20)
        return sum(vals) / sum(wts) if vals else None

    def get_phase(p3, p6):
        if p3 is None or np.isnan(p3): return "Weak"
        if p3 >= 80 and (p6 is None or np.isnan(p6) or p6 >= 70): return "Leader"
        if p3 >= 60: return "Emerging"
        if p3 >= 40: return "Recovering"
        return "Weak"

    # ── Backfill missing dates ───────────────────────────────────────────────
    all_tickers = sorted(ticker_data.keys())
    WINDOWS = [(21, '1m'), (63, '3m'), (126, '6m')]

    for as_of_date in missing_dates:
        print(f"\n  Backfilling {as_of_date}...", end=" ")

        # QQQ returns at this date
        qqq_close = qqq_dict.get(as_of_date)
        if not qqq_close:
            print("SKIP (no QQQ data)")
            continue

        qqq_returns = {}
        for w_days, w_name in WINDOWS:
            qqq_past = get_close_n_days_before(qqq, as_of_date, w_days)
            qqq_returns[w_name] = (qqq_close / qqq_past - 1) if qqq_past else None

        # Compute raw RS for each ticker
        rs1m_raw = np.full(len(all_tickers), np.nan)
        rs3m_raw = np.full(len(all_tickers), np.nan)
        rs6m_raw = np.full(len(all_tickers), np.nan)

        for i, ticker in enumerate(all_tickers):
            data = ticker_data[ticker]
            curr_close = get_close_at(data, as_of_date)
            if curr_close is None:
                continue
            for j, (w_days, w_name) in enumerate(WINDOWS):
                past_close = get_close_n_days_before(data, as_of_date, w_days)
                if past_close is None or qqq_returns[w_name] is None:
                    continue
                s_ret = curr_close / past_close - 1
                rs = raw_rs(s_ret, qqq_returns[w_name])
                if rs is not None:
                    if w_name == '1m': rs1m_raw[i] = rs
                    elif w_name == '3m': rs3m_raw[i] = rs
                    elif w_name == '6m': rs6m_raw[i] = rs

        # Rerank to percentiles
        rs1m_pct = rerank_percentile(rs1m_raw)
        rs3m_pct = rerank_percentile(rs3m_raw)
        rs6m_pct = rerank_percentile(rs6m_raw)

        # Build insert rows
        rows = []
        n_valid = 0
        for i, ticker in enumerate(all_tickers):
            p1 = float(rs1m_pct[i]) if not np.isnan(rs1m_pct[i]) else None
            p3 = float(rs3m_pct[i]) if not np.isnan(rs3m_pct[i]) else None
            p6 = float(rs6m_pct[i]) if not np.isnan(rs6m_pct[i]) else None
            comp = safe_composite(
                rs1m_pct[i] if not np.isnan(rs1m_pct[i]) else np.nan,
                rs3m_pct[i] if not np.isnan(rs3m_pct[i]) else np.nan,
                rs6m_pct[i] if not np.isnan(rs6m_pct[i]) else np.nan
            )
            if comp is not None:
                phase = get_phase(p3, p6)
                rows.append((ticker, as_of_date, p1, p3, p6, comp, phase))
                n_valid += 1

        c.executemany("""
            INSERT OR IGNORE INTO rs_daily
            (ticker, date, rs_1m_pct, rs_3m_pct, rs_6m_pct, rs_composite, phase)
            VALUES (?,?,?,?,?,?,?)
        """, rows)
        conn.commit()
        print(f"inserted {n_valid} tickers")

    # ── Compute RS change columns for all dates in rs_daily ─────────────────
    print("\nComputing RS change columns (1W=5d, 4W=21d)...")

    # Get all dates in rs_daily sorted
    c.execute("SELECT DISTINCT SUBSTR(date,1,10) FROM rs_daily ORDER BY date ASC")
    rs_dates = [r[0] for r in c.fetchall()]
    print(f"  Total dates in rs_daily: {len(rs_dates)}")

    # For each date, compute change vs 5 and 21 trading days ago
    td_dates = all_trading_days  # trading days from QQQ

    updated_1w = updated_4w = 0
    for as_of_date in rs_dates:
        if as_of_date not in all_trading_days_set:
            continue
        idx = td_dates.index(as_of_date)

        for chg_days, col_name in [(5, 'rs_comp_chg_1w'), (21, 'rs_comp_chg_4w')]:
            past_idx = idx - chg_days
            if past_idx < 0:
                continue
            past_date = td_dates[past_idx]
            if past_date not in existing_rs_dates and past_date not in [d for d in missing_dates]:
                continue

            # Update via SQL join
            c.execute(f"""
                UPDATE rs_daily AS r
                SET {col_name} = (
                    r.rs_composite - (
                        SELECT p.rs_composite FROM rs_daily p
                        WHERE p.ticker = r.ticker
                          AND SUBSTR(p.date,1,10) = ?
                    )
                )
                WHERE SUBSTR(r.date,1,10) = ?
                  AND r.rs_composite IS NOT NULL
            """, (past_date, as_of_date))
            if col_name == 'rs_comp_chg_1w':
                updated_1w += c.rowcount
            else:
                updated_4w += c.rowcount

    conn.commit()
    print(f"  Updated rs_comp_chg_1w: {updated_1w} rows")
    print(f"  Updated rs_comp_chg_4w: {updated_4w} rows")

    # Verify
    c.execute("""
        SELECT SUBSTR(date,1,10), COUNT(*),
               ROUND(AVG(rs_comp_chg_1w),1),
               ROUND(AVG(rs_comp_chg_4w),1)
        FROM rs_daily
        WHERE rs_comp_chg_1w IS NOT NULL
        GROUP BY SUBSTR(date,1,10)
        ORDER BY date DESC LIMIT 5
    """)
    print("\n  RS change verification (last 5 dates):")
    print(f"  {'Date':<12} {'Tickers':>8} {'Avg_1W':>8} {'Avg_4W':>8}")
    for r in c.fetchall():
        print(f"  {r[0]:<12} {r[1]:>8} {str(r[2]):>8} {str(r[3]):>8}")

    conn.close()
    print("\n[OK] RS history backfill complete.")
    print("  Now run pipeline_metrics.py to update screening_results with RS change columns.")


if __name__ == "__main__":
    main()
