"""
dr_daily_screen.py — PULSE-DR daily screen

Takes top US PULSE signals (pulse_us_daily_signals.json),
filters to those with SET DRs, outputs top 5 "PULSE-DR" section,
and sends Telegram notification.

Run after pulse_us_daily.py completes.
Output: data/research/dr/dr_daily_signals.json
"""
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

DR_MAP_PATH = ROOT / "data/research/dr/dr_map.json"
US_SIGNALS_PATH = ROOT / "data/research/pulse_us/pulse_us_daily_signals.json"
SHORTLIST_PATH = ROOT / "data/research/shortlist_today.json"
FOCUS_LIST_PATH = ROOT / "data/leadership/top30_watchlist.json"
OUTPUT_PATH = ROOT / "data/research/dr/dr_daily_signals.json"

MAX_DR_PICKS = 5


def load_dr_map() -> dict:
    if not DR_MAP_PATH.exists():
        raise FileNotFoundError(f"DR map missing: {DR_MAP_PATH}. Run dr_mapper.py first.")
    return json.loads(DR_MAP_PATH.read_text(encoding="utf-8"))["map"]


def load_us_signals() -> dict:
    if not US_SIGNALS_PATH.exists():
        raise FileNotFoundError(f"US signals missing: {US_SIGNALS_PATH}")
    return json.loads(US_SIGNALS_PATH.read_text(encoding="utf-8"))


def load_focus_list() -> list[str]:
    """Load tickers from AlphaAbsolute focus list (A06 top30) and shortlist."""
    focus = set()
    # A06 top30 watchlist
    if FOCUS_LIST_PATH.exists():
        data = json.loads(FOCUS_LIST_PATH.read_text(encoding="utf-8"))
        for item in data if isinstance(data, list) else data.get("watchlist", data.get("tickers", [])):
            t = item.get("ticker", item) if isinstance(item, dict) else str(item)
            focus.add(t.upper())
    # shortlist_today.json us_candidates
    if SHORTLIST_PATH.exists():
        data = json.loads(SHORTLIST_PATH.read_text(encoding="utf-8"))
        for c in data.get("us_candidates", []):
            t = c.get("ticker", c) if isinstance(c, dict) else str(c)
            focus.add(t.upper())
    return list(focus)


def pick_dr_candidates(us_signals: dict, dr_map: dict) -> list[dict]:
    """
    Take ALL PULSE-US tickers → filter those with SET DR → rank → top MAX_DR_PICKS.

    Source: pulse_scores (all tickers screened today, breadth=0 included).
    Rank: breadth DESC → avg_h3 DESC → val_thb DESC.
    Focus list tickers appended at end if not already in results (tagged FocusList).
    """
    pulse_scores = us_signals.get("pulse_scores", {})
    seen: set[str] = set()
    candidates: list[dict] = []

    for ticker, sc in pulse_scores.items():
        if ticker not in dr_map:
            continue
        seen.add(ticker)
        dr_info = dr_map[ticker]
        candidates.append({
            "us_ticker": ticker,
            "set_symbol": dr_info["set_symbol"],
            "issuer": dr_info["issuer"],
            "issuer_code": dr_info["issuer_code"],
            "val_thb": dr_info["val_thb"],
            "all_dr": dr_info["all_dr"],
            "h3_score": sc.get("avg_h3", 0),
            "breadth": sc.get("breadth", 0),
            "n_signals": sc.get("n_signals_fired", 0),
            "top_signal_labels": sc.get("top_signals", [])[:3],
            "source": "PULSE-US",
        })

    # Append Focus List tickers not already covered
    focus_tickers = load_focus_list()
    for ticker in focus_tickers:
        if ticker not in dr_map or ticker in seen:
            continue
        seen.add(ticker)
        dr_info = dr_map[ticker]
        sc = pulse_scores.get(ticker, {})
        candidates.append({
            "us_ticker": ticker,
            "set_symbol": dr_info["set_symbol"],
            "issuer": dr_info["issuer"],
            "issuer_code": dr_info["issuer_code"],
            "val_thb": dr_info["val_thb"],
            "all_dr": dr_info["all_dr"],
            "h3_score": sc.get("avg_h3", 0),
            "breadth": sc.get("breadth", 0),
            "n_signals": sc.get("n_signals_fired", 0),
            "top_signal_labels": sc.get("top_signals", [])[:3],
            "source": "FocusList",
        })

    # Sort: h3_score DESC → breadth DESC → val_thb tiebreak
    # (breadth * h3_score) was wrong — it scores zero for high-h3 single-signal tickers
    candidates.sort(key=lambda x: (-x["h3_score"], -x["breadth"], -x["val_thb"]))
    return candidates[:MAX_DR_PICKS]


def format_telegram(picks: list[dict], today: str, n_screened: int = 0) -> str:
    if not picks:
        screened_note = f" ({n_screened} US tickers screened)" if n_screened else ""
        return f"📡 PULSE-DR [{today}]\nNo DR candidates today.{screened_note}"

    lines = [f"📡 PULSE-DR [{today}] — Top {len(picks)} of {n_screened} screened\n"]
    for i, p in enumerate(picks, 1):
        val_m = p["val_thb"] / 1_000_000
        alts = ", ".join(p["all_dr"]) if len(p["all_dr"]) > 1 else ""
        line = (
            f"{i}. ${p['us_ticker']} → {p['set_symbol']} ({p['issuer']})\n"
            f"   h3={p['h3_score']:.0f}% | vol={val_m:.1f}M THB | signals={p['n_signals']}\n"
        )
        if alts:
            line += f"   alts: {alts}\n"
        lines.append(line)

    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("[WARN] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping push")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, json=payload, timeout=10, verify=False)
        if resp.status_code == 400:
            # Retry without parse_mode (unescaped special chars)
            payload.pop("parse_mode")
            resp = requests.post(url, json=payload, timeout=10, verify=False)
        resp.raise_for_status()
        print(f"[OK] Telegram sent ({len(text)} chars)")
        return True
    except Exception as e:
        print(f"[ERROR] Telegram failed: {e}")
        return False


def run():
    today = str(date.today())
    print(f"[PULSE-DR] Running for {today}")

    dr_map = load_dr_map()
    print(f"  DR map loaded: {len(dr_map)} US tickers")

    us_signals = load_us_signals()
    pulse_scores = us_signals.get("pulse_scores", {})
    active_pulse = sum(1 for sc in pulse_scores.values() if sc.get("breadth", 0) > 0)
    print(f"  US signals loaded: {len(pulse_scores)} tickers ({active_pulse} with breadth>0)")

    n_screened = len(us_signals.get("pulse_scores", {}))
    picks = pick_dr_candidates(us_signals, dr_map)
    print(f"  DR picks: {len(picks)}")
    for p in picks:
        print(f"    {p['us_ticker']:8s} -> {p['set_symbol']:15s} h3={p['h3_score']:.0f}% val={p['val_thb']/1e6:.1f}M THB")

    # Save output
    output = {
        "date": today,
        "dr_map_version": json.loads(DR_MAP_PATH.read_text(encoding="utf-8")).get("built_at", ""),
        "n_screened": n_screened,
        "n_picks": len(picks),
        "picks": picks,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved → {OUTPUT_PATH}")

    # Telegram
    msg = format_telegram(picks, today, n_screened=n_screened)
    print("\n" + msg)
    send_telegram(msg)

    return output


if __name__ == "__main__":
    run()
