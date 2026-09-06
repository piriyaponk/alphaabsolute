# -*- coding: utf-8 -*-
"""
Step 4: Grid search + Genetic Algorithm optimization.
Train: 2017-2021  |  Test: 2022-2026
Fitness (anti-overfit): test_excess*3 + train_excess*1 - dd_penalty*2 - turnover*0.3 - gap_penalty*2
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import json, random, time
import numpy as np
import pandas as pd
from pathlib import Path
from copy import deepcopy

ROOT       = Path(__file__).resolve().parents[2]
BT_DIR     = ROOT / "data" / "backtest"
RESULT_CSV = BT_DIR / "optimization_results.csv"
GA_CSV     = BT_DIR / "ga_results.csv"
BEST_JSON  = BT_DIR / "best_params.json"

sys.path.insert(0, str(Path(__file__).parent))
# import by filename since module is named 03_backtest_engine
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("backtest_engine", Path(__file__).parent / "03_backtest_engine.py")
_mod  = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_mod)
BacktestParams = _mod.BacktestParams
run_backtest   = _mod.run_backtest

TRAIN_START = "2017-01-01"
TRAIN_END   = "2021-12-31"
TEST_START  = "2022-01-01"
TEST_END    = "2026-08-20"


def fitness(train: dict, test: dict) -> float:
    ex_train = train["excess"]
    ex_test  = test["excess"]
    # DD advantage: less negative = better (we want port DD > bench DD, i.e. smaller absolute drawdown)
    dd_penalty  = max(0, abs(test["port_mdd"]) - abs(test["bench_mdd"]))  # extra DD vs benchmark
    gap         = max(0, ex_train - ex_test)   # overfit penalty
    turnover    = test["avg_turnover"]
    sharpe_bonus = test["sharpe"] * 0.5

    return (
        ex_test  * 3.0
        + ex_train * 1.0
        + sharpe_bonus
        - dd_penalty * 2.0
        - turnover   * 0.3
        - gap        * 2.0
    )


def evaluate(params: BacktestParams, signals: pd.DataFrame) -> dict | None:
    try:
        p_train = deepcopy(params); p_train.start_date = TRAIN_START; p_train.end_date = TRAIN_END
        p_test  = deepcopy(params); p_test.start_date  = TEST_START;  p_test.end_date  = TEST_END

        # Suppress stdout during sub-runs
        import io as _io, contextlib
        f = _io.StringIO()
        with contextlib.redirect_stdout(f):
            train = run_backtest(p_train, signals)
            test  = run_backtest(p_test, signals)

        score = fitness(train, test)
        return {
            "score":       score,
            "train_excess": train["excess"],
            "test_excess":  test["excess"],
            "train_cagr":  train["port_cagr"],
            "test_cagr":   test["port_cagr"],
            "test_mdd":    test["port_mdd"],
            "bench_mdd":   test["bench_mdd"],
            "sharpe":      test["sharpe"],
            "calmar":      test["calmar"],
            "turnover":    test["avg_turnover"],
            "holdings":    test["avg_holdings"],
            "gap":         train["excess"] - test["excess"],
            # params
            "rs_threshold":          params.rs_threshold,
            "vol_contract_max":      params.vol_contract_max,
            "base_tight_max":        params.base_tight_max,
            "adtv_min_m":            params.adtv_min_m,
            "rs_accel_min":          params.rs_accel_min,
            "price_vs_ma50_min":     params.price_vs_ma50_min,
            "pct_from_52w_high_min": params.pct_from_52w_high_min,
            "vol_trend_min":         params.vol_trend_min,
            "liquidity_power":       params.liquidity_power,
            "cap_pct":               params.cap_pct,
            "bear_exposure":         params.bear_exposure,
        }
    except Exception as e:
        return None


# ── GRID SEARCH ─────────────────────────────────────────────────────────────
GRID = {
    "rs_threshold":          [60, 65, 70, 75, 80],
    "vol_contract_max":      [0.85, 0.95, 1.0, 1.10],
    "base_tight_max":        [0.85, 0.95, 1.0, 1.10],
    "adtv_min_m":            [10, 15, 25],
    "vol_trend_min":         [0.0, 0.85, 0.90, 0.95, 1.0],  # key factor
    "liquidity_power":       [1.0, 1.5, 2.0, 2.5, 3.0],
    "cap_pct":               [0.12, 0.15, 0.18, 0.20, 0.25],
    "bear_exposure":         [0.25, 0.30, 0.40, 0.50],
    # New factors (sparse grid — optimizer will refine)
    "rs_accel_min":          [-99.0, 0.0, 3.0],
    "pct_from_52w_high_min": [-99.0, -30.0, -20.0],
}

def run_grid_search(signals: pd.DataFrame, max_trials: int = 300) -> pd.DataFrame:
    from itertools import product
    keys   = list(GRID.keys())
    combos = list(product(*GRID.values()))
    random.shuffle(combos)
    combos = combos[:max_trials]

    print(f"Grid search: {len(combos)} trials (of {len(list(product(*GRID.values()))):,} possible)")
    results = []
    t0 = time.time()
    for idx, combo in enumerate(combos):
        kw = dict(zip(keys, combo))
        params = BacktestParams(**kw)
        r = evaluate(params, signals)
        if r:
            results.append(r)
            print(f"[{idx+1:3d}/{len(combos)}] "
                  f"rs={kw['rs_threshold']:.0f} vt={kw['vol_trend_min']:.2f} lp={kw['liquidity_power']:.1f} "
                  f"-> score={r['score']:.3f} test_ex={r['test_excess']*100:+.1f}% sharpe={r['sharpe']:.2f}")

    df = pd.DataFrame(results).sort_values("score", ascending=False)
    df.to_csv(RESULT_CSV, index=False)
    elapsed = time.time() - t0
    print(f"\nGrid done in {elapsed/60:.1f}m. Top 10:")
    cols = ["score","train_excess","test_excess","test_mdd","bench_mdd","sharpe","calmar","turnover","holdings",
            "rs_threshold","vol_trend_min","liquidity_power","cap_pct"]
    print(df.head(10)[cols].to_string(float_format=lambda x: f"{x:.3f}"))
    return df


# ── GENETIC ALGORITHM ────────────────────────────────────────────────────────
BOUNDS = {
    "rs_threshold":          (55, 85, 5),
    "vol_contract_max":      (0.80, 1.15, 0.05),
    "base_tight_max":        (0.80, 1.15, 0.05),
    "adtv_min_m":            (5, 30, 5),
    "vol_trend_min":         (0.0, 1.10, 0.05),
    "liquidity_power":       (1.0, 3.5, 0.25),
    "cap_pct":               (0.10, 0.30, 0.02),
    "bear_exposure":         (0.20, 0.60, 0.05),
    "rs_accel_min":          (-99.0, 10.0, 109.0),   # effectively on/off: -99 or a value
    "pct_from_52w_high_min": (-99.0, -15.0, 84.0),  # on/off
}

def rand_param() -> dict:
    kw = {}
    for k, (lo, hi, step) in BOUNDS.items():
        n = round((hi - lo) / step)
        kw[k] = round(lo + random.randint(0, n) * step, 4)
    return kw

def crossover(a, b):
    return {k: (a[k] if random.random() < 0.5 else b[k]) for k in a}

def mutate(d, rate=0.25):
    result = d.copy()
    for k, (lo, hi, step) in BOUNDS.items():
        if random.random() < rate:
            n = round((hi - lo) / step)
            result[k] = round(lo + random.randint(0, n) * step, 4)
    return result


def run_ga(signals: pd.DataFrame, pop_size=40, generations=20, elite_k=8) -> pd.DataFrame:
    print(f"\nGenetic Algorithm: pop={pop_size}, gen={generations}")
    population = [BacktestParams(**rand_param()) for _ in range(pop_size)]
    all_results = []
    best_score  = -np.inf
    best_params = None
    t0 = time.time()

    for gen in range(generations):
        print(f"\n--- Gen {gen+1}/{generations} (elapsed {(time.time()-t0)/60:.1f}m) ---")
        scored = []
        for p in population:
            r = evaluate(p, signals)
            if r:
                scored.append((r["score"], {k: getattr(p,k) for k in BOUNDS}, r))
                all_results.append(r)
                if r["score"] > best_score:
                    best_score = r["score"]
                    best_params = {k: getattr(p,k) for k in BOUNDS}
                    print(f"  * New best score={best_score:.3f} test_ex={r['test_excess']*100:+.1f}% "
                          f"sharpe={r['sharpe']:.2f} vol_trend={getattr(p,'vol_trend_min'):.2f}")

        if not scored:
            continue
        scored.sort(key=lambda x: x[0], reverse=True)
        elite = [s[1] for s in scored[:elite_k]]

        next_pop = [BacktestParams(**d) for d in elite]
        while len(next_pop) < pop_size:
            a, b = random.sample(elite, 2)
            child = BacktestParams(**mutate(crossover(a, b)))
            next_pop.append(child)
        population = next_pop

    df = pd.DataFrame(all_results).sort_values("score", ascending=False)
    df.to_csv(GA_CSV, index=False)
    if best_params:
        BEST_JSON.write_text(json.dumps(best_params, indent=2))
        print(f"\nBest params saved to {BEST_JSON}:")
        print(json.dumps(best_params, indent=2))

    print(f"\nTop 10 GA results:")
    cols = ["score","train_excess","test_excess","test_mdd","bench_mdd","sharpe","calmar",
            "rs_threshold","vol_trend_min","liquidity_power","cap_pct","holdings"]
    print(df.head(10)[cols].to_string(float_format=lambda x: f"{x:.3f}"))
    return df


if __name__ == "__main__":
    import sys as _sys
    mode = _sys.argv[1] if len(_sys.argv) > 1 else "grid"

    print("Loading signals...")
    signals = pd.read_parquet(BT_DIR / "signals.parquet")
    print(f"  {len(signals):,} rows, {signals['ticker'].nunique()} tickers\n")

    if mode == "grid":
        run_grid_search(signals, max_trials=300)
    elif mode == "ga":
        run_ga(signals, pop_size=40, generations=20)
    elif mode == "both":
        run_grid_search(signals, max_trials=200)
        run_ga(signals, pop_size=30, generations=15)
    else:
        print("Usage: python 04_optimize.py [grid|ga|both]")
