"""
pulse_us_daily.py
=================
PULSE-US Daily Screener — runs top signals from q_library_1000.json on live data.
Sends Telegram in PULSE-TH style: Breadth + HR + Focus List.

Signal catalog (top S_BOTH per family, sorted by h3):
  SIG1  F1  h3=83.3%  rs95 + vt75 + ddr12  (VCP/ATH tight)
  SIG2  F6  h3=83.3%  rs95 + ddr12 + full + vt75  (Recovery full)
  SIG3  F8  h3=81.0%  PRISM + tc50 + ddr15  (Multi-factor)
  SIG4  F21 h3=80.0%  rs90 + bt60 + ATH3 + ddr15 + vc70  (Tight ATH)
  SIG5  F5  h3=77.1%  rs95 + tc50 + BF_style  (Trend strength)
  SIG6  F10 h3=75.8%  rs95 + spring + vc75 + ATH3 + vt75 + OR  (Spring/Wyckoff)

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
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH  = "data/ohlcv.db"
RS_PATH  = "data/rs_universe/latest.json"
OUT_PATH = "data/research/pulse_us/pulse_us_daily_signals.json"


# ─────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────

def load_rs_universe():
    """Load RS universe, return {ticker: {rs_pct, rs_pct_3m, rs_pct_6m}}."""
    with open(RS_PATH) as f:
        rs_data = json.load(f)
    universe = rs_data.get('universe', {})
    rows = []
    for ticker, item in universe.items():
        rows.append({
            'ticker':    ticker,
            'rs_pct':    float(item.get('rs_composite_pct') or 0),
            'rs_pct_3m': float(item.get('rs_3m_pct') or 0),
            'rs_pct_6m': float(item.get('rs_6m_pct') or 0),
            'rs_accel':  float(item.get('rs_1m_pct') or 0) - float(item.get('rs_3m_pct') or 0),
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


# ─────────────────────────────────────────────
# Factor computation (matches backtest parquet)
# ─────────────────────────────────────────────

def compute_factors(grp):
    """Compute all signal factors for a single ticker. Returns dict or None."""
    grp = grp.sort_values('date').copy()
    if len(grp) < 63:
        return None

    close  = grp['close'].values
    volume = grp['volume'].values
    high   = grp['high'].values
    low    = grp['low'].values
    n      = len(close)
    px     = close[-1]

    # pct_from_52w_high
    high252 = np.nanmax(close[-252:]) if n >= 252 else np.nanmax(close)
    pct_from_52w_high = (px / high252 - 1) * 100

    # pct_from_52w_low
    low252 = np.nanmin(close[-252:]) if n >= 252 else np.nanmin(close)
    pct_from_52w_low  = (px / low252 - 1) * 100 if low252 > 0 else 0.0

    # price_vs_ma50_pct
    ma50 = np.nanmean(close[-50:]) if n >= 50 else np.nanmean(close)
    price_vs_ma50_pct = (px / ma50 - 1) * 100 if ma50 > 0 else 0.0

    # price_vs_ma200_pct
    ma200 = np.nanmean(close[-200:]) if n >= 200 else np.nanmean(close)
    price_vs_ma200_pct = (px / ma200 - 1) * 100 if ma200 > 0 else 0.0

    # ma_200 trend (compare to 21d ago)
    ma200_21d = np.nanmean(close[-221:-21]) if n >= 221 else ma200
    ma200_trending_up = ma200 > ma200_21d

    # vol_trend: 20d slope of normalized volume
    if n >= 20:
        vols = pd.Series(volume[-20:]).ffill().fillna(0).values
        x = np.arange(len(vols))
        vol_mean = np.nanmean(vols)
        vols_norm = vols / (vol_mean + 1e-10)
        try:
            slope = np.polyfit(x, vols_norm, 1)[0]
            vol_trend = float(slope)
        except Exception:
            vol_trend = 0.0
    else:
        vol_trend = 0.0

    # vol_contraction: recent 10d avg / prior 20d avg
    if n >= 30:
        vols = pd.Series(volume[-30:]).ffill().fillna(0).values
        rec   = np.nanmean(vols[-10:])
        prior = np.nanmean(vols[-30:-10])
        vol_contraction = rec / (prior + 1e-10) if prior > 0 else 1.0
    else:
        vol_contraction = 1.0

    # base_tight: avg(high-low)/avg(close) in last 20d
    if n >= 20:
        h20 = high[-20:]; l20 = low[-20:]; c20 = close[-20:]
        valid = ~(np.isnan(h20) | np.isnan(l20))
        if valid.sum() >= 5:
            atr    = np.nanmean(h20[valid] - l20[valid])
            base_tight = atr / (np.nanmean(c20[valid]) + 1e-10)
        else:
            base_tight = 1.0
    else:
        base_tight = 1.0

    # pocket_pivot: latest up-day volume vs max down-day vol in prior 10d
    pocket_pivot = 0.0
    if n >= 11:
        returns_10 = np.diff(close[-11:])
        down_vols  = volume[-10:][returns_10 < 0]
        max_down   = np.nanmax(down_vols) if len(down_vols) > 0 else 0
        last_ret   = close[-1] - close[-2]
        last_vol   = volume[-1] if not np.isnan(volume[-1]) else 0
        if last_ret > 0 and last_vol > max_down and max_down > 0:
            pocket_pivot = float(last_vol / max_down)

    # dd_recovery: close / rolling 63d min
    if n >= 63:
        min63 = np.nanmin(close[-63:])
        dd_recovery = px / min63 if min63 > 0 else 1.0
    else:
        dd_recovery = 1.0

    # trend_consistency: pct of 20d returns > 0
    if n >= 21:
        ret20 = np.diff(close[-21:])
        trend_consistency = float((ret20 > 0).mean() * 100)
    else:
        trend_consistency = 50.0

    # sharpe_60d
    if n >= 61:
        ret60 = np.diff(np.log(close[-61:]))
        sharpe_60d = float(np.nanmean(ret60) / (np.nanstd(ret60) + 1e-10) * np.sqrt(252))
    else:
        sharpe_60d = 0.0

    # up_vol_ratio: 15d up-vol / total vol
    if n >= 15:
        ret15  = np.diff(close[-16:])
        vol15  = volume[-15:]
        valid  = ~np.isnan(vol15)
        if valid.sum() > 0:
            up_vol = vol15[valid][ret15[valid] > 0].sum() if (ret15[valid] > 0).any() else 0
            up_vol_ratio = float(up_vol / (vol15[valid].sum() + 1e-10))
        else:
            up_vol_ratio = 0.5
    else:
        up_vol_ratio = 0.5

    # spring_score: simplified Wyckoff spring proxy
    # Price dips below 10d low then closes back above it
    spring_score = 0.0
    if n >= 11:
        low10 = np.nanmin(low[-11:-1]) if not np.all(np.isnan(low[-11:-1])) else px
        if low[-1] < low10 and close[-1] > low10:
            spring_score = float((close[-1] - low[-1]) / (low10 - low[-1] + 1e-10))

    # ad_slope: 10d A/D line slope (simplified)
    ad_slope = 0.0
    if n >= 11:
        h = high[-10:]; l = low[-10:]; c = close[-10:]; v = volume[-10:]
        hl_range = h - l
        clv = np.where(hl_range > 0, ((c - l) - (h - c)) / (hl_range + 1e-10), 0)
        mfv  = clv * np.where(np.isnan(v), 0, v)
        adl  = np.cumsum(mfv)
        if len(adl) >= 2:
            x = np.arange(len(adl))
            try:
                ad_slope = float(np.polyfit(x, adl / (np.abs(adl).mean() + 1e-10), 1)[0])
            except Exception:
                ad_slope = 0.0

    # sector_resilience_6m proxy: rs_pct_6m / 50 (normalized)
    # Populated from RS data later

    return {
        'price':              px,
        'pct_from_52w_high':  pct_from_52w_high,
        'pct_from_52w_low':   pct_from_52w_low,
        'price_vs_ma50_pct':  price_vs_ma50_pct,
        'price_vs_ma200_pct': price_vs_ma200_pct,
        'ma200_trending_up':  ma200_trending_up,
        'vol_trend':          vol_trend,
        'vol_contraction':    vol_contraction,
        'base_tight':         base_tight,
        'pocket_pivot':       pocket_pivot,
        'dd_recovery':        dd_recovery,
        'trend_consistency':  trend_consistency,
        'sharpe_60d':         sharpe_60d,
        'up_vol_ratio':       up_vol_ratio,
        'spring_score':       spring_score,
        'ad_slope':           ad_slope,
    }


# ─────────────────────────────────────────────
# Signal definitions (6 top S_BOTH signals)
# ─────────────────────────────────────────────

SIGNALS = [
    {
        'id': 'SIG1',
        'label': 'F1_vt75_ddr12',
        'family': 'F1',
        'desc': 'VCP/ATH tight + vol momentum + strong recovery',
        'h3': 83.3, 'h25': 92.3, 'h20': 72.7,
        'gate': lambda f, rs: (
            rs['rs_pct'] >= 95 and
            f['base_tight'] <= 0.70 and
            f['pct_from_52w_high'] >= -3.0 and
            rs['rs_pct_3m'] > rs['rs_pct_6m'] and
            f['vol_trend'] > 0.75 and
            f['dd_recovery'] >= 1.2
        ),
    },
    {
        'id': 'SIG2',
        'label': 'F6_rs95_ddr12_full_vt75',
        'family': 'F6',
        'desc': 'Recovery full combo: rs95 + tight base + pocket pivot + vol rise',
        'h3': 83.3, 'h25': 92.3, 'h20': 72.7,
        'gate': lambda f, rs: (
            rs['rs_pct'] >= 95 and
            f['dd_recovery'] >= 1.2 and
            f['base_tight'] <= 0.70 and
            f['pct_from_52w_high'] >= -3.0 and
            rs['rs_pct_3m'] > rs['rs_pct_6m'] and
            f['vol_contraction'] <= 0.75 and
            f['pocket_pivot'] > 0.5 and
            f['vol_trend'] > 0.75
        ),
    },
    {
        'id': 'SIG3',
        'label': 'F8_PRISM_tc50_ddr15',
        'family': 'F8',
        'desc': 'Multi-factor PRISM: rs95 + ATH3 + trend consistency + deep recovery',
        'h3': 81.0, 'h25': 100.0, 'h20': 66.7,
        'gate': lambda f, rs: (
            rs['rs_pct'] >= 95 and
            f['pct_from_52w_high'] >= -3.0 and
            rs['rs_pct_3m'] > rs['rs_pct_6m'] and
            f['vol_trend'] > 0.75 and
            f['vol_contraction'] <= 0.75 and
            f['pocket_pivot'] > 0.5 and
            f['trend_consistency'] >= 50 and
            f['dd_recovery'] >= 1.5
        ),
    },
    {
        'id': 'SIG4',
        'label': 'F21_rs90_bt60_ATH3_ddr15_vc70',
        'family': 'F21',
        'desc': 'Very tight base at ATH: rs90 + bt≤0.60 + ddr≥1.5',
        'h3': 80.0, 'h25': 100.0, 'h20': 83.3,
        'gate': lambda f, rs: (
            rs['rs_pct'] >= 90 and
            f['base_tight'] <= 0.60 and
            f['pct_from_52w_high'] >= -3.0 and
            rs['rs_pct_3m'] > rs['rs_pct_6m'] and
            f['dd_recovery'] >= 1.5 and
            f['vol_contraction'] <= 0.70
        ),
    },
    {
        'id': 'SIG5',
        'label': 'F5_rs95_tc50_BF_style',
        'family': 'F5',
        'desc': 'Trend strength: rs95 + high trend consistency + BestFinal style',
        'h3': 77.1, 'h25': 84.2, 'h20': 75.0,
        'gate': lambda f, rs: (
            rs['rs_pct'] >= 95 and
            f['base_tight'] <= 0.70 and
            f['pct_from_52w_high'] >= -3.0 and
            rs['rs_pct_3m'] > rs['rs_pct_6m'] and
            f['vol_trend'] > 0.75 and
            f['vol_contraction'] <= 0.75 and
            f['pocket_pivot'] > 0.5 and
            f['trend_consistency'] >= 50 and
            (f['dd_recovery'] >= 1.5 or f['price_vs_ma50_pct'] >= 20)
        ),
    },
    {
        'id': 'SIG6',
        'label': 'F10_rs95_spring_vc75_ATH3_vt75_OR',
        'family': 'F10',
        'desc': 'Wyckoff spring + rs95 + ATH + vol dry-up',
        'h3': 75.8, 'h25': 83.3, 'h20': 75.0,
        'gate': lambda f, rs: (
            rs['rs_pct'] >= 95 and
            f['spring_score'] > 0 and
            f['vol_contraction'] <= 0.75 and
            f['pct_from_52w_high'] >= -3.0 and
            f['vol_trend'] > 0.75 and
            (f['dd_recovery'] >= 1.5 or f['price_vs_ma50_pct'] >= 20)
        ),
    },
]


# ─────────────────────────────────────────────
# Breadth computation (PULSE-TH formula)
# ─────────────────────────────────────────────

# n_quality = number of signals with h3 > 70% (all 6 qualify)
N_QUALITY = sum(1 for s in SIGNALS if s['h3'] > 70.0)  # = 6


def compute_market_context():
    """Market-level RS counts (context only — not the main breadth metric)."""
    try:
        rs_df   = load_rs_universe()
        n_total = len(rs_df)
        n_rs70  = int((rs_df['rs_pct'] >= 70).sum())
        n_rs95  = int((rs_df['rs_pct'] >= 95).sum())
        pct70   = round(n_rs70 / n_total * 100, 1) if n_total > 0 else 0.0
        return {'n_universe': n_total, 'n_rs70': n_rs70, 'n_rs95': n_rs95, 'pct_rs70': pct70}
    except Exception:
        return {'n_universe': 0, 'n_rs70': 0, 'n_rs95': 0, 'pct_rs70': 0.0}


def calc_pulse_scores(ticker_hits):
    """
    PULSE-TH breadth formula applied to PULSE-US:
      breadth     = number of signals (h3>70%) that fired for this ticker
      breadth_pct = breadth / N_QUALITY * 100
      avg_h3      = average h3 of top-3 fired signals (same as PULSE-TH)
    """
    sig_h3 = {s['id']: s['h3'] for s in SIGNALS}
    scores = {}
    for ticker, fired_ids in ticker_hits.items():
        breadth = len(fired_ids)
        breadth_pct = round(breadth / N_QUALITY * 100, 1) if N_QUALITY > 0 else 0.0
        # avg_h3: top-3 h3 values of fired signals
        h3_vals = sorted([sig_h3[sid] for sid in fired_ids], reverse=True)[:3]
        avg_h3  = round(sum(h3_vals) / len(h3_vals), 1) if h3_vals else 0.0
        scores[ticker] = {
            'breadth':     breadth,
            'breadth_pct': breadth_pct,
            'avg_h3':      avg_h3,
        }
    return scores


# ─────────────────────────────────────────────
# Main screen
# ─────────────────────────────────────────────

def run_screen(target_date=None):
    """Run all 6 signals on live universe. Return dict of results per signal."""
    rs_df = load_rs_universe()
    n_total = len(rs_df)

    # Pre-filter: RS >= 90 (reduces universe ~90%)
    candidates_df = rs_df[rs_df['rs_pct'] >= 90].copy()
    candidates    = candidates_df['ticker'].tolist()
    print(f"Universe: {n_total} tickers | RS≥90 candidates: {len(candidates)}")

    if not candidates:
        return {}

    # Load OHLCV
    min_date = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
    ohlcv    = load_ohlcv(candidates, min_date)

    if target_date:
        td   = pd.Timestamp(target_date)
        ohlcv = ohlcv[ohlcv['date'] <= td]

    # Compute factors per ticker
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
            'rs_pct':    rs_row['rs_pct'].iloc[0],
            'rs_pct_3m': rs_row['rs_pct_3m'].iloc[0],
            'rs_pct_6m': rs_row['rs_pct_6m'].iloc[0],
            'rs_accel':  rs_row['rs_accel'].iloc[0],
        })
        factor_rows.append(row)

    if not factor_rows:
        return {}

    fact_df = pd.DataFrame(factor_rows)
    print(f"Factors computed: {len(fact_df)} tickers")

    # Evaluate each signal
    results = {}
    ticker_hits = {}  # ticker → list of signal ids

    for sig in SIGNALS:
        hits = []
        for _, row in fact_df.iterrows():
            f  = row.to_dict()
            rs = {'rs_pct': row['rs_pct'], 'rs_pct_3m': row['rs_pct_3m'], 'rs_pct_6m': row['rs_pct_6m']}
            try:
                if sig['gate'](f, rs):
                    hits.append({
                        'ticker':             row['ticker'],
                        'price':              round(row['price'], 2),
                        'rs_pct':             round(row['rs_pct'], 1),
                        'pct_from_52w_high':  round(row['pct_from_52w_high'], 1),
                        'price_vs_ma50_pct':  round(row['price_vs_ma50_pct'], 1),
                        'dd_recovery':        round(row['dd_recovery'], 2),
                        'vol_trend':          round(row['vol_trend'], 3),
                        'vol_contraction':    round(row['vol_contraction'], 2),
                        'base_tight':         round(row['base_tight'], 3),
                        'pocket_pivot':       round(row['pocket_pivot'], 2),
                        'trend_consistency':  round(row['trend_consistency'], 1),
                        'sharpe_60d':         round(row['sharpe_60d'], 2),
                    })
                    ticker_hits.setdefault(row['ticker'], []).append(sig['id'])
            except Exception:
                pass
        results[sig['id']] = hits
        print(f"  {sig['id']} ({sig['family']}): {len(hits)} hits")

    # Compute PULSE-TH style per-ticker scores
    pulse_scores = calc_pulse_scores(ticker_hits)

    return results, ticker_hits, fact_df, pulse_scores


# ─────────────────────────────────────────────
# Telegram formatting (PULSE-TH formula)
# ─────────────────────────────────────────────

def format_telegram(results, ticker_hits, pulse_scores, mkt, run_date):
    """
    PULSE-TH style Telegram output.
      - Header: date + market context (RS≥95 count, RS≥70%)
      - Per ticker: breadth_pct% | avg_h3% | signals fired
      - Sorted by breadth_pct desc, then avg_h3 desc
    """
    date_str       = run_date.strftime('%Y-%m-%d')
    unique_tickers = len(ticker_hits)

    # ── MSG 1: Header + signal count ──────────────
    lines = [
        f"[US] PULSE-US Daily  |  {date_str}",
        f"{'─'*38}",
        f"Universe: {mkt['n_universe']}  RS≥95: {mkt['n_rs95']}  RS≥70: {mkt['pct_rs70']:.0f}%",
        f"n_quality signals: {N_QUALITY}  |  tickers hit: {unique_tickers}",
        f"{'─'*38}",
    ]
    for sig in SIGNALS:
        n = len(results.get(sig['id'], []))
        lines.append(f"{sig['id']} ({sig['family']}) h3={sig['h3']}%  →  {n} hits")
    msg1 = "\n".join(lines)

    if not ticker_hits:
        return [msg1]

    # Build flat ticker table with pulse scores
    all_hits_flat = {}
    for sig_id, hits in results.items():
        for h in hits:
            tk = h['ticker']
            if tk not in all_hits_flat:
                all_hits_flat[tk] = dict(h)
    for tk, row in all_hits_flat.items():
        row['sigs']        = ticker_hits[tk]
        row['breadth']     = pulse_scores[tk]['breadth']
        row['breadth_pct'] = pulse_scores[tk]['breadth_pct']
        row['avg_h3']      = pulse_scores[tk]['avg_h3']

    # Sort: breadth_pct desc → avg_h3 desc → rs_pct desc (PULSE-TH order)
    ranked = sorted(
        all_hits_flat.values(),
        key=lambda x: (-x['breadth_pct'], -x['avg_h3'], -x['rs_pct'])
    )

    # ── MSG 2: Focus List ─────────────────────────
    lines2 = [
        f"FOCUS LIST  |  {date_str}",
        f"{'─'*38}",
        f"{'Ticker':<8}  {'Brdth':>5}  {'AvgHR':>6}  {'RS':>4}  {'ATH%':>6}",
        f"{'─'*38}",
    ]
    for row in ranked[:10]:
        star = ('★' if row['breadth_pct'] >= 66 else
                '◆' if row['breadth_pct'] >= 33 else '·')
        lines2.append(
            f"{star} ${row['ticker']:<7} "
            f"{row['breadth_pct']:>4.0f}%  "
            f"{row['avg_h3']:>5.1f}%  "
            f"RS{int(row['rs_pct']):>3}  "
            f"{row['pct_from_52w_high']:>+5.1f}%"
        )
    lines2 += [
        f"",
        f"Brdth = signals fired / {N_QUALITY} (h3>70%)",
        f"AvgHR = avg h3 of top-3 fired signals",
        f"★≥66%  ◆≥33%  ·<33%",
    ]
    msg2 = "\n".join(lines2)

    messages = [msg1, msg2]

    # ── MSG 3: Detail cards for high-breadth tickers ──
    top_cards = [r for r in ranked if r['breadth_pct'] >= 33][:5]
    for row in top_cards:
        sigs_str = '  '.join(
            f"{s}({next(x['h3'] for x in SIGNALS if x['id']==s):.0f}%)"
            for s in row['sigs']
        )
        card = [
            f"${row['ticker']}  RS={int(row['rs_pct'])}  ${row['price']:.2f}",
            f"Breadth: {row['breadth_pct']:.0f}% ({row['breadth']}/{N_QUALITY})  AvgHR: {row['avg_h3']:.1f}%",
            f"ATH: {row['pct_from_52w_high']:+.1f}%  MA50: {row['price_vs_ma50_pct']:+.1f}%",
            f"DDR: {row['dd_recovery']:.2f}x  BT: {row['base_tight']:.3f}",
            f"{sigs_str}",
        ]
        messages.append("\n".join(card))

    return messages


def send_telegram(text, token, chat_id):
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    try:
        r = requests.post(url, json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
                          verify=False, timeout=10)
        if r.ok:
            return True
        elif r.status_code == 400:
            r2 = requests.post(url, json={'chat_id': chat_id, 'text': text}, verify=False, timeout=10)
            return r2.ok
        return False
    except Exception as e:
        print(f"[Telegram] Exception: {e}")
        return False


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date',        default=None, help='Target date YYYY-MM-DD')
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    run_date = datetime.strptime(args.date, '%Y-%m-%d') if args.date else datetime.now()
    print(f"PULSE-US Daily Screen — {run_date.date()}")
    print("=" * 60)

    mkt = compute_market_context()
    print(f"Market context: {mkt}")

    screen_result = run_screen(args.date)
    if not screen_result:
        print("No results.")
        results, ticker_hits, fact_df, pulse_scores = {}, {}, pd.DataFrame(), {}
    else:
        results, ticker_hits, fact_df, pulse_scores = screen_result

    total_hits = sum(len(v) for v in results.values())
    print(f"\nTotal signal hits: {total_hits} across {len(ticker_hits)} tickers")
    if pulse_scores:
        print("Per-ticker PULSE scores:")
        for tk, sc in sorted(pulse_scores.items(), key=lambda x: -x[1]['breadth_pct']):
            print(f"  {tk}: breadth={sc['breadth']}/{N_QUALITY} ({sc['breadth_pct']:.0f}%)  avg_h3={sc['avg_h3']:.1f}%")

    # Save output
    Path(OUT_PATH).parent.mkdir(parents=True, exist_ok=True)
    out = {
        'date':            run_date.strftime('%Y-%m-%d'),
        'market_context':  mkt,
        'n_quality':       N_QUALITY,
        'total_hits':      total_hits,
        'unique_tickers':  len(ticker_hits),
        'pulse_scores':    pulse_scores,
        'signals': {sig_id: hits for sig_id, hits in results.items()},
    }
    with open(OUT_PATH, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nSaved → {OUT_PATH}")

    # Telegram
    token   = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')

    messages = format_telegram(results, ticker_hits, pulse_scores, mkt, run_date)

    if args.no_telegram or not token or not chat_id:
        print("\n--- TELEGRAM PREVIEW ---")
        for i, msg in enumerate(messages):
            print(f"\n[MSG {i+1}]")
            print(msg)
        return

    for i, msg in enumerate(messages):
        ok = send_telegram(msg, token, chat_id)
        print(f"[Telegram] msg {i+1}: {'OK' if ok else 'FAIL'}")


if __name__ == '__main__':
    main()
