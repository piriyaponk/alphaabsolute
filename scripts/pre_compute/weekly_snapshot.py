"""
AlphaAbsolute v2 — Weekly Forward-Test Snapshot
================================================
Captures a cohort snapshot every Friday (or on-demand) for the
forward-testing learning loop.

What it does:
1. Reads today's screening_history rows (already written by trend_template_screener.py)
2. Stamps each row with current market regime + rank_score from setups_today.json
3. Writes a fwd_test_cohorts record with gate-pass counts + QQQ/SPY close
4. Cohorts then wait 4 / 8 / 13 weeks for fwd_fill.py to fill returns

Run: Every Friday after screening, or: python scripts/pre_compute/weekly_snapshot.py
Schedule: Fridays 8:45 AM (after trend_template_screener.py)
"""

from __future__ import annotations
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

DB         = ROOT / "data" / "ohlcv.db"
REGIME_F   = ROOT / "data" / "regime" / "market_health.json"
SETUPS_F   = ROOT / "data" / "setups" / "setups_today.json"


def _load_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _get_qqq_spy_close() -> tuple[Optional[float], Optional[float]]:
    """Read latest QQQ and SPY close from ohlcv table."""
    try:
        conn = sqlite3.connect(DB)
        cur  = conn.cursor()
        qqq_close = spy_close = None
        cur.execute("SELECT close FROM ohlcv WHERE ticker='QQQ' ORDER BY date DESC LIMIT 1")
        row = cur.fetchone()
        if row: qqq_close = row[0]
        cur.execute("SELECT close FROM ohlcv WHERE ticker='SPY' ORDER BY date DESC LIMIT 1")
        row = cur.fetchone()
        if row: spy_close = row[0]
        conn.close()
        return qqq_close, spy_close
    except Exception:
        return None, None


def run(force: bool = False) -> None:
    today = date.today()
    today_str = today.isoformat()

    print(f"\n{'='*55}")
    print(f"  Weekly Snapshot  [{today_str}]")
    print(f"{'='*55}")

    # Run every Friday (weekday==4), or with force flag
    if today.weekday() != 4 and not force:
        print(f"  Today is {today.strftime('%A')} — snapshot runs Fridays only. Use force=True to override.")
        return

    conn = sqlite3.connect(DB)
    cur  = conn.cursor()

    # ── Check if cohort already exists ────────────────────────────────────────
    cur.execute("SELECT cohort_date FROM fwd_test_cohorts WHERE cohort_date=?", (today_str,))
    if cur.fetchone():
        print(f"  Cohort {today_str} already exists — skipping.")
        conn.close()
        return

    # ── Load regime ───────────────────────────────────────────────────────────
    regime_data = _load_json(REGIME_F)
    regime      = regime_data.get("regime", "Unknown")

    # ── Load setup rank_scores (for enriching screening_history) ──────────────
    setups_raw = _load_json(SETUPS_F)
    setups_list = setups_raw if isinstance(setups_raw, list) else setups_raw.get("setups", [])
    setup_map: dict[str, dict] = {}
    for s in setups_list:
        tk = s.get("ticker", "")
        if tk:
            setup_map[tk] = {
                "setup_type": s.get("setup_type"),
                "rr_ratio":   s.get("rr_ratio"),
            }

    # ── Fetch today's screening_history rows ──────────────────────────────────
    cur.execute("""
        SELECT ticker, gates_passed, rs_3m_pct, rs_6m_pct, rs_momentum_1m_3m,
               rs_composite, gate_rs, gate_stage2, gate_52w, gate_adtv,
               gate_eps, gate_rev, gate_gm
        FROM screening_history
        WHERE run_date = ?
    """, (today_str,))
    rows = cur.fetchall()
    if not rows:
        # Fall back to most recent run_date
        cur.execute("SELECT MAX(run_date) FROM screening_history")
        latest_date = cur.fetchone()[0]
        if not latest_date:
            print("  No screening_history rows found — run trend_template_screener.py first.")
            conn.close()
            return
        print(f"  [!] No rows for today — using latest run_date: {latest_date}")
        cur.execute("""
            SELECT ticker, gates_passed, rs_3m_pct, rs_6m_pct, rs_momentum_1m_3m,
                   rs_composite, gate_rs, gate_stage2, gate_52w, gate_adtv,
                   gate_eps, gate_rev, gate_gm
            FROM screening_history WHERE run_date = ?
        """, (latest_date,))
        rows = cur.fetchall()
        today_str = latest_date  # cohort date = data date

    print(f"  Rows found: {len(rows)} tickers")

    # ── Stamp regime on screening_history rows ────────────────────────────────
    # Also stamp setup_type + rank_score for tickers that have setups
    update_count = 0
    for row in rows:
        ticker = row[0]
        gates  = row[1]
        su     = setup_map.get(ticker, {})
        setup_type = su.get("setup_type")
        # Simple rank_score proxy: gates_passed * 10 + rs_3m_pct/10
        rs_3m  = row[2] or 0.0
        rs_mom = row[4] or 0.0
        rank_score = round(gates * 10 + rs_3m / 10 + rs_mom, 2)

        cur.execute("""
            UPDATE screening_history
            SET regime=?, setup_type=?, rank_score=?
            WHERE run_date=? AND ticker=?
        """, (regime, setup_type, rank_score, today_str, ticker))
        update_count += 1

    # ── Gate-pass counts ──────────────────────────────────────────────────────
    gate_counts = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
    for row in rows:
        g = row[1] or 0
        if g in gate_counts:
            gate_counts[g] += 1

    # ── QQQ and SPY closes ────────────────────────────────────────────────────
    qqq_close, spy_close = _get_qqq_spy_close()

    # ── Write cohort record ───────────────────────────────────────────────────
    cur.execute("""
        INSERT OR IGNORE INTO fwd_test_cohorts
            (cohort_date, regime, universe_n,
             n_5gate, n_4gate, n_3gate, n_2gate, n_1gate,
             qqq_close, spy_close)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        today_str, regime, len(rows),
        gate_counts[5], gate_counts[4], gate_counts[3],
        gate_counts[2], gate_counts[1],
        qqq_close, spy_close,
    ))

    conn.commit()
    conn.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n  Cohort {today_str} recorded:")
    print(f"    Regime:    {regime}")
    print(f"    Universe:  {len(rows)} tickers")
    print(f"    5-gate:    {gate_counts[5]}")
    print(f"    4-gate:    {gate_counts[4]}")
    print(f"    3-gate:    {gate_counts[3]}")
    print(f"    QQQ close: {qqq_close}")
    print(f"    SPY close: {spy_close}")
    print(f"    regime/setup stamps: {update_count} rows updated")
    print(f"\n  Next: fwd_fill.py will fill returns at +4W, +8W, +13W")
    print(f"  First fill date: ~{date.today().isoformat()} + 28 days")


if __name__ == "__main__":
    force = "--force" in sys.argv
    run(force=force)
