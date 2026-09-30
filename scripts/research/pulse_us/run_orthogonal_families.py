"""
run_orthogonal_families.py
==========================
Adds 3 new orthogonal signal families to q_library_1000.json.

Problem with existing 258 quality signals:
  ALL use rs_pct >= 85/90/95 as PRIMARY gate → all fire on same RS-top tickers
  → high within-family AND cross-family correlation
  → breadth metric measures nothing independent

Solution: change PRIMARY gate to a different axis per family:
  F_VOL  — Volume accumulation (up_vol_ratio) as primary
  F_REC  — Recovery strength (dd_recovery) as primary
  F_BASE — Base quality / coiling (base_tight + base_position) as primary

These fire on stocks that institutions are accumulating (F_VOL),
stocks bouncing hard from pullbacks (F_REC), and stocks coiling
tightly near highs regardless of RS rank (F_BASE).
"""
import pandas as pd
import numpy as np
import json
import time
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

OUT_PATH = Path('data/research/pulse_us/library/q_library_1000.json')
t0 = time.time()

# ── Load data ─────────────────────────────────────────────────────
sig = pd.read_parquet('data/backtest/signals.parquet')
px  = pd.read_parquet('data/backtest/prices.parquet')
px['date'] = pd.to_datetime(px['date'])
sig['date'] = pd.to_datetime(sig['date'])
px = px.sort_values(['ticker', 'date'])
px['fwd3'] = px.groupby('ticker')['close'].transform(lambda x: x.shift(-3) / x - 1)
df = sig.merge(px[['ticker', 'date', 'fwd3']], on=['ticker', 'date'], how='left')
df = df[(df['spy_above_ma200'] == 1) & (df['rs_pct'] >= 70)].copy()
df = df.sort_values(['ticker', 'date']).reset_index(drop=True)
print(f'Loaded {len(df):,} rows ({time.time()-t0:.1f}s)')
print(f'Columns: {sorted(df.columns.tolist())}')

with open(OUT_PATH) as f:
    lib = json.load(f)
existing       = lib.get('results', [])
existing_labels = {r['label'] for r in existing}
print(f'Existing: {len(existing)} total | quality={sum(1 for r in existing if r["grade"] in ("S_BOTH","S","A"))}')

# ── Scoring helpers ───────────────────────────────────────────────
def dedup_vec(sub, cooldown=10):
    if len(sub) == 0:
        return sub
    s = sub.sort_values(['ticker', 'date']).copy()
    s['_di']   = (s['date'] - pd.Timestamp('2000-01-01')).dt.days
    s['_prev'] = s.groupby('ticker')['_di'].shift(1, fill_value=-9999)
    s['_grp']  = ((s['_di'] - s['_prev']) >= cooldown).astype(int)
    s['_grp']  = s.groupby('ticker')['_grp'].cumsum()
    return s.groupby(['ticker', '_grp']).first().reset_index() \
             .drop(columns=['_di', '_prev', '_grp'], errors='ignore')

def score(mask, label, family):
    if label in existing_labels:
        return None
    sub = df[mask]
    if len(sub) < 5:
        return None
    d = dedup_vec(sub)
    if len(d) < 5:
        return None
    h3  = (d['fwd3'] > 0).mean() * 100
    avg = d['fwd3'].mean() * 100
    d25 = d[d['date'].dt.year == 2025]
    d20 = d[d['date'].dt.year == 2020]
    h25 = (d25['fwd3'] > 0).mean() * 100 if len(d25) >= 5 else float('nan')
    h20 = (d20['fwd3'] > 0).mean() * 100 if len(d20) >= 5 else float('nan')
    grade = ('S_BOTH' if h25 >= 65 and not np.isnan(h20) and h20 >= 65 else
             'S'      if h25 >= 65 else
             'A'      if h3  >= 65 else '')
    return {
        'label':  label,
        'family': family,
        'h3':     round(h3, 1),
        'h25':    round(h25, 1) if not np.isnan(h25) else None,
        'h20':    round(h20, 1) if not np.isnan(h20) else None,
        'avg':    round(avg, 3),
        'N':      len(d),
        'N25':    len(d25),
        'N20':    len(d20),
        'grade':  grade,
    }

new_results = []
tested = 0

def add(mask, label, family):
    global tested
    tested += 1
    r = score(mask, label, family)
    if r:
        new_results.append(r)

# ── Boolean atoms ─────────────────────────────────────────────────
rs95 = df['rs_pct'] >= 95; rs90 = df['rs_pct'] >= 90
rs85 = df['rs_pct'] >= 85; rs80 = df['rs_pct'] >= 80; rs70 = df['rs_pct'] >= 70
bt70 = df['base_tight'] <= 0.70; bt60 = df['base_tight'] <= 0.60
bt50 = df['base_tight'] <= 0.50; bt40 = df['base_tight'] <= 0.40
ATH3 = df['pct_from_52w_high'] >= -3; ATH5 = df['pct_from_52w_high'] >= -5
ATH8 = df['pct_from_52w_high'] >= -8; ATH10 = df['pct_from_52w_high'] >= -10
vc75 = df['vol_contraction'] <= 0.75; vc70 = df['vol_contraction'] <= 0.70
vc60 = df['vol_contraction'] <= 0.60
pp   = df['pocket_pivot'] > 0.5
rsmom = df['rs_pct_3m'] > df['rs_pct_6m']
vt75 = df['vol_trend'] > 0.75; vt80 = df['vol_trend'] > 0.80
vt50 = df['vol_trend'] > 0.50; vt60 = df['vol_trend'] > 0.60
ddr10 = df['dd_recovery'] >= 1.0; ddr12 = df['dd_recovery'] >= 1.2
ddr15 = df['dd_recovery'] >= 1.5; ddr20 = df['dd_recovery'] >= 2.0
ddr25 = df['dd_recovery'] >= 2.5; ddr30 = df['dd_recovery'] >= 3.0
ma200_10 = df['price_vs_ma200_pct'] >= 10; ma200_20 = df['price_vs_ma200_pct'] >= 20
ma200_30 = df['price_vs_ma200_pct'] >= 30
ma50_20 = df['price_vs_ma50_pct'] >= 20; ma50_25 = df['price_vs_ma50_pct'] >= 25
tc50 = df['trend_consistency'] >= 50; tc60 = df['trend_consistency'] >= 60
tc70 = df['trend_consistency'] >= 70; tc75 = df['trend_consistency'] >= 75
tc80 = df['trend_consistency'] >= 80
sh05 = df['sharpe_60d'] >= 0.5; sh10 = df['sharpe_60d'] >= 1.0
sh15 = df['sharpe_60d'] >= 1.5; sh20 = df['sharpe_60d'] >= 2.0
uvr55 = df['up_vol_ratio'] >= 0.55; uvr60 = df['up_vol_ratio'] >= 0.60
uvr65 = df['up_vol_ratio'] >= 0.65; uvr70 = df['up_vol_ratio'] >= 0.70
sr10 = df['sector_resilience_6m'] >= 1.0; sr15 = df['sector_resilience_6m'] >= 1.5
sr20 = df['sector_resilience_6m'] >= 2.0; sr25 = df['sector_resilience_6m'] >= 2.5
res10 = df['resilience_3m'] >= 1.0; res15 = df['resilience_3m'] >= 1.5
res20 = df['resilience_3m'] >= 2.0
res6m15 = df['resilience_6m'] >= 1.5; res6m20 = df['resilience_6m'] >= 2.0
lo30 = df['pct_from_52w_low'] >= 30; lo50 = df['pct_from_52w_low'] >= 50
lo70 = df['pct_from_52w_low'] >= 70
ads  = df['ad_slope'] > 0
bp70 = df['base_position'] >= 0.70; bp80 = df['base_position'] >= 0.80
bp85 = df['base_position'] >= 0.85; bp90 = df['base_position'] >= 0.90
rs_accel5 = df['rs_accel'] >= 5; rs_accel10 = df['rs_accel'] >= 10

if 'spring_score' in df.columns:
    spring_any = df['spring_score'] > 0
    spring_med = df['spring_score'] >= 0.5
    spring_hi  = df['spring_score'] >= 1.0
else:
    spring_any = pp & ddr10
    spring_med = pp & ddr15
    spring_hi  = pp & ddr20

# =====================================================================
# F_VOL — Volume Accumulation as PRIMARY gate
# Intent: institutions buying quietly before price explodes
# Primary axis: up_vol_ratio (Lowry Buying Power proxy)
# Does NOT require RS >= 90/95 first — RS is a secondary confirmer
# Orthogonal to existing families because low-RS stocks can qualify
# =====================================================================
print("\n" + "="*60)
print("F_VOL: Volume Accumulation (up_vol_ratio primary)")
print("="*60)

for uvr_g, uvr_n in [(uvr70, '70'), (uvr65, '65'), (uvr60, '60')]:
    BASE_VOL = uvr_g & rsmom  # primary gate: strong buying volume + RS trending up

    # Pure volume + structure (no RS floor requirement)
    add(BASE_VOL & ATH3,              f'F_VOL_uvr{uvr_n}_ATH3',              'F_VOL')
    add(BASE_VOL & ATH5,              f'F_VOL_uvr{uvr_n}_ATH5',              'F_VOL')
    add(BASE_VOL & ATH3 & bt70,       f'F_VOL_uvr{uvr_n}_ATH3_bt70',        'F_VOL')
    add(BASE_VOL & ATH3 & bt60,       f'F_VOL_uvr{uvr_n}_ATH3_bt60',        'F_VOL')
    add(BASE_VOL & ATH3 & vc75,       f'F_VOL_uvr{uvr_n}_ATH3_vc75',        'F_VOL')
    add(BASE_VOL & ATH3 & vc70,       f'F_VOL_uvr{uvr_n}_ATH3_vc70',        'F_VOL')
    add(BASE_VOL & ATH3 & pp,         f'F_VOL_uvr{uvr_n}_ATH3_pp',          'F_VOL')
    add(BASE_VOL & ATH3 & bt70 & vc75, f'F_VOL_uvr{uvr_n}_ATH3_bt70_vc75', 'F_VOL')
    add(BASE_VOL & ATH3 & bt70 & pp,  f'F_VOL_uvr{uvr_n}_ATH3_bt70_pp',    'F_VOL')
    add(BASE_VOL & ATH3 & vt75,       f'F_VOL_uvr{uvr_n}_ATH3_vt75',        'F_VOL')
    add(BASE_VOL & ATH5 & vt75,       f'F_VOL_uvr{uvr_n}_ATH5_vt75',        'F_VOL')
    add(BASE_VOL & tc70,              f'F_VOL_uvr{uvr_n}_tc70',              'F_VOL')
    add(BASE_VOL & tc75,              f'F_VOL_uvr{uvr_n}_tc75',              'F_VOL')
    add(BASE_VOL & tc70 & ATH5,       f'F_VOL_uvr{uvr_n}_tc70_ATH5',        'F_VOL')
    add(BASE_VOL & tc70 & ATH3,       f'F_VOL_uvr{uvr_n}_tc70_ATH3',        'F_VOL')
    add(BASE_VOL & sh15,              f'F_VOL_uvr{uvr_n}_sh15',              'F_VOL')
    add(BASE_VOL & sh10 & ATH5,       f'F_VOL_uvr{uvr_n}_sh10_ATH5',        'F_VOL')
    add(BASE_VOL & ads & ATH5,        f'F_VOL_uvr{uvr_n}_ads_ATH5',         'F_VOL')
    add(BASE_VOL & ads & ATH3,        f'F_VOL_uvr{uvr_n}_ads_ATH3',         'F_VOL')
    add(BASE_VOL & lo50,              f'F_VOL_uvr{uvr_n}_lo50',              'F_VOL')
    add(BASE_VOL & lo70,              f'F_VOL_uvr{uvr_n}_lo70',              'F_VOL')
    add(BASE_VOL & bp80,              f'F_VOL_uvr{uvr_n}_bp80',              'F_VOL')
    add(BASE_VOL & bp85,              f'F_VOL_uvr{uvr_n}_bp85',              'F_VOL')
    add(BASE_VOL & bp90 & ATH5,       f'F_VOL_uvr{uvr_n}_bp90_ATH5',        'F_VOL')
    add(BASE_VOL & sr20,              f'F_VOL_uvr{uvr_n}_sr20',              'F_VOL')
    add(BASE_VOL & sr20 & ATH5,       f'F_VOL_uvr{uvr_n}_sr20_ATH5',        'F_VOL')
    add(BASE_VOL & ma200_20,          f'F_VOL_uvr{uvr_n}_ma200_20',          'F_VOL')
    add(BASE_VOL & ma200_20 & ATH5,   f'F_VOL_uvr{uvr_n}_ma200_20_ATH5',    'F_VOL')
    add(BASE_VOL & ddr15,             f'F_VOL_uvr{uvr_n}_ddr15',             'F_VOL')
    add(BASE_VOL & ddr15 & ATH5,      f'F_VOL_uvr{uvr_n}_ddr15_ATH5',       'F_VOL')
    add(BASE_VOL & ddr15 & ATH3,      f'F_VOL_uvr{uvr_n}_ddr15_ATH3',       'F_VOL')
    add(BASE_VOL & spring_med,        f'F_VOL_uvr{uvr_n}_spring_med',        'F_VOL')
    add(BASE_VOL & spring_hi,         f'F_VOL_uvr{uvr_n}_spring_hi',         'F_VOL')

    # Volume + RS acceleration (RS trending up AND volume flowing in)
    add(BASE_VOL & rs_accel5,         f'F_VOL_uvr{uvr_n}_rsaccel5',          'F_VOL')
    add(BASE_VOL & rs_accel10,        f'F_VOL_uvr{uvr_n}_rsaccel10',         'F_VOL')
    add(BASE_VOL & rs_accel5 & ATH5,  f'F_VOL_uvr{uvr_n}_rsaccel5_ATH5',    'F_VOL')
    add(BASE_VOL & rs_accel5 & ATH3,  f'F_VOL_uvr{uvr_n}_rsaccel5_ATH3',    'F_VOL')

    # Volume + RS confirming (moderate RS — orthogonal to high-RS families)
    for rs_g, rs_n in [(rs85, '85'), (rs80, '80'), (rs70, '70')]:
        add(BASE_VOL & rs_g & ATH5,         f'F_VOL_uvr{uvr_n}_rs{rs_n}_ATH5',     'F_VOL')
        add(BASE_VOL & rs_g & ATH3,         f'F_VOL_uvr{uvr_n}_rs{rs_n}_ATH3',     'F_VOL')
        add(BASE_VOL & rs_g & ATH3 & bt70,  f'F_VOL_uvr{uvr_n}_rs{rs_n}_ATH3_bt70','F_VOL')
        add(BASE_VOL & rs_g & ATH3 & vc75,  f'F_VOL_uvr{uvr_n}_rs{rs_n}_ATH3_vc75','F_VOL')
        add(BASE_VOL & rs_g & tc70,         f'F_VOL_uvr{uvr_n}_rs{rs_n}_tc70',      'F_VOL')
        add(BASE_VOL & rs_g & sh15,         f'F_VOL_uvr{uvr_n}_rs{rs_n}_sh15',      'F_VOL')
        add(BASE_VOL & rs_g & pp & ATH5,    f'F_VOL_uvr{uvr_n}_rs{rs_n}_pp_ATH5',  'F_VOL')
        add(BASE_VOL & rs_g & sr20,         f'F_VOL_uvr{uvr_n}_rs{rs_n}_sr20',      'F_VOL')

# =====================================================================
# F_REC — Recovery Strength as PRIMARY gate
# Intent: stocks that recovered hard from a drawdown = institutional demand
# Primary axis: dd_recovery (current price / 52W low, proxy for recovery momentum)
# Captures stocks in early re-discovery phase before RS catches up
# =====================================================================
print("\n" + "="*60)
print("F_REC: Recovery Strength (dd_recovery primary)")
print("="*60)

for ddr_g, ddr_n in [(ddr30, '30'), (ddr25, '25'), (ddr20, '20'), (ddr15, '15')]:
    BASE_REC = ddr_g & rsmom  # primary gate: strong recovery + RS trending up

    # Pure recovery + structure
    add(BASE_REC & ATH10,             f'F_REC_ddr{ddr_n}_ATH10',             'F_REC')
    add(BASE_REC & ATH8,              f'F_REC_ddr{ddr_n}_ATH8',              'F_REC')
    add(BASE_REC & ATH5,              f'F_REC_ddr{ddr_n}_ATH5',              'F_REC')
    add(BASE_REC & ATH3,              f'F_REC_ddr{ddr_n}_ATH3',              'F_REC')
    add(BASE_REC & ATH5 & bt70,       f'F_REC_ddr{ddr_n}_ATH5_bt70',        'F_REC')
    add(BASE_REC & ATH5 & vc75,       f'F_REC_ddr{ddr_n}_ATH5_vc75',        'F_REC')
    add(BASE_REC & ATH5 & pp,         f'F_REC_ddr{ddr_n}_ATH5_pp',          'F_REC')
    add(BASE_REC & ATH3 & pp,         f'F_REC_ddr{ddr_n}_ATH3_pp',          'F_REC')
    add(BASE_REC & ATH3 & bt70 & vc75, f'F_REC_ddr{ddr_n}_ATH3_bt70_vc75', 'F_REC')
    add(BASE_REC & ATH5 & vt75,       f'F_REC_ddr{ddr_n}_ATH5_vt75',        'F_REC')
    add(BASE_REC & ATH3 & vt75,       f'F_REC_ddr{ddr_n}_ATH3_vt75',        'F_REC')
    add(BASE_REC & tc70,              f'F_REC_ddr{ddr_n}_tc70',              'F_REC')
    add(BASE_REC & tc70 & ATH8,       f'F_REC_ddr{ddr_n}_tc70_ATH8',        'F_REC')
    add(BASE_REC & tc70 & ATH5,       f'F_REC_ddr{ddr_n}_tc70_ATH5',        'F_REC')
    add(BASE_REC & sh10,              f'F_REC_ddr{ddr_n}_sh10',              'F_REC')
    add(BASE_REC & sh15,              f'F_REC_ddr{ddr_n}_sh15',              'F_REC')
    add(BASE_REC & sh15 & ATH5,       f'F_REC_ddr{ddr_n}_sh15_ATH5',        'F_REC')
    add(BASE_REC & uvr60,             f'F_REC_ddr{ddr_n}_uvr60',             'F_REC')
    add(BASE_REC & uvr65,             f'F_REC_ddr{ddr_n}_uvr65',             'F_REC')
    add(BASE_REC & uvr65 & ATH5,      f'F_REC_ddr{ddr_n}_uvr65_ATH5',       'F_REC')
    add(BASE_REC & ads,               f'F_REC_ddr{ddr_n}_ads',               'F_REC')
    add(BASE_REC & ads & ATH5,        f'F_REC_ddr{ddr_n}_ads_ATH5',         'F_REC')
    add(BASE_REC & sr15,              f'F_REC_ddr{ddr_n}_sr15',              'F_REC')
    add(BASE_REC & sr20,              f'F_REC_ddr{ddr_n}_sr20',              'F_REC')
    add(BASE_REC & sr20 & ATH5,       f'F_REC_ddr{ddr_n}_sr20_ATH5',        'F_REC')
    add(BASE_REC & ma200_20,          f'F_REC_ddr{ddr_n}_ma200_20',          'F_REC')
    add(BASE_REC & ma200_20 & ATH5,   f'F_REC_ddr{ddr_n}_ma200_20_ATH5',    'F_REC')
    add(BASE_REC & bp80,              f'F_REC_ddr{ddr_n}_bp80',              'F_REC')
    add(BASE_REC & bp85 & ATH5,       f'F_REC_ddr{ddr_n}_bp85_ATH5',        'F_REC')
    add(BASE_REC & spring_med,        f'F_REC_ddr{ddr_n}_spring_med',        'F_REC')
    add(BASE_REC & spring_hi,         f'F_REC_ddr{ddr_n}_spring_hi',         'F_REC')

    # Recovery + RS confirming (any RS level — early recovery stocks often RS 70-85)
    for rs_g, rs_n in [(rs90, '90'), (rs85, '85'), (rs80, '80'), (rs70, '70')]:
        add(BASE_REC & rs_g & ATH5,         f'F_REC_ddr{ddr_n}_rs{rs_n}_ATH5',      'F_REC')
        add(BASE_REC & rs_g & ATH3,         f'F_REC_ddr{ddr_n}_rs{rs_n}_ATH3',      'F_REC')
        add(BASE_REC & rs_g & ATH5 & vc75,  f'F_REC_ddr{ddr_n}_rs{rs_n}_ATH5_vc75','F_REC')
        add(BASE_REC & rs_g & tc70,         f'F_REC_ddr{ddr_n}_rs{rs_n}_tc70',       'F_REC')
        add(BASE_REC & rs_g & uvr60 & ATH5, f'F_REC_ddr{ddr_n}_rs{rs_n}_uvr60_ATH5','F_REC')
        add(BASE_REC & rs_g & sh10 & ATH5,  f'F_REC_ddr{ddr_n}_rs{rs_n}_sh10_ATH5', 'F_REC')

# =====================================================================
# F_BASE — Base Quality / Coiling as PRIMARY gate
# Intent: tight consolidation near highs = institutional holding, not selling
# Primary axis: base_tight (low) + base_position (high) = coiling near 52W high
# Minervini VCP principle — tightness + position = institutional conviction
# =====================================================================
print("\n" + "="*60)
print("F_BASE: Base Quality / Coiling (base_tight primary)")
print("="*60)

for bt_g, bt_n in [(bt50, '50'), (bt60, '60'), (bt40, '40')]:
    for bp_g, bp_n in [(bp85, '85'), (bp80, '80'), (bp90, '90')]:
        BASE_COIL = bt_g & bp_g & rsmom  # primary: tight base at high position

        add(BASE_COIL,                     f'F_BASE_bt{bt_n}_bp{bp_n}',              'F_BASE')
        add(BASE_COIL & ATH5,              f'F_BASE_bt{bt_n}_bp{bp_n}_ATH5',         'F_BASE')
        add(BASE_COIL & ATH3,              f'F_BASE_bt{bt_n}_bp{bp_n}_ATH3',         'F_BASE')
        add(BASE_COIL & vc75,              f'F_BASE_bt{bt_n}_bp{bp_n}_vc75',         'F_BASE')
        add(BASE_COIL & vc70,              f'F_BASE_bt{bt_n}_bp{bp_n}_vc70',         'F_BASE')
        add(BASE_COIL & vc60,              f'F_BASE_bt{bt_n}_bp{bp_n}_vc60',         'F_BASE')
        add(BASE_COIL & ATH5 & vc75,       f'F_BASE_bt{bt_n}_bp{bp_n}_ATH5_vc75',   'F_BASE')
        add(BASE_COIL & ATH3 & vc75,       f'F_BASE_bt{bt_n}_bp{bp_n}_ATH3_vc75',   'F_BASE')
        add(BASE_COIL & ATH3 & vc70,       f'F_BASE_bt{bt_n}_bp{bp_n}_ATH3_vc70',   'F_BASE')
        add(BASE_COIL & pp,                f'F_BASE_bt{bt_n}_bp{bp_n}_pp',           'F_BASE')
        add(BASE_COIL & pp & ATH5,         f'F_BASE_bt{bt_n}_bp{bp_n}_pp_ATH5',     'F_BASE')
        add(BASE_COIL & pp & ATH3,         f'F_BASE_bt{bt_n}_bp{bp_n}_pp_ATH3',     'F_BASE')
        add(BASE_COIL & vt75,              f'F_BASE_bt{bt_n}_bp{bp_n}_vt75',         'F_BASE')
        add(BASE_COIL & ATH5 & vt75,       f'F_BASE_bt{bt_n}_bp{bp_n}_ATH5_vt75',   'F_BASE')
        add(BASE_COIL & tc70,              f'F_BASE_bt{bt_n}_bp{bp_n}_tc70',         'F_BASE')
        add(BASE_COIL & tc75,              f'F_BASE_bt{bt_n}_bp{bp_n}_tc75',         'F_BASE')
        add(BASE_COIL & tc70 & ATH5,       f'F_BASE_bt{bt_n}_bp{bp_n}_tc70_ATH5',   'F_BASE')
        add(BASE_COIL & sh10,              f'F_BASE_bt{bt_n}_bp{bp_n}_sh10',         'F_BASE')
        add(BASE_COIL & sh15,              f'F_BASE_bt{bt_n}_bp{bp_n}_sh15',         'F_BASE')
        add(BASE_COIL & sh15 & ATH5,       f'F_BASE_bt{bt_n}_bp{bp_n}_sh15_ATH5',   'F_BASE')
        add(BASE_COIL & uvr60,             f'F_BASE_bt{bt_n}_bp{bp_n}_uvr60',        'F_BASE')
        add(BASE_COIL & uvr65 & ATH5,      f'F_BASE_bt{bt_n}_bp{bp_n}_uvr65_ATH5',  'F_BASE')
        add(BASE_COIL & ads,               f'F_BASE_bt{bt_n}_bp{bp_n}_ads',          'F_BASE')
        add(BASE_COIL & ads & ATH5,        f'F_BASE_bt{bt_n}_bp{bp_n}_ads_ATH5',    'F_BASE')
        add(BASE_COIL & ddr15,             f'F_BASE_bt{bt_n}_bp{bp_n}_ddr15',        'F_BASE')
        add(BASE_COIL & ddr20 & ATH5,      f'F_BASE_bt{bt_n}_bp{bp_n}_ddr20_ATH5', 'F_BASE')
        add(BASE_COIL & sr15,              f'F_BASE_bt{bt_n}_bp{bp_n}_sr15',         'F_BASE')
        add(BASE_COIL & sr20 & ATH5,       f'F_BASE_bt{bt_n}_bp{bp_n}_sr20_ATH5',  'F_BASE')
        add(BASE_COIL & ma200_20,          f'F_BASE_bt{bt_n}_bp{bp_n}_ma200_20',    'F_BASE')
        add(BASE_COIL & lo70,              f'F_BASE_bt{bt_n}_bp{bp_n}_lo70',         'F_BASE')
        add(BASE_COIL & lo70 & ATH5,       f'F_BASE_bt{bt_n}_bp{bp_n}_lo70_ATH5',  'F_BASE')
        add(BASE_COIL & spring_med,        f'F_BASE_bt{bt_n}_bp{bp_n}_spring_med',  'F_BASE')

        # Base quality + RS confirming (at any RS level)
        for rs_g, rs_n in [(rs90, '90'), (rs85, '85'), (rs80, '80')]:
            add(BASE_COIL & rs_g,                f'F_BASE_bt{bt_n}_bp{bp_n}_rs{rs_n}',          'F_BASE')
            add(BASE_COIL & rs_g & ATH5,         f'F_BASE_bt{bt_n}_bp{bp_n}_rs{rs_n}_ATH5',     'F_BASE')
            add(BASE_COIL & rs_g & ATH3,         f'F_BASE_bt{bt_n}_bp{bp_n}_rs{rs_n}_ATH3',     'F_BASE')
            add(BASE_COIL & rs_g & vc70 & ATH5,  f'F_BASE_bt{bt_n}_bp{bp_n}_rs{rs_n}_vc70_ATH5','F_BASE')
            add(BASE_COIL & rs_g & tc70,         f'F_BASE_bt{bt_n}_bp{bp_n}_rs{rs_n}_tc70',      'F_BASE')
            add(BASE_COIL & rs_g & uvr60 & ATH5, f'F_BASE_bt{bt_n}_bp{bp_n}_rs{rs_n}_uvr60_ATH5','F_BASE')
            add(BASE_COIL & rs_g & sh15,         f'F_BASE_bt{bt_n}_bp{bp_n}_rs{rs_n}_sh15',      'F_BASE')

# ── Report ────────────────────────────────────────────────────────
print(f'\nTotal tested: {tested} | New results: {len(new_results)}')

if new_results:
    df_new = pd.DataFrame(new_results)
    q_new  = df_new[df_new['grade'].isin(['S_BOTH', 'S', 'A'])]
    print(f'\nNew quality signals: {len(q_new)}'
          f' (S_BOTH={len(df_new[df_new["grade"]=="S_BOTH"])}'
          f', S={len(df_new[df_new["grade"]=="S"])}'
          f', A={len(df_new[df_new["grade"]=="A"])})')

    print('\nTop 20 new signals by h3:')
    for _, r in df_new.nlargest(20, 'h3').iterrows():
        h20 = f"{r['h20']:.0f}" if r['h20'] else 'nan'
        h25 = f"{r['h25']:.0f}" if r['h25'] else 'nan'
        print(f"  {r['label']:<55} h3={r['h3']:>5.1f}% h25={h25:>3} h20={h20:>3} N={r['N']:>4} [{r['grade']}]")

    print('\nQuality signals by family:')
    for fam in ['F_VOL', 'F_REC', 'F_BASE']:
        sub = q_new[q_new['family'] == fam]
        if len(sub) > 0:
            best = sub.nlargest(1, 'h3').iloc[0]
            print(f"  {fam}: {len(sub)} quality signals | best h3={best['h3']}% ({best['label']})")
        else:
            print(f"  {fam}: 0 quality signals")

# ── Merge and save ────────────────────────────────────────────────
all_results = existing + new_results
df_res  = pd.DataFrame(all_results)
quality = len(df_res[df_res['grade'].isin(['S_BOTH', 'S', 'A'])])
sb = len(df_res[df_res['grade'] == 'S_BOTH'])
s  = len(df_res[df_res['grade'] == 'S'])
a  = len(df_res[df_res['grade'] == 'A'])

print(f'\n=== FINAL: {len(all_results)} total | Quality: {quality} (S_BOTH={sb}, S={s}, A={a}) ===')
print(f'Elapsed: {time.time()-t0:.1f}s')

out = {
    'total_scored':   len(all_results),
    'grade_s_both':   sb,
    'grade_s':        s,
    'grade_a':        a,
    'quality_total':  quality,
    'results':        df_res.to_dict('records'),
}
with open(OUT_PATH, 'w') as f:
    json.dump(out, f, indent=2, default=str)
print(f'Saved -> {OUT_PATH}')
