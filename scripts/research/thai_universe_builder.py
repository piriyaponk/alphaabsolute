"""
AlphaModel-TH — SET Universe Builder
=====================================
Downloads full SET universe (~500 tickers) from Yahoo Finance,
filters by ADTV >= 6M THB, and saves to data/research/set_universe.json

Run:
  python scripts/research/thai_universe_builder.py
"""

import ssl, urllib.request, json, time, warnings
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

warnings.filterwarnings("ignore")

START = "2024-01-01"   # 2yr for ADTV calc
END   = datetime.today().strftime("%Y-%m-%d")
ADTV_MIN_THB = 6_000_000   # 6M THB avg daily traded

# Full SET-listed tickers (common .BK names — ~300 liquid names)
SET_FULL = [
    # Banks / Finance
    "KBANK.BK","BBL.BK","SCB.BK","KTB.BK","BAY.BK","TISCO.BK","KKP.BK","TMB.BK",
    "MTC.BK","SAWAD.BK","TIDLOR.BK","THCOM.BK","AEONTH.BK","ASK.BK","CIMBT.BK",
    # Energy / Petrochem
    "PTT.BK","PTTEP.BK","PTTGC.BK","TOP.BK","IRPC.BK","BCP.BK","BAFS.BK",
    "SPRC.BK","LANNA.BK","BANPU.BK",
    # Infrastructure / Utilities / Renewables
    "EGCO.BK","GULF.BK","GPSC.BK","RATCH.BK","EA.BK","BGRIM.BK","TPIPP.BK",
    "CKP.BK","BCPG.BK","SPCG.BK","SUPER.BK","ACE.BK","ESSO.BK",
    # Telecom / Tech
    "ADVANC.BK","INTUCH.BK","TRUE.BK","DTAC.BK","JAS.BK","INET.BK",
    "MFEC.BK","SIS.BK","SVOA.BK","CSL.BK",
    # Consumer / Retail
    "CPALL.BK","CPN.BK","HMPRO.BK","COM7.BK","BJC.BK","TU.BK","CBG.BK","OSP.BK",
    "MINT.BK","CENTEL.BK","ERW.BK","MAJOR.BK","MEGA.BK","TNP.BK","JMART.BK",
    "SINGER.BK","AU.BK","JMT.BK","BEAUTY.BK","SYNEX.BK","DOHOME.BK",
    # Real Estate
    "AP.BK","LH.BK","QH.BK","SIRI.BK","SPALI.BK","SC.BK","ORI.BK","PS.BK",
    "LPN.BK","NOBLE.BK","EVER.BK","PRUKSA.BK","LALIN.BK","RML.BK","MJD.BK",
    # Transport / Logistics
    "AOT.BK","BEM.BK","AAV.BK","BA.BK","NOK.BK","THAI.BK","BTS.BK",
    "TTA.BK","PSL.BK","TGR.BK","LEO.BK",
    # Healthcare
    "BDMS.BK","BH.BK","BCH.BK","CHG.BK","PR9.BK","RJH.BK","SVH.BK",
    "SKR.BK","RAM.BK","VIBHA.BK","AHC.BK","NHC.BK","PRINC.BK",
    # Industrial / Materials / Packaging
    "SCC.BK","SCGP.BK","IVL.BK","INOX.BK","STGT.BK","TKN.BK",
    "TPBI.BK","PYLON.BK","TASCO.BK","TIPCO.BK","SUSCO.BK",
    # Electronics / Hard Disk / Semiconductor
    "HANA.BK","KCE.BK","DELTA.BK","SVI.BK","CCET.BK","AJ.BK",
    "SMT.BK","TCC.BK","SYNTEC.BK",
    # Media / Entertainment
    "BEC.BK","WORK.BK","GMM.BK","RS.BK","MCOT.BK",
    # Agriculture / Food / Agro
    "CPF.BK","TFG.BK","GFPT.BK","TVO.BK","ASIAN.BK","NRF.BK",
    "MALEE.BK","SORKON.BK","STA.BK",
    # Insurance
    "BLA.BK","THRE.BK","TQM.BK","OIC.BK",
    # Mining / Natural Resources
    "THL.BK","TMILL.BK",
    # Property / REIT
    "WHA.BK","AMATA.BK","ROJNA.BK","HEMRAJ.BK",
    # Other liquid SET names
    "GLOBAL.BK","SEAFCO.BK","SAPPE.BK","KAMART.BK",
    "MOSHI.BK","DDD.BK","PCSGH.BK","ITEL.BK","LEA.BK",
    "PTL.BK","M.BK","MACO.BK","ITD.BK","SPA.BK",
    "TPARK.BK","RPCX.BK","WICE.BK","SAAM.BK","MAKRO.BK",
    "TH.BK","COTTO.BK","TRC.BK","NTV.BK","ANAN.BK","HYDRO.BK",
]

def _fetch(ticker: str, verbose: bool = False) -> tuple:
    s = int(datetime.strptime(START, "%Y-%m-%d").timestamp())
    e = int(datetime.strptime(END,   "%Y-%m-%d").timestamp())
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1d&period1={s}&period2={e}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            d = json.loads(resp.read())
        res     = d["chart"]["result"][0]
        ts      = (pd.to_datetime(res["timestamp"], unit="s", utc=True)
                  .tz_convert("Asia/Bangkok")
                  .tz_localize(None))
        closes  = res["indicators"]["quote"][0]["close"]
        volumes = res["indicators"]["quote"][0].get("volume", [None]*len(closes))
        c = pd.Series(closes,  index=ts, dtype=float).dropna()
        v = pd.Series(volumes, index=ts, dtype=float)
        return c, v
    except Exception as ex:
        if verbose:
            print(f"    [{ticker}] failed: {ex}")
        return pd.Series(dtype=float), pd.Series(dtype=float)


def main():
    print("=" * 60)
    print("AlphaModel-TH — SET Universe Builder")
    print(f"ADTV filter: >= {ADTV_MIN_THB/1e6:.0f}M THB")
    print("=" * 60)

    # Deduplicate
    tickers = list(dict.fromkeys(SET_FULL))
    print(f"\nFetching {len(tickers)} tickers (2yr for ADTV)...")

    passed, failed = [], []
    adtv_data = {}

    for i, tkr in enumerate(tickers):
        c, v = _fetch(tkr)
        if len(c) < 50:
            failed.append(tkr)
        else:
            # ADTV = avg(close × volume) over last 126 trading days (~6 months)
            common_idx = c.index.intersection(v.index)
            if len(common_idx) < 50:
                failed.append(tkr)
                continue
            turnover = c[common_idx] * v[common_idx].fillna(0)
            adtv     = turnover.tail(126).mean()
            adtv_data[tkr] = adtv
            if adtv >= ADTV_MIN_THB:
                passed.append(tkr)
            else:
                failed.append(tkr)

        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(tickers)} done... ({len(passed)} pass ADTV so far)")
        time.sleep(0.10)

    # Sort by ADTV descending
    passed_sorted = sorted(passed, key=lambda t: adtv_data.get(t, 0), reverse=True)

    print(f"\n{'='*60}")
    print(f"ADTV >= {ADTV_MIN_THB/1e6:.0f}M THB: {len(passed_sorted)} tickers pass")
    print(f"Below threshold / no data: {len(failed)} tickers")
    print(f"\nTop 30 by ADTV:")
    print(f"{'Ticker':<14} {'ADTV (M THB)':>14}")
    print("-" * 30)
    for t in passed_sorted[:30]:
        print(f"{t:<14} {adtv_data[t]/1e6:>13.1f}M")

    # Save universe
    out = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "adtv_min_thb": ADTV_MIN_THB,
        "universe": passed_sorted,
        "count": len(passed_sorted),
        "adtv_by_ticker": {t: round(adtv_data.get(t, 0)/1e6, 1) for t in passed_sorted},
    }
    (ROOT / "data" / "research").mkdir(parents=True, exist_ok=True)
    with open(ROOT / "data" / "research" / "set_universe.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\n[Saved] data/research/set_universe.json — {len(passed_sorted)} tickers")
    print("\nNext: use this universe in thai_combined.py for proper full-SET backtest")


if __name__ == "__main__":
    main()
