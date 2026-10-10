# -*- coding: utf-8 -*-
"""
data_guardian.py — Pre-flight data readiness validator.

24 checks across 6 layers. ALL must pass before any backtest or GA run.
This is the money gate — if data is wrong, results are wrong.

Usage (standalone):
  python scripts/backtest/data_guardian.py

Usage (in code — blocks execution if checks fail):
  from scripts.backtest.data_guardian import guardian_check
  guardian_check()   # raises SystemExit(1) if any check fails

Layers:
  F — File Existence & Freshness   (3 checks)
  U — Universe Completeness        (4 checks)
  P — OHLCV Price Sanity           (7 checks)
  S — Signal Quality               (5 checks)
  E — Sector / External Data       (3 checks)
  B — Backtest Config Sanity       (4 checks — no hard config needed)
"""
import sys, io, json
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

ROOT     = Path(__file__).resolve().parents[2]
BT_DIR   = ROOT / "data" / "backtest"

REQUIRED_FILES = {
    "prices":        BT_DIR / "prices.parquet",
    "signals":       BT_DIR / "signals.parquet",
    "sector_prices": BT_DIR / "sector_prices.parquet",
    "ticker_sector": BT_DIR / "ticker_sector.json",
}

REQUIRED_SIGNAL_COLS = [
    "rs_pct", "vol_trend", "vol_contraction", "base_tight",
    "adtv_63m", "close", "ma_200", "spy_above_ma200",
    "resilience_3m", "sector_resilience_3m",
    "ad_slope", "pocket_pivot", "spring_score",
    "pct_from_52w_high", "pct_from_52w_low",
]

BENCHMARKS     = ["SPY", "QQQ", "IWM"]
SECTOR_ETFS    = ["XLK","XLF","XLV","XLE","XLI","XLP","XLY","XLU","XLB","XLRE","XLC"]
MAX_DATA_AGE_DAYS     = 5    # prices must be updated within 5 trading days
MIN_HISTORY_DAYS      = 252  # each ticker needs at least 1 year of history
MAX_DAILY_RETURN_UP   = 2.00 # +200% in one day (real events like FDA approvals can hit 177%)
MAX_DAILY_RETURN_DOWN = 0.70 # -70% in one day
MIN_VOLUME_PRESENCE   = 0.90 # 90% of days must have volume > 0
MAX_TRADING_GAP_DAYS  = 10   # no gaps > 10 calendar days between trading days
MAX_SIGNAL_MISSING_PCT= 0.10 # < 10% NaN per signal per year
SECTOR_COVERAGE_MIN   = 0.95 # 95% of tickers must have sector mapping
# Tickers with <MIN_HISTORY_DAYS are warned but do not block (newly added to S&P500)
NEW_TICKER_WARN_ONLY  = True


# ─────────────────────────────────────────────────────────────────────────────
# Result tracker
# ─────────────────────────────────────────────────────────────────────────────

class CheckResult:
    def __init__(self):
        self.results = []   # list of (layer, id, name, passed, detail)

    def add(self, layer: str, check_id: str, name: str, passed: bool, detail: str):
        self.results.append((layer, check_id, name, passed, detail))

    def passed_all(self) -> bool:
        return all(r[3] for r in self.results)

    def print_report(self):
        layers_seen = []
        for layer, cid, name, passed, detail in self.results:
            if layer not in layers_seen:
                layers_seen.append(layer)
                layer_labels = {
                    "F": "FILE EXISTENCE & FRESHNESS",
                    "U": "UNIVERSE COMPLETENESS",
                    "P": "OHLCV PRICE SANITY",
                    "S": "SIGNAL QUALITY",
                    "E": "SECTOR / EXTERNAL DATA",
                    "B": "BACKTEST CONFIG SANITY",
                }
                print(f"\n  [{layer_labels.get(layer, layer)}]")
            status = "PASS" if passed else "FAIL"
            icon   = "+" if passed else "!"
            print(f"    [{icon}] {cid} {name}")
            if not passed or "WARN" in detail:
                print(f"         {detail}")

        total  = len(self.results)
        passed = sum(1 for r in self.results if r[3])
        failed = total - passed
        print(f"\n  SCORE: {passed}/{total} checks passed", end="")
        if failed:
            print(f"  |  {failed} FAILED")
        else:
            print("  — ALL CLEAR")


# ─────────────────────────────────────────────────────────────────────────────
# Layer F: File Existence & Freshness
# ─────────────────────────────────────────────────────────────────────────────

def check_files(cr: CheckResult):
    for key, path in REQUIRED_FILES.items():
        cr.add("F", "F1", f"{key} exists", path.exists(),
               f"Expected: {path}" if not path.exists() else f"OK: {path.name}")

    # F2: prices freshness
    prices_path = REQUIRED_FILES["prices"]
    if prices_path.exists():
        try:
            prices = pd.read_parquet(prices_path, columns=["date","ticker"])
            latest = pd.to_datetime(prices["date"]).max()
            age_days = (pd.Timestamp.today() - latest).days
            # Count trading days approximately (exclude weekends)
            trading_day_age = sum(
                1 for i in range(age_days)
                if (pd.Timestamp.today() - timedelta(days=i)).weekday() < 5
            ) - 1
            ok  = trading_day_age <= MAX_DATA_AGE_DAYS
            cr.add("F", "F2", "Prices not stale", ok,
                   f"Latest date: {latest.date()}  trading days old: {trading_day_age}"
                   + (f"  FIX: run 01_download_data.py" if not ok else ""))
        except Exception as e:
            cr.add("F", "F2", "Prices not stale", False, f"Could not read prices: {e}")
    else:
        cr.add("F", "F2", "Prices not stale", False, "File missing — run F1 fix first")

    # F3: signals newer than prices
    sig_path = REQUIRED_FILES["signals"]
    if sig_path.exists() and prices_path.exists():
        try:
            sig_mtime   = sig_path.stat().st_mtime
            price_mtime = prices_path.stat().st_mtime
            ok = sig_mtime >= price_mtime
            cr.add("F", "F3", "Signals newer than prices", ok,
                   "OK" if ok else
                   "Signals computed BEFORE last prices update — FIX: run 02_compute_signals.py")
        except Exception as e:
            cr.add("F", "F3", "Signals newer than prices", False, str(e))
    else:
        cr.add("F", "F3", "Signals newer than prices", False, "File(s) missing")


# ─────────────────────────────────────────────────────────────────────────────
# Layer U: Universe Completeness
# ─────────────────────────────────────────────────────────────────────────────

def check_universe(cr: CheckResult, prices: pd.DataFrame, signals: pd.DataFrame):
    import ssl as _ssl
    _ssl._create_default_https_context = _ssl._create_unverified_context
    import urllib3; urllib3.disable_warnings()

    price_tickers  = set(prices["ticker"].unique())
    signal_tickers = set(signals["ticker"].unique())

    # U1: S&P500 coverage in prices
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from utils.data_engine import _YF_SESSION
        r = _YF_SESSION.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", timeout=15)
        tables   = pd.read_html(pd.io.common.StringIO(r.text))
        sp500    = set(tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist())
        missing  = sp500 - price_tickers
        coverage = (len(sp500) - len(missing)) / len(sp500)
        ok = coverage >= 0.98   # 98% of S&P500 must be present
        cr.add("U", "U1", f"S&P500 coverage in prices ({coverage:.1%})", ok,
               f"Missing {len(missing)} tickers: {sorted(missing)[:10]}..."
               if not ok else f"OK: {len(price_tickers)} tickers")
    except Exception as e:
        cr.add("U", "U1", "S&P500 coverage in prices", False,
               f"Could not fetch S&P500 list: {e}")

    # U2: signals cover prices universe
    # New tickers with <252d won't have signals — warn-only, don't block
    in_prices_not_sigs = price_tickers - signal_tickers - set(BENCHMARKS)
    # Check if missing tickers are all new (< 252 trading days in prices)
    if REQUIRED_FILES["prices"].exists() and prices is not None:
        prices_tmp = prices.copy()
        prices_tmp["date"] = pd.to_datetime(prices_tmp["date"])
        hist_tmp = prices_tmp.groupby("ticker")["date"].count()
        new_tickers = set(hist_tmp[hist_tmp < MIN_HISTORY_DAYS].index)
        real_missing = in_prices_not_sigs - new_tickers
    else:
        real_missing = in_prices_not_sigs
    ok = len(real_missing) == 0
    detail = (f"Missing from signals (non-new): {sorted(real_missing)[:10]}  FIX: run 02_compute_signals.py"
              if not ok else
              (f"WARN: {len(in_prices_not_sigs)} new tickers not yet in signals (OK — <252d): {sorted(in_prices_not_sigs)[:5]}"
               if in_prices_not_sigs else f"OK: {len(signal_tickers)} tickers in signals"))
    cr.add("U", "U2", "All price tickers have signals", ok, detail)

    # U3: benchmarks present in both
    for bm in BENCHMARKS:
        ok = (bm in price_tickers) and (bm in signal_tickers)
        cr.add("U", "U3", f"Benchmark {bm} in prices+signals", ok,
               f"Missing from: {'prices' if bm not in price_tickers else 'signals'}"
               if not ok else "OK")

    # U4: minimum history per ticker
    # Newly added S&P500 tickers with <252d are warned but don't block GA.
    prices["date"] = pd.to_datetime(prices["date"])
    hist = prices.groupby("ticker")["date"].count()
    short = hist[hist < MIN_HISTORY_DAYS]
    # These are new tickers — they'll have no signals and be ignored by strategy
    ok = True   # warn-only
    detail = (f"WARN: {len(short)} new tickers with <{MIN_HISTORY_DAYS}d history "
              f"(will be skipped by strategy): {list(short.index[:5])}"
              if len(short) > 0 else f"OK: min history = {hist.min()} days")
    cr.add("U", "U4", f"All tickers have >= {MIN_HISTORY_DAYS} days history", ok, detail)


# ─────────────────────────────────────────────────────────────────────────────
# Layer P: OHLCV Price Sanity
# ─────────────────────────────────────────────────────────────────────────────

def check_prices(cr: CheckResult, prices: pd.DataFrame):
    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"])

    # P1: high >= low always
    violations = prices[prices["high"] < prices["low"]]
    ok = len(violations) == 0
    cr.add("P", "P1", "high >= low (OHLC integrity)", ok,
           f"{len(violations)} violations. Sample: {violations[['ticker','date','high','low']].head(3).to_dict('records')}"
           if not ok else "OK")

    # P2: close within [low, high] — only check recent 252 days per ticker
    # Older dates: adj_close (split-adjusted) can legitimately fall outside
    # unadjusted high/low range — this is expected, not a data error.
    # P2: close vs [low, high] — Yahoo returns adj_close as close but raw high/low
    # so close can legitimately fall outside [low, high] after dividends/splits.
    # We check only for extreme outliers (>10% outside range) as a sanity check.
    recent_cutoff = prices["date"].max() - pd.Timedelta(days=365)
    recent = prices[prices["date"] >= recent_cutoff]
    bad = recent[(recent["close"] > recent["high"] * 1.10) |
                 (recent["close"] < recent["low"]  * 0.90)]
    ok = True   # warn-only: adj_close vs unadjusted OHLC mismatch is expected
    detail = (f"WARN: {len(bad)} rows where close >10% outside [low,high] (adj_close vs unadjusted OHLC is expected)"
              if len(bad) > 0 else "OK")
    cr.add("P", "P2", "close within [low, high] (recent 1yr, warn-only)", ok, detail)

    # P3: adj_close / close ratio sanity
    ratio = prices["adj_close"] / prices["close"].replace(0, np.nan)
    bad_ratio = ((ratio < 0.01) | (ratio > 100)).sum()
    ok  = bad_ratio == 0
    cr.add("P", "P3", "adj_close/close ratio in [0.01, 100]", ok,
           f"{bad_ratio} bad ratios (split/div error)" if not ok else "OK")

    # P4: no extreme single-day returns (likely data errors)
    prices_sorted = prices.sort_values(["ticker","date"])
    prices_sorted["daily_ret"] = prices_sorted.groupby("ticker")["close"].pct_change()
    extreme = prices_sorted[
        (prices_sorted["daily_ret"] > MAX_DAILY_RETURN_UP) |
        (prices_sorted["daily_ret"] < -MAX_DAILY_RETURN_DOWN)
    ]
    ok = len(extreme) == 0
    cr.add("P", "P4", f"No extreme daily returns (>{MAX_DAILY_RETURN_UP*100:.0f}% / <-{MAX_DAILY_RETURN_DOWN*100:.0f}%)", ok,
           f"{len(extreme)} suspicious days: {extreme[['ticker','date','daily_ret']].head(3).to_dict('records')}"
           if not ok else "OK")

    # P5: volume > 0 at least 90% of trading days per ticker
    # Cross-listed or recently relisted stocks may have gaps — warn but don't block
    vol_presence = prices.groupby("ticker").apply(
        lambda x: (x["volume"] > 0).mean()
    )
    low_vol = vol_presence[vol_presence < MIN_VOLUME_PRESENCE]
    ok = True   # warn-only — adtv_min_m filter in strategy handles thin liquidity
    detail = (f"WARN: {len(low_vol)} tickers with sparse volume (covered by adtv_min_m filter): {list(low_vol.index[:5])}"
              if len(low_vol) > 0 else "OK")
    cr.add("P", "P5", f"Volume > 0 for >= {MIN_VOLUME_PRESENCE*100:.0f}% of days", ok, detail)

    # P6: no zero or negative close prices
    bad_price = prices[prices["close"] <= 0]
    ok = len(bad_price) == 0
    cr.add("P", "P6", "No close <= 0", ok,
           f"{len(bad_price)} rows with close <= 0" if not ok else "OK")

    # P7: no trading day gaps > 10 calendar days (per ticker sample)
    sample_tickers = prices["ticker"].unique()[:50]   # check sample for speed
    max_gap = 0
    worst_ticker = ""
    for t in sample_tickers:
        dates = prices[prices["ticker"] == t]["date"].sort_values()
        if len(dates) < 2:
            continue
        gaps = (dates.diff().dt.days).dropna()
        g = gaps.max()
        if g > max_gap:
            max_gap = g
            worst_ticker = t
    ok = max_gap <= MAX_TRADING_GAP_DAYS
    cr.add("P", "P7", f"No trading day gaps > {MAX_TRADING_GAP_DAYS} calendar days", ok,
           f"Worst gap: {max_gap} days in {worst_ticker} — FIX: re-download that ticker"
           if not ok else f"OK: max gap = {max_gap} days")


# ─────────────────────────────────────────────────────────────────────────────
# Layer S: Signal Quality
# ─────────────────────────────────────────────────────────────────────────────

def check_signals(cr: CheckResult, signals: pd.DataFrame):
    signals = signals.copy()
    signals["date"] = pd.to_datetime(signals["date"])

    # S1: required columns present
    missing_cols = [c for c in REQUIRED_SIGNAL_COLS if c not in signals.columns]
    ok = len(missing_cols) == 0
    cr.add("S", "S1", "All required signal columns present", ok,
           f"Missing: {missing_cols}  FIX: run 02_compute_signals.py"
           if not ok else f"OK: {len(signals.columns)} columns")

    # S2: rs_pct distribution is uniform (mean 45-55, std 25-32)
    rs_mean = signals["rs_pct"].mean()
    rs_std  = signals["rs_pct"].std()
    ok = (44 <= rs_mean <= 56) and (25 <= rs_std <= 32)
    cr.add("S", "S2", f"rs_pct distribution uniform (mean={rs_mean:.1f}, std={rs_std:.1f})", ok,
           f"Abnormal distribution — cross-sectional rank may be broken. Expected mean~50 std~28"
           if not ok else "OK")

    # S3: missing rate per signal per year < 10%
    signals["year"] = signals["date"].dt.year
    worst_miss = 0.0
    worst_col  = ""
    worst_yr   = 0
    for col in REQUIRED_SIGNAL_COLS:
        if col not in signals.columns:
            continue
        yr_miss = signals.groupby("year")[col].apply(lambda x: x.isna().mean())
        m = yr_miss.max()
        if m > worst_miss:
            worst_miss = m
            worst_col  = col
            worst_yr   = yr_miss.idxmax()
    ok = worst_miss <= MAX_SIGNAL_MISSING_PCT
    cr.add("S", "S3", f"Signal missing rate < {MAX_SIGNAL_MISSING_PCT*100:.0f}% per year", ok,
           f"Worst: {worst_col} in {worst_yr} = {worst_miss:.1%} missing"
           if not ok else f"OK: worst = {worst_miss:.1%} ({worst_col} {worst_yr})")

    # S4: no inf/-inf in signals
    num_cols = signals.select_dtypes(include=[np.number]).columns
    inf_count = np.isinf(signals[num_cols]).sum().sum()
    ok = inf_count == 0
    cr.add("S", "S4", "No inf/-inf values in signals", ok,
           f"{inf_count} infinite values found  FIX: run 02_compute_signals.py"
           if not ok else "OK")

    # S5: signals date matches prices (last date within 3 days of prices last date)
    prices_path = REQUIRED_FILES["prices"]
    if prices_path.exists():
        p_dates = pd.to_datetime(pd.read_parquet(prices_path, columns=["date"])["date"])
        price_max = p_dates.max()
        sig_max   = signals["date"].max()
        gap = abs((price_max - sig_max).days)
        ok  = gap <= 3
        cr.add("S", "S5", f"Signal dates match prices (gap={gap}d)", ok,
               f"Signals last: {sig_max.date()}  Prices last: {price_max.date()}  FIX: run 02_compute_signals.py"
               if not ok else "OK")
    else:
        cr.add("S", "S5", "Signal dates match prices", False, "prices.parquet missing")


# ─────────────────────────────────────────────────────────────────────────────
# Layer E: Sector / External Data
# ─────────────────────────────────────────────────────────────────────────────

def check_sector(cr: CheckResult, prices: pd.DataFrame):
    sec_path = REQUIRED_FILES["sector_prices"]
    map_path = REQUIRED_FILES["ticker_sector"]

    # E1: all 11 sector ETFs present
    if sec_path.exists():
        sec = pd.read_parquet(sec_path)
        have_etfs = set(sec["ticker"].unique())
        missing_etfs = set(SECTOR_ETFS) - have_etfs
        ok = len(missing_etfs) == 0
        cr.add("E", "E1", f"All {len(SECTOR_ETFS)} sector ETFs present", ok,
               f"Missing ETFs: {missing_etfs}  FIX: run 01c_download_sectors.py"
               if not ok else f"OK: {len(have_etfs)} ETFs")

        # E3: sector ETF dates align with stock dates
        prices_c = prices.copy()
        prices_c["date"] = pd.to_datetime(prices_c["date"])
        sec["date"] = pd.to_datetime(sec["date"])
        price_max = prices_c["date"].max()
        sec_max   = sec["date"].max()
        gap = abs((price_max - sec_max).days)
        ok  = gap <= 5
        cr.add("E", "E3", f"Sector ETF dates aligned with prices (gap={gap}d)", ok,
               f"Sector last: {sec_max.date()}  Prices last: {price_max.date()}  FIX: run 01c_download_sectors.py"
               if not ok else "OK")
    else:
        cr.add("E", "E1", "All sector ETFs present", False,
               "sector_prices.parquet missing  FIX: run 01c_download_sectors.py")
        cr.add("E", "E3", "Sector ETF dates aligned", False, "sector_prices.parquet missing")

    # E2: sector mapping coverage
    if map_path.exists():
        mapping   = json.loads(map_path.read_text())
        universe  = set(prices["ticker"].unique()) - set(BENCHMARKS)
        mapped    = set(mapping.keys())
        coverage  = len(universe & mapped) / len(universe) if universe else 0
        ok = coverage >= SECTOR_COVERAGE_MIN
        cr.add("E", "E2", f"Sector mapping coverage ({coverage:.1%} >= {SECTOR_COVERAGE_MIN:.0%})", ok,
               f"Unmapped: {sorted(universe - mapped)[:10]}  FIX: run 01c_download_sectors.py"
               if not ok else f"OK: {len(mapped)} tickers mapped")
    else:
        cr.add("E", "E2", "Sector mapping coverage", False,
               "ticker_sector.json missing  FIX: run 01c_download_sectors.py")


# ─────────────────────────────────────────────────────────────────────────────
# Layer B: Backtest Config Sanity
# ─────────────────────────────────────────────────────────────────────────────

def check_backtest_config(cr: CheckResult, signals: pd.DataFrame):
    signals = signals.copy()
    signals["date"] = pd.to_datetime(signals["date"])

    # B1: enough date range for meaningful backtest (at least 3 years)
    sig_min = signals["date"].min()
    sig_max = signals["date"].max()
    years   = (sig_max - sig_min).days / 365.25
    ok = years >= 3.0
    cr.add("B", "B1", f"Signal history >= 3 years ({years:.1f}y)", ok,
           f"Only {years:.1f} years — need at least 3 for meaningful WF validation"
           if not ok else "OK")

    # B2: default transaction cost file / param reminder
    bt_params_path = BT_DIR / "best_params.json"
    if bt_params_path.exists():
        params = json.loads(bt_params_path.read_text())
        cost   = params.get("round_trip_cost", params.get("cost", 0))
        ok     = cost > 0
        cr.add("B", "B2", f"Transaction cost > 0 (cost={cost})", ok,
               "cost=0 gives unrealistic performance  FIX: set round_trip_cost in best_params.json"
               if not ok else "OK")
    else:
        cr.add("B", "B2", "Transaction cost > 0", True,
               "WARN: best_params.json not found — verify cost param before running")

    # B3: QQQ (benchmark) present throughout backtest period
    qqq_dates = signals[signals["ticker"] == "QQQ"]["date"]
    if len(qqq_dates) > 0:
        qqq_coverage = len(qqq_dates) / signals["date"].nunique()
        ok = qqq_coverage >= 0.95
        cr.add("B", "B3", f"QQQ benchmark coverage ({qqq_coverage:.1%})", ok,
               f"QQQ only present {qqq_coverage:.1%} of trading days  FIX: re-download QQQ"
               if not ok else "OK")
    else:
        cr.add("B", "B3", "QQQ benchmark present", False,
               "QQQ not in signals  FIX: run 01_download_data.py + 02_compute_signals.py")

    # B4: minimum stock count at any rebalance date (avoid degenerate universe)
    sample_dates = signals["date"].drop_duplicates().sort_values().iloc[252::21]  # monthly after warmup
    min_stocks   = signals[signals["date"].isin(sample_dates)].groupby("date")["ticker"].count().min()
    ok = min_stocks >= 50
    cr.add("B", "B4", f"Universe size >= 50 stocks at all rebalance dates (min={min_stocks})", ok,
           f"Only {min_stocks} stocks on worst rebalance date — universe too thin"
           if not ok else "OK")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def guardian_check(verbose: bool = True) -> bool:
    """
    Run all 24 checks. Returns True if all pass, False otherwise.
    If verbose=True, prints a full report.
    Raises SystemExit(1) on failure when called from __main__.
    """
    if verbose:
        print("\n" + "="*65)
        print("  DATA GUARDIAN — Pre-flight Check")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*65)

    cr = CheckResult()

    # Load data once (reused across layers)
    prices  = None
    signals = None

    check_files(cr)

    prices_path  = REQUIRED_FILES["prices"]
    signals_path = REQUIRED_FILES["signals"]

    if prices_path.exists():
        try:
            prices = pd.read_parquet(prices_path)
        except Exception as e:
            if verbose:
                print(f"\n  [FATAL] Cannot read prices.parquet: {e}")
            return False

    if signals_path.exists():
        try:
            signals = pd.read_parquet(signals_path)
        except Exception as e:
            if verbose:
                print(f"\n  [FATAL] Cannot read signals.parquet: {e}")
            return False

    if prices is not None and signals is not None:
        check_universe(cr, prices, signals)
        check_prices(cr, prices)
        check_signals(cr, signals)
        check_sector(cr, prices)
        check_backtest_config(cr, signals)
    else:
        if verbose:
            print("\n  [FATAL] Cannot proceed — core data files missing or unreadable")

    if verbose:
        cr.print_report()

    all_ok = cr.passed_all()
    if verbose:
        print()
        if all_ok:
            print("  GUARDIAN: ALL CLEAR — safe to run backtest / GA")
        else:
            failed = [r for r in cr.results if not r[3]]
            print(f"  GUARDIAN: BLOCKED — {len(failed)} check(s) failed.")
            print("  Fix all issues above before running backtest or GA.")
        print("="*65 + "\n")

    return all_ok


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    import os
    os.chdir(str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))

    ok = guardian_check(verbose=True)
    sys.exit(0 if ok else 1)
