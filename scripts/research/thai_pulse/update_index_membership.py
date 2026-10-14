"""
update_index_membership.py — Fetch SET index constituents from SET.or.th official API.

Fetches SET50, SET100, SETHD, sSET from:
  https://www.set.or.th/api/set/index/{INDEX}/composition

Saves to: data/research/thai_pulse/th_index_membership.json
with _meta field (last_fetched, next_rebalance, source).

Fallback for SET50/SET100: TradingView scanner (no auth required).
Fallback for SETHD/sSET: previous data (no live source available).

Run: monthly on the 1st (wired in pre_market_runner.py with day_filter).
Also runnable standalone: python -X utf8 scripts/research/thai_pulse/update_index_membership.py
"""
from __future__ import annotations
import json, sys
from datetime import date
from pathlib import Path
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parents[3]
INDEX_PATH = ROOT / "data" / "research" / "thai_pulse" / "th_index_membership.json"
_INDICES = ["SET50", "SET100", "SETHD", "sSET"]
_TV_SUPPORTED = {"SET50", "SET100"}


def _fetch_set_or_th(index_sym: str, session: requests.Session) -> list:
    r = session.get(
        f"https://www.set.or.th/api/set/index/{index_sym}/composition",
        timeout=12, verify=False,
    )
    r.raise_for_status()
    stocks = r.json().get("composition", {}).get("stockInfos", [])
    if not stocks:
        raise ValueError(f"Empty stockInfos for {index_sym}")
    return [s["symbol"] for s in stocks]


def _make_set_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0",
        "Accept": "application/json, */*",
        "Accept-Language": "th-TH,th;q=0.9",
        "Referer": "https://www.set.or.th/th/market/index/setindex/setindex50",
    })
    try:
        session.get("https://www.set.or.th/th/market/index/setindex/setindex50", timeout=10, verify=False)
    except Exception as e:
        print(f"[index] SET.or.th cookie seed failed: {e}")
    return session


def _fetch_tradingview(index_sym: str) -> list:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json",
        "Origin": "https://www.tradingview.com",
        "Referer": "https://www.tradingview.com/",
    }
    all_rows = []
    for start in range(0, 601, 100):
        payload = {
            "columns": ["name", "indexes"],
            "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
            "range": [start, start + 100],
        }
        r = requests.post("https://scanner.tradingview.com/thailand/scan",
                          headers=headers, json=payload, timeout=12, verify=False)
        r.raise_for_status()
        all_rows.extend(r.json().get("data", []))
    result = []
    for row in all_rows:
        name = row["d"][0]
        idx_list = row["d"][1] or []
        if not name or len(name) > 8:
            continue
        if index_sym in [i["name"] for i in idx_list]:
            result.append(name)
    if not result:
        raise ValueError(f"TradingView returned 0 tickers for {index_sym}")
    return result


def _load_existing() -> dict:
    if not INDEX_PATH.exists():
        return {}
    try:
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _log_diff(idx_sym: str, prev: list, curr: list) -> None:
    added   = sorted(set(curr) - set(prev))
    removed = sorted(set(prev) - set(curr))
    if added or removed:
        print(f"  [{idx_sym}] CHANGES: added={added} removed={removed}")
    else:
        print(f"  [{idx_sym}] No changes ({len(curr)} members)")


def run() -> None:
    print("[index] Updating SET index membership from SET.or.th ...")
    existing = _load_existing()
    today = date.today()
    yr = today.year
    candidates = [date(yr, 3, 1), date(yr, 9, 1), date(yr+1, 3, 1)]
    next_rebalance = min(d for d in candidates if d >= today).isoformat()

    new_data = {}
    sources_used = {}
    session = _make_set_session()

    for idx_sym in _INDICES:
        try:
            tickers = _fetch_set_or_th(idx_sym, session)
            tickers_bk = [f"{t}.BK" for t in tickers]
            new_data[idx_sym] = tickers_bk
            sources_used[idx_sym] = "set.or.th"
            print(f"  [{idx_sym}] SET.or.th OK -- {len(tickers_bk)} members")
            _log_diff(idx_sym, existing.get(idx_sym, []), tickers_bk)
            continue
        except Exception as e:
            print(f"  [{idx_sym}] SET.or.th failed: {e}")

        if idx_sym in _TV_SUPPORTED:
            try:
                tickers = _fetch_tradingview(idx_sym)
                tickers_bk = [f"{t}.BK" for t in tickers]
                new_data[idx_sym] = tickers_bk
                sources_used[idx_sym] = "tradingview"
                print(f"  [{idx_sym}] TradingView fallback OK -- {len(tickers_bk)} members")
                _log_diff(idx_sym, existing.get(idx_sym, []), tickers_bk)
                continue
            except Exception as e:
                print(f"  [{idx_sym}] TradingView failed: {e}")

        prev_members = existing.get(idx_sym, [])
        if prev_members:
            new_data[idx_sym] = prev_members
            sources_used[idx_sym] = "previous_data"
            print(f"  [{idx_sym}] Using previous data -- {len(prev_members)} members")
        else:
            new_data[idx_sym] = []
            sources_used[idx_sym] = "empty"
            print(f"  [{idx_sym}] WARNING: no data")

    output = {
        "_meta": {
            "last_fetched": today.isoformat(),
            "next_rebalance": next_rebalance,
            "sources": sources_used,
            "note": ("SET50/SET100/SETHD/sSET from SET.or.th official API. "
                     "Rebalances: March + September. "
                     "Fallback: TradingView (SET50/SET100), previous data (SETHD/sSET)."),
        },
    }
    output.update(new_data)
    _save(output)
    print(f"[index] Saved to {INDEX_PATH}")
    print(f"[index] Next rebalance: {next_rebalance}")


if __name__ == "__main__":
    run()
