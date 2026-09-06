"""
16_egarch_vol_overlay.py — Isolated test of EGARCH-inspired vol-sizing overlay
Adapted from template models/top_country_v2/egarch_cycle_v3.py

Idea: At each month-end rebalance, check SPY rolling vol ratio (5d / 21d).
      If vol has spiked, scale down deployed capital by a trim multiplier.
      This sits ABOVE the existing bear_exposure gate (min of both applies).

Test configs vs SYSTEM v3.0 baseline (no overlay):
  A: trim_ratio=1/3, trigger=vol5/vol21>1.5  (spike = pull back 33%)
  B: trim_ratio=1/3, trigger=vol5/vol21>2.0  (less sensitive)
  C: trim_ratio=1/2, trigger=vol5/vol21>1.5  (more aggressive trim)
  D: trim_ratio=1/3, trigger=vol5/vol21>1.5 + rebuy=vol5/vol21<1.0  (hold trim until quiet)

All tests use SYSTEM v3.0 base params + expanded 1395-ticker universe.
Governance: ISOLATION RULE — compare full excess + WF2 + DD vs baseline.
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd
import numpy as np
import importlib
import importlib.util
import pathlib

# ── Load backtest engine ──────────────────────────────────────────────────────
import importlib.util, pathlib
_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)
dl = v2.BacktestDataloader()
print(f"BacktestDataloader: {dl.signals['ticker'].nunique()} tickers, signals {len(dl.signals):,} rows")

# ── SYSTEM v3.0 base params ───────────────────────────────────────────────────
BASE = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=9.9, base_tight_max=9.9, adtv_min_m=10,
    cap_pct=0.18, bear_exposure=0.50, bull_exposure=1.00,
    softmax_alpha=1.0,
    start_date='2017-01-01', end_date='2026-08-20'
)

TOP_N = 15

# ── top_n monkey patch ────────────────────────────────────────────────────────
_orig_screen = v2.BacktestStrategy.screen
def _topn_screen(self, date):
    result = _orig_screen(self, date)
    top_n = TOP_N
    if not result.empty and len(result) > top_n:
        result = result.nlargest(top_n, 'rs_pct').copy()
        w = result['weight'].clip(upper=self.params.cap_pct)
        result['weight'] = w / w.sum() * result['weight'].sum()
    return result
v2.BacktestStrategy.screen = _topn_screen

# ── SPY vol ratio precompute ──────────────────────────────────────────────────
spy_px = dl.signals[dl.signals['ticker'] == 'SPY'].copy()
spy_px = spy_px.sort_values('date')
spy_px['ret'] = spy_px['close'].pct_change()
spy_px['vol5']  = spy_px['ret'].rolling(5).std()
spy_px['vol21'] = spy_px['ret'].rolling(21).std()
spy_px['vol_ratio'] = spy_px['vol5'] / spy_px['vol21'].replace(0, np.nan)
spy_px['date_str'] = spy_px['date'].astype(str)
spy_vol = spy_px.set_index('date_str')['vol_ratio']
print(f"SPY vol ratio: p50={spy_vol.median():.2f}, p75={spy_vol.quantile(0.75):.2f}, p95={spy_vol.quantile(0.95):.2f}")
# How often does ratio exceed thresholds?
for thr in [1.5, 2.0, 2.5]:
    pct = (spy_vol > thr).mean() * 100
    print(f"  vol_ratio > {thr}: {pct:.1f}% of days")


# ── Vol overlay injection ─────────────────────────────────────────────────────
def make_vol_overlay_screen(orig_screen, spy_vol_series, trigger, trim_ratio, rebuy_trigger=None):
    """
    Returns a patched screen method that applies vol-sizing overlay.
    trim_ratio=1/3 means deployed capital * (1 - 1/3) = 67% when triggered.
    rebuy_trigger: if set, hold trim until vol_ratio drops below this (hysteresis).
    """
    in_trim_state = [False]  # mutable closure state

    def patched_screen(self, date):
        result = orig_screen(self, date)
        if result.empty:
            return result

        date_str = str(date)[:10]
        vr = spy_vol_series.get(date_str, np.nan)

        # Hysteresis: update state
        if not np.isnan(vr):
            if vr >= trigger:
                in_trim_state[0] = True
            elif rebuy_trigger is not None and vr < rebuy_trigger:
                in_trim_state[0] = False
            elif rebuy_trigger is None:
                in_trim_state[0] = False  # no hysteresis: apply only when triggered

        if in_trim_state[0]:
            # Scale weights down by trim_ratio (reduce exposure)
            result = result.copy()
            result['weight'] = result['weight'] * (1 - trim_ratio)
            # Normalize back (still capped, just lower total exposure)
        return result

    return patched_screen


# ── Run one backtest config ───────────────────────────────────────────────────
def run_config(label, extra_params=None, vol_trigger=None, trim_ratio=None, rebuy_trigger=None):
    params_dict = {**BASE, **(extra_params or {})}
    p = v2.StrategyParams(**params_dict)


    # Reset screen to base (topn patch applied)
    v2.BacktestStrategy.screen = _topn_screen

    if vol_trigger is not None:
        # Apply vol overlay on top of topn patch
        overlay_screen = make_vol_overlay_screen(
            _topn_screen, spy_vol, vol_trigger, trim_ratio, rebuy_trigger
        )
        v2.BacktestStrategy.screen = overlay_screen

    strat = v2.BacktestStrategy(dl, p)
    port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
    model = v2.BacktestModel(dl, strat, port, p)
    r = model.run(verbose=False)

    ye = {yr: d['excess'] for yr, d in r.get('yearly', {}).items()}
    wins = sum(1 for v in ye.values() if v > 0)

    # WF periods
    wf1 = sum(ye.get(yr, 0) for yr in [2020, 2021]) / 2
    wf2 = sum(ye.get(yr, 0) for yr in [2022, 2023]) / 2
    wf3 = sum(ye.get(yr, 0) for yr in [2024, 2025]) / 2

    print(f"\n{'='*60}")
    print(f"Config: {label}")
    print(f"  CAGR: Port={r['port_cagr']:.1f}% | Bench={r['bench_cagr']:.1f}% | Excess={r['excess']:.1f}%")
    print(f"  DD={r['port_mdd']:.1f}% | Sharpe={r['sharpe']:.2f} | Calmar={r['calmar']:.2f}")
    print(f"  AvgHolds={r['avg_holdings']:.0f} | Win={wins}/{len(ye)}")
    print(f"  WF1(2020-21)={wf1:+.1f}% | WF2(2022-23)={wf2:+.1f}% | WF3(2024-25)={wf3:+.1f}%")
    yr_str = ' | '.join(f"{yr}:{v:+.1f}%" for yr, v in sorted(ye.items()))
    print(f"  By year: {yr_str}")

    return {
        'label': label,
        'excess': r['excess'], 'port_cagr': r['port_cagr'],
        'dd': r['port_mdd'], 'sharpe': r['sharpe'], 'calmar': r['calmar'],
        'wins': wins, 'total_years': len(ye),
        'wf1': wf1, 'wf2': wf2, 'wf3': wf3,
        'avg_holdings': r['avg_holdings'],
    }


# ── Run tests ─────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("EGARCH Vol Overlay — Isolation Test")
print("Baseline = SYSTEM v3.0 on expanded 1395-ticker universe")
print("="*60)

results = []

# Baseline (no overlay)
results.append(run_config("v3.0 BASELINE (no overlay)"))

# Config A: moderate trigger, 1/3 trim, no hysteresis
results.append(run_config(
    "A: trigger=1.5, trim=1/3, no hysteresis",
    vol_trigger=1.5, trim_ratio=1/3, rebuy_trigger=None
))

# Config B: higher trigger (less sensitive), 1/3 trim
results.append(run_config(
    "B: trigger=2.0, trim=1/3, no hysteresis",
    vol_trigger=2.0, trim_ratio=1/3, rebuy_trigger=None
))

# Config C: moderate trigger, 1/2 trim (more aggressive)
results.append(run_config(
    "C: trigger=1.5, trim=1/2, no hysteresis",
    vol_trigger=1.5, trim_ratio=0.5, rebuy_trigger=None
))

# Config D: hysteresis — trim until vol_ratio < 1.0 (stays in trim longer)
results.append(run_config(
    "D: trigger=1.5, trim=1/3, rebuy<1.0 (hysteresis)",
    vol_trigger=1.5, trim_ratio=1/3, rebuy_trigger=1.0
))

# Config E: same trigger but with vol_contract_max=1.5 quality filter
results.append(run_config(
    "E: trigger=1.5, trim=1/3 + vol_contract_max=1.5",
    extra_params={'vol_contract_max': 1.5},
    vol_trigger=1.5, trim_ratio=1/3, rebuy_trigger=None
))

# ── Summary table ─────────────────────────────────────────────────────────────
print("\n\n" + "="*60)
print("SUMMARY TABLE")
print(f"{'Config':<42} {'Excess':>7} {'DD':>7} {'Sharpe':>7} {'WF2':>7} {'Win':>5}")
print("-"*60)
for r in results:
    print(f"{r['label']:<42} {r['excess']:>+7.1f}% {r['dd']:>7.1f}% {r['sharpe']:>7.2f} {r['wf2']:>+7.1f}% {r['wins']}/{r['total_years']}")

print("\n[GOVERNANCE CHECK] vs SYSTEM v3.0 on SP500 (Excess=+35.8%, WF2=+37.9%, DD=-40.1%)")
print("  A candidate must beat baseline on ALL 3: FULL excess + WF2 + DD simultaneously.")
baseline = results[0]
print(f"\n  Expanded universe baseline: Excess={baseline['excess']:+.1f}%, WF2={baseline['wf2']:+.1f}%, DD={baseline['dd']:.1f}%")
for r in results[1:]:
    beats_excess = r['excess'] > baseline['excess']
    beats_wf2    = r['wf2']    > baseline['wf2']
    beats_dd     = r['dd']     > baseline['dd']  # less negative = better
    verdict = "BEATS ALL 3" if (beats_excess and beats_wf2 and beats_dd) else "partial"
    print(f"  {r['label'][:40]}: excess={'Y' if beats_excess else 'N'} wf2={'Y' if beats_wf2 else 'N'} dd={'Y' if beats_dd else 'N'} -> {verdict}")
