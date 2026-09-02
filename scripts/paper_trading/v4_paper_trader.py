"""
v4_paper_trader.py — AlphaAbsolute SYSTEM v4.0 Paper Trading
=============================================================
Self-contained. No dependency on signals.parquet or v2 engine.
Downloads fresh prices from Yahoo Finance query2 every run.

Modes:
  --mode daily      : update NAV + send Telegram (current holdings only)
  --mode rebalance  : full monthly rebalance + send Telegram
  --mode init       : initialize portfolio from scratch (run once)

State persisted in: data/paper_trading/state.json
Tickers list:       data/paper_trading/sp500_tickers.txt
"""
import sys, os, json, time, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

os.chdir(Path(__file__).parent.parent.parent)

import pandas as pd
import numpy as np
import requests
import ssl
ssl._create_default_https_context = ssl._create_unverified_context

# ── CONFIG ────────────────────────────────────────────────────────────────────
STATE_FILE   = 'data/paper_trading/state.json'
TICKERS_FILE = 'data/paper_trading/sp500_tickers.txt'
START_NAV    = 1_000_000.0   # virtual $1M
COST_RT      = 0.0015        # 0.15% round-trip (US retail)
RS_THRESH    = 80
ADTV_MIN     = 10.0          # $M
TOP_N        = 15
BASE_CAP     = 0.18
BETA_CAP     = 0.12
FETCH_THREADS = 8
BKK = timezone(timedelta(hours=7))

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT  = os.environ.get('TELEGRAM_CHAT_ID', '')


# ── YAHOO FINANCE ─────────────────────────────────────────────────────────────
def fetch_yahoo(ticker, days=300, session=None):
    """Fetch adjusted close prices for `days` days from Yahoo query2."""
    end   = int(time.time())
    start = end - days * 86400
    url   = (f'https://query2.finance.yahoo.com/v8/finance/chart/{ticker}'
             f'?interval=1d&period1={start}&period2={end}&events=history')
    try:
        r = (session or requests).get(
            url, headers={'User-Agent': 'Mozilla/5.0'},
            verify=False, timeout=15)
        d = r.json()['chart']['result'][0]
        ts  = pd.to_datetime(d['timestamp'], unit='s').normalize()
        adj = d['indicators']['adjclose'][0]['adjclose']
        return pd.Series(adj, index=ts, name=ticker).dropna().sort_index()
    except Exception:
        return None


def fetch_many(tickers, days=300, threads=8):
    """Parallel fetch for multiple tickers. Returns dict {ticker: Series}."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = {}
    with ThreadPoolExecutor(max_workers=threads) as ex:
        futs = {ex.submit(fetch_yahoo, t, days): t for t in tickers}
        for f in as_completed(futs):
            t = futs[f]
            r = f.result()
            if r is not None and len(r) > 20:
                results[t] = r
    return results


# ── SIGNALS COMPUTATION ───────────────────────────────────────────────────────
def compute_signals(price_dict, spy_prices, iwm_prices):
    """
    Compute v4.0 signals from raw price series dict.
    Returns DataFrame with columns: ticker, rs_pct, vol_trend, adtv_63m,
                                    price_vs_ma200_pct, beta_252, close
    """
    rows = []
    spy_ret  = spy_prices.pct_change()
    spy_cum  = spy_ret.rolling(252, min_periods=20).sum()

    for ticker, px in price_dict.items():
        if ticker in ('SPY', 'QQQ', 'IWM'): continue
        if len(px) < 60: continue

        try:
            # Align with spy
            aligned = px.reindex(spy_prices.index, method='ffill').dropna()
            if len(aligned) < 60: continue

            close = float(aligned.iloc[-1])

            # RS composite (20/60/126/252 day excess vs SPY)
            ret = aligned.pct_change()
            def er(lb, w):
                stock_cum = ret.rolling(lb, min_periods=int(lb*0.6)).sum()
                spy_c     = spy_ret.rolling(lb, min_periods=int(lb*0.6)).sum()
                exc = stock_cum - spy_c
                return exc.iloc[-1] * w if not pd.isna(exc.iloc[-1]) else None

            parts = [er(20,0.1), er(60,0.2), er(126,0.3), er(252,0.4)]
            if any(p is None for p in parts): continue
            rs_raw = sum(parts)

            # Vol trend
            log_ret = np.log(aligned / aligned.shift(1)).dropna()
            vol20 = log_ret.rolling(20, min_periods=10).std().iloc[-1]
            vol60 = log_ret.rolling(60, min_periods=30).std().iloc[-1]
            vol_trend = vol20 / vol60 if vol60 > 0 else 0

            # ADTV (need volume — approx from px * 1M shares as placeholder)
            # We fetch volume separately later; for now estimate from price range
            # Actually we'll use a separate volume fetch

            # MA200
            ma200 = aligned.rolling(200, min_periods=150).mean().iloc[-1]
            vs_ma200 = (close / ma200 - 1) * 100 if not pd.isna(ma200) else None
            if vs_ma200 is None: continue

            # Beta vs IWM
            iwm_aligned = iwm_prices.reindex(aligned.index, method='ffill').dropna()
            common = log_ret.index.intersection(
                np.log(iwm_aligned / iwm_aligned.shift(1)).dropna().index)
            if len(common) > 60:
                s_r = log_ret.reindex(common)
                i_r = np.log(iwm_aligned / iwm_aligned.shift(1)).dropna().reindex(common)
                cov = s_r.cov(i_r)
                var = i_r.var()
                beta = cov / var if var > 0 else 1.0
            else:
                beta = 1.0

            # Realized vol for weighting
            realized_vol = log_ret.rolling(63, min_periods=20).std().iloc[-1] * np.sqrt(252)

            rows.append({
                'ticker': ticker,
                'close': close,
                'rs_raw': rs_raw,
                'vol_trend': vol_trend,
                'vs_ma200_pct': vs_ma200,
                'beta': max(0.3, min(float(beta), 4.0)),
                'realized_vol': max(float(realized_vol), 0.01) if not pd.isna(realized_vol) else 0.30,
            })
        except Exception:
            continue

    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows)

    # Cross-sectional RS percentile
    df['rs_pct'] = df['rs_raw'].rank(pct=True) * 100
    return df


def fetch_adtv(ticker, days=63):
    """Fetch ADTV in $M for a single ticker."""
    end   = int(time.time())
    start = end - days * 86400 * 2
    url   = (f'https://query2.finance.yahoo.com/v8/finance/chart/{ticker}'
             f'?interval=1d&period1={start}&period2={end}&events=history')
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'},
                         verify=False, timeout=10)
        d = r.json()['chart']['result'][0]
        vol = d['indicators']['quote'][0].get('volume', [])
        adj = d['indicators']['adjclose'][0]['adjclose']
        df  = pd.DataFrame({'vol': vol, 'close': adj}).dropna()
        adtv = (df['vol'] * df['close']).tail(days).mean() / 1e6
        return float(adtv) if not pd.isna(adtv) else 0.0
    except Exception:
        return 0.0


# ── VOL-PARITY WEIGHTS ────────────────────────────────────────────────────────
def vp_weights(df_screen, exposure):
    vols = df_screen['realized_vol'].values
    betas = df_screen['beta'].values
    maxws = np.array([min(BASE_CAP, BETA_CAP / max(b, 0.5)) for b in betas])
    raw_w = 1.0 / vols; raw_w /= raw_w.sum()
    w = raw_w.copy()
    for _ in range(25):
        over = w > maxws
        if not over.any(): break
        exc = (w[over] - maxws[over]).sum(); w[over] = maxws[over]
        recv = ~over
        if not recv.any(): break
        w[recv] += exc * w[recv] / w[recv].sum()
    return w * exposure


# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def tg_send(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print('[Telegram] No credentials — printing instead:')
        print(text)
        return
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    try:
        r = requests.post(url, json={
            'chat_id': TELEGRAM_CHAT,
            'text': text,
            'parse_mode': 'HTML'
        }, timeout=10)
        if r.ok:
            print('[Telegram] Sent OK')
        else:
            print(f'[Telegram] Error: {r.text}')
    except Exception as e:
        print(f'[Telegram] Exception: {e}')


# ── STATE HELPERS ─────────────────────────────────────────────────────────────
def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE) as f:
        return json.load(f)

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


# ── LOAD TICKERS ──────────────────────────────────────────────────────────────
def load_tickers():
    with open(TICKERS_FILE) as f:
        return [l.strip() for l in f if l.strip()]


# ── MODE: INIT ────────────────────────────────────────────────────────────────
def run_init():
    print('=== INIT: Running first v4.0 screen ===')
    state = run_rebalance(init=True)
    return state


# ── MODE: REBALANCE ───────────────────────────────────────────────────────────
def run_rebalance(init=False):
    tickers = load_tickers()
    print(f'Downloading prices for {len(tickers)} tickers (+SPY+IWM+QQQ)...')

    all_tickers = ['SPY', 'IWM', 'QQQ'] + tickers
    price_dict  = fetch_many(all_tickers, days=310, threads=FETCH_THREADS)

    spy = price_dict.get('SPY')
    iwm = price_dict.get('IWM')
    qqq = price_dict.get('QQQ')

    if spy is None or iwm is None:
        print('ERROR: SPY or IWM fetch failed'); return None

    # IWM regime
    iwm_ma200 = iwm.rolling(200, min_periods=150).mean()
    bull = bool(iwm.iloc[-1] > iwm_ma200.iloc[-1])
    vt   = 0.75 if bull else 0.95
    exp  = 1.0  if bull else 0.5
    regime = 'BULL' if bull else 'BEAR'
    print(f'Regime: {regime} | vol_trend threshold: {vt}')

    # Compute signals
    print('Computing signals...')
    sig = compute_signals(price_dict, spy, iwm)
    if sig.empty: print('ERROR: No signals'); return None

    # Apply screens
    mask = ((sig['rs_pct'] >= RS_THRESH) &
            (sig['vs_ma200_pct'] >= 0) &
            (sig['vol_trend'] >= vt))
    passed = sig[mask].nlargest(TOP_N * 3, 'rs_pct')  # fetch ADTV for top candidates

    # ADTV filter (fetch for top candidates only)
    print(f'Fetching ADTV for {min(len(passed), TOP_N*3)} candidates...')
    adtvs = {}
    for tkr in passed['ticker'].tolist():
        adtvs[tkr] = fetch_adtv(tkr)
        time.sleep(0.1)
    passed = passed.copy()
    passed['adtv_63m'] = passed['ticker'].map(adtvs)
    passed = passed[passed['adtv_63m'] >= ADTV_MIN].nlargest(TOP_N, 'rs_pct').copy()

    if len(passed) == 0:
        print('ERROR: No stocks passed all screens'); return None

    print(f'Stocks passing all gates: {len(passed)}')

    # Weights
    weights = vp_weights(passed, exp)
    passed['weight'] = weights

    # Build new positions
    state = load_state() or {}
    today = datetime.now(BKK).strftime('%Y-%m-%d')
    nav   = float(state.get('nav', START_NAV))
    prev_positions = state.get('positions', {})

    new_positions = {}
    for _, row in passed.iterrows():
        tkr = row['ticker']
        alloc = nav * float(row['weight'])
        px    = float(row['close'])
        shares = alloc / px
        new_positions[tkr] = {
            'shares':       round(shares, 4),
            'cost_basis':   round(px, 4),
            'weight_target': round(float(row['weight']), 4),
            'entry_date':   today,
            'adtv_m':       round(float(row['adtv_63m']), 1),
            'rs_pct':       round(float(row['rs_pct']), 1),
            'beta':         round(float(row['beta']), 2),
        }

    # Cash: weight sum < 1.0 means some cash (bear mode)
    deployed = weights.sum()
    cash = nav * (1 - deployed)

    new_state = {
        'nav':            round(nav, 2),
        'cash':           round(cash, 2),
        'inception_nav':  state.get('inception_nav', START_NAV),
        'inception_date': state.get('inception_date', today),
        'last_rebalance': today,
        'regime':         regime,
        'positions':      new_positions,
        'nav_history':    state.get('nav_history', {}),
        'qqq_inception':  state.get('qqq_inception', float(qqq.iloc[-1]) if qqq is not None else 0),
    }
    new_state['nav_history'][today] = round(nav, 2)
    save_state(new_state)

    # Diff vs prev
    prev_tkrs = set(prev_positions.keys())
    new_tkrs  = set(new_positions.keys())
    added     = new_tkrs - prev_tkrs
    removed   = prev_tkrs - new_tkrs
    stayed    = prev_tkrs & new_tkrs

    # ── TELEGRAM: Rebalance Report ────────────────────────────────────────────
    bkk_now = datetime.now(BKK).strftime('%d %b %Y')
    since_inception = (nav / state.get('inception_nav', START_NAV) - 1) * 100

    if qqq is not None:
        qqq_now  = float(qqq.iloc[-1])
        qqq_inc  = float(state.get('qqq_inception', qqq_now))
        qqq_ret  = (qqq_now / qqq_inc - 1) * 100 if qqq_inc > 0 else 0
    else:
        qqq_ret = 0

    lines = [
        f'<b>AlphaAbsolute v4.0 — Monthly Rebalance</b>',
        f'<b>{bkk_now}</b> | Regime: <b>{"BULL" if bull else "BEAR"}</b>',
        f'',
        f'NAV: <b>${nav:,.0f}</b> | Since inception: <b>{since_inception:+.1f}%</b>',
        f'vs QQQ inception: {qqq_ret:+.1f}%',
        f'',
        f'<b>NEW PORTFOLIO ({len(new_positions)} stocks | {deployed*100:.0f}% deployed)</b>',
        f'{"Ticker":<7} {"Wt":>5}  {"RS":>4}  {"Beta":>5}  {"vs MA200":>9}',
    ]
    for _, row in passed.iterrows():
        lines.append(
            f'{row["ticker"]:<7} {row["weight"]*100:>4.1f}%  '
            f'{row["rs_pct"]:>4.0f}  {row["beta"]:>5.2f}  '
            f'{row["vs_ma200_pct"]:>+8.1f}%'
        )

    if added:
        lines.append(f'\n<b>ADDED ({len(added)}):</b> ' + ', '.join(sorted(added)))
    if removed:
        lines.append(f'<b>REMOVED ({len(removed)}):</b> ' + ', '.join(sorted(removed)))
    if stayed and not init:
        lines.append(f'<b>HELD ({len(stayed)}):</b> ' + ', '.join(sorted(stayed)))

    lines.append(f'\nNext rebalance: end of next month')
    tg_send('\n'.join(lines))

    print('=== Rebalance complete ===')
    return new_state


# ── MODE: DAILY UPDATE ────────────────────────────────────────────────────────
def run_daily():
    state = load_state()
    if not state:
        print('No state found — run --mode init first')
        return

    positions = state.get('positions', {})
    if not positions:
        print('No positions — nothing to update')
        return

    today = datetime.now(BKK).strftime('%Y-%m-%d')
    bkk_now = datetime.now(BKK).strftime('%d %b %Y %H:%M')

    # Fetch current prices for holdings + QQQ + IWM
    fetch_tkrs = list(positions.keys()) + ['QQQ', 'IWM']
    print(f'Fetching {len(fetch_tkrs)} prices...')
    price_dict = fetch_many(fetch_tkrs, days=5, threads=10)

    # IWM regime
    iwm = price_dict.get('IWM')
    if iwm is not None:
        iwm_full = fetch_yahoo('IWM', days=300)
        if iwm_full is not None:
            iwm_ma200 = iwm_full.rolling(200, min_periods=150).mean()
            bull = bool(iwm_full.iloc[-1] > iwm_ma200.iloc[-1])
        else:
            bull = state.get('regime', 'BULL') == 'BULL'
    else:
        bull = state.get('regime', 'BULL') == 'BULL'

    # Calculate portfolio values
    total_mkt_value = float(state.get('cash', 0))
    pos_rows = []

    for tkr, pos in positions.items():
        px_series = price_dict.get(tkr)
        if px_series is not None and len(px_series) > 0:
            cur_px = float(px_series.iloc[-1])
        else:
            cur_px = float(pos['cost_basis'])  # fallback to cost basis

        shares    = float(pos['shares'])
        cost      = float(pos['cost_basis'])
        mkt_value = shares * cur_px
        pnl_pct   = (cur_px / cost - 1) * 100
        pnl_usd   = mkt_value - shares * cost
        total_mkt_value += mkt_value

        pos_rows.append({
            'ticker':    tkr,
            'cur_px':    cur_px,
            'cost':      cost,
            'shares':    shares,
            'mkt_value': mkt_value,
            'pnl_pct':   pnl_pct,
            'pnl_usd':   pnl_usd,
            'weight_act': 0,  # fill after total
            'target_w':  float(pos.get('weight_target', 0)),
        })

    nav = total_mkt_value
    for row in pos_rows:
        row['weight_act'] = row['mkt_value'] / nav * 100 if nav > 0 else 0

    pos_rows.sort(key=lambda r: r['mkt_value'], reverse=True)

    # QQQ comparison
    qqq_series = price_dict.get('QQQ')
    qqq_today  = float(qqq_series.iloc[-1]) if qqq_series is not None else 0
    qqq_inc    = float(state.get('qqq_inception', qqq_today))
    qqq_ret    = (qqq_today / qqq_inc - 1) * 100 if qqq_inc > 0 else 0

    inc_nav   = float(state.get('inception_nav', START_NAV))
    inc_date  = state.get('inception_date', today)
    since_inc = (nav / inc_nav - 1) * 100 if inc_nav > 0 else 0

    # Previous NAV for daily change
    nav_hist = state.get('nav_history', {})
    sorted_dates = sorted(nav_hist.keys())
    if len(sorted_dates) >= 1:
        prev_date = sorted_dates[-1]
        prev_nav  = nav_hist[prev_date]
        daily_chg = (nav / prev_nav - 1) * 100 if prev_nav > 0 else 0
    else:
        daily_chg = 0

    # Save updated state
    nav_hist[today] = round(nav, 2)
    state['nav']         = round(nav, 2)
    state['nav_history'] = nav_hist
    state['regime']      = 'BULL' if bull else 'BEAR'
    save_state(state)

    # ── TELEGRAM ──────────────────────────────────────────────────────────────
    regime_icon = 'BULL' if bull else 'BEAR'
    pnl_icon = '+' if since_inc >= 0 else ''
    cash_pct = float(state.get('cash', 0)) / nav * 100 if nav > 0 else 0

    lines = [
        f'<b>AlphaAbsolute v4.0 | {bkk_now} BKK</b>',
        f'Regime: <b>{regime_icon}</b> | Cash: {cash_pct:.0f}%',
        f'',
        f'<b>NAV: ${nav:,.0f}</b>  ({daily_chg:+.1f}% today)',
        f'Since {inc_date}: <b>{pnl_icon}{since_inc:.1f}%</b> | QQQ: {qqq_ret:+.1f}%',
        f'Excess: <b>{since_inc - qqq_ret:+.1f}%</b>',
        f'',
        f'<b>Holdings ({len(pos_rows)} stocks)</b>',
        f'{"Ticker":<7} {"Wt%":>4}  {"P&L%":>7}  {"Value":>10}',
        f'{"─"*38}',
    ]

    for r in pos_rows:
        icon = '' if r['pnl_pct'] >= 0 else ''
        lines.append(
            f'{icon}{r["ticker"]:<6} {r["weight_act"]:>4.1f}%  '
            f'{r["pnl_pct"]:>+6.1f}%  ${r["mkt_value"]:>9,.0f}'
        )

    total_pnl_usd = sum(r['pnl_usd'] for r in pos_rows)
    lines.append(f'{"─"*38}')
    lines.append(f'Total P&L: <b>${total_pnl_usd:>+,.0f}</b>')

    # Month-end warning
    bkk_dt = datetime.now(BKK)
    import calendar
    last_day = calendar.monthrange(bkk_dt.year, bkk_dt.month)[1]
    days_left = last_day - bkk_dt.day
    if days_left <= 3:
        lines.append(f'\n<b>Rebalance in {days_left} day(s)</b>')

    tg_send('\n'.join(lines))
    print(f'Daily update done. NAV=${nav:,.0f} | {since_inc:+.1f}% since inception')


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['daily', 'rebalance', 'init'],
                        default='daily')
    args = parser.parse_args()

    print(f'[{datetime.now(BKK).strftime("%Y-%m-%d %H:%M")} BKK] mode={args.mode}')

    if args.mode == 'init':
        run_init()
    elif args.mode == 'rebalance':
        run_rebalance()
    elif args.mode == 'daily':
        # Auto-detect: if no state or it's month-end → rebalance
        state = load_state()
        bkk_dt = datetime.now(BKK)
        import calendar
        last_day = calendar.monthrange(bkk_dt.year, bkk_dt.month)[1]
        is_month_end = (bkk_dt.day == last_day or
                        (bkk_dt.day >= last_day - 1 and bkk_dt.weekday() == 4))  # Fri near month end
        if state is None:
            print('No state — running init...')
            run_init()
        elif is_month_end:
            print('Month-end detected — running rebalance...')
            run_rebalance()
        else:
            run_daily()
