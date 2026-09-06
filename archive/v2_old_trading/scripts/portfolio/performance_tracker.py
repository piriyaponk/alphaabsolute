"""
AlphaAbsolute v2 -- A12 Performance Tracker
=============================================
Monthly performance report: P&L attribution vs QQQ, mistake classification,
top 3 recurring errors with $ impact, and win/loss statistics.

Run: 1st of every month (automated via pre_market_runner.py --mode monthly)
     or manually: python scripts/portfolio/performance_tracker.py

Outputs:
  output/performance_YYMMDD.md     -- full monthly report
  data/calibration/perf_summary.json  -- machine-readable summary

Cost: $0 (Python only, uses paper_portfolio_state.json trade history)
"""

from __future__ import annotations
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "utils"))

PAPER_FILE  = ROOT / "data" / "portfolio" / "paper_portfolio_state.json"
OUT_DIR     = ROOT / "output"
CALIB_DIR   = ROOT / "data" / "calibration"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CALIB_DIR.mkdir(parents=True, exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _get_qqq_return(start_date: str, end_date: str) -> Optional[float]:
    """Fetch QQQ return between two dates via data_engine.
    get_ohlcv() returns a pandas DataFrame with DatetimeIndex and Close column.
    """
    try:
        from data_engine import get_ohlcv
        df = get_ohlcv("QQQ", period="1y")
        if df is None or df.empty:
            return None
        # Normalize index to date strings for filtering
        if hasattr(df.index, "strftime"):
            df = df.copy()
            df["_date"] = df.index.strftime("%Y-%m-%d")
        else:
            df = df.copy()
            df["_date"] = df.index.astype(str).str[:10]
        # Filter to the period window
        mask = (df["_date"] >= start_date) & (df["_date"] <= end_date)
        relevant = df[mask]
        if len(relevant) < 2:
            return None
        # Use Close column (capital C from yfinance/polygon convention)
        close_col = "Close" if "Close" in relevant.columns else "close"
        first_close = float(relevant[close_col].iloc[0])
        last_close  = float(relevant[close_col].iloc[-1])
        if first_close and first_close > 0:
            return round((last_close - first_close) / first_close * 100, 2)
    except Exception as e:
        print(f"  [WARN] _get_qqq_return failed: {e}")
    return None


# ── Mistake Classifier ────────────────────────────────────────────────────────

MISTAKE_TYPES = {
    "stop_too_tight":    "Stopped out then stock recovered — stop may have been too tight",
    "exit_too_early":    "Sold before full target — left profit on the table",
    "bought_extended":   "Entered beyond 3% of pivot — chased the breakout",
    "wrong_regime":      "Entered during Distribution/Markdown — ignored regime gate",
    "wrong_mode":        "Applied Mode A criteria to Mode B stock or vice versa",
    "missed_stop":       "Loss exceeded -10% — stop not honored",
    "earnings_surprise": "Held through earnings — gap down cost more than -8%",
    "size_too_large":    "Position > mode limit (>10% Mode A or >5% Mode B first add)",
}


def _classify_trade_mistake(trade: dict) -> list[str]:
    """Return list of mistake codes for a closed trade."""
    mistakes = []
    entry   = trade.get("entry_price", 0) or 0
    exit_p  = trade.get("exit_price", 0) or 0
    pivot   = trade.get("pivot", entry) or entry
    mode    = trade.get("mode", "A")
    regime  = trade.get("regime_at_entry", "Markup")
    size    = trade.get("size_pct", 10)
    reason  = trade.get("exit_reason", "")

    if entry <= 0 or exit_p <= 0:
        return mistakes

    pnl_pct = (exit_p - entry) / entry * 100

    # Stop too tight: stopped out at -8% but stock could have been a winner
    if "stop" in reason.lower() and -9 < pnl_pct < -6:
        mistakes.append("stop_too_tight")

    # Exit too early: profit taken but target not reached
    if "profit" in reason.lower() and 0 < pnl_pct < 15:
        mistakes.append("exit_too_early")

    # Bought extended: entry > 3% above pivot
    if pivot > 0 and entry > pivot * 1.03:
        mistakes.append("bought_extended")

    # Wrong regime: entered in Distribution or Markdown
    if regime in ("Distribution", "Markdown") and mode == "B":
        mistakes.append("wrong_regime")

    # Missed stop: loss > stop level
    stop_pct = -8 if mode == "A" else -10
    if pnl_pct < stop_pct - 2:
        mistakes.append("missed_stop")

    # Earnings surprise (gap down)
    if "earnings" in reason.lower() and pnl_pct < -8:
        mistakes.append("earnings_surprise")

    # Size too large
    mode_a_limit = 10
    mode_b_limit = 5
    if mode == "A" and size > mode_a_limit + 2:
        mistakes.append("size_too_large")
    elif mode == "B" and size > mode_b_limit + 1:
        mistakes.append("size_too_large")

    return mistakes


# ── Core Analysis ─────────────────────────────────────────────────────────────

def analyse_period(trades: list[dict], period_start: str, period_end: str) -> dict:
    """Analyse all trades closed within the period."""
    closed = [
        t for t in trades
        if t.get("status") == "closed"
        and period_start <= t.get("close_date", "") <= period_end
    ]

    if not closed:
        return {"n_trades": 0, "message": "No closed trades in this period"}

    winners = [t for t in closed if (t.get("pnl_pct", 0) or 0) > 0]
    losers  = [t for t in closed if (t.get("pnl_pct", 0) or 0) <= 0]

    total_pnl  = sum(t.get("pnl_usd", 0) or 0 for t in closed)
    win_rate   = len(winners) / len(closed) * 100 if closed else 0

    avg_win    = (sum(t.get("pnl_pct", 0) or 0 for t in winners) / len(winners)
                  if winners else 0)
    avg_loss   = (sum(t.get("pnl_pct", 0) or 0 for t in losers)  / len(losers)
                  if losers else 0)

    wl_ratio   = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    # Attribution by mode
    mode_a = [t for t in closed if t.get("mode") == "A"]
    mode_b = [t for t in closed if t.get("mode") == "B"]

    # Mistake frequency
    all_mistakes: list[str] = []
    mistake_pnl: dict[str, float] = defaultdict(float)
    for t in closed:
        ms = _classify_trade_mistake(t)
        all_mistakes.extend(ms)
        for m in ms:
            mistake_pnl[m] += t.get("pnl_usd", 0) or 0

    mistake_counts: dict[str, int] = defaultdict(int)
    for m in all_mistakes:
        mistake_counts[m] += 1

    top3_mistakes = sorted(mistake_counts.items(), key=lambda x: -x[1])[:3]

    return {
        "period_start":  period_start,
        "period_end":    period_end,
        "n_trades":      len(closed),
        "n_winners":     len(winners),
        "n_losers":      len(losers),
        "win_rate_pct":  round(win_rate, 1),
        "avg_win_pct":   round(avg_win, 2),
        "avg_loss_pct":  round(avg_loss, 2),
        "wl_ratio":      round(wl_ratio, 2),
        "total_pnl_usd": round(total_pnl, 2),
        "mode_a_trades": len(mode_a),
        "mode_b_trades": len(mode_b),
        "mode_a_pnl":    round(sum(t.get("pnl_usd", 0) or 0 for t in mode_a), 2),
        "mode_b_pnl":    round(sum(t.get("pnl_usd", 0) or 0 for t in mode_b), 2),
        "top3_mistakes": [
            {
                "code":       code,
                "count":      count,
                "pnl_impact": round(mistake_pnl.get(code, 0), 2),
                "description": MISTAKE_TYPES.get(code, code),
            }
            for code, count in top3_mistakes
        ],
        "closed_trades": closed,
    }


# ── Report Builder ────────────────────────────────────────────────────────────

def build_report(analysis: dict, qqq_return: Optional[float],
                 paper: dict, today: str) -> str:
    lines: list[str] = []
    period_start = analysis.get("period_start", "?")
    period_end   = analysis.get("period_end", "?")
    n            = analysis.get("n_trades", 0)

    lines.append(f"# ALPHAABSOLUTE v2 — Monthly Performance Report")
    lines.append(f"## Period: {period_start} → {period_end}")
    lines.append(f"*Generated: {today}*")
    lines.append("")

    if n == 0:
        lines.append("## No closed trades this period.")
        lines.append("Paper portfolio is active — positions pending close.")
        lines.append("")
        pv = paper.get("portfolio_value", 100000)
        start = paper.get("_meta", {}).get("starting_value", 100000)
        total_ret = (pv - start) / start * 100 if start else 0
        lines.append(f"**Paper Portfolio Value:** ${pv:,.0f} ({total_ret:+.1f}% from ${start:,.0f} start)")
        return "\n".join(lines)

    # ── Summary Stats ─────────────────────────────────────────────────────────
    lines.append("## Summary")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total trades | {n} |")
    lines.append(f"| Win rate | **{analysis['win_rate_pct']:.1f}%** (target >55%) |")
    lines.append(f"| Avg winner | +{analysis['avg_win_pct']:.1f}% |")
    lines.append(f"| Avg loser | {analysis['avg_loss_pct']:.1f}% |")
    lines.append(f"| W/L ratio | **{analysis['wl_ratio']:.2f}x** (target >2.5x) |")
    lines.append(f"| Net P&L | **${analysis['total_pnl_usd']:+,.0f}** |")
    lines.append("")

    # QQQ comparison
    lines.append("## vs QQQ Benchmark")
    if qqq_return is not None:
        # Estimate paper portfolio return for period
        pv    = paper.get("portfolio_value", 100000)
        start = paper.get("_meta", {}).get("starting_value", 100000)
        port_ret = (pv - start) / start * 100 if start else 0
        alpha    = port_ret - qqq_return
        alpha_str = f"**+{alpha:.1f}% alpha**" if alpha > 0 else f"{alpha:.1f}% (underperform)"
        lines.append(f"| | Return |")
        lines.append(f"|---|---|")
        lines.append(f"| Paper Portfolio | {port_ret:+.1f}% |")
        lines.append(f"| QQQ | {qqq_return:+.1f}% |")
        lines.append(f"| Alpha | {alpha_str} |")
    else:
        lines.append("QQQ comparison unavailable (data fetch failed).")
    lines.append("")

    # ── Attribution by Mode ───────────────────────────────────────────────────
    lines.append("## Attribution by Mode")
    lines.append(f"| Mode | Trades | Net P&L |")
    lines.append(f"|------|--------|---------|")
    lines.append(f"| Mode A (Leadership) | {analysis['mode_a_trades']} | ${analysis['mode_a_pnl']:+,.0f} |")
    lines.append(f"| Mode B (Big Shot)   | {analysis['mode_b_trades']} | ${analysis['mode_b_pnl']:+,.0f} |")
    lines.append("")

    # ── Top 3 Recurring Mistakes ──────────────────────────────────────────────
    lines.append("## Top 3 Recurring Mistakes (This Month)")
    if analysis["top3_mistakes"]:
        for i, m in enumerate(analysis["top3_mistakes"], 1):
            impact_str = f"${m['pnl_impact']:+,.0f}" if m["pnl_impact"] else "n/a"
            lines.append(f"### {i}. {m['code']} (×{m['count']} trades, {impact_str} impact)")
            lines.append(f"{m['description']}")
            lines.append("")
        # Actionable improvement — most common mistake
        top = analysis["top3_mistakes"][0]
        lines.append("### Actionable Improvement")
        if top["code"] == "stop_too_tight":
            lines.append("**Widen initial stop slightly or use wider pivot** — "
                          "consider 10% stop for high-volatility setups.")
        elif top["code"] == "exit_too_early":
            lines.append("**Let winners run** — use trailing stop at 10-week MA "
                          "instead of fixed profit targets.")
        elif top["code"] == "bought_extended":
            lines.append("**Be patient with entries** — wait for pullback to pivot "
                          "or set limit order at or below pivot +3%.")
        elif top["code"] == "wrong_regime":
            lines.append("**Respect regime gate** — no Mode B entries in "
                          "Distribution. Check A01 output before any new position.")
        elif top["code"] == "missed_stop":
            lines.append("**Honor the hard stop** — set stop orders at entry, "
                          "not mental stops. -8% Mode A, -10% Mode B, no exceptions.")
        else:
            lines.append("**Review signal calibration** — run A12 Bayesian calibrator "
                          "to update signal weights based on this month's outcomes.")
    else:
        lines.append("No recurring mistakes detected — excellent discipline!")
    lines.append("")

    # ── Trade Log ─────────────────────────────────────────────────────────────
    lines.append("## Closed Trades Log")
    lines.append("| # | Ticker | Mode | Entry | Exit | P&L% | P&L$ | Setup | Exit Reason |")
    lines.append("|---|--------|------|-------|------|------|------|-------|-------------|")
    for i, t in enumerate(analysis.get("closed_trades", []), 1):
        ticker  = t.get("ticker", "?")
        mode    = t.get("mode", "?")
        entry   = t.get("entry_price", 0) or 0
        exit_p  = t.get("exit_price", 0) or 0
        pnl_pct = t.get("pnl_pct", 0) or 0
        pnl_usd = t.get("pnl_usd", 0) or 0
        setup   = t.get("setup_type", "?")
        reason  = (t.get("exit_reason", "?") or "?")[:30]
        lines.append(f"| {i} | {ticker} | {mode} | ${entry:.2f} | ${exit_p:.2f} | "
                     f"{pnl_pct:+.1f}% | ${pnl_usd:+,.0f} | {setup} | {reason} |")
    lines.append("")

    # ── System Health Check ───────────────────────────────────────────────────
    lines.append("## System Health Check")
    wr  = analysis["win_rate_pct"]
    wlr = analysis["wl_ratio"]
    lines.append(f"- Win rate: {wr:.1f}% {'✅' if wr >= 55 else '⚠️ (below 55% target)'}")
    lines.append(f"- W/L ratio: {wlr:.2f}x {'✅' if wlr >= 2.5 else '⚠️ (below 2.5x target)'}")
    if qqq_return is not None:
        pv    = paper.get("portfolio_value", 100000)
        start = paper.get("_meta", {}).get("starting_value", 100000)
        port_ret = (pv - start) / start * 100 if start else 0
        alpha = port_ret - qqq_return
        lines.append(f"- Alpha vs QQQ: {alpha:+.1f}% {'✅' if alpha > 0 else '⚠️ (underperforming QQQ)'}")
    lines.append("")
    lines.append("---")
    lines.append(f"*AlphaAbsolute v2 | Performance Tracker | {today}*")

    return "\n".join(lines)


# ── Entry Point ───────────────────────────────────────────────────────────────

def run() -> dict:
    today = date.today().isoformat()
    today_str = date.today().strftime("%y%m%d")

    print(f"\n{'='*55}")
    print(f"  A12 Performance Tracker  [{today}]")
    print(f"{'='*55}")

    # Determine report period (prior calendar month)
    first_of_this_month = date.today().replace(day=1)
    period_end   = (first_of_this_month - timedelta(days=1)).isoformat()
    period_start = (first_of_this_month - timedelta(days=1)).replace(day=1).isoformat()
    print(f"  Period: {period_start} → {period_end}")

    # Load paper portfolio trade history
    paper = _load_json(PAPER_FILE)
    all_trades = paper.get("trades_history", [])
    print(f"  Total trades in history: {len(all_trades)}")

    # Analyse the period
    analysis = analyse_period(all_trades, period_start, period_end)

    # QQQ benchmark return
    qqq_ret = _get_qqq_return(period_start, period_end)
    if qqq_ret is not None:
        print(f"  QQQ return ({period_start}→{period_end}): {qqq_ret:+.1f}%")
    else:
        print("  QQQ return: unavailable")

    n = analysis.get("n_trades", 0)
    print(f"  Closed trades: {n} | Win rate: {analysis.get('win_rate_pct',0):.1f}%")
    print(f"  Net P&L: ${analysis.get('total_pnl_usd',0):+,.0f}")

    if analysis.get("top3_mistakes"):
        print("  Top mistakes:", ", ".join(m["code"] for m in analysis["top3_mistakes"]))

    # Build report
    report_md  = build_report(analysis, qqq_ret, paper, today)
    report_file = OUT_DIR / f"performance_{today_str}.md"
    report_file.write_text(report_md, encoding="utf-8")
    print(f"  -> Report: {report_file}")

    # Save machine-readable summary
    summary = {
        "date":          today,
        "period_start":  period_start,
        "period_end":    period_end,
        "n_trades":      n,
        "win_rate_pct":  analysis.get("win_rate_pct", 0),
        "wl_ratio":      analysis.get("wl_ratio", 0),
        "total_pnl_usd": analysis.get("total_pnl_usd", 0),
        "qqq_return_pct": qqq_ret,
        "top3_mistakes": analysis.get("top3_mistakes", []),
    }
    perf_file = CALIB_DIR / "perf_summary.json"
    perf_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"  -> Summary: {perf_file}")

    return summary


if __name__ == "__main__":
    run()
