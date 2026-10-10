"""
35_intramonth_unified.py — Unified Intra-Month Exit (Dollar-Based Simulation)

Fixes scripts 32/33/34 which had NAV accounting bug:
  OLD: weight = fraction of initial nav (doesn't grow with portfolio)
  NEW: track actual shares × price in dollars (correct compounding)

Philosophy confirmed by CIO: "วัดที่ตัวๆ ไป" — each stock has its own exit signal
based on its own volatility profile, not market-wide signals.

EXIT METHODS TESTED:
  A. Trailing stop (fixed %): -8%, -12%, -15% from intra-month high
  B. ATR trailing stop: N × ATR(14) from high (adapts to each stock's own vol)
  C. Vol spike exit: 10d realized vol > X × 63d baseline (EGARCH proxy)
  D. Combo: vol spike OR ATR trail
  E. Ladder + ATR trail on last lot (take partial, let winner run, cut on vol signal)
  F. Ladder + vol spike on last lot
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
COST_RT  = 0.0015   # 0.15% round-trip → 0.075% per side
BASELINE = dict(excess=35.8, dd=-40.1, sharpe=1.44, cagr=57.0)

print("Loading data...")
dl  = v2.BacktestDataloader()
sig = dl.signals.copy()
sig['date'] = pd.to_datetime(sig['date'])

# ── Daily OHLCV tables ────────────────────────────────────────────────────────
print("Building signal tables...")
close_px = sig.pivot_table(index='date', columns='ticker', values='close').sort_index()

# Load full OHLCV from prices.parquet for high/low (signals.parquet has close only)
prices_path = pathlib.Path('data/backtest/prices.parquet')
if prices_path.exists():
    pr = pd.read_parquet(prices_path)
    pr['date'] = pd.to_datetime(pr['date'])
    high_px = pr.pivot_table(index='date', columns='ticker', values='high').sort_index()
    low_px  = pr.pivot_table(index='date', columns='ticker', values='low').sort_index()
    # Align to same date range as close_px
    high_px = high_px.reindex(close_px.index)
    low_px  = low_px.reindex(close_px.index)
    HAS_OHLCV = True
    print(f"  Loaded prices.parquet — high/low available")
else:
    high_px = close_px.copy()   # fallback: use close as proxy
    low_px  = close_px.copy()
    HAS_OHLCV = False
    print("  WARNING: prices.parquet not found — ATR will be approximate")

# Realized vol (annualized)
log_ret  = np.log(close_px / close_px.shift(1))
vol_10d  = log_ret.rolling(10,  min_periods=5).std()  * np.sqrt(252)
vol_63d  = log_ret.rolling(63,  min_periods=20).std() * np.sqrt(252)
vol_ratio = (vol_10d / vol_63d).clip(0, 10)   # spike indicator

# ATR(14) — exponential smoothed true range
prev_cl = close_px.shift(1)
common_cols = [c for c in close_px.columns if c in high_px.columns and c in low_px.columns]
atr_rows = {}
for col in common_cols:
    tr = pd.concat([
        (high_px[col] - low_px[col]).abs(),
        (high_px[col] - prev_cl[col]).abs(),
        (low_px[col]  - prev_cl[col]).abs()
    ], axis=1).max(axis=1)
    atr_rows[col] = tr.ewm(span=14, adjust=False).mean()
atr_14 = pd.DataFrame(atr_rows, index=close_px.index)

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

spy_rows     = sig[sig['ticker']=='SPY'][['date','close','ma_200']].dropna()
spy_bull_map = dict(zip(spy_rows['date'], spy_rows['close'] > spy_rows['ma_200']))
_orig_screen = v2.BacktestStrategy.screen

def get_portfolio(rebal_ts):
    """Run v3.1 screen, return {tkr: weight} and exposure."""
    is_bull = spy_bull_map.get(rebal_ts, True)

    def capped(self, date):
        r = _orig_screen(self, date)
        if r.empty: return r
        if len(r) > TOP_N:
            r = r.nlargest(TOP_N, 'rs_pct').copy()
            w = r['weight'].clip(upper=self.params.cap_pct)
            r['weight'] = w / w.sum() * r['weight'].sum()
        return r

    v2.BacktestStrategy.screen = capped
    p     = v2.StrategyParams(**BASE_PARAMS)
    strat = v2.BacktestStrategy(dl, p)
    out   = strat.screen(rebal_ts)
    v2.BacktestStrategy.screen = _orig_screen

    expo = 1.0 if is_bull else 0.5
    if out.empty: return {}, expo
    total_w = out['weight'].sum()
    return {r['ticker']: (r['weight']/total_w)*expo for _, r in out.iterrows()}, expo

# ── Dollar-based simulation ───────────────────────────────────────────────────
class Pos:
    """Tracks actual shares owned (dollar-correct compounding)."""
    __slots__ = ['shares','orig_shares','ep','high','lots']
    def __init__(self, shares, ep):
        self.shares = shares; self.orig_shares = shares
        self.ep = ep; self.high = ep; self.lots = 0

def simulate(label, exit_fn=None, ladder=None, verbose=True):
    """
    exit_fn(tkr, pos, px, dt) -> bool  — fire full exit of remaining shares
    ladder: [(tp_return, fraction_of_original_shares), ...]  — partial takes
    """
    cash = 1.0          # dollar value, starts at 1.0
    positions = {}      # tkr -> Pos (tracks actual shares)
    nav_series = {}
    qqq = close_px['QQQ'].dropna() if 'QQQ' in close_px.columns else None
    all_dates = close_px.index[(close_px.index >= START) & (close_px.index <= END)]
    n_partial = 0; n_full = 0

    for i, dt in enumerate(all_dates):

        # ── REBALANCE ─────────────────────────────────────────────────────────
        if dt in rebal_set:
            # Current total value
            total = cash
            for tkr, pos in positions.items():
                if tkr in close_px.columns and dt in close_px.index:
                    px = float(close_px.loc[dt, tkr])
                    if not np.isnan(px): total += pos.shares * px

            # Turnover cost (approx)
            new_port, expo = get_portfolio(dt)
            old_set = set(positions); new_set = set(new_port)
            churn = len(old_set ^ new_set) / max(len(old_set | new_set), 1)
            total *= (1 - COST_RT * churn)

            # Allocate dollars into new shares
            positions = {}
            cash = total * (1.0 - expo)
            for tkr, w in new_port.items():
                if tkr in close_px.columns and dt in close_px.index:
                    px = float(close_px.loc[dt, tkr])
                    if not np.isnan(px) and px > 0:
                        shares = total * w / px
                        positions[tkr] = Pos(shares, px)

            nav_series[dt] = total

        # ── INTRA-MONTH ───────────────────────────────────────────────────────
        else:
            to_exit = []

            for tkr, pos in list(positions.items()):
                if tkr not in close_px.columns or dt not in close_px.index: continue
                px = float(close_px.loc[dt, tkr])
                if np.isnan(px) or pos.shares < 1e-10: continue

                pos.high = max(pos.high, px)

                # Partial take-profit ladder
                if ladder:
                    ret = px / pos.ep - 1
                    for lot_idx, (tp_ret, frac) in enumerate(ladder):
                        if lot_idx < pos.lots: continue
                        if ret >= tp_ret:
                            sell_sh = pos.orig_shares * frac
                            sell_sh = min(sell_sh, pos.shares)
                            cash += sell_sh * px * (1 - COST_RT * 0.5)
                            pos.shares -= sell_sh
                            pos.lots += 1
                            n_partial += 1
                            break

                # Exit signal on remaining shares
                if pos.shares > 1e-10 and exit_fn:
                    try:
                        if exit_fn(tkr, pos, px, dt):
                            to_exit.append(tkr)
                    except Exception:
                        pass

            # Execute full exits (remaining shares → cash)
            for tkr in to_exit:
                pos = positions.pop(tkr)
                if tkr in close_px.columns and dt in close_px.index:
                    px = float(close_px.loc[dt, tkr])
                    if not np.isnan(px):
                        cash += pos.shares * px * (1 - COST_RT * 0.5)
                        pos.shares = 0
                        n_full += 1

            # NAV = cash + mark-to-market of all positions
            total = cash
            for tkr, pos in positions.items():
                if tkr in close_px.columns and dt in close_px.index:
                    px = float(close_px.loc[dt, tkr])
                    if not np.isnan(px): total += pos.shares * px
            nav_series[dt] = total

    # ── Stats ─────────────────────────────────────────────────────────────────
    nav_s  = pd.Series(nav_series).sort_index()
    years  = (nav_s.index[-1] - nav_s.index[0]).days / 365.25
    p_cagr = (nav_s.iloc[-1] / nav_s.iloc[0]) ** (1/years) - 1

    if qqq is not None:
        qr     = qqq.pct_change().dropna()
        qnav   = (1 + qr).cumprod().reindex(nav_s.index, method='ffill')
        q_cagr = (qnav.iloc[-1] / qnav.iloc[0]) ** (1/years) - 1
        excess = (p_cagr - q_cagr) * 100
    else:
        excess = 0

    dd      = ((nav_s - nav_s.cummax()) / nav_s.cummax()).min() * 100
    pct_ret = nav_s.pct_change().dropna()
    sharpe  = (pct_ret.mean() / pct_ret.std()) * np.sqrt(252) if pct_ret.std() > 0 else 0

    ye = {}
    if qqq is not None:
        for yr in range(2017, 2027):
            yn = nav_s[nav_s.index.year == yr]
            yq = qqq[qqq.index.year == yr]
            if len(yn) > 5 and len(yq) > 5:
                ye[yr] = ((yn.iloc[-1]/yn.iloc[0]) - (yq.iloc[-1]/yq.iloc[0])) * 100

    wins = sum(1 for v in ye.values() if v > 0)
    wf2  = (ye.get(2022,0) + ye.get(2023,0)) / 2
    cagr_p = p_cagr * 100

    ex_ok = excess > BASELINE['excess']
    dd_ok = dd     > BASELINE['dd']
    sh_ok = sharpe > BASELINE['sharpe']
    gov   = 'STRICT'  if (ex_ok and dd_ok and sh_ok) else \
            'PARTIAL' if ((ex_ok or sh_ok) and dd_ok) else 'no'

    yr_str = ' '.join(f"{yr}:{'+' if v>=0 else ''}{v:.1f}%" for yr,v in sorted(ye.items()))

    if verbose:
        print(f"\n{'='*65}")
        print(f"  {label}")
        print(f"  CAGR={cagr_p:.1f}% | Excess={excess:+.1f}% | DD={dd:.1f}%")
        print(f"  Sharpe={sharpe:.2f} | Win={wins}/{len(ye)} | WF2={wf2:+.1f}% | Gov={gov}")
        print(f"  Partial exits={n_partial} | Full exits={n_full}")
        print(f"  {yr_str}")

    return dict(cagr=cagr_p, excess=excess, dd=dd, sharpe=sharpe,
                wins=wins, wf2=wf2, gov=gov, np=n_partial, nf=n_full, ye=ye)

# ── Exit functions (per-stock, not market-wide) ───────────────────────────────

def trail_pct(pct):
    def fn(tkr, pos, px, dt):
        return px < pos.high * (1 - pct)
    return fn

def atr_trail(n):
    def fn(tkr, pos, px, dt):
        if tkr not in atr_14.columns or dt not in atr_14.index: return False
        a = float(atr_14.loc[dt, tkr])
        return not np.isnan(a) and a > 0 and px < pos.high - n * a
    return fn

def vol_spike(thr):
    def fn(tkr, pos, px, dt):
        if tkr not in vol_ratio.columns or dt not in vol_ratio.index: return False
        r = float(vol_ratio.loc[dt, tkr])
        return not np.isnan(r) and r > thr
    return fn

def either(f1, f2):
    return lambda tkr, pos, px, dt: f1(tkr,pos,px,dt) or f2(tkr,pos,px,dt)

# ── Configs ───────────────────────────────────────────────────────────────────
CONFIGS = [
    # Baseline
    ("BASELINE — no exit",                    None,              None),

    # A. Fixed trailing stop
    ("TRAIL 8%",                              trail_pct(0.08),   None),
    ("TRAIL 12%",                             trail_pct(0.12),   None),
    ("TRAIL 15%",                             trail_pct(0.15),   None),

    # B. ATR trailing stop (per-stock adaptive)
    ("ATR 2.0x from high",                    atr_trail(2.0),    None),
    ("ATR 2.5x from high",                    atr_trail(2.5),    None),
    ("ATR 3.0x from high",                    atr_trail(3.0),    None),

    # C. Vol spike (EGARCH proxy — exits when stock's own vol explodes)
    ("VOL SPIKE 1.5x (aggressive)",           vol_spike(1.5),    None),
    ("VOL SPIKE 2.0x",                        vol_spike(2.0),    None),
    ("VOL SPIKE 2.5x",                        vol_spike(2.5),    None),

    # D. Combo: vol OR ATR (fires whichever comes first)
    ("VOL 2.0x OR ATR 2.5x",                 either(vol_spike(2.0), atr_trail(2.5)), None),
    ("VOL 2.5x OR ATR 2.5x",                 either(vol_spike(2.5), atr_trail(2.5)), None),
    ("VOL 2.5x OR ATR 3.0x",                 either(vol_spike(2.5), atr_trail(3.0)), None),

    # E. Ladder + ATR trail on last lot
    ("LADDER(+15%,+30%) + ATR-2.5x last",    atr_trail(2.5),    [(0.15, 1/3),(0.30, 1/3)]),
    ("LADDER(+20%,+40%) + ATR-3.0x last",    atr_trail(3.0),    [(0.20, 1/3),(0.40, 1/3)]),
    ("LADDER(+15%,+30%) + TRAIL-12% last",   trail_pct(0.12),   [(0.15, 1/3),(0.30, 1/3)]),
    ("LADDER(+10%,+20%) + ATR-2.0x last",    atr_trail(2.0),    [(0.10, 1/3),(0.20, 1/3)]),

    # F. Ladder + vol spike on last lot (aggressive — exit on panic vol)
    ("LADDER(+15%,+30%) + VOL-2.0x last",    vol_spike(2.0),    [(0.15, 1/3),(0.30, 1/3)]),
    ("LADDER(+20%,+40%) + VOL-2.5x last",    vol_spike(2.5),    [(0.20, 1/3),(0.40, 1/3)]),

    # G. Ladder + combo (most complete)
    ("LADDER(+15%,+30%) + (VOL2.0x OR ATR2.5x)",
     either(vol_spike(2.0), atr_trail(2.5)),  [(0.15, 1/3),(0.30, 1/3)]),
]

# ── RUN ───────────────────────────────────────────────────────────────────────
print("\n" + "="*65)
print("INTRA-MONTH EXIT: LADDER + VOL/ATR (Dollar-Based Simulation)")
print("Philosophy: each stock exits on its OWN volatility signal")
print("="*65)

results = []
for label, fn, ladder in CONFIGS:
    r = simulate(label, fn, ladder)
    results.append((label, r))

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("FULL SUMMARY vs v3.1 LOCKED BASELINE")
print(f"  {'Config':<48}  {'Excess':>7}  {'DD':>7}  {'Sh':>5}  {'WF2':>6}  {'Part':>4}  {'Full':>4}  Gov")
print("-"*105)
for label, r in results:
    print(f"  {label:<48}  {r['excess']:>+6.1f}%  {r['dd']:>+6.1f}%  {r['sharpe']:>5.2f}"
          f"  {r['wf2']:>+5.1f}%  {r['np']:>4}  {r['nf']:>4}  {r['gov']}")

b_ref = results[0][1]
print(f"\n  v3.1 LOCKED: Excess={BASELINE['excess']:+.1f}% DD={BASELINE['dd']:.1f}% Sharpe={BASELINE['sharpe']:.2f}")
print(f"  Same-run Baseline: Excess={b_ref['excess']:+.1f}% DD={b_ref['dd']:.1f}% Sharpe={b_ref['sharpe']:.2f}")

best_dd  = min(results[1:], key=lambda x: x[1]['dd'])
best_sh  = max(results[1:], key=lambda x: x[1]['sharpe'])
best_ex  = max(results[1:], key=lambda x: x[1]['excess'])
gov_pass = [(l,r) for l,r in results if r['gov'] in ('STRICT','PARTIAL')]

print(f"\nBest DD:     {best_dd[0]}")
print(f"  -> DD={best_dd[1]['dd']:.1f}% | Excess={best_dd[1]['excess']:+.1f}% | Sharpe={best_dd[1]['sharpe']:.2f}")
print(f"Best Sharpe: {best_sh[0]}")
print(f"  -> Sharpe={best_sh[1]['sharpe']:.2f} | DD={best_sh[1]['dd']:.1f}%")
print(f"Best Excess: {best_ex[0]}")
print(f"  -> Excess={best_ex[1]['excess']:+.1f}% | DD={best_ex[1]['dd']:.1f}%")

if gov_pass:
    print(f"\nGov PASS ({len(gov_pass)} configs):")
    for l, r in gov_pass:
        print(f"  [{r['gov']}] {l}")
        print(f"    Excess={r['excess']:+.1f}% DD={r['dd']:.1f}% Sh={r['sharpe']:.2f} WF2={r['wf2']:+.1f}%")
else:
    print(f"\nNo config passed v3.1 governance.")
    print("  -> Key question: does same-run baseline beat itself? (DD & Sharpe)")
    top3 = sorted(results[1:], key=lambda x: x[1]['sharpe'], reverse=True)[:3]
    print(f"  -> Top 3 by Sharpe vs same-run baseline (Sh={b_ref['sharpe']:.2f} DD={b_ref['dd']:.1f}%):")
    for l, r in top3:
        dd_d  = r['dd']     - b_ref['dd']
        sh_d  = r['sharpe'] - b_ref['sharpe']
        ex_d  = r['excess'] - b_ref['excess']
        print(f"     {l}: Sh{sh_d:+.2f} DD{dd_d:+.1f}% Ex{ex_d:+.1f}%")

# Year-by-year top 3 by Sharpe
print(f"\n{'='*65}")
print("YEAR-BY-YEAR: baseline vs top configs by Sharpe")
top3 = sorted(results[1:], key=lambda x: x[1]['sharpe'], reverse=True)[:3]
print(f"  {'Yr':<6}  {'BASE':>8}" + "".join(f"  {l[:16]:>16}" for l,_ in top3))
print("-"*75)
for yr in range(2017, 2027):
    bv  = b_ref['ye'].get(yr, 0)
    row = f"  {yr}  {bv:>+7.1f}%"
    for _, r in top3:
        v = r['ye'].get(yr, 0); flag = '*' if abs(v-bv) > 5 else ' '
        row += f"  {v:>+15.1f}%{flag}"
    print(row)
