"""
AlphaAbsolute -- Auto Post-Mortem Loop  (I8 + Learning Engine)
==============================================================
The system that makes AlphaAbsolute SMARTER with every trade.

"Most traders repeat the same mistakes. AlphaAbsolute writes its own
 prescriptions and updates its own weights." -- System Design Note

Loop: Trade closes -> Post-mortem auto-generated -> Lesson extracted ->
      Calibrator updates weights -> Next trade benefits from the lesson.

What this does:
  1. Detects newly closed trades (not yet post-mortemed)
  2. Analyzes each trade: what worked, what didn't, which signals were active
  3. Generates a structured lesson (signal scorecard)
  4. Appends lesson to data/postmortems/lessons.json
  5. Triggers framework_calibrator to update signal weights
  6. Writes human-readable post-mortem to output/postmortem_{TICKER}_{DATE}.md

Lesson format:
  {
    "ticker": "NVDA",
    "outcome": "WIN",
    "pnl_pct": +18.4,
    "signals_active": ["emls_90plus", "base_2", "phase_3", "rs_90plus"],
    "signals_that_predicted_win": [...],
    "signals_that_predicted_loss": [...],
    "lesson": "Phase 3 + Base 2 + RS 90+ = highest conviction. Hold longer.",
    "prescription": "Raise stop to -6% from -8% for EMLS 90+ names",
    "date": "2026-05-16",
  }

Cost: $0 (pure Python -- all analysis from cached JSON files)
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ── Encoding fix for Thai terminal (cp874) ───────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[2]
OUT_DIR  = BASE_DIR / "data" / "postmortems"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TRADE_LOG      = BASE_DIR / "data" / "portfolio" / "paper_trades.jsonl"   # v2 canonical: JSONL format
LESSONS_FILE   = OUT_DIR / "lessons.json"
PROCESSED_FILE = OUT_DIR / "processed_trades.json"
OPS_DIR        = BASE_DIR / "output"

sys.path.insert(0, str(BASE_DIR / "scripts"))

try:
    from pre_compute.framework_calibrator import calibrate, _extract_signal_keys, DEFAULT_WEIGHTS
except ImportError:
    def calibrate(**kw): return {}
    def _extract_signal_keys(t): return []
    DEFAULT_WEIGHTS = {}

try:
    from utils.data_engine import get_ohlcv
except ImportError:
    def get_ohlcv(ticker, period="1y", **kw): return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def _load_trade_log() -> list:
    """Load paper trades from JSONL file (one JSON object per line).
    auto_trader.py writes paper_trades.jsonl in JSONL format — not JSON array.
    """
    if not TRADE_LOG.exists():
        return []
    try:
        lines = TRADE_LOG.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]
    except Exception as e:
        print(f"  [WARN] _load_trade_log() failed: {e}")
        return []


def _load_processed() -> set:
    data = _load_json(PROCESSED_FILE, [])
    return set(data) if isinstance(data, list) else set()


def _save_processed(processed: set):
    PROCESSED_FILE.write_text(
        json.dumps(sorted(processed), indent=2), encoding="utf-8"
    )


def _load_lessons() -> list:
    return _load_json(LESSONS_FILE, [])


def _save_lessons(lessons: list):
    LESSONS_FILE.write_text(
        json.dumps(lessons[-200:], indent=2), encoding="utf-8"  # keep last 200
    )


# ── Post-Mortem Analysis ──────────────────────────────────────────────────────

def _analyze_trade(trade: dict) -> dict:
    """Generate structured post-mortem analysis for one closed trade."""
    ticker    = trade.get("ticker", "")
    pnl_pct   = float(trade.get("pnl_pct", 0))
    pnl_usd   = float(trade.get("pnl_usd", 0))
    emls      = float(trade.get("emls_score", 0))
    setup     = trade.get("setup_type", "leader")
    entry     = float(trade.get("entry_price", 0))
    exit_p    = float(trade.get("exit_price", 0))
    days_held = int(trade.get("days_held", 0))
    reason    = trade.get("reason", "")
    open_date = trade.get("open_date", "")
    close_date = trade.get("close_date", date.today().isoformat())

    win = pnl_pct > 0
    outcome = "WIN" if win else ("BREAK_EVEN" if abs(pnl_pct) < 0.5 else "LOSS")

    # Get active signals at entry
    signal_keys = _extract_signal_keys(trade)

    # Signal scorecard -- which signals were active and how did outcome fare?
    good_signals = []   # Predicted WIN
    bad_signals  = []   # Predicted LOSS or failed to protect

    # High-conviction signals that should mean WIN
    WIN_SIGNALS = {
        "emls_90plus", "base_1", "base_2", "phase_2", "phase_3",
        "rs_90plus", "rs_70_90", "inflection_70plus", "inflection_50_70",
        "exhaustion_normal", "regime_power", "regime_confirmed",
        "canslim_a_plus", "canslim_a", "td_buy9"
    }
    # Risky signals that should mean smaller size or no trade
    RISK_SIGNALS = {
        "base_4plus", "base_3", "phase_4", "rs_below_50",
        "exhaustion_high", "exhaustion_de_risk", "regime_correction",
        "td_sell7_9", "emls_below_70"
    }

    for sig in signal_keys:
        if sig in WIN_SIGNALS:
            if win:
                good_signals.append(f"{sig} [OK] CONFIRMED")
            else:
                bad_signals.append(f"{sig} [X] DID NOT PROTECT")
        elif sig in RISK_SIGNALS:
            if not win:
                bad_signals.append(f"{sig} [!]️ RISK CONFIRMED")
            else:
                good_signals.append(f"{sig} (traded despite risk, still won)")

    # Max adverse excursion analysis
    mae_pct = min(pnl_pct, 0)   # Worst it got (approximation)
    mfe_pct = max(pnl_pct, 0)   # Best it got

    # Lesson generation -- rule-based, systematic
    lessons = []
    prescriptions = []

    # Exit timing analysis
    if win and pnl_pct < 10 and days_held > 30:
        lessons.append(f"Small win ({pnl_pct:+.1f}%) after {days_held}d -- possible early exit or thesis was weak")
        prescriptions.append("For EMLS 80+: hold minimum 20 bars before considering trim")

    if win and pnl_pct >= 25 and days_held < 15:
        lessons.append(f"Fast winner ({pnl_pct:+.1f}% in {days_held}d) -- consider holding partial")
        prescriptions.append("On +20% in <15d: sell only 25%, hold rest with trail stop")

    if not win and "STOP LOSS" in reason:
        lessons.append(f"Hard stop triggered ({pnl_pct:+.1f}%) -- stop discipline worked")
        if emls >= 80:
            prescriptions.append(f"EMLS {emls:.0f} stop triggered -- check if regime was wrong at entry")

    if not win and pnl_pct < -15:
        lessons.append(f"Large loss {pnl_pct:+.1f}% -- did stop fail or was size too large?")
        prescriptions.append("Review if position size was > PRISM rules at entry")

    # Phase signal accuracy
    if "phase_3" in signal_keys and win and pnl_pct >= 15:
        lessons.append(f"Phase 3 + win {pnl_pct:+.1f}% -- framework working")

    if "phase_2" in signal_keys and win:
        lessons.append(f"Phase 2 early entry paid off: {pnl_pct:+.1f}% in {days_held}d")
        prescriptions.append("Phase 2 entries: use 50% initial size + add on first pullback to 10EMA")

    if not lessons:
        lessons.append(f"Standard {'win' if win else 'loss'}: {pnl_pct:+.1f}% in {days_held}d. No edge violations detected.")

    if not prescriptions:
        prescriptions.append("Maintain current rules -- no framework adjustment needed from this trade.")

    return {
        "ticker":           ticker,
        "outcome":          outcome,
        "pnl_pct":          round(pnl_pct, 2),
        "pnl_usd":          round(pnl_usd, 2),
        "emls_score":       emls,
        "setup_type":       setup,
        "days_held":        days_held,
        "entry_price":      entry,
        "exit_price":       exit_p,
        "close_reason":     reason,
        "open_date":        open_date,
        "close_date":       close_date,
        "signals_active":   signal_keys,
        "good_signals":     good_signals,
        "bad_signals":      bad_signals,
        "lessons":          lessons,
        "prescriptions":    prescriptions,
        "date":             close_date,
        "analyzed_at":      datetime.now().isoformat(),
    }


def _write_md_report(pm: dict) -> Path:
    """Write human-readable post-mortem markdown to output/."""
    ticker     = pm["ticker"]
    close_date = pm["close_date"].replace("-", "")[:6]
    filename   = OPS_DIR / f"postmortem_{ticker}_{close_date}.md"
    OPS_DIR.mkdir(parents=True, exist_ok=True)

    outcome_emoji = "[OK]" if pm["outcome"] == "WIN" else ("[YLW]" if pm["outcome"] == "BREAK_EVEN" else "[X]")

    lines = [
        f"# Post-Mortem: {ticker} [{pm['outcome']}] {outcome_emoji}",
        f"",
        f"**Date:** {pm['close_date']}  |  **Days held:** {pm['days_held']}",
        f"**Entry:** ${pm['entry_price']:.2f}  |  **Exit:** ${pm['exit_price']:.2f}",
        f"**P&L:** {pm['pnl_pct']:+.2f}% (${pm['pnl_usd']:+,.0f})",
        f"**Exit reason:** {pm['close_reason']}",
        f"**Setup:** {pm['setup_type'].upper()}  |  **EMLS Score:** {pm['emls_score']:.0f}",
        f"",
        f"---",
        f"",
        f"## Signal Scorecard",
        f"",
        f"**Active signals at entry:**",
    ]
    for sig in pm["signals_active"]:
        lines.append(f"- `{sig}`")

    if pm["good_signals"]:
        lines += ["", "**[OK] Signals that confirmed:**"]
        for s in pm["good_signals"]:
            lines.append(f"- {s}")

    if pm["bad_signals"]:
        lines += ["", "**[!]️ Signals that failed/warned:**"]
        for s in pm["bad_signals"]:
            lines.append(f"- {s}")

    lines += [
        "",
        "---",
        "",
        "## Lessons",
        "",
    ]
    for i, lesson in enumerate(pm["lessons"], 1):
        lines.append(f"{i}. {lesson}")

    lines += [
        "",
        "## Prescriptions",
        "",
    ]
    for i, presc in enumerate(pm["prescriptions"], 1):
        lines.append(f"{i}. {presc}")

    lines += [
        "",
        "---",
        f"*Auto-generated by AlphaAbsolute I8 Post-Mortem Engine -- {pm['analyzed_at'][:19]}*",
    ]

    filename.write_text("\n".join(lines), encoding="utf-8")
    return filename


# ── Main Run ──────────────────────────────────────────────────────────────────

def run() -> dict:
    """
    Scan trade log for unprocessed closed trades.
    Generate post-mortems -> extract lessons -> trigger calibration.
    """
    today = date.today().isoformat()
    print(f"\n[I8 Post-Mortem] Running {today}")

    # Seed required files if missing — prevents crash on first EOD run
    if not LESSONS_FILE.exists():
        LESSONS_FILE.write_text("[]", encoding="utf-8")
        print(f"  [Seed] Created lessons.json (empty)")
    if not PROCESSED_FILE.exists():
        PROCESSED_FILE.write_text("[]", encoding="utf-8")
        print(f"  [Seed] Created processed_trades.json (empty)")

    trade_log = _load_trade_log()
    processed  = _load_processed()
    lessons    = _load_lessons()

    closed = [t for t in trade_log if t.get("action") == "CLOSE"]
    new_trades = [
        t for t in closed
        if f"{t.get('ticker','')}_{t.get('close_date','')}" not in processed
    ]

    if not new_trades:
        print("  No new closed trades to post-mortem")
        # Still write output file so runner doesn't flag as failed
        summary = {
            "date":            date.today().isoformat(),
            "new_postmortems": 0,
            "wins":            0,
            "losses":          0,
            "total_lessons":   len(lessons),
            "calibration":     {},
        }
        LESSONS_FILE.write_text(json.dumps(lessons, indent=2), encoding="utf-8")
        return summary

    print(f"  New closed trades: {len(new_trades)}")

    new_lessons = []
    wins = losses = 0

    for trade in new_trades:
        ticker = trade.get("ticker", "")
        pm     = _analyze_trade(trade)
        lessons.append(pm)
        new_lessons.append(pm)

        if pm["outcome"] == "WIN":
            wins += 1
        elif pm["outcome"] == "LOSS":
            losses += 1

        # Write markdown report
        md_path = _write_md_report(pm)
        print(f"  {'[OK]' if pm['outcome']=='WIN' else '[X]'} {ticker}: "
              f"{pm['pnl_pct']:+.1f}% ({pm['days_held']}d) -> {md_path.name}")
        if pm["lessons"]:
            print(f"     Lesson: {pm['lessons'][0]}")
        if pm["prescriptions"] and pm["prescriptions"][0] != "Maintain current rules -- no framework adjustment needed from this trade.":
            print(f"     Prescription: {pm['prescriptions'][0]}")

        # Mark as processed
        processed.add(f"{ticker}_{trade.get('close_date','')}")

    _save_lessons(lessons)
    _save_processed(processed)

    # ── Trigger Calibration ─────────────────────────────────────────────────
    print(f"\n  Triggering I9 calibration with {len(lessons)} total trades...")
    cal_result = calibrate(verbose=False)

    if cal_result:
        print(f"  I9 Win rate: {cal_result.get('win_rate',0):.1f}% | "
              f"Payoff: {cal_result.get('payoff_ratio',0):.2f}x | "
              f"EV: {cal_result.get('expected_value',0):+.2f}%/trade")

    # ── Weekly lesson digest ─────────────────────────────────────────────────
    if len(new_lessons) >= 1:
        _append_to_ops_log(new_lessons, cal_result)

    result = {
        "date":             today,
        "new_postmortems":  len(new_lessons),
        "wins":             wins,
        "losses":           losses,
        "total_lessons":    len(lessons),
        "calibration":      cal_result,
    }

    print(f"\n  Done: {len(new_lessons)} post-mortems | {wins}W / {losses}L | "
          f"{len(lessons)} total lessons in memory")
    return result


def _append_to_ops_log(new_lessons: list, cal: dict):
    """Append post-mortem summary to today's ops log."""
    today   = date.today().strftime("%y%m%d")
    ops_log = OPS_DIR / f"ops_log_{today}.md"
    OPS_DIR.mkdir(parents=True, exist_ok=True)

    lines = [
        f"\n## Post-Mortem Summary [{date.today().isoformat()}]",
        f"",
    ]
    for pm in new_lessons:
        emoji = "[OK]" if pm["outcome"] == "WIN" else "[X]"
        lines.append(f"- {emoji} **{pm['ticker']}** {pm['pnl_pct']:+.1f}% ({pm['days_held']}d): {pm['lessons'][0]}")

    if cal:
        lines += [
            f"",
            f"**Calibration:** Win={cal.get('win_rate',0):.0f}% | Payoff={cal.get('payoff_ratio',0):.1f}x | EV={cal.get('expected_value',0):+.1f}%",
        ]

    with open(ops_log, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    run()
