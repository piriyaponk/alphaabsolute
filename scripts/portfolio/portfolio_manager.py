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
    """Fetch latest closing price from data_engine."""
    try:
        from data_engine import get_ohlcv
        df = get_ohlcv(ticker, period="5d")
        if df is not None and not df.empty:
            return float(df["Close"].iloc[-1])
    except Exception:
        pass
    return None


def _get_rs_pct(ticker: str) -> Optional[float]:
    """Get current RS 3M percentile from latest RS file."""
    try:
        latest_file = ROOT / "data" / "rs_universe" / "latest.json"
        data = json.loads(latest_file.read_text(encoding="utf-8"))
        for item in data.get("results", []):
            if item.get("ticker") == ticker:
                return item.get("rs_pct_3m")
    except Exception:
        pass
    return None


def _get_sma50(ticker: str) -> Optional[float]:
    """Get 50-day SMA for a ticker."""
    try:
        from data_engine import get_ohlcv
        df = get_ohlcv(ticker, period="6mo")
        if df is not None and len(df) >= 50:
            return float(df["Close"].tail(50).mean())
    except Exception:
        pass
    return None


def _check_earnings_soon(ticker: str, days_ahead: int = 5) -> bool:
    """
    Check if earnings are within days_ahead trading days.
    Reads from FMP earnings calendar cache if available.
    """
    earnings_file = ROOT / "data" / "fundamentals" / f"{ticker}_earnings.json"
    if not earnings_file.exists():
        return False
    try:
        data = json.loads(earnings_file.read_text(encoding="utf-8"))
        next_date_str = data.get("next_earnings_date")
        if not next_date_str:
            return False
        next_date = date.fromisoformat(next_date_str)
        delta = (next_date - date.today()).days
        return 0 <= delta <= days_ahead
    except Exception:
        return False


# ── Position action checks ────────────────────────────────────────────────────

def check_position(ticker: str, pos: dict, regime: str,
                   max_deployed: float, current_deployed: float) -> list[dict]:
    """
    Run all checks on a single position. Returns list of action signals.
    """
    signals = []
    today   = date.today().isoformat()

    entry_price = pos.get("entry_price", 0)
    entry_date  = pos.get("entry_date", today)
    mode        = pos.get("mode", "A")
    size_pct    = pos.get("size_pct", 10.0)
    stop_pct    = -0.08 if mode == "A" else -0.10

    current_price = _get_price(ticker)
    if not current_price or not entry_price:
        return signals

    pnl_pct = (current_price - entry_price) / entry_price * 100

    # Update position with current price
    pos["current_price"] = round(current_price, 2)
    pos["pnl_pct"]       = round(pnl_pct, 2)
    pos["last_updated"]  = today

    # ── Check 1: Stop hit ──────────────────────────────────────────────────
    stop_level = entry_price * (1 + stop_pct)
    if current_price <= stop_level:
        signals.append({
            "ticker":   ticker,
            "action":   "SELL_STOP",
            "priority": "IMMEDIATE",
            "reason":   f"Stop hit: {pnl_pct:.1f}% (stop={stop_pct*100:.0f}% from entry)",
            "size":     size_pct,
            "price":    round(current_price, 2),
            "date":     today,
        })
        return signals  # no need to check further

    # ── Check 2: Earnings within 5 days ───────────────────────────────────
    if _check_earnings_soon(ticker):
        signals.append({
            "ticker":   ticker,
            "action":   "FLAG_EARNINGS",
            "priority": "TODAY",
            "reason":   "Earnings within 5 trading days — no new adds, consider reduce",
            "size":     0,
            "price":    round(current_price, 2),
            "date":     today,
        })

    # ── Check 3: RS deteriorating + 50DMA break (Mode A only) ─────────────
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

    # ── Check 4: Profit targets ────────────────────────────────────────────
    if pnl_pct >= 50:
        signals.append({
            "ticker":   ticker,
            "action":   "TAKE_PROFIT_50PCT",
            "priority": "TODAY",
            "reason":   f"+{pnl_pct:.1f}% gain — take 50% off, trail remaining with 10W MA",
            "size":     round(size_pct * 0.5, 1),
            "price":    round(current_price, 2),
            "date":     today,
        })
    elif pnl_pct >= 25:
        # Check if we've already taken 25% profit (logged in position state)
        if not pos.get("took_25pct_profit"):
            signals.append({
                "ticker":   ticker,
                "action":   "TAKE_PROFIT_25PCT",
                "priority": "TODAY",
                "reason":   f"+{pnl_pct:.1f}% gain — take 25% off, trail stop to breakeven",
                "size":     round(size_pct * 0.25, 1),
                "price":    round(current_price, 2),
                "date":     today,
            })
            pos["took_25pct_profit"] = True
    elif pnl_pct >= 15 and not pos.get("breakeven_stop_set"):
        signals.append({
            "ticker":   ticker,
            "action":   "TRAIL_STOP_BREAKEVEN",
            "priority": "REVIEW",
            "reason":   f"+{pnl_pct:.1f}% gain — trail stop to breakeven ({entry_price:.2f})",
            "size":     0,
            "price":    round(current_price, 2),
            "date":     today,
        })
        pos["breakeven_stop_set"] = True

    # ── Check 5: Cash floor breach ─────────────────────────────────────────
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
