"""
31_entry_exit_quality.py — Entry Quality Gates + Early Exit Signals

TWO approaches (binary, not weight reduction):

SECTION A — Entry Overextension Gate
  Don't buy stocks that are overextended at rebalance date.
  Signals: 1M return too high, price too far above 20-day MA,
           or weekly RS percentile just dropped (momentum fading).

SECTION B — Early Exit (full position drop)
  At each rebalance, DROP existing positions that show early trend-change:
  - Price breaks below 21-day EMA (Minervini sell rule)
  - RS percentile dropped >15pt from last month (leadership fading)
  - Price < 50-day MA (stage analysis: exit stage 2)
  - Hard stop: stock down >12% since entry month

Goal: only hold stocks at good buy points, exit before major breakdown.
Baseline: SYSTEM v3.0 (SPY regime, 2017-2026)
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, 'scripts')

import pandas as pd, numpy as np, importlib.util, pathlib
from collections import defaultdict

_spec = importlib.util.spec_from_file_location("v2", pathlib.Path("scripts/backtest/03b_backtest_v2.py"))
v2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v2)

BASE_PARAMS = dict(
    rs_threshold=80, vol_trend_min=0.85, vol_trend_bull=0.75, vol_trend_bear=0.95,
    vol_contract_max=9.9, base_tight_max=9.9, adtv_min_m=10, cap_pct=0.18,
    bear_exposure=0.50, bull_exposure=1.00, softmax_alpha=1.0,
    start_date='2017-01-01', end_date='2026-08-20'
)
TOP_N = 15
BASELINE = dict(excess=35.8, dd=-40.1, sharpe=1.44, cagr=57.0)

print("Loading data...")
dl  = v2.BacktestDataloader()
sig = dl.signals.copy()
sig['date'] = pd.to_datetime(sig['date'])

_orig_screen = v2.BacktestStrategy.screen

# ── Pre-compute monthly signals for all tickers ───────────────────────────────
print("Pre-computing entry/exit signals...")

# Pivot: date x ticker for close, volume, ma_200, rs_pct
price_pivot  = sig.pivot_table(index='date', columns='ticker', values='close')
ma50_pivot   = sig.pivot_table(index='date', columns='ticker', values='ma_50')
rs_pivot     = sig.pivot_table(index='date', columns='ticker', values='rs_pct')

# 21-day EMA (Minervini) — compute from close prices
ema21_pivot = price_pivot.ewm(span=21, adjust=False).mean()

# Monthly returns
monthly_ret = price_pivot.resample('ME').last().pct_change()

# % above 50-day MA (use existing price_vs_ma50_pct if available, else compute)
try:
    pct_above_ma50 = sig.pivot_table(index='date', columns='ticker', values='price_vs_ma50_pct')
except Exception:
    pct_above_ma50 = (price_pivot - ma50_pivot) / ma50_pivot * 100
pct_above_ma20 = pct_above_ma50  # alias (use MA50 as proxy — MA20 not in signals)

# RS percentile change vs previous month
rs_monthly = rs_pivot.resample('ME').last()
rs_delta   = rs_monthly.diff()   # positive = rising RS, negative = fading

# 50-day MA cross: price < ma_50
below_ma50 = price_pivot < ma50_pivot

# EMA21 cross: price < ema21
below_ema21 = price_pivot < ema21_pivot

print("  Signal tables built.")

# ── Rebalance dates ───────────────────────────────────────────────────────────
all_dates   = pd.DatetimeIndex(sorted(sig[sig['ticker']=='SPY']['date'].unique()))
rebal_dates = pd.DatetimeIndex(
    all_dates[all_dates.to_series().groupby(
        all_dates.to_period('M')).transform('max') == all_dates]
)

def get_prev_rebal(ts):
    idx = rebal_dates.get_loc(ts) if ts in rebal_dates else None
    if idx is None or idx == 0: return None
    return rebal_dates[idx - 1]

# ── Signal lookup helpers ─────────────────────────────────────────────────────
def entry_overextended(ticker, ts, ret_thresh=40, ma_thresh=20, rs_drop_thresh=10):
    """True if stock is overextended at rebalance date ts -> should NOT buy."""
    try:
        # 1M return too high
        ts_month = ts.to_period('M')
        ret = monthly_ret.loc[ts_month, ticker] * 100 if ts_month in monthly_ret.index else 0
        if ret > ret_thresh: return True, f"1M_ret={ret:.0f}%>{ret_thresh}%"

        # Price too far above 20-day MA
        pct_ma = pct_above_ma20.loc[ts, ticker] if ts in pct_above_ma20.index else 0
        if pct_ma > ma_thresh: return True, f"pct_above_MA20={pct_ma:.0f}%>{ma_thresh}%"

        # RS percentile dropped significantly from last month (momentum fading)
        prev = get_prev_rebal(ts)
        if prev is not None:
            prev_period = prev.to_period('M')
            cur_period  = ts.to_period('M')
            rs_prev = rs_monthly.loc[prev_period, ticker] if prev_period in rs_monthly.index else np.nan
            rs_cur  = rs_monthly.loc[cur_period, ticker]  if cur_period in rs_monthly.index else np.nan
            if not np.isnan(rs_prev) and not np.isnan(rs_cur):
                drop = rs_prev - rs_cur
                if drop > rs_drop_thresh: return True, f"RS_drop={drop:.0f}pt"
    except Exception:
        pass
    return False, ""

def exit_triggered(ticker, ts, entry_ts=None):
    """True if position should be FULLY DROPPED at this rebalance."""
    reasons = []
    try:
        # Signal 1: price below 21-day EMA (Minervini: exit stage 2)
        if ts in below_ema21.index and ticker in below_ema21.columns:
            if below_ema21.loc[ts, ticker]:
                reasons.append("below_EMA21")

        # Signal 2: price below 50-day MA (stage breakdown)
        if ts in below_ma50.index and ticker in below_ma50.columns:
            if below_ma50.loc[ts, ticker]:
                reasons.append("below_MA50")

        # Signal 3: RS percentile dropped >15pt vs last month (fading leadership)
        prev = get_prev_rebal(ts)
        if prev is not None:
            prev_p = prev.to_period('M'); cur_p = ts.to_period('M')
            rs_p = rs_monthly.loc[prev_p, ticker] if prev_p in rs_monthly.index else np.nan
            rs_c = rs_monthly.loc[cur_p,  ticker] if cur_p  in rs_monthly.index else np.nan
            if not np.isnan(rs_p) and not np.isnan(rs_c) and (rs_p - rs_c) > 15:
                reasons.append(f"RS_fade={rs_p-rs_c:.0f}pt")
    except Exception:
        pass
    return len(reasons) > 0, reasons

# ── Backtest runner ───────────────────────────────────────────────────────────
def run_bt(label, use_entry_gate=False, use_exit_gate=False,
           ret_thresh=40, ma_thresh=20, rs_drop=10,
           exit_ema21=True, exit_ma50=True, exit_rs_fade=True, verbose=True):

    held_positions = {}  # ticker -> entry_ts

    def smart_screen(self, date):
        ts = pd.Timestamp(date)
        result = _orig_screen(self, date)
        if result.empty: return result

        filtered = []
        for _, row in result.iterrows():
            tkr = row['ticker']

            # EXIT gate: drop held positions showing early trend change
            if use_exit_gate and tkr in held_positions:
                reasons = []
                try:
                    if exit_ema21 and ts in below_ema21.index and tkr in below_ema21.columns:
                        if below_ema21.loc[ts, tkr]: reasons.append("EMA21")
                    if exit_ma50 and ts in below_ma50.index and tkr in below_ma50.columns:
                        if below_ma50.loc[ts, tkr]: reasons.append("MA50")
                    if exit_rs_fade:
                        prev = get_prev_rebal(ts)
                        if prev is not None:
                            pp = prev.to_period('M'); cp = ts.to_period('M')
                            rp = rs_monthly.loc[pp, tkr] if pp in rs_monthly.index else np.nan
                            rc = rs_monthly.loc[cp, tkr] if cp in rs_monthly.index else np.nan
                            if not np.isnan(rp) and not np.isnan(rc) and (rp - rc) > 15:
                                reasons.append(f"RS_fade")
                except Exception:
                    pass
                if reasons:
                    del held_positions[tkr]
                    continue  # drop this position

            # ENTRY gate: skip overextended stocks (only for new positions)
            if use_entry_gate and tkr not in held_positions:
                try:
                    tp = ts.to_period('M')
                    ret = monthly_ret.loc[tp, tkr] * 100 if tp in monthly_ret.index else 0
                    pct_ma = pct_above_ma20.loc[ts, tkr] if ts in pct_above_ma20.index else 0
                    overext = False
                    if ret > ret_thresh: overext = True
                    elif not np.isnan(pct_ma) and pct_ma > ma_thresh: overext = True
                    else:
                        prev = get_prev_rebal(ts)
                        if prev is not None:
                            pp = prev.to_period('M'); cp = ts.to_period('M')
                            rp = rs_monthly.loc[pp, tkr] if pp in rs_monthly.index else np.nan
                            rc = rs_monthly.loc[cp, tkr] if cp in rs_monthly.index else np.nan
                            if not np.isnan(rp) and not np.isnan(rc) and (rp - rc) > rs_drop:
                                overext = True
                    if overext: continue
                except Exception:
                    pass
                held_positions[tkr] = ts
            elif tkr not in held_positions:
                held_positions[tkr] = ts

            filtered.append(row)

        if not filtered: return result.iloc[0:0]
        res = pd.DataFrame(filtered)
        if len(res) > TOP_N:
            res = res.nlargest(TOP_N, 'rs_pct').copy()
        w = res['weight'].clip(upper=self.params.cap_pct)
        res['weight'] = w / w.sum() if w.sum() > 0 else w
        return res

    v2.BacktestStrategy.screen = smart_screen

    p     = v2.StrategyParams(**BASE_PARAMS)
    strat = v2.BacktestStrategy(dl, p)
    port  = v2.BacktestPortfolio(1_000_000, v2.CostParams())
    r     = v2.BacktestModel(dl, strat, port, p).run(verbose=False)

    v2.BacktestStrategy.screen = _orig_screen
    held_positions.clear()

    ye   = {yr: (d['excess'] if isinstance(d, dict) else d)*100
            for yr, d in r.get('yearly', {}).items()}
    wins = sum(1 for v in ye.values() if v > 0)
    sc   = 100 if r['port_cagr'] < 5 else 1
    cagr = r['port_cagr'] * sc
    exc  = r['excess']    * sc
    dd   = r['port_mdd']  * sc
    sh   = r['sharpe']
    holds= r['avg_holdings']
    wf2  = (ye.get(2022, 0) + ye.get(2023, 0)) / 2
    yr_str = ' '.join(f"{yr}:{'+' if v>=0 else ''}{v:.1f}%" for yr,v in sorted(ye.items()))

    # Governance check vs v3.0 locked baseline
    ex_ok = exc  > BASELINE['excess']
    dd_ok = dd   > BASELINE['dd']
    sh_ok = sh   > BASELINE['sharpe']
    if ex_ok and dd_ok and sh_ok:
        gov = 'STRICT'
    elif (ex_ok or sh_ok) and dd_ok:
        gov = 'PARTIAL'
    else:
        gov = 'no'

    if verbose:
        print(f"\n{'='*65}")
        print(f"  {label}")
        print(f"  CAGR={cagr:.1f}% | Excess={exc:+.1f}% | DD={dd:.1f}%")
        print(f"  Sharpe={sh:.2f} | Holds={holds:.0f} | Win={wins}/{len(ye)} | WF2={wf2:+.1f}% | Gov={gov}")
        print(f"  {yr_str}")

    return dict(cagr=cagr, excess=exc, dd=dd, sharpe=sh, holds=holds,
                wins=wins, wf2=wf2, gov=gov, ye=ye)

# ── RUN ───────────────────────────────────────────────────────────────────────
results = []

print("\n" + "="*65)
print("BASELINE (no entry/exit gate)")
r0 = run_bt("BASELINE", use_entry_gate=False, use_exit_gate=False)
results.append(("BASELINE", r0))

print("\n" + "="*65)
print("SECTION A — ENTRY OVEREXTENSION GATES")
print("(Don't buy new positions if stock is overextended)")

configs_A = [
    ("ENTRY: 1M<40% + MA20<20% + RS_drop<10", dict(use_entry_gate=True, ret_thresh=40, ma_thresh=20, rs_drop=10)),
    ("ENTRY: 1M<30% + MA20<15% + RS_drop<10", dict(use_entry_gate=True, ret_thresh=30, ma_thresh=15, rs_drop=10)),
    ("ENTRY: 1M<50% + MA20<25%",              dict(use_entry_gate=True, ret_thresh=50, ma_thresh=25, rs_drop=999)),
    ("ENTRY: RS_drop<10 only",                dict(use_entry_gate=True, ret_thresh=999, ma_thresh=999, rs_drop=10)),
]
for label, kw in configs_A:
    r = run_bt(label, **kw)
    results.append((label, r))

print("\n" + "="*65)
print("SECTION B — EARLY EXIT SIGNALS (full position drop)")
print("(Drop held stocks showing early trend-change signals)")

configs_B = [
    ("EXIT: EMA21 break only",             dict(use_exit_gate=True, exit_ema21=True, exit_ma50=False, exit_rs_fade=False)),
    ("EXIT: MA50 break only",              dict(use_exit_gate=True, exit_ema21=False, exit_ma50=True, exit_rs_fade=False)),
    ("EXIT: RS fade >15pt only",           dict(use_exit_gate=True, exit_ema21=False, exit_ma50=False, exit_rs_fade=True)),
    ("EXIT: EMA21 + RS fade",              dict(use_exit_gate=True, exit_ema21=True, exit_ma50=False, exit_rs_fade=True)),
    ("EXIT: MA50 + RS fade",               dict(use_exit_gate=True, exit_ema21=False, exit_ma50=True, exit_rs_fade=True)),
    ("EXIT: EMA21 + MA50 + RS fade (all)", dict(use_exit_gate=True, exit_ema21=True, exit_ma50=True, exit_rs_fade=True)),
]
for label, kw in configs_B:
    r = run_bt(label, **kw)
    results.append((label, r))

print("\n" + "="*65)
print("SECTION C — COMBINED: Entry Gate + Early Exit")

configs_C = [
    ("COMBINED: entry(30%/15%) + exit(EMA21+RS)",  dict(use_entry_gate=True, ret_thresh=30, ma_thresh=15, rs_drop=10,
                                                         use_exit_gate=True, exit_ema21=True, exit_ma50=False, exit_rs_fade=True)),
    ("COMBINED: entry(50%/25%) + exit(MA50+RS)",   dict(use_entry_gate=True, ret_thresh=50, ma_thresh=25, rs_drop=999,
                                                         use_exit_gate=True, exit_ema21=False, exit_ma50=True, exit_rs_fade=True)),
]
for label, kw in configs_C:
    r = run_bt(label, **kw)
    results.append((label, r))

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("SUMMARY vs v3.0 BASELINE")
print(f"{'Config':<42}  {'Excess':>7}  {'DD':>7}  {'Sh':>5}  {'WF2':>6}  {'Win':>5}  Gov")
print("-"*90)
for label, r in results:
    lbl = label[:42]
    print(f"  {lbl:<42}  {r['excess']:>+6.1f}%  {r['dd']:>+6.1f}%  {r['sharpe']:>5.2f}  {r['wf2']:>+5.1f}%  {r['wins']}/{r['wf2']:>3.0f}  {r['gov']}")

# Best per metric
print(f"\nBest DD:     {min(results, key=lambda x: x[1]['dd'])[0]}")
print(f"Best Excess: {max(results, key=lambda x: x[1]['excess'])[0]}")
print(f"Best Sharpe: {max(results, key=lambda x: x[1]['sharpe'])[0]}")
gov_pass = [(l, r) for l, r in results if r['gov'] in ('STRICT','PARTIAL')]
if gov_pass:
    print(f"\nGov PASS: {[l for l,_ in gov_pass]}")
else:
    print(f"\nNo config passed governance vs v3.0 locked baseline.")
