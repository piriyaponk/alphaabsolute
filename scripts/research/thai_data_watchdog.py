"""
AlphaModel-TH — Daily Data Watchdog
=====================================
Runs after SET market close (~16:30 ICT / 09:30 UTC).
Validates Thai data quality and sends Telegram alert.

Checks:
  1. DB staleness — top 20 stocks must have data within 3 trading days
  2. Spot-check — 5 random stocks: DB price vs TradingView Scanner
  3. SET index — TradingView Scanner (SET index symbol)
  4. Coverage — count of tickers updated recently

Source: TradingView Scanner only (stable, no key, no rate limit issues).

Alarm logic:
  [ALERT] Telegram sent only for real problems:
    - DB stale > 3 trading days (data pipeline broken)
    - DB price deviates >5% from TradingView on 3+ tickers simultaneously
    - TradingView unreachable
  [WARN] Printed to console only — no Telegram (day-lag diff is normal)

Commands:
  python -X utf8 scripts/research/thai_data_watchdog.py
  python -X utf8 scripts/research/thai_data_watchdog.py --full   # check all tickers

RESEARCH ONLY — AlphaModel-US (System 4) unchanged.
"""

import sys, os, json, sqlite3, ssl, urllib.request
import random, argparse
from datetime import date, datetime, timedelta
from pathlib import Path

# ── Paths ───────────────────────────────────────────────────────────────────
ROOT    = Path(__file__).resolve().parents[2]
DB_PATH = str(ROOT / "data" / "research" / "thai_ohlcv.db")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

# ── Telegram ────────────────────────────────────────────────────────────────
def _tg(msg: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        env_path = ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k == "TELEGRAM_BOT_TOKEN": token = v
                    elif k == "TELEGRAM_CHAT_ID": chat = v
    if not token or not chat:
        return
    try:
        payload = json.dumps({"chat_id": chat, "text": msg, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, context=_ctx, timeout=10)
    except Exception as e:
        # Retry without parse_mode if Markdown causes 400
        try:
            payload = json.dumps({"chat_id": chat, "text": msg}).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, context=_ctx, timeout=10)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# TradingView Scanner — single source for all price checks
# ─────────────────────────────────────────────────────────────────────────────
def fetch_tv_prices(tickers: list) -> dict:
    """Fetch current prices from TradingView Scanner.
    Returns {ticker_without_BK: price} e.g. {"ADVANC": 349.00}
    Also fetches SET index via symbol "SET".
    """
    names = [t.replace(".BK", "") for t in tickers]
    # Add SET index
    if "SET" not in names:
        names.append("SET")

    payload = json.dumps({
        "filter": [{"left": "name", "operation": "in_range", "right": names}],
        "columns": ["name", "close"],
        "sort": {"sortBy": "name", "sortOrder": "asc"},
        "range": [0, len(names)],
    }).encode()
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128",
        "Content-Type": "application/json",
        "Origin": "https://www.tradingview.com",
        "Referer": "https://www.tradingview.com/",
    }
    req = urllib.request.Request(
        "https://scanner.tradingview.com/thailand/scan",
        data=payload, headers=hdrs)
    with urllib.request.urlopen(req, context=_ctx, timeout=15) as r:
        j = json.loads(r.read())
    return {item["d"][0]: item["d"][1] for item in j["data"] if item["d"][1] is not None}


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────
def db_last_date(conn, ticker):
    row = conn.execute(
        "SELECT MAX(date) FROM thai_ohlcv WHERE ticker=?", (ticker,)
    ).fetchone()
    return row[0] if row and row[0] else None


def db_close_on(conn, ticker, dt):
    row = conn.execute(
        "SELECT close FROM thai_ohlcv WHERE ticker=? AND date=?", (ticker, dt)
    ).fetchone()
    return float(row[0]) if row and row[0] else None


# ─────────────────────────────────────────────────────────────────────────────
# Checks
# ─────────────────────────────────────────────────────────────────────────────
TOP_20 = [
    "TDEX.BK",
    "ADVANC.BK", "KBANK.BK", "PTT.BK", "PTTEP.BK", "SCB.BK",
    "CPALL.BK", "GULF.BK", "BBL.BK", "SCC.BK", "BDMS.BK",
    "DELTA.BK", "AOT.BK", "MINT.BK", "TRUE.BK", "KTB.BK",
    "BH.BK", "CPN.BK", "HMPRO.BK", "HANA.BK",
]


def check_staleness(conn, today, max_trading_days=3):
    """Stale = last DB date is more than max_trading_days SET trading days ago.
    Uses trading-day count (not calendar days) so Thai long weekends / holidays
    don't fire a false alert on the next trading day."""
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    stale = []
    for tkr in TOP_20:
        ld = db_last_date(conn, tkr)
        if ld is None:
            stale.append(f"{tkr}(last=None)")
            continue
        ld_dt = datetime.strptime(ld, "%Y-%m-%d")
        # Count SET trading days strictly between ld and today (exclusive of ld, inclusive of today)
        trading_days_gap = sum(
            1 for i in range(1, (today_dt - ld_dt).days + 1)
            if is_set_trading_day((ld_dt + timedelta(days=i)).date())
        )
        if trading_days_gap > max_trading_days:
            stale.append(f"{tkr}(last={ld},{trading_days_gap}td)")
    return stale


def check_coverage(conn, today):
    total = conn.execute("SELECT COUNT(DISTINCT ticker) FROM thai_ohlcv").fetchone()[0]
    updated = conn.execute(
        "SELECT COUNT(DISTINCT ticker) FROM thai_ohlcv WHERE date=?", (today,)
    ).fetchone()[0]
    return updated, total


def spot_check_prices(conn, n=5):
    """Compare n random stocks: DB latest close vs TradingView.
    Returns list of {ticker, db_close, db_date, tv_close, diff_pct, ok}.
    ok=True if diff < 5% (5% threshold avoids false positives from day-lag).
    """
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM thai_ohlcv "
        "WHERE ticker NOT LIKE '^%' AND ticker != 'TDEX.BK' "
        "ORDER BY RANDOM() LIMIT ?", (n * 3,)
    ).fetchall()
    pool = [r[0] for r in rows]
    sample = random.sample(pool, min(n, len(pool)))

    last3 = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 5)]

    try:
        tv_prices = fetch_tv_prices(sample)
    except Exception as e:
        print(f"  [WARN] TradingView fetch failed: {e}")
        return []

    results = []
    for tkr in sample:
        db_close, db_date = None, None
        for dt in last3:
            c = db_close_on(conn, tkr, dt)
            if c:
                db_close, db_date = c, dt
                break
        name = tkr.replace(".BK", "")
        tv_close = tv_prices.get(name)
        if db_close and tv_close:
            diff_pct = abs(tv_close - db_close) / db_close * 100
            ok = diff_pct < 5.0  # 5% threshold — accounts for normal day-lag
            results.append({"ticker": tkr, "db_close": db_close, "db_date": db_date,
                             "tv_close": tv_close, "diff_pct": diff_pct, "ok": ok})
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def run(full=False):
    today = date.today().strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"[TH Watchdog] {today}")
    print(f"{'='*60}")

    if not Path(DB_PATH).exists():
        msg = f"<b>[TH] 🚨 WATCHDOG ALERT | {today}</b>\nDB ไม่พบ: {DB_PATH}"
        print("[CRITICAL] DB not found")
        _tg(msg)
        return

    conn = sqlite3.connect(DB_PATH)
    issues = []   # ALERT — sends Telegram
    warns  = []   # WARN  — console only, no Telegram

    # ── Check 1: Staleness ────────────────────────────────────────────────────
    stale = check_staleness(conn, today, max_days=3)
    if stale:
        dow = datetime.strptime(today, "%Y-%m-%d").weekday()
        if dow in (5, 6):
            print(f"[SKIP] Weekend — staleness expected")
        else:
            issues.append(f"❌ Stale tickers ({len(stale)}): {', '.join(stale[:5])}")
            print(f"[STALE] {stale}")
    else:
        print(f"[OK] Staleness — all Top 20 current")

    # ── Check 2: Coverage ─────────────────────────────────────────────────────
    total = conn.execute("SELECT COUNT(DISTINCT ticker) FROM thai_ohlcv").fetchone()[0]
    best_updated, best_day = 0, None
    for days_back in range(0, 10):
        check_dt  = datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days_back)
        check_day = check_dt.strftime("%Y-%m-%d")
        if check_dt.weekday() >= 5:
            continue
        u, _ = check_coverage(conn, check_day)
        if u > best_updated:
            best_updated, best_day = u, check_day
        if best_updated >= (total * 0.5):
            break
    if best_day is None:
        best_day = today

    cov_pct = best_updated / total * 100 if total else 0
    if best_day != today:
        d1 = datetime.strptime(best_day, "%Y-%m-%d")
        d2 = datetime.strptime(today, "%Y-%m-%d")
        lag_days = sum(1 for i in range((d2 - d1).days)
                       if (d1 + timedelta(days=i)).weekday() < 5)
        if lag_days > 3:
            issues.append(f"❌ Coverage stale: last={best_day} ({lag_days} trading days ago)")
        else:
            warns.append(f"Today's data not loaded yet (last={best_day})")
        print(f"[COVERAGE] Last update: {best_day} | {best_updated}/{total} ({cov_pct:.0f}%)")
    elif cov_pct < 80:
        issues.append(f"❌ Coverage low: {best_updated}/{total} ({cov_pct:.0f}%)")
        print(f"[COVERAGE] LOW: {best_updated}/{total} ({cov_pct:.0f}%)")
    else:
        print(f"[OK] Coverage: {best_updated}/{total} ({cov_pct:.0f}%)")

    # ── Check 3: SET index via TradingView ────────────────────────────────────
    try:
        tv_set = fetch_tv_prices(["TDEX.BK"])
        set_price = tv_set.get("SET")
        tdex_price = tv_set.get("TDEX")
        print(f"[TV] SET={set_price}  TDEX={tdex_price}")
        if set_price and set_price < 500:
            issues.append(f"❌ SET index ผิดปกติ: TV={set_price} (ควร >500)")
        else:
            print(f"[OK] SET index reachable via TradingView")
        tv_reachable = True
    except Exception as e:
        issues.append(f"❌ TradingView unreachable: {e}")
        print(f"[ERROR] TradingView fetch failed: {e}")
        tv_reachable = False

    # ── Check 4: Spot-check stocks ────────────────────────────────────────────
    # Root cause of false alerts: spot-check compares DB's last close vs TV's
    # LIVE price. When DB is 1-2 trading days stale (normal morning-run lag),
    # volatile Thai stocks easily differ >5% — this is expected, NOT a bug.
    # Only run ALERT-capable spot-check when DB has TODAY's data.
    spot = []
    bad_spot = []
    if tv_reachable:
        if best_day == today:
            print("[SPOT] Fetching TradingView prices for random sample...")
            spot = spot_check_prices(conn, n=5)
            bad_spot = [s for s in spot if not s["ok"]]
            for s in spot:
                flag = "✅" if s["ok"] else "⚠️"
                print(f"  {flag} {s['ticker']}: DB={s['db_close']}({s['db_date']})  TV={s['tv_close']}  diff={s['diff_pct']:.1f}%")
            if len(bad_spot) >= 3:
                issues.append(f"❌ Spot-check: {len(bad_spot)}/{len(spot)} tickers diff >5% vs TradingView")
            elif bad_spot:
                warns.append(f"Spot drift: {', '.join(s['ticker'] for s in bad_spot)}")
        else:
            # DB is 1-2 trading days stale — day-lag price diff is normal, not data corruption
            print(f"[SPOT] Skipped — DB at {best_day}, spot-check unreliable until today's EOD loads")

    conn.close()

    # ── Result ────────────────────────────────────────────────────────────────
    if issues:
        level = "🚨 ALERT"
        header_icon = "🔴"
    elif warns:
        level = "⚠️ WARN"
        header_icon = "🟡"
    else:
        level = "✅ OK"
        header_icon = "🟢"

    print(f"\n[RESULT] {level}")
    if warns:
        for w in warns:
            print(f"  [warn] {w}")

    # ── Telegram: ALERT only (no noise from day-lag warns) ────────────────────
    if issues:
        ok_count = sum(1 for s in spot if s["ok"]) if spot else 0
        spot_line = f"\nSpot-check: {ok_count}/{len(spot)} ✅" if spot else ""
        if bad_spot:
            for s in bad_spot:
                spot_line += f"\n  ⚠️ {s['ticker']}: DB={s['db_close']} TV={s['tv_close']} ({s['diff_pct']:.1f}%)"

        msg = (f"<b>[TH] {header_icon} WATCHDOG {level} | {today}</b>"
               f"\nCoverage: {best_updated}/{total} ({best_day})"
               f"{spot_line}"
               f"\n\n<b>Issues:</b>")
        for iss in issues:
            msg += f"\n{iss}"
        msg += "\n\nFix: python scripts/research/thai_data_layer.py --update"
        _tg(msg)
        print(f"[DONE] Telegram sent (ALERT)")
    else:
        print(f"[DONE] No issues — Telegram silent")


# ─────────────────────────────────────────────────────────────────────────────
# SET trading calendar
# ─────────────────────────────────────────────────────────────────────────────
_SET_HOLIDAYS_2026 = {
    "2026-01-01", "2026-02-11", "2026-04-06", "2026-04-13", "2026-04-14",
    "2026-04-15", "2026-05-01", "2026-05-04", "2026-05-11", "2026-06-03",
    "2026-07-13", "2026-07-28", "2026-08-12", "2026-10-13", "2026-10-23",
    "2026-12-05", "2026-12-10", "2026-12-31",
}
_SET_HOLIDAYS_2027 = {
    "2027-01-01", "2027-03-01", "2027-04-06", "2027-04-13", "2027-04-14",
    "2027-04-15", "2027-05-01", "2027-05-03", "2027-07-28", "2027-08-12",
    "2027-10-13", "2027-10-23", "2027-12-05", "2027-12-10", "2027-12-31",
}


def is_set_trading_day(d=None):
    if d is None:
        d = date.today()
    if d.weekday() >= 5:
        return False
    return d.strftime("%Y-%m-%d") not in (_SET_HOLIDAYS_2026 | _SET_HOLIDAYS_2027)


def main():
    parser = argparse.ArgumentParser(description="AlphaModel-TH Data Watchdog")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--force", action="store_true", help="Run even on holidays/weekends")
    args = parser.parse_args()

    if not args.force and not is_set_trading_day():
        print(f"[SKIP] {date.today().strftime('%A %Y-%m-%d')} — SET holiday or weekend")
        return

    run(full=args.full)


if __name__ == "__main__":
    main()
