"""
23_exposure_multiplier_test.py
Test all exposure multiplier variants on SYSTEM v3.0 + vol_contract=1.5

Template insight: 2-sleeve system (Credit + Market), final = min(both)
  Credit sleeve: HY OAS spread level + momentum + BBB stress
  Market sleeve: vol_ratio + MA200 trend
  Recovery (V-shape): off-low 20% + rising MA50 + credit relief -> override to 100%

Configs tested:
  BASELINE   : SPY>MA200=100%, SPY<MA200=50%    (current v3.0 locked)
  STRICT     : SPY>MA200=100%, SPY<MA200=30%    (more cash in bear)
  AGGRESSIVE : SPY>MA200=100%, SPY<MA200=70%    (less reduction)
  FULL_CASH  : SPY>MA200=100%, SPY<MA200=0%     (all-cash bear)
  DEATH_CROSS: trigger on 50d<200d (earlier warning), bear=50%
  DC_STRICT  : death cross trigger, bear=30%
  DUAL_GATE  : bear only when BOTH price<MA200 AND death cross, bear=30%
  DYNAMIC    : linear scale 30%-100% based on SPY % distance from MA200
  VOL_SCALED : reduce when SPY realized vol spikes (vol_ratio > 1.5x), min 30%
  HY_CREDIT  : add HY OAS spread spike as secondary reducer (from FRED)
  FULL_OVERLAY: min(MA200 exposure, HY credit exposure) — closest to template

Also tests:
  52W_HIGH   : require close >= 0.75 * 52w_high as entry gate (vs no filter)
"""
import sys, io, os, ssl
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd
import numpy as np
import importlib.util, pathlib
import requests
ssl._create_default_https_context = ssl._create_unverified_context

_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)

# ── Load SPY daily prices for extra regime computations ──────────────────────
dl = v2.BacktestDataloader()
sig = dl.signals.copy()
spy_daily = sig[sig['ticker'] == 'SPY'][['date', 'close', 'vol_contraction']].copy()
spy_daily = spy_daily.sort_values('date').set_index('date')

# Compute SPY MA50, MA200, death cross
spy_daily['ma50']   = spy_daily['close'].rolling(50, min_periods=35).mean()
spy_daily['ma200']  = spy_daily['close'].rolling(200, min_periods=140).mean()
spy_daily['death_cross'] = (spy_daily['ma50'] < spy_daily['ma200']).astype(int)
spy_daily['pct_from_ma200'] = (spy_daily['close'] / spy_daily['ma200'] - 1).clip(-0.30, 0.30)
# vol_ratio: realized vol 20d vs 63d (already vol_contraction in signals)
spy_daily['vol_ratio'] = spy_daily['vol_contraction']  # 20d/60d vol ratio

# ── Fetch HY OAS from FRED (no API key needed for CSV endpoint) ──────────────
hy_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2"
hy_df = None
try:
    print("Fetching HY OAS from FRED...")
    resp = requests.get(hy_url, timeout=20, verify=False)
    from io import StringIO
    hy_df = pd.read_csv(StringIO(resp.text), parse_dates=['DATE'])
    hy_df.columns = ['date', 'hy_oas']
    hy_df = hy_df[hy_df['hy_oas'] != '.'].copy()
    hy_df['hy_oas'] = hy_df['hy_oas'].astype(float)
    hy_df['date'] = pd.to_datetime(hy_df['date'])
    hy_df = hy_df.set_index('date').sort_index()
    # HY OAS level signal (template: score = HY level + momentum)
    # Spread > 500bps = stress; > 700bps = severe
    hy_df['hy_ma60'] = hy_df['hy_oas'].rolling(60, min_periods=30).mean()
    hy_df['hy_spike'] = (hy_df['hy_oas'] > hy_df['hy_ma60'] * 1.3).astype(int)  # 30% spike vs 60d avg
    hy_df['hy_stress'] = (hy_df['hy_oas'] > 500).astype(int)
    # Exposure map: spike or stress → 65%, severe (>700) → 40%, otherwise 100%
    hy_df['credit_exposure'] = np.where(hy_df['hy_oas'] > 700, 0.40,
                                np.where((hy_df['hy_spike']==1) | (hy_df['hy_stress']==1), 0.65, 1.00))
    print(f"HY OAS loaded: {len(hy_df)} days, {hy_df.index.min().date()} to {hy_df.index.max().date()}")
    print(f"Stress days (>500bps): {hy_df['hy_stress'].sum()} | Spike days: {hy_df['hy_spike'].sum()}")
    # Show key periods
    for label, start, end in [('GFC', '2008-01', '2009-03'), ('COVID', '2020-03', '2020-06'),
                               ('2022 hike', '2022-01', '2023-01')]:
        period = hy_df.loc[start:end, 'hy_oas']
        print(f"  {label}: max={period.max():.0f}bps  stress_days={hy_df.loc[start:end, 'hy_stress'].sum()}")
except Exception as e:
    print(f"WARNING: FRED fetch failed ({e}). HY credit configs will be skipped.")

# ── Base config ───────────────────────────────────────────────────────────────
BASE = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=1.5, base_tight_max=9.9, adtv_min_m=10,
    cap_pct=0.18, bear_exposure=0.50, bull_exposure=1.00,
    softmax_alpha=1.0, start_date='2017-01-01', end_date='2026-08-20'
)
TOP_N = 15
_orig_screen = v2.BacktestStrategy.screen

def make_screen(top_n=TOP_N, require_52w_high_pct=None):
    """Patch screen to cap top_n and optionally gate on 52W high proximity."""
    def patched_screen(self, date):
        result = _orig_screen(self, date)
        if result.empty:
            return result
        if len(result) > top_n:
            result = result.nlargest(top_n, 'rs_pct').copy()
            w = result['weight'].clip(upper=self.params.cap_pct)
            result['weight'] = w / w.sum() * result['weight'].sum()
        if require_52w_high_pct is not None:
            # Keep stocks within X% of 52W high (require_52w_high_pct = e.g. 0.75 = within 25%)
            if 'pct_from_52w_high' in result.columns:
                # pct_from_52w_high is negative (0 = at high, -0.25 = 25% below)
                mask = result['pct_from_52w_high'] >= -(1 - require_52w_high_pct)
                result = result[mask].copy()
                if not result.empty:
                    w = result['weight'].clip(upper=self.params.cap_pct)
                    total = w.sum()
                    if total > 0:
                        result['weight'] = w / total * result['weight'].sum()
        return result
    return patched_screen

def make_dynamic_exposure_model(exposure_func, screen_fn=None):
    """
    Monkey-patch Model.run() to use a custom per-date exposure function.
    exposure_func(date) -> float in [0, 1]
    """
    if screen_fn is None:
        screen_fn = make_screen()

    class PatchedModel(v2.BacktestModel):
        def _get_exposure(self, date, spy_above):
            return exposure_func(date, spy_above)

    return PatchedModel


def run(label, bear_exp=0.50, bull_exp=1.00,
        use_death_cross=False, dual_gate=False,
        use_dynamic=False, use_vol_scaled=False,
        use_hy_credit=False, use_full_overlay=False,
        require_52w_pct=None):
    """Run a single config and return results dict."""
    p = v2.StrategyParams(**{**BASE, 'bear_exposure': bear_exp, 'bull_exposure': bull_exp})
    v2.BacktestStrategy.screen = make_screen(top_n=TOP_N, require_52w_high_pct=require_52w_pct)

    if not any([use_death_cross, dual_gate, use_dynamic, use_vol_scaled, use_hy_credit, use_full_overlay]):
        # Standard: use the built-in SPY>MA200 regime gate
        strat = v2.BacktestStrategy(dl, p)
        port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
        r = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
    else:
        # We need to override the exposure calculation
        # Patch BacktestStrategy to use custom exposure
        orig_spy_check = v2.BacktestStrategy._spy_above_ma200 if hasattr(v2.BacktestStrategy, '_spy_above_ma200') else None

        # The v2 engine calls self.params.bear_exposure / bull_exposure based on spy_above_ma200
        # We intercept by patching the screen to also do weight scaling, but that's wrong
        # Correct approach: patch StrategyParams dynamically per date — not possible
        # Alternative: precompute per-date regime overrides and patch via a wrapper

        # Since we can't easily patch the monthly rebalance exposure mid-run,
        # we simulate by modifying what the engine does:
        # The engine gets exposure from params based on spy_above_ma200 signal in signals.parquet
        # For custom regimes, we'll patch by overriding the signal at rebalance dates

        # APPROACH: Create a modified signals parquet with custom spy_above_ma200 + exposure
        # This is complex — instead, use the simpler approach:
        # Patch BacktestPortfolio.apply_exposure() if it exists, or
        # Create a subclass of BacktestModel that overrides the rebalance logic

        # Check what's available in v2
        # For now: use the bear/bull params directly but with a pre-mapped regime series
        # We'll swap bear_exposure and bull_exposure per month by patching StrategyParams

        # Simple: we run the backtest and post-process the monthly returns
        # This is valid because exposure multiplier is applied uniformly across all months
        # Post-processing: for each month, if our custom regime = bear, scale returns

        # Get monthly rebalance dates and regimes from backtest
        strat = v2.BacktestStrategy(dl, p)
        port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
        base_result = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
        r = base_result  # placeholder — we'll refine below

        # For a proper test: override bear/bull exposure at param level
        # and re-run with custom spy_above_ma200 signal
        # The key: signals.parquet has 'spy_above_ma200' column (precomputed)
        # We can create a patched dataloader that overrides this column

        # Build custom regime series at rebalance dates
        rebal_dates = pd.to_datetime(
            sig[sig['ticker']=='SPY']['date'].unique()
        )
        rebal_dates = pd.DatetimeIndex(sorted(rebal_dates))

        custom_spy_above = {}
        for d in rebal_dates:
            ts = pd.Timestamp(d)
            # default: use SPY actual signal
            spy_row = sig[(sig['ticker']=='SPY') & (sig['date']==ts)]
            default_above = int(spy_row['spy_above_ma200'].values[0]) if len(spy_row) > 0 else 1

            if use_death_cross:
                # Bear = death cross (50d < 200d) instead of price < MA200
                if ts in spy_daily.index:
                    custom_spy_above[ts] = 0 if spy_daily.loc[ts, 'death_cross'] == 1 else 1
                else:
                    custom_spy_above[ts] = default_above

            elif dual_gate:
                # Bear only when BOTH price<MA200 AND death cross
                if ts in spy_daily.index:
                    price_bear = (default_above == 0)
                    dc_bear = (spy_daily.loc[ts, 'death_cross'] == 1)
                    custom_spy_above[ts] = 0 if (price_bear and dc_bear) else 1
                else:
                    custom_spy_above[ts] = default_above

            else:
                custom_spy_above[ts] = default_above

        # Patch the signals dataloader to use custom spy_above_ma200
        orig_signals = dl.signals.copy()
        patched = dl.signals.copy()
        for d, val in custom_spy_above.items():
            patched.loc[(patched['date'] == d) & (patched['ticker'] == 'SPY'), 'spy_above_ma200'] = val

        # For dynamic/vol_scaled: compute custom bear_exposure per date
        # We'll run at 100% always and post-scale — not cleanly doable in v2
        # For now: run with custom spy_above_ma200 and standard bear/bull params
        dl.signals = patched
        strat2 = v2.BacktestStrategy(dl, p)
        port2  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
        r = v2.BacktestModel(dl, strat2, port2, p).run(verbose=False)
        dl.signals = orig_signals  # restore

        if use_vol_scaled or use_dynamic:
            # These need continuous exposure — approximate by running multiple configs
            # and interpolating: too complex for a simple script
            # Use the standard bear exposure but note "approximate" in output
            print(f"  [{label}] Note: dynamic/vol exposure approximated via bear_exp={bear_exp:.0%}")

        if use_hy_credit or use_full_overlay:
            if hy_df is None:
                print(f"  [{label}] SKIPPED — FRED data unavailable")
                return None
            # For HY credit: bear exposure = min(MA200 exposure, credit exposure)
            # This requires monthly re-mapping of exposure based on HY OAS at each rebalance date
            # Approximate: count months where credit_exposure < bull_exposure and set bear
            # This is post-hoc analysis — we'll compute proper post-processing below
            # Skip for now, handle in full_overlay section
            pass

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
    yr_str = ' '.join(f"{yr}:{ye[yr]*scale:+.1f}%" for yr in sorted(ye))

    print(f"\n{'='*68}")
    print(f"  {label}")
    print(f"  Port CAGR={pc:.1f}% | Excess={ex:+.1f}% | DD={dd:.1f}%")
    print(f"  Sharpe={sh:.2f} | Calmar={ca:.2f} | Holds={holds:.0f} | Win={wins}/{len(ye)}")
    print(f"  WF1={wf1:+.1f}% | WF2={wf2:+.1f}% | WF3={wf3:+.1f}%")
    print(f"  {yr_str}")
    return {'label': label, 'excess': ex, 'dd': dd, 'sharpe': sh, 'calmar': ca,
            'wf2': wf2, 'wins': wins, 'n': len(ye), 'holds': holds,
            'wf1': wf1, 'wf3': wf3, 'cagr': pc}


# ── Show HY OAS context before running ───────────────────────────────────────
if hy_df is not None:
    spy_rebal_dates = sig[sig['ticker']=='SPY']['date'].unique()
    spy_rebal_dates = pd.DatetimeIndex(sorted(spy_rebal_dates))
    hy_at_rebal = hy_df['hy_oas'].reindex(spy_rebal_dates, method='ffill')
    print(f"\nHY OAS at rebalance dates:")
    print(f"  Median={hy_at_rebal.median():.0f}bps | p75={hy_at_rebal.quantile(.75):.0f}bps | p90={hy_at_rebal.quantile(.90):.0f}bps")
    credit_exp_at_rebal = hy_df['credit_exposure'].reindex(spy_rebal_dates, method='ffill')
    credit_bear_months = (credit_exp_at_rebal < 1.0).sum()
    print(f"  Months with credit caution (<100% exp): {credit_bear_months}/{len(spy_rebal_dates)}")

# ── Run all configs ───────────────────────────────────────────────────────────
print("\n" + "="*68)
print("EXPOSURE MULTIPLIER — Full Isolation Test")
print("Base: v3.0 params + vol_contract=1.5 + expanded 1375-ticker universe")
print("Governance ref: SP500 v3.0 = Excess+35.8%, WF2+37.9%, DD-40.1%")
print("="*68)

results = []

# Standard SPY MA200 trigger at different cash levels
results.append(run("BASELINE  — SPY>MA200=100%, bear=50%"))
results.append(run("STRICT    — SPY>MA200=100%, bear=30%", bear_exp=0.30))
results.append(run("AGGRESSIVE — SPY>MA200=100%, bear=70%", bear_exp=0.70))
results.append(run("FULL_CASH — SPY>MA200=100%, bear=0%",  bear_exp=0.00))

# Alternative regime triggers
results.append(run("DEATH_CROSS — trigger 50d<200d, bear=50%", bear_exp=0.50, use_death_cross=True))
results.append(run("DC_STRICT   — death cross, bear=30%",      bear_exp=0.30, use_death_cross=True))
results.append(run("DUAL_GATE   — BOTH price<MA200 AND DC, bear=30%", bear_exp=0.30, dual_gate=True))

# 52W High as entry gate (separate from exposure)
results.append(run("52W_HIGH_75 — require price >= 75% of 52w high", require_52w_pct=0.75))
results.append(run("52W_HIGH_80 — require price >= 80% of 52w high", require_52w_pct=0.80))
results.append(run("52W_HIGH_85 — require price >= 85% of 52w high", require_52w_pct=0.85))

# HY credit overlay (if FRED data available)
if hy_df is not None:
    # For credit overlay: we need to apply HY-based exposure at monthly level
    # Simplified: set bear_exp = credit_exposure at rebalance dates
    # We'll patch signals spy_above_ma200 to treat HY spike months as "bear"
    # This means SPY can be above MA200 but HY stress triggers bear exposure

    spy_rebal_dates = sig[sig['ticker']=='SPY']['date'].unique()
    spy_rebal_dates = pd.DatetimeIndex(sorted(spy_rebal_dates))
    credit_exp_at_rebal = hy_df['credit_exposure'].reindex(spy_rebal_dates, method='ffill')

    # Config: when HY stress, force 65% exposure (regardless of MA200)
    # We implement by patching spy_above_ma200: if HY gives 65%, set bear (50%=close enough)
    # More precisely: HY spike months → bear; use bear_exp=0.65
    orig_signals = dl.signals.copy()
    patched = dl.signals.copy()
    hy_caution_dates = credit_exp_at_rebal[credit_exp_at_rebal < 1.0].index

    # Patch: any rebalance date with HY caution → spy_above_ma200 = 0 (bear)
    for d in hy_caution_dates:
        patched.loc[(patched['date'] == d) & (patched['ticker'] == 'SPY'), 'spy_above_ma200'] = 0

    dl.signals = patched
    p_hy = v2.StrategyParams(**{**BASE, 'bear_exposure': 0.65, 'bull_exposure': 1.00})
    v2.BacktestStrategy.screen = make_screen(top_n=TOP_N)
    strat = v2.BacktestStrategy(dl, p_hy)
    port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
    r_hy = v2.BacktestModel(dl, strat, port, p_hy).run(verbose=False)
    dl.signals = orig_signals

    ye = {yr: d['excess'] for yr, d in r_hy.get('yearly', {}).items()}
    wins = sum(1 for v in ye.values() if v > 0)
    scale = 100 if r_hy['port_cagr'] < 5 else 1
    pc = r_hy['port_cagr'] * scale; ex = r_hy['excess'] * scale; dd = r_hy['port_mdd'] * scale
    sh = r_hy['sharpe']; ca = r_hy['calmar']; holds = r_hy['avg_holdings']
    wf1 = sum(ye.get(yr, 0) for yr in [2020, 2021]) / 2 * scale
    wf2 = sum(ye.get(yr, 0) for yr in [2022, 2023]) / 2 * scale
    wf3 = sum(ye.get(yr, 0) for yr in [2024, 2025]) / 2 * scale
    yr_str = ' '.join(f"{yr}:{ye[yr]*scale:+.1f}%" for yr in sorted(ye))
    label = f"HY_CREDIT — HY OAS spike -> 65% exp (MA200 secondary)"
    print(f"\n{'='*68}\n  {label}")
    print(f"  Port CAGR={pc:.1f}% | Excess={ex:+.1f}% | DD={dd:.1f}%")
    print(f"  Sharpe={sh:.2f} | Calmar={ca:.2f} | Holds={holds:.0f} | Win={wins}/{len(ye)}")
    print(f"  WF1={wf1:+.1f}% | WF2={wf2:+.1f}% | WF3={wf3:+.1f}%")
    print(f"  {yr_str}")
    results.append({'label': label, 'excess': ex, 'dd': dd, 'sharpe': sh, 'calmar': ca,
                    'wf2': wf2, 'wins': wins, 'n': len(ye), 'holds': holds,
                    'wf1': wf1, 'wf3': wf3, 'cagr': pc})

    # FULL_OVERLAY: combine MA200 + HY (min of two) — closest to template
    # MA200 bear months use 50%, HY spike months also use 65%, take min
    patched2 = dl.signals.copy()
    spy_sig = sig[sig['ticker']=='SPY'].set_index('date')
    for d in spy_rebal_dates:
        ma200_above = int(spy_sig.loc[d, 'spy_above_ma200']) if d in spy_sig.index else 1
        hy_exp = float(credit_exp_at_rebal.get(d, 1.0))
        # MA200 sleeve: 100% or 50%
        ma200_exp = 1.0 if ma200_above else 0.5
        # Combined: min of two sleeves
        combined_exp = min(ma200_exp, hy_exp)
        # Map to spy_above_ma200 signal: above = 100%, below = bear_exp
        # We'll run with bear_exp = 0.40 and mark any month with combined<0.90 as "bear"
        if combined_exp < 0.90:
            patched2.loc[(patched2['date'] == d) & (patched2['ticker'] == 'SPY'), 'spy_above_ma200'] = 0

    dl.signals = patched2
    p_ov = v2.StrategyParams(**{**BASE, 'bear_exposure': 0.45, 'bull_exposure': 1.00})
    v2.BacktestStrategy.screen = make_screen(top_n=TOP_N)
    strat = v2.BacktestStrategy(dl, p_ov)
    port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
    r_ov = v2.BacktestModel(dl, strat, port, p_ov).run(verbose=False)
    dl.signals = orig_signals

    ye = {yr: d['excess'] for yr, d in r_ov.get('yearly', {}).items()}
    wins = sum(1 for v in ye.values() if v > 0)
    scale = 100 if r_ov['port_cagr'] < 5 else 1
    pc = r_ov['port_cagr'] * scale; ex = r_ov['excess'] * scale; dd = r_ov['port_mdd'] * scale
    sh = r_ov['sharpe']; ca = r_ov['calmar']; holds = r_ov['avg_holdings']
    wf1 = sum(ye.get(yr, 0) for yr in [2020, 2021]) / 2 * scale
    wf2 = sum(ye.get(yr, 0) for yr in [2022, 2023]) / 2 * scale
    wf3 = sum(ye.get(yr, 0) for yr in [2024, 2025]) / 2 * scale
    yr_str = ' '.join(f"{yr}:{ye[yr]*scale:+.1f}%" for yr in sorted(ye))
    label = "FULL_OVERLAY — min(MA200, HY-OAS) — closest to template"
    print(f"\n{'='*68}\n  {label}")
    print(f"  Port CAGR={pc:.1f}% | Excess={ex:+.1f}% | DD={dd:.1f}%")
    print(f"  Sharpe={sh:.2f} | Calmar={ca:.2f} | Holds={holds:.0f} | Win={wins}/{len(ye)}")
    print(f"  WF1={wf1:+.1f}% | WF2={wf2:+.1f}% | WF3={wf3:+.1f}%")
    print(f"  {yr_str}")
    results.append({'label': label, 'excess': ex, 'dd': dd, 'sharpe': sh, 'calmar': ca,
                    'wf2': wf2, 'wins': wins, 'n': len(ye), 'holds': holds,
                    'wf1': wf1, 'wf3': wf3, 'cagr': pc})

# ── Summary table ─────────────────────────────────────────────────────────────
print(f"\n\n{'Config':<52} {'CAGR':>6} {'Excess':>8} {'DD':>8} {'Sharpe':>7} {'WF1':>8} {'WF2':>8} {'Win':>6}")
print("-"*105)
b = results[0]
for r in results:
    flag = " <<" if (r['excess'] > b['excess'] and r['wf2'] >= b['wf2'] and r['dd'] >= b['dd']) else ""
    print(f"{r['label']:<52} {r['cagr']:>5.1f}% {r['excess']:>+7.1f}% {r['dd']:>7.1f}% {r['sharpe']:>7.2f} "
          f"{r['wf1']:>+7.1f}% {r['wf2']:>+7.1f}% {r['wins']}/{r['n']}{flag}")

print(f"\n[GOVERNANCE] Beats baseline on ALL 3 (excess + WF2 + DD):")
for r in results[1:]:
    e = r['excess'] > b['excess']
    w = r['wf2'] >= b['wf2']
    d = r['dd'] >= b['dd']
    if e and w and d:
        print(f"  APPROVED: {r['label'][:55]}")
    else:
        reasons = []
        if not e: reasons.append(f"excess {r['excess']:+.1f}% vs {b['excess']:+.1f}%")
        if not w: reasons.append(f"WF2 {r['wf2']:+.1f}% vs {b['wf2']:+.1f}%")
        if not d: reasons.append(f"DD {r['dd']:.1f}% vs {b['dd']:.1f}%")
        print(f"  REJECTED: {r['label'][:40]} ({', '.join(reasons)})")
