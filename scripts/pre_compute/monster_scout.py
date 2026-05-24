"""
AlphaAbsolute v2 -- A07 Monster Scout (Mode B)
===============================================
Finds early-stage Big Shot candidates breaking out BEFORE institutional discovery.

3 hard gates (ALL must pass -- no exceptions):
  1. Breakout gate: price at >= 63-day high (3-month minimum)
                   preferred: >= 126-day high (6-month)
  2. Narrative gate: must be in 1 of 14 official themes
  3. Stage gate: Base 0 or Base 1 only (no late-stage entries)

RS is NOT used as a filter. RS is context only.
Output: max 5 candidates per day, ranked by breakout strength + narrative + base.

Input sources:
  - data/rs_universe/theme_rs_latest.json (A05 theme members)
  - data/rs_universe/latest.json (RS context only)
  - Price data via data_engine.py

Output: data/bigshot/candidates.json
Run: 8:00 AM daily (after A05 theme heatmap)

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

OUT_DIR    = ROOT / "data" / "bigshot"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CAND_FILE  = OUT_DIR / "candidates.json"

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
    """Returns list of closing prices, oldest first. Returns [] on failure."""
    try:
        from data_engine import get_ohlcv
        df = get_ohlcv(ticker, period="6mo")
        if df is not None and not df.empty:
            closes = [float(row["Close"]) for _, row in df.iterrows()]
            return closes[-days:]
    except Exception:
        pass
    return []


def _fetch_adtv(ticker: str, days: int = 126) -> Optional[float]:
    """Returns 6-month average daily dollar volume in USD."""
    try:
        from data_engine import get_ohlcv
        df = get_ohlcv(ticker, period="6mo")
        if df is not None and not df.empty:
            recent = df.tail(days)
            dollar_vols = recent["Close"] * recent["Volume"]
            return float(dollar_vols.mean())
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


# ── Score candidate ───────────────────────────────────────────────────────────

def score_candidate(breakout: dict, base: dict, theme_hot: bool,
                    adtv_usd: Optional[float],
                    inflection_label: Optional[str] = None) -> float:
    """
    Composite conviction score for ranking (higher = better).
    Max ~33 points (30 base + 3 revenue inflection bonus).

    Revenue inflection bonus (Fund Manager spec — CONDITIONAL APPROVED 2026-05-24):
      FIRST_INFLECTION  → +3.0 (first quarter above 25% rev growth)
      TURNAROUND        → +2.0 (first positive quarter after negative)
      ACCELERATING      → +1.5 (3Q of improving rev growth)
    Regime constraint enforced in run() — Distribution/Markdown candidates
    are downgraded to watch_queue regardless of score.
    """
    score = 0.0

    # Breakout quality (0-10)
    score += breakout.get("score", 0)

    # Base number (0 or 1 = better, max 5 pts)
    base_count = base.get("base_count", 2)
    if   base_count == 0: score += 5.0
    elif base_count == 1: score += 3.0

    # HOT theme bonus (5 pts)
    if theme_hot:
        score += 5.0

    # Liquidity bonus (0-5 pts)
    if adtv_usd is not None:
        if   adtv_usd >= 50_000_000:  score += 5.0   # >$50M ADTV — very liquid
        elif adtv_usd >= 10_000_000:  score += 3.5   # >$10M ADTV
        elif adtv_usd >=  3_000_000:  score += 2.0   # >$3M ADTV — minimum
        else:                          score += 0.5   # below min (warn)

    # Momentum bonus: pct above 63d low
    pct = breakout.get("pct_above_63d_low", 0)
    if   pct > 50: score += 5.0
    elif pct > 25: score += 3.0
    elif pct > 10: score += 1.5

    # Revenue inflection bonus (sort inflecting stocks to top of candidate list)
    inflection_bonus = {
        "FIRST_INFLECTION": 3.0,
        "TURNAROUND":       2.0,
        "ACCELERATING":     1.5,
        "SUSTAINED_GROWTH": 0.5,
    }
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

        # Load pct_from_3m_high and adtv from DB for all theme tickers at once
        db_tech: dict = {}
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                tickers_in = "','".join(theme_members_wq.keys())
                rows = conn.execute(f"""
                    SELECT ticker, pct_from_3m_high, pct_from_6m_high, adtv_6m_usd,
                           mode_b_eligible, last_close, stage2_flag
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
            watch_queue.append({
                "ticker":           ticker,
                "theme":            theme_info.get("theme"),
                "theme_grade":      theme_info.get("theme_grade"),
                "pct_from_3m_high": round(pct_3m, 1),
                "pct_from_6m_high": round(pct_6m, 1) if pct_6m else None,
                "rs_pct_3m":        rs_3m,
                "adtv_usd":         round(adtv, 0) if adtv else None,
                "conviction_score": round(score, 1),
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

    # Load earnings inflection data (priority sort bonus)
    inflection_lookup: dict[str, str] = {}  # {ticker: inflection_label}
    inflection_file = ROOT / "data" / "bigshot" / "earnings_inflection.json"
    if inflection_file.exists():
        try:
            inf_data = json.loads(inflection_file.read_text(encoding="utf-8"))
            for c in inf_data.get("inflection_candidates", []) + inf_data.get("accelerating_candidates", []):
                ticker = c.get("ticker", "")
                label  = c.get("inflection_label", "")
                if ticker and label:
                    inflection_lookup[ticker] = label
            if inflection_lookup:
                print(f"  Inflection data loaded: {len(inflection_lookup)} tickers with revenue signal")
        except Exception as _e:
            print(f"  [WARN] Could not load earnings_inflection.json: {_e}")

    if not theme_members:
        print("  [WARN] No theme data — run rs_theme_ranker.py first")
        result = {"date": today, "candidates": [], "note": "No theme data available"}
        CAND_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    # Screen all theme members
    candidates = []
    checked    = 0
    skipped_breakout = 0
    skipped_adtv     = 0
    skipped_base     = 0

    print(f"  Screening {len(theme_members)} theme tickers...")

    for ticker, theme_info in theme_members.items():
        checked += 1

        # Fetch price history
        closes = _fetch_price_history(ticker, days=130)
        if not closes or len(closes) < 64:
            continue

        # Gate 1: Breakout (price at >= 63-day high)
        breakout = check_breakout(closes)
        if not breakout["passes"]:
            skipped_breakout += 1
            continue

        # Gate 2: Narrative (already passed by being in theme_members)
        # (theme membership IS the narrative gate)

        # Gate 3: Base count (0 or 1 only)
        base = estimate_base_count(closes)
        if not base["passes"]:
            skipped_base += 1
            continue

        # ADTV check (> $3M minimum for Mode B)
        adtv = _fetch_adtv(ticker)
        if adtv is not None and adtv < 3_000_000:
            skipped_adtv += 1
            continue

        # All gates passed — score the candidate
        is_hot            = theme_info.get("is_hot", False)
        inflection_label  = inflection_lookup.get(ticker)
        conv_score = score_candidate(breakout, base, is_hot, adtv, inflection_label)
        rs_ctx = get_rs_context(ticker, rs_data)

        candidates.append({
            "ticker":           ticker,
            "mode":             "B",
            "theme":            theme_info.get("theme"),
            "theme_grade":      theme_info.get("theme_grade"),

            # Gate results
            "breakout_type":    breakout["breakout_type"],
            "pct_above_63d_low": breakout.get("pct_above_63d_low"),
            "days_at_high":     breakout.get("days_at_high"),
            "base_count":       base["base_count"],

            # Sizing guidance (A09/A10 will finalize)
            "initial_size_pct": 5.0,          # Mode B always starts at 5%
            "max_size_pct":     10.0,          # before full confirmation
            "stop_pct":         -10.0,         # -10% from breakout pivot

            # Context (informational only)
            "rs_pct_3m":        rs_ctx.get("rs_pct_3m"),
            "rs_pct_6m":        rs_ctx.get("rs_pct_6m"),
            "rs_phase":         rs_ctx.get("rs_phase"),
            "adtv_usd":         round(adtv, 0) if adtv else None,

            # Revenue inflection signal (from earnings_inflection_scout.py)
            "revenue_inflection": inflection_label,  # None if no inflection data

            # Score for ranking
            "conviction_score": conv_score,
        })

    # Sort by conviction score, take top MAX_CANDIDATES
    candidates.sort(key=lambda x: x["conviction_score"], reverse=True)
    top_candidates = candidates[:MAX_CANDIDATES]

    # Summary
    print(f"\n  Screened: {checked} | Breakout fail: {skipped_breakout} | ADTV fail: {skipped_adtv} | Base fail: {skipped_base}")
    print(f"  Passed ALL gates: {len(candidates)} | Outputting top {len(top_candidates)}")

    if top_candidates:
        print("\n  TOP BIG SHOT CANDIDATES:")
        for i, c in enumerate(top_candidates, 1):
            hot_tag = " [HOT]" if c["theme_grade"] == "HOT" else ""
            print(f"    {i}. {c['ticker']:6} {c['breakout_type']:8} | Base {c['base_count']} | "
                  f"Theme: {c['theme']}{hot_tag} | Score: {c['conviction_score']}")

    # BOA-020-A1: Scan existing portfolio positions for Monster fingerprint
    # Stocks gaining ≥20% in ≤21 days → EMA_HOLD pyramid add candidates
    rapid_breakouts = rapid_breakout_scan()
    if rapid_breakouts:
        print(f"\n  MONSTER FINGERPRINT DETECTED ({len(rapid_breakouts)} positions):")
        for rb in rapid_breakouts:
            print(f"    {rb['ticker']:6} +{rb['pnl_pct']:.1f}% in {rb['days_in']}d "
                  f"| Hold {rb['hold_days_remaining']}d more | EMA_HOLD eligible")

    result = {
        "date":        today,
        "regime":      health.get("regime", "Unknown"),
        "bigshot_ok":  True,
        "screened":    checked,
        "passed":      len(candidates),
        "candidates":  top_candidates,
        "rapid_breakouts": rapid_breakouts,   # BOA-020-A1: Monster fingerprint positions
        "note":        f"Screened {checked} theme tickers — {len(candidates)} passed all Mode B gates",
        "generated_at": datetime.now().strftime("%H:%M"),
    }

    CAND_FILE.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"\n  -> Written: {CAND_FILE}")

    return result


if __name__ == "__main__":
    run()
