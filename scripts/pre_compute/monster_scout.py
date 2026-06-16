"""
AlphaAbsolute v2 -- A07 Monster Scout (Monster Scout) v2
=========================================================
Finds early-stage Big Shot candidates breaking out BEFORE institutional discovery.
"10x Before The Supercycle" -- pre-institutional, narrative-driven, asymmetric.

v2 CIO-APPROVED FRAMEWORK (2026-05-25, revised 2026-05-27):

TWO-OUTPUT DESIGN:
  universe.json   = Monster Scout UNIVERSE: all tickers eligible to enter (maintained weekly)
                    Gates: mktcap + rev2Q + theme — NO base count, NO breakout required
                    Setup Scanner checks universe daily for buy points
  candidates.json = subset of universe currently AT breakout (backward compat + daily briefing)

UNIVERSE HARD GATES (all must pass to be in universe.json):
  1. Market cap < $20B       (Monster zone: <$20B ceiling -- no blue chips)
                              GRACEFUL: if market_cap unknown -> do NOT fail, log as unknown
  2. Revenue: 2 quarters MINIMUM
                              rev_yoy_q1 must be NOT NULL in fundamentals_summary
                              HARD: no data = excluded from universe (no graceful pass)
                              Pre-revenue companies with no EDGAR data are excluded.
  3. Narrative gate          must be in 1 of 14 official themes (implicit — iterates theme_members)

  NOTE: Base count is NOT a gate. It is informational context only.
        Monster Scout universe includes ALL stages — early and late.
        CIO decision 2026-05-27: base gate removed entirely.

CANDIDATES ADDITIONAL GATE (required for candidates.json):
  4. Breakout gate           price at >= 63-day high (3-month minimum)

BONUS SCORING (ranking only -- not gates):
  +15 pts  market_cap < $2B  (Monster Elite Zone)
  +8 pts   market_cap $2B-$10B
  +10 pts  rev_yoy latest quarter > 50%
  +5 pts   rev_yoy latest quarter > 25%
  +12 pts  short_interest_pct > 25% (squeeze potential)
  +8 pts   short_interest_pct > 15%
  +10 pts  bottleneck owner (data/themes/bottleneck_owners.json)
  +8 pts   gross margin expanding (eps acceleration proxy)
  +5 pts   theme RS > 80th percentile (HOT theme)
  +8 pts   peer confirmation (2+ theme peers with RS > 70)

PHASE CLASSIFICATION (auto-tag on output):
  PHASE_B      = turnaround/first_inflection + base<=1 + rs_3m<70  (pre-institutional sweet spot)
  PHASE_C      = accelerating_strong + rs_3m>=70                   (institutional discovery)
  PHASE_D_RISK = rs_3m>=90 + pct_above_63d_low>=15%                (extended -- late entry risk)
  UNKNOWN      = insufficient data

RS is NOT a gate. RS is context only (shows where institutional discovery stands).

Input sources:
  - data/rs_universe/theme_rs_latest.json (A05 theme members + theme RS grade)
  - data/rs_universe/latest.json (RS context only)
  - data/themes/market_cap_cache.json (Finnhub market cap -- built by fetch_market_cap.py)
  - data/themes/bottleneck_owners.json (static bottleneck lookup)
  - data/bigshot/earnings_inflection.json (revenue multi-quarter data)
  - Price data via data_engine.py

Output: data/bigshot/candidates.json
Run: 8:00 AM daily (after A05 theme heatmap)
Prerequisite: python scripts/pre_compute/fetch_market_cap.py  (weekly)

Cost: $0 (Python only)
"""

from __future__ import annotations
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

OUT_DIR       = ROOT / "data" / "bigshot"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CAND_FILE     = OUT_DIR / "candidates.json"
UNIVERSE_FILE = OUT_DIR / "universe.json"   # full pre-breakout universe (new)
DB_PATH       = ROOT / "data" / "ohlcv.db"

# 14 official themes (must match rs_theme_ranker.py PRIMARY_THEME_MAP)
OFFICIAL_THEMES = {
    "AI_Related", "Memory_HBM", "Space", "Quantum_Computing", "Photonics",
    "DefenseTech", "Data_Center", "Nuclear_SMR", "NeoCloud", "AI_Infrastructure",
    "Data_Center_Infra", "Drone_UAV", "Robotics", "Connectivity"
}

MAX_CANDIDATES = 5  # max outputs per day


# ── Data helpers ──────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _load_list_json(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        return data if isinstance(data, list) else data.get("results", [])
    except Exception:
        return []


def _fetch_price_history(ticker: str, days: int = 130) -> list[float]:
    """Returns list of closing prices, oldest first. Reads from SQLite — zero network calls."""
    try:
        import sqlite3
        if DB_PATH.exists():
            with sqlite3.connect(str(DB_PATH)) as conn:
                rows = conn.execute(
                    "SELECT close FROM ohlcv WHERE ticker=? AND close IS NOT NULL ORDER BY date ASC",
                    (ticker,)
                ).fetchall()
            if rows:
                return [float(r[0]) for r in rows][-days:]
    except Exception:
        pass
    return []


def _fetch_adtv(ticker: str, days: int = 126) -> Optional[float]:
    """Returns 6-month average daily dollar volume. Reads from SQLite — zero network calls."""
    try:
        import sqlite3
        if DB_PATH.exists():
            with sqlite3.connect(str(DB_PATH)) as conn:
                rows = conn.execute(
                    """SELECT close, volume FROM ohlcv
                       WHERE ticker=? AND close IS NOT NULL AND volume IS NOT NULL
                       ORDER BY date DESC LIMIT ?""",
                    (ticker, days)
                ).fetchall()
            if rows:
                return float(sum(r[0] * r[1] for r in rows) / len(rows))
    except Exception:
        pass
    return None


# ── Breakout gate (Hard gate #1) ──────────────────────────────────────────────

def check_breakout(closes: list[float]) -> dict:
    """
    Check if price is at a multi-month high.
    Returns:
      {
        "passes": bool,
        "breakout_type": "ATH" | "6M_HIGH" | "3M_HIGH" | "NONE",
        "days_at_high": int,   # how far back is the current high
        "pct_above_63d_low": float,
        "score": float,        # 0-10, higher = stronger breakout
      }
    """
    if len(closes) < 64:
        return {"passes": False, "breakout_type": "NONE", "score": 0.0}

    current = closes[-1]

    # Check vs 63-day (3M), 126-day (6M), 252-day (1Y) highs
    high_63  = max(closes[-63:])   if len(closes) >= 63  else None
    high_126 = max(closes[-126:])  if len(closes) >= 126 else None
    high_252 = max(closes[-252:])  if len(closes) >= 252 else None

    # Passes if price is AT or breaking the 63-day high
    passes = high_63 is not None and current >= high_63 * 0.995  # within 0.5% of high

    if not passes:
        return {"passes": False, "breakout_type": "NONE", "score": 0.0}

    # Classify breakout quality
    if high_252 and current >= high_252 * 0.995:
        btype = "ATH"
        base_score = 10.0
    elif high_126 and current >= high_126 * 0.995:
        btype = "6M_HIGH"
        base_score = 7.5
    else:
        btype = "3M_HIGH"
        base_score = 5.0

    # Momentum bonus: how much has it gained from the 63-day low?
    low_63 = min(closes[-63:])
    pct_above_low = (current / low_63 - 1) * 100 if low_63 > 0 else 0

    # How many days at/near this high? (sustained breakout = stronger)
    days_at_high = sum(1 for c in closes[-10:] if c >= high_63 * 0.98)

    score = min(base_score + days_at_high * 0.3, 10.0)

    return {
        "passes": True,
        "breakout_type": btype,
        "pct_above_63d_low": round(pct_above_low, 1),
        "days_at_high": days_at_high,
        "score": round(score, 2),
    }


# ── Base count estimation (Gate #3) ──────────────────────────────────────────

def estimate_base_count(closes: list[float]) -> dict:
    """
    Heuristic base count from price history.
    Base 0 = stock never broke out before (no prior base counted)
    Base 1 = first proper base after prior advance
    Base 2+ = late stage (BLOCKED in Mode B)

    Heuristic: count distinct "advance-then-consolidate" cycles.
    A cycle = price rises >20% then consolidates >5 weeks (>25 trading days)
    """
    if len(closes) < 60:
        return {"base_count": 0, "passes": True, "note": "Insufficient history — assume Base 0"}

    base_count = 0
    i = 0
    advance_start = 0
    in_consolidation = False
    peak_price = closes[0]

    while i < len(closes):
        c = closes[i]

        if not in_consolidation:
            # Looking for an advance (>20% from local low)
            local_low = min(closes[max(0, i-20):i+1])
            if local_low > 0 and (c / local_low - 1) >= 0.20:
                peak_price = c
                in_consolidation = True
                advance_start = i
        else:
            # In consolidation: looking for >25 day sideways (<5% range from peak)
            drawdown = (peak_price - c) / peak_price
            if drawdown > 0.30:
                # Too deep — not a proper consolidation, reset
                in_consolidation = False
                peak_price = c
            elif i - advance_start >= 25:
                # Completed a consolidation = one base
                base_count += 1
                in_consolidation = False
                advance_start = i

        i += 1
        if c > peak_price:
            peak_price = c

    passes = base_count <= 1  # Mode B: Base 0 or 1 only

    return {
        "base_count": base_count,
        "passes": passes,
        "note": f"Estimated Base {base_count}" + ("" if passes else " [BLOCKED — too late stage]"),
    }


# ── RS context (not a gate) ───────────────────────────────────────────────────

def get_rs_context(ticker: str, rs_data: dict) -> dict:
    """RS is informational only in Mode B — not a filter.
    BOA-005-BUG-05 fix (2026-05-24): latest.json uses 'rs_3m_pct' (not 'rs_pct_3m').
    Both key names tried for backward compatibility.
    """
    if ticker in rs_data:
        r = rs_data[ticker]
        # latest.json uses rs_3m_pct / rs_6m_pct (rs_ranker.py schema)
        # Some older code used rs_pct_3m / rs_pct_6m — try both
        rs3m = r.get("rs_3m_pct") or r.get("rs_pct_3m")
        rs6m = r.get("rs_6m_pct") or r.get("rs_pct_6m")
        phase = r.get("nrgc_phase") or r.get("phase", "Unknown")
        return {
            "rs_pct_3m": rs3m,
            "rs_pct_6m": rs6m,
            "rs_phase":  phase,
            "note": "RS context only — not used as gate in Mode B",
        }
    return {"rs_pct_3m": None, "rs_pct_6m": None, "rs_phase": "Unknown", "note": "No RS data"}


# ── Score candidate v2 (CIO-approved 2026-05-25) ─────────────────────────────

def score_candidate_v2(
    theme_hot: bool,
    theme_rs_pct: Optional[float],
    market_cap_usd: Optional[float],
    rev_yoy_q0: Optional[float],
    short_interest_pct: Optional[float],
    is_bottleneck_owner: bool,
    eps_accel: bool,
    peer_rs70_count: int,
) -> tuple[float, dict]:
    """
    Monster Scout v2 bonus scoring (ranking only — hard gates enforced separately).
    Returns (total_bonus_score, breakdown_dict).

    Bonus structure (CIO-approved 2026-05-25):
      Market cap tier  : +15 (<$2B), +8 ($2B-$10B), +0 ($10B-$20B)
      Revenue growth   : +10 (q0 > 50%), +5 (q0 > 25%)
      Short interest   : +12 (>25%), +8 (>15%)
      Bottleneck owner : +10
      EPS acceleration : +8
      Theme HOT/RS>80  : +5
      Peer confirmation: +8 (2+ theme peers RS>70)
    Max possible: 66 pts
    """
    score = 0.0
    breakdown: dict = {}

    if market_cap_usd is not None:
        if market_cap_usd < 2_000_000_000:
            score += 15; breakdown["mktcap_elite_lt2b"] = 15
        elif market_cap_usd < 10_000_000_000:
            score += 8;  breakdown["mktcap_standard_lt10b"] = 8

    if rev_yoy_q0 is not None:
        if rev_yoy_q0 > 50:
            score += 10; breakdown["rev_gt50pct"] = 10
        elif rev_yoy_q0 > 25:
            score += 5;  breakdown["rev_gt25pct"] = 5

    if short_interest_pct is not None:
        if short_interest_pct > 25:
            score += 12; breakdown["short_gt25pct"] = 12
        elif short_interest_pct > 15:
            score += 8;  breakdown["short_gt15pct"] = 8

    if is_bottleneck_owner:
        score += 10; breakdown["bottleneck_owner"] = 10

    if eps_accel:
        score += 8; breakdown["eps_accel"] = 8

    if theme_hot or (theme_rs_pct is not None and theme_rs_pct > 80):
        score += 5; breakdown["theme_hot_rs80"] = 5

    if peer_rs70_count >= 2:
        score += 8; breakdown["peer_confirmation_2plus"] = 8

    return round(score, 2), breakdown


# ── Legacy score_candidate (watch_queue path only) ────────────────────────────

def score_candidate(breakout: dict, base: dict, theme_hot: bool,
                    adtv_usd: Optional[float],
                    inflection_label: Optional[str] = None) -> float:
    """Legacy scoring for watch_queue path only. Main candidates use score_candidate_v2."""
    score = 0.0
    score += breakout.get("score", 0)
    base_count = base.get("base_count", 2)
    if   base_count == 0: score += 5.0
    elif base_count == 1: score += 3.0
    if theme_hot: score += 5.0
    if adtv_usd is not None:
        if   adtv_usd >= 50_000_000: score += 5.0
        elif adtv_usd >= 10_000_000: score += 3.5
        elif adtv_usd >=  3_000_000: score += 2.0
        else:                         score += 0.5
    pct = breakout.get("pct_above_63d_low", 0)
    if   pct > 50: score += 5.0
    elif pct > 25: score += 3.0
    elif pct > 10: score += 1.5
    inflection_bonus = {"FIRST_INFLECTION": 3.0, "TURNAROUND": 2.0,
                        "ACCELERATING": 1.5, "SUSTAINED_GROWTH": 0.5}
    if inflection_label and inflection_label in inflection_bonus:
        score += inflection_bonus[inflection_label]
    return round(score, 2)


# ── Rapid Breakout scanner (BOA-020-A1) ──────────────────────────────────────

def rapid_breakout_scan() -> list[dict]:
    """
    BOA-020-A1 (2026-05-24 — APPROVED 4/4): Monster fingerprint detector.

    A stock gaining ≥ 20% in ≤ 21 trading days from breakout = Monster fingerprint.
    These stocks should NOT be sold at the normal +25% profit-taking level.
    Instead they go onto the Monster Scout watch list as secondary entry candidates
    (buy 10EMA touches during the 8-week hold window).

    Reads from paper_portfolio_state.json + rs_universe/latest.json.
    Returns list of tickers currently in "rapid breakout" phase.
    """
    results = []
    today = date.today().isoformat()

    # Read paper portfolio positions
    paper_file = ROOT / "data" / "portfolio" / "paper_portfolio_state.json"
    if not paper_file.exists():
        return results
    try:
        paper = json.loads(paper_file.read_text(encoding="utf-8"))
    except Exception:
        return results

    positions = paper.get("positions", {})
    for ticker, pos in positions.items():
        entry_date_str = pos.get("entry_date", "")
        entry_price    = pos.get("entry_price", 0)
        current_price  = pos.get("current_price", 0)
        mode           = pos.get("mode", "A")

        if not entry_price or not current_price or mode != "A":
            continue

        # Compute trading days since entry
        try:
            entry = date.fromisoformat(entry_date_str[:10])
            today_d = date.today()
            days_in = sum(1 for i in range((today_d - entry).days)
                         if (entry + __import__('datetime').timedelta(days=i+1)).weekday() < 5)
        except Exception:
            continue

        pnl_pct = (current_price - entry_price) / entry_price * 100

        # Monster fingerprint: ≥ +20% in ≤ 21 trading days
        if days_in <= 21 and pnl_pct >= 20.0:
            results.append({
                "ticker":          ticker,
                "mode":            "A",
                "fingerprint":     "MONSTER",
                "days_in":         days_in,
                "pnl_pct":         round(pnl_pct, 1),
                "entry_price":     entry_price,
                "current_price":   current_price,
                "eight_week_hold": True,
                "hold_days_remaining": max(0, 56 - days_in),
                "ema_hold_eligible": True,   # A08 can add EMA_HOLD pyramid signal
                "note": (
                    f"Monster fingerprint: +{pnl_pct:.1f}% in {days_in} trading days. "
                    f"8-week hold active — do NOT take profit. "
                    f"Watch for 10EMA pullback for pyramid add (EMA_HOLD setup)."
                ),
                "date": today,
            })

    return results


# ── Load theme data ───────────────────────────────────────────────────────────

def load_theme_members() -> dict[str, dict]:
    """
    Returns {ticker: {theme, is_hot}} from theme_rs_latest.json.
    """
    members: dict[str, dict] = {}

    theme_file = ROOT / "data" / "rs_universe" / "theme_rs_latest.json"
    if not theme_file.exists():
        return members

    try:
        data = json.loads(theme_file.read_text(encoding="utf-8"))
        # theme_rs_latest.json structure: {"date": ..., "themes": {"Memory_HBM": {...}, ...}}
        # Fall back to flat structure if "themes" key not present (old format)
        themes_dict = data.get("themes", data)
        for theme_id, theme_data in themes_dict.items():
            if theme_id.startswith("_"):
                continue
            if not isinstance(theme_data, dict):
                continue
            is_hot = theme_data.get("grade") == "HOT"
            # members is a dict {ticker: {...}} in new format, or list in old format
            members_raw = theme_data.get("members", {})
            tickers_iter = members_raw.keys() if isinstance(members_raw, dict) else members_raw
            for ticker in tickers_iter:
                if isinstance(ticker, str) and ticker.upper():
                    members[ticker.upper()] = {
                        "theme": theme_data.get("name", theme_id),
                        "is_hot": is_hot,
                        "theme_grade": theme_data.get("grade", "UNKNOWN"),
                    }
    except Exception as e:
        print(f"  [WARN] load_theme_members: {e}")

    return members


def load_rs_data() -> dict[str, dict]:
    """Returns {ticker: {rs_pct_3m, rs_pct_6m, phase}} from latest.json."""
    rs: dict[str, dict] = {}
    latest_file = ROOT / "data" / "rs_universe" / "latest.json"
    if not latest_file.exists():
        return rs
    try:
        data = json.loads(latest_file.read_text(encoding="utf-8"))
        # rs_ranker.py writes universe as a dict keyed by ticker under "universe" key
        # NOT as a list under "results" — that key does not exist
        universe = data.get("universe", {})
        for ticker, item in universe.items():
            rs[ticker.upper()] = item
    except Exception as e:
        print(f"  [WARN] monster_scout load_rs_data failed: {e}")
    return rs


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    today = date.today().isoformat()
    print(f"\n{'='*55}")
    print(f"  A07 Monster Scout (Mode B)  [{today}]")
    print(f"{'='*55}")

    # Check regime first — in Distribution/Markdown, no new Big Shot entries
    health_file = ROOT / "data" / "regime" / "market_health.json"
    health = _load_json(health_file)
    if not health.get("bigshot_ok", True):
        regime = health.get("regime", "Unknown")
        print(f"  [BLOCKED] bigshot_ok=False (regime={regime}) — no Mode B entries today")
        # Build watch_queue from DB (fast — no API calls, uses cached ticker_meta + RS data)
        # Purpose: show which Mode B candidates to act on immediately when regime clears
        print("  Building watch_queue from DB cache (no API calls)...")
        import sqlite3
        db_path = ROOT / "data" / "ohlcv.db"
        rs_data_wq    = load_rs_data()
        theme_members_wq = load_theme_members()
        watch_queue   = []

        # Load inflection + bottleneck for watch_queue enrichment (same files as normal path)
        wq_inflection_lookup: dict[str, str] = {}
        wq_inflection_detail: dict[str, dict] = {}
        wq_inf_file = ROOT / "data" / "bigshot" / "earnings_inflection.json"
        if wq_inf_file.exists():
            try:
                _inf = json.loads(wq_inf_file.read_text(encoding="utf-8"))
                for _c in _inf.get("inflection_candidates", []) + _inf.get("accelerating_candidates", []):
                    _t = _c.get("ticker", "")
                    _l = _c.get("inflection_label", "")
                    if _t and _l:
                        wq_inflection_lookup[_t] = _l
                        wq_inflection_detail[_t] = _c
            except Exception:
                pass

        wq_bottleneck_set: set[str] = set()
        _wq_bn_file = ROOT / "data" / "themes" / "bottleneck_owners.json"
        if _wq_bn_file.exists():
            try:
                _bn = json.loads(_wq_bn_file.read_text(encoding="utf-8"))
                wq_bottleneck_set = {e.get("ticker","") for e in _bn.get("bottleneck_owners", [])}
            except Exception:
                pass

        # Load pct_from_3m_high, adtv, market_cap from DB for all theme tickers at once
        db_tech: dict = {}
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                tickers_in = "','".join(theme_members_wq.keys())
                rows = conn.execute(f"""
                    SELECT ticker, pct_from_3m_high, pct_from_6m_high, adtv_6m_usd,
                           mode_b_eligible, last_close, stage2_flag, market_cap
                    FROM ticker_meta WHERE ticker IN ('{tickers_in}')
                """).fetchall()
                conn.close()
                for r in rows:
                    db_tech[r["ticker"]] = dict(r)
            except Exception as e:
                print(f"  [WARN] DB read for watch_queue: {e}")

        for ticker, theme_info in theme_members_wq.items():
            tech = db_tech.get(ticker, {})
            # Gate: at or near 3-month high (within -5% of 3M high = breakout zone)
            pct_3m = tech.get("pct_from_3m_high")  # negative = below, 0 = at high
            if pct_3m is None or pct_3m < -5.0:    # more than 5% below 3M high → not breaking out
                continue
            # v2 Gate: Market cap < $20B (from ticker_meta.market_cap — populated by fetch_market_cap.py)
            _wq_mc = tech.get("market_cap")
            if _wq_mc is not None and _wq_mc >= 20_000_000_000:
                continue  # mega-cap → out of Monster Scout scope
            # ADTV gate: Mode B minimum $3M
            adtv = tech.get("adtv_6m_usd")
            if adtv is not None and adtv < 3_000_000:
                continue
            # RS context
            rs_ctx = get_rs_context(ticker, rs_data_wq)
            rs_3m  = rs_ctx.get("rs_pct_3m") or 0
            # Conviction score: HOT theme + near ATH + RS
            is_hot = theme_info.get("is_hot", False)
            pct_6m = tech.get("pct_from_6m_high") or pct_3m
            score  = (10 if is_hot else 5) + max(0, 10 + pct_3m) + rs_3m * 0.05
            # Fundamental enrichment for watch_queue
            _wq_infl   = wq_inflection_lookup.get(ticker)
            _wq_detail = wq_inflection_detail.get(ticker, {})
            _wq_rev    = _wq_detail.get("rev_yoy_q0") or _wq_detail.get("rev_yoy_pct")
            _wq_eps    = _wq_detail.get("eps_yoy_q0") or _wq_detail.get("eps_yoy_pct")
            # Phase classification
            if _wq_infl in ("FIRST_INFLECTION", "TURNAROUND") and rs_3m < 70:
                _wq_phase = "PHASE_B"
            elif _wq_infl in ("ACCELERATING_STRONG", "ACCELERATING") and rs_3m >= 70:
                _wq_phase = "PHASE_C"
            elif rs_3m >= 90:
                _wq_phase = "PHASE_D_RISK"
            else:
                _wq_phase = "UNKNOWN"
            watch_queue.append({
                "ticker":           ticker,
                "theme":            theme_info.get("theme"),
                "theme_grade":      theme_info.get("theme_grade"),
                "pct_from_3m_high": round(pct_3m, 1),
                "pct_from_6m_high": round(pct_6m, 1) if pct_6m else None,
                "rs_pct_3m":        rs_3m,
                "adtv_usd":         round(adtv, 0) if adtv else None,
                "conviction_score": round(score, 1),
                # Monster Scout differentiator fields
                "revenue_inflection":    _wq_infl,
                "rev_yoy_pct":           round(_wq_rev, 1) if _wq_rev is not None else None,
                "eps_yoy_pct":           round(_wq_eps, 1) if _wq_eps is not None else None,
                "phase_classification":  _wq_phase,
                "is_bottleneck_owner":   ticker in wq_bottleneck_set,
                "note":             "WATCH — regime clears → review for entry",
            })

        watch_queue.sort(key=lambda x: x["conviction_score"], reverse=True)
        watch_queue = watch_queue[:10]   # top 10 only
        if watch_queue:
            print(f"  Watch queue: {len(watch_queue)} candidates ready when regime improves")
            for w in watch_queue:
                pct3m = w.get('pct_from_3m_high', 0)
                print(f"    {w['ticker']:6} | {str(w['theme'] or ''):25} | Score={w['conviction_score']:.1f} | RS3M={w['rs_pct_3m']:.0f} | {pct3m:+.1f}% from 3M high")
        else:
            print("  Watch queue: 0 (no theme tickers near 3-month highs)")
        # BOA-020-A1: Still scan for monster fingerprints even in Distribution regime
        # (existing positions may be in 8-week hold regardless of regime)
        rapid_breakouts = rapid_breakout_scan()
        if rapid_breakouts:
            print(f"  Monster fingerprint positions: {len(rapid_breakouts)} (8-week hold active)")

        result = {
            "date":        today,
            "regime":      regime,
            "bigshot_ok":  False,
            "candidates":  [],
            "watch_queue": watch_queue,
            "rapid_breakouts": rapid_breakouts,
            "note": f"Mode B blocked by regime ({regime}). {len(watch_queue)} candidates in watch queue — ready to act when bigshot_ok=True.",
        }
        CAND_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    # Load data sources
    print("  Loading theme members and RS data...")
    theme_members = load_theme_members()
    rs_data       = load_rs_data()
    print(f"  Theme members: {len(theme_members)} | RS data: {len(rs_data)} tickers")

    # ── Load revenue multi-quarter directly from DB (primary source) ─────────
    # This is the authoritative source for the rev2Q hard gate.
    # earnings_inflection.json is kept as supplementary source for eps_yoy only.
    import sqlite3 as _sqlite3
    rev_data_db: dict[str, dict] = {}   # {ticker: {q0,q1,q2,q3,label}}
    try:
        _conn = _sqlite3.connect(DB_PATH)
        _rows = _conn.execute(
            "SELECT ticker, rev_yoy_pct, rev_yoy_q1, rev_yoy_q2, rev_yoy_q3, "
            "rev_inflection_label FROM fundamentals_summary"
        ).fetchall()
        _conn.close()
        for _r in _rows:
            rev_data_db[_r[0]] = {
                "rev_yoy_q0": _r[1],
                "rev_yoy_q1": _r[2],
                "rev_yoy_q2": _r[3],
                "rev_yoy_q3": _r[4],
                "label":      _r[5],
            }
        print(f"  Revenue DB: {len(rev_data_db)} tickers | "
              f"2Q data: {sum(1 for v in rev_data_db.values() if v['rev_yoy_q1'] is not None)}")
    except Exception as _e:
        print(f"  [WARN] Could not load revenue data from DB: {_e}")

    # Load earnings inflection data (supplementary — eps_yoy enrichment only)
    inflection_lookup: dict[str, str] = {}   # {ticker: inflection_label} (fallback)
    inflection_detail: dict[str, dict] = {}  # {ticker: eps context}
    inflection_file = ROOT / "data" / "bigshot" / "earnings_inflection.json"
    if inflection_file.exists():
        try:
            inf_data = json.loads(inflection_file.read_text(encoding="utf-8"))
            for c in inf_data.get("inflection_candidates", []) + inf_data.get("accelerating_candidates", []):
                ticker = c.get("ticker", "")
                label  = c.get("inflection_label", "")
                if ticker and label:
                    inflection_lookup[ticker] = label
                    inflection_detail[ticker] = c
        except Exception as _e:
            print(f"  [WARN] Could not load earnings_inflection.json: {_e}")

    # Load bottleneck owners lookup (BOA-017-A1)
    bottleneck_set: set[str] = set()
    bottleneck_desc_map: dict[str, str] = {}
    bn_file = ROOT / "data" / "themes" / "bottleneck_owners.json"
    if bn_file.exists():
        try:
            bn_data = json.loads(bn_file.read_text(encoding="utf-8"))
            for entry in bn_data.get("bottleneck_owners", []):
                t = entry.get("ticker", "")
                if t:
                    bottleneck_set.add(t)
                    bottleneck_desc_map[t] = entry.get("bottleneck_description", "")
            print(f"  Bottleneck owners loaded: {len(bottleneck_set)} tickers")
        except Exception:
            pass

    # Load market cap cache (v2 hard gate: <$20B) — built by fetch_market_cap.py
    market_cap_cache: dict[str, dict] = {}
    mc_file = ROOT / "data" / "themes" / "market_cap_cache.json"
    if mc_file.exists():
        try:
            mc_data = json.loads(mc_file.read_text(encoding="utf-8"))
            market_cap_cache = mc_data.get("tickers", {})
            covered = sum(1 for v in market_cap_cache.values() if v.get("market_cap"))
            print(f"  Market cap cache: {covered}/{len(market_cap_cache)} tickers with data")
        except Exception as e:
            print(f"  [WARN] Could not load market_cap_cache.json: {e}")
    else:
        print("  [WARN] market_cap_cache.json missing — run fetch_market_cap.py to enable $20B gate")

    # Build peer RS>70 count per theme (for peer confirmation bonus)
    peer_rs70_by_theme: dict[str, int] = {}
    for _ticker, _rd in rs_data.items():
        rs3m_val = _rd.get("rs_3m_pct") or _rd.get("rs_pct_3m") or 0
        if rs3m_val >= 70:
            _ti = theme_members.get(_ticker, {})
            _theme = _ti.get("theme", "")
            if _theme:
                peer_rs70_by_theme[_theme] = peer_rs70_by_theme.get(_theme, 0) + 1

    if not theme_members:
        print("  [WARN] No theme data — run rs_theme_ranker.py first")
        result = {"date": today, "candidates": [], "note": "No theme data available"}
        CAND_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    # Screen all theme members
    universe   = []   # all tickers passing mktcap + rev2Q + theme (no breakout needed)
    candidates = []   # subset of universe currently at breakout
    checked    = 0
    skipped_market_cap  = 0
    skipped_rev_accel   = 0   # includes no-data (hard gate now)
    unknown_market_cap  = 0

    print(f"  Screening {len(theme_members)} theme tickers (v2 universe gates)...")

    for ticker, theme_info in theme_members.items():
        checked += 1

        # ── v2 Gate 1: Market cap < $20B ──────────────────────────────────────
        mc_entry = market_cap_cache.get(ticker, {})
        market_cap_usd = mc_entry.get("market_cap")   # None if unknown
        if market_cap_usd is not None:
            if market_cap_usd >= 20_000_000_000:       # >= $20B → reject
                skipped_market_cap += 1
                continue
        else:
            unknown_market_cap += 1                    # unknown → don't fail, log

        # ── v2 Gate 2: Revenue — 2 quarters MINIMUM (HARD GATE) ──────────────
        # Primary source: fundamentals_summary DB (populated by fetch_revenue_multiquarter.py)
        # No graceful pass. No q1 = excluded from universe.
        rev_db        = rev_data_db.get(ticker, {})
        rev_yoy_q0    = rev_db.get("rev_yoy_q0")
        rev_yoy_q1    = rev_db.get("rev_yoy_q1")   # MUST be NOT NULL
        rev_yoy_q2    = rev_db.get("rev_yoy_q2")
        inflection_label = rev_db.get("label") or inflection_lookup.get(ticker)

        if rev_yoy_q1 is None:
            # Hard fail: less than 2 quarters of revenue data
            # Run: python scripts/pre_compute/fetch_revenue_multiquarter.py to fix
            skipped_rev_accel += 1
            continue

        # Classify revenue trend for rev_gate_status field
        q0q1_accel  = (rev_yoy_q0 is not None and rev_yoy_q0 > rev_yoy_q1)
        turnaround  = (rev_yoy_q1 < 0 and rev_yoy_q0 is not None and rev_yoy_q0 > 0)
        if turnaround:
            rev_gate_status = "turnaround"
        elif q0q1_accel:
            rev_gate_status = "accelerating"
        elif inflection_label in ("FIRST_INFLECTION", "TURNAROUND", "ACCELERATING",
                                   "ACCELERATING_STRONG", "SUSTAINED_GROWTH"):
            rev_gate_status = "label_pass"
        else:
            rev_gate_status = "has_2q_data"   # has data, not accelerating — still in universe

        # ── UNIVERSE GATE PASSED (mktcap + rev2Q + theme) ─────────────────────
        # Base count and ADTV are NOT gates. Informational only.
        # Ticker qualifies for Monster Scout universe regardless of stage or liquidity.
        # Setup Scanner checks this universe daily for buy points.

        # Fetch OHLCV for breakout check + informational base count
        closes = _fetch_price_history(ticker, days=130)

        # ── Gate 3: Breakout (price at >= 63-day high) — candidates only ──────
        # Does NOT gate universe membership — gates candidates.json only.
        breakout = check_breakout(closes) if closes and len(closes) >= 64 else \
                   {"passes": False, "breakout_type": "NONE", "score": 0.0}

        # Base count: informational only (not a gate — CIO 2026-05-27)
        base = estimate_base_count(closes) if closes and len(closes) >= 60 else \
               {"base_count": None, "passes": True, "note": "No OHLCV data"}

        # ADTV: informational only
        adtv = _fetch_adtv(ticker)

        # ── All universe gates passed → compute v2 bonus score ────────────────
        is_hot       = theme_info.get("is_hot", False)
        theme_rs_pct = theme_info.get("theme_rs_pct")   # from theme_rs_latest
        rs_ctx       = get_rs_context(ticker, rs_data)
        rs_3m        = rs_ctx.get("rs_pct_3m") or 0

        # eps_yoy: from earnings_inflection.json supplementary source
        _infl_detail = inflection_detail.get(ticker, {})
        eps_yoy = _infl_detail.get("eps_yoy_pct") or _infl_detail.get("eps_yoy_q0")
        # EPS acceleration proxy: if eps_yoy exists and positive, treat as expanding
        eps_accel_flag = (eps_yoy is not None and eps_yoy > 0)

        # Peer confirmation: count theme peers with RS > 70
        my_theme = theme_info.get("theme", "")
        peer_count = peer_rs70_by_theme.get(my_theme, 0)

        bonus_score, bonus_breakdown = score_candidate_v2(
            theme_hot           = is_hot,
            theme_rs_pct        = theme_rs_pct,
            market_cap_usd      = market_cap_usd,
            rev_yoy_q0          = rev_yoy_q0,
            short_interest_pct  = None,      # not yet sourced — future BOA item
            is_bottleneck_owner = ticker in bottleneck_set,
            eps_accel           = eps_accel_flag,
            peer_rs70_count     = peer_count,
        )

        # Phase classification (base count removed as condition — CIO 2026-05-27)
        if inflection_label in ("FIRST_INFLECTION", "TURNAROUND") and rs_3m < 70:
            phase = "PHASE_B"   # pre-institutional: revenue just turning, institutions not yet in
        elif inflection_label in ("ACCELERATING_STRONG", "ACCELERATING") and rs_3m >= 70:
            phase = "PHASE_C"   # institutional discovery: acceleration confirmed + RS elevated
        elif rs_3m >= 90 and breakout.get("pct_above_63d_low", 0) >= 15:
            phase = "PHASE_D_RISK"  # extended: well-discovered, late entry risk
        else:
            phase = "UNKNOWN"

        is_bn_owner = ticker in bottleneck_set
        bn_desc     = bottleneck_desc_map.get(ticker)

        # Phase-based size guidance
        if phase == "PHASE_D_RISK":
            size_pct = 0.0   # WATCH_ONLY — too extended
        else:
            size_pct = 5.0   # standard Monster Scout size

        # Build the full record (shared between universe + candidates)
        record = {
            "ticker":           ticker,
            "mode":             "B",
            "theme":            theme_info.get("theme"),
            "theme_grade":      theme_info.get("theme_grade"),

            # v2 gate results
            "market_cap_usd":   round(market_cap_usd, 0) if market_cap_usd else None,
            "market_cap_b":     round(market_cap_usd / 1e9, 2) if market_cap_usd else None,
            "market_cap_bucket": mc_entry.get("bucket", "unknown"),
            "market_cap_gate":  "unknown" if market_cap_usd is None else "pass",
            "rev_gate_status":  rev_gate_status,
            "rev_yoy_q1":       rev_yoy_q1,   # confirm 2Q data present

            # Breakout status (informational for universe; required for candidates)
            "breakout_type":    breakout["breakout_type"],
            "at_breakout":      breakout["passes"],
            "pct_above_63d_low": breakout.get("pct_above_63d_low"),
            "days_at_high":     breakout.get("days_at_high"),
            "base_count":       base["base_count"],

            # Sizing guidance
            "initial_size_pct": size_pct,
            "max_size_pct":     10.0,
            "stop_pct":         -10.0,

            # RS context (informational only)
            "rs_pct_3m":        rs_ctx.get("rs_pct_3m"),
            "rs_pct_6m":        rs_ctx.get("rs_pct_6m"),
            "rs_phase":         rs_ctx.get("rs_phase"),
            "adtv_usd":         round(adtv, 0) if adtv else None,

            # Revenue signal (from DB)
            "revenue_inflection": inflection_label,
            "rev_yoy_pct":        round(rev_yoy_q0, 1) if rev_yoy_q0 is not None else None,
            "rev_yoy_q1":         round(rev_yoy_q1, 1) if rev_yoy_q1 is not None else None,
            "eps_yoy_pct":        round(eps_yoy, 1) if eps_yoy is not None else None,

            # Phase classification
            "phase_classification": phase,

            # Bottleneck owner
            "is_bottleneck_owner":    is_bn_owner,
            "bottleneck_description": bn_desc,

            # v2 bonus score (ranking only)
            "bonus_score":     bonus_score,
            "bonus_breakdown": bonus_breakdown,

            # Legacy conviction_score field (for backward compatibility with setup_scanner, report_writer)
            "conviction_score": bonus_score,
        }

        # Add to universe (all that passed mktcap + rev2Q + base + adtv)
        universe.append(record)

        # Add to candidates only if at breakout
        if breakout["passes"]:
            candidates.append(record)

    # Sort by bonus_score (v2), take top MAX_CANDIDATES for candidates
    universe.sort(key=lambda x: x["bonus_score"], reverse=True)
    candidates.sort(key=lambda x: x["bonus_score"], reverse=True)
    top_candidates = candidates[:MAX_CANDIDATES]

    # Summary
    print(f"\n  Screened: {checked}")
    print(f"  Gate 1 (mktcap<$20B) fail: {skipped_market_cap} | unknown: {unknown_market_cap}")
    print(f"  Gate 2 (rev 2Q hard) fail: {skipped_rev_accel}  <- run fetch_revenue_multiquarter.py to reduce")
    print(f"  UNIVERSE size: {len(universe)} | At breakout today: {len(candidates)} | Top {len(top_candidates)} output")

    if top_candidates:
        print("\n  TOP BIG SHOT CANDIDATES (v2):")
        for i, c in enumerate(top_candidates, 1):
            hot_tag = " [HOT]" if c["theme_grade"] == "HOT" else ""
            mc_b = c.get("market_cap_b")
            mc_str = f"${mc_b}B" if mc_b else "mktcap=?"
            bc = c.get("base_count")
            bc_str = f"Base {bc}" if bc is not None else "Base ?"
            print(f"    {i}. {c['ticker']:6} {c['breakout_type']:8} | {bc_str} [info] | "
                  f"Phase={c['phase_classification']:12} | {mc_str} | "
                  f"Theme: {c['theme']}{hot_tag} | Score: {c['bonus_score']}")

    # BOA-020-A1: Scan existing portfolio positions for Monster fingerprint
    # Stocks gaining ≥20% in ≤21 days → EMA_HOLD pyramid add candidates
    rapid_breakouts = rapid_breakout_scan()
    if rapid_breakouts:
        print(f"\n  MONSTER FINGERPRINT DETECTED ({len(rapid_breakouts)} positions):")
        for rb in rapid_breakouts:
            print(f"    {rb['ticker']:6} +{rb['pnl_pct']:.1f}% in {rb['days_in']}d "
                  f"| Hold {rb['hold_days_remaining']}d more | EMA_HOLD eligible")

    # ── Write universe.json (full pre-breakout universe) ─────────────────────
    universe_result = {
        "date":        today,
        "regime":      health.get("regime", "Unknown"),
        "framework":   "v2",
        "universe_size": len(universe),
        "at_breakout_today": len(candidates),
        "gate_stats": {
            "screened":              checked,
            "fail_market_cap_gt20b": skipped_market_cap,
            "unknown_market_cap":    unknown_market_cap,
            "fail_rev_2q_hard":      skipped_rev_accel,
        },
        "universe":    universe,   # ALL qualifying tickers (breakout or not)
        "generated_at": datetime.now().strftime("%H:%M"),
        "note": (f"Universe: {len(universe)} stocks eligible | "
                 f"{len(candidates)} at breakout today | "
                 f"Setup Scanner scans universe daily for buy points | "
                 f"Gates: mktcap<$20B + rev_yoy_q1 NOT NULL only"),
    }
    UNIVERSE_FILE.write_text(json.dumps(universe_result, indent=2, default=str), encoding="utf-8")
    print(f"\n  -> Universe written: {UNIVERSE_FILE} ({len(universe)} tickers)")

    # ── Write candidates.json (at-breakout subset, backward compat) ──────────
    result = {
        "date":        today,
        "regime":      health.get("regime", "Unknown"),
        "bigshot_ok":  True,
        "framework":   "v2",
        "screened":    checked,
        "universe_size": len(universe),
        "passed":      len(candidates),
        "gate_stats": {
            "screened":              checked,
            "fail_market_cap_gt20b": skipped_market_cap,
            "unknown_market_cap":    unknown_market_cap,
            "fail_rev_2q_hard":      skipped_rev_accel,
        },
        "candidates":  top_candidates,
        "rapid_breakouts": rapid_breakouts,
        "note":        (f"v2: {len(universe)} in universe | "
                        f"{len(candidates)} at breakout | "
                        f"Top {len(top_candidates)} output"),
        "generated_at": datetime.now().strftime("%H:%M"),
    }

    CAND_FILE.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"  -> Candidates written: {CAND_FILE} ({len(top_candidates)} top candidates)")

    return result


if __name__ == "__main__":
    run()
