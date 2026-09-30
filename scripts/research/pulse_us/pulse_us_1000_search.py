"""
pulse_us_1000_search.py
=======================
Systematic search across 8 signal families → 1000+ tested combinations.
Goal: build PULSE-US Q-signal library comparable to PULSE-TH (825 Q-signals).

8 Families:
  F1: VCP/ATH Family      — tight base near ATH (what we know works)
  F2: Breakout Family     — price clearing N-day highs on volume
  F3: RS Inflection       — RS acceleration signals
  F4: Volume Surge        — accumulation day patterns
  F5: Trend Strength      — MA alignment + trend consistency
  F6: Recovery/Resilience — drawdown recovery + resilience combo
  F7: Sector Rotation     — sector momentum × stock RS
  F8: Multi-Factor Blend  — orthogonal combinations across families

Grade S BOTH: dedup h3 ≥65% in BOTH 2025 AND 2020
Grade S:      dedup h3 ≥65% in 2025
"""

import pandas as pd
import numpy as np
import warnings
import json
from pathlib import Path
from itertools import product

warnings.filterwarnings('ignore')
OUT_DIR = Path('data/research/pulse_us/library')
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Load data ──────────────────────────────────────────────────────────────────
print("Loading data...")
sig = pd.read_parquet('data/backtest/signals.parquet')
px  = pd.read_parquet('data/backtest/prices.parquet')
px['date']  = pd.to_datetime(px['date'])
sig['date'] = pd.to_datetime(sig['date'])
px  = px.sort_values(['ticker', 'date'])
px['fwd3'] = px.groupby('ticker')['close'].transform(lambda x: x.shift(-3) / x - 1)
df = sig.merge(px[['ticker', 'date', 'fwd3']], on=['ticker', 'date'], how='left')
df = df[(df['spy_above_ma200'] == 1) & (df['rs_pct'] >= 70)].copy()
print(f"Universe: {len(df):,} rows")

# ── Dedup ──────────────────────────────────────────────────────────────────────
def dedup(sub, cooldown=10):
    sub = sub.sort_values(['ticker', 'date']).copy()
    keep, last = [], {}
    for _, r in sub.iterrows():
        t, d = r['ticker'], r['date']
        if t not in last or (d - last[t]).days >= cooldown:
            keep.append(True); last[t] = d
        else:
            keep.append(False)
    return sub[keep]

# ── Score function ─────────────────────────────────────────────────────────────
def score(mask, label):
    sub = df[mask]
    if len(sub) < 5:
        return None
    d = dedup(sub)
    if len(d) < 5:
        return None
    h3  = (d['fwd3'] > 0).mean() * 100
    avg = d['fwd3'].mean() * 100
    d25 = d[d['date'].dt.year == 2025]
    d20 = d[d['date'].dt.year == 2020]
    h25 = (d25['fwd3'] > 0).mean() * 100 if len(d25) >= 5 else float('nan')
    h20 = (d20['fwd3'] > 0).mean() * 100 if len(d20) >= 5 else float('nan')
    grade = ''
    if h25 >= 65 and not np.isnan(h20) and h20 >= 65:
        grade = 'S_BOTH'
    elif h25 >= 65:
        grade = 'S'
    elif h3 >= 65:
        grade = 'A'
    return {
        'label': label, 'h3': round(h3, 1), 'h25': round(h25, 1),
        'h20': round(h20, 1) if not np.isnan(h20) else None,
        'avg': round(avg, 3), 'N': len(d), 'N25': len(d25), 'N20': len(d20),
        'grade': grade
    }

# ── Pre-compute masks ──────────────────────────────────────────────────────────
# Core factors
rs95 = df['rs_pct'] >= 95;  rs90 = df['rs_pct'] >= 90;  rs85 = df['rs_pct'] >= 85
bt70 = df['base_tight'] <= 0.70;  bt80 = df['base_tight'] <= 0.80
ATH3 = df['pct_from_52w_high'] >= -3
ATH5 = df['pct_from_52w_high'] >= -5
ATH10= df['pct_from_52w_high'] >= -10
vc75 = df['vol_contraction'] <= 0.75;  vc60 = df['vol_contraction'] <= 0.60;  vc90 = df['vol_contraction'] <= 0.90
pp   = df['pocket_pivot'] > 0.5
rsmom= df['rs_pct_3m'] > df['rs_pct_6m']
vt05 = df['vol_trend'] > 0.5;  vt75 = df['vol_trend'] > 0.75;  vt10 = df['vol_trend'] > 1.0
ddr10= df['dd_recovery'] >= 1.0;  ddr15= df['dd_recovery'] >= 1.5;  ddr12= df['dd_recovery'] >= 1.2
ma20 = df['price_vs_ma50_pct'] >= 20;  ma15= df['price_vs_ma50_pct'] >= 15;  ma10= df['price_vs_ma50_pct'] >= 10
res15= df['resilience_3m'] >= 1.5;  res12= df['resilience_3m'] >= 1.2;  res20= df['resilience_3m'] >= 2.0
sr15 = df['sector_resilience_6m'] >= 1.5;  sr20= df['sector_resilience_6m'] >= 2.0
tc60 = df['trend_consistency'] >= 60;  tc50= df['trend_consistency'] >= 50;  tc40= df['trend_consistency'] >= 40
ads  = df['ad_slope'] > 0
sh05 = df['sharpe_60d'] >= 0.5;  sh10= df['sharpe_60d'] >= 1.0
uvr55= df['up_vol_ratio'] >= 0.55;  uvr60= df['up_vol_ratio'] >= 0.60
bp70 = df['base_position'] >= 0.70;  bp60= df['base_position'] >= 0.60
pct30= df['pct_from_52w_low'] >= 30;  pct50= df['pct_from_52w_low'] >= 50
ma200= df['price_vs_ma200_pct'] >= 10;  ma200_20= df['price_vs_ma200_pct'] >= 20
beta_lo= df['beta_252'] <= 2.0;  beta_mid= df['beta_252'] <= 1.5
sp6m = df['sector_resilience_3m'] >= 1.5

all_results = []
tested = 0

def add(mask, label, family):
    global tested
    tested += 1
    r = score(mask, label)
    if r:
        r['family'] = family
        all_results.append(r)

print("\n── F1: VCP/ATH Family ─────────────────────────────────────────")
CORE = rs95 & bt70 & ATH3 & rsmom & vc75 & pp
for vt_gate, vt_name in [(vt05, 'vt05'), (vt75, 'vt75'), (vt10, 'vt10')]:
    CORE_VT = CORE & vt_gate
    for ddr_gate, ddr_name in [(ddr10, 'ddr10'), (ddr12, 'ddr12'), (ddr15, 'ddr15'), (None, '')]:
        d = CORE_VT & ddr_gate if ddr_gate is not None else CORE_VT
        add(d, f'F1_rs95_bt70_ATH3_rsmom_{vt_name}{"_"+ddr_name if ddr_name else ""}', 'F1')
        add(d & ma20,  f'F1_rs95_bt70_ATH3_rsmom_{vt_name}{"_"+ddr_name if ddr_name else ""}_ma20', 'F1')
        add(d & res15, f'F1_rs95_bt70_ATH3_rsmom_{vt_name}{"_"+ddr_name if ddr_name else ""}_res15', 'F1')
        add(d & ads,   f'F1_rs95_bt70_ATH3_rsmom_{vt_name}{"_"+ddr_name if ddr_name else ""}_ads', 'F1')
        add(d & sr15,  f'F1_rs95_bt70_ATH3_rsmom_{vt_name}{"_"+ddr_name if ddr_name else ""}_sr15', 'F1')
        add(d & tc60,  f'F1_rs95_bt70_ATH3_rsmom_{vt_name}{"_"+ddr_name if ddr_name else ""}_tc60', 'F1')
        add(d & bp70,  f'F1_rs95_bt70_ATH3_rsmom_{vt_name}{"_"+ddr_name if ddr_name else ""}_bp70', 'F1')
        add(d & uvr55, f'F1_rs95_bt70_ATH3_rsmom_{vt_name}{"_"+ddr_name if ddr_name else ""}_uvr55', 'F1')
        # OR gate variants
        add(d & (ddr15 | ma20), f'F1_rs95_bt70_ATH3_rsmom_{vt_name}{"_"+ddr_name if ddr_name else ""}_OR_ddr15_ma20', 'F1')
# RS threshold variants
for rs_gate, rs_name in [(rs90, 'rs90'), (rs85, 'rs85')]:
    C = rs_gate & bt70 & ATH3 & rsmom & vt05 & vc75 & pp
    add(C & ddr10, f'F1_{rs_name}_bt70_ATH3_rsmom_vt05_ddr10', 'F1')
    add(C & vt75 & (ddr15 | ma20), f'F1_{rs_name}_bt70_ATH3_rsmom_vt75_OR_ddr15_ma20', 'F1')
# base_tight variants
for bt_gate, bt_name in [(bt80, 'bt80')]:
    C = rs95 & bt_gate & ATH3 & rsmom & vt05 & vc75 & pp
    add(C & ddr10, f'F1_rs95_{bt_name}_ATH3_rsmom_vt05_ddr10', 'F1')
    add(C & vt75 & ddr15, f'F1_rs95_{bt_name}_ATH3_rsmom_vt75_ddr15', 'F1')
# ATH variants
for ath_gate, ath_name in [(ATH5, 'ATH5'), (ATH10, 'ATH10')]:
    C = rs95 & bt70 & ath_gate & rsmom & vt05 & vc75 & pp
    add(C & ddr10, f'F1_rs95_bt70_{ath_name}_rsmom_vt05_ddr10', 'F1')
    add(C & ddr10 & ma20, f'F1_rs95_bt70_{ath_name}_rsmom_vt05_ddr10_ma20', 'F1')
print(f"  F1: {tested} tested, {len(all_results)} scored")

# ─── F2: Breakout Family ──────────────────────────────────────────────────────
print("── F2: Breakout Family ────────────────────────────────────────")
t2 = tested
# Simulate "N-day high" breakout using pct_from_52w_high and vol surge
# pct_from_52w_high >= -1% = just broke to near ATH
# vol_trend rising = volume confirming breakout
BKT_CORE = rs90 & (df['pct_from_52w_high'] >= -1) & vt75
for rs_g, rs_n in [(rs95, '95'), (rs90, '90')]:
    for vol_g, vol_n in [(vt75, 'vt75'), (vt10, 'vt10')]:
        B = rs_g & (df['pct_from_52w_high'] >= -1) & vol_g
        add(B, f'F2_rs{rs_n}_ATH1_{vol_n}', 'F2')
        add(B & rsmom, f'F2_rs{rs_n}_ATH1_{vol_n}_rsmom', 'F2')
        add(B & rsmom & pp, f'F2_rs{rs_n}_ATH1_{vol_n}_rsmom_pp', 'F2')
        add(B & rsmom & vc75, f'F2_rs{rs_n}_ATH1_{vol_n}_rsmom_vc75', 'F2')
        add(B & rsmom & vc75 & pp, f'F2_rs{rs_n}_ATH1_{vol_n}_rsmom_vc75_pp', 'F2')
        add(B & rsmom & vc75 & pp & ddr10, f'F2_rs{rs_n}_ATH1_{vol_n}_rsmom_vc75_pp_ddr10', 'F2')
        add(B & rsmom & vc75 & pp & ma20, f'F2_rs{rs_n}_ATH1_{vol_n}_rsmom_vc75_pp_ma20', 'F2')
        add(B & rsmom & bt70, f'F2_rs{rs_n}_ATH1_{vol_n}_rsmom_bt70', 'F2')
        add(B & rsmom & bt70 & vc75 & pp, f'F2_rs{rs_n}_ATH1_{vol_n}_rsmom_bt70_vc75_pp', 'F2')
        add(B & tc60, f'F2_rs{rs_n}_ATH1_{vol_n}_tc60', 'F2')
        add(B & rsmom & uvr55, f'F2_rs{rs_n}_ATH1_{vol_n}_rsmom_uvr55', 'F2')
        add(B & sr15, f'F2_rs{rs_n}_ATH1_{vol_n}_sr15', 'F2')
        add(B & rsmom & sr15, f'F2_rs{rs_n}_ATH1_{vol_n}_rsmom_sr15', 'F2')
print(f"  F2: {tested-t2} tested, {len([r for r in all_results if r['family']=='F2'])} scored")

# ─── F3: RS Inflection Family ─────────────────────────────────────────────────
print("── F3: RS Inflection Family ───────────────────────────────────")
t3 = tested
RS_MOM = df['rs_pct_3m'] > df['rs_pct_6m']
RS_ACCEL_POS = df['rs_accel'] > 0
RS_ACCEL2 = df['rs_accel'] > 2
RS_ACCEL5 = df['rs_accel'] > 5
for rs_g, rs_n in [(rs95, '95'), (rs90, '90'), (rs85, '85')]:
    for mom_g, mom_n in [(RS_MOM, 'rsmom'), (RS_ACCEL_POS, 'accel_pos'), (RS_ACCEL2, 'accel2')]:
        R = rs_g & mom_g
        add(R, f'F3_rs{rs_n}_{mom_n}', 'F3')
        add(R & bt70, f'F3_rs{rs_n}_{mom_n}_bt70', 'F3')
        add(R & bt70 & ATH3, f'F3_rs{rs_n}_{mom_n}_bt70_ATH3', 'F3')
        add(R & bt70 & ATH3 & pp, f'F3_rs{rs_n}_{mom_n}_bt70_ATH3_pp', 'F3')
        add(R & bt70 & ATH3 & vc75, f'F3_rs{rs_n}_{mom_n}_bt70_ATH3_vc75', 'F3')
        add(R & bt70 & ATH3 & vc75 & pp, f'F3_rs{rs_n}_{mom_n}_bt70_ATH3_vc75_pp', 'F3')
        add(R & bt70 & ATH3 & vc75 & pp & vt75, f'F3_rs{rs_n}_{mom_n}_bt70_ATH3_vc75_pp_vt75', 'F3')
        add(R & res15, f'F3_rs{rs_n}_{mom_n}_res15', 'F3')
        add(R & sr15, f'F3_rs{rs_n}_{mom_n}_sr15', 'F3')
        add(R & ads, f'F3_rs{rs_n}_{mom_n}_ads', 'F3')
        add(R & bt70 & vt75 & vc75, f'F3_rs{rs_n}_{mom_n}_bt70_vt75_vc75', 'F3')
        add(R & bt70 & vt75 & vc75 & ddr10, f'F3_rs{rs_n}_{mom_n}_bt70_vt75_vc75_ddr10', 'F3')
        add(R & bt70 & vt75 & vc75 & (ddr15 | ma20), f'F3_rs{rs_n}_{mom_n}_bt70_vt75_vc75_OR', 'F3')
print(f"  F3: {tested-t3} tested, {len([r for r in all_results if r['family']=='F3'])} scored")

# ─── F4: Volume Surge / Accumulation Family ───────────────────────────────────
print("── F4: Volume Surge Family ────────────────────────────────────")
t4 = tested
# Up volume ratio = proportion of volume on up days
# uvr55 = good breadth of buying volume
for uvr_g, uvr_n in [(uvr55, 'uvr55'), (uvr60, 'uvr60')]:
    for rs_g, rs_n in [(rs95, '95'), (rs90, '90')]:
        V = rs_g & uvr_g
        add(V, f'F4_rs{rs_n}_{uvr_n}', 'F4')
        add(V & bt70, f'F4_rs{rs_n}_{uvr_n}_bt70', 'F4')
        add(V & bt70 & ATH3, f'F4_rs{rs_n}_{uvr_n}_bt70_ATH3', 'F4')
        add(V & bt70 & ATH3 & rsmom, f'F4_rs{rs_n}_{uvr_n}_bt70_ATH3_rsmom', 'F4')
        add(V & bt70 & ATH3 & rsmom & pp, f'F4_rs{rs_n}_{uvr_n}_bt70_ATH3_rsmom_pp', 'F4')
        add(V & bt70 & ATH3 & rsmom & vc75, f'F4_rs{rs_n}_{uvr_n}_bt70_ATH3_rsmom_vc75', 'F4')
        add(V & pp & rsmom, f'F4_rs{rs_n}_{uvr_n}_pp_rsmom', 'F4')
        add(V & pp & rsmom & bt70 & vc75, f'F4_rs{rs_n}_{uvr_n}_pp_rsmom_bt70_vc75', 'F4')
        add(V & pp & rsmom & bt70 & vc75 & ATH3, f'F4_rs{rs_n}_{uvr_n}_pp_rsmom_bt70_vc75_ATH3', 'F4')
        add(V & pp & rsmom & bt70 & vc75 & ATH3 & ddr10, f'F4_rs{rs_n}_{uvr_n}_pp_rsmom_bt70_vc75_ATH3_ddr10', 'F4')
        add(V & vt75, f'F4_rs{rs_n}_{uvr_n}_vt75', 'F4')
        add(V & vt75 & bt70 & ATH3 & rsmom, f'F4_rs{rs_n}_{uvr_n}_vt75_bt70_ATH3_rsmom', 'F4')
        add(V & sh05, f'F4_rs{rs_n}_{uvr_n}_sh05', 'F4')
        add(V & sh05 & bt70 & ATH3, f'F4_rs{rs_n}_{uvr_n}_sh05_bt70_ATH3', 'F4')
print(f"  F4: {tested-t4} tested, {len([r for r in all_results if r['family']=='F4'])} scored")

# ─── F5: Trend Strength Family ───────────────────────────────────────────────
print("── F5: Trend Strength Family ──────────────────────────────────")
t5 = tested
# trend_consistency = % of last 63d where close > MA50
# sharpe_60d = risk-adjusted trend strength
for tc_g, tc_n in [(tc60, 'tc60'), (tc50, 'tc50'), (tc40, 'tc40')]:
    for rs_g, rs_n in [(rs95, '95'), (rs90, '90')]:
        T = rs_g & tc_g
        add(T, f'F5_rs{rs_n}_{tc_n}', 'F5')
        add(T & bt70, f'F5_rs{rs_n}_{tc_n}_bt70', 'F5')
        add(T & bt70 & ATH3, f'F5_rs{rs_n}_{tc_n}_bt70_ATH3', 'F5')
        add(T & bt70 & ATH3 & rsmom, f'F5_rs{rs_n}_{tc_n}_bt70_ATH3_rsmom', 'F5')
        add(T & bt70 & ATH3 & rsmom & pp, f'F5_rs{rs_n}_{tc_n}_bt70_ATH3_rsmom_pp', 'F5')
        add(T & bt70 & ATH3 & rsmom & vc75 & pp, f'F5_rs{rs_n}_{tc_n}_bt70_ATH3_rsmom_vc75_pp', 'F5')
        add(T & bt70 & ATH3 & rsmom & vc75 & pp & vt75, f'F5_rs{rs_n}_{tc_n}_bt70_ATH3_rsmom_vc75_pp_vt75', 'F5')
        add(T & rsmom & ddr10, f'F5_rs{rs_n}_{tc_n}_rsmom_ddr10', 'F5')
        add(T & rsmom & ma20, f'F5_rs{rs_n}_{tc_n}_rsmom_ma20', 'F5')
        add(T & rsmom & ma200, f'F5_rs{rs_n}_{tc_n}_rsmom_ma200_10', 'F5')
        add(T & sh05, f'F5_rs{rs_n}_{tc_n}_sh05', 'F5')
        add(T & sh10, f'F5_rs{rs_n}_{tc_n}_sh10', 'F5')
        add(T & sh05 & bt70 & ATH3 & rsmom, f'F5_rs{rs_n}_{tc_n}_sh05_bt70_ATH3_rsmom', 'F5')
print(f"  F5: {tested-t5} tested, {len([r for r in all_results if r['family']=='F5'])} scored")

# ─── F6: Recovery/Resilience Family ─────────────────────────────────────────
print("── F6: Recovery/Resilience Family ────────────────────────────")
t6 = tested
for ddr_g, ddr_n in [(ddr10, 'ddr10'), (ddr12, 'ddr12'), (ddr15, 'ddr15')]:
    for res_g, res_n in [(res12, 'res12'), (res15, 'res15'), (res20, 'res20'), (None, '')]:
        R_base = ddr_g if res_g is None else ddr_g & res_g
        lbl = f'{ddr_n}{"_"+res_n if res_n else ""}'
        for rs_g, rs_n in [(rs95, '95'), (rs90, '90')]:
            R = rs_g & R_base
            add(R, f'F6_rs{rs_n}_{lbl}', 'F6')
            add(R & bt70, f'F6_rs{rs_n}_{lbl}_bt70', 'F6')
            add(R & bt70 & ATH3, f'F6_rs{rs_n}_{lbl}_bt70_ATH3', 'F6')
            add(R & bt70 & ATH3 & rsmom, f'F6_rs{rs_n}_{lbl}_bt70_ATH3_rsmom', 'F6')
            add(R & bt70 & ATH3 & rsmom & vc75, f'F6_rs{rs_n}_{lbl}_bt70_ATH3_rsmom_vc75', 'F6')
            add(R & bt70 & ATH3 & rsmom & vc75 & pp, f'F6_rs{rs_n}_{lbl}_bt70_ATH3_rsmom_vc75_pp', 'F6')
            add(R & bt70 & ATH3 & rsmom & vc75 & pp & vt75, f'F6_rs{rs_n}_{lbl}_bt70_ATH3_rsmom_vc75_pp_vt75', 'F6')
            add(R & bt70 & ATH3 & rsmom & vt75 & vc75 & pp & ma20, f'F6_rs{rs_n}_{lbl}_bt70_ATH3_rsmom_vt75_vc75_pp_ma20', 'F6')
            add(R & ads, f'F6_rs{rs_n}_{lbl}_ads', 'F6')
            add(R & bt70 & rsmom & ads, f'F6_rs{rs_n}_{lbl}_bt70_rsmom_ads', 'F6')
            add(R & ma20, f'F6_rs{rs_n}_{lbl}_ma20', 'F6')
print(f"  F6: {tested-t6} tested, {len([r for r in all_results if r['family']=='F6'])} scored")

# ─── F7: Sector Rotation Family ──────────────────────────────────────────────
print("── F7: Sector Rotation Family ─────────────────────────────────")
t7 = tested
for sr_g, sr_n in [(sr15, 'sr15'), (sr20, 'sr20')]:
    for rs_g, rs_n in [(rs95, '95'), (rs90, '90'), (rs85, '85')]:
        S = rs_g & sr_g
        add(S, f'F7_rs{rs_n}_{sr_n}', 'F7')
        add(S & rsmom, f'F7_rs{rs_n}_{sr_n}_rsmom', 'F7')
        add(S & bt70, f'F7_rs{rs_n}_{sr_n}_bt70', 'F7')
        add(S & bt70 & ATH3, f'F7_rs{rs_n}_{sr_n}_bt70_ATH3', 'F7')
        add(S & bt70 & ATH3 & rsmom, f'F7_rs{rs_n}_{sr_n}_bt70_ATH3_rsmom', 'F7')
        add(S & bt70 & ATH3 & rsmom & pp, f'F7_rs{rs_n}_{sr_n}_bt70_ATH3_rsmom_pp', 'F7')
        add(S & bt70 & ATH3 & rsmom & vc75, f'F7_rs{rs_n}_{sr_n}_bt70_ATH3_rsmom_vc75', 'F7')
        add(S & bt70 & ATH3 & rsmom & vc75 & pp, f'F7_rs{rs_n}_{sr_n}_bt70_ATH3_rsmom_vc75_pp', 'F7')
        add(S & bt70 & ATH3 & rsmom & vc75 & pp & vt75, f'F7_rs{rs_n}_{sr_n}_bt70_ATH3_rsmom_vc75_pp_vt75', 'F7')
        add(S & ddr10, f'F7_rs{rs_n}_{sr_n}_ddr10', 'F7')
        add(S & ddr10 & bt70 & ATH3 & rsmom & vc75 & pp, f'F7_rs{rs_n}_{sr_n}_ddr10_bt70_ATH3_rsmom_vc75_pp', 'F7')
        add(S & ads, f'F7_rs{rs_n}_{sr_n}_ads', 'F7')
        add(S & res15, f'F7_rs{rs_n}_{sr_n}_res15', 'F7')
        add(S & res15 & bt70 & ATH3 & rsmom, f'F7_rs{rs_n}_{sr_n}_res15_bt70_ATH3_rsmom', 'F7')
        add(S & sp6m, f'F7_rs{rs_n}_{sr_n}_sp3m15', 'F7')
        add(S & sp6m & bt70 & ATH3 & rsmom, f'F7_rs{rs_n}_{sr_n}_sp3m15_bt70_ATH3_rsmom', 'F7')
print(f"  F7: {tested-t7} tested, {len([r for r in all_results if r['family']=='F7'])} scored")

# ─── F8: Multi-Factor Blend (orthogonal combinations) ────────────────────────
print("── F8: Multi-Factor Blend ─────────────────────────────────────")
t8 = tested
# Combine different families' core signals
PRISM_CORE = rs95 & bt70 & ATH3 & rsmom & vt75 & vc75 & pp
# Add each additional dimension one at a time
for add_gate, add_name in [
    (tc60, 'tc60'), (tc50, 'tc50'),
    (sh05, 'sh05'), (sh10, 'sh10'),
    (uvr55, 'uvr55'), (uvr60, 'uvr60'),
    (beta_lo, 'beta_lo'), (beta_mid, 'beta_mid'),
    (pct30, 'pct52wlo30'), (pct50, 'pct52wlo50'),
    (ma200, 'ma200_10'), (ma200_20, 'ma200_20'),
    (bp70, 'bp70'), (bp60, 'bp60'),
]:
    add(PRISM_CORE & add_gate, f'F8_PRISM_{add_name}', 'F8')
    add(PRISM_CORE & add_gate & ddr10, f'F8_PRISM_{add_name}_ddr10', 'F8')
    add(PRISM_CORE & add_gate & ddr15, f'F8_PRISM_{add_name}_ddr15', 'F8')
    add(PRISM_CORE & add_gate & ma20,  f'F8_PRISM_{add_name}_ma20', 'F8')
    add(PRISM_CORE & add_gate & (ddr15 | ma20), f'F8_PRISM_{add_name}_OR_ddr15_ma20', 'F8')
    add(PRISM_CORE & add_gate & sr15, f'F8_PRISM_{add_name}_sr15', 'F8')
    add(PRISM_CORE & add_gate & res15, f'F8_PRISM_{add_name}_res15', 'F8')

# Cross-family combos
for a_g, a_n in [(res15, 'res15'), (res20, 'res20')]:
    for b_g, b_n in [(sr15, 'sr15'), (sr20, 'sr20')]:
        for rs_g, rs_n in [(rs95, '95'), (rs90, '90')]:
            X = rs_g & bt70 & ATH3 & rsmom & vc75 & pp & a_g & b_g
            add(X, f'F8_rs{rs_n}_bt70_ATH3_rsmom_vc75_pp_{a_n}_{b_n}', 'F8')
            add(X & vt75, f'F8_rs{rs_n}_bt70_ATH3_rsmom_vc75_pp_{a_n}_{b_n}_vt75', 'F8')
            add(X & ddr10, f'F8_rs{rs_n}_bt70_ATH3_rsmom_vc75_pp_{a_n}_{b_n}_ddr10', 'F8')
            add(X & ads, f'F8_rs{rs_n}_bt70_ATH3_rsmom_vc75_pp_{a_n}_{b_n}_ads', 'F8')

# OR-gate structural variants (different from BEST_FINAL)
CORE50 = rs95 & bt70 & ATH3 & rsmom & vt05 & vc75 & pp
for or1_g, or1_n in [(ddr15, 'ddr15'), (ma20, 'ma20'), (res15, 'res15'), (sr15, 'sr15'), (tc60, 'tc60')]:
    for or2_g, or2_n in [(ddr10, 'ddr10'), (ma15, 'ma15'), (res12, 'res12'), (uvr55, 'uvr55'), (ads, 'ads')]:
        if or1_n != or2_n:
            add(CORE50 & (or1_g | or2_g), f'F8_CORE50_OR_{or1_n}_{or2_n}', 'F8')
            add(PRISM_CORE & (or1_g | or2_g), f'F8_PRISM_OR_{or1_n}_{or2_n}', 'F8')

print(f"  F8: {tested-t8} tested, {len([r for r in all_results if r['family']=='F8'])} scored")

# ── Final Report ───────────────────────────────────────────────────────────────
print(f"\n{'='*70}")
print(f"TOTAL TESTED: {tested} combinations")
scored = len(all_results)
grade_s_both = [r for r in all_results if r['grade'] == 'S_BOTH']
grade_s = [r for r in all_results if r['grade'] == 'S']
grade_a = [r for r in all_results if r['grade'] == 'A']
print(f"SCORED (N≥5): {scored}")
print(f"Grade S BOTH (h3≥65% in BOTH 2025+2020): {len(grade_s_both)}")
print(f"Grade S (h3≥65% in 2025): {len(grade_s)}")
print(f"Grade A (h3≥65% overall): {len(grade_a)}")

# Top 30 by h3
df_res = pd.DataFrame(all_results)
print(f"\n{'TOP 30 SIGNALS by h3:'}")
print(f"{'Label':<70} {'h3':>6} {'h25':>6} {'h20':>6} {'N':>5} {'Grade':<8} {'Fam'}")
print('-' * 115)
top = df_res.nlargest(30, 'h3')
for _, r in top.iterrows():
    h20 = f"{r['h20']:.0f}" if r['h20'] else 'nan'
    h25 = f"{r['h25']:.0f}" if not np.isnan(r['h25']) else 'nan'
    print(f"{r['label']:<70} {r['h3']:>6.1f} {h25:>6} {h20:>6} {r['N']:>5} {r['grade']:<8} {r['family']}")

print(f"\n{'TOP 30 Grade S BOTH:'}")
top_s = df_res[df_res['grade'] == 'S_BOTH'].nlargest(30, 'h3')
print(f"{'Label':<70} {'h3':>6} {'h25':>6} {'h20':>6} {'N':>5} {'avg%':>6}")
print('-' * 115)
for _, r in top_s.iterrows():
    h20 = f"{r['h20']:.0f}" if r['h20'] else 'nan'
    h25 = f"{r['h25']:.0f}" if not np.isnan(r['h25']) else 'nan'
    print(f"{r['label']:<70} {r['h3']:>6.1f} {h25:>6} {h20:>6} {r['N']:>5} {r['avg']:>6.3f}")

# Per-family summary
print(f"\nPer-family Grade S BOTH count:")
for fam in ['F1','F2','F3','F4','F5','F6','F7','F8']:
    fam_res = [r for r in all_results if r['family'] == fam]
    fam_s   = [r for r in fam_res if r['grade'] == 'S_BOTH']
    print(f"  {fam}: {len(fam_res)} scored, {len(fam_s)} Grade S BOTH")

# Save library
out_path = OUT_DIR / 'q_library_1000.json'
with open(out_path, 'w') as f:
    json.dump({'total_tested': tested, 'results': all_results}, f, indent=2)
print(f"\nFull library saved → {out_path}")
