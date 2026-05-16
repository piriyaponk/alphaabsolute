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

    # ── NRGC top picks (Phase 3+ from nrgc/state/)
    nrgc_dir = ROOT / "data/nrgc/state"
    if nrgc_dir.exists():
        nrgc_all = {}
        for f in nrgc_dir.glob("*.json"):
            try:
                d = load_json(f)
                if d:
                    nrgc_all[f.stem] = d
            except Exception:
                pass
        if nrgc_all:
            # nrgc state files use "phase" key; score is "nrgc_composite_score"
            # Phase 2-3 = entry zones; Phase 4 = hold/reduce; Phase 5-6 = avoid
            entry_zone = sorted(
                [(t,
                  d.get("nrgc_composite_score", d.get("emls_score", 0)),
                  d.get("phase", 0))
                 for t, d in nrgc_all.items() if (d.get("phase") or 0) in (2, 3)],
                key=lambda x: (-x[1])
            )[:5]
            phase4 = [(t, d.get("nrgc_composite_score", 0), d.get("phase", 0))
                      for t, d in nrgc_all.items() if (d.get("phase") or 0) == 4]
            if entry_zone:
                lines.append("\n[Entry Zones Ph2-3] " + " | ".join(
                    f"{t} Ph{ph} score={sc}" for t, sc, ph in entry_zone
                ))
            if phase4:
                names = " | ".join(f"{t}({sc})" for t, sc, ph in sorted(phase4, key=lambda x: -x[1])[:5])
                lines.append(f"[Phase 4 Hold] {names}")

    # ── EMLS top scores (from smart signals if available)
    signals_path = ROOT / "data/smart_signals/latest.json"
    signals = load_json(signals_path)
    if signals:
        top = sorted(
            [(t, d.get("emls_boost", 0)) for t, d in signals.items() if d.get("emls_boost", 0) >= 3],
            key=lambda x: -x[1]
        )[:5]
        if top:
            lines.append("\n[Top Edge Signals] " + " | ".join(f"{t} +{b}" for t, b in top))

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
