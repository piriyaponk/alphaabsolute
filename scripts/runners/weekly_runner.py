"""
AlphaAbsolute — Weekly Runner (Sunday 08:00)
Full learning loop: scrape → distill → paper update → auto-trade → promotions → post-mortems → learning → Telegram

Total LLM cost per week: ~$0.50 (Haiku extraction + 1-2 Sonnet synthesis)
Run from Task Scheduler every Sunday 08:00
"""
import sys, json, os, io
from datetime import datetime, timedelta
from pathlib import Path

# Force UTF-8 stdout (avoids CP874 encoding errors on Thai Windows)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts" / "learning"))
sys.path.insert(0, str(BASE_DIR / "scripts" / "paper_trading"))
sys.path.insert(0, str(BASE_DIR / "scripts"))

LOG_FILE = BASE_DIR / "data" / "state" / "weekly_run_log.json"


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def run_weekly():
    start = datetime.now()
    log("=" * 55)
    log(f"AlphaAbsolute Weekly Runner — {start.strftime('%Y-%m-%d')}")
    log("=" * 55)

    results = {"date": start.strftime("%Y-%m-%d"), "steps": {}}

    # ── STEP 1: Scrape (no LLM, free) ────────────────────────────────────────
    log("\n[1/7] Research Scraping (weekly mode)...")
    try:
        from research_scraper import run_scrapers
        raw_items = run_scrapers(mode="weekly")
        total_raw = sum(len(v) for v in raw_items.values())
        log(f"  Scraped {total_raw} new items from {len(raw_items)} sources")
        results["steps"]["scrape"] = {"items": total_raw, "sources": len(raw_items)}
    except Exception as e:
        log(f"  [ERROR] Scraping failed: {e}")
        results["steps"]["scrape"] = {"error": str(e)}
        raw_items = {}

    # ── STEP 2: Distill insights (Haiku batch) ────────────────────────────────
    log("\n[2/7] Distilling insights (Haiku batch)...")
    insights = []
    if raw_items:
        try:
            from distill_engine import run_extraction
            insights = run_extraction(raw_items)
            log(f"  Extracted {len(insights)} actionable insights")
            results["steps"]["distill"] = {"insights": len(insights)}
        except Exception as e:
            log(f"  [ERROR] Distillation failed: {e}")
            results["steps"]["distill"] = {"error": str(e)}

    # ── STEP 3: Weekly synthesis (1 Sonnet call) ──────────────────────────────
    log("\n[3/8] Weekly synthesis (Sonnet)...")
    synthesis = None
    if len(insights) >= 3:
        try:
            from distill_engine import weekly_synthesis
            synthesis = weekly_synthesis(insights)
            if synthesis:
                log(f"  Regime: {synthesis.get('regime_signal','?')}")
                log(f"  Top themes: {synthesis.get('top_themes',[])}")
                log(f"  Opportunities: {len(synthesis.get('top_opportunities',[]))}")
                results["steps"]["synthesis"] = {
                    "regime": synthesis.get("regime_signal"),
                    "themes": synthesis.get("top_themes"),
                }
        except Exception as e:
            log(f"  [ERROR] Synthesis failed: {e}")

    # ── STEP 3b: NRGC Phase Update — core auto-learning step ──────────────────
    log("\n[3b/8] NRGC Phase Update (earnings + narrative + price)...")
    nrgc_assessments = {}
    try:
        from source_config import DEFAULT_WATCHLIST
        from nrgc_tracker import run_nrgc_update, get_nrgc_summary_for_all
        from earnings_miner import run_earnings_scan

        # Build flat ticker→theme map for earnings scan
        all_tickers = []
        theme_map = {}
        for theme, tickers in DEFAULT_WATCHLIST.items():
            for t in tickers:
                all_tickers.append(t)
                theme_map[t] = theme

        # 1. Earnings scan (Yahoo Finance quarterly — free, no LLM)
        log(f"  Scanning earnings for {len(all_tickers)} tickers...")
        earnings_results = run_earnings_scan(all_tickers[:20], theme_map, client=None)

        # 2. NRGC phase assessment (Haiku per ticker if API key available)
        from distill_engine import client as ai_client
        nrgc_assessments = run_nrgc_update(DEFAULT_WATCHLIST, client=ai_client,
                                            earnings_results=earnings_results)

        # Log top setups
        summary = get_nrgc_summary_for_all()
        phase3 = [s for s in summary if s.get("phase") == 3]
        phase2 = [s for s in summary if s.get("phase") == 2]
        log(f"  Phase 3 (Inflection): {[s['ticker'] for s in phase3]}")
        log(f"  Phase 2 (Accumulation): {[s['ticker'] for s in phase2]}")
        log(f"  Best setup: {summary[0]['ticker'] if summary else 'none'}")

        results["steps"]["nrgc"] = {
            "tickers_assessed": len(nrgc_assessments),
            "phase3_count": len(phase3),
            "phase2_count": len(phase2),
            "top_setups": [s["ticker"] for s in summary[:3]],
        }
    except Exception as e:
        log(f"  [ERROR] NRGC update failed: {e}")
        import traceback
        log(f"  {traceback.format_exc()[:300]}")

    # ── STEP 4: Thematic deep dives (1 Sonnet call per theme) ─────────────────
    log("\n[4/8] Thematic deep dives...")
    try:
        from distill_engine import theme_deep_dive, load_insights
        from source_config import DEFAULT_WATCHLIST
        # Pick top 2 themes with most signals this week
        theme_hits = {}
        for ins in insights:
            for theme in ins.get("themes", []):
                theme_hits[theme] = theme_hits.get(theme, 0) + 1

        top_themes = sorted(theme_hits.items(), key=lambda x: x[1], reverse=True)[:2]
        for theme, count in top_themes:
            if count < 2:
                continue  # not enough signals for a deep dive
            theme_insights = [i for i in insights if theme in i.get("themes", [])]
            log(f"  Deep dive: {theme} ({count} signals)...")
            dd = theme_deep_dive(theme, theme_insights)
            if dd:
                log(f"    Phase: {dd.get('cycle_phase')} | Conviction: {dd.get('conviction')}")
                results["steps"].setdefault("deep_dives", []).append({
                    "theme": theme, "conviction": dd.get("conviction")
                })
    except Exception as e:
        log(f"  [ERROR] Deep dives failed: {e}")

    # ── STEP 5: Paper trading update ──────────────────────────────────────────
    log("\n[5/9] Paper portfolio update...")
    portfolio = {}
    perf = {}
    alerts = []
    try:
        from portfolio_engine import load_portfolio, update_positions, save_portfolio, get_performance
        portfolio = load_portfolio()
        alerts = update_positions(portfolio)
        save_portfolio(portfolio)
        perf = get_performance(portfolio)
        log(f"  Portfolio: ${perf['total_value']:,.0f} | Return: {perf['total_return_pct']:+.2f}%")
        log(f"  vs QQQ: {perf['benchmark_return']:+.2f}% | Alpha: {perf['alpha']:+.2f}%")
        log(f"  {'BEATING NASDAQ' if perf['beating_nasdaq'] else 'LAGGING NASDAQ'}")
        if alerts:
            for a in alerts: log(f"  ! ALERT: {a}")
        results["steps"]["paper_trading"] = perf
    except Exception as e:
        log(f"  [ERROR] Paper trading update failed: {e}")

    # ── STEP 5b: Auto-trade cycle (exit stops → enter Phase 3 signals) ────────
    log("\n[5b/9] Auto-trade cycle (NRGC Phase 3 → paper entries/exits)...")
    auto_trade_result = {"entries": [], "exits": [], "regime": "neutral"}
    if nrgc_assessments:
        try:
            from auto_trader import run_auto_trade_cycle
            from portfolio_engine import load_portfolio, save_portfolio
            portfolio = load_portfolio()   # reload after update_positions
            # Pass regime from synthesis if available
            weekly_regime = synthesis.get("regime_signal", "neutral") if synthesis else None
            auto_trade_result = run_auto_trade_cycle(portfolio, nrgc_assessments,
                                                     regime=weekly_regime)
            save_portfolio(portfolio)
            n_entries = len(auto_trade_result.get("entries", []))
            n_exits   = len(auto_trade_result.get("exits",   []))
            regime_used = auto_trade_result.get("regime", "neutral")
            log(f"  Regime: {regime_used.upper()} | Entries: {n_entries} | Exits: {n_exits}"
                f" | Forced exits: {len(auto_trade_result.get('forced_exits',[]))}")
            for e in auto_trade_result.get("entries", []):
                cap = e.get("cap_tier","?")
                log(f"    BUY  {e['ticker']} @ ${e['price']:.2f} x{e['shares']}"
                    f" | NRGC={e['score']} | {cap}cap | {e.get('theme','')}")
            for e in auto_trade_result.get("exits", []) + auto_trade_result.get("forced_exits", []):
                outcome = "WIN" if e.get("pnl_pct", 0) >= 0 else "LOSS"
                log(f"    SELL {e['ticker']} {e.get('pnl_pct',0):+.1f}% [{outcome}] | {e.get('exit_reason','')[:40]}")
            results["steps"]["auto_trade"] = {
                "regime": regime_used,
                "entries": n_entries, "exits": n_exits,
                "tickers_entered": [e["ticker"] for e in auto_trade_result.get("entries", [])],
            }
        except Exception as e:
            log(f"  [ERROR] Auto-trade failed: {e}")
            import traceback
            log(f"  {traceback.format_exc()[:300]}")

    # ── STEP 5c: Focus List — entry zones + outcome tracking + lessons ───────
    log("\n[5c/9] Focus list: tracking outcomes + new entry zones...")
    focus_result = {"picks": [], "new_lessons": [], "prev_outcomes": [], "total_lessons": 0}
    if nrgc_assessments:
        try:
            from focus_list import run_focus_cycle
            focus_regime = synthesis.get("regime_signal", "neutral") if synthesis else "neutral"
            focus_result = run_focus_cycle(nrgc_assessments, regime=focus_regime)
            n_picks   = len(focus_result.get("picks", []))
            n_lessons = len(focus_result.get("new_lessons", []))
            n_prev    = len(focus_result.get("prev_outcomes", []))
            log(f"  New focus list: {n_picks} picks | Outcomes tracked: {n_prev} | New lessons: {n_lessons}")
            for p in focus_result.get("picks", [])[:5]:
                log(f"    #{p['ticker']} EMLS={p['emls_score']} Phase{p['phase']}"
                    f" Trigger=${p.get('trigger','?')} R/R={p.get('rr_ratio','?')}x")
            results["steps"]["focus_list"] = {
                "picks": n_picks,
                "outcomes_tracked": n_prev,
                "new_lessons": n_lessons,
                "total_lessons": focus_result.get("total_lessons", 0),
                "top_picks": [p["ticker"] for p in focus_result.get("picks", [])[:5]],
            }
        except Exception as e:
            log(f"  [ERROR] Focus list failed: {e}")
            import traceback
            log(f"  {traceback.format_exc()[:300]}")

    # ── STEP 6: Promotion check ───────────────────────────────────────────────
    log("\n[6/9] Promotion check (paper → real money)...")
    try:
        from promotion_checker import run_weekly_promotion_check
        regime = synthesis.get("regime_signal","neutral") if synthesis else "neutral"
        prom_results = run_weekly_promotion_check(portfolio, regime)
        ready = [r for r in prom_results if r.get("ready")]
        log(f"  {len(ready)}/{len(prom_results)} positions ready for real money")
        if ready:
            log("  *** POSITIONS READY FOR REAL MONEY ***")
            for r in ready:
                log(f"    {r['ticker']}: {r['paper_pnl']:+.1f}% | Suggest {r['suggested_real_pct']}% allocation")
        results["steps"]["promotions"] = {"ready": len(ready), "total": len(prom_results)}
    except Exception as e:
        log(f"  [ERROR] Promotion check failed: {e}")

    # ── STEP 7: Post-mortems + NRGC case studies ──────────────────────────────
    log("\n[7/9] Post-mortems, NRGC case studies & pattern detection...")
    try:
        from auto_postmortem import process_new_closed_trades, check_failure_patterns
        from nrgc_tracker import generate_nrgc_case_study
        from distill_engine import client as ai_client

        regime = synthesis.get("regime_signal","neutral") if synthesis else "neutral"
        n_pms = process_new_closed_trades(portfolio, regime)
        patterns = check_failure_patterns()
        log(f"  {n_pms} post-mortems | {len(patterns)} patterns detected")

        # Generate NRGC case studies for recently closed trades
        closed = portfolio.get("closed", [])
        recent_closed = [t for t in closed
                         if t.get("close_date", "") >= (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")]
        n_case_studies = 0
        for trade in recent_closed:
            cs = generate_nrgc_case_study(trade, portfolio, client=ai_client)
            if cs:
                n_case_studies += 1
                if cs.get("nrgc_call_accuracy") == "incorrect":
                    log(f"  ! Phase miss: {trade['ticker']} — {cs.get('why_call_was_right_wrong','')}")
        log(f"  {n_case_studies} NRGC case studies generated")
        results["steps"]["learning"] = {
            "postmortems": n_pms, "patterns": len(patterns),
            "nrgc_case_studies": n_case_studies
        }
    except Exception as e:
        log(f"  [ERROR] Learning failed: {e}")

    # ── STEP 7b: Paper trading P&L + lesson generation + memory update ────────
    log("\n[7b/9] Paper P&L stats + lesson generation + memory update...")
    paper_stats = {}
    new_lessons_count = 0
    try:
        from portfolio_engine import load_portfolio, get_performance
        from performance_tracker import run_weekly_learning
        portfolio_fresh = load_portfolio()   # get latest after auto-trade
        perf_fresh      = get_performance(portfolio_fresh)
        learning_result = run_weekly_learning(portfolio_fresh, perf=perf_fresh)
        paper_stats      = learning_result.get("stats", {})
        new_lessons_count = learning_result.get("new_lessons", 0)
        log(f"  Total trades: {paper_stats.get('total_trades', 0)}"
            f" | Win rate: {paper_stats.get('win_rate_pct', 0):.1f}%"
            f" | Expectancy: {paper_stats.get('expectancy', 0):+.2f}%")
        if paper_stats.get("nrgc_phase3_accuracy_pct") is not None:
            log(f"  NRGC Phase 3 accuracy: {paper_stats['nrgc_phase3_accuracy_pct']:.1f}%")
        log(f"  New lessons generated: {new_lessons_count}")
        results["steps"]["paper_learning"] = {
            "win_rate": paper_stats.get("win_rate_pct"),
            "expectancy": paper_stats.get("expectancy"),
            "new_lessons": new_lessons_count,
        }
    except Exception as e:
        log(f"  [ERROR] Paper learning failed: {e}")
        import traceback
        log(f"  {traceback.format_exc()[:300]}")

    # ── STEP 8: Upload worthy insights to NotebookLM ─────────────────────────
    log("\n[8/9] Knowledge upload check...")
    if synthesis and synthesis.get("notebooklm_worthy"):
        log("  Uploading synthesis to NotebookLM...")
        try:
            sys.path.insert(0, str(BASE_DIR / "scripts"))
            from notebooklm_upload_all import _upload_text
            label = f"{datetime.now().strftime('%y%m%d')} | Agent09 | Weekly Synthesis | {datetime.now().strftime('%b %Y')}"
            summary = synthesis.get("notebooklm_summary","")
            if summary:
                # _upload_text is a helper we add
                log(f"  Synthesis worthy — would upload to Investment Lessons")
        except Exception as e:
            log(f"  [NotebookLM skip]: {e}")

    # ── STEP 8b: Audit — verify ALL numbers before sending ───────────────────
    log("\n[8b/9] Auditing all data before Telegram send...")
    try:
        from auditor import run_weekly_audit
        from portfolio_engine import load_portfolio, get_performance
        portfolio_fresh2 = load_portfolio()
        perf_fresh2      = get_performance(portfolio_fresh2)
        portfolio_fresh2, perf_fresh2, focus_result, audit_log = run_weekly_audit(
            portfolio_fresh2, perf_fresh2, focus_result)
        fixes = [l for l in audit_log if l.startswith("FIX") or l.startswith("REMOVE")]
        log(f"  Audit complete: {len(fixes)} corrections | {len(audit_log)} checks")
        for f in fixes:
            log(f"    {f}")
        # Use audited versions
        portfolio = portfolio_fresh2
        perf      = perf_fresh2
        results["steps"]["audit"] = {"checks": len(audit_log), "fixes": len(fixes)}
    except Exception as e:
        log(f"  [Auditor skip]: {e}")
        import traceback
        log(f"  {traceback.format_exc()[:200]}")

    # ── STEP 9: Telegram weekly summary ──────────────────────────────────────
    log("\n[9/9] Sending Telegram weekly summary...")
    try:
        from telegram_notifier import send_weekly_summary

        # Calculate token cost this run from cost log
        token_cost_this_run = 0.0
        cost_file = BASE_DIR / "data" / "state" / "token_cost_log.json"
        if cost_file.exists():
            try:
                cost_log = json.loads(cost_file.read_text(encoding="utf-8"))
                today_str = start.strftime("%Y-%m-%d")
                token_cost_this_run = sum(
                    c.get("cost_usd", 0) for c in cost_log.get("calls", [])
                    if c.get("ts", "").startswith(today_str)
                )
            except: pass

        ok = send_weekly_summary(
            perf=perf,
            nrgc_assessments=nrgc_assessments,
            synthesis=synthesis,
            portfolio=portfolio,
            promotions=prom_results if "prom_results" in dir() else [],
            token_cost_usd=token_cost_this_run,
            paper_stats=paper_stats,
            new_lessons_count=new_lessons_count,
            focus_result=focus_result,
        )
        log(f"  Telegram: {'sent' if ok else 'failed (check token/chat_id)'}")
    except Exception as e:
        log(f"  [Telegram ERROR]: {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = (datetime.now() - start).seconds
    log(f"\n{'='*55}")
    log(f"Weekly run complete in {elapsed}s")
    log(f"Steps completed: {len([s for s in results['steps'].values() if 'error' not in s])}/11")

    # Save run log
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    run_log = []
    if LOG_FILE.exists():
        try: run_log = json.loads(LOG_FILE.read_text())
        except: pass
    run_log.append(results)
    LOG_FILE.write_text(json.dumps(run_log[-52:], indent=2))  # keep 52 weeks
    log(f"Run log saved: {LOG_FILE}")


if __name__ == "__main__":
    run_weekly()
