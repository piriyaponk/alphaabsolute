"""
AlphaModel-TH — Data Layer
===========================
SQLite storage for full SET universe price history.
Mirrors the US ohlcv.db architecture.

Schema: thai_ohlcv (ticker, date, open, high, low, close, volume)
Source: Yahoo Finance query2 (.BK tickers) — free, no API key needed

ADTV filter (applied at backtest time): avg 6-month daily turnover >= 20M THB

Commands:
  python scripts/research/thai_data_layer.py --init      # first-time bulk download (~20 min)
  python scripts/research/thai_data_layer.py --update    # daily incremental update (~26s)
  python scripts/research/thai_data_layer.py --status    # show coverage stats
"""

import sys, ssl, urllib.request, json, time, warnings, argparse, sqlite3
import pandas as pd
from datetime import datetime, date, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")

# W4: absolute path — safe regardless of CWD
DB_PATH    = str(Path(__file__).resolve().parents[2] / "data" / "research" / "thai_ohlcv.db")
START_HIST = "2015-01-01"   # 10yr history for backtest + MA200 warmup

# ADTV threshold for investable universe (applied at backtest/screen time, not here)
ADTV_MIN_THB = 20_000_000   # 20M THB avg daily turnover (6-month lookback)

# ─────────────────────────────────────────────────────────────────────────────
# Full SET universe — ~260 tickers covering SET + MAI + sSET
# Sorted roughly by market cap descending
# ─────────────────────────────────────────────────────────────────────────────
SET_ALL_TICKERS = [
    # SET Index (benchmark — kept for regime gate, exclude from stock screens)
    "^SET.BK",

    # ── Large Cap / SET50 ─────────────────────────────────────────────────────
    # Banks
    "KBANK.BK","BBL.BK","SCB.BK","KTB.BK","BAY.BK","TISCO.BK","KKP.BK","CIMBT.BK",
    # Energy / Petrochem
    "PTT.BK","PTTEP.BK","PTTGC.BK","TOP.BK","IRPC.BK","BCP.BK","BAFS.BK","SPRC.BK",
    "ESSO.BK","SUSCO.BK",
    # Telecom
    "ADVANC.BK","INTUCH.BK","TRUE.BK","DTAC.BK","JAS.BK",
    # Consumer / Retail
    "CPALL.BK","CPN.BK","HMPRO.BK","COM7.BK","BJC.BK","MAKRO.BK","ROBINS.BK",
    # Utilities / Renewables
    "GULF.BK","GPSC.BK","RATCH.BK","EGCO.BK","EA.BK","BGRIM.BK","TPIPP.BK",
    "CKP.BK","BCPG.BK","SPCG.BK","SUPER.BK","ACE.BK","WHA.BK",
    # Transport
    "AOT.BK","BEM.BK","BTS.BK","THAI.BK","AAV.BK","BA.BK","NOK.BK",
    # Healthcare
    "BDMS.BK","BH.BK","BCH.BK","CHG.BK","PR9.BK","RJH.BK","SVH.BK",
    "PRINC.BK","VIBHA.BK","NHC.BK","SKR.BK","RAM.BK","AHC.BK",
    # Industrial / Packaging
    "SCC.BK","SCGP.BK","IVL.BK","INOX.BK","STGT.BK","TPBI.BK",
    "PYLON.BK","TASCO.BK","TIPCO.BK",
    # Electronics / HDD / PCB
    "HANA.BK","KCE.BK","DELTA.BK","SVI.BK","CCET.BK","SMT.BK","SNC.BK",
    "TCC.BK","SYNTEC.BK","AJ.BK",
    # Finance / Leasing
    "MTC.BK","SAWAD.BK","TIDLOR.BK","AEONTH.BK","ASK.BK","TMB.BK",
    "THCOM.BK","GL.BK","SINGER.BK","BFIT.BK",
    # Real Estate
    "AP.BK","LH.BK","QH.BK","SIRI.BK","SPALI.BK","SC.BK","ORI.BK","PS.BK",
    "LPN.BK","NOBLE.BK","EVER.BK","LALIN.BK","RML.BK","MJD.BK","ANAN.BK",
    "AMATA.BK","ROJNA.BK","HEMRAJ.BK","TPARK.BK",
    # Food / Agro
    "CPF.BK","TFG.BK","GFPT.BK","TU.BK","CBG.BK","OSP.BK","TVO.BK",
    "ASIAN.BK","NRF.BK","MALEE.BK","STA.BK","SORKON.BK","NWR.BK",
    # Media / Entertainment
    "BEC.BK","WORK.BK","GMM.BK","RS.BK","MCOT.BK","MAJOR.BK",
    # Insurance
    "BLA.BK","THRE.BK","TQM.BK","BKI.BK",
    # Hospitality / Tourism
    "MINT.BK","CENTEL.BK","ERW.BK","DUSIT.BK","AWC.BK",
    # Mining / Natural Resources
    "BANPU.BK","LANNA.BK","THL.BK","TMILL.BK",
    # Tech / IT Services
    "MFEC.BK","SIS.BK","SVOA.BK","CSL.BK","INET.BK","ITEL.BK","DDD.BK",
    "PCSGH.BK","LEA.BK",

    # ── Mid Cap / SET100 ──────────────────────────────────────────────────────
    "JMART.BK","JMT.BK","AU.BK","BEAUTY.BK","SYNEX.BK","DOHOME.BK","TKN.BK",
    "TNP.BK","MEGA.BK","GLOBAL.BK","SAPPE.BK","SEAFCO.BK",
    "TTA.BK","PSL.BK","TGR.BK","LEO.BK",
    "COTTO.BK","TRC.BK","M.BK","MACO.BK","ITD.BK","SPA.BK",
    "WICE.BK","SAAM.BK","NTV.BK","HYDRO.BK","PTL.BK",
    "TH.BK","PTTMEP.BK","KAMART.BK","MOSHI.BK",

    # ── sSET / Smaller SET ────────────────────────────────────────────────────
    "YUASA.BK","STANLY.BK","IRCP.BK",
    "SMIT.BK","CSP.BK","CHARAN.BK",
    "RICHY.BK","BLAND.BK","KBS.BK","SWC.BK",
    "DIMET.BK","TOPP.BK",
    "PRG.BK","BSBM.BK","TRUBB.BK",
    "UPOIC.BK","KASET.BK","TRT.BK",
    "NSI.BK","SMPC.BK","TFD.BK",
    "BMCL.BK","NNCL.BK","EASTW.BK",
    "TTW.BK","JWD.BK","WHAUP.BK","WHABT.BK",
    "TPIPL.BK","TPIPM.BK",
    "VGI.BK","PLANB.BK","MOGA.BK",
    "CI.BK","CSC.BK","ALUCON.BK",
    "NFC.BK","BIG.BK","CMO.BK","UV.BK",

    # ── MAI Market ────────────────────────────────────────────────────────────
    "MOANA.BK","AYUD.BK","LIT.BK","SOLAR.BK",
    "AIMIRT.BK","TMI.BK","ICC.BK",
    "BBGI.BK","GUNKUL.BK","DEMCO.BK","SKN.BK",
    "KOOL.BK","BTW.BK","NUSA.BK","PLAT.BK",
    "SENA.BK","PROUD.BK","PKORP.BK",
    "EKH.BK","MEDEZE.BK","GJS.BK","BMH.BK",
    "KUN.BK","PATO.BK","TIGER.BK",
    "FORTH.BK","MLINK.BK","NETBAY.BK",
    "WINNER.BK","HUMAN.BK","FVC.BK",
    "BGC.BK","SCCC.BK","CRANE.BK",
]

# Deduplicate preserving order
_seen = set()
_deduped = []
for _t in SET_ALL_TICKERS:
    if _t not in _seen:
        _seen.add(_t)
        _deduped.append(_t)
SET_ALL_TICKERS = _deduped


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS thai_ohlcv (
            ticker  TEXT    NOT NULL,
            date    TEXT    NOT NULL,
            open    REAL,
            high    REAL,
            low     REAL,
            close   REAL,
            volume  INTEGER,
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ticker_date ON thai_ohlcv(ticker, date)")
    conn.commit()


def last_date(conn: sqlite3.Connection, ticker: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) FROM thai_ohlcv WHERE ticker = ?", (ticker,)
    ).fetchone()
    # fetchone() always returns a row; MAX on empty set returns (None,)
    return row[0] if row and row[0] else None


def upsert_bars(conn: sqlite3.Connection, ticker: str, df: pd.DataFrame) -> int:
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
    # C1: INSERT OR IGNORE — never overwrite existing historical bars
    conn.executemany(
        "INSERT OR IGNORE INTO thai_ohlcv (ticker,date,open,high,low,close,volume) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Yahoo Finance fetch  (W1: 1 retry on transient errors)
# ─────────────────────────────────────────────────────────────────────────────

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
    for attempt in range(2):   # W1: retry once on transient failure
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
                d = json.loads(resp.read())
            res = d["chart"]["result"][0]
            # C3: convert to ICT (UTC+7) before dropping tz
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
                raise   # permanent — don't retry
            last_exc = ex
            time.sleep(2)
        except (urllib.error.URLError, TimeoutError) as ex:
            last_exc = ex
            time.sleep(2)
    raise last_exc


# ─────────────────────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────────────────────

def cmd_init(tickers: list[str]):
    """Bulk download all tickers from START_HIST to today."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    today = date.today().strftime("%Y-%m-%d")

    ok, skip, fail = 0, 0, 0
    fail_log = []
    print(f"Bulk download: {len(tickers)} tickers  {START_HIST} → {today}")
    print("(takes ~15-20 min for full universe)\n")

    for i, tkr in enumerate(tickers):
        ld = last_date(conn, tkr)
        if ld and ld >= today:
            skip += 1
            continue
        start = (datetime.strptime(ld, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d") \
                if ld else START_HIST
        try:
            df = fetch_yahoo(tkr, start, today)
            if len(df) == 0:
                fail += 1
                fail_log.append(f"{tkr}: empty response")
            else:
                upsert_bars(conn, tkr, df)
                ok += 1
        except Exception as ex:
            fail += 1
            # C2: log every failure with reason
            reason = str(ex)[:80]
            fail_log.append(f"{tkr}: {reason}")
        if (i + 1) % 25 == 0:
            print(f"  [{i+1}/{len(tickers)}]  ok={ok}  fail={fail}  skip={skip}")
        time.sleep(0.15)

    conn.close()
    print(f"\nDone.  ok={ok}  fail={fail}  skip={skip}")
    if fail_log:
        print(f"\nFailed tickers ({len(fail_log)}):")
        for line in fail_log:
            print(f"  {line}")
    print(f"\nDB: {DB_PATH}")


def cmd_update(tickers: list[str]):
    """Incremental update — fetch only new bars since last stored date."""
    # C4: guard — don't silently create empty DB on update
    if not Path(DB_PATH).exists():
        print("ERROR: DB not found — run --init first")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    today = date.today().strftime("%Y-%m-%d")
    ok, up2date, fail = 0, 0, 0
    fail_log = []

    split_flags = []
    for tkr in tickers:
        ld = last_date(conn, tkr)
        if ld and ld >= today:
            up2date += 1
            continue
        start = (datetime.strptime(ld, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d") \
                if ld else START_HIST
        try:
            df = fetch_yahoo(tkr, start, today)
            if len(df) > 0:
                # Split validation: check new bars against last stored close
                if ld:
                    prev_row = conn.execute(
                        "SELECT close FROM thai_ohlcv WHERE ticker=? AND date=?", (tkr, ld)
                    ).fetchone()
                    if prev_row and prev_row[0] and len(df) > 0:
                        first_new_close = df["close"].iloc[0]
                        prev_close = prev_row[0]
                        if prev_close > 0:
                            jump = abs(first_new_close / prev_close - 1)
                            if jump > 0.40:
                                split_flags.append(f"{tkr}: {jump*100:.0f}% jump at {ld}→{df.index[0].strftime('%Y-%m-%d')}")
                upsert_bars(conn, tkr, df)
                ok += 1
        except Exception as ex:
            fail += 1
            fail_log.append(f"{tkr}: {str(ex)[:80]}")
        time.sleep(0.10)

    conn.close()
    print(f"[thai_data_layer] update done  new={ok}  up_to_date={up2date}  fail={fail}")
    if split_flags:
        print(f"\n[SPLIT AUTO-FIX] {len(split_flags)} ticker(s) detected — re-fetching full history...")
        conn2 = sqlite3.connect(DB_PATH)
        today2 = date.today().strftime("%Y-%m-%d")
        for line in split_flags:
            tkr2 = line.split(":")[0]
            try:
                df2 = fetch_yahoo(tkr2, START_HIST, today2)
                if len(df2) >= 100:
                    conn2.execute("DELETE FROM thai_ohlcv WHERE ticker=?", (tkr2,))
                    rows2 = []
                    for dt2, r2 in df2.iterrows():
                        rows2.append((
                            tkr2, dt2.strftime("%Y-%m-%d"),
                            float(r2["open"])   if pd.notna(r2.get("open"))   else None,
                            float(r2["high"])   if pd.notna(r2.get("high"))   else None,
                            float(r2["low"])    if pd.notna(r2.get("low"))    else None,
                            float(r2["close"])  if pd.notna(r2.get("close"))  else None,
                            int(r2["volume"])   if pd.notna(r2.get("volume")) else None,
                        ))
                    conn2.executemany(
                        "INSERT OR IGNORE INTO thai_ohlcv (ticker,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
                        rows2,
                    )
                    conn2.commit()
                    print(f"  ✅ {tkr2} re-fetched {len(rows2)} bars")
                else:
                    print(f"  ⚠️  {tkr2} only {len(df2)} bars — skipped")
            except Exception as ex2:
                print(f"  ❌ {tkr2} fix failed: {str(ex2)[:60]}")
            time.sleep(0.5)
        conn2.close()
    if fail_log:
        for line in fail_log:
            print(f"  FAIL {line}")


def cmd_status():
    """Show coverage summary."""
    if not Path(DB_PATH).exists():
        print("DB not found — run --init first")
        return
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT ticker,
               COUNT(*) as bars,
               MIN(date) as first,
               MAX(date) as last
        FROM thai_ohlcv
        GROUP BY ticker
        ORDER BY bars DESC
    """).fetchall()

    # ADTV stats for top tickers
    adtv_rows = conn.execute("""
        SELECT ticker, AVG(close * COALESCE(volume, 0)) as adtv
        FROM (
            SELECT ticker, date, close, volume
            FROM thai_ohlcv
            WHERE date >= date('now', '-180 days')
              AND close IS NOT NULL
        )
        GROUP BY ticker
        HAVING adtv > 0
        ORDER BY adtv DESC
        LIMIT 20
    """).fetchall()
    conn.close()

    total_tickers = len(rows)
    total_bars    = sum(r[1] for r in rows)
    print(f"\nDB: {DB_PATH}")
    print(f"Tickers: {total_tickers}  |  Total bars: {total_bars:,}")
    if rows:
        dates = [r[2] for r in rows if r[2]]
        lasts = [r[3] for r in rows if r[3]]
        print(f"Date range: {min(dates)} → {max(lasts)}")

    print(f"\n{'Ticker':<14} {'Bars':>6}  {'First':>12}  {'Last':>12}")
    print("-" * 48)
    for tkr, bars, first, last in rows[:30]:
        print(f"{tkr:<14} {bars:>6}  {first:>12}  {last:>12}")
    if len(rows) > 30:
        print(f"  ... and {len(rows)-30} more")

    print(f"\nTop 20 by 6M ADTV (>= {ADTV_MIN_THB/1e6:.0f}M THB threshold):")
    print(f"{'Ticker':<14} {'ADTV (M THB)':>14}")
    print("-" * 30)
    for tkr, adtv in adtv_rows:
        flag = "✅" if adtv >= ADTV_MIN_THB else "  "
        print(f"{flag} {tkr:<12} {adtv/1e6:>12.1f}M")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # W7: Windows cp874 safety
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="AlphaModel-TH Data Layer")
    parser.add_argument("--init",   action="store_true", help="Bulk download all tickers")
    parser.add_argument("--update", action="store_true", help="Incremental update")
    parser.add_argument("--status", action="store_true", help="Coverage stats + ADTV")
    args = parser.parse_args()

    tickers = SET_ALL_TICKERS

    if args.status:
        cmd_status()
    elif args.update:
        print(f"Updating {len(tickers)} tickers...")
        cmd_update(tickers)
    else:
        cmd_init(tickers)


if __name__ == "__main__":
    main()
