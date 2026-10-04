"""
pulse_us_best_final.py
======================
Daily PULSE-US screener using BEST_FINAL signal (Phase 6 research).

BEST_FINAL = CORE75 & (dd_recovery>=1.5 OR price_vs_ma50>=20)
  CORE75:
    rs_pct >= 95              (RS at 95th pct vs universe)
    base_tight <= 0.70        (tight consolidation — ATR ratio)
    pct_from_52w_high >= -3   (within 3% of ATH)
    rs_pct_3m > rs_pct_6m    (RS momentum accelerating)
    vol_trend > 0.75          (rising volume trend — 20d slope)
    vol_contraction <= 0.75   (volume drying in base)
    pocket_pivot > 0.5        (pocket pivot signal)
  OR gate:
    dd_recovery >= 1.5        (recovered 150%+ from recent drawdown)
    price_vs_ma50 >= 20       (price >20% above 50DMA)

Backtest: h3=77.1% (N=48), IS=73.9%, OOS(2024+)=80.0%, avg=+2.03%
Grade S BOTH (2025: 84.2%, 2020: 75%)

Usage:
    python scripts/research/pulse_us/pulse_us_best_final.py [--date YYYY-MM-DD] [--no-telegram]
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

# Use lightweight pulse_us_ohlcv.db if available (GitHub Actions / no local ohlcv.db)
import os as _os
_pulse_db = "data/research/pulse_us/pulse_us_ohlcv.db"
_full_db  = "data/ohlcv.db"
DB_PATH   = _pulse_db if (_os.path.exists(_pulse_db) and not _os.path.exists(_full_db)) else \
            (_full_db  if _os.path.exists(_full_db)  else _pulse_db)
RS_PATH   = "data/rs_universe/latest.json"
OUT_PATH  = "data/research/pulse_us/pulse_us_best_final_signals.json"
MIN_ROWS  = 252  # need 1 year of history


def load_ohlcv(tickers, min_date):
    """Load OHLCV from ohlcv.db for list of tickers."""
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
    df['close'] = df['close'].astype(float)
    df['high']  = df['high'].apply(lambda x: float(x) if x is not None else np.nan)
    df['low']   = df['low'].apply(lambda x: float(x) if x is not None else np.nan)
    df['volume'] = df['volume'].apply(lambda x: float(x) if x is not None else np.nan)
    return df


def compute_factors(grp):
    """Compute all BEST_FINAL factors for a single ticker's OHLCV series."""
    grp = grp.sort_values('date').copy()
    if len(grp) < 63:
        return None

    close  = grp['close'].values
    volume = grp['volume'].values
    high   = grp['high'].values
    low    = grp['low'].values

    n = len(close)
    px = close[-1]

    # --- pct_from_52w_high ---
    high252 = np.nanmax(close[-252:]) if n >= 252 else np.nanmax(close)
    pct_from_52w_high = (px / high252 - 1) * 100

    # --- MA50 ---
    ma50 = np.mean(close[-50:]) if n >= 50 else np.mean(close)
    price_vs_ma50_pct = (px / ma50 - 1) * 100

    # --- vol_trend: 20d slope of volume (normalized) ---
    if n >= 20 and not np.all(np.isnan(volume[-20:])):
        vols = pd.Series(volume[-20:]).ffill().values
        x = np.arange(len(vols))
        vols_norm = vols / (np.mean(vols) + 1e-10)
        try:
            slope = np.polyfit(x, vols_norm, 1)[0]
            vol_trend = float(slope)
        except Exception:
            vol_trend = 0.0
    else:
        vol_trend = 0.0

    # --- vol_contraction: recent 10d avg vol / prior 20d avg vol ---
    if n >= 30 and not np.all(np.isnan(volume[-30:])):
        vols = pd.Series(volume[-30:]).ffill().values
        rec = np.nanmean(vols[-10:])
        prior = np.nanmean(vols[-30:-10])
        vol_contraction = rec / (prior + 1e-10) if prior > 0 else 1.0
    else:
        vol_contraction = 1.0

    # --- base_tight: avg daily range (ATR) / price in last 20d ---
    if n >= 20 and not np.all(np.isnan(high[-20:])) and not np.all(np.isnan(low[-20:])):
        h20 = high[-20:]
        l20 = low[-20:]
        c20 = close[-20:]
        valid = ~(np.isnan(h20) | np.isnan(l20))
        if valid.sum() >= 5:
            ranges = (h20[valid] - l20[valid])
            atr = np.mean(ranges)
            base_tight = atr / (np.mean(c20[valid]) + 1e-10)
        else:
            base_tight = 1.0
    else:
        base_tight = 1.0

    # --- pocket_pivot: today's vol > max down-day vol in prior 10d ---
    pocket_pivot = 0.0
    if n >= 11 and not np.isnan(volume[-1]):
        today_vol = volume[-1]
        prev_closes = close[-11:-1]
        prev_vols   = volume[-11:-1]
        down_vols = [v for c, v, p in zip(prev_closes[1:], prev_vols[1:], prev_closes[:-1])
                     if not np.isnan(v) and not np.isnan(c) and not np.isnan(p) and c < p]
        if down_vols and not np.isnan(today_vol):
            pocket_pivot = 1.0 if today_vol > max(down_vols) else 0.0
        elif not down_vols:
            pocket_pivot = 1.0  # no down days in window = very tight

    # --- dd_recovery: price / 50-day rolling low ---
    if n >= 50:
        low50 = np.nanmin(close[-50:])
        dd_recovery = px / (low50 + 1e-10) if low50 > 0 else 1.0
    else:
        dd_recovery = 1.0

    return {
        'pct_from_52w_high': pct_from_52w_high,
        'price_vs_ma50_pct': price_vs_ma50_pct,
        'vol_trend':         vol_trend,
        'vol_contraction':   vol_contraction,
        'base_tight':        base_tight,
        'pocket_pivot':      pocket_pivot,
        'dd_recovery':       dd_recovery,
        'price':             px,
        'ma50':              ma50,
    }


def run_screen(target_date=None):
    """Run BEST_FINAL screen and return DataFrame of signals."""
    # Load RS universe
    if not Path(RS_PATH).exists():
        print(f"[ERROR] {RS_PATH} not found — run rs_ranker.py first")
        return pd.DataFrame()

    with open(RS_PATH) as f:
        rs_data = json.load(f)

    # Build RS dataframe — universe is a dict keyed by ticker
    universe = rs_data.get('universe', {})
    rs_rows = []
    for ticker, item in universe.items():
        rs_pct_3m = float(item.get('rs_3m_pct') or 0)
        rs_pct_6m = float(item.get('rs_6m_pct') or 0)
        rs_rows.append({
            'ticker':    ticker,
            'rs_pct':    float(item.get('rs_composite_pct') or 0),
            'rs_pct_3m': rs_pct_3m,
            'rs_pct_6m': rs_pct_6m,
        })
    rs_df = pd.DataFrame(rs_rows).dropna()

    # Pre-filter: RS >= 95 (cuts universe from 1800 → ~90 tickers)
    candidates = rs_df[rs_df['rs_pct'] >= 95]['ticker'].tolist()
    if not candidates:
        print("[WARN] No tickers with RS>=95")
        return pd.DataFrame()

    print(f"RS>=95 candidates: {len(candidates)}")

    # Load OHLCV — need 1yr history
    min_date = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
    ohlcv = load_ohlcv(candidates, min_date)

    if target_date:
        td = pd.Timestamp(target_date)
        ohlcv = ohlcv[ohlcv['date'] <= td]

    # Compute factors per ticker
    results = []
    for ticker, grp in ohlcv.groupby('ticker'):
        rs_row = rs_df[rs_df['ticker'] == ticker]
        if rs_row.empty:
            continue

        factors = compute_factors(grp)
        if factors is None:
            continue

        rs_pct    = rs_row['rs_pct'].iloc[0]
        rs_pct_3m = rs_row['rs_pct_3m'].iloc[0]
        rs_pct_6m = rs_row['rs_pct_6m'].iloc[0]

        # CORE75 gates
        core75 = (
            rs_pct    >= 95                          and
            factors['base_tight']        <= 0.70     and
            factors['pct_from_52w_high'] >= -3.0     and
            rs_pct_3m > rs_pct_6m                    and
            factors['vol_trend']         > 0.75      and
            factors['vol_contraction']   <= 0.75     and
            factors['pocket_pivot']      > 0.5
        )

        if not core75:
            continue

        # OR gate
        ddr_ok = factors['dd_recovery']       >= 1.5
        ma_ok  = factors['price_vs_ma50_pct'] >= 20.0
        if not (ddr_ok or ma_ok):
            continue

        results.append({
            'ticker':          ticker,
            'price':           factors['price'],
            'rs_pct':          rs_pct,
            'rs_pct_3m':       rs_pct_3m,
            'rs_pct_6m':       rs_pct_6m,
            'pct_from_52w_high': factors['pct_from_52w_high'],
            'price_vs_ma50_pct': factors['price_vs_ma50_pct'],
            'vol_trend':       factors['vol_trend'],
            'vol_contraction': factors['vol_contraction'],
            'base_tight':      factors['base_tight'],
            'pocket_pivot':    factors['pocket_pivot'],
            'dd_recovery':     factors['dd_recovery'],
            'ddr_gate':        ddr_ok,
            'ma_gate':         ma_ok,
            'or_gate':         'ddr+ma' if (ddr_ok and ma_ok) else ('ddr' if ddr_ok else 'ma20'),
        })

    df = pd.DataFrame(results)
    if df.empty:
        return df

    # Rank by RS percentile then dd_recovery
    df = df.sort_values(['rs_pct', 'dd_recovery'], ascending=[False, False]).reset_index(drop=True)
    return df


def format_telegram(signals_df, run_date, n_candidates):
    """Format Telegram message in PULSE-TH style."""
    date_str = run_date.strftime('%Y-%m-%d')
    n = len(signals_df)

    lines = [
        f"[US] PULSE-US Best Final  |  {date_str}",
        f"h3=77.1% (OOS 2024+=80%)  |  Candidates screened: {n_candidates}",
        ""
    ]

    if n == 0:
        lines.append("No signals today.")
        lines.append("")
        lines.append("BEST_FINAL: CORE75 & (ddr≥1.5 OR ma50+20%)")
        return "\n".join(lines)

    lines.append(f"{'#':<4} {'Ticker':<10} {'RS':>5}  {'ATH%':>6}  {'MA50%':>6}  {'Gate'}")
    lines.append("─" * 50)

    for i, row in signals_df.iterrows():
        gate = row['or_gate'].upper()
        lines.append(
            f"{i+1:<4} {row['ticker']:<10} {int(row['rs_pct']):>5}"
            f"  {row['pct_from_52w_high']:>+6.1f}%"
            f"  {row['price_vs_ma50_pct']:>+6.1f}%"
            f"  {gate}"
        )

    lines.append("")
    lines.append(f"* RS = pct rank vs S&P+Nasdaq universe")
    lines.append(f"* Gate: DDR=recovered 150%+ | MA20=price >20% above MA50")
    return "\n".join(lines)


def send_telegram(text):
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat  = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat:
        print("[Telegram] No credentials — printing instead:")
        print(text)
        return
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    try:
        r = requests.post(url, json={'chat_id': chat, 'text': text, 'parse_mode': 'HTML'},
                          verify=False, timeout=10)
        if r.ok:
            print('[Telegram] Sent OK')
        elif r.status_code == 400:
            r2 = requests.post(url, json={'chat_id': chat, 'text': text}, verify=False, timeout=10)
            print('[Telegram] Sent (plain)' if r2.ok else f'[Telegram] Error: {r2.text}')
        else:
            print(f'[Telegram] Error {r.status_code}: {r.text}')
    except Exception as e:
        print(f'[Telegram] Exception: {e}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=None, help='Date to screen (YYYY-MM-DD)')
    parser.add_argument('--no-telegram', action='store_true', help='Skip Telegram push')
    args = parser.parse_args()

    run_date = datetime.strptime(args.date, '%Y-%m-%d') if args.date else datetime.now()
    print(f"PULSE-US Best Final Screen — {run_date.date()}")
    print("=" * 60)

    # Load RS to get candidate count
    with open(RS_PATH) as f:
        rs_data = json.load(f)
    universe = rs_data.get('universe', {})
    n_rs95 = sum(1 for v in universe.values() if float(v.get('rs_composite_pct') or 0) >= 95)

    signals = run_screen(args.date)
    n = len(signals)

    print(f"RS>=95 pre-filter: {n_rs95} tickers")
    print(f"BEST_FINAL signals: {n}")

    if not signals.empty:
        print("\nSignals:")
        print(signals[['ticker','price','rs_pct','pct_from_52w_high','price_vs_ma50_pct',
                        'dd_recovery','or_gate']].to_string(index=False))

    # Save
    Path(OUT_PATH).parent.mkdir(parents=True, exist_ok=True)
    out = {
        'date': run_date.strftime('%Y-%m-%d'),
        'n_signals': n,
        'signals': signals.to_dict('records') if not signals.empty else []
    }
    with open(OUT_PATH, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nSaved → {OUT_PATH}")

    # Telegram
    if not args.no_telegram:
        msg = format_telegram(signals, run_date, n_rs95)
        send_telegram(msg)


def run():
    """Entry point for pre_market_runner pipeline."""
    main()

if __name__ == '__main__':
    main()
