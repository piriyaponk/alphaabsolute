"""
AlphaAbsolute v2 -- Master Daily Runner
========================================
Chains all 12 agents in the correct dependency order per CLAUDE.md schedule.

Modes:
  premarket  (default)  6:00-9:00 AM -- Foundation → Intelligence → Curation → Execution → Output
  eod                   4:30 PM     -- Post-mortem + EOD price update
  monthly               1st of month -- Bayesian calibration + performance report

Usage:
  python scripts/runners/pre_market_runner.py                    # full premarket run
  python scripts/runners/pre_market_runner.py --mode eod         # EOD update only
  python scripts/runners/pre_market_runner.py --mode monthly     # monthly reports
  python scripts/runners/pre_market_runner.py --step a01         # single step
  python scripts/runners/pre_market_runner.py --dry-run          # check deps only

Dependency order (CLAUDE.md §Daily Automation Schedule):
  PRE-MARKET (6:00 AM)   A01 market_regime.py → A02 macro_monitor.py
  MORNING    (7:00 AM)   A03 rs_benchmark.py [Fri] → rs_ranker.py → rs_theme_ranker.py
                              prewarm_analyst_cache.py [Tue]
  CURATION   (8:00 AM)   A06 trend_template_screener.py → A07 monster_scout.py
  EXECUTION  (8:30 AM)   A08 setup_scanner.py → A09 portfolio_manager.py → A10 risk_guardian.py
  OUTPUT     (9:00 AM)   A11 report_writer.py
  EOD        (4:30 PM)   A12 auto_postmortem.py → A09 portfolio_manager.py --mode eod
  MONTHLY    (1st)       A12 framework_calibrator.py → A12 performance_tracker.py

Windows Task Scheduler:
  Action: python C:\\...\\AlphaAbsolute\\scripts\\runners\\pre_market_runner.py
  Trigger: Daily at 6:00 AM (premarket) + 4:30 PM (--mode eod)
"""

from __future__ import annotations
import sys
import os
import json
import time
import argparse
import importlib.util
from datetime import datetime, date
from pathlib import Path

# Fix encoding for Thai terminals (cp874 cannot handle Unicode)
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and _stream.encoding and \
            _stream.encoding.lower() in ("cp874", "cp1252", "ascii"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[2]
LOG_DIR  = BASE_DIR / "data" / "runner_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))


# ── Load .env ─────────────────────────────────────────────────────────────────
def _load_env() -> None:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for ln in env_path.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()


# ── Pipeline Step Registry ────────────────────────────────────────────────────
#
# Each step:
#   id              unique short name (used with --step)
#   layer           display grouping
#   name            human-readable name
#   module          dotted Python module path from BASE_DIR
#   func            function to call  (must accept no args)
#   output          primary output file (relative to BASE_DIR) — used for dry-run
#   desc            one-line description
#   critical        if True and step fails → abort pipeline
#   modes           which run modes include this step (premarket / eod / monthly)
#   friday_only     only runs on Fridays when in premarket mode
#   tuesday_only    only runs on Tuesdays when in premarket mode
#   skip_if_missing if True, quietly skip when script file does not yet exist
#   kwargs          extra keyword args to pass (step must accept **kwargs)

STEPS: list[dict] = [
    # ── Step 0: System Health Check (always first — surfaces problems early) ──
    {
        "id":       "health_check",
        "layer":    "0-Diagnostics",
        "name":     "System Health Check",
        "module":   "scripts.diagnostics.health_check",
        "func":     "run_with_heal",         # auto-heal: silent fix → re-check → print once
        "output":   "data/health/health_report.json",
        "desc":     "Full pipeline diagnostic + auto-heal: data freshness, DB schema, API keys, imports",
        "critical": False,   # warn but don't abort if check finds issues
        "modes":    ["premarket", "eod"],
    },

    # ── OHLCV Update — MUST run FIRST so all downstream agents see fresh prices ──
    # Premarket (6 AM Bangkok = 11 PM UTC): fetches YESTERDAY's data (US closed 2h ago)
    # EOD (5 PM Bangkok = 10 AM UTC): catches any gaps; skips today (market still open)
    # critical=False: an OHLCV failure should NOT abort the entire pipeline — better to run
    # regime/RS/screener on yesterday's prices than to skip the brief entirely.
    # Telegram alert still fires on any step failure (see _send_pipeline_alert).
    {
        "id":       "ohlcv_update",
        "layer":    "0-Data",
        "name":     "OHLCV Bulk Update (Polygon Grouped)",
        "module":   "scripts.pre_compute.update_ohlcv_bulk",
        "func":     "run",
        "output":   "data/ohlcv.db",
        "desc":     "1 Polygon grouped call → ALL tickers in <10s. Premarket=fresh data for analysis; EOD=gap fill.",
        "critical": False,
        "modes":    ["premarket", "eod"],
    },
    {
        "id":       "fix_volumes",
        "layer":    "0-Data",
        "name":     "Fix Float Volumes",
        "module":   "scripts.pre_compute.fix_dates_volumes",
        "func":     "run",
        "output":   "data/ohlcv.db",
        "desc":     "Convert float volumes to INTEGER in ohlcv.db after Polygon grouped insert",
        "critical": False,
        "modes":    ["premarket", "eod"],
    },

    # ── Pre-Market Data Prep ──────────────────────────────────────────────────
    {
        "id":       "earnings_cal",
        "layer":    "0-Data",
        "name":     "Earnings Calendar Fetch",
        "module":   "scripts.pre_compute.fetch_earnings_calendar",
        "func":     "run",
        "output":   "data/regime/earnings_next30.json",
        "desc":     "FMP bulk earnings calendar -> earnings_calendar table + within_5td gate file",
        "critical": False,
        "modes":    ["premarket"],
    },
    {
        "id":       "data_quality",
        "layer":    "0-Data",
        "name":     "Data Quality Check",
        "module":   "scripts.pre_compute.data_quality",
        "func":     "run",
        "output":   "data/quality/quality_latest.json",
        "desc":     "10-check data health audit: dates, volumes, coverage, freshness",
        "critical": False,
        "modes":    ["premarket"],
    },

    # ── Layer 0: Foundation ───────────────────────────────────────────────────
    {
        "id":       "a01",
        "layer":    "0-Foundation",
        "name":     "A01 Market Health Engine",
        "module":   "scripts.pre_compute.market_regime",
        "func":     "run",
        "output":   "data/regime/market_health.json",
        "desc":     "4-state regime + cash floor + TD signals — gates all downstream agents",
        "critical": True,
        "modes":    ["premarket"],
    },
    {
        "id":       "a01b",
        "layer":    "0-Foundation",
        "name":     "A01b Full-Market Breadth (Polygon)",
        "module":   "scripts.pre_compute.fetch_market_breadth",
        "func":     "run",
        "output":   "data/breadth/market_breadth_history.json",
        "desc":     "BOA-004-A7: fetch yesterday full-market NH/NL from Polygon grouped daily (~15s)",
        "critical": False,
        "modes":    ["premarket"],
    },
    {
        "id":       "a02",
        "layer":    "0-Foundation",
        "name":     "A02 Macro Monitor",
        "module":   "scripts.pre_compute.macro_monitor",
        "func":     "run",
        "output":   "data/regime/macro_state.json",
        "desc":     "FRED yields + credit spread + yield curve → macro_modifier",
        "critical": False,
        "modes":    ["premarket"],
    },

    # ── Layer 1: Intelligence ─────────────────────────────────────────────────
    {
        "id":          "a03_bench",
        "layer":       "1-Intelligence",
        "name":        "A03 RS Benchmark Builder",
        "module":      "scripts.pre_compute.rs_benchmark",
        "func":        "build",
        "output":      "data/rs_universe/benchmark_distribution.json",
        "desc":        "Build S&P+Nasdaq market-wide RS distribution (Fridays only — ~5 min)",
        "critical":    False,
        "modes":       ["premarket"],
        "friday_only": True,
    },
    {
        "id":       "a03",
        "layer":    "1-Intelligence",
        "name":     "A03 RS Universe Ranker",
        "module":   "scripts.pre_compute.rs_ranker",
        "func":     "run",
        "output":   "data/rs_universe/latest.json",
        "desc":     "RS percentile 1M/3M/6M/12M for 500+ tickers — foundation of Mode A screen",
        "critical": False,
        "modes":    ["premarket"],
    },
    {
        "id":       "a03c",
        "layer":    "1-Intelligence",
        "name":     "A03c RS Change Detector",
        "module":   "scripts.pre_compute.rs_change_detector",
        "func":     "run",
        "output":   "data/rs_universe/changes_today.json",
        "desc":     "Daily RS snapshot diff → Climbers (new leaders/breakouts) + Droppers (fallen/MA50 breaks)",
        "critical": False,
        "modes":    ["premarket"],
    },
    {
        "id":       "a05",
        "layer":    "1-Intelligence",
        "name":     "A05 Theme Intelligence (RS Theme Ranker)",
        "module":   "scripts.pre_compute.rs_theme_ranker",
        "func":     "run",
        "output":   "data/rs_universe/theme_rs_latest.json",
        "desc":     "14-theme HOT/WARM/WEAK heatmap + Strong Leader Scores",
        "critical": False,
        "modes":    ["premarket"],
    },
    {
        "id":              "a04_prewarm",
        "layer":           "1-Intelligence",
        "name":            "A04 Analyst Cache Pre-Warm",
        "module":          "scripts.pre_compute.prewarm_analyst_cache",
        "func":            "run",
        "output":          "data/fundamentals/analyst_cache/",
        "desc":            "Batch-fetch analyst coverage counts for Discovery Index (Tuesdays only)",
        "critical":        False,
        "modes":           [],      # DISABLED: HTTP 402 on every request — API plan doesn't support endpoint. Re-enable when API upgraded.
        "tuesday_only":    True,
        "skip_if_missing": False,
    },

    # ── Data Enrichment (weekly scrapers — 7-day TTL, near-zero daily cost) ─────
    {
        "id":          "sa_fetcher",
        "layer":       "1-Intelligence",
        "name":        "StockAnalysis Fundamentals Fetcher",
        "module":      "scripts.pre_compute.stockanalysis_fetcher",
        "func":        "run",
        # FIX: use checkpoint file not ohlcv.db — ohlcv.db always exists so success was always
        # reported even on total failure. The fetcher writes this checkpoint on every run.
        "output":      "data/sa_fetcher_checkpoint.json",
        "desc":        "StockAnalysis.com: fill missing gm_latest, rev_yoy_q1, eps_yoy_pct (7-day TTL, skips fresh)",
        "critical":    False,
        "modes":       ["premarket"],
    },
    {
        "id":          "finviz_fetcher",
        "layer":       "1-Intelligence",
        "name":        "Finviz Market Cap Fetcher",
        "module":      "scripts.pre_compute.finviz_fetcher",
        "func":        "run",
        # FIX: use checkpoint file not ohlcv.db — same silent-success issue
        "output":      "data/finviz_fetcher_checkpoint.json",
        "desc":        "Finviz: fill missing ticker_meta.market_cap (7-day TTL, skips fresh)",
        "critical":    False,
        "modes":       ["premarket"],
    },

    # ── Layer 2: Curation ─────────────────────────────────────────────────────
    {
        "id":       "a06",
        "layer":    "2-Curation",
        "name":     "A06 Leadership Curator (Mode A)",
        "module":   "scripts.pre_compute.trend_template_screener",
        "func":     "run",
        "output":   "data/leadership/top30_watchlist.json",
        "desc":     "Full Mode A 5-gate screen → Top 30 Watchlist + Top 10 Active Leaders",
        "critical": False,
        "modes":    ["premarket"],
    },
    {
        "id":       "earnings_inflection",
        "layer":    "2-Curation",
        "name":     "Earnings Inflection Scout",
        "module":   "scripts.pre_compute.earnings_inflection_scout",
        "func":     "run",
        "output":   "data/bigshot/earnings_inflection.json",
        "desc":     "Revenue inflection scan — 270 themed tickers, FIRST_INFLECTION/TURNAROUND/ACCELERATING",
        "critical": False,
        "modes":    ["premarket"],
    },
    {
        "id":       "a07",
        "layer":    "2-Curation",
        "name":     "A07 Monster Scout (Mode B)",
        "module":   "scripts.pre_compute.monster_scout",
        "func":     "run",
        "output":   "data/bigshot/candidates.json",
        "desc":     "Mode B screen → Big Shot breakout candidates (max 5, breakout ≥63D high, inflection bonus)",
        "critical": False,
        "modes":    ["premarket"],
    },
    {
        "id":          "motw_selector",
        "layer":       "2-Curation",
        "name":        "Monster of the Week Selector",
        "module":      "scripts.pre_compute.monster_of_week_selector",
        "func":        "run",
        "output":      "data/bigshot/motw_selection.json",
        "desc":        "Phase 1 scoring (100 pts) → pick top Monster Scout candidate for deep research (Sundays)",
        "critical":    False,
        "modes":       ["premarket"],
        "sunday_only": True,
        "skip_if_missing": False,
    },
    {
        "id":          "motw_research",
        "layer":       "2-Curation",
        "name":        "Monster Deep Research + Telegram",
        "module":      "scripts.pre_compute.monster_deep_research",
        "func":        "run",
        "output":      "data/bigshot/",
        "desc":        "Claude Haiku plain-language 10x thesis + 3-message Telegram brief (Sundays)",
        "critical":    False,
        "modes":       ["premarket"],
        "sunday_only": True,
        "skip_if_missing": False,
    },

    # ── Layer 3: Execution ────────────────────────────────────────────────────
    {
        "id":       "a08",
        "layer":    "3-Execution",
        "name":     "A08 Setup Scanner",
        "module":   "scripts.pre_compute.setup_scanner",
        "func":     "run",
        "output":   "data/setups/setups_today.json",
        "desc":     "8 setup types (VCP/BKT/PPT/EMA/SPR/FIB) → entry/stop/target/RR for each candidate",
        "critical": False,
        "modes":    ["premarket"],
    },
    {
        "id":       "a09",
        "layer":    "3-Execution",
        "name":     "A09 Portfolio Manager (pre-market)",
        "module":   "scripts.portfolio.portfolio_manager",
        "func":     "run",
        "output":   "data/portfolio/action_signals.json",
        "desc":     "Position monitoring + stop checks + cash floor enforcement + paper auto-trade",
        "critical": False,
        "modes":    ["premarket"],
    },
    {
        "id":       "a10_trader",
        "layer":    "3-Execution",
        "name":     "A10 Paper Trader (premarket)",
        "module":   "scripts.paper_trading.auto_trader",
        "func":     "run",
        "output":   "data/portfolio/paper_portfolio_state.json",
        "desc":     "Execute Grade A (always) + Grade B (Markup only) setups in paper portfolio",
        "critical": False,
        "modes":    ["premarket"],
    },
    {
        "id":       "a10",
        "layer":    "3-Execution",
        "name":     "A10 Risk Guardian",
        "module":   "scripts.pre_compute.risk_guardian",
        "func":     "run",
        "output":   "data/risk/risk_report.json",
        "desc":     "Portfolio-level hard limits + devil's advocate + entry approval/block",
        "critical": False,
        "modes":    ["premarket"],
    },

    # ── Weekly Data Enrichment (Fridays only, before Monster Scout) ─────────
    {
        "id":          "fetch_rev_multiq",
        "layer":       "1-Intelligence",
        "name":        "Revenue Multi-Quarter Fetcher",
        "module":      "scripts.pre_compute.fetch_revenue_multiquarter",
        "func":        "main",
        "output":      "data/themes/revenue_multiquarter_cache.json",
        "desc":        "Fetch 4Q revenue YoY trend from EDGAR for all theme tickers (Fridays only)",
        "critical":    False,
        "modes":       ["premarket"],
        "friday_only": True,
    },
    {
        "id":          "fetch_mktcap",
        "layer":       "1-Intelligence",
        "name":        "Market Cap Cache Builder",
        "module":      "scripts.pre_compute.fetch_market_cap",
        "func":        "main",
        "output":      "data/themes/market_cap_cache.json",
        "desc":        "Fetch market cap from Finnhub for all theme tickers — $20B gate (Fridays only)",
        "critical":    False,
        "modes":       ["premarket"],
        "friday_only": True,
    },

    # ── Weekly Forward-Test Snapshot (Fridays only, after A06) ──────────────
    {
        "id":          "fwd_snapshot",
        "layer":       "2-Curation",
        "name":        "Weekly Forward-Test Snapshot",
        "module":      "scripts.pre_compute.weekly_snapshot",
        "func":        "run",
        "output":      "data/ohlcv.db",
        "desc":        "Capture Friday cohort for 4W/8W/13W learning loop (Fridays only)",
        "critical":    False,
        "modes":       ["premarket"],
        "friday_only": True,
    },
    # ── Forward Test Report (Fridays, before A11) ─────────────────────────
    {
        "id":          "fwd_report",
        "layer":       "4-Output",
        "name":        "Forward Test Attribution Report",
        "module":      "scripts.output.fwd_report",
        "func":        "run",
        "output":      "output/",
        "desc":        "Gate attribution: which gates predict 4W/8W/13W excess returns (Fridays only)",
        "critical":    False,
        "modes":       ["premarket"],
        "friday_only": True,
    },

    # ── System Health Probe (runs after risk, before report) ─────────────────
    {
        "id":       "health_probe",
        "layer":    "3-Execution",
        "name":     "System Health Probe",
        "module":   "scripts.pre_compute.system_health_probe",
        "func":     "run",
        "output":   "data/system_health/alerts.json",
        "desc":     "7-probe pipeline health check — PASS/WARN/FAIL. FAILs surfaced in daily brief.",
        "critical": False,
        "modes":    ["premarket"],
    },

    # ── Layer 4: Output ───────────────────────────────────────────────────────
    {
        "id":       "a11",
        "layer":    "4-Output",
        "name":     "A11 Report Writer + Telegram",
        "module":   "scripts.output.report_writer",
        "func":     "run",
        "output":   "output/",
        "desc":     "Daily brief markdown + Telegram push before market open",
        "critical": False,
        "modes":    ["premarket"],
    },

    # ── Weekly Prediction System ──────────────────────────────────────────────
    {
        "id":       "weekly_picker",
        "layer":    "4-Output",
        "name":     "Weekly Top-5 Picker (Monday only)",
        "module":   "scripts.weekly.weekly_picker",
        "func":     "run",
        "output":   "data/weekly_picks/",
        "desc":     "Picks top 5 setups for the week, sends Telegram. Runs Sunday only.",
        "critical": False,
        "modes":    ["premarket"],
        "day_filter": [6],  # Sunday = 6
    },
    {
        "id":       "weekly_scorer",
        "layer":    "4-Output",
        "name":     "Weekly Scorer (Friday only)",
        "module":   "scripts.weekly.weekly_scorer",
        "func":     "run",
        "output":   "data/weekly_picks/learning_curve.json",
        "desc":     "Scores this week's picks vs QQQ, updates learning curve, sends Telegram. Runs Saturday only.",
        "critical": False,
        "modes":    ["eod"],
        "day_filter": [5],  # Saturday = 5
    },

    # ── EOD Mode (post-market analysis) ───────────────────────────────────────
    {
        "id":       "rs_history",
        "layer":    "1-Intelligence",
        "name":     "RS History Backfill (rolling 30d)",
        "module":   "scripts.pre_compute.pipeline_rs_history",
        "func":     "run",
        "output":   "data/rs_universe/latest.json",
        "desc":     "Backfill rs_daily for last 30 trading dates (keeps history current after gaps)",
        "critical": False,
        "modes":    ["eod"],
        "skip_if_missing": True,
    },
    {
        "id":       "pipeline_metrics",
        "layer":    "1-Intelligence",
        "name":     "Pipeline Metrics (full recompute)",
        "module":   "scripts.pre_compute.pipeline_metrics",
        "func":     "main",
        "output":   "data/screening/mode_a_full_latest.json",
        "desc":     "ADTV + 52W + MAs + Stage2 + RS percentiles + screening + history append",
        "critical": False,
        "modes":    ["eod"],
    },
    {
        "id":       "fundamentals",
        "layer":    "1-Intelligence",
        "name":     "A04 Fundamentals Pipeline",
        "module":   "scripts.pre_compute.pipeline_fundamentals",
        "func":     "main",
        "output":   "data/ohlcv.db",
        "desc":     "EDGAR+FMP EPS/Rev/GM fetch → fundamentals_summary + gate_eps/gate_rev/gate_gm (top candidates first)",
        "critical": False,
        "modes":    ["eod"],
    },
    {
        "id":              "fill_ohlc",
        "layer":           "0-Data",
        "name":            "Fill OHLC Gaps (open/high/low)",
        "module":          "scripts.pre_compute.fill_ohlc_gaps",
        "func":            "run",
        "output":          "data/quality/quality_latest.json",
        "desc":            "Update NULL open/high/low for existing rows via Polygon (run until coverage=100%)",
        "critical":        False,
        "modes":           ["eod"],
        "skip_if_missing": True,
    },
    {
        "id":       "data_quality_eod",
        "layer":    "0-Data",
        "name":     "Data Quality Check (EOD)",
        "module":   "scripts.pre_compute.data_quality",
        "func":     "run",
        "output":   "data/quality/quality_latest.json",
        "desc":     "EOD data health audit after OHLCV update",
        "critical": False,
        "modes":    ["eod"],
    },
    {
        "id":       "ohlcv_prefetch",
        "layer":    "0-Data",
        "name":     "OHLCV Pre-fetch (600 tickers)",
        "module":   "scripts.pre_compute.ohlcv_prefetch",
        "func":     "run",
        "output":   "data/ohlcv_cache/_manifest.json",
        "desc":     "Download 290d OHLCV for all 600 benchmark tickers → disk cache → full universe tomorrow",
        "critical": False,
        "modes":    ["eod"],
    },
    {
        "id":       "fwd_fill",
        "layer":    "4-Learning",
        "name":     "Forward Return Filler",
        "module":   "scripts.pre_compute.fwd_fill",
        "func":     "run",
        "output":   "data/ohlcv.db",
        "desc":     "Fill 4W/8W/13W forward returns for cohorts whose window has elapsed",
        "critical": False,
        "modes":    ["eod"],
    },
    {
        "id":       "a12_postmortem",
        "layer":    "4-Learning",
        "name":     "A12 Auto Post-Mortem",
        "module":   "scripts.pre_compute.auto_postmortem",
        "func":     "run",
        "output":   "data/postmortems/lessons.json",
        "desc":     "Analyse closed trades → lesson extraction + signal scorecard",
        "critical": False,
        "modes":    ["eod"],
    },
    {
        "id":       "a09_eod",
        "layer":    "3-Execution",
        "name":     "A09 Portfolio Manager (EOD update)",
        "module":   "scripts.portfolio.portfolio_manager",
        "func":     "run",
        "kwargs":   {"mode": "eod"},
        "output":   "data/portfolio/portfolio_state.json",
        "desc":     "End-of-day price update + P&L recalculation for all positions",
        "critical": False,
        "modes":    ["eod"],
    },
    {
        "id":       "a10_trader_eod",
        "layer":    "3-Execution",
        "name":     "A10 Paper Trader (EOD mark-to-market)",
        "module":   "scripts.paper_trading.auto_trader",
        "func":     "run",
        "output":   "data/portfolio/paper_portfolio_state.json",
        "desc":     "EOD mark-to-market for paper portfolio — checks exits, updates prices",
        "critical": False,
        "modes":    ["eod"],
        "kwargs":   {"mode": "eod"},
    },

    # ── Monthly Mode ─────────────────────────────────────────────────────────
    {
        "id":              "theme_mapper",
        "layer":           "1-Intelligence",
        "name":            "A05 Theme Mapper (auto-classify)",
        "module":          "scripts.pre_compute.theme_mapper",
        "func":            "run",
        "output":          "data/themes/ticker_labels.json",
        "desc":            "Tier 2 keyword + Tier 3 SIC → auto-label tickers into 14 themes (monthly)",
        "critical":        False,
        "modes":           ["monthly"],
        "skip_if_missing": False,
    },
    {
        "id":              "a12_calibrate",
        "layer":           "4-Learning",
        "name":            "A12 Framework Calibrator (Bayesian)",
        "module":          "scripts.pre_compute.framework_calibrator",
        "func":            "calibrate",
        "output":          "data/calibration/signal_weights.json",
        "desc":            "Bayesian signal weight update from all closed trades (monthly)",
        "critical":        False,
        "modes":           ["monthly"],
        "skip_if_missing": False,
    },
    {
        "id":              "a12_performance",
        "layer":           "4-Learning",
        "name":            "A12 Performance Tracker (monthly report)",
        "module":          "scripts.portfolio.performance_tracker",
        "func":            "run",
        "output":          "output/",
        "desc":            "Attribution + mistake classification + QQQ comparison (monthly)",
        "critical":        False,
        "modes":           ["monthly"],
        "skip_if_missing": True,    # not yet built — skip silently
    },
]


# ── Logger ────────────────────────────────────────────────────────────────────

class RunnerLog:
    def __init__(self, mode: str):
        self.today    = date.today().strftime("%y%m%d")
        self.mode     = mode
        self.log_file = LOG_DIR / f"runner_{self.today}_{mode}.json"
        self.entries: list[dict] = []

    def record(self, step_id: str, name: str, success: bool,
               duration: float, output: str, error: str = "", skipped: bool = False) -> None:
        tag    = "[SKIP]" if skipped else ("[OK]" if success else "[FAIL]")
        status = "skipped" if skipped else ("ok" if success else "fail")
        entry  = {
            "step":     step_id,
            "name":     name,
            "status":   status,
            "duration": round(duration, 1),
            "output":   output,
            "error":    error,
            "time":     datetime.now().strftime("%H:%M:%S"),
        }
        self.entries.append(entry)
        msg = f"  {tag} {name} [{duration:.1f}s]"
        if error:
            msg += f" -- {error[:100]}"
        print(msg)

    def save(self) -> dict:
        passed  = sum(1 for e in self.entries if e["status"] == "ok")
        failed  = sum(1 for e in self.entries if e["status"] == "fail")
        skipped = sum(1 for e in self.entries if e["status"] == "skipped")
        summary = {
            "date":    date.today().isoformat(),
            "mode":    self.mode,
            "run_at":  datetime.now().isoformat(),
            "steps":   self.entries,
            "passed":  passed,
            "failed":  failed,
            "skipped": skipped,
        }
        self.log_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary


# ── Step Runner ───────────────────────────────────────────────────────────────

def _resolve_output(step: dict) -> Path:
    """Return the output path — handle both files and directories."""
    p = BASE_DIR / step["output"]
    return p


def _execute_step_once(step: dict, full_path) -> tuple[bool, float, str]:
    """
    Execute one attempt of a pipeline step.
    Returns (success, duration, error_msg).
    Separated from run_step() so retry logic can call it cleanly.
    """
    t0 = time.time()
    try:
        spec   = importlib.util.spec_from_file_location(step["id"], full_path)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        func = getattr(module, step["func"], None)
        if func is None:
            return False, time.time() - t0, f"Function '{step['func']}' not in {step['module']}"

        kw = step.get("kwargs", {})
        func(**kw) if kw else func()
        duration = time.time() - t0

        # Verify output exists
        output_p = _resolve_output(step)
        if step["output"].endswith("/"):
            today_str = date.today().strftime("%y%m%d")
            if output_p.exists() and step["id"] == "a11":
                brief = output_p / f"daily_brief_{today_str}.md"
                if not brief.exists():
                    return False, duration, f"A11: daily_brief_{today_str}.md not written"
        else:
            if not output_p.exists():
                return False, duration, "Output file not written"

        return True, duration, ""

    except Exception as exc:
        import traceback
        err = f"{type(exc).__name__}: {exc}"
        print(f"\n    TRACEBACK:")
        traceback.print_exc()
        return False, time.time() - t0, err[:200]


# Errors that are worth retrying (transient: network, rate-limit, SSL, file lock)
_RETRYABLE = (
    "ConnectionError", "Timeout", "HTTPError", "ReadTimeout", "ConnectTimeout",
    "429", "503", "SSLError", "RemoteDisconnected", "IncompleteRead",
    "PermissionError", "WinError 5",   # file-lock on Windows — usually clears in seconds
    "Output file not written",         # script ran but output not flushed yet
)

# Steps whose output is cheap to recompute — suppress retry noise for these
_NO_RETRY_STEPS = {"health_check", "data_quality", "data_quality_eod"}

# Max retry attempts for transient failures (total attempts = 1 + MAX_RETRIES)
MAX_RETRIES = 2
RETRY_DELAY = 5   # seconds between retries


def run_step(step: dict, dry_run: bool = False) -> tuple[bool, float, str, bool]:
    """
    Run a single pipeline step with auto-retry on transient failures.
    Returns (success, duration, error_msg, was_skipped).
    """
    t0 = time.time()

    # --- skip if day_filter doesn't match today ---
    day_filter = step.get("day_filter")
    if day_filter is not None:
        today_weekday = date.today().weekday()  # 0=Mon, 4=Fri
        if today_weekday not in day_filter:
            return True, 0.0, "", True  # silently skip — wrong day

    # --- skip if script file missing ---
    module_path = step["module"].replace(".", "/") + ".py"
    full_path   = BASE_DIR / module_path
    if not full_path.exists():
        if step.get("skip_if_missing"):
            return True, 0.0, "", True   # silently skip
        return False, 0.0, f"Script not found: {module_path}", False

    if dry_run:
        output_p = _resolve_output(step)
        if step["output"].endswith("/"):
            # Directory outputs: dry-run always passes — the script creates the dir on first run.
            # FIX: old code returned FAIL when analyst_cache/ didn't exist yet, causing
            # "PARTIAL FAILURE | Failed: 0 step(s)" Telegram (alert fired but step list
            # was inconsistent due to the directory-output special handling).
            return True, 0.0, "", False
        exists = output_p.exists()
        return exists, 0.0, "" if exists else f"Output missing: {step['output']}", False

    # --- Attempt 1 ---
    ok, duration, err = _execute_step_once(step, full_path)
    if ok:
        return True, duration, "", False

    # --- Retry logic ---
    # Skip retry for non-transient errors (code bugs, missing functions, etc.)
    # and for cheap diagnostic steps that aren't worth re-running
    step_id = step.get("id", "")
    is_transient = any(kw in err for kw in _RETRYABLE)
    should_retry = is_transient and step_id not in _NO_RETRY_STEPS and not dry_run

    if not should_retry:
        return False, time.time() - t0, err, False

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n    [RETRY {attempt}/{MAX_RETRIES}] {step['name']} — sleeping {RETRY_DELAY}s then retrying...")
        print(f"    Last error: {err[:120]}")
        time.sleep(RETRY_DELAY)

        ok, duration, err = _execute_step_once(step, full_path)
        if ok:
            print(f"    [RETRY {attempt}/{MAX_RETRIES}] RECOVERED after {attempt} retry(ies)")
            return True, time.time() - t0, "", False
        print(f"    [RETRY {attempt}/{MAX_RETRIES}] Still failing: {err[:120]}")

    # All retries exhausted
    print(f"\n    [RETRY] All {MAX_RETRIES} retries exhausted for {step['name']}")
    return False, time.time() - t0, f"[after {MAX_RETRIES} retries] {err}", False


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _cleanup_stale_pkl_cache() -> None:
    """Delete ohlcv_cache PKL files from previous days (BOA-022 DEC-024).
    PKLs are intraday deduplication caches — they have zero readers once the
    calendar date advances. Only today's date is kept.
    """
    cache_dir = BASE_DIR / "data" / "ohlcv_cache"
    if not cache_dir.exists():
        return
    today_str = date.today().strftime("%Y-%m-%d")
    deleted = 0
    for f in cache_dir.glob("*.pkl"):
        # Filename format: TICKER_<period>_<YYYY-MM-DD>.pkl
        # Keep files containing today's date string; delete all others.
        if today_str not in f.name:
            try:
                f.unlink()
                deleted += 1
            except OSError:
                pass
    if deleted:
        print(f"  [Cleanup] Removed {deleted} stale PKL cache files (BOA-022)")


# All directories that pipeline scripts expect to exist at import time.
# Runner creates these before any step runs → module-level mkdir() calls become
# no-ops (exist_ok=True on an existing dir is always safe, no PermissionError).
_REQUIRED_DIRS = [
    "data/regime", "data/rs_universe", "data/rs_universe/snapshots",
    "data/themes", "data/leadership", "data/bigshot",
    "data/setups", "data/portfolio", "data/risk",
    "data/postmortems", "data/calibration", "data/runner_logs",
    "data/health", "data/breadth", "data/board", "data/quality",
    "data/system_health", "data/weekly_picks", "data/weekly_picks",
    "data/fundamentals", "data/fundamentals/analyst_cache",
    "data/fundamentals/company_names", "data/macro",
    "data/weekly_picks", "data/nrgc", "data/nrgc/state", "data/nrgc/weekly",
    "data/insights", "data/paper_trading",
    "output", "output/postmortems",
    "data/ohlcv_cache",
]


def _ensure_dirs() -> None:
    """Create all required output directories before any script is imported.
    Prevents PermissionError on module-level mkdir() calls on Windows/OneDrive
    (exist_ok=True on an already-existing dir is a safe no-op)."""
    for rel in _REQUIRED_DIRS:
        d = BASE_DIR / rel
        if not d.exists():
            try:
                d.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                pass   # dir was created by another process between exists() and mkdir()


def run_pipeline(mode: str = "premarket", step_filter: str | None = None,
                 dry_run: bool = False) -> dict:
    _ensure_dirs()   # guarantee all output dirs exist before any step imports
    today_str = date.today().strftime("%Y-%m-%d")
    run_mode  = "DRY-RUN" if dry_run else mode.upper()
    weekday    = date.today().weekday()   # 0=Mon, 4=Fri, 1=Tue, 6=Sun
    is_friday  = weekday == 4
    is_tuesday = weekday == 1
    is_sunday  = weekday == 6
    is_first_of_month = date.today().day == 1

    # BOA-022 DEC-024: purge stale PKL cache before pipeline starts
    if not dry_run:
        _cleanup_stale_pkl_cache()

    print(f"\n{'='*62}")
    print(f"  AlphaAbsolute v2 Runner  [{today_str}]  [{run_mode}]")
    print(f"  Started: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*62}")

    # Select steps
    if step_filter:
        steps_to_run = [s for s in STEPS if s["id"] == step_filter]
        if not steps_to_run:
            print(f"\n  ERROR: No step with id='{step_filter}'")
            print(f"  Available: {', '.join(s['id'] for s in STEPS)}")
            return {}
    else:
        steps_to_run = [
            s for s in STEPS
            if mode in s.get("modes", ["premarket"])
            and not (s.get("friday_only")  and not is_friday)
            and not (s.get("tuesday_only") and not is_tuesday)
            and not (s.get("sunday_only")  and not is_sunday)
        ]

    log = RunnerLog(mode)
    aborted = False

    for step in steps_to_run:
        if aborted:
            log.record(step["id"], step["name"], False, 0.0,
                       step["output"], "Pipeline aborted by critical failure")
            continue

        print(f"\n  [{step['layer']}] {step['name']}")
        print(f"  -> {step['desc']}")

        ok, dur, err, skipped = run_step(step, dry_run=dry_run)
        log.record(step["id"], step["name"], ok, dur, step["output"], err, skipped)

        if not ok and not skipped and step.get("critical"):
            print(f"\n  !! CRITICAL STEP FAILED — pipeline aborted.")
            print(f"     Fix '{step['module']}' before retrying.")
            aborted = True

    summary = log.save()

    print(f"\n{'='*62}")
    n = len([e for e in summary["steps"] if e["status"] != "skipped"])
    print(f"  Complete: {summary['passed']}/{n} passed | "
          f"{summary['failed']} failed | {summary['skipped']} skipped")
    print(f"  Log: {log.log_file.name}")
    print(f"{'='*62}\n")

    # Always send a pipeline status alert to Telegram
    _send_pipeline_alert(summary, mode, aborted)

    _print_regime_summary()
    return summary


def _send_pipeline_alert(summary: dict, mode: str, aborted: bool) -> None:
    """
    Send a Telegram pipeline status alert.
    - ALL PASS  → green check, step counts, no failures listed
    - PARTIAL FAILURE / ABORT → warning with failed step names
    Always fires (not just on failure) so the user can confirm the pipeline ran.
    Silent if Telegram not configured.
    """
    import os, requests as _req
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return

    failed_count = summary.get("failed", 0)
    passed_count = summary.get("passed", 0)
    skipped_count = summary.get("skipped", 0)
    run_date = summary.get("date", date.today().isoformat())

    try:
        failed_steps = [s["name"] for s in summary.get("steps", [])
                        if s.get("status") == "fail"]
    except Exception:
        failed_steps = []

    display_count = failed_count if failed_count != len(failed_steps) else len(failed_steps)

    if aborted:
        aborted_step = next(
            (s["name"] for s in summary.get("steps", [])
             if s.get("status") == "fail"
             and "Pipeline aborted by critical failure" not in s.get("error", "")),
            "unknown step"
        )
        status_line = f"[ABORT] PIPELINE ABORTED | {mode.upper()} | {run_date}"
        detail = f"Failed: {display_count} step(s)\nAborted at: {aborted_step} — downstream skipped"
        if failed_steps:
            detail += "\nFailed steps: " + ", ".join(failed_steps[:5])
    elif failed_count > 0:
        status_line = f"[WARN] PARTIAL FAILURE | {mode.upper()} | {run_date}"
        detail = f"Failed: {display_count} step(s) | Passed: {passed_count} | Skipped: {skipped_count}"
        if failed_steps:
            detail += "\nFailed: " + ", ".join(failed_steps[:5])
        else:
            detail += "\n(step names unavailable — check runner log)"
    else:
        status_line = f"[OK] ALL PASS | {mode.upper()} | {run_date}"
        detail = f"Passed: {passed_count} | Skipped: {skipped_count} | No failures"

    msg = f"{status_line}\n{detail}"

    try:
        _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=10,
            verify=False,
        )
        label = "ALL PASS" if failed_count == 0 and not aborted else ("ABORT" if aborted else "PARTIAL FAILURE")
        print(f"  [Alert] Telegram pipeline status sent: {label}")
    except Exception as e:
        print(f"  [Alert] Telegram send failed: {e}")


# ── Regime Summary ────────────────────────────────────────────────────────────

def _print_regime_summary() -> None:
    """Print compact v2 regime + macro summary after pipeline completes."""
    health_file = BASE_DIR / "data/regime/market_health.json"
    macro_file  = BASE_DIR / "data/regime/macro_state.json"

    try:
        if health_file.exists():
            h = json.loads(health_file.read_text(encoding="utf-8"))
            regime     = h.get("regime", "?")
            cash_floor = h.get("cash_floor", 0.0)
            max_dep    = h.get("max_deployed", 1.0)
            leaders_ok = h.get("leaders_ok", True)
            bigshot_ok = h.get("bigshot_ok", True)
            spy_td     = h.get("spy_td_signal", "Neutral")
            qqq_td     = h.get("qqq_td_signal", "Neutral")
            note       = h.get("regime_note", "")

            emoji = {"Markup": "[+]", "Sideways": "[~]",
                     "Distribution": "[!]", "Markdown": "[-]"}.get(regime, "[?]")

            print(f"  {emoji} REGIME: {regime}")
            print(f"     Cash floor: {cash_floor:.0%} | Max deployed: {max_dep:.0%}")
            print(f"     Leaders OK: {'Yes' if leaders_ok else 'NO'} | "
                  f"Big Shot OK: {'Yes' if bigshot_ok else 'NO'}")
            print(f"     SPY TD: {spy_td} | QQQ TD: {qqq_td}")
            if note:
                print(f"     Note: {note}")

        if macro_file.exists():
            m = json.loads(macro_file.read_text(encoding="utf-8"))
            rate_env  = m.get("rate_environment", "?")
            modifier  = m.get("macro_modifier", 1.0)
            credit    = m.get("credit_stress", False)
            yc        = m.get("yield_curve", "?")
            mnote     = m.get("macro_note", "")
            print(f"\n  [MACRO] {rate_env} | Modifier: {modifier:.2f}x | "
                  f"Credit stress: {'YES' if credit else 'No'} | Curve: {yc}")
            if mnote:
                print(f"     {mnote}")
        print()
    except Exception:
        pass


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AlphaAbsolute v2 — Master Daily Pipeline Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  premarket   Full pre-market pipeline (default) — runs all agents in order
  eod         End-of-day: post-mortem + price update only
  monthly     Monthly: Bayesian calibration + performance report

Examples:
  python scripts/runners/pre_market_runner.py
  python scripts/runners/pre_market_runner.py --mode eod
  python scripts/runners/pre_market_runner.py --step a07
  python scripts/runners/pre_market_runner.py --dry-run

Available step IDs:
  Premarket: ohlcv_update earnings_cal data_quality a01 a02 a03_bench a03 a03c a05 a04_prewarm a06 a07 motw_selector motw_research [Sun] fwd_snapshot fwd_report a08 a09 a10_trader a10 health_probe a11
  EOD:       ohlcv_update rs_history pipeline_metrics fundamentals fill_ohlc data_quality_eod ohlcv_prefetch fwd_fill a12_postmortem a09_eod a10_trader_eod
  Monthly:   theme_mapper a12_calibrate a12_performance
        """,
    )
    parser.add_argument(
        "--mode", choices=["premarket", "eod", "monthly"], default="premarket",
        help="Run mode (default: premarket)",
    )
    parser.add_argument(
        "--step", type=str, default=None,
        help="Run only one step by its ID (overrides --mode filter)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Check whether expected output files exist without running scripts",
    )
    args = parser.parse_args()

    result = run_pipeline(mode=args.mode, step_filter=args.step, dry_run=args.dry_run)
    # Exit non-zero on failures so GitHub Actions marks the workflow as failed
    if result and (result.get("failed", 0) > 0):
        sys.exit(1)
