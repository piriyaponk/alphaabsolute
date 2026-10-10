"""
27_exit_signal_study.py — Exit Signal Research

Study 4 exit methods to reduce drawdown without capping weights:
  1. Buying Climax detection (volume spike + extended price = distribution warning)
  2. TD Sequential sell setup (count 9 bars = exhaustion)
  3. Trailing stop (ATR-based, % from peak)
  4. Partial take-profit ladder (sell 25% at +25%, 50% at +50%, full at +75%)

Approach:
- Run on the WORST episodes identified in script 26
- Each method applied as a "reduce weight" modifier at monthly rebalance
- Measure: how much DD would have been avoided, and how much excess is lost

Method: All signals computed monthly (at rebalance date).
  If signal fires for a stock → weight *= reduction_factor (0.0 to 1.0)
  This is conservative: we don't sell intra-month, only reduce at next rebalance.
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

# Price pivot (daily)
price_pivot = prices.pivot_table(index='date', columns='ticker', values='close')
vol_pivot   = prices.pivot_table(index='date', columns='ticker', values='volume')

TOP_N = 15
BASE_PARAMS = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=1.5, base_tight_max=9.9, adtv_min_m=10,
    cap_pct=0.18, bear_exposure=0.50, bull_exposure=1.00,
    softmax_alpha=1.0, start_date='2017-01-01', end_date='2026-08-20'
)

_orig_screen = v2.BacktestStrategy.screen

# ── Signal Computation ────────────────────────────────────────────────────────
print("Computing exit signals for all tickers x rebalance dates...")

# Month-end rebalance dates
spy_sig = sig[sig['ticker']=='SPY'].set_index('date').sort_index()
all_spy_dates = pd.DatetimeIndex(sorted(spy_sig.index))
rebal_dates = pd.DatetimeIndex(
    all_spy_dates[all_spy_dates.to_series().groupby(
        all_spy_dates.to_period('M')).transform('max') == all_spy_dates]
)

def compute_signals_for_ticker(tk, lookback=126):
    """Compute all 4 exit signals for a ticker at each rebalance date."""
    if tk not in price_pivot.columns:
        return {}
    px  = price_pivot[tk].dropna()
    vol = vol_pivot[tk].dropna() if tk in vol_pivot.columns else pd.Series(dtype=float)

    results = {}
    for rb in rebal_dates:
        if rb not in px.index:
            continue
        window = px.loc[:rb].tail(lookback)
        if len(window) < 30:
            continue
        vol_window = vol.loc[:rb].tail(lookback) if len(vol) > 0 else pd.Series(dtype=float)

        close_now = float(px.loc[rb])
        ret_1m  = (px.loc[rb] / px.loc[:rb].tail(22).iloc[0] - 1) if len(px.loc[:rb].tail(22)) >= 2 else 0
        ret_3m  = (px.loc[rb] / px.loc[:rb].tail(63).iloc[0] - 1) if len(px.loc[:rb].tail(63)) >= 2 else 0
        ret_6m  = (px.loc[rb] / px.loc[:rb].tail(126).iloc[0] - 1) if len(px.loc[:rb].tail(126)) >= 2 else 0

        # ── 1. BUYING CLIMAX (monthly bars) ─────────────────────────────────
        # Classic definition: stock up 50-100%+ in recent months
        #   + volume 2x+ recent average + now extended above 52W avg
        ma_52w    = window.tail(252).mean() if len(window) >= 50 else np.nan
        pct_above = (close_now / ma_52w - 1) if pd.notna(ma_52w) else 0
        vol_surge = 0
        if len(vol_window) >= 20:
            vol_avg20 = float(vol_window.tail(20).mean())
            vol_now   = float(vol_window.iloc[-1]) if len(vol_window) > 0 else 0
            vol_surge = vol_now / vol_avg20 if vol_avg20 > 0 else 1

        # Climax score: 0-3
        #   +1 if 1m return > 30%
        #   +1 if 3m return > 60% or 6m return > 100%
        #   +1 if volume surge > 2x AND price extended > 50% above 52W avg
        climax_score = 0
        if ret_1m   > 0.30: climax_score += 1
        if ret_3m   > 0.60 or ret_6m > 1.00: climax_score += 1
        if vol_surge > 2.0 and pct_above > 0.50: climax_score += 1

        # ── 2. TD SEQUENTIAL (weekly bars, count to 9) ──────────────────────
        # TD Buy/Sell Setup: 9 consecutive closes higher than close 4 bars ago
        # We use daily bars here (9 consecutive days close > close[4])
        td_count = 0
        daily = px.loc[:rb].tail(30)  # last 30 days
        for i in range(4, len(daily)):
            if daily.iloc[i] > daily.iloc[i-4]:
                td_count += 1
            else:
                td_count = 0
        # TD sell signal fires when count reaches 9
        td_sell = (td_count >= 9)
        td_count_val = min(td_count, 13)  # cap at 13 (perfected)

        # ── 3. ATR TRAILING STOP ────────────────────────────────────────────
        # ATR(14) = average true range over 14 days
        # Trail = peak - 3x ATR (a stock at peak within 3 ATR is safe)
        daily_ret = window.pct_change().tail(14).dropna()
        atr_pct   = daily_ret.abs().mean() * np.sqrt(252) / np.sqrt(252) if len(daily_ret) >= 5 else 0.02
        atr_daily = close_now * atr_pct / np.sqrt(252)  # ~daily ATR in $
        # Rolling peak (63d = 3 months)
        peak_63d  = float(window.tail(63).max())
        drawdown_from_peak = (close_now / peak_63d - 1) if peak_63d > 0 else 0
        # Trailing stop: if price dropped > 3x ATR from 63d peak = stop triggered
        atr_trail_triggered = (drawdown_from_peak < -3 * atr_pct / np.sqrt(252) * 20)
        # Simpler: if down >15% from 3M peak = stop
        stop_15pct = drawdown_from_peak < -0.15
        stop_20pct = drawdown_from_peak < -0.20

        # ── 4. TAKE PROFIT LADDER ───────────────────────────────────────────
        # Since we don't track entry price in monthly backtest,
        # use 6M return as proxy for "how much profit is sitting"
        # If 6M return > 50% = take some profit (reduce weight by 50%)
        # If 6M return > 100% = take more profit (reduce weight by 75%)
        # If 3M return > 40% AND vol_surge > 1.5 = hot momentum, hold
        tp_50 = ret_6m > 0.50
        tp_100 = ret_6m > 1.00
        tp_hot = (ret_3m > 0.40 and vol_surge > 1.5)  # hot = don't sell yet

        results[rb] = {
            'ret_1m': ret_1m, 'ret_3m': ret_3m, 'ret_6m': ret_6m,
            'pct_above_ma52w': pct_above,
            'vol_surge': vol_surge,
            'climax_score': climax_score,
            'td_count': td_count_val,
            'td_sell': td_sell,
            'drawdown_from_peak_3m': drawdown_from_peak,
            'atr_trail': atr_trail_triggered,
            'stop_15pct': stop_15pct,
            'stop_20pct': stop_20pct,
            'tp_50': tp_50,
            'tp_100': tp_100,
            'tp_hot': tp_hot,
        }
    return results

# Compute for all tickers in the signals universe
all_tickers = [t for t in sig['ticker'].unique() if t != 'SPY']
signal_db = {}
for i, tk in enumerate(all_tickers):
    s = compute_signals_for_ticker(tk)
    if s:
        signal_db[tk] = s
    if (i+1) % 200 == 0:
        print(f"  {i+1}/{len(all_tickers)} tickers computed...")
print(f"Signal DB: {len(signal_db)} tickers x {len(rebal_dates)} dates")

# ── Show signal stats at the BAD months (from script 26 autopsy) ──────────────
BAD_MONTHS = {
    '2018-09': "AMD/CVNA/ARWR all kill (RS crashed -33 to -41%)",
    '2025-01': "IONQ/RGTI/RKLB quantum bubble",
    '2025-10': "RGTI/QBTS/OKLO space bubble",
    '2026-07': "MXL/SNDK semis",
}

print(f"\n{'='*70}")
print("EXIT SIGNAL READINGS AT PRE-CRASH REBALANCE DATES")
print("(signals shown for the rebalance BEFORE the crash month)")
print(f"{'='*70}")

for ym, desc in BAD_MONTHS.items():
    # Find rebalance date just BEFORE the crash month
    crash_dt = pd.Timestamp(ym)
    pre_rb = max([d for d in rebal_dates if d < crash_dt], default=None)
    if pre_rb is None:
        continue
    print(f"\n[{ym}] {desc}")
    print(f"Pre-crash rebalance: {pre_rb.strftime('%Y-%m-%d')}")

    # What was held at that rebalance
    rb_sig = sig[sig['date'] == pre_rb].copy()
    cands = rb_sig[
        (rb_sig['rs_pct'] >= 80) & (rb_sig['ticker'] != 'SPY') & (rb_sig['adtv_63m'] >= 10)
    ].copy()
    spy_close = float(spy_sig.loc[pre_rb, 'close']) if pre_rb in spy_sig.index else 0
    spy_ma200 = float(spy_sig.loc[pre_rb, 'ma_200']) if pre_rb in spy_sig.index else 0
    regime = 'BULL' if spy_close > spy_ma200 else 'BEAR'
    vt_thr = 0.75 if regime == 'BULL' else 0.95
    if 'vol_trend' in cands.columns:
        cands = cands[cands['vol_trend'] >= vt_thr]
    if 'close' in cands.columns and 'ma_200' in cands.columns:
        cands = cands[cands['close'] > cands['ma_200']]
    if len(cands) > TOP_N:
        cands = cands.nlargest(TOP_N, 'rs_pct')

    print(f"  {'Ticker':>8} {'RS':>5} {'1M%':>7} {'3M%':>7} {'6M%':>7} "
          f"{'VolSrg':>7} {'Clx':>5} {'TDcnt':>6} {'DD3M':>7} {'Signal':>30}")
    print("  " + "-"*90)

    # Also compute next-month actual return for validation
    crash_rb = min([d for d in rebal_dates if d >= crash_dt], default=None)

    for _, row in cands.iterrows():
        tk = row['ticker']
        s  = signal_db.get(tk, {}).get(pre_rb, {})
        if not s:
            continue

        # Actual next month return
        actual_ret = np.nan
        if crash_rb and tk in price_pivot.columns:
            p0 = price_pivot.loc[pre_rb, tk]   if pre_rb    in price_pivot.index else np.nan
            p1 = price_pivot.loc[crash_rb, tk] if crash_rb  in price_pivot.index else np.nan
            if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                actual_ret = (p1/p0 - 1) * 100

        # Which signals would fire
        signals_fired = []
        if s.get('climax_score', 0) >= 2:
            signals_fired.append(f"CLIMAX({s['climax_score']})")
        if s.get('td_sell', False):
            signals_fired.append(f"TD9({s['td_count']})")
        if s.get('stop_15pct', False):
            signals_fired.append("STOP15%")
        if s.get('tp_100', False) and not s.get('tp_hot', False):
            signals_fired.append("TP100%")
        elif s.get('tp_50', False) and not s.get('tp_hot', False):
            signals_fired.append("TP50%")

        sig_str = "+".join(signals_fired) if signals_fired else "-"
        crash_str = f"[CRASH {actual_ret:+.0f}%]" if pd.notna(actual_ret) and actual_ret < -8 else ""

        print(f"  {tk:>8} {row['rs_pct']:>5.0f} "
              f"{s['ret_1m']*100:>+6.0f}% {s['ret_3m']*100:>+6.0f}% {s['ret_6m']*100:>+6.0f}% "
              f"{s['vol_surge']:>7.1f} {s['climax_score']:>5} {s['td_count']:>6} "
              f"{s['drawdown_from_peak_3m']*100:>+6.0f}%  "
              f"{sig_str:<22} {crash_str}")

# ── Backtest each exit method ─────────────────────────────────────────────────
print(f"\n\n{'='*70}")
print("BACKTEST — Exit Methods vs Baseline")
print(f"{'='*70}")

def make_exit_screen(method_name, climax_thr=2, td_sell=True, stop_pct=None, tp_6m=None, tp_reduce=0.5):
    """Returns a screen function that applies exit signal reductions."""
    def custom_screen(self, date):
        result = _orig_screen(self, date)
        if result.empty: return result
        if len(result) > TOP_N:
            result = result.nlargest(TOP_N, 'rs_pct').copy()
            w = result['weight'].clip(upper=self.params.cap_pct)
            result['weight'] = w / w.sum() * result['weight'].sum()
        else:
            result = result.copy()

        ts = pd.Timestamp(date)
        total_reduction = 0
        for idx, row in result.iterrows():
            tk = row.get('ticker', '')
            s  = signal_db.get(tk, {}).get(ts, {})
            if not s:
                continue
            reduce_factor = 1.0
            reasons = []

            # Climax exit
            if climax_thr and s.get('climax_score', 0) >= climax_thr:
                reduce_factor *= 0.0  # full exit on climax
                reasons.append('CLIMAX')

            # TD Sequential
            if td_sell and s.get('td_sell', False) and reduce_factor > 0:
                reduce_factor *= 0.5  # halve on TD9
                reasons.append('TD9')

            # Trailing stop
            if stop_pct and s.get(f'stop_{int(stop_pct*100)}pct', False) and reduce_factor > 0:
                reduce_factor *= 0.0  # full exit on stop
                reasons.append(f'STOP{int(stop_pct*100)}%')

            # Take profit
            if tp_6m and s.get('tp_100', False) and not s.get('tp_hot', False) and reduce_factor > 0:
                reduce_factor *= (1 - tp_reduce)
                reasons.append('TP100%')
            elif tp_6m and s.get('tp_50', False) and not s.get('tp_hot', False) and reduce_factor > 0:
                reduce_factor *= (1 - tp_reduce * 0.5)
                reasons.append('TP50%')

            if reduce_factor < 1.0:
                result.loc[idx, 'weight'] *= reduce_factor
                total_reduction += (1 - reduce_factor)

        # Renormalize weights
        w_sum = result['weight'].sum()
        if w_sum > 0:
            result['weight'] = result['weight'] / w_sum * min(w_sum, 1.0)
        return result
    return custom_screen

def run_exit_backtest(label, screen_fn, verbose=True):
    v2.BacktestStrategy.screen = screen_fn
    p     = v2.StrategyParams(**BASE_PARAMS)
    strat = v2.BacktestStrategy(dl, p)
    port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
    r = v2.BacktestModel(dl, strat, port, p).run(verbose=False)
    v2.BacktestStrategy.screen = _orig_screen

    ye    = {yr: (d['excess'] if isinstance(d, dict) else d) * 100
             for yr, d in r.get('yearly', {}).items()}
    wins  = sum(1 for v in ye.values() if v > 0)
    sc    = 100 if r['port_cagr'] < 5 else 1
    pc    = r['port_cagr']*sc; ex = r['excess']*sc; dd = r['port_mdd']*sc
    sh    = r['sharpe'];       ca = r['calmar'];     holds = r['avg_holdings']
    wf2   = (ye.get(2022, 0) + ye.get(2023, 0)) / 2

    if verbose:
        print(f"\n{'='*65}")
        print(f"  {label}")
        print(f"  CAGR={pc:.1f}% | Excess={ex:+.1f}% | DD={dd:.1f}%")
        print(f"  Sharpe={sh:.2f} | Calmar={ca:.2f} | Holds={holds:.0f} | Win={wins}/{len(ye)}")
        print(f"  WF2(2022-23)={wf2:+.1f}%")
        print(f"  {' '.join(f'{yr}:{ye[yr]:+.1f}%' for yr in sorted(ye))}")
    return {'label':label,'cagr':pc,'excess':ex,'dd':dd,'sharpe':sh,
            'calmar':ca,'wins':wins,'n':len(ye),'wf2':wf2,'holds':holds}

results = []

# Baseline
def base_screen(self, date):
    result = _orig_screen(self, date)
    if result.empty: return result
    if len(result) > TOP_N:
        result = result.nlargest(TOP_N, 'rs_pct').copy()
        w = result['weight'].clip(upper=self.params.cap_pct)
        result['weight'] = w / w.sum() * result['weight'].sum()
    return result

results.append(run_exit_backtest("BASELINE — no exit signal", base_screen))
b = results[0]

# ── Method 1: Buying Climax ───────────────────────────────────────────────────
print(f"\n{'─'*65}")
print("METHOD 1: BUYING CLIMAX EXIT")
print("Score >= 2: up >30% 1M + (up>60% 3M or >100% 6M) + optional vol surge")
results.append(run_exit_backtest("CLIMAX2 — exit if climax score >= 2",
    make_exit_screen("climax2", climax_thr=2, td_sell=False, stop_pct=None, tp_6m=None)))
results.append(run_exit_backtest("CLIMAX3 — exit if climax score = 3 (strict)",
    make_exit_screen("climax3", climax_thr=3, td_sell=False, stop_pct=None, tp_6m=None)))

# ── Method 2: TD Sequential ───────────────────────────────────────────────────
print(f"\n{'─'*65}")
print("METHOD 2: TD SEQUENTIAL (9-count daily)")
results.append(run_exit_backtest("TD9 — halve weight on TD9 sell signal",
    make_exit_screen("td9", climax_thr=None, td_sell=True, stop_pct=None, tp_6m=None)))

# ── Method 3: Trailing Stop ───────────────────────────────────────────────────
print(f"\n{'─'*65}")
print("METHOD 3: TRAILING STOP from 3M peak")
results.append(run_exit_backtest("STOP15 — exit if down >15% from 3M peak",
    make_exit_screen("stop15", climax_thr=None, td_sell=False, stop_pct=0.15, tp_6m=None)))
results.append(run_exit_backtest("STOP20 — exit if down >20% from 3M peak",
    make_exit_screen("stop20", climax_thr=None, td_sell=False, stop_pct=0.20, tp_6m=None)))

# ── Method 4: Take Profit Ladder ─────────────────────────────────────────────
print(f"\n{'─'*65}")
print("METHOD 4: TAKE PROFIT LADDER (6M return based)")
results.append(run_exit_backtest("TP25 — reduce 25% if 6M>50% or 50% if 6M>100%",
    make_exit_screen("tp25", climax_thr=None, td_sell=False, stop_pct=None, tp_6m=0.5, tp_reduce=0.25)))
results.append(run_exit_backtest("TP50 — reduce 50% if 6M>50% or 75% if 6M>100%",
    make_exit_screen("tp50", climax_thr=None, td_sell=False, stop_pct=None, tp_6m=0.5, tp_reduce=0.50)))

# ── Combo: Best of each method ────────────────────────────────────────────────
print(f"\n{'─'*65}")
print("METHOD 5: COMBOS")
results.append(run_exit_backtest("CLIMAX2 + TD9 + TP25",
    make_exit_screen("combo_light", climax_thr=2, td_sell=True, stop_pct=None, tp_6m=0.5, tp_reduce=0.25)))
results.append(run_exit_backtest("CLIMAX2 + STOP20",
    make_exit_screen("combo_stop", climax_thr=2, td_sell=False, stop_pct=0.20, tp_6m=None)))
results.append(run_exit_backtest("TD9 + TP25 + STOP20",
    make_exit_screen("combo_td_tp", climax_thr=None, td_sell=True, stop_pct=0.20, tp_6m=0.5, tp_reduce=0.25)))
results.append(run_exit_backtest("CLIMAX2 + TD9 + STOP15 + TP25 (FULL)",
    make_exit_screen("combo_full", climax_thr=2, td_sell=True, stop_pct=0.15, tp_6m=0.5, tp_reduce=0.25)))

# ── Summary table ─────────────────────────────────────────────────────────────
print(f"\n\n{'='*70}")
print("SUMMARY COMPARISON")
print(f"{'='*70}")
print(f"{'Method':<40} {'CAGR':>7} {'Excess':>8} {'DD':>8} {'Sharpe':>7} {'WF2':>8} {'Win':>5}")
print("─"*85)
for r in results:
    dd_delta  = r['dd']  - b['dd']
    ex_delta  = r['excess'] - b['excess']
    sh_delta  = r['sharpe'] - b['sharpe']
    # Governance: beats baseline on CAGR + Sharpe + excess (DD allowed slightly worse given exit adds cost)
    gov = "OK" if r['cagr'] >= b['cagr'] and r['sharpe'] >= b['sharpe'] else ("SH+" if r['sharpe'] > b['sharpe'] else "no")
    print(f"{r['label']:<40} {r['cagr']:>6.1f}% {r['excess']:>+7.1f}% {r['dd']:>7.1f}%({dd_delta:>+.1f}) "
          f"{r['sharpe']:>6.2f}({sh_delta:>+.2f}) {r['wf2']:>+7.1f}% {r['wins']}/{r['n']} {gov}")

# Find best DD reducer that doesn't kill excess
print(f"\n--- Best DD reduction with acceptable excess loss (< -5%) ---")
good = [r for r in results[1:] if r['excess'] >= b['excess'] - 5.0]
if good:
    best_dd = min(good, key=lambda x: x['dd'])
    print(f"  Winner: {best_dd['label']}")
    print(f"  DD: {b['dd']:.1f}% -> {best_dd['dd']:.1f}% ({best_dd['dd']-b['dd']:+.1f}%)")
    print(f"  Excess: {b['excess']:+.1f}% -> {best_dd['excess']:+.1f}% ({best_dd['excess']-b['excess']:+.1f}%)")
    print(f"  Sharpe: {b['sharpe']:.2f} -> {best_dd['sharpe']:.2f} ({best_dd['sharpe']-b['sharpe']:+.2f})")
else:
    print("  No method reduced DD while keeping excess within -5%")

print(f"\n--- Best Sharpe improvement ---")
best_sh = max(results[1:], key=lambda x: x['sharpe'])
print(f"  Winner: {best_sh['label']}")
print(f"  Sharpe: {b['sharpe']:.2f} -> {best_sh['sharpe']:.2f} | DD: {b['dd']:.1f}% -> {best_sh['dd']:.1f}%")

# ── Signal hit rate on actual crashes ─────────────────────────────────────────
print(f"\n{'='*70}")
print("SIGNAL HIT RATE — did signals fire BEFORE the crashes?")
print(f"{'='*70}")
print(f"\n{'Method':<20} {'Fired before crash':>20} {'Not fired':>12} {'Hit%':>8}")
print("─"*65)

# The actual killer stocks from script 26 autopsy
actual_killers = [
    ('AMD',  pd.Timestamp('2018-08-31'), -41.0),
    ('CVNA', pd.Timestamp('2018-08-31'), -34.0),
    ('ARWR', pd.Timestamp('2018-08-31'), -33.6),
    ('IONQ', pd.Timestamp('2024-12-31'), -37.8),
    ('RGTI', pd.Timestamp('2024-12-31'), -35.8),
    ('RKLB', pd.Timestamp('2024-12-31'), -29.5),
    ('OKLO', pd.Timestamp('2024-12-31'), -19.8),
    ('RGTI', pd.Timestamp('2025-09-30'), -42.2),
    ('QBTS', pd.Timestamp('2025-09-30'), -38.8),
    ('RKLB', pd.Timestamp('2025-09-30'), -33.1),
]

methods = {
    'Climax>=2':     lambda s: s.get('climax_score', 0) >= 2,
    'Climax>=3':     lambda s: s.get('climax_score', 0) >= 3,
    'TD9 sell':      lambda s: s.get('td_sell', False),
    'Stop15%':       lambda s: s.get('stop_15pct', False),
    'Stop20%':       lambda s: s.get('stop_20pct', False),
    'TP 6M>50%':     lambda s: s.get('tp_50', False) and not s.get('tp_hot', False),
    'TP 6M>100%':    lambda s: s.get('tp_100', False) and not s.get('tp_hot', False),
}

for mname, mfn in methods.items():
    fired = missed = 0
    for tk, rb, actual_ret in actual_killers:
        s = signal_db.get(tk, {}).get(rb, {})
        if not s:
            missed += 1
            continue
        if mfn(s):
            fired += 1
        else:
            missed += 1
    pct = fired / len(actual_killers) * 100
    print(f"  {mname:<20} {fired:>10}/{len(actual_killers):<8} {missed:>10}  {pct:>7.0f}%")

# Detailed: what each signal read for each killer
print(f"\n--- Per-stock signal readings before crash ---")
print(f"{'Stock':>6} {'Date':>10} {'ActRet':>7} {'Clx':>5} {'TD':>5} {'Stp15':>6} {'Stp20':>6} "
      f"{'TP50':>5} {'TP100':>6} {'1M%':>6} {'3M%':>6} {'6M%':>6} {'VS':>5}")
print("─"*90)
for tk, rb, actual_ret in actual_killers:
    s = signal_db.get(tk, {}).get(rb, {})
    if not s:
        print(f"{tk:>6} {rb.strftime('%Y-%m'):>10} {actual_ret:>+6.0f}%  NO SIGNAL DATA")
        continue
    print(f"{tk:>6} {rb.strftime('%Y-%m'):>10} {actual_ret:>+6.0f}%  "
          f"{s.get('climax_score',0):>5} "
          f"{'Y' if s.get('td_sell') else 'n':>5} "
          f"{'Y' if s.get('stop_15pct') else 'n':>6} "
          f"{'Y' if s.get('stop_20pct') else 'n':>6} "
          f"{'Y' if s.get('tp_50') else 'n':>5} "
          f"{'Y' if s.get('tp_100') else 'n':>6} "
          f"{s.get('ret_1m',0)*100:>+5.0f}% "
          f"{s.get('ret_3m',0)*100:>+5.0f}% "
          f"{s.get('ret_6m',0)*100:>+5.0f}% "
          f"{s.get('vol_surge',0):>5.1f}")
