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

    # ── STEP 0: Smart Signals — zero-token intelligence layer ─────────────────
    # Run FIRST: FRED regime + insider clusters + XBRL revenue + volume anomalies
    # All free, all zero tokens — enriches every downstream step
    log("\n[0/9] Smart Signals — zero-token intelligence (FRED/Insider/XBRL/Volume)...")
    smart_data = {
        "fred_regime":    {"regime": "neutral", "regime_score": 0},
        "enrichment":     {},
        "insider_signals": {},
        "xbrl_financials": {},
        "volume_anomalies": {},
        "alerts_8k":      [],
        "summary": {"regime": "neutral", "regime_score": 0,
                    "insider_clusters": [], "volume_positive": [],
                    "xbrl_accel": [], "alerts_count": 0},
    }
    try:
        sys.path.insert(0, str(BASE_DIR / "scripts" / "learning"))
        from smart_signals import run_smart_signals
        from source_config import DEFAULT_WATCHLIST as _wl
        _all_tickers = list({t for tickers in _wl.values() for t in tickers})
        smart_data = run_smart_signals(
            watchlist_tickers=_all_tickers,
            nrgc_tickers=None,   # Phase 2-4 tickers unknown yet — use full watchlist
        )
        _sm = smart_data.get("summary", {})
        log(f"  FRED Regime: {_sm.get('regime','?')} (score {_sm.get('regime_score',0):+d})")
        log(f"  Insider clusters: {_sm.get('insider_clusters','none') or 'none'}")
        log(f"  Volume positive: {(_sm.get('volume_positive') or [])[:5] or 'none'}")
        log(f"  XBRL accelerating rev: {(_sm.get('xbrl_accel') or [])[:5] or 'none'}")
        log(f"  8-K alerts: {_sm.get('alerts_count',0)}")
        results["steps"]["smart_signals"] = _sm
    except Exception as e:
        log(f"  [ERROR] Smart signals: {e}")
        import traceback
        log(f"  {traceback.format_exc()[:300]}")

    # ── STEP 0b: Edge Intelligence — Alternative Data + Specialized Edge ────────
    # Zero-token sector-specific leading indicators: Semi cycle, AI capex, HBM,
    # Defense flow, Nuclear catalysts, DC Power + Google Trends + Short Interest
    log("\n[0b/9] Edge Intelligence (AltData + SpecializedEdge + NarrativeTracker)...")
    alt_data_result  = {"google_trends": {}, "short_interest": {}, "nrgc_theme_boosts": {}, "nrgc_ticker_boosts": {}}
    edge_result      = {"nrgc_theme_boosts": {}, "semiconductor": {}, "ai_capex": {}, "hbm_memory": {}, "defense": {}, "nuclear": {}, "data_center_power": {}}
    narrative_result = {"nrgc_boosts": {}, "top_accelerating": [], "super_narratives": {}}
    try:
        from alternative_data  import run_alternative_data, get_altdata_telegram_line
        from specialized_edge  import run_specialized_edge, get_edge_telegram_lines
        from narrative_tracker import run_narrative_tracker, get_narrative_telegram_line
        # Use full watchlist tickers for short interest screen
        try:
            from source_config import DEFAULT_WATCHLIST as _wl2
            _si_tickers = list({t for tickers in _wl2.values() for t in tickers})
        except Exception:
            _si_tickers = []
        alt_data_result  = run_alternative_data(tickers=_si_tickers)
        edge_result      = run_specialized_edge()
        # Narrative tracker runs BEFORE scraping to catch prior-week data too
        narrative_result = run_narrative_tracker()
        log(f"  AltData: {get_altdata_telegram_line(alt_data_result)}")
        log(f"  Narrative: {get_narrative_telegram_line(narrative_result)}")
        _top_edge = edge_result.get("top_signals", [])[:2]
        log(f"  Edge signals: {[(sig, f'{boost:+d}') for sig, boost, _ in _top_edge]}")
        results["steps"]["edge_intelligence"] = {
            "alt_data_boosts":  len(alt_data_result.get("nrgc_theme_boosts", {})),
            "edge_boosts":      len(edge_result.get("nrgc_theme_boosts", {})),
            "narrative_boosts": len(narrative_result.get("nrgc_boosts", {})),
            "squeeze_candidates": len(alt_data_result.get("short_interest", {}).get("squeeze_candidates", [])),
        }
    except Exception as e:
        log(f"  [Edge Intelligence skip]: {e}")

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

    # ── STEP 2b: Earnings Tone Analysis — zero/near-zero token ───────────────
    log("\n[2b/9] Earnings tone analysis (keyword + optional LLM)...")
    tone_results = {}
    try:
        from earnings_tone import analyze_earnings_batch
        # Build transcripts from insights that contain earnings/transcript text
        _transcripts = {}
        for ins in insights:
            tkr = ins.get("ticker", "")
            txt = ins.get("transcript", ins.get("content", ins.get("summary", "")))
            if tkr and len(txt) > 200 and ins.get("source_type") in ("earnings", "transcript", "quartr"):
                if tkr not in _transcripts:
                    _transcripts[tkr] = {"current": txt}
                else:
                    _transcripts[tkr]["prior"] = txt   # second entry = prior quarter
        # Also check raw earnings text files
        _raw_transcripts_dir = BASE_DIR / "data" / "raw"
        if _raw_transcripts_dir.exists():
            for f in sorted(_raw_transcripts_dir.glob("transcript_*.txt"))[-10:]:
                try:
                    tkr = f.stem.split("_")[1].upper()
                    txt = f.read_text(encoding="utf-8", errors="replace")
                    if tkr and len(txt) > 200:
                        _transcripts.setdefault(tkr, {})["current"] = txt[:6000]
                except Exception:
                    pass
        if _transcripts:
            tone_results = analyze_earnings_batch(_transcripts, use_llm=False)
            positive_signals = [(t, d["nrgc_signal"]) for t, d in tone_results.items()
                                if d.get("combined_nrgc_boost", 0) > 2]
            log(f"  Tone analyzed: {len(tone_results)} tickers | "
                f"Phase-change signals: {[t for t, _ in positive_signals[:3]]}")
            results["steps"]["earnings_tone"] = {
                "tickers": len(tone_results),
                "phase_signals": len(positive_signals),
            }
        else:
            log("  No transcript data available this week — skip")
    except Exception as e:
        log(f"  [Earnings tone skip]: {e}")

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

        # ── Merge ALL intelligence layers into NRGC assessments ─────────────
        _enrichment   = smart_data.get("enrichment", {})
        _enrich_count = 0
        for _t, _assessment in nrgc_assessments.items():
            _theme = _assessment.get("theme", "")
            _boost = 0
            _signals_applied = []

            # --- Layer 1: Smart Signals (FRED/Insider/XBRL/Volume) ---
            _e = _enrichment.get(_t, {})
            if _e:
                _assessment["smart_signals"] = _e
                if _e.get("insider_cluster"):
                    _boost += 5; _signals_applied.append("insider_cluster")
                if _e.get("xbrl_accel"):
                    _boost += 3; _signals_applied.append("xbrl_revenue_accel")
                if any("52W_HIGH" in str(s) for s in _e.get("vol_signals", [])):
                    _boost += 3; _signals_applied.append("52w_breakout")
                if any("POCKET_PIVOT" in str(s) for s in _e.get("vol_signals", [])):
                    _boost += 2; _signals_applied.append("pocket_pivot")

            # --- Layer 2: Alternative Data (Google Trends + Short Interest) ---
            _alt_ticker_boost = alt_data_result.get("nrgc_ticker_boosts", {}).get(_t, 0)
            _alt_theme_boost  = alt_data_result.get("nrgc_theme_boosts", {}).get(_theme, 0)
            if _alt_ticker_boost:
                _boost += _alt_ticker_boost; _signals_applied.append("squeeze_setup")
            if _alt_theme_boost:
                _boost += _alt_theme_boost
                _trends_sig = alt_data_result.get("google_trends", {}).get(_theme, {}).get("nrgc_signal", "")
                _signals_applied.append(f"trends:{_trends_sig}")

            # --- Layer 3: Specialized Edge (Semi/Capex/HBM/Defense/Nuclear/DCPower) ---
            _edge_boost = edge_result.get("nrgc_theme_boosts", {}).get(_theme, 0)
            if _edge_boost:
                _boost += min(abs(_edge_boost), 5) * (1 if _edge_boost > 0 else -1)
                _signals_applied.append(f"edge:{_theme.lower().replace(' ', '_')}")

            # --- Layer 4: Narrative Tracker ---
            _narr_boost = narrative_result.get("nrgc_boosts", {}).get(_theme, 0)
            if _narr_boost:
                _boost += _narr_boost; _signals_applied.append("narrative_accel")

            # --- Layer 5: Earnings Tone ---
            _tone_boost = tone_results.get(_t, {}).get("combined_nrgc_boost", 0)
            if _tone_boost:
                _boost += _tone_boost; _signals_applied.append("earnings_tone")

            if _boost:
                _assessment["emls_boost"] = _assessment.get("emls_boost", 0) + _boost
                _assessment["edge_signals"] = _signals_applied
            _enrich_count += 1

        if _enrich_count:
            _top_boosted = sorted(
                [(t, a.get("emls_boost", 0)) for t, a in nrgc_assessments.items()
                 if a.get("emls_boost", 0) > 0],
                key=lambda x: -x[1]
            )[:5]
            log(f"  Enriched {_enrich_count} tickers | Top boosts: {_top_boosted}")
        # ─────────────────────────────────────────────────────────────────────

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
            # Pass regime: LLM synthesis first, FRED rule-based as fallback
            _fred_regime = smart_data.get("summary", {}).get("regime", "neutral")
            weekly_regime = (synthesis.get("regime_signal", _fred_regime)
                             if synthesis else _fred_regime)
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
            _fred_regime2 = smart_data.get("summary", {}).get("regime", "neutral")
            focus_regime = (synthesis.get("regime_signal", _fred_regime2)
                            if synthesis else _fred_regime2)
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

    # ── STEP 7d: Indicator Learning — TradingView + GitHub + Reddit + QuantConnect
    log("\n[7d/9] Indicator knowledge base — weekly discovery cycle...")
    indicator_result = {"new_items_added": 0, "total_kb_size": 0, "top_new_items": []}
    try:
        from indicator_learner import run_indicator_discovery
        from portfolio_engine import load_portfolio as _lp2
        _port_for_indicators = _lp2() if "portfolio" not in dir() else portfolio
        indicator_result = run_indicator_discovery(portfolio=_port_for_indicators)
        log(f"  Scraped: {indicator_result.get('raw_scraped',0)} | "
            f"New: {indicator_result.get('new_items_added',0)} | "
            f"KB total: {indicator_result.get('total_kb_size',0)} | "
            f"Novel: {indicator_result.get('novel_count',0)}")
        for it in indicator_result.get("top_new_items",[])[:3]:
            log(f"    + {it.get('name','')} [{it.get('source','')} | "
                f"score={it.get('quality_score',0)}]")
        results["steps"]["indicator_learning"] = {
            "raw_scraped":     indicator_result.get("raw_scraped", 0),
            "new_added":       indicator_result.get("new_items_added", 0),
            "total_kb_size":   indicator_result.get("total_kb_size", 0),
            "novel_count":     indicator_result.get("novel_count", 0),
        }
    except Exception as e:
        log(f"  [ERROR] Indicator learning: {e}")
        import traceback
        log(f"  {traceback.format_exc()[:300]}")

    # ── STEP 7c: Research Memory Loop — source quality + thesis + knowledge base
    log("\n[7c/9] Research team memory loop (read/write/analyze/store)...")
    research_memory_result = {}
    try:
        from research_memory import run_research_memory_loop
        research_memory_result = run_research_memory_loop(
            raw_items=raw_items if "raw_items" in dir() else {},
            insights=insights,
            synthesis=synthesis,
            nrgc_assessments=nrgc_assessments,
            portfolio=portfolio,
            focus_result=focus_result,
        )
        log(f"  Top source: {research_memory_result.get('top_source','?')}"
            f" | Theses verified: {research_memory_result.get('theses_verified',0)}"
            f" | Accuracy: {research_memory_result.get('thesis_accuracy','N/A')}")
        log(f"  KB: {research_memory_result.get('kb_tickers',0)} tickers"
            f" | +{research_memory_result.get('kb_added',0)} facts this week")
        results["steps"]["research_memory"] = research_memory_result
    except Exception as e:
        log(f"  [ERROR] Research memory: {e}")
        import traceback
        log(f"  {traceback.format_exc()[:300]}")

    # ── STEP 8a: Agent Memory Loop — verify all agent calls + write lessons ──
    log("\n[8a/9] Agent memory loop (6 agents learning this week)...")
    agent_memory_result = {}
    try:
        sys.path.insert(0, str(BASE_DIR / "scripts" / "learning"))
        from agent_memory_loop import run_agent_memory_loop
        agent_memory_result = run_agent_memory_loop(
            synthesis=synthesis,
            nrgc_assessments=nrgc_assessments,
            auto_trade_result=auto_trade_result,
            focus_result=focus_result,
            portfolio=portfolio,
            audit_log=results.get("steps", {}).get("audit", {}).get("log", []),
        )
        n_verified = len(agent_memory_result.get("verified_agents", []))
        n_lessons  = agent_memory_result.get("lessons_generated", 0)
        updated    = agent_memory_result.get("agents_updated", [])
        log(f"  Verified: {n_verified} agents | Lessons: {n_lessons} | Updated: {updated}")
        results["steps"]["agent_memory"] = {
            "verified": n_verified,
            "lessons": n_lessons,
            "agents_updated": updated,
        }
    except Exception as e:
        log(f"  [ERROR] Agent memory loop: {e}")
        import traceback
        log(f"  {traceback.format_exc()[:300]}")

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

    # ── STEP 8c: Lifetime stats update ───────────────────────────────────────
    log("\n[8c/9] Updating lifetime stats...")
    lifetime_stats = {}
    try:
        from lifetime_tracker import update_weekly, get_lifetime_telegram_block
        from portfolio_engine import load_portfolio as _lp3, get_performance as _gp3
        _pf3  = _lp3()
        _perf3 = _gp3(_pf3)
        lifetime_stats = update_weekly(
            perf=_perf3,
            portfolio=_pf3,
            focus_result=focus_result,
            indicator_result=indicator_result,
            research_result=research_memory_result,
            agent_result=agent_memory_result,
            lessons_count=new_lessons_count,
        )
        age_days = lifetime_stats.get("runs", {}).get("weekly", 0)
        total_kb = lifetime_stats.get("knowledge", {}).get("indicator_kb_size", 0)
        total_les = lifetime_stats.get("knowledge", {}).get("total_lessons", 0)
        log(f"  Weekly run #{age_days} | KB: {total_kb} | Lessons: {total_les}")
        results["steps"]["lifetime"] = {
            "weekly_run": age_days,
            "kb_size":    total_kb,
            "lessons":    total_les,
        }
    except Exception as e:
        log(f"  [Lifetime tracker]: {e}")

    # ── STEP 8d: Obsidian Knowledge Base update ──────────────────────────────
    log("\n[8d/9] Obsidian knowledge base update (ticker + theme notes)...")
    obsidian_written = 0
    try:
        sys.path.insert(0, str(BASE_DIR / "scripts" / "utils"))
        from obsidian_writer import (write_ticker_note, append_theme_signal,
                                     write_daily_note, _is_available)
        if _is_available():
            # Write/update each ticker note with latest NRGC assessment
            for ticker, assessment in (nrgc_assessments or {}).items():
                ok = write_ticker_note(ticker, assessment)
                if ok:
                    obsidian_written += 1
            # Append weekly edge signals to theme notes
            theme_boosts = edge_result.get("nrgc_theme_boosts", {})
            for theme, boost in theme_boosts.items():
                if boost != 0:
                    sig = f"Edge signal {boost:+d} NRGC | {datetime.now().strftime('%Y-W%W')}"
                    append_theme_signal(theme, sig)
            # Write daily summary note
            daily_content = f"""# Weekly Brief {start.strftime('%Y-%m-%d')}

## Portfolio
- NAV: ${perf.get('total_value', 0):,.0f} | Return: {perf.get('total_return_pct', 0):+.2f}%
- vs QQQ: {perf.get('alpha', 0):+.2f}% alpha

## Top NRGC Signals
{chr(10).join(f"- {t}: Phase {a.get('nrgc_phase','?')} | EMLS {a.get('emls_score','?')}" for t, a in list((nrgc_assessments or {}).items())[:5])}

## Edge Intelligence
- {get_edge_telegram_lines(edge_result) if 'get_edge_telegram_lines' in dir() else 'N/A'}
"""
            write_daily_note(daily_content)
            log(f"  Obsidian: {obsidian_written} ticker notes written + theme signals + daily note")
        else:
            log("  Obsidian REST API not running (skip — start Obsidian to enable)")
        results["steps"]["obsidian"] = {"tickers_written": obsidian_written}
    except Exception as e:
        log(f"  [Obsidian skip]: {e}")

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
            agent_memory_result=agent_memory_result,
            research_memory_result=research_memory_result,
            indicator_result=indicator_result,
            lifetime_stats=lifetime_stats,
            alt_data_result=alt_data_result,
            edge_result=edge_result,
            narrative_result=narrative_result,
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
