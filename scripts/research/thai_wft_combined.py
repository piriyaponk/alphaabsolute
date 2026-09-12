"""
AlphaModel-TH — WFT fixed on COMBINED config + DELTA attribution
=================================================================
1. WFT: Test COMBINED directly across all windows (no selection — fixed config)
2. Attribution: check if DELTA.BK drives losses in bad periods
3. Re-run excluding DELTA to see isolated impact

RESEARCH ONLY — AlphaModel-US unchanged
"""

import sys, sqlite3, warnings
import numpy as np
import pandas as pd
from pathlib import Path

warnings.filterwarnings("ignore")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB_PATH   = str(Path(__file__).resolve().parents[2] / "data" / "research" / "thai_ohlcv.db")
SET_INDEX = "^SET.BK"
TCOST_BUY  = 0.0015   # 0.15% per side (SET online broker standard)
TCOST_SELL = 0.0015
TCOST_RT   = TCOST_BUY + TCOST_SELL  # 0.30% round trip
RF_ANNUAL  = 0.025

ADTV_MIN_THB  = 20_000_000
MIN_PRICE_THB = 1.0
DAILY_RET_CAP = 0.25

COMBINED = dict(rs_days=21, top_n=10, rebal_days=5, regime_ma=50, rs_type="vol_weight")

WINDOWS = [
    ("W1", "2017-01-01", "2021-12-31", "2022-01-01", "2023-12-31"),
    ("W2", "2018-01-01", "2022-12-31", "2023-01-01", "2024-12-31"),
    ("W3", "2019-01-01", "2023-12-31", "2024-01-01", "2025-12-31"),
    ("W4", "2020-01-01", "2024-12-31", "2025-01-01", "2026-09-05"),
]
STRESS_WINDOWS = [
    ("S1-COVID",    "2017-01-01", "2019-12-31", "2020-01-01", "2020-12-31"),
    ("S2-RateHike", "2018-01-01", "2021-12-31", "2022-01-01", "2022-12-31"),
    ("S3-SETBear",  "2019-01-01", "2022-12-31", "2023-01-01", "2023-12-31"),
    ("S4-2025Bear", "2020-01-01", "2023-12-31", "2025-01-01", "2025-12-31"),
]


def load_all():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        "SELECT ticker, date, close, volume FROM thai_ohlcv WHERE close IS NOT NULL",
        conn, parse_dates=["date"]
    )
    conn.close()
    closes  = df.pivot(index="date", columns="ticker", values="close").sort_index().ffill(limit=5)
    vol_raw = df.pivot(index="date", columns="ticker", values="volume").sort_index().ffill(limit=5)
    vol_raw.columns = [c + "_vol" for c in vol_raw.columns]
    return closes, vol_raw


def slice_period(prices, volumes, start, end):
    mask = (prices.index >= start) & (prices.index <= end)
    return prices.loc[mask], volumes.loc[mask]


def eligible_universe(prices, volumes, today, lookback=126, exclude_tickers=None):
    hard_exclude = {SET_INDEX, "^SET.BK"} | (exclude_tickers or set())
    eligible = set()
    idx_pos = prices.index.get_loc(today)
    lb_pos  = max(0, idx_pos - lookback)
    window  = prices.index[lb_pos: idx_pos + 1]
    for tkr in [c for c in prices.columns if c not in hard_exclude]:
        px = prices.loc[today, tkr]
        if pd.isna(px) or px < MIN_PRICE_THB:
            continue
        vol_col = tkr + "_vol"
        if vol_col in volumes.columns:
            px_w  = prices.loc[window, tkr].ffill()
            vol_w = volumes.loc[window, vol_col].fillna(0)
            if (px_w * vol_w).mean() < ADTV_MIN_THB:
                continue
        eligible.add(tkr)
    return eligible


def backtest(prices, volumes, rs_days=21, top_n=10, rebal_days=5,
             regime_ma=50, rs_type="vol_weight",
             exclude_tickers=None, track_holdings=False):
    if SET_INDEX not in prices.columns:
        return None

    set_idx       = prices[SET_INDEX].dropna()
    trading_dates = prices.index.tolist()
    regime_ma_s   = set_idx.rolling(regime_ma, min_periods=int(regime_ma * 0.75)).mean()
    bull_mask     = set_idx > regime_ma_s

    nav = 1.0; set_nav = 1.0
    nav_log = {trading_dates[0]: 1.0}; set_log = {trading_dates[0]: 1.0}
    daily_rets = []; holdings = []; last_rebal = 0
    _eligible = set()
    holdings_log = {}  # date → list of holdings (for attribution)

    for i in range(1, len(trading_dates)):
        today = trading_dates[i]; prev = trading_dates[i - 1]
        sp0 = set_idx.get(prev); sp1 = set_idx.get(today)
        set_ret = (sp1 / sp0 - 1) if (sp0 and sp1 and sp0 > 0) else 0.0
        set_nav *= (1 + set_ret); set_log[today] = set_nav

        bull = bool(bull_mask.get(today, False))
        if not bull:
            if holdings:
                nav *= (1 - TCOST_SELL * len(holdings) / top_n); holdings = []; last_rebal = i
            nav_log[today] = nav; daily_rets.append(0.0); continue

        if (i - last_rebal) >= rebal_days or i == 1:
            _eligible = eligible_universe(prices, volumes, today,
                                          exclude_tickers=exclude_tickers)

        t_lb = trading_dates[max(0, i - rs_days)]
        px_now = prices.loc[today, list(_eligible)].dropna()
        px_lb  = prices.loc[t_lb,  list(_eligible)].dropna()
        common = px_now.index.intersection(px_lb.index)
        if len(common) < 5:
            nav_log[today] = nav; daily_rets.append(0.0); continue

        rs_raw = px_now[common] / px_lb[common] - 1
        if rs_type == "vol_weight":
            c_clean = [c for c in common if c + "_vol" in volumes.columns]
            if len(c_clean) >= 5:
                avg_vol   = volumes[[c + "_vol" for c in c_clean]].loc[:today].tail(21).mean()
                vol_ratio = (avg_vol / avg_vol.mean()).clip(0.1, 5.0)
                vol_ratio.index = [x.replace("_vol", "") for x in vol_ratio.index]
                rs = rs_raw.copy()
                vw = rs_raw.index.intersection(vol_ratio.index)
                rs[vw] = rs_raw[vw] * vol_ratio[vw]
            else:
                rs = rs_raw
        else:
            rs = rs_raw

        top_n_list = list(rs.nlargest(top_n).index)
        top30      = set(rs.nlargest(30).index)

        dropped = [h for h in holdings if h not in top30]
        if dropped and (i - last_rebal) >= 3:
            for d in dropped:
                holdings.remove(d); nav *= (1 - TCOST_SELL)
            for t in top_n_list:
                if t not in holdings and len(holdings) < top_n:
                    holdings.append(t); nav *= (1 - TCOST_BUY)

        if (i - last_rebal) >= rebal_days:
            old = set(holdings); new = set(top_n_list)
            sells = len(old - new); buys = len(new - old)
            if sells + buys > 0:
                nav *= (1 - TCOST_SELL * sells / top_n)
                nav *= (1 - TCOST_BUY  * buys  / top_n)
            holdings = top_n_list[:]; last_rebal = i

        if track_holdings and holdings:
            holdings_log[today] = holdings[:]

        if holdings:
            h_rets = []
            for h in holdings:
                p0 = prices.loc[prev, h]  if h in prices.columns else np.nan
                p1 = prices.loc[today, h] if h in prices.columns else np.nan
                if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                    h_rets.append(max(-DAILY_RET_CAP, min(DAILY_RET_CAP, p1/p0 - 1)))
            port_ret = np.mean(h_rets) if h_rets else 0.0
        else:
            port_ret = 0.0

        nav *= (1 + port_ret); nav_log[today] = nav; daily_rets.append(port_ret)

    nav_s = pd.Series(nav_log); set_s = pd.Series(set_log)
    n_yr  = (nav_s.index[-1] - nav_s.index[0]).days / 365.25
    if n_yr < 0.1:
        return None

    cagr     = (nav_s.iloc[-1] ** (1/n_yr) - 1) * 100
    set_cagr = (set_s.iloc[-1] ** (1/n_yr) - 1) * 100
    roll_max = nav_s.cummax()
    max_dd   = ((nav_s / roll_max) - 1).min() * 100
    d = np.array(daily_rets)
    rf_d = RF_ANNUAL / 252
    std = (d - rf_d).std()
    sharpe = ((d - rf_d).mean() / std * np.sqrt(252)) if std > 1e-8 else 0.0
    return {
        "cagr": round(cagr, 1), "set_cagr": round(set_cagr, 1),
        "excess": round(cagr - set_cagr, 1),
        "max_dd": round(max_dd, 1), "sharpe": round(sharpe, 2),
        "nav_series": nav_s, "holdings_log": holdings_log,
    }


def run_section(label, windows, prices, volumes):
    results = []
    for wname, tr_s, tr_e, te_s, te_e in windows:
        px_te, vl_te = slice_period(prices, volumes, te_s, te_e)
        te_yr = f"{te_s[:4]}–{te_e[:4]}" if te_e[:4] != te_s[:4] else te_s[:4]

        # Base: COMBINED as-is
        r = backtest(px_te, vl_te, **COMBINED)
        # Excluding DELTA
        r_nd = backtest(px_te, vl_te, **COMBINED, exclude_tickers={"DELTA.BK"})

        if r and r_nd:
            diff = round(r_nd["cagr"] - r["cagr"], 1)
            beat = r["cagr"] > r["set_cagr"]
            flag = "✅" if beat else "❌"
            delta_impact = f"+{diff}%" if diff > 0 else f"{diff}%"
            results.append({
                "window": wname, "period": te_yr,
                "cagr": r["cagr"], "set_cagr": r["set_cagr"],
                "excess": r["excess"], "max_dd": r["max_dd"], "sharpe": r["sharpe"],
                "cagr_no_delta": r_nd["cagr"], "delta_impact": diff, "beat": beat,
            })
            print(f"  {wname} ({te_yr})  CAGR {r['cagr']:>+6.1f}%  SET {r['set_cagr']:>+6.1f}%  "
                  f"Ex {r['excess']:>+6.1f}%  DD {r['max_dd']:>+6.1f}%  Sharpe {r['sharpe']:.2f}  {flag}"
                  f"  |  ex-DELTA {r_nd['cagr']:>+6.1f}% ({delta_impact})")
    return results


def delta_deep_dive(prices, volumes):
    """How often does DELTA appear in top-10? What's its return contribution in 2025?"""
    print("\n── DELTA.BK Deep Dive ──")

    # DELTA price history
    if "DELTA.BK" in prices.columns:
        px25 = prices["DELTA.BK"].loc["2025-01-01":"2025-12-31"].dropna()
        if len(px25) > 0:
            ret25 = (px25.iloc[-1] / px25.iloc[0] - 1) * 100
            dd25  = ((px25 / px25.cummax()) - 1).min() * 100
            print(f"  DELTA.BK 2025: start {px25.iloc[0]:.1f}  end {px25.iloc[-1]:.1f}  "
                  f"return {ret25:>+.1f}%  MaxDD {dd25:>+.1f}%")

    # Track holdings in 2025 to see how often DELTA appears
    px25, vl25 = slice_period(prices, volumes, "2024-01-01", "2025-12-31")
    r_track = backtest(px25, vl25, **COMBINED, track_holdings=True)
    if r_track:
        hl = r_track["holdings_log"]
        dates_2025 = [d for d in hl if d.year == 2025]
        delta_count = sum(1 for d in dates_2025 if "DELTA.BK" in hl[d])
        total = len(dates_2025)
        print(f"  DELTA in portfolio (2025): {delta_count}/{total} trading days "
              f"({delta_count/total*100:.0f}% of days)" if total > 0 else "  No holdings data")

        # Top 5 most-held stocks in 2025
        from collections import Counter
        counter = Counter()
        for d in dates_2025:
            for tkr in hl.get(d, []):
                counter[tkr] += 1
        print(f"  Most-held in 2025 (top 8):")
        for tkr, cnt in counter.most_common(8):
            pct = cnt / total * 100 if total > 0 else 0
            # individual return in 2025
            if tkr in prices.columns:
                px_t = prices[tkr].loc["2025-01-01":"2025-12-31"].dropna()
                ret_t = (px_t.iloc[-1] / px_t.iloc[0] - 1) * 100 if len(px_t) > 1 else 0
                print(f"    {tkr:<14} held {pct:>4.0f}% of days  2025 return {ret_t:>+6.1f}%")


def main():
    print("=" * 75)
    print("AlphaModel-TH — WFT Fixed on COMBINED + DELTA Attribution")
    print("=" * 75)

    prices, volumes = load_all()
    print(f"  DB: {len(prices.columns)} tickers  "
          f"{prices.index[0].date()} → {prices.index[-1].date()}\n")

    # ── WFT Standard Windows ──
    print("STANDARD WFT — COMBINED (fixed, no selection)")
    print(f"{'─'*75}")
    print(f"  {'Window':<18} {'CAGR':>8} {'SET':>7} {'Excess':>8} {'MaxDD':>7} {'Sharpe':>8}  Beat?  "
          f"| ex-DELTA")
    print(f"  {'─'*73}")
    wft_res = run_section("WFT", WINDOWS, prices, volumes)

    wft_beats = sum(1 for r in wft_res if r["beat"])
    wft_avg_ex = sum(r["excess"] for r in wft_res) / len(wft_res)
    wft_avg_sh = sum(r["sharpe"] for r in wft_res) / len(wft_res)
    print(f"\n  Beat SET: {wft_beats}/4  |  Avg Excess: {wft_avg_ex:>+.1f}%  |  Avg Sharpe: {wft_avg_sh:.2f}")

    # ── Stress Windows ──
    print(f"\n{'─'*75}")
    print("STRESS TEST — COMBINED (test ON crash year only)")
    print(f"  {'─'*73}")
    stress_res = run_section("Stress", STRESS_WINDOWS, prices, volumes)

    stress_beats = sum(1 for r in stress_res if r["beat"])
    stress_pos   = sum(1 for r in stress_res if r["cagr"] > 0)
    print(f"\n  Beat SET: {stress_beats}/4  |  Positive: {stress_pos}/4")

    # ── DELTA Attribution ──
    delta_deep_dive(prices, volumes)

    # ── Summary ──
    print(f"\n{'='*75}")
    print("SUMMARY — COMBINED vs ex-DELTA impact")
    print(f"{'─'*75}")
    all_res = wft_res + stress_res
    delta_helped  = sum(1 for r in all_res if r["delta_impact"] < 0)   # removing hurts = DELTA helped
    delta_dragged = sum(1 for r in all_res if r["delta_impact"] > 0.5) # removing helps = DELTA dragged
    avg_delta_impact = sum(r["delta_impact"] for r in all_res) / len(all_res)
    print(f"  Windows where DELTA helped  (removing hurt):   {delta_helped}/{len(all_res)}")
    print(f"  Windows where DELTA dragged (removing helped): {delta_dragged}/{len(all_res)}")
    print(f"  Avg DELTA impact on CAGR: {avg_delta_impact:>+.1f}%/yr")

    if avg_delta_impact > 1.0:
        print(f"\n  ⚠️  DELTA is a net DRAG — consider excluding from TH universe")
    elif avg_delta_impact < -1.0:
        print(f"\n  ✅ DELTA is a net CONTRIBUTOR — keep in universe")
    else:
        print(f"\n  ➡️  DELTA impact is negligible — no action needed")

    # ── Final verdict on COMBINED ──
    print(f"\n{'='*75}")
    print("VERDICT — COMBINED as AlphaModel-TH Baseline?")
    print(f"{'─'*75}")
    p1 = wft_beats >= 3
    p2 = wft_avg_sh >= 1.0
    worst_dd = min(r["max_dd"] for r in wft_res)
    p3 = worst_dd >= -30.0
    print(f"  [{'PASS' if p1 else 'FAIL'}]  WFT Beat SET ≥ 3/4:    {wft_beats}/4")
    print(f"  [{'PASS' if p2 else 'FAIL'}]  Avg Sharpe ≥ 1.0:      {wft_avg_sh:.2f}")
    print(f"  [{'PASS' if p3 else 'FAIL'}]  Worst DD ≥ -30%:       {worst_dd:.1f}%")
    if p1 and p2 and p3:
        print(f"\n  ✅ COMBINED PASSES — ready to lock as AlphaModel-TH Baseline")
    else:
        print(f"\n  ❌ COMBINED FAILS — use simpler config")
    print(f"{'='*75}")


if __name__ == "__main__":
    main()
