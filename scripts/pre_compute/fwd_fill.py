"""
AlphaAbsolute v2 — Forward Return Filler
=========================================
For each cohort whose fill window has elapsed, fetches current price
and calculates:
  fwd_ret_4w   = (price_now / price_entry) - 1  at 4-week mark
  fwd_ret_8w   = ...                             at 8-week mark
  fwd_ret_13w  = ...                             at 13-week mark
  qqq_ret_Nw   = same calculation for QQQ (alpha benchmark)

Runs daily but only fills rows when enough time has passed:
  4W  = 28 calendar days (≈20 trading days)
  8W  = 56 calendar days (≈40 trading days)
  13W = 91 calendar days (≈65 trading days)

Each ticker's entry price = last_close on the cohort's run_date.
Window opens 1 day after the calendar date to avoid partial-day fills.

Run: python scripts/pre_compute/fwd_fill.py
Schedule: Daily post-market (4:45 PM) in pre_market_runner.py
"""

from __future__ import annotations
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "utils"))
DB = ROOT / "data" / "ohlcv.db"

# Days after cohort_date when each window can be filled
WINDOWS = {
    "4w":  28,   # ~4 calendar weeks
    "8w":  56,   # ~8 calendar weeks
    "13w": 91,   # ~13 calendar weeks
}


def _get_price(cur: sqlite3.Cursor, ticker: str, on_date: str) -> Optional[float]:
    """Latest close ≤ on_date from ohlcv table."""
    cur.execute(
        "SELECT close FROM ohlcv WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
        (ticker, on_date),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _get_price_on(cur: sqlite3.Cursor, ticker: str, target_date: str) -> Optional[float]:
    """Close on or nearest trading day after target_date (max 5 days forward)."""
    for i in range(6):
        d = (date.fromisoformat(target_date) + timedelta(days=i)).isoformat()
        cur.execute(
            "SELECT close FROM ohlcv WHERE ticker=? AND date=? LIMIT 1", (ticker, d)
        )
        row = cur.fetchone()
        if row:
            return row[0]
    return None


def run() -> None:
    today = date.today()
    today_str = today.isoformat()

    print(f"\n{'='*55}")
    print(f"  Forward Return Filler  [{today_str}]")
    print(f"{'='*55}")

    conn = sqlite3.connect(DB)
    cur  = conn.cursor()

    # ── Load all cohorts that still have unfilled windows ────────────────────
    cur.execute("""
        SELECT cohort_date, qqq_close,
               fill_4w_date, fill_8w_date, fill_13w_date
        FROM fwd_test_cohorts
        WHERE fill_13w_date IS NULL
        ORDER BY cohort_date
    """)
    cohorts = cur.fetchall()
    print(f"  Pending cohorts: {len(cohorts)}")

    filled_total = 0

    for cohort_row in cohorts:
        cohort_date_str, qqq_entry_close, fill_4w, fill_8w, fill_13w = cohort_row
        cohort_date = date.fromisoformat(cohort_date_str)

        windows_to_fill: list[tuple[str, str, str]] = []
        for win_name, days in WINDOWS.items():
            fill_date = (cohort_date + timedelta(days=days)).isoformat()
            col_fill  = f"fill_{win_name}_date"
            already   = {"4w": fill_4w, "8w": fill_8w, "13w": fill_13w}[win_name]
            if already is None and today_str >= fill_date:
                windows_to_fill.append((win_name, fill_date, col_fill))

        if not windows_to_fill:
            continue

        # ── For each window, fetch current prices for all cohort tickers ──────
        for win_name, fill_date, col_fill in windows_to_fill:
            print(f"\n  Filling cohort {cohort_date_str} → {win_name} (fill date: {fill_date})")

            # QQQ return for this window
            qqq_now = _get_price_on(cur, "QQQ", fill_date)
            qqq_ret = None
            if qqq_entry_close and qqq_now:
                qqq_ret = round((qqq_now / qqq_entry_close) - 1, 6)

            # Get all tickers in this cohort
            cur.execute("""
                SELECT ticker, last_close
                FROM screening_history
                WHERE run_date = ?
                  AND last_close IS NOT NULL
                  AND last_close > 0
            """, (cohort_date_str,))
            ticker_rows = cur.fetchall()

            win_filled = 0
            win_missing = 0
            for ticker, entry_price in ticker_rows:
                price_now = _get_price_on(cur, ticker, fill_date)
                if price_now is None:
                    win_missing += 1
                    continue

                fwd_ret = round((price_now / entry_price) - 1, 6)
                ret_col  = f"fwd_ret_{win_name}"
                qqq_col  = f"qqq_ret_{win_name}"

                cur.execute(f"""
                    UPDATE screening_history
                    SET {ret_col}=?, {qqq_col}=?, fwd_filled_at=date('now')
                    WHERE run_date=? AND ticker=?
                """, (fwd_ret, qqq_ret, cohort_date_str, ticker))
                win_filled += 1

            # Mark cohort window as filled
            cur.execute(f"""
                UPDATE fwd_test_cohorts
                SET {col_fill} = ?
                WHERE cohort_date = ?
            """, (fill_date, cohort_date_str))

            filled_total += win_filled
            print(f"    Filled: {win_filled} | Missing data: {win_missing} | QQQ ret: {qqq_ret}")

    conn.commit()
    conn.close()

    if filled_total == 0:
        print("\n  Nothing to fill today — all windows still pending or already complete.")
    else:
        print(f"\n  Done. Total cells filled: {filled_total}")
        print(f"  Run fwd_report.py to see gate attribution analysis.")


if __name__ == "__main__":
    run()
