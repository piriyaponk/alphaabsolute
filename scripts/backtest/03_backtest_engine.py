# -*- coding: utf-8 -*-
"""
Step 3: Backtesting engine -- monthly rebalance, liquidity-tilt weighting, regime exposure.

Parameters:
  rs_threshold:       RS composite percentile min (same as ChatGPT)
  rs_accel_min:       NEW -- RS acceleration min (positive = rising in rankings)
  vol_contract_max:   Vol contraction ratio max
  base_tight_max:     Base tightening ratio max
  adtv_min_m:         Min ADTV $M
  price_vs_ma50_min:  NEW -- min % above MA50 (e.g. 0 = must be above MA50)
  pct_from_52w_high_min: NEW -- max allowed distance from 52W high (e.g. -20)
  vol_trend_min:      NEW -- min volume trend ratio (>1 = rising volume)
  liquidity_power:    Weight = adtv^power
  cap_pct:            Single stock cap
  bull_exposure:      Deployment in bull regime
  bear_exposure:      Deployment in bear regime (SPY below MA200)
  benchmark:          QQQ or SPY
"""
import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field

ROOT        = Path(__file__).resolve().parents[2]
BT_DIR      = ROOT / "data" / "backtest"
SIGNAL_FILE = BT_DIR / "signals.parquet"


@dataclass
class BacktestParams:
    # --- Core filters (same as ChatGPT baseline) ---
    rs_threshold:     float = 70.0    # RS composite pct min
    vol_contract_max: float = 1.0     # vol_20/vol_60 <= this
    base_tight_max:   float = 1.0     # range_20/range_65 <= this
    adtv_min_m:       float = 15.0    # $M ADTV
    # --- Additional filters (our edge over ChatGPT) ---
    rs_accel_min:          float = -99.0  # RS accel min (default off = -99)
    price_vs_ma50_min:     float = -99.0  # must be X% above MA50 (default off)
    pct_from_52w_high_min: float = -99.0  # must be within X% of 52W high (default off)
    vol_trend_min:         float = 0.0    # volume trend (default off)
    # New v2 factors
    up_vol_ratio_min:      float = 0.0    # Lowry buying power (default off)
    trend_consistency_min: float = 0.0    # % positive days min (default off)
    sharpe_60d_min:        float = -99.0  # 60d Sharpe min (default off)
    beta_max:              float = 99.0   # max beta vs SPY (default off)
    # --- Portfolio construction ---
    liquidity_power: float = 2.0
    cap_pct:         float = 0.18
    bull_exposure:   float = 1.00
    bear_exposure:   float = 0.40
    # --- Cost model (from template: 50bps slippage + 30bps commission = 80bps round-trip) ---
    round_trip_cost: float = 0.008   # 0.8% per round-trip (buy+sell). 0=no cost model
    # --- Persistence (asymmetric exit: hold winners unless EXIT gate triggers) ---
    # If a stock was held last month, keep it unless rs_exit or vol_exit triggers
    # This dramatically reduces turnover without sacrificing alpha
    rs_exit_threshold:  float = 0.0   # Exit if rs_pct drops below this (0=disabled, e.g. 50)
    vol_exit_threshold: float = 0.0   # Exit if vol_trend drops below this (0=disabled, e.g. 0.80)
    # --- Meta ---
    rebal_freq:  str  = "ME"
    benchmark:   str  = "QQQ"
    start_date:  str  = "2017-01-01"
    end_date:    str  = "2026-08-20"
    exclude_tickers: list = field(default_factory=lambda: ["QQQ","SPY","IWM"])


def run_backtest(params: BacktestParams, signals: pd.DataFrame) -> dict:
    sig = signals.copy()
    sig["date"] = pd.to_datetime(sig["date"])
    sig = sig[(sig["date"] >= params.start_date) & (sig["date"] <= params.end_date)]
    sig = sig[~sig["ticker"].isin(params.exclude_tickers)]

    close_wide  = sig.pivot_table(index="date", columns="ticker", values="close")
    all_sig     = signals.copy()
    all_sig["date"] = pd.to_datetime(all_sig["date"])
    bench_close = all_sig[all_sig["ticker"] == params.benchmark].set_index("date")["close"]
    bench_close = bench_close[bench_close.index >= params.start_date]

    trading_days = close_wide.index.sort_values()
    rebal_dates  = pd.date_range(params.start_date, params.end_date, freq=params.rebal_freq)
    rebal_dates  = [trading_days[trading_days <= d][-1]
                    for d in rebal_dates if len(trading_days[trading_days <= d]) > 0]
    rebal_dates  = sorted(set(rebal_dates))

    portfolio_value = [1.0]
    bench_value     = [1.0]
    dates_out       = [pd.to_datetime(params.start_date)]
    monthly_returns = []
    turnover_log    = []
    prev_holdings   = {}
    holdings_count  = []

    for i, rebal_date in enumerate(rebal_dates[:-1]):
        next_date = rebal_dates[i + 1]
        day_sig   = sig[sig["date"] == rebal_date].copy()
        if day_sig.empty:
            continue

        # Regime: SPY above/below MA200
        spy_row = all_sig[(all_sig["ticker"] == "SPY") & (all_sig["date"] == rebal_date)]
        if not spy_row.empty:
            spy_above = spy_row["close"].iloc[0] > spy_row["ma_200"].iloc[0]
        else:
            spy_above = True
        exposure = params.bull_exposure if spy_above else params.bear_exposure

        # --- Screening ---
        mask = (
            (day_sig["rs_pct"]          >= params.rs_threshold) &
            (day_sig["vol_contraction"] <= params.vol_contract_max) &
            (day_sig["base_tight"]      <= params.base_tight_max) &
            (day_sig["adtv_63m"]        >= params.adtv_min_m) &
            (day_sig["close"]           >  day_sig["ma_200"])   # Stage 2 proxy
        )
        # Optional extra filters
        if params.rs_accel_min > -90:
            mask &= (day_sig["rs_accel"] >= params.rs_accel_min)
        if params.price_vs_ma50_min > -90:
            mask &= (day_sig["price_vs_ma50_pct"] >= params.price_vs_ma50_min)
        if params.pct_from_52w_high_min > -90:
            mask &= (day_sig["pct_from_52w_high"] >= params.pct_from_52w_high_min)
        if params.vol_trend_min > 0:
            mask &= (day_sig["vol_trend"] >= params.vol_trend_min)
        if params.up_vol_ratio_min > 0 and "up_vol_ratio" in day_sig.columns:
            mask &= (day_sig["up_vol_ratio"] >= params.up_vol_ratio_min)
        if params.trend_consistency_min > 0 and "trend_consistency" in day_sig.columns:
            mask &= (day_sig["trend_consistency"] >= params.trend_consistency_min)
        if params.sharpe_60d_min > -90 and "sharpe_60d" in day_sig.columns:
            mask &= (day_sig["sharpe_60d"] >= params.sharpe_60d_min)
        if params.beta_max < 90 and "beta_252" in day_sig.columns:
            mask &= (day_sig["beta_252"] <= params.beta_max)

        sel = day_sig[mask].copy()

        # Persistence: also include previously held stocks that pass a LOOSER exit gate
        # This prevents unnecessary turnover — hold until clearly broken
        if params.rs_exit_threshold > 0 or params.vol_exit_threshold > 0:
            held_tickers = set(prev_holdings.keys())
            if held_tickers:
                held_rows = day_sig[day_sig["ticker"].isin(held_tickers)].copy()
                exit_mask = pd.Series(True, index=held_rows.index)
                if params.rs_exit_threshold > 0:
                    exit_mask &= held_rows["rs_pct"] >= params.rs_exit_threshold
                if params.vol_exit_threshold > 0 and "vol_trend" in held_rows.columns:
                    exit_mask &= held_rows["vol_trend"] >= params.vol_exit_threshold
                # Also require still above MA200
                exit_mask &= held_rows["close"] > held_rows["ma_200"]
                keepers = held_rows[exit_mask]
                sel = pd.concat([sel, keepers]).drop_duplicates(subset=["ticker"])

        if sel.empty:
            holdings = {}
        else:
            sel["weight_raw"] = sel["adtv_63m"] ** params.liquidity_power
            sel["weight"]     = sel["weight_raw"] / sel["weight_raw"].sum()
            sel["weight"]     = sel["weight"].clip(upper=params.cap_pct)
            total_w           = sel["weight"].sum()
            if total_w > 0:
                sel["weight"] = sel["weight"] / total_w * exposure
            holdings = dict(zip(sel["ticker"], sel["weight"]))

        holdings_count.append(len(holdings))

        # Turnover
        all_t    = set(holdings) | set(prev_holdings)
        turnover = sum(abs(holdings.get(t,0) - prev_holdings.get(t,0)) for t in all_t)
        turnover_log.append(turnover)

        # Period returns
        period_days = trading_days[(trading_days > rebal_date) & (trading_days <= next_date)]
        if len(period_days) == 0:
            continue

        period_rets = {}
        for ticker, weight in holdings.items():
            if ticker in close_wide.columns:
                s = close_wide.loc[rebal_date, ticker]   if rebal_date    in close_wide.index else np.nan
                e = close_wide.loc[period_days[-1], ticker] if period_days[-1] in close_wide.index else np.nan
                if pd.notna(s) and pd.notna(e) and s > 0:
                    period_rets[ticker] = (e/s - 1) * weight

        cash_weight = 1.0 - sum(holdings.values())
        port_ret    = sum(period_rets.values())  # cash earns 0

        # Transaction cost: apply to changed positions only
        if params.round_trip_cost > 0:
            all_t = set(holdings) | set(prev_holdings)
            trade_value = sum(abs(holdings.get(t, 0) - prev_holdings.get(t, 0)) for t in all_t)
            port_ret -= trade_value * params.round_trip_cost

        if rebal_date in bench_close.index and period_days[-1] in bench_close.index:
            bench_ret = bench_close.loc[period_days[-1]] / bench_close.loc[rebal_date] - 1
        else:
            bench_ret = 0.0

        portfolio_value.append(portfolio_value[-1] * (1 + port_ret))
        bench_value.append(bench_value[-1] * (1 + bench_ret))
        dates_out.append(period_days[-1])
        monthly_returns.append(port_ret)
        prev_holdings = holdings

    port_series  = pd.Series(portfolio_value, index=dates_out)
    bench_series = pd.Series(bench_value,     index=dates_out)
    years = (dates_out[-1] - dates_out[0]).days / 365.25

    def cagr(s):
        return (s.iloc[-1]/s.iloc[0]) ** (1/years) - 1 if years > 0 else 0

    def max_dd(s):
        peak = s.cummax()
        return ((s - peak)/peak).min()

    def sharpe(rets):
        r = pd.Series(rets)
        return (r.mean()/r.std() * np.sqrt(12)) if len(r) > 2 and r.std() > 0 else 0

    def calmar(cagr_val, mdd_val):
        return cagr_val / abs(mdd_val) if abs(mdd_val) > 0 else 0

    port_cagr   = cagr(port_series)
    bench_cagr  = cagr(bench_series)
    port_mdd    = max_dd(port_series)
    bench_mdd   = max_dd(bench_series)
    port_sharpe = sharpe(monthly_returns)
    avg_holdings = np.mean(holdings_count) if holdings_count else 0
    avg_turnover = np.mean(turnover_log)   if turnover_log   else 0

    print(f"\n{'='*58}")
    print(f"  BACKTEST  {params.start_date} to {params.end_date}")
    print(f"{'='*58}")
    print(f"  Portfolio CAGR :  {port_cagr*100:.2f}%")
    print(f"  {params.benchmark} CAGR      :  {bench_cagr*100:.2f}%")
    print(f"  Excess         :  {(port_cagr-bench_cagr)*100:+.2f}%")
    print(f"  Max DD         :  {port_mdd*100:.2f}%  (vs {params.benchmark} {bench_mdd*100:.2f}%)")
    print(f"  Sharpe         :  {port_sharpe:.2f}")
    print(f"  Calmar         :  {calmar(port_cagr,port_mdd):.2f}")
    print(f"  Avg holdings   :  {avg_holdings:.1f} stocks")
    print(f"  Avg turnover   :  {avg_turnover:.1%}/mo")
    print(f"{'='*58}")

    return {
        "port_cagr":    port_cagr,
        "bench_cagr":   bench_cagr,
        "excess":       port_cagr - bench_cagr,
        "port_mdd":     port_mdd,
        "bench_mdd":    bench_mdd,
        "sharpe":       port_sharpe,
        "calmar":       calmar(port_cagr, port_mdd),
        "avg_turnover": avg_turnover,
        "avg_holdings": avg_holdings,
        "port_series":  port_series,
        "bench_series": bench_series,
    }


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    print("Loading signals...")
    signals = pd.read_parquet(SIGNAL_FILE)
    print(f"  {len(signals):,} rows, {signals['ticker'].nunique()} tickers")

    print("\n--- BASELINE (ChatGPT-equivalent defaults) ---")
    p = BacktestParams()
    run_backtest(p, signals)

    print("\n--- WITH RS ACCELERATION (our edge) ---")
    p2 = BacktestParams(rs_accel_min=2.0)   # RS must be rising
    run_backtest(p2, signals)

    print("\n--- WITH VOLUME TREND (rising volume) ---")
    p3 = BacktestParams(vol_trend_min=0.95)
    run_backtest(p3, signals)

    print("\n--- COMBINED EXTRA FACTORS ---")
    p4 = BacktestParams(rs_accel_min=2.0, vol_trend_min=0.95, pct_from_52w_high_min=-25.0)
    run_backtest(p4, signals)
