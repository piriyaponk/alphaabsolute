# -*- coding: utf-8 -*-
"""
06_validation_tests.py  --  AlphaAbsolute Backtest Validation Suite
Quant Analyst: Piriyapon Kongvanich | 2026-08-30
Sections: 1=Unit Tests  2=Signal Gates  3=Year-by-Year  4=vol_contraction 2022  5=Capacity
"""
import sys, io, importlib.util
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def _load_v2():
    spec = importlib.util.spec_from_file_location(
        "bt_v2", ROOT / "scripts" / "backtest" / "03b_backtest_v2.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PASS_THRESH = 0.5


def _pct_diff(a, b):
    if a is None or b is None or np.isnan(a) or np.isnan(b):
        return float("nan")
    denom = (abs(a) + abs(b)) / 2
    if denom < 1e-10:
        return 0.0
    return abs(a - b) / denom * 100


def _chk(label, stored, computed, tol=PASS_THRESH):
    diff = _pct_diff(stored, computed)
    tag = "[SKIP]" if np.isnan(diff) else ("[PASS]" if diff <= tol else "[FAIL]")
    d_str = "%.3f" % diff if not np.isnan(diff) else "nan"
    print("  %s %-32s stored=%-12.6f  computed=%-12.6f  diff=%s%%" % (
        tag, label, stored, computed, d_str))
    return tag


# ============================================================
# SECTION 1: UNIT TESTS
# ============================================================
def section1_unit_tests(prices, signals):
    print("\n" + "="*70)
    print("SECTION 1: UNIT TESTS -- formula verification vs raw prices")
    print("="*70)
    print("Tolerance: diff < 0.5%% = PASS | Test date: 2023-06-30\n")
    TEST_DATE = pd.Timestamp("2023-06-30")
    TEST_TICKERS = ["NVDA", "XOM", "AAPL", "INTC"]
    prices["date"]  = pd.to_datetime(prices["date"])
    signals["date"] = pd.to_datetime(signals["date"])
    spy_px = prices[prices["ticker"] == "SPY"].set_index("date")["close"].sort_index()
    tp = tf = ts = 0

    for ticker in TEST_TICKERS:
        print("--- %s @ %s ---" % (ticker, TEST_DATE.date()))
        tkr = prices[prices["ticker"] == ticker].set_index("date").sort_index().loc[:TEST_DATE]
        if len(tkr) < 252:
            print("  [SKIP] insufficient history (%d rows)" % len(tkr))
            ts += 7; print(); continue
        sig_row = signals[(signals["ticker"] == ticker) & (signals["date"] == TEST_DATE)]
        if sig_row.empty:
            print("  [SKIP] no signal row"); ts += 7; print(); continue
        sig = sig_row.iloc[0]
        c = tkr["close"]; h = tkr["high"]; lo = tkr["low"]; v = tkr["volume"]
        dr = h - lo  # kept for legacy; vol formulas now use correct log/normalized calc
        tests = [
            ("%s / ma_200"          % ticker, sig["ma_200"],         c.iloc[-200:].mean()),
            ("%s / adtv_63m"        % ticker, sig["adtv_63m"],       (c*v).iloc[-63:].mean()/1e6),
            ("%s / base_tight"      % ticker, sig["base_tight"],     ((h-lo)/c).rolling(20).mean().iloc[-1]/((h-lo)/c).rolling(65).mean().iloc[-1]),
            ("%s / vol_contraction" % ticker, sig["vol_contraction"],np.log(c/c.shift(1)).rolling(20).std().iloc[-1]/np.log(c/c.shift(1)).rolling(60).std().iloc[-1]),
            ("%s / vol_trend"       % ticker, sig["vol_trend"],      v.iloc[-20:].mean()/v.iloc[-60:].mean()),
        ]
        for lbl, stored, computed in tests:
            tag = _chk(lbl, stored, computed)
            if tag == "[PASS]": tp += 1
            elif tag == "[FAIL]": tf += 1
            else: ts += 1
        spy_s = spy_px.loc[:TEST_DATE]
        def _r(s, n):
            return (s.iloc[-1]/s.iloc[-(n+1)]-1) if len(s) >= n+1 else np.nan
        rs_comp = (0.1*(_r(c,20)-_r(spy_s,20)) + 0.2*(_r(c,60)-_r(spy_s,60)) +
                   0.3*(_r(c,126)-_r(spy_s,126)) + 0.4*(_r(c,252)-_r(spy_s,252)))
        tag = _chk("%s / rs_composite" % ticker, sig["rs_composite"], rs_comp)
        if tag == "[PASS]": tp += 1
        elif tag == "[FAIL]": tf += 1
        else: ts += 1
        print()
    print("--- rs_pct cross-sectional rank @ 2023-06-30 (tolerance 2.0%%) ---")
    dsigs = signals[signals["date"] == TEST_DATE].copy()
    if not dsigs.empty:
        dsigs["rs_pct_r"] = dsigs["rs_composite"].rank(pct=True) * 100
        for ticker in TEST_TICKERS:
            row = dsigs[dsigs["ticker"] == ticker]
            if row.empty: continue
            tag = _chk("%s / rs_pct" % ticker, row["rs_pct"].values[0], row["rs_pct_r"].values[0], tol=2.0)
            if tag == "[PASS]": tp += 1
            elif tag == "[FAIL]": tf += 1
            else: ts += 1
    print()
    print("UNIT TEST SUMMARY: %d PASS  |  %d FAIL  |  %d SKIP" % (tp, tf, ts))
    return tp, tf


# ============================================================
# SECTION 2: SIGNAL GATE WALK-THROUGH
# ============================================================
def section2_signal_verification(signals):
    print("\n" + "="*70)
    print("SECTION 2: BUY/SELL SIGNAL GATE WALK-THROUGH  (last 12 rebal dates)")
    print("="*70)
    print("Thresholds: rs>=75  vol_contract<=1.0  vol_trend>=0.95  adtv>=15  price>MA200")
    print("BORDER zones: rs 67.5-82.5 | vol_con 0.90-1.10 | vol_tr 0.855-1.045 | adtv 13.5-16.5\n")
    RS=75.0; VC=1.0; VT=0.95; AD=15.0
    TICKERS = ["NVDA","XOM","SMCI","INTC","AAPL"]
    signals["date"] = pd.to_datetime(signals["date"])
    tdays = signals["date"].sort_values().unique()
    rebal = sorted({tdays[tdays<=d][-1] for d in pd.date_range("2025-08-01","2026-08-20",freq="ME")
                    if len(tdays[tdays<=d])})

    for ticker in TICKERS:
        print("-"*70)
        print("  %s" % ticker)
        print("  %-13s %8s %9s %8s %8s %7s  Result" % ("Date","rs_pct","vol_con","vol_tr","adtv",">MA200"))
        tkr = signals[signals["ticker"]==ticker].set_index("date")
        for d in rebal:
            if d not in tkr.index:
                print("  %-13s [NO DATA]" % str(d.date())); continue
            row = tkr.loc[d]
            rs_=row["rs_pct"]; vc_=row["vol_contraction"]; vt_=row["vol_trend"]
            ad_=row["adtv_63m"]; abv=bool(row["close"]>row["ma_200"])
            g={"rs":rs_>=RS,"vc":vc_<=VC,"vt":vt_>=VT,"adtv":ad_>=AD,"ma":abv}
            failed=[k for k,v in g.items() if not v]
            brd=[]
            if RS*0.9<=rs_<=RS*1.1: brd.append("rs")
            if VC*0.9<=vc_<=VC*1.1: brd.append("vc")
            if VT*0.9<=vt_<=VT*1.1: brd.append("vt")
            if AD*0.9<=ad_<=AD*1.1: brd.append("adtv")
            res = "PASS" if all(g.values()) else ("FAIL(%s)"%",".join(failed))
            bstr = (" [BORDER:%s]"%",".join(brd)) if brd else ""
            print("  %-13s %8.1f %9.3f %8.3f %8.1f %7s  %s%s" % (
                str(d.date()),rs_,vc_,vt_,ad_,"YES" if abv else "NO",res,bstr))
        print()


# ============================================================
# SECTION 3: YEAR-BY-YEAR PERFORMANCE
# ============================================================
def section3_yearly_performance(bt_mod):
    print("\n" + "="*70)
    print("SECTION 3: YEAR-BY-YEAR PERFORMANCE  (2017-2026)")
    print("="*70)
    print("rs=75, vol_contract<=1.0, vol_trend>=0.95, adtv=15, cap=0.18, no regime gate, 0.14%% RT\n")
    p = bt_mod.StrategyParams(
        rs_threshold=75, vol_trend_min=0.95, vol_contract_max=1.0,
        base_tight_max=9.99, weighting="softmax", softmax_alpha=0.5,
        cap_pct=0.18, bull_exposure=1.0, bear_exposure=1.0,
        adtv_min_m=15.0, start_date="2017-01-01", end_date="2026-08-20",
    )
    costs = bt_mod.CostParams(slippage=0.0007, commission=0.0)
    exp   = ROOT / "data" / "backtest" / "signals_expanded.parquet"
    dl    = bt_mod.BacktestDataloader(signal_file=exp)
    res   = bt_mod.BacktestModel(
        dl, bt_mod.BacktestStrategy(dl, p),
        bt_mod.BacktestPortfolio(1_000_000, costs), p
    ).run(verbose=False)
    nav_s=res["nav_series"]; bench_s=res["bench_series"]
    txn=pd.DataFrame(res["transactions"])
    rows=[]
    for yr in range(2017, 2027):
        s=pd.Timestamp("%d-01-01"%yr); e=pd.Timestamp("%d-12-31"%yr)
        nv=nav_s[(nav_s.index>=s)&(nav_s.index<=e)]
        bv=bench_s[(bench_s.index>=s)&(bench_s.index<=e)]
        if len(nv)<5: continue
        pr=(nv.iloc[-1]/nv.iloc[0]-1)*100
        br=(bv.iloc[-1]/bv.iloc[0]-1)*100 if len(bv)>1 else float("nan")
        mdd=((nv-nv.cummax())/nv.cummax()).min()*100
        ah=at_=float("nan")
        if not txn.empty:
            ty=txn[txn["date"].dt.year==yr]
            if not ty.empty:
                by_=ty[ty["action"]=="BUY"]
                if not by_.empty:
                    ah=by_.groupby(by_["date"].dt.to_period("M"))["ticker"].nunique().mean()
                nvy=nv.mean()
                if nvy>0:
                    at_=ty.groupby(ty["date"].dt.to_period("M"))["value"].sum().mean()/nvy*100
        rows.append({"yr":yr,"pr":pr,"br":br,"ex":pr-br,"mdd":mdd,"ah":ah,"at":at_})
    df=pd.DataFrame(rows)
    print("  %-7s %8s %8s %10s %9s %9s %11s" % ("Year","Port%","QQQ%","Excess%","MaxDD%","AvgHold","Turnover%"))
    print("  " + "-"*63)
    exc=[]
    for r in df.itertuples():
        tag="%d%s"%(r.yr,"*" if r.yr==2026 else " ")
        ah="%.1f"%r.ah if not np.isnan(r.ah) else "n/a"
        at="%.1f"%r.at if not np.isnan(r.at) else "n/a"
        print("  %-7s %8.1f %8.1f %10.1f %9.1f %9s %11s"%(tag,r.pr,r.br,r.ex,r.mdd,ah,at))
        exc.append(r.ex)
    ea=np.array(exc); n=len(ea); me=ea.mean(); sd=ea.std(ddof=1)
    ts=me/(sd/np.sqrt(n)) if sd>0 else float("nan")
    print("\n  (* 2026 = partial year to 2026-08-20)")
    print("\n  Annual Excess Stats (N=%d years):" % n)
    print("    Mean annual excess : %+.1f%%" % me)
    print("    Std annual excess  : %.1f%%" % sd)
    print("    t-statistic        : %.2f  (>1.96 = 95%% confidence)" % ts)
    print("    Information Ratio  : %.2f" % (me/sd if sd>0 else float("nan")))
    print("    %% years positive   : %.0f%%" % ((ea>0).sum()/n*100))
    print("    Worst year excess  : %+.1f%%" % ea.min())
    return res


# ============================================================
# SECTION 4: vol_contraction ISOLATION 2022
# ============================================================
ENERGY={"XOM","CVX","COP","SLB","OXY","PSX","MPC","VLO","HAL","BKR"}
TECH={"AAPL","MSFT","GOOGL","AMZN","META","NVDA","AMD","GOOG","TSLA","NFLX","ADBE","CRM","AVGO","QCOM"}

def _sect(t):
    if t in ENERGY: return "Energy"
    if t in TECH:   return "Tech"
    return "Other"


def section4_vol_contraction_2022(bt_mod):
    print("\n" + "="*70)
    print("SECTION 4: vol_contraction FACTOR ISOLATION IN 2022")
    print("="*70)
    exp=ROOT/"data"/"backtest"/"signals_expanded.parquet"
    bk=dict(rs_threshold=75,vol_trend_min=0.95,base_tight_max=9.99,
            weighting="softmax",softmax_alpha=0.5,cap_pct=0.18,
            bull_exposure=1.0,bear_exposure=1.0,adtv_min_m=15.0,
            start_date="2022-01-01",end_date="2022-12-31")
    costs=bt_mod.CostParams(slippage=0.0007,commission=0.0)

    print("\n--- Monthly Holdings WITH vol_contraction <= 1.0 ---")
    p_w=bt_mod.StrategyParams(**dict(bk,vol_contract_max=1.0))
    dl_w=bt_mod.BacktestDataloader(signal_file=exp)
    st_w=bt_mod.BacktestStrategy(dl_w,p_w)
    r_w=bt_mod.BacktestModel(dl_w,st_w,bt_mod.BacktestPortfolio(1_000_000,costs),p_w).run(verbose=False)

    msec=[]
    for rd in dl_w.get_rebal_dates(p_w):
        sel=st_w.screen(rd)
        if sel.empty: continue
        sel=sel.copy(); sel["sector"]=sel["ticker"].apply(_sect)
        tw=sel["weight"].sum()
        if tw==0: continue
        bs=sel.groupby("sector")["weight"].sum()/tw*100
        t3=sel.nlargest(3,"weight")["ticker"].tolist()
        ep=bs.get("Energy",0); tp_=bs.get("Tech",0); op=bs.get("Other",0)
        msec.append((rd,ep,tp_,op,len(sel)))
        print("  %s  Energy=%4.1f%%  Tech=%4.1f%%  Other=%4.1f%%  N=%d  Top3: %s"%(
            rd.date(),ep,tp_,op,len(sel),", ".join(t3)))

    if msec:
        av=float(np.mean([r[1] for r in msec]))
        print("\n  Max energy weight any month : %.1f%%" % max(r[1] for r in msec))
        print("  Avg energy weight in 2022   : %.1f%%" % av)
        print("  %s" % ("  WARNING: >40%% avg -- possible sector artifact" if av>40
                         else "  OK: avg <=40%% -- not a pure energy-sector bet"))

    p_n=bt_mod.StrategyParams(**dict(bk,vol_contract_max=9.99))
    dl_n=bt_mod.BacktestDataloader(signal_file=exp)
    r_n=bt_mod.BacktestModel(dl_n,bt_mod.BacktestStrategy(dl_n,p_n),
                              bt_mod.BacktestPortfolio(1_000_000,costs),p_n).run(verbose=False)

    def _ex(nav,bench):
        c_=nav.index.intersection(bench.index); n_=nav.loc[c_]; b_=bench.loc[c_]
        if len(n_)<2: return float("nan")
        return ((n_.iloc[-1]/n_.iloc[0]-1)-(b_.iloc[-1]/b_.iloc[0]-1))*100

    ew=_ex(r_w["nav_series"],r_w["bench_series"])
    en=_ex(r_n["nav_series"],r_n["bench_series"])
    print("\n  2022 EXCESS  WITH    vol_contract<=1.0  : %+.1f%%" % ew)
    print("  2022 EXCESS  WITHOUT vol_contract gate  : %+.1f%%" % en)
    print("  Alpha from vol_contraction filter       : %+.1f%%" % (ew-en))
    print("  VERDICT: %s" % ("vol_contraction ADDS alpha in 2022 -- keep it" if ew>en
                               else "vol_contraction does NOT add alpha in 2022 -- investigate"))


# ============================================================
# SECTION 5: CAPACITY ANALYSIS
# ============================================================
def section5_capacity(full_res, bt_mod):
    print("\n" + "="*70)
    print("SECTION 5: CAPACITY ANALYSIS")
    print("="*70)
    txn=pd.DataFrame(full_res["transactions"]); nav_s=full_res["nav_series"]; nav_avg=nav_s.mean()
    if not txn.empty and nav_avg>0:
        mv=txn.groupby(txn["date"].dt.to_period("M"))["value"].sum()
        amt=mv.mean()/nav_avg
    else:
        amt=full_res.get("avg_turnover",0)
    drag=amt*12*0.0014*100
    print("\n  Avg monthly turnover (one-way / NAV) : %.1f%%" % (amt*100))
    print("  Annualised cost drag @ 14 bps RT     : %.2f%%" % drag)
    if not txn.empty:
        bt_=txn[txn["action"]=="BUY"].copy()
        if not bt_.empty and nav_avg>0:
            mx=bt_["value"].max(); tkr=bt_.loc[bt_["value"].idxmax(),"ticker"]
            print("  Max single-stock position observed   : %.1f%% (%s)" % (mx/nav_avg*100,tkr))
    exp=ROOT/"data"/"backtest"/"signals_expanded.parquet"
    sg=pd.read_parquet(exp); sg["date"]=pd.to_datetime(sg["date"])
    mask=((sg["rs_pct"]>=75)&(sg["vol_trend"]>=0.95)&(sg["vol_contraction"]<=1.0)
          &(sg["adtv_63m"]>=15.0)&(sg["close"]>sg["ma_200"]))
    ss=sg[mask]; avg_adtv=ss["adtv_63m"].mean()
    print("  Avg ADTV of selected stocks (full)   : $%.1fM/day" % avg_adtv)
    print("  Total screen observations            : %d" % len(ss))
    print("\n  Rule: position_size (cap_pct=18%% * NAV) <= 20%% of monthly ADTV")
    print("  Monthly ADTV = adtv_63m * $1M * 21 trading days\n")
    print("  %-13s  %15s  %18s  %10s" % ("Portfolio","Pos Size ($M)","AvgMoLiq ($M)","Breach %%"))
    print("  "+"-"*62)
    mo_liq=avg_adtv*1e6*21
    for pm in [1,10,50,100]:
        ps=0.18*pm*1e6; thr=ps/(0.20*1e6*21)
        bp=(ss["adtv_63m"]<thr).sum()/len(ss)*100 if len(ss)>0 else 0
        print("  $%10dM  %14.2fM  %17.0fM  %9.1f%%" % (pm,ps/1e6,mo_liq/1e6,bp))
    print("\n  0%% breach = no liquidity constraint; high breach %% = capacity limited")


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    print("="*70)
    print("AlphaAbsolute Backtest Validation Suite  v1.0")
    print("Quant Analyst  |  2026-08-30")
    print("="*70)
    print("\nLoading data...")
    prices  = pd.read_parquet(ROOT / "data" / "backtest" / "prices.parquet")
    signals = pd.read_parquet(ROOT / "data" / "backtest" / "signals.parquet")
    print("  prices:  %d rows | %d tickers" % (len(prices), prices["ticker"].nunique()))
    print("  signals: %d rows | %d tickers" % (len(signals), signals["ticker"].nunique()))
    print("  Loading backtest engine...")
    bt_mod = _load_v2()
    section1_unit_tests(prices, signals)
    section2_signal_verification(signals)
    full_result = section3_yearly_performance(bt_mod)
    section4_vol_contraction_2022(bt_mod)
    section5_capacity(full_result, bt_mod)
    print("\n" + "="*70)
    print("Validation complete.")
    print("="*70)
