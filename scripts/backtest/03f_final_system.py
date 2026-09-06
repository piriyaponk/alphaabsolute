# -*- coding: utf-8 -*-
"""Final SYSTEM v1.0 results with corrected 14 bps round-trip cost."""
import sys, io, importlib.util
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

v2 = load_module(ROOT / "scripts/backtest/03b_backtest_v2.py", "v2")
dl = v2.BacktestDataloader()
us = v2.CostParams()   # 7 bps one-way = 14 bps round-trip (corrected)
print(f"Cost model: {us.slippage*10000:.0f} bps one-way slippage, "
      f"{us.commission*10000:.0f} bps commission = "
      f"{us.round_trip*10000:.0f} bps round-trip")

WINDOWS = [
    ("WF1 COVID+recovery", "2020-01-01", "2021-12-31"),
    ("WF2 Rate-hike bear", "2022-01-01", "2023-12-31"),
    ("WF3 AI bull       ", "2024-01-01", "2026-08-20"),
    ("FULL 2017-2026    ", "2017-01-01", "2026-08-20"),
]

print()
print(f"{'Window':22s} | Excess | DD      | Sharpe | Calmar | Holds")
print("-" * 75)
for wlabel, start, end in WINDOWS:
    p = v2.StrategyParams(
        rs_threshold=80, vol_trend_min=0.95,
        weighting="softmax", softmax_alpha=0.5,
        bear_exposure=0.30, cap_pct=0.18,
        start_date=start, end_date=end
    )
    strat = v2.BacktestStrategy(dl, p)
    port  = v2.BacktestPortfolio(1_000_000, us)
    model = v2.BacktestModel(dl, strat, port, p)
    r     = model.run(verbose=False)
    print(f"{wlabel:22s} | {r['excess']*100:+5.1f}% | {r['port_mdd']*100:6.1f}% | "
          f"{r['sharpe']:5.2f} | {r['calmar']:5.2f} | {r['avg_holdings']:5.0f}")
