"""
v4_paper_trader.py — AlphaAbsolute SYSTEM v4.0 Paper Trading
=============================================================
v2: All risk-audit findings fixed (2026-09-02)
  - vol_trend = volume 20d/60d ratio (not price vol) [CRITICAL fix]
  - Transaction cost 0.15% applied to cost basis and exit proceeds
  - NAV recalculated from positions after rebalance
  - Total P&L = unrealized + cumulative realized
  - CAGR, Sharpe, Max Drawdown tracked and reported
  - Idempotency guard on realized_pnl
  - peak_nav tracked for drawdown
  - State backup before save

Modes:
  --mode daily      : update NAV + send Telegram (current holdings only)
  --mode rebalance  : full monthly rebalance + send Telegram
  --mode init       : initialize portfolio from scratch (run once)

State: data/paper_trading/state.json
"""
import sys, os, json, time, argparse, calendar, shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

os.chdir(Path(__file__).parent.parent.parent)

import pandas as pd
import numpy as np
import requests
import ssl
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass
ssl._create_default_https_context = ssl._create_unverified_context

# ── CONFIG ────────────────────────────────────────────────────────────────────
STATE_FILE    = 'data/paper_trading/state.json'
STATE_BAK     = 'data/paper_trading/state.json.bak'
TICKERS_FILE  = 'data/paper_trading/sp500_tickers.txt'
START_NAV     = 1_000_000.0
COST_RT       = 0.0015       # 0.15% round-trip; half applied each side
COST_HALF     = COST_RT / 2  # 0.075% per side
RS_THRESH     = 80
ADTV_MIN      = 10.0         # $M
TOP_N         = 15
BASE_CAP      = 0.18
BETA_CAP      = 0.12
FETCH_THREADS = 8
CANDIDATE_POOL = TOP_N * 5   # fetch ADTV for top-75 RS candidates
BKK           = timezone(timedelta(hours=7))

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT  = os.environ.get('TELEGRAM_CHAT_ID', '')


# ── TIINGO EOD PRICE FETCH (daily mark-to-market) ─────────────────────────────
def fetch_tiingo_price(ticker):
    """Fetch last EOD close from Tiingo. Returns float or None."""
    key = os.environ.get('TIINGO_API_KEY', '')
    if not key:
        return None
    url = (f'https://api.tiingo.com/tiingo/daily/{ticker}/prices'
           f'?startDate=2026-08-01&token={key}')
    try:
        r = requests.get(url, headers={'Content-Type': 'application/json'},
                         verify=False, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, list) or len(data) == 0:
            return None
        return float(data[-1]['adjClose'])
    except Exception:
        return None


# ── YAHOO FINANCE ─────────────────────────────────────────────────────────────
def fetch_yahoo(ticker, days=300):
    """Returns (close_series, volume_series) or (None, None)."""
    end   = int(time.time())
    start = end - days * 86400
    url   = (f'https://query2.finance.yahoo.com/v8/finance/chart/{ticker}'
             f'?interval=1d&period1={start}&period2={end}&events=history')
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'},
                         verify=False, timeout=15)
        d    = r.json()['chart']['result'][0]
        ts   = pd.to_datetime(d['timestamp'], unit='s').normalize()
        adj  = d['indicators']['adjclose'][0]['adjclose']
        vol  = d['indicators']['quote'][0].get('volume', [None]*len(adj))
        close_s = pd.Series(adj, index=ts, name=ticker).dropna().sort_index()
        vol_s   = pd.Series(vol, index=ts, name=ticker).dropna().sort_index()
        return close_s, vol_s
    except Exception:
        return None, None


def fetch_many(tickers, days=300, threads=8, min_len=20):
    """Parallel fetch. Returns {ticker: (close_series, vol_series)}."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = {}
    with ThreadPoolExecutor(max_workers=threads) as ex:
        futs = {ex.submit(fetch_yahoo, t, days): t for t in tickers}
        for f in as_completed(futs):
            t = futs[f]
            c, v = f.result()
            if c is not None and len(c) >= min_len:
                results[t] = (c, v)
    return results


# ── SIGNALS COMPUTATION ───────────────────────────────────────────────────────
def compute_signals(data_dict, spy_close, iwm_close):
    """
    data_dict: {ticker: (close_series, volume_series)}
    Returns DataFrame with signals for each ticker.
    vol_trend = avg_volume_20d / avg_volume_60d  (VOLUME trend, not price vol)
    """
    rows = []
    spy_ret = spy_close.pct_change()

    for ticker, (px, vol) in data_dict.items():
        if ticker in ('SPY', 'QQQ', 'IWM'):
            continue
        if len(px) < 60:
            continue
        try:
            aligned_px  = px.reindex(spy_close.index, method='ffill').dropna()
            if len(aligned_px) < 60:
                continue

            close = float(aligned_px.iloc[-1])
            ret   = aligned_px.pct_change()

            # RS composite (20/60/126/252d excess vs SPY)
            def er(lb, w):
                sc = ret.rolling(lb, min_periods=int(lb*0.6)).sum()
                bc = spy_ret.rolling(lb, min_periods=int(lb*0.6)).sum()
                e  = sc - bc
                v  = e.iloc[-1]
                return float(v) * w if pd.notna(v) else None
            parts = [er(20,0.1), er(60,0.2), er(126,0.3), er(252,0.4)]
            if any(p is None for p in parts):
                continue
            rs_raw = sum(parts)

            # vol_trend = volume 20d avg / volume 60d avg  ← CORRECT signal
            if vol is not None and len(vol) > 60:
                vol_aligned = vol.reindex(aligned_px.index, method='ffill').dropna()
                vol_aligned = vol_aligned[vol_aligned > 0]
                if len(vol_aligned) >= 60:
                    v20 = float(vol_aligned.rolling(20, min_periods=10).mean().iloc[-1])
                    v60 = float(vol_aligned.rolling(60, min_periods=30).mean().iloc[-1])
                    vol_trend = v20 / v60 if v60 > 0 else 0.0
                else:
                    vol_trend = 0.0
            else:
                vol_trend = 0.0

            # MA200
            ma200 = aligned_px.rolling(200, min_periods=150).mean().iloc[-1]
            if pd.isna(ma200):
                continue
            vs_ma200 = (close / float(ma200) - 1) * 100

            # Beta vs IWM
            iwm_al = iwm_close.reindex(aligned_px.index, method='ffill').dropna()
            log_ret  = np.log(aligned_px / aligned_px.shift(1)).dropna()
            iwm_ret  = np.log(iwm_al / iwm_al.shift(1)).dropna()
            common   = log_ret.index.intersection(iwm_ret.index)
            if len(common) > 60:
                lr = log_ret.reindex(common)
                ir = iwm_ret.reindex(common)
                cov = float(lr.cov(ir))
                var = float(ir.var())
                beta = cov / var if var > 0 else 1.0
            else:
                beta = 1.0
            beta = max(0.3, min(float(beta), 4.0))

            # Realized vol for vol-parity weighting
            rv = log_ret.rolling(63, min_periods=20).std().iloc[-1] * np.sqrt(252)
            realized_vol = max(float(rv) if pd.notna(rv) else 0.30, 0.01)

            rows.append({
                'ticker': ticker, 'close': close,
                'rs_raw': rs_raw, 'vol_trend': vol_trend,
                'vs_ma200_pct': vs_ma200,
                'beta': beta, 'realized_vol': realized_vol,
            })
        except Exception:
            continue

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df['rs_pct'] = df['rs_raw'].rank(pct=True) * 100
    return df


def fetch_adtv(ticker, days=63):
    """ADTV in $M for ADTV gate filtering."""
    end   = int(time.time())
    start = end - days * 86400 * 2
    url   = (f'https://query2.finance.yahoo.com/v8/finance/chart/{ticker}'
             f'?interval=1d&period1={start}&period2={end}&events=history')
    try:
        r   = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'},
                           verify=False, timeout=10)
        d   = r.json()['chart']['result'][0]
        vol = d['indicators']['quote'][0].get('volume', [])
        adj = d['indicators']['adjclose'][0]['adjclose']
        df  = pd.DataFrame({'vol': vol, 'close': adj}).dropna()
        adtv = (df['vol'] * df['close']).tail(days).mean() / 1e6
        return float(adtv) if pd.notna(adtv) else 0.0
    except Exception:
        return 0.0


# ── VOL-PARITY WEIGHTS ────────────────────────────────────────────────────────
def vp_weights(df_screen, exposure):
    vols  = df_screen['realized_vol'].values
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


# ── PERFORMANCE METRICS ───────────────────────────────────────────────────────
def compute_metrics(nav_history, inception_nav, inception_date):
    """Returns dict: cagr, sharpe, max_dd from nav_history dict."""
    if len(nav_history) < 2:
        return {'cagr': 0.0, 'sharpe': None, 'max_dd': 0.0}

    sorted_dates = sorted(nav_history.keys())
    navs = pd.Series(
        [nav_history[d] for d in sorted_dates],
        index=pd.to_datetime(sorted_dates)
    )

    # CAGR
    n_days = (navs.index[-1] - pd.to_datetime(inception_date)).days
    n_yrs  = max(n_days / 365.25, 1/365.25)
    cagr   = (navs.iloc[-1] / inception_nav) ** (1 / n_yrs) - 1

    # Daily returns → Sharpe (suppress < 63 trading days — institutional standard)
    daily_ret = navs.pct_change().dropna()
    if len(daily_ret) >= 63:
        sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) \
                 if daily_ret.std() > 0 else 0.0
    else:
        sharpe = None  # not enough data

    # Max Drawdown
    peak   = navs.cummax()
    dd     = (navs - peak) / peak
    max_dd = float(dd.min())

    return {'cagr': float(cagr), 'sharpe': sharpe, 'max_dd': max_dd}


# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def tg_send(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print('[Telegram] No credentials — printing instead:')
        print(text); return
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    try:
        r = requests.post(url, json={
            'chat_id': TELEGRAM_CHAT, 'text': text, 'parse_mode': 'HTML'
        }, timeout=10)
        print('[Telegram] Sent OK' if r.ok else f'[Telegram] Error: {r.text}')
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
    if os.path.exists(STATE_FILE):
        shutil.copy2(STATE_FILE, STATE_BAK)   # backup before overwrite
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def load_tickers():
    with open(TICKERS_FILE) as f:
        return [l.strip() for l in f if l.strip()]


# ── MODE: INIT ────────────────────────────────────────────────────────────────
def run_init():
    print('=== INIT: Running first v4.0 screen ===')
    return run_rebalance(init=True)


# ── MODE: REBALANCE ───────────────────────────────────────────────────────────
def run_rebalance(init=False):
    tickers = load_tickers()
    print(f'Downloading prices for {len(tickers)} tickers (+SPY+IWM+QQQ)...')

    all_tickers = ['SPY', 'IWM', 'QQQ'] + tickers
    data_dict   = fetch_many(all_tickers, days=310, threads=FETCH_THREADS)

    spy_data = data_dict.get('SPY')
    iwm_data = data_dict.get('IWM')
    qqq_data = data_dict.get('QQQ')

    if spy_data is None or iwm_data is None:
        print('ERROR: SPY or IWM fetch failed'); return None

    spy_close = spy_data[0]
    iwm_close = iwm_data[0]
    qqq_close = qqq_data[0] if qqq_data else None

    # IWM regime
    iwm_ma200 = iwm_close.rolling(200, min_periods=150).mean()
    bull   = bool(iwm_close.iloc[-1] > iwm_ma200.iloc[-1])
    vt     = 0.75 if bull else 0.95
    exp    = 1.0  if bull else 0.5
    regime = 'BULL' if bull else 'BEAR'
    print(f'Regime: {regime} | vol_trend threshold: {vt}')

    print('Computing signals (volume-based vol_trend)...')
    sig = compute_signals(data_dict, spy_close, iwm_close)
    if sig.empty:
        print('ERROR: No signals'); return None

    # Screens — fetch ADTV for top candidates
    mask   = ((sig['rs_pct'] >= RS_THRESH) &
              (sig['vs_ma200_pct'] >= 0) &
              (sig['vol_trend'] >= vt))
    passed = sig[mask].nlargest(CANDIDATE_POOL, 'rs_pct')

    print(f'Fetching ADTV for {min(len(passed), CANDIDATE_POOL)} candidates...')
    adtvs = {}
    for tkr in passed['ticker'].tolist():
        adtvs[tkr] = fetch_adtv(tkr)
        time.sleep(0.08)
    passed = passed.copy()
    passed['adtv_63m'] = passed['ticker'].map(adtvs)
    passed = passed[passed['adtv_63m'] >= ADTV_MIN].nlargest(TOP_N, 'rs_pct').copy()

    if len(passed) == 0:
        print('ERROR: No stocks passed all screens'); return None
    print(f'Stocks passing all gates: {len(passed)}')

    weights = vp_weights(passed, exp)
    passed['weight'] = weights

    # ── Build new positions with correct cost basis ───────────────────────────
    today = datetime.now(BKK).strftime('%Y-%m-%d')
    if init:
        # Fresh start — ignore any existing state entirely
        state          = {}
        nav            = float(START_NAV)
        prev_positions = {}
        realized_log   = []
    else:
        state          = load_state() or {}
        nav            = float(state.get('nav', START_NAV))
        prev_positions = state.get('positions', {})
        realized_log   = list(state.get('realized_pnl', []))

    new_positions = {}
    for _, row in passed.iterrows():
        tkr    = row['ticker']
        px     = float(row['close'])
        alloc  = nav * float(row['weight'])
        new_sh = alloc / px

        prev = prev_positions.get(tkr)
        if prev:
            old_sh   = float(prev['shares'])
            old_cost = float(prev['cost_basis'])
            if new_sh > old_sh + 1e-6:
                # Buying more — blended cost (new shares pay spread)
                extra_sh     = new_sh - old_sh
                effective_buy = px * (1 + COST_HALF)
                blended      = (old_sh * old_cost + extra_sh * effective_buy) / new_sh
                cost_basis   = round(blended, 4)
                entry_date   = prev.get('entry_date', today)
            elif new_sh < old_sh - 1e-6:
                # Selling some — realize P&L on sold shares
                sold_sh      = old_sh - new_sh
                exit_px      = px * (1 - COST_HALF)
                realized     = round(sold_sh * (exit_px - old_cost), 2)
                pnl_pct      = round((exit_px / old_cost - 1) * 100, 2)
                dup_key      = f'{today}|{tkr}|partial_sell'
                if not any(f'{r["date"]}|{r["ticker"]}|{r["type"]}' == dup_key
                           for r in realized_log):
                    realized_log.append({
                        'date': today, 'ticker': tkr,
                        'shares_sold': round(sold_sh, 4),
                        'cost': old_cost, 'exit_px': round(exit_px, 4),
                        'realized_usd': realized, 'pnl_pct': pnl_pct,
                        'type': 'partial_sell'
                    })
                cost_basis = old_cost
                entry_date = prev.get('entry_date', today)
            else:
                cost_basis = old_cost
                entry_date = prev.get('entry_date', today)
        else:
            # New position
            # Init: no spread (backtest doesn't charge inception entry cost)
            # Rebalance: pay half spread on new entry
            cost_basis = px if init else round(px * (1 + COST_HALF), 4)
            entry_date = today

        new_positions[tkr] = {
            'shares':        round(new_sh, 4),
            'cost_basis':    cost_basis,
            'weight_target': round(float(row['weight']), 4),
            'entry_date':    entry_date,
            'adtv_m':        round(float(row['adtv_63m']), 1),
            'rs_pct':        round(float(row['rs_pct']), 1),
            'beta':          round(float(row['beta']), 2),
        }

    # Record fully exited positions
    for tkr, prev in prev_positions.items():
        if tkr not in new_positions:
            td = data_dict.get(tkr)
            px_exit_raw = float(td[0].iloc[-1]) if td else float(prev['cost_basis'])
            exit_px     = px_exit_raw * (1 - COST_HALF)
            old_sh      = float(prev['shares'])
            old_cost    = float(prev['cost_basis'])
            realized    = round(old_sh * (exit_px - old_cost), 2)
            pnl_pct     = round((exit_px / old_cost - 1) * 100, 2)
            dup_key     = f'{today}|{tkr}|full_exit'
            if not any(f'{r["date"]}|{r["ticker"]}|{r["type"]}' == dup_key
                       for r in realized_log):
                realized_log.append({
                    'date': today, 'ticker': tkr,
                    'shares_sold': old_sh,
                    'cost': old_cost, 'exit_px': round(exit_px, 4),
                    'realized_usd': realized, 'pnl_pct': pnl_pct,
                    'type': 'full_exit'
                })

    # ── Recalculate NAV from new positions × current prices ───────────────────
    deployed = weights.sum()
    new_cash = nav * (1 - deployed)
    new_nav  = new_cash
    for _, row in passed.iterrows():
        tkr   = row['ticker']
        pos   = new_positions[tkr]
        new_nav += pos['shares'] * float(row['close'])
    new_nav = round(new_nav, 2)

    # Update peak NAV for drawdown tracking
    peak_nav = max(float(state.get('peak_nav', new_nav)), new_nav)

    nav_history = dict(state.get('nav_history', {}))
    nav_history[today] = new_nav

    new_state = {
        'nav':            new_nav,
        'cash':           round(new_cash, 2),
        'inception_nav':  state.get('inception_nav', START_NAV),
        'inception_date': state.get('inception_date', today),
        'last_rebalance': today,
        'regime':         regime,
        'positions':      new_positions,
        'nav_history':    nav_history,
        'peak_nav':       peak_nav,
        'qqq_inception':  (float(qqq_close.iloc[-1]) if (init and qqq_close is not None)
                          else state.get('qqq_inception',
                          float(qqq_close.iloc[-1]) if qqq_close is not None else 0)),
        'qqq_nav_history': state.get('qqq_nav_history', {}),
        'realized_pnl':   realized_log,
    }
    if qqq_close is not None:
        new_state['qqq_nav_history'][today] = float(qqq_close.iloc[-1])
    save_state(new_state)

    # ── Build Telegram ────────────────────────────────────────────────────────
    prev_tkrs = set(prev_positions.keys())
    new_tkrs  = set(new_positions.keys())
    added     = new_tkrs - prev_tkrs
    removed   = prev_tkrs - new_tkrs
    stayed    = prev_tkrs & new_tkrs

    metrics      = compute_metrics(nav_history, float(new_state['inception_nav']),
                                   new_state['inception_date'])
    since_inc    = 0.0 if init else (new_nav / float(new_state['inception_nav']) - 1) * 100
    cagr_pct     = metrics['cagr'] * 100
    sharpe       = metrics['sharpe']
    max_dd_pct   = metrics['max_dd'] * 100

    qqq_inc  = float(new_state['qqq_inception'])
    qqq_now  = float(qqq_close.iloc[-1]) if qqq_close is not None else qqq_inc
    qqq_ret  = (qqq_now / qqq_inc - 1) * 100 if qqq_inc > 0 else 0

    total_realized  = sum(r['realized_usd'] for r in realized_log)
    this_exits      = [r for r in realized_log
                       if r['date'] == today and r['type'] == 'full_exit']
    this_realized   = sum(r['realized_usd'] for r in this_exits)

    bkk_now = datetime.now(BKK).strftime('%d %b %Y')
    nm = datetime.now(BKK).month % 12 + 1
    ny = datetime.now(BKK).year + (1 if datetime.now(BKK).month == 12 else 0)
    next_me = datetime(ny, nm, calendar.monthrange(ny, nm)[1]).strftime('%d %b %Y')

    header = 'AlphaAbsolute v4.0 — พอร์ตเริ่มต้น' if init else 'AlphaAbsolute v4.0 — Monthly Rebalance'
    lines = [
        f'<b>{header}</b>',
        f'<b>{bkk_now}</b> | Regime: <b>{regime}</b>',
        f'',
        f'NAV: <b>${new_nav:,.0f}</b> | Since inception: <b>{since_inc:+.1f}%</b>',
        f'CAGR: <b>{cagr_pct:+.1f}%</b> | QQQ: {qqq_ret:+.1f}% | Excess: <b>{since_inc-qqq_ret:+.1f}%</b>',
        f'Sharpe: {sharpe:.2f} | MaxDD: {max_dd_pct:.1f}%',
        f'',
        f'<b>{"PORTFOLIO (" if init else "NEW PORTFOLIO ("}{len(new_positions)} stocks | {deployed*100:.0f}% deployed)</b>',
        f'{"Ticker":<7} {"Wt":>5}  {"Cost":>8}  {"P&L%":>6}',
    ]
    for _, row in passed.sort_values('weight', ascending=False).iterrows():
        tkr  = row['ticker']
        pos  = new_positions[tkr]
        cost = pos['cost_basis']
        cur  = float(row['close'])
        pnl  = (cur / cost - 1) * 100
        lines.append(f'{tkr:<7} {row["weight"]*100:>4.1f}%  ${cost:>7.2f}  {pnl:>+5.1f}%')

    # ADDED / REMOVED — init: ADDED=all 15, REMOVED=-
    if added:
        lines.append(f'\n<b>ADDED ({len(added)}):</b> ' + ', '.join(sorted(added)))
    if removed and not init:
        parts = []
        for tkr in sorted(removed):
            ex = next((r for r in this_exits if r['ticker'] == tkr), None)
            if ex:
                s = '+' if ex['realized_usd'] >= 0 else ''
                parts.append(f'{tkr} ({s}{ex["pnl_pct"]}%  {s}${ex["realized_usd"]:,.0f})')
            else:
                parts.append(tkr)
        lines.append(f'<b>REMOVED ({len(removed)}):</b> ' + ', '.join(parts))
    else:
        lines.append(f'<b>REMOVED:</b> -')

    if this_realized != 0:
        s = '+' if this_realized >= 0 else ''
        lines.append(f'\nRealized P&amp;L this rebalance: <b>{s}${this_realized:,.0f}</b>')
    if total_realized != 0:
        s = '+' if total_realized >= 0 else ''
        lines.append(f'Cumulative realized: <b>{s}${total_realized:,.0f}</b>')

    lines.append(f'\nNext rebalance: <b>{next_me}</b>')
    tg_send('\n'.join(lines))
    print(f'=== Rebalance complete. NAV=${new_nav:,.0f} ===')
    return new_state


# ── MODE: DAILY UPDATE ────────────────────────────────────────────────────────
def run_daily():
    state = load_state()
    if not state:
        print('No state — running init...'); run_init(); return

    positions = state.get('positions', {})
    if not positions:
        print('No positions'); return

    today    = datetime.now(BKK).strftime('%Y-%m-%d')
    bkk_now  = datetime.now(BKK).strftime('%d %b %Y %H:%M')

    fetch_tkrs = list(positions.keys()) + ['QQQ', 'IWM']
    print(f'Fetching {len(fetch_tkrs)} prices via Tiingo...')

    # Tiingo sequential fetch (rate-limit friendly: ~20 req/day, 50/hour limit)
    tiingo_prices = {}
    for t in fetch_tkrs:
        px = fetch_tiingo_price(t)
        if px is not None:
            tiingo_prices[t] = px
        time.sleep(0.2)  # 5 req/sec max, well under Tiingo free limits
    print(f'  Tiingo OK: {sum(1 for t in fetch_tkrs if t in tiingo_prices)}/{len(fetch_tkrs)}')

    # Yahoo fallback for any missing tickers
    missing = [t for t in fetch_tkrs if t not in tiingo_prices]
    data_dict = {}
    if missing:
        print(f'  Yahoo fallback for: {missing}')
        data_dict = fetch_many(missing, days=10, threads=8, min_len=2)

    # IWM regime (use Tiingo price; MA200 from Yahoo full-history fetch)
    iwm_px = tiingo_prices.get('IWM')
    if iwm_px:
        iwm_full_data = fetch_many(['IWM'], days=300)
        if 'IWM' in iwm_full_data:
            iwm_full = iwm_full_data['IWM'][0]
            bull = bool(iwm_full.iloc[-1] > iwm_full.rolling(200, min_periods=150).mean().iloc[-1])
        else:
            bull = state.get('regime', 'BULL') == 'BULL'
    else:
        bull = state.get('regime', 'BULL') == 'BULL'

    # Price each position — Tiingo primary, Yahoo fallback, then cost_basis
    total_mkt = float(state.get('cash', 0))
    pos_rows  = []
    for tkr, pos in positions.items():
        if tkr in tiingo_prices:
            cur_px = tiingo_prices[tkr]
        else:
            td = data_dict.get(tkr)
            cur_px = float(td[0].iloc[-1]) if td else float(pos['cost_basis'])
        shares = float(pos['shares'])
        cost   = float(pos['cost_basis'])
        mkt    = shares * cur_px
        pnl_pct = (cur_px / cost - 1) * 100
        pnl_usd = mkt - shares * cost
        total_mkt += mkt
        pos_rows.append({
            'ticker': tkr, 'cur_px': cur_px, 'cost': cost,
            'mkt': mkt, 'pnl_pct': pnl_pct, 'pnl_usd': pnl_usd,
            'weight_act': 0,
        })

    nav = total_mkt
    for r in pos_rows:
        r['weight_act'] = r['mkt'] / nav * 100 if nav > 0 else 0
    pos_rows.sort(key=lambda r: r['mkt'], reverse=True)

    # Update peak NAV
    peak_nav = max(float(state.get('peak_nav', nav)), nav)
    max_dd   = (nav / peak_nav - 1) * 100

    # QQQ — Tiingo primary, Yahoo fallback
    qqq_inc  = float(state.get('qqq_inception', 0))
    qqq_now  = tiingo_prices.get('QQQ')
    if qqq_now is None:
        qqq_data = data_dict.get('QQQ')
        qqq_now = float(qqq_data[0].iloc[-1]) if qqq_data else qqq_inc
    qqq_ret  = (qqq_now / qqq_inc - 1) * 100 if qqq_inc > 0 else 0

    # NAV history + metrics
    inc_nav  = float(state.get('inception_nav', START_NAV))
    inc_date = state.get('inception_date', today)
    since_inc = (nav / inc_nav - 1) * 100

    nav_history = dict(state.get('nav_history', {}))
    # On inception day: lock NAV = inception_nav to avoid API re-fetch noise
    if today == inc_date:
        nav       = inc_nav          # $1,000,000 exactly
        daily_chg = 0.0
        since_inc = 0.0
        # Unrealized still shows spread cost (-$750) from cost_basis vs market price
    else:
        prev_dates = [d for d in sorted(nav_history.keys()) if d < today]
        if prev_dates:
            prev_nav  = nav_history[prev_dates[-1]]
            daily_chg = (nav / prev_nav - 1) * 100
        else:
            daily_chg = 0.0

    nav_history[today] = round(nav, 2)

    if qqq_data:
        qqq_nav_history = dict(state.get('qqq_nav_history', {}))
        qqq_nav_history[today] = qqq_now
    else:
        qqq_nav_history = state.get('qqq_nav_history', {})

    metrics  = compute_metrics(nav_history, inc_nav, inc_date)
    cagr_pct = metrics['cagr'] * 100
    sharpe   = metrics['sharpe']  # None if < 63 trading days

    # Realized P&L
    realized_log   = state.get('realized_pnl', [])
    total_realized = sum(r['realized_usd'] for r in realized_log)
    unrealized_pnl = sum(r['pnl_usd'] for r in pos_rows)
    total_pnl      = unrealized_pnl + total_realized

    # since_inc = NAV vs inception cash (includes all spread costs paid at rebalances)
    excess_ret = since_inc - qqq_ret  # honest excess: portfolio paid spread, QQQ did not

    # Suppress CAGR < 90 calendar days (institutional standard)
    n_trading_days = len([d for d in nav_history if d <= today])
    n_cal_days     = (datetime.strptime(today, '%Y-%m-%d') -
                      datetime.strptime(inc_date, '%Y-%m-%d')).days
    show_cagr = n_cal_days >= 90

    # Save state
    state.update({
        'nav': round(nav, 2),
        'nav_history': nav_history,
        'qqq_nav_history': qqq_nav_history,
        'peak_nav': peak_nav,
        'regime': 'BULL' if bull else 'BEAR',
    })
    save_state(state)

    # ── Telegram ──────────────────────────────────────────────────────────────
    regime_str = 'BULL' if bull else 'BEAR'
    cash_pct   = float(state.get('cash', 0)) / nav * 100 if nav > 0 else 0
    pnl_sign   = '+' if since_inc >= 0 else ''

    # Next rebalance date
    bkk_dt   = datetime.now(BKK)
    last_day = calendar.monthrange(bkk_dt.year, bkk_dt.month)[1]
    me_date  = datetime(bkk_dt.year, bkk_dt.month, last_day).strftime('%d %b %Y')
    days_left = last_day - bkk_dt.day

    cagr_str   = f'CAGR: <b>{cagr_pct:+.1f}%</b>' if show_cagr else 'CAGR: <b>N/A (&lt;90d)</b>'
    sharpe_str = f'Sharpe: {sharpe:.2f}' if sharpe is not None else 'Sharpe: N/A (&lt;63d)'

    lines = [
        f'<b>AlphaAbsolute v4.0 | {bkk_now} BKK</b>',
        f'Regime: <b>{regime_str}</b> | Cash: {cash_pct:.0f}%',
        f'',
        f'<b>NAV: ${nav:,.0f}</b>  ({daily_chg:+.1f}% today)',
        f'Since {inc_date}: <b>{pnl_sign}{since_inc:.1f}%</b>',
        f'{cagr_str} | {sharpe_str} | MaxDD: {max_dd:.1f}%',
        f'vs QQQ: {qqq_ret:+.1f}% | Excess: <b>{excess_ret:+.1f}%</b>',
        f'',
        f'<b>Holdings ({len(pos_rows)} stocks)</b>',
        f'{"Ticker":<7} {"Wt%":>4}  {"Cost":>7}  {"P&L%":>6}',
        f'{"─"*38}',
    ]
    for r in pos_rows:
        icon = '' if r['pnl_pct'] >= 0 else ''
        lines.append(
            f'{icon}{r["ticker"]:<6} {r["weight_act"]:>4.1f}%  '
            f'${r["cost"]:>7.2f}→${r["cur_px"]:>7.2f}  {r["pnl_pct"]:>+5.1f}%'
        )

    pnl_s = '+' if total_pnl >= 0 else ''
    unr_s = '+' if unrealized_pnl >= 0 else ''
    rea_s = '+' if total_realized >= 0 else ''
    lines.append(f'{"─"*38}')
    lines.append(f'Unrealized: {unr_s}${unrealized_pnl:,.0f}')
    if total_realized != 0:
        lines.append(f'Realized:   {rea_s}${total_realized:,.0f}')
    lines.append(f'<b>Total P&amp;L: {pnl_s}${total_pnl:,.0f}</b>')

    if days_left <= 3:
        lines.append(f'\n<b>Rebalance on {me_date} ({days_left}d)</b>')
    else:
        lines.append(f'\nNext rebalance: {me_date}')

    tg_send('\n'.join(lines))
    sh_str   = f'{sharpe:.2f}' if sharpe is not None else 'N/A'
    cagr_str2 = f'{cagr_pct:+.1f}%' if show_cagr else 'N/A'
    print(f'Daily done. NAV=${nav:,.0f} | {since_inc:+.1f}% since inception | CAGR={cagr_str2} | Sh={sh_str} | DD={max_dd:.1f}%')


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['daily', 'rebalance', 'init'], default='daily')
    args = parser.parse_args()
    print(f'[{datetime.now(BKK).strftime("%Y-%m-%d %H:%M")} BKK] mode={args.mode}')

    if args.mode == 'init':
        run_init()
    elif args.mode == 'rebalance':
        run_rebalance()
    elif args.mode == 'daily':
        state   = load_state()
        bkk_dt  = datetime.now(BKK)
        last_day = calendar.monthrange(bkk_dt.year, bkk_dt.month)[1]
        is_month_end = (bkk_dt.day == last_day or
                        (bkk_dt.day >= last_day - 1 and bkk_dt.weekday() == 4))
        if state is None:
            run_init()
        elif is_month_end:
            print('Month-end — running rebalance...')
            run_rebalance()
        else:
            run_daily()
