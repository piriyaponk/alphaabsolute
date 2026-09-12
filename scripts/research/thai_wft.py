"""
AlphaModel-TH — Walk-Forward Test
===================================
Train on 5 years → pick best config → test on next 2 years → roll forward.

Windows:
  W1: Train 2017-2021  | Test 2022-2023
  W2: Train 2018-2022  | Test 2023-2024  (1yr roll)
  W3: Train 2019-2023  | Test 2024-2025  (1yr roll)
  W4: Train 2020-2024  | Test 2025-2026  (1yr roll)

For each window:
  1. Run all 7 configs on TRAIN period → pick best by Sharpe
  2. Apply that config on TEST period (blind — no re-optimization)
  3. Record: did the best-in-sample config beat SET out-of-sample?

Pass criteria:
  - OOS CAGR > SET in 3+ of 4 windows → robust
  - OOS Sharpe > 1.0 average → tradeable
  - OOS MaxDD < -30% in any window → risk acceptable

RESEARCH ONLY — AlphaModel-US (System 4) unchanged
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
TCOST     = 0.0025
RF_ANNUAL = 0.025

ADTV_MIN_THB  = 20_000_000
MIN_PRICE_THB = 1.0
DAILY_RET_CAP = 0.25

CONFIGS = [
    # label,            rs_days, top_n, rebal, regime_ma, rs_type
    ("Baseline",              63,    15,    10,      200, "simple"),
    ("MA50 regime",           63,    15,    10,       50, "simple"),
    ("1M RS",                 21,    15,    10,      200, "simple"),
    ("Vol-weighted RS",       63,    15,    10,      200, "vol_weight"),
    ("Top-10",                63,    10,    10,      200, "simple"),
    ("Weekly rebal",          63,    15,     5,      200, "simple"),
    ("COMBINED",              21,    10,     5,       50, "vol_weight"),
]

WINDOWS = [
    ("W1", "2017-01-01", "2021-12-31", "2022-01-01", "2023-12-31"),
    ("W2", "2018-01-01", "2022-12-31", "2023-01-01", "2024-12-31"),
    ("W3", "2019-01-01", "2023-12-31", "2024-01-01", "2025-12-31"),
    ("W4", "2020-01-01", "2024-12-31", "2025-01-01", "2026-09-05"),
]

# Stress test windows — train before crash, test ON the crash year only
# SET returns: 2018=-10.8%, 2020=-8.3%(full yr), 2023=-15.2%, 2025=-10.0%
STRESS_WINDOWS = [
    ("S1-COVID",    "2017-01-01", "2019-12-31", "2020-01-01", "2020-12-31"),  # Test 2020 crash
    ("S2-RateHike", "2018-01-01", "2021-12-31", "2022-01-01", "2022-12-31"),  # Test 2022 volatile
    ("S3-SETBear",  "2019-01-01", "2022-12-31", "2023-01-01", "2023-12-31"),  # Test 2023 -15%
    ("S4-2025Bear", "2020-01-01", "2023-12-31", "2025-01-01", "2025-12-31"),  # Test 2025 -10%
]


# ─── Data load ────────────────────────────────────────────────────────────────

def load_all() -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        "SELECT ticker, date, close, volume FROM thai_ohlcv "
        "WHERE close IS NOT NULL",
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


# ─── Universe eligibility (per-rebalance dynamic) ─────────────────────────────

def eligible_universe(prices, volumes, today, lookback=126):
    exclude = {SET_INDEX, "^SET.BK"}
    eligible = set()
    idx_pos = prices.index.get_loc(today)
    lb_pos  = max(0, idx_pos - lookback)
    window  = prices.index[lb_pos: idx_pos + 1]
    for tkr in [c for c in prices.columns if c not in exclude]:
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


# ─── Backtest engine ──────────────────────────────────────────────────────────

def backtest(prices, volumes, rs_days=63, top_n=15, rebal_days=10,
             regime_ma=200, rs_type="simple"):
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

    for i in range(1, len(trading_dates)):
        today = trading_dates[i]; prev = trading_dates[i - 1]
        sp0 = set_idx.get(prev); sp1 = set_idx.get(today)
        set_ret = (sp1 / sp0 - 1) if (sp0 and sp1 and sp0 > 0) else 0.0
        set_nav *= (1 + set_ret); set_log[today] = set_nav

        bull = bool(bull_mask.get(today, False))
        if not bull:
            if holdings:
                nav *= (1 - TCOST * len(holdings) / top_n); holdings = []; last_rebal = i
            nav_log[today] = nav; daily_rets.append(0.0); continue

        if (i - last_rebal) >= rebal_days or i == 1:
            _eligible = eligible_universe(prices, volumes, today)

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
                avg_vol  = volumes[[c + "_vol" for c in c_clean]].loc[:today].tail(21).mean()
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
                holdings.remove(d); nav *= (1 - TCOST)
            for t in top_n_list:
                if t not in holdings and len(holdings) < top_n:
                    holdings.append(t); nav *= (1 - TCOST / 2)

        if (i - last_rebal) >= rebal_days:
            old = set(holdings); new = set(top_n_list)
            n_trades = len(old - new) + len(new - old)
            if n_trades > 0:
                nav *= (1 - TCOST * n_trades / top_n)
            holdings = top_n_list[:]; last_rebal = i

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
    sharpe = ((d - rf_d).mean() / std * np.sqrt(252)) if (std > 1e-8) else 0.0
    return {"cagr": round(cagr, 1), "set_cagr": round(set_cagr, 1),
            "excess": round(cagr - set_cagr, 1),
            "max_dd": round(max_dd, 1), "sharpe": round(sharpe, 2)}


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("AlphaModel-TH — Walk-Forward Test")
    print("Train 5yr → pick best Sharpe → test 2yr (blind, no re-optimize)")
    print("=" * 70)

    prices, volumes = load_all()
    print(f"  DB loaded: {len(prices.columns)} tickers  "
          f"{prices.index[0].date()} → {prices.index[-1].date()}\n")

    oos_results = []

    for wname, tr_s, tr_e, te_s, te_e in WINDOWS:
        print(f"{'─'*70}")
        print(f"{wname}  Train: {tr_s[:4]}–{tr_e[:4]}  |  Test: {te_s[:4]}–{te_e[:4]}")

        px_tr, vl_tr = slice_period(prices, volumes, tr_s, tr_e)
        px_te, vl_te = slice_period(prices, volumes, te_s, te_e)

        # ── TRAIN: find best config by Sharpe ──
        best_label, best_sharpe, best_params = None, -99, None
        print(f"  {'Config':<22} {'CAGR':>8} {'Sharpe':>8}  (train)")
        for label, rs_days, top_n, rebal, regime_ma, rs_type in CONFIGS:
            r = backtest(px_tr, vl_tr, rs_days=rs_days, top_n=top_n,
                         rebal_days=rebal, regime_ma=regime_ma, rs_type=rs_type)
            if r is None:
                continue
            marker = ""
            if r["sharpe"] > best_sharpe:
                best_sharpe = r["sharpe"]; best_label = label
                best_params = (rs_days, top_n, rebal, regime_ma, rs_type)
                marker = " ←"
            print(f"  {label:<22} {r['cagr']:>+7.1f}%  {r['sharpe']:>7.2f}{marker}")

        print(f"\n  Best in-sample: [{best_label}]  Sharpe={best_sharpe:.2f}")

        # ── TEST: apply best config blind ──
        rs_d, tn, rb, rm, rt = best_params
        r_oos = backtest(px_te, vl_te, rs_days=rs_d, top_n=tn,
                         rebal_days=rb, regime_ma=rm, rs_type=rt)
        if r_oos:
            beat = r_oos["cagr"] > r_oos["set_cagr"]
            flag = "✅ BEAT" if beat else "❌ MISS"
            print(f"\n  OOS result [{best_label}]:")
            print(f"  CAGR {r_oos['cagr']:>+6.1f}%  SET {r_oos['set_cagr']:>+6.1f}%  "
                  f"Excess {r_oos['excess']:>+6.1f}%  DD {r_oos['max_dd']:>+6.1f}%  "
                  f"Sharpe {r_oos['sharpe']:.2f}  {flag}")
            oos_results.append({
                "window": wname, "config": best_label,
                "oos_cagr": r_oos["cagr"], "oos_set": r_oos["set_cagr"],
                "oos_excess": r_oos["excess"], "oos_dd": r_oos["max_dd"],
                "oos_sharpe": r_oos["sharpe"], "beat": beat,
            })

    # ── Summary ──
    print(f"\n{'='*70}")
    print("WALK-FORWARD SUMMARY")
    print(f"{'='*70}")
    print(f"\n{'Window':<6} {'Config':<22} {'OOS CAGR':>9} {'SET':>7} {'Excess':>8} {'MaxDD':>8} {'Sharpe':>8}  Result")
    print("─" * 80)

    beats = 0
    avg_sharpe = []
    avg_excess = []
    for r in oos_results:
        flag = "✅" if r["beat"] else "❌"
        if r["beat"]: beats += 1
        avg_sharpe.append(r["oos_sharpe"])
        avg_excess.append(r["oos_excess"])
        print(f"{r['window']:<6} {r['config']:<22} {r['oos_cagr']:>+8.1f}%  "
              f"{r['oos_set']:>+6.1f}%  {r['oos_excess']:>+7.1f}%  "
              f"{r['oos_dd']:>+7.1f}%  {r['oos_sharpe']:>7.2f}  {flag}")

    print("─" * 80)
    n = len(oos_results)
    print(f"{'OOS Average':<30} {'':>9}  {'':>7}  {sum(avg_excess)/n:>+7.1f}%  "
          f"{'':>7}  {sum(avg_sharpe)/n:>7.2f}")
    print(f"\nBeat SET OOS: {beats}/{n} windows")

    # ── Stress Test ──
    print(f"\n{'='*70}")
    print("STRESS TEST — Train before crash, Test ON the crash year")
    print("Question: does the strategy survive / preserve capital in bad years?")
    print(f"{'='*70}")

    stress_results = []
    for wname, tr_s, tr_e, te_s, te_e in STRESS_WINDOWS:
        px_tr, vl_tr = slice_period(prices, volumes, tr_s, tr_e)
        px_te, vl_te = slice_period(prices, volumes, te_s, te_e)

        # Pick best config from train period
        best_label2, best_sharpe2, best_params2 = None, -99, None
        for label, rs_days, top_n, rebal, regime_ma, rs_type in CONFIGS:
            r = backtest(px_tr, vl_tr, rs_days=rs_days, top_n=top_n,
                         rebal_days=rebal, regime_ma=regime_ma, rs_type=rs_type)
            if r and r["sharpe"] > best_sharpe2:
                best_sharpe2 = r["sharpe"]; best_label2 = label
                best_params2 = (rs_days, top_n, rebal, regime_ma, rs_type)

        rs_d, tn, rb, rm, rt = best_params2
        r_oos = backtest(px_te, vl_te, rs_days=rs_d, top_n=tn,
                         rebal_days=rb, regime_ma=rm, rs_type=rt)

        # Also run ALL configs OOS to see if any survive the crash
        all_oos = {}
        for label, rs_days, top_n, rebal, regime_ma, rs_type in CONFIGS:
            r = backtest(px_te, vl_te, rs_days=rs_days, top_n=top_n,
                         rebal_days=rebal, regime_ma=regime_ma, rs_type=rs_type)
            if r:
                all_oos[label] = r

        if r_oos:
            beat = r_oos["cagr"] > r_oos["set_cagr"]
            pos  = r_oos["cagr"] > 0
            flag = "✅ BEAT+POS" if (beat and pos) else ("⚡ BEAT" if beat else ("📉 POS" if pos else "❌ LOSS"))
            stress_results.append({
                "window": wname, "config": best_label2,
                "oos_cagr": r_oos["cagr"], "oos_set": r_oos["set_cagr"],
                "oos_excess": r_oos["excess"], "oos_dd": r_oos["max_dd"],
                "oos_sharpe": r_oos["sharpe"], "beat": beat, "positive": pos,
            })
            te_label = f"Test {te_s[:4]}" + (f"–{te_e[:4]}" if te_e[:4] != te_s[:4] else "")
            print(f"\n{wname} ({te_label})  best-in-sample=[{best_label2}]")
            print(f"  OOS: CAGR {r_oos['cagr']:>+6.1f}%  SET {r_oos['set_cagr']:>+6.1f}%  "
                  f"Excess {r_oos['excess']:>+6.1f}%  DD {r_oos['max_dd']:>+6.1f}%  "
                  f"Sharpe {r_oos['sharpe']:.2f}  {flag}")

            # Show all configs OOS for this stress window
            print(f"  All configs OOS [{te_label}]:")
            for lbl, ro in sorted(all_oos.items(), key=lambda x: -x[1]["cagr"]):
                b = "✅" if ro["cagr"] > ro["set_cagr"] else "❌"
                print(f"    {lbl:<22} CAGR {ro['cagr']:>+6.1f}%  DD {ro['max_dd']:>+6.1f}%  "
                      f"Sharpe {ro['sharpe']:.2f}  {b}")

    # Stress summary
    print(f"\n{'─'*70}")
    print("STRESS SUMMARY")
    beats_stress = sum(1 for r in stress_results if r["beat"])
    pos_stress   = sum(1 for r in stress_results if r["positive"])
    print(f"  Beat SET in crash years:     {beats_stress}/{len(stress_results)}")
    print(f"  Positive return in crash yrs: {pos_stress}/{len(stress_results)}")
    worst_stress_dd = min(r["oos_dd"] for r in stress_results) if stress_results else 0
    print(f"  Worst drawdown in crash year: {worst_stress_dd:.1f}%")

    # ── Verdict ──
    print(f"\n{'='*70}")
    print("VERDICT")
    print("─" * 70)
    avg_s = sum(avg_sharpe) / n
    avg_e = sum(avg_excess) / n
    worst_dd = min(r["oos_dd"] for r in oos_results)

    pass1 = beats >= 3
    pass2 = avg_s >= 1.0
    pass3 = worst_dd >= -30.0

    print(f"  [{'PASS' if pass1 else 'FAIL'}]  Beat SET OOS ≥ 3/4 windows:  {beats}/4")
    print(f"  [{'PASS' if pass2 else 'FAIL'}]  Avg OOS Sharpe ≥ 1.0:        {avg_s:.2f}")
    print(f"  [{'PASS' if pass3 else 'FAIL'}]  Worst OOS MaxDD ≥ -30%:      {worst_dd:.1f}%")

    if pass1 and pass2 and pass3:
        # Find most-selected config
        from collections import Counter
        best_config = Counter(r["config"] for r in oos_results).most_common(1)[0][0]
        print(f"\n  ✅ ROBUST — Lock [{best_config}] as AlphaModel-TH Baseline")
    elif pass1 and pass2:
        print(f"\n  ⚠️  CONDITIONAL — Strategy works but watch drawdown")
    else:
        print(f"\n  ❌ NOT READY — Overfit likely. Use simpler config (MA50 regime)")

    print(f"{'='*70}")


if __name__ == "__main__":
    main()
