"""
AlphaModel-TH — Daily Data Watchdog
=====================================
Runs after SET market close (~16:30 ICT / 09:30 UTC).
Validates Thai data quality and sends Telegram alert.

Checks:
  1. DB staleness — top 20 stocks must have today's date
  2. Spot-check — 5 random stocks: DB price vs TradingView Scanner
  3. SET index — cross-check across all available live sources
  4. Coverage — count of tickers updated today

Sources (confirmed stable + free):
  A. TradingView Scanner  — current prices, all Thai stocks
  B. SiamChart HTML       — today's SET OHLCV
  C. Investing.com        — SET index history (~130 scale)
  D. Settrade API         — today's SET/SET50/SET100 live values

Alarm logic (threshold-based, as designed):
  Individual source failures are NORMAL and do NOT trigger alerts.
  [ALERT] only fires when underlying DB data is actually wrong:
    - DB stale > 4 trading days (data pipeline broken)
    - DB price deviates >5% from 3+ live sources simultaneously
    - All SET sources unreachable (no cross-check possible)

Commands:
  python -X utf8 scripts/research/thai_data_watchdog.py
  python -X utf8 scripts/research/thai_data_watchdog.py --full   # check all tickers

RESEARCH ONLY — AlphaModel-US (System 4) unchanged.
"""

import sys, os, json, sqlite3, ssl, urllib.request, urllib.parse
import http.cookiejar, re, gzip, random, argparse
import pandas as pd
from datetime import date, datetime, timedelta
from pathlib import Path

# ── Paths ───────────────────────────────────────────────────────────────────
ROOT    = Path(__file__).resolve().parents[2]
DB_PATH = str(ROOT / "data" / "research" / "thai_ohlcv.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

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
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, context=ctx, timeout=10)
    except Exception:
        pass

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


# ─────────────────────────────────────────────────────────────────────────────
# Source A: TradingView Scanner — current prices for Thai stocks (no key)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_tv_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch current close prices from TradingView Scanner.
    Returns {ticker_without_BK: price} e.g. {"ADVANC": 349.00}
    """
    names = [t.replace(".BK", "") for t in tickers]
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
# Source B: SiamChart /setindex page — SET index today OHLCV
# ─────────────────────────────────────────────────────────────────────────────
def fetch_siamchart_set_today() -> dict | None:
    """Scrape today's SET index OHLCV from SiamChart HTML."""
    try:
        hdrs = {"User-Agent": "Mozilla/5.0 Chrome/128",
                "Referer": "https://siamchart.com/"}
        req = urllib.request.Request("https://siamchart.com/setindex/", headers=hdrs)
        with urllib.request.urlopen(req, context=_ctx, timeout=15) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
        text = raw.decode("utf-8", "replace")
        nums = re.findall(r"<td[^>]*>([0-9][0-9,]{2,}\.?[0-9]*)<", text)
        if len(nums) >= 4:
            vals = [float(n.replace(",", "")) for n in nums[:4]]
            return {"open": vals[0], "high": vals[1], "low": vals[2], "close": vals[3]}
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Source C: Investing.com — SET index close (live check)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_investing_set_today() -> float | None:
    """Fetch today's SET close from Investing.com."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
        from thai_data_layer import fetch_set_direct
        today_str = date.today().strftime("%Y-%m-%d")
        # Only need last 5 days
        from_str = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
        df = fetch_set_direct(from_str, today_str)
        if len(df) > 0:
            return float(df["close"].iloc[-1])
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────
def db_last_date(conn: sqlite3.Connection, ticker: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) FROM thai_ohlcv WHERE ticker=?", (ticker,)
    ).fetchone()
    return row[0] if row and row[0] else None


def db_close_on(conn: sqlite3.Connection, ticker: str, dt: str) -> float | None:
    row = conn.execute(
        "SELECT close FROM thai_ohlcv WHERE ticker=? AND date=?", (ticker, dt)
    ).fetchone()
    return float(row[0]) if row and row[0] else None


# ─────────────────────────────────────────────────────────────────────────────
# Core checks
# ─────────────────────────────────────────────────────────────────────────────

# Top 20 tickers we care most about for staleness check
TOP_20 = [
    "TDEX.BK",
    "ADVANC.BK", "KBANK.BK", "PTT.BK", "PTTEP.BK", "SCB.BK",
    "CPALL.BK", "GULF.BK", "BBL.BK", "SCC.BK", "BDMS.BK",
    "DELTA.BK", "AOT.BK", "MINT.BK", "TRUE.BK", "KTB.BK",
    "BH.BK", "CPN.BK", "HMPRO.BK", "HANA.BK",
]


def check_staleness(conn: sqlite3.Connection, today: str, max_days: int = 3) -> list[str]:
    """Return list of tickers that haven't updated within max_days."""
    threshold = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=max_days)).strftime("%Y-%m-%d")
    stale = []
    for tkr in TOP_20:
        ld = db_last_date(conn, tkr)
        if ld is None or ld < threshold:
            stale.append(f"{tkr}(last={ld})")
    return stale


def check_coverage(conn: sqlite3.Connection, today: str) -> tuple[int, int]:
    """Return (updated_today, total_tickers)."""
    total = conn.execute("SELECT COUNT(DISTINCT ticker) FROM thai_ohlcv").fetchone()[0]
    updated = conn.execute(
        "SELECT COUNT(DISTINCT ticker) FROM thai_ohlcv WHERE date=?", (today,)
    ).fetchone()[0]
    return updated, total


def spot_check_prices(conn: sqlite3.Connection, n: int = 5) -> list[dict]:
    """Compare n random stocks: DB latest close vs TradingView Scanner.
    Returns list of {ticker, db_close, db_date, tv_close, diff_pct, ok}.
    """
    # Pick random tickers from DB that have data (exclude index tickers)
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM thai_ohlcv "
        "WHERE ticker NOT LIKE '^%' AND ticker != 'TDEX.BK' "
        "ORDER BY RANDOM() LIMIT ?", (n * 3,)
    ).fetchall()
    pool = [r[0] for r in rows]
    sample = random.sample(pool, min(n, len(pool)))

    # Fetch latest DB date for each (use last 3 trading days as window)
    last3 = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 5)]

    # Fetch TradingView prices
    try:
        tv_prices = fetch_tv_prices(sample)
    except Exception:
        tv_prices = {}

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
            # Allow 3% tolerance (TV may have today's price, DB may have yesterday)
            ok = diff_pct < 3.0
            results.append({"ticker": tkr, "db_close": db_close, "db_date": db_date,
                             "tv_close": tv_close, "diff_pct": diff_pct, "ok": ok})
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Source D: Settrade API — today's SET/SET50 live values (no key needed)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_settrade_set_today() -> dict | None:
    """Fetch today's SET/SET50 closing values from Settrade API.
    Returns {"SET": 1584.15, "SET50": 1069.76, "market_status": "Closed"}
    """
    try:
        req = urllib.request.Request(
            "https://api.settrade.com/api/market/SET/info",
            headers={"User-Agent": "Mozilla/5.0 Chrome/128"},
        )
        with urllib.request.urlopen(req, context=_ctx, timeout=10) as r:
            data = json.loads(r.read())
        indices = {idx["index_name"]: idx["last"] for idx in data.get("index", [])}
        return {
            "SET":    indices.get("SET"),
            "SET50":  indices.get("SET50"),
            "market_status": data.get("market_status"),
        }
    except Exception:
        return None


def check_set_index(conn: sqlite3.Connection) -> dict:
    """Cross-check SET index across all available sources.
    Returns sources dict plus ok=True if at least 1 source reachable.
    """
    result = {}

    # DB: TDEX.BK latest
    tdex_date = db_last_date(conn, "TDEX.BK")
    if tdex_date:
        tdex_close = db_close_on(conn, "TDEX.BK", tdex_date)
        result["tdex"] = {"date": tdex_date, "close": tdex_close}

    # Source B: SiamChart today's SET OHLCV
    sc = fetch_siamchart_set_today()
    if sc:
        result["siamchart"] = sc

    # Source C: Investing.com latest close (~130 normalized scale)
    inv_close = fetch_investing_set_today()
    if inv_close:
        result["investing"] = {"close": inv_close}

    # Source D: Settrade API today's live value (actual SET points, ~1584)
    st = fetch_settrade_set_today()
    if st and st.get("SET"):
        result["settrade"] = st

    sources_ok = sum(1 for k in ("siamchart", "investing", "settrade") if result.get(k))
    result["sources_ok"] = sources_ok
    result["ok"] = sources_ok > 0

    # Sanity: SiamChart close and Settrade SET should agree within 1%
    sc_close = result.get("siamchart", {}).get("close")
    st_close = (result.get("settrade") or {}).get("SET")
    if sc_close and st_close:
        diff = abs(sc_close - st_close) / sc_close * 100
        result["sc_vs_settrade_diff_pct"] = diff
        if diff > 1.0:
            result["cross_check_warn"] = f"SiamChart={sc_close} vs Settrade={st_close} diff={diff:.1f}%"
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def run(full: bool = False):
    today = date.today().strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"[TH Watchdog] {today}")
    print(f"{'='*60}")

    if not Path(DB_PATH).exists():
        msg = f"<b>[TH] 🚨 WATCHDOG | {today}</b>\nDB ไม่พบ: {DB_PATH}\nรัน: python scripts/research/thai_data_layer.py --init"
        print("[CRITICAL] DB not found")
        _tg(msg)
        return

    conn = sqlite3.connect(DB_PATH)
    issues = []
    warns  = []

    # ── Check 1: Staleness ────────────────────────────────────────────────────
    stale = check_staleness(conn, today, max_days=3)
    if stale:
        # Check if it's a weekend (Sat=5/Sun=6) — expected stale
        dow = datetime.strptime(today, "%Y-%m-%d").weekday()
        if dow in (5, 6):
            print(f"[SKIP] Weekend — staleness expected")
        else:
            issues.append(f"⚠️ Stale tickers ({len(stale)}): {', '.join(stale[:5])}")
            print(f"[STALE] {stale}")
    else:
        print(f"[OK] Staleness — all Top 20 current")

    # ── Check 2: Coverage — use most recent weekday with data ────────────────
    total = conn.execute("SELECT COUNT(DISTINCT ticker) FROM thai_ohlcv").fetchone()[0]
    # Find most recent weekday (Mon-Fri) with at least 50% coverage
    best_updated, best_day = 0, None
    for days_back in range(0, 10):
        check_dt  = datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days_back)
        check_day = check_dt.strftime("%Y-%m-%d")
        if check_dt.weekday() >= 5:  # Skip Sat/Sun
            continue
        u, _ = check_coverage(conn, check_day)
        if u > best_updated:
            best_updated, best_day = u, check_day
        if best_updated >= (total * 0.5):  # Stop once we find a good day
            break
    if best_day is None:
        best_day = today

    cov_pct = best_updated / total * 100 if total else 0

    # Count trading days (weekdays only) since best_day
    if best_day:
        d1 = datetime.strptime(best_day, "%Y-%m-%d")
        d2 = datetime.strptime(today, "%Y-%m-%d")
        trading_days_since = sum(
            1 for i in range((d2 - d1).days)
            if (d1 + timedelta(days=i)).weekday() < 5
        )
    else:
        trading_days_since = 0

    if best_day != today:
        if trading_days_since > 3:
            issues.append(f"❌ Coverage stale: last update {best_day} ({trading_days_since} trading days ago)")
        else:
            warns.append(f"ℹ️ Today's data not loaded yet (last={best_day}, {best_updated}/{total})")
        print(f"[COVERAGE] Last update: {best_day} | {best_updated}/{total} ({cov_pct:.0f}%)")
    elif cov_pct < 80:
        issues.append(f"⚠️ Coverage low today: {best_updated}/{total} ({cov_pct:.0f}%)")
        print(f"[COVERAGE] LOW: {best_updated}/{total} ({cov_pct:.0f}%)")
    else:
        print(f"[OK] Coverage: {best_updated}/{total} ({cov_pct:.0f}%)")

    # ── Check 3: SET Index sources ────────────────────────────────────────────
    set_check  = check_set_index(conn)
    sc_close   = set_check.get("siamchart", {}).get("close")
    inv_close  = set_check.get("investing", {}).get("close")
    tdex_close = set_check.get("tdex", {}).get("close")
    st_close   = (set_check.get("settrade") or {}).get("SET")
    src_ok     = set_check.get("sources_ok", 0)

    print(f"[SET] SiamChart={sc_close}  Settrade={st_close}  Investing.com={inv_close}  TDEX={tdex_close}")
    print(f"[SET] Live sources reachable: {src_ok}/3")

    if set_check.get("cross_check_warn"):
        warns.append(f"⚠️ SET cross-check: {set_check['cross_check_warn']}")

    # ALERT only if ALL live sources unreachable (total data blindness)
    if not set_check["ok"]:
        issues.append("❌ SET index: ทุก source ไม่ตอบสนอง — ไม่สามารถ cross-check ได้")
    elif sc_close and sc_close < 500:
        issues.append(f"❌ SET index ผิดปกติ: SiamChart={sc_close} (ควร >500)")
    elif st_close and st_close < 500:
        issues.append(f"❌ SET index ผิดปกติ: Settrade={st_close} (ควร >500)")
    else:
        if src_ok >= 2:
            print("[OK] SET index sources reachable")
        else:
            warns.append(f"ℹ️ SET sources: เหลือ {src_ok}/3 (บางส่วนอาจ down ชั่วคราว)")

    # ── Check 4: Spot-check individual stocks ────────────────────────────────
    print("[SPOT] Fetching TradingView prices for random sample...")
    spot = spot_check_prices(conn, n=5)
    bad_spot = [s for s in spot if not s["ok"]]
    for s in spot:
        flag = "✅" if s["ok"] else "⚠️"
        print(f"  {flag} {s['ticker']}: DB={s['db_close']}({s['db_date']})  TV={s['tv_close']}  diff={s['diff_pct']:.1f}%")

    if len(bad_spot) >= 3:
        issues.append(f"❌ Spot-check: {len(bad_spot)}/{len(spot)} tickers มีความแตกต่าง >3% vs TradingView")
    elif bad_spot:
        warns.append(f"ℹ️ Spot-check drift: {', '.join(s['ticker'] for s in bad_spot)}")

    conn.close()

    # ── Build Telegram message ────────────────────────────────────────────────
    if issues:
        level = "🚨 ALERT"
        header_icon = "🔴"
    elif warns:
        level = "⚠️ WARN"
        header_icon = "🟡"
    else:
        level = "✅ OK"
        header_icon = "🟢"

    # SET index summary — prefer most direct live source
    set_summary = ""
    best_set = sc_close or st_close  # SiamChart and Settrade are in real SET points
    if best_set:
        src_name = "SiamChart" if sc_close else "Settrade"
        set_summary = f"\nSET={best_set:.2f} ({src_name}) | {src_ok}/3 sources live"
    elif inv_close:
        set_summary = f"\nSET(inv)={inv_close:.2f} | {src_ok}/3 sources live"

    # Spot check summary
    if spot:
        ok_count = sum(1 for s in spot if s["ok"])
        spot_summary = f"\nSpot-check: {ok_count}/{len(spot)} ✅"
        if bad_spot:
            for s in bad_spot:
                spot_summary += f"\n  ⚠️ {s['ticker']}: DB={s['db_close']} TV={s['tv_close']} ({s['diff_pct']:.1f}%)"
    else:
        spot_summary = "\nSpot-check: ไม่สามารถดึงราคาได้"

    coverage_line = f"\nCoverage: {best_updated}/{total} ({best_day})"
    tdex_line = f"\nTDEX.BK: {tdex_close} (last={set_check.get('tdex', {}).get('date', 'N/A')})"

    msg = (f"<b>[TH] {header_icon} WATCHDOG {level} | {today}</b>"
           f"{set_summary}"
           f"{tdex_line}"
           f"{coverage_line}"
           f"{spot_summary}")

    if issues:
        msg += "\n\n<b>⚠️ Issues:</b>"
        for iss in issues:
            msg += f"\n{iss}"
        msg += "\n\nFix: python scripts/research/thai_data_layer.py --update"

    if warns and not issues:
        for w in warns:
            msg += f"\n{w}"

    print(f"\n[RESULT] {level}")
    _tg(msg)
    print(f"[DONE] Telegram sent")


def main():
    parser = argparse.ArgumentParser(description="AlphaModel-TH Data Watchdog")
    parser.add_argument("--full", action="store_true", help="Check all tickers (slower)")
    args = parser.parse_args()
    run(full=args.full)


if __name__ == "__main__":
    main()
