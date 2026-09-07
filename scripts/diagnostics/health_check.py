"""
AlphaAbsolute — System Health Check (System 4)
Checks OHLCV freshness, RS data, earnings calendar, and S4 state.
"""
from __future__ import annotations
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

def _load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}

def _days_old(date_str: str) -> int:
    try:
        return (date.today() - datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()).days
    except Exception:
        return 999

def _last_trading_day() -> date:
    d = date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _check_ohlcv():
    db = ROOT / "data" / "ohlcv.db"
    if not db.exists():
        return {"id": "ohlcv_exists", "status": "FAIL", "msg": "ohlcv.db missing"}
    try:
        with sqlite3.connect(str(db)) as conn:
            row = conn.execute("SELECT MAX(date) FROM ohlcv").fetchone()
            latest = row[0] if row else None
            days = _days_old(latest or "")
            if days > 5:
                return {"id": "ohlcv_fresh", "status": "FAIL",
                        "msg": f"OHLCV {days}d old ({latest}) — run pre_market_runner.py --mode eod"}
            elif days > 2:
                return {"id": "ohlcv_fresh", "status": "WARN",
                        "msg": f"OHLCV {days}d old ({latest})"}
            return {"id": "ohlcv_fresh", "status": "PASS", "msg": f"OHLCV fresh ({latest})"}
    except Exception as e:
        return {"id": "ohlcv_fresh", "status": "FAIL", "msg": str(e)}


def _check_rs():
    f = DATA / "rs_universe" / "latest.json"
    if not f.exists():
        return {"id": "rs_fresh", "status": "WARN", "msg": "RS data missing — run pre_market_runner.py"}
    rs = _load_json(f)
    run_date = rs.get("run_date", "")
    days = _days_old(run_date) if run_date else 999
    if days > 3:
        return {"id": "rs_fresh", "status": "WARN", "msg": f"RS data {days}d old ({run_date})"}
    return {"id": "rs_fresh", "status": "PASS", "msg": f"RS fresh ({run_date})"}


def _check_s4_state():
    f = DATA / "paper_trading" / "state.json"
    if not f.exists():
        return {"id": "s4_state", "status": "WARN",
                "msg": "S4 state missing — run v4_paper_trader.py --mode rebalance"}
    s4 = _load_json(f)
    nav = s4.get("nav", 0)
    if nav <= 0:
        return {"id": "s4_state", "status": "FAIL", "msg": "S4 NAV is zero"}
    n_pos = len(s4.get("positions", {}))
    regime = s4.get("regime", "?")
    return {"id": "s4_state", "status": "PASS",
            "msg": f"S4 OK | NAV ${nav:,.0f} | {n_pos} positions | Regime: {regime}"}


def _check_earnings_cal():
    f = DATA / "regime" / "earnings_next30.json"
    if not f.exists():
        return {"id": "earnings_cal", "status": "WARN", "msg": "Earnings calendar missing"}
    ec = _load_json(f)
    gen = ec.get("generated_at", ec.get("date", ""))
    days = _days_old(gen[:10]) if gen else 999
    if days > 5:
        return {"id": "earnings_cal", "status": "WARN", "msg": f"Earnings calendar {days}d old"}
    return {"id": "earnings_cal", "status": "PASS", "msg": f"Earnings calendar fresh ({gen[:10]})"}


def run() -> dict:
    checks = [
        _check_ohlcv(),
        _check_rs(),
        _check_s4_state(),
        _check_earnings_cal(),
    ]

    passed  = sum(1 for c in checks if c["status"] == "PASS")
    warned  = sum(1 for c in checks if c["status"] == "WARN")
    failed  = sum(1 for c in checks if c["status"] == "FAIL")

    result = {
        "generated_at": datetime.now().isoformat(),
        "date": date.today().isoformat(),
        "checks": checks,
        "passed": passed,
        "warned": warned,
        "failed": failed,
        "overall": "FAIL" if failed > 0 else ("WARN" if warned > 0 else "PASS"),
    }

    out_dir = DATA / "health"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "health_report.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )

    for c in checks:
        tag = "[PASS]" if c["status"] == "PASS" else ("[WARN]" if c["status"] == "WARN" else "[FAIL]")
        print(f"  {tag} {c['id']}: {c['msg']}")

    return result


def run_with_heal() -> dict:
    """Called by pre_market_runner — same as run() for S4."""
    return run()


if __name__ == "__main__":
    r = run()
    sys.exit(0 if r["overall"] != "FAIL" else 1)
