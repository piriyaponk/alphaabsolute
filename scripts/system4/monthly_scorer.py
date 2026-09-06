"""
System 4 — Monthly Performance Scorer

Runs on rebalance day (end of month). Computes:
  - Portfolio return vs QQQ since inception and last-30d
  - Per-position attribution (winner/loser)
  - Appends to data/system4/learning_curve.json for track record

Also callable mid-month for a snapshot.

Usage:
  python -X utf8 scripts/system4/monthly_scorer.py
"""
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

STATE_FILE   = ROOT / "data" / "paper_trading" / "state.json"
CURVE_FILE   = ROOT / "data" / "system4" / "learning_curve.json"
OHLCV_DB     = ROOT / "data" / "ohlcv.db"

TODAY = date.today().isoformat()


def _load_state() -> dict:
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def _load_curve() -> list:
    if CURVE_FILE.exists():
        try:
            return json.loads(CURVE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_curve(curve: list):
    CURVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURVE_FILE.write_text(json.dumps(curve, indent=2, default=str), encoding="utf-8")


def _latest_price(ticker: str) -> float | None:
    """Get latest close from ohlcv.db."""
    try:
        import sqlite3
        db = sqlite3.connect(OHLCV_DB)
        row = db.execute(
            "SELECT close FROM ohlcv WHERE ticker=? ORDER BY date DESC LIMIT 1",
            (ticker,)
        ).fetchone()
        db.close()
        return float(row[0]) if row else None
    except Exception:
        return None


def _pct(a: float, b: float) -> float:
    if b and b != 0:
        return (a - b) / b * 100
    return 0.0


def score_performance() -> dict:
    state = _load_state()

    nav_now       = state.get("nav", 0)
    inception_nav = state.get("inception_nav", 1_000_000)
    inception_date= state.get("inception_date", TODAY)
    qqq_inception = state.get("qqq_inception")
    positions     = state.get("positions", {})

    # Portfolio return since inception
    port_return_total = _pct(nav_now, inception_nav)

    # QQQ return since inception (from state if available, else fetch)
    qqq_now = _latest_price("QQQ")
    if qqq_now and qqq_inception:
        qqq_return_total = _pct(qqq_now, float(qqq_inception))
    else:
        qqq_return_total = None

    alpha_total = (port_return_total - qqq_return_total) if qqq_return_total is not None else None

    # Last 30d from nav_history (dict keyed by date)
    nav_history = state.get("nav_history", {})
    qqq_history = state.get("qqq_nav_history", {})
    port_return_30d = None
    qqq_return_30d  = None
    if isinstance(nav_history, dict) and len(nav_history) >= 2:
        vals = [nav_history[k] for k in sorted(nav_history)]
        port_return_30d = _pct(vals[-1], vals[0])
    if isinstance(qqq_history, dict) and len(qqq_history) >= 2:
        vals = [qqq_history[k] for k in sorted(qqq_history)]
        qqq_return_30d = _pct(vals[-1], vals[0])
    alpha_30d = (port_return_30d - qqq_return_30d) if (port_return_30d is not None and qqq_return_30d is not None) else None

    # Per-position attribution
    attribution = []
    for ticker, pos in positions.items():
        entry = pos.get("entry_price", 0)
        weight= pos.get("weight", 0)
        latest= _latest_price(ticker) or entry
        ret   = _pct(latest, entry)
        attribution.append({
            "ticker": ticker,
            "weight_pct": round(weight * 100, 1),
            "return_pct": round(ret, 2),
            "contribution_bps": round(weight * ret * 100, 1),
        })
    attribution.sort(key=lambda x: x["contribution_bps"], reverse=True)

    # Realized P&L
    realized = state.get("realized_pnl", 0)

    result = {
        "date":                TODAY,
        "inception_date":      inception_date,
        "nav":                 round(nav_now, 2),
        "inception_nav":       inception_nav,
        "port_return_total_pct": round(port_return_total, 2),
        "qqq_return_total_pct":  round(qqq_return_total, 2) if qqq_return_total is not None else None,
        "alpha_total_pct":       round(alpha_total, 2) if alpha_total is not None else None,
        "port_return_30d_pct":   round(port_return_30d, 2) if port_return_30d is not None else None,
        "qqq_return_30d_pct":    round(qqq_return_30d, 2) if qqq_return_30d is not None else None,
        "alpha_30d_pct":         round(alpha_30d, 2) if alpha_30d is not None else None,
        "n_positions":           len(positions),
        "realized_pnl":          realized,
        "attribution":           attribution,
    }
    return result


def _send_telegram(msg: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat  = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    import requests
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg},
            timeout=10,
        )
    except Exception:
        pass


def run():
    print("[S4 Monthly Scorer] Computing performance...")
    perf = score_performance()

    # Append to learning curve
    curve = _load_curve()
    curve.append(perf)
    _save_curve(curve)

    # Print summary
    alpha = perf.get("alpha_total_pct")
    alpha_str = f"{alpha:+.2f}pp" if alpha is not None else "N/A"
    print(f"  NAV:         ${perf['nav']:,.0f}")
    print(f"  Port return: {perf['port_return_total_pct']:+.2f}% (since {perf['inception_date']})")
    if perf.get("qqq_return_total_pct") is not None:
        print(f"  QQQ return:  {perf['qqq_return_total_pct']:+.2f}%")
    print(f"  Alpha:       {alpha_str}")
    print(f"  Positions:   {perf['n_positions']}")
    print()
    print("  Top contributors:")
    for p in perf["attribution"][:5]:
        print(f"    {p['ticker']:6s}  {p['weight_pct']:.1f}%  ret={p['return_pct']:+.1f}%  contrib={p['contribution_bps']:+.0f}bps")

    # Telegram push
    a_str = f"{alpha:+.2f}pp" if alpha is not None else "N/A"
    msg = (
        f"📊 S4 Monthly Score [{TODAY}]\n"
        f"NAV: ${perf['nav']:,.0f}\n"
        f"Port: {perf['port_return_total_pct']:+.2f}% | QQQ: {perf.get('qqq_return_total_pct') or '?'}"
        f"% | Alpha: {a_str}\n"
        f"Positions: {perf['n_positions']} | Saved to learning_curve.json"
    )
    _send_telegram(msg)
    print(f"\n[S4 Monthly Scorer] Saved → {CURVE_FILE}")
    return perf


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    run()
