#!/usr/bin/env python3
"""
AlphaAbsolute — Daily Trade Runner
===================================
Run this ONCE per day (after market close / before next open).
Applies SSL patch first, then runs the full pipeline:

  1. Load portfolio + NRGC state
  2. Get market regime (QQQ trend + TD Sequential)
  3. Run auto_trader cycle (exit stale, enter new)
  4. Update position prices
  5. Compute performance vs QQQ
  6. Send Telegram report with:
     - Full trade log (buy/sell today)
     - Portfolio state (all positions + P&L)
     - TD Sequential status (market timing)
     - Health Check scores
     - NRGC top picks (Phase 2-3 watchlist)

Purpose: CIO receives daily Telegram showing exactly what was bought/sold
         and what's on the watchlist — can follow along or copy trades.

Usage:
  python scripts/paper_trading/run_daily_trade.py
  python scripts/paper_trading/run_daily_trade.py --dry-run   (no save, no Telegram)
  python scripts/paper_trading/run_daily_trade.py --telegram-only (skip trading, just report)
"""

import sys
import json
import argparse
from datetime import datetime, date, timedelta
from pathlib import Path

# ── SSL patch MUST be first ────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "paper_trading"))
from utils.ssl_patch import apply as _ssl_apply
_ssl_apply()

# ── Now import everything else ────────────────────────────────────────────────
from portfolio_engine import (
    load_portfolio, save_portfolio, load_trade_log,
    get_current_price, update_positions,
)
from auto_trader import run_auto_trade_cycle

# TD Sequential + Health Check
try:
    sys.path.insert(0, str(ROOT / "scripts" / "learning"))
    from td_sequential import (
        get_td_regime_signal, load_cached_td_regime,
        td_entry_gate
    )
    HAS_TD = True
except Exception:
    HAS_TD = False

try:
    from health_check import load_cached_hc, run_health_check
    HAS_HC = True
except Exception:
    HAS_HC = False

# Telegram
try:
    sys.path.insert(0, str(ROOT / "scripts"))
    import telegram_notifier as tg
    HAS_TG = bool(tg.BOT_TOKEN and tg.CHAT_ID)
except Exception:
    HAS_TG = False


# =============================================================================
# NRGC STATE LOADER
# =============================================================================

def load_nrgc_state() -> dict:
    """Load all NRGC state files → dict keyed by ticker."""
    nrgc_dir = ROOT / "data" / "nrgc" / "state"
    assessments = {}
    if nrgc_dir.exists():
        for f in nrgc_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data:
                    assessments[f.stem] = data
            except Exception:
                pass
    return assessments


# =============================================================================
# PERFORMANCE CALCULATOR
# =============================================================================

def calc_performance(portfolio: dict) -> dict:
    """Calculate current portfolio performance vs QQQ."""
    capital   = portfolio.get("capital", 100_000)
    cash      = portfolio.get("cash", 0)
    positions = portfolio.get("positions", {})
    realized  = portfolio.get("realized_pnl_usd", 0)

    invested_value = sum(
        pos.get("shares", 0) * pos.get("current_price", pos.get("entry_price", 0))
        for pos in positions.values()
    )
    total_value = cash + invested_value
    total_return_pct = (total_value / capital - 1) * 100
    unrealized_pnl = sum(pos.get("pnl_usd", 0) for pos in positions.values())

    # QQQ benchmark
    qqq_ret = 0.0
    qqq_start = portfolio.get("benchmark_start_price")
    if qqq_start:
        qqq_now = get_current_price("QQQ") or qqq_start
        qqq_ret = (qqq_now / qqq_start - 1) * 100

    alpha   = total_return_pct - qqq_ret
    beating = total_return_pct > qqq_ret

    start = portfolio.get("start_date", date.today().isoformat())
    try:
        days_running = (date.today() - date.fromisoformat(start)).days
    except Exception:
        days_running = 0

    # Win rate from closed trades
    closed = portfolio.get("closed", [])
    wins   = [t for t in closed if t.get("pnl_pct", 0) >= 0]
    losses = [t for t in closed if t.get("pnl_pct", 0) < 0]
    win_rate = len(wins) / max(len(closed), 1) * 100
    avg_win  = sum(t.get("pnl_pct", 0) for t in wins)  / max(len(wins), 1)
    avg_loss = sum(t.get("pnl_pct", 0) for t in losses) / max(len(losses), 1)

    return {
        "capital":           capital,
        "total_value":       round(total_value, 2),
        "cash":              round(cash, 2),
        "cash_pct":          round(cash / max(total_value, 1) * 100, 1),
        "invested_value":    round(invested_value, 2),
        "invested_pct":      round(invested_value / max(total_value, 1) * 100, 1),
        "total_return_pct":  round(total_return_pct, 2),
        "unrealized_pnl_usd": round(unrealized_pnl, 2),
        "realized_pnl_usd":  round(realized, 2),
        "benchmark_return":  round(qqq_ret, 2),
        "alpha":             round(alpha, 2),
        "beating_nasdaq":    beating,
        "num_positions":     len(positions),
        "closed_trades":     len(closed),
        "win_rate":          round(win_rate, 1),
        "avg_win_pct":       round(avg_win, 2),
        "avg_loss_pct":      round(avg_loss, 2),
        "days_running":      days_running,
    }


# =============================================================================
# TELEGRAM TRADE REPORT
# =============================================================================

def build_telegram_report(portfolio: dict, perf: dict,
                           cycle_result: dict,
                           nrgc_assessments: dict,
                           td_regime: dict = None) -> str:
    """
    Build the full daily Telegram message.
    Format optimized for CIO to read on mobile and copy trades.
    """
    now       = datetime.now().strftime("%Y-%m-%d %H:%M")
    regime    = cycle_result.get("regime", "neutral")
    entries   = cycle_result.get("entries", [])
    exits     = cycle_result.get("exits", [])
    positions = portfolio.get("positions", {})
    capital   = perf.get("capital", 100_000)
    today_str = date.today().isoformat()

    lines = []

    # Normalize regime to standard labels
    _regime_norm = {
        "risk-on": "risk-on", "risk_on": "risk-on", "bull": "risk-on", "bullish": "risk-on",
        "risk-off": "risk-off", "risk_off": "risk-off", "bear": "risk-off", "bearish": "risk-off",
        "neutral": "neutral",
    }
    regime = _regime_norm.get(str(regime).lower(), "neutral")

    # ── Header ─────────────────────────────────────────────────────────────────
    regime_tag = {"risk-on": "RISK-ON", "neutral": "NEUTRAL", "risk-off": "RISK-OFF"}.get(regime, regime.upper())
    beat_tag   = "BEATING QQQ" if perf.get("beating_nasdaq") else "Lagging QQQ"

    lines += [
        f"<b>AlphaAbsolute Model Fund</b> | {now}",
        f"",
        f"<b>NAV:</b> ${perf['total_value']:,.0f}  ({perf['total_return_pct']:+.2f}%)",
        f"vs QQQ: {perf['benchmark_return']:+.2f}%  |  Alpha: <b>{perf['alpha']:+.2f}pp</b>  {beat_tag}",
        f"Regime: <b>{regime_tag}</b>  |  Cash: {perf['cash_pct']:.1f}%  |  {perf['num_positions']}/10 positions",
    ]

    # ── TD Sequential Status ───────────────────────────────────────────────────
    if td_regime:
        td_mod   = td_regime.get("regime_modifier", "neutral").upper()
        td_score = td_regime.get("score", 0)
        td_summ  = td_regime.get("summary", "")
        spy_sig  = td_regime.get("signals", {}).get("SPY", {})
        qqq_sig  = td_regime.get("signals", {}).get("QQQ", {})
        spy_setup = spy_sig.get("setup_count", 0) if spy_sig else 0
        qqq_setup = qqq_sig.get("setup_count", 0) if qqq_sig else 0

        lines += [
            f"",
            f"<b>TD Sequential</b> | {td_mod} (score={td_score})",
            f"  SPY Setup: {spy_setup:+d}/9  |  QQQ Setup: {qqq_setup:+d}/9",
            f"  {td_summ}",
        ]

    # ── TODAY'S TRADES (most important for CIO to copy) ───────────────────────
    all_today_trades = []
    try:
        log_path = ROOT / "data" / "paper_trading" / "trade_log.json"
        if log_path.exists():
            log = json.loads(log_path.read_text(encoding="utf-8"))
            all_today_trades = [t for t in log if t.get("date", "") == today_str]
    except Exception:
        pass

    if all_today_trades:
        lines += ["", "<b>TODAY'S TRADES:</b>"]
        for t in all_today_trades:
            action = t.get("action", "?")
            ticker = t.get("ticker", "?")
            price  = t.get("price", 0)
            shares = t.get("shares", 0)
            cost   = round(price * shares, 0)
            reason = t.get("reason", "")
            pnl    = t.get("pnl_pct")

            if action == "BUY":
                td_info = ""
                if HAS_TD:
                    try:
                        gate = td_entry_gate(ticker)
                        if gate.get("boost", 0) > 0:
                            td_info = f" | TD BUY SIGNAL"
                        elif gate.get("setup_count", 0) > 0:
                            td_info = f" | TD Setup {gate['setup_count']}/9"
                    except Exception:
                        pass

                hc_info = ""
                if HAS_HC:
                    try:
                        hc = load_cached_hc(ticker) or {}
                        hc_score = hc.get("score", 0)
                        if hc_score:
                            hc_info = f" | HC {hc_score}/8"
                    except Exception:
                        pass

                entry_pos = portfolio.get("positions", {}).get(ticker, {})
                stop = entry_pos.get("stop", price * 0.92)
                stop_pct = (stop / price - 1) * 100

                lines += [
                    f"",
                    f"  <b>BUY {ticker}</b> @ ${price:.2f}",
                    f"  Shares: {shares} | Cost: ${cost:,.0f}{td_info}{hc_info}",
                    f"  Stop: ${stop:.2f} ({stop_pct:.1f}%)",
                    f"  {reason[:80]}",
                ]
            elif action == "SELL":
                pnl_str = f"{pnl:+.1f}%" if pnl is not None else ""
                outcome = "WIN" if (pnl or 0) >= 0 else "LOSS"
                lines += [
                    f"",
                    f"  <b>SELL {ticker}</b> @ ${price:.2f}  [{outcome} {pnl_str}]",
                    f"  {reason[:80]}",
                ]
    else:
        lines += ["", "<b>TODAY'S TRADES:</b> No trades today (criteria not met)"]

    # ── CURRENT HOLDINGS ──────────────────────────────────────────────────────
    if positions:
        lines += ["", "<b>OPEN POSITIONS:</b>"]
        for ticker, pos in sorted(positions.items(),
                                   key=lambda x: x[1].get("pnl_pct", 0), reverse=True):
            entry    = pos.get("entry_price", 0)
            curr     = pos.get("current_price", entry)
            pnl_pct  = pos.get("pnl_pct", 0)
            cost     = pos.get("cost", 0)
            stop     = pos.get("stop", entry * 0.92)
            days     = pos.get("days_held", 0)
            theme    = pos.get("theme", "")
            weight   = round(cost / capital * 100, 1) if capital else 0
            stop_pct = (stop / entry - 1) * 100

            # TD + HC status for current position
            td_note = ""
            hc_note = ""
            if HAS_TD:
                try:
                    from td_sequential import load_cached_td_regime as _lctr
                    td_f = ROOT / "data" / "td_sequential" / f"{ticker}.json"
                    if td_f.exists():
                        td_d = json.loads(td_f.read_text())
                        s = td_d.get("setup_count", 0)
                        if s >= 7:
                            td_note = f" | TD CAUTION {s}/9"
                        elif s == 9:
                            td_note = f" | TD SELL 9!"
                except Exception:
                    pass

            pnl_sign = "+" if pnl_pct >= 0 else ""
            trail = pos.get("trail_stop")
            trail_str = f" | Trail ${trail:.2f}" if trail else ""

            lines += [
                f"  <b>{ticker}</b> [{weight}%] {theme}",
                f"    Entry ${entry:.2f} -> ${curr:.2f} ({pnl_sign}{pnl_pct:.1f}%)"
                f"  |  Stop ${stop:.2f} ({stop_pct:+.1f}%){trail_str}{td_note}",
                f"    {days}d held | HC{hc_note}",
            ]

    # ── WATCHLIST — NRGC Phase 2-3 Picks for CIO ─────────────────────────────
    phase3 = [(t, d) for t, d in nrgc_assessments.items()
              if d.get("phase") in (2, 3) and t not in positions]
    phase3.sort(key=lambda x: -x[1].get("nrgc_composite_score", 0))

    if phase3:
        lines += ["", "<b>WATCHLIST (Phase 2-3, not in portfolio):</b>"]
        for ticker, a in phase3[:8]:
            score  = a.get("nrgc_composite_score", 0)
            phase  = a.get("phase", "?")
            theme  = a.get("theme", "")
            action = a.get("action", "")
            conf   = a.get("confidence", 0)

            # TD gate status
            td_gate_str = ""
            if HAS_TD:
                try:
                    td_f = ROOT / "data" / "td_sequential" / f"{ticker}.json"
                    if td_f.exists():
                        td_d = json.loads(td_f.read_text())
                        s = td_d.get("setup_count", 0)
                        cd = td_d.get("countdown_count", 0)
                        as_of = td_d.get("as_of", "")
                        if s == 9:
                            td_gate_str = " [TD SELL 9 - BLOCKED]"
                        elif s >= 7:
                            td_gate_str = f" [TD Caution {s}/9]"
                        elif s <= -9:
                            td_gate_str = " [TD BUY 9 - ENTRY]"
                        elif s < 0:
                            td_gate_str = f" [TD Buy {-s}/9]"
                except Exception:
                    pass

            # HC score
            hc_str = ""
            if HAS_HC:
                try:
                    hc_f = ROOT / "data" / "health_checks" / f"{ticker}.json"
                    if hc_f.exists():
                        hc_d = json.loads(hc_f.read_text())
                        hc_score = hc_d.get("score", 0)
                        hc_rating = hc_d.get("rating", "?").split(" ")[0]
                        hc_str = f" | HC {hc_score}/8"
                except Exception:
                    pass

            why_blocked = ""
            if td_gate_str and "BLOCKED" in td_gate_str:
                why_blocked = " - TD BLOCKED"
            elif score < 50:
                why_blocked = " - Score too low"

            lines += [
                f"  <b>{ticker}</b> Ph{phase} Score={score} | {theme}{td_gate_str}{hc_str}",
                f"    {action[:70]}" if action else "",
            ]

    # ── Recent Closed Trades ───────────────────────────────────────────────────
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    recent_closed = [t for t in portfolio.get("closed", [])
                     if t.get("exit_date", t.get("close_date", "")) >= week_ago]
    if recent_closed:
        lines += ["", "<b>CLOSED THIS WEEK:</b>"]
        for t in recent_closed[-5:]:
            outcome = "WIN" if t.get("pnl_pct", 0) >= 0 else "LOSS"
            ticker  = t.get("ticker", "?")
            pnl_pct = t.get("pnl_pct", 0)
            pnl_usd = t.get("pnl_usd", 0)
            reason  = t.get("exit_reason", t.get("reason", ""))[:50]
            lines.append(f"  [{outcome}] <b>{ticker}</b> {pnl_pct:+.1f}% (${pnl_usd:+,.0f}) | {reason}")

    # ── Footer ──────────────────────────────────────────────────────────────────
    lines += [
        f"",
        f"P&L: Realized ${perf['realized_pnl_usd']:+,.0f} | Unrealized ${perf['unrealized_pnl_usd']:+,.0f}",
        f"Win rate: {perf['win_rate']:.1f}% | Running: {perf['days_running']}d | Closed: {perf['closed_trades']}",
        f"",
        f"AlphaAbsolute | NRGC+PULSE+TD Sequential",
    ]

    return "\n".join(l for l in lines if l is not None)


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def run(dry_run: bool = False, telegram_only: bool = False):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*60}")
    print(f"  AlphaAbsolute Daily Trade Runner | {now}")
    print(f"{'='*60}\n")

    # Load state
    portfolio = load_portfolio()
    nrgc      = load_nrgc_state()

    if not nrgc:
        print("  [!] No NRGC state found — run weekly screener first")
        print(f"      Expected: {ROOT}/data/nrgc/state/*.json")

    print(f"  Portfolio: {len(portfolio.get('positions', {}))} positions "
          f"| Cash: ${portfolio.get('cash', 0):,.0f}")
    print(f"  NRGC: {len(nrgc)} tickers assessed")

    # TD Sequential market regime
    td_regime = None
    if HAS_TD:
        print("\n  [TD Sequential] Fetching market regime...")
        try:
            td_regime = load_cached_td_regime()
            if not td_regime:
                td_regime = get_td_regime_signal(["SPY", "QQQ"])
            print(f"  TD Modifier: {td_regime.get('regime_modifier','?').upper()} "
                  f"| {td_regime.get('summary','')}")
        except Exception as e:
            print(f"  [TD error] {e}")

    # Update existing positions with current prices
    print("\n  [Update] Refreshing position prices...")
    alerts = update_positions(portfolio)
    if alerts:
        for a in alerts:
            print(f"  [Alert] {a}")

    # Run auto trade cycle (exit + entry with all gates)
    print("\n  [Trade Cycle] Running auto_trader...")
    if not telegram_only:
        cycle_result = run_auto_trade_cycle(portfolio, nrgc)
    else:
        print("  [Skip] telegram-only mode")
        cycle_result = {
            "regime": portfolio.get("cached_regime", "neutral"),
            "entries": [], "exits": [], "forced_exits": [],
        }

    # Save portfolio
    if not dry_run:
        save_portfolio(portfolio)
        print(f"\n  [Saved] Portfolio saved to {ROOT}/data/paper_trading/portfolio_state.json")

    # Calculate performance
    perf = calc_performance(portfolio)
    print(f"\n  NAV: ${perf['total_value']:,.0f} ({perf['total_return_pct']:+.2f}%)")
    print(f"  vs QQQ: {perf['benchmark_return']:+.2f}% | Alpha: {perf['alpha']:+.2f}pp")
    print(f"  {'BEATING' if perf['beating_nasdaq'] else 'Lagging'} QQQ")

    # Build + send Telegram
    print("\n  [Telegram] Building report...")
    report = build_telegram_report(portfolio, perf, cycle_result, nrgc, td_regime)

    if dry_run:
        print("\n  [DRY RUN] Telegram message preview:")
        print("-" * 50)
        # Strip HTML for console preview
        import re
        plain = re.sub(r"<[^>]+>", "", report)
        print(plain[:3000])
        print("-" * 50)
    elif HAS_TG:
        print("  Sending to Telegram...")
        if len(report) > 4000:
            tg.send_chunks(report)
        else:
            tg.send(report)
        print("  [Sent]")
    else:
        print("  [!] Telegram not configured (TELEGRAM_BOT_TOKEN or CHAT_ID missing)")
        print("      Set in .env file")

    print(f"\n  Done. {len(cycle_result.get('entries', []))} entries, "
          f"{len(cycle_result.get('exits', []))} exits today.\n")

    return perf


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AlphaAbsolute Daily Trade Runner")
    parser.add_argument("--dry-run", action="store_true",
                        help="Do not save or send Telegram — preview only")
    parser.add_argument("--telegram-only", action="store_true",
                        help="Skip trading, just send current state to Telegram")
    args = parser.parse_args()

    run(dry_run=args.dry_run, telegram_only=args.telegram_only)
