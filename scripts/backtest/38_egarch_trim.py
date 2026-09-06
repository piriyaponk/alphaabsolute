"""
38_egarch_trim.py — EGARCH-style Trim 1/3 + Auto-Rebuy (per-stock)

Ported from template: models/top_country_v2/egarch_cycle_v4.py

CONCEPT:
  - When a stock's 10d realized vol spikes > X × 63d baseline: TRIM 1/3 (not full exit)
  - After trim: wait up to rebuy_window trading days
  - If price recovers (above EMA21 or vol normalizes): REBUY the trimmed 1/3
  - If no rebuy trigger: rebuy anyway at window end (catch the recovery)
  - Next trim cooldown: 10 trading days from last trim (avoid repeated trims)

ADVANTAGE OVER FULL EXIT:
  - Partial reduction = less opportunity cost if stock continues higher
  - Auto-rebuy = captures recovery, doesn't miss the rest of the move
  - Per-stock signal = each stock managed independently (CIO philosophy: วัดที่ตัวๆ ไป)

Uses correct dollar-based NAV simulation (fixed from script 35).
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
BASELINE = dict(excess=35.8, dd=-40.1, sharpe=1.44)

print("Loading data...")
dl  = v2.BacktestDataloader()
sig = dl.signals.copy()
sig['date'] = pd.to_datetime(sig['date'])
close_px = sig.pivot_table(index='date', columns='ticker', values='close').sort_index()

# Load OHLCV for EMA21 calculation
pr = pd.read_parquet('data/backtest/prices.parquet')
pr['date'] = pd.to_datetime(pr['date'])
# EMA21
ema21 = {}
for tkr, grp in pr.groupby('ticker'):
    ema21[tkr] = grp.set_index('date')['close'].ewm(span=21, adjust=False).mean()
ema21_df = pd.DataFrame(ema21).reindex(close_px.index)

# Vol ratio (10d/63d realized vol) — EGARCH proxy
log_ret  = np.log(close_px / close_px.shift(1))
vol_10d  = log_ret.rolling(10, min_periods=5).std() * np.sqrt(252)
vol_63d  = log_ret.rolling(63, min_periods=20).std() * np.sqrt(252)
vol_ratio = (vol_10d / vol_63d).clip(0, 10)

print(f"  Data loaded. Dates={len(close_px)} Tickers={close_px.shape[1]}")

# ── Rebalance dates (same as script 35) ─────────────────────────────────────
all_dates   = pd.DatetimeIndex(sorted(sig[sig['ticker']=='SPY']['date'].unique()))
rebal_dates = pd.DatetimeIndex(
    all_dates[all_dates.to_series().groupby(all_dates.to_period('M')).transform('max') == all_dates]
)
START = pd.Timestamp('2017-01-01'); END = pd.Timestamp('2026-08-20')
rebal_dates = rebal_dates[(rebal_dates >= START) & (rebal_dates <= END)]
rebal_set   = set(rebal_dates)

spy_rows     = sig[sig['ticker']=='SPY'][['date','close','ma_200']].dropna()
spy_bull_map = dict(zip(spy_rows['date'], spy_rows['close'] > spy_rows['ma_200']))
_orig_screen = v2.BacktestStrategy.screen

def get_portfolio(rebal_ts):
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

    is_bull = spy_bull_map.get(rebal_ts, True)
    expo = 1.0 if is_bull else 0.5
    if out.empty: return {}, expo
    total_w = out['weight'].sum()
    return {r['ticker']: (r['weight']/total_w)*expo for _, r in out.iterrows()}, expo

# ── EGARCH Trim + Rebuy Position ─────────────────────────────────────────────
class EGARCHPos:
    __slots__ = ['shares','orig_shares','ep','trim_frac','trimmed_shares','days_since_trim','cooldown_days','rebuy_window']
    def __init__(self, shares, ep, rebuy_window=10, cooldown_days=10):
        self.shares         = shares
        self.orig_shares    = shares
        self.ep             = ep
        self.trim_frac      = 0.0       # fraction of orig shares trimmed
        self.trimmed_shares = 0.0       # shares held in "trimmed" pool waiting rebuy
        self.days_since_trim = -1        # -1 = not trimmed; 0+ = days elapsed
        self.cooldown_days  = cooldown_days
        self.rebuy_window   = rebuy_window

# ── Simulation ────────────────────────────────────────────────────────────────

def simulate(label, vol_thr=2.0, trim_frac=1/3, rebuy_window=10, cooldown_days=10,
             rebuy_trigger='vol_normalize', verbose=True):
    """
    vol_thr: fire trim when vol_ratio > vol_thr
    trim_frac: fraction of position to trim (template default = 1/3)
    rebuy_window: days to wait for rebuy trigger before forcing rebuy
    cooldown_days: minimum days between two trims on same stock
    rebuy_trigger: 'vol_normalize' (vol_ratio < 1.2), 'ema21' (price > EMA21), 'either'
    """
    cash = 1.0
    positions = {}   # tkr -> EGARCHPos
    nav_series = {}
    qqq = close_px['QQQ'].dropna() if 'QQQ' in close_px.columns else None
    sim_dates = close_px.index[(close_px.index >= START) & (close_px.index <= END)]
    n_trims = 0; n_rebuys = 0; n_forced = 0

    for dt in sim_dates:
        # ── REBALANCE ─────────────────────────────────────────────────────
        if dt in rebal_set:
            total = cash + sum(
                pos.shares * float(close_px.loc[dt, tkr])
                for tkr, pos in positions.items()
                if tkr in close_px.columns and not np.isnan(float(close_px.loc[dt, tkr]))
            )
            new_port, expo = get_portfolio(dt)
            old_set = set(positions); new_set = set(new_port)
            churn = len(old_set ^ new_set) / max(len(old_set | new_set), 1)
            total *= (1 - COST_RT * churn)
            positions = {}
            cash = total * (1.0 - expo)
            for tkr, w in new_port.items():
                if tkr in close_px.columns and dt in close_px.index:
                    px = float(close_px.loc[dt, tkr])
                    if not np.isnan(px) and px > 0:
                        sh = total * w / px
                        positions[tkr] = EGARCHPos(sh, px, rebuy_window, cooldown_days)
            nav_series[dt] = total

        # ── INTRA-MONTH ───────────────────────────────────────────────────
        else:
            for tkr, pos in list(positions.items()):
                if tkr not in close_px.columns or dt not in close_px.index: continue
                px = float(close_px.loc[dt, tkr])
                if np.isnan(px) or pos.shares < 1e-10: continue

                vr = float(vol_ratio.loc[dt, tkr]) if (tkr in vol_ratio.columns and dt in vol_ratio.index) else 1.0
                e21 = float(ema21_df.loc[dt, tkr]) if (tkr in ema21_df.columns and dt in ema21_df.index) else px

                # Update trim counter
                if pos.days_since_trim >= 0:
                    pos.days_since_trim += 1

                # ── REBUY check (if we have trimmed shares waiting) ───────
                if pos.trimmed_shares > 1e-10 and pos.days_since_trim >= 0:
                    rebuy = False
                    if pos.days_since_trim >= rebuy_window:
                        rebuy = True; n_forced += 1  # forced rebuy at window end
                    elif rebuy_trigger == 'vol_normalize' and vr < 1.2:
                        rebuy = True; n_rebuys += 1
                    elif rebuy_trigger == 'ema21' and px > e21:
                        rebuy = True; n_rebuys += 1
                    elif rebuy_trigger == 'either' and (vr < 1.2 or px > e21):
                        rebuy = True; n_rebuys += 1

                    if rebuy:
                        # Buy back trimmed shares (cost applied)
                        cost = pos.trimmed_shares * px * COST_RT * 0.5
                        rebuy_cost = pos.trimmed_shares * px + cost
                        if cash >= rebuy_cost:
                            cash -= rebuy_cost
                            pos.shares += pos.trimmed_shares
                        else:
                            # Partial rebuy with available cash
                            affordable = cash / (px * (1 + COST_RT*0.5))
                            pos.shares += affordable
                            cash = 0
                        pos.trimmed_shares = 0.0
                        pos.days_since_trim = -1  # reset

                # ── TRIM check ────────────────────────────────────────────
                elif (pos.days_since_trim < 0 or pos.days_since_trim >= cooldown_days):
                    if not np.isnan(vr) and vr > vol_thr and pos.shares > 1e-10:
                        # Trim trim_frac of ORIGINAL position
                        trim_sh = pos.orig_shares * trim_frac
                        trim_sh = min(trim_sh, pos.shares)
                        sell_val = trim_sh * px * (1 - COST_RT * 0.5)
                        cash += sell_val
                        pos.shares -= trim_sh
                        pos.trimmed_shares = trim_sh   # remember for rebuy
                        pos.days_since_trim = 0
                        n_trims += 1

            # NAV
            total = cash
            for tkr, pos in positions.items():
                if tkr in close_px.columns and dt in close_px.index:
                    px = float(close_px.loc[dt, tkr])
                    if not np.isnan(px): total += pos.shares * px
            nav_series[dt] = total

    # ── Stats ─────────────────────────────────────────────────────────────
    nav_s  = pd.Series(nav_series).sort_index()
    years  = (nav_s.index[-1] - nav_s.index[0]).days / 365.25
    p_cagr = (nav_s.iloc[-1] / nav_s.iloc[0]) ** (1/years) - 1

    if qqq is not None:
        qr    = qqq.pct_change().dropna()
        qnav  = (1+qr).cumprod().reindex(nav_s.index, method='ffill')
        q_cagr = (qnav.iloc[-1]/qnav.iloc[0]) ** (1/years) - 1
        excess = (p_cagr - q_cagr) * 100
    else: excess = 0

    dd     = ((nav_s - nav_s.cummax()) / nav_s.cummax()).min() * 100
    ret    = nav_s.pct_change().dropna()
    sharpe = (ret.mean() / ret.std()) * np.sqrt(252) if ret.std() > 0 else 0

    ye = {}
    if qqq is not None:
        for yr in range(2017,2027):
            yn = nav_s[nav_s.index.year==yr]; yq = qqq[qqq.index.year==yr]
            if len(yn)>5 and len(yq)>5:
                ye[yr] = ((yn.iloc[-1]/yn.iloc[0]) - (yq.iloc[-1]/yq.iloc[0]))*100

    wins = sum(1 for v in ye.values() if v > 0)
    wf2  = (ye.get(2022,0)+ye.get(2023,0))/2

    ex_ok = excess>BASELINE['excess']; dd_ok=dd>BASELINE['dd']; sh_ok=sharpe>BASELINE['sharpe']
    gov   = 'STRICT' if (ex_ok and dd_ok and sh_ok) else 'PARTIAL' if ((ex_ok or sh_ok) and dd_ok) else 'no'
    yr_str = ' '.join(f"{yr}:{'+' if v>=0 else ''}{v:.1f}%" for yr,v in sorted(ye.items()))

    if verbose:
        print(f"\n{'='*65}")
        print(f"  {label}")
        print(f"  CAGR={p_cagr*100:.1f}% | Excess={excess:+.1f}% | DD={dd:.1f}%")
        print(f"  Sharpe={sharpe:.2f} | Win={wins}/{len(ye)} | WF2={wf2:+.1f}% | Gov={gov}")
        print(f"  Trims={n_trims} | Rebuys={n_rebuys} | Forced={n_forced}")
        print(f"  {yr_str}")

    return dict(label=label, cagr=p_cagr*100, excess=excess, dd=dd, sharpe=sharpe,
                wins=wins, wf2=wf2, gov=gov, ye=ye, n_trims=n_trims, n_rebuys=n_rebuys)

# ── CONFIGS ──────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("EGARCH TRIM 1/3 + AUTO-REBUY (per-stock)")
print(f"{'='*65}")

results = []

# Baseline
results.append(simulate("BASELINE — no trim", vol_thr=99.0))

# Vol threshold variants
for vt in [1.5, 2.0, 2.5]:
    results.append(simulate(f"TRIM 1/3 at vol>{vt}x | rebuy=vol_norm | window=10d",
                            vol_thr=vt, trim_frac=1/3, rebuy_window=10, rebuy_trigger='vol_normalize'))

# Trim fraction variants (vol=2.0 best from above usually)
for frac, fname in [(0.25,'1/4'), (1/3,'1/3'), (0.50,'1/2')]:
    results.append(simulate(f"TRIM {fname} at vol>2.0x | rebuy=vol_norm",
                            vol_thr=2.0, trim_frac=frac, rebuy_trigger='vol_normalize'))

# Rebuy trigger variants
for rt in ['vol_normalize','ema21','either']:
    results.append(simulate(f"vol>2.0x trim 1/3 | rebuy={rt} | window=10d",
                            vol_thr=2.0, trim_frac=1/3, rebuy_trigger=rt))

# Rebuy window variants
for rw in [5, 10, 20]:
    results.append(simulate(f"vol>2.0x trim 1/3 | rebuy=vol_norm | window={rw}d",
                            vol_thr=2.0, trim_frac=1/3, rebuy_window=rw, rebuy_trigger='vol_normalize'))

# Cooldown variants
for cd in [5, 10, 20]:
    results.append(simulate(f"vol>2.0x trim 1/3 | cooldown={cd}d",
                            vol_thr=2.0, trim_frac=1/3, cooldown_days=cd, rebuy_trigger='either'))

# ── SUMMARY ──────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("SUMMARY — EGARCH TRIM + REBUY")
print(f"  {'Config':<52}  {'Excess':>7}  {'DD':>7}  {'Sh':>5}  {'Trims':>5}  {'WF2':>6}  Gov")
print("-"*110)
b = results[0]
for r in results:
    print(f"  {r['label']:<52}  {r['excess']:>+6.1f}%  {r['dd']:>+6.1f}%  {r['sharpe']:>5.2f}"
          f"  {r['n_trims']:>5}  {r['wf2']:>+5.1f}%  {r['gov']}")

print(f"\n  v3.1 LOCKED: Excess={BASELINE['excess']:+.1f}% DD={BASELINE['dd']:.1f}% Sharpe={BASELINE['sharpe']:.2f}")
print(f"  Same-run Baseline: Excess={b['excess']:+.1f}% DD={b['dd']:.1f}% Sharpe={b['sharpe']:.2f}")

gov_pass = [(r['label'],r) for r in results if r['gov'] in ('STRICT','PARTIAL')]
if gov_pass:
    print(f"\nGov PASS ({len(gov_pass)}):")
    for lbl,r in gov_pass:
        print(f"  [{r['gov']}] {lbl}")
        print(f"    Excess={r['excess']:+.1f}% DD={r['dd']:.1f}% Sh={r['sharpe']:.2f} WF2={r['wf2']:+.1f}%")
else:
    print("\nNo config passed governance. Top 3 by DD improvement vs baseline:")
    top3 = sorted(results[1:], key=lambda x: x['dd'], reverse=True)[:3]
    for r in top3:
        print(f"  {r['label']}: DD{r['dd']-b['dd']:+.1f}% Ex{r['excess']-b['excess']:+.1f}% Sh{r['sharpe']-b['sharpe']:+.2f}")
