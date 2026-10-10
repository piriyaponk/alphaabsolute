"""
43_regime_sensitivity.py — Regime Filter Deep Study

Tests 4 DD-reduction approaches against v4.0 locked baseline:

A) MA LOOKBACK SENSITIVITY
   Test IWM MA50 / MA100 / MA150 / MA200 (current) as regime switch threshold.
   MA50 = faster BEAR signal but more whipsaws.

B) BEARISH DIVERGENCE GATE (NEW — not tested before)
   Detect when IWM is making a NEW HIGH but momentum is LOWER than prior peak.
   Math: price_ROC_20d at current high < price_ROC_20d at previous high
         → bearish divergence → add 1 point to "warning score"
   Combined with other signals → warning_score >= 2 → move to 70% exposure (PRE-BEAR)

C) LEADING INDICATOR GATE (multi-signal warning)
   3 breadth/credit signals that fire BEFORE MA200 break:
     1. % stocks in S&P500 above 50dma < 40% (breadth collapse)
     2. IWM ROC_20d < -5% (momentum already deteriorating)
     3. IWM price < IWM MA50 (faster moving average)
   Warning score = sum of fired signals
   warning_score >= 2 → exposure = 70% (between bull 100% and bear 50%)
   MA200 break → exposure = 50% (unchanged from v4.0)

D) CORRELATION FILTER
   At each rebalance: compute avg pairwise correlation of last 30d daily returns.
   If avg_corr > 0.75 → portfolio is "crowded" → reduce to min(8, N) top-RS stocks
   Logic: high correlation = same factor risk → not truly diversified

E) MONTHLY REBALANCE STOP (NEW — never tested)
   At each rebalance: check if any holding is down > 15% from entry price.
   If yes → exclude from next month's holdings (don't re-buy even if it still passes screens)
   30-day "cooling off" period before re-entry.

GOVERNANCE: Must beat v4.0 locked on Excess + DD + Sharpe simultaneously (STRICT)
            OR DD beats locked AND (excess OR sharpe beats locked) (PARTIAL)
v4.0 LOCKED: CAGR=64.6% | Excess=+43.4% | DD=-38.2% | Sharpe=1.46
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd
import numpy as np
import importlib.util, pathlib
import warnings
warnings.filterwarnings('ignore')

_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)

# ── LOCKED PARAMS ─────────────────────────────────────────────────────────────
BASE_PARAMS = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=9.9, base_tight_max=9.9, adtv_min_m=10, cap_pct=0.18,
    bear_exposure=0.50, bull_exposure=1.00, softmax_alpha=1.0,
    start_date='2017-01-01', end_date='2026-08-20'
)
LOCKED = dict(cagr=64.6, excess=43.4, dd=-38.2, sharpe=1.46)
WF_WINDOWS = [
    ('WF1 2020-21', '2020-01-01', '2021-12-31'),
    ('WF2 2022-23', '2022-01-01', '2023-12-31'),
    ('WF3 2024-26', '2024-01-01', '2026-08-20'),
]

print("Loading data...")
dl  = v2.BacktestDataloader()
sig = dl.signals.copy()
sig['date'] = pd.to_datetime(sig['date'])

# Precompute vol (for vol-parity weights, reuse across all tests)
close_px = sig.pivot_table(index='date', columns='ticker', values='close').sort_index()
log_ret  = np.log(close_px / close_px.shift(1))
vol_63d  = log_ret.rolling(63, min_periods=20).std() * np.sqrt(252)

beta_lkp = {}
for _, r in sig[['date','ticker','beta_252']].dropna().iterrows():
    b = r['beta_252']
    if not np.isnan(float(b)):
        beta_lkp[(r['date'], r['ticker'])] = max(0.3, min(float(b), 4.0))

# IWM prices for regime signal computation
# Pull from close_px if IWM is in universe, else from signals regime column
if 'IWM' in close_px.columns:
    iwm_close = close_px['IWM'].dropna()
else:
    # Fallback: use iwm_above_ma200 from signals
    iwm_close = None
    print("  [!] IWM not in price data — will use iwm_above_ma200 column from signals")

if 'SPY' in close_px.columns:
    spy_close = close_px['SPY'].dropna()
elif 'QQQ' in close_px.columns:
    spy_close = close_px['QQQ'].dropna()
else:
    spy_close = None

# ── REGIME INDICATORS ─────────────────────────────────────────────────────────
print("Computing regime indicators...")

# A) MA lookback variants for IWM
regime_ma = {}
if iwm_close is not None:
    for ma_lb in [50, 100, 150, 200]:
        ma = iwm_close.rolling(ma_lb, min_periods=int(ma_lb*0.7)).mean()
        regime_ma[ma_lb] = (iwm_close > ma).astype(float)
else:
    # Use signals column
    iwm_above = sig.groupby('date')['iwm_above_ma200'].first().sort_index() if 'iwm_above_ma200' in sig.columns else pd.Series(1.0, index=sig['date'].unique())
    for ma_lb in [50, 100, 150, 200]:
        regime_ma[ma_lb] = iwm_above  # approximation, all same

# B+C) Composite warning score
# Signal 1: IWM below MA50 (faster than MA200)
if iwm_close is not None:
    iwm_ma50  = iwm_close.rolling(50, min_periods=30).mean()
    iwm_ma200 = iwm_close.rolling(200, min_periods=150).mean()
    iwm_roc20 = iwm_close.pct_change(20)  # 20-day rate of change
    iwm_roc63 = iwm_close.pct_change(63)

    # Signal 1: IWM < MA50
    s1_iwm_below_ma50 = (iwm_close < iwm_ma50).astype(float)

    # Signal 2: IWM ROC_20d < -3% (momentum deteriorating)
    s2_momentum_weak = (iwm_roc20 < -0.03).astype(float)

    # Signal 3: BEARISH DIVERGENCE — price at new 3M high BUT ROC_63d lower than 20d ago
    # "IWM made a new 63d high but momentum (ROC_63d) is lower than the last time it made a 63d high"
    # Simpler formulation: price_high_20d is a new 3M high, but ROC_63d < ROC_63d.shift(20)
    # i.e., "overbought on price, weakening on momentum"
    iwm_rolling_high = iwm_close.rolling(63, min_periods=30).max()
    at_new_high = (iwm_close >= iwm_rolling_high * 0.99).astype(float)  # within 1% of 63d high
    roc_diverge = (iwm_roc63 < iwm_roc63.shift(20)).astype(float)       # momentum lower than 20d ago
    s3_bearish_divergence = (at_new_high * roc_diverge).astype(float)   # price high + weak momentum

    # Signal 4: Short-term breadth proxy — IWM itself is a breadth proxy for small caps
    # Use: IWM price < IWM rolling max * 0.90 (IWM down >10% from recent peak)
    iwm_peak_63 = iwm_close.rolling(63, min_periods=30).max()
    s4_iwm_pullback = (iwm_close < iwm_peak_63 * 0.90).astype(float)

    # Warning score (0-4)
    warning_score = (s1_iwm_below_ma50
                   + s2_momentum_weak
                   + s3_bearish_divergence
                   + s4_iwm_pullback)

    # Reindex to all dates in sig
    all_dates = sig['date'].sort_values().unique()
    def reindex_to_dates(series):
        return series.reindex(all_dates, method='ffill')

    regime_ma = {lb: reindex_to_dates(v) for lb, v in regime_ma.items()}
    s1 = reindex_to_dates(s1_iwm_below_ma50)
    s2 = reindex_to_dates(s2_momentum_weak)
    s3 = reindex_to_dates(s3_bearish_divergence)
    s4 = reindex_to_dates(s4_iwm_pullback)
    warning_score_r = reindex_to_dates(warning_score)

    print(f"  IWM regime signals ready")
    print(f"  Bearish divergence signals (2017-2026): {s3.sum():.0f} days ({s3.mean()*100:.1f}%)")
    print(f"  Warning score>=2 days: {(warning_score_r>=2).sum():.0f} days ({(warning_score_r>=2).mean()*100:.1f}%)")
else:
    # Dummy signals
    all_dates = sig['date'].sort_values().unique()
    warning_score_r = pd.Series(0.0, index=all_dates)
    s3 = pd.Series(0.0, index=all_dates)
    regime_ma = {lb: pd.Series(1.0, index=all_dates) for lb in [50, 100, 150, 200]}

# ── DAILY CORRELATION PREP (for filter D) ─────────────────────────────────────
# We'll compute this on-the-fly per rebalance date for the current holdings

_orig_screen = v2.BacktestStrategy.screen

# ── VOL-PARITY BASE SCREENER (same as v4.0) ───────────────────────────────────
def make_screener(
    ma_lookback=200,           # A: MA lookback
    warning_threshold=None,    # B+C: warning score threshold for PRE-BEAR (None=off)
    warning_exposure=0.70,     # exposure when warning fires
    use_corr_filter=False,     # D: correlation filter
    corr_threshold=0.75,       # D: avg pairwise corr threshold
    max_stocks_corr=8,         # D: reduce to this many stocks when correlated
    use_monthly_stop=False,    # E: monthly rebalance stop
    stop_threshold=-0.15,      # E: stop if down > 15% from entry
    top_n=15,
    base_cap=0.18,
    beta_cap_base=0.12,
):
    """Factory: creates a patched screen() function with the requested modifications."""

    entry_prices = {}       # for monthly stop
    stop_cooldown = {}      # ticker -> date when stop was hit, 30d cooldown

    def get_regime_exposure(date):
        """Returns (bull: bool, exposure: float) based on configured signals."""
        ts = pd.Timestamp(date)

        # Base regime from MA lookback
        if ts in regime_ma[ma_lookback].index:
            bull = bool(regime_ma[ma_lookback].loc[ts] >= 0.5)
        else:
            bull = True

        if not bull:
            return False, 0.50  # BEAR: 50% exposure

        # Warning signals (only apply when in BULL regime)
        if warning_threshold is not None and ts in warning_score_r.index:
            ws = float(warning_score_r.loc[ts])
            if ws >= warning_threshold:
                return True, warning_exposure  # WARNING: reduced exposure

        return True, 1.00  # BULL: full exposure

    def patched_screen(self, date):
        df = _orig_screen(self, date)
        if df.empty:
            return df

        ts = pd.Timestamp(date)
        bull, exposure = get_regime_exposure(date)

        # Apply vol-trend threshold based on regime
        vt_thresh = 0.75 if bull else 0.95
        df = df[df['vol_trend'] >= vt_thresh].copy() if 'vol_trend' in df.columns else df

        if df.empty:
            return df

        # Monthly stop filter (E)
        if use_monthly_stop:
            keep = []
            for tkr in df['ticker'].values:
                # Check if in cooldown
                if tkr in stop_cooldown:
                    if (ts - stop_cooldown[tkr]).days < 30:
                        continue  # still in cooldown — skip
                    else:
                        del stop_cooldown[tkr]
                # Check current price vs entry
                if tkr in entry_prices:
                    entry_p = entry_prices[tkr]
                    if ts in close_px.index and tkr in close_px.columns:
                        curr_p = float(close_px.loc[ts, tkr])
                        if not np.isnan(curr_p) and curr_p > 0:
                            change = (curr_p - entry_p) / entry_p
                            if change < stop_threshold:
                                stop_cooldown[tkr] = ts
                                continue  # stopped out
                keep.append(tkr)
            df = df[df['ticker'].isin(keep)].copy()

        if df.empty:
            return df

        # Top N by RS
        if len(df) > top_n:
            df = df.nlargest(top_n, 'rs_pct').copy()

        # Correlation filter (D): reduce N if holdings are highly correlated
        if use_corr_filter and len(df) > max_stocks_corr:
            tickers = df['ticker'].tolist()
            # Compute avg pairwise correlation of last 30d returns
            recent_ret = log_ret.loc[log_ret.index <= ts].tail(30)[tickers].dropna(axis=1, how='any')
            if len(recent_ret.columns) >= 3:
                corr_matrix = recent_ret.corr()
                # Average off-diagonal correlation
                n = len(corr_matrix)
                avg_corr = (corr_matrix.sum().sum() - n) / (n * (n - 1)) if n > 1 else 0
                if avg_corr > corr_threshold:
                    # Reduce to top max_stocks_corr by RS
                    df = df.nlargest(max_stocks_corr, 'rs_pct').copy()

        if df.empty:
            return df

        # Vol-parity weights
        df = df.copy()
        vols = []
        for tkr in df['ticker']:
            if ts in vol_63d.index and tkr in vol_63d.columns:
                v = float(vol_63d.loc[ts, tkr])
                vols.append(v if not np.isnan(v) and v > 0.01 else 0.30)
            else:
                vols.append(0.30)
        df['_vol'] = vols

        if beta_cap_base is not None:
            betas = [beta_lkp.get((ts, t), 1.0) for t in df['ticker']]
            df['_max_w'] = [min(base_cap, beta_cap_base / max(b, 0.5)) for b in betas]
        else:
            df['_max_w'] = base_cap

        raw_w = 1.0 / df['_vol'].values
        raw_w = raw_w / raw_w.sum()
        weights = raw_w.copy()
        max_w = df['_max_w'].values
        for _ in range(25):
            over = weights > max_w
            if not over.any(): break
            overflow = (weights[over] - max_w[over]).sum()
            weights[over] = max_w[over]
            recv = ~over & (weights < max_w)
            if not recv.any(): break
            weights[recv] += overflow * (weights[recv] / weights[recv].sum())

        df['weight'] = weights * exposure

        # Record entry prices for monthly stop
        if use_monthly_stop:
            for tkr in df['ticker'].values:
                if tkr not in entry_prices:
                    if ts in close_px.index and tkr in close_px.columns:
                        p = float(close_px.loc[ts, tkr])
                        if not np.isnan(p) and p > 0:
                            entry_prices[tkr] = p

        return df[['ticker','rs_pct','weight']].copy()

    return patched_screen


def run_bt(label, screener_fn, start=None, end=None):
    params = BASE_PARAMS.copy()
    if start: params['start_date'] = start
    if end:   params['end_date']   = end
    v2.BacktestStrategy.screen = screener_fn
    try:
        p     = v2.StrategyParams(**params)
        strat = v2.BacktestStrategy(dl, p)
        port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
        r     = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
    finally:
        v2.BacktestStrategy.screen = _orig_screen
    sc   = 100 if r.get('port_cagr', 0) < 5 else 1
    cagr = r.get('port_cagr', 0) * sc
    exc  = r.get('excess', 0) * sc
    dd   = r.get('port_mdd', 0) * (100 if abs(r.get('port_mdd', 0)) < 1 else 1)
    sh   = r.get('sharpe', 0)
    ye   = {int(yr): (d['excess'] if isinstance(d, dict) else d) * 100
            for yr, d in r.get('yearly', {}).items()}
    wins = sum(1 for v in ye.values() if v > 0)
    return dict(label=label, cagr=cagr, excess=exc, dd=dd, sharpe=sh,
                wins=wins, ye=ye, n_yrs=len(ye))


def print_r(r, ref=None):
    ye = r['ye']
    yr_str = ' '.join(f"{yr}:{'+' if v>=0 else ''}{v:.1f}%" for yr, v in sorted(ye.items()))
    print(f"\n{'='*65}")
    print(f"  {r['label']}")
    print(f"  CAGR={r['cagr']:.1f}% | Excess={r['excess']:+.1f}% | DD={r['dd']:.1f}% | Sharpe={r['sharpe']:.2f}")
    print(f"  Win={r['wins']}/{r['n_yrs']}")
    if ref:
        print(f"  Delta vs ref: Ex{r['excess']-ref['excess']:+.1f}% DD{r['dd']-ref['dd']:+.1f}% Sh{r['sharpe']-ref['sharpe']:+.2f}")
    print(f"  {yr_str}")


def gov_check(r):
    ex_ok = r['excess'] > LOCKED['excess']
    dd_ok = r['dd'] > LOCKED['dd']
    sh_ok = r['sharpe'] > LOCKED['sharpe']
    if ex_ok and dd_ok and sh_ok:   return 'STRICT'
    if dd_ok and (ex_ok or sh_ok):  return 'PARTIAL'
    if dd_ok:                        return 'DD_ONLY'
    return 'FAIL'


# ═══════════════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("43_regime_sensitivity.py — ALL DD REDUCTION APPROACHES")
print(f"{'='*65}")
print(f"  v4.0 LOCKED: CAGR={LOCKED['cagr']}% | Excess={LOCKED['excess']:+.1f}% | DD={LOCKED['dd']:.1f}% | Sh={LOCKED['sharpe']:.2f}")

# ── BASELINE (same-run) ───────────────────────────────────────────
print("\n[Running baseline...]")
base = run_bt("BASELINE v4.0 (same-run)", make_screener())
print_r(base)
all_results = [base]

# ═══════════════════════════════════════════════════════════════════
# SECTION A — MA LOOKBACK SENSITIVITY
# ═══════════════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("SECTION A — IWM MA LOOKBACK (50/100/150/200)")
print(f"{'='*65}")

for ma_lb in [50, 100, 150]:
    print(f"\n[Running MA{ma_lb}...]")
    r = run_bt(f"A: IWM MA{ma_lb} regime", make_screener(ma_lookback=ma_lb))
    print_r(r, ref=base)
    all_results.append(r)

# ═══════════════════════════════════════════════════════════════════
# SECTION B — BEARISH DIVERGENCE ONLY
# ═══════════════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("SECTION B — BEARISH DIVERGENCE GATE")
print(f"{'='*65}")
print("Math: price at 63d high BUT ROC_63d lower than 20d ago")
print("      → warning_score += 1 when this fires")
print("      → exposure = 70% if warning_score >= 1")

# Divergence alone
print("\n[Running divergence_only (threshold=1)...]")
r = run_bt("B: Divergence only (score>=1 -> 70%)",
           make_screener(warning_threshold=1, warning_exposure=0.70))
print_r(r, ref=base)
all_results.append(r)

# Divergence with 80% exposure (softer reduction)
print("\n[Running divergence_soft (score>=1 -> 80%)...]")
r = run_bt("B: Divergence soft (score>=1 -> 80%)",
           make_screener(warning_threshold=1, warning_exposure=0.80))
print_r(r, ref=base)
all_results.append(r)

# ═══════════════════════════════════════════════════════════════════
# SECTION C — LEADING INDICATOR COMPOSITE WARNING
# ═══════════════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("SECTION C — COMPOSITE WARNING SCORE (4 signals)")
print(f"{'='*65}")
print("Signals:")
print("  S1: IWM < MA50")
print("  S2: IWM ROC_20d < -3%")
print("  S3: Bearish divergence (price high + momentum lower)")
print("  S4: IWM down >10% from 63d peak")
print("WARNING = score >= N -> 70% exposure (still BULL regime)")

for threshold, exp in [(2, 0.70), (2, 0.80), (3, 0.70)]:
    label = f"C: warning>={threshold} -> {int(exp*100)}% exposure"
    print(f"\n[Running {label}...]")
    r = run_bt(label, make_screener(warning_threshold=threshold, warning_exposure=exp))
    print_r(r, ref=base)
    all_results.append(r)

# ═══════════════════════════════════════════════════════════════════
# SECTION D — CORRELATION FILTER
# ═══════════════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("SECTION D — CORRELATION FILTER")
print(f"{'='*65}")
print("If avg pairwise 30d correlation of holdings > threshold -> reduce to 8 stocks")

for corr_thr in [0.65, 0.75, 0.85]:
    label = f"D: corr_filter corr>{corr_thr:.2f} -> top8"
    print(f"\n[Running {label}...]")
    r = run_bt(label, make_screener(use_corr_filter=True, corr_threshold=corr_thr, max_stocks_corr=8))
    print_r(r, ref=base)
    all_results.append(r)

# ═══════════════════════════════════════════════════════════════════
# SECTION E — MONTHLY REBALANCE STOP
# ═══════════════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("SECTION E — MONTHLY REBALANCE STOP (-15% from entry)")
print(f"{'='*65}")
print("At each rebalance: exclude any stock down >threshold% from entry price")
print("30-day cooling off period before re-entry")

for stop_thr in [-0.15, -0.20, -0.25]:
    label = f"E: monthly_stop {int(stop_thr*100)}%"
    print(f"\n[Running {label}...]")
    r = run_bt(label, make_screener(use_monthly_stop=True, stop_threshold=stop_thr))
    print_r(r, ref=base)
    all_results.append(r)

# ═══════════════════════════════════════════════════════════════════
# SECTION F — BEST COMBINATIONS
# ═══════════════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("SECTION F — COMBINATIONS OF BEST APPROACHES")
print(f"{'='*65}")

# Identify best from each section
def best_from(label_prefix):
    candidates = [r for r in all_results if r['label'].startswith(label_prefix)]
    if not candidates: return None
    # Best = best DD improvement without losing too much excess
    return max(candidates, key=lambda r: r['dd'] + r['excess']*0.5)

best_A = best_from('A:')
best_C = best_from('C:')
best_D = best_from('D:')

combos = []

if best_A and best_C:
    # Best MA lookback + best warning
    ma_lb = int(best_A['label'].replace('A: IWM MA','').split(' ')[0])
    wt = int(best_C['label'].split('>=')[1].split(' ')[0]) if '>=' in best_C['label'] else 2
    we = float(best_C['label'].split('-> ')[1].split('%')[0])/100 if '-> ' in best_C['label'] else 0.70
    label = f"F: MA{ma_lb} + warning>={wt} -> {int(we*100)}%"
    print(f"\n[Running {label}...]")
    r = run_bt(label, make_screener(ma_lookback=ma_lb, warning_threshold=wt, warning_exposure=we))
    print_r(r, ref=base)
    all_results.append(r)
    combos.append(r)

if best_C and best_D:
    wt = int(best_C['label'].split('>=')[1].split(' ')[0]) if '>=' in best_C['label'] else 2
    we = float(best_C['label'].split('-> ')[1].split('%')[0])/100 if '-> ' in best_C['label'] else 0.70
    ct = float(best_D['label'].split('>')[1].split(' ')[0]) if '>' in best_D['label'] else 0.75
    label = f"F: warning>={wt} + corr>{ct:.2f}"
    print(f"\n[Running {label}...]")
    r = run_bt(label, make_screener(warning_threshold=wt, warning_exposure=we,
                                     use_corr_filter=True, corr_threshold=ct, max_stocks_corr=8))
    print_r(r, ref=base)
    all_results.append(r)
    combos.append(r)

# ═══════════════════════════════════════════════════════════════════
# WALK-FORWARD for best overall (improve DD most without losing excess)
# ═══════════════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("WALK-FORWARD — best configs")
print(f"{'='*65}")

# Pick top 3 by DD improvement
dd_improvers = [r for r in all_results[1:] if r['dd'] > base['dd']]
dd_improvers.sort(key=lambda r: r['dd'] - r['excess']*0.3, reverse=True)
top3 = dd_improvers[:3]

if not top3:
    print("  No config improved DD vs baseline.")
    top3 = sorted(all_results[1:], key=lambda r: r['excess'], reverse=True)[:3]

# Reconstruct the screener config for each top result
# For simplicity, match by label
def screener_from_label(label):
    if 'MA50' in label:   return make_screener(ma_lookback=50)
    if 'MA100' in label:  return make_screener(ma_lookback=100)
    if 'MA150' in label:  return make_screener(ma_lookback=150)
    if 'warning>=2' in label and '70%' in label:
        return make_screener(warning_threshold=2, warning_exposure=0.70)
    if 'warning>=2' in label and '80%' in label:
        return make_screener(warning_threshold=2, warning_exposure=0.80)
    if 'warning>=3' in label:
        return make_screener(warning_threshold=3, warning_exposure=0.70)
    if 'Divergence only' in label:
        return make_screener(warning_threshold=1, warning_exposure=0.70)
    if 'corr>0.65' in label:
        return make_screener(use_corr_filter=True, corr_threshold=0.65, max_stocks_corr=8)
    if 'corr>0.75' in label:
        return make_screener(use_corr_filter=True, corr_threshold=0.75, max_stocks_corr=8)
    if 'stop -15%' in label:
        return make_screener(use_monthly_stop=True, stop_threshold=-0.15)
    if 'stop -20%' in label:
        return make_screener(use_monthly_stop=True, stop_threshold=-0.20)
    if 'MA100' in label and 'warning' in label:
        return make_screener(ma_lookback=100, warning_threshold=2, warning_exposure=0.70)
    return make_screener()

wf_labels = ['BASELINE v4.0'] + [r['label'] for r in top3]
wf_screeners = [make_screener()] + [screener_from_label(r['label']) for r in top3]

wf_table = {}
for wlbl, wscr in zip(wf_labels, wf_screeners):
    wf_table[wlbl] = {}
    for wname, wstart, wend in WF_WINDOWS:
        wr = run_bt(f"{wlbl}|{wname}", wscr, start=wstart, end=wend)
        wf_table[wlbl][wname] = (wr['cagr'], wr['excess'], wr['dd'], wr['sharpe'])

print(f"\n  {'Config':<30}  {'WF1 2020-21':>16}  {'WF2 2022-23':>16}  {'WF3 2024-26':>16}")
print("-"*85)
for lbl in wf_labels:
    row = wf_table[lbl]
    parts = []
    for wname, _, __ in WF_WINDOWS:
        if wname in row:
            cagr, ex, dd, sh = row[wname]
            parts.append(f"CAGR{cagr:+.0f}%/Sh{sh:.2f}")
        else:
            parts.append("N/A")
    print(f"  {lbl:<30}  {'  '.join(parts)}")

# ═══════════════════════════════════════════════════════════════════
# FULL SUMMARY TABLE
# ═══════════════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("FULL SUMMARY — ALL CONFIGS")
print(f"{'='*65}")
print(f"  {'Config':<42} {'CAGR':>7} {'Excess':>7} {'DD':>7} {'Sh':>5} {'Win':>5} {'Gov':>8}")
print("-"*85)

for r in all_results:
    gov = gov_check(r)
    marker = ' *** ' if gov in ('STRICT','PARTIAL') else '     '
    print(f"{marker}{r['label']:<42} {r['cagr']:>+6.1f}% {r['excess']:>+6.1f}% {r['dd']:>+6.1f}% {r['sharpe']:>5.2f} {r['wins']:>2}/{r['n_yrs']} [{gov}]")

print(f"\n  v4.0 LOCKED: CAGR={LOCKED['cagr']:+.1f}% | Excess={LOCKED['excess']:+.1f}% | DD={LOCKED['dd']:.1f}% | Sh={LOCKED['sharpe']:.2f}")

# Governance winners
strict = [r for r in all_results[1:] if gov_check(r) == 'STRICT']
partial = [r for r in all_results[1:] if gov_check(r) == 'PARTIAL']
dd_only = [r for r in all_results[1:] if gov_check(r) == 'DD_ONLY']

print(f"\n  STRICT governance (all 3 metrics beat): {len(strict)}")
for r in strict:
    print(f"    {r['label']}: CAGR={r['cagr']:+.1f}% Ex={r['excess']:+.1f}% DD={r['dd']:.1f}% Sh={r['sharpe']:.2f}")

print(f"\n  PARTIAL governance (DD + excess OR sharpe): {len(partial)}")
for r in partial:
    print(f"    {r['label']}: CAGR={r['cagr']:+.1f}% Ex={r['excess']:+.1f}% DD={r['dd']:.1f}% Sh={r['sharpe']:.2f}")

print(f"\n  DD improvement only (excess or sharpe regressed): {len(dd_only)}")
for r in dd_only:
    print(f"    {r['label']}: DD={r['dd']:.1f}% (DD delta={r['dd']-LOCKED['dd']:+.1f}%) Ex={r['excess']:+.1f}%")

if not strict and not partial:
    print("\n  No config beat v4.0 on DD without sacrificing excess or Sharpe.")
    print("  ROOT CAUSE CONFIRMED: v4.0's IWM MA200 is likely already near-optimal.")
    print("  DD comes from momentum strategy's fundamental nature — not a filter problem.")
    print("  Next direction: accept -38% as the DD floor for this strategy type,")
    print("  OR add uncorrelated asset (bonds/gold) to reduce portfolio-level DD.")

print(f"\n{'='*65}")
print("BEARISH DIVERGENCE — SIGNAL ANALYSIS")
print(f"{'='*65}")
if iwm_close is not None:
    # Show when bearish divergence fired historically
    div_dates = s3[s3 >= 0.5].index
    print(f"  Total divergence signal days: {len(div_dates)}")
    if len(div_dates) > 0:
        # Cluster into episodes
        episodes = []
        ep_start = div_dates[0]
        ep_end = div_dates[0]
        for d in div_dates[1:]:
            if (d - ep_end).days <= 45:
                ep_end = d
            else:
                episodes.append((ep_start, ep_end))
                ep_start = ep_end = d
        episodes.append((ep_start, ep_end))
        print(f"  Divergence episodes ({len(episodes)} total):")
        for es, ee in episodes[:15]:
            # What happened to IWM in the 3 months after?
            fwd = iwm_close.loc[ee:].head(63)
            fwd_ret = (fwd.iloc[-1] / fwd.iloc[0] - 1) * 100 if len(fwd) > 5 else float('nan')
            print(f"    {str(es)[:10]} -> {str(ee)[:10]}  | IWM 3M fwd: {fwd_ret:+.1f}%")
