"""
33_ladder_exit.py — Ladder Exit + Trailing Stop on Last Lot (Intra-Month)

Strategy:
  Month-end rebalance -> buy positions normally (v3.0 screen)
  Intra-month, track each position in "lots":
    Lot 1: sell fraction F1 when return >= TP1  (lock profit early)
    Lot 2: sell fraction F2 when return >= TP2  (lock more)
    Last lot: hold until trailing stop from high triggers (exit ALL remaining)

  Key difference vs script 27 (monthly TP):
    - TP fires intra-month, not at next month-end
    - Trailing stop on last lot = let winner run, cut on reversal
    - After partial exits, remaining weight grows relative to portfolio

Configs tested:
  A. 3-lot: sell 1/3 at +15%, 1/3 at +30%, last 1/3 on trail-12%
  B. 4-lot: sell 1/4 at +15%, 1/4 at +25%, 1/4 at +40%, last 1/4 on trail-15%
  C. 2-lot: sell 1/2 at +20%, last 1/2 on trail-12%
  D. Aggressive: sell 1/4 at +25%, 1/2 at +50%, last 1/4 on trail-10%
  E. Conservative: sell 1/3 at +10%, 1/3 at +20%, last 1/3 on trail-8%
  F. No partial, just trailing stop on full position (8%, 12%, 15%)
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
print("Building daily price tables...")
close_px = sig.pivot_table(index='date', columns='ticker', values='close').sort_index()
ema21_px = close_px.ewm(span=21, adjust=False).mean()
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

# ── SPY regime ────────────────────────────────────────────────────────────────
spy_rows = sig[sig['ticker'] == 'SPY'][['date','close','ma_200']].dropna()
spy_bull_map = dict(zip(spy_rows['date'], spy_rows['close'] > spy_rows['ma_200']))

_orig_screen = v2.BacktestStrategy.screen

def get_monthly_portfolio(rebal_ts):
    """Run v3.0 screen, return {ticker: weight} and exposure."""
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
    port = {row['ticker']: (row['weight'] / total_w) * expo
            for _, row in out.iterrows()}
    return port, expo

# ── Position class ────────────────────────────────────────────────────────────
class Position:
    def __init__(self, ticker, weight, entry_price):
        self.ticker       = ticker
        self.weight       = weight        # current portfolio weight (shrinks as lots sold)
        self.entry_price  = entry_price
        self.high         = entry_price   # intra-month high watermark
        self.lots_taken   = []            # list of (fraction_of_original, sell_price)
        self.original_w   = weight        # original weight at entry

    def current_return(self, px):
        return px / self.entry_price - 1

    def take_partial(self, fraction_of_original, px):
        """Sell fraction of original weight. Returns the weight sold."""
        sold_w = self.original_w * fraction_of_original
        self.weight  = max(0, self.weight - sold_w)
        self.lots_taken.append((fraction_of_original, px))
        return sold_w

    @property
    def pct_remaining(self):
        return self.weight / self.original_w if self.original_w > 0 else 0

# ── Main simulation ───────────────────────────────────────────────────────────
def run_ladder(label, ladder, trail_pct, verbose=True):
    """
    ladder: list of (tp_return, fraction_of_original) — take partial at tp_return
    trail_pct: trailing stop % from high on remaining position (full exit)

    Example: ladder=[(0.15, 1/3), (0.30, 1/3)], trail_pct=0.12
      -> sell 1/3 at +15%, 1/3 at +30%, exit last 1/3 if -12% from high
    """
    positions = {}   # ticker -> Position
    nav       = 1.0
    cash_pct  = 1.0

    nav_series   = {}
    all_sim_dates = close_px.index[(close_px.index >= START) & (close_px.index <= END)]

    qqq_close = close_px['QQQ'].dropna() if 'QQQ' in close_px.columns else None

    partial_exits = 0
    full_exits    = 0

    for i, dt in enumerate(all_sim_dates):

        # ── REBALANCE DAY ─────────────────────────────────────────────────────
        if dt in rebal_set:
            # Mark existing positions to market
            equity = sum(
                pos.weight * (close_px.loc[dt, tkr] / pos.entry_price)
                for tkr, pos in positions.items()
                if tkr in close_px.columns and dt in close_px.index
                and not np.isnan(close_px.loc[dt, tkr])
            )
            nav_cur = cash_pct + equity

            new_port, expo = get_monthly_portfolio(dt)

            # Turnover cost
            old_set = set(positions.keys())
            new_set = set(new_port.keys())
            churn   = len(old_set.symmetric_difference(new_set)) / max(len(old_set | new_set), 1)
            nav_cur *= (1 - COST_RT * churn)
            nav      = nav_cur

            # Reset
            positions = {}
            cash_pct  = 1.0 - expo
            for tkr, w in new_port.items():
                px0 = close_px.loc[dt, tkr] if tkr in close_px.columns and dt in close_px.index else None
                if px0 and not np.isnan(px0):
                    positions[tkr] = Position(tkr, w, px0)

        else:
            # ── INTRA-MONTH DAY ───────────────────────────────────────────────
            daily_pnl = 0.0
            to_full_exit = []

            for tkr, pos in list(positions.items()):
                if tkr not in close_px.columns: continue
                if dt not in close_px.index: continue
                px_now = close_px.loc[dt, tkr]
                if np.isnan(px_now) or pos.weight <= 1e-6: continue

                # Update high watermark
                pos.high = max(pos.high, px_now)

                # Daily P&L
                prev_dt = all_sim_dates[i-1] if i > 0 else dt
                if prev_dt in close_px.index:
                    px_prev = close_px.loc[prev_dt, tkr]
                    if not np.isnan(px_prev):
                        daily_pnl += pos.weight * (px_now / px_prev - 1)

                # Check partial take-profit lots
                ret = pos.current_return(px_now)
                lots_done = len(pos.lots_taken)
                for lot_idx, (tp_ret, frac) in enumerate(ladder):
                    if lot_idx < lots_done: continue  # already taken
                    if ret >= tp_ret:
                        sold_w = pos.take_partial(frac, px_now)
                        # Cost on partial exit
                        nav    *= (1 - COST_RT * sold_w * 0.5)   # half-turn (sell side only)
                        cash_pct += sold_w * (1 + ret)
                        partial_exits += 1
                        break  # take one lot at a time per day

                # Trailing stop on remaining lot
                if trail_pct > 0 and pos.high > 0:
                    trail_level = pos.high * (1 - trail_pct)
                    if px_now < trail_level:
                        to_full_exit.append(tkr)

            nav *= (1 + daily_pnl)

            # Full exits (trailing stop triggered)
            for tkr in to_full_exit:
                pos = positions.pop(tkr)
                px_now = close_px.loc[dt, tkr] if dt in close_px.index and tkr in close_px.columns else pos.entry_price
                if np.isnan(px_now): px_now = pos.entry_price
                # Pay exit cost, recover remaining position value
                nav       *= (1 - COST_RT * pos.weight * 0.5)
                ret_now    = px_now / pos.entry_price - 1
                cash_pct  += pos.weight * (1 + ret_now)
                pos.weight  = 0
                full_exits += 1

        nav_series[dt] = nav

    # ── Stats ─────────────────────────────────────────────────────────────────
    nav_s = pd.Series(nav_series).sort_index()

    years  = (nav_s.index[-1] - nav_s.index[0]).days / 365.25
    p_cagr = (nav_s.iloc[-1] / nav_s.iloc[0]) ** (1/years) - 1

    if qqq_close is not None:
        qqq_ret  = qqq_close.pct_change().dropna()
        qqq_nav  = (1 + qqq_ret).cumprod().reindex(nav_s.index, method='ffill')
        q_cagr   = (qqq_nav.iloc[-1] / qqq_nav.iloc[0]) ** (1/years) - 1
        excess   = (p_cagr - q_cagr) * 100
    else:
        excess = q_cagr = 0

    roll_max = nav_s.cummax()
    dd       = ((nav_s - roll_max) / roll_max).min() * 100
    pct_ret  = nav_s.pct_change().dropna()
    sharpe   = (pct_ret.mean() / pct_ret.std()) * np.sqrt(252) if pct_ret.std() > 0 else 0

    # Yearly excess
    ye = {}
    if qqq_close is not None:
        for yr in range(2017, 2027):
            yn = nav_s[nav_s.index.year == yr]
            yq = qqq_close[qqq_close.index.year == yr]
            if len(yn) > 5 and len(yq) > 5:
                ye[yr] = ((yn.iloc[-1]/yn.iloc[0]) - (yq.iloc[-1]/yq.iloc[0])) * 100

    wins = sum(1 for v in ye.values() if v > 0)
    wf2  = (ye.get(2022, 0) + ye.get(2023, 0)) / 2
    cagr_pct = p_cagr * 100

    ex_ok = excess  > BASELINE['excess']
    dd_ok = dd      > BASELINE['dd']
    sh_ok = sharpe  > BASELINE['sharpe']
    gov   = 'STRICT' if (ex_ok and dd_ok and sh_ok) else \
            'PARTIAL' if ((ex_ok or sh_ok) and dd_ok) else 'no'

    yr_str = ' '.join(f"{yr}:{'+' if v>=0 else ''}{v:.1f}%" for yr,v in sorted(ye.items()))

    if verbose:
        print(f"\n{'='*65}")
        print(f"  {label}")
        print(f"  CAGR={cagr_pct:.1f}% | Excess={excess:+.1f}% | DD={dd:.1f}%")
        print(f"  Sharpe={sharpe:.2f} | Win={wins}/{len(ye)} | WF2={wf2:+.1f}% | Gov={gov}")
        print(f"  Partial exits: {partial_exits} | Full exits (trail): {full_exits}")
        print(f"  {yr_str}")

    return dict(cagr=cagr_pct, excess=excess, dd=dd, sharpe=sharpe,
                wins=wins, wf2=wf2, gov=gov, partial=partial_exits,
                full=full_exits, ye=ye)

# ── Define configs ────────────────────────────────────────────────────────────
# Format: (label, ladder [(tp_pct, fraction_of_original)], trail_pct)
CONFIGS = [
    # Section 0: Baseline
    ("BASELINE (no exit)",
     [], 0.0),

    # Section 1: Pure trailing stop only (no partial)
    ("TRAIL ONLY 8%  from high",
     [], 0.08),
    ("TRAIL ONLY 12% from high",
     [], 0.12),
    ("TRAIL ONLY 15% from high",
     [], 0.15),

    # Section 2: Ladder + Trailing stop on last lot
    ("LADDER-3 lots: sell 1/3@+15%, 1/3@+30%, trail-12% on last",
     [(0.15, 1/3), (0.30, 1/3)], 0.12),

    ("LADDER-3 lots: sell 1/3@+20%, 1/3@+40%, trail-15% on last",
     [(0.20, 1/3), (0.40, 1/3)], 0.15),

    ("LADDER-4 lots: sell 1/4@+15%, 1/4@+25%, 1/4@+40%, trail-15% on last",
     [(0.15, 0.25), (0.25, 0.25), (0.40, 0.25)], 0.15),

    ("LADDER-2 lots: sell 1/2@+20%, trail-12% on last",
     [(0.20, 0.50)], 0.12),

    ("LADDER-2 lots: sell 1/2@+30%, trail-15% on last",
     [(0.30, 0.50)], 0.15),

    # Section 3: Aggressive (lock early, let last lot run long)
    ("AGGRESSIVE: sell 1/4@+25%, 1/2@+50%, trail-10% on last 1/4",
     [(0.25, 0.25), (0.50, 0.50)], 0.10),

    # Section 4: Conservative (lock profit quickly, tight stop)
    ("CONSERVATIVE: sell 1/3@+10%, 1/3@+20%, trail-8% on last",
     [(0.10, 1/3), (0.20, 1/3)], 0.08),

    # Section 5: Minervini-style
    ("MINERVINI: sell 1/3@+20%, 1/3@+40%, trail-8% on last",
     [(0.20, 1/3), (0.40, 1/3)], 0.08),
]

# ── RUN ALL ───────────────────────────────────────────────────────────────────
print("\n" + "="*65)
print("LADDER EXIT + TRAILING STOP ON LAST LOT")
print("All exits are INTRA-MONTH (daily simulation on v3.0 portfolio)")
print("="*65)

results = []
for label, ladder, trail in CONFIGS:
    r = run_ladder(label, ladder, trail)
    results.append((label, r))

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("SUMMARY vs v3.0 LOCKED BASELINE")
hdr = f"  {'Config':<48}  {'CAGR':>6}  {'Excess':>7}  {'DD':>7}  {'Sh':>5}  {'WF2':>6}  {'Partial':>7}  {'Trail':>5}  Gov"
print(hdr); print("-"*len(hdr))
for label, r in results:
    lbl = label[:48]
    print(f"  {lbl:<48}  {r['cagr']:>5.1f}%  {r['excess']:>+6.1f}%  {r['dd']:>+6.1f}%  "
          f"{r['sharpe']:>5.2f}  {r['wf2']:>+5.1f}%  {r['partial']:>7}  {r['full']:>5}  {r['gov']}")

print(f"\n  v3.0 LOCKED: CAGR={BASELINE['cagr']:.1f}% Excess={BASELINE['excess']:+.1f}% DD={BASELINE['dd']:.1f}% Sharpe={BASELINE['sharpe']:.2f}")

best_dd  = min(results, key=lambda x: x[1]['dd'])
best_sh  = max(results, key=lambda x: x[1]['sharpe'])
best_ex  = max(results, key=lambda x: x[1]['excess'])
gov_pass = [(l, r) for l, r in results if r['gov'] in ('STRICT', 'PARTIAL')]

print(f"\nBest DD:     {best_dd[0]} -> {best_dd[1]['dd']:.1f}%")
print(f"Best Sharpe: {best_sh[0]} -> {best_sh[1]['sharpe']:.2f}")
print(f"Best Excess: {best_ex[0]} -> {best_ex[1]['excess']:+.1f}%")
if gov_pass:
    print(f"\nGov PASS ({len(gov_pass)} configs):")
    for l, r in gov_pass:
        print(f"  {l}: excess={r['excess']:+.1f}% DD={r['dd']:.1f}% Sh={r['sharpe']:.2f} [{r['gov']}]")
else:
    print("\nNo config passed governance vs v3.0 locked baseline.")
    print("-> Compare vs same-run BASELINE to see relative improvement.")

# Year-by-year for best configs
print(f"\n{'='*65}")
print("YEAR-BY-YEAR: BASELINE vs best ladder configs")
base_ye = results[0][1]['ye']
cands   = sorted(results[1:], key=lambda x: x[1]['sharpe'], reverse=True)[:3]
hdrs = ['BASELINE'] + [l[:20] for l,_ in cands]
print(f"  {'Year':<6}  " + "  ".join(f"{h:>22}" for h in hdrs))
print("-"*100)
for yr in range(2017, 2027):
    vals = [base_ye.get(yr, 0)] + [r['ye'].get(yr, 0) for _, r in cands]
    row  = f"  {yr}  " + "  ".join(f"{v:>+21.1f}%" for v in vals)
    print(row)
