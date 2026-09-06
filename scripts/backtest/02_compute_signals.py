# -*- coding: utf-8 -*-
"""
Step 2: Compute all signals from price data.

Base signals (same as ChatGPT system):
  - RS composite: weighted 20d/60d/126d/252d excess return vs SPY (0.1/0.2/0.3/0.4)
  - RS percentile: cross-sectional rank 0-100
  - Vol contraction: 20d realized vol / 60d realized vol
  - Base tightening: avg daily range 20d / avg daily range 65d
  - ADTV: 63-day avg dollar volume ($M)
  - MA50, MA200
  - SPY regime (price above MA200)

Additional factors to beat ChatGPT:
  - rs_accel: RS momentum acceleration (rs_pct today - rs_pct 21d ago) -- identifies stocks MOVING UP in rankings
  - price_vs_ma50_pct: % above/below MA50 (positive = above)
  - price_vs_ma200_pct: % above/below MA200
  - pct_from_52w_high: closeness to 52W high (0 = at high, -20 = 20% below)
  - pct_from_52w_low: distance from 52W low
  - vol_trend: avg volume 20d / avg volume 60d (>1 = rising volume)
  - rs_pct_3m, rs_pct_6m separately for optimization
"""
import sys, io

import pandas as pd
import numpy as np
from pathlib import Path

ROOT          = Path(__file__).resolve().parents[2]
BT_DIR        = ROOT / "data" / "backtest"
PRICE_FILE    = BT_DIR / "prices.parquet"
SIGNAL_FILE   = BT_DIR / "signals.parquet"
SECTOR_FILE   = BT_DIR / "sector_prices.parquet"
SECTOR_MAP    = BT_DIR / "ticker_sector.json"


def compute_signals(prices: pd.DataFrame,
                    sector_prices: pd.DataFrame = None,
                    ticker_sector: dict = None) -> pd.DataFrame:
    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"])
    prices = prices.sort_values(["ticker", "date"])

    close  = prices.pivot(index="date", columns="ticker", values="close")
    high   = prices.pivot(index="date", columns="ticker", values="high")
    low    = prices.pivot(index="date", columns="ticker", values="low")
    volume = prices.pivot(index="date", columns="ticker", values="volume")

    print(f"Computing signals: {close.shape[1]} tickers x {close.shape[0]} days")

    spy = close["SPY"] if "SPY" in close.columns else pd.Series(1, index=close.index)

    # --- Returns ---
    def excess_return(n):
        ret  = close.pct_change(n)
        spy_ret = spy.pct_change(n)
        return ret.subtract(spy_ret, axis=0)

    ex_20  = excess_return(20)
    ex_60  = excess_return(60)
    ex_126 = excess_return(126)
    ex_252 = excess_return(252)

    rs_composite = 0.1*ex_20 + 0.2*ex_60 + 0.3*ex_126 + 0.4*ex_252
    rs_pct       = rs_composite.rank(axis=1, pct=True) * 100   # 0-100

    # RS at 3M and 6M separately (for individual thresholds in optimizer)
    rs_pct_3m = ex_60.rank(axis=1, pct=True) * 100
    rs_pct_6m = ex_126.rank(axis=1, pct=True) * 100

    # RS acceleration: RS percentile now vs 21 days ago
    rs_accel = rs_pct - rs_pct.shift(21)

    # --- Volatility ---
    log_ret = np.log(close / close.shift(1))
    vol_20  = log_ret.rolling(20).std() * np.sqrt(252)
    vol_60  = log_ret.rolling(60).std() * np.sqrt(252)
    vol_contraction = vol_20 / vol_60   # < 1.0 = contracting

    # --- Base tightening ---
    daily_range  = (high - low) / close
    range_20     = daily_range.rolling(20).mean()
    range_65     = daily_range.rolling(65).mean()
    base_tight   = range_20 / range_65  # < 1.0 = tightening

    # --- ADTV ---
    dollar_vol  = close * volume
    adtv_63     = dollar_vol.rolling(63).mean() / 1e6  # $M

    # --- Moving averages ---
    ma_50  = close.rolling(50).mean()
    ma_200 = close.rolling(200).mean()

    price_vs_ma50_pct  = (close - ma_50)  / ma_50  * 100   # % above MA50
    price_vs_ma200_pct = (close - ma_200) / ma_200 * 100   # % above MA200

    # --- 52W levels ---
    high_52w = close.rolling(252).max()
    low_52w  = close.rolling(252).min()
    pct_from_52w_high = (close - high_52w) / high_52w * 100  # negative
    pct_from_52w_low  = (close - low_52w)  / low_52w  * 100  # positive

    # --- Volume trend ---
    vol_20_avg = volume.rolling(20).mean()
    vol_60_avg = volume.rolling(60).mean()
    vol_trend  = vol_20_avg / vol_60_avg   # > 1.0 = rising volume

    # --- Up-volume ratio (Lowry Buying Power) ---
    # Fraction of total 20d dollar volume on up-days
    up_days   = log_ret > 0
    up_dvol   = (dollar_vol * up_days).rolling(20).sum()
    tot_dvol  = dollar_vol.rolling(20).sum()
    up_vol_ratio = up_dvol / tot_dvol.replace(0, np.nan)   # > 0.6 = accumulation

    # --- Beta vs SPY (252d) ---
    spy_log = np.log(spy / spy.shift(1))
    spy_var  = spy_log.rolling(252).var()
    beta_252 = log_ret.apply(lambda col: col.rolling(252).cov(spy_log)).div(spy_var, axis=0)

    # --- Trend consistency: % positive days last 63d ---
    trend_consistency = (log_ret > 0).rolling(63).mean() * 100   # 50=random, >60=strong trend

    # --- 60-day Sharpe: mean/std of daily log returns scaled ---
    sharpe_60d = (log_ret.rolling(60).mean() / log_ret.rolling(60).std()) * np.sqrt(252)

    # --- Drawdown recovery: max DD 63d vs max DD 63d prior ---
    def rolling_mdd(df, n):
        roll_max = df.rolling(n).max()
        return ((df - roll_max) / roll_max).rolling(n).min()  # most negative

    mdd_63  = rolling_mdd(close, 63)
    mdd_126 = rolling_mdd(close.shift(63), 63)   # prior 63d window
    # ratio > 1 = DD getting worse, < 1 = recovering (we want improving)
    dd_recovery = mdd_63 / mdd_126.replace(0, np.nan)   # < 1 = improving

    # --- Resilience vs market: stock DD / SPY DD over same window ---
    # resilience_3m < 1.0 → stock fell LESS than SPY → strong stock (lower = better)
    # resilience_6m < 1.0 → same for 6 months
    spy_mdd_63  = rolling_mdd(spy.to_frame("SPY"), 63)["SPY"]
    spy_mdd_126 = rolling_mdd(spy.to_frame("SPY"), 126)["SPY"]

    # Avoid div by zero (flat SPY) — replace 0 with small number
    spy_mdd_63_safe  = spy_mdd_63.replace(0, np.nan)
    spy_mdd_126_safe = spy_mdd_126.replace(0, np.nan)

    # stock_mdd / spy_mdd: ratio < 1 = held up better than market
    stock_mdd_63  = rolling_mdd(close, 63)
    stock_mdd_126 = rolling_mdd(close, 126)
    resilience_3m = stock_mdd_63.div(spy_mdd_63_safe, axis=0)
    resilience_6m = stock_mdd_126.div(spy_mdd_126_safe, axis=0)

    # Clip extremes (SPY barely moved → ratio blows up)
    resilience_3m = resilience_3m.clip(-5, 5)
    resilience_6m = resilience_6m.clip(-5, 5)

    # --- Base position: where in the 20d range is price? ---
    # 0 = at base low, 1 = at base high, ~0.3-0.5 = good buy zone
    base_range = (high.rolling(20).max() - low.rolling(20).min()).replace(0, np.nan)
    base_position = (close - low.rolling(20).min()) / base_range  # 0-1

    # --- Accumulation / Distribution Line slope (Wyckoff / smart money flow) ---
    # A/D line = running sum of money flow volume
    # Positive slope = accumulation (smart money buying); Negative = distribution
    clv = ((close - low) - (high - close)) / (high - low).replace(0, np.nan)  # Close Location Value
    ad_volume = clv * volume                                    # money flow volume per day
    ad_line   = ad_volume.cumsum()                             # running total
    # Slope: 20d change in A/D line, normalized by avg daily dollar volume
    ad_slope_raw = ad_line.diff(20)
    ad_slope     = ad_slope_raw / (dollar_vol.rolling(20).mean().replace(0, np.nan))  # dimensionless

    # --- Pocket Pivot (Kacher & Morales / Minervini) ---
    # Buy signal: up day where volume > max volume on any DOWN day in prior 10 sessions
    # Higher = stronger institutional footprint; 0 = no pocket pivot today
    is_up_day    = log_ret > 0
    max_dn_vol   = (volume * (~is_up_day)).rolling(10).max()           # max volume on down days last 10d
    pocket_pivot = (volume * is_up_day) / max_dn_vol.replace(0, np.nan)  # > 1.0 = pocket pivot signal

    # --- Wyckoff Spring strength ---
    # Spring = false breakdown below N-day low followed by recovery above it
    # spring_score > 0 means a spring occurred within last 5 days (strength = recovery %)
    low_20_min = low.rolling(20).min()
    broke_below  = (low < low_20_min.shift(1)).astype(float)          # intraday breach of 20d low
    recovered    = (close > low_20_min.shift(1)).astype(float)         # close recovered above 20d low
    spring_day   = broke_below * recovered                             # 1 if spring candle
    recovery_pct = ((close - low) / close * 100) * spring_day         # size of recovery
    spring_score = recovery_pct.rolling(5).max()                       # max spring in last 5 days

    # --- Sector-relative resilience (better than SPY-relative) ---
    # Compare stock's max DD to its own SECTOR ETF's max DD over same window
    # sector_resilience_3m < 1.0 = stock held up better than its sector = leader
    # Falls back to SPY-relative if sector data not available
    if sector_prices is not None and ticker_sector is not None:
        sec_prices_clean = sector_prices.copy()
        sec_prices_clean["date"] = pd.to_datetime(sec_prices_clean["date"])
        sec_close_wide = sec_prices_clean.pivot(index="date", columns="ticker", values="close")

        # Build per-ticker sector ETF DD series
        sec_mdd_63_dict  = {}
        sec_mdd_126_dict = {}
        for etf in sec_close_wide.columns:
            s = sec_close_wide[etf].reindex(close.index, method="ffill")
            s_frame = s.to_frame(etf)
            sec_mdd_63_dict[etf]  = rolling_mdd(s_frame, 63)[etf]
            sec_mdd_126_dict[etf] = rolling_mdd(s_frame, 126)[etf]

        # For each stock, divide its DD by its sector ETF DD
        sector_res_3m_cols  = {}
        sector_res_6m_cols  = {}
        for ticker in close.columns:
            etf = ticker_sector.get(ticker, "SPY")
            if etf in sec_mdd_63_dict:
                sec_dd_63  = sec_mdd_63_dict[etf].replace(0, np.nan)
                sec_dd_126 = sec_mdd_126_dict[etf].replace(0, np.nan)
            else:
                sec_dd_63  = spy_mdd_63_safe
                sec_dd_126 = spy_mdd_126_safe
            sector_res_3m_cols[ticker]  = (stock_mdd_63[ticker]  / sec_dd_63).clip(-5, 5)
            sector_res_6m_cols[ticker]  = (stock_mdd_126[ticker] / sec_dd_126).clip(-5, 5)

        sector_resilience_3m = pd.DataFrame(sector_res_3m_cols, index=close.index)
        sector_resilience_6m = pd.DataFrame(sector_res_6m_cols, index=close.index)
    else:
        # Fallback: SPY-relative (same as resilience_3m/6m)
        sector_resilience_3m = resilience_3m.copy()
        sector_resilience_6m = resilience_6m.copy()

    # --- SPY regime ---
    spy_ma200        = spy.rolling(200).mean()
    spy_above_ma200  = (spy > spy_ma200).astype(int)

    # --- Stack to long format ---
    all_cols = {
        "rs_composite":       rs_composite,
        "rs_pct":             rs_pct,
        "rs_pct_3m":          rs_pct_3m,
        "rs_pct_6m":          rs_pct_6m,
        "rs_accel":           rs_accel,
        "vol_contraction":    vol_contraction,
        "base_tight":         base_tight,
        "adtv_63m":           adtv_63,
        "close":              close,
        "ma_50":              ma_50,
        "ma_200":             ma_200,
        "price_vs_ma50_pct":  price_vs_ma50_pct,
        "price_vs_ma200_pct": price_vs_ma200_pct,
        "pct_from_52w_high":  pct_from_52w_high,
        "pct_from_52w_low":   pct_from_52w_low,
        "vol_trend":          vol_trend,
        "vol_20":             vol_20,
        "vol_60":             vol_60,
        # v2 factors
        "up_vol_ratio":       up_vol_ratio,
        "beta_252":           beta_252,
        "trend_consistency":  trend_consistency,
        "sharpe_60d":         sharpe_60d,
        "dd_recovery":        dd_recovery,
        # v3 factors
        "resilience_3m":         resilience_3m,
        "resilience_6m":         resilience_6m,
        "base_position":         base_position,
        "sector_resilience_3m":  sector_resilience_3m,
        "sector_resilience_6m":  sector_resilience_6m,
        "ad_slope":              ad_slope,
        "pocket_pivot":          pocket_pivot,
        "spring_score":          spring_score,
    }

    long_frames = []
    for name, df in all_cols.items():
        stacked = df.stack(future_stack=True).rename(name)
        long_frames.append(stacked)

    signals = pd.concat(long_frames, axis=1).reset_index()
    signals.columns.name = None

    # Fix column names after stack
    if "level_1" in signals.columns:
        signals = signals.rename(columns={"level_1": "ticker"})

    # Merge SPY regime
    spy_reg = spy_above_ma200.rename("spy_above_ma200").reset_index()
    spy_reg.columns = ["date", "spy_above_ma200"]
    signals = signals.merge(spy_reg, on="date", how="left")

    signals["date"] = signals["date"].dt.strftime("%Y-%m-%d")
    signals = signals.dropna(subset=["rs_composite", "close"])

    print(f"Signals: {len(signals):,} rows, {signals['ticker'].nunique()} tickers")
    return signals


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    import json as _json

    print("Loading prices...")
    prices = pd.read_parquet(PRICE_FILE)
    print(f"  {len(prices):,} rows, {prices['ticker'].nunique()} tickers")

    sector_prices = None
    ticker_sector = None
    if SECTOR_FILE.exists() and SECTOR_MAP.exists():
        print("Loading sector data...")
        sector_prices = pd.read_parquet(SECTOR_FILE)
        ticker_sector = _json.loads(SECTOR_MAP.read_text())
        print(f"  Sector ETFs: {sector_prices['ticker'].nunique()}  |  Mapped tickers: {len(ticker_sector)}")
    else:
        print("  [WARN] sector_prices.parquet or ticker_sector.json not found — falling back to SPY-relative")

    signals = compute_signals(prices, sector_prices=sector_prices, ticker_sector=ticker_sector)
    signals.to_parquet(SIGNAL_FILE, index=False)
    print(f"Saved: {SIGNAL_FILE}")
    print(signals[["rs_pct","rs_accel","vol_contraction","base_tight","adtv_63m","vol_trend"]].describe().round(2))
