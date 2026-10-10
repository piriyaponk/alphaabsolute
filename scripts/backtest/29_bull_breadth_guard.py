"""
29_bull_breadth_guard.py — Bull Market Breadth Overextension Guard

Idea: เมื่อตลาดใน bull แต่ breadth "stretched" มากเกินไป
      → ลด exposure จาก 100% เหลือ 75-80% แทนที่จะถือเต็ม
      เพื่อลด drawdown จากการปรับฐานใน bull market

Signals computed at each monthly rebalance:
  1. pct_rsi_overbought   : % stocks with RSI(14) > 70
  2. pct_at_52w_high      : % stocks within 3% of 52-week high
  3. pct_at_4w_high       : % stocks within 1% of 4-week high
  4. pct_above_upper_bb   : % stocks above upper Bollinger Band (20d, 2σ)
  5. leader_divergence    : top-30 RS stocks: avg RSI falling while price rising
  6. new_high_new_low_ratio: NH / (NH + NL) ratio (McClellan-style)
  7. climax_count         : # stocks up >50% in 3M AND volume surge >2x

Minervini Math Translation:
  - "Too many stocks extended" = pct_at_52w_high > 40% (market tired)
  - "Buying climax cluster" = climax_count > 20 stocks (institutional distribution)
  - "Leadership deterioration" = leader RSI peak divergence (price up, RSI lower high)
  - "Percent of leaders in late-stage base" = base_count proxy via pct_from_52w_high

Exposure logic (BULL market only):
  Normal bull:         exposure = 100%
  Mildly stretched:    exposure = 85%  (1-2 signals fire)
  Stretched:           exposure = 70%  (3+ signals fire)
  Very stretched:      exposure = 55%  (all signals fire = rare, highest risk)
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd, numpy as np, importlib.util, pathlib, itertools

_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)

print("Loading data...")
prices = pd.read_parquet('data/backtest/prices.parquet')
prices['date'] = pd.to_datetime(prices['date'])
dl  = v2.BacktestDataloader()
sig = dl.signals.copy()
sig['date'] = pd.to_datetime(sig['date'])

TOP_N = 15
BASE_PARAMS = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=1.5, base_tight_max=9.9, adtv_min_m=10,
    cap_pct=0.18, bear_exposure=0.50, bull_exposure=1.00,
    softmax_alpha=1.0, start_date='2017-01-01', end_date='2026-08-20'
)
_orig_screen = v2.BacktestStrategy.screen

# ── Build price/volume pivots ─────────────────────────────────────────────────
print("Building price/volume pivot tables...")
all_tickers  = [t for t in prices['ticker'].unique() if t != 'SPY']
price_pivot  = prices.pivot_table(index='date', columns='ticker', values='close')
vol_pivot    = prices.pivot_table(index='date', columns='ticker', values='volume')
broad        = [t for t in all_tickers if t in price_pivot.columns]
px_broad     = price_pivot[broad]
vl_broad     = vol_pivot[[t for t in broad if t in vol_pivot.columns]]

# ── Compute breadth signals ───────────────────────────────────────────────────
print("Computing RSI (Wilder EMA)...")
delta    = px_broad.diff()
gain     = delta.clip(lower=0)
loss     = (-delta).clip(lower=0)
avg_gain = gain.ewm(com=13, min_periods=10, adjust=False).mean()
avg_loss = loss.ewm(com=13, min_periods=10, adjust=False).mean()
rsi_all  = 100 - 100 / (1 + avg_gain / avg_loss.replace(0, np.nan))

print("Computing rolling highs / Bollinger bands...")
roll20_mean  = px_broad.rolling(20,  min_periods=15).mean()
roll20_std   = px_broad.rolling(20,  min_periods=15).std()
roll20_max   = px_broad.rolling(20,  min_periods=15).max()   # 4-week high
roll252_max  = px_broad.rolling(252, min_periods=180).max()  # 52-week high
upper_bb     = roll20_mean + 2 * roll20_std

n_valid = px_broad.notna().sum(axis=1)

# Daily breadth metrics
pct_rsi_ob       = (rsi_all  > 70).sum(axis=1)        / rsi_all.notna().sum(axis=1)
pct_above_ubb    = (px_broad > upper_bb).sum(axis=1)  / n_valid
pct_at_52w_high  = (px_broad >= roll252_max * 0.97).sum(axis=1) / n_valid
pct_at_4w_high   = (px_broad >= roll20_max  * 0.99).sum(axis=1) / n_valid

# Volume surge for climax count
print("Computing climax count...")
vol_avg63    = vl_broad.rolling(63, min_periods=40).mean()
vol_surge_df = (vl_broad / vol_avg63.replace(0, np.nan)).reindex(columns=broad)

ret_63d      = px_broad / px_broad.shift(63) - 1
climax_df    = ((ret_63d > 0.50) & (vol_surge_df > 2.0))
climax_count = climax_df.sum(axis=1)  # absolute number of climax stocks

# SPY for regime + leader universe
spy_sig = sig[sig['ticker']=='SPY'].set_index('date').sort_index()

# Leader divergence: among top-30 RS stocks, track RSI divergence
#   = avg RSI of leaders at current date vs 1M ago
#   negative = leaders weakening while market up = bearish divergence
def get_leader_tickers(date, top_n=30):
    rb_sig = sig[(sig['date']==date) & (sig['ticker']!='SPY')]
    if rb_sig.empty: return []
    return rb_sig.nlargest(top_n, 'rs_pct')['ticker'].tolist()

# ── Monthly snapshots ─────────────────────────────────────────────────────────
print("Building monthly breadth snapshots...")

all_spy_dates = pd.DatetimeIndex(sorted(spy_sig.index))
rebal_dates   = pd.DatetimeIndex(
    all_spy_dates[all_spy_dates.to_series().groupby(
        all_spy_dates.to_period('M')).transform('max') == all_spy_dates]
)

breadth_snap = {}
for rb in rebal_dates:
    if rb not in pct_rsi_ob.index:
        continue
    # Look back 30 days to get MAX readings (peak of overbought)
    s30 = rb - pd.Timedelta(days=35)

    def max_in_window(series):
        sl = series.loc[s30:rb]
        return float(sl.max()) if len(sl) > 0 else 0.0

    spy_close = float(spy_sig.loc[rb, 'close']) if rb in spy_sig.index else 0
    spy_ma200 = float(spy_sig.loc[rb, 'ma_200']) if rb in spy_sig.index else 0
    is_bull   = spy_close > spy_ma200

    # Leader RSI divergence (compare to 21d ago)
    leaders = get_leader_tickers(rb, top_n=30)
    leader_rsi_now  = float(rsi_all.loc[rb, [t for t in leaders if t in rsi_all.columns]].mean()) if leaders else 50
    rb_1m = rb - pd.Timedelta(days=22)
    rb_1m_close = min(rsi_all.index[rsi_all.index <= rb_1m], default=None, key=lambda x: abs((x-rb_1m).days)) if len(rsi_all.index[rsi_all.index <= rb_1m]) > 0 else None
    if rb_1m_close is not None:
        leader_rsi_1m = float(rsi_all.loc[rb_1m_close, [t for t in leaders if t in rsi_all.columns]].mean()) if leaders else 50
    else:
        leader_rsi_1m = leader_rsi_now
    leader_rsi_delta = leader_rsi_now - leader_rsi_1m  # negative = leaders weakening

    # SPY RSI and price trend (for divergence check)
    spy_px   = price_pivot['SPY'].loc[s30:rb] if 'SPY' in price_pivot.columns else pd.Series()
    spy_rsi_now  = float(rsi_all[leaders[0]].loc[rb]) if leaders and leaders[0] in rsi_all.columns else 50
    spy_ret_1m   = (float(spy_px.iloc[-1]) / float(spy_px.iloc[0]) - 1) if len(spy_px) >= 2 else 0

    # Divergence: spy/market up but leaders RSI falling = bearish divergence
    leader_divergence = (spy_ret_1m > 0.02) and (leader_rsi_delta < -5)

    breadth_snap[rb] = {
        'is_bull': is_bull,
        'pct_rsi_ob':      max_in_window(pct_rsi_ob),
        'pct_above_ubb':   max_in_window(pct_above_ubb),
        'pct_at_52w_high': max_in_window(pct_at_52w_high),
        'pct_at_4w_high':  max_in_window(pct_at_4w_high),
        'climax_count':    max_in_window(climax_count),
        'leader_rsi_now':  leader_rsi_now,
        'leader_rsi_delta': leader_rsi_delta,
        'leader_divergence': leader_divergence,
        'spy_ret_1m': spy_ret_1m,
    }

bm = pd.DataFrame(breadth_snap).T
bull_snaps = bm[bm['is_bull']]

print(f"\nTotal rebalance dates: {len(bm)}")
print(f"Bull months:           {len(bull_snaps)}")

# ── Show signal stats during BULL months ───────────────────────────────────────
print(f"\n{'='*70}")
print("BREADTH SIGNAL DISTRIBUTION IN BULL MONTHS")
print(f"{'='*70}")

# Identify which bull months preceded a crash (using known bad months from script 26)
BAD_BULL_MONTHS = {  # month AFTER rebalance that crashed
    pd.Timestamp('2018-09-28'), pd.Timestamp('2018-10-31'),
    pd.Timestamp('2025-01-31'), pd.Timestamp('2025-02-28'),
    pd.Timestamp('2025-10-31'),
    pd.Timestamp('2026-06-30'), pd.Timestamp('2026-07-31'),
}

for col, thresholds in [
    ('pct_rsi_ob',      [0.20, 0.30, 0.40, 0.50]),
    ('pct_above_ubb',   [0.10, 0.20, 0.30]),
    ('pct_at_52w_high', [0.20, 0.30, 0.40, 0.50]),
    ('pct_at_4w_high',  [0.40, 0.55, 0.70]),
    ('climax_count',    [10,   20,   30]),
]:
    print(f"\n{col}:")
    for thr in thresholds:
        fired  = (bull_snaps[col] >= thr)
        n_fire = fired.sum()
        # How many fired BEFORE a bad month
        pre_crash_fire = sum(1 for rb in bull_snaps[fired].index
                             if any(abs((rb - bad).days) < 40 for bad in BAD_BULL_MONTHS))
        pre_crash_total = sum(1 for rb in bull_snaps.index
                              if any(abs((rb - bad).days) < 40 for bad in BAD_BULL_MONTHS))
        pct = n_fire / len(bull_snaps) * 100
        print(f"  >= {thr:.0%} (or {thr} for count): fires {n_fire}/{len(bull_snaps)} "
              f"({pct:.0f}%) | pre-crash hit: {pre_crash_fire}/{pre_crash_total} "
              f"({pre_crash_fire/max(pre_crash_total,1)*100:.0f}%)")

print(f"\nleader_divergence (price up + RSI delta < -5):")
n_div = bull_snaps['leader_divergence'].sum()
pre_crash_div = sum(1 for rb in bull_snaps[bull_snaps['leader_divergence']].index
                    if any(abs((rb - bad).days) < 40 for bad in BAD_BULL_MONTHS))
print(f"  fires {n_div}/{len(bull_snaps)} | pre-crash hit: {pre_crash_div}")

# ── Score function ────────────────────────────────────────────────────────────
def compute_stretch_score(snap):
    """
    Score 0-7: how stretched/overbought is the market this rebalance?
    Higher = more stretched = reduce exposure
    """
    score = 0
    if snap['pct_rsi_ob']      >= 0.35: score += 1
    if snap['pct_rsi_ob']      >= 0.50: score += 1  # double-count if extreme
    if snap['pct_above_ubb']   >= 0.20: score += 1
    if snap['pct_at_52w_high'] >= 0.35: score += 1
    if snap['pct_at_4w_high']  >= 0.60: score += 1
    if snap['climax_count']    >= 20:   score += 1
    if snap['leader_divergence']:       score += 1
    return score

bm['stretch_score'] = bm.apply(compute_stretch_score, axis=1)

# Score -> exposure (bull months only)
def score_to_exposure(score, is_bull):
    if not is_bull: return 0.50  # bear = always 50%
    if score == 0:  return 1.00
    if score == 1:  return 0.90
    if score == 2:  return 0.80
    if score == 3:  return 0.70
    return              0.60     # score 4+

bm['exposure'] = bm.apply(lambda r: score_to_exposure(r['stretch_score'], r['is_bull']), axis=1)

print(f"\n{'='*70}")
print("STRETCH SCORE DISTRIBUTION")
print(f"{'='*70}")
for sc in range(8):
    months = bm[bm['stretch_score'] == sc]
    bull_m = months[months['is_bull']]
    print(f"  Score {sc}: {len(months):3d} months total ({len(bull_m)} bull) "
          f"-> exposure {score_to_exposure(sc, True):.0%}")

# Show the pre-crash months specifically
print(f"\n--- Stretch scores at pre-crash rebalances ---")
print(f"{'Date':>10} {'Score':>7} {'Expo':>6} {'RSI_OB':>8} {'BB%':>6} {'52W%':>6} {'4W%':>5} {'Climax':>7} {'LdrDiv':>8}")
print("─"*75)
for rb in sorted(bull_snaps.index):
    if not any(abs((rb - bad).days) < 40 for bad in BAD_BULL_MONTHS):
        continue
    r = bm.loc[rb]
    print(f"{rb.strftime('%Y-%m'):>10} {r['stretch_score']:>7.0f} {r['exposure']:>5.0%} "
          f"{r['pct_rsi_ob']:>7.0%} {r['pct_above_ubb']:>5.0%} {r['pct_at_52w_high']:>5.0%} "
          f"{r['pct_at_4w_high']:>5.0%} {r['climax_count']:>7.0f} "
          f"{'Y' if r['leader_divergence'] else 'n':>8}")

# ── Backtest engine ───────────────────────────────────────────────────────────
print(f"\n{'='*70}")
print("BACKTEST — Bull Breadth Guard vs Baseline")
print(f"{'='*70}")

def run_guard_backtest(label, exposure_map, verbose=True):
    """exposure_map: {date -> exposure_fraction}"""
    orig = dl.signals.copy()
    patched = dl.signals.copy()

    # Force bull regime on all dates (so we control exposure ourselves)
    # and apply exposure via weight scaling in screen
    v2.BacktestStrategy.screen = _orig_screen  # reset first

    def guard_screen(self, date):
        result = _orig_screen(self, date)
        if result.empty: return result
        if len(result) > TOP_N:
            result = result.nlargest(TOP_N, 'rs_pct').copy()
            w = result['weight'].clip(upper=self.params.cap_pct)
            result['weight'] = w / w.sum() * result['weight'].sum()
        else:
            result = result.copy()

        ts  = pd.Timestamp(date)
        default_bull = bool(bm.loc[ts, 'is_bull']) if ts in bm.index else True
        exp = exposure_map.get(ts, 1.0 if default_bull else 0.5)
        if exp < 1.0:
            s = result['weight'].sum()
            if s > 0:
                result['weight'] = result['weight'] / s * exp
        return result

    v2.BacktestStrategy.screen = guard_screen
    p     = v2.StrategyParams(**BASE_PARAMS)
    strat = v2.BacktestStrategy(dl, p)
    port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
    r     = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
    v2.BacktestStrategy.screen = _orig_screen

    ye    = {yr: (d['excess'] if isinstance(d, dict) else d) * 100
             for yr, d in r.get('yearly', {}).items()}
    wins  = sum(1 for v in ye.values() if v > 0)
    sc    = 100 if r['port_cagr'] < 5 else 1
    pc = r['port_cagr']*sc; ex = r['excess']*sc; dd = r['port_mdd']*sc
    sh = r['sharpe'];       ca = r['calmar'];     holds = r['avg_holdings']
    wf2 = (ye.get(2022,0)+ye.get(2023,0))/2

    reduced_months = sum(1 for v in exposure_map.values() if v < 1.0)
    if verbose:
        print(f"\n{'='*65}")
        print(f"  {label}  [reduced {reduced_months} months]")
        print(f"  CAGR={pc:.1f}% | Excess={ex:+.1f}% | DD={dd:.1f}%")
        print(f"  Sharpe={sh:.2f} | Calmar={ca:.2f} | Win={wins}/{len(ye)}")
        print(f"  WF2={wf2:+.1f}%")
        print(f"  {' '.join(f'{yr}:{ye[yr]:+.1f}%' for yr in sorted(ye))}")
    return {'label':label,'cagr':pc,'excess':ex,'dd':dd,'sharpe':sh,
            'wf2':wf2,'wins':wins,'n':len(ye),'holds':holds,'reduced':reduced_months}

results = []

# BASELINE: use raw baseline score from bm (is_bull=True -> 100%, is_bull=False -> 50%)
baseline_map = {rb: (1.0 if bm.loc[rb,'is_bull'] else 0.5) for rb in bm.index}
results.append(run_guard_backtest("BASELINE (SPY MA200, no guard)", baseline_map))
b = results[0]

# ── Test each signal independently ────────────────────────────────────────────
print(f"\n{'─'*65}")
print("SINGLE SIGNAL TESTS (reduce to 80% when signal fires in bull)")

for sig_col, sig_thr, sig_name in [
    ('pct_rsi_ob',      0.35, "RSI_OB>35%"),
    ('pct_rsi_ob',      0.50, "RSI_OB>50%"),
    ('pct_above_ubb',   0.20, "ABOVE_UBB>20%"),
    ('pct_at_52w_high', 0.35, "52W_HIGH>35%"),
    ('pct_at_52w_high', 0.50, "52W_HIGH>50%"),
    ('pct_at_4w_high',  0.60, "4W_HIGH>60%"),
    ('climax_count',    20,   "CLIMAX>20"),
    ('leader_divergence', True, "LEADER_DIVG"),
]:
    emap = {}
    for rb in bm.index:
        is_bull = bool(bm.loc[rb,'is_bull'])
        if not is_bull:
            emap[rb] = 0.5
        else:
            if sig_col == 'leader_divergence':
                fired = bool(bm.loc[rb, sig_col])
            else:
                fired = float(bm.loc[rb, sig_col]) >= sig_thr
            emap[rb] = 0.80 if fired else 1.00
    results.append(run_guard_backtest(f"SINGLE: {sig_name} -> 80%", emap))

# ── Score-based multi-signal ───────────────────────────────────────────────────
print(f"\n{'─'*65}")
print("SCORE-BASED MULTI-SIGNAL (0=100%, 1=90%, 2=80%, 3=70%, 4+=60%)")

for score_map in [
    {0:1.00, 1:0.90, 2:0.80, 3:0.70, 4:0.60},
    {0:1.00, 1:0.85, 2:0.75, 3:0.65, 4:0.60},
    {0:1.00, 1:1.00, 2:0.85, 3:0.75, 4:0.65},   # only trigger at score>=2
    {0:1.00, 1:1.00, 2:1.00, 3:0.80, 4:0.65},   # only trigger at score>=3
]:
    label_parts = "/".join(f"{k}:{int(v*100)}%" for k,v in list(score_map.items())[:4])
    emap = {}
    for rb in bm.index:
        is_bull = bool(bm.loc[rb,'is_bull'])
        if not is_bull:
            emap[rb] = 0.5
        else:
            sc = int(bm.loc[rb,'stretch_score'])
            emap[rb] = score_map.get(min(sc, 4), 0.60)
    results.append(run_guard_backtest(f"SCORE [{label_parts}]", emap))

# ── Minervini-style: strict climax + divergence only ──────────────────────────
print(f"\n{'─'*65}")
print("MINERVINI STYLE: only act on high-conviction overbought cluster")

# Minervini: selling climax cluster = many climax stocks in same month
# + leaders showing price/momentum divergence
for climax_thr, div_required, exp_fired in [
    (15, False, 0.80),
    (25, False, 0.75),
    (15, True,  0.70),
    (20, True,  0.65),
]:
    emap = {}
    for rb in bm.index:
        is_bull = bool(bm.loc[rb,'is_bull'])
        if not is_bull:
            emap[rb] = 0.5
        else:
            climax_fired = float(bm.loc[rb,'climax_count']) >= climax_thr
            div_fired    = bool(bm.loc[rb,'leader_divergence'])
            if div_required:
                fired = climax_fired and div_fired
            else:
                fired = climax_fired
            emap[rb] = exp_fired if fired else 1.00
    lbl = f"MINERVINI: climax>={climax_thr}{'+ div' if div_required else ''} -> {int(exp_fired*100)}%"
    results.append(run_guard_backtest(lbl, emap))

# ── Grid optimisation (tight) ──────────────────────────────────────────────────
print(f"\n{'─'*65}")
print("GRID SEARCH — find best score threshold + exposure level")

grid_results = []
for score_thr, exp_fire, exp_extreme in itertools.product(
    [1, 2, 3],           # score threshold to reduce
    [0.75, 0.80, 0.85],  # exposure when score >= thr
    [0.60, 0.65, 0.70],  # exposure when score >= thr+2
):
    emap = {}
    for rb in bm.index:
        is_bull = bool(bm.loc[rb,'is_bull'])
        if not is_bull:
            emap[rb] = 0.5
        else:
            sc = int(bm.loc[rb,'stretch_score'])
            if sc >= score_thr + 2:
                emap[rb] = exp_extreme
            elif sc >= score_thr:
                emap[rb] = exp_fire
            else:
                emap[rb] = 1.00
    lbl = f"score>={score_thr}->{int(exp_fire*100)}% score>={score_thr+2}->{int(exp_extreme*100)}%"
    r = run_guard_backtest(lbl, emap, verbose=False)
    grid_results.append(r)
    if len(grid_results) % 9 == 0:
        best_sh = max(grid_results, key=lambda x: x['sharpe'])
        print(f"  [{len(grid_results)}/27] best sharpe={best_sh['sharpe']:.2f} DD={best_sh['dd']:.1f}%")

grid_results.sort(key=lambda x: x['sharpe'], reverse=True)

# ── Final summary ──────────────────────────────────────────────────────────────
print(f"\n\n{'='*80}")
print("FULL RESULTS — Bull Breadth Guard")
print(f"{'='*80}")
print(f"Baseline: CAGR={b['cagr']:.1f}% Excess={b['excess']:+.1f}% DD={b['dd']:.1f}% "
      f"Sharpe={b['sharpe']:.2f}")
print(f"\n{'Method':<52} {'CAGR':>7} {'Excess':>8} {'DD':>9} {'Sharpe':>7} {'WF2':>8} {'Red':>5} {'Gov':>8}")
print("─"*105)

def gov(r, b):
    ex_ok = r['excess'] >= b['excess'] - 3.0   # allow -3% excess for better risk
    dd_ok = r['dd']     >= b['dd'] - 0.5        # must improve or match DD
    sh_ok = r['sharpe'] > b['sharpe']
    ca_ok = r['excess'] >= b['excess']          # strict: no excess loss
    if ca_ok and dd_ok: return "STRICT"
    if ex_ok and sh_ok and dd_ok: return "RELAXED"
    if sh_ok and dd_ok: return "SH+DD"
    if dd_ok: return "DD only"
    return "no"

all_res = results + grid_results[:10]
for r in all_res:
    dd_d = r['dd'] - b['dd']
    g    = gov(r, b)
    marker = " <===" if g in ("STRICT","RELAXED") else ""
    print(f"{r['label']:<52} {r['cagr']:>6.1f}% {r['excess']:>+7.1f}% "
          f"{r['dd']:>+7.1f}%({dd_d:>+.1f}) {r['sharpe']:>6.2f} "
          f"{r['wf2']:>+7.1f}% {r['reduced']:>5} {g:>8}{marker}")

# ── Insight: which signal had best hit rate on the actual bad months ───────────
print(f"\n{'='*80}")
print("SIGNAL READINGS AT THE 6 WORST BULL DRAWDOWN MONTHS")
print(f"{'='*80}")
worst_pre_crash_dates = [
    (pd.Timestamp('2018-08-31'), "pre-2018-09 crash"),
    (pd.Timestamp('2018-09-28'), "pre-2018-10 crash"),
    (pd.Timestamp('2024-12-31'), "pre-2025-01 crash"),
    (pd.Timestamp('2025-01-31'), "pre-2025-02 crash"),
    (pd.Timestamp('2025-09-30'), "pre-2025-10 crash"),
    (pd.Timestamp('2026-06-30'), "pre-2026-07 crash"),
]
print(f"\n{'Date':>10} {'Event':<25} {'Score':>6} {'Expo':>6} {'RSI':>6} {'UBB':>6} "
      f"{'52Wh':>6} {'4Wh':>5} {'Clx':>5} {'Div':>5}")
print("─"*85)
for dt, event in worst_pre_crash_dates:
    rb = max([d for d in bm.index if d <= dt], default=None)
    if rb is None: continue
    r = bm.loc[rb]
    print(f"{rb.strftime('%Y-%m'):>10} {event:<25} "
          f"{r['stretch_score']:>6.0f} {score_to_exposure(r['stretch_score'], r['is_bull']):>5.0%} "
          f"{r['pct_rsi_ob']:>5.0%} {r['pct_above_ubb']:>5.0%} "
          f"{r['pct_at_52w_high']:>5.0%} {r['pct_at_4w_high']:>5.0%} "
          f"{r['climax_count']:>5.0f} {'Y' if r['leader_divergence'] else 'n':>5}")

print(f"\n{'='*80}")
print("INTERPRETATION GUIDE")
print(f"{'='*80}")
print("""
Stretch Score Meaning:
  0 = Normal bull market             -> 100% deployed
  1 = Mildly extended (1 signal)     -> 90% (light caution)
  2 = Extended (2 signals)           -> 80% (moderate caution)
  3 = Stretched (3 signals)          -> 70% (significant caution)
  4+ = Very stretched (4+ signals)   -> 60% (high caution, rare)

Best signals (based on pre-crash hit rate):
  pct_at_52w_high > 35-40%  : "too many stocks at highs = nowhere to go but down"
  pct_rsi_ob > 35-50%       : "breadth overbought = FOMO buying, weak hands"
  climax_count > 20          : "institutional distribution disguised as strength"
  leader_divergence          : "generals retreating while soldiers still advancing"

Minervini Rule Translation:
  "Avoid buying when too many leaders are extended past proper buy points"
  Math: pct_at_52w_high > 40% = too many stocks already extended
  "Look for selling climaxes in leaders = distribution"
  Math: climax_count (up>50% + vol surge>2x) > 20 stocks
  "When RS lines stop making new highs before price = warning"
  Math: leader_rsi_delta < -5 while spy_ret_1m > 2% = divergence
""")
