"""
AlphaAbsolute — A15 Performance Monitor
Monitors paper portfolio R:R and win rate health.
Alerts when system goes off — red/yellow thresholds.
Writes monthly attribution to Obsidian Performance/.

Called by: session_start.py (daily check) + monthly GitHub Actions runner.
Cost: $0 — reads local JSON files only.
"""
import sys, json
from pathlib import Path
from datetime import date, datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.brain.obsidian_writer_v2 import write_performance_note, is_available

ROOT = Path(__file__).resolve().parents[2]
STATE_FILE  = ROOT / "data/paper_trading/state.json"
POSTMORTEMS = ROOT / "data/postmortems"

# ── Alert thresholds ──────────────────────────────────────────────────────────
WIN_RATE_RED    = 0.50  # < 50% = RED (system not working)
WIN_RATE_YELLOW = 0.60  # < 60% = YELLOW (monitoring)
RR_RED          = 2.0   # realized R:R < 2.0x = RED
RR_YELLOW       = 2.5   # realized R:R < 2.5x = YELLOW
MIN_TRADES      = 5     # need at least N trades before alerting


def _load_state() -> Optional[dict]:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_postmortems() -> list:
    """Load closed trades from paper trading state (closed_trades list)."""
    state = _load_state()
    if not state:
        return []
    # paper trader stores closed positions under 'closed_trades' or 'trade_history'
    for key in ("closed_trades", "trade_history", "trades"):
        if key in state and isinstance(state[key], list):
            raw = state[key]
            # Normalize field names
            trades = []
            for t in raw:
                # Support both pnl_pct and pnl_percent, return_pct etc
                pnl = (t.get("pnl_pct") or t.get("pnl_percent")
                       or t.get("return_pct") or t.get("pnl_pct_total"))
                if pnl is None:
                    # compute from entry/exit if available
                    ep = t.get("entry_price", 0)
                    xp = t.get("exit_price", 0) or t.get("close_price", 0)
                    pnl = (xp - ep) / ep * 100 if ep else 0
                trades.append({"pnl_pct": float(pnl)})
            return trades
    return []


def _compute_metrics(trades: list) -> dict:
    """Compute win rate, realized R:R, avg win, avg loss from closed trades."""
    if not trades:
        return {"n": 0, "win_rate": None, "avg_rr": None,
                "avg_win_pct": None, "avg_loss_pct": None}

    wins  = [t for t in trades if t.get("pnl_pct", 0) > 0]
    loses = [t for t in trades if t.get("pnl_pct", 0) <= 0]

    win_rate = len(wins) / len(trades) if trades else 0

    # Realized R:R = avg win / abs(avg loss)
    avg_win  = sum(t["pnl_pct"] for t in wins)  / len(wins)  if wins  else 0
    avg_loss = sum(t["pnl_pct"] for t in loses) / len(loses) if loses else 0

    # None = uncomputable (all wins or zero avg loss) — avoids false RED alert
    if not loses or avg_loss == 0:
        realized_rr = None
    else:
        realized_rr = round(abs(avg_win / avg_loss), 2)

    return {
        "n":            len(trades),
        "n_wins":       len(wins),
        "n_losses":     len(loses),
        "win_rate":     round(win_rate, 3),
        "avg_rr":       realized_rr,
        "avg_win_pct":  round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
    }


def _alert_level(metrics: dict) -> str:
    """Return RED / YELLOW / GREEN based on metrics."""
    n = metrics["n"]
    if n < MIN_TRADES:
        return "INSUFFICIENT_DATA"

    wr = metrics["win_rate"]
    rr = metrics["avg_rr"]  # may be None (all-wins or uncomputable)

    wr_bad = wr < WIN_RATE_RED
    rr_bad = (rr is not None) and (rr < RR_RED)
    if wr_bad or rr_bad:
        return "RED"

    wr_warn = wr < WIN_RATE_YELLOW
    rr_warn = (rr is not None) and (rr < RR_YELLOW)
    if wr_warn or rr_warn:
        return "YELLOW"
    return "GREEN"


def _format_terminal(state: Optional[dict], metrics: dict, alert: str) -> str:
    """Return compact terminal output for session_start."""
    if metrics["n"] < MIN_TRADES:
        n = metrics["n"]
        needed = MIN_TRADES - n
        return f"[A15] {n} closed trades ({needed} more needed for health check)"

    wr   = metrics["win_rate"] * 100
    rr   = metrics["avg_rr"]   # may be None
    n    = metrics["n"]
    icon = {"RED": "🔴", "YELLOW": "🟡", "GREEN": "🟢"}.get(alert, "⚪")
    rr_str = f"{rr:.1f}x" if rr is not None else "--"

    lines = [f"[A15 Perf Monitor] {icon} {alert} | {n} trades | WinRate={wr:.0f}% | RR={rr_str}"]

    if alert == "RED":
        lines.append("  !! System performance below minimum thresholds — review setups and exits")
        if metrics["win_rate"] < WIN_RATE_RED:
            lines.append(f"  !! Win rate {wr:.0f}% < {WIN_RATE_RED*100:.0f}% floor")
        if rr is not None and rr < RR_RED:
            lines.append(f"  !! Realized R:R {rr:.1f}x < {RR_RED}x minimum")
    elif alert == "YELLOW":
        lines.append("  ! Performance trending toward warning zone — monitor closely")

    # NAV vs QQQ from state
    if state:
        nav = state.get("nav", 0)
        inc = state.get("inception_nav", nav or 1)
        nav_pct = (nav - inc) / inc * 100 if inc else 0
        qqq_h = state.get("qqq_nav_history", {})
        qqq_inc = state.get("qqq_inception")
        qqq_vals = [qqq_h[k] for k in sorted(qqq_h)] if isinstance(qqq_h, dict) and qqq_h else []
        qqq_pct = (qqq_vals[-1] - float(qqq_inc)) / float(qqq_inc) * 100 if qqq_vals and qqq_inc else 0
        alpha   = nav_pct - qqq_pct
        alpha_str = f"+{alpha:.1f}%" if alpha >= 0 else f"{alpha:.1f}%"
        lines.append(f"  NAV={nav_pct:+.1f}% | QQQ={qqq_pct:+.1f}% | Alpha={alpha_str}")

    return "\n".join(lines)


def _write_obsidian_monthly(metrics: dict, state: Optional[dict]) -> bool:
    """Write monthly performance note to Obsidian."""
    today = date.today()
    period = f"{today.year}-{today.month:02d}"

    if state:
        nav = state.get("nav", 0); inc = state.get("inception_nav", nav or 1)
        nav_pct = (nav - inc) / inc * 100 if inc else 0
        qqq_h = state.get("qqq_nav_history", {}); qqq_inc = state.get("qqq_inception")
        qqq_vals = [qqq_h[k] for k in sorted(qqq_h)] if isinstance(qqq_h, dict) and qqq_h else []
        qqq_pct = (qqq_vals[-1] - float(qqq_inc)) / float(qqq_inc) * 100 if qqq_vals and qqq_inc else 0
    else:
        nav_pct = 0; qqq_pct = 0
    alpha   = nav_pct - qqq_pct

    content = f"""\
---
period: {period}
date: {today.isoformat()}
nav_return_pct: {nav_pct:.2f}
qqq_return_pct: {qqq_pct:.2f}
alpha_pct: {alpha:.2f}
win_rate: {metrics.get("win_rate", 0):.3f}
avg_rr: {metrics.get("avg_rr", 0):.2f}
n_trades: {metrics.get("n", 0)}
---

# Performance Report — {period}

## Summary

| Metric | Value | Target |
|--------|-------|--------|
| NAV Return | {nav_pct:+.1f}% | Beat QQQ |
| QQQ Return | {qqq_pct:+.1f}% | — |
| Alpha | {alpha:+.1f}% | > +2%/month |
| Win Rate | {metrics.get("win_rate", 0)*100:.0f}% | > 60% |
| Realized R:R | {metrics.get("avg_rr", 0):.1f}x | > 2.5x |
| Trades | {metrics.get("n", 0)} | — |
| Wins | {metrics.get("n_wins", 0)} | — |
| Losses | {metrics.get("n_losses", 0)} | — |
| Avg Win | {metrics.get("avg_win_pct", 0):+.1f}% | — |
| Avg Loss | {metrics.get("avg_loss_pct", 0):+.1f}% | — |

## Attribution

_Which positions drove alpha vs QQQ?_

[Fill from postmortems]

## Recurring Mistakes

1. [Identify from postmortems]
2.
3.

## System Health Verdict

Alert Level: {_alert_level(metrics)}

## Next Month Focus

-
"""
    return write_performance_note(period, content)


def run(write_monthly: bool = False) -> str:
    """Main entry point. Returns terminal summary."""
    state   = _load_state()
    trades  = _load_postmortems()
    metrics = _compute_metrics(trades)
    alert   = _alert_level(metrics)

    terminal = _format_terminal(state, metrics, alert)

    if write_monthly and is_available() and metrics["n"] >= MIN_TRADES:
        _write_obsidian_monthly(metrics, state)

    return terminal


def get_alert_level() -> str:
    """Quick check — returns RED/YELLOW/GREEN/INSUFFICIENT_DATA."""
    trades  = _load_postmortems()
    metrics = _compute_metrics(trades)
    return _alert_level(metrics)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--monthly", action="store_true", help="Also write Obsidian monthly report")
    args = p.parse_args()
    print(run(write_monthly=args.monthly))
