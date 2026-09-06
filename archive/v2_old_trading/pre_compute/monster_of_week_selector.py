"""
AlphaAbsolute v2 — Monster of the Week Selector
=================================================
Picks ONE Monster Scout candidate for deep weekly research.
Feeds monster_deep_research.py → Telegram 3-message brief.

Selection logic (BOA-013 Phase 1 v2 scoring):
  1. Read candidates.json (A07 Monster Scout output)
  2. Score each on Phase 1 signals (7 existing data signals, 90 pts max)
  3. Apply regime gate: Distribution/Markdown → pick from watch_queue only
  4. Output top candidate to data/bigshot/motw_selection.json

Phase 1 Scoring (90 pts):
  Fundamental layer (40 pts):
    - Revenue inflection: FIRST_INFLECTION=15, ACCELERATING_STRONG=12,
                          TURNAROUND=10, ACCELERATING=7, SUSTAINED_GROWTH=3
    - EPS positive this quarter: +8 pts
    - EPS accelerating (Q0 > Q1): +5 pts (bonus on top of positive)
    - Gross margin stable/expanding: +7 pts
    - Revenue absolute growth >= 50%: +5 pts extra (monster growth)

  Market structure layer (15 pts):
    - Base 0: +10 pts | Base 1: +7 pts | Base 2: +5 pts (Base 3+: BLOCKED)
    - Breakout at or near 52W high (within 5%): +5 pts bonus
    - market_cap scoring: PENDING Phase 2 (BOA-013-A4) — will restore when
      Finnhub market_cap is wired into candidates.json

  Technical layer (35 pts):
    - 3-month high breakout (hard gate — already enforced by A07): +15 pts baseline
    - 6-month high: +20 pts (vs 15 for 3M)
    - All-time high: +25 pts (vs 15 for 3M)
    - RS 3M >= 70: +10 pts
    - RS 3M >= 85: +15 pts (replaces 10 pts — leader within theme)
    - RS 1M acceleration (1M > 3M by >= 5pp): +5 pts (early institutional buying)

Regime gate:
  Markup       → pick from active candidates (Grade A then Grade B)
  Distribution → pick from watch_queue (pre-position for next Markup)
  Markdown     → pick from watch_queue (study only)
  Sideways     → pick from watch_queue (prep only)

Output: data/bigshot/motw_selection.json
Run: Sundays (weekly), or whenever a new Grade A monster appears

Cost: $0 (reads from SQLite + JSON — no API calls)
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
OUT_FILE = OUT_DIR / "motw_selection.json"


# ── Revenue inflection score ──────────────────────────────────────────────────

INFLECTION_SCORE = {
    "FIRST_INFLECTION":    15,
    "ACCELERATING_STRONG": 12,
    "TURNAROUND":          10,
    "ACCELERATING":         7,
    "SUSTAINED_GROWTH":     3,
    "NONE":                 0,
    "":                     0,
}


# ── Load helpers ──────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _load_candidates() -> list[dict]:
    data = _load_json(OUT_DIR / "candidates.json")
    return data.get("candidates", [])


def _load_inflection() -> dict:
    """Returns {ticker: inflection_dict} from earnings_inflection.json"""
    data = _load_json(OUT_DIR / "earnings_inflection.json")
    all_cands = (
        data.get("inflection_candidates", []) +
        data.get("accelerating_candidates", [])
    )
    return {c["ticker"]: c for c in all_cands if c.get("ticker")}


def _load_regime() -> dict:
    return _load_json(ROOT / "data" / "regime" / "market_health.json")


def _load_rs_universe() -> dict:
    data = _load_json(ROOT / "data" / "rs_universe" / "latest.json")
    return data.get("universe", {})


def _load_fundamentals_from_db(ticker: str) -> dict:
    """Pull latest fundamentals_summary row for ticker."""
    if not DB_PATH.exists():
        return {}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute("""
            SELECT eps_yoy_pct, rev_yoy_pct, gross_margin, gm_trend,
                   rev_inflection_label, rev_yoy_q1, eps_yoy_q1
            FROM fundamentals_summary
            WHERE ticker = ?
            LIMIT 1
        """, (ticker,)).fetchone()
        conn.close()
        if row:
            return {
                "eps_yoy_pct":       row[0],
                "rev_yoy_pct":       row[1],
                "gross_margin":      row[2],
                "gm_trend":          row[3],
                "rev_inflection_label": row[4],
                "rev_yoy_q1":        row[5],
                "eps_yoy_q1":        row[6],
            }
    except Exception:
        pass
    return {}


# ── Phase 1 Scorer ────────────────────────────────────────────────────────────

def score_candidate(candidate: dict, inflection_map: dict, rs_universe: dict) -> dict:
    """
    Score a Monster Scout candidate on Phase 1 signals (90 pts max).
    Returns the candidate dict enriched with score details.
    """
    ticker = candidate.get("ticker", "")
    score  = 0
    breakdown = {}

    # ── Pull data from all sources ──────────────────────────────────────────
    infl       = inflection_map.get(ticker, {})
    rs_entry   = rs_universe.get(ticker, {})
    fund_db    = _load_fundamentals_from_db(ticker)

    # Merge: candidate dict → inflection map → db → rs universe (priority order)
    inflection_label = (
        candidate.get("revenue_inflection") or
        candidate.get("inflection_label") or
        infl.get("inflection_label") or
        fund_db.get("rev_inflection_label") or
        "NONE"
    )
    rev_yoy   = candidate.get("rev_yoy_pct") or infl.get("rev_yoy_q0") or fund_db.get("rev_yoy_pct")
    rev_yoy_q1 = infl.get("rev_yoy_q1") or fund_db.get("rev_yoy_q1")
    eps_yoy   = candidate.get("eps_yoy_pct") or fund_db.get("eps_yoy_pct")
    eps_yoy_q1 = fund_db.get("eps_yoy_q1")
    gm_trend  = candidate.get("gm_trend") or fund_db.get("gm_trend") or ""
    base_num  = candidate.get("base_number") or candidate.get("base_count") or 0

    rs_1m = rs_entry.get("rs_pct_1m") or rs_entry.get("rs_1m_pct") or candidate.get("rs_pct_1m") or 0
    rs_3m = rs_entry.get("rs_pct_3m") or rs_entry.get("rs_3m_pct") or candidate.get("rs_pct_3m") or 0

    pct_from_52wh = candidate.get("pct_from_52w_high") or 0   # negative = below high

    # ── FUNDAMENTAL LAYER (40 pts) ──────────────────────────────────────────

    # Revenue inflection (0–15 pts)
    infl_pts = INFLECTION_SCORE.get(inflection_label, 0)
    score += infl_pts
    breakdown["inflection"] = f"{inflection_label}: +{infl_pts}"

    # EPS positive (8 pts)
    eps_pts = 0
    if eps_yoy is not None and eps_yoy > 0:
        eps_pts += 8
        # EPS acceleration bonus (5 pts)
        if eps_yoy_q1 is not None and eps_yoy > eps_yoy_q1:
            eps_pts += 5
    score += eps_pts
    breakdown["eps"] = f"EPS {eps_yoy:+.0f}% (Q1={eps_yoy_q1}): +{eps_pts}" if eps_yoy else "EPS: +0"

    # Gross margin trend (7 pts)
    gm_pts = 7 if gm_trend in ("Expanding", "Stable") else 0
    score += gm_pts
    breakdown["gm_trend"] = f"GM {gm_trend}: +{gm_pts}"

    # Monster revenue absolute (≥50% = extra 5 pts)
    monster_rev_pts = 0
    if rev_yoy is not None and rev_yoy >= 50:
        monster_rev_pts = 5
    score += monster_rev_pts
    if monster_rev_pts:
        breakdown["monster_rev"] = f"Rev {rev_yoy:.0f}% ≥50%: +5"

    # ── MARKET STRUCTURE LAYER (15 pts) ────────────────────────────────────────
    # NOTE: market_cap scoring (+10 pts) removed — dead code (BOA-017 DEC-023).
    # monster_scout.py never writes market_cap to candidates.json, so cap_pts
    # was always 0. Will restore in Phase 2 when Finnhub market_cap is wired
    # into candidates.json (BOA-013-A4).

    # Base number (0–10 pts, Base 3+ = 0 / should be blocked at A07)
    base_pts = 0
    try:
        bn = int(base_num) if base_num else 99
        if bn == 0:   base_pts = 10
        elif bn == 1: base_pts = 7
        elif bn == 2: base_pts = 5
        # Base 3+ → 0 pts (BOA-013: hard block at A07 level)
    except (ValueError, TypeError):
        pass
    score += base_pts
    breakdown["base"] = f"Base {base_num}: +{base_pts}"

    # Near 52W high bonus (+5 if within 5% of 52W high)
    near_high_pts = 0
    if pct_from_52wh is not None and pct_from_52wh >= -5:
        near_high_pts = 5
    score += near_high_pts
    if near_high_pts:
        breakdown["near_high"] = f"{pct_from_52wh:.1f}% from 52Wh: +5"

    # ── TECHNICAL LAYER (35 pts) ────────────────────────────────────────────

    # Breakout quality (15–25 pts)
    # A07 already enforces >= 3-month high, so all candidates get baseline 15 pts
    # Bonus for 6M high (20 pts) or ATH (25 pts)
    is_6m_high = candidate.get("is_6m_high") or candidate.get("is_126d_high") or False
    is_ath     = candidate.get("is_ath") or candidate.get("at_ath") or False
    bkout_pts  = 25 if is_ath else (20 if is_6m_high else 15)
    score += bkout_pts
    bkout_label = "ATH" if is_ath else ("6M high" if is_6m_high else "3M high")
    breakdown["breakout"] = f"{bkout_label}: +{bkout_pts}"

    # RS technical score (0–15 pts)
    rs_pts = 0
    if rs_3m >= 85:
        rs_pts = 15
    elif rs_3m >= 70:
        rs_pts = 10
    score += rs_pts
    breakdown["rs"] = f"RS3M={rs_3m:.0f}: +{rs_pts}"

    # RS acceleration: 1M > 3M by ≥5pp = early institutional buying (+5 pts)
    rs_accel_pts = 0
    if rs_1m and rs_3m and (rs_1m - rs_3m) >= 5:
        rs_accel_pts = 5
    score += rs_accel_pts
    if rs_accel_pts:
        breakdown["rs_accel"] = f"RS accel {rs_1m:.0f}vs{rs_3m:.0f}: +5"

    return {
        **candidate,
        "motw_score":      score,
        "score_breakdown": breakdown,
        "inflection_label_resolved": inflection_label,
        "rev_yoy_resolved":          rev_yoy,
        "eps_yoy_resolved":          eps_yoy,
        "rs_1m_resolved":            rs_1m,
        "rs_3m_resolved":            rs_3m,
        "base_num_resolved":         base_num,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    today = date.today().isoformat()
    print(f"\n{'='*55}")
    print(f"  Monster of the Week Selector  [{today}]")
    print(f"{'='*55}")

    # Load all data
    candidates     = _load_candidates()
    inflection_map = _load_inflection()
    regime_data    = _load_regime()
    rs_universe    = _load_rs_universe()

    regime    = regime_data.get("regime", "Unknown")
    bigshot_ok = regime_data.get("bigshot_ok", False)

    print(f"  Regime: {regime} | bigshot_ok: {bigshot_ok}")
    print(f"  Candidates: {len(candidates)} | Inflection signals: {len(inflection_map)}")

    if not candidates:
        print("  [WARN] No Monster Scout candidates — A07 may not have run yet")
        result = {
            "date": today, "regime": regime,
            "selected": None, "status": "no_candidates"
        }
        OUT_FILE.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        return result

    # Score all candidates
    scored = [score_candidate(c, inflection_map, rs_universe) for c in candidates]

    # Filter by regime:
    # Markup → prefer active Grade A, then Grade B
    # Others → watch_queue only (still pick the best for research)
    if bigshot_ok:
        active  = [c for c in scored if c.get("setup_grade", "B") in ("A", "B")]
        standby = [c for c in scored if c not in active]
        pool    = sorted(active, key=lambda x: x["motw_score"], reverse=True) or \
                  sorted(standby, key=lambda x: x["motw_score"], reverse=True)
    else:
        # Distribution/Markdown — pick best for watch/study
        pool = sorted(scored, key=lambda x: x["motw_score"], reverse=True)

    if not pool:
        print("  [WARN] Empty pool after filtering")
        result = {
            "date": today, "regime": regime,
            "selected": None, "status": "empty_pool"
        }
        OUT_FILE.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        return result

    selected = pool[0]
    top5     = [{"ticker": c["ticker"], "score": c["motw_score"],
                 "grade": c.get("setup_grade", "?"),
                 "inflection": c.get("inflection_label_resolved", "")} for c in pool[:5]]

    # Status: active (Markup, can enter) vs watch (Distribution/Markdown, study only)
    entry_status = "ACTIVE" if bigshot_ok else "WATCH_ONLY"

    print(f"  Selected: {selected['ticker']} | Score: {selected['motw_score']}/100 | {entry_status}")
    print(f"  Breakdown: {selected.get('score_breakdown', {})}")
    print(f"  Top 5 scored:")
    for c in top5:
        print(f"    {c['ticker']:6} {c['score']:3}/100  Grade:{c['grade']}  {c['inflection']}")

    result = {
        "date":         today,
        "regime":       regime,
        "entry_status": entry_status,
        "bigshot_ok":   bigshot_ok,
        "selected":     selected,
        "top5_scored":  top5,
        "total_candidates": len(candidates),
        "status":       "ok",
    }

    OUT_FILE.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"  -> Written: {OUT_FILE}")

    return result


if __name__ == "__main__":
    run()
