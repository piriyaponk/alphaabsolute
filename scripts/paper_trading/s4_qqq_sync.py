"""
S4 QQQ Benchmark Sync
======================
Patches qqq_nav_history in S4 state.json with actual DB prices.
Runs daily in pipeline (before Obsidian sync) so alpha is always accurate.

Root cause fixed: v4_paper_trader.py is archived (BOA-022 Option A),
so qqq_nav_history was never updated past the last manual run date.
"""

import json, sqlite3
from datetime import date
from pathlib import Path

ROOT       = Path(__file__).resolve().parents[2]
STATE_PATH = ROOT / "data" / "paper_trading" / "state.json"
OHLCV_DB   = ROOT / "data" / "ohlcv.db"


def run():
    if not STATE_PATH.exists():
        print("[SKIP] state.json not found")
        return

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if "qqq_nav_history" not in state:
        print("[SKIP] no qqq_nav_history in state")
        return

    conn = sqlite3.connect(str(OHLCV_DB))
    rows = conn.execute(
        "SELECT date, close FROM ohlcv WHERE ticker='QQQ' ORDER BY date"
    ).fetchall()
    conn.close()

    patched = 0
    for date_str, close in rows:
        if close is None:
            continue
        old = state["qqq_nav_history"].get(date_str)
        if old != float(close):
            state["qqq_nav_history"][date_str] = float(close)
            patched += 1

    # Recalculate alpha for display
    nav      = state.get("nav", 0)
    qqq_inc  = state.get("qqq_inception", 0)
    if nav and qqq_inc:
        latest_date = max(state["qqq_nav_history"].keys())
        latest_qqq  = state["qqq_nav_history"][latest_date]
        s4_ret  = (nav / 1_000_000 - 1) * 100
        qqq_ret = (latest_qqq / qqq_inc - 1) * 100
        alpha   = s4_ret - qqq_ret
        print(f"[S4 QQQ Sync] {patched} entries patched | QQQ latest={latest_date} ${latest_qqq:.2f}")
        print(f"  S4={s4_ret:+.2f}%  QQQ={qqq_ret:+.2f}%  Alpha={alpha:+.2f}%")
    else:
        print(f"[S4 QQQ Sync] {patched} entries patched")

    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


if __name__ == "__main__":
    run()
