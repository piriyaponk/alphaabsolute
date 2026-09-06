# -*- coding: utf-8 -*-
"""
Walk-forward comparison: Softmax vs ADTV^power on v2 engine.
Tests all 3 WF windows + full period on SYSTEM v1.0 criteria.

Proves (data-first) which weighting method is better.
"""
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

dl = v2.BacktestDataloader()
us = v2.CostParams(slippage=0.0003, commission=0.0)   # US retail: $0 commission

# Walk-forward windows (same as v1 convention)
WINDOWS = [
    ("WF1 COVID+recovery", "2020-01-01", "2021-12-31"),
    ("WF2 Rate-hike bear ", "2022-01-01", "2023-12-31"),
    ("WF3 AI bull        ", "2024-01-01", "2026-08-20"),
    ("FULL 2017-2026     ", "2017-01-01", "2026-08-20"),
]

# Weighting configs to compare (criteria FIXED at SYSTEM v1.0)
WEIGHTS = [
    ("Softmax  alpha=0.5", dict(weighting="softmax",    softmax_alpha=0.5)),
    ("Softmax  alpha=1.0", dict(weighting="softmax",    softmax_alpha=1.0)),
    ("Softmax  alpha=1.5", dict(weighting="softmax",    softmax_alpha=1.5)),
    ("ADTV^1.0           ", dict(weighting="adtv_power", adtv_power=1.0)),
    ("ADTV^1.5           ", dict(weighting="adtv_power", adtv_power=1.5)),
    ("ADTV^2.0           ", dict(weighting="adtv_power", adtv_power=2.0)),
    ("Equal weight       ", dict(weighting="equal")),
]

print("SYSTEM v1.0 criteria: rs>=80, vol_trend>=0.95, price>MA200, ADTV>=15M, bear=30%")
print("Cost: 0.006% round-trip (US retail)")
print()

# Results matrix: {weight_label: {window_label: result_dict}}
results = {}
for wlabel, wkw in WEIGHTS:
    results[wlabel] = {}
    for win_label, start, end in WINDOWS:
        p = v2.StrategyParams(
            rs_threshold=80, vol_trend_min=0.95,
            bear_exposure=0.30, cap_pct=0.18,
            start_date=start, end_date=end,
            **wkw
        )
        strat = v2.BacktestStrategy(dl, p)
        port  = v2.BacktestPortfolio(1_000_000, us)
        model = v2.BacktestModel(dl, strat, port, p)
        r     = model.run(verbose=False)
        results[wlabel][win_label] = r

# ── Print: Excess return table ──────────────────────────────
print("=== EXCESS RETURN vs QQQ ===")
header = f"{'Weighting':22s}"
for win_label, _, _ in WINDOWS:
    header += f" | {win_label.strip():16s}"
print(header)
print("-" * (22 + 20 * len(WINDOWS)))
for wlabel, wins in results.items():
    row = f"{wlabel:22s}"
    for win_label, _, _ in WINDOWS:
        r = wins[win_label]
        row += f" | {r['excess']*100:+7.1f}%        "
    print(row)

print()
print("=== MAX DRAWDOWN ===")
header = f"{'Weighting':22s}"
for win_label, _, _ in WINDOWS:
    header += f" | {win_label.strip():16s}"
print(header)
print("-" * (22 + 20 * len(WINDOWS)))
for wlabel, wins in results.items():
    row = f"{wlabel:22s}"
    for win_label, _, _ in WINDOWS:
        r = wins[win_label]
        row += f" | {r['port_mdd']*100:7.1f}%        "
    print(row)

print()
print("=== SHARPE RATIO ===")
header = f"{'Weighting':22s}"
for win_label, _, _ in WINDOWS:
    header += f" | {win_label.strip():16s}"
print(header)
print("-" * (22 + 20 * len(WINDOWS)))
for wlabel, wins in results.items():
    row = f"{wlabel:22s}"
    for win_label, _, _ in WINDOWS:
        r = wins[win_label]
        row += f" | {r['sharpe']:7.2f}         "
    print(row)

print()
print("=== AVG HOLDINGS ===")
header = f"{'Weighting':22s}"
for win_label, _, _ in WINDOWS:
    header += f" | {win_label.strip():16s}"
print(header)
print("-" * (22 + 20 * len(WINDOWS)))
for wlabel, wins in results.items():
    row = f"{wlabel:22s}"
    for win_label, _, _ in WINDOWS:
        r = wins[win_label]
        row += f" | {r['avg_holdings']:7.1f}          "
    print(row)

# ── Score each weighting: weighted average across windows ───
print()
print("=== COMPOSITE SCORE (higher = better) ===")
print("  Score = WF1*1.0 + WF2*2.0 + WF3*1.0 + FULL*1.5  (WF2 bear penalized most)")
print()
best_score = -999
best_label = ""
for wlabel, wins in results.items():
    wf1 = wins["WF1 COVID+recovery"]["excess"] * 100
    wf2 = wins["WF2 Rate-hike bear "]["excess"] * 100
    wf3 = wins["WF3 AI bull        "]["excess"] * 100
    full = wins["FULL 2017-2026     "]["excess"] * 100
    full_dd  = wins["FULL 2017-2026     "]["port_mdd"] * 100
    full_sh  = wins["FULL 2017-2026     "]["sharpe"]
    score = wf1*1.0 + wf2*2.0 + wf3*1.0 + full*1.5
    marker = " <-- BEST" if score > best_score else ""
    if score > best_score:
        best_score = score
        best_label = wlabel
    print(f"  {wlabel:22s}: {score:+7.1f}  "
          f"(WF2={wf2:+5.1f}%, FULL excess={full:+5.1f}%, DD={full_dd:6.1f}%, Sharpe={full_sh:.2f}){marker}")

print(f"\n  WINNER: {best_label.strip()}")
print()
print("VERDICT: Data speaks. Use the winner as default weighting for SYSTEM v1.0")
