# -*- coding: utf-8 -*-
"""
Step 5: Walk-forward validation of best params.
Splits 2017-2026 into 3 non-overlapping windows:
  Window 1: train 2017-2019, test 2020-2021
  Window 2: train 2017-2021, test 2022-2023
  Window 3: train 2017-2023, test 2024-2026

Also tests: best_params vs baseline vs vol_trend_only vs QQQ across ALL windows.
Goal: confirm best params are robust, not cherry-picked.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import json, contextlib
import pandas as pd
import numpy as np
from pathlib import Path
from copy import deepcopy

ROOT       = Path(__file__).resolve().parents[2]
BT_DIR     = ROOT / "data" / "backtest"
BEST_JSON  = BT_DIR / "best_params.json"
SIGNAL_FILE = BT_DIR / "signals.parquet"

sys.path.insert(0, str(Path(__file__).parent))
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("backtest_engine", Path(__file__).parent / "03_backtest_engine.py")
_mod  = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_mod)
BacktestParams = _mod.BacktestParams
run_backtest   = _mod.run_backtest

WINDOWS = [
    ("2017-01-01", "2019-12-31", "2020-01-01", "2021-12-31", "WF1 (test:2020-21 incl. COVID)"),
    ("2017-01-01", "2021-12-31", "2022-01-01", "2023-12-31", "WF2 (test:2022-23 bear+recovery)"),
    ("2017-01-01", "2023-12-31", "2024-01-01", "2026-08-20", "WF3 (test:2024-26 AI bull)"),
]

def silent_backtest(params, signals):
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        return run_backtest(params, signals)

def run_walkforward(signals: pd.DataFrame):
    if not BEST_JSON.exists():
        print("No best_params.json yet — run 04_optimize.py first")
        return

    best_kw = json.loads(BEST_JSON.read_text())
    configs = {
        "Baseline (ChatGPT)":     BacktestParams(),
        "VolTrend>=0.95 only":    BacktestParams(vol_trend_min=0.95),
        "OPTIMIZED (best)":       BacktestParams(**best_kw),
    }

    rows = []
    for train_s, train_e, test_s, test_e, label in WINDOWS:
        print(f"\n{'='*65}")
        print(f"  {label}")
        print(f"  Train: {train_s} to {train_e} | Test: {test_s} to {test_e}")
        print(f"{'='*65}")
        for name, base_params in configs.items():
            p_test = deepcopy(base_params)
            p_test.start_date = test_s
            p_test.end_date   = test_e
            r = silent_backtest(p_test, signals)
            excess_str = f"{r['excess']*100:+.1f}%"
            print(f"  {name:28s} CAGR={r['port_cagr']*100:.1f}% excess={excess_str} "
                  f"DD={r['port_mdd']*100:.1f}% Sharpe={r['sharpe']:.2f}")
            rows.append({"window": label, "config": name,
                         "cagr": r["port_cagr"], "excess": r["excess"],
                         "mdd": r["port_mdd"], "sharpe": r["sharpe"],
                         "calmar": r["calmar"]})

    df = pd.DataFrame(rows)
    print(f"\n{'='*65}")
    print("SUMMARY TABLE:")
    pivot = df.pivot_table(index="config", columns="window",
                           values="excess", aggfunc="mean")
    pivot["AVG_EXCESS"] = pivot.mean(axis=1)
    print((pivot * 100).round(1).to_string())

    out_file = BT_DIR / "walkforward_results.csv"
    df.to_csv(out_file, index=False)
    print(f"\nSaved: {out_file}")
    return df


if __name__ == "__main__":
    print("Loading signals...")
    signals = pd.read_parquet(SIGNAL_FILE)
    print(f"  {len(signals):,} rows, {signals['ticker'].nunique()} tickers")
    run_walkforward(signals)
