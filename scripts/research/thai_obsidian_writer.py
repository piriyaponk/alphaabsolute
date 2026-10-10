"""thai_obsidian_writer.py -- Thai Paper Portfolio + PULSE-TH -> Obsidian Vault
One-way pipeline: JSON + SQLite -> Python -> Markdown -> Obsidian vault.
Silently skips when OBSIDIAN_VAULT_PATH is not set.
Usage: python -X utf8 scripts/research/thai_obsidian_writer.py
"""
import sys, os, json, sqlite3, time
from pathlib import Path
from datetime import datetime, timezone
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
_ROOT           = Path(__file__).resolve().parents[2]
_PORTFOLIO_JSON = _ROOT / "data" / "research" / "thai_paper_portfolio.json"
_PULSE_JSON     = _ROOT / "data" / "research" / "thai_pulse" / "pulse_th_daily_signals.json"
_SUMMARY_CSV    = _ROOT / "data" / "research" / "thai_entry_screen_summary.csv"
_DB_PATH        = _ROOT / "data" / "research" / "thai_ohlcv.db"
_STARTING_NAV   = 1_000_000.0


def _write_with_retry(path, content, retries=3, delay=1.0):
    for attempt in range(retries):
        try:
            path.write_text(content, encoding='utf-8')
            return
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                raise


def _fmt_pct(val, decimals=2):
    if val is None:
        return 'N/A'
    sign = '+' if val >= 0 else ''
    return f'{sign}{val:.{decimals}f}%'


def _fmt_thb(val):
    if val is None:
        return 'N/A'
    return f'฿' + f'{val:,.0f}'


def load_portfolio():
    """Load thai_paper_portfolio.json with safe .get() defaults on all keys."""
    try:
        with open(_PORTFOLIO_JSON, encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f'[thai_obsidian_writer] WARN: cannot read portfolio JSON: {e}')
        return {}

    nav           = data.get('nav', 0.0)
    daily_log     = data.get('daily_log', [])
    inception_nav = daily_log[0]['nav'] if daily_log else _STARTING_NAV
    nav_return    = ((nav / inception_nav) - 1) * 100 if inception_nav else None

    set_nav    = data.get('set_nav') or None
    set_return = ((set_nav / _STARTING_NAV) - 1) * 100 if set_nav else None

    alpha = None
    if nav_return is not None and set_return is not None:
        alpha = nav_return - set_return

    holdings     = data.get('holdings', [])
    entry_prices = data.get('entry_prices', {})
    cash_pct     = max(0.0, 100.0 - len(holdings) * 10.0)

    return {
        'nav':          nav,
        'nav_return':   nav_return,
        'set_return':   set_return,
        'alpha':        alpha,
        'holdings':     holdings,
        'entry_prices': entry_prices,
        'inception':    data.get('inception', 'N/A'),
        'last_update':  data.get('last_update', 'N/A'),
        'trade_count':  data.get('trade_count', 0),
        'daily_log':    daily_log,
        'cash_pct':     cash_pct,
    }


def load_pulse_signals():
    """Load PULSE-TH signals. Primary source: pulse_th_daily_signals.json (decoded JSON).
    Fallback: CSV. Fallback: SQLite ticker names only (signals_packed BLOB not decoded).
    Returns empty dict when no source available.
    """
    if _PULSE_JSON.exists():
        try:
            with open(_PULSE_JSON, encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f'[thai_obsidian_writer] WARN: pulse JSON read error: {e}')

    if _SUMMARY_CSV.exists():
        try:
            import csv
            today = datetime.now(timezone.utc).date().isoformat()
            rows = []
            with open(_SUMMARY_CSV, encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('date') == today:
                        rows.append(row)
            if rows:
                return {'date': today, 'pulse_scores': {}}
        except Exception as e:
            print(f'[thai_obsidian_writer] WARN: CSV read error: {e}')

    if _DB_PATH.exists():
        try:
            today = datetime.now(timezone.utc).date().isoformat()
            with sqlite3.connect(str(_DB_PATH)) as conn:
                tbl = conn.execute(
                    "SELECT name FROM sqlite_master"
                    " WHERE type='table' AND name='entry_screen_signals'"
                ).fetchone()
                if tbl:
                    rows = conn.execute(
                        "SELECT DISTINCT ticker FROM entry_screen_signals"
                        " WHERE date = ? LIMIT 50",
                        (today,)
                    ).fetchall()
                    if rows:
                        return {
                            'date': today,
                            'pulse_scores': {r[0]: {'n_signals_fired': 1} for r in rows},
                        }
        except Exception as e:
            print(f'[thai_obsidian_writer] WARN: SQLite fallback error: {e}')

    return {}


def build_portfolio_note(portfolio):
    NL = chr(10)
    now_str     = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    nav         = portfolio.get('nav', 0.0)
    holdings    = portfolio.get('holdings', [])
    ep          = portfolio.get('entry_prices', {})
    daily_log   = portfolio.get('daily_log', [])
    last_update = portfolio.get('last_update', 'N/A')
    cash_pct    = portfolio.get('cash_pct', 100.0)
    nr          = portfolio.get('nav_return')
    sr          = portfolio.get('set_return')
    alpha       = portfolio.get('alpha')

    lines = [
        '---',
        f'updated: {now_str}',
        'source: thai_paper_portfolio.json',
        '---',
        '',
        '# 🇹🇭 Thai Paper Portfolio',
        '',
        (f'**NAV**: {_fmt_thb(nav)} | '
         f'**Return**: {_fmt_pct(nr)} vs SET {_fmt_pct(sr)} | '
         f'**Alpha**: {_fmt_pct(alpha)}'),
        (f'**Positions**: {len(holdings)} | **Cash**: {cash_pct:.0f}% | '
         f'**Trades**: {portfolio.get("trade_count", 0)} | '
         f'**Last update**: {last_update}'),
        '',
    ]

    lines.append('## Holdings')
    lines.append('')
    if not holdings:
        lines.append('_No open positions._')
    else:
        lines.append('| Ticker | Entry | P&L |')
        lines.append('|--------|-------|-----|')
        for t in holdings:
            safe_t    = t.replace('|', r'\|')
            entry_px  = ep.get(t)
            entry_str = (_fmt_thb(entry_px) if entry_px is not None else '—')
            lines.append(f'| {safe_t} | {entry_str} | — |')

    lines.append('')
    lines.append('## Recent Activity')
    lines.append('')
    if not daily_log:
        lines.append('_No activity logged yet._')
    else:
        recent = daily_log[-5:]
        lines.append('| Date | NAV | Daily Ret | SET Daily |')
        lines.append('|------|-----|-----------|----------|')
        for entry in reversed(recent):
            d  = entry.get('date', '—')
            n  = entry.get('nav', 0.0)
            dr = entry.get('daily_ret', 0.0)
            sd = entry.get('set_daily', 0.0)
            lines.append(f'| {d} | {_fmt_thb(n)} | {_fmt_pct(dr)} | {_fmt_pct(sd)} |')
        trade_lines = []
        for entry in reversed(recent):
            for b in entry.get('buys', []):
                trade_lines.append(f'- BUY {b} on {entry.get("date", "?")}')
            for s in entry.get('sells', []):
                trade_lines.append(f'- SELL {s} on {entry.get("date", "?")}')
        if trade_lines:
            lines.append('')
            lines.append('**Trades:**')
            lines.extend(trade_lines)

    lines.append('')
    return NL.join(lines)


def build_pulse_note(signals):
    NL = chr(10)
    now_str  = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    sig_date = signals.get('date', 'N/A')
    lines = [
        '---',
        f'updated: {now_str}',
        'source: pulse_th_daily_signals.json',
        '---',
        '',
        f'# PULSE-TH Daily — {sig_date}',
        '',
    ]
    pulse_scores = signals.get('pulse_scores', {})
    fired = {t: v for t, v in pulse_scores.items() if v.get('n_signals_fired', 0) > 0}
    if not fired:
        lines.append('_No signals fired today._')
        lines.append('')
    else:
        ranked = sorted(
            fired.items(),
            key=lambda x: (x[1].get('avg_h3', 0.0), x[1].get('breadth_pct', 0.0)),
            reverse=True,
        )
        lines.append('## Top Signals Today')
        lines.append('')
        lines.append('| Ticker | Signals Fired | Breadth | Avg h3 |')
        lines.append('|--------|--------------|---------|--------|')
        for t, v in ranked[:20]:
            safe_t  = t.replace('|', r'\|')
            n_fired = v.get('n_signals_fired', 0)
            breadth = v.get('breadth_pct', 0.0)
            avg_h3  = v.get('avg_h3', 0.0)
            lines.append(f'| {safe_t} | {n_fired} | {breadth:.0f}% | {avg_h3:.1f}% |')
        lines.append('')
        lines.append(f'_Total tickers with signals: {len(fired)} / {len(pulse_scores)}_')
    n_quality = signals.get('n_quality', 0)
    n_q_sigs  = signals.get('n_quality_signals', 0)
    if n_quality or n_q_sigs:
        lines.append('')
        lines.append('## Signal Library')
        lines.append('')
        lines.append(f'- Quality tickers screened: **{n_quality}**')
        lines.append(f'- Quality signals loaded: **{n_q_sigs}**')
    lines.append('')
    return NL.join(lines)


def main():
    vault_raw = os.environ.get('OBSIDIAN_VAULT_PATH', '').strip()
    if not vault_raw:
        print('[thai_obsidian_writer] OBSIDIAN_VAULT_PATH not set — skipping')
        return
    vault_path = Path(vault_raw).resolve()
    try:
        if vault_path.is_relative_to(_ROOT):
            print('[thai_obsidian_writer] WARN: vault inside repo tree — skipping')
            return
    except (TypeError, ValueError):
        pass
    if not vault_path.exists():
        print(f'[thai_obsidian_writer] WARN: vault not found: {vault_path} — skipping')
        return
    target_dir = vault_path / 'AlphaAbsolute'
    target_dir.mkdir(parents=True, exist_ok=True)
    portfolio = load_portfolio()
    signals   = load_pulse_signals()
    portfolio_path = target_dir / 'TH-Paper-Portfolio.md'
    _write_with_retry(portfolio_path, build_portfolio_note(portfolio))
    print(f'[thai_obsidian_writer] wrote: {portfolio_path}')
    pulse_path = target_dir / 'PULSE-TH-Daily.md'
    _write_with_retry(pulse_path, build_pulse_note(signals))
    print(f'[thai_obsidian_writer] wrote: {pulse_path}')
    print('[thai_obsidian_writer] done')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'[thai_obsidian_writer] FATAL: {e}')
        sys.exit(0)
