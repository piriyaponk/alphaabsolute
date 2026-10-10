# -*- coding: utf-8 -*-
"""
Iterative Improvement Pipeline
================================
Phase 1 — Regime: lock R7 Codex 3-state vs R4 Death cross vs R0 no-gate
Phase 2 — Universe: expand S&P500 → S&P500 + Nasdaq-100
Phase 3 — Filters: test vol_contraction + base_tight additions
Phase 4 — Weighting: RS-only vs RS×vol_trend composite
Phase 5 — Final: combine best from each phase → SYSTEM v2.0

Rule: test one thing at a time, hold everything else fixed.
"""
import sys, io, ssl, time, importlib.util, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pandas as pd
import numpy as np
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT   = Path(__file__).resolve().parents[2]
BT_DIR = ROOT / "data/backtest"
os.chdir(ROOT)
ssl._create_default_https_context = ssl._create_unverified_context

sys.path.insert(0, str(ROOT / "scripts"))
from utils.data_engine import _YF_SESSION as SESSION

def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

v2 = load_module(ROOT / "scripts/backtest/03b_backtest_v2.py", "v2")

dl_sp500 = v2.BacktestDataloader()
us       = v2.CostParams()

# ── Regime signals from SPY ───────────────────────────────────
sig = pd.read_parquet(ROOT / "data/backtest/signals.parquet")
sig["date"] = pd.to_datetime(sig["date"])
spy = sig[sig["ticker"] == "SPY"].set_index("date").sort_index()
spy["ma_50"]          = spy["close"].rolling(50,  min_periods=40).mean()
spy["ma_200"]         = spy["close"].rolling(200, min_periods=180).mean()
spy["above_ma200"]    = spy["close"] > spy["ma_200"]
spy["death_cross"]    = spy["ma_50"] < spy["ma_200"]
spy["ma200_slope"]    = spy["ma_200"].diff(20)
spy["ma200_rising"]   = spy["ma200_slope"] > 0
spy["dist_from_ma200"] = (spy["close"] - spy["ma_200"]) / spy["ma_200"]
def _n_day_below(series, n):
    return (~series).astype(int).rolling(n).sum() >= n
spy["below_3days"] = _n_day_below(spy["above_ma200"], 3)
spy["below_5days"] = _n_day_below(spy["above_ma200"], 5)

BEAR_EXP = 0.30
SIDE_EXP = 0.75

def get_exposure(date: pd.Timestamp, method: str) -> float:
    prior = spy.index[spy.index <= date]
    if len(prior) == 0: return 1.0
    row = spy.loc[prior[-1]]
    if   method == "R0_no_regime":    return 1.0
    elif method == "R1_ma200_simple": return 1.0 if row["above_ma200"] else BEAR_EXP
    elif method == "R2_buffer_2pct":
        d = row["dist_from_ma200"]
        return 1.0 if d > 0.02 else (BEAR_EXP if d < -0.01 else SIDE_EXP)
    elif method == "R3_nday3":  return BEAR_EXP if row["below_3days"] else 1.0
    elif method == "R3_nday5":  return BEAR_EXP if row["below_5days"] else 1.0
    elif method == "R4_death_cross": return BEAR_EXP if row["death_cross"] else 1.0
    elif method == "R5_ma200_slope":
        if row["above_ma200"] and row["ma200_rising"]:  return 1.0
        elif not row["above_ma200"]:                    return BEAR_EXP
        else:                                           return SIDE_EXP
    elif method == "R6_dual_confirm":
        if row["above_ma200"] and not row["death_cross"]: return 1.0
        elif not row["above_ma200"]:                      return BEAR_EXP
        else:                                             return SIDE_EXP
    elif method == "R7_codex_3state":
        d = row["dist_from_ma200"]
        return 1.0 if d > -0.015 else 0.40
    elif method == "R8_composite":
        sc = int(row["above_ma200"]) + int(not row["death_cross"]) + int(row["ma200_rising"])
        return {3:1.0, 2:SIDE_EXP, 1:0.50}.get(sc, BEAR_EXP)
    return 1.0


class RegimeStrategy(v2.BacktestStrategy):
    def __init__(self, dl, params, regime_method):
        super().__init__(dl, params)
        self.regime_method = regime_method

    def screen(self, date):
        day_sig = self.dl.get_signals_at(date, self.params)
        if day_sig.empty:
            return pd.DataFrame(columns=["ticker","weight"])
        mask = (
            (day_sig["rs_pct"]          >= self.params.rs_threshold) &
            (day_sig["vol_trend"]       >= self.params.vol_trend_min) &
            (day_sig["vol_contraction"] <= self.params.vol_contract_max) &
            (day_sig["base_tight"]      <= self.params.base_tight_max) &
            (day_sig["adtv_63m"]        >= self.params.adtv_min_m) &
            (day_sig["close"]           >  day_sig["ma_200"])
        )
        sel = day_sig[mask].copy()
        if sel.empty:
            return pd.DataFrame(columns=["ticker","weight"])
        exposure = get_exposure(date, self.regime_method)
        scores   = sel["rs_pct"].values
        shifted  = 0.5 * (scores - scores.max())
        weights  = np.exp(shifted); weights /= weights.sum()
        weights  = np.minimum(weights, self.params.cap_pct)
        total    = weights.sum()
        if total > 0: weights = weights / total * exposure
        return pd.DataFrame({"ticker": sel["ticker"].values, "weight": weights,
                              "rs_pct": sel["rs_pct"].values, "adtv": sel["adtv_63m"].values})


WINDOWS = [
    ("WF1 COVID(2020-21)", "2020-01-01", "2021-12-31"),
    ("WF2 Bear(2022-23) ", "2022-01-01", "2023-12-31"),
    ("WF3 Bull(2024-26) ", "2024-01-01", "2026-08-20"),
    ("FULL(2017-2026)   ", "2017-01-01", "2026-08-20"),
]

def run_wf(dl, params_dict, regime_method="R0_no_regime", weight_formula="softmax_rs"):
    out = {}
    for wlabel, start, end in WINDOWS:
        p = v2.StrategyParams(**params_dict, start_date=start, end_date=end)
        if weight_formula == "rs_x_voltrend":
            strat = RegimeCompositeStrategy(dl, p, regime_method, "rs_x_voltrend")
        else:
            strat = RegimeStrategy(dl, p, regime_method)
        port  = v2.BacktestPortfolio(1_000_000, us)
        model = v2.BacktestModel(dl, strat, port, p)
        out[wlabel] = model.run(verbose=False)
    return out


def score(results):
    wf1  = results["WF1 COVID(2020-21)"]["excess"] * 100
    wf2  = results["WF2 Bear(2022-23) "]["excess"] * 100
    wf3  = results["WF3 Bull(2024-26) "]["excess"] * 100
    full = results["FULL(2017-2026)   "]["excess"] * 100
    return wf1*1 + wf2*3 + wf3*1 + full*2


def print_results(label, results):
    r = results["FULL(2017-2026)   "]
    wf2 = results["WF2 Bear(2022-23) "]["excess"] * 100
    wf3 = results["WF3 Bull(2024-26) "]["excess"] * 100
    print(f"  {label:42s} | WF2={wf2:+5.1f}% | WF3={wf3:+5.1f}% | "
          f"FULL={r['excess']*100:+5.1f}% | DD={r['port_mdd']*100:5.1f}% | "
          f"Sh={r['sharpe']:.2f} | Score={score(results):+.0f}")


# ── Composite weight strategy ─────────────────────────────────
class RegimeCompositeStrategy(RegimeStrategy):
    def __init__(self, dl, params, regime_method, weight_method):
        super().__init__(dl, params, regime_method)
        self.weight_method = weight_method

    def screen(self, date):
        base = super().screen(date)
        if base.empty or self.weight_method == "softmax_rs":
            return base

        # rs_x_voltrend: weight = softmax(RS_norm × vol_trend)
        day_sig  = self.dl.get_signals_at(date, self.params)
        day_sig  = day_sig.set_index("ticker")
        tickers  = base["ticker"].tolist()
        rs_vals  = np.array([day_sig.loc[t, "rs_pct"]   if t in day_sig.index else 50.0 for t in tickers])
        vt_vals  = np.array([day_sig.loc[t, "vol_trend"] if t in day_sig.index else 1.0 for t in tickers])

        composite = (rs_vals / 100) * np.clip(vt_vals, 0.5, 2.0)
        shifted   = 0.5 * (composite - composite.max())
        weights   = np.exp(shifted); weights /= weights.sum()
        weights   = np.minimum(weights, self.params.cap_pct)
        total_exp = base["weight"].sum()   # keep same regime exposure
        total     = weights.sum()
        if total > 0:
            weights = weights / total * total_exp

        base = base.copy()
        base["weight"] = weights
        return base


# ═══════════════════════════════════════════════════════════════
# PHASE 1 — REGIME COMPARISON (top 3 + current)
# ═══════════════════════════════════════════════════════════════
print("=" * 80)
print("PHASE 1 — REGIME METHOD  (criteria fixed: rs=80 vt=0.95)")
print("=" * 80)
print(f"  {'Config':42s} | WF2    | WF3    | FULL   | DD     | Sh   | Score")
print("-" * 90)

BASE = dict(
    rs_threshold=80, vol_trend_min=0.95,
    vol_contract_max=1.10, base_tight_max=1.10,
    adtv_min_m=15, cap_pct=0.18,
    bull_exposure=1.0, bear_exposure=1.0,
    weighting="softmax", softmax_alpha=0.5,
)

regime_results = {}
for label, method in [
    ("R0  No regime gate   [upper bound]", "R0_no_regime"),
    ("R1  Simple MA200     [current]    ", "R1_ma200_simple"),
    ("R4  Death cross 50<200            ", "R4_death_cross"),
    ("R7  Codex 3-state    [recommend]  ", "R7_codex_3state"),
    ("R8  Composite 3-signal            ", "R8_composite"),
]:
    r = run_wf(dl_sp500, BASE, regime_method=method)
    regime_results[label] = r
    print_results(label, r)

best_regime = max(regime_results, key=lambda k: score(regime_results[k]))
print(f"\n  BEST REGIME: {best_regime.strip()}")
best_regime_method = {
    "R0  No regime gate   [upper bound]": "R0_no_regime",
    "R1  Simple MA200     [current]    ": "R1_ma200_simple",
    "R4  Death cross 50<200            ": "R4_death_cross",
    "R7  Codex 3-state    [recommend]  ": "R7_codex_3state",
    "R8  Composite 3-signal            ": "R8_composite",
}[best_regime]


# ═══════════════════════════════════════════════════════════════
# PHASE 2 — UNIVERSE EXPANSION: Download Nasdaq-100 extras
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 80)
print("PHASE 2 — UNIVERSE EXPANSION (S&P500 + Nasdaq-100 additions)")
print("=" * 80)

# Nasdaq-100 pure additions (not already in S&P500 universe)
# These are NDX100 members that are NOT typically in S&P500
NDX_EXTRA = [
    "ABNB","ADSK","AEP","AMAT","AMD","AMGN","AMZN","ANSS","APP",
    "ARM","ASML","AVGO","AZN","BIIB","BKNG","CDNS","CEG","CMCSA",
    "COST","CPRT","CRWD","CSCO","CSX","DXCM","EA","ENPH","EXC",
    "FANG","FAST","FTNT","GEHC","GFS","GILD","GOOG","GOOGL","HON",
    "IDXX","ILMN","INTC","INTU","ISRG","KDP","KHC","KLAC","LRCX",
    "LULU","MAR","MCHP","MDLZ","META","MELI","MNST","MRNA","MRVL",
    "MSFT","MU","NFLX","NXPI","ODFL","ON","ORLY","PANW","PAYX",
    "PCAR","PDD","PEP","PYPL","QCOM","REGN","ROP","ROST","SBUX",
    "SMCI","SNPS","TEAM","TMUS","TSLA","TTD","TTWO","TXN","VRSK",
    "VRTX","WBD","WDAY","XEL","ZS"
]

# Check which are already in our signals
existing_tickers = set(dl_sp500.signals["ticker"].unique())
new_tickers = [t for t in NDX_EXTRA if t not in existing_tickers]
print(f"  NDX extra tickers: {len(NDX_EXTRA)}  new (not in S&P500 data): {len(new_tickers)}")
print(f"  Already have: {len(NDX_EXTRA)-len(new_tickers)} NDX tickers")

# Download prices for new tickers
CACHE_FILE = BT_DIR / "ndx_extra_prices.parquet"

def fetch_ohlcv_yahoo(ticker: str, start="2014-01-01", end="2026-08-27"):
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1d&range=12y")
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    try:
        r = SESSION.get(url, headers=headers, timeout=15, verify=False)
        if r.status_code != 200:
            return None
        data = r.json()
        res  = data["chart"]["result"][0]
        ts   = pd.to_datetime(res["timestamp"], unit="s").normalize()
        ohlcv = res["indicators"]["quote"][0]
        adj   = res["indicators"].get("adjclose", [{}])[0]
        df = pd.DataFrame({
            "date":   ts,
            "open":   ohlcv.get("open"),
            "high":   ohlcv.get("high"),
            "low":    ohlcv.get("low"),
            "close":  adj.get("adjclose") or ohlcv.get("close"),
            "volume": ohlcv.get("volume"),
            "ticker": ticker,
        })
        df = df.dropna(subset=["close"])
        return df
    except Exception:
        return None

if new_tickers:
    print(f"  Downloading {len(new_tickers)} new tickers...")
    frames = []
    done = 0
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(fetch_ohlcv_yahoo, t): t for t in new_tickers}
        for fut in as_completed(futures):
            df = fut.result()
            if df is not None and len(df) > 100:
                frames.append(df)
            done += 1
            if done % 20 == 0:
                sys.stdout.write(f"\r    {done}/{len(new_tickers)} downloaded")
                sys.stdout.flush()
            time.sleep(0.05)
    print()
    if frames:
        ndx_prices = pd.concat(frames, ignore_index=True)
        ndx_prices.to_parquet(CACHE_FILE, index=False)
        print(f"  Saved {len(frames)} new tickers, {len(ndx_prices):,} rows")
    else:
        print("  WARNING: no new tickers downloaded")
        ndx_prices = pd.DataFrame()
else:
    if CACHE_FILE.exists():
        ndx_prices = pd.read_parquet(CACHE_FILE)
        print(f"  Loaded cached NDX extras: {len(ndx_prices):,} rows")
    else:
        ndx_prices = pd.DataFrame()
        print("  No new tickers needed")

# Compute signals for new tickers and merge
if len(ndx_prices) > 0:
    print("  Computing signals for new tickers...")

    compute = load_module(ROOT / "scripts/backtest/02_compute_signals.py", "sig2")

    # compute_signals needs SPY in the prices df — add it
    spy_prices = pd.read_parquet(BT_DIR / "prices.parquet")
    spy_prices = spy_prices[spy_prices["ticker"] == "SPY"][["date","open","high","low","close","volume","ticker"]]
    ndx_with_spy = pd.concat([ndx_prices, spy_prices], ignore_index=True)
    ndx_with_spy["date"] = pd.to_datetime(ndx_with_spy["date"])

    import builtins
    _orig = builtins.print
    builtins.print = lambda *a, **k: None
    try:
        new_sigs = compute.compute_signals(ndx_with_spy)
    finally:
        builtins.print = _orig
    # Drop SPY from new sigs (already in existing)
    new_sigs = new_sigs[new_sigs["ticker"] != "SPY"]
    print(f"  Computed signals: {len(new_sigs):,} rows for {new_sigs['ticker'].nunique()} tickers")

    # Merge with existing signals — normalise date column to datetime first
    existing_sigs = pd.read_parquet(BT_DIR / "signals.parquet")
    existing_sigs["date"] = pd.to_datetime(existing_sigs["date"])
    new_sigs["date"] = pd.to_datetime(new_sigs["date"])
    combined_sigs = pd.concat([existing_sigs, new_sigs], ignore_index=True)
    combined_sigs = combined_sigs.drop_duplicates(subset=["date","ticker"])
    combined_sigs = combined_sigs.sort_values(["ticker","date"])

    EXPANDED_FILE = BT_DIR / "signals_expanded.parquet"
    combined_sigs.to_parquet(EXPANDED_FILE, index=False)
    print(f"  Saved signals_expanded.parquet: {len(combined_sigs):,} rows, "
          f"{combined_sigs['ticker'].nunique()} tickers")

    dl_expanded = v2.BacktestDataloader(signal_file=EXPANDED_FILE)
    HAS_EXPANDED = True
else:
    EXPANDED_FILE = BT_DIR / "signals_expanded.parquet"
    if EXPANDED_FILE.exists():
        dl_expanded = v2.BacktestDataloader(signal_file=EXPANDED_FILE)
        n = dl_expanded.signals["ticker"].nunique()
        print(f"  Loaded existing signals_expanded.parquet: {n} tickers")
        HAS_EXPANDED = True
    else:
        dl_expanded = dl_sp500
        HAS_EXPANDED = False

# Compare S&P500-only vs S&P500+NDX100
print()
print(f"  {'Config':42s} | WF2    | WF3    | FULL   | DD     | Sh   | Score")
print("-" * 90)

r_sp500 = run_wf(dl_sp500,    BASE, regime_method=best_regime_method)
print_results(f"S&P500 only  ({dl_sp500.signals['ticker'].nunique()} tickers)", r_sp500)

if HAS_EXPANDED:
    r_exp = run_wf(dl_expanded, BASE, regime_method=best_regime_method)
    print_results(f"S&P500+NDX100  ({dl_expanded.signals['ticker'].nunique()} tickers)", r_exp)
    best_dl = dl_expanded if score(r_exp) > score(r_sp500) else dl_sp500
    best_dl_name = "S&P500+NDX100" if score(r_exp) > score(r_sp500) else "S&P500 only"
else:
    best_dl = dl_sp500
    best_dl_name = "S&P500 only"

print(f"\n  BEST UNIVERSE: {best_dl_name}")


# ═══════════════════════════════════════════════════════════════
# PHASE 3 — FILTER COMBINATIONS
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 80)
print(f"PHASE 3 — FILTER COMBINATIONS  (regime={best_regime_method}, universe={best_dl_name})")
print("=" * 80)
print(f"  {'Config':42s} | WF2    | WF3    | FULL   | DD     | Sh   | Score")
print("-" * 90)

filter_configs = [
    ("F1 rs=80 vt=0.95 [current best]",
     dict(rs_threshold=80,  vol_trend_min=0.95, vol_contract_max=1.10, base_tight_max=1.10)),
    ("F2 + vol_contract<1.0 (Codex)",
     dict(rs_threshold=80,  vol_trend_min=0.95, vol_contract_max=1.00, base_tight_max=1.10)),
    ("F3 + base_tight<0.95 (Codex)",
     dict(rs_threshold=80,  vol_trend_min=0.95, vol_contract_max=1.10, base_tight_max=0.95)),
    ("F4 + vol_contract + base_tight",
     dict(rs_threshold=80,  vol_trend_min=0.95, vol_contract_max=1.00, base_tight_max=0.95)),
    ("F5 rs=75 vt=0.95",
     dict(rs_threshold=75,  vol_trend_min=0.95, vol_contract_max=1.10, base_tight_max=1.10)),
    ("F6 rs=75 + vol_contract<1.0",
     dict(rs_threshold=75,  vol_trend_min=0.95, vol_contract_max=1.00, base_tight_max=1.10)),
    ("F7 rs=75 + base_tight<0.95",
     dict(rs_threshold=75,  vol_trend_min=0.95, vol_contract_max=1.10, base_tight_max=0.95)),
    ("F8 rs=85 vt=0.95",
     dict(rs_threshold=85,  vol_trend_min=0.95, vol_contract_max=1.10, base_tight_max=1.10)),
    ("F9 rs=80 vt=1.0 (stricter vol)",
     dict(rs_threshold=80,  vol_trend_min=1.00, vol_contract_max=1.10, base_tight_max=1.10)),
]

filter_results = {}
for label, fkw in filter_configs:
    p = {**BASE, **fkw}
    r = run_wf(best_dl, p, regime_method=best_regime_method)
    filter_results[label] = r
    print_results(label, r)

best_filter_label = max(filter_results, key=lambda k: score(filter_results[k]))
best_filter_params = {
    "F1 rs=80 vt=0.95 [current best]":
        dict(rs_threshold=80,  vol_trend_min=0.95, vol_contract_max=1.10, base_tight_max=1.10),
    "F2 + vol_contract<1.0 (Codex)":
        dict(rs_threshold=80,  vol_trend_min=0.95, vol_contract_max=1.00, base_tight_max=1.10),
    "F3 + base_tight<0.95 (Codex)":
        dict(rs_threshold=80,  vol_trend_min=0.95, vol_contract_max=1.10, base_tight_max=0.95),
    "F4 + vol_contract + base_tight":
        dict(rs_threshold=80,  vol_trend_min=0.95, vol_contract_max=1.00, base_tight_max=0.95),
    "F5 rs=75 vt=0.95":
        dict(rs_threshold=75,  vol_trend_min=0.95, vol_contract_max=1.10, base_tight_max=1.10),
    "F6 rs=75 + vol_contract<1.0":
        dict(rs_threshold=75,  vol_trend_min=0.95, vol_contract_max=1.00, base_tight_max=1.10),
    "F7 rs=75 + base_tight<0.95":
        dict(rs_threshold=75,  vol_trend_min=0.95, vol_contract_max=1.10, base_tight_max=0.95),
    "F8 rs=85 vt=0.95":
        dict(rs_threshold=85,  vol_trend_min=0.95, vol_contract_max=1.10, base_tight_max=1.10),
    "F9 rs=80 vt=1.0 (stricter vol)":
        dict(rs_threshold=80,  vol_trend_min=1.00, vol_contract_max=1.10, base_tight_max=1.10),
}[best_filter_label]
print(f"\n  BEST FILTER: {best_filter_label.strip()}")


# ═══════════════════════════════════════════════════════════════
# PHASE 4 — WEIGHTING
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 80)
print(f"PHASE 4 — WEIGHTING  (regime={best_regime_method}, filters=best)")
print("=" * 80)
print(f"  {'Config':42s} | WF2    | WF3    | FULL   | DD     | Sh   | Score")
print("-" * 90)

weight_configs = [
    ("W1 softmax(RS) alpha=0.5 [current]", "softmax_rs"),
    ("W2 softmax(RS x vol_trend)         ", "rs_x_voltrend"),
]

weight_results = {}
final_params = {**BASE, **best_filter_params}

for wlabel, wmethod in weight_configs:
    r = run_wf(best_dl, final_params, regime_method=best_regime_method, weight_formula=wmethod)
    weight_results[wlabel] = r
    print_results(wlabel, r)

best_weight_label = max(weight_results, key=lambda k: score(weight_results[k]))
print(f"\n  BEST WEIGHT: {best_weight_label.strip()}")


# ═══════════════════════════════════════════════════════════════
# PHASE 5 — FINAL SYSTEM v2.0
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 80)
print("PHASE 5 — SYSTEM v2.0  (all best components combined)")
print("=" * 80)

best_weight_method = "softmax_rs" if "current" in best_weight_label else "rs_x_voltrend"

r_v1  = run_wf(dl_sp500, {**BASE,
               "rs_threshold":80, "vol_trend_min":0.95,
               "vol_contract_max":1.10, "base_tight_max":1.10},
               regime_method="R1_ma200_simple")
r_v2  = run_wf(best_dl, final_params,
               regime_method=best_regime_method,
               weight_formula=best_weight_method)

print(f"  {'Config':42s} | WF2    | WF3    | FULL   | DD     | Sh   | Score")
print("-" * 90)
print_results("SYSTEM v1.0  (original baseline)", r_v1)
print_results("SYSTEM v2.0  (all improvements)",  r_v2)

# Save best params
v2_params = {
    "regime":          best_regime_method,
    "universe":        best_dl_name,
    "rs_threshold":    final_params.get("rs_threshold", 80),
    "vol_trend_min":   final_params.get("vol_trend_min", 0.95),
    "vol_contract_max":final_params.get("vol_contract_max", 1.10),
    "base_tight_max":  final_params.get("base_tight_max", 1.10),
    "adtv_min_m":      15,
    "cap_pct":         0.18,
    "weighting":       best_weight_method,
    "softmax_alpha":   0.5,
    "cost_bps_roundtrip": 14,
    "wf2_excess":      round(r_v2["WF2 Bear(2022-23) "]["excess"]*100, 2),
    "full_excess":     round(r_v2["FULL(2017-2026)   "]["excess"]*100, 2),
    "full_sharpe":     round(r_v2["FULL(2017-2026)   "]["sharpe"], 2),
}
import json
(BT_DIR / "best_params_v2.json").write_text(json.dumps(v2_params, indent=2))
print(f"\n  Saved: data/backtest/best_params_v2.json")
print(f"  {json.dumps(v2_params, indent=4)}")
