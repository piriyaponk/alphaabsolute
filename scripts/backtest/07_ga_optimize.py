# -*- coding: utf-8 -*-
"""
07_ga_optimize.py — GA Optimiser v2 (regime-conditional + circuit breaker)
===========================================================================
Changes vs v1:
  - 3 WF windows: WF0(2017-19 bull) + WF2(2022-23 bear) + FULL(2017-26)
  - Regime-conditional vol_contraction (fixes 2017 catastrophe)
  - DD circuit breaker parameter in search space
  - Speed: S&P500-only signals for GA search (~14s/eval vs 24s)
  - Pop=30, Gen=20 → 600 evals → ~140 min

Objective (C-variant):
  score = WF0×2 + WF2×4 + FULL×2 + FULL_sharpe×8 - consistency_penalty×2
  Hard rejects: WF0 < -5%, WF2 < -3%, avg_holdings < 5, turnover > 200%

Search space includes regime-conditional params:
  vol_contract_bull: [1.05, 1.10, 1.20, 9.9]   (9.9 = no filter in bull)
  vol_contract_bear: [0.90, 0.95, 1.00, 1.05]
  dd_circuit_pct:    [0.0, 0.12, 0.15, 0.20]    (0 = off)
"""

import sys, io, os, json, random, time, importlib.util
from pathlib import Path
import pandas as pd
import numpy as np

ROOT   = Path(__file__).resolve().parents[2]
BT_DIR = ROOT / "data/backtest"

# ── Data Guardian gate ─────────────────────────────────────────────────────
sys.path.insert(0, str(ROOT / "scripts" / "backtest"))
from data_guardian import guardian_check
if not guardian_check(verbose=True):
    print("\n[ABORT] Data Guardian blocked GA run. Fix all issues above first.")
    sys.exit(1)
# ──────────────────────────────────────────────────────────────────────────


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


v2   = _load(ROOT / "scripts/backtest/03b_backtest_v2.py", "v2")
COST = v2.CostParams()

# Use S&P500-only for GA speed (~14s vs 24s per eval)
SP500_FILE = BT_DIR / "signals.parquet"
NDX_FILE   = BT_DIR / "signals_expanded.parquet"


# ── Walk-forward windows ─────────────────────────────────────────────────────

WINDOWS = {
    "WF0": ("2017-01-01", "2019-12-31"),   # bull market — fix 2017 catastrophe
    "WF2": ("2022-01-01", "2023-12-31"),   # bear + recovery — main stress test
    "FULL": ("2017-01-01", "2026-08-20"),  # full period quality
}

WINDOW_WEIGHTS = {"WF0": 2, "WF2": 4, "FULL": 2}


def run_individual(dl, gene: dict) -> dict:
    """Evaluate one gene across all WF windows."""
    results = {}
    for wname, (start, end) in WINDOWS.items():
        p = v2.StrategyParams(
            rs_threshold       = gene["rs_threshold"],
            vol_trend_min      = gene["vol_trend_bear"],   # legacy field = most restrictive
            vol_contract_max   = gene["vol_contract_bear"],
            vol_contract_bull  = gene["vol_contract_bull"],
            vol_contract_bear  = gene["vol_contract_bear"],
            vol_trend_bull     = gene["vol_trend_bull"],
            vol_trend_bear     = gene["vol_trend_bear"],
            rs_bull            = gene["rs_bull"],
            rs_bear            = gene["rs_bear"],
            base_tight_max     = gene["base_tight_max"],
            adtv_min_m         = gene["adtv_min_m"],
            cap_pct            = gene["cap_pct"],
            weighting          = "softmax",
            softmax_alpha      = gene["softmax_alpha"],
            dd_circuit_pct     = gene["dd_circuit_pct"],
            dd_circuit_exp     = gene["dd_circuit_exp"],
            bull_exposure      = 1.0,
            bear_exposure      = 1.0,
            start_date=start, end_date=end,
        )
        strat = v2.BacktestStrategy(dl, p)
        port  = v2.BacktestPortfolio(1_000_000, COST)
        model = v2.BacktestModel(dl, strat, port, p)
        results[wname] = model.run(verbose=False)

    wf0  = results["WF0"]["excess"]  * 100
    wf2  = results["WF2"]["excess"]  * 100
    full = results["FULL"]["excess"] * 100
    sh   = results["FULL"]["sharpe"]
    dd   = results["FULL"]["port_mdd"] * 100
    holds= results["FULL"]["avg_holdings"]
    turn = results["FULL"].get("avg_turnover", 0) * 100

    # Year-by-year stats from FULL window
    yearly      = results["FULL"].get("yearly", {})
    worst_yr_dd = results["FULL"].get("worst_yr_dd", 0) * 100
    worst_yr_ex = min((v["excess"]*100 for v in yearly.values()), default=0)
    # Count years with negative excess
    neg_yr_count = sum(1 for v in yearly.values() if v["excess"] < 0)
    yr_count     = len(yearly)

    base = {"wf0": wf0, "wf2": wf2, "full": full,
            "sharpe": sh, "dd": dd, "holds": holds, "turn": turn,
            "worst_yr_dd": worst_yr_dd, "worst_yr_ex": worst_yr_ex,
            "neg_yr_count": neg_yr_count}

    # ── Hard rejects ──
    if wf0        < -5.0:  return {"score": -9999, "reject": f"WF0<-5% ({wf0:+.1f}%)",            **base}
    if wf2        < -3.0:  return {"score": -9999, "reject": f"WF2<-3% ({wf2:+.1f}%)",            **base}
    if holds      <  5:    return {"score": -9999, "reject": f"holds<5 ({holds:.0f})",             **base}
    if turn       > 200:   return {"score": -9999, "reject": f"turn>200% ({turn:.0f}%)",           **base}
    if worst_yr_dd < -30:  return {"score": -9999, "reject": f"yr_DD<-30% ({worst_yr_dd:.1f}%)",  **base}
    if neg_yr_count > (yr_count * 0.4):  # max 40% of years negative
        return {"score": -9999, "reject": f"too many neg years ({neg_yr_count}/{yr_count})", **base}

    # ── Soft score ──
    # Consistency penalty: if WF0 and WF2 are very different → regime-dependent
    consistency_penalty = max(0, abs(wf0 - wf2) - 20)  # allow 20% spread
    # DD penalty: penalize worst single-year DD worse than -20%
    dd_yr_penalty = max(0, -worst_yr_dd - 20)   # 0 if worst DD > -20%, else excess amount
    # Win-rate bonus: reward winning more years
    win_rate_bonus = (yr_count - neg_yr_count) / yr_count * 10 if yr_count > 0 else 0

    score = (wf0  * WINDOW_WEIGHTS["WF0"]
           + wf2  * WINDOW_WEIGHTS["WF2"]
           + full * WINDOW_WEIGHTS["FULL"]
           + sh   * 8.0
           - consistency_penalty * 2.0
           - dd_yr_penalty       * 3.0   # penalize bad DD years hard
           + win_rate_bonus      * 1.5)  # reward winning more calendar years

    return {"score": score, "reject": None, **base}


# ── Search space ─────────────────────────────────────────────────────────────

SEARCH_SPACE = {
    # Base RS threshold (reference; regime-conditional may override)
    "rs_threshold":      [70, 75, 80, 85],
    # Regime-conditional: BULL mode (relax filters — breakouts have expanding vol)
    "vol_contract_bull": [1.05, 1.10, 1.20, 9.9],   # 9.9 = no filter in bull
    "vol_trend_bull":    [0.70, 0.75, 0.80, 0.85],  # lower bar in bull
    "rs_bull":           [75, 80, 85],
    # Regime-conditional: BEAR mode (strict filters — contraction = holding up)
    "vol_contract_bear": [0.90, 0.95, 1.00, 1.05],
    "vol_trend_bear":    [0.90, 0.95, 1.00],
    "rs_bear":           [65, 70, 75],
    # Shared params
    "base_tight_max":    [0.95, 1.00, 1.05, 1.10],
    "adtv_min_m":        [10, 15, 20],
    "cap_pct":           [0.12, 0.15, 0.18, 0.20],
    "softmax_alpha":     [0.3, 0.5, 0.7, 1.0],
    # DD circuit breaker (off=0 is valid; circuit hurts WF0 so GA may prefer off)
    "dd_circuit_pct":    [0.0, 0.15, 0.20],
    "dd_circuit_exp":    [0.30, 0.40, 0.50],
}


def random_gene() -> dict:
    return {k: random.choice(v) for k, v in SEARCH_SPACE.items()}


def crossover(a: dict, b: dict) -> dict:
    keys = list(SEARCH_SPACE.keys())
    pt   = random.randint(1, len(keys) - 1)
    return {k: (a[k] if i < pt else b[k]) for i, k in enumerate(keys)}


def mutate(gene: dict, rate: float = 0.20) -> dict:
    child = dict(gene)
    for k, opts in SEARCH_SPACE.items():
        if random.random() < rate:
            child[k] = random.choice(opts)
    return child


def tournament(pop_scored, k=4):
    contestants = random.sample(pop_scored, min(k, len(pop_scored)))
    return max(contestants, key=lambda x: x[0])[1]


# ── GA main ──────────────────────────────────────────────────────────────────

def run_ga(dl, pop_size=30, n_gen=20, elite=5, cx_rate=0.70,
           mut_rate=0.20, tourn_k=4, seed=42):

    random.seed(seed); np.random.seed(seed)

    space_size = 1
    for v in SEARCH_SPACE.values():
        space_size *= len(v)

    print(f"Search space: {space_size:,} combinations")
    print(f"GA: pop={pop_size}  gen={n_gen}  evals={pop_size*n_gen}")
    print(f"Windows: WF0(2017-19 bull)  WF2(2022-23 bear)  FULL(2017-26)")
    print(f"Objective: WF0x2 + WF2x4 + FULLx2 + Sharpex8 - consistency_penalty\n")

    pop     = [random_gene() for _ in range(pop_size)]
    best    = None
    history = []

    for gen in range(n_gen):
        t0 = time.time()
        scored = []
        for i, gene in enumerate(pop):
            res = run_individual(dl, gene)
            scored.append((res["score"], gene, res))
            sys.stdout.write(f"\r  Gen {gen+1:2d}/{n_gen}  eval {i+1:2d}/{pop_size}")
            sys.stdout.flush()

        scored.sort(key=lambda x: x[0], reverse=True)
        gb_score, gb_gene, gb_res = scored[0]

        if best is None or gb_score > best[0]:
            best = (gb_score, gb_gene, gb_res)

        valid  = [s for s in scored if s[0] > -9999]
        reject_reasons = {}
        for sc, _, res in scored:
            if sc <= -9999:
                r = res.get("reject", "unknown")[:10]
                reject_reasons[r] = reject_reasons.get(r, 0) + 1

        dt = time.time() - t0
        rej_str = "  ".join(f"{k}:{v}" for k, v in sorted(reject_reasons.items()))
        print(f"\r  Gen {gen+1:2d}/{n_gen}  best={gb_score:+6.1f}"
              f"  WF0={gb_res.get('wf0',0):+5.1f}%"
              f"  WF2={gb_res.get('wf2',0):+5.1f}%"
              f"  FULL={gb_res.get('full',0):+5.1f}%"
              f"  Sh={gb_res.get('sharpe',0):.2f}"
              f"  valid={len(valid)}/{pop_size}"
              f"  {dt:.0f}s"
              + (f"  rej:[{rej_str}]" if rej_str else ""))

        history.append({"gen": gen+1, "score": gb_score,
                        "wf0": gb_res.get("wf0",0), "wf2": gb_res.get("wf2",0),
                        "full": gb_res.get("full",0), "sharpe": gb_res.get("sharpe",0),
                        "n_valid": len(valid)})

        # Next generation
        next_pop = [g for _, g, _ in scored[:elite]]
        while len(next_pop) < pop_size:
            if random.random() < cx_rate and len(valid) >= 2:
                p1 = tournament([(s, g) for s, g, _ in valid], tourn_k)
                p2 = tournament([(s, g) for s, g, _ in valid], tourn_k)
                child = crossover(p1, p2)
            else:
                child = dict(tournament([(s, g) for s, g, _ in scored], tourn_k))
            next_pop.append(mutate(child, mut_rate))
        pop = next_pop

    return best, history, scored


# ── Results ───────────────────────────────────────────────────────────────────

def print_results(best, history, scored):
    bs, bg, br = best
    print("\n" + "="*80)
    print("  GA RESULTS")
    print("="*80)
    print(f"\nBEST  score={bs:+.1f}")
    print(f"  WF0 (2017-19 bull): {br.get('wf0',0):+.1f}%   [target: >-5%]")
    print(f"  WF2 (2022-23 bear): {br.get('wf2',0):+.1f}%   [target: >-3%]")
    print(f"  FULL 2017-2026:     {br.get('full',0):+.1f}%")
    print(f"  Sharpe: {br.get('sharpe',0):.2f}   DD: {br.get('dd',0):.1f}%   "
          f"holds: {br.get('holds',0):.0f}   turn: {br.get('turn',0):.0f}%")

    print(f"\nBest params:")
    for k, v in bg.items():
        flag = ""
        if k == "vol_contract_bull" and v >= 9.0:
            flag = "  <- no filter in bull"
        elif k == "vol_contract_bear":
            flag = "  <- protective in bear"
        elif k == "dd_circuit_pct" and v > 0:
            flag = f"  <- circuit at {v*100:.0f}% DD"
        print(f"  {k:22s}: {v}{flag}")

    print(f"\nTop 10:")
    header = f"  {'#':3s} | {'Score':7s} | {'WF0':7s} | {'WF2':7s} | {'FULL':7s} | {'Sh':5s} | {'DD':7s} | Key"
    print(header); print("-"*95)
    for i, (sc, gene, res) in enumerate(scored[:10]):
        if sc <= -9999: break
        key = (f"rs={gene['rs_threshold']} "
               f"vcB={gene['vol_contract_bull']} "
               f"vcBear={gene['vol_contract_bear']} "
               f"DD%={gene['dd_circuit_pct']}")
        print(f"  {i+1:3d} | {sc:+7.1f} | {res.get('wf0',0):+6.1f}% | "
              f"{res.get('wf2',0):+6.1f}% | {res.get('full',0):+6.1f}% | "
              f"{res.get('sharpe',0):5.2f} | {res.get('dd',0):6.1f}% | {key}")

    print(f"\nGeneration history:")
    print(f"  {'Gen':4s} | {'Score':7s} | {'WF0':7s} | {'WF2':7s} | {'FULL':7s} | {'Valid'}")
    print("-"*55)
    for h in history:
        print(f"  {h['gen']:4d} | {h['score']:+7.1f} | {h['wf0']:+6.1f}% | "
              f"{h['wf2']:+6.1f}% | {h['full']:+6.1f}% | {h['n_valid']}")

    # vs baselines
    print(f"\nComparison:")
    print(f"  {'Config':28s} | {'WF0':7s} | {'WF2':7s} | {'FULL':7s} | {'Sharpe'}")
    print("-"*70)
    baselines = [
        ("v1.0 (rs=80 bear_exp=0.3)",     None, None,  "+5.0%", "0.95"),
        ("v2.0 (rs=75 vc<1.0 no gate)",   None, "+32.4%", "+28.9%", "1.52"),
        ("GA best",  f"{br.get('wf0',0):+.1f}%", f"{br.get('wf2',0):+.1f}%",
                     f"{br.get('full',0):+.1f}%", f"{br.get('sharpe',0):.2f}"),
    ]
    for name, w0, w2, wf, sh in baselines:
        print(f"  {name:28s} | {str(w0):7s} | {str(w2):7s} | {wf:7s} | {sh}")


def save_results(best, history, scored):
    bs, bg, br = best
    out = {
        "system": "GA_v2", "date": "2026-08-30", "score": bs,
        "params": bg,
        "metrics": {"wf0": br.get("wf0",0), "wf2": br.get("wf2",0),
                    "full": br.get("full",0), "sharpe": br.get("sharpe",0),
                    "dd": br.get("dd",0), "holds": br.get("holds",0)},
        "top10": [{"rank": i+1, "score": sc, "params": gene,
                   "wf0": res.get("wf0",0), "wf2": res.get("wf2",0),
                   "full": res.get("full",0), "sharpe": res.get("sharpe",0)}
                  for i, (sc, gene, res) in enumerate(scored[:10]) if sc > -9999],
        "history": history,
    }
    path = BT_DIR / "ga_results_v2.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved: {path}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    sig_file = SP500_FILE  # S&P500-only for speed; validate best params on NDX file after
    print(f"Loading: {sig_file.name}")
    dl = v2.BacktestDataloader(signal_file=sig_file)
    print(f"Universe: {dl.signals['ticker'].nunique()} tickers  "
          f"(S&P500-only for GA speed; validate best on expanded after)\n")

    # Quick timing estimate
    t0 = time.time()
    gene0 = random_gene()
    _ = run_individual(dl, gene0)
    t_per = time.time() - t0
    total_evals = 30 * 20
    print(f"Timing: {t_per:.1f}s per eval  ->  estimated {total_evals*t_per/60:.0f} min for full GA\n")

    t_start = time.time()
    best, history, scored = run_ga(dl, pop_size=30, n_gen=20, elite=5,
                                   cx_rate=0.70, mut_rate=0.20, seed=42)
    elapsed = time.time() - t_start
    print(f"\nTotal: {elapsed/60:.1f} min")

    print_results(best, history, scored)
    save_results(best, history, scored)

    # ── Validate best params on expanded universe ─────────────────────────────
    print("\n" + "="*80)
    print("  VALIDATION ON EXPANDED UNIVERSE (S&P500 + NDX100)")
    print("="*80)
    if NDX_FILE.exists():
        dl_exp = v2.BacktestDataloader(signal_file=NDX_FILE)
        _, bg, _ = best
        print(f"\nRunning best GA params on {dl_exp.signals['ticker'].nunique()} tickers...")
        print(f"  {'Window':28s} | Excess  | DD      | Sharpe | Holds")
        print("-"*70)
        for wname, (start, end) in WINDOWS.items():
            p = v2.StrategyParams(
                rs_threshold=bg["rs_threshold"],
                vol_trend_min=bg["vol_trend_bear"],
                vol_contract_max=bg["vol_contract_bear"],
                vol_contract_bull=bg["vol_contract_bull"], vol_contract_bear=bg["vol_contract_bear"],
                vol_trend_bull=bg["vol_trend_bull"],       vol_trend_bear=bg["vol_trend_bear"],
                rs_bull=bg["rs_bull"],                     rs_bear=bg["rs_bear"],
                base_tight_max=bg["base_tight_max"], adtv_min_m=bg["adtv_min_m"],
                cap_pct=bg["cap_pct"], weighting="softmax",
                softmax_alpha=bg["softmax_alpha"],
                dd_circuit_pct=bg["dd_circuit_pct"], dd_circuit_exp=bg["dd_circuit_exp"],
                bull_exposure=1.0, bear_exposure=1.0,
                start_date=start, end_date=end)
            r = v2.BacktestModel(dl_exp, v2.BacktestStrategy(dl_exp, p),
                                 v2.BacktestPortfolio(1_000_000, COST), p).run(verbose=False)
            print(f"  {wname:28s} | {r['excess']*100:+6.1f}% | {r['port_mdd']*100:6.1f}% | "
                  f"{r['sharpe']:6.2f} | {r['avg_holdings']:5.0f}")
    else:
        print("  signals_expanded.parquet not found — skipping")
