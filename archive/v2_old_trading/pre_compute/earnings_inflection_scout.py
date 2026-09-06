"""
AlphaAbsolute v2 — Earnings Inflection Scout
=============================================
Scans all 270 themed tickers for revenue inflection signals.
Feeds Monster Scout (A07) as priority candidates.

Revenue inflection labels (Fund Manager spec):
  FIRST_INFLECTION  — rev_yoy Q0 >= 25% AND Q1 < 25% (first time crossing threshold)
  TURNAROUND        — rev_yoy Q0 > 0% AND Q1 <= 0% (first positive after negative)
  ACCELERATING      — rev_yoy Q0 > Q1 > Q2 (sustained and improving)
  SUSTAINED_GROWTH  — rev_yoy Q0 >= 25%, Q1 >= 25% (strong but not inflecting)

Monster Scout integration:
  FIRST_INFLECTION  → +3.0 pts rank bonus
  TURNAROUND        → +2.0 pts rank bonus
  ACCELERATING      → +1.5 pts rank bonus
  Regime gate: Distribution/Markdown → watch_queue only, not active candidates

Output: data/bigshot/earnings_inflection.json
Run: daily (7:00 AM block, after pipeline_fundamentals.py)

Cost: $0 (reads from SQLite — no extra API calls)
"""

from __future__ import annotations
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT    = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "ohlcv.db"
OUT_DIR = ROOT / "data" / "bigshot"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT_DIR / "earnings_inflection.json"


# ── Freshness gate ────────────────────────────────────────────────────────────

def _is_fresh() -> bool:
    """Skip if already ran today."""
    if not OUT_FILE.exists():
        return False
    try:
        d = json.loads(OUT_FILE.read_text(encoding="utf-8"))
        return d.get("date") == date.today().isoformat()
    except Exception:
        return False


# ── Load themed tickers ───────────────────────────────────────────────────────

def _load_themed_tickers() -> dict[str, str]:
    """
    Returns {ticker: theme_name} for all 270 themed tickers.
    Sources (in priority order):
      1. data/themes/themed_tickers.json
      2. data/rs_universe/theme_rs_latest.json (members sub-dict)
      3. company_info table in ohlcv.db
    """
    by_ticker: dict[str, str] = {}

    # Source 1: themed_tickers.json — can be flat list ["AAOI","NVDA",...] or dict {theme: [tickers]}
    f1 = ROOT / "data" / "themes" / "themed_tickers.json"
    if f1.exists():
        try:
            raw = json.loads(f1.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for theme, tickers in raw.items():
                    if isinstance(tickers, list):
                        for t in tickers:
                            if isinstance(t, str) and t:
                                by_ticker.setdefault(t, theme)
            elif isinstance(raw, list):
                for item in raw:
                    if isinstance(item, str) and item:
                        by_ticker.setdefault(item, "Unknown")   # flat list — no theme info yet
                    elif isinstance(item, dict):
                        by_ticker.setdefault(item.get("ticker",""), item.get("theme","Unknown"))
        except Exception:
            pass

    # Source 2: theme_rs_latest.json members
    if len(by_ticker) < 50:  # fallback if themed_tickers is thin
        f2 = ROOT / "data" / "rs_universe" / "theme_rs_latest.json"
        if f2.exists():
            try:
                data = json.loads(f2.read_text(encoding="utf-8"))
                for theme_id, tv in data.get("themes", data).items():
                    if not isinstance(tv, dict):
                        continue
                    theme_name = tv.get("name", theme_id)
                    for ticker in tv.get("members", {}):
                        by_ticker.setdefault(ticker, theme_name)
            except Exception:
                pass

    # Source 3: company_info in ohlcv.db
    if len(by_ticker) < 50 and DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(DB_PATH))
            rows = conn.execute(
                "SELECT ticker, theme FROM company_info WHERE theme IS NOT NULL AND theme != ''"
            ).fetchall()
            conn.close()
            for ticker, theme in rows:
                by_ticker.setdefault(ticker, theme)
        except Exception:
            pass

    return {k: v for k, v in by_ticker.items() if k}  # remove empty keys


# ── Backfill inflection labels from existing data ─────────────────────────────

def _backfill_inflection_labels() -> int:
    """
    Populate rev_inflection_label using existing rev_yoy_pct + rev_acceleration
    as a proxy until pipeline_fundamentals.py re-fetches with actual Q1/Q2/Q3.

    Mapping (conservative — real Q1/Q2/Q3 will be more precise):
      rev_yoy_pct >= 25 AND rev_acceleration = 'EARLY'      → FIRST_INFLECTION
      rev_yoy_pct >= 25 AND rev_acceleration = 'ACCELERATING'→ ACCELERATING
      rev_yoy_pct >= 25 AND rev_acceleration = 'STABLE'      → SUSTAINED_GROWTH
      rev_acceleration IN ('TURNAROUND', ...) AND rev_yoy > 0 → TURNAROUND
    Only fills NULL rows — does not overwrite rows that pipeline_fundamentals wrote.
    """
    if not DB_PATH.exists():
        return 0
    try:
        conn = sqlite3.connect(str(DB_PATH))
        updated = conn.execute("""
            UPDATE fundamentals_summary
            SET rev_inflection_label = CASE
                WHEN rev_yoy_pct >= 25 AND rev_acceleration IN ('EARLY', 'TURNAROUND')
                    THEN 'FIRST_INFLECTION'
                WHEN rev_yoy_pct > 0 AND rev_acceleration = 'TURNAROUND'
                    THEN 'TURNAROUND'
                WHEN rev_yoy_pct >= 25 AND rev_acceleration = 'ACCELERATING'
                    THEN 'ACCELERATING'
                WHEN rev_yoy_pct >= 25 AND rev_acceleration IN ('STABLE', 'DECELERATING')
                    THEN 'SUSTAINED_GROWTH'
                ELSE 'NONE'
            END
            WHERE rev_inflection_label IS NULL
              AND rev_yoy_pct IS NOT NULL
        """).rowcount
        conn.commit()
        conn.close()
        if updated > 0:
            print(f"  Backfilled rev_inflection_label for {updated} tickers (proxy from rev_acceleration)")
        return updated
    except Exception as e:
        print(f"  [WARN] Backfill failed: {e}")
        return 0


# ── Query fundamentals DB ─────────────────────────────────────────────────────

def _get_inflection_data(tickers: list[str]) -> list[dict]:
    """
    Query fundamentals_summary for inflection candidates.
    Returns list of dicts sorted by priority.
    """
    if not DB_PATH.exists():
        return []

    try:
        conn = sqlite3.connect(str(DB_PATH))
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(f"""
            SELECT
                ticker,
                rev_yoy_pct,
                rev_yoy_q1,
                rev_yoy_q2,
                rev_yoy_q3,
                rev_inflection_label,
                rev_acceleration,
                eps_yoy_pct,
                latest_period,
                last_fetched
            FROM fundamentals_summary
            WHERE ticker IN ({placeholders})
              AND rev_inflection_label IS NOT NULL
              AND rev_inflection_label != 'NONE'
              AND rev_inflection_label != ''
            ORDER BY
                CASE rev_inflection_label
                    WHEN 'FIRST_INFLECTION' THEN 1
                    WHEN 'TURNAROUND'       THEN 2
                    WHEN 'ACCELERATING'     THEN 3
                    WHEN 'SUSTAINED_GROWTH' THEN 4
                    ELSE 5
                END,
                rev_yoy_pct DESC
        """, tickers).fetchall()
        conn.close()
    except Exception as e:
        print(f"  [WARN] DB query failed: {e}")
        return []

    results = []
    for row in rows:
        (ticker, rev_q0, rev_q1, rev_q2, rev_q3,
         inflection_label, rev_acc, eps_yoy, latest_period, last_fetched) = row

        # Build human-readable trend string: e.g. "-12%→+3%→+31%"
        trend_parts = []
        for val in [rev_q3, rev_q2, rev_q1, rev_q0]:
            if val is not None:
                sign = "+" if val >= 0 else ""
                trend_parts.append(f"{sign}{val:.0f}%")
        trend_str = "→".join(trend_parts) if trend_parts else "N/A"

        # Note for display
        note_map = {
            "FIRST_INFLECTION": f"First quarter above 25% rev growth | Trend: {trend_str}",
            "TURNAROUND":       f"First positive quarter after negative | Trend: {trend_str}",
            "ACCELERATING":     f"Revenue accelerating 3Q | Trend: {trend_str}",
            "SUSTAINED_GROWTH": f"Sustained growth above 25% | Trend: {trend_str}",
        }

        results.append({
            "ticker":            ticker,
            "rev_yoy_q0":        rev_q0,
            "rev_yoy_q1":        rev_q1,
            "rev_yoy_q2":        rev_q2,
            "rev_yoy_q3":        rev_q3,
            "inflection_label":  inflection_label,
            "rev_acceleration":  rev_acc,
            "eps_yoy_pct":       eps_yoy,
            "latest_period":     latest_period,
            "last_fetched":      last_fetched,
            "trend_str":         trend_str,
            "note":              note_map.get(inflection_label, trend_str),
        })

    return results


# ── Monster Scout rank bonus ──────────────────────────────────────────────────

INFLECTION_BONUS = {
    "FIRST_INFLECTION": 3.0,
    "TURNAROUND":       2.0,
    "ACCELERATING":     1.5,
    "SUSTAINED_GROWTH": 0.5,
}


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    today = date.today().isoformat()
    print(f"\n{'='*55}")
    print(f"  Earnings Inflection Scout  [{today}]")
    print(f"{'='*55}")

    if _is_fresh():
        print("  [SKIP] Already ran today — output is fresh")
        try:
            return json.loads(OUT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Load themed tickers
    themed = _load_themed_tickers()
    n_themed = len(themed)
    print(f"  Themed tickers: {n_themed}")

    if n_themed == 0:
        print("  [WARN] No themed tickers found — check themed_tickers.json")
        result = {
            "date": today, "screened": 0,
            "inflection_candidates": [], "accelerating_candidates": [],
            "inflections_found": 0, "status": "no_tickers"
        }
        OUT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    # Backfill rev_inflection_label from existing rev_yoy_pct + rev_acceleration
    # (runs until pipeline_fundamentals.py re-fetches with actual Q1/Q2/Q3 data)
    _backfill_inflection_labels()

    # Query inflection data from fundamentals_summary
    tickers_list = list(themed.keys())
    all_candidates = _get_inflection_data(tickers_list)

    # Attach theme info
    for c in all_candidates:
        c["theme"] = themed.get(c["ticker"], "Unknown")

    # Split into priority groups
    inflection_candidates = [
        c for c in all_candidates
        if c["inflection_label"] in ("FIRST_INFLECTION", "TURNAROUND")
    ]
    accelerating_candidates = [
        c for c in all_candidates
        if c["inflection_label"] == "ACCELERATING"
    ]

    # Build lookup set for Monster Scout
    inflection_set = {c["ticker"] for c in inflection_candidates}

    print(f"  FIRST_INFLECTION + TURNAROUND: {len(inflection_candidates)}")
    print(f"  ACCELERATING: {len(accelerating_candidates)}")

    if inflection_candidates:
        print("  Top inflection candidates:")
        for c in inflection_candidates[:5]:
            print(f"    {c['ticker']:6} {c['inflection_label']:20} Rev: {c['trend_str']} | {c['theme']}")

    result = {
        "date":                    today,
        "screened":                n_themed,
        "inflections_found":       len(inflection_candidates),
        "accelerating_found":      len(accelerating_candidates),
        "inflection_candidates":   inflection_candidates,
        "accelerating_candidates": accelerating_candidates[:10],
        "inflection_tickers":      list(inflection_set),  # for Monster Scout fast lookup
        "rank_bonus":              INFLECTION_BONUS,
        "status":                  "ok",
    }

    OUT_FILE.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"  -> Written: {OUT_FILE}")

    return result


if __name__ == "__main__":
    run()
