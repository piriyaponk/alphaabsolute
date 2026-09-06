"""
44_dd_reduction_v2.py — DD Reduction Round 2: 5 New Approaches
Self-contained mini backtest loop (fast, no v2 engine dependency).

Approaches tested:
  1. Portfolio vol targeting (scale positions when port_vol > target)
  2. HYG/LQD credit ratio early warning (reduce when credit stress)
  3. Defensive momentum rotation (trend_consistency filter on warning)
  4. Drawdown-based top_n reduction (fewer names when already hurting)
  5. ERC weighting (covariance-aware, each stock = equal risk)

GOVERNANCE: v4.0 LOCKED — Excess=+43.4% | DD=-38.2% | Sharpe=1.46
"""
import sys, io, os, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))

import pandas as pd
import numpy as np
import requests
import warnings
warnings.filterwarnings('ignore')

LOCKED = dict(excess=43.4, dd=-38.2, sharpe=1.46)

# ── LOAD DATA ─────────────────────────────────────────────────────────────────
print("Loading data...")
sig = pd.read_parquet('data/backtest/signals.parquet')
sig['date'] = pd.to_datetime(sig['date'])
sig = sig.sort_values(['date','ticker'])

close_px = sig.pivot_table(index='date', columns='ticker', values='close').sort_index()
log_ret  = np.log(close_px / close_px.shift(1))

qqq_close = close_px.get('QQQ', pd.Series(dtype=float))

vol_63d = log_ret.rolling(63, min_periods=20).std() * np.sqrt(252)

beta_lkp = {}
for row in sig[['date','ticker','beta_252']].dropna().itertuples():
    b = float(row.beta_252)
    if not np.isnan(b):
        beta_lkp[(row.date, row.ticker)] = max(0.3, min(b, 4.0))

# IWM regime
all_dates = sig['date'].sort_values().unique()
if 'IWM' in close_px.columns:
    iwm = close_px['IWM'].dropna()
    iwm_ma200 = iwm.rolling(200, min_periods=150).mean()
    iwm_bull = (iwm > iwm_ma200).reindex(all_dates, method='ffill').fillna(1.0)
else:
    iwm_bull = pd.Series(1.0, index=all_dates)

has_tc = 'trend_consistency' in sig.columns
print(f"  trend_consistency available: {has_tc}")

# ── HYG/LQD ──────────────────────────────────────────────────────────────────
def fetch_yahoo(ticker):
    import ssl; ssl._create_default_https_context = ssl._create_unverified_context
    url = (f'https://query2.finance.yahoo.com/v8/finance/chart/{ticker}'
           f'?interval=1d&period1=1420070400&period2=1756684800&events=history')
    try:
        r = requests.get(url, headers={'User-Agent':'Mozilla/5.0'}, verify=False, timeout=20)
        d = r.json()['chart']['result'][0]
        s = pd.Series(d['indicators']['adjclose'][0]['adjclose'],
                      index=pd.to_datetime(d['timestamp'], unit='s').normalize())
        return s.sort_index().dropna()
    except: return None

CREDIT_FILE = 'data/backtest/hyg_lqd_prices.parquet'
hyg = lqd = None
if os.path.exists(CREDIT_FILE):
    credit_df = pd.read_parquet(CREDIT_FILE)
    credit_df.index = pd.to_datetime(credit_df.index)
    hyg, lqd = credit_df['HYG'], credit_df['LQD']
    print(f"  HYG/LQD cached: {hyg.index.min().date()} -> {hyg.index.max().date()}")
else:
    print("  Downloading HYG and LQD...")
    hyg = fetch_yahoo('HYG'); lqd = fetch_yahoo('LQD')
    if hyg is not None and lqd is not None:
        pd.DataFrame({'HYG': hyg, 'LQD': lqd}).to_parquet(CREDIT_FILE)
        print(f"  Downloaded: {hyg.index.min().date()} -> {hyg.index.max().date()}")

if hyg is not None and lqd is not None:
    cr = (hyg / lqd).reindex(all_dates, method='ffill')
    cr_ma20 = cr.rolling(20, min_periods=10).mean()
    credit_stress = (cr < cr_ma20).astype(float).reindex(all_dates, method='ffill').fillna(0)
    print(f"  Credit stress: {credit_stress.mean()*100:.1f}% of trading days")
else:
    credit_stress = pd.Series(0.0, index=all_dates)
    print("  [!] HYG/LQD not available — approach 2/3 will show no effect")


# ── WEIGHT HELPERS ────────────────────────────────────────────────────────────
def vp_weights(ts, tickers, exposure, base_cap=0.18, beta_cap=0.12):
    vols, maxws = [], []
    for t in tickers:
        v = float(vol_63d.loc[ts, t]) if (ts in vol_63d.index and t in vol_63d.columns) else 0.30
        vols.append(max(v if not np.isnan(v) else 0.30, 0.01))
        b = beta_lkp.get((ts, t), 1.0)
        maxws.append(min(base_cap, beta_cap / max(b, 0.5)))
    raw_w = 1.0 / np.array(vols); raw_w /= raw_w.sum()
    w = raw_w.copy(); mw = np.array(maxws)
    for _ in range(25):
        over = w > mw
        if not over.any(): break
        exc = (w[over] - mw[over]).sum(); w[over] = mw[over]
        recv = ~over
        if not recv.any(): break
        w[recv] += exc * w[recv] / w[recv].sum()
    return (w * exposure).tolist()

def erc_weights(ts, tickers, exposure, base_cap=0.18, beta_cap=0.12):
    recent = log_ret.loc[log_ret.index <= ts].tail(63)[tickers].dropna(axis=1, how='any')
    valid  = recent.columns.tolist()
    if len(valid) < 2:
        return vp_weights(ts, tickers, exposure, base_cap, beta_cap)
    df_t = [t for t in tickers if t in valid]
    cov = recent[df_t].cov().values * 252
    n   = len(df_t)
    w   = np.ones(n) / n
    betas_v = [beta_lkp.get((ts, t), 1.0) for t in df_t]
    mw = np.array([min(base_cap, beta_cap / max(b, 0.5)) for b in betas_v])
    for _ in range(60):
        port_var = w @ cov @ w
        if port_var < 1e-10: break
        rc = w * (cov @ w); target = port_var / n
        w = w * (target / np.maximum(rc, 1e-10))
        w = np.maximum(w, 0); w = np.minimum(w, mw)
        tot = w.sum()
        if tot < 1e-10: break
        w = w / tot
    # Map back to full ticker list (some may have been dropped due to NaN)
    wmap = {t: w[i] for i, t in enumerate(df_t)}
    return [(wmap.get(t, 0.0) * exposure) for t in tickers]


# ── BASE SCREENER ─────────────────────────────────────────────────────────────
def screen(ts, ds, bull, rs_thresh=80, top_n=15, exposure=None,
           tc_filter=False, weight='vp'):
    if ds.empty: return [], []
    vt  = 0.75 if bull else 0.95
    exp = (1.0 if bull else 0.5) if exposure is None else exposure
    mask = (ds['rs_pct'] >= rs_thresh) & (ds['price_vs_ma200_pct'] >= 0)
    if 'vol_trend' in ds.columns: mask &= ds['vol_trend'] >= vt
    if 'adtv_63m' in ds.columns:  mask &= ds['adtv_63m'] >= 10
    df = ds[mask].copy()
    if tc_filter and has_tc and 'trend_consistency' in df.columns:
        df = df[df['trend_consistency'] >= 55].copy()
    if df.empty: return [], []
    df = df.nlargest(top_n, 'rs_pct')
    tkrs = df['ticker'].tolist()
    wts  = vp_weights(ts, tkrs, exp) if weight == 'vp' else erc_weights(ts, tkrs, exp)
    return tkrs, wts


# ── MINI BACKTEST ─────────────────────────────────────────────────────────────
def run_backtest(label, screen_fn, start='2017-01-01', end='2026-08-20'):
    t0 = time.time()
    sd, ed = pd.Timestamp(start), pd.Timestamp(end)
    months = pd.date_range(start=sd, end=ed, freq='ME')

    equity = 1.0; peak = 1.0; max_dd = 0.0
    rets = []; monthly_rets = []
    yearly = {}; prev_h = set()

    for i, me in enumerate(months[:-1]):
        ne = months[i + 1]
        if me < sd or me > ed: continue

        ts = me
        bull = bool(iwm_bull.get(ts, 1.0) >= 0.5)
        cst  = bool(credit_stress.get(ts, 0.0) >= 0.5)
        pv   = float(np.std(monthly_rets[-6:]) * np.sqrt(12)) if len(monthly_rets) >= 3 else 0.20
        dd   = (equity - peak) / peak if peak > 0 else 0.0

        day_sig = sig[sig['date'] == ts].copy()
        if day_sig.empty:
            closest = sig[sig['date'] <= ts]['date'].max()
            if pd.isna(closest): month_ret = 0.0; rets.append(0.0); monthly_rets.append(0.0); continue
            day_sig = sig[sig['date'] == closest].copy()

        tkrs, wts = screen_fn(ts, day_sig, bull, cst, dd, pv)

        if not tkrs:
            month_ret = 0.0
        else:
            fwd = []
            for t, w in zip(tkrs, wts):
                if t not in close_px.columns: fwd.append(0.0); continue
                try:
                    p0 = close_px.loc[close_px.index <= ts, t].dropna().iloc[-1]
                    p1 = close_px.loc[close_px.index <= ne, t].dropna().iloc[-1]
                    fwd.append((p1 / p0 - 1) * w)
                except: fwd.append(0.0)
            gross = sum(fwd)
            new_h = set(tkrs)
            turn  = len(prev_h.symmetric_difference(new_h)) / max(len(prev_h | new_h), 1)
            month_ret = gross - 0.0015 * turn
            prev_h = new_h

        equity *= (1 + month_ret)
        monthly_rets.append(month_ret)
        if equity > peak: peak = equity
        dd2 = (equity - peak) / peak
        if dd2 < max_dd: max_dd = dd2
        rets.append(month_ret)

        yr = ne.year
        try:
            bm0 = qqq_close.loc[qqq_close.index <= ts].iloc[-1]
            bm1 = qqq_close.loc[qqq_close.index <= ne].iloc[-1]
            bm  = bm1 / bm0 - 1
        except: bm = 0.0
        yearly.setdefault(yr, {'p': 1.0, 'b': 1.0})
        yearly[yr]['p'] *= (1 + month_ret)
        yearly[yr]['b'] *= (1 + bm)

    n_yrs = (ed - sd).days / 365.25
    cagr  = (equity ** (1 / max(n_yrs, 0.1)) - 1) * 100
    try:
        q0 = qqq_close.loc[qqq_close.index >= sd].iloc[0]
        q1 = qqq_close.loc[qqq_close.index <= ed].iloc[-1]
        qqq_cagr = ((q1 / q0) ** (1 / n_yrs) - 1) * 100
    except: qqq_cagr = 0.0
    excess = cagr - qqq_cagr
    sharpe = (np.mean(rets) / np.std(rets) * np.sqrt(12)) if np.std(rets) > 1e-9 else 0
    ye     = {yr: (v['p'] - 1 - (v['b'] - 1)) * 100 for yr, v in yearly.items()}
    wins   = sum(1 for v in ye.values() if v > 0)
    elapsed = time.time() - t0
    print(f"  [{elapsed:.0f}s] {label}: CAGR={cagr:+.1f}% Ex={excess:+.1f}% DD={max_dd*100:.1f}% Sh={sharpe:.2f} Win={wins}/{len(ye)}")
    return dict(label=label, cagr=cagr, excess=excess, dd=max_dd*100,
                sharpe=sharpe, wins=wins, n=len(ye), ye=ye)


def gov(r):
    if r['excess'] > LOCKED['excess'] and r['dd'] > LOCKED['dd'] and r['sharpe'] > LOCKED['sharpe']:
        return 'STRICT'
    if r['dd'] > LOCKED['dd'] and (r['excess'] > LOCKED['excess'] or r['sharpe'] > LOCKED['sharpe']):
        return 'PARTIAL'
    if r['dd'] > LOCKED['dd']:
        return 'DD_ONLY'
    return 'FAIL'


# ═══════════════════════════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("44_dd_reduction_v2.py — 5 NEW DD REDUCTION APPROACHES")
print(f"{'='*65}")
print(f"  LOCKED: Excess={LOCKED['excess']:+.1f}% | DD={LOCKED['dd']:.1f}% | Sh={LOCKED['sharpe']:.2f}")

results = []

# BASELINE
print("\n[BASELINE v4.0]")
def scr_base(ts, ds, bull, cst, dd, pv):
    return screen(ts, ds, bull)
r = run_backtest("BASELINE v4.0", scr_base); results.append(r); base = r

# ── APPROACH 1: Portfolio Vol Targeting ──────────────────────────────────────
print(f"\n[Approach 1: Portfolio Vol Targeting]")
for target in [0.12, 0.15, 0.20]:
    def make_pvt(t=target):
        def fn(ts, ds, bull, cst, dd, pv):
            tkrs, wts = screen(ts, ds, bull)
            if not tkrs: return [], []
            scale = min(1.0, t / max(pv, 0.01))
            return tkrs, [w * scale for w in wts]
        return fn
    r = run_backtest(f"1: PortVol target={int(target*100)}%", make_pvt(target))
    results.append(r)

# ── APPROACH 2: HYG/LQD Credit Ratio ─────────────────────────────────────────
print(f"\n[Approach 2: Credit Ratio Early Warning]")
for credit_exp in [0.60, 0.70, 0.80]:
    def make_crd(ce=credit_exp):
        def fn(ts, ds, bull, cst, dd, pv):
            exp = 0.50 if not bull else (ce if cst else 1.0)
            return screen(ts, ds, bull, exposure=exp)
        return fn
    r = run_backtest(f"2: Credit -> {int(credit_exp*100)}% on stress", make_crd(credit_exp))
    results.append(r)

# ── APPROACH 3: Defensive Momentum Rotation ───────────────────────────────────
print(f"\n[Approach 3: Defensive Momentum Rotation]")
if has_tc:
    for w_exp in [0.70, 0.85]:
        def make_def(we=w_exp):
            def fn(ts, ds, bull, cst, dd, pv):
                if not bull: return screen(ts, ds, bull, exposure=0.50)
                elif cst:    return screen(ts, ds, bull, exposure=we, tc_filter=True)
                else:        return screen(ts, ds, bull)
            return fn
        r = run_backtest(f"3: Defensive {int(w_exp*100)}%+tc>=55", make_def(w_exp))
        results.append(r)
else:
    print("  [SKIP] trend_consistency not in signals")

# ── APPROACH 4: DD-Based Top_N ────────────────────────────────────────────────
print(f"\n[Approach 4: DD-Based Top_N]")
def scr_dd_topn(ts, ds, bull, cst, dd, pv):
    n = 15 if dd > -0.05 else (12 if dd > -0.10 else (8 if dd > -0.20 else (5 if dd > -0.30 else 3)))
    return screen(ts, ds, bull, top_n=n)
r = run_backtest("4: DD-based top_n (15/12/8/5/3)", scr_dd_topn); results.append(r)

def scr_dd_topn_v2(ts, ds, bull, cst, dd, pv):
    n = 15 if dd > -0.03 else (10 if dd > -0.08 else (6 if dd > -0.15 else 4))
    return screen(ts, ds, bull, top_n=n)
r = run_backtest("4b: DD-topn aggressive (15/10/6/4)", scr_dd_topn_v2); results.append(r)

# ── APPROACH 5: ERC Weighting ─────────────────────────────────────────────────
print(f"\n[Approach 5: ERC Weighting]")
def scr_erc(ts, ds, bull, cst, dd, pv):
    return screen(ts, ds, bull, weight='erc')
r = run_backtest("5: ERC weighting", scr_erc); results.append(r)

# ── COMBOS ────────────────────────────────────────────────────────────────────
print(f"\n[Combos]")
def scr_combo_crd_topn(ts, ds, bull, cst, dd, pv):
    n = 15 if dd > -0.05 else (10 if dd > -0.10 else (6 if dd > -0.20 else 3))
    exp = 0.50 if not bull else (0.70 if cst else 1.0)
    return screen(ts, ds, bull, top_n=n, exposure=exp)
r = run_backtest("C1: Credit+DD-topn", scr_combo_crd_topn); results.append(r)

def scr_combo_pvt_crd(ts, ds, bull, cst, dd, pv):
    exp = 0.50 if not bull else (0.70 if cst else 1.0)
    tkrs, wts = screen(ts, ds, bull, exposure=exp)
    if not tkrs: return [], []
    scale = min(1.0, 0.15 / max(pv, 0.01))
    return tkrs, [w * scale for w in wts]
r = run_backtest("C2: Credit+PortVol15%", scr_combo_pvt_crd); results.append(r)

# ── FULL SUMMARY ──────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("FULL RESULTS SUMMARY")
print(f"{'='*65}")
print(f"  {'Config':<45} {'CAGR':>7} {'Excess':>7} {'DD':>7} {'Sh':>5} {'W':>5}  Gov")
print("-"*85)
for r in results:
    g = gov(r); mk = '***' if g in ('STRICT','PARTIAL') else '   '
    print(f"  {mk} {r['label']:<45} {r['cagr']:>+6.1f}% {r['excess']:>+6.1f}% "
          f"{r['dd']:>+6.1f}% {r['sharpe']:>5.2f} {r['wins']:>2}/{r['n']}  [{g}]")
print(f"\n  LOCKED: Excess={LOCKED['excess']:+.1f}% | DD={LOCKED['dd']:.1f}% | Sh={LOCKED['sharpe']:.2f}")

# ── YEAR-BY-YEAR ──────────────────────────────────────────────────────────────
notable = [r for r in results[1:] if r['dd'] > base['dd'] or r['excess'] > base['excess']][:4]
if notable:
    print(f"\n{'='*65}")
    print("YEAR-BY-YEAR EXCESS vs QQQ (delta highlights)")
    print(f"{'='*65}")
    yrs = sorted(base['ye'].keys())
    print("  " + " ".join(f"{y:>6}" for y in yrs))
    for r in [base] + notable[:3]:
        row = " ".join(f"{r['ye'].get(y, 0):>+5.1f}%" for y in yrs)
        print(f"  {r['label'][:42]:<42}")
        print(f"  {row}")

# ── WALK-FORWARD on best ───────────────────────────────────────────────────────
wf_cands = sorted([r for r in results[1:] if r['dd'] > base['dd']], key=lambda r: r['dd'], reverse=True)[:2]
if wf_cands:
    print(f"\n{'='*65}")
    print("WALK-FORWARD — top DD improvers")
    print(f"{'='*65}")
    scr_map = {
        "BASELINE v4.0": scr_base,
        "4: DD-based top_n (15/12/8/5/3)": scr_dd_topn,
        "4b: DD-topn aggressive (15/10/6/4)": scr_dd_topn_v2,
        "5: ERC weighting": scr_erc,
        "C1: Credit+DD-topn": scr_combo_crd_topn,
        "C2: Credit+PortVol15%": scr_combo_pvt_crd,
    }
    # add credit screeners
    for ce in [0.60, 0.70, 0.80]:
        def _add(c=ce):
            scr_map[f"2: Credit -> {int(c*100)}% on stress"] = (lambda c2=c: lambda ts,ds,bull,cst,dd,pv: screen(ts,ds,bull,exposure=(0.50 if not bull else (c2 if cst else 1.0))))(c)
        _add(ce)
    for t in [0.12, 0.15, 0.20]:
        def _add_pvt(t2=t):
            def fn(ts, ds, bull, cst, dd, pv, _t=t2):
                tkrs, wts = screen(ts, ds, bull)
                if not tkrs: return [], []
                scale = min(1.0, _t / max(pv, 0.01))
                return tkrs, [w * scale for w in wts]
            scr_map[f"1: PortVol target={int(t2*100)}%"] = fn
        _add_pvt(t)
    if has_tc:
        for we in [0.70, 0.85]:
            def _add_def(we2=we):
                def fn(ts, ds, bull, cst, dd, pv, _we=we2):
                    if not bull: return screen(ts, ds, bull, exposure=0.50)
                    elif cst:    return screen(ts, ds, bull, exposure=_we, tc_filter=True)
                    else:        return screen(ts, ds, bull)
                scr_map[f"3: Defensive {int(we2*100)}%+tc>=55"] = fn
            _add_def(we)

    WF_WINDOWS = [('WF1','2020-01-01','2021-12-31'),
                  ('WF2','2022-01-01','2023-12-31'),
                  ('WF3','2024-01-01','2026-08-20')]
    wf_run = [("BASELINE", scr_base)] + [(r['label'], scr_map.get(r['label'], scr_base)) for r in wf_cands]
    print(f"\n  {'Config':<40}  {'WF1 2020-21':>14}  {'WF2 2022-23':>14}  {'WF3 2024-26':>14}")
    print("-"*88)
    for lbl, sfn in wf_run:
        parts = []
        for wname, ws, we in WF_WINDOWS:
            wr = run_backtest(f"{lbl}|{wname}", sfn, start=ws, end=we)
            parts.append(f"Ex{wr['excess']:+.0f}%/Sh{wr['sharpe']:.2f}")
        print(f"  {lbl:<40}  {'  '.join(parts)}")

# ── GOVERNANCE ────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
strict  = [r for r in results[1:] if gov(r) == 'STRICT']
partial = [r for r in results[1:] if gov(r) == 'PARTIAL']
dd_only = [r for r in results[1:] if gov(r) == 'DD_ONLY']

print(f"GOVERNANCE: STRICT({len(strict)}) | PARTIAL({len(partial)}) | DD_ONLY({len(dd_only)})")
if strict:
    print("  STRICT:");  [print(f"    {r['label']}: Ex={r['excess']:+.1f}% DD={r['dd']:.1f}% Sh={r['sharpe']:.2f}") for r in strict]
if partial:
    print("  PARTIAL:"); [print(f"    {r['label']}: Ex={r['excess']:+.1f}% DD={r['dd']:.1f}% Sh={r['sharpe']:.2f}") for r in partial]
if dd_only:
    print("  DD_ONLY:"); [print(f"    {r['label']}: DD={r['dd']:.1f}% (delta {r['dd']-base['dd']:+.1f}%)") for r in dd_only]
if not strict and not partial:
    best = sorted(results[1:], key=lambda r: r['dd'], reverse=True)[0]
    print(f"\n  CONCLUSION: No approach beats v4.0 locked on all 3 metrics.")
    print(f"  Best DD: {best['label']} -> DD={best['dd']:.1f}% (delta {best['dd']-base['dd']:+.1f}%), Ex={best['excess']:+.1f}%")
    print(f"  DD is structural cost of momentum strategy with high-beta stocks.")
    print(f"  Next: consider bond/gold overlay on top of momentum core (uncorrelated).")
