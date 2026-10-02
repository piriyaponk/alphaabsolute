"""
fetch_forward_estimates.py — Daily forward EPS/Rev estimate fetcher
=====================================================================
Fetches analyst consensus forward estimates for RS>=90 + ADTV>=15M universe
from Yahoo Finance quoteSummary (free, no API key required).

Outputs:
  data/fundamentals/forward_estimates.json   — latest snapshot (overwritten daily)
  data/fundamentals/forward_estimates_history.db  — SQLite daily archive

Fields per ticker:
  eps_fwd_growth_pct   — next FY EPS vs current FY EPS (%)
  rev_fwd_growth_pct   — next FY Rev vs current FY Rev (%)
  eps_fwd_next1y       — next FY EPS estimate (raw $)
  rev_fwd_next1y       — next FY Rev estimate (raw $)
  fwd_earn_growth_yf   — YF financialData earningsGrowth (TTM forward proxy)
  fwd_rev_growth_yf    — YF financialData revenueGrowth (TTM forward proxy)
  target_price         — analyst mean price target
  n_analysts           — number of analysts covering

Run: python scripts/pre_compute/fetch_forward_estimates.py
"""

import json
import sqlite3
import sys
import time
import warnings
from datetime import date, datetime
from pathlib import Path

import requests

warnings.filterwarnings("ignore")

BASE_DIR   = Path(__file__).resolve().parents[2]
RS_PATH    = BASE_DIR / "data" / "rs_universe" / "latest.json"
OUT_JSON   = BASE_DIR / "data" / "fundamentals" / "forward_estimates.json"
OUT_DB     = BASE_DIR / "data" / "fundamentals" / "forward_estimates_history.db"

ADTV_MIN   = 15_000_000
RS_MIN     = 90
SLEEP_SEC  = 0.4   # between tickers — polite to Yahoo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ── Yahoo session ──────────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    s = requests.Session()
    s.verify = False
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://finance.yahoo.com/",
    })
    return s


def _get_crumb(session: requests.Session) -> str | None:
    try:
        session.get("https://finance.yahoo.com/", timeout=15, allow_redirects=True)
    except Exception:
        pass
    for url in [
        "https://query2.finance.yahoo.com/v1/test/getcrumb",
        "https://query1.finance.yahoo.com/v1/test/getcrumb",
    ]:
        try:
            r = session.get(url, timeout=10)
            if r.status_code == 200 and r.text and len(r.text) < 50:
                return r.text.strip()
        except Exception:
            continue
    return None


# ── Universe loader ────────────────────────────────────────────────────────────

def load_universe() -> list[str]:
    with open(RS_PATH) as f:
        data = json.load(f)
    uni = data.get("universe", data)
    tickers = [
        t for t, v in uni.items()
        if float(v.get("rs_composite_pct") or 0) >= RS_MIN
        and float(v.get("adtv_6m_usd") or 0) >= ADTV_MIN
    ]
    return sorted(tickers)


# ── Per-ticker fetch ───────────────────────────────────────────────────────────

def fetch_estimates(ticker: str, session: requests.Session, crumb: str) -> dict:
    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
    params = {"modules": "earningsTrend,financialData", "crumb": crumb}
    try:
        r = session.get(url, params=params, timeout=12)
        if not r.ok:
            return {"ticker": ticker, "error": f"HTTP {r.status_code}"}
        result = r.json().get("quoteSummary", {}).get("result", [{}])[0]
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}

    et = result.get("earningsTrend", {}).get("trend", [])
    fd = result.get("financialData", {})

    fwd_1y = next((t for t in et if t.get("period") == "+1y"), {})
    cur_y  = next((t for t in et if t.get("period") == "0y"), {})

    def _raw(d, *keys):
        for k in keys:
            d = d.get(k, {}) if isinstance(d, dict) else {}
        return d.get("raw") if isinstance(d, dict) else d

    eps_next = _raw(fwd_1y, "earningsEstimate", "avg")
    eps_cur  = _raw(cur_y,  "earningsEstimate", "avg")
    rev_next = _raw(fwd_1y, "revenueEstimate",  "avg")
    rev_cur  = _raw(cur_y,  "revenueEstimate",  "avg")
    n_analysts = _raw(fwd_1y, "numberOfAnalysts")

    eps_fwd = round((eps_next / eps_cur - 1) * 100, 1) if eps_next and eps_cur and eps_cur > 0 else None
    rev_fwd = round((rev_next / rev_cur - 1) * 100, 1) if rev_next and rev_cur and rev_cur > 0 else None

    return {
        "ticker":              ticker,
        "eps_fwd_growth_pct":  eps_fwd,
        "rev_fwd_growth_pct":  rev_fwd,
        "eps_fwd_next1y":      eps_next,
        "rev_fwd_next1y":      rev_next,
        "fwd_earn_growth_yf":  _raw(fd, "earningsGrowth"),
        "fwd_rev_growth_yf":   _raw(fd, "revenueGrowth"),
        "target_price":        _raw(fd, "targetMeanPrice"),
        "n_analysts":          int(n_analysts) if n_analysts else None,
        "fetched_at":          datetime.now().isoformat(),
    }


# ── SQLite archive ─────────────────────────────────────────────────────────────

def _init_db(db_path: Path):
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS forward_estimates (
            date               TEXT,
            ticker             TEXT,
            eps_fwd_growth_pct REAL,
            rev_fwd_growth_pct REAL,
            eps_fwd_next1y     REAL,
            rev_fwd_next1y     REAL,
            fwd_earn_growth_yf REAL,
            fwd_rev_growth_yf  REAL,
            target_price       REAL,
            n_analysts         INTEGER,
            PRIMARY KEY (date, ticker)
        )
    """)
    con.commit()
    return con


def _upsert_db(con: sqlite3.Connection, today: str, records: list[dict]):
    rows = [
        (
            today,
            r["ticker"],
            r.get("eps_fwd_growth_pct"),
            r.get("rev_fwd_growth_pct"),
            r.get("eps_fwd_next1y"),
            r.get("rev_fwd_next1y"),
            r.get("fwd_earn_growth_yf"),
            r.get("fwd_rev_growth_yf"),
            r.get("target_price"),
            r.get("n_analysts"),
        )
        for r in records if "error" not in r
    ]
    con.executemany("""
        INSERT OR REPLACE INTO forward_estimates
        (date,ticker,eps_fwd_growth_pct,rev_fwd_growth_pct,
         eps_fwd_next1y,rev_fwd_next1y,fwd_earn_growth_yf,
         fwd_rev_growth_yf,target_price,n_analysts)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, rows)
    con.commit()


# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    today = date.today().isoformat()
    print(f"\n[fwd-estimates] {today}")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    tickers = load_universe()
    print(f"Universe: {len(tickers)} tickers (RS>={RS_MIN} + ADTV>=${ADTV_MIN/1e6:.0f}M)")

    session = _make_session()
    crumb   = _get_crumb(session)
    if not crumb:
        print("[fwd-estimates] ERROR: could not get Yahoo crumb — aborting")
        return False

    print(f"Fetching estimates for {len(tickers)} tickers...")
    records = []
    ok_count = 0
    for i, tkr in enumerate(tickers, 1):
        rec = fetch_estimates(tkr, session, crumb)
        records.append(rec)
        if "error" not in rec:
            ok_count += 1
            eps = rec.get("eps_fwd_growth_pct")
            rev = rec.get("rev_fwd_growth_pct")
            print(f"  [{i:02d}/{len(tickers)}] {tkr:<8} eps_fwd={eps}%  rev_fwd={rev}%")
        else:
            print(f"  [{i:02d}/{len(tickers)}] {tkr:<8} ERROR: {rec['error']}")
        time.sleep(SLEEP_SEC)

    # ── Save JSON snapshot ─────────────────────────────────────────────────────
    out = {
        "date":        today,
        "generated_at": datetime.now().isoformat(),
        "n_total":     len(tickers),
        "n_ok":        ok_count,
        "records":     records,
        "ticker_index": {r["ticker"]: r for r in records if "error" not in r},
    }
    OUT_JSON.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\n[fwd-estimates] Saved → {OUT_JSON.name} ({ok_count}/{len(tickers)} OK)")

    # ── Archive to SQLite ──────────────────────────────────────────────────────
    con = _init_db(OUT_DB)
    _upsert_db(con, today, records)
    con.close()
    print(f"[fwd-estimates] Archived → {OUT_DB.name}")

    # ── Quick summary ──────────────────────────────────────────────────────────
    ok_recs = [r for r in records if "error" not in r and r.get("eps_fwd_growth_pct") is not None]
    if ok_recs:
        avg_eps = sum(r["eps_fwd_growth_pct"] for r in ok_recs) / len(ok_recs)
        avg_rev = sum(r["rev_fwd_growth_pct"] for r in ok_recs if r.get("rev_fwd_growth_pct") is not None) / max(1, sum(1 for r in ok_recs if r.get("rev_fwd_growth_pct") is not None))
        high_conv = [r["ticker"] for r in ok_recs if (r.get("eps_fwd_growth_pct") or 0) > 25 and (r.get("rev_fwd_growth_pct") or 0) > 15]
        print(f"\nSummary: avg eps_fwd={avg_eps:.1f}%  avg_rev_fwd={avg_rev:.1f}%")
        print(f"High conviction (eps>25% + rev>15%): {len(high_conv)} tickers: {high_conv}")

    return True


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
