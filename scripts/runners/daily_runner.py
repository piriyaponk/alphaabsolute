"""
AlphaAbsolute — Daily Runner (Weekdays 06:30)
Fast daily loop: news scrape → position update → alerts → Telegram
No Sonnet calls. Haiku only if new content found.
Cost: ~$0.02/day
"""
import sys, json, io
from datetime import datetime
from pathlib import Path

# Force UTF-8 stdout (avoids CP874 encoding errors on Thai Windows)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts" / "learning"))
sys.path.insert(0, str(BASE_DIR / "scripts" / "paper_trading"))
sys.path.insert(0, str(BASE_DIR / "scripts"))

def run_daily():
    start = datetime.now()
    print(f"\n[AlphaAbsolute Daily] {start.strftime('%Y-%m-%d %H:%M')}")
    print("-" * 45)

    insights = []
    urgent = []
    portfolio = {}
    perf = {}
    alerts = []

    # 1. Fast scrape (daily sources only — no LLM)
    print("[1/3] Daily scrape (news + insider)...")
    try:
        from research_scraper import run_scrapers
        raw = run_scrapers(mode="daily")
        total = sum(len(v) for v in raw.values())
        print(f"  {total} new items")

        # Only distill if we have new content (save tokens)
        if total >= 3:
            from distill_engine import run_extraction
            insights = run_extraction(raw)
            # Check for IMMEDIATE signals
            urgent = [i for i in insights if i.get("urgency") == "immediate"]
            if urgent:
                print(f"\n  *** {len(urgent)} URGENT SIGNALS ***")
                for sig in urgent:
                    print(f"  ! {sig.get('headline','')}")
                    if sig.get("ticker"):
                        print(f"    Ticker: {sig['ticker']} | {sig.get('action_note','')}")
    except Exception as e:
        print(f"  [ERROR]: {e}")

    # 2. Update paper portfolio prices + auto-exit stops
    print("\n[2/3] Portfolio prices update + stop-loss check...")
    try:
        from portfolio_engine import load_portfolio, update_positions, save_portfolio, get_performance
        portfolio = load_portfolio()
        alerts = update_positions(portfolio)

        # Auto-exit on stop loss / phase change (uses last saved NRGC state, no LLM)
        try:
            from auto_trader import auto_exit, _get_market_regime
            nrgc_state_dir = BASE_DIR / "data" / "nrgc" / "state"
            import json as _json
            nrgc_assessments_daily = {}
            if nrgc_state_dir.exists():
                for f in nrgc_state_dir.glob("*.json"):
                    try:
                        nrgc_assessments_daily[f.stem] = _json.loads(f.read_text(encoding="utf-8"))
                    except Exception:
                        pass
            daily_regime = _get_market_regime(portfolio)
            exits = auto_exit(portfolio, nrgc_assessments_daily, regime=daily_regime)
            if exits:
                print(f"  Auto-exits (stop/phase): {len(exits)}")
                for e in exits:
                    outcome = "WIN" if e.get("pnl_pct", 0) >= 0 else "LOSS"
                    print(f"    SELL {e['ticker']} {e.get('pnl_pct',0):+.1f}% [{outcome}]")
                    alerts.append(f"Auto-exit {e['ticker']} {e.get('pnl_pct',0):+.1f}% — {e.get('exit_reason','')[:40]}")
        except Exception as e2:
            print(f"  [Auto-exit skip]: {e2}")

        save_portfolio(portfolio)
        perf = get_performance(portfolio)
        print(f"  ${perf['total_value']:,.0f} | {perf['total_return_pct']:+.2f}% | Alpha: {perf['alpha']:+.2f}%")
        print(f"  {'BEATING' if perf['beating_nasdaq'] else 'LAGGING'} Nasdaq | Cash: {perf['cash_pct']:.0f}%")
        for a in alerts:
            print(f"  ! {a}")
    except Exception as e:
        print(f"  [ERROR]: {e}")

    # 3. Audit — verify all prices + calculations before sending
    print("\n[3/4] Auditing data before Telegram send...")
    try:
        from auditor import run_daily_audit
        portfolio, perf, audit_log = run_daily_audit(portfolio, perf)
    except Exception as e:
        print(f"  [Auditor skip]: {e}")

    # 4. Telegram notification
    print("\n[4/4] Sending Telegram notification...")
    try:
        from telegram_notifier import send_daily_summary
        ok = send_daily_summary(
            portfolio=portfolio,
            perf=perf,
            alerts=alerts,
            insights=insights,
            urgent=urgent,
        )
        print(f"  Telegram: {'sent' if ok else 'failed (check token/chat_id)'}")
    except Exception as e:
        print(f"  [Telegram ERROR]: {e}")

    # 4. Update state file for other agents
    try:
        state_file = BASE_DIR / "data" / "state" / "active_state.json"
        state = {}
        if state_file.exists():
            state = json.loads(state_file.read_text())
        state["last_daily_run"] = start.strftime("%Y-%m-%d %H:%M")
        state_file.write_text(json.dumps(state, indent=2))
    except: pass

    elapsed = (datetime.now() - start).seconds
    print(f"\n[Done] {elapsed}s")

if __name__ == "__main__":
    run_daily()
