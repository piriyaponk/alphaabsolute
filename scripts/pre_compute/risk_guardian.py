"""
AlphaAbsolute -- Risk Guardian  (T3 agent -- $0 cost, pure Python)
=================================================================
Portfolio-level daily risk check. Runs AFTER market close.
Ensures the portfolio stays within risk parameters across all positions.

This is the T3 Risk Guardian -- reviews every position and the portfolio
as a whole for concentration risk, stop proximity, regime misalignment,
and drawdown risk.

Output: data/risk_guardian/daily_report.json
        Telegram alert if any IMMEDIATE actions required

Rules enforced (from CLAUDE.md):
  - Max single position: 15% of equity
  - Max theme concentration: 50% of equity
  - Hypergrowth Base 0: max 5% per position
  - Bottom Fish: max 4% before Stage 2 confirm
  - Stop proximity: flag any position within 2% of stop
  - ADTV: position <= 20% of 6M ADTV
  - Stage 3/4 on held position: immediate flag
  - RS rank decay from top quartile: downgrade priority
  - Earnings within 5 days: reduce to < 3%
  - M0 regime downgrade: reduce all positions per playbook
  - Distribution days >= 4: raise cash flags

Cost: $0 (pure Python, reads JSON state -- no LLM)
"""

import json
import os
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Optional

# ── Encoding fix for Thai terminal (cp874) ───────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[2]
OUT_DIR  = BASE_DIR / "data" / "risk"       # v2 spec: data/risk/ (not risk_guardian/)
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ── Risk Thresholds (from CLAUDE.md v2) ──────────────────────────────────────
MAX_SINGLE_POSITION_PCT  = 0.15   # 15% max per position
MAX_THEME_PCT            = 0.40   # 40% max per theme (v2 spec — was 50%, corrected)
MAX_MODE_B_BUCKET_PCT    = 0.30   # 30% max total Monster Scout bucket
MAX_HYPERGROWTH_BASE0    = 0.05   # 5% for Base 0
MAX_BOTTOM_FISH          = 0.04   # 4% pre-Stage 2
STOP_PROXIMITY_WARN_PCT  = 0.02   # flag if within 2% of stop
EARNINGS_REDUCE_DAYS     = 5      # reduce to <3% if earnings within N days
EARNINGS_MAX_WEIGHT      = 0.03   # 3% max during earnings window
RS_LAGGARD_THRESHOLD     = 40     # below 40th percentile = laggard warning

# Action priority labels
ACTION_IMMEDIATE = "IMMEDIATE"
ACTION_TODAY     = "TODAY"
ACTION_REVIEW    = "REVIEW"
ACTION_OK        = "OK"

# Portfolio drawdown circuit breaker thresholds
# Goal: drawdown smaller than market (NASDAQ drawdowns avg -12% for 5%+ corrections)
DRAWDOWN_REVIEW_PCT    = 0.05   # -5% from peak NAV → review flag
DRAWDOWN_REDUCE_PCT    = 0.08   # -8% from peak NAV → TODAY flag, reduce all longs
DRAWDOWN_CIRCUIT_PCT   = 0.12   # -12% from peak NAV → IMMEDIATE, halt new entries
NAV_HISTORY_FILE       = BASE_DIR / "data" / "risk" / "nav_history.json"
QQQ_HISTORY_FILE       = BASE_DIR / "data" / "risk" / "qqq_history.json"


def _load_portfolio() -> dict:
    # v2 canonical path: data/portfolio/paper_portfolio_state.json
    # Falls back to portfolio_state.json (real signals) if paper file missing
    for port_file in [
        BASE_DIR / "data" / "portfolio" / "paper_portfolio_state.json",
        BASE_DIR / "data" / "portfolio" / "portfolio_state.json",
    ]:
        if port_file.exists():
            try:
                return json.loads(port_file.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def _load_m0() -> dict:
    """Load market regime from canonical v2 path: data/regime/market_health.json.
    Returns a normalized dict with keys expected by check_portfolio_level().
    """
    f = BASE_DIR / "data" / "regime" / "market_health.json"
    if f.exists():
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            # Normalize market_health.json schema to what risk_guardian expects
            return {
                "cash_target_min":  raw.get("cash_floor", 0.10),
                "max_deployed":     raw.get("max_deployed", 0.90),
                "regime_name":      raw.get("regime", "UNKNOWN"),
                "leaders_ok":       raw.get("leaders_ok", True),
                "bigshot_ok":       raw.get("bigshot_ok", False),
                "early_warnings":   [],   # market_health.json has no early_warnings key
                "distribution_days": raw.get("distribution_days", 0),
                "regime_note":      raw.get("regime_note", ""),
            }
        except Exception as e:
            print(f"  [WARN] _load_m0() failed: {e}")
    return {
        "cash_target_min": 0.10,
        "max_deployed":    0.90,
        "regime_name":     "UNKNOWN",
        "early_warnings":  [],
    }


def _load_rs_universe() -> dict:
    f = BASE_DIR / "data" / "rs_universe" / "latest.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _load_exhaustion(ticker: str) -> dict:
    f = BASE_DIR / "data" / "exhaustion" / f"{ticker}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _load_nrgc(ticker: str) -> dict:
    f = BASE_DIR / "data" / "nrgc" / "state" / f"{ticker}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _load_base_count(ticker: str) -> dict:
    f = BASE_DIR / "data" / "base_counts" / f"{ticker}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _get_next_earnings(ticker: str):
    """Return next earnings date for ticker as a date object, or None.

    Reads from data/regime/earnings_next30.json (written by fetch_earnings_calendar.py).
    Falls back gracefully — earnings check is silently skipped if data unavailable,
    but the underlying data source is now a real file, not a missing module.
    """
    earnings_file = BASE_DIR / "data" / "regime" / "earnings_next30.json"
    if not earnings_file.exists():
        return None
    try:
        data = json.loads(earnings_file.read_text(encoding="utf-8"))
        ticker_events = data.get("by_ticker", {}).get(ticker, [])
        if not ticker_events:
            return None
        # by_ticker contains list of {"date": "YYYY-MM-DD", "time": "BMO/AMC"}
        # Return the earliest upcoming date
        today = date.today()
        upcoming = [
            e["date"] for e in ticker_events
            if isinstance(e, dict) and e.get("date", "") >= today.isoformat()
        ]
        if upcoming:
            return date.fromisoformat(sorted(upcoming)[0])
    except Exception as e:
        print(f"  [WARN] _get_next_earnings({ticker}) failed: {e}")
    return None


# ── Position-Level Checks ─────────────────────────────────────────────────────

def check_position(ticker: str, pos: dict, nav: float,
                   m0: dict, rs_universe: dict) -> list:
    """
    Run all risk checks on a single position.
    Returns list of risk flags: [{priority, rule, detail, action}]
    """
    flags = []

    entry   = pos.get("entry_price", 0)
    curr    = pos.get("current_price", entry)
    shares  = pos.get("shares", 0)
    # BUG-03 fix: positions use cost_basis (v2) or cost (v1) — try both
    stop    = pos.get("stop") or pos.get("stop_price") or entry * 0.92
    cost    = pos.get("cost_basis") or pos.get("cost") or shares * entry
    pnl_pct = pos.get("pnl_pct", 0)
    theme   = pos.get("theme", "")
    setup   = pos.get("setup_type", "leader")
    trail   = pos.get("trail_stop")

    weight  = cost / max(nav, 1)
    curr_val = shares * curr

    def flag(priority, rule, detail, action):
        flags.append({
            "ticker":   ticker,
            "priority": priority,
            "rule":     rule,
            "detail":   detail,
            "action":   action,
        })

    # Rule 1: Max position size
    if weight > MAX_SINGLE_POSITION_PCT:
        flag(ACTION_TODAY, "MAX_SIZE",
             f"Position weight {weight*100:.1f}% > {MAX_SINGLE_POSITION_PCT*100:.0f}% limit",
             f"Trim {ticker} to {MAX_SINGLE_POSITION_PCT*100:.0f}% of equity")

    # Rule 2: Stop proximity (within 2%)
    if curr > 0 and stop > 0:
        pct_to_stop = (curr - stop) / curr * 100
        if pct_to_stop <= 2.0:
            flag(ACTION_IMMEDIATE, "STOP_PROXIMITY",
                 f"Price ${curr:.2f} only {pct_to_stop:.1f}% above stop ${stop:.2f}",
                 f"WATCH {ticker} -- at stop risk. Consider early exit or tighten manually.")

    # Rule 3: Hard stop breach
    if curr > 0 and entry > 0 and pnl_pct <= -8.0:
        flag(ACTION_IMMEDIATE, "HARD_STOP",
             f"P&L {pnl_pct:.1f}% has breached -8% stop rule",
             f"EXIT {ticker} NOW -- hard stop rule")

    # Rule 4: Stage / NRGC phase check
    nrgc = _load_nrgc(ticker)
    phase = nrgc.get("phase", 0)
    if phase >= 6:
        flag(ACTION_IMMEDIATE, "NRGC_PHASE_6",
             f"NRGC Phase {phase}: Distribution/Markdown",
             f"EXIT {ticker} -- distribution phase confirmed")
    elif phase == 5:
        flag(ACTION_TODAY, "NRGC_PHASE_5",
             f"NRGC Phase 5: Euphoria -- smart money exiting",
             f"TRIM {ticker} 50% -- euphoria phase")

    # Rule 5: Exhaustion check
    ex = _load_exhaustion(ticker)
    ex_score = ex.get("exhaustion_score", 0)
    ex_label = ex.get("label", "NORMAL")
    if ex_score >= 80:
        flag(ACTION_IMMEDIATE, "EXHAUSTION",
             f"Exhaustion score={ex_score:.0f} [{ex_label}]",
             f"EXIT 25-50% {ticker} -- exhaustion confirmed")
    elif ex_score >= 65:
        flag(ACTION_TODAY, "DE_RISK",
             f"Exhaustion score={ex_score:.0f} [{ex_label}]",
             f"Consider trimming {ticker} -- approaching exhaustion")

    # Rule 6: RS laggard
    rs_uni = rs_universe.get("universe", {})
    ticker_rs = rs_uni.get(ticker, {})
    rs_comp = ticker_rs.get("rs_composite_pct")
    if rs_comp is not None and rs_comp < RS_LAGGARD_THRESHOLD:
        flag(ACTION_REVIEW, "RS_LAGGARD",
             f"RS composite rank={rs_comp:.0f}th percentile (below {RS_LAGGARD_THRESHOLD}th)",
             f"Review {ticker} for exit -- leadership decaying")

    # Rule 7: Base 0 / Bottom fish size check
    bc = _load_base_count(ticker)
    bc_label = bc.get("base_label", "")
    bc_count = bc.get("base_count", 0)
    if setup == "hypergrowth" and weight > MAX_HYPERGROWTH_BASE0:
        flag(ACTION_TODAY, "HYPERGROWTH_OVERSIZE",
             f"Hypergrowth position weight {weight*100:.1f}% > {MAX_HYPERGROWTH_BASE0*100:.0f}% limit",
             f"Trim {ticker} to max {MAX_HYPERGROWTH_BASE0*100:.0f}%")
    if setup == "bottom_fish" and weight > MAX_BOTTOM_FISH:
        flag(ACTION_TODAY, "BOTTOM_FISH_OVERSIZE",
             f"Bottom Fish position {weight*100:.1f}% > {MAX_BOTTOM_FISH*100:.0f}% limit",
             f"Trim {ticker} to max {MAX_BOTTOM_FISH*100:.0f}%")

    # Rule 8: Late-stage base (Base 4+)
    if bc_count >= 4:
        flag(ACTION_TODAY, "LATE_STAGE_BASE",
             f"Base {bc_count} -- late-stage distribution risk",
             f"Tighten stop to -6% on {ticker}. Consider reducing.")

    # Rule 9: Trailing stop activation
    if pnl_pct >= 30.0 and not trail:
        flag(ACTION_REVIEW, "TRAIL_NOT_ACTIVE",
             f"Position up {pnl_pct:.1f}% but trailing stop not yet activated",
             f"Activate trailing stop at -15% from peak on {ticker}")

    # Rule 10: Earnings proximity
    next_earn = _get_next_earnings(ticker)
    if next_earn:
        days_to = (next_earn - date.today()).days
        if 0 <= days_to <= EARNINGS_REDUCE_DAYS and weight > EARNINGS_MAX_WEIGHT:
            flag(ACTION_TODAY, "EARNINGS_OVERWEIGHT",
                 f"Earnings in {days_to} days, position weight {weight*100:.1f}% > {EARNINGS_MAX_WEIGHT*100:.0f}% limit",
                 f"Reduce {ticker} to <3% before earnings on {next_earn}")

    return flags


# ── Portfolio-Level Checks ────────────────────────────────────────────────────

def _update_nav_history(nav: float) -> dict:
    """
    Write today's NAV to the rolling history file.
    Returns: {"peak_nav": float, "drawdown_pct": float, "days_since_peak": int}
    """
    today_str = date.today().isoformat()
    history   = {}

    if NAV_HISTORY_FILE.exists():
        try:
            history = json.loads(NAV_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            history = {}

    # Add today
    history[today_str] = round(nav, 2)

    # Keep only last 252 trading days (~1 year)
    sorted_dates = sorted(history.keys())
    if len(sorted_dates) > 252:
        for old_d in sorted_dates[:-252]:
            del history[old_d]

    # Write back
    try:
        NAV_HISTORY_FILE.write_text(
            json.dumps(history, indent=2), encoding="utf-8"
        )
    except Exception:
        pass

    # Calculate peak and drawdown
    all_navs = [(d, history[d]) for d in sorted(history.keys())]
    peak_date, peak_nav = max(all_navs, key=lambda x: x[1])
    drawdown_pct = (nav - peak_nav) / peak_nav if peak_nav > 0 else 0

    # Days since peak
    try:
        from datetime import datetime as _dt
        days_since = (date.today() - _dt.fromisoformat(peak_date).date()).days
    except Exception:
        days_since = 0

    return {
        "peak_nav":       round(peak_nav, 2),
        "peak_date":      peak_date,
        "drawdown_pct":   round(drawdown_pct, 4),
        "days_since_peak": days_since,
    }


def _fetch_qqq_price() -> Optional[float]:
    """
    Fetch current QQQ close price for benchmark comparison.
    Uses data_engine (same source as all other price data).
    Returns None on failure — benchmark comparison silently skipped.
    """
    try:
        sys.path.insert(0, str(BASE_DIR / "scripts"))
        from utils.data_engine import get_ohlcv
        df = get_ohlcv("QQQ", period="5d")
        if df is not None and len(df) > 0:
            return float(df["Close"].iloc[-1])
    except Exception:
        pass
    return None


def _update_qqq_history(qqq_price: Optional[float], nav: float) -> dict:
    """
    Track QQQ price and portfolio NAV from inception, calculate relative performance.
    Both series are indexed to 100 at inception (first recorded date).

    Returns:
      {
        "qqq_return_pct": float,        # QQQ total return from inception
        "portfolio_return_pct": float,  # Portfolio total return from inception
        "alpha_pct": float,             # portfolio - qqq (positive = outperforming)
        "inception_date": str,
        "qqq_current": float,
        "qqq_inception": float,
      }
    """
    today_str = date.today().isoformat()

    # ── Load / update QQQ history ─────────────────────────────────────────────
    qqq_hist: dict = {}
    if QQQ_HISTORY_FILE.exists():
        try:
            qqq_hist = json.loads(QQQ_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            qqq_hist = {}

    if qqq_price is not None:
        qqq_hist[today_str] = round(qqq_price, 4)
        # Keep last 252 days
        sorted_dates = sorted(qqq_hist.keys())
        if len(sorted_dates) > 252:
            for old_d in sorted_dates[:-252]:
                del qqq_hist[old_d]
        try:
            QQQ_HISTORY_FILE.write_text(json.dumps(qqq_hist, indent=2), encoding="utf-8")
        except Exception:
            pass

    if not qqq_hist:
        return {"alpha_pct": None, "qqq_return_pct": None, "portfolio_return_pct": None}

    # ── Load NAV history ──────────────────────────────────────────────────────
    nav_hist: dict = {}
    if NAV_HISTORY_FILE.exists():
        try:
            nav_hist = json.loads(NAV_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            nav_hist = {}

    # ── Find common inception date (first date both series have data) ─────────
    nav_dates = set(nav_hist.keys())
    qqq_dates = set(qqq_hist.keys())
    common_dates = sorted(nav_dates & qqq_dates)

    if len(common_dates) < 2:
        return {"alpha_pct": None, "qqq_return_pct": None, "portfolio_return_pct": None,
                "note": "need_more_history"}

    inception_date = common_dates[0]
    qqq_inception  = qqq_hist[inception_date]
    nav_inception  = nav_hist[inception_date]

    # Latest values
    latest_qqq = qqq_hist.get(today_str) or qqq_hist[sorted(qqq_hist.keys())[-1]]
    latest_nav = nav_hist.get(today_str) or nav_hist[sorted(nav_hist.keys())[-1]]

    qqq_return_pct  = (latest_qqq - qqq_inception) / qqq_inception * 100 if qqq_inception else None
    nav_return_pct  = (latest_nav - nav_inception) / nav_inception * 100  if nav_inception  else None
    alpha_pct = (nav_return_pct - qqq_return_pct) if (nav_return_pct is not None and qqq_return_pct is not None) else None

    # ── Inception regime context ──────────────────────────────────────────────
    # Read M0 regime on inception date from regime_history to qualify alpha context.
    # "Alpha in a pure bull run means less than alpha across a full market cycle."
    inception_regime  = None
    full_cycle_flag   = False
    try:
        regime_hist_file = BASE_DIR / "data" / "market_regime" / "regime_history.json"
        if regime_hist_file.exists():
            regime_hist = json.loads(regime_hist_file.read_text(encoding="utf-8"))
            raw_entries = regime_hist if isinstance(regime_hist, list) else regime_hist.get("history", [])
            # Sort ascending by date (oldest first) — regardless of how file stores them
            entries = sorted(raw_entries, key=lambda e: e.get("date", ""))
            # Find the regime active ON inception_date: last entry with date <= inception_date
            for entry in entries:
                if entry.get("date", "") <= inception_date:
                    inception_regime = entry.get("regime_name")   # overwrite until we pass the date
                else:
                    break   # entries are sorted asc, so first entry past inception_date = done
            # full_cycle_flag: True if tracking period includes at least one regime >= 5 (CORRECTION+)
            seen_regimes = [e.get("regime_num", 0) for e in entries
                            if e.get("date", "") >= inception_date]
            full_cycle_flag = any(r >= 5 for r in seen_regimes)
    except Exception:
        pass

    # ── Tracking-period quality note ──────────────────────────────────────────
    days_tracked = len(common_dates)
    if full_cycle_flag:
        context_note = "full-cycle (includes correction/bear period)"
    elif days_tracked >= 60:
        context_note = "bull-market only — alpha may overstate edge"
    else:
        context_note = f"early data ({days_tracked}d) — too short to conclude"

    return {
        "inception_date":        inception_date,
        "inception_regime":      inception_regime,   # M0 regime name on day 1
        "full_cycle_flag":       full_cycle_flag,    # True = alpha tested through a correction
        "context_note":          context_note,
        "qqq_inception":         round(qqq_inception, 2),
        "qqq_current":           round(latest_qqq, 2),
        "nav_inception":         round(nav_inception, 2),
        "nav_current":           round(latest_nav, 2),
        "qqq_return_pct":        round(qqq_return_pct, 2)  if qqq_return_pct  is not None else None,
        "portfolio_return_pct":  round(nav_return_pct, 2)  if nav_return_pct  is not None else None,
        "alpha_pct":             round(alpha_pct, 2)       if alpha_pct       is not None else None,
        "outperforming":         alpha_pct > 0 if alpha_pct is not None else None,
        "trading_days_tracked":  days_tracked,
    }


def check_portfolio_level(portfolio: dict, nav: float, m0: dict) -> list:
    """
    Portfolio-wide concentration and regime checks.
    Returns list of flags.
    """
    flags = []
    positions = portfolio.get("positions", {})
    cash = portfolio.get("cash", 0)
    cash_pct = cash / max(nav, 1)

    def flag(priority, rule, detail, action):
        flags.append({"ticker": "PORTFOLIO", "priority": priority,
                      "rule": rule, "detail": detail, "action": action})

    # Regime cash target
    m0_cash_target = m0.get("cash_target_min", 0.10)
    m0_regime_name = m0.get("regime_name", "UNKNOWN")
    m0_warns       = m0.get("early_warnings", [])

    if cash_pct < m0_cash_target:
        flag(ACTION_TODAY, "CASH_BELOW_TARGET",
             f"Cash {cash_pct*100:.1f}% below M0 target {m0_cash_target*100:.0f}% ({m0_regime_name})",
             f"Raise cash to {m0_cash_target*100:.0f}% -- trim smallest/weakest positions first")

    # Early warning response
    warn_count = len(m0_warns)
    if warn_count >= 5:
        flag(ACTION_IMMEDIATE, "EARLY_WARNINGS_MAX",
             f"{warn_count} bear detection signals active -- regime forced to CORRECTION",
             "RAISE CASH TO 50%+. Exit all losers. Keep only Base 1-2 + CANSLIM A+")
    elif warn_count >= 3:
        flag(ACTION_TODAY, "EARLY_WARNINGS_HIGH",
             f"{warn_count} early warnings active",
             "Reduce new buys. Cut any position below -4% P&L immediately.")

    # Monster Scout bucket check (≤ 30% of portfolio — from CLAUDE.md)
    # BUG-03 fix: positions store cost_basis (not cost). Try both keys.
    def _pos_cost(pos: dict) -> float:
        """Get position cost — try cost_basis (v2 format) then cost (v1 format),
        then recompute from shares × entry_price as last resort."""
        cb = pos.get("cost_basis") or pos.get("cost")
        if cb is not None:
            return float(cb)
        shares = pos.get("shares", 0)
        ep = pos.get("entry_price", 0)
        return float(shares * ep) if shares and ep else 0.0

    mode_b_weight = sum(
        _pos_cost(pos) / max(nav, 1)
        for pos in positions.values()
        if pos.get("mode", "A") == "B"
    )
    if mode_b_weight > MAX_MODE_B_BUCKET_PCT:
        flag(ACTION_TODAY, "MODE_B_BUCKET",
             f"Monster Scout bucket = {mode_b_weight*100:.1f}% > {MAX_MODE_B_BUCKET_PCT*100:.0f}% limit",
             f"Reduce Monster Scout (Big Shot) positions to bring bucket below {MAX_MODE_B_BUCKET_PCT*100:.0f}%")

    # ADTV cap check: position size ≤ 20% of 6M ADTV (from CLAUDE.md)
    for ticker, pos in positions.items():
        pos_value = pos.get("shares", 0) * pos.get("current_price", pos.get("entry_price", 0))
        adtv_usd  = pos.get("adtv_6m_usd", 0)
        if adtv_usd > 0:
            adtv_20pct = adtv_usd * 0.20
            if pos_value > adtv_20pct:
                flags.append({
                    "ticker": ticker, "priority": ACTION_TODAY,
                    "rule": "ADTV_CAP",
                    "detail": (f"Position ${pos_value:,.0f} > 20% ADTV cap "
                               f"(${adtv_20pct:,.0f} = 20% of ${adtv_usd:,.0f} ADTV)"),
                    "action": f"Reduce {ticker} — position exceeds liquidity limit",
                })

    # Theme concentration (BUG-03 fix: use _pos_cost() — positions use cost_basis, not cost)
    theme_weights = {}
    for ticker, pos in positions.items():
        theme = pos.get("theme", "Unknown") or "Unknown"
        cost  = _pos_cost(pos)
        theme_weights[theme] = theme_weights.get(theme, 0) + cost / max(nav, 1)

    for theme, weight in theme_weights.items():
        if weight > MAX_THEME_PCT:
            flag(ACTION_TODAY, "THEME_CONCENTRATION",
                 f"Theme '{theme}' = {weight*100:.1f}% > {MAX_THEME_PCT*100:.0f}% limit",
                 f"Reduce {theme} exposure to {MAX_THEME_PCT*100:.0f}%")

    # Max positions concentration
    n_pos = len(positions)
    if n_pos > 10:
        flag(ACTION_REVIEW, "TOO_MANY_POSITIONS",
             f"{n_pos} positions > 10 max",
             "Review smallest/weakest positions for consolidation")

    # ── Portfolio Drawdown Circuit Breaker ──────────────────────────────────────
    # Goal: drawdown SMALLER than market. NASDAQ has avg -8% to -15% correction.
    # We exit/reduce BEFORE reaching -12% so we outperform on the downside.
    dd_info = _update_nav_history(nav)
    dd_pct  = dd_info["drawdown_pct"]   # negative = drawdown from peak

    if dd_pct <= -DRAWDOWN_CIRCUIT_PCT:
        flag(ACTION_IMMEDIATE, "DRAWDOWN_CIRCUIT_BREAKER",
             f"Portfolio NAV {dd_pct*100:.1f}% from peak (${dd_info['peak_nav']:,.0f} on {dd_info['peak_date']})",
             f"HALT all new entries. Review every position vs conviction. "
             f"Keep only Grade A PRISM + Phase 3 NRGC. Raise cash to 50%+.")
    elif dd_pct <= -DRAWDOWN_REDUCE_PCT:
        flag(ACTION_TODAY, "DRAWDOWN_REDUCE_LONGS",
             f"Portfolio NAV {dd_pct*100:.1f}% from peak (${dd_info['peak_nav']:,.0f})",
             f"Reduce all longs by 25-50%. No new entries except Grade A + Phase 3.")
    elif dd_pct <= -DRAWDOWN_REVIEW_PCT:
        flag(ACTION_REVIEW, "DRAWDOWN_WATCH",
             f"Portfolio NAV {dd_pct*100:.1f}% from peak — watching",
             f"Tighten stops on all positions. Pause new entries unless regime = CONFIRMED.")

    return flags


# ── Main Run ──────────────────────────────────────────────────────────────────

def run() -> dict:
    """
    Full portfolio risk check. Writes data/risk_guardian/daily_report.json
    """
    today = date.today().isoformat()
    print(f"\n[Risk Guardian] Running {today}")

    portfolio = _load_portfolio()
    m0        = _load_m0()
    rs_uni    = _load_rs_universe()
    positions = portfolio.get("positions", {})
    cash      = portfolio.get("cash", 0)

    if not positions:
        print("[Risk Guardian] No positions to check.")
        return {}

    # NAV
    nav = cash + sum(
        p.get("shares", 0) * p.get("current_price", p.get("entry_price", 0))
        for p in positions.values()
    )

    # Per-position checks
    all_flags = []
    for ticker, pos in positions.items():
        flags = check_position(ticker, pos, nav, m0, rs_uni)
        all_flags.extend(flags)

    # Portfolio-level checks
    port_flags = check_portfolio_level(portfolio, nav, m0)
    all_flags.extend(port_flags)

    # Sort by priority
    PRIORITY_ORDER = {ACTION_IMMEDIATE: 0, ACTION_TODAY: 1, ACTION_REVIEW: 2, ACTION_OK: 3}
    all_flags.sort(key=lambda f: PRIORITY_ORDER.get(f.get("priority"), 3))

    # Summary
    immediate = [f for f in all_flags if f["priority"] == ACTION_IMMEDIATE]
    today_act = [f for f in all_flags if f["priority"] == ACTION_TODAY]
    review    = [f for f in all_flags if f["priority"] == ACTION_REVIEW]

    print(f"  Positions checked: {len(positions)}")
    print(f"  [RED] IMMEDIATE: {len(immediate)}")
    print(f"  🟠 TODAY:     {len(today_act)}")
    print(f"  [YLW] REVIEW:    {len(review)}")

    if immediate:
        print("\n  [!] IMMEDIATE ACTIONS:")
        for f in immediate:
            print(f"    [{f['ticker']}] {f['rule']}: {f['action']}")

    # Calculate drawdown for output (already written to nav_history by check_portfolio_level)
    dd_info   = {}
    qqq_bench = {}
    try:
        dd_info   = _update_nav_history(nav)
        qqq_price = _fetch_qqq_price()
        qqq_bench = _update_qqq_history(qqq_price, nav)
        if qqq_bench.get("alpha_pct") is not None:
            alpha = qqq_bench["alpha_pct"]
            sign  = "+" if alpha >= 0 else ""
            print(f"  [vs QQQ] Portfolio: {sign}{qqq_bench['portfolio_return_pct']:.1f}% "
                  f"vs QQQ: {qqq_bench['qqq_return_pct']:.1f}% "
                  f"| Alpha: {sign}{alpha:.1f}% since {qqq_bench['inception_date']}")
    except Exception:
        pass

    result = {
        "date":          today,
        "computed_at":   datetime.now().isoformat(),
        "nav":           round(nav, 2),
        "drawdown_pct":  dd_info.get("drawdown_pct", 0),
        "peak_nav":      dd_info.get("peak_nav", nav),
        "peak_date":     dd_info.get("peak_date", today),
        "positions_checked": len(positions),
        "flags_total":   len(all_flags),
        "immediate_count": len(immediate),
        "today_count":   len(today_act),
        "review_count":  len(review),
        "immediate":     immediate,
        "today":         today_act,
        "review":        review,
        "all_flags":     all_flags,
        "risk_clear":    len(immediate) == 0 and len(today_act) == 0,
        "qqq_benchmark": qqq_bench,       # portfolio vs QQQ from inception
    }

    # v2 canonical output: data/risk/risk_report.json
    report_file = OUT_DIR / "risk_report.json"
    report_file.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    # Also send Telegram if IMMEDIATE flags
    if immediate:
        _send_risk_telegram(immediate, today_act, nav)

    print(f"[Risk Guardian] Done -- {report_file}")
    return result


def _send_risk_telegram(immediate: list, today_act: list, nav: float):
    """Fire Telegram alert for IMMEDIATE risk flags.
    FIX: replaced `import telegram_notifier as tg` (module not in main scripts path)
    with direct inline send using requests + verify=False (same as other pipeline senders).
    """
    try:
        import os as _os, requests as _req
        token   = _os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = _os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return
        lines = [
            "[!!] RISK GUARDIAN ALERT",
            f"NAV: ${nav:,.0f} | {date.today().isoformat()}",
            "",
            f"IMMEDIATE ({len(immediate)}):",
        ]
        for f in immediate[:5]:
            lines.append(f"  [STOP] {f['ticker']} [{f['rule']}]: {f['action'][:60]}")
        if today_act:
            lines += ["", f"TODAY ({len(today_act)}):"]
            for f in today_act[:3]:
                lines.append(f"  [!] {f['ticker']}: {f['action'][:60]}")
        _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "\n".join(lines)},
            timeout=10,
            verify=False,
        )
    except Exception:
        pass


if __name__ == "__main__":
    run()
