"""
AlphaAbsolute Lifetime Learning Runner
=======================================
Runs after market close daily. Called by run_eod.bat after the EOD pipeline.

Learning loop:
  1. Read paper_portfolio_state.json -- find closed positions
  2. Log newly closed trades to data/learning/trades_closed_log.json (idempotent)
  3. Compute rolling stats (last 20): win rate, avg winner, avg loser, expectancy
  4. If month-end (day 1): run framework_calibrator Bayesian update
  5. Write data/learning/learning_stats.json
  6. Send Telegram: WinRate / AvgWin / Expectancy / N

Idempotent: trade_id = ticker_entrydate_exitdate deduplicates log entries.
Never raises: per-step try/except -- learning failure never blocks EOD.
UTF-8 stdout for Windows cp874 environments.
"""

from __future__ import annotations
import sys
import os
import json
import importlib.util
from datetime import datetime, date, timezone
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure") and _s.encoding and             _s.encoding.lower() in ("cp874", "cp1252", "ascii"):
        _s.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR     = Path(__file__).resolve().parents[2]
LEARNING_DIR = BASE_DIR / "data" / "learning"
LEARNING_DIR.mkdir(parents=True, exist_ok=True)

PAPER_STATE_PATH = BASE_DIR / "data" / "portfolio" / "paper_portfolio_state.json"
CLOSED_LOG_PATH  = LEARNING_DIR / "trades_closed_log.json"
STATS_PATH       = LEARNING_DIR / "learning_stats.json"

TODAY = date.today()


def _load_env() -> None:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for ln in env_path.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()


def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [WARN] Could not load {path.name}: {e}")
    return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _make_trade_id(trade: dict) -> str:
    ticker     = trade.get("ticker", "UNKNOWN")
    entry_date = trade.get("entry_date", "")
    exit_date  = trade.get("exit_date", trade.get("close_date", ""))
    return f"{ticker}_{entry_date}_{exit_date}"


def _collect_new_closed_trades() -> list:
    """Read paper_portfolio_state.json and log newly closed trades. Idempotent."""
    raw = _load_json(PAPER_STATE_PATH, None)
    if raw is None:
        print("  [SKIP] paper_portfolio_state.json not found -- no closed trades.")
        return []

    all_positions: list = []
    if isinstance(raw, dict):
        for key in ("closed_positions", "trades", "history", "positions"):
            if key in raw and isinstance(raw[key], list):
                all_positions.extend(raw[key])
        if not all_positions:
            for v in raw.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    all_positions.extend(v)
    elif isinstance(raw, list):
        all_positions = raw

    closed = []
    for pos in all_positions:
        status = str(pos.get("status", "")).upper()
        if status in ("CLOSED", "EXITED", "SOLD"):
            closed.append(pos)
        elif pos.get("exit_date") or pos.get("close_date"):
            closed.append(pos)

    if not closed:
        print("  [INFO] No closed positions found.")
        return []

    existing_log: list = _load_json(CLOSED_LOG_PATH, [])
    seen_ids = {e["trade_id"] for e in existing_log if "trade_id" in e}

    new_trades = []
    for trade in closed:
        tid = _make_trade_id(trade)
        if tid not in seen_ids:
            ep  = float(trade.get("entry_price", 0) or 0)
            xp  = float(trade.get("exit_price", 0) or trade.get("close_price", 0) or 0)
            pnl = float(trade.get("pnl_pct", 0) or trade.get("return_pct", 0) or 0)
            if pnl == 0 and ep > 0 and xp > 0:
                pnl = ((xp / ep) - 1) * 100
            entry = {
                "trade_id":    tid,
                "logged_at":   datetime.now(tz=timezone.utc).isoformat(),
                "ticker":      trade.get("ticker", "UNKNOWN"),
                "mode":        trade.get("mode", "A"),
                "entry_date":  trade.get("entry_date", ""),
                "exit_date":   trade.get("exit_date") or trade.get("close_date", ""),
                "entry_price": ep,
                "exit_price":  xp,
                "pnl_pct":     round(pnl, 2),
                "exit_reason": trade.get("exit_reason", trade.get("reason", "")),
                "setup_type":  trade.get("setup_type", trade.get("setup", "")),
                "grade":       trade.get("grade", ""),
            }
            new_trades.append(entry)
            seen_ids.add(tid)

    if new_trades:
        existing_log.extend(new_trades)
        _save_json(CLOSED_LOG_PATH, existing_log)
        print(f"  [OK] Logged {len(new_trades)} new closed trade(s).")
    else:
        print(f"  [INFO] No new closed trades (all {len(closed)} already logged).")

    return new_trades


def _compute_stats() -> dict:
    """Rolling stats over last 20 closed trades. n_total covers full history."""
    log = _load_json(CLOSED_LOG_PATH, [])
    if not log:
        return {
            "as_of": TODAY.isoformat(), "n_total": 0, "n_window": 0,
            "win_rate": None, "avg_win_pct": None, "avg_loss_pct": None,
            "expectancy_pct": None, "note": "No closed trades yet.",
        }
    window  = log[-20:]
    winners = [t["pnl_pct"] for t in window if t.get("pnl_pct", 0) > 0]
    losers  = [t["pnl_pct"] for t in window if t.get("pnl_pct", 0) <= 0]
    n_total  = len(winners) + len(losers)
    win_rate = (len(winners) / n_total * 100) if n_total > 0 else 0.0
    avg_win  = sum(winners) / len(winners) if winners else 0.0
    avg_loss = sum(losers)  / len(losers)  if losers  else 0.0
    loss_rate  = 1.0 - win_rate / 100
    expectancy = (win_rate / 100 * avg_win) + (loss_rate * avg_loss)
    return {
        "as_of":          TODAY.isoformat(),
        "n_total":        len(log),
        "n_window":       len(window),
        "win_rate":       round(win_rate, 1),
        "avg_win_pct":    round(avg_win, 2),
        "avg_loss_pct":   round(avg_loss, 2),
        "expectancy_pct": round(expectancy, 2),
        "last_trade":     log[-1].get("trade_id", "") if log else "",
    }


def _maybe_run_calibration() -> bool:
    """Run framework_calibrator on day 1 of each month."""
    if TODAY.day != 1:
        return False
    print("  [INFO] Month-end -- running Bayesian calibration...")
    path = BASE_DIR / "scripts" / "pre_compute" / "framework_calibrator.py"
    if not path.exists():
        print("  [WARN] framework_calibrator.py not found.")
        return False
    try:
        spec   = importlib.util.spec_from_file_location("framework_calibrator", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn = getattr(module, "calibrate", None)
        if fn is None:
            print("  [WARN] No calibrate() function in framework_calibrator.")
            return False
        fn()
        print("  [OK] Bayesian calibration complete.")
        return True
    except Exception as e:
        print(f"  [WARN] Calibration failed: {e}")
        return False


def _send_telegram(stats: dict, new_trades: list) -> None:
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("  [SKIP] Telegram not configured (TELEGRAM_BOT_TOKEN/CHAT_ID missing).")
        return
    if stats.get("n_total", 0) == 0:
        print("  [SKIP] No trades yet -- skipping Telegram.")
        return
    wr  = stats.get("win_rate")
    aw  = stats.get("avg_win_pct")
    al  = stats.get("avg_loss_pct")
    exp = stats.get("expectancy_pct")
    nw  = stats.get("n_window", 0)
    n   = stats.get("n_total", 0)
    win_str = f"{wr:.1f}%" if wr is not None else "N/A"
    aw_str  = f"+{aw:.1f}%" if aw is not None else "N/A"
    al_str  = f"{al:.1f}%"  if al is not None else "N/A"
    exp_str = f"{exp:.2f}%" if exp is not None else "N/A"
    new_part = ""
    if new_trades:
        lines = []
        for t in new_trades[:3]:
            pnl    = t.get("pnl_pct", 0)
            sign   = "+" if pnl >= 0 else ""
            tkr    = t.get("ticker", "??")
            reason = t.get("exit_reason", "?")
            lines.append(f"  {tkr}: {sign}{pnl:.1f}% ({reason})")
        new_part = chr(10) + "Closed today:" + chr(10) + chr(10).join(lines)
        if len(new_trades) > 3:
            extra = len(new_trades) - 3
            new_part += chr(10) + f"  ...+{extra} more"
    _NL = chr(10)
    msg = (
        f"Learning Update [{TODAY.isoformat()}]" + _NL
        + f"WinRate={win_str} | AvgWin={aw_str} | AvgLoss={al_str}" + _NL
        + f"Expectancy={exp_str} | N={n} (window={nw})"
        + new_part
    )
    try:
        import urllib.request, ssl
        payload = json.dumps({"chat_id": chat_id, "text": msg}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"},
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        with urllib.request.urlopen(req, context=ctx, timeout=10):
            pass
        print("  [OK] Telegram learning summary sent.")
    except Exception as e:
        print(f"  [WARN] Telegram send failed: {e}")


def run() -> None:
    sep = "=" * 56
    print("")
    print(sep)
    print(f"  AlphaAbsolute Lifetime Learning Runner [{TODAY}]")
    print(sep)

    print("  [Step 1/5] Checking for newly closed paper trades...")
    new_trades = _collect_new_closed_trades()

    print("  [Step 2/5] Computing rolling performance stats...")
    stats = _compute_stats()
    n  = stats.get("n_total", 0)
    wr = stats.get("win_rate")
    ex = stats.get("expectancy_pct")
    if wr is not None:
        print(f"  N={n} total | WinRate={wr:.1f}% | Expectancy={ex:.2f}%")
    else:
        print(f"  N={n} -- no stats yet.")

    print("  [Step 3/5] Saving learning_stats.json...")
    _save_json(STATS_PATH, stats)
    print(f"  [OK] {STATS_PATH.name} written.")

    print("  [Step 4/5] Checking month-end calibration...")
    cal = _maybe_run_calibration()
    if not cal and TODAY.day != 1:
        print(f"  [SKIP] Not month-end (day={TODAY.day}).")

    print("  [Step 5/5] Sending Telegram summary...")
    _send_telegram(stats, new_trades)

    print(sep)
    print("  Learning runner complete.")
    print(sep)
    print("")


if __name__ == "__main__":
    run()
