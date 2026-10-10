"""
pulse_us_daily.py — PULSE-US Daily Screener
============================================
Architecture identical to PULSE-TH:
  - Loads ALL quality signals (h3>70%) from q_library_1000.json (~1,045 signals)
  - Parses signal labels → vectorized boolean conditions (mirrors signals.parquet columns)
  - Builds tickers × signals bool matrix in one numpy pass
  - breadth_pct = fired / N_QUALITY * 100   (same formula as PULSE-TH)
  - avg_h3      = avg h3 of top-3 fired signals per ticker

N_QUALITY = count of quality signals in library (h3>70%, grade in S_BOTH/S/A)
Resolution ≈ 0.1% per signal vs 5.3% per family (old 19-family version)

Usage:
    python scripts/research/pulse_us/pulse_us_daily.py [--date YYYY-MM-DD] [--no-telegram]
"""

import sqlite3
import pandas as pd
import numpy as np
import json
import os
import requests
import argparse
from datetime import datetime, timedelta
try:
    from pulse_us_db import init_db, upsert_signals, get_connection
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False
from pathlib import Path

# Use lightweight pulse_us_ohlcv.db if available (GitHub Actions / no local ohlcv.db)
# Fall back to full ohlcv.db when running locally with full pipeline
_BASE_DIR = Path(__file__).resolve().parents[3]
_pulse_db = _BASE_DIR / "data" / "research" / "pulse_us" / "pulse_us_ohlcv.db"
_full_db  = _BASE_DIR / "data" / "ohlcv.db"
DB_PATH   = _pulse_db if (_pulse_db.exists() and not _full_db.exists()) else \
            (_full_db  if _full_db.exists()  else _pulse_db)
RS_PATH  = _BASE_DIR / "data" / "rs_universe" / "latest.json"
LIB_PATH = _BASE_DIR / "data" / "research" / "pulse_us" / "library" / "q_library_1000.json"
OUT_PATH = _BASE_DIR / "data" / "research" / "pulse_us" / "pulse_us_daily_signals.json"


# ─────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────

def load_rs_universe():
    with open(RS_PATH, encoding="utf-8") as f:
        rs_data = json.load(f)
    universe = rs_data.get('universe', {})
    rows = []
    for ticker, item in universe.items():
        rows.append({
            'ticker':       ticker,
            'rs_pct':       float(item.get('rs_composite_pct') or 0),
            'rs_pct_3m':    float(item.get('rs_3m_pct') or 0),
            'rs_pct_6m':    float(item.get('rs_6m_pct') or 0),
            'rs_pct_1m':    float(item.get('rs_1m_pct') or 0),
            'adtv_6m_usd':  float(item.get('adtv_6m_usd') or 0),
        })
    return pd.DataFrame(rows).dropna()


def load_ohlcv(tickers, min_date):
    conn = sqlite3.connect(DB_PATH)
    placeholders = ','.join('?' * len(tickers))
    rows = conn.execute(
        f"SELECT ticker, date, open, high, low, close, volume FROM ohlcv "
        f"WHERE ticker IN ({placeholders}) AND date >= ? AND close IS NOT NULL "
        f"ORDER BY ticker, date",
        tickers + [min_date]
    ).fetchall()
    conn.close()
    df = pd.DataFrame(rows, columns=['ticker', 'date', 'open', 'high', 'low', 'close', 'volume'])
    df['date'] = pd.to_datetime(df['date'])
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


def load_quality_signals():
    """Load all quality signals (h3>70%, grade S_BOTH/S/A) from library."""
    with open(LIB_PATH, encoding="utf-8") as f:
        lib = json.load(f)
    quality = [
        r for r in lib.get('results', [])
        if r.get('h3', 0) > 70.0 and r.get('grade', '') in ('S_BOTH', 'S', 'A')
    ]
    quality.sort(key=lambda x: -x['h3'])
    return quality


# ─────────────────────────────────────────────
# Factor computation
# ─────────────────────────────────────────────

def compute_factors(grp):
    grp = grp.sort_values('date').copy()
    if len(grp) < 63:
        return None
    close  = grp['close'].values
    volume = grp['volume'].values
    high   = grp['high'].values
    low    = grp['low'].values
    n      = len(close)
    px     = close[-1]

    def safe_mean(arr): return np.nanmean(arr) if not np.all(np.isnan(arr)) else np.nan

    high252 = np.nanmax(close[-252:]) if n >= 252 else np.nanmax(close)
    low252  = np.nanmin(close[-252:]) if n >= 252 else np.nanmin(close)
    pct_from_52w_high = (px / high252 - 1) * 100
    pct_from_52w_low  = (px / low252 - 1) * 100 if low252 > 0 else 0.0

    ma50  = safe_mean(close[-50:])  if n >= 50  else safe_mean(close)
    ma200 = safe_mean(close[-200:]) if n >= 200 else safe_mean(close)
    price_vs_ma50_pct  = (px / ma50  - 1) * 100 if ma50  and ma50  > 0 else 0.0
    price_vs_ma200_pct = (px / ma200 - 1) * 100 if ma200 and ma200 > 0 else 0.0

    vol_trend = 0.0
    if n >= 20:
        vols = pd.Series(volume[-20:]).ffill().fillna(0).values
        vm   = np.nanmean(vols) or 1e-10
        try:   vol_trend = float(np.polyfit(np.arange(len(vols)), vols / vm, 1)[0])
        except Exception: pass

    vol_contraction = 1.0
    if n >= 30:
        vols = pd.Series(volume[-30:]).ffill().fillna(0).values
        rec, prior = np.nanmean(vols[-10:]), np.nanmean(vols[-30:-10])
        if prior > 0: vol_contraction = rec / prior

    base_tight = 1.0
    if n >= 20:
        h20, l20, c20 = high[-20:], low[-20:], close[-20:]
        valid = ~(np.isnan(h20) | np.isnan(l20))
        if valid.sum() >= 5:
            base_tight = np.nanmean(h20[valid] - l20[valid]) / (np.nanmean(c20[valid]) + 1e-10)

    pocket_pivot = 0.0
    if n >= 11:
        rets10    = np.diff(close[-11:])
        down_vols = volume[-10:][rets10 < 0]
        max_down  = np.nanmax(down_vols) if len(down_vols) > 0 else 0
        last_ret  = close[-1] - close[-2]
        last_vol  = volume[-1] if not np.isnan(volume[-1]) else 0
        if last_ret > 0 and last_vol > max_down > 0:
            pocket_pivot = float(last_vol / max_down)

    dd_recovery = 1.0
    if n >= 63:
        min63 = np.nanmin(close[-63:])
        if min63 > 0: dd_recovery = px / min63

    trend_consistency = 50.0
    if n >= 21:
        trend_consistency = float((np.diff(close[-21:]) > 0).mean() * 100)

    sharpe_60d = 0.0
    if n >= 61:
        ret60 = np.diff(np.log(close[-61:]))
        sharpe_60d = float(np.nanmean(ret60) / (np.nanstd(ret60) + 1e-10) * np.sqrt(252))

    up_vol_ratio = 0.5
    if n >= 16:
        ret15 = np.diff(close[-16:])
        vol15 = volume[-15:]
        valid = ~np.isnan(vol15)
        if valid.sum() > 0:
            tot = vol15[valid].sum()
            up  = vol15[valid][ret15[valid] > 0].sum() if (ret15[valid] > 0).any() else 0
            up_vol_ratio = float(up / (tot + 1e-10))

    def resilience(period):
        if n < period: return 0.0
        c = close[-period:]
        ret = (c[-1] / c[0] - 1) if c[0] > 0 else 0.0
        peak, trough = np.nanmax(c), np.nanmin(c)
        dd = (trough / peak - 1) if peak > 0 else -1.0
        return abs(ret / dd) if dd < 0 else ret * 10
    resilience_3m = resilience(63)
    resilience_6m = resilience(126)

    spring_score = 0.0
    if n >= 11:
        low10 = np.nanmin(low[-11:-1]) if not np.all(np.isnan(low[-11:-1])) else px
        if low[-1] < low10 and close[-1] > low10:
            spring_score = float((close[-1] - low[-1]) / (low10 - low[-1] + 1e-10))

    ad_slope = 0.0
    if n >= 11:
        h = high[-10:]; l = low[-10:]; c = close[-10:]; v = volume[-10:]
        hl_range = h - l
        clv = np.where(hl_range > 0, ((c - l) - (h - c)) / (hl_range + 1e-10), 0)
        mfv = clv * np.where(np.isnan(v), 0, v)
        adl = np.cumsum(mfv)
        adl_mean = np.abs(adl).mean() or 1e-10
        try:   ad_slope = float(np.polyfit(np.arange(len(adl)), adl / adl_mean, 1)[0])
        except Exception: pass

    range52 = high252 - low252
    base_position = (px - low252) / range52 if range52 > 0 else 0.5

    return {
        'price':               px,
        'pct_from_52w_high':   pct_from_52w_high,
        'pct_from_52w_low':    pct_from_52w_low,
        'price_vs_ma50_pct':   price_vs_ma50_pct,
        'price_vs_ma200_pct':  price_vs_ma200_pct,
        'vol_trend':           vol_trend,
        'vol_contraction':     vol_contraction,
        'base_tight':          base_tight,
        'pocket_pivot':        pocket_pivot,
        'dd_recovery':         dd_recovery,
        'trend_consistency':   trend_consistency,
        'sharpe_60d':          sharpe_60d,
        'up_vol_ratio':        up_vol_ratio,
        'resilience_3m':       resilience_3m,
        'resilience_6m':       resilience_6m,
        'sector_resilience_6m': resilience_6m,
        'spring_score':        spring_score,
        'ad_slope':            ad_slope,
        'base_position':       base_position,
    }


# ─────────────────────────────────────────────
# PULSE-TH style: vectorized condition dict
# Mirrors how signals.parquet columns are pre-computed
# ─────────────────────────────────────────────

def build_condition_dict(df):
    """Build atomic boolean Series for every known signal token."""
    c = {}

    # RS
    c['rs95'] = df['rs_pct'] >= 95
    c['rs90'] = df['rs_pct'] >= 90
    c['rs85'] = df['rs_pct'] >= 85
    c['rs80'] = df['rs_pct'] >= 80

    # 52W high/low
    c['ATH3'] = df['pct_from_52w_high'] >= -3
    c['ATH5'] = df['pct_from_52w_high'] >= -5
    c['lo30'] = df['pct_from_52w_low'] >= 30
    c['lo50'] = df['pct_from_52w_low'] >= 50
    c['lo70'] = df['pct_from_52w_low'] >= 70

    # RS momentum
    c['rsmom']  = df['rs_pct_3m'] > df['rs_pct_6m']
    c['accel5'] = (df['rs_pct_1m'] - df['rs_pct_3m']) >= 5

    # Base tight
    c['bt70'] = df['base_tight'] <= 0.70
    c['bt60'] = df['base_tight'] <= 0.60

    # Volume contraction
    c['vc75'] = df['vol_contraction'] <= 0.75
    c['vc70'] = df['vol_contraction'] <= 0.70

    # Pocket pivot
    c['pp'] = df['pocket_pivot'] > 0.5

    # Volume trend
    c['vt05'] = df['vol_trend'] > 0.05
    c['vt10'] = df['vol_trend'] > 0.10
    c['vt12'] = df['vol_trend'] > 0.12
    c['vt75'] = df['vol_trend'] > 0.75
    c['vt80'] = df['vol_trend'] > 0.80

    # DD recovery
    c['ddr10'] = df['dd_recovery'] >= 1.0
    c['ddr12'] = df['dd_recovery'] >= 1.2
    c['ddr15'] = df['dd_recovery'] >= 1.5
    c['ddr20'] = df['dd_recovery'] >= 2.0

    # Trend consistency
    c['tc50'] = df['trend_consistency'] >= 50
    c['tc60'] = df['trend_consistency'] >= 60
    c['tc70'] = df['trend_consistency'] >= 70
    c['tc75'] = df['trend_consistency'] >= 75

    # Sharpe
    c['sh05'] = df['sharpe_60d'] >= 0.5
    c['sh10'] = df['sharpe_60d'] >= 1.0
    c['sh15'] = df['sharpe_60d'] >= 1.5
    c['sh20'] = df['sharpe_60d'] >= 2.0

    # Up-vol ratio
    c['uvr55'] = df['up_vol_ratio'] >= 0.55
    c['uvr60'] = df['up_vol_ratio'] >= 0.60
    c['uvr65'] = df['up_vol_ratio'] >= 0.65

    # Resilience 3m
    c['res15'] = df['resilience_3m'] >= 1.5
    c['res20'] = df['resilience_3m'] >= 2.0

    # Sector / resilience 6m (proxy = resilience_6m)
    c['sr15']    = df['sector_resilience_6m'] >= 1.5
    c['sr20']    = df['sector_resilience_6m'] >= 2.0
    c['res6m15'] = df['resilience_6m'] >= 1.5
    c['res6m20'] = df['resilience_6m'] >= 2.0

    # MA-relative
    c['ma200_10']  = df['price_vs_ma200_pct'] >= 10
    c['ma200_20']  = df['price_vs_ma200_pct'] >= 20
    c['ma200_30']  = df['price_vs_ma200_pct'] >= 30
    c['ma50_neg5'] = df['price_vs_ma50_pct'] >= -5
    c['ma20']      = df['price_vs_ma50_pct'] >= 20
    c['ma25']      = df['price_vs_ma50_pct'] >= 25

    # Spring / Wyckoff
    c['spring_any'] = df['spring_score'] > 0
    c['spring_med'] = df['spring_score'] >= 0.5
    c['spring_hi']  = df['spring_score'] >= 1.0

    # A/D slope
    c['ads'] = df['ad_slope'] > 0

    # Base position
    c['bp70'] = df['base_position'] >= 0.70
    c['bp80'] = df['base_position'] >= 0.80
    c['bp90'] = df['base_position'] >= 0.90

    # ── Composite (shorthand tokens used in signal labels) ────────────
    c['full']     = c['bt70'] & c['ATH3'] & c['rsmom'] & c['vc75'] & c['pp']
    c['BF_style'] = c['full'] & c['vt75'] & (c['ddr15'] | c['ma20'])
    c['PRISM']    = c['rs95'] & c['ATH3'] & c['rsmom'] & c['vc75'] & c['pp'] & c['vt75']
    c['OR']       = c['ddr15'] | c['ma20']

    return c


# Token lists sorted longest-first to avoid prefix collisions
_MULTI_TOKENS = sorted([
    'ma200_10', 'ma200_20', 'ma200_30',
    'ma50_neg5',
    'res6m15', 'res6m20',
    'spring_any', 'spring_med', 'spring_hi',
    'BF_style',
], key=len, reverse=True)

_SINGLE_TOKENS = sorted([
    'rs95', 'rs90', 'rs85', 'rs80',
    'ATH3', 'ATH5',
    'rsmom', 'accel5',
    'bt70', 'bt60',
    'vc75', 'vc70',
    'pp',
    'vt05', 'vt10', 'vt12', 'vt75', 'vt80',
    'ddr10', 'ddr12', 'ddr15', 'ddr20',
    'tc50', 'tc60', 'tc70', 'tc75',
    'sh05', 'sh10', 'sh15', 'sh20',
    'uvr55', 'uvr60', 'uvr65',
    'sr15', 'sr20',
    'res15', 'res20',
    'lo30', 'lo50', 'lo70',
    'ads',
    'bp70', 'bp80', 'bp90',
    'full', 'PRISM', 'OR',
    'ma20', 'ma25',
], key=len, reverse=True)


def label_to_tokens(label):
    """Parse signal label → list of condition token names."""
    idx = label.find('_')
    if idx == -1:
        return []
    s = label[idx + 1:]
    tokens = []
    while s:
        matched = False
        for tok in _MULTI_TOKENS:
            if s == tok or s.startswith(tok + '_'):
                tokens.append(tok)
                s = s[len(tok) + 1:] if len(s) > len(tok) else ''
                matched = True
                break
        if not matched:
            for tok in _SINGLE_TOKENS:
                if s == tok or s.startswith(tok + '_'):
                    tokens.append(tok)
                    s = s[len(tok) + 1:] if len(s) > len(tok) else ''
                    matched = True
                    break
        if not matched:
            nxt = s.find('_')
            if nxt == -1:
                break
            s = s[nxt + 1:]
    return tokens


def compute_signal_series(label, cond_dict):
    """Convert signal label → boolean Series over all tickers (vectorized).

    Returns None if ANY token in the label is not in cond_dict — unknown tokens
    mean we cannot compute the full AND formula, so the entire signal is skipped.
    Silently dropping unknown tokens (old behavior) caused stocks to pass more
    easily than the real formula because fewer AND conditions were applied.
    """
    tokens = label_to_tokens(label)
    if not tokens:
        return None
    unknown = [t for t in tokens if t not in cond_dict]
    if unknown:
        # Log once per unknown token family to aid diagnosis
        print(f"[PULSE-US] SKIP signal '{label}': unknown token(s) {unknown}")
        return None
    result = cond_dict[tokens[0]].copy()
    for tok in tokens[1:]:
        result = result & cond_dict[tok]
    return result


# ─────────────────────────────────────────────
# Market context
# ─────────────────────────────────────────────

def compute_market_context():
    try:
        rs_df   = load_rs_universe()
        n_total = len(rs_df)
        return {
            'n_universe': n_total,
            'n_rs70':     int((rs_df['rs_pct'] >= 70).sum()),
            'n_rs95':     int((rs_df['rs_pct'] >= 95).sum()),
            'pct_rs70':   round((rs_df['rs_pct'] >= 70).mean() * 100, 1),
        }
    except Exception:
        return {'n_universe': 0, 'n_rs70': 0, 'n_rs95': 0, 'pct_rs70': 0.0}


# ─────────────────────────────────────────────
# PULSE-TH breadth formula (vectorized numpy)
# ─────────────────────────────────────────────

def calc_pulse_scores(signal_matrix, tickers, sig_h3_arr, sig_labels, n_quality_families):
    """
    PULSE-TH compatible formula with family-level dedup:
      breadth     = number of FAMILIES that fired ≥1 signal (not raw signal count)
      breadth_pct = breadth_families / n_quality_families * 100
      avg_h3      = avg h3 of top-3 individual signals fired (unchanged)

    Why family dedup: PULSE-US has 1,045 correlated signals across 19 families.
    A stock can fire 50+ F6 variants at once (all h3>70%), inflating raw breadth.
    Family-level counting mirrors PULSE-TH's independent axes of evidence.
    """
    # Extract family prefix (F1, F2, ... F22) from each signal label
    sig_families = [lbl.split('_')[0] for lbl in sig_labels]
    unique_families = sorted(set(sig_families))

    scores = {}
    for i, ticker in enumerate(tickers):
        fired_mask = signal_matrix[i]

        # Family breadth: count distinct families with ≥1 signal fired
        fired_families = set(sig_families[j] for j, f in enumerate(fired_mask) if f)
        breadth = len(fired_families)
        breadth_pct = round(breadth / n_quality_families * 100, 1) if n_quality_families > 0 else 0.0

        # avg_h3: top-3 individual signals (same as PULSE-TH, not deduplicated)
        if fired_mask.any():
            top3 = sorted(sig_h3_arr[fired_mask].tolist(), reverse=True)[:3]
            avg_h3 = round(sum(top3) / len(top3), 1)
        else:
            avg_h3 = 0.0

        scores[ticker] = {
            'breadth':        breadth,           # families fired
            'breadth_pct':    breadth_pct,        # families / 19 * 100
            'avg_h3':         avg_h3,
            'n_signals_fired': int(fired_mask.sum()),  # raw for debug
        }
    return scores


# ─────────────────────────────────────────────
# Main screen
# ─────────────────────────────────────────────

def run_screen(quality_sigs, target_date=None):
    rs_df = load_rs_universe()
    candidates_df = rs_df[(rs_df['rs_pct'] >= 90) & (rs_df['adtv_6m_usd'] >= 15_000_000)].copy()
    candidates    = candidates_df['ticker'].tolist()
    n_rs90 = (rs_df['rs_pct'] >= 90).sum()
    print(f"Universe: {len(rs_df)} | RS≥90: {n_rs90} | RS≥90 + ADTV≥$15M: {len(candidates)}")

    if not candidates:
        return None

    min_date = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
    ohlcv    = load_ohlcv(candidates, min_date)
    if target_date:
        ohlcv = ohlcv[ohlcv['date'] <= pd.Timestamp(target_date)]

    print(f"Computing factors for {ohlcv['ticker'].nunique()} tickers...")
    factor_rows = []
    for ticker, grp in ohlcv.groupby('ticker'):
        factors = compute_factors(grp)
        if factors is None:
            continue
        rs_row = candidates_df[candidates_df['ticker'] == ticker]
        if rs_row.empty:
            continue
        row = {'ticker': ticker}
        row.update(factors)
        row.update({
            'rs_pct':     rs_row['rs_pct'].iloc[0],
            'rs_pct_3m':  rs_row['rs_pct_3m'].iloc[0],
            'rs_pct_6m':  rs_row['rs_pct_6m'].iloc[0],
            'rs_pct_1m':  rs_row['rs_pct_1m'].iloc[0],
        })
        factor_rows.append(row)

    if not factor_rows:
        return None

    fact_df      = pd.DataFrame(factor_rows).reset_index(drop=True)
    tickers_list = fact_df['ticker'].tolist()
    print(f"Factors ready: {len(fact_df)} tickers")

    # ── Vectorized condition dict (mirrors signals.parquet in PULSE-TH) ──
    cond_dict = build_condition_dict(fact_df)

    # ── Parse every quality signal → boolean Series → stack into matrix ──
    print(f"Building signal matrix: {len(quality_sigs)} signals × {len(fact_df)} tickers...")
    sig_labels  = []
    sig_h3_list = []
    sig_cols    = []
    parse_failures = 0

    for sig in quality_sigs:
        s = compute_signal_series(sig['label'], cond_dict)
        if s is None:
            parse_failures += 1
            continue
        sig_labels.append(sig['label'])
        sig_h3_list.append(sig['h3'])
        sig_cols.append(s.values)

    n_quality = len(sig_labels)
    print(f"  Parsed: {n_quality} | Skipped (unrecognized tokens): {parse_failures}")

    if n_quality == 0:
        return None

    # (N_tickers × N_signals) bool matrix — identical to PULSE-TH's q_arr
    signal_matrix = np.column_stack(sig_cols)
    sig_h3_arr    = np.array(sig_h3_list)

    # n_quality_families = distinct families that have ≥1 quality signal
    # (denominator for breadth_pct — comparable to PULSE-TH's N_QUALITY)
    sig_families_all = [lbl.split('_')[0] for lbl in sig_labels]
    n_quality_families = len(set(sig_families_all))
    print(f"  Families with quality signals: {n_quality_families}")

    # Per-ticker scores (family-dedup breadth)
    pulse_scores = calc_pulse_scores(signal_matrix, tickers_list, sig_h3_arr,
                                     sig_labels, n_quality_families)

    # Fired signal labels per ticker (for output / detail cards)
    ticker_hits = {}
    for i, ticker in enumerate(tickers_list):
        fired_idx = np.where(signal_matrix[i])[0]
        if len(fired_idx) > 0:
            ticker_hits[ticker] = [sig_labels[j] for j in fired_idx]

    # Factor snapshot per ticker (for Telegram cards)
    ticker_factors = {
        row['ticker']: {
            'price':             round(row['price'], 2),
            'rs_pct':            round(row['rs_pct'], 1),
            'pct_from_52w_high': round(row['pct_from_52w_high'], 1),
            'price_vs_ma50_pct': round(row['price_vs_ma50_pct'], 1),
            'dd_recovery':       round(row['dd_recovery'], 2),
            'base_tight':        round(row['base_tight'], 3),
            'pocket_pivot':      round(row['pocket_pivot'], 2),
            'up_vol_ratio':      round(row['up_vol_ratio'], 2),
        }
        for _, row in fact_df.iterrows()
    }

    return {
        'n_quality':           n_quality,            # total individual signals parsed
        'n_quality_families':  n_quality_families,   # distinct families = breadth denominator
        'signal_matrix':       signal_matrix,
        'sig_labels':          sig_labels,
        'sig_h3_arr':          sig_h3_arr,
        'tickers':             tickers_list,
        'pulse_scores':        pulse_scores,
        'ticker_hits':         ticker_hits,
        'ticker_factors':      ticker_factors,
    }


# ─────────────────────────────────────────────
# Telegram — identical structure to PULSE-TH
# MSG 1: header + regime/breadth summary
# MSG 2: Focus List  (#  Ticker  RS  Price  HR  Brd  🔴🟠🟡)
# MSG 3: PULSE Top 5 ranked by HR×Breadth composite
# ─────────────────────────────────────────────

def _pulse_icon(h3, bp):
    # Icon = h3 (hit rate) only — breadth is NOT a gate, only a ranking tiebreaker
    # Distribution analysis 2026-09-30: breadth has no predictive power for fwd3 in US
    # breadth is shown in display but does not change the icon tier
    if h3 >= 80:
        return "🔴"
    elif h3 >= 75:
        return "🟠"
    return "🟡"


def format_telegram(result, mkt, run_date):
    date_str          = run_date.strftime('%Y-%m-%d')
    n_quality         = result['n_quality']
    n_quality_families = result['n_quality_families']
    pulse_scores      = result['pulse_scores']
    ticker_hits       = result['ticker_hits']
    ticker_factors    = result['ticker_factors']
    sig_h3_map        = dict(zip(result['sig_labels'], result['sig_h3_arr'].tolist()))

    total = sum(len(v) for v in ticker_hits.values())

    # ── MSG 1: Header (mirrors [TH] AlphaAbsolute-TH header style) ───
    lines1 = [
        f"<b>[US] PULSE-US Daily  |  {date_str}</b>",
        f"Universe: {mkt['n_universe']}  RS≥95: {mkt['n_rs95']}  RS≥70: {mkt['pct_rs70']:.0f}%",
        f"Signals: {n_quality} ({n_quality_families} families, h3>70%)",
        f"Tickers hit: {len(ticker_hits)}  |  Total fires: {total}",
    ]
    if not ticker_hits:
        # Still send one message if nothing fired
        return [f"<b>[US] PULSE-US  |  {date_str}</b>\nNo tickers fired any quality signal today."]
    messages = []  # Skip header — send only Focus List + Top 5

    # Sort by RS descending (same as PULSE-TH focus list default)
    focus_by_rs = sorted(
        ticker_hits.keys(),
        key=lambda t: -ticker_factors[t]['rs_pct']
    )

    # ── MSG 2: Focus List (mirrors _send_focus_list exactly) ──────────
    lines2 = [f"<b>[US] Focus List  |  {date_str}</b>"]
    lines2.append(f"{'#':<3} {'Ticker':<10} {'RS':>4}  {'Price':>8}  {'HR':>5} {'Brd':>5}")
    lines2.append("─" * 50)

    for rank, ticker in enumerate(focus_by_rs[:20], 1):
        sc  = pulse_scores[ticker]
        fac = ticker_factors[ticker]
        h3  = sc['avg_h3']
        bp  = sc['breadth_pct']
        px_str = f"${fac['price']:,.2f}"

        if bp > 0:
            icon = _pulse_icon(h3, bp)
            pulse_str = f"  {icon}HR{h3:.0f}% Brd{bp:.0f}%"
        else:
            pulse_str = ""

        lines2.append(f"{rank:<3} {ticker:<10} {fac['rs_pct']:>4.0f}  {px_str:>8}{pulse_str}")

    lines2.append("")
    lines2.append("* HR = avg hitrate ของ top-3 signals ที่ดีที่สุดที่ fire วันนี้")
    lines2.append(f"* Breadth = families (/{n_quality_families}) ที่มี signal h3>70% fire พร้อมกัน")
    lines2.append("🔴HR>=80% STRONG  🟠HR>=75% WATCH  Brd=tiebreaker")
    messages.append("\n".join(lines2))

    # ── MSG 3: PULSE Top 5 ────────────────────────────────────────────
    # Sort: h3 (hit rate) PRIMARY, breadth_pct as tiebreaker
    # breadth is informational — not a gate, not a primary rank signal
    rows = []
    for ticker, sc in pulse_scores.items():
        if ticker not in ticker_hits:
            continue
        h3 = sc['avg_h3']
        bp = sc['breadth_pct']
        if h3 < 62.0:
            continue
        composite = h3 * 1000 + bp  # h3 strictly primary, bp breaks ties
        fac = ticker_factors[ticker]
        rows.append({'ticker': ticker, 'h3': h3, 'bp': bp,
                     'composite': composite, 'price': fac['price'],
                     'rs_pct': fac['rs_pct']})

    if rows:
        top5 = sorted(rows, key=lambda x: -x['composite'])[:5]
        lines3 = [f"<b>[US] PULSE-US Top 5  |  {date_str}</b>"]
        lines3.append("rank by HR (primary), Breadth (tiebreaker)")
        lines3.append(f"{'#':<3} {'Ticker':<10} {'Price':>8}  {'HR':>5} {'Brd':>5}")
        lines3.append("─" * 44)
        for i, r in enumerate(top5, 1):
            px_str = f"${r['price']:,.2f}"
            icon   = _pulse_icon(r['h3'], r['bp'])
            lines3.append(
                f"{i:<3} {r['ticker']:<10} {px_str:>8}  "
                f"{icon}HR{r['h3']:.0f}% Brd{r['bp']:.0f}%"
            )
        lines3.append("")
        lines3.append("* HR = avg top-3 signal hitrate")
        lines3.append("* Breadth = % ของ signals คุณภาพสูง (h3>70%) ที่ fire พร้อมกัน")
        messages.append("\n".join(lines3))

    return messages


def send_telegram(text, token, chat_id):
    # Telegram hard limit: 4096 chars per message
    if len(text) > 4000:
        text = text[:3970] + '\n... [truncated]'
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    try:
        r = requests.post(url, json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
                          verify=False, timeout=10)
        if r.ok:
            return True
        if r.status_code == 400:
            r2 = requests.post(url, json={'chat_id': chat_id, 'text': text},
                               verify=False, timeout=10)
            return r2.ok
        return False
    except Exception as e:
        print(f"[Telegram] {e}")
        return False


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date',        default=None)
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    run_date = datetime.strptime(args.date, '%Y-%m-%d') if args.date else datetime.now()

    quality_sigs = load_quality_signals()
    print(f"PULSE-US Daily — {run_date.date()}  |  Library: {len(quality_sigs)} quality signals")
    print("=" * 60)

    mkt    = compute_market_context()
    result = run_screen(quality_sigs, args.date)

    if result is None:
        print("No results.")
        Path(OUT_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(OUT_PATH, 'w', encoding='utf-8') as f:
            json.dump({'date': run_date.strftime('%Y-%m-%d'), 'n_quality': 0,
                       'total_hits': 0, 'unique_tickers': 0}, f)
        return

    n_quality    = result['n_quality']
    pulse_scores = result['pulse_scores']
    ticker_hits  = result['ticker_hits']

    print(f"\nN_QUALITY={n_quality}  |  Tickers hit: {len(ticker_hits)}")
    ranked = sorted(ticker_hits, key=lambda t: -(pulse_scores[t]['avg_h3'] * 1000 + pulse_scores[t]['breadth_pct']))
    for ticker in ranked[:20]:
        sc = pulse_scores[ticker]
        print(f"  {ticker}: {sc['breadth']}/{n_quality} ({sc['breadth_pct']:.1f}%)  avg_h3={sc['avg_h3']:.1f}%")

    Path(OUT_PATH).parent.mkdir(parents=True, exist_ok=True)
    out = {
        'date':           run_date.strftime('%Y-%m-%d'),
        'market_context': mkt,
        'n_quality':      n_quality,
        'total_hits':     sum(len(v) for v in ticker_hits.values()),
        'unique_tickers': len(ticker_hits),
        'pulse_scores':   pulse_scores,
        'ticker_hits':    {t: v for t, v in sorted(
                              ticker_hits.items(),
                              key=lambda x: -pulse_scores[x[0]]['breadth_pct'])},
    }
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nSaved → {OUT_PATH}")

    token   = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    messages = format_telegram(result, mkt, run_date)

    if args.no_telegram or not token or not chat_id:
        print("\n--- TELEGRAM PREVIEW ---")
        for i, msg in enumerate(messages):
            print(f"\n[MSG {i+1}]"); print(msg)
        return

    for i, msg in enumerate(messages):
        ok = send_telegram(msg, token, chat_id)
        print(f"[Telegram] msg {i+1}: {'OK' if ok else 'FAIL'}")


def run():
    """Entry point for pre_market_runner pipeline."""
    main()

if __name__ == '__main__':
    main()
