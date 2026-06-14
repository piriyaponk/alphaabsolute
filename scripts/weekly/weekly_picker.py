"""
weekly_picker.py — AlphaAbsolute Weekly Top-5 Prediction
=========================================================
Runs Sunday (auto via runner day_filter=[6]).
Before picking, refreshes ALL underlying data in dependency order:
  Layer 0: ohlcv update → fix_volumes → market_regime → macro → breadth
  Layer 1: rs_ranker → rs_theme_ranker
  Layer 2: trend_template_screener → setup_scanner

Ranking formula (100 pts total):
  40 pts  RS score  (strong_leader_score from rs_universe)
  20 pts  RR to T2  (capped at 8x = full 20pts)
  20 pts  EPS YoY   (>100% = full 20pts, >25% = 10pts)
  10 pts  Setup tier (VCP/BKT/CWH = 10, SPR/PPT = 6, other = 3)
  10 pts  RS momentum 1m vs 3m acceleration (positive = 10)

Only includes setups where wait_for_better_entry=False and context_only=False.
"""

import importlib
import json
import os
import sys
import ssl
import time
import urllib.request
import urllib.parse
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SETUPS_FILE    = ROOT / "data" / "setups"    / "setups_today.json"
SL_FILE        = ROOT / "data" / "rs_universe" / "strong_leader_latest.json"
REGIME_FILE    = ROOT / "data" / "regime"    / "market_health.json"
PICKS_DIR      = ROOT / "data" / "weekly_picks"
PICKS_DIR.mkdir(parents=True, exist_ok=True)

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# Setup tier scores
TIER1_SETUPS = {"VCP", "BKT", "CWH", "SOS"}
TIER2_SETUPS = {"SPR", "PPT"}

# ── helpers ──────────────────────────────────────────────────────────────────

def _load(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def _week_id() -> str:
    """ISO week string e.g. '2026-W25'"""
    d = date.today()
    return f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"


def _picks_file() -> Path:
    return PICKS_DIR / f"picks_{_week_id()}.json"


def _send_telegram(msg: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("  [WARN] Telegram not configured — printing only")
        print(msg)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    def _post(payload):
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req  = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode()).get("ok", False)

    try:
        return _post({"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
    except Exception as e:
        if "400" in str(e):
            try:
                return _post({"chat_id": TELEGRAM_CHAT_ID, "text": msg})
            except Exception as e2:
                print(f"  [ERROR] Telegram: {e2}")
                return False
        print(f"  [ERROR] Telegram: {e}")
        return False


# ── data refresh ─────────────────────────────────────────────────────────────

# Pipeline layers — must run in this order (each layer depends on the previous)
_REFRESH_PIPELINE = [
    # Layer 0: Price + Regime foundation
    ("scripts.pre_compute.update_ohlcv_bulk",      "run",       "OHLCV price update"),
    ("scripts.pre_compute.fix_dates_volumes",       "run",       "Fix float volumes"),
    ("scripts.pre_compute.market_regime",           "run",       "Market regime (A01)"),
    ("scripts.pre_compute.macro_monitor",           "run",       "Macro state (A02)"),
    ("scripts.pre_compute.fetch_market_breadth",    "run",       "Market breadth"),
    # Layer 1: RS Intelligence
    ("scripts.pre_compute.rs_ranker",               "run",       "RS Universe Ranker (A03)"),
    ("scripts.pre_compute.rs_theme_ranker",         "run",       "Theme RS Ranker (A05)"),
    # Layer 2: Curation + Execution
    ("scripts.pre_compute.trend_template_screener", "run",       "PRISM Screener (A06)"),
    ("scripts.pre_compute.setup_scanner",           "run",       "Setup Scanner (A08)"),
]


def _run_step(module_str: str, func_name: str, label: str) -> bool:
    """Import and call one pipeline step. Returns True on success."""
    script_path = ROOT / (module_str.replace(".", "/") + ".py")
    if not script_path.exists():
        print(f"  [refresh] SKIP {label} — script not found")
        return True  # non-fatal

    t0 = time.time()
    try:
        spec   = importlib.util.spec_from_file_location(module_str, script_path)
        mod    = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, func_name, None)
        if fn is None:
            print(f"  [refresh] SKIP {label} — no {func_name}() found")
            return True
        fn()
        print(f"  [refresh] OK   {label} ({time.time()-t0:.0f}s)")
        return True
    except Exception as e:
        print(f"  [refresh] FAIL {label}: {e}")
        return False


def _last_trading_day() -> str:
    """Return the most recent weekday (Mon-Fri) date string."""
    from datetime import timedelta
    d = date.today()
    while d.weekday() >= 5:  # Sat=5, Sun=6
        d -= timedelta(days=1)
    return d.isoformat()


def _data_is_fresh(last_trading_day: str) -> bool:
    """Quick check — are the key output files already dated to last trading day?"""
    key_files = [
        ROOT / "data" / "setups"       / "setups_today.json",
        ROOT / "data" / "rs_universe"  / "strong_leader_latest.json",
        ROOT / "data" / "leadership"   / "top30_watchlist.json",
        ROOT / "data" / "regime"       / "market_health.json",
    ]
    for f in key_files:
        if not f.exists():
            return False
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            file_date = d.get("date") or d.get("computed_at", "")[:10]
            if file_date != last_trading_day:
                return False
        except Exception:
            return False
    return True


def refresh_data(force: bool = False) -> bool:
    """
    Refresh all pipeline data before picking.
    Skips if all key files already reflect the last trading day (unless force=True).
    Returns True if data is ready, False if a critical step failed.
    """
    last_td = _last_trading_day()
    if not force and _data_is_fresh(last_td):
        print(f"  [refresh] Data already fresh for {last_td} — skipping pipeline refresh")
        return True

    print(f"  [refresh] Refreshing pipeline data for {last_td}...")
    print(f"  [refresh] Running {len(_REFRESH_PIPELINE)} steps in dependency order")
    print()

    failed = []
    for module_str, func_name, label in _REFRESH_PIPELINE:
        ok = _run_step(module_str, func_name, label)
        if not ok:
            failed.append(label)

    print()
    if failed:
        print(f"  [refresh] WARNING: {len(failed)} step(s) failed: {failed}")
        print(f"  [refresh] Proceeding with best available data")
        return False
    else:
        print(f"  [refresh] All pipeline steps complete — data is fresh")
        return True


# ── scoring ──────────────────────────────────────────────────────────────────

def _score_setup(setup: dict, sl_stocks: dict) -> float:
    ticker = setup.get("ticker", "")
    sl     = sl_stocks.get(ticker, {})

    # 1. RS score (0–40)
    sl_score = sl.get("strong_leader_score") or sl.get("sl_score") or 0
    rs_pts   = min(sl_score / 100 * 40, 40)

    # 2. RR to T2 (0–20)
    rr   = setup.get("rr_to_t2") or setup.get("rr_ratio") or 0
    rr_pts = min(rr / 8 * 20, 20)

    # 3. EPS (0–20)
    eps = setup.get("eps_yoy_pct") or 0
    if eps >= 100:
        eps_pts = 20
    elif eps >= 50:
        eps_pts = 15
    elif eps >= 25:
        eps_pts = 10
    elif eps > 0:
        eps_pts = 5
    else:
        eps_pts = 0

    # 4. Setup tier (0–10)
    st = setup.get("setup_type", "")
    if st in TIER1_SETUPS:
        tier_pts = 10
    elif st in TIER2_SETUPS:
        tier_pts = 6
    else:
        tier_pts = 3

    # 5. RS momentum 1m vs 3m (0–10)
    mom = sl.get("rs_mom_1m_3m") or 0
    mom_pts = 10 if mom > 0 else (5 if mom == 0 else 0)

    total = rs_pts + rr_pts + eps_pts + tier_pts + mom_pts
    return round(total, 1)


# ── main ─────────────────────────────────────────────────────────────────────

def run(skip_refresh: bool = False) -> dict:
    today   = date.today().isoformat()
    week_id = _week_id()

    # ── Step 1: Refresh all pipeline data before screening ────────────────────
    if not skip_refresh:
        print(f"\n{'='*60}")
        print(f"  Weekly Picker — {week_id} | {today}")
        print(f"{'='*60}")
        refresh_data()
        print()

    # Load data (now guaranteed fresh)
    raw_setups = _load(SETUPS_FILE, {})
    setups     = raw_setups.get("setups", raw_setups) if isinstance(raw_setups, dict) else raw_setups
    sl_data    = _load(SL_FILE, {})
    sl_stocks  = sl_data.get("stocks", {}) if isinstance(sl_data, dict) else {}
    regime_d   = _load(REGIME_FILE, {})
    regime     = regime_d.get("regime", "Unknown")

    if not setups:
        print(f"  [weekly_picker] No setups found in {SETUPS_FILE}")
        return {"status": "no_setups"}

    # Filter: skip context-only and wait-for-entry flags
    actionable = [
        s for s in setups
        if not s.get("wait_for_better_entry", False)
        and not s.get("context_only", False)
    ]
    print(f"  [weekly_picker] {len(setups)} total setups => {len(actionable)} actionable")

    # Score and rank
    scored = []
    for s in actionable:
        score = _score_setup(s, sl_stocks)
        scored.append({**s, "_score": score})

    scored.sort(key=lambda x: x["_score"], reverse=True)
    top5 = scored[:5]

    if not top5:
        print("  [weekly_picker] No actionable setups this week")
        return {"status": "no_picks"}

    # Build pick records
    picks = []
    for rank, s in enumerate(top5, 1):
        ticker = s.get("ticker", "?")
        sl_info = sl_stocks.get(ticker, {})
        picks.append({
            "rank":           rank,
            "ticker":         ticker,
            "theme":          s.get("theme", "?"),
            "setup_type":     s.get("setup_type", "?"),
            "score":          s["_score"],
            "entry_price":    s.get("current_price"),
            "pivot":          s.get("pivot"),
            "stop":           s.get("stop"),
            "target_1":       s.get("target_1"),
            "target_2":       s.get("target_2"),
            "rr_ratio":       s.get("rr_ratio"),
            "rr_to_t2":       s.get("rr_to_t2"),
            "eps_yoy_pct":    s.get("eps_yoy_pct"),
            "rs_3m_pct":      sl_info.get("rs_3m_pct") or s.get("rs_3m_pct"),
            "rs_6m_pct":      sl_info.get("rs_6m_pct") or s.get("rs_6m_pct"),
            "sl_score":       sl_info.get("strong_leader_score"),
            "rs_mom_1m_3m":   sl_info.get("rs_mom_1m_3m"),
            "entry_note":     s.get("entry_note", ""),
            "result_pct":     None,  # filled by weekly_scorer
            "qqq_return_pct": None,
            "beat_qqq":       None,
        })

    # Fetch QQQ entry price for week-end comparison
    qqq_price = None
    try:
        import sqlite3
        conn = sqlite3.connect(str(ROOT / "data" / "ohlcv.db"))
        row  = conn.execute("SELECT close FROM ohlcv WHERE ticker='QQQ' ORDER BY date DESC LIMIT 1").fetchone()
        conn.close()
        qqq_price = float(row[0]) if row else None
    except Exception:
        pass

    # Save prediction file
    output = {
        "week_id":          week_id,
        "pick_date":        today,
        "regime":           regime,
        "qqq_entry_price":  qqq_price,
        "score_date":       None,
        "picks":            picks,
        "accuracy_5":       None,
        "avg_return":       None,
        "qqq_return":       None,
        "beat_count":       None,
    }
    picks_file = _picks_file()
    picks_file.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [weekly_picker] Saved → {picks_file.name}")

    # Build Telegram message
    regime_emoji = {"Markup": "🟢", "Distribution": "🟡", "Sideways": "🟡", "Markdown": "🔴"}.get(regime, "⚪")
    lines = [
        f"*WEEKLY TOP-5 PICKS* | {week_id}",
        f"Regime: {regime_emoji} {regime} | {today}",
        "",
    ]
    for p in picks:
        eps_str = f"EPS+{p['eps_yoy_pct']:.0f}%" if p.get("eps_yoy_pct") else "EPS=?"
        rs_str  = f"RS{p['rs_3m_pct']:.0f}" if p.get("rs_3m_pct") else "RS=?"
        rr_str  = f"RR{p['rr_to_t2']:.1f}x" if p.get("rr_to_t2") else f"RR{p['rr_ratio']:.1f}x"
        lines.append(
            f"#{p['rank']} ${p['ticker']} [{p['setup_type']}] {rr_str} | {rs_str} | {eps_str}"
        )
        lines.append(f"   Entry: ${p['entry_price']} | Stop: ${p['stop']} | T2: ${p['target_2']}")
        lines.append(f"   Score: {p['score']}/100 | {p['theme']}")
        lines.append("")

    lines.append("_Scored: RS(40) + RR(20) + EPS(20) + Setup(10) + Momentum(10)_")
    lines.append("_Results tracked Friday EOD_")

    msg = "\n".join(lines)
    # Plain text fallback (strip markdown)
    _send_telegram(msg)

    print(f"\n  TOP-5 PICKS FOR {week_id}:")
    for p in picks:
        print(f"    #{p['rank']} {p['ticker']:6s}  score={p['score']:5.1f}  {p['setup_type']}  rr_t2={p.get('rr_to_t2','?')}")

    return {"status": "ok", "week_id": week_id, "picks": [p["ticker"] for p in picks], "file": str(picks_file)}


if __name__ == "__main__":
    run()
