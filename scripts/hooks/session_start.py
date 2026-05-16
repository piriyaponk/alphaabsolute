"""
AlphaAbsolute — Session Start Hook
Fires when Claude Code session begins.
Displays: portfolio state, EMLS top scores, open risk flags, today's key events.
"""
import json, os, sys
from pathlib import Path
from datetime import datetime, date

ROOT = Path(__file__).resolve().parents[2]

def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default or {}

def session_start():
    today = date.today().strftime("%Y-%m-%d")
    lines = [f"\n{'='*60}", f"  AlphaAbsolute — Session Start  [{today}]", f"{'='*60}"]

    # ── Portfolio state
    port_path = ROOT / "data/paper_trading/portfolio_state.json"
    port = load_json(port_path)
    positions = port.get("positions", {})
    cash = port.get("cash", 0)
    if positions:
        nav = cash + sum(
            v.get("shares", 0) * v.get("current_price", v.get("entry_price", 0))
            for v in positions.values()
        )
        cash_pct = round(cash / max(nav, 1) * 100, 1)
        lines.append(f"\n[Portfolio] {len(positions)} positions | Cash {cash_pct}% | NAV ${nav:,.0f}")
        # Show positions with P&L
        for ticker, pos in sorted(positions.items()):
            entry = pos.get("entry_price", 0)
            curr  = pos.get("current_price", entry)
            pnl   = round((curr - entry) / max(entry, 0.01) * 100, 1) if entry else 0
            sign  = "+" if pnl >= 0 else ""
            lines.append(f"  {ticker:<6} {sign}{pnl}% | entry ${entry:.2f} | {pos.get('shares',0)} shares")
    else:
        lines.append("\n[Portfolio] No open positions | Cash only")

    # ── EMLS top scores (from smart signals if available)
    signals_path = ROOT / "data/smart_signals/latest.json"
    signals = load_json(signals_path)
    if signals:
        top = sorted(
            [(t, d.get("emls_boost", 0)) for t, d in signals.items() if d.get("emls_boost", 0) >= 3],
            key=lambda x: -x[1]
        )[:5]
        if top:
            lines.append("\n[Top EMLS Signals] " + " | ".join(f"{t} +{b}" for t, b in top))

    # ── Risk flags (any position near stop)
    risk_flags = []
    for ticker, pos in positions.items():
        entry = pos.get("entry_price", 0)
        curr  = pos.get("current_price", entry)
        if entry and curr:
            drawdown = (curr - entry) / entry * 100
            if drawdown <= -5:
                risk_flags.append(f"{ticker} {drawdown:.1f}% (stop at -8%)")
    if risk_flags:
        lines.append("\n[!] RISK FLAGS: " + " | ".join(risk_flags))

    # ── Latest ops log snippet
    ops_today = ROOT / f"output/ops_log_{date.today().strftime('%y%m%d')}.md"
    ops_files = sorted((ROOT / "output").glob("ops_log_*.md"), reverse=True)
    if ops_files:
        last_ops = ops_files[0]
        content  = last_ops.read_text(encoding="utf-8", errors="ignore")
        last_decision = [l for l in content.splitlines() if l.startswith("##")]
        if last_decision:
            lines.append(f"\n[Last Ops] {last_ops.name}: {last_decision[-1]}")

    # ── Today's context reminder
    lines.append(f"\n[Context] Load mode: research.md / execution.md / review.md")
    lines.append(f"{'='*60}\n")

    print("\n".join(lines), flush=True)

if __name__ == "__main__":
    session_start()
