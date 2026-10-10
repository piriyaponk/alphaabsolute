"""
dr_refresh.py — Monthly DR universe refresh

Fetches latest 551+ DR list from Settrade via selenium/requests fallback.
Since Settrade API returns 403 from external Python, this script:
  1. Tries direct API fetch (works if Settrade changes CORS policy)
  2. Falls back to reading a manually-fetched JSON (dr_universe_fresh.json)
  3. Logs that manual refresh is needed if stale > 35 days

Runs 1st of each month via pre_market_runner.py.
Output: data/research/dr/dr_universe.json + dr_map.json (rebuilt)
"""
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

DR_DIR = ROOT / "data/research/dr"
UNIVERSE_PATH = DR_DIR / "dr_universe.json"
MAP_PATH = DR_DIR / "dr_map.json"
FRESH_PATH = DR_DIR / "dr_universe_fresh.json"  # manual drop zone

SETTRADE_API = "https://www.settrade.com/api/set/dr/list?lang=en&page=1&pageSize=600"
MAX_AGE_DAYS = 35  # warn if older than this


def _age_days() -> int:
    if not MAP_PATH.exists():
        return 9999
    try:
        built_at = json.loads(MAP_PATH.read_text(encoding="utf-8")).get("built_at", "")
        return (date.today() - datetime.fromisoformat(built_at).date()).days
    except Exception:
        return 9999


def _try_direct_fetch() -> list[dict] | None:
    """Try direct API fetch (usually 403 — Settrade blocks external Python)."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.settrade.com/th/equities/dr/overview",
        }
        resp = requests.get(SETTRADE_API, headers=headers, timeout=15, verify=False)
        if resp.status_code == 200:
            data = resp.json()
            rows = data.get("data", data.get("rows", []))
            if rows:
                print(f"  [OK] Direct API fetch: {len(rows)} rows")
                return rows
    except Exception as e:
        print(f"  [WARN] Direct fetch failed: {e}")
    return None


def _try_fresh_file() -> list[dict] | None:
    """Check for manually-dropped dr_universe_fresh.json. Parse only — do NOT unlink here."""
    if not FRESH_PATH.exists():
        return None
    try:
        data = json.loads(FRESH_PATH.read_text(encoding="utf-8"))
        rows = data.get("rows", data) if isinstance(data, dict) else data
        if rows:
            print(f"  [OK] Fresh file found: {len(rows)} rows")
            return rows
    except Exception as e:
        print(f"  [WARN] Fresh file parse failed: {e}")
    return None


def run():
    age = _age_days()
    print(f"[DR-Refresh] DR map age: {age} days")

    if age < 25:
        print(f"  Universe is fresh ({age}d < 25d) — skipping refresh")
        return {"status": "skipped", "age_days": age}

    # Attempt 1: manual drop zone
    rows = _try_fresh_file()

    # Attempt 2: direct API
    if not rows:
        rows = _try_direct_fetch()

    if rows:
        from scripts.research.dr.dr_mapper import build_from_json
        # Normalize field names (Settrade returns camelCase in some versions)
        normalized = []
        for r in rows:
            normalized.append({
                "s": r.get("symbol") or r.get("set_symbol") or r.get("s", ""),
                "u": r.get("underlying") or r.get("underlyingSymbol") or r.get("u", ""),
                "issuer": r.get("issuerShortName") or r.get("issuer", ""),
                "val": float(r.get("totalValue") or r.get("value") or r.get("val") or 0),
            })
        valid = [r for r in normalized if r.get("s") and r.get("u")]
        if not valid:
            raise ValueError("All rows invalid after normalization — no s/u fields found")
        build_from_json(valid)
        # Only unlink the manual drop file AFTER successful write
        if FRESH_PATH.exists():
            FRESH_PATH.unlink()
        return {"status": "refreshed", "rows": len(valid)}
    else:
        msg = (
            f"[DR-Refresh] MANUAL REFRESH NEEDED — DR map is {age} days old.\n"
            f"To refresh: open https://www.settrade.com/th/equities/dr/overview in browser,\n"
            f"run JS fetch in console, save to {FRESH_PATH},\n"
            f"then re-run dr_refresh.py"
        )
        print(msg)

        # Send Telegram warning if very stale
        if age > MAX_AGE_DAYS:
            _warn_telegram(age)

        return {"status": "stale", "age_days": age}


def _warn_telegram(age: int):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    msg = f"⚠️ DR Universe stale ({age}d). Manual refresh needed.\nDrop JSON at: data/research/dr/dr_universe_fresh.json"
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=10,
            verify=False,
        )
    except Exception:
        pass


if __name__ == "__main__":
    result = run()
    print(f"Result: {result}")
