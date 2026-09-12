"""
AlphaModel-TH — Combined Optimized Config
==========================================
RESEARCH ONLY — does NOT touch AlphaModel-US (System 4).

Takes the best signal from each dimension (isolated tests):
  Regime gate  : MA50  (Sharpe 2.19, CAGR +17.0%)
  RS lookback  : 21d / 1M  (Sharpe 2.30)
  RS method    : Vol-weighted  (Sharpe 2.11)
  Top N        : 10  (best CAGR/DD balance)
  Rebalance    : Weekly 5d  (best Sharpe in isolation)

Compares combined vs each isolated win vs baseline.

Run:
  python scripts/research/thai_combined.py
"""

import ssl, urllib.request, json, time, warnings, sqlite3
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

SET_UNIVERSE = [
    "KBANK.BK","BBL.BK","SCB.BK","KTB.BK","BAY.BK","TISCO.BK","KKP.BK",
    "MTC.BK","SAWAD.BK","TIDLOR.BK",
    "PTT.BK","PTTEP.BK","PTTGC.BK","TOP.BK","IRPC.BK","BCP.BK",
    "EGCO.BK","GULF.BK","GPSC.BK","RATCH.BK","EA.BK","BGRIM.BK","TPIPP.BK",
    "ADVANC.BK","INTUCH.BK",
    "CPALL.BK","CPN.BK","HMPRO.BK","COM7.BK","BJC.BK","TU.BK","CBG.BK","OSP.BK",
    "AP.BK","LH.BK","QH.BK","SIRI.BK","SPALI.BK","SC.BK",
    "AOT.BK","BEM.BK","AAV.BK","BA.BK",
    "BDMS.BK","BH.BK","BCH.BK","CHG.BK","PR9.BK",
    "SCC.BK","SCGP.BK","IVL.BK",
    "BEC.BK","WORK.BK",
    "CPF.BK","TFG.BK","GFPT.BK",
    "BLA.BK",
    "TRUE.BK","WHA.BK","AMATA.BK","MINT.BK","CENTEL.BK","ERW.BK",
    "MAJOR.BK","HANA.BK","DELTA.BK","KCE.BK","STA.BK","TKN.BK",
    "BEAUTY.BK","SYNEX.BK","DOHOME.BK","INOX.BK","CKP.BK","STGT.BK",
]

SET_INDEX = "^SET.BK"
START     = "2017-01-01"
END       = "2026-09-05"
TCOST_BUY  = 0.0015   # 0.15% per side (SET online broker standard)
TCOST_SELL = 0.0015   # 0.15% per side
TCOST_RT   = TCOST_BUY + TCOST_SELL  # 0.30% round trip
RF_ANNUAL  = 0.025


def _fetch(ticker: str) -> pd.Series:
    s = int(datetime.strptime(START, "%Y-%m-%d").timestamp())
    e = int(datetime.strptime(END,   "%Y-%m-%d").timestamp())
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1d&period1={s}&period2={e}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
            d = json.loads(resp.read())
        res    = d["chart"]["result"][0]
        ts     = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_localize(None)
        closes = res["indicators"]["quote"][0]["close"]
        volumes = res["indicators"]["quote"][0].get("volume", [None]*len(closes))
        s_close = pd.Series(closes,  index=ts, dtype=float, name=ticker).dropna()
        s_vol   = pd.Series(volumes, index=ts, dtype=float, name=ticker+"_vol")
        return s_close, s_vol
    except Exception:
        return pd.Series(dtype=float, name=ticker), pd.Series(dtype=float, name=ticker+"_vol")


DB_PATH = str(Path(__file__).resolve().parents[2] / "data" / "research" / "thai_ohlcv.db")


def load_prices(verbose: bool = True, full_universe: bool = False):
    """Load prices + volumes from thai_ohlcv.db (fast, no network).

    full_universe=True  → all tickers in DB (224 names)
    full_universe=False → SET_UNIVERSE list only (legacy ~70 names)
    """
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(f"DB not found: {DB_PATH} — run thai_data_layer.py --init first")

    conn = sqlite3.connect(DB_PATH)
    if full_universe:
        df = pd.read_sql(
            f"SELECT ticker, date, close, volume FROM thai_ohlcv "
            f"WHERE date >= '{START}' AND date <= '{END}' AND close IS NOT NULL",
            conn, parse_dates=["date"]
        )
    else:
        placeholders = ",".join("?" * len(SET_UNIVERSE + [SET_INDEX]))
        df = pd.read_sql(
            f"SELECT ticker, date, close, volume FROM thai_ohlcv "
            f"WHERE date >= '{START}' AND date <= '{END}' AND close IS NOT NULL "
            f"AND ticker IN ({placeholders})",
            conn, params=SET_UNIVERSE + [SET_INDEX], parse_dates=["date"]
        )
    conn.close()

    closes  = df.pivot(index="date", columns="ticker", values="close")
    vol_raw = df.pivot(index="date", columns="ticker", values="volume")

    # Rename volume columns to ticker_vol for backtest engine
    vol_raw.columns = [c + "_vol" for c in vol_raw.columns]

    closes  = closes.sort_index().ffill(limit=5)
    vol_raw = vol_raw.sort_index().ffill(limit=5)

    # Ensure SET index is present (needed for regime gate)
    if SET_INDEX not in closes.columns:
        raise ValueError(f"{SET_INDEX} missing from DB — add it via thai_data_layer.py")

    if verbose:
        print(f"  Loaded from DB: {len(closes.columns)} tickers  "
              f"{closes.index[0].date()} → {closes.index[-1].date()}")
    return closes, vol_raw


ADTV_MIN_THB  = 20_000_000   # Criteria 1: 6M avg daily turnover > 20M THB
MIN_PRICE_THB = 1.0          # Criteria 2: price > 1 THB (exclude penny stocks)
DAILY_RET_CAP = 0.25         # Criteria 3: cap single-day P&L at ±25%


def eligible_universe(prices: pd.DataFrame, volumes: pd.DataFrame,
                      today, lookback: int = 126) -> set:
    """Return set of tickers passing ADTV + min-price filters at `today`."""
    eligible = set()
    idx_pos = prices.index.get_loc(today)
    lb_pos  = max(0, idx_pos - lookback)
    window  = prices.index[lb_pos: idx_pos + 1]

    exclude = {SET_INDEX, "^SET.BK"}
    for tkr in [c for c in prices.columns if c not in exclude]:
        # Criteria 2: price > 1 THB today
        px = prices.loc[today, tkr]
        if pd.isna(px) or px < MIN_PRICE_THB:
            continue
        # Criteria 1: 6M avg daily turnover > 20M THB
        vol_col = tkr + "_vol"
        if vol_col in volumes.columns:
            px_w   = prices.loc[window, tkr].ffill()
            vol_w  = volumes.loc[window, vol_col].fillna(0)
            adtv   = (px_w * vol_w).mean()
            if adtv < ADTV_MIN_THB:
                continue
        eligible.add(tkr)
    return eligible


def backtest(prices: pd.DataFrame, volumes: pd.DataFrame,
             rs_days: int = 63,
             top_n:   int = 15,
             rebal_days: int = 10,
             regime_ma:  int = 200,
             rs_type:    str = "simple",   # simple | vol_weight
             ) -> dict:

    if SET_INDEX not in prices.columns:
        raise ValueError("SET Index missing")

    set_idx       = prices[SET_INDEX].dropna()
    univ_cols     = [c for c in prices.columns if c != SET_INDEX]
    trading_dates = prices.index.tolist()

    regime_ma_s = set_idx.rolling(regime_ma, min_periods=int(regime_ma * 0.75)).mean()
    bull_mask   = set_idx > regime_ma_s

    nav        = 1.0
    nav_log    = {trading_dates[0]: 1.0}
    set_nav    = 1.0
    set_log    = {trading_dates[0]: 1.0}
    daily_rets = []
    holdings   = []
    last_rebal = 0

    for i in range(1, len(trading_dates)):
        today = trading_dates[i]
        prev  = trading_dates[i - 1]

        sp0 = set_idx.get(prev)
        sp1 = set_idx.get(today)
        set_ret = (sp1 / sp0 - 1) if (sp0 and sp1 and sp0 > 0) else 0.0
        set_nav *= (1 + set_ret)
        set_log[today] = set_nav

        bull = bool(bull_mask.get(today, False))

        if not bull:
            if holdings:
                nav *= (1 - TCOST_SELL * len(holdings) / top_n)
                holdings = []
                last_rebal = i
            nav_log[today] = nav
            daily_rets.append(0.0)
            continue

        t_lb_idx = max(0, i - rs_days)
        t_lb     = trading_dates[t_lb_idx]

        # Criteria 1+2: filter eligible universe at rebalance
        if (i - last_rebal) >= rebal_days or i == 1:
            _eligible = eligible_universe(prices, volumes, today)
        else:
            _eligible = _eligible  # reuse between rebalances

        px_now = prices.loc[today, list(_eligible)].dropna()
        px_lb  = prices.loc[t_lb,  list(_eligible)].dropna()
        common = px_now.index.intersection(px_lb.index)

        if len(common) < 5:
            nav_log[today] = nav
            daily_rets.append(0.0)
            continue

        rs_raw = px_now[common] / px_lb[common] - 1

        if rs_type == "vol_weight":
            # RS × (stock avg volume / universe avg volume) — volume confirmation
            vol_cols = [c + "_vol" for c in common if c + "_vol" in volumes.columns]
            c_clean  = [c.replace("_vol", "") for c in vol_cols]
            if len(c_clean) >= 5:
                avg_vol = volumes[[c + "_vol" for c in c_clean]].loc[:today].tail(21).mean()
                univ_avg = avg_vol.mean()
                vol_ratio = (avg_vol / univ_avg).clip(0.1, 5.0)
                vol_ratio.index = [x.replace("_vol", "") for x in vol_ratio.index]
                common_vw = rs_raw.index.intersection(vol_ratio.index)
                rs = rs_raw.copy()
                rs[common_vw] = rs_raw[common_vw] * vol_ratio[common_vw]
            else:
                rs = rs_raw
        else:
            rs = rs_raw

        top30 = set(rs.nlargest(30).index)
        top_n_list = list(rs.nlargest(top_n).index)

        # Early exit
        dropped = [h for h in holdings if h not in top30]
        if dropped and (i - last_rebal) >= 3:
            for d in dropped:
                holdings.remove(d)
                nav *= (1 - TCOST_SELL)      # sell cost
            for t in top_n_list:
                if t not in holdings and len(holdings) < top_n:
                    holdings.append(t)
                    nav *= (1 - TCOST_BUY)   # buy cost

        # Scheduled rebalance — each stock sold+bought = round trip per changed position
        if (i - last_rebal) >= rebal_days:
            old = set(holdings)
            new = set(top_n_list)
            sells = len(old - new)
            buys  = len(new - old)
            if sells + buys > 0:
                nav *= (1 - TCOST_SELL * sells / top_n)
                nav *= (1 - TCOST_BUY  * buys  / top_n)
            holdings   = top_n_list[:]
            last_rebal = i

        if holdings:
            h_rets = []
            for h in holdings:
                p0 = prices.loc[prev, h]  if (h in prices.columns and prev  in prices.index) else np.nan
                p1 = prices.loc[today, h] if (h in prices.columns and today in prices.index) else np.nan
                if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                    # Criteria 3: cap single-day return at ±25% (corporate events not executable)
                    raw_ret = p1 / p0 - 1
                    h_rets.append(max(-DAILY_RET_CAP, min(DAILY_RET_CAP, raw_ret)))
            port_ret = np.mean(h_rets) if h_rets else 0.0
        else:
            port_ret = 0.0

        nav *= (1 + port_ret)
        nav_log[today] = nav
        daily_rets.append(port_ret)

    nav_s = pd.Series(nav_log)
    set_s = pd.Series(set_log)
    n_yr  = (nav_s.index[-1] - nav_s.index[0]).days / 365.25

    cagr     = (nav_s.iloc[-1] ** (1 / n_yr) - 1) * 100 if n_yr > 0 else 0
    set_cagr = (set_s.iloc[-1] ** (1 / n_yr) - 1) * 100 if n_yr > 0 else 0

    roll_max = nav_s.cummax()
    max_dd   = ((nav_s / roll_max) - 1).min() * 100

    d        = np.array(daily_rets)
    rf_daily = RF_ANNUAL / 252
    excess_d = d - rf_daily
    sharpe   = (excess_d.mean() / excess_d.std() * np.sqrt(252)) if excess_d.std() > 0 else 0

    nav_m  = nav_s.resample("ME").last().pct_change().dropna()
    set_m  = set_s.resample("ME").last().pct_change().dropna()
    common_m = nav_m.index.intersection(set_m.index)
    win_rt = (nav_m[common_m] > set_m[common_m]).mean() * 100 if len(common_m) > 0 else 0

    return {
        "cagr": round(cagr, 1), "set_cagr": round(set_cagr, 1),
        "excess": round(cagr - set_cagr, 1),
        "max_dd": round(max_dd, 1), "sharpe": round(sharpe, 2),
        "win_rate": round(win_rt, 1), "final_nav": round(nav_s.iloc[-1], 3),
        "nav_series": nav_s, "set_series": set_s,
    }


def print_yearly(result: dict, label: str):
    nav_y = result["nav_series"].resample("YE").last().dropna()
    set_y = result["set_series"].resample("YE").last().dropna()
    nav_r = nav_y.pct_change().dropna()
    set_r = set_y.pct_change().dropna()
    beats = 0
    print(f"\n{'Year':<6} {'AlphaTH':>9} {'SET':>9} {'Excess':>9}  Beat?")
    print("─" * 46)
    for yr in nav_r.index:
        p  = nav_r.get(yr, float("nan")) * 100
        s  = set_r.get(yr, float("nan")) * 100
        ex = p - s
        beat = p > s
        if beat: beats += 1
        flag = "✅" if beat else "❌"
        print(f"{yr.year:<6} {p:>+8.1f}%  {s:>+8.1f}%  {ex:>+8.1f}%  {flag}")
    print("─" * 46)
    print(f"{'Beat SET':>38}  {beats}/{len(nav_r.index)} yrs")


def main():
    if hasattr(__import__("sys").stdout, "reconfigure"):
        __import__("sys").stdout.reconfigure(encoding="utf-8")

    print("=" * 70)
    print("AlphaModel-TH  |  Full DB Universe Backtest")
    print(f"Period: {START} → {END}  |  ADTV ≥ 20M THB  |  Price ≥ 1 THB  |  Cap ±25%")
    print("RESEARCH ONLY — AlphaModel-US (System 4) unchanged")
    print("=" * 70)

    prices, volumes = load_prices(full_universe=True)

    configs = [
        # label,            rs_days, top_n, rebal, regime_ma, rs_type
        ("Baseline",              63,    15,    10,      200, "simple"),
        ("MA50 regime",           63,    15,    10,       50, "simple"),
        ("1M RS",                 21,    15,    10,      200, "simple"),
        ("Vol-weighted RS",       63,    15,    10,      200, "vol_weight"),
        ("Top-10",                63,    10,    10,      200, "simple"),
        ("Weekly rebal",          63,    15,     5,      200, "simple"),
        ("COMBINED (all best)",   21,    10,     5,       50, "vol_weight"),
    ]

    print(f"\n{'Config':<24} {'CAGR':>8} {'SET':>8} {'Excess':>8} {'MaxDD':>8} {'Sharpe':>8} {'WR%':>6}")
    print("─" * 76)

    results = {}
    for label, rs_days, top_n, rebal, regime_ma, rs_type in configs:
        r = backtest(prices, volumes,
                     rs_days=rs_days, top_n=top_n, rebal_days=rebal,
                     regime_ma=regime_ma, rs_type=rs_type)
        results[label] = r
        marker = "  ◀ COMBINED" if "COMBINED" in label else ("  ◀ BASELINE" if "Baseline" in label else "")
        print(f"{label:<24} {r['cagr']:>+7.1f}%  {r['set_cagr']:>+7.1f}%  {r['excess']:>+7.1f}%  "
              f"{r['max_dd']:>+7.1f}%  {r['sharpe']:>8.2f}  {r['win_rate']:>5.0f}%{marker}")

    print("─" * 76)

    # Year-by-year for COMBINED
    print("\n" + "=" * 70)
    print("Year-by-Year  |  COMBINED (all best)  vs  SET Index")
    print_yearly(results["COMBINED (all best)"], "COMBINED")

    # Also show Baseline year-by-year
    print("\n" + "=" * 70)
    print("Year-by-Year  |  Baseline  vs  SET Index")
    print_yearly(results["Baseline"], "Baseline")

    # Save
    import json as _json
    out = {}
    for label, r in results.items():
        out[label] = {k: v for k, v in r.items() if k not in ("nav_series", "set_series")}

    Path("data/research").mkdir(parents=True, exist_ok=True)
    with open("data/research/alphamodel_th_combined.json", "w") as f:
        _json.dump(out, f, indent=2)
    print("\n[Saved] data/research/alphamodel_th_combined.json")


if __name__ == "__main__":
    main()
