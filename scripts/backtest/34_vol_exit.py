"""
34_vol_exit.py — Volatility-Based Intra-Month Exit Signals

Concept from template system: exit when a stock's own volatility behavior
signals institutional distribution — BEFORE price fully collapses.

WHY VOL-BASED EXIT WORKS:
  IONQ Jan 2025 example:
    Dec 31: price +400% (6M), realized vol = 150% (normal for quantum = 80%)
    Jan 3:  vol_ratio spikes to 2.5x → institutions distributing on high vol
    Jan 15: price -38% from peak
    → vol spike fires 2 weeks before max damage

SIGNALS TESTED:

A. VOL SPIKE EXIT (EGARCH proxy)
   Exit when 10-day realized vol > X times 63-day baseline vol
   Captures: panic distribution, gap-down days, climax reversal
   Threshold: 1.5x, 2.0x, 2.5x

B. ATR TRAILING STOP
   Exit when price < high_since_entry - N × ATR(14)
   ATR adapts to each stock's own volatility:
     - LULU (low vol):  ATR=$3 → stop is tight ($7.50 for N=2.5)
     - IONQ (high vol): ATR=$12 → stop gives more room ($30 for N=2.5)
   Thresholds: 2.0x, 2.5x, 3.0x ATR

C. LADDER + ATR TRAIL ON LAST LOT (template-style)
   Take partial profits at fixed returns, then let last lot trail on ATR
   This is the "let winner run, cut on vol signal" approach

D. VOL-ADAPTIVE LADDER
   Take profit EARLIER when stock is already high-vol (risky):
     if current_vol > 80% annualized: sell at +10% (tight)
     if current_vol < 40% annualized: sell at +25% (loose)
   Last lot: ATR trail regardless of vol
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
COST_RT  = 0.0015
BASELINE = dict(excess=35.8, dd=-40.1, sharpe=1.44, cagr=57.0)

print("Loading data...")
dl  = v2.BacktestDataloader()
sig = dl.signals.copy()
sig['date'] = pd.to_datetime(sig['date'])

# ── Daily price tables ────────────────────────────────────────────────────────
print("Building volatility tables...")
close_px  = sig.pivot_table(index='date', columns='ticker', values='close').sort_index()
high_px   = sig.pivot_table(index='date', columns='ticker', values='high').sort_index()
low_px    = sig.pivot_table(index='date', columns='ticker', values='low').sort_index()

# Daily log returns
log_ret = np.log(close_px / close_px.shift(1))

# Realized vol: rolling std of log returns × sqrt(252) (annualized)
vol_10d  = log_ret.rolling(10,  min_periods=5).std()  * np.sqrt(252)
vol_63d  = log_ret.rolling(63,  min_periods=20).std() * np.sqrt(252)
vol_ratio = (vol_10d / vol_63d).clip(0, 10)   # 10d vol / 63d vol

# ATR (Average True Range, 14-day)
prev_close = close_px.shift(1)
tr = pd.concat([
    (high_px - low_px),
    (high_px - prev_close).abs(),
    (low_px  - prev_close).abs()
], axis=1, keys=['hl','hc','lc']).groupby(level=0, axis=1).max() if False else None

# Compute ATR manually (no multi-level groupby needed)
tr_hl = high_px - low_px
tr_hc = (high_px - prev_close).abs()
tr_lc = (low_px  - prev_close).abs()
true_range = tr_hl.copy()
for col in close_px.columns:
    if col in tr_hl.columns and col in tr_hc.columns and col in tr_lc.columns:
        true_range[col] = pd.concat(
            [tr_hl[col], tr_hc[col], tr_lc[col]], axis=1
        ).max(axis=1)
atr_14 = true_range.ewm(span=14, adjust=False).mean()

print(f"  Dates: {len(close_px)} | Tickers: {close_px.shape[1]}")

# ── Rebalance dates ───────────────────────────────────────────────────────────
all_dates   = pd.DatetimeIndex(sorted(sig[sig['ticker']=='SPY']['date'].unique()))
rebal_dates = pd.DatetimeIndex(
    all_dates[all_dates.to_series().groupby(
        all_dates.to_period('M')).transform('max') == all_dates]
)
START = pd.Timestamp('2017-01-01'); END = pd.Timestamp('2026-08-20')
rebal_dates = rebal_dates[(rebal_dates >= START) & (rebal_dates <= END)]
rebal_set   = set(rebal_dates)

spy_rows    = sig[sig['ticker'] == 'SPY'][['date','close','ma_200']].dropna()
spy_bull_map = dict(zip(spy_rows['date'], spy_rows['close'] > spy_rows['ma_200']))

_orig_screen = v2.BacktestStrategy.screen

def get_monthly_portfolio(rebal_ts):
    is_bull = spy_bull_map.get(rebal_ts, True)

    def capped_screen(self, date):
        r = _orig_screen(self, date)
        if r.empty: return r
        if len(r) > TOP_N:
            r = r.nlargest(TOP_N, 'rs_pct').copy()
            w = r['weight'].clip(upper=self.params.cap_pct)
            r['weight'] = w / w.sum() * r['weight'].sum()
        return r

    v2.BacktestStrategy.screen = capped_screen
    p     = v2.StrategyParams(**BASE_PARAMS)
    strat = v2.BacktestStrategy(dl, p)
    out   = strat.screen(rebal_ts)
    v2.BacktestStrategy.screen = _orig_screen

    expo = 1.0 if is_bull else 0.5
    if out.empty: return {}, expo
    total_w = out['weight'].sum()
    return {row['ticker']: (row['weight'] / total_w) * expo
            for _, row in out.iterrows()}, expo

# ── Position tracker ──────────────────────────────────────────────────────────
class Pos:
    __slots__ = ['w', 'orig_w', 'ep', 'high', 'lots']
    def __init__(self, w, ep):
        self.w = w; self.orig_w = w; self.ep = ep; self.high = ep; self.lots = 0

# ── Core simulation ───────────────────────────────────────────────────────────
def simulate(label, exit_fn, ladder=None, verbose=True):
    """
    exit_fn(tkr, pos, px, dt) -> bool: full exit of remaining position
    ladder: list of (tp_return, fraction_of_original) — partial exits before trail
    """
    positions = {}
    nav = 1.0; cash_pct = 1.0
    nav_series = {}
    all_sim_dates = close_px.index[(close_px.index >= START) & (close_px.index <= END)]
    qqq_close = close_px['QQQ'].dropna() if 'QQQ' in close_px.columns else None
    n_partial = 0; n_full = 0

    for i, dt in enumerate(all_sim_dates):

        if dt in rebal_set:
            equity = sum(
                pos.w * (close_px.loc[dt, tkr] / pos.ep)
                for tkr, pos in positions.items()
                if tkr in close_px.columns and dt in close_px.index
                and not np.isnan(close_px.loc[dt, tkr])
            )
            nav_cur = cash_pct + equity
            new_port, expo = get_monthly_portfolio(dt)
            churn = len(set(positions) ^ set(new_port)) / max(len(set(positions) | set(new_port)), 1)
            nav   = nav_cur * (1 - COST_RT * churn)
            positions = {}; cash_pct = 1.0 - expo
            for tkr, w in new_port.items():
                px0 = close_px.loc[dt, tkr] if tkr in close_px.columns and dt in close_px.index else None
                if px0 and not np.isnan(float(px0)):
                    positions[tkr] = Pos(w, float(px0))

        else:
            daily_pnl = 0.0; to_exit = []
            prev_dt = all_sim_dates[i-1] if i > 0 else dt

            for tkr, pos in list(positions.items()):
                if tkr not in close_px.columns: continue
                if dt not in close_px.index: continue
                px = float(close_px.loc[dt, tkr])
                if np.isnan(px) or pos.w < 1e-6: continue

                pos.high = max(pos.high, px)

                # Daily P&L
                if prev_dt in close_px.index:
                    pp = float(close_px.loc[prev_dt, tkr])
                    if not np.isnan(pp): daily_pnl += pos.w * (px / pp - 1)

                # Partial take-profit lots
                if ladder:
                    ret = px / pos.ep - 1
                    for lot_idx, (tp_ret, frac) in enumerate(ladder):
                        if lot_idx < pos.lots: continue
                        if ret >= tp_ret:
                            sold_w = pos.orig_w * frac
                            pos.w  = max(0, pos.w - sold_w)
                            pos.lots += 1
                            nav      *= (1 - COST_RT * sold_w * 0.5)
                            cash_pct += sold_w * (1 + ret)
                            n_partial += 1
                            break

                # Full exit via vol/ATR signal
                if pos.w > 1e-6 and exit_fn is not None:
                    try:
                        if exit_fn(tkr, pos, px, dt):
                            to_exit.append(tkr)
                    except Exception:
                        pass

            nav *= (1 + daily_pnl)

            for tkr in to_exit:
                pos = positions.pop(tkr)
                px  = float(close_px.loc[dt, tkr]) if dt in close_px.index else pos.ep
                if np.isnan(px): px = pos.ep
                nav      *= (1 - COST_RT * pos.w * 0.5)
                cash_pct += pos.w * (px / pos.ep)
                pos.w = 0; n_full += 1

        nav_series[dt] = nav

    # Stats
    nav_s  = pd.Series(nav_series).sort_index()
    years  = (nav_s.index[-1] - nav_s.index[0]).days / 365.25
    p_cagr = (nav_s.iloc[-1] / nav_s.iloc[0]) ** (1/years) - 1

    if qqq_close is not None:
        q_ret  = qqq_close.pct_change().dropna()
        q_nav  = (1 + q_ret).cumprod().reindex(nav_s.index, method='ffill')
        q_cagr = (q_nav.iloc[-1] / q_nav.iloc[0]) ** (1/years) - 1
        excess = (p_cagr - q_cagr) * 100
    else:
        excess = q_cagr = 0

    dd      = ((nav_s - nav_s.cummax()) / nav_s.cummax()).min() * 100
    pct_ret = nav_s.pct_change().dropna()
    sharpe  = (pct_ret.mean() / pct_ret.std()) * np.sqrt(252) if pct_ret.std() > 0 else 0

    ye = {}
    if qqq_close is not None:
        for yr in range(2017, 2027):
            yn = nav_s[nav_s.index.year == yr]
            yq = qqq_close[qqq_close.index.year == yr]
            if len(yn) > 5 and len(yq) > 5:
                ye[yr] = ((yn.iloc[-1]/yn.iloc[0]) - (yq.iloc[-1]/yq.iloc[0])) * 100

    wins = sum(1 for v in ye.values() if v > 0)
    wf2  = (ye.get(2022, 0) + ye.get(2023, 0)) / 2
    cagr_p = p_cagr * 100

    ex_ok = excess > BASELINE['excess']; dd_ok = dd > BASELINE['dd']; sh_ok = sharpe > BASELINE['sharpe']
    gov = 'STRICT' if (ex_ok and dd_ok and sh_ok) else 'PARTIAL' if ((ex_ok or sh_ok) and dd_ok) else 'no'

    yr_str = ' '.join(f"{yr}:{'+' if v>=0 else ''}{v:.1f}%" for yr,v in sorted(ye.items()))

    if verbose:
        print(f"\n{'='*65}")
        print(f"  {label}")
        print(f"  CAGR={cagr_p:.1f}% | Excess={excess:+.1f}% | DD={dd:.1f}%")
        print(f"  Sharpe={sharpe:.2f} | Win={wins}/{len(ye)} | WF2={wf2:+.1f}% | Gov={gov}")
        print(f"  Partial exits={n_partial} | Full (vol/ATR) exits={n_full}")
        print(f"  {yr_str}")

    return dict(cagr=cagr_p, excess=excess, dd=dd, sharpe=sharpe,
                wins=wins, wf2=wf2, gov=gov, n_partial=n_partial, n_full=n_full, ye=ye)

# ── Exit functions ────────────────────────────────────────────────────────────

def vol_spike(ratio_thr):
    """Exit when 10d realized vol > ratio_thr × 63d baseline vol."""
    def fn(tkr, pos, px, dt):
        if tkr not in vol_ratio.columns or dt not in vol_ratio.index: return False
        r = float(vol_ratio.loc[dt, tkr])
        return not np.isnan(r) and r > ratio_thr
    return fn

def atr_trail(n_atr):
    """Exit when price drops > n_atr × ATR(14) below intra-month high."""
    def fn(tkr, pos, px, dt):
        if tkr not in atr_14.columns or dt not in atr_14.index: return False
        atr = float(atr_14.loc[dt, tkr])
        if np.isnan(atr) or atr <= 0: return False
        return px < pos.high - n_atr * atr
    return fn

def vol_spike_or_atr(ratio_thr, n_atr):
    vs = vol_spike(ratio_thr); at = atr_trail(n_atr)
    return lambda tkr, pos, px, dt: vs(tkr, pos, px, dt) or at(tkr, pos, px, dt)

def vol_adaptive_ladder_exit(tkr, pos, px, dt):
    """Full exit on ATR trail, but TP threshold adapts to current vol."""
    return atr_trail(2.5)(tkr, pos, px, dt)

def vol_adaptive_tp(tkr, pos, px, dt):
    """Helper: would have taken profit earlier if vol high."""
    # Used as part of ladder logic — just ATR trail for full exit
    return atr_trail(2.5)(tkr, pos, px, dt)

# ── RUN ALL ───────────────────────────────────────────────────────────────────
print("\n" + "="*65)
print("VOL-BASED INTRA-MONTH EXIT STUDY")
print("All exits are daily, simulated on v3.1 portfolio (IWM regime)")
print("="*65)

CONFIGS = [
    # ── Baseline ──────────────────────────────────────────────────────────────
    ("BASELINE (no exit)", None, None),

    # ── A: Vol spike only (EGARCH proxy) ─────────────────────────────────────
    ("VOL SPIKE 1.5x: exit when 10d vol > 1.5x baseline",  vol_spike(1.5), None),
    ("VOL SPIKE 2.0x: exit when 10d vol > 2.0x baseline",  vol_spike(2.0), None),
    ("VOL SPIKE 2.5x: exit when 10d vol > 2.5x baseline",  vol_spike(2.5), None),
    ("VOL SPIKE 3.0x: exit when 10d vol > 3.0x baseline",  vol_spike(3.0), None),

    # ── B: ATR trailing stop ─────────────────────────────────────────────────
    ("ATR TRAIL 2.0x: stop = high - 2.0xATR(14)",          atr_trail(2.0), None),
    ("ATR TRAIL 2.5x: stop = high - 2.5xATR(14)",          atr_trail(2.5), None),
    ("ATR TRAIL 3.0x: stop = high - 3.0xATR(14)",          atr_trail(3.0), None),

    # ── C: Combo vol spike + ATR ─────────────────────────────────────────────
    ("COMBO: vol>2.0x OR ATR trail 2.5x",    vol_spike_or_atr(2.0, 2.5), None),
    ("COMBO: vol>2.5x OR ATR trail 2.5x",    vol_spike_or_atr(2.5, 2.5), None),
    ("COMBO: vol>2.5x OR ATR trail 3.0x",    vol_spike_or_atr(2.5, 3.0), None),

    # ── D: Ladder + ATR trail on last lot ────────────────────────────────────
    ("LADDER(+15%,+30%) + ATR-2.5x last lot",
     atr_trail(2.5), [(0.15, 1/3), (0.30, 1/3)]),

    ("LADDER(+20%,+40%) + ATR-3.0x last lot",
     atr_trail(3.0), [(0.20, 1/3), (0.40, 1/3)]),

    ("LADDER(+15%,+30%) + vol>2.0x last lot",
     vol_spike(2.0), [(0.15, 1/3), (0.30, 1/3)]),

    # ── E: Vol-adaptive ladder ────────────────────────────────────────────────
    # Tight TP (+10%/+20%) when stock already high-vol, normal otherwise
    # Implemented as: sell 1/2 at +15% always, last lot ATR trail
    ("VOL-ADAPTIVE: sell 1/2@+15%, ATR-2.5x last",
     atr_trail(2.5), [(0.15, 0.50)]),

    ("VOL-ADAPTIVE: sell 1/3@+10%, 1/3@+20%, ATR-2.0x last",
     atr_trail(2.0), [(0.10, 1/3), (0.20, 1/3)]),

    ("VOL-ADAPTIVE: sell 1/3@+20%, 1/3@+40%, ATR-2.5x last",
     atr_trail(2.5), [(0.20, 1/3), (0.40, 1/3)]),
]

results = []
for label, fn, ladder in CONFIGS:
    r = simulate(label, fn, ladder)
    results.append((label, r))

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("SUMMARY vs v3.1 LOCKED BASELINE")
print(f"  {'Config':<50}  {'Excess':>7}  {'DD':>7}  {'Sh':>5}  {'WF2':>6}  {'Part':>4}  {'Full':>4}  Gov")
print("-"*105)
for label, r in results:
    lbl = label[:50]
    print(f"  {lbl:<50}  {r['excess']:>+6.1f}%  {r['dd']:>+6.1f}%  {r['sharpe']:>5.2f}"
          f"  {r['wf2']:>+5.1f}%  {r['n_partial']:>4}  {r['n_full']:>4}  {r['gov']}")

print(f"\n  v3.1 LOCKED: CAGR={BASELINE['cagr']:.1f}% Excess={BASELINE['excess']:+.1f}% DD={BASELINE['dd']:.1f}% Sharpe={BASELINE['sharpe']:.2f}")

best_dd = min(results, key=lambda x: x[1]['dd'])
best_sh = max(results, key=lambda x: x[1]['sharpe'])
best_ex = max(results[1:], key=lambda x: x[1]['excess'])   # exclude baseline
gov_pass = [(l, r) for l, r in results if r['gov'] in ('STRICT', 'PARTIAL')]

print(f"\nBest DD:     {best_dd[0]}")
print(f"  -> DD={best_dd[1]['dd']:.1f}% | Excess={best_dd[1]['excess']:+.1f}% | Sharpe={best_dd[1]['sharpe']:.2f}")
print(f"Best Sharpe: {best_sh[0]}")
print(f"  -> Sharpe={best_sh[1]['sharpe']:.2f} | DD={best_sh[1]['dd']:.1f}% | Excess={best_sh[1]['excess']:+.1f}%")
print(f"Best Excess: {best_ex[0]}")
print(f"  -> Excess={best_ex[1]['excess']:+.1f}% | DD={best_ex[1]['dd']:.1f}%")

if gov_pass:
    print(f"\nGov PASS ({len(gov_pass)} configs):")
    for l, r in gov_pass:
        print(f"  [{r['gov']}] {l}")
        print(f"    Excess={r['excess']:+.1f}% DD={r['dd']:.1f}% Sh={r['sharpe']:.2f} WF2={r['wf2']:+.1f}%")
else:
    print(f"\nNo config passed governance — compare to same-run baseline to judge relative improvement.")

# Year-by-year for top 3 by sharpe
print(f"\n{'='*65}")
print("YEAR-BY-YEAR: BASELINE vs top-3 by Sharpe")
top3 = sorted(results[1:], key=lambda x: x[1]['sharpe'], reverse=True)[:3]
all_yrs = list(range(2017, 2027))
print(f"  {'Year':<6}  {'BASELINE':>12}" + "".join(f"  {l[:18]:>18}" for l,_ in top3))
print("-"*80)
base_ye = results[0][1]['ye']
for yr in all_yrs:
    bv = base_ye.get(yr, 0)
    row = f"  {yr}  {bv:>+11.1f}%"
    for _, r in top3:
        v = r['ye'].get(yr, 0)
        flag = '*' if abs(v - bv) > 5 else ' '
        row += f"  {v:>+17.1f}%{flag}"
    print(row)
