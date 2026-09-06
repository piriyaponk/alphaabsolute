"""
39_softmax_variants.py — Softmax Dummy + Risk-Flag Cap (template port)

Ported from template: models/top_country_v2/weighting_v2.py

TWO IDEAS FROM TEMPLATE:
1. softmax_with_dummy: inject "cash dummy slot" into denominator
   → portfolio naturally holds <100% when all stocks are mediocre quality
   → exposure reduces smoothly without a hard bear/bull switch
   → template uses this as "liquidity cushion"

2. risk_flag cap: classify each stock into risk tier:
   - risk=0 (normal beta): cap = base_cap (18%)
   - risk=1 (high beta 1.5-2.0): cap = min(50% × softmax_weight, 10%)
   - risk=2 (very high beta >2.0): exclude from portfolio entirely
   This way the portfolio auto-reduces concentration in volatile stocks

Both run through v2.BacktestModel → accurate Sharpe.
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd, numpy as np, importlib.util, pathlib

_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)

BASE_PARAMS = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=9.9, base_tight_max=9.9, adtv_min_m=10, cap_pct=0.18,
    bear_exposure=0.50, bull_exposure=1.00, softmax_alpha=1.0,
    start_date='2017-01-01', end_date='2026-08-20'
)
TOP_N    = 15
BASELINE = dict(excess=35.8, dd=-40.1, sharpe=1.44)

print("Loading data...")
dl  = v2.BacktestDataloader()
sig = dl.signals.copy()
sig['date'] = pd.to_datetime(sig['date'])

# Beta lookup (precomputed in signals)
beta_lookup = {}
for _, r in sig[['date','ticker','beta_252']].dropna().iterrows():
    beta_lookup[(r['date'], r['ticker'])] = r['beta_252']

# Realized 63d vol lookup (proxy for risk classification if beta not available)
close_px = sig.pivot_table(index='date', columns='ticker', values='close').sort_index()
log_ret  = np.log(close_px / close_px.shift(1))
vol_63d  = log_ret.rolling(63, min_periods=20).std() * np.sqrt(252)

def get_beta(tkr, date):
    b = beta_lookup.get((pd.Timestamp(date), tkr))
    if b is None or np.isnan(float(b if b is not None else 0)):
        # fallback: estimate from 63d vol (higher vol ~ higher beta)
        if tkr in vol_63d.columns and date in vol_63d.index:
            v = float(vol_63d.loc[date, tkr])
            return v / 0.20 if not np.isnan(v) and v > 0 else 1.0
        return 1.0
    return max(0.3, min(float(b), 4.0))

print("  Data loaded.")

_orig_screen = v2.BacktestStrategy.screen

# ── Template: softmax_with_dummy (simplified) ────────────────────────────────

def softmax_with_dummy(scores, alpha=1.0, dummy_value=None, dummy_count=1):
    """Inject dummy slot(s) into denominator — reduces total weight if stocks are mediocre."""
    x = alpha * scores
    x = x - x.max()
    e_x = np.exp(x)
    if dummy_value is None:
        dummy_value = scores.mean()
    dummy_exp = np.exp(alpha * dummy_value - alpha * scores.max())
    denom = e_x.sum() + dummy_count * dummy_exp
    return e_x / denom

# ── Template: iterative redistribution cap ───────────────────────────────────

def apply_risk_flag_cap(df, date, base_cap=0.18, risky_cap_mult=0.5, risky_cap_max=0.10,
                        beta_warn=1.5, beta_exclude=2.5, max_iter=15):
    """
    risk=2 (beta>exclude): excluded
    risk=1 (beta>warn): cap = min(risky_cap_mult × weight, risky_cap_max)
    risk=0: cap = base_cap
    Overflow redistributed proportionally to risk=0 stocks.
    """
    df = df.copy()
    betas = {r['ticker']: get_beta(r['ticker'], date) for _, r in df.iterrows()}

    # risk classification
    df['beta_']  = df['ticker'].map(betas)
    df['risk']   = df['beta_'].apply(lambda b: 2 if b>beta_exclude else 1 if b>beta_warn else 0)
    df['max_w']  = base_cap

    # Exclude risk=2
    df = df[df['risk'] < 2].copy()
    if df.empty: return df

    # Re-normalize weights after exclusion
    total = df['weight'].sum()
    if total > 1e-9: df['weight'] = df['weight'] / total * total

    # Risky cap for risk=1
    orig_w = df['weight'].copy()
    df.loc[df['risk']==1, 'max_w'] = df.loc[df['risk']==1].apply(
        lambda r: min(risky_cap_mult * r['weight'], risky_cap_max), axis=1)

    # Iterative redistribution
    weights = df['weight'].copy()
    for _ in range(max_iter):
        overflow_mask  = weights > df['max_w']
        capped = weights.where(~overflow_mask, df['max_w'])
        overflow_total = (weights[overflow_mask] - df['max_w'][overflow_mask]).sum()
        receive_mask   = (df['risk']==0) & (capped < df['max_w'])
        if overflow_total < 1e-8 or not receive_mask.any():
            weights = capped; break
        eligible = capped[receive_mask]
        redistributed = eligible * (1 + overflow_total / eligible.sum())
        weights[receive_mask] = np.minimum(redistributed, df['max_w'][receive_mask])
        weights[overflow_mask] = df['max_w'][overflow_mask]

    df['weight'] = weights
    return df[['ticker','rs_pct','weight']].copy()

# ── Screener factory ──────────────────────────────────────────────────────────

def make_screener(dummy_count=0, dummy_value_offset=0,
                  use_risk_flag=False, beta_warn=1.5, beta_exclude=2.5,
                  risky_cap_max=0.10, top_n=TOP_N):

    def patched(self, date):
        df = _orig_screen(self, date)
        if df.empty: return df
        if len(df) > top_n:
            df = df.nlargest(top_n, 'rs_pct').copy()

        # Softmax with dummy (re-compute weights from RS scores)
        if dummy_count > 0 and 'rs_pct' in df.columns:
            scores = df['rs_pct'].fillna(df['rs_pct'].mean())
            dv = scores.mean() + dummy_value_offset  # dummy = mean + offset (negative = lower quality)
            sw = softmax_with_dummy(scores, alpha=1.0,
                                    dummy_value=dv, dummy_count=dummy_count)
            df = df.copy()
            df['weight'] = sw.values
            # Keep exposure scaling (the weights now sum < 1.0 naturally)

        # Risk-flag cap
        if use_risk_flag:
            df = apply_risk_flag_cap(df, date, beta_warn=beta_warn,
                                     beta_exclude=beta_exclude, risky_cap_max=risky_cap_max)

        return df
    return patched

def run_config(label, dummy_count=0, dummy_value_offset=0,
               use_risk_flag=False, beta_warn=1.5, beta_exclude=2.5,
               risky_cap_max=0.10, cap_pct_override=0.18):
    params = BASE_PARAMS.copy()
    params['cap_pct'] = cap_pct_override

    v2.BacktestStrategy.screen = make_screener(
        dummy_count, dummy_value_offset, use_risk_flag,
        beta_warn, beta_exclude, risky_cap_max)
    try:
        p     = v2.StrategyParams(**params)
        strat = v2.BacktestStrategy(dl, p)
        port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
        r     = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
    finally:
        v2.BacktestStrategy.screen = _orig_screen

    sc   = 100 if r.get('port_cagr',0) < 5 else 1
    cagr = r.get('port_cagr', 0) * sc
    exc  = r.get('excess', 0) * sc
    dd   = r.get('port_mdd', 0) * (100 if abs(r.get('port_mdd',0)) < 1 else 1)
    sh   = r.get('sharpe', 0)
    holds = r.get('avg_holdings', 0)
    ye = {int(yr): (d['excess'] if isinstance(d,dict) else d)*100
          for yr,d in r.get('yearly',{}).items()}
    wins = sum(1 for v in ye.values() if v > 0)
    wf2  = (ye.get(2022,0)+ye.get(2023,0))/2

    ex_ok=exc>BASELINE['excess']; dd_ok=dd>BASELINE['dd']; sh_ok=sh>BASELINE['sharpe']
    gov = 'STRICT' if (ex_ok and dd_ok and sh_ok) else 'PARTIAL' if ((ex_ok or sh_ok) and dd_ok) else 'no'
    yr_str = ' '.join(f"{yr}:{'+' if v>=0 else ''}{v:.1f}%" for yr,v in sorted(ye.items()))

    print(f"\n{'='*65}")
    print(f"  {label}")
    print(f"  CAGR={cagr:.1f}% | Excess={exc:+.1f}% | DD={dd:.1f}%")
    print(f"  Sharpe={sh:.2f} | Holds={holds:.0f} | Win={wins}/{len(ye)} | WF2={wf2:+.1f}% | Gov={gov}")
    print(f"  {yr_str}")
    return dict(label=label, cagr=cagr, excess=exc, dd=dd, sharpe=sh,
                holds=holds, wins=wins, wf2=wf2, gov=gov, ye=ye)

# ── RUN ───────────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("SOFTMAX DUMMY + RISK-FLAG CAP (template port)")
print(f"{'='*65}")

results = []

# Baseline
results.append(run_config("BASELINE — current v3.1"))

# A. Softmax dummy: inject N cash slots at mean RS score
# dummy_count=1 → ~portfolio naturally 85-90% deployed
# dummy_value_offset=0 → dummy at mean RS (average quality cash)
# dummy_value_offset=-10 → dummy at below-average quality (more aggressive)
for dc, dv in [(1,0),(2,0),(3,0),(1,-5),(2,-5),(1,5)]:
    results.append(run_config(
        f"SOFTMAX DUMMY cnt={dc} offset={dv:+d}",
        dummy_count=dc, dummy_value_offset=dv))

# B. Risk-flag cap: exclude very high beta (>2.5), reduce high beta (>1.5)
for bw, be, rc in [(1.5,2.5,0.10),(1.5,2.0,0.08),(2.0,3.0,0.12)]:
    results.append(run_config(
        f"RISK FLAG (warn={bw} excl={be} risky_cap={rc*100:.0f}%)",
        use_risk_flag=True, beta_warn=bw, beta_exclude=be, risky_cap_max=rc))

# C. Softmax dummy + risk-flag cap (combined)
for dc, be in [(1,2.5),(2,2.5),(1,2.0)]:
    results.append(run_config(
        f"DUMMY cnt={dc} + RISK FLAG excl={be}",
        dummy_count=dc, use_risk_flag=True, beta_exclude=be))

# ── SUMMARY ──────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("FULL SUMMARY — SOFTMAX DUMMY + RISK-FLAG CAP")
print(f"  {'Config':<48}  {'Excess':>7}  {'DD':>7}  {'Sh':>5}  {'Holds':>5}  {'WF2':>6}  Gov")
print("-"*100)
b = results[0]
for r in results:
    print(f"  {r['label']:<48}  {r['excess']:>+6.1f}%  {r['dd']:>+6.1f}%  {r['sharpe']:>5.2f}"
          f"  {r['holds']:>5.0f}  {r['wf2']:>+5.1f}%  {r['gov']}")

print(f"\n  v3.1 LOCKED: Excess={BASELINE['excess']:+.1f}% DD={BASELINE['dd']:.1f}% Sharpe={BASELINE['sharpe']:.2f}")
print(f"  Same-run Baseline: Excess={b['excess']:+.1f}% DD={b['dd']:.1f}% Sharpe={b['sharpe']:.2f}")

gov_pass = [(r['label'],r) for r in results if r['gov'] in ('STRICT','PARTIAL')]
if gov_pass:
    print(f"\nGov PASS ({len(gov_pass)}):")
    for lbl,r in gov_pass:
        print(f"  [{r['gov']}] {lbl}: Ex={r['excess']:+.1f}% DD={r['dd']:.1f}% Sh={r['sharpe']:.2f} WF2={r['wf2']:+.1f}%")
else:
    print("\nNo absolute gov pass. Delta vs same-run baseline:")
    for r in results[1:]:
        print(f"  {r['label']}: Ex{r['excess']-b['excess']:+.1f}% DD{r['dd']-b['dd']:+.1f}% Sh{r['sharpe']-b['sharpe']:+.2f}")
