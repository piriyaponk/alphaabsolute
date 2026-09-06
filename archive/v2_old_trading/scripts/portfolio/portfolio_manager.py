"""
AlphaAbsolute v2 -- A09 Portfolio Manager
==========================================
Monitors all current positions daily.
Issues hold / reduce / exit action signals.
Enforces cash floor vs regime.

Daily checks per position:
  1. Stop hit? -> IMMEDIATE exit
  2. Earnings within 5 days? -> FLAG (no adds, consider reduce)
  3. RS dropping from top quartile + break 50DMA? -> REDUCE
  4. +25% gain reached? -> Take 25% profit signal
  5. +50% gain reached? -> Take 50% off signal
  6. Cash floor breach? -> Reduce lowest-conviction positions

Paper portfolio:
  - Simulates fills using closing prices (no broker connection)
  - Tracks P&L vs QQQ benchmark
  - State stored in: data/portfolio/paper_portfolio_state.json

Real signals:
  - Output to: data/portfolio/action_signals.json
  - CIO executes manually

Output:
  data/portfolio/portfolio_state.json (updated)
  data/portfolio/paper_portfolio_state.json (updated)
  data/portfolio/action_signals.json (today's signals)

Run: 8:30 AM (with setup scanner) + 4:30 PM EOD update
Cost: $0
"""

from __future__ import annotations
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

PORTFOLIO_DIR  = ROOT / "data" / "portfolio"
PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)

REAL_PORT_FILE  = PORTFOLIO_DIR / "portfolio_state.json"
PAPER_PORT_FILE = PORTFOLIO_DIR / "paper_portfolio_state.json"
SIGNALS_FILE    = PORTFOLIO_DIR / "action_signals.json"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _get_price(ticker: str) -> Optional[float]:
    """Fetch latest closing price from SQLite ohlcv.db — zero network calls."""
    try:
        import sqlite3
        db = ROOT / "data" / "ohlcv.db"
        if db.exists():
            with sqlite3.connect(str(db)) as conn:
                row = conn.execute(
                    "SELECT close FROM ohlcv WHERE ticker=? AND close IS NOT NULL ORDER BY date DESC LIMIT 1",
                    (ticker,)
                ).fetchone()
            if row:
                return float(row[0])
    except Exception:
        pass
    return None


def _get_rs_pct(ticker: str) -> Optional[float]:
    """Get current RS 3M percentile from latest RS file."""
    try:
        latest_file = ROOT / "data" / "rs_universe" / "latest.json"
        data = json.loads(latest_file.read_text(encoding="utf-8"))
        # rs_ranker.py writes universe as a dict keyed by ticker under "universe" key
        # NOT as a list under "results" (that key does not exist)
        universe = data.get("universe", {})
        item = universe.get(ticker) or universe.get(ticker.upper())
        if item:
            return item.get("rs_pct_3m") or item.get("rs_3m_pct")
    except Exception as e:
        print(f"  [WARN] _get_rs_pct({ticker}) failed: {e}")
    return None


def _get_sma50(ticker: str) -> Optional[float]:
    """Get 50-day SMA from SQLite ohlcv.db — zero network calls."""
    try:
        import sqlite3
        db = ROOT / "data" / "ohlcv.db"
        if db.exists():
            with sqlite3.connect(str(db)) as conn:
                rows = conn.execute(
                    "SELECT close FROM ohlcv WHERE ticker=? AND close IS NOT NULL ORDER BY date DESC LIMIT 50",
                    (ticker,)
                ).fetchall()
            if len(rows) >= 50:
                return float(sum(r[0] for r in rows) / len(rows))
    except Exception:
        pass
    return None


def _get_rs_line_direction(ticker: str) -> Optional[int]:
    """
    Get rs_line_direction (1=rising, 0=flat, -1=falling) from ticker_meta in ohlcv.db.
    Computed by pipeline_metrics.py step_b5_rs_line (BOA-009-A1).
    Returns None if db not available or column missing.
    """
    try:
        import sqlite3
        db_path = ROOT / "data" / "ohlcv.db"
        if not db_path.exists():
            return None
        with sqlite3.connect(str(db_path)) as conn:
            # Column added by BOA-009-A1 — may not exist in older DBs
            try:
                row = conn.execute(
                    "SELECT rs_line_direction FROM ticker_meta WHERE ticker = ?",
                    (ticker.upper(),)
                ).fetchone()
                if row and row[0] is not None:
                    return int(row[0])
            except sqlite3.OperationalError:
                # Column doesn't exist yet (pipeline hasn't run with new schema)
                pass
    except Exception:
        pass
    return None


def _get_weekly_ohlcv(ticker: str, weeks: int = 15):
    """
    Compute weekly OHLCV from daily bars in SQLite. Zero network calls.
    Returns DataFrame with Open/High/Low/Close/Volume indexed by week-end date.
    """
    try:
        import sqlite3, pandas as pd
        db = ROOT / "data" / "ohlcv.db"
        if not db.exists():
            return None
        days_needed = weeks * 7 + 14
        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute(
                """SELECT date, open, high, low, close, volume FROM ohlcv
                   WHERE ticker=? AND close IS NOT NULL ORDER BY date DESC LIMIT ?""",
                (ticker, days_needed)
            ).fetchall()
        if not rows or len(rows) < 15:
            return None
        df = pd.DataFrame(rows, columns=["date","Open","High","Low","Close","Volume"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        weekly = df.resample("W-FRI").agg({
            "Open": "first", "High": "max", "Low": "min",
            "Close": "last", "Volume": "sum",
        }).dropna(subset=["Close"])
        return weekly if len(weekly) >= 3 else None
    except Exception:
        return None


def _check_earnings_soon(ticker: str, days_ahead: int = 5) -> bool:
    """
    Check if earnings are within days_ahead trading days.
    Reads from fetch_earnings_calendar.py output: data/regime/earnings_next30.json
    Format: {"within_5_trading_days": ["AAPL", "NVDA", ...], "events": [...]}
    Falls back to False (conservative = allow entry) if calendar not available.
    """
    cal_path = ROOT / "data" / "regime" / "earnings_next30.json"
    if not cal_path.exists():
        return False
    try:
        data = json.loads(cal_path.read_text(encoding="utf-8"))
        # Primary: check the pre-computed within_5_trading_days list
        within_5 = data.get("within_5_trading_days", [])
        if ticker in within_5 or ticker.upper() in within_5:
            return True
        # Fallback: scan events list for this ticker within days_ahead calendar days
        for ev in data.get("events", []):
            if ev.get("symbol", "").upper() != ticker.upper():
                continue
            ev_date_str = ev.get("date") or ev.get("reportDate") or ev.get("next_earnings_date")
            if not ev_date_str:
                continue
            try:
                ev_date = date.fromisoformat(ev_date_str[:10])
                delta = (ev_date - date.today()).days
                if 0 <= delta <= days_ahead:
                    return True
            except ValueError:
                continue
        return False
    except Exception as e:
        print(f"  [WARN] _check_earnings_soon({ticker}) failed: {e}")
        return False


# ── Position action checks ────────────────────────────────────────────────────

def _trading_days_since(entry_date_str: str) -> int:
    """Approximate number of trading days (Mon-Fri) between entry_date and today."""
    try:
        entry = date.fromisoformat(entry_date_str[:10])
        today = date.today()
        if today <= entry:
            return 0
        # Count weekdays only (approximation — doesn't exclude holidays)
        count = 0
        d = entry + timedelta(days=1)
        while d <= today:
            if d.weekday() < 5:  # 0=Mon..4=Fri
                count += 1
            d += timedelta(days=1)
        return count
    except Exception:
        return 999  # unknown → assume past window


def check_position(ticker: str, pos: dict, regime: str,
                   max_deployed: float, current_deployed: float) -> list[dict]:
    """
    Run all checks on a single position. Returns list of action signals.

    Priority order:
      1. Stop hit                → IMMEDIATE exit
      2. Base failure (0-10d)    → IMMEDIATE exit  [Minervini Lesson 9]
      3. Earnings within 5d      → FLAG / no new adds
      4. RS + 50DMA dual fail    → REDUCE 50% (Mode A)
      5. Profit-taking ladder    → regime-calibrated thresholds
      6. Cash floor              → handled at portfolio level

    Distribution regime profit-taking (tighter):
      +15% → take 25% off  (vs +25% in Markup)
      +30% → take 50% off  (vs +50% in Markup)
    """
    signals = []
    today   = date.today().isoformat()

    entry_price = pos.get("entry_price", 0)
    entry_date  = pos.get("entry_date", today)
    mode        = pos.get("mode", "A")
    size_pct    = pos.get("size_pct", 10.0)
    # Regime-calibrated stops (per A12 backtest 2026-05-23)
    if regime == "Markup":
        stop_pct = -0.12 if mode == "A" else -0.10
    else:
        stop_pct = -0.08 if mode == "A" else -0.10

    current_price = _get_price(ticker)
    if not current_price or not entry_price:
        return signals

    pnl_pct = (current_price - entry_price) / entry_price * 100

    # Update position with current price
    pos["current_price"] = round(current_price, 2)
    pos["pnl_pct"]       = round(pnl_pct, 2)
    pos["last_updated"]  = today

    # ── Check 1: Hard stop hit ─────────────────────────────────────────────
    stop_level = entry_price * (1 + stop_pct)
    if current_price <= stop_level:
        signals.append({
            "ticker":   ticker,
            "action":   "SELL_STOP",
            "priority": "IMMEDIATE",
            "reason":   f"Stop hit: {pnl_pct:.1f}% (stop={stop_pct*100:.0f}% from entry, regime={regime})",
            "size":     size_pct,
            "price":    round(current_price, 2),
            "date":     today,
        })
        return signals  # no need to check further

    # ── Check 2: Earnings within 5 days ───────────────────────────────────
    # DA Analyst Issue 5 fix: earnings check BEFORE BASE_FAILURE.
    # Pre-earnings pullback below pivot is expected (window dressing / hedging)
    # and should NOT trigger IMMEDIATE exit — earnings event must be respected first.
    earnings_soon = _check_earnings_soon(ticker)
    if earnings_soon:
        signals.append({
            "ticker":   ticker,
            "action":   "FLAG_EARNINGS",
            "priority": "TODAY",
            "reason":   "Earnings within 5 trading days — no new adds, consider reduce",
            "size":     0,
            "price":    round(current_price, 2),
            "date":     today,
        })

    # ── Check 3: Breakout confirmation (Minervini Lesson 9) ───────────────
    # If within 10 trading days of entry AND price closes back below the
    # breakout pivot → BASE_FAILURE → exit immediately.
    # Skipped if earnings within 5 days (pre-earnings pullback is expected behaviour).
    # The pivot is stored as pos["pivot"] when the position is entered.
    # Fallback: use entry_price as pivot if not stored.
    days_in   = _trading_days_since(entry_date)
    pivot     = pos.get("pivot") or entry_price
    if (days_in <= 10
            and current_price < pivot
            and not pos.get("base_failure_checked")
            and not earnings_soon):    # earnings gate: don't call BASE_FAILURE pre-earnings
        signals.append({
            "ticker":   ticker,
            "action":   "BASE_FAILURE",
            "priority": "IMMEDIATE",
            "reason":   (
                f"Price {current_price:.2f} closed below breakout pivot {pivot:.2f} "
                f"on day {days_in} (within 10-day confirmation window). "
                f"Base failure — exit immediately. [Minervini L9]"
            ),
            "size":     size_pct,
            "price":    round(current_price, 2),
            "date":     today,
        })
        pos["base_failure_checked"] = True
        return signals  # IMMEDIATE — no further checks needed

    # If still in confirmation window but holding — track status
    if days_in <= 10:
        pos["breakout_day"] = days_in
        pos["holding_above_pivot"] = current_price >= pivot

    # ── Check 4: RS deteriorating + 50DMA break (Mode A only) ─────────────
    if mode == "A":
        rs_pct = _get_rs_pct(ticker)
        sma50  = _get_sma50(ticker)
        rs_dropping = rs_pct is not None and rs_pct < 50
        below_50dma = sma50 is not None and current_price < sma50

        if rs_dropping and below_50dma:
            signals.append({
                "ticker":   ticker,
                "action":   "REDUCE",
                "priority": "TODAY",
                "reason":   f"RS={rs_pct:.0f}th pct + price below 50DMA ({sma50:.2f}) — dual deterioration",
                "size":     round(size_pct * 0.5, 1),  # reduce 50%
                "price":    round(current_price, 2),
                "date":     today,
            })

    # ── Check 4b: 8-Week Hold Rule (BOA-005 DEC-011, 2026-05-24 — APPROVED 4/4) ──
    # If stock gains ≥ 20% in first 21 trading days → hold min 56 trading days.
    # During 8-week hold: suppress ALL profit ladder signals.
    # Mode A only (Mode B: higher volatility, maintain normal exit logic).
    # HYPOTHESIS until N≥10 qualifying events.
    is_eight_week_hold = False
    if mode == "A":
        # Record pnl at day 21 (first time we cross day 21)
        if days_in == 21 and pos.get("day_21_gain_pct") is None:
            pos["day_21_gain_pct"] = round(pnl_pct, 2)
            if pnl_pct >= 20.0:
                pos["eight_week_hold_active"] = True
                pos["eight_week_hold_start"] = entry_date
                print(f"  [8WK HOLD] {ticker}: +{pnl_pct:.1f}% in 21 days → 8-week hold activated (suppress profit ladder until day 56)")

        # Check if 8-week hold is still active (suppress until day 56)
        if pos.get("eight_week_hold_active"):
            if days_in < 56:
                is_eight_week_hold = True
            else:
                # 8-week window passed — deactivate and resume normal ladder
                pos["eight_week_hold_active"] = False
                print(f"  [8WK HOLD] {ticker}: 8-week hold complete at day {days_in} — resuming profit ladder")

    # ── Check 4c: Behavior-Based Exits (BOA-008-A1, DEC-018 — Markup only) ──
    # Markup regime: exits driven by price ACTION, not fixed % thresholds.
    # Distribution/Sideways: keep mechanical profit ladder (less trend follow).
    # 8-week hold SUPPRESSES all four signals (let the monster run).
    # HYPOTHESIS status — log all fires, review after N=10 qualifying events.
    if regime == "Markup" and not is_eight_week_hold and mode == "A":

        # (a) RS Line Break — rs_line falling while in meaningful profit
        # Source: rs_line_direction from ticker_meta (BOA-009-A1)
        # Signal: TODAY (reduce 25% — protect gain, keep core position running)
        rs_dir = _get_rs_line_direction(ticker)
        if rs_dir is not None and rs_dir == -1 and pnl_pct > 15.0:
            signals.append({
                "ticker":   ticker,
                "action":   "RS_LINE_BREAK",
                "priority": "TODAY",
                "reason":   (
                    f"RS line falling (20-day slope negative) while +{pnl_pct:.1f}% — "
                    f"relative strength deteriorating vs SPY. Take 25% off, hold rest."
                ),
                "size":     round(size_pct * 0.25, 1),
                "price":    round(current_price, 2),
                "date":     today,
                "behavior_exit": True,
            })

        # (b) 10-Week MA Breach on heavy volume
        # Signal: IMMEDIATE — large institutions are exiting (volume confirms)
        # Sell 75% of position; trail remainder with 10W MA
        weekly = _get_weekly_ohlcv(ticker, weeks=15)
        if weekly is not None and len(weekly) >= 12:
            # 10W SMA: average of 10 completed weeks BEFORE the most recent
            ma10w             = float(weekly["Close"].iloc[-11:-1].mean())
            last_weekly_close = float(weekly["Close"].iloc[-1])
            avg_weekly_vol    = float(weekly["Volume"].iloc[-11:-1].mean())
            last_weekly_vol   = float(weekly["Volume"].iloc[-1])

            if (last_weekly_close < ma10w
                    and avg_weekly_vol > 0
                    and last_weekly_vol > avg_weekly_vol * 1.5
                    and pnl_pct > 0):      # only protective exit — stop handles losses
                signals.append({
                    "ticker":   ticker,
                    "action":   "TEN_WEEK_MA_BREAK",
                    "priority": "IMMEDIATE",
                    "reason":   (
                        f"Weekly close {last_weekly_close:.2f} < 10W MA {ma10w:.2f} "
                        f"on {last_weekly_vol/avg_weekly_vol:.1f}x avg volume — "
                        f"institutional exit signal. Sell 75%."
                    ),
                    "size":     round(size_pct * 0.75, 1),
                    "price":    round(current_price, 2),
                    "date":     today,
                    "behavior_exit": True,
                })

            # (c) Climax Top: +20% in single week, volume >2x avg, total gain >75%
            # Signal: TODAY — exhaustion move, lock most gains before reversal
            elif len(weekly) >= 3:
                prev_weekly_close = float(weekly["Close"].iloc[-2])
                weekly_gain_pct   = (last_weekly_close - prev_weekly_close) / prev_weekly_close * 100

                if (weekly_gain_pct >= 20.0
                        and avg_weekly_vol > 0
                        and last_weekly_vol > avg_weekly_vol * 2.0
                        and pnl_pct >= 75.0):
                    signals.append({
                        "ticker":   ticker,
                        "action":   "CLIMAX_TOP",
                        "priority": "TODAY",
                        "reason":   (
                            f"Climax top: +{weekly_gain_pct:.1f}% last week on "
                            f"{last_weekly_vol/avg_weekly_vol:.1f}x avg volume, "
                            f"total gain={pnl_pct:.1f}% from entry. Exit 75% — exhaustion move."
                        ),
                        "size":     round(size_pct * 0.75, 1),
                        "price":    round(current_price, 2),
                        "date":     today,
                        "behavior_exit": True,
                    })

        # (d) TD Weekly Countdown 13 — stateful weekly TD tracking
        # TODO: implement weekly TD Sequential counter.
        # Requires: tracking weekly TD setup count + countdown state per position
        # across sessions. Complex stateful logic deferred pending dedicated module.
        # When ready: if td_weekly_countdown >= 13 → exit 75%, priority TODAY.

    # ── Check 5: Profit-taking ladder (regime-calibrated) ─────────────────
    # 8-week hold overrides: suppress ladder if monster fingerprint active
    # Distribution/Sideways: tighter — protect gains faster
    # Markup: standard Minervini ladder
    is_distribution = regime in ("Distribution", "Sideways")

    tp50_threshold = 30.0 if is_distribution else 50.0   # take 50% off at...
    tp25_threshold = 15.0 if is_distribution else 25.0   # take 25% off at...

    if is_eight_week_hold:
        # 8-week hold active: suppress profit ladder entirely. Only stop/base-failure applies.
        # Record the hold status in position for audit trail
        pos["eight_week_hold_day"] = days_in
        # No profit ladder signals emitted
    elif pnl_pct >= tp50_threshold:
        if not pos.get("took_50pct_profit"):
            signals.append({
                "ticker":   ticker,
                "action":   "TAKE_PROFIT_50PCT",
                "priority": "TODAY",
                "reason":   (
                    f"+{pnl_pct:.1f}% gain — take 50% off at {tp50_threshold:.0f}% threshold "
                    f"({'Distribution/Sideways regime' if is_distribution else 'Markup regime'}), "
                    f"trail remaining with 10W MA"
                ),
                "size":     round(size_pct * 0.5, 1),
                "price":    round(current_price, 2),
                "date":     today,
            })
            pos["took_50pct_profit"] = True

    elif pnl_pct >= tp25_threshold:
        if not pos.get("took_25pct_profit"):
            signals.append({
                "ticker":   ticker,
                "action":   "TAKE_PROFIT_25PCT",
                "priority": "TODAY",
                "reason":   (
                    f"+{pnl_pct:.1f}% gain — take 25% off at {tp25_threshold:.0f}% threshold "
                    f"({'Distribution/Sideways regime' if is_distribution else 'Markup regime'}), "
                    f"trail stop to breakeven"
                ),
                "size":     round(size_pct * 0.25, 1),
                "price":    round(current_price, 2),
                "date":     today,
            })
            pos["took_25pct_profit"] = True

    elif not is_eight_week_hold:
        # DA Quant Issue 3 fix: regime-calibrated breakeven trigger
        # Markup: stop=-12%, breakeven at +18% (1.5×12) — not at +15% (sub-3:1 realized RR)
        # Distribution/Sideways: stop=-8%, breakeven at +12% (1.5×8)
        # This prevents the breakeven stop from firing at a gain below the implied 3:1 threshold
        # 8-week hold active: skip breakeven trail — let it run (BOA-005 DEC-011)
        be_trigger = 12.0 if is_distribution else 18.0
        if pnl_pct >= be_trigger and not pos.get("breakeven_stop_set"):
            signals.append({
                "ticker":   ticker,
                "action":   "TRAIL_STOP_BREAKEVEN",
                "priority": "REVIEW",
                "reason":   (
                    f"+{pnl_pct:.1f}% gain — trail stop to breakeven ({entry_price:.2f}). "
                    f"Trigger: {be_trigger:.0f}% (1.5× |stop| for {regime} regime)"
                ),
                "size":     0,
                "price":    round(current_price, 2),
                "date":     today,
            })
            pos["breakeven_stop_set"] = True

    # ── Check 6: Cash floor breach ─────────────────────────────────────────
    # (handled at portfolio level below, flagged per position if needed)

    return signals


# ── Portfolio-level cash enforcement ─────────────────────────────────────────

def enforce_cash_floor(portfolio: dict, regime_health: dict) -> list[dict]:
    """
    If deployed > max_deployed, identify lowest-conviction positions to reduce.
    Returns list of additional REDUCE signals.
    """
    signals = []
    max_deployed   = regime_health.get("max_deployed", 1.0)
    current_deployed = portfolio.get("deployed_pct", 0)

    if current_deployed <= max_deployed:
        return signals

    excess = current_deployed - max_deployed
    positions = portfolio.get("positions", {})

    # Sort positions by conviction (lowest first):
    # - Positions with negative P&L
    # - Mode B positions
    # - Positions with lowest RS
    sorted_pos = sorted(
        positions.items(),
        key=lambda kv: (
            kv[1].get("pnl_pct", 0),           # negative P&L first
            1 if kv[1].get("mode") == "B" else 0,  # B before A
        )
    )

    reduced_pct = 0
    for ticker, pos in sorted_pos:
        if reduced_pct >= excess:
            break
        size = pos.get("size_pct", 5)
        signals.append({
            "ticker":   ticker,
            "action":   "REDUCE_CASH_FLOOR",
            "priority": "TODAY",
            "reason":   f"Cash floor enforcement: deployed={current_deployed*100:.0f}% > max={max_deployed*100:.0f}%",
            "size":     round(size * 0.5, 1),  # reduce 50% of position
            "price":    pos.get("current_price", 0),
            "date":     date.today().isoformat(),
        })
        reduced_pct += size * 0.5

    return signals


# ── Paper portfolio: simulate fills ──────────────────────────────────────────

def execute_paper_signal(signal: dict, paper_port: dict) -> None:
    """
    Apply a Grade A setup signal to the paper portfolio (auto-execute).
    Grade B only in Markup regime.
    """
    ticker = signal.get("ticker")
    action = signal.get("action")
    today  = date.today().isoformat()

    if not ticker or not action:
        return

    positions = paper_port.setdefault("positions", {})
    portfolio_value = paper_port.get("portfolio_value", 100_000)
    cash = paper_port.get("cash", portfolio_value)

    if action == "BUY":
        size_pct  = signal.get("recommended_size_pct", 5.0)
        price     = signal.get("pivot", 0)
        if price <= 0:
            return

        position_value = portfolio_value * (size_pct / 100)
        if position_value > cash:
            return  # not enough cash

        shares = position_value / price
        positions[ticker] = {
            "mode":         signal.get("mode", "A"),
            "entry_price":  round(price, 2),
            "pivot":        round(price, 2),   # breakout pivot for base-failure check
            "entry_date":   today,
            "shares":       round(shares, 4),
            "size_pct":     size_pct,
            "stop":         signal.get("stop", price * 0.92),
            "target_1":     signal.get("target_1"),
            "setup_type":   signal.get("setup_type"),
            "theme":        signal.get("theme"),
            "current_price": round(price, 2),
            "pnl_pct":      0.0,
        }
        paper_port["cash"] = round(cash - position_value, 2)

    elif action in ("SELL_STOP", "TAKE_PROFIT_25PCT", "TAKE_PROFIT_50PCT"):
        if ticker not in positions:
            return

        pos   = positions[ticker]
        price = signal.get("price", pos.get("current_price", pos["entry_price"]))
        shares_to_sell = pos["shares"]

        if "50PCT" in action:
            shares_to_sell = pos["shares"] * 0.5
        elif "25PCT" in action:
            shares_to_sell = pos["shares"] * 0.25

        proceeds = round(shares_to_sell * price, 2)
        paper_port["cash"] = round(paper_port.get("cash", 0) + proceeds, 2)

        remaining = pos["shares"] - shares_to_sell
        if remaining <= 0.001 or action == "SELL_STOP":
            del positions[ticker]

            # Log to trade history
            pnl = (price - pos["entry_price"]) / pos["entry_price"] * 100
            paper_port.setdefault("trades_history", []).append({
                "ticker":      ticker,
                "entry_price": pos["entry_price"],
                "exit_price":  price,
                "entry_date":  pos["entry_date"],
                "exit_date":   today,
                "pnl_pct":     round(pnl, 2),
                "exit_reason": action,
                "mode":        pos.get("mode"),
            })
        else:
            pos["shares"] = round(remaining, 4)


def compute_expectancy(paper_port: dict) -> dict:
    """
    Compute Minervini expectancy metrics from closed trades.
    E = (win_rate × avg_winner) - (loss_rate × avg_loser)
    Returns dict with win_rate, avg_winner_pct, avg_loser_pct,
    expectancy_pct, profit_factor, and total_closed_trades.
    """
    trades = paper_port.get("trades_history", [])
    if not trades:
        return {
            "total_closed_trades": 0,
            "win_rate": None,
            "avg_winner_pct": None,
            "avg_loser_pct": None,
            "expectancy_pct": None,
            "profit_factor": None,
        }

    winners = [t["pnl_pct"] for t in trades if t.get("pnl_pct", 0) > 0]
    losers  = [t["pnl_pct"] for t in trades if t.get("pnl_pct", 0) <= 0]
    n       = len(trades)

    win_rate     = len(winners) / n if n else 0
    avg_winner   = sum(winners) / len(winners) if winners else 0
    avg_loser    = sum(losers)  / len(losers)  if losers  else 0  # negative number
    expectancy   = (win_rate * avg_winner) + ((1 - win_rate) * avg_loser)
    total_wins   = sum(winners) if winners else 0
    total_losses = abs(sum(losers)) if losers else 0
    profit_factor = round(total_wins / total_losses, 2) if total_losses > 0 else None

    return {
        "total_closed_trades": n,
        "win_rate":       round(win_rate, 3),
        "avg_winner_pct": round(avg_winner, 2),
        "avg_loser_pct":  round(avg_loser, 2),
        "expectancy_pct": round(expectancy, 2),
        "profit_factor":  profit_factor,
    }


def update_paper_portfolio_value(paper_port: dict) -> None:
    """Recalculate paper portfolio value using current prices."""
    cash  = paper_port.get("cash", 100_000)
    total = cash

    for ticker, pos in paper_port.get("positions", {}).items():
        price = _get_price(ticker) or pos.get("current_price", pos["entry_price"])
        pos["current_price"] = round(price, 2)
        pnl_pct = (price - pos["entry_price"]) / pos["entry_price"] * 100
        pos["pnl_pct"] = round(pnl_pct, 2)
        total += pos["shares"] * price

    paper_port["portfolio_value"]  = round(total, 2)
    paper_port["cash"]             = round(cash, 2)
    paper_port["deployed_pct"]     = round((total - cash) / total, 4) if total > 0 else 0
    paper_port["cash_pct"]         = round(cash / total, 4) if total > 0 else 1.0
    paper_port["total_positions"]  = len(paper_port.get("positions", {}))
    paper_port["last_updated"]     = date.today().isoformat()

    # Update expectancy metrics from all closed trades
    paper_port["expectancy"] = compute_expectancy(paper_port)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(mode: str = "premarket") -> dict:
    """
    mode = "premarket" | "eod"
    premarket: generate action signals + check positions
    eod: update prices only
    """
    today = date.today().isoformat()
    print(f"\n{'='*55}")
    print(f"  A09 Portfolio Manager [{mode.upper()}]  [{today}]")
    print(f"{'='*55}")

    # Load state files
    real_port  = _load_json(REAL_PORT_FILE)
    paper_port = _load_json(PAPER_PORT_FILE)
    health     = _load_json(ROOT / "data" / "regime" / "market_health.json")
    regime     = health.get("regime", "Unknown")
    max_dep    = health.get("max_deployed", 1.0)
    cash_floor = health.get("cash_floor", 0.0)

    print(f"  Regime: {regime} | Cash floor: {int(cash_floor*100)}% | Max deployed: {int(max_dep*100)}%")

    # ── Update paper portfolio prices ──────────────────────────────────────
    update_paper_portfolio_value(paper_port)

    # ── Compute current deployment (real) ──────────────────────────────────
    real_positions  = real_port.get("positions", {})
    total_size      = sum(p.get("size_pct", 0) / 100 for p in real_positions.values())
    real_port["deployed_pct"]    = round(total_size, 4)
    real_port["cash_pct"]        = round(1 - total_size, 4)
    real_port["total_positions"] = len(real_positions)

    print(f"  Real: {len(real_positions)} positions | Deployed: {total_size*100:.0f}%")
    print(f"  Paper: {paper_port['total_positions']} positions | Value: ${paper_port['portfolio_value']:,.0f}")

    if mode == "eod":
        # EOD: just update prices and save
        for ticker, pos in real_positions.items():
            price = _get_price(ticker)
            if price:
                pos["current_price"] = round(price, 2)
                if pos.get("entry_price"):
                    pos["pnl_pct"] = round((price - pos["entry_price"]) / pos["entry_price"] * 100, 2)

        real_port["last_updated"] = today
        _save_json(REAL_PORT_FILE, real_port)
        _save_json(PAPER_PORT_FILE, paper_port)
        print("  EOD prices updated.")
        return {"mode": "eod", "status": "ok", "positions": len(real_positions)}

    # ── Pre-market: full position checks ──────────────────────────────────
    all_signals = []

    for ticker, pos in list(real_positions.items()):
        signals = check_position(
            ticker, pos, regime, max_dep, total_size
        )
        all_signals.extend(signals)
        if signals:
            for sig in signals:
                print(f"  [{sig['priority']:10}] {ticker}: {sig['action']} — {sig['reason'][:60]}")

    # Cash floor enforcement
    floor_signals = enforce_cash_floor(real_port, health)
    all_signals.extend(floor_signals)
    if floor_signals:
        for sig in floor_signals:
            print(f"  [CASH FLOOR] {sig['ticker']}: {sig['action']}")

    # Sort by priority
    priority_order = {"IMMEDIATE": 0, "TODAY": 1, "REVIEW": 2}
    all_signals.sort(key=lambda s: priority_order.get(s.get("priority", "REVIEW"), 3))

    # ── Auto-execute Grade A signals in paper portfolio ────────────────────
    setup_data = _load_json(ROOT / "data" / "setups" / "setups_today.json")
    for setup in setup_data.get("setups", []):
        grade = setup.get("setup_grade")
        auto_execute = (
            grade == "A" or
            (grade == "B" and regime == "Markup")
        )
        if auto_execute and setup.get("ticker") not in paper_port.get("positions", {}):
            paper_signal = {**setup, "action": "BUY"}
            execute_paper_signal(paper_signal, paper_port)
            print(f"  [PAPER BUY] {setup['ticker']} Grade {grade} | Size: {setup.get('recommended_size_pct')}%")

    # Apply stop/profit signals to paper
    for sig in all_signals:
        if sig.get("action") in ("SELL_STOP", "TAKE_PROFIT_25PCT", "TAKE_PROFIT_50PCT"):
            execute_paper_signal(sig, paper_port)

    # Recalculate after executions
    update_paper_portfolio_value(paper_port)

    # ── Save everything ────────────────────────────────────────────────────
    real_port["last_updated"] = today
    _save_json(REAL_PORT_FILE,  real_port)
    _save_json(PAPER_PORT_FILE, paper_port)

    signals_output = {
        "date":          today,
        "regime":        regime,
        "cash_floor_pct": int(cash_floor * 100),
        "max_deployed_pct": int(max_dep * 100),
        "signals":       all_signals,
        "signal_count":  len(all_signals),
        "immediate":     sum(1 for s in all_signals if s.get("priority") == "IMMEDIATE"),
        "today":         sum(1 for s in all_signals if s.get("priority") == "TODAY"),
        "review":        sum(1 for s in all_signals if s.get("priority") == "REVIEW"),
        "generated_at":  datetime.now().strftime("%H:%M"),
    }
    _save_json(SIGNALS_FILE, signals_output)

    print(f"\n  Signals: {len(all_signals)} total | "
          f"IMMEDIATE: {signals_output['immediate']} | "
          f"TODAY: {signals_output['today']} | "
          f"REVIEW: {signals_output['review']}")
    print(f"  Paper portfolio: ${paper_port['portfolio_value']:,.0f} | "
          f"{paper_port['total_positions']} positions")
    print(f"  -> Written: {SIGNALS_FILE}")

    return signals_output


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="premarket", choices=["premarket", "eod"])
    args = parser.parse_args()
    run(mode=args.mode)
