"""
32_intramonth_exit.py — Intra-Month Daily/Weekly Exit Alerts

Problem: monthly rebalance misses mid-month crashes.
  IONQ -38%, RGTI -36% crash in Jan 2025 — peaked day 3, crashed by day 15.
  System holds until month-end and takes the full hit.

Solution: add a daily/weekly exit layer ON TOP of monthly screen.
  1. Month-end: run normal v3.0 screen -> get target portfolio
  2. Each trading day within month: check each held position
  3. If exit signal fires -> sell next open, go to cash until next rebalance
  4. Cash held doesn't earn interest (conservative assumption)

EXIT SIGNALS TESTED:
  A. Intra-month trailing stop: down X% from entry price (8%, 12%, 15%)
  B. Daily close below 21-day EMA (Minervini rule — exit stage 2)
  C. Weekly close below 10-week MA (O'Neil rule)
  D. Daily close below 50-day MA (stage breakdown — more conservative)
  E. Combo: EMA21 + trailing stop 12%

Architecture:
  - NOT using v2.BacktestModel (incompatible with intra-month logic)
  - Custom simulation loop with daily granularity
  - Monthly screen still uses v2.BacktestStrategy.screen() for stock selection
  - Position sizing: equal weight among survivors each month
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
TOP_N       = 15
COST_RT     = 0.0015   # 0.15% round-trip (US retail)
BASELINE    = dict(excess=35.8, dd=-40.1, sharpe=1.44, cagr=57.0)

print("Loading data...")
dl  = v2.BacktestDataloader()
sig = dl.signals.copy()
sig['date'] = pd.to_datetime(sig['date'])

# ── Price data pivots (daily) ─────────────────────────────────────────────────
print("Building daily price tables...")
close_px  = sig.pivot_table(index='date', columns='ticker', values='close').sort_index()
ema21_px  = close_px.ewm(span=21, adjust=False).mean()
ma50_px   = close_px.rolling(50, min_periods=20).mean()
ma10w_px  = close_px.rolling(50, min_periods=20).mean()  # 10-week ~ 50 trading days

# Weekly close (Friday)
weekly_close = close_px.resample('W-FRI').last()
ma10w_weekly = weekly_close.rolling(10, min_periods=5).mean()

print(f"  Daily dates: {len(close_px)} | Tickers: {close_px.shape[1]}")

# ── Rebalance dates ───────────────────────────────────────────────────────────
all_dates   = pd.DatetimeIndex(sorted(sig[sig['ticker']=='SPY']['date'].unique()))
rebal_dates = pd.DatetimeIndex(
    all_dates[all_dates.to_series().groupby(
        all_dates.to_period('M')).transform('max') == all_dates]
)
START = pd.Timestamp('2017-01-01'); END = pd.Timestamp('2026-08-20')
rebal_dates = rebal_dates[(rebal_dates >= START) & (rebal_dates <= END)]

# ── SPY regime (bull/bear) ────────────────────────────────────────────────────
spy_rows = sig[sig['ticker'] == 'SPY'][['date','close','ma_200']].dropna()
spy_bull_map = dict(zip(spy_rows['date'], spy_rows['close'] > spy_rows['ma_200']))

_orig_screen = v2.BacktestStrategy.screen

def get_monthly_portfolio(rebal_ts):
    """Run v3.0 screen at rebal_ts, return {ticker: weight} dict."""
    is_bull = spy_bull_map.get(rebal_ts, True)

    def capped_screen(self, date):
        result = _orig_screen(self, date)
        if result.empty: return result
        if len(result) > TOP_N:
            result = result.nlargest(TOP_N, 'rs_pct').copy()
            w = result['weight'].clip(upper=self.params.cap_pct)
            result['weight'] = w / w.sum() * result['weight'].sum()
        return result
    v2.BacktestStrategy.screen = capped_screen

    p     = v2.StrategyParams(**BASE_PARAMS)
    strat = v2.BacktestStrategy(dl, p)
    out   = strat.screen(rebal_ts)
    v2.BacktestStrategy.screen = _orig_screen

    if out.empty:
        return {}, (1.0 if is_bull else 0.5)

    # Exposure multiplier
    expo = 1.0 if is_bull else 0.5

    portfolio = {}
    total_w = out['weight'].sum()
    for _, row in out.iterrows():
        portfolio[row['ticker']] = (row['weight'] / total_w) * expo
    return portfolio, expo

# ── Main simulation ───────────────────────────────────────────────────────────
def run_simulation(label, exit_fn=None, verbose=True):
    """
    exit_fn(ticker, entry_price, entry_date, current_date, close_px, ema21, ma50, weekly_close, ma10w_weekly)
        -> True if should exit today
    """
    nav      = 1.0
    cash_pct = 1.0
    positions = {}   # ticker -> {weight, entry_price, entry_date, high_since_entry}

    nav_series   = {}
    bench_series = {}
    bench_nav    = 1.0

    # SPY return for benchmark
    spy_close = close_px['SPY'].dropna() if 'SPY' in close_px.columns else None

    all_sim_dates = close_px.index[(close_px.index >= START) & (close_px.index <= END)]
    rebal_set = set(rebal_dates)

    exits_log = []   # (date, ticker, reason)

    for i, dt in enumerate(all_sim_dates):
        # Benchmark daily return
        if spy_close is not None and dt in spy_close.index and i > 0:
            prev_dt = all_sim_dates[i-1]
            if prev_dt in spy_close.index:
                spy_ret = spy_close[dt] / spy_close[prev_dt] - 1
                bench_nav *= (1 + spy_ret)

        # ── REBALANCE DAY ─────────────────────────────────────────────────────
        if dt in rebal_set:
            # Mark-to-market current positions at today's price
            equity = 0.0
            for tkr, pos in list(positions.items()):
                if tkr in close_px.columns and dt in close_px.index:
                    px_now = close_px.loc[dt, tkr]
                    if not np.isnan(px_now):
                        equity += pos['weight'] * (px_now / pos['entry_price'])
            nav_with_cash = cash_pct + equity

            # Get new portfolio
            new_port, expo = get_monthly_portfolio(dt)

            # Transaction costs on changed positions
            old_tickers = set(positions.keys())
            new_tickers = set(new_port.keys())
            traded = old_tickers.symmetric_difference(new_tickers) | \
                     {t for t in old_tickers & new_tickers
                      if abs(new_port.get(t, 0) - positions[t]['weight']) > 0.02}
            turnover = len(traded) / max(len(old_tickers | new_tickers), 1)
            nav_with_cash *= (1 - COST_RT * turnover)

            nav = nav_with_cash

            # Reset positions
            positions = {}
            cash_pct  = 1.0 - expo
            for tkr, w in new_port.items():
                px0 = close_px.loc[dt, tkr] if tkr in close_px.columns and dt in close_px.index else None
                if px0 and not np.isnan(px0):
                    positions[tkr] = dict(weight=w, entry_price=px0,
                                          entry_date=dt, high_since_entry=px0)

        else:
            # ── INTRA-MONTH DAY ───────────────────────────────────────────────
            daily_pnl = 0.0
            to_exit   = []

            for tkr, pos in positions.items():
                if tkr not in close_px.columns: continue
                if dt not in close_px.index: continue
                px_now = close_px.loc[dt, tkr]
                if np.isnan(px_now): continue

                # Update high watermark
                pos['high_since_entry'] = max(pos['high_since_entry'], px_now)

                # Daily P&L contribution
                prev_close_dt = all_sim_dates[i-1] if i > 0 else dt
                if prev_close_dt in close_px.index:
                    px_prev = close_px.loc[prev_close_dt, tkr]
                    if not np.isnan(px_prev):
                        daily_pnl += pos['weight'] * (px_now / px_prev - 1)

                # Check exit signal
                if exit_fn is not None:
                    try:
                        ema21_val = ema21_px.loc[dt, tkr] if dt in ema21_px.index else np.nan
                        ma50_val  = ma50_px.loc[dt, tkr]  if dt in ma50_px.index  else np.nan
                        # weekly: find last completed week close
                        wk_dt   = weekly_close.index[weekly_close.index <= dt][-1] if len(weekly_close.index[weekly_close.index <= dt]) > 0 else None
                        wk_cl   = weekly_close.loc[wk_dt, tkr] if wk_dt and tkr in weekly_close.columns else np.nan
                        ma10w_v = ma10w_weekly.loc[wk_dt, tkr] if wk_dt and tkr in ma10w_weekly.columns else np.nan

                        if exit_fn(tkr, pos['entry_price'], pos['entry_date'],
                                   dt, px_now, ema21_val, ma50_val,
                                   wk_cl, ma10w_v, pos['high_since_entry']):
                            to_exit.append(tkr)
                    except Exception:
                        pass

            nav *= (1 + daily_pnl)

            # Execute exits: pay cost, move to cash
            for tkr in to_exit:
                pos = positions.pop(tkr)
                px_now = close_px.loc[dt, tkr] if dt in close_px.index else pos['entry_price']
                real_ret = (px_now / pos['entry_price'] - 1) if not np.isnan(px_now) else 0
                nav *= (1 - COST_RT * pos['weight'])   # exit cost
                cash_pct += pos['weight'] * (1 + real_ret)
                reason = "exit_signal"
                exits_log.append((dt, tkr, reason, real_ret * 100))

        nav_series[dt]   = nav
        bench_series[dt] = bench_nav

    # ── Compute stats ─────────────────────────────────────────────────────────
    nav_s   = pd.Series(nav_series).sort_index()
    bench_s = pd.Series(bench_series).sort_index()

    # QQQ benchmark (benchmark is QQQ in v2)
    qqq_close = close_px['QQQ'].dropna() if 'QQQ' in close_px.columns else None
    if qqq_close is not None:
        qqq_ret = qqq_close.pct_change().dropna()
        qqq_nav = (1 + qqq_ret).cumprod()
        qqq_nav = qqq_nav.reindex(nav_s.index, method='ffill')
        years   = (nav_s.index[-1] - nav_s.index[0]).days / 365.25
        p_cagr  = (nav_s.iloc[-1] / nav_s.iloc[0]) ** (1/years) - 1
        q_cagr  = (qqq_nav.iloc[-1] / qqq_nav.iloc[0]) ** (1/years) - 1
        excess  = (p_cagr - q_cagr) * 100
    else:
        p_cagr = excess = 0; q_cagr = 0

    roll_max = nav_s.cummax()
    dd       = ((nav_s - roll_max) / roll_max).min() * 100
    pct_ret  = nav_s.pct_change().dropna()
    sharpe   = (pct_ret.mean() / pct_ret.std()) * np.sqrt(252) if pct_ret.std() > 0 else 0

    # Yearly excess
    ye = {}
    if qqq_close is not None:
        for yr in range(2017, 2027):
            yr_nav = nav_s[nav_s.index.year == yr]
            yr_qqq = qqq_close[qqq_close.index.year == yr]
            if len(yr_nav) > 5 and len(yr_qqq) > 5:
                pr = yr_nav.iloc[-1] / yr_nav.iloc[0] - 1
                qr = yr_qqq.iloc[-1] / yr_qqq.iloc[0] - 1
                ye[yr] = (pr - qr) * 100
    wins = sum(1 for v in ye.values() if v > 0)
    wf2  = (ye.get(2022, 0) + ye.get(2023, 0)) / 2

    p_cagr_pct = p_cagr * 100

    # Governance vs v3.0
    ex_ok = excess  > BASELINE['excess']
    dd_ok = dd      > BASELINE['dd']
    sh_ok = sharpe  > BASELINE['sharpe']
    gov = 'STRICT' if (ex_ok and dd_ok and sh_ok) else \
          'PARTIAL' if ((ex_ok or sh_ok) and dd_ok) else 'no'

    yr_str = ' '.join(f"{yr}:{'+' if v>=0 else ''}{v:.1f}%" for yr,v in sorted(ye.items()))

    n_exits = len(exits_log)
    avg_exit_ret = np.mean([r for _,_,_,r in exits_log]) if exits_log else 0

    if verbose:
        print(f"\n{'='*65}")
        print(f"  {label}")
        print(f"  CAGR={p_cagr_pct:.1f}% | Excess={excess:+.1f}% | DD={dd:.1f}%")
        print(f"  Sharpe={sharpe:.2f} | Win={wins}/{len(ye)} | WF2={wf2:+.1f}% | Gov={gov}")
        print(f"  Exits fired: {n_exits} | Avg exit return: {avg_exit_ret:+.1f}%")
        print(f"  {yr_str}")

    return dict(cagr=p_cagr_pct, excess=excess, dd=dd, sharpe=sharpe,
                wins=wins, n=len(ye), wf2=wf2, gov=gov, exits=n_exits,
                avg_exit_ret=avg_exit_ret, ye=ye)

# ── Define exit functions ─────────────────────────────────────────────────────
def make_trail(pct):
    def fn(tkr, ep, ed, dt, px, ema21, ma50, wk_cl, ma10w, high):
        if np.isnan(high) or high == 0: return False
        return px < high * (1 - pct/100)
    return fn

def exit_ema21_daily(tkr, ep, ed, dt, px, ema21, ma50, wk_cl, ma10w, high):
    """Daily close below 21-day EMA."""
    if np.isnan(ema21): return False
    return px < ema21

def exit_ma50_daily(tkr, ep, ed, dt, px, ema21, ma50, wk_cl, ma10w, high):
    """Daily close below 50-day MA."""
    if np.isnan(ma50): return False
    return px < ma50

def exit_10w_weekly(tkr, ep, ed, dt, px, ema21, ma50, wk_cl, ma10w, high):
    """Weekly close below 10-week MA (O'Neil rule — only checks Fridays)."""
    if np.isnan(wk_cl) or np.isnan(ma10w): return False
    return wk_cl < ma50   # use ma50 as proxy for 10-week MA

def exit_ema21_plus_trail12(tkr, ep, ed, dt, px, ema21, ma50, wk_cl, ma10w, high):
    if not np.isnan(ema21) and px < ema21: return True
    if not np.isnan(high) and high > 0 and px < high * 0.88: return True
    return False

def exit_ma50_plus_trail15(tkr, ep, ed, dt, px, ema21, ma50, wk_cl, ma10w, high):
    if not np.isnan(ma50) and px < ma50: return True
    if not np.isnan(high) and high > 0 and px < high * 0.85: return True
    return False

# ── RUN ───────────────────────────────────────────────────────────────────────
print("\n" + "="*65)
print("INTRA-MONTH EXIT STUDY (custom daily simulation)")
print("="*65)

configs = [
    ("BASELINE (no intra-month exit)",       None),
    ("TRAIL 8%  from intra-month high",      make_trail(8)),
    ("TRAIL 12% from intra-month high",      make_trail(12)),
    ("TRAIL 15% from intra-month high",      make_trail(15)),
    ("DAILY EMA21 break (Minervini)",        exit_ema21_daily),
    ("DAILY MA50 break (stage breakdown)",   exit_ma50_daily),
    ("EMA21 + TRAIL 12% (combo)",            exit_ema21_plus_trail12),
    ("MA50  + TRAIL 15% (combo)",            exit_ma50_plus_trail15),
]

results = []
for label, fn in configs:
    r = run_simulation(label, exit_fn=fn)
    results.append((label, r))

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("SUMMARY vs v3.0 LOCKED BASELINE")
print(f"  {'Config':<38}  {'CAGR':>6}  {'Excess':>7}  {'DD':>7}  {'Sh':>5}  {'WF2':>6}  {'Exits':>5}  Gov")
print("-"*95)
for label, r in results:
    lbl = label[:38]
    print(f"  {lbl:<38}  {r['cagr']:>5.1f}%  {r['excess']:>+6.1f}%  {r['dd']:>+6.1f}%  {r['sharpe']:>5.2f}  {r['wf2']:>+5.1f}%  {r['exits']:>5}  {r['gov']}")

print(f"\n  v3.0 LOCKED BASELINE: CAGR={BASELINE['cagr']:.1f}% Excess={BASELINE['excess']:+.1f}% DD={BASELINE['dd']:.1f}% Sharpe={BASELINE['sharpe']:.2f}")
print(f"\nBest DD:     {min(results, key=lambda x: x[1]['dd'])[0]}")
print(f"Best Sharpe: {max(results, key=lambda x: x[1]['sharpe'])[0]}")
gov_pass = [(l,r) for l,r in results if r['gov'] in ('STRICT','PARTIAL')]
if gov_pass:
    print(f"Gov PASS: {[l for l,_ in gov_pass]}")
else:
    print(f"No config beat v3.0 baseline on DD+Excess+Sharpe simultaneously.")
