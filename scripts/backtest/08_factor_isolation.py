# -*- coding: utf-8 -*-
"""
08_factor_isolation.py — Isolated factor backtest.

For each new v3 factor, run:
  BASE (SYSTEM v1.0) vs BASE + factor

Reports:
  - Full 2017-2026 excess vs QQQ, DD, Sharpe, Calmar
  - WF0 2017-2019 bull  WF2 2022-2023 bear  WF3 2024-2026 AI bull
  - Additionality: does it improve BASE?

Base = rs>=80, vol_trend>=0.95, price>MA200, adtv>=15, bear_exposure=0.30
"""
import sys, io, os, importlib.util
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import pandas as pd
import numpy as np

ROOT   = Path(__file__).resolve().parents[2]
BT_DIR = ROOT / "data" / "backtest"
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "backtest"))


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


v2   = _load(ROOT / "scripts/backtest/03b_backtest_v2.py", "v2")
COST = v2.CostParams()

print("Loading signals...")
loader = v2.BacktestDataloader()
print(f"  {len(loader.signals):,} rows  |  {loader.signals['ticker'].nunique()} tickers")

# ─── Base params (SYSTEM v1.0) ───────────────────────────────────────────────
BASE = dict(
    rs_threshold      = 80.0,
    vol_trend_min     = 0.95,
    vol_contract_max  = 9.9,    # off — vol_trend is our filter
    base_tight_max    = 9.9,    # off
    adtv_min_m        = 15.0,
    bear_exposure     = 0.30,
    cap_pct           = 0.18,
    softmax_alpha     = 1.0,
)

WINDOWS = {
    "FULL":  ("2017-01-01", "2026-08-20"),
    "WF0":   ("2017-01-01", "2021-12-31"),
    "WF2":   ("2022-01-01", "2023-12-31"),
    "WF3":   ("2024-01-01", "2026-08-20"),
}


def run_window(extra_params: dict, start: str, end: str) -> dict:
    params    = v2.StrategyParams(**{**BASE, **extra_params}, start_date=start, end_date=end)
    strategy  = v2.BacktestStrategy(loader, params)
    portfolio = v2.BacktestPortfolio(costs=COST)
    model     = v2.BacktestModel(loader, strategy, portfolio, params)
    return model.run(verbose=False)


def evaluate(extra_params: dict, label: str) -> dict:
    row = {"config": label}
    for win_name, (s, e) in WINDOWS.items():
        r = run_window(extra_params, s, e)
        row[f"{win_name}_excess"] = round(r.get("excess", 0)       * 100, 1)
        row[f"{win_name}_dd"]     = round(r.get("port_mdd", 0)     * 100, 1)
        row[f"{win_name}_sharpe"] = round(r.get("sharpe", 0),              2)
        row[f"{win_name}_holds"]  = round(r.get("avg_holdings", 0),        1)
    return row


# ─── Factor definitions ──────────────────────────────────────────────────────
# Each entry: (label, extra_params_dict)
# Direction: lower = better for resilience (stock fell less than sector)
#            higher = better for ad_slope, pocket_pivot, spring_score
#            higher = better for pct_from_52w_low (far from low = healthier base)

FACTOR_TESTS = [
    # Baseline
    ("BASE (v1.0)",          {}),

    # Grid best config for reference
    ("Grid_best (rs=80,52wH>-20,vt=1.0,adtv=25)", dict(
        rs_threshold=80, vol_trend_min=1.0, adtv_min_m=25,
        pct_from_52w_high_min=-20,
    )),

    # --- Individual new factors vs BASE ---

    # 1. sector_resilience_3m: stock fell less than its own sector
    #    ratio < 1.0 = leader vs sector; lower = better
    #    test threshold: < 0.90 (stock held up 10%+ better than sector)
    ("BASE + sector_res<0.90",  {"sector_resilience_3m_max": 0.90}),
    ("BASE + sector_res<0.95",  {"sector_resilience_3m_max": 0.95}),
    ("BASE + sector_res<1.00",  {"sector_resilience_3m_max": 1.00}),

    # 2. pct_from_52w_low: stock far from 52W low = established uptrend
    #    higher = healthier; test: must be at least X% above 52W low
    ("BASE + 52wL>50%",  {"pct_from_52w_low_min": 50}),
    ("BASE + 52wL>30%",  {"pct_from_52w_low_min": 30}),
    ("BASE + 52wL>20%",  {"pct_from_52w_low_min": 20}),

    # 3. ad_slope: A/D line rising = accumulation (Wyckoff)
    #    higher = better; test: positive slope
    ("BASE + ad_slope>0",    {"ad_slope_min": 0.0}),
    ("BASE + ad_slope>0.1",  {"ad_slope_min": 0.1}),

    # 4. pocket_pivot: institutional footprint (Minervini)
    #    ratio > 1.0 = today's up-volume > max down-day volume last 10d
    #    sparse signal — use rolling 5d max > threshold
    ("BASE + pocket_pivot>0.5",  {"pocket_pivot_min": 0.5}),
    ("BASE + pocket_pivot>1.0",  {"pocket_pivot_min": 1.0}),

    # 5. spring_score: Wyckoff spring (false breakdown + recovery)
    #    > 0 = spring event in last 5 days; very sparse
    ("BASE + spring>0",   {"spring_score_min": 0.001}),
    ("BASE + spring>0.5", {"spring_score_min": 0.5}),

    # --- Combinations of best candidates ---
    ("BASE + sector_res<1.0 + 52wL>30%",
     {"sector_resilience_3m_max": 1.0, "pct_from_52w_low_min": 30}),
    ("BASE + sector_res<1.0 + ad_slope>0",
     {"sector_resilience_3m_max": 1.0, "ad_slope_min": 0.0}),
]


# ─── Patch BacktestStrategy.screen() to support new v3 filter params ────────
# The original screen() already handles pct_from_52w_high_min + sharpe_60d_min.
# We extend it to also apply: sector_resilience_3m_max, pct_from_52w_low_min,
# ad_slope_min, pocket_pivot_min, spring_score_min.

_orig_screen = v2.BacktestStrategy.screen

def _patched_screen(self, date: pd.Timestamp) -> pd.DataFrame:
    result = _orig_screen(self, date)
    if result.empty:
        return result
    p = self.params

    # Get full signal row for the tickers that passed original screen
    day_sig = self.dl.get_signals_at(date, self.params)
    day_sig = day_sig[day_sig["ticker"].isin(result["ticker"])].copy()

    mask = pd.Series(True, index=day_sig.index)

    sector_res_max = getattr(p, "sector_resilience_3m_max", 99.0)
    if sector_res_max < 99.0 and "sector_resilience_3m" in day_sig.columns:
        mask &= day_sig["sector_resilience_3m"].fillna(99) <= sector_res_max

    pct_52wl_min = getattr(p, "pct_from_52w_low_min", -99.0)
    if pct_52wl_min > -99 and "pct_from_52w_low" in day_sig.columns:
        mask &= day_sig["pct_from_52w_low"].fillna(-99) >= pct_52wl_min

    ad_slope_min = getattr(p, "ad_slope_min", -99.0)
    if ad_slope_min > -99 and "ad_slope" in day_sig.columns:
        mask &= day_sig["ad_slope"].fillna(-99) >= ad_slope_min

    pocket_min = getattr(p, "pocket_pivot_min", -99.0)
    if pocket_min > -99 and "pocket_pivot" in day_sig.columns:
        mask &= day_sig["pocket_pivot"].fillna(-99) >= pocket_min

    spring_min = getattr(p, "spring_score_min", -99.0)
    if spring_min > -99 and "spring_score" in day_sig.columns:
        mask &= day_sig["spring_score"].fillna(-99) >= spring_min

    allowed = set(day_sig[mask]["ticker"])
    return result[result["ticker"].isin(allowed)].copy()

v2.BacktestStrategy.screen = _patched_screen

# Allow extra kwargs on StrategyParams (v3 factor thresholds not in dataclass)
import dataclasses as _dc
_orig_dataclass_init = v2.StrategyParams.__init__
def _flexible_init(self, **kwargs):
    field_names = {f.name for f in _dc.fields(v2.StrategyParams)}
    clean = {k: val for k, val in kwargs.items() if k in field_names}
    extra = {k: val for k, val in kwargs.items() if k not in field_names}
    _orig_dataclass_init(self, **clean)
    for k, val in extra.items():
        setattr(self, k, val)

v2.StrategyParams.__init__ = _flexible_init


# ─── Run all tests ────────────────────────────────────────────────────────────

print(f"\n{'='*100}")
print(f"  FACTOR ISOLATION TEST  — BASE = rs>=80, vol_trend>=0.95, adtv>=15, bear=30%")
print(f"{'='*100}")

results = []
for i, (label, extra) in enumerate(FACTOR_TESTS):
    print(f"[{i+1:02d}/{len(FACTOR_TESTS)}] {label} ...", end="", flush=True)
    try:
        row = evaluate(extra, label)
        results.append(row)
        print(f"  FULL={row['FULL_excess']:+.1f}%  WF0={row['WF0_excess']:+.1f}%  "
              f"WF2={row['WF2_excess']:+.1f}%  WF3={row['WF3_excess']:+.1f}%  "
              f"DD={row['FULL_dd']:.1f}%  Sh={row['FULL_sharpe']:.2f}  H={row['FULL_holds']:.1f}")
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback; traceback.print_exc()


# ─── Summary table ────────────────────────────────────────────────────────────

df = pd.DataFrame(results)

print(f"\n{'='*100}")
print("  RESULTS SUMMARY")
print(f"{'='*100}")
print(f"{'Config':<42} {'FULL':>6} {'WF0':>6} {'WF2':>6} {'WF3':>6} {'DD':>7} {'Sharpe':>7} {'Calmar':>7} {'Holds':>6}")
print("-"*100)

base_row = df[df["config"] == "BASE (v1.0)"].iloc[0]
for _, row in df.iterrows():
    ex_delta = row["FULL_excess"] - base_row["FULL_excess"]
    flag     = " <--" if (ex_delta > 1 and row["WF2_excess"] > base_row["WF2_excess"]) \
               else (" (worse)" if ex_delta < -1 else "")
    print(f"{row['config']:<42} "
          f"{row['FULL_excess']:>+6.1f}% "
          f"{row['WF0_excess']:>+6.1f}% "
          f"{row['WF2_excess']:>+6.1f}% "
          f"{row['WF3_excess']:>+6.1f}% "
          f"{row['FULL_dd']:>7.1f}% "
          f"{row['FULL_sharpe']:>7.2f} "
          f"{'n/a':>7} "
          f"{row['FULL_holds']:>6.1f}"
          f"{flag}")

print("-"*100)
print("\nFlag <-- = beats BASE on FULL and WF2 (bear survival)")

# Save
out = ROOT / "data" / "backtest" / "factor_isolation_results.csv"
df.to_csv(out, index=False)
print(f"\nSaved: {out}")
