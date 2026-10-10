"""
v4_price_verifier.py — Daily Price Cross-Check for AlphaAbsolute-US
=====================================================================
Verifies that prices used in v4_paper_trader match TradingView.
Runs AFTER v4_paper_trader --mode daily as a separate QA step.

Outputs:
  - Console: per-ticker source vs TV comparison
  - Telegram ALERT if any ticker differs > ALERT_THRESHOLD (10%)
  - data/paper_trading/price_verify_latest.json

Logic:
  1. Read current positions from state.json
  2. Fetch prices from Tiingo (same as paper trader)
  3. Fetch prices from TradingView US scanner
  4. Compare — flag if diff > 2% (WARN) or > 10% (ALERT → Telegram)
"""
import os, sys, json, time, requests, ssl
from datetime import datetime, timezone, timedelta
from pathlib import Path

os.chdir(Path(__file__).parent.parent.parent)
ssl._create_default_https_context = ssl._create_unverified_context

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

STATE_FILE   = 'data/paper_trading/state.json'
OUT_FILE     = 'data/paper_trading/price_verify_latest.json'
BKK          = timezone(timedelta(hours=7))
WARN_PCT     = 2.0    # log WARN
ALERT_PCT    = 10.0   # send Telegram

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT  = os.environ.get('TELEGRAM_CHAT_ID', '')


def tg_send(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    try:
        r = requests.post(url, json={'chat_id': TELEGRAM_CHAT, 'text': text,
                                      'parse_mode': 'HTML'}, verify=False, timeout=10)
        if r.status_code == 400:
            requests.post(url, json={'chat_id': TELEGRAM_CHAT, 'text': text}, verify=False, timeout=10)
    except Exception:
        pass


def fetch_tiingo(ticker):
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
        return float(data[-1]['adjClose']) if data else None
    except Exception:
        return None


def fetch_tv_us(tickers):
    """TradingView Scanner — US stocks."""
    url = 'https://scanner.tradingview.com/america/scan'
    payload = {
        'symbols': {'tickers': [f'NASDAQ:{t}' for t in tickers] +
                                [f'NYSE:{t}' for t in tickers]},
        'columns': ['close'],
    }
    try:
        r = requests.post(url, json=payload, timeout=15, verify=False,
                          headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code != 200:
            return {}
        result = {}
        for row in r.json().get('data', []):
            sym   = row.get('s', '').split(':')[-1]
            price = (row.get('d') or [None])[0]
            if price and sym not in result:
                result[sym] = float(price)
        return result
    except Exception:
        return {}


def main():
    print('=' * 55)
    print('v4_price_verifier.py — Daily Price Cross-Check')
    print('=' * 55)

    # Load state
    try:
        state = json.loads(Path(STATE_FILE).read_text())
        positions = state.get('positions', {})
    except Exception as e:
        print(f'[ERROR] Cannot read state: {e}'); sys.exit(1)

    if not positions:
        print('[SKIP] No positions'); sys.exit(0)

    tickers = list(positions.keys())
    today   = datetime.now(BKK).strftime('%Y-%m-%d')
    print(f'Date: {today} | Positions: {len(tickers)}')
    print(f'Tickers: {tickers}')

    # Fetch Tiingo prices
    print('\n[1] Tiingo prices...')
    tiingo = {}
    for t in tickers:
        px = fetch_tiingo(t)
        tiingo[t] = px
        time.sleep(0.2)
        status = f'${px:.2f}' if px else 'FAIL'
        print(f'  {t:<7} {status}')

    # Fetch TradingView prices
    print('\n[2] TradingView prices...')
    tv = fetch_tv_us(tickers)
    for t in tickers:
        status = f'${tv[t]:.2f}' if t in tv else 'FAIL'
        print(f'  {t:<7} {status}')

    # Compare
    print('\n[3] Cross-check...')
    print(f'  {"Ticker":<7} {"Tiingo":>9} {"TV":>9} {"Diff%":>7} {"Status"}')
    print('  ' + '-' * 45)

    results = []
    alerts  = []

    for t in tickers:
        px_t = tiingo.get(t)
        px_v = tv.get(t)
        cost = float(positions[t]['cost_basis'])

        if px_t is None and px_v is None:
            status = 'BOTH_FAIL'
            diff   = None
        elif px_t is None:
            status = 'TIINGO_FAIL'
            diff   = None
        elif px_v is None:
            status = 'TV_FAIL'
            diff   = None
        else:
            diff = (px_t / px_v - 1) * 100
            if abs(diff) >= ALERT_PCT:
                status = 'ALERT'
                alerts.append((t, px_t, px_v, diff, cost))
            elif abs(diff) >= WARN_PCT:
                status = 'WARN'
            else:
                status = 'OK'

        diff_str = f'{diff:+.1f}%' if diff is not None else 'N/A'
        t_str    = f'${px_t:.2f}' if px_t else 'N/A'
        v_str    = f'${px_v:.2f}' if px_v else 'N/A'
        print(f'  {t:<7} {t_str:>9} {v_str:>9} {diff_str:>7}  {status}')

        results.append({
            'ticker': t, 'tiingo': px_t, 'tv': px_v,
            'diff_pct': round(diff, 2) if diff is not None else None,
            'cost_basis': cost, 'status': status,
        })

    # Save output
    output = {'date': today, 'results': results,
               'alerts': len(alerts), 'warns': sum(1 for r in results if r['status'] == 'WARN')}
    Path(OUT_FILE).write_text(json.dumps(output, indent=2))
    print(f'\n[SAVED] {OUT_FILE}')

    # Telegram ALERT
    if alerts:
        bkk_now = datetime.now(BKK).strftime('%d %b %Y %H:%M')
        lines = [f'<b>⚠️ [US] Price Discrepancy Alert — {bkk_now}</b>\n']
        for t, px_t, px_v, diff, cost in alerts:
            pnl_t = (px_t / cost - 1) * 100
            pnl_v = (px_v / cost - 1) * 100
            lines.append(
                f'<b>{t}</b>: Tiingo ${px_t:.2f} ({pnl_t:+.1f}%) vs TV ${px_v:.2f} ({pnl_v:+.1f}%)'
                f' — diff <b>{diff:+.1f}%</b>'
            )
        lines.append('\nPortfolio may use wrong prices — check Tiingo data quality.')
        tg_send('\n'.join(lines))
        print(f'\n[ALERT] Telegram sent for {len(alerts)} ticker(s)')
    else:
        print('\n[OK] All prices match — Telegram silent')

    ok = sum(1 for r in results if r['status'] == 'OK')
    print(f'\nSummary: {ok} OK | {output["warns"]} WARN | {len(alerts)} ALERT out of {len(tickers)} tickers')


if __name__ == '__main__':
    main()
