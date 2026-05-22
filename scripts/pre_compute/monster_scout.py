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
    """RS is informational only in Mode B — not a filter."""
    if ticker in rs_data:
        r = rs_data[ticker]
        return {
            "rs_pct_3m": r.get("rs_pct_3m"),
            "rs_pct_6m": r.get("rs_pct_6m"),
            "rs_phase":  r.get("phase", "Unknown"),
            "note": "RS context only — not used as gate in Mode B",
        }
    return {"rs_pct_3m": None, "rs_pct_6m": None, "rs_phase": "Unknown", "note": "No RS data"}


# ── Score candidate ───────────────────────────────────────────────────────────

def score_candidate(breakout: dict, base: dict, theme_hot: bool,
                    adtv_usd: Optional[float]) -> float:
    """
    Composite conviction score for ranking (higher = better).
    Max ~30 points.
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

    return round(score, 2)


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
        for theme_id, theme_data in data.items():
            if theme_id.startswith("_"):
                continue
            if not isinstance(theme_data, dict):
                continue
            is_hot = theme_data.get("grade") == "HOT"
            for ticker in theme_data.get("members", []):
                if isinstance(ticker, str) and ticker.upper():
                    members[ticker.upper()] = {
                        "theme": theme_id,
                        "is_hot": is_hot,
                        "theme_grade": theme_data.get("grade", "UNKNOWN"),
                    }
    except Exception:
        pass

    return members


def load_rs_data() -> dict[str, dict]:
    """Returns {ticker: {rs_pct_3m, rs_pct_6m, phase}} from latest.json."""
    rs: dict[str, dict] = {}
    latest_file = ROOT / "data" / "rs_universe" / "latest.json"
    if not latest_file.exists():
        return rs
    try:
        data = json.loads(latest_file.read_text(encoding="utf-8"))
        for item in data.get("results", []):
            ticker = item.get("ticker", "").upper()
            if ticker:
                rs[ticker] = item
    except Exception:
        pass
    return rs


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    today = date.today().isoformat()
    print(f"\n{'='*55}")
    print(f"  A07 Monster Scout (Mode B)  [{today}]")
    print(f"{'='*55}")

    # Check regime first — in Markdown, no Big Shot entries
    health_file = ROOT / "data" / "regime" / "market_health.json"
    health = _load_json(health_file)
    if not health.get("bigshot_ok", True):
        regime = health.get("regime", "Unknown")
        print(f"  [BLOCKED] bigshot_ok=False (regime={regime}) — no Mode B entries today")
        result = {
            "date": today,
            "regime": regime,
            "bigshot_ok": False,
            "candidates": [],
            "note": f"Mode B blocked by regime ({regime})",
        }
        CAND_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    # Load data sources
    print("  Loading theme members and RS data...")
    theme_members = load_theme_members()
    rs_data       = load_rs_data()
    print(f"  Theme members: {len(theme_members)} | RS data: {len(rs_data)} tickers")

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
        is_hot = theme_info.get("is_hot", False)
        conv_score = score_candidate(breakout, base, is_hot, adtv)
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

    result = {
        "date":        today,
        "regime":      health.get("regime", "Unknown"),
        "bigshot_ok":  True,
        "screened":    checked,
        "passed":      len(candidates),
        "candidates":  top_candidates,
        "note":        f"Screened {checked} theme tickers — {len(candidates)} passed all Mode B gates",
        "generated_at": datetime.now().strftime("%H:%M"),
    }

    CAND_FILE.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"\n  -> Written: {CAND_FILE}")

    return result


if __name__ == "__main__":
    run()
