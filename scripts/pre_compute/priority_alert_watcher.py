"""
AlphaAbsolute v2 -- Priority Alert Watcher
=======================================
CIO-designated priority tickers get a dedicated "don't miss" Telegram alert
the moment they hit a valid Minervini-style buy point -- independent of
whether they made today's curated Top 30 (A06) or Big Shot (A07) lists.

Two trigger paths per ticker:
  1. AUTHORITATIVE -- ticker appears in today's setups_today.json (A08) with
     Grade A/B, not context-only, not "wait for better entry", and
     current_price >= pivot. Uses A08's own entry/stop/target/RR.
  2. RAW FALLBACK -- ticker not in today's curated A08 output (e.g. it
     didn't make A06/A07's daily shortlist). Proxy check: close >= trailing
     63-day high (Monster Scout hard gate) AND volume >= 1.5x 20d average.
     Flagged clearly as unverified -- not a graded setup.

A state file prevents re-pinging the same still-extended trigger every
single day (re-alert only after REALERT_DAYS calendar days).

Config: data/watchlist/priority_alerts.json   (CIO-edited ticker list)
State:  data/watchlist/priority_alerts_state.json
Output: data/watchlist/priority_alerts_status.json  (today's status, all tickers)

Run: after A08 Setup Scanner, same premarket pass.
Cost: $0 (SQLite + existing Telegram creds only)
"""

from __future__ import annotations
import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
WATCH_DIR = ROOT / "data" / "watchlist"
WATCH_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = WATCH_DIR / "priority_alerts.json"
STATE_FILE  = WATCH_DIR / "priority_alerts_state.json"
STATUS_FILE = WATCH_DIR / "priority_alerts_status.json"
SETUPS_FILE = ROOT / "data" / "setups" / "setups_today.json"
DB_PATH     = ROOT / "data" / "ohlcv.db"

REALERT_DAYS = 10  # don't re-ping a still-extended trigger more than once per N calendar days

DEFAULT_CONFIG = {
    "updated": date.today().isoformat(),
    "note": "CIO priority watch list -- Telegram alert the moment any ticker hits a valid Minervini-style buy point.",
    "tickers": ["MU", "SNDK", "OUST", "MRVL"],
}


def _load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception:
        return default


def _save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _load_priority_config() -> dict:
    cfg = _load_json(CONFIG_FILE)
    if not cfg or not cfg.get("tickers"):
        cfg = DEFAULT_CONFIG
        _save_json(CONFIG_FILE, cfg)
    return cfg


def _fetch_bars(ticker: str, days: int = 140) -> list[dict]:
    if not DB_PATH.exists():
        return []
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            rows = conn.execute(
                """SELECT date, high, low, close, volume FROM ohlcv
                   WHERE ticker = ? ORDER BY date ASC""",
                (ticker,),
            ).fetchall()
    except Exception:
        return []
    bars = []
    for r in rows:
        rdate, high, low, close, vol = r
        if high is None or low is None or close is None:
            continue
        bars.append({"date": rdate, "high": float(high), "low": float(low),
                      "close": float(close), "volume": float(vol or 0)})
    return bars[-days:]


def _raw_breakout_check(bars: list[dict]) -> dict:
    if len(bars) < 30:
        return {"status": "NO_DATA"}
    today = bars[-1]
    prior = bars[:-1]
    high_63  = max(b["high"] for b in prior[-63:]) if len(prior) >= 20 else None
    high_126 = max(b["high"] for b in prior[-126:]) if len(prior) >= 126 else None
    vol20 = (sum(b["volume"] for b in prior[-20:]) / min(20, len(prior))) if prior else None
    vol_ratio = (today["volume"] / vol20) if vol20 else None
    is_63d_breakout = high_63 is not None and today["close"] >= high_63
    is_breakout = bool(is_63d_breakout and vol_ratio and vol_ratio >= 1.5)
    pct_from_63d_high = ((today["close"] / high_63) - 1) * 100 if high_63 else None
    return {
        "status": "BREAKOUT" if is_breakout else "WATCHING",
        "as_of": today["date"],
        "close": today["close"],
        "high_63d": high_63,
        "high_126d": high_126,
        "pct_from_63d_high": round(pct_from_63d_high, 2) if pct_from_63d_high is not None else None,
        "vol_ratio_20d": round(vol_ratio, 2) if vol_ratio else None,
    }


def _load_setups_today() -> dict:
    d = _load_json(SETUPS_FILE, {})
    return d if isinstance(d, dict) else {}


def _import_send_telegram():
    path = ROOT / "scripts" / "output" / "report_writer.py"
    spec = importlib.util.spec_from_file_location("report_writer_for_alerts", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.send_telegram


def _fmt_money(v) -> str:
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "n/a"


def run() -> dict:
    today = date.today().isoformat()
    print(f"\n{'='*55}")
    print(f"  Priority Alert Watcher  [{today}]")
    print(f"{'='*55}")

    cfg = _load_priority_config()
    tickers = cfg.get("tickers", [])

    setups_today = _load_setups_today()
    setups_date = setups_today.get("date")
    setups_by_ticker = {s["ticker"]: s for s in setups_today.get("setups", [])}

    state = _load_json(STATE_FILE, {}) or {}
    status_out = {"date": today, "setups_data_date": setups_date, "tickers": {}}

    send_telegram = None
    alerts_sent = 0

    for t in tickers:
        entry = setups_by_ticker.get(t)
        triggered = False
        source = None
        detail: dict = {}

        if (
            entry
            and setups_date == today  # A08 snapshot must be from TODAY -- a stale
                                       # snapshot's current_price is not a live trigger
            and entry.get("setup_grade") in ("A", "B")
            and not entry.get("context_only")
            and not entry.get("wait_for_better_entry")
            and entry.get("current_price", 0) >= entry.get("pivot", float("inf"))
        ):
            triggered = True
            source = "A08_SETUP_SCANNER"
            detail = {
                "setup_type":    entry.get("setup_type"),
                "grade":         entry.get("setup_grade"),
                "pivot":         entry.get("pivot"),
                "buy_zone":      entry.get("buy_zone"),
                "stop":          entry.get("stop"),
                "target_1":      entry.get("target_1"),
                "target_2":      entry.get("target_2"),
                "rr_ratio":      entry.get("rr_ratio"),
                "current_price": entry.get("current_price"),
                "theme":         entry.get("theme"),
                "entry_note":    entry.get("entry_note"),
            }
        else:
            bars = _fetch_bars(t)
            raw = _raw_breakout_check(bars)
            detail = raw
            if raw.get("status") == "BREAKOUT":
                triggered = True
                source = "RAW_63D_BREAKOUT"

        status_out["tickers"][t] = {
            "triggered": triggered,
            "source": source,
            "detail": detail,
            "in_curated_setups_today": entry is not None,
        }

        if not triggered:
            print(f"  [WATCH] {t}: no trigger yet ({detail.get('status', 'n/a')})")
            continue

        prior = state.get(t, {})
        last_date = prior.get("last_alert_date")
        resend = True
        if last_date:
            try:
                days_since = (date.today() - date.fromisoformat(last_date)).days
                resend = days_since >= REALERT_DAYS
            except Exception:
                resend = True

        if not resend:
            print(f"  [SKIP] {t}: already alerted {last_date} (<{REALERT_DAYS}d ago)")
            continue

        if send_telegram is None:
            send_telegram = _import_send_telegram()

        if source == "A08_SETUP_SCANNER":
            bz = detail.get("buy_zone") or [None, None]
            msg = (
                f"\U0001F3AF\U0001F6A8 *PRIORITY BUY ALERT: ${t}*\n"
                f"Setup: {detail.get('setup_type')} | Grade: {detail.get('grade')} | {detail.get('theme','')}\n"
                f"Pivot: {_fmt_money(detail.get('pivot'))} | Buy zone: {_fmt_money(bz[0])}-{_fmt_money(bz[1])}\n"
                f"Stop: {_fmt_money(detail.get('stop'))} | Target: {_fmt_money(detail.get('target_1'))} | "
                f"RR: {detail.get('rr_ratio')}x\n"
                f"Current: {_fmt_money(detail.get('current_price'))}\n"
                f"⚡ CIO priority watch — {str(detail.get('entry_note') or '')[:80]}"
            )
        else:
            msg = (
                f"\U0001F3AF⚠️ *PRIORITY WATCH — RAW BREAKOUT: ${t}*\n"
                f"Close {_fmt_money(detail.get('close'))} >= 63d high {_fmt_money(detail.get('high_63d'))}\n"
                f"Volume {detail.get('vol_ratio_20d')}x 20d avg\n"
                f"NOT yet in curated A06/A07 screen today ({setups_date or 'no data'}) — verify manually before entry.\n"
                f"⚡ CIO priority watch (raw proxy signal)"
            )

        ok = send_telegram(msg)
        if ok:
            alerts_sent += 1
            state[t] = {
                "last_alert_date":   today,
                "last_alert_source": source,
                "last_alert_detail": detail,
            }
        print(f"  [{'SENT' if ok else 'FAIL'}] {t}: {source}")

    _save_json(STATE_FILE, state)
    _save_json(STATUS_FILE, status_out)

    print(f"  -> Status: {STATUS_FILE}")
    print(f"  -> Alerts sent: {alerts_sent}")

    return {
        "date": today,
        "tickers_watched": tickers,
        "alerts_sent": alerts_sent,
        "status_file": str(STATUS_FILE),
    }


if __name__ == "__main__":
    run()
