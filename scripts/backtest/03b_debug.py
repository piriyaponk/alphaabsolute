# -*- coding: utf-8 -*-
"""Debug: compare v2 adtv_power vs v1 results."""
import sys, io, importlib.util
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

v2 = load_module(ROOT / "scripts/backtest/03b_backtest_v2.py", "v2")
v1 = load_module(ROOT / "scripts/backtest/03_backtest_engine.py", "v1")

signals = pd.read_parquet(ROOT / "data/backtest/signals.parquet")
dl      = v2.BacktestDataloader()
us      = v2.CostParams(slippage=0.0003, commission=0.0)

configs = [
    ("v2 softmax alpha=0.5",   dict(weighting="softmax",    softmax_alpha=0.5)),
    ("v2 softmax alpha=1.0",   dict(weighting="softmax",    softmax_alpha=1.0)),
    ("v2 softmax alpha=2.0",   dict(weighting="softmax",    softmax_alpha=2.0)),
    ("v2 adtv_power=1.5",      dict(weighting="adtv_power", adtv_power=1.5)),
]

print(f"{'Config':35s} | Excess | DD      | Sharpe | Calmar | Holds")
print("-" * 90)

for label, kw in configs:
    p = v2.StrategyParams(
        rs_threshold=80, vol_trend_min=0.95,
        bear_exposure=0.30, cap_pct=0.18, **kw
    )
    strat = v2.BacktestStrategy(dl, p)
    port  = v2.BacktestPortfolio(1_000_000, us)
    model = v2.BacktestModel(dl, strat, port, p)
    r     = model.run(verbose=False)
    print(f"{label:35s} | {r['excess']*100:+5.1f}% | {r['port_mdd']*100:6.1f}% | "
          f"{r['sharpe']:5.2f} | {r['calmar']:5.2f} | {r['avg_holdings']:5.0f}")

print("\n--- V1 engine (monthly approx, 0.15% round-trip) ---")
p1 = v1.BacktestParams(
    rs_threshold=80, vol_trend_min=0.95, vol_contract_max=1.10, base_tight_max=1.10,
    adtv_min_m=15, liquidity_power=1.5, cap_pct=0.18, bear_exposure=0.30,
    round_trip_cost=0.0015, benchmark="QQQ", start_date="2017-01-01", end_date="2026-08-20"
)
r1 = v1.run_backtest(p1, signals)
