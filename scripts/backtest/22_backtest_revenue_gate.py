"""
22_backtest_revenue_gate.py
Isolated backtest: add revenue gate to SYSTEM v3.0 + vol_contract=1.5
on expanded 1375-ticker universe.

Tests:
  BASELINE: no revenue filter
  A: rev_yoy >= 20% (or NA pass)
  B: rev_yoy >= 20% (strict - NA fails)
  C: rev_yoy >= 20% AND accel > 0 (NA pass)
  D: rev_accel > 0 only (NA pass)
  E: rev_yoy >= 15% AND accel > 0 (NA pass)

Governance: must beat BASELINE on ALL 3: excess + WF2 + DD simultaneously.
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd, numpy as np, importlib.util, pathlib

_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)

# ── Load revenue signals ──────────────────────────────────────────────────────
rev_path = 'data/backtest/revenue_signals.parquet'
if not pathlib.Path(rev_path).exists():
    print("ERROR: revenue_signals.parquet not found. Run 21_build_revenue_signals.py first.")
    sys.exit(1)

rev = pd.read_parquet(rev_path)
rev['date'] = pd.to_datetime(rev['date'])

# Winsorize outliers — biotech pre-revenue companies can have ±100,000% swings
# Cap at ±500% for YoY growth, ±200% for acceleration (still captures all real cases)
rev['rev_yoy_pct'] = rev['rev_yoy_pct'].clip(-500, 500)
rev['rev_accel']   = rev['rev_accel'].clip(-200, 200)

print(f"Revenue signals: {len(rev):,} rows, {rev['ticker'].nunique()} tickers")
print(f"Coverage: rev_yoy_pct for {rev['rev_yoy_pct'].notna().sum():,} rows ({rev['rev_yoy_pct'].notna().mean()*100:.0f}%)")
print(f"rev_yoy_pct after winsorize: p25={rev['rev_yoy_pct'].quantile(.25):.1f}%  median={rev['rev_yoy_pct'].median():.1f}%  p75={rev['rev_yoy_pct'].quantile(.75):.1f}%")

# Pre-index for fast lookup
rev_idx = rev.set_index(['date', 'ticker'])

dl = v2.BacktestDataloader()

BASE = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=1.5, base_tight_max=9.9, adtv_min_m=10,
    cap_pct=0.18, bear_exposure=0.50, bull_exposure=1.00,
    softmax_alpha=1.0, start_date='2017-01-01', end_date='2026-08-20'
)

TOP_N = 15
_orig_screen = v2.BacktestStrategy.screen

def make_screen(rev_yoy_min=None, accel_min=None, na_pass=True):
    """
    Returns a patched screen method that applies revenue filter.
    na_pass=True: tickers with no revenue data pass through (don't penalize)
    na_pass=False: tickers with no revenue data are excluded
    """
    def patched_screen(self, date):
        # 1. Apply original screen + top_n
        result = _orig_screen(self, date)
        if result.empty:
            return result

        # 2. Apply top_n
        if len(result) > TOP_N:
            result = result.nlargest(TOP_N, 'rs_pct').copy()
            w = result['weight'].clip(upper=self.params.cap_pct)
            result['weight'] = w / w.sum() * result['weight'].sum()

        if rev_yoy_min is None and accel_min is None:
            return result

        # 3. Revenue filter — look up point-in-time revenue at this date
        keep = []
        for _, row in result.iterrows():
            ticker = row['ticker']
            try:
                rev_row = rev_idx.loc[(date, ticker)]
                yoy = rev_row['rev_yoy_pct']
                accel = rev_row['rev_accel']
            except KeyError:
                # No revenue data for this ticker at this date
                if na_pass:
                    keep.append(True)
                else:
                    keep.append(False)
                continue

            passes = True
            if rev_yoy_min is not None:
                if pd.isna(yoy):
                    passes = na_pass
                elif yoy < rev_yoy_min:
                    passes = False

            if passes and accel_min is not None:
                if pd.isna(accel):
                    if not na_pass:
                        passes = False
                elif accel < accel_min:
                    passes = False

            keep.append(passes)

        result = result[keep].copy()
        if result.empty:
            return result

        # Re-normalize weights after filtering
        w = result['weight'].clip(upper=self.params.cap_pct)
        total = w.sum()
        if total > 0:
            result['weight'] = w / total * result['weight'].sum()
        return result

    return patched_screen


def run(label, rev_yoy_min=None, accel_min=None, na_pass=True):
    p = v2.StrategyParams(**BASE)
    v2.BacktestStrategy.screen = make_screen(rev_yoy_min, accel_min, na_pass)
    strat = v2.BacktestStrategy(dl, p)
    port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
    r = v2.BacktestModel(dl, strat, port, p).run(verbose=False)

    ye = {yr: d['excess'] for yr, d in r.get('yearly', {}).items()}
    wins = sum(1 for v in ye.values() if v > 0)
    scale = 100 if r['port_cagr'] < 5 else 1
    pc   = r['port_cagr'] * scale
    ex   = r['excess'] * scale
    dd   = r['port_mdd'] * scale
    sh   = r['sharpe']
    ca   = r['calmar']
    wf1  = sum(ye.get(yr, 0) for yr in [2020, 2021]) / 2 * scale
    wf2  = sum(ye.get(yr, 0) for yr in [2022, 2023]) / 2 * scale
    wf3  = sum(ye.get(yr, 0) for yr in [2024, 2025]) / 2 * scale
    holds = r['avg_holdings']
    yr_str = ' | '.join(f"{yr}:{ye[yr]*scale:+.1f}%" for yr in sorted(ye))

    print(f"\n{'='*65}")
    print(f"  {label}")
    print(f"  Port CAGR={pc:.1f}% | Excess={ex:+.1f}% | DD={dd:.1f}%")
    print(f"  Sharpe={sh:.2f} | Calmar={ca:.2f} | Holds={holds:.0f} | Win={wins}/{len(ye)}")
    print(f"  WF1={wf1:+.1f}% | WF2={wf2:+.1f}% | WF3={wf3:+.1f}%")
    print(f"  {yr_str}")
    return {'label': label, 'excess': ex, 'dd': dd, 'sharpe': sh,
            'wf2': wf2, 'wins': wins, 'n': len(ye), 'holds': holds,
            'wf1': wf1, 'wf3': wf3, 'cagr': pc}


print("\n" + "="*65)
print("REVENUE GATE — Isolation Test")
print("Base: v3.0 params + vol_contract=1.5 + expanded 1375-ticker universe")
print("Governance ref: SP500 v3.0 = Excess+35.8%, WF2+37.9%, DD-40.1%")
print("="*65)

results = []
results.append(run("BASELINE (no revenue filter)"))
results.append(run("A: rev_yoy>=20% (NA=pass)",      rev_yoy_min=20,  na_pass=True))
results.append(run("B: rev_yoy>=20% (NA=fail strict)", rev_yoy_min=20, na_pass=False))
results.append(run("C: rev_yoy>=20% AND accel>0 (NA=pass)", rev_yoy_min=20, accel_min=0, na_pass=True))
results.append(run("D: accel>0 only (NA=pass)",       accel_min=0,     na_pass=True))
results.append(run("E: rev_yoy>=15% AND accel>0 (NA=pass)", rev_yoy_min=15, accel_min=0, na_pass=True))
results.append(run("F: rev_yoy>=25% (NA=pass)",       rev_yoy_min=25,  na_pass=True))
results.append(run("G: rev_yoy>=10% AND accel>0 (NA=pass)", rev_yoy_min=10, accel_min=0, na_pass=True))

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n\n{'Config':<45} {'Excess':>8} {'DD':>8} {'Sharpe':>7} {'WF2':>8} {'Win':>6} {'Holds':>6}")
print("-"*90)
for r in results:
    print(f"{r['label']:<45} {r['excess']:>+7.1f}% {r['dd']:>7.1f}% {r['sharpe']:>7.2f} {r['wf2']:>+7.1f}% {r['wins']}/{r['n']} {r['holds']:>6.0f}")

print(f"\n[GOVERNANCE] Baseline expanded universe (vol_contract=1.5):")
b = results[0]
print(f"  Excess={b['excess']:+.1f}% | WF2={b['wf2']:+.1f}% | DD={b['dd']:.1f}% | Win={b['wins']}/{b['n']}")
print(f"\n  Config beats baseline on:")
for r in results[1:]:
    e = 'Y' if r['excess'] > b['excess'] else 'N'
    w = 'Y' if r['wf2']    > b['wf2']    else 'N'
    d = 'Y' if r['dd']     > b['dd']     else 'N'
    verdict = "BEATS ALL 3" if e=='Y' and w=='Y' and d=='Y' else f"excess={e} wf2={w} dd={d}"
    print(f"  {r['label'][:44]}: {verdict}")
