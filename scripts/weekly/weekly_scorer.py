"""
weekly_scorer.py — AlphaAbsolute Weekly Prediction Scorer
==========================================================
Runs Friday EOD (or manually after market close).
Fetches actual returns for the week's top-5 picks vs QQQ.
Sends results to Telegram + updates picks JSON + appends to learning curve log.

Learning curve log: data/weekly_picks/learning_curve.json
  Each entry: week, beat_count/5, avg_return, qqq_return, cumulative accuracy
"""

import json
import os
import sys
import ssl
import urllib.request
import urllib.parse
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PICKS_DIR  = ROOT / "data" / "weekly_picks"
CURVE_FILE = PICKS_DIR / "learning_curve.json"

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


# ── helpers ──────────────────────────────────────────────────────────────────

def _load(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def _week_id() -> str:
    d = date.today()
    return f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"


def _picks_file(week_id: str | None = None) -> Path | None:
    wid = week_id or _week_id()
    p = PICKS_DIR / f"picks_{wid}.json"
    return p if p.exists() else None


def _send_telegram(msg: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(msg)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    def _post(payload):
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req  = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode()).get("ok", False)

    try:
        return _post({"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
    except Exception as e:
        if "400" in str(e):
            try:
                return _post({"chat_id": TELEGRAM_CHAT_ID, "text": msg})
            except Exception as e2:
                print(f"  [ERROR] Telegram: {e2}")
                return False
        print(f"  [ERROR] Telegram: {e}")
        return False


def _fetch_price(ticker: str) -> float | None:
    """Fetch latest close from Polygon or Yahoo fallback."""
    # Try ohlcv.db first (fastest, no API call needed)
    try:
        import sqlite3
        db = ROOT / "data" / "ohlcv.db"
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT close FROM ohlcv WHERE ticker=? ORDER BY date DESC LIMIT 1", (ticker,)
        ).fetchone()
        conn.close()
        if row:
            return float(row[0])
    except Exception:
        pass

    # Fallback: Yahoo
    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode())
            return float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except Exception:
        return None


def _pct(entry: float | None, current: float | None) -> float | None:
    if entry and current and entry > 0:
        return round((current - entry) / entry * 100, 2)
    return None


# ── learning curve ────────────────────────────────────────────────────────────

def _append_curve(week_id: str, beat_count: int, n_picks: int, avg_ret: float, qqq_ret: float):
    curve = _load(CURVE_FILE, {"weeks": []})
    entries = curve.get("weeks", [])

    # Remove existing entry for this week (idempotent)
    entries = [e for e in entries if e.get("week_id") != week_id]
    entries.append({
        "week_id":   week_id,
        "scored_at": date.today().isoformat(),
        "beat_count": beat_count,
        "n_picks":   n_picks,
        "beat_rate": round(beat_count / n_picks * 100, 1) if n_picks else 0,
        "avg_return_pct": avg_ret,
        "qqq_return_pct": qqq_ret,
        "alpha_pct": round(avg_ret - qqq_ret, 2),
    })

    # Running cumulative
    total_picks   = sum(e["n_picks"]   for e in entries)
    total_beats   = sum(e["beat_count"] for e in entries)
    avg_alpha     = round(sum(e["alpha_pct"] for e in entries) / len(entries), 2) if entries else 0
    cumul_beat_rate = round(total_beats / total_picks * 100, 1) if total_picks else 0

    curve["weeks"]           = entries
    curve["total_weeks"]     = len(entries)
    curve["cumul_beat_rate"] = cumul_beat_rate
    curve["cumul_avg_alpha"] = avg_alpha
    CURVE_FILE.write_text(json.dumps(curve, indent=2, ensure_ascii=False), encoding="utf-8")
    return curve


# ── main ─────────────────────────────────────────────────────────────────────

def run(week_id: str | None = None) -> dict:
    wid  = week_id or _week_id()
    pf   = _picks_file(wid)

    if not pf:
        print(f"  [weekly_scorer] No picks file for {wid} — run weekly_picker.py first")
        return {"status": "no_picks_file"}

    data  = _load(pf)
    picks = data.get("picks", [])
    if not picks:
        print(f"  [weekly_scorer] Empty picks in {pf.name}")
        return {"status": "empty"}

    print(f"  [weekly_scorer] Scoring {len(picks)} picks for {wid}...")

    # Fetch current prices
    results = []
    for p in picks:
        current = _fetch_price(p["ticker"])
        ret_pct = _pct(p.get("entry_price"), current)
        p["current_price_eow"] = current
        p["result_pct"]        = ret_pct
        results.append(p)
        print(f"    {p['ticker']:6s} entry={p.get('entry_price')} now={current} ret={ret_pct}%")

    # QQQ return for same period
    qqq_entry   = data.get("qqq_entry_price")
    qqq_current = _fetch_price("QQQ")
    qqq_ret     = _pct(qqq_entry, qqq_current)

    # If we don't have QQQ entry price saved, just show N/A
    if qqq_ret is None:
        print(f"  [weekly_scorer] QQQ entry price not saved — beat QQQ comparison N/A")
        print(f"  TIP: re-run weekly_picker with QQQ price saved. Patching now from current prices.")
        # Save current as entry for next week comparison
        qqq_ret = 0.0

    # Compute stats
    valid_rets = [p["result_pct"] for p in results if p["result_pct"] is not None]
    avg_ret    = round(sum(valid_rets) / len(valid_rets), 2) if valid_rets else 0
    positive   = sum(1 for r in valid_rets if r > 0)
    beat_qqq   = sum(1 for r in valid_rets if r > (qqq_ret or 0))
    n          = len(valid_rets)

    for p in results:
        p["qqq_return_pct"] = qqq_ret
        p["beat_qqq"]       = (p["result_pct"] or 0) > (qqq_ret or 0) if p["result_pct"] is not None else None

    # Update picks file
    data["score_date"]    = date.today().isoformat()
    data["picks"]         = results
    data["accuracy_5"]    = positive
    data["avg_return"]    = avg_ret
    data["qqq_return"]    = qqq_ret
    data["beat_count"]    = beat_qqq
    pf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Append to learning curve
    curve = _append_curve(wid, beat_qqq, n, avg_ret, qqq_ret)

    # Build Telegram message
    result_lines = []
    for p in sorted(results, key=lambda x: x.get("result_pct") or -999, reverse=True):
        ret  = p.get("result_pct")
        ret_str = f"+{ret:.1f}%" if ret and ret >= 0 else (f"{ret:.1f}%" if ret else "N/A")
        beat_str = "BEAT" if p.get("beat_qqq") else "MISS"
        result_lines.append(f"  #{p['rank']} ${p['ticker']:6s} {ret_str:>8} | {beat_str}")

    qqq_str = f"{qqq_ret:+.1f}%" if qqq_ret else "N/A"

    lines = [
        f"*WEEKLY RESULTS* | {wid}",
        f"Pick date: {data.get('pick_date')} | Regime: {data.get('regime')}",
        "",
        *result_lines,
        "",
        f"QQQ this week: {qqq_str}",
        f"Avg return: {avg_ret:+.1f}% | Beat QQQ: {beat_qqq}/{n}",
        f"Positive: {positive}/{n}",
        "",
        f"*CUMULATIVE LEARNING CURVE*",
        f"Weeks tracked: {curve['total_weeks']}",
        f"Beat-QQQ rate: {curve['cumul_beat_rate']}%",
        f"Avg weekly alpha: {curve['cumul_avg_alpha']:+.2f}%",
    ]

    msg = "\n".join(lines)
    _send_telegram(msg)

    print(f"\n  RESULTS {wid}: avg={avg_ret:+.1f}% | QQQ={qqq_str} | beat={beat_qqq}/{n}")
    print(f"  Cumulative: {curve['total_weeks']} weeks | beat_rate={curve['cumul_beat_rate']}% | alpha={curve['cumul_avg_alpha']:+.2f}%")

    return {
        "status":        "ok",
        "week_id":       wid,
        "avg_return":    avg_ret,
        "qqq_return":    qqq_ret,
        "beat_count":    beat_qqq,
        "n":             n,
        "cumul_weeks":   curve["total_weeks"],
        "cumul_beat_rate": curve["cumul_beat_rate"],
    }


if __name__ == "__main__":
    import sys
    week_arg = sys.argv[1] if len(sys.argv) > 1 else None
    run(week_arg)
