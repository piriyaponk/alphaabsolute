"""
AlphaAbsolute v2 -- RS Change Detector  (A03c)
===============================================
Compares today's RS universe snapshot with yesterday's to detect:

  CLIMBERS (Add to Focus List):
  • New RS Leader        — newly crossed RS 3M > 70th percentile
  • Fresh Breakout       — price at new 63-day high (not yesterday)
  • Sector RS Improver   — theme_vs_themes_pct rose > 8 pts since yesterday
  • Revenue Inflection   — first quarter EPS/Rev > 25% (EDGAR check)
  • New HOT Theme Entry  — stock newly classified under a HOT theme

  DROPPERS (Trim / Remove from Focus List):
  • RS Fallen Below 70   — was >70, now <70 on 3M
  • RS Rank Drop > 20    — RS 3M fell > 20 percentile points
  • MA50 Break           — price crossed below 50DMA (was above)
  • Failed Breakout      — was within 3% of 63D high, now -5% or more
  • Sector Lost Momentum — theme_vs_themes_pct dropped > 10 pts
  • Trim / Remove signal — stop hit OR 3 consecutive RS drops

Output:  data/rs_universe/changes_today.json
Run:     Daily after rs_ranker.py + rs_theme_ranker.py (Step A03c in pipeline)

Cost: $0 (reads local JSON files only — no API calls)
"""

from __future__ import annotations
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT    = Path(__file__).resolve().parents[2]
RS_DIR  = ROOT / "data" / "rs_universe"
RS_DIR.mkdir(parents=True, exist_ok=True)

SNAPSHOT_DIR = RS_DIR / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

OUT_FILE = RS_DIR / "changes_today.json"

# ── Thresholds ─────────────────────────────────────────────────────────────────
RS_LEADER_THRESHOLD    = 70.0   # RS 3M > 70 = "leader"
RS_DROP_ALERT          = 20.0   # RS 3M dropped > 20 pts = major drop
SECTOR_IMPROVE_MIN     = 8.0    # theme_pct rose > 8 pts = sector improver
SECTOR_LOSE_MIN        = 10.0   # theme_pct dropped > 10 pts = sector deteriorating
FAILED_BKT_THRESHOLD   = -5.0   # was near 63D high, now -5% = failed breakout
NEAR_63D_HIGH          = 1.03   # within 3% of 63D high = "breakout zone"
NEW_63D_HIGH_THRESHOLD = 0.0    # pct_from_52w_high > -8 AND was lower yesterday = breakout


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _today_str() -> str:
    return date.today().strftime("%y%m%d")


def _prev_snapshot() -> dict:
    """Find the most recent previous snapshot (up to 5 trading days back)."""
    for days_back in range(1, 8):
        d = date.today() - timedelta(days=days_back)
        snap = SNAPSHOT_DIR / f"snapshot_{d.strftime('%y%m%d')}.json"
        if snap.exists():
            try:
                data = json.loads(snap.read_text(encoding="utf-8"))
                return data, d.strftime("%y%m%d")
            except Exception:
                continue
    return {}, ""


def _save_snapshot(universe: dict) -> None:
    """Save today's RS universe as a dated snapshot."""
    snap_file = SNAPSHOT_DIR / f"snapshot_{_today_str()}.json"
    # Save compact version (only the fields we need for diffing)
    # Supports both DB-format (rs_3m key) and JSON-format (rs_3m_pct key)
    compact = {}
    for ticker, data in universe.items():
        rs_3m = (data.get("rs_3m") or data.get("rs_3m_pct")
                 or data.get("rs_pct_3m") or 0)
        rs_6m = (data.get("rs_6m") or data.get("rs_6m_pct")
                 or data.get("rs_pct_6m") or 0)
        compact[ticker] = {
            "rs_3m":      rs_3m,
            "rs_6m":      rs_6m,
            "rs_mom":     (data.get("rs_mom") or data.get("rs_mom_3m_6m")
                           or data.get("rs_momentum_3m_6m") or 0),
            "pct_52w_hi": (data.get("pct_52w_hi") or data.get("pct_from_52w_high") or -99),
            "pct_52w_lo": (data.get("pct_52w_lo") or data.get("pct_from_52w_low") or 0),
            "pct_50d":    (data.get("pct_50d") or data.get("pct_from_50d_ma")
                           or data.get("pct_above_ma50") or 0),
            "theme_pct":  data.get("theme_pct") or data.get("pulse_rs_theme_pct"),
            "theme_name": data.get("theme_name") or data.get("pulse_rs_theme_name"),
            "price":      data.get("price") or data.get("price_last"),
        }
    snap_file.write_text(json.dumps(compact, indent=2), encoding="utf-8")

    # Prune snapshots older than 10 days
    cutoff = date.today() - timedelta(days=10)
    for snap in SNAPSHOT_DIR.glob("snapshot_*.json"):
        try:
            snap_date_str = snap.stem.replace("snapshot_", "")
            snap_date = datetime.strptime(snap_date_str, "%y%m%d").date()
            if snap_date < cutoff:
                snap.unlink()
        except Exception:
            pass


def _theme_phase(ticker: str, theme_data: dict) -> str:
    """Look up theme phase for a ticker from rs_theme_ranker output."""
    for theme_id, t_info in theme_data.items():
        members = t_info.get("members", [])
        for m in members:
            tkr = m.get("ticker") if isinstance(m, dict) else str(m)
            if tkr == ticker:
                return t_info.get("phase", "WEAK")
    return "WEAK"


# ── Event Detectors ───────────────────────────────────────────────────────────

def _detect_climbers(today_u: dict, prev_u: dict, theme_data: dict) -> list[dict]:
    """Detect stocks that improved meaningfully since last snapshot."""
    events: list[dict] = []

    for ticker, today in today_u.items():
        prev = prev_u.get(ticker, {})

        rs_now  = today.get("rs_3m")  or 0
        rs_prev = prev.get("rs_3m")   or 0
        th_now  = today.get("theme_pct") or 0
        th_prev = prev.get("theme_pct") or 0
        p52hi   = today.get("pct_52w_hi") or -99
        p52hi_p = prev.get("pct_52w_hi")  or -99

        theme_name  = today.get("theme_name") or "—"
        theme_phase = _theme_phase(ticker, theme_data)

        # ── New RS Leader ─────────────────────────────────────────────────────
        if rs_now >= RS_LEADER_THRESHOLD and (rs_prev < RS_LEADER_THRESHOLD or not prev):
            events.append({
                "ticker":  ticker,
                "event":   "new_rs_leader",
                "rs_3m_prev": round(rs_prev, 1),
                "rs_3m_curr": round(rs_now,  1),
                "rs_3m_delta": round(rs_now - rs_prev, 1),
                "theme":   theme_name,
                "theme_phase": theme_phase,
                "note":    f"RS 3M crossed 70th: {rs_prev:.0f}→{rs_now:.0f}",
            })
            continue   # don't double-count as sector improver

        # ── Fresh Breakout (crossed new 63-day high territory) ────────────────
        # Proxy: pct_from_52w_high improved by > 3 pts AND is now near high (>-10%)
        pct_hi_delta = p52hi - p52hi_p if p52hi_p else 0
        if (p52hi >= -10.0 and p52hi_p < -10.0
                and rs_now >= 50):   # must have reasonable RS
            events.append({
                "ticker":  ticker,
                "event":   "fresh_breakout",
                "pct_52w_high": round(p52hi, 1),
                "pct_52w_high_prev": round(p52hi_p, 1),
                "rs_3m":   round(rs_now, 1),
                "theme":   theme_name,
                "theme_phase": theme_phase,
                "note":    f"Price surged to -{abs(p52hi):.1f}% from 52W high",
            })

        # ── Sector RS Improver ────────────────────────────────────────────────
        elif th_now > 0 and th_prev > 0 and (th_now - th_prev) >= SECTOR_IMPROVE_MIN:
            events.append({
                "ticker":  ticker,
                "event":   "sector_rs_improver",
                "theme_pct_prev": round(th_prev, 1),
                "theme_pct_curr": round(th_now,  1),
                "theme_pct_delta": round(th_now - th_prev, 1),
                "rs_3m":   round(rs_now, 1),
                "theme":   theme_name,
                "theme_phase": theme_phase,
                "note":    f"Sector RS: {th_prev:.0f}→{th_now:.0f} (+{th_now-th_prev:.0f}pts)",
            })

    # Sort by event priority, then rs_3m descending
    priority = {"new_rs_leader": 0, "fresh_breakout": 1, "sector_rs_improver": 2}
    events.sort(key=lambda x: (priority.get(x["event"], 9), -x.get("rs_3m", 0)))
    return events[:15]   # cap at 15 climbers per day


def _detect_droppers(today_u: dict, prev_u: dict, theme_data: dict,
                     portfolio: dict) -> list[dict]:
    """Detect stocks that deteriorated meaningfully since last snapshot."""
    events: list[dict] = []

    held_tickers = set(portfolio.get("positions", {}).keys())

    for ticker, prev in prev_u.items():
        today = today_u.get(ticker, {})
        if not today:
            continue   # ticker dropped from universe entirely

        # Support both snapshot format (rs_3m) and DB format (rs_3m_pct)
        rs_now_raw = today.get("rs_3m") or today.get("rs_3m_pct") or today.get("rs_pct_3m")
        rs_prev    = prev.get("rs_3m")  or prev.get("rs_3m_pct")  or 0

        # Skip data gaps: rs_now is None/NULL means no price data for this date, not a real drop
        if rs_now_raw is None:
            continue
        rs_now = float(rs_now_raw)

        th_now  = today.get("theme_pct") or 0
        th_prev = prev.get("theme_pct") or 0
        p50_now = today.get("pct_50d") or today.get("pct_from_50d_ma") or today.get("pct_above_ma50") or 0
        p50_prev = prev.get("pct_50d") or 0
        p52hi   = today.get("pct_52w_hi") or today.get("pct_from_52w_high") or -99
        p52hi_p = prev.get("pct_52w_hi") or -99

        theme_name  = today.get("theme_name") or prev.get("theme_name") or "—"
        theme_phase = _theme_phase(ticker, theme_data)
        is_held     = ticker in held_tickers

        # ── RS Fell Below 70 ──────────────────────────────────────────────────
        if rs_prev >= RS_LEADER_THRESHOLD and rs_now < RS_LEADER_THRESHOLD:
            action = "TRIM" if is_held else "REMOVE_WATCHLIST"
            events.append({
                "ticker":    ticker,
                "event":     "rs_fell_below_70",
                "action":    action,
                "rs_3m_prev": round(rs_prev, 1),
                "rs_3m_curr": round(rs_now,  1),
                "rs_3m_delta": round(rs_now - rs_prev, 1),
                "theme":     theme_name,
                "theme_phase": theme_phase,
                "held":      is_held,
                "note":      f"RS 3M fell below 70th: {rs_prev:.0f}→{rs_now:.0f}",
            })

        # ── RS Rank Drop > 20 pts ─────────────────────────────────────────────
        elif rs_prev > 0 and (rs_prev - rs_now) >= RS_DROP_ALERT:
            action = "REVIEW" if is_held else "WATCHLIST_REDUCE"
            events.append({
                "ticker":    ticker,
                "event":     "rs_rank_drop_20",
                "action":    action,
                "rs_3m_prev": round(rs_prev, 1),
                "rs_3m_curr": round(rs_now,  1),
                "rs_3m_delta": round(rs_now - rs_prev, 1),
                "theme":     theme_name,
                "theme_phase": theme_phase,
                "held":      is_held,
                "note":      f"RS 3M dropped {rs_prev-rs_now:.0f}pts: {rs_prev:.0f}→{rs_now:.0f}",
            })

        # ── MA50 Break ────────────────────────────────────────────────────────
        if p50_prev >= 0 and p50_now < -2.0:   # crossed from above to >2% below
            action = "STOP_CHECK" if is_held else "WATCHLIST_REMOVE"
            events.append({
                "ticker":    ticker,
                "event":     "ma50_break",
                "action":    action,
                "pct_50d_prev": round(p50_prev, 1),
                "pct_50d_curr": round(p50_now,  1),
                "rs_3m":     round(rs_now, 1),
                "theme":     theme_name,
                "theme_phase": theme_phase,
                "held":      is_held,
                "note":      f"Broke below 50DMA: {p50_prev:+.1f}%→{p50_now:+.1f}%",
            })

        # ── Failed Breakout ───────────────────────────────────────────────────
        if (p52hi_p >= -3.0               # was near 52W high yesterday
                and p52hi <= FAILED_BKT_THRESHOLD   # now -5% or worse
                and rs_now >= 50):        # was a reasonable RS stock
            action = "TRIM_IMMEDIATELY" if is_held else "REMOVE_WATCHLIST"
            events.append({
                "ticker":    ticker,
                "event":     "failed_breakout",
                "action":    action,
                "pct_52w_high_prev": round(p52hi_p, 1),
                "pct_52w_high_curr": round(p52hi, 1),
                "rs_3m":     round(rs_now, 1),
                "theme":     theme_name,
                "theme_phase": theme_phase,
                "held":      is_held,
                "note":      f"Breakout failed: {p52hi_p:+.1f}%→{p52hi:+.1f}% from 52W high",
            })

        # ── Sector Lost Momentum ──────────────────────────────────────────────
        if th_prev > 0 and th_now > 0 and (th_prev - th_now) >= SECTOR_LOSE_MIN:
            events.append({
                "ticker":    ticker,
                "event":     "sector_lost_momentum",
                "action":    "REVIEW",
                "theme_pct_prev": round(th_prev, 1),
                "theme_pct_curr": round(th_now,  1),
                "theme_pct_delta": round(th_now - th_prev, 1),
                "rs_3m":     round(rs_now, 1),
                "theme":     theme_name,
                "theme_phase": theme_phase,
                "held":      is_held,
                "note":      f"Sector RS fell {th_prev-th_now:.0f}pts: {th_prev:.0f}→{th_now:.0f}",
            })

    # Sort: IMMEDIATE actions first, then by severity
    action_priority = {
        "TRIM_IMMEDIATELY": 0, "STOP_CHECK": 1, "TRIM": 2,
        "REVIEW": 3, "REMOVE_WATCHLIST": 4, "WATCHLIST_REDUCE": 5,
        "WATCHLIST_REMOVE": 6,
    }
    events.sort(key=lambda x: (action_priority.get(x.get("action","REVIEW"), 9),
                               x.get("rs_3m_delta", 0)))
    return events[:20]


def _detect_revenue_inflections(today_u: dict, prev_u: dict) -> list[dict]:
    """
    Detect stocks where EPS/Revenue went positive for the first time.
    Uses EDGAR cache via data_engine.
    """
    events = []
    sys.path.insert(0, str(ROOT / "scripts" / "utils"))
    try:
        from data_engine import get_fundamentals
    except Exception:
        return []

    # Only check stocks that have RS > 50 (worth tracking)
    candidates = [t for t, d in today_u.items() if (d.get("rs_3m") or 0) >= 50]

    for ticker in candidates[:50]:   # cap at 50 to avoid API overload
        try:
            fund = get_fundamentals(ticker)
            if not fund:
                continue
            eps_hist = fund.get("eps_history", [])
            rev_hist = fund.get("revenue_history", [])
            if len(eps_hist) < 2 or len(rev_hist) < 2:
                continue

            # Current quarter
            eps_q0 = eps_hist[0].get("yoy_growth")
            eps_q1 = eps_hist[1].get("yoy_growth")
            rev_q0 = rev_hist[0].get("yoy_growth")
            rev_q1 = rev_hist[1].get("yoy_growth")

            # Revenue inflection: current Q > 25% AND prior Q was < 15%
            if (rev_q0 is not None and rev_q1 is not None
                    and rev_q0 > 25 and rev_q1 < 15):
                events.append({
                    "ticker":    ticker,
                    "event":     "revenue_inflection",
                    "rev_q0":    round(rev_q0, 1),
                    "rev_q1":    round(rev_q1, 1),
                    "eps_q0":    round(eps_q0, 1) if eps_q0 is not None else None,
                    "rs_3m":     round(today_u[ticker].get("rs_3m") or 0, 1),
                    "theme":     today_u[ticker].get("theme_name") or "—",
                    "note":      f"Rev inflection: Q-1={rev_q1:.0f}%→Q0={rev_q0:.0f}%",
                })
        except Exception:
            continue

    events.sort(key=lambda x: -x.get("rev_q0", 0))
    return events[:8]


# ── DB-backed universe loader ─────────────────────────────────────────────────

def _load_universe_from_db() -> dict:
    """
    Load today's RS universe from SQLite screening_results + ticker_meta.
    Returns dict: ticker -> {rs_3m_pct, rs_6m_pct, rs_mom, pct_52w_hi, pct_50d, ...}
    Normalized to the same key names the snapshot format uses.
    """
    import sqlite3
    db_path = ROOT / "data" / "ohlcv.db"
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT sr.ticker,
                   sr.rs_3m_pct, sr.rs_6m_pct, sr.rs_1m_pct,
                   sr.rs_momentum_1m_3m, sr.rs_momentum_3m_6m,
                   sr.rs_composite,
                   tm.pct_from_52w_high, tm.pct_from_52w_low,
                   tm.pct_above_ma50,
                   tm.adtv_6m_usd, tm.last_close
            FROM screening_results sr
            LEFT JOIN ticker_meta tm ON sr.ticker = tm.ticker
            WHERE sr.date = (SELECT MAX(date) FROM screening_results)
        """).fetchall()
        conn.close()
        result = {}
        for row in rows:
            d = dict(row)
            tkr = d.pop("ticker")
            # Normalize to snapshot format keys
            result[tkr] = {
                "rs_3m_pct":     d.get("rs_3m_pct"),
                "rs_6m_pct":     d.get("rs_6m_pct"),
                "rs_mom_3m_6m":  d.get("rs_momentum_3m_6m"),
                "pct_from_52w_high":  d.get("pct_from_52w_high"),
                "pct_from_52w_low":   d.get("pct_from_52w_low"),
                "pct_from_50d_ma":    d.get("pct_above_ma50"),
                "adtv_6m_usd":   d.get("adtv_6m_usd"),
                "price_last":    d.get("last_close"),
                # Snapshot-compatible aliases
                "rs_3m":     d.get("rs_3m_pct"),
                "rs_6m":     d.get("rs_6m_pct"),
                "rs_mom":    d.get("rs_momentum_3m_6m"),
                "pct_52w_hi": d.get("pct_from_52w_high"),
                "pct_52w_lo": d.get("pct_from_52w_low"),
                "pct_50d":   d.get("pct_above_ma50"),
            }
        return result
    except Exception as e:
        print(f"  [!] DB universe load: {e}")
        return {}


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> dict:
    today     = date.today().isoformat()
    today_str = _today_str()

    print(f"\n{'='*55}")
    print(f"  A03c RS Change Detector  [{today}]")
    print(f"{'='*55}")

    # Load today's RS universe — prefer SQLite (pipeline_metrics output), fallback to JSON
    today_u = _load_universe_from_db()
    if not today_u:
        # Fallback: read from latest.json
        rs_raw  = _load_json(RS_DIR / "latest.json")
        today_u = rs_raw.get("universe", rs_raw)

    if not today_u:
        print("  [!] No RS data — run pipeline_metrics.py or rs_ranker.py first")
        _write_empty(today)
        return {}

    print(f"  Universe: {len(today_u)} tickers")

    # Load theme data (for HOT classification)
    theme_raw  = _load_json(RS_DIR / "theme_rs_latest.json")
    theme_data = theme_raw.get("themes", {})

    # Load portfolio (for held position context)
    portfolio = _load_json(ROOT / "data" / "portfolio" / "portfolio_state.json")

    # Save today's snapshot BEFORE comparing (so we have it for tomorrow)
    _save_snapshot(today_u)

    # Load previous snapshot
    prev_u, prev_date = _prev_snapshot()
    if not prev_u:
        print("  [!] No previous snapshot found — first run today, nothing to compare")
        print("  Snapshot saved for tomorrow's comparison.")
        result = {
            "date": today,
            "prev_date": None,
            "first_run": True,
            "climbers": [],
            "droppers": [],
            "revenue_inflections": [],
            "note": "First run — no previous snapshot available for comparison",
        }
        OUT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    print(f"  Comparing vs snapshot from: {prev_date}")

    # Detect changes
    climbers   = _detect_climbers(today_u, prev_u, theme_data)
    droppers   = _detect_droppers(today_u, prev_u, theme_data, portfolio)
    rev_inflx  = _detect_revenue_inflections(today_u, prev_u)

    # Console summary
    print(f"\n  CLIMBERS ({len(climbers)}):")
    for c in climbers[:8]:
        print(f"    [{c['event'][:18]:<18}] {c['ticker']:<7} | {c['note']}")

    print(f"\n  DROPPERS ({len(droppers)}):")
    for d in droppers[:8]:
        action = d.get("action", "REVIEW")
        print(f"    [{action:<20}] {d['ticker']:<7} | {d['note']}")

    if rev_inflx:
        print(f"\n  REVENUE INFLECTIONS ({len(rev_inflx)}):")
        for r in rev_inflx:
            print(f"    {r['ticker']:<7} | {r['note']}")

    # Write output
    result = {
        "date":             today,
        "prev_date":        prev_date,
        "n_universe":       len(today_u),
        "n_climbers":       len(climbers),
        "n_droppers":       len(droppers),
        "n_rev_inflections": len(rev_inflx),
        "climbers":         climbers,
        "droppers":         droppers,
        "revenue_inflections": rev_inflx,
        "generated_at":     datetime.now().strftime("%H:%M"),
    }
    OUT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\n  -> Written: {OUT_FILE}")
    return result


def _write_empty(today: str) -> None:
    OUT_FILE.write_text(json.dumps({
        "date": today, "first_run": True,
        "climbers": [], "droppers": [], "revenue_inflections": [],
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    run()
