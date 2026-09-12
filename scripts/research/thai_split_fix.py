"""
AlphaModel-TH — Split Artifact Detector & Auto-Fix
====================================================
Scans thai_ohlcv.db for single-day returns > ±THRESHOLD (default 40%).
These are split/reverse-split artifacts where Yahoo's adjusted close
had an inconsistency on the event date.

Fix: DELETE all rows for the flagged ticker, then re-fetch from Yahoo
today so the full series is consistently forward-adjusted.

Usage:
  python scripts/research/thai_split_fix.py             # scan + fix
  python scripts/research/thai_split_fix.py --scan-only # report only, no changes
  python scripts/research/thai_split_fix.py --threshold 0.30  # custom threshold
"""

import sys, ssl, urllib.request, json, time, argparse, sqlite3
import pandas as pd
from datetime import datetime, date, timedelta
from pathlib import Path

DB_PATH    = str(Path(__file__).resolve().parents[2] / "data" / "research" / "thai_ohlcv.db")
START_HIST = "2015-01-01"
THRESHOLD  = 0.40   # 40% single-day return = almost certainly a split artifact


# ─── Yahoo fetch (same as thai_data_layer.py) ────────────────────────────────

def fetch_yahoo(ticker: str, start: str, end: str) -> pd.DataFrame:
    s = int(datetime.strptime(start, "%Y-%m-%d").timestamp())
    e = int(datetime.strptime(end,   "%Y-%m-%d").timestamp())
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1d&period1={s}&period2={e}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE

    last_exc = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
                d = json.loads(resp.read())
            res = d["chart"]["result"][0]
            ts  = (pd.to_datetime(res["timestamp"], unit="s", utc=True)
                     .tz_convert("Asia/Bangkok")
                     .tz_localize(None))
            q   = res["indicators"]["quote"][0]
            df  = pd.DataFrame({
                "open":   q.get("open",   [None]*len(ts)),
                "high":   q.get("high",   [None]*len(ts)),
                "low":    q.get("low",    [None]*len(ts)),
                "close":  q.get("close",  [None]*len(ts)),
                "volume": q.get("volume", [None]*len(ts)),
            }, index=ts)
            return df.dropna(subset=["close"])
        except urllib.error.HTTPError as ex:
            if ex.code in (404, 400):
                raise
            last_exc = ex
            time.sleep(2)
        except (urllib.error.URLError, TimeoutError) as ex:
            last_exc = ex
            time.sleep(2)
    raise last_exc


# ─── Scan ─────────────────────────────────────────────────────────────────────

def scan_artifacts(conn: sqlite3.Connection, threshold: float) -> list[dict]:
    """Return list of {ticker, date, return_pct, prev_close, curr_close}."""
    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM thai_ohlcv WHERE ticker != '^SET.BK' ORDER BY ticker"
    ).fetchall()]

    artifacts = []
    for tkr in tickers:
        rows = conn.execute(
            "SELECT date, close FROM thai_ohlcv WHERE ticker=? AND close IS NOT NULL ORDER BY date",
            (tkr,)
        ).fetchall()
        if len(rows) < 2:
            continue
        for i in range(1, len(rows)):
            prev_dt, prev_c = rows[i-1]
            curr_dt, curr_c = rows[i]
            if prev_c and prev_c > 0:
                ret = (curr_c / prev_c) - 1
                if abs(ret) > threshold:
                    artifacts.append({
                        "ticker":     tkr,
                        "date":       curr_dt,
                        "return_pct": round(ret * 100, 1),
                        "prev_close": round(prev_c, 2),
                        "curr_close": round(curr_c, 2),
                    })
    return artifacts


# ─── Fix ──────────────────────────────────────────────────────────────────────

def fix_ticker(conn: sqlite3.Connection, ticker: str) -> str:
    """Delete all rows for ticker and re-fetch full history from Yahoo."""
    today = date.today().strftime("%Y-%m-%d")
    try:
        df = fetch_yahoo(ticker, START_HIST, today)
        if len(df) < 100:
            return f"SKIP — only {len(df)} bars fetched, keeping original"

        # Delete existing
        conn.execute("DELETE FROM thai_ohlcv WHERE ticker=?", (ticker,))

        # Re-insert with INSERT OR IGNORE (fresh start, so OR IGNORE = INSERT always)
        rows = []
        for dt, r in df.iterrows():
            rows.append((
                ticker,
                dt.strftime("%Y-%m-%d"),
                float(r["open"])   if pd.notna(r.get("open"))   else None,
                float(r["high"])   if pd.notna(r.get("high"))   else None,
                float(r["low"])    if pd.notna(r.get("low"))    else None,
                float(r["close"])  if pd.notna(r.get("close"))  else None,
                int(r["volume"])   if pd.notna(r.get("volume")) else None,
            ))
        conn.executemany(
            "INSERT OR IGNORE INTO thai_ohlcv (ticker,date,open,high,low,close,volume) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        return f"FIXED — deleted old rows, inserted {len(rows)} fresh bars"
    except Exception as ex:
        conn.rollback()
        return f"ERROR — {str(ex)[:80]}"


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Thai OHLCV split artifact fixer")
    parser.add_argument("--scan-only", action="store_true", help="Report only, no changes")
    parser.add_argument("--threshold", type=float, default=THRESHOLD,
                        help=f"Single-day return threshold (default {THRESHOLD})")
    args = parser.parse_args()

    if not Path(DB_PATH).exists():
        print("ERROR: DB not found — run thai_data_layer.py --init first")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    print(f"Scanning for single-day |return| > {args.threshold*100:.0f}% ...")
    artifacts = scan_artifacts(conn, args.threshold)

    if not artifacts:
        print("No split artifacts found — DB is clean.")
        conn.close()
        return

    # Group by ticker
    by_ticker: dict[str, list] = {}
    for a in artifacts:
        by_ticker.setdefault(a["ticker"], []).append(a)

    print(f"\nFound {len(artifacts)} artifact(s) across {len(by_ticker)} ticker(s):\n")
    print(f"{'Ticker':<14} {'Date':>12}  {'Return':>8}  {'Prev':>8}  {'Curr':>8}")
    print("-" * 56)
    for tkr in sorted(by_ticker):
        for a in by_ticker[tkr]:
            sign = "▲" if a["return_pct"] > 0 else "▼"
            print(f"{tkr:<14} {a['date']:>12}  {sign}{abs(a['return_pct']):>6.1f}%"
                  f"  {a['prev_close']:>8.2f}  {a['curr_close']:>8.2f}")

    if args.scan_only:
        print("\n[scan-only mode] No changes made.")
        conn.close()
        return

    print(f"\nFixing {len(by_ticker)} ticker(s) — full re-fetch from Yahoo...")
    print("(This will DELETE and re-download each flagged ticker)\n")

    ok_count, fail_count = 0, 0
    for tkr in sorted(by_ticker):
        status = fix_ticker(conn, tkr)
        icon = "✅" if status.startswith("FIXED") else ("⚠️" if status.startswith("SKIP") else "❌")
        print(f"  {icon} {tkr:<14} {status}")
        if status.startswith("FIXED"):
            ok_count += 1
        else:
            fail_count += 1
        time.sleep(0.5)

    conn.close()
    print(f"\nDone.  fixed={ok_count}  failed/skipped={fail_count}")
    print("\nRun --scan-only again to confirm all artifacts are gone.")


if __name__ == "__main__":
    main()
