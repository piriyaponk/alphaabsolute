"""
AlphaAbsolute v2 -- A06 Leadership Curator (Mode A)
====================================================
Runs the full Mode A screen against every stock in the RS universe.
Produces:
  Top 30 Watchlist  — passes 4+ of 5 gates (monitor these)
  Top 10 Active     — passes ALL 5 gates AND has best RS momentum (act on these)

Mode A Gate (CLAUDE.md — ALL must pass for Top 10, 4+ for Top 30):
  Gate 1  RS Gate        : rs_3m_pct > 70 AND rs_6m_pct > 70
                            (HOT theme bonus: relax to 60th — pending backtest)
  Gate 2  Fundamental    : eps_yoy_latest > 25% AND rev_yoy_latest > 25%
  Gate 3  Quality        : gross margin trend = Stable or Expanding (not Contracting)
  Gate 4  Technical      : pct_from_52w_high > -20%  AND  adtv > $10M
  Gate 5  Stage          : Stage 2 (above key MAs, price in upper half of range)

Ranking (Top 10 Active):
  1. RS momentum (acceleration of RS percentile)
  2. Fundamental acceleration strength
  3. HOT theme bonus (+1 rank)

Outputs:
  data/trend_template/screener_latest.json    — full scored universe (all tickers)
  data/leadership/top30_watchlist.json        — A06 Top 30 Watchlist
  data/leadership/top10_active.json           — A06 Top 10 Active Leaders

Run: Daily at 8:00 AM, after rs_ranker.py + rs_theme_ranker.py

Cost: $0 (EDGAR 7-day disk cache via data_engine, no LLM)
"""

from __future__ import annotations
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR     = Path(__file__).resolve().parents[2]
DATA_DIR     = BASE_DIR / "data"
OUT_DIR      = DATA_DIR / "trend_template"
LEADER_DIR   = DATA_DIR / "leadership"
RS_DIR       = DATA_DIR / "rs_universe"

OUT_DIR.mkdir(parents=True, exist_ok=True)
LEADER_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR / "scripts" / "utils"))

# ── v2 Thresholds (per CLAUDE.md) ────────────────────────────────────────────

V2 = {
    "rs_3m_min":         70.0,   # Gate 1: 3M RS vs market > 70th
    "rs_6m_min":         70.0,   # Gate 1: 6M RS vs market > 70th
    "rs_hot_bonus":      60.0,   # Gate 1: relax to 60th if HOT theme (pending backtest)
    "eps_yoy_min":       25.0,   # Gate 2: EPS YoY growth > 25%
    "rev_yoy_min":       25.0,   # Gate 2: Revenue YoY growth > 25%
    "pct_52w_high_min": -20.0,   # Gate 4: within 20% of 52W high
    "adtv_min_usd":      10e6,   # Gate 4: 6M ADTV > $10M
    "stage2_50d_min":    -5.0,   # Gate 5: not more than 5% below 50DMA
    "stage2_ma_min":     -3.0,   # Gate 5: 150DMA > 200DMA (or within 3%)
    "stage2_low_min":    20.0,   # Gate 5: > 20% above 52W low
}

HOT_THRESHOLD = 75.0   # theme_vs_themes_pct > 75 = HOT


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


_FUND_CACHE: dict = {}   # ticker -> fundamentals dict, loaded once from DB

def _load_fundamentals_from_db() -> dict:
    """Load all fundamentals from SQLite fundamentals_summary (fast, no API)."""
    import sqlite3
    db_path = BASE_DIR / "data" / "ohlcv.db"
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT ticker, eps_yoy_pct, rev_yoy_pct, gm_trend,
                   gm_latest, gm_1q_ago, gm_2q_ago, gm_3q_ago, gm_4q_ago,
                   gate_eps, gate_rev, gate_gm,
                   eps_acceleration, rev_acceleration
            FROM fundamentals_summary
        """).fetchall()
        conn.close()
        result = {}
        for row in rows:
            d = dict(row)
            tkr = d.pop("ticker")
            gm_5q = [d.get("gm_latest"), d.get("gm_1q_ago"), d.get("gm_2q_ago"),
                     d.get("gm_3q_ago"), d.get("gm_4q_ago")]
            eps_accel = d.get("eps_acceleration") == "ACCELERATING"
            rev_accel = d.get("rev_acceleration") == "ACCELERATING"
            accel = "ACCELERATING" if (eps_accel and rev_accel) else (
                    "STABLE" if d.get("eps_yoy_pct", 0) and d.get("eps_yoy_pct", 0) > 0 else "UNKNOWN")
            result[tkr] = {
                "eps_yoy_latest":     d.get("eps_yoy_pct"),
                "rev_yoy_latest":     d.get("rev_yoy_pct"),
                "gm_trend":           (d.get("gm_trend") or "Unknown").capitalize(),
                "acceleration_label": accel,
                "gate_eps":           d.get("gate_eps"),
                "gate_rev":           d.get("gate_rev"),
                "gate_gm":            d.get("gate_gm"),
                "eps_accel":          eps_accel,
                "rev_accel":          rev_accel,
                "gm_5q_trend":        gm_5q,
                "_from_db":           True,
            }
        return result
    except Exception as e:
        print(f"  [!] fundamentals_summary load warning: {e}")
        return {}


def _get_fundamentals(ticker: str) -> dict:
    """
    Fetch EPS/Rev/GM — SQLite fundamentals_summary first, EDGAR fallback.
    """
    global _FUND_CACHE
    # Check in-memory cache (loaded at startup)
    if ticker in _FUND_CACHE:
        return _FUND_CACHE[ticker]

    # Fallback: try EDGAR via data_engine (slower, for tickers not in DB)
    try:
        from data_engine import get_fundamentals
        raw = get_fundamentals(ticker)
        if not raw:
            return {}

        eps_5q = [e.get("yoy_growth") for e in (raw.get("eps_history") or [])[:5]]
        rev_5q = [r.get("yoy_growth") for r in (raw.get("revenue_history") or [])[:5]]
        gm_5q  = [g.get("gross_margin") for g in (raw.get("gross_margin_history") or [])[:5]]

        eps_accel = raw.get("eps_accel", False)
        rev_accel = raw.get("rev_accel", False)
        accel = ("ACCELERATING" if (eps_accel and rev_accel) else
                 "STABLE" if raw.get("eps_yoy_pct", 0) else "UNKNOWN")

        return {
            "eps_yoy_latest":      raw.get("eps_yoy_pct"),
            "rev_yoy_latest":      raw.get("rev_yoy_pct"),
            "gm_trend":            _classify_gm_trend(gm_5q),
            "acceleration_label":  accel,
            "eps_5q_trend":        eps_5q,
            "rev_5q_trend":        rev_5q,
            "gm_5q_trend":         gm_5q,
            "eps_accel":           eps_accel,
            "rev_accel":           rev_accel,
        }
    except Exception:
        pass
    return {}


def _classify_gm_trend(gm_5q: list) -> str:
    """Classify gross margin trend from 5-quarter history (newest first)."""
    vals = [v for v in (gm_5q or [])[:3] if v is not None]
    if len(vals) < 2:
        return "Unknown"
    # newest vs oldest of available
    delta = vals[0] - vals[-1]
    if delta > 0.5:
        return "Expanding"
    elif delta < -0.5:
        return "Contracting"
    return "Stable"


def _infer_accel_label(eps_5q: list, rev_5q: list) -> str:
    """Simple acceleration label from 5Q trends."""
    eps = [v for v in (eps_5q or [])[:3] if v is not None]
    rev = [v for v in (rev_5q or [])[:3] if v is not None]
    if not eps or not rev:
        return "UNKNOWN"
    if eps[0] is not None and eps[0] > 0 and (len(eps) < 2 or (eps[-1] or 0) <= 0):
        return "TURNAROUND"
    if len(eps) >= 2 and eps[0] > eps[-1]:
        return "ACCELERATING"
    if len(eps) >= 2 and eps[0] < eps[-1] and eps[0] > 0:
        return "DECELERATING"
    if eps and eps[0] and eps[0] > 0:
        return "STABLE"
    return "UNKNOWN"


# ── Load All Pre-Computed Data ────────────────────────────────────────────────

def _load_technical_from_db() -> dict:
    """
    Load technical gate fields from SQLite screening_results (latest date).
    Returns dict: ticker -> {adtv_6m_usd, pct_from_52w_high, gate_stage2,
                              pct_from_50d_ma, ma150_vs_ma200_pct, pct_from_52w_low,
                              gate_rs, gate_adtv, gate_52w, gate_stage2}
    """
    import sqlite3
    db_path = BASE_DIR / "data" / "ohlcv.db"
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # Get the latest date's screening_results with all technical fields
        rows = conn.execute("""
            SELECT sr.ticker,
                   sr.rs_3m_pct, sr.rs_6m_pct, sr.rs_1m_pct, sr.rs_composite,
                   sr.rs_momentum_1m_3m, sr.rs_momentum_3m_6m,
                   sr.rs_comp_chg_1w, sr.rs_comp_chg_4w,
                   sr.gate_rs, sr.gate_adtv, sr.gate_52w, sr.gate_stage2,
                   sr.gate_earnings,
                   tm.adtv_6m_usd, tm.pct_from_52w_high, tm.pct_from_52w_low,
                   tm.pct_above_ma50, tm.pct_above_ma150, tm.pct_above_ma200,
                   tm.stage2_flag,
                   tm.ma_50d, tm.ma_150d, tm.ma_200d,
                   tm.high_52w, tm.low_52w,
                   tm.last_close
            FROM screening_results sr
            LEFT JOIN ticker_meta tm ON sr.ticker = tm.ticker
            WHERE sr.date = (SELECT MAX(date) FROM screening_results)
        """).fetchall()
        conn.close()
        result = {}
        for row in rows:
            d = dict(row)
            ticker = d.pop("ticker")
            # Compute derived fields expected by screener
            adtv    = d.get("adtv_6m_usd") or 0
            pct_52w = d.get("pct_from_52w_high") or -99
            pct_52l = d.get("pct_from_52w_low") or 0
            # pct_from_50d_ma = pct_above_ma50 (percent above 50DMA)
            pct_50d = d.get("pct_above_ma50") or -99
            # ma150_vs_ma200_pct: (ma150/ma200 - 1) * 100
            ma150 = d.get("ma_150d") or 0
            ma200 = d.get("ma_200d") or 0
            ma150_vs_200 = ((ma150 / ma200) - 1) * 100 if ma200 and ma150 else -99
            d["adtv_6m_usd"]         = adtv
            d["pct_from_52w_high"]   = pct_52w
            d["pct_from_52w_low"]    = pct_52l
            d["pct_from_50d_ma"]     = pct_50d
            d["ma150_vs_ma200_pct"]  = ma150_vs_200
            result[ticker] = d
        return result
    except Exception as e:
        print(f"  [!] DB load warning: {e}")
        return {}


def _load_sources() -> dict:
    # RS universe (rs_ranker output — RS percentiles)
    rs_raw     = _load_json(RS_DIR / "latest.json")
    rs_universe = rs_raw.get("universe", rs_raw)

    # Enrich RS universe with technical fields from SQLite (pipeline_metrics output)
    db_tech = _load_technical_from_db()
    for ticker, tech in db_tech.items():
        if ticker not in rs_universe:
            rs_universe[ticker] = {"ticker": ticker}
        rs_universe[ticker].update(tech)

    # Theme RS (rs_theme_ranker output — for HOT/WARM + theme name)
    theme_raw  = _load_json(RS_DIR / "theme_rs_latest.json")
    themes     = theme_raw.get("themes", {})

    # Build ticker → theme phase lookup
    ticker_theme: dict[str, dict] = {}
    for theme_id, t_info in themes.items():
        phase = t_info.get("phase", "WEAK")
        name  = t_info.get("name", theme_id)
        pct   = t_info.get("theme_vs_themes_pct", 0) or 0
        for member in (t_info.get("members") or []):
            tkr = member.get("ticker") if isinstance(member, dict) else str(member)
            if tkr:
                ticker_theme[tkr] = {
                    "theme_id":   theme_id,
                    "theme_name": name,
                    "theme_phase": phase,
                    "theme_vs_themes_pct": pct,
                }

    return {
        "rs_universe":  rs_universe,
        "ticker_theme": ticker_theme,
        "themes":       themes,
    }


# ── Mode A Gate Evaluator ─────────────────────────────────────────────────────

def _evaluate_gates(ticker: str, rs: dict, theme_info: dict,
                    fund: dict) -> dict:
    """
    Evaluate all 5 Mode A gates. Returns gate results + gate count + metadata.
    """
    theme_phase = theme_info.get("theme_phase", "WEAK")
    is_hot      = theme_phase == "HOT"
    rs_floor    = V2["rs_hot_bonus"] if is_hot else V2["rs_3m_min"]

    # ── Gate 1: RS ────────────────────────────────────────────────────────────
    rs_3m  = rs.get("rs_3m_pct")  or 0
    rs_6m  = rs.get("rs_6m_pct")  or 0
    rs_mom = rs.get("rs_mom_3m_6m") or 0

    gate1_rs = (rs_3m >= rs_floor) and (rs_6m >= V2["rs_6m_min"])
    gate1_note = f"RS 3M={rs_3m:.0f} 6M={rs_6m:.0f} floor={rs_floor:.0f}"
    if is_hot:
        gate1_note += " [HOT bonus]"

    # ── Gate 2: Fundamental ───────────────────────────────────────────────────
    eps_yoy    = fund.get("eps_yoy_latest")
    rev_yoy    = fund.get("rev_yoy_latest")
    accel      = fund.get("acceleration_label", "UNKNOWN")
    eps_5q     = fund.get("eps_5q_trend", [])
    rev_5q     = fund.get("rev_5q_trend", [])

    # Infer from 5Q trend if direct field missing
    if eps_yoy is None and eps_5q:
        eps_yoy = eps_5q[0] if eps_5q[0] is not None else None
    if rev_yoy is None and rev_5q:
        rev_yoy = rev_5q[0] if rev_5q[0] is not None else None

    gate2_fund = (eps_yoy is not None and eps_yoy > V2["eps_yoy_min"] and
                  rev_yoy is not None and rev_yoy > V2["rev_yoy_min"])
    gate2_note = (f"EPS={eps_yoy:.0f}% Rev={rev_yoy:.0f}%"
                  if eps_yoy is not None and rev_yoy is not None
                  else "EPS/Rev data unavailable")

    # ── Gate 3: Quality (Gross Margin) ────────────────────────────────────────
    gm_5q       = fund.get("gm_5q_trend", fund.get("gm_5q", []))
    gm_trend    = fund.get("gm_trend", _classify_gm_trend(gm_5q))
    gate3_quality = gm_trend != "Contracting"
    gate3_note  = f"GM trend={gm_trend}"

    # ── Gate 4: Technical ─────────────────────────────────────────────────────
    pct_52w     = rs.get("pct_from_52w_high") or -99
    adtv_usd    = rs.get("adtv_6m_usd") or 0

    gate4_tech  = (pct_52w >= V2["pct_52w_high_min"]) and (adtv_usd >= V2["adtv_min_usd"])
    gate4_note  = f"52Whi={pct_52w:.1f}% ADTV=${adtv_usd/1e6:.1f}M"

    # ── Gate 5: Stage 2 ───────────────────────────────────────────────────────
    pct_50d     = rs.get("pct_from_50d_ma")    or -99
    ma150_200   = rs.get("ma150_vs_ma200_pct") or -99
    pct_52w_low = rs.get("pct_from_52w_low")   or 0

    gate5_stage = (pct_50d   >= V2["stage2_50d_min"] and
                   ma150_200 >= V2["stage2_ma_min"]   and
                   pct_52w_low >= V2["stage2_low_min"])
    gate5_note  = (f"50dMA={pct_50d:.1f}% | 150/200MA={ma150_200:.1f}% | "
                   f"52Wlo=+{pct_52w_low:.0f}%")

    gates = {
        "rs":    gate1_rs,
        "fund":  gate2_fund,
        "qual":  gate3_quality,
        "tech":  gate4_tech,
        "stage": gate5_stage,
    }
    gate_count = sum(gates.values())
    all_pass   = all(gates.values())

    # Assign grade
    if all_pass:
        grade = "A"
    elif gate_count >= 4:
        grade = "B"
    elif gate_count >= 3:
        grade = "C"
    else:
        grade = "D"

    return {
        "ticker":      ticker,
        "grade":       grade,
        "gate_count":  gate_count,
        "all_pass":    all_pass,
        "gates":       gates,
        # Gate notes
        "gate1_rs":    gate1_rs,    "note_rs":    gate1_note,
        "gate2_fund":  gate2_fund,  "note_fund":  gate2_note,
        "gate3_qual":  gate3_quality, "note_qual": gate3_note,
        "gate4_tech":  gate4_tech,  "note_tech":  gate4_note,
        "gate5_stage": gate5_stage, "note_stage": gate5_note,
        # RS values (for ranking)
        "rs_3m":       round(rs_3m,  1),
        "rs_6m":       round(rs_6m,  1),
        "rs_mom":      round(rs_mom, 2),
        "rs_composite": rs.get("rs_composite_pct") or 0,
        # Technical values
        "pct_52w_high": round(pct_52w,    1),
        "pct_50d_ma":   round(pct_50d,    1),
        "ma150_200":    round(ma150_200,  1),
        "adtv_m":       round(adtv_usd/1e6, 1),
        # Fundamental values
        "eps_yoy":      round(eps_yoy, 1) if eps_yoy is not None else None,
        "rev_yoy":      round(rev_yoy, 1) if rev_yoy is not None else None,
        "gm_trend":     gm_trend,
        "accel_label":  accel,
        "eps_5q":       (eps_5q or [])[:5],
        "rev_5q":       (rev_5q or [])[:5],
        # Theme
        "theme":        theme_info.get("theme_name", "—"),
        "theme_phase":  theme_phase,
        "theme_pct":    round(theme_info.get("theme_vs_themes_pct", 0), 1),
        # Rank score (used to sort Top 10)
        "rank_score":   _rank_score(rs_3m, rs_6m, rs_mom, gate_count, is_hot,
                                    eps_yoy, rev_yoy, accel),
    }


def _rank_score(rs_3m: float, rs_6m: float, rs_mom: float,
                gate_count: int, is_hot: bool,
                eps_yoy: Optional[float], rev_yoy: Optional[float],
                accel: str) -> float:
    """
    Rank score for sorting Top 10. Higher = better.
    Priority: RS momentum > RS percentile > Fundamentals > HOT theme.
    """
    score = 0.0
    # RS momentum is most important (double-weight)
    score += rs_mom * 2.0
    # RS level
    score += (rs_3m + rs_6m) * 0.5
    # Fundamentals
    if eps_yoy and eps_yoy > 25:
        score += min(eps_yoy / 10, 10)  # cap at 10pts
    if rev_yoy and rev_yoy > 25:
        score += min(rev_yoy / 10, 5)   # cap at 5pts
    if accel == "ACCELERATING":
        score += 5.0
    elif accel == "TURNAROUND":
        score += 3.0
    # HOT theme bonus
    if is_hot:
        score += 8.0
    # Gate count bonus
    score += gate_count * 2.0
    return round(score, 2)


# ── Main Screener ─────────────────────────────────────────────────────────────

def run() -> dict:
    global _FUND_CACHE
    today     = date.today().isoformat()
    today_str = date.today().strftime("%y%m%d")

    print(f"\n{'='*58}")
    print(f"  A06 Leadership Curator (Mode A)  [{today}]")
    print(f"{'='*58}")

    # Pre-load fundamentals from SQLite (fast, no API calls)
    _FUND_CACHE = _load_fundamentals_from_db()
    print(f"  Fundamentals loaded from DB: {len(_FUND_CACHE)} tickers")

    sources  = _load_sources()
    universe = sources["rs_universe"]
    tickers  = sorted(universe.keys())
    print(f"  Universe: {len(tickers)} tickers")

    if not tickers:
        print("  [!] No RS data — run rs_ranker.py first")
        _emit_empty(today)
        return {}

    # ── Phase 1: Fast pre-filter (RS + Technical + Stage, no API) ───────────
    # Only fetch fundamentals for stocks that pass the fast gates
    pre_filtered: list[str] = []
    for ticker in tickers:
        rs  = universe[ticker]
        th  = sources["ticker_theme"].get(ticker, {})
        is_hot = th.get("theme_phase") == "HOT"
        rs_floor = V2["rs_hot_bonus"] if is_hot else V2["rs_3m_min"]

        rs_3m    = rs.get("rs_3m_pct") or 0
        rs_6m    = rs.get("rs_6m_pct") or 0
        pct_52w  = rs.get("pct_from_52w_high") or -99
        adtv_usd = rs.get("adtv_6m_usd") or 0
        pct_50d  = rs.get("pct_from_50d_ma") or -99
        ma150    = rs.get("ma150_vs_ma200_pct") or -99
        pct_low  = rs.get("pct_from_52w_low") or 0

        rs_ok    = rs_3m >= rs_floor and rs_6m >= V2["rs_6m_min"]
        tech_ok  = pct_52w >= V2["pct_52w_high_min"] and adtv_usd >= V2["adtv_min_usd"]
        stage_ok = (pct_50d >= V2["stage2_50d_min"] and
                    ma150   >= V2["stage2_ma_min"] and
                    pct_low >= V2["stage2_low_min"])

        # Must pass at least RS gate + 1 other to bother fetching fundamentals
        fast_passes = sum([rs_ok, tech_ok, stage_ok])
        if fast_passes >= 2:
            pre_filtered.append(ticker)

    print(f"  Pre-filter (RS+Tech+Stage): {len(pre_filtered)}/{len(tickers)} qualify")

    # ── Phase 2: Full evaluation with fundamentals ───────────────────────────
    results: list[dict] = []
    fund_fetched = 0

    for ticker in pre_filtered:
        rs  = universe[ticker]
        th  = sources["ticker_theme"].get(ticker, {})

        # Fetch fundamentals from EDGAR (disk-cached, fast for cached tickers)
        fund = _get_fundamentals(ticker)
        if fund:
            fund_fetched += 1

        try:
            result = _evaluate_gates(ticker, rs, th, fund)
            results.append(result)
        except Exception as e:
            pass

    print(f"  Fundamentals fetched: {fund_fetched}/{len(pre_filtered)} (rest from cache)")

    # Sort by rank_score descending
    results.sort(key=lambda x: -x["rank_score"])

    # ── Grade A (all 5 gates) and B (4+ gates) breakdown ────────────────────
    grade_a = [r for r in results if r["grade"] == "A"]
    grade_b = [r for r in results if r["grade"] == "B"]
    grade_c = [r for r in results if r["grade"] == "C"]

    print(f"\n  Grade A (all 5 gates): {len(grade_a)}")
    print(f"  Grade B (4 gates):     {len(grade_b)}")
    print(f"  Grade C (3 gates):     {len(grade_c)}")

    # ── Top 30 Watchlist (grade A+B, up to 30) ───────────────────────────────
    top30 = (grade_a + grade_b)[:30]

    # ── Top 10 Active (grade A only, top 10 by rank_score) ───────────────────
    top10 = grade_a[:10]

    # Print console summary
    _print_summary(top10, "TOP 10 ACTIVE LEADERS")
    _print_summary(top30[len(top10):20], "TOP 30 WATCHLIST (next batch)")

    # ── Write outputs ─────────────────────────────────────────────────────────
    full_output = {
        "date":         today,
        "computed_at":  datetime.now().isoformat(),
        "n_universe":   len(tickers),
        "n_prescreened": len(pre_filtered),
        "n_grade_a":    len(grade_a),
        "n_grade_b":    len(grade_b),
        "n_grade_c":    len(grade_c),
        "thresholds":   V2,
        "results":      results,
    }
    (OUT_DIR / "screener_latest.json").write_text(
        json.dumps(full_output, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    top30_out = {
        "date":       today,
        "computed_at": datetime.now().isoformat(),
        "n_stocks":   len(top30),
        "note":       "Mode A passes 4+ of 5 gates. Monitor for setup formation.",
        "watchlist":  _to_summary(top30),
    }
    (LEADER_DIR / "top30_watchlist.json").write_text(
        json.dumps(top30_out, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    top10_out = {
        "date":       today,
        "computed_at": datetime.now().isoformat(),
        "n_stocks":   len(top10),
        "note":       "Mode A passes ALL 5 gates. Highest conviction for immediate action.",
        "leaders":    _to_summary(top10),
    }
    (LEADER_DIR / "top10_active.json").write_text(
        json.dumps(top10_out, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\n  -> data/trend_template/screener_latest.json")
    print(f"  -> data/leadership/top30_watchlist.json ({len(top30)} stocks)")
    print(f"  -> data/leadership/top10_active.json ({len(top10)} active leaders)")

    return full_output


def _to_summary(stocks: list[dict]) -> list[dict]:
    """Strip down to compact summary for watchlist output."""
    return [
        {
            "ticker":     s["ticker"],
            "grade":      s["grade"],
            "gate_count": s["gate_count"],
            "gates":      s["gates"],
            "rs_3m":      s["rs_3m"],
            "rs_6m":      s["rs_6m"],
            "rs_mom":     s["rs_mom"],
            "eps_yoy":    s["eps_yoy"],
            "rev_yoy":    s["rev_yoy"],
            "gm_trend":   s["gm_trend"],
            "accel_label": s["accel_label"],
            "pct_52w_high": s["pct_52w_high"],
            "adtv_m":     s["adtv_m"],
            "theme":      s["theme"],
            "theme_phase": s["theme_phase"],
            "rank_score": s["rank_score"],
        }
        for s in stocks
    ]


def _print_summary(stocks: list[dict], title: str) -> None:
    if not stocks:
        return
    print(f"\n  {title}")
    print(f"  {'TICKER':<7} {'Grd':<4} {'Gates':<6} {'RS3M':>4} {'RS6M':>4} "
          f"{'Mom':>5} {'EPS%':>6} {'Rev%':>6} {'GM':>12} {'Theme'}")
    print(f"  {'-'*85}")
    for r in stocks:
        gm  = (r.get("gm_trend") or "?")[:10]
        eps = f"{r['eps_yoy']:+.0f}%" if r.get("eps_yoy") is not None else "  N/A"
        rev = f"{r['rev_yoy']:+.0f}%" if r.get("rev_yoy") is not None else "  N/A"
        gates_str = "".join("✓" if r["gates"].get(g) else "✗"
                            for g in ["rs","fund","qual","tech","stage"])
        theme = f"{r.get('theme','—')[:16]} {('[HOT]' if r['theme_phase']=='HOT' else '')}"
        print(f"  {r['ticker']:<7} {r['grade']:<4} {gates_str:<6} "
              f"{r['rs_3m']:>4.0f} {r['rs_6m']:>4.0f} {r['rs_mom']:>+5.1f} "
              f"{eps:>6} {rev:>6} {gm:>12} {theme}")


def _emit_empty(today: str) -> None:
    """Write empty outputs when no universe data is available."""
    empty_w = {"date": today, "n_stocks": 0, "note": "No RS data", "watchlist": []}
    empty_l = {"date": today, "n_stocks": 0, "note": "No RS data", "leaders": []}
    (LEADER_DIR / "top30_watchlist.json").write_text(
        json.dumps(empty_w, indent=2), encoding="utf-8"
    )
    (LEADER_DIR / "top10_active.json").write_text(
        json.dumps(empty_l, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "screener_latest.json").write_text(
        json.dumps({"date": today, "n_universe": 0, "results": []}, indent=2),
        encoding="utf-8"
    )


if __name__ == "__main__":
    run()
