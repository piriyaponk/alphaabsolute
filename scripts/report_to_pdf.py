#!/usr/bin/env python3
"""
AlphaAbsolute — Report → PDF Generator  (v3 — Playwright/Chromium)
Renders Thai text, tables, and dashboard layouts correctly.
Uses Playwright headless Chromium for pixel-perfect PDF output.
"""

import re
import sys
import json as _json
from pathlib import Path
from datetime import date as date_mod, datetime as datetime_mod
from typing import Optional

# ── Apply SSL proxy patch for yfinance once at module load ─────────────────────
try:
    import urllib3
    urllib3.disable_warnings()
    from curl_cffi import requests as _cffi_req
    import yfinance.data as _yfd
    _yfd_orig_init = _yfd.YfData.__init__
    def _yfd_patched_init(self, session=None):
        _yfd_orig_init(self, session=_cffi_req.Session(impersonate="chrome", verify=False))
    _yfd.YfData.__init__ = _yfd_patched_init
    _YF_SSL_PATCHED = True
except Exception:
    _YF_SSL_PATCHED = False


# ═══════════════════════════════════════════════════════════════════════════════
# DATA EXTRACTION — parse structured data from daily brief markdown
# ═══════════════════════════════════════════════════════════════════════════════

def _clean(text: str) -> str:
    """Strip markdown formatting and emoji shortcodes."""
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'`(.*?)`', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # Replace signal codes
    for old, new in [('[G]', '✓'), ('[R]', '✗'), ('[Y]', '~'), ('[OK]', '✓'), ('[X]', '✗'), ('[!]', '⚠')]:
        text = text.replace(old, new)
    return text.strip()


def _parse_md_table(md_block: str) -> list[list[str]]:
    """Parse a markdown table block into list of rows."""
    rows = []
    for line in md_block.strip().split('\n'):
        line = line.strip()
        if not line.startswith('|'):
            continue
        if re.match(r'^\|[-| :]+\|$', line):
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        rows.append(cells)
    return rows


def _find_section(md: str, header: str, next_headers: Optional[list] = None) -> str:
    """Extract text between two section headers."""
    escaped = re.escape(header)
    if next_headers:
        nexts = '|'.join(re.escape(h) for h in next_headers)
        m = re.search(rf'{escaped}(.*?)(?={nexts}|\Z)', md, re.DOTALL | re.IGNORECASE)
    else:
        m = re.search(rf'{escaped}(.*?)(?=\n##|\Z)', md, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ''


def extract_report_data(md: str) -> dict:
    """Parse all key structured data from the daily brief markdown."""
    d = {}

    # ── Verdict ───────────────────────────────────────────────────────────────
    m = re.search(r'MARKET VERDICT[:\s]*([A-Z][A-Z\s]+?)(?:\n|$)', md)
    d['verdict'] = m.group(1).strip() if m else 'UPTREND'

    m = re.search(r'>\s*\[.\]\s*(.*?)(?:\n|$)', md)
    d['verdict_detail'] = _clean(m.group(1)) if m else ''

    # Color for verdict
    v = d['verdict'].upper()
    if 'STRONG' in v and 'UP' in v:
        d['verdict_color'] = '#15803d'
        d['verdict_bg'] = '#dcfce7'
    elif 'UP' in v:
        d['verdict_color'] = '#16a34a'
        d['verdict_bg'] = '#f0fdf4'
    elif 'CAUTION' in v or 'WATCH' in v:
        d['verdict_color'] = '#b45309'
        d['verdict_bg'] = '#fef9c3'
    else:
        d['verdict_color'] = '#dc2626'
        d['verdict_bg'] = '#fee2e2'

    # ── Macro rows (parsed first so idx cards can pull MTD/YTD) ─────────────
    macro_block = _find_section(md, '## MACRO SNAPSHOT')
    d['macro_rows'] = _parse_md_table(macro_block)

    # Build lookup: indicator name → {mtd, ytd} from macro table
    # Macro table columns: Indicator | Value | 1D | WoW | MTD | YTD | Signal
    _macro_perf = {}
    for row in d['macro_rows'][1:]:
        if len(row) >= 6:
            ind = row[0].strip()
            mtd_v = row[4].strip() if len(row) > 4 else '—'
            ytd_v = row[5].strip() if len(row) > 5 else '—'
            _macro_perf[ind] = {'mtd': mtd_v, 'ytd': ytd_v}

    # ── Index stats ───────────────────────────────────────────────────────────
    indexes = []
    # Map display name → macro table key (partial match)
    _idx_macro_keys = {'S&P 500': 'S&P 500', 'Nasdaq': 'Nasdaq', 'SOX Semi': 'SOX Semi'}
    patterns = [
        ('S&P 500', r'\| S&P 500\s*\|[^|]*\|\s*([+-]?\d+\.?\d*)%\s*\|'),
        ('Nasdaq',  r'\| Nasdaq\s*100?\s*\|[^|]*\|\s*([+-]?\d+\.?\d*)%\s*\|'),
        ('SOX Semi',r'\| SOX Semi\s*\|[^|]*\|\s*([+-]?\d+\.?\d*)%\s*\|'),
    ]
    # Also try the verdict table format for price extraction
    price_pats = [
        ('S&P 500', r'S&P 500.*?\$?([\d,]+\.?\d*)\s*\|\s*1D:\s*([+-]?\d+\.?\d*)%'),
        ('Nasdaq',  r'Nasdaq.*?\$?([\d,]+\.?\d*)\s*\|\s*1D:\s*([+-]?\d+\.?\d*)%'),
        ('SOX Semi',r'SOX Semi.*?\$?([\d,]+\.?\d*)\s*\|\s*1D:\s*([+-]?\d+\.?\d*)%'),
    ]
    for name, ppat in price_pats:
        pm = re.search(ppat, md)
        if pm:
            price = pm.group(1)
            d1 = float(pm.group(2))
            # Lookup MTD/YTD from macro table rows — try exact match then partial
            perf = _macro_perf.get(name, {})
            if not perf:
                # Try partial match: "Nasdaq" matches "Nasdaq 100"
                for k, v in _macro_perf.items():
                    if name.lower() in k.lower() or k.lower() in name.lower():
                        perf = v
                        break
            indexes.append({
                'name':  name,
                'price': price,
                '1d':    f"{'+' if d1>=0 else ''}{d1:.1f}%",
                'mtd':   perf.get('mtd', '—'),
                'ytd':   perf.get('ytd', '—'),
                'color': '#16a34a' if d1 >= 0 else '#dc2626',
            })
    d['indexes'] = indexes

    # ── NRGC phase ────────────────────────────────────────────────────────────
    m = re.search(r'NRGC Phase\s*\|?\s*(Phase \d[^|<\n]*)', md)
    d['nrgc_phase'] = m.group(1).strip() if m else 'Phase 4 (Recognition)'

    # ── Narratives ────────────────────────────────────────────────────────────
    narr_block = _find_section(md, '## NARRATIVE', ['## MACRO', '## THEME', '## KEY'])
    narratives = []
    items = re.split(r'\n(?=\d+\))', narr_block)
    for item in items:
        item = item.strip()
        if not item or not re.match(r'\d+\)', item):
            continue
        lines = [l.strip() for l in item.split('\n') if l.strip()]
        if not lines:
            continue
        # Title is first line after number
        title_line = re.sub(r'^\d+\)\s*', '', lines[0])
        title_parts = re.split(r'\s*--\s*|\s*—\s*', title_line, 1)
        title = title_parts[0].strip()
        subtitle = title_parts[1].strip() if len(title_parts) > 1 else ''
        body_lines = lines[1:3]  # up to 2 body lines
        body = ' '.join(body_lines)[:220]
        narratives.append({'title': title, 'subtitle': subtitle, 'body': _clean(body)})
    d['narratives'] = narratives[:4]

    # ── Theme heatmap ─────────────────────────────────────────────────────────
    theme_block = _find_section(md, '## THEME HEATMAP', ['## KEY FACTORS', '## MACRO'])
    d['theme_rows'] = _parse_md_table(theme_block)

    # ── Key factors ───────────────────────────────────────────────────────────
    factor_block = _find_section(md, '## KEY FACTORS', ['## KEY RISKS', '## WATCHLIST', '\n---'])
    factors = []
    items = re.split(r'\n(?=\d+\))', factor_block)
    for item in items:
        item = item.strip()
        if not item or not re.match(r'\d+\)', item):
            continue
        lines = [l.strip() for l in item.split('\n') if l.strip()]
        title_line = re.sub(r'^\d+\)\s*', '', lines[0]) if lines else ''
        body = ' '.join(lines[1:])[:200] if len(lines) > 1 else ''
        factors.append({'title': _clean(title_line), 'body': _clean(body)})
    d['factors'] = factors[:5]

    # ── Key risks ─────────────────────────────────────────────────────────────
    risk_block = _find_section(md, '## KEY RISKS', ['\n---', '## WATCHLIST'])
    d['risk_rows'] = _parse_md_table(risk_block)

    # ── Alpha picks ───────────────────────────────────────────────────────────
    alpha_picks = []
    # Match alpha pick blocks: #### [N] TICKER -- Name (Theme)
    pick_pattern = re.compile(
        r'#### \[\d+\] (\w+) -- (.*?)\n((?:\|.*\n)*)',
        re.MULTILINE
    )
    for m in pick_pattern.finditer(md):
        ticker = m.group(1)
        name_theme = _clean(m.group(2))
        table_text = m.group(3)
        pick = {'ticker': ticker, 'name_theme': name_theme}
        rows = _parse_md_table(table_text)
        for row in rows:
            if len(row) >= 2:
                # Keep parens in key, only replace spaces with underscores
                key = row[0].strip().lower().replace(' ', '_')
                # Join ALL value cells (some rows have extra | like Price, RS vs SPY, Target)
                full_val = ' | '.join(c for c in row[1:] if c.strip())
                pick[key] = _clean(full_val)
        alpha_picks.append(pick)

    # Take top 10 picks for chart page
    d['alpha_picks'] = alpha_picks[:10]

    # ── Screener data — Universe Top 30 + Phase Changers ─────────────────────
    universe_top25 = []
    phase_changers = {'accelerating': [], 'decelerating': []}

    # Try today's screener, then most recent
    output_dir = Path(__file__).parent.parent / 'output'
    screener_path = output_dir / f"screener_{date_mod.today().strftime('%y%m%d')}.md"
    if not screener_path.exists():
        candidates = sorted(output_dir.glob('screener_*.md'))
        if candidates:
            screener_path = candidates[-1]

    if screener_path.exists():
        sm = screener_path.read_text(encoding='utf-8')

        # ── Universe Top 30 ───────────────────────────────────────────────────
        u25_block = (_find_section(sm, '## UNIVERSE TOP 30', ['## PHASE CHANGERS', '## SCREEN', '## GLOBAL'])
                     or _find_section(sm, '## UNIVERSE TOP 25', ['## PHASE CHANGERS', '## SCREEN', '## GLOBAL']))
        u25_rows = _parse_md_table(u25_block)
        for row in u25_rows[1:31]:   # skip header, max 30 rows
            if len(row) >= 9:
                ticker = row[1].replace('**', '').strip()
                if ticker and ticker not in ('#', 'Rank', 'Ticker'):
                    # Detect format by checking if ordinal ranks present (e.g. "96th", "85th")
                    is_ordinal = any('th' in r or 'st' in r or 'nd' in r or 'rd' in r for r in row[5:9])
                    has_two_deltas = len(row) >= 12  # New format with 1M-3M Δ AND 3M-6M Δ

                    if has_two_deltas and is_ordinal:
                        # Newest format (12 cols):
                        # Rank | Ticker | Theme | Price | 1D | RS Pct 1M | RS Pct 3M | RS Pct 6M | 1M-3M Δ | 3M-6M Δ | Stage | Score
                        universe_top25.append({
                            'rank':       row[0].strip(),
                            'ticker':     ticker,
                            'theme':      row[2].strip()[:22],
                            'price':      row[3].strip(),
                            '1d':         row[4].strip(),
                            'rs1m':       row[5].strip(),   # RS Pct 1M (most recent)
                            'rs3m':       row[6].strip(),   # RS Pct 3M
                            'rs6m':       row[7].strip(),   # RS Pct 6M
                            'delta_1m3m': row[8].strip(),   # 1M-3M Δ rank
                            'delta_3m6m': row[9].strip(),   # 3M-6M Δ rank
                            'stage':      row[10].strip() if len(row) > 10 else '',
                            'score':      row[11].replace('**', '').strip() if len(row) > 11 else '',
                        })
                    elif is_ordinal and len(row) >= 11:
                        # Old "new" format (11 cols — 6M first, one delta):
                        # Rank | Ticker | Theme | Price | 1D | RS 6M | RS 3M | RS 1M | Δ rank | Stage | Score
                        universe_top25.append({
                            'rank':       row[0].strip(),
                            'ticker':     ticker,
                            'theme':      row[2].strip()[:22],
                            'price':      row[3].strip(),
                            '1d':         row[4].strip(),
                            'rs1m':       row[7].strip(),   # RS 1M (was col 7 in old format)
                            'rs3m':       row[6].strip(),   # RS 3M
                            'rs6m':       row[5].strip(),   # RS 6M (was col 5 in old format)
                            'delta_1m3m': row[8].strip(),   # single delta
                            'delta_3m6m': '—',
                            'stage':      row[9].strip() if len(row) > 9 else '',
                            'score':      row[10].replace('**', '').strip() if len(row) > 10 else '',
                        })
                    else:
                        # Legacy format (raw %)
                        stage_raw = row[8].strip() if len(row) > 8 else ''
                        score_raw = row[9].replace('**', '').strip() if len(row) > 9 else ''
                        universe_top25.append({
                            'rank':       row[0].strip(),
                            'ticker':     ticker,
                            'theme':      row[2].strip()[:22],
                            'price':      row[3].strip(),
                            '1d':         row[4].strip(),
                            'rs1m':       row[6].strip(),
                            'rs3m':       '—',
                            'rs6m':       row[5].strip(),
                            'delta_1m3m': '—',
                            'delta_3m6m': '—',
                            'stage':      stage_raw,
                            'score':      score_raw,
                        })

        # ── Phase Changers ────────────────────────────────────────────────────
        phase_block = _find_section(sm, '## PHASE CHANGERS', ['## GLOBAL', '## SCREEN'])
        if phase_block:
            accel_match = re.search(r'Accelerating[^\n]*\n((?:\|[^\n]+\n)+)', phase_block)
            fade_match  = re.search(r'Fading[^\n]*\n((?:\|[^\n]+\n)+)', phase_block)

            def _parse_phase_table(block_text):
                items = []
                rows_p = _parse_md_table(block_text)
                for row in rows_p[1:]:
                    if len(row) >= 5:
                        ticker = row[0].replace('**', '').strip()
                        theme  = row[1].strip()
                        delta  = row[4].strip() if len(row) > 4 else ''
                        if ticker and ticker not in ('#', 'Ticker'):
                            items.append({'ticker': ticker, 'theme': theme,
                                          'delta': delta, 'd1': ''})
                return items

            if accel_match:
                phase_changers['accelerating'] = _parse_phase_table(accel_match.group(0))
            if fade_match:
                phase_changers['decelerating'] = _parse_phase_table(fade_match.group(0))

    d['universe_top25'] = universe_top25
    d['phase_changers'] = phase_changers

    # ── Events ────────────────────────────────────────────────────────────────
    event_block = _find_section(md, '## FACTORS TO WATCH', ['\n---', '## ภาษา', '## TELEGRAM'])
    d['event_rows'] = _parse_md_table(event_block)

    # ── Overnight Recap (from TELEGRAM_SUMMARY — first narrative paragraphs) ──
    tg_block = _find_section(md, '## TELEGRAM_SUMMARY', ['\n---', '\n```'])
    overnight_lines = []
    if tg_block:
        # Skip header line (📊 AlphaAbsolute...) and empty lines, collect prose
        in_prose = False
        for line in tg_block.strip().split('\n'):
            stripped = line.strip()
            if not stripped:
                if in_prose and overnight_lines:
                    # Blank line — stop after first paragraph block
                    if len(overnight_lines) >= 3:
                        break
                continue
            # Skip the header line, tone badge, scoreboard/emoji sections
            if stripped.startswith('📊 *AlphaAbsolute') or stripped.startswith('*['):
                continue
            if stripped.startswith('📈') or stripped.startswith('📉') or \
               stripped.startswith('*📊') or stripped.startswith('──') or \
               stripped.startswith('🔑') or stripped.startswith('🏆') or \
               stripped.startswith('_NRGC'):
                break  # stop at scoreboard/factor sections
            # Clean markdown formatting
            clean_line = re.sub(r'\*([^*]+)\*', r'\1', stripped)  # remove bold
            clean_line = re.sub(r'`([^`]+)`', r'\1', clean_line)
            if clean_line:
                in_prose = True
                overnight_lines.append(clean_line)
                if len(overnight_lines) >= 5:
                    break
    d['overnight_recap'] = '  '.join(overnight_lines) if overnight_lines else ''

    # ── Report date ───────────────────────────────────────────────────────────
    m = re.search(r'(\d{1,2}\s+\w+\s+\d{4}|\d{4}-\d{2}-\d{2})', md[:200])
    d['report_date'] = m.group(1) if m else date_mod.today().strftime('%d %B %Y')

    return d


# ═══════════════════════════════════════════════════════════════════════════════
# HTML TEMPLATE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def _signal_class(text: str) -> str:
    """Return CSS class based on signal/verdict text."""
    t = text.upper()
    if any(x in t for x in ['[G]', 'OVERWEIGHT', 'STRONG', 'PASS', 'GREEN', 'ADD', '✓', 'MARKUP', 'SOS']):
        return 'sig-green'
    if any(x in t for x in ['[R]', 'UNDERWEIGHT', 'FAIL', 'RED', 'STAGE 3', 'STAGE 4', '✗']):
        return 'sig-red'
    if any(x in t for x in ['[Y]', 'HOLD', 'WATCH', 'YELLOW', 'MEDIUM', '~', 'CAUTION']):
        return 'sig-yellow'
    return ''


def _pct_class(val: str) -> str:
    """CSS class for positive/negative percentage."""
    try:
        n = float(re.sub(r'[^0-9.\-+]', '', val))
        return 'pos' if n >= 0 else 'neg'
    except Exception:
        return ''


def _render_macro_table(rows: list) -> str:
    if not rows:
        return ''
    html = '<table class="macro-table"><thead><tr>'
    header = rows[0]
    for cell in header:
        html += f'<th>{_clean(cell)}</th>'
    html += '</tr></thead><tbody>'
    for row in rows[1:]:
        html += '<tr>'
        for i, cell in enumerate(row):
            cls = _signal_class(cell) if i in (3,) else _pct_class(cell) if i in (1,) else ''
            html += f'<td class="{cls}">{_clean(cell)}</td>'
        html += '</tr>'
    html += '</tbody></table>'
    return html


def _render_theme_table(rows: list) -> str:
    if not rows:
        return ''
    html = '<table class="theme-table"><thead><tr>'
    header = rows[0]
    for cell in header:
        html += f'<th>{_clean(cell)}</th>'
    html += '</tr></thead><tbody>'
    for row in rows[1:]:
        verdict = row[-1] if row else ''
        row_cls = _signal_class(verdict)
        html += f'<tr class="{row_cls}-row">'
        for i, cell in enumerate(row):
            c = _clean(cell)
            if i == 0:  # number
                html += f'<td class="num-col">{c}</td>'
            elif i == 1:  # theme name
                html += f'<td class="theme-name">{c}</td>'
            elif i in (2, 3, 4):  # RS numbers
                cls = _pct_class(c)
                html += f'<td class="num {cls}">{c}</td>'
            elif i == 5:  # signal
                cls = _signal_class(c)
                html += f'<td class="sig-badge {cls}">{c}</td>'
            else:  # verdict
                cls = _signal_class(c)
                html += f'<td class="verdict-badge {cls}">{c}</td>'
        html += '</tr>'
    html += '</tbody></table>'
    return html


def _render_risk_table(rows: list) -> str:
    if not rows:
        return ''
    html = '<table class="risk-table"><thead><tr>'
    header = rows[0] if rows else []
    for cell in header:
        html += f'<th>{_clean(cell)}</th>'
    html += '</tr></thead><tbody>'
    for row in rows[1:]:
        html += '<tr>'
        for i, cell in enumerate(row):
            c = _clean(cell)
            cls = _signal_class(c) if i == 1 else ''
            html += f'<td class="{cls}">{c}</td>'
        html += '</tr>'
    html += '</tbody></table>'
    return html


def _render_alpha_card(pick: dict) -> str:
    ticker = pick.get('ticker', '')
    name_theme = pick.get('name_theme', '')
    price_raw = pick.get('price', '')
    # rs_rating field: "6M: 85th | 1M: 72nd | Δ rank: +12 (1M−3M momentum)"
    rs_raw = pick.get('rs_rating', '') or pick.get('rs_vs_spy', '')
    stage = pick.get('stage', '')
    nrgc = pick.get('nrgc_phase', '')
    # entry field now = "~$XXX | WHY: explanation text"
    entry = pick.get('entry', '') or pick.get('entry_zone', '')
    # Key is "stop_loss_(est.)" — parens preserved, spaces→underscores
    stop = pick.get('stop_loss_(est.)', '') or pick.get('stop_loss', '')
    target = pick.get('target_(est.)', '') or pick.get('target', '')
    pulse = pick.get('pulse_(basic)', '') or pick.get('pulse_(basic)', '') or pick.get('pulse', '')
    from_high = pick.get('from_52w_high', '')

    # price_raw now = "$746.81 | 1D: +15.5% | 1W: +29.6%" (full row joined)
    price_str = ''
    change_str = ''
    change_num = 0.0
    if price_raw:
        pm = re.search(r'\$?([\d,]+\.?\d*)', price_raw)
        price_str = f'${pm.group(1)}' if pm else price_raw.split('|')[0].strip()
        cm = re.search(r'1D:\s*([+-]?\d+\.?\d*)%', price_raw)
        if cm:
            change_num = float(cm.group(1))
            change_str = f"{'+' if change_num >= 0 else ''}{change_num:.1f}%"
        else:
            change_str = ''

    change_cls = 'neg' if change_num < 0 else 'pos'
    stage_cls = 'sig-green' if 'Stage 2' in stage else 'sig-red'

    # Extract R/R from target text (target = "$858.83 (+15.0%) | R/R: 1:2.1")
    rr_text = ''
    rr_m = re.search(r'R/R:\s*(1:\d+\.?\d*)', target)
    if rr_m:
        rr_text = f'R/R {rr_m.group(1)}'

    # Clean entry/stop/target — extract just the price
    def _price_from(s: str) -> str:
        m2 = re.search(r'~?\$?([\d,]+\.?\d+)', s)
        return f'${m2.group(1)}' if m2 else s.split('|')[0].split('--')[0].strip()

    entry_clean = _price_from(entry)
    stop_clean = _price_from(stop)
    target_clean = _price_from(target)

    # Extract stop % from stop string  e.g. "$694.53 (-7.0%%)"
    stop_pct = ''
    sp = re.search(r'\(([+-]?\d+\.?\d*)%%?\)', stop)
    if sp:
        stop_pct = f' ({sp.group(1)}%)'

    # Extract target % from target string e.g. "$858.83 (+15.0%)"
    target_pct = ''
    tp = re.search(r'\(([+-]?\d+\.?\d*)%%?\)', target)
    if tp:
        target_pct = f' ({tp.group(1)}%)'

    # Parse RS display — new format: "6M: 85th | 1M: 72nd | Δ rank: +12 (1M−3M momentum)"
    rs_display = rs_raw
    # Try ordinal format first: "6M: 85th | 1M: 72nd"
    rs_ord = re.search(r'6M:\s*(\d+\w+).*?1M:\s*(\d+\w+)', rs_raw)
    if rs_ord:
        # Also extract Δ rank if present
        delta_m = re.search(r'Δ\s*rank:\s*([+\-]?\d+)', rs_raw)
        delta_str = f' | Δ {delta_m.group(1)}' if delta_m else ''
        rs_display = f'6M: {rs_ord.group(1)} | 1M: {rs_ord.group(2)}{delta_str}'
    else:
        # Fallback: old % format
        rs_m2 = re.search(r'6M:\s*([+-]?\d+\.?\d*)%.*?1M:\s*([+-]?\d+\.?\d*)%', rs_raw)
        if rs_m2:
            rs_display = f'6M: {rs_m2.group(1)}% | 1M: {rs_m2.group(2)}%'

    # Stage display: "Stage 2 [G] | Wyckoff: ..." → "Stage 2"
    stage_clean = stage.split('[')[0].split('|')[0].strip()

    nrgc_short = _clean(nrgc).split('|')[0].strip()
    # Shorten long NRGC descriptions: "Phase 5-6 (Consensus/Euphoria)" → "Ph5-6 Consensus"
    nrgc_short = re.sub(r'Phase\s+(\d[\d\-]*)\s*\(([^)]+)\)', r'Ph\1 \2', nrgc_short)
    from_high_clean = _clean(from_high).split('|')[0].strip()

    # Extract WHY from entry field: "~$XXX | WHY: explanation text"
    entry_why_text = ''
    why_m = re.search(r'WHY:\s*(.+)', entry)
    if why_m:
        entry_why_text = _clean(why_m.group(1))[:120]  # truncate for card

    # Extract WHERE from target field: "... | WHERE: explanation"
    target_where_text = ''
    where_m = re.search(r'WHERE:\s*(.+)', target)
    if where_m:
        target_where_text = _clean(where_m.group(1))[:120]

    return f'''
<div class="alpha-card">
  <div class="alpha-header">
    <div class="alpha-ticker">{ticker}</div>
    <div class="alpha-name-wrap">
      <span class="alpha-name">{name_theme}</span>
      <span class="alpha-badges">
        <span class="stage-badge {stage_cls}">{stage_clean}</span>
        <span class="pulse-badge">PULSE {_clean(pulse)}</span>
      </span>
    </div>
    <div class="alpha-price-block">
      <span class="alpha-price">{price_str}</span>
      <span class="alpha-change {change_cls}">{change_str}</span>
    </div>
  </div>
  <div class="alpha-chips-row">
    <span class="chip"><span class="chip-label">RS</span> {rs_display}</span>
    <span class="chip"><span class="chip-label">NRGC</span> {nrgc_short}</span>
    <span class="chip"><span class="chip-label">52W High</span> {from_high_clean}</span>
  </div>
  <div class="alpha-trade-row">
    <div class="trade-pill entry-pill">
      <span class="tp-label">ENTRY</span>
      <span class="tp-val">{entry_clean}</span>
    </div>
    <span class="trade-arrow">→</span>
    <div class="trade-pill stop-pill">
      <span class="tp-label">STOP</span>
      <span class="tp-val">{stop_clean}</span><span class="tp-pct">{stop_pct}</span>
    </div>
    <span class="trade-arrow">→</span>
    <div class="trade-pill target-pill">
      <span class="tp-label">TARGET</span>
      <span class="tp-val">{target_clean}</span><span class="tp-pct">{target_pct}</span>
    </div>
    {"<div class='rr-pill'>" + rr_text + "</div>" if rr_text else ""}
  </div>
  {f'<div class="alpha-entry-why"><span style="font-weight:600;color:#F2994A;">WHY ENTRY:</span> {entry_why_text}</div>' if entry_why_text else ''}
  {f'<div class="alpha-entry-why" style="color:#6FCF97;"><span style="font-weight:600;">TARGET FROM:</span> {target_where_text}</div>' if target_where_text else ''}
</div>'''


def _render_alpha_card_compact(pick: dict, rank: int = 0) -> str:
    """Full-width horizontal row alpha card — 10 rows per page, sorted by conviction."""
    ticker    = pick.get('ticker', '')
    name_theme= pick.get('name_theme', '')
    price_raw = pick.get('price', '')
    # RS: actual field keys from markdown table
    rs_raw    = (pick.get('rs_pct_1m', '') or pick.get('rs_rating', '')
                 or pick.get('rs_vs_spy', '') or pick.get('rs_percentile', ''))
    rs_delta_raw = pick.get('1m-3m_rs_δ', '') or pick.get('rs_delta', '') or pick.get('rs_momentum', '')
    stage     = pick.get('stage', '') or pick.get('weinstein_stage', '')
    nrgc      = pick.get('nrgc_phase', '') or pick.get('nrgc', '')
    entry_raw = pick.get('entry', '') or pick.get('entry_zone', '') or pick.get('buy_zone', '')
    stop_raw  = pick.get('stop_loss_(est.)', '') or pick.get('stop_loss', '') or pick.get('stop', '')
    target_raw= pick.get('target_(est.)', '') or pick.get('target', '') or pick.get('price_target', '')
    pulse_raw = pick.get('pulse_(basic)', '') or pick.get('pulse', '') or pick.get('pulse_score', '')

    # Price + 1D change
    pm = re.search(r'\$?([\d,]+\.?\d*)', price_raw)
    price_str = f'${pm.group(1)}' if pm else '—'
    cm = re.search(r'1D:\s*([+-]?\d+\.?\d*)%', price_raw)
    chg_num = float(cm.group(1)) if cm else 0.0
    chg_str = f"{'+' if chg_num >= 0 else ''}{chg_num:.1f}%" if cm else '—'
    chg_cls = 'acc-chg-pos' if chg_num >= 0 else 'acc-chg-neg'

    # Name — allow up to 20 chars for single column layout
    short_name = name_theme[:20] + ('…' if len(name_theme) > 20 else '')

    # RS: parse from format "95th | RS Pct 3M: 81st | RS Pct 6M: 95th"
    # rs_raw is the 1M field; rs_raw also contains 3M/6M inline after " | "
    rs6m = rs3m = rs1m = rs_delta_str = ''
    # 1M = first value before any " | "
    m1m = re.search(r'^(\d+\w+)', rs_raw.strip())
    if m1m: rs1m = m1m.group(1)
    m3m = re.search(r'(?:RS\s+Pct\s+)?3M[:\s]+(\d+\w+)', rs_raw, re.IGNORECASE)
    if m3m: rs3m = m3m.group(1)
    m6m = re.search(r'(?:RS\s+Pct\s+)?6M[:\s]+(\d+\w+)', rs_raw, re.IGNORECASE)
    if m6m: rs6m = m6m.group(1)
    # Delta from separate delta field: "+14 rank positions ..." or "+14"
    md = re.search(r'([+\-]\d+)\s*(?:rank|position)?', rs_delta_raw)
    if md: rs_delta_str = f' Δ{md.group(1)}'
    # Build compact RS display
    if rs1m or rs6m:
        parts = []
        if rs1m: parts.append(f'1M:{rs1m}')
        if rs3m: parts.append(f'3M:{rs3m}')
        if rs6m: parts.append(f'6M:{rs6m}')
        rs_display = 'RS ' + ' '.join(parts) + rs_delta_str
    else:
        rs_display = rs_raw[:22]

    # Stage badge
    s2_ok = 'Stage 2' in stage or '✅' in stage
    s2_badge = (f'<span class="acc-badge acc-s2">S2✓</span>'
                if s2_ok else
                f'<span class="acc-badge acc-fail">S{stage[6] if len(stage) > 6 else "?"}</span>')

    # PULSE
    pulse_n = re.search(r'(\d)', pulse_raw)
    pulse_badge = (f'<span class="acc-badge acc-pulse">P{pulse_n.group(1)}/5</span>'
                   if pulse_n else '')

    # NRGC — short
    nrgc_m = re.search(r'Phase\s*([\d\-]+)', nrgc)
    nrgc_badge = (f'<span class="acc-badge acc-nrgc">Ph{nrgc_m.group(1)}</span>'
                  if nrgc_m else '')

    # Entry / Stop / Target — numeric only
    def _px(s):
        m = re.search(r'~?\$?([\d,]+\.?\d+)', s)
        return f'${m.group(1)}' if m else '—'
    def _pct(s):
        m = re.search(r'\(([+-]?\d+\.?\d*)%', s)
        return f'{m.group(1)}%' if m else ''

    e_str = _px(entry_raw)
    s_str = _px(stop_raw)
    s_pct = _pct(stop_raw)
    t_str = _px(target_raw)
    t_pct = _pct(target_raw)

    # R/R
    rr_m = re.search(r'R/R:\s*(1:\d+\.?\d*)', target_raw)
    rr_badge = (f'<span class="acc-rr">{rr_m.group(1)}</span>' if rr_m else '')

    # WHY — search both entry field and dedicated why field
    why_txt = ''
    why_m = re.search(r'WHY:\s*(.+)', entry_raw)
    if why_m:
        why_txt = _clean(why_m.group(1))
    if not why_txt:
        why_raw = pick.get('why', '') or pick.get('why_entry', '') or pick.get('thesis', '')
        why_txt = _clean(why_raw)
    # Allow up to 160 chars — full width row has much more room
    why_txt = why_txt[:160]

    # Stop with pct label
    stop_display = f'{s_str} <span style="color:#94a3b8;font-size:5.5pt;">({s_pct})</span>' if s_pct else s_str
    tgt_display  = f'{t_str} <span style="color:#94a3b8;font-size:5.5pt;">({t_pct})</span>' if t_pct else t_str

    return f'''<div class="alpha-card-compact">
  <span class="acc-rank">#{rank}</span>
  <div class="acc-id">
    <span class="acc-ticker">{ticker}</span>
    <span class="acc-name">{short_name}</span>
  </div>
  <div class="acc-price-col">
    <span class="acc-price">{price_str}</span>
    <span class="{chg_cls}">{chg_str}</span>
  </div>
  <div class="acc-signal-col">
    <span class="acc-rs">{rs_display}</span>
    <div class="acc-badges">{s2_badge}{pulse_badge}{nrgc_badge}</div>
  </div>
  <div class="acc-trade-col">
    <span class="acc-entry">📍{e_str}</span>
    <span class="acc-arr">→</span>
    <span class="acc-stop">🛑{stop_display}</span>
    <span class="acc-arr">→</span>
    <span class="acc-target">🎯{tgt_display}</span>
    {rr_badge}
  </div>
  <div class="acc-why-col">
    <span class="acc-why">💡 {why_txt}</span>
  </div>
</div>'''


def _render_event_table(rows: list) -> str:
    if not rows:
        return ''
    html = '<table class="event-table"><thead><tr>'
    for cell in (rows[0] if rows else []):
        html += f'<th>{_clean(cell)}</th>'
    html += '</tr></thead><tbody>'
    # Limit to 5 data rows — compact layout on page 3
    for row in rows[1:6]:
        impact = row[4] if len(row) > 4 else ''
        cls = _signal_class(impact)
        html += f'<tr class="{cls}-row">'
        for i, cell in enumerate(row):
            c = _clean(cell)
            html += f'<td class="{"impact-cell" if i==4 else ""} {_signal_class(c) if i==4 else ""}">{c}</td>'
        html += '</tr>'
    html += '</tbody></table>'
    return html


def _render_screener_table(picks: list) -> str:
    if not picks:
        return '<p class="no-data">ไม่มีข้อมูล screener วันนี้</p>'
    html = '''<table class="screener-table">
<thead><tr><th>#</th><th>Ticker</th><th>Theme</th><th>Price</th><th>1D</th><th>RS 6M</th><th>Score</th></tr></thead>
<tbody>'''
    for i, p in enumerate(picks, 1):
        d1_cls = _pct_class(p.get('1d', ''))
        rs_cls = _pct_class(p.get('rs6m', ''))
        html += f'''<tr>
  <td class="num-col">{i}</td>
  <td class="ticker-cell">{p.get("ticker","")}</td>
  <td>{_clean(p.get("theme",""))}</td>
  <td>${p.get("price","").replace("$","")}</td>
  <td class="{d1_cls}">{p.get("1d","")}</td>
  <td class="{rs_cls}">{p.get("rs6m","")}</td>
  <td class="score-cell">{p.get("score","")}</td>
</tr>'''
    html += '</tbody></table>'
    return html


def _render_universe_table(picks: list) -> str:
    """Render Universe Top 30 — compact ranked table.
    Column order: RS Pct 1M | RS Pct 3M | RS Pct 6M | 1M-3M Δ | 3M-6M Δ | Stage | Score
    """
    if not picks:
        return '<p class="no-data">Universe data unavailable — run run_screener.py first</p>'

    html = '''<table class="universe-table">
<thead><tr>
  <th>#</th><th>Ticker</th><th>Theme</th>
  <th>Price</th><th>1D</th>
  <th>RS Pct 1M</th><th>RS Pct 3M</th><th>RS Pct 6M</th>
  <th>1M-3M Δ</th><th>3M-6M Δ</th>
  <th>Stage</th><th>Score</th>
</tr></thead><tbody>'''

    def _rank_cls(v: str) -> str:
        """Green if high rank (≥75th), red if low (<25th), yellow otherwise."""
        try:
            n = int(''.join(c for c in v if c.isdigit()))
            if n >= 75: return 'sig-green'
            if n < 25:  return 'sig-red'
            return 'sig-yellow'
        except Exception:
            return ''

    def _delta_cls(v: str) -> str:
        try:
            n = int(str(v).replace('+',''))
            return 'pos' if n > 0 else ('neg' if n < 0 else '')
        except Exception:
            return ''

    for i, p in enumerate(picks[:30], 1):
        d1_cls  = _pct_class(p.get('1d', ''))
        stage   = p.get('stage', '')
        s_cls   = 'sig-green' if '✅' in stage or 'Stage 2' in stage else 'sig-red'
        score   = p.get('score', '').replace('**', '')
        # Column order: 1M first, then 3M, then 6M
        rs1     = p.get('rs1m',  p.get('rs_rank_1m', ''))
        rs3     = p.get('rs3m',  p.get('rs_rank_3m', ''))
        rs6     = p.get('rs6m',  p.get('rs_rank_6m', ''))
        d_1m3m  = p.get('delta_1m3m', p.get('delta', ''))   # 1M-3M Δ
        d_3m6m  = p.get('delta_3m6m', '')                    # 3M-6M Δ
        rank_disp = {1: '🥇', 2: '🥈', 3: '🥉'}.get(i, str(i))
        html += f'''<tr>
  <td class="rank-col">{rank_disp}</td>
  <td class="ticker-cell">{p.get("ticker","")}</td>
  <td class="theme-col">{_clean(p.get("theme",""))}</td>
  <td>{p.get("price","").replace("$","")}</td>
  <td class="{d1_cls}">{p.get("1d","")}</td>
  <td class="{_rank_cls(rs1)}">{rs1}</td>
  <td class="{_rank_cls(rs3)}">{rs3}</td>
  <td class="{_rank_cls(rs6)}">{rs6}</td>
  <td class="{_delta_cls(d_1m3m)}">{d_1m3m}</td>
  <td class="{_delta_cls(d_3m6m)}">{d_3m6m}</td>
  <td class="{s_cls} stage-col">{"S2✓" if "✅" in stage else "—"}</td>
  <td class="score-col">{score}</td>
</tr>'''
    html += '</tbody></table>'
    return html


def _render_phase_changers(phase_changers: dict) -> str:
    """Render accelerating / fading phase changer chips for page 3."""
    accel = phase_changers.get('accelerating', [])
    fade  = phase_changers.get('decelerating', [])
    if not accel and not fade:
        return ''

    def _chip(item, kind):
        bg  = '#f0fdf4' if kind == 'accel' else '#fff5f5'
        bdr = '#86efac' if kind == 'accel' else '#fca5a5'
        txt = '#15803d' if kind == 'accel' else '#dc2626'
        icon = '🚀' if kind == 'accel' else '📉'
        delta = item.get('delta', '')
        d1    = item.get('d1', '')
        theme = item.get('theme', '')[:18]
        return (
            f'<div class="phase-chip" style="background:{bg};border:1px solid {bdr};border-radius:5px;'
            f'padding:4px 8px;display:inline-flex;flex-direction:column;gap:1px;">'
            f'<span style="font-size:8pt;font-weight:bold;color:{txt};">{icon} {item["ticker"]}</span>'
            f'<span style="font-size:6.5pt;color:#64748b;">{theme}</span>'
            f'<span style="font-size:7pt;color:{txt};">Δ{delta} &nbsp;|&nbsp; {d1}</span>'
            f'</div>'
        )

    html = '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px;">'
    for item in accel[:5]:
        html += _chip(item, 'accel')
    for item in fade[:4]:
        html += _chip(item, 'fade')
    html += '</div>'
    return html


# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO PAGE — AlphaAbsolute Portfolio Management (Page 4)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_portfolio(market_data: dict = None) -> dict:
    """Load portfolio.json and enrich with live prices from market_data."""
    portfolio_path = Path(__file__).parent.parent / 'data' / 'portfolio.json'
    if not portfolio_path.exists():
        return {}
    try:
        port = _json.loads(portfolio_path.read_text(encoding='utf-8'))
    except Exception:
        return {}

    # Enrich holdings with live prices from market_data or yfinance
    holdings = port.get('holdings', [])

    # Collect tickers needing yfinance fetch (not in market_data cache)
    need_fetch = []
    for h in holdings:
        ticker = h.get('ticker', '')
        if ticker == 'EXAMPLE' or not h.get('entry_price'):
            continue
        if not (market_data and ticker in market_data and
                market_data[ticker].get('price')):
            need_fetch.append(ticker)

    # Fetch missing tickers individually from yfinance (respects SSL proxy patch)
    yf_prices = {}
    if need_fetch:
        try:
            import yfinance as yf
            import urllib3
            urllib3.disable_warnings()
            # Try to apply SSL patch (same as daily_report.py)
            try:
                from curl_cffi import requests as _cffi_req
                import yfinance.data as _yfd
                _orig = _yfd.YfData.__init__
                def _patched(self, session=None):
                    _orig(self, session=_cffi_req.Session(impersonate="chrome", verify=False))
                _yfd.YfData.__init__ = _patched
            except Exception:
                pass
            for t in need_fetch:
                try:
                    hist = yf.Ticker(t).history(period='5d')
                    if not hist.empty:
                        yf_prices[t] = round(float(hist['Close'].iloc[-1]), 2)
                except Exception:
                    pass
        except Exception:
            pass

    for h in holdings:
        ticker = h.get('ticker', '')
        if ticker == 'EXAMPLE' or not h.get('entry_price'):
            continue
        # Check market_data cache first, then yfinance fallback
        live = None
        if market_data and ticker in market_data:
            live = market_data[ticker].get('price')
        if not live and ticker in yf_prices:
            live = yf_prices[ticker]
        if live and live > 0:
            h['current_price'] = round(live, 2)
        else:
            h['current_price'] = None

        # Compute live P&L
        ep = h.get('entry_price', 0)
        cp = h.get('current_price')
        if ep and cp:
            h['unrealized_pnl_pct'] = round((cp - ep) / ep * 100, 1)
        else:
            h['unrealized_pnl_pct'] = None

    port['holdings'] = [h for h in holdings if h.get('ticker') != 'EXAMPLE']
    return port


def _action_chip(action: str) -> str:
    """Return styled action chip HTML."""
    cfg = {
        'HOLD':         ('#1e3a2f', '#6FCF97', '◼ HOLD'),
        'ADD':          ('#1a3a1a', '#27AE60', '▲ ADD'),
        'REDUCE':       ('#3a2a10', '#F2994A', '▼ REDUCE'),
        'TAKE_PROFIT':  ('#3a1a3a', '#BB6BD9', '✦ TAKE PROFIT'),
        'SELL':         ('#3a1a1a', '#EB5757', '✗ SELL'),
        'WATCH':        ('#2a2a3a', '#9B9BC7', '◈ WATCH'),
    }
    act = action.upper().replace(' ', '_')
    bg, txt, label = cfg.get(act, ('#2D2D30', '#9A9A9D', action))
    return (f'<span style="background:{bg};color:{txt};padding:2px 8px;'
            f'border-radius:3px;font-size:7pt;font-weight:bold;white-space:nowrap;">{label}</span>')


def _render_portfolio_page(portfolio: dict) -> str:
    """Build HTML for portfolio page 4 — fund manager briefing style."""
    if not portfolio:
        return '<p class="no-data">portfolio.json not found — add positions to data/portfolio.json</p>'

    alloc      = portfolio.get('allocation', {})
    perf       = portfolio.get('performance', {})
    holdings   = portfolio.get('holdings', [])
    closed     = portfolio.get('closed_trades', [])
    watchlist  = portfolio.get('watchlist_on_deck', [])
    regime     = portfolio.get('regime', 'Unknown')
    bench      = portfolio.get('benchmark', 'QQQ')
    bench_ytd  = portfolio.get('benchmark_ytd_pct', 0)
    port_ytd   = portfolio.get('portfolio_ytd_pct', 0)
    alpha_pp   = port_ytd - bench_ytd
    last_upd   = portfolio.get('last_updated', '')

    cash_pct    = alloc.get('cash_pct', 0)
    equity_pct  = alloc.get('stocks_total_pct', 0)
    slots_used  = len(holdings)
    open_slots  = 10 - slots_used

    win_rate    = perf.get('win_rate_pct', 0)
    avg_win     = perf.get('avg_winner_pct', 0)
    avg_loss    = perf.get('avg_loser_pct', 0)
    avg_r       = perf.get('avg_r_multiple', 0)
    max_dd      = perf.get('max_drawdown_pct', 0)
    sharpe      = perf.get('sharpe_estimate', 0)

    alpha_color = '#2D6A4F' if alpha_pp >= 0 else '#AE2012'
    alpha_sign  = '+' if alpha_pp >= 0 else ''
    port_color  = '#2D6A4F' if port_ytd >= 0 else '#AE2012'
    port_sign   = '+' if port_ytd >= 0 else ''

    # ── Summary Banner ──────────────────────────────────────────────────────────
    banner = f'''
<div class="port-banner">
  <div class="port-stat-block">
    <div class="port-stat-label">REGIME</div>
    <div class="port-stat-val" style="color:#F2994A">{regime.upper()}</div>
  </div>
  <div class="port-stat-sep"></div>
  <div class="port-stat-block">
    <div class="port-stat-label">CASH</div>
    <div class="port-stat-val" style="color:#C8A96E">{cash_pct}%</div>
  </div>
  <div class="port-stat-block">
    <div class="port-stat-label">EQUITY</div>
    <div class="port-stat-val" style="color:#6FCF97">{equity_pct}%</div>
  </div>
  <div class="port-stat-sep"></div>
  <div class="port-stat-block">
    <div class="port-stat-label">SLOTS USED</div>
    <div class="port-stat-val">{slots_used} / 10</div>
    <div class="port-stat-sub">{open_slots} open</div>
  </div>
  <div class="port-stat-sep"></div>
  <div class="port-stat-block">
    <div class="port-stat-label">PORTFOLIO YTD</div>
    <div class="port-stat-val" style="color:{port_color}">{port_sign}{port_ytd:.1f}%</div>
    <div class="port-stat-sub">vs {bench}: {bench_ytd:.1f}%</div>
  </div>
  <div class="port-stat-block">
    <div class="port-stat-label">ALPHA vs {bench}</div>
    <div class="port-stat-val" style="color:{alpha_color}">{alpha_sign}{alpha_pp:.1f} pp</div>
  </div>
  <div class="port-stat-sep"></div>
  <div class="port-stat-block">
    <div class="port-stat-label">WIN RATE</div>
    <div class="port-stat-val">{win_rate:.0f}%</div>
    <div class="port-stat-sub">{len(closed)} closed</div>
  </div>
  <div class="port-stat-block">
    <div class="port-stat-label">AVG WIN / LOSS</div>
    <div class="port-stat-val"><span style="color:#2D6A4F">+{avg_win:.1f}%</span> / <span style="color:#AE2012">{avg_loss:.1f}%</span></div>
  </div>
  <div class="port-stat-block">
    <div class="port-stat-label">AVG R</div>
    <div class="port-stat-val">{avg_r:.1f}R</div>
  </div>
</div>'''

    # ── Holdings Table ──────────────────────────────────────────────────────────
    if not holdings:
        holdings_html = '<p class="no-data">ยังไม่มีหุ้นในพอร์ต — เพิ่ม positions ใน data/portfolio.json</p>'
    else:
        rows = ''
        for h in holdings:
            ticker   = h.get('ticker', '')
            theme    = h.get('theme', '')[:16]
            setup    = h.get('setup_type', '')
            weight   = h.get('weight_pct', 0)
            ep       = h.get('entry_price', 0)
            cp       = h.get('current_price')
            pnl      = h.get('unrealized_pnl_pct')
            stop     = h.get('stop_loss_price', 0)
            target   = h.get('target_price', 0)
            action   = h.get('action', 'HOLD')
            act_det  = h.get('action_detail', '')

            cp_str  = f'${cp:.2f}' if cp else '—'
            ep_str  = f'${ep:.2f}' if ep else '—'
            stop_str = f'${stop:.2f}' if stop else '—'
            tgt_str  = f'${target:.2f}' if target else '—'

            # R/R calculation
            if ep and stop and target and ep != stop:
                risk   = abs(ep - stop)
                reward = abs(target - ep)
                rr     = reward / risk if risk > 0 else 0
                rr_str = f'1:{rr:.1f}'
            else:
                rr_str = '—'

            # P&L styling
            pnl_str = f'{pnl:+.1f}%' if pnl is not None else '—'
            pnl_cls = 'pos' if (pnl or 0) >= 0 else 'neg'

            # Distance to stop
            if cp and stop:
                to_stop = (stop - cp) / cp * 100
                stop_risk = f'({to_stop:+.1f}%)'
            else:
                stop_risk = ''

            # Days held
            try:
                entry_d = datetime_mod.strptime(h.get('entry_date',''), '%Y-%m-%d').date()
                days_held = (date_mod.today() - entry_d).days
                days_str = f'{days_held}d'
            except Exception:
                days_str = '—'

            # Setup badge color
            setup_colors = {
                'Leader': ('#1A3A2A', '#6FCF97'),
                'Misprice': ('#1A2A3A', '#56CCF2'),
                'Hypergrowth': ('#3A1A3A', '#BB6BD9'),
                'Bottom Fish': ('#3A2A10', '#F2994A'),
            }
            sc_bg, sc_txt = setup_colors.get(setup, ('#2D2D30', '#9A9A9D'))

            rows += f'''<tr>
  <td class="ticker-cell" style="font-size:9pt;font-weight:bold">{ticker}</td>
  <td style="font-size:6.5pt;color:#6B6B6E">{theme}</td>
  <td><span style="background:{sc_bg};color:{sc_txt};padding:1px 5px;border-radius:2px;font-size:6pt;font-weight:bold">{setup}</span></td>
  <td style="font-size:7pt">{days_str}</td>
  <td style="font-weight:bold">{weight}%</td>
  <td style="font-size:7.5pt">{ep_str}</td>
  <td style="font-size:7.5pt;font-weight:bold">{cp_str}</td>
  <td class="{pnl_cls}" style="font-weight:bold;font-size:8pt">{pnl_str}</td>
  <td style="font-size:7pt;color:#AE2012">{stop_str} <span style="font-size:6pt;color:#9A9A9D">{stop_risk}</span></td>
  <td style="font-size:7pt;color:#2D6A4F">{tgt_str}</td>
  <td style="font-size:7pt;color:#6B6B6E">{rr_str}</td>
  <td>{_action_chip(action)}</td>
</tr>
<tr class="action-detail-row">
  <td colspan="12" style="font-size:6.5pt;color:#6B6B6E;padding:1px 6px 4px 28px;line-height:1.4">{act_det}</td>
</tr>'''

        # Total equity weight
        total_eq_w = sum(h.get('weight_pct', 0) for h in holdings)
        holdings_html = f'''<table class="port-table">
<thead><tr>
  <th>Ticker</th><th>Theme</th><th>Setup</th><th>Age</th><th>Wt%</th>
  <th>Entry</th><th>Now</th><th>P&amp;L</th><th>Stop</th><th>Target</th><th>R/R</th><th>Action</th>
</tr></thead>
<tbody>{rows}</tbody>
<tfoot><tr style="background:#F0EFEC;font-weight:bold">
  <td colspan="4" style="font-size:7pt">Total Equity Deployed</td>
  <td style="font-size:8pt;color:#1C1C1E">{total_eq_w}%</td>
  <td colspan="3"></td>
  <td colspan="4" style="font-size:6.5pt;color:#6B6B6E">Cash: {cash_pct}% &nbsp;·&nbsp; Open slots: {open_slots}/10</td>
</tr></tfoot>
</table>'''

    # ── Performance Stats Row ────────────────────────────────────────────────────
    stats_html = f'''
<div class="port-stats-row">
  <div class="port-mini-stat">
    <div class="pms-label">Win Rate</div>
    <div class="pms-val">{win_rate:.0f}%</div>
    <div class="pms-sub">{len(closed)} closed trades</div>
  </div>
  <div class="port-mini-stat">
    <div class="pms-label">Avg Winner</div>
    <div class="pms-val pos">+{avg_win:.1f}%</div>
  </div>
  <div class="port-mini-stat">
    <div class="pms-label">Avg Loser</div>
    <div class="pms-val neg">{avg_loss:.1f}%</div>
  </div>
  <div class="port-mini-stat">
    <div class="pms-label">Avg R-Multiple</div>
    <div class="pms-val">{avg_r:.1f}R</div>
    <div class="pms-sub">Expectancy positive</div>
  </div>
  <div class="port-mini-stat">
    <div class="pms-label">Max Drawdown</div>
    <div class="pms-val neg">{max_dd:.1f}%</div>
  </div>
  <div class="port-mini-stat">
    <div class="pms-label">Sharpe (est.)</div>
    <div class="pms-val">{sharpe:.1f}</div>
  </div>
  <div class="port-mini-stat" style="border-left:2px solid #C8A96E;padding-left:10px">
    <div class="pms-label">Alpha vs {bench}</div>
    <div class="pms-val" style="color:{alpha_color}">{alpha_sign}{alpha_pp:.1f} pp</div>
    <div class="pms-sub">YTD outperformance</div>
  </div>
</div>'''

    # ── Closed Trades Mini-Log ───────────────────────────────────────────────────
    closed_html = ''
    if closed:
        closed_rows = ''
        for c in closed[-3:]:  # show last 3
            pnl_c = c.get('pnl_pct', 0)
            outcome = c.get('outcome', 'WIN')
            pnl_col = '#2D6A4F' if pnl_c >= 0 else '#AE2012'
            pnl_sign = '+' if pnl_c >= 0 else ''
            icon = '✓' if outcome == 'WIN' else '✗'
            closed_rows += f'''<tr>
  <td style="font-size:6.5pt;font-weight:bold;color:{'#2D6A4F' if outcome=='WIN' else '#AE2012'}">{icon} {c.get("ticker","")}</td>
  <td style="font-size:6.5pt">{c.get("theme","")}</td>
  <td style="font-size:6.5pt">{c.get("setup_type","")}</td>
  <td style="font-size:6.5pt">{c.get("entry_date","")}</td>
  <td style="font-size:6.5pt">{c.get("exit_date","")}</td>
  <td style="font-size:7.5pt;font-weight:bold;color:{pnl_col}">{pnl_sign}{pnl_c:.1f}%</td>
  <td style="font-size:6pt;color:#6B6B6E">{c.get("lesson","")[:80]}</td>
</tr>'''
        closed_html = f'''
<div class="section-title">📋 Closed Trade Log — Last 3 Trades</div>
<table class="port-closed-table">
<thead><tr><th>Ticker</th><th>Theme</th><th>Setup</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Lesson</th></tr></thead>
<tbody>{closed_rows}</tbody>
</table>'''

    # ── Watchlist On Deck ────────────────────────────────────────────────────────
    deck_html = ''
    if watchlist:
        chips = ''
        for w in watchlist:
            chips += f'''<div class="deck-chip">
  <div style="font-size:8pt;font-weight:bold;color:#C8A96E">{w.get("ticker","")}</div>
  <div style="font-size:6pt;color:#9A9A9D">{w.get("theme","")}</div>
  <div style="font-size:6.5pt;color:#D4D4D7;margin-top:2px">{w.get("note","")[:60]}</div>
  <div style="font-size:6pt;color:#F2994A;margin-top:1px">⚡ {w.get("trigger","")}</div>
</div>'''
        deck_html = f'''
<div class="section-title">🔍 Watchlist On Deck — Next Entry Candidates (Slots: {open_slots} open)</div>
<div style="display:flex;gap:8px;flex-wrap:wrap;">{chips}</div>'''

    return f'''
{banner}
<div class="section-title">📊 Open Positions — AlphaAbsolute Model Portfolio (Max 10 Slots)</div>
{holdings_html}
{stats_html}
<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:6px">
  <div>{closed_html}</div>
  <div>{deck_html}</div>
</div>
<div style="margin-top:6px;padding:4px 8px;background:#FBF7EE;border-left:2px solid #C8A96E;font-size:6.5pt;color:#7D5A00">
  ⚠ Portfolio data as of {last_upd}. Live prices from yfinance (15-min delayed). P&L = unrealized mark-to-market.
  Entry signals verified via NRGC+PULSE framework. All positions must pass Wyckoff + Stage 2 gate before entry.
  ไม่ใช่คำแนะนำการลงทุน — ผู้ใช้งานต้องรับผิดชอบการตัดสินใจลงทุนด้วยตนเอง
</div>'''


# ═══════════════════════════════════════════════════════════════════════════════
# CHART OF THE DAY — Candlestick charts for 5 Alpha picks (matplotlib → base64)
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_price_from_field(s: str) -> Optional[float]:
    """Extract first dollar/numeric price from a pick field string."""
    if not s:
        return None
    m = re.search(r'\$?([\d,]+\.?\d*)', str(s).replace(',', ''))
    if m:
        try:
            return float(m.group(1).replace(',', ''))
        except ValueError:
            return None
    return None


def _make_candle_chart_b64(ticker: str,
                            entry: Optional[float],
                            stop:  Optional[float],
                            target: Optional[float],
                            entry_lbl: str = '',
                            stop_lbl:  str = '',
                            target_lbl: str = '') -> str:
    """
    Fetch 3-month OHLCV via yfinance + draw candlestick chart.
    Returns base64-encoded PNG string (empty string on any failure).
    """
    try:
        import yfinance as yf
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import matplotlib.ticker as mticker
        import numpy as np
        import io, base64

        tk = yf.Ticker(ticker)
        df = tk.history(period='3mo')
        if df is None or df.empty or len(df) < 5:
            return ''
        df = df.rename(columns=str.title)

        opens  = df['Open'].values.astype(float)
        highs  = df['High'].values.astype(float)
        lows   = df['Low'].values.astype(float)
        closes = df['Close'].values.astype(float)
        vols   = df['Volume'].values.astype(float)
        n = len(df)

        # ── Luxury gold/gray dark theme ───────────────────────────────────────
        BG      = '#0d1117'
        PANEL   = '#161b22'
        UP_CLR  = '#26a69a'          # teal-green  (TradingView dark style)
        DN_CLR  = '#ef5350'          # coral red
        GRID    = '#21262d'
        TEXT    = '#c9d1d9'
        SPINE   = '#30363d'
        GOLD    = '#F2C94C'          # AlphaAbsolute gold
        GOLD2   = '#C8A96E'          # muted gold
        ENTRY_C = '#60a5fa'          # sky blue
        STOP_C  = '#f87171'          # soft red
        TGT_C   = '#34d399'          # emerald
        MA20_C  = GOLD               # gold MA line
        TLINE_C = GOLD2              # trendline = muted gold

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(5.2, 2.6),
            gridspec_kw={'height_ratios': [3.5, 1], 'hspace': 0.02},
            facecolor=BG
        )
        for ax in (ax1, ax2):
            ax.set_facecolor(PANEL)
            ax.tick_params(colors=TEXT, labelsize=4.0, length=2, width=0.5)
            for spine in ax.spines.values():
                spine.set_color(SPINE)
                spine.set_linewidth(0.6)
            ax.yaxis.set_major_formatter(mticker.FormatStrFormatter('%.0f'))
            ax.grid(True, color=GRID, linewidth=0.35, linestyle='-', alpha=0.7)

        # ── Candlesticks ──────────────────────────────────────────────────────
        for i in range(n):
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            clr = UP_CLR if c >= o else DN_CLR
            body = max(abs(c - o), (h - l) * 0.008)
            ax1.bar(i, body, bottom=min(o, c), color=clr, width=0.6,
                    zorder=3, edgecolor=clr, linewidth=0.2, alpha=0.92)
            ax1.plot([i, i], [l, min(o, c)], color=clr, lw=0.6, zorder=2)
            ax1.plot([i, i], [max(o, c), h], color=clr, lw=0.6, zorder=2)

        # ── 20-day MA (gold) ──────────────────────────────────────────────────
        if n >= 20:
            ma20 = np.convolve(closes, np.ones(20) / 20, mode='valid')
            ax1.plot(range(19, n), ma20, color=MA20_C, lw=1.2,
                     zorder=4, alpha=0.95, label='MA20')

        # ── Trend line detection ──────────────────────────────────────────────
        def _swing_points(prices, kind='low', lb=4):
            pts = []
            for i in range(lb, n - lb):
                if kind == 'low':
                    if all(prices[i] <= prices[i-j] for j in range(1, lb+1)) and \
                       all(prices[i] <= prices[i+j] for j in range(1, lb+1)):
                        pts.append(i)
                else:
                    if all(prices[i] >= prices[i-j] for j in range(1, lb+1)) and \
                       all(prices[i] >= prices[i+j] for j in range(1, lb+1)):
                        pts.append(i)
            return pts

        swing_lows  = _swing_points(lows,  'low',  lb=3)
        swing_highs = _swing_points(highs, 'high', lb=3)

        # Draw uptrend support line through last 2 swing lows (if rising)
        if len(swing_lows) >= 2:
            p1, p2 = swing_lows[-2], swing_lows[-1]
            if lows[p2] >= lows[p1]:               # rising lows = uptrend
                slope = (lows[p2] - lows[p1]) / (p2 - p1)
                x_end = n - 1
                y1 = lows[p1]
                y2 = lows[p1] + slope * (x_end - p1)
                ax1.plot([p1, x_end], [y1, y2],
                         color=TLINE_C, lw=1.0, linestyle='-',
                         alpha=0.75, zorder=5)
                ax1.annotate('Support', xy=(x_end, y2),
                             fontsize=3.8, color=TLINE_C,
                             xytext=(2, 0), textcoords='offset points', va='center')

        # Draw downtrend resistance line through last 2 swing highs (if falling)
        if len(swing_highs) >= 2:
            p1h, p2h = swing_highs[-2], swing_highs[-1]
            if highs[p2h] <= highs[p1h]:           # falling highs = downtrend
                slope = (highs[p2h] - highs[p1h]) / (p2h - p1h)
                x_end = n - 1
                y1h = highs[p1h]
                y2h = highs[p1h] + slope * (x_end - p1h)
                ax1.plot([p1h, x_end], [y1h, y2h],
                         color='#a78bfa', lw=0.9, linestyle='--',
                         alpha=0.70, zorder=5)
                ax1.annotate('Resistance', xy=(x_end, y2h),
                             fontsize=3.8, color='#a78bfa',
                             xytext=(2, 0), textcoords='offset points', va='center')

        # ── Chart pattern detection & annotation ──────────────────────────────
        pattern_label = ''
        if n >= 20:
            seg = n // 3
            r1 = max(highs[:seg])  - min(lows[:seg])
            r2 = max(highs[seg:2*seg]) - min(lows[seg:2*seg])
            r3 = max(highs[2*seg:]) - min(lows[2*seg:])
            price_range = max(highs) - min(lows)

            # VCP: ranges contracting each third + volume declining
            vol_avg_early = np.mean(vols[:seg])
            vol_avg_late  = np.mean(vols[2*seg:])
            is_vcp = (r2 < r1 * 0.80 and r3 < r2 * 0.80
                      and vol_avg_late < vol_avg_early * 0.85
                      and closes[-1] > np.mean(closes))
            if is_vcp:
                pattern_label = 'VCP'
                # Shade the contraction zone
                ax1.axvspan(2*seg, n-1, alpha=0.06, color=GOLD, zorder=1)
                ax1.text(2*seg + 1, max(highs[2*seg:]) * 1.002,
                         '◀ VCP', fontsize=4.0, color=GOLD,
                         va='bottom', fontweight='bold')

            # Cup shape: price dip then recovery to near starting level
            elif (not is_vcp and n >= 30):
                left_h  = np.mean(closes[:5])
                mid_low = min(lows[5:n-5])
                right_h = np.mean(closes[-5:])
                cup_depth = (left_h - mid_low) / left_h if left_h > 0 else 0
                cup_recov = (right_h - mid_low) / (left_h - mid_low) if (left_h - mid_low) > 0 else 0
                if 0.10 < cup_depth < 0.40 and cup_recov > 0.70:
                    pattern_label = 'CUP'
                    cup_bot_i = int(np.argmin(lows[5:n-5])) + 5
                    # Draw cup arc approximation
                    arc_xs = np.linspace(3, n-4, 40)
                    mid_i  = (3 + n - 4) / 2
                    arc_ys = (mid_low + (left_h - mid_low) *
                              ((arc_xs - mid_i) / (mid_i - 3)) ** 2)
                    ax1.plot(arc_xs, arc_ys, color='#a78bfa', lw=0.8,
                             linestyle=':', alpha=0.65, zorder=4)
                    ax1.text(cup_bot_i, mid_low * 0.998,
                             'CUP', fontsize=4.0, color='#a78bfa',
                             ha='center', va='top')

        # ── Entry / Stop / Target lines ───────────────────────────────────────
        right_x = n + 0.2
        ax1.set_xlim(-0.8, n + 9)

        def _hline(y, color, lbl):
            if y is None or not lbl:
                return
            ax1.axhline(y, color=color, linestyle='--', lw=0.9,
                        alpha=0.90, zorder=6)
            ax1.text(right_x, y, f' {lbl}',
                     color=color, fontsize=3.8, va='center', fontweight='bold',
                     bbox=dict(boxstyle='round,pad=0.12', facecolor=BG,
                               edgecolor=color, linewidth=0.5, alpha=0.95))

        _hline(entry,  ENTRY_C, entry_lbl  or (f'E {entry:.0f}'  if entry  else ''))
        _hline(stop,   STOP_C,  stop_lbl   or (f'S {stop:.0f}'   if stop   else ''))
        _hline(target, TGT_C,   target_lbl or (f'T {target:.0f}' if target else ''))

        # ── Volume bars ───────────────────────────────────────────────────────
        for i in range(n):
            clr = UP_CLR if closes[i] >= opens[i] else DN_CLR
            ax2.bar(i, vols[i], color=clr, alpha=0.55, width=0.6)
        ax2.set_xlim(-0.8, n + 9)
        ax2.yaxis.set_major_formatter(mticker.FuncFormatter(
            lambda x, _: f'{x/1e6:.0f}M' if x >= 1e6 else f'{x/1e3:.0f}K'))

        # ── X axis ────────────────────────────────────────────────────────────
        step = max(1, n // 4)
        xt   = list(range(0, n, step))
        ax1.set_xticks(xt); ax1.set_xticklabels([])
        ax2.set_xticks(xt)
        ax2.set_xticklabels(
            [df.index[i].strftime('%b %d') for i in xt],
            fontsize=3.8, color=TEXT)
        ax2.set_ylabel('Vol', color=GOLD2, fontsize=4.0, labelpad=1)

        # ── Title with pattern tag ─────────────────────────────────────────────
        ptag = f'  [{pattern_label}]' if pattern_label else ''
        ax1.set_title(f'{ticker}  3M daily{ptag}',
                      color=GOLD, fontsize=7.0, fontweight='bold', pad=3)

        # ── Legend: MA20 ──────────────────────────────────────────────────────
        ma_patch = mpatches.Patch(color=GOLD, label='MA20')
        ax1.legend(handles=[ma_patch], loc='upper left',
                   fontsize=3.5, framealpha=0.4,
                   facecolor=PANEL, edgecolor=SPINE)

        fig.patch.set_facecolor(BG)
        plt.subplots_adjust(left=0.09, right=0.84, top=0.90, bottom=0.10)

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=150,
                    bbox_inches='tight', facecolor=BG, edgecolor='none')
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode('utf-8')

    except Exception as exc:
        print(f'  [CHART] {ticker} chart failed: {exc}')
        return ''


def _render_chart_page(alpha_picks: list) -> str:
    """
    Chart of the Day — 10 charts, 2 columns × 5 rows, full page.
    Only candlestick + volume. Entry/stop/target lines annotated on chart.
    Light mode. No detail strip below — chart speaks for itself.
    """
    if not alpha_picks:
        return '<p style="color:#6b7280;font-size:8pt;text-align:center;">No alpha picks available today.</p>'

    cards = ''
    for pick in alpha_picks[:10]:
        ticker     = pick.get('ticker', '?')
        price_raw  = pick.get('price', '')
        entry_raw  = pick.get('entry', '')
        stop_raw   = pick.get('stop_loss_(est.)', '')
        target_raw = pick.get('target_(est.)', '')
        stage_raw  = pick.get('stage', '')
        rs_raw     = pick.get('rs_rating', '') or pick.get('rs_vs_spy', '')

        px      = _parse_price_from_field(price_raw)
        entry_v = _parse_price_from_field(entry_raw) or px
        stop_v  = _parse_price_from_field(stop_raw)
        tgt_v   = _parse_price_from_field(target_raw)

        if entry_v and not stop_v:
            stop_v = entry_v * 0.93
        if entry_v and not tgt_v:
            tgt_v  = entry_v * 1.15

        stop_pct = ((stop_v  - entry_v) / entry_v * 100) if (stop_v  and entry_v) else 0
        tgt_pct  = ((tgt_v   - entry_v) / entry_v * 100) if (tgt_v   and entry_v) else 0
        rr_val   = abs(tgt_pct / stop_pct) if stop_pct != 0 else 0

        # Labels shown directly on chart
        entry_lbl = f'Entry {entry_v:,.0f}' if entry_v else ''
        stop_lbl  = f'Stop {stop_v:,.0f} ({stop_pct:+.0f}%)' if stop_v else ''
        tgt_lbl   = f'Target {tgt_v:,.0f} ({tgt_pct:+.0f}%)' if tgt_v else ''

        b64 = _make_candle_chart_b64(
            ticker, entry_v, stop_v, tgt_v,
            entry_lbl, stop_lbl, tgt_lbl
        )

        # Micro info bar (single line below chart ticker is on chart already)
        cm = re.search(r'1D:\s*([+-]?\d+\.?\d*)%', price_raw)
        chg = float(cm.group(1)) if cm else 0.0
        chg_str = f"{'+' if chg >= 0 else ''}{chg:.1f}%"
        chg_color = '#15803d' if chg >= 0 else '#dc2626'

        # RS short
        m1 = re.search(r'1M:\s*(\d+\w+)', rs_raw)
        rs_str = m1.group(1) if m1 else ''
        s2_ok = 'Stage 2' in stage_raw or '✅' in stage_raw

        # Overlay info bar — position:absolute at bottom, semi-transparent
        s2_chip  = '<span style="background:#dcfce7;color:#15803d;border-radius:2px;padding:0 2px;margin-left:2px;">S2✓</span>' if s2_ok else ''
        rs_chip  = f'<span style="background:#dbeafe;color:#1d4ed8;border-radius:2px;padding:0 2px;margin-left:2px;">RS {rs_str}</span>' if rs_str else ''
        rr_chip  = f'<span style="background:#fef3c7;color:#92400e;border-radius:2px;padding:0 2px;margin-left:2px;">R/R 1:{rr_val:.1f}</span>' if rr_val > 1 else ''
        info_bar = (
            f'<div style="position:absolute;bottom:0;left:0;right:0;'
            f'background:rgba(255,255,255,0.90);border-top:1px solid #e5e7eb;'
            f'padding:1px 4px;display:flex;align-items:center;gap:2px;">'
            f'<span style="font-weight:bold;color:#111827;font-size:5.5pt;">{ticker}</span>'
            f'<span style="color:{chg_color};font-weight:bold;font-size:5pt;">{chg_str}</span>'
            f'{s2_chip}{rs_chip}{rr_chip}'
            f'</div>'
        )

        if b64:
            chart_html = (
                f'<img src="data:image/png;base64,{b64}" '
                f'style="width:100%;height:100%;object-fit:fill;display:block;" '
                f'alt="{ticker} chart"/>'
            )
        else:
            chart_html = (
                f'<div style="width:100%;height:100%;display:flex;align-items:center;'
                f'justify-content:center;background:#f9fafb;color:#9ca3af;font-size:6pt;">'
                f'Chart unavailable</div>'
            )

        cards += f'''<div style="position:relative;background:#ffffff;
                         border:1px solid #e2e8f0;border-radius:4px;
                         overflow:hidden;min-height:0;">
  {chart_html}
  {info_bar}
</div>'''

    # 2-col × 5-row grid, fills page via flex:1 in .page-tight
    return f'''
<div style="display:grid;
            grid-template-columns:1fr 1fr;
            grid-template-rows:repeat(5,1fr);
            gap:4px;
            flex:1;
            min-height:0;
            margin-bottom:14mm;">
{cards}
</div>
<div style="font-size:4.5pt;color:#9ca3af;text-align:center;line-height:1.4;margin-top:2px;">
  🔵 Blue = Entry &nbsp;·&nbsp; 🔴 Red = Stop Loss &nbsp;·&nbsp; 🟢 Green = Target &nbsp;·&nbsp;
  Amber = 20-day MA &nbsp;·&nbsp; 3M daily OHLCV · yfinance (delayed)
</div>'''


def build_html(data: dict) -> str:
    """Build full 6-page HTML document from extracted data."""

    # ── Narrative cards ───────────────────────────────────────────────────────
    narr_html = ''
    for n in data.get('narratives', []):
        narr_html += f'''
<div class="narr-card">
  <div class="narr-title">{_clean(n["title"])}</div>
  {f'<div class="narr-subtitle">{_clean(n["subtitle"])}</div>' if n.get("subtitle") else ''}
  <div class="narr-body">{n["body"]}</div>
</div>'''

    # ── Index cards ───────────────────────────────────────────────────────────
    idx_html = ''
    for idx in data.get('indexes', []):
        idx_html += f'''
<div class="idx-card">
  <div class="idx-name">{idx["name"]}</div>
  <div class="idx-price">{idx["price"]}</div>
  <div class="idx-change" style="color:{idx["color"]}">{idx["1d"]}</div>
  <div class="idx-sub">MTD: {idx.get("mtd","—")} &nbsp;|&nbsp; YTD: {idx.get("ytd","—")}</div>
</div>'''

    # ── Key factors ───────────────────────────────────────────────────────────
    factors_html = ''
    for i, f in enumerate(data.get('factors', []), 1):
        factors_html += f'''
<div class="factor-item">
  <span class="factor-num">{i}</span>
  <div class="factor-text">
    <strong>{f["title"]}</strong>
    {f'<br><span class="factor-body">{f["body"]}</span>' if f.get("body") else ''}
  </div>
</div>'''

    # ── Alpha cards — compact 2×5 grid for 10 picks on one page ─────────────
    picks = data.get('alpha_picks', [])
    alpha_compact_html = ''.join(_render_alpha_card_compact(p, i + 1) for i, p in enumerate(picks))
    if not alpha_compact_html:
        alpha_compact_html = '<p class="no-data">No alpha picks today — market breadth check required</p>'

    # ── Chart of the Day page ─────────────────────────────────────────────────
    chart_page_inner = _render_chart_page(picks)

    verdict = data.get('verdict', 'UPTREND')
    verdict_color = data.get('verdict_color', '#15803d')
    verdict_bg = data.get('verdict_bg', '#dcfce7')
    verdict_detail = data.get('verdict_detail', '')
    nrgc = data.get('nrgc_phase', 'Phase 4')
    report_date = data.get('report_date', date_mod.today().strftime('%d %B %Y'))

    macro_html    = _render_macro_table(data.get('macro_rows', []))
    theme_html    = _render_theme_table(data.get('theme_rows', []))
    risk_html     = _render_risk_table(data.get('risk_rows', []))
    event_html    = _render_event_table(data.get('event_rows', []))
    universe_html = _render_universe_table(data.get('universe_top25', []))
    phase_html    = _render_phase_changers(data.get('phase_changers', {}))
    phase_section = (
        '<div class="section-title">⚡ Phase Changers — RS Momentum Shifts</div>'
        + phase_html
    ) if phase_html else ''

    # ── Overnight recap HTML ──────────────────────────────────────────────────
    overnight_text = data.get('overnight_recap', '')
    overnight_html = (
        f'<div class="overnight-recap">'
        f'<div class="overnight-label">Overnight US Market Recap</div>'
        f'{overnight_text}'
        f'</div>'
    ) if overnight_text else ''

    css = '''
        /* ═══ ALPHAABSOLUTE — PREMIUM WEALTH THEME ═══════════════════════
           Palette: Charcoal #1C1C1E · Warm White #F7F6F3 · Gold #C8A96E
           Clean, luxurious, institutional — no navy, no neon blues
        ══════════════════════════════════════════════════════════════════ */

        :root {
            --gold:      #C8A96E;
            --gold-lt:   #E8D5A3;
            --gold-dk:   #A0804A;
            --charcoal:  #1C1C1E;
            --charcoal2: #2D2D30;
            --charcoal3: #3D3D40;
            --gray-dk:   #6B6B6E;
            --gray-md:   #9A9A9D;
            --gray-lt:   #D4D4D7;
            --gray-bg:   #F0EFEC;
            --white:     #FAFAF8;
            --pos:       #2D6A4F;
            --pos-bg:    #D8F3DC;
            --neg:       #AE2012;
            --neg-bg:    #FDEBD0;
            --warn:      #7D5A00;
            --warn-bg:   #FFF3CD;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: 'Leelawadee UI', 'Leelawadee', 'Tahoma', 'THSarabunNew', 'Segoe UI', sans-serif;
            font-size: 8.5pt;
            color: var(--charcoal);
            background: var(--gray-bg);
            line-height: 1.45;
            -webkit-font-smoothing: antialiased;
        }

        /* ── Page layout ──────────────────────────────────────────────── */
        .page {
            width: 210mm;
            min-height: 297mm;
            padding: 12mm 13mm 18mm 13mm;
            background: var(--white);
            page-break-after: always;
            position: relative;
        }
        /* Tight pages lock to exactly one A4 sheet — nothing overflows */
        .page-tight {
            min-height: 0;
            height: 297mm;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }
        .page:last-child { page-break-after: avoid; }
        @page { size: A4; margin: 0; }
        @media print { body { -webkit-print-color-adjust: exact; } }

        /* ── Header ───────────────────────────────────────────────────── */
        .page-header {
            background: var(--charcoal);
            padding: 8px 14px;
            margin: -12mm -13mm 11px -13mm;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 2px solid var(--gold);
        }
        .header-brand {
            font-size: 12.5pt;
            font-weight: bold;
            color: var(--gold);
            letter-spacing: 1px;
            text-transform: uppercase;
        }
        .header-sub { font-size: 7.5pt; color: var(--gray-md); margin-top: 1px; }
        .header-right { text-align: right; }
        .header-date { font-size: 9.5pt; font-weight: bold; color: var(--gold-lt); }
        .header-fw  { font-size: 7pt; color: var(--gray-md); }

        /* ── Footer ───────────────────────────────────────────────────── */
        .page-footer {
            position: absolute;
            bottom: 4mm;
            left: 13mm;
            right: 13mm;
            font-size: 6.5pt;
            color: var(--gray-md);
            border-top: 1px solid var(--gray-lt);
            padding-top: 3px;
        }
        .page-footer-row { display: flex; justify-content: space-between; align-items: center; }
        .page-footer-audit { font-size: 6pt; color: var(--gray-md); margin-top: 1px; line-height: 1.3; }

        /* ── Verdict banner ───────────────────────────────────────────── */
        .verdict-banner {
            background: var(--charcoal);
            border-radius: 6px;
            padding: 9px 14px;
            margin-bottom: 9px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border: 1px solid var(--charcoal3);
        }
        .verdict-label {
            font-size: 6.5pt;
            color: var(--gray-md);
            text-transform: uppercase;
            letter-spacing: 1.5px;
            margin-bottom: 2px;
        }
        .verdict-text {
            font-size: 14pt;
            font-weight: bold;
            padding: 2px 10px;
            border-radius: 3px;
            background: VAR_VERDICT_BG;
            color: VAR_VERDICT_COLOR;
            letter-spacing: 0.5px;
        }
        .verdict-detail { color: var(--gray-lt); font-size: 8pt; margin-top: 2px; }
        .nrgc-badge {
            background: var(--charcoal2);
            color: var(--gold-lt);
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 8pt;
            font-weight: bold;
            border: 1px solid var(--gold-dk);
        }

        /* ── Index cards ──────────────────────────────────────────────── */
        .idx-row { display: flex; gap: 7px; margin-bottom: 9px; }
        .idx-card {
            flex: 1;
            background: var(--white);
            border: 1px solid var(--gray-lt);
            border-radius: 5px;
            padding: 7px 9px;
            border-top: 2px solid var(--gold);
        }
        .idx-name { font-size: 6.5pt; color: var(--gray-dk); font-weight: bold; text-transform: uppercase; letter-spacing: 0.8px; }
        .idx-price { font-size: 12pt; font-weight: bold; color: var(--charcoal); margin: 1px 0; }
        .idx-change { font-size: 10pt; font-weight: bold; }
        .idx-sub { font-size: 6.5pt; color: var(--gray-md); margin-top: 2px; }

        /* ── Section title ────────────────────────────────────────────── */
        .section-title {
            font-size: 7.5pt;
            font-weight: bold;
            color: var(--charcoal);
            background: var(--gray-bg);
            border-left: 3px solid var(--gold);
            padding: 3px 8px;
            margin: 9px 0 5px 0;
            text-transform: uppercase;
            letter-spacing: 0.8px;
        }

        /* ── Narrative cards ──────────────────────────────────────────── */
        .narr-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; margin-bottom: 9px; }
        .narr-card {
            background: var(--white);
            border: 1px solid var(--gray-lt);
            border-radius: 5px;
            padding: 7px 9px;
            border-left: 2px solid var(--gold);
        }
        .narr-title { font-size: 8.5pt; font-weight: bold; color: var(--charcoal); margin-bottom: 2px; }
        .narr-subtitle { font-size: 7.5pt; color: var(--gray-dk); margin-bottom: 2px; }
        .narr-body { font-size: 8pt; color: var(--charcoal2); line-height: 1.4; }

        /* ── Macro table ──────────────────────────────────────────────── */
        .macro-table { width: 100%; border-collapse: collapse; font-size: 8pt; margin-bottom: 7px; }
        .macro-table th {
            background: var(--charcoal);
            color: var(--gold-lt);
            padding: 4px 6px;
            text-align: left;
            font-size: 7pt;
            font-weight: 600;
            letter-spacing: 0.5px;
        }
        .macro-table td { padding: 3px 6px; border-bottom: 1px solid var(--gray-bg); }
        .macro-table tr:nth-child(even) td { background: var(--gray-bg); }

        /* ── Theme table — compact for page 1 ────────────────────────── */
        .theme-table { width: 100%; border-collapse: collapse; font-size: 7pt; margin-bottom: 5px; }
        .theme-table th {
            background: var(--charcoal);
            color: var(--gold-lt);
            padding: 2.5px 4px;
            text-align: center;
            font-size: 6.5pt;
        }
        .theme-table td { padding: 1.5px 4px; border-bottom: 1px solid var(--gray-bg); text-align: center; line-height: 1.3; }
        .theme-table .theme-name { text-align: left; font-weight: 500; }
        .theme-table .num-col { text-align: center; color: var(--gray-md); width: 18px; }
        .theme-table tr.sig-green-row { background: #F0F7F2; }
        .theme-table tr.sig-red-row   { background: #FBF0EE; }
        .theme-table tr.sig-yellow-row{ background: #FBF7EE; }

        /* ── Risk table ───────────────────────────────────────────────── */
        .risk-table { width: 100%; border-collapse: collapse; font-size: 8pt; margin-bottom: 7px; }
        .risk-table th {
            background: var(--charcoal2);
            color: var(--gold-lt);
            padding: 3px 6px;
            text-align: left;
            font-size: 7pt;
        }
        .risk-table td { padding: 2.5px 6px; border-bottom: 1px solid var(--gray-bg); }
        .risk-table tr:nth-child(even) td { background: var(--gray-bg); }

        /* ── Universe Top 30 table — compact to fit page 2 ──────────── */
        .universe-table { width: 100%; border-collapse: collapse; font-size: 7pt; }
        .universe-table th {
            background: var(--charcoal);
            color: var(--gold-lt);
            padding: 2.5px 3px;
            text-align: center;
            font-size: 6pt;
            font-weight: 600;
            letter-spacing: 0.2px;
        }
        .universe-table td { padding: 1.5px 3px; border-bottom: 1px solid var(--gray-bg); text-align: center; line-height: 1.2; }
        .universe-table tr:nth-child(even) td { background: var(--gray-bg); }
        .universe-table tr:nth-child(1) td { font-weight: bold; background: #FFFBF0; }
        .universe-table tr:nth-child(2) td { background: #FAFAF7; }
        .universe-table tr:nth-child(3) td { background: #FAFAF7; }
        .universe-table .ticker-cell { font-weight: bold; color: var(--charcoal); text-align: left; }
        .universe-table .theme-col   { text-align: left; color: var(--gray-dk); }
        .universe-table .rank-col    { color: var(--gold-dk); font-size: 7.5pt; }
        .universe-table .score-col   { font-weight: bold; color: var(--gold-dk); }
        .universe-table .stage-col   { font-size: 7pt; }

        /* ── Signal / color classes ───────────────────────────────────── */
        .sig-green, .pos { color: var(--pos); font-weight: 600; }
        .sig-red,   .neg { color: var(--neg); font-weight: 600; }
        .sig-yellow      { color: var(--warn); }
        .sig-green-row td:first-child { border-left: 2.5px solid var(--pos); }
        .sig-red-row   td:first-child { border-left: 2.5px solid var(--neg); }
        .sig-yellow-row td:first-child{ border-left: 2.5px solid var(--warn); }

        /* ── Key factors ──────────────────────────────────────────────── */
        .factor-list { display: flex; flex-direction: column; gap: 4px; margin-bottom: 7px; }
        .factor-item { display: flex; gap: 7px; align-items: flex-start; }
        .factor-num {
            background: var(--charcoal);
            color: var(--gold);
            width: 18px; height: 18px;
            border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            font-size: 7pt; font-weight: bold; flex-shrink: 0;
        }
        .factor-text { font-size: 8pt; color: var(--charcoal2); }
        .factor-body { color: var(--gray-dk); font-size: 7.5pt; }

        /* ── Alpha cards ──────────────────────────────────────────────── */
        .alpha-grid { display: flex; flex-direction: column; gap: 7px; margin-bottom: 8px; }

        /* ── Compact alpha grid — 10 cards per page, 1-col × 10-row, fills page ── */
        .alpha-grid-compact {
            display: grid;
            grid-template-columns: 1fr;
            grid-template-rows: repeat(10, 1fr);
            gap: 3px;
            flex: 1;          /* fills remaining flex height in .page-tight */
            min-height: 0;    /* allow shrink inside flex */
            margin-bottom: 14mm;  /* clearance for absolute footer */
        }
        .alpha-card-compact {
            background: var(--white);
            border: 1px solid #e2e8f0;
            border-left: 4px solid var(--gold);
            border-radius: 4px;
            padding: 0 10px;
            display: flex;
            flex-direction: row;
            align-items: center;
            gap: 0;
            overflow: hidden;
            min-height: 0;
        }
        /* ── Rank column ─────────────────────────────────────── */
        .acc-rank {
            font-size: 7.5pt; font-weight: bold; color: #94a3b8;
            min-width: 18px; text-align: center; flex-shrink: 0;
        }
        /* ── Ticker + Name block ─────────────────────────────── */
        .acc-id {
            display: flex; flex-direction: column; justify-content: center;
            min-width: 88px; max-width: 88px; flex-shrink: 0;
            padding: 0 6px 0 5px;
            border-right: 1px solid #e2e8f0;
        }
        .acc-ticker   { font-size: 9pt; font-weight: bold; color: var(--gold);
                        white-space: nowrap; line-height: 1.2; }
        .acc-name     { font-size: 5.5pt; color: var(--gray-md);
                        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
                        max-width: 86px; line-height: 1.2; }
        /* ── Price + change column ───────────────────────────── */
        .acc-price-col {
            display: flex; flex-direction: column; justify-content: center; align-items: flex-end;
            min-width: 58px; max-width: 58px; flex-shrink: 0;
            padding: 0 6px;
            border-right: 1px solid #e2e8f0;
        }
        .acc-price    { font-size: 7.5pt; font-weight: bold; color: var(--charcoal);
                        white-space: nowrap; line-height: 1.2; }
        .acc-chg-pos  { font-size: 7pt; font-weight: bold; color: #16a34a; line-height: 1.2; }
        .acc-chg-neg  { font-size: 7pt; font-weight: bold; color: #dc2626; line-height: 1.2; }
        /* ── RS + badges column ──────────────────────────────── */
        .acc-signal-col {
            display: flex; flex-direction: column; justify-content: center;
            min-width: 98px; max-width: 98px; flex-shrink: 0;
            padding: 0 6px;
            border-right: 1px solid #e2e8f0;
            gap: 2px;
        }
        .acc-rs    { font-size: 5.5pt; color: #1d4ed8; background: #eff6ff;
                     border-radius: 2px; padding: 0 3px; white-space: nowrap;
                     display: inline-block; }
        .acc-badges { display: flex; gap: 3px; align-items: center; flex-wrap: nowrap; }
        .acc-badge { font-size: 5.5pt; padding: 0 3px; border-radius: 2px; font-weight: bold; white-space: nowrap; }
        .acc-s2   { background: #dcfce7; color: #15803d; }
        .acc-fail { background: #fee2e2; color: #991b1b; }
        .acc-pulse { background: #fef3c7; color: #92400e; }
        .acc-nrgc  { background: #ede9fe; color: #5b21b6; }
        /* ── Trade column ────────────────────────────────────── */
        .acc-trade-col {
            display: flex; flex-direction: row; align-items: center;
            gap: 3px; flex-shrink: 0;
            padding: 0 6px;
            border-right: 1px solid #e2e8f0;
            font-size: 6.5pt; white-space: nowrap;
        }
        .acc-entry  { color: #1d4ed8; font-weight: bold; }
        .acc-stop   { color: #dc2626; font-weight: bold; }
        .acc-target { color: #15803d; font-weight: bold; }
        .acc-arr    { color: var(--gray-md); font-size: 5.5pt; }
        .acc-rr     { background: var(--charcoal); color: var(--gold);
                      font-size: 5.5pt; font-weight: bold; padding: 1px 4px;
                      border-radius: 2px; white-space: nowrap; margin-left: 2px; }
        /* ── WHY column — takes remaining space ──────────────── */
        .acc-why-col {
            flex: 1; min-width: 0;
            padding: 0 8px;
            display: flex; align-items: center;
        }
        .acc-why    { font-size: 6pt; color: #475569; line-height: 1.3;
                      overflow: hidden;
                      display: -webkit-box; -webkit-line-clamp: 2;
                      -webkit-box-orient: vertical; }
        .alpha-card {
            background: var(--white);
            border: 1px solid var(--gray-lt);
            border-radius: 6px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        }
        .alpha-header {
            background: var(--charcoal);
            padding: 6px 12px;
            display: flex;
            align-items: center;
            gap: 10px;
            border-bottom: 1px solid var(--gold-dk);
        }
        .alpha-ticker { font-size: 13pt; font-weight: bold; color: var(--gold); min-width: 60px; letter-spacing: 0.5px; }
        .alpha-name-wrap { flex: 1; display: flex; flex-direction: column; gap: 2px; }
        .alpha-name { font-size: 8pt; color: var(--gold-lt); }
        .alpha-badges { display: flex; gap: 5px; }
        .stage-badge {
            font-size: 6.5pt; font-weight: bold; padding: 1px 5px;
            border-radius: 3px; background: #1A3A2A; color: #6FCF97;
        }
        .stage-badge.sig-red { background: #3A1A1A; color: #EB5757; }
        .pulse-badge { font-size: 6.5pt; color: var(--gray-md); }
        .alpha-price-block { text-align: right; flex-shrink: 0; }
        .alpha-price { font-size: 11pt; color: white; font-weight: bold; }
        .alpha-change { font-size: 10pt; font-weight: bold; }
        .alpha-change.pos { color: #6FCF97; }
        .alpha-change.neg { color: #EB5757; }

        .alpha-chips-row {
            padding: 4px 12px;
            display: flex; gap: 9px; flex-wrap: wrap;
            background: var(--gray-bg);
            border-bottom: 1px solid var(--gray-lt);
        }
        .chip { font-size: 7pt; color: var(--charcoal2); }
        .chip-label { font-weight: bold; color: var(--gray-dk); margin-right: 2px; font-size: 6.5pt; text-transform: uppercase; }

        .alpha-trade-row {
            padding: 5px 12px;
            display: flex; align-items: center; gap: 6px;
            background: var(--white);
        }
        .trade-pill {
            display: flex; align-items: center; gap: 5px;
            padding: 3px 9px; border-radius: 4px;
        }
        .entry-pill  { background: #EEF3FF; border: 1px solid var(--gold-lt); }
        .stop-pill   { background: #FBF0EE; border: 1px solid #E8B4AD; }
        .target-pill { background: #EEF7EE; border: 1px solid #A8D5A2; }
        .tp-label { font-size: 6.5pt; font-weight: bold; color: var(--gray-dk); text-transform: uppercase; }
        .tp-val   { font-size: 8.5pt; font-weight: bold; }
        .entry-pill  .tp-val { color: var(--charcoal); }
        .stop-pill   .tp-val { color: var(--neg); }
        .target-pill .tp-val { color: var(--pos); }
        .tp-pct { font-size: 7pt; color: var(--gray-dk); }
        .trade-arrow { color: var(--gold); font-size: 9pt; }
        .rr-pill {
            margin-left: auto; background: var(--charcoal);
            color: var(--gold); padding: 3px 9px;
            border-radius: 4px; font-size: 7.5pt; font-weight: bold;
            border: 1px solid var(--gold-dk);
        }
        .alpha-entry-why {
            padding: 3px 12px 4px;
            font-size: 6.5pt;
            color: #94A3B8;
            line-height: 1.4;
            border-top: 1px solid var(--charcoal3);
        }

        /* ── Event table ──────────────────────────────────────────────── */
        .event-table { width: 100%; border-collapse: collapse; font-size: 7.5pt; margin-bottom: 7px; }
        .event-table th {
            background: var(--charcoal2);
            color: var(--gold-lt);
            padding: 3px 5px;
            text-align: left;
            font-size: 7pt;
        }
        .event-table td { padding: 2px 5px; border-bottom: 1px solid var(--gray-bg); line-height: 1.35; }
        .event-table tr.sig-red-row    { background: #FBF0EE; }
        .event-table tr.sig-yellow-row { background: #FBF7EE; }
        .impact-cell.sig-red    { color: var(--neg); font-weight: bold; }
        .impact-cell.sig-yellow { color: var(--warn); }

        /* ── Two-column layout ────────────────────────────────────────── */
        .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; margin-bottom: 7px; }

        /* ── Overnight recap box ─────────────────────────────────────── */
        .overnight-recap {
            background: var(--charcoal);
            color: #D4D4D7;
            border-radius: 5px;
            padding: 7px 11px;
            margin-bottom: 8px;
            font-size: 7.5pt;
            line-height: 1.6;
            border-left: 3px solid var(--gold);
        }
        .overnight-label {
            font-size: 5.5pt;
            font-weight: bold;
            color: var(--gold);
            letter-spacing: 1.2px;
            text-transform: uppercase;
            margin-bottom: 3px;
        }

        /* ── Macro + Events two-column ───────────────────────────────── */
        .macro-events-grid {
            display: grid;
            grid-template-columns: 58% 40%;
            gap: 10px;
            align-items: start;
        }

        /* ── Misc ─────────────────────────────────────────────────────── */
        .no-data { color: var(--gray-md); font-style: italic; font-size: 8pt; padding: 6px 0; }
        .sep { border: none; border-top: 1px solid var(--gray-lt); margin: 7px 0; }

        /* ── Portfolio Page (Page 4) ──────────────────────────────────── */
        .port-banner {
            background: var(--charcoal);
            border-radius: 6px;
            padding: 6px 10px;
            margin-bottom: 9px;
            display: flex;
            align-items: center;
            gap: 3px;
            flex-wrap: nowrap;          /* force single row — never wrap */
            overflow: hidden;
            border: 1px solid var(--charcoal3);
            border-bottom: 2px solid var(--gold-dk);
        }
        .port-stat-block {
            flex: 1 1 0;
            min-width: 0;               /* allow shrink below min-content */
            text-align: center;
            padding: 2px 2px;
        }
        .port-stat-label {
            font-size: 4.8pt;
            color: var(--gray-md);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 1px;
        }
        .port-stat-val {
            font-size: 8.5pt;           /* reduced from 11pt to prevent wrapping */
            font-weight: bold;
            color: var(--gold-lt);
            line-height: 1.2;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .port-stat-sub { font-size: 5.5pt; color: var(--gray-dk); margin-top: 1px; white-space: nowrap; }
        .port-stat-sep {
            width: 1px; background: var(--charcoal3);
            margin: 0 1px; align-self: stretch;
            flex-shrink: 0;
        }

        /* Portfolio holdings table */
        .port-table { width: 100%; border-collapse: collapse; font-size: 7.5pt; margin-bottom: 5px; }
        .port-table th {
            background: var(--charcoal);
            color: var(--gold-lt);
            padding: 3px 5px;
            text-align: center;
            font-size: 6pt;
            font-weight: 600;
            letter-spacing: 0.3px;
        }
        .port-table th:first-child { text-align: left; }
        .port-table td {
            padding: 3px 5px;
            border-bottom: 1px solid var(--gray-bg);
            text-align: center;
            vertical-align: top;
        }
        .port-table td.ticker-cell { text-align: left; }
        .port-table tr:nth-child(4n+1) td { background: #FAFAF8; }
        .port-table tr:nth-child(4n+3) td { background: var(--gray-bg); }
        .port-table .action-detail-row td {
            border-bottom: 2px solid var(--gray-lt);
            background: #F7F6F3 !important;
            padding: 1px 6px 4px 28px;
        }
        .port-table tfoot tr td {
            background: var(--gray-bg);
            border-top: 2px solid var(--gold-lt);
            padding: 3px 5px;
            text-align: center;
        }

        /* Performance stats row */
        .port-stats-row {
            display: flex; gap: 0; margin: 7px 0 6px 0;
            background: var(--charcoal); border-radius: 5px;
            overflow: hidden; border: 1px solid var(--charcoal3);
        }
        .port-mini-stat {
            flex: 1; padding: 6px 8px; text-align: center;
            border-right: 1px solid var(--charcoal3);
        }
        .port-mini-stat:last-child { border-right: none; }
        .pms-label { font-size: 5.5pt; color: var(--gray-md); text-transform: uppercase; letter-spacing: 0.7px; margin-bottom: 2px; }
        .pms-val   { font-size: 9.5pt; font-weight: bold; color: var(--gold-lt); }
        .pms-sub   { font-size: 6pt; color: var(--gray-dk); margin-top: 1px; }

        /* Closed trades mini-table */
        .port-closed-table { width: 100%; border-collapse: collapse; font-size: 7pt; }
        .port-closed-table th {
            background: var(--charcoal2); color: var(--gold-lt);
            padding: 2px 4px; font-size: 6pt;
        }
        .port-closed-table td { padding: 2px 4px; border-bottom: 1px solid var(--gray-bg); }

        /* Watchlist on deck chips */
        .deck-chip {
            background: var(--charcoal2);
            border: 1px solid var(--charcoal3);
            border-radius: 5px;
            padding: 6px 10px;
            flex: 1; min-width: 120px;
        }
    '''

    # Replace CSS template vars
    css = css.replace('VAR_VERDICT_BG', verdict_bg).replace('VAR_VERDICT_COLOR', verdict_color)

    def _hdr(subtitle, page_n):
        return f'''  <div class="page-header">
    <div>
      <div class="header-brand">AlphaAbsolute</div>
      <div class="header-sub">{subtitle}</div>
    </div>
    <div class="header-right">
      <div class="header-date">{report_date}</div>
      <div class="header-fw">NRGC + PULSE v2.0 &nbsp;·&nbsp; {page_n}</div>
    </div>
  </div>'''

    def _ftr(page_n, extra=''):
        return f'''  <div class="page-footer">
    <div class="page-footer-row">
      <span>AlphaAbsolute Research &nbsp;·&nbsp; For Internal Use Only</span>
      <span>{page_n}</span>
      <span>Not Investment Advice</span>
    </div>
    {f'<div class="page-footer-audit">{extra}</div>' if extra else ''}
  </div>'''

    # ── Portfolio page ─────────────────────────────────────────────────────────
    portfolio    = data.get('portfolio', {})
    portfolio_html = _render_portfolio_page(portfolio)

    html = f'''<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<style>{css}</style>
</head>
<body>

<!-- ═══════════════════════ PAGE 1 — MACRO + THEME OVERVIEW ══════════════ -->
<div class="page">
{_hdr("Daily Market Pulse &nbsp;·&nbsp; Macro &amp; Theme Overview", "Page 1 / 6")}

  <div class="verdict-banner">
    <div>
      <div class="verdict-label">Market Verdict</div>
      <div class="verdict-text">{verdict}</div>
      <div class="verdict-detail">{verdict_detail}</div>
    </div>
    <div class="nrgc-badge">NRGC {nrgc}</div>
  </div>

  <div class="idx-row">{idx_html}</div>

  {overnight_html}

  <div class="macro-events-grid">
    <div>
      <div class="section-title">Macro Snapshot &nbsp;·&nbsp; FRED + yfinance</div>
      {macro_html}
    </div>
    <div>
      <div class="section-title">Events Calendar — This Week</div>
      {event_html}
    </div>
  </div>

  <div class="section-title">Theme Heatmap — RS Percentile Ranking (Leader ≥ 75th pct)</div>
  {theme_html}

{_ftr("Page 1 / 6")}
</div>


<!-- ═══════════════════════ PAGE 2 — FACTORS & UNIVERSE ═════════════════ -->
<div class="page">
{_hdr("Key Factors &amp; PULSE Universe Top 30", "Page 2 / 6")}

  <div class="two-col">
    <div>
      <div class="section-title">Key Factors Driving the Market</div>
      <div class="factor-list">{factors_html}</div>
    </div>
    <div>
      <div class="section-title">Key Risks to Monitor</div>
      {risk_html}
    </div>
  </div>

  <div class="section-title">PULSE Universe Top 30 — Ranked by RS Percentile Score</div>
  {universe_html}

{_ftr("Page 2 / 6")}
</div>


<!-- ═══════════════════════ PAGE 3 — ALPHA OF THE DAY ════════════════════ -->
<div class="page page-tight">
{_hdr("Alpha of the Day &amp; Events Calendar", "Page 3 / 6")}
  <div style="font-size:6.5pt;font-weight:bold;color:var(--charcoal);
              background:var(--gray-bg);border-left:3px solid var(--gold);
              padding:2px 8px;margin:4px 0 3px;text-transform:uppercase;
              letter-spacing:0.8px;flex-shrink:0;">
    Top 10 Conviction Picks &nbsp;·&nbsp;
    <span style="font-weight:normal;color:var(--gray-md);text-transform:none;">
      Stage 2 &nbsp;·&nbsp; RS ≥ 75th &nbsp;·&nbsp; PULSE ≥ 3/5 &nbsp;·&nbsp; NRGC Phase 3–5
    </span>
  </div>
  <div class="alpha-grid-compact">{alpha_compact_html}</div>
{_ftr("Page 3 / 6",
  "RS = Percentile rank within PULSE universe (22 stocks) &nbsp;·&nbsp; "
  "Entry/Stop/Target: PULSE framework estimates — verify via Bloomberg before trading &nbsp;·&nbsp; "
  "ไม่ใช่คำแนะนำการลงทุน — ผู้ใช้งานต้องรับผิดชอบการตัดสินใจลงทุนด้วยตนเอง"
)}
</div>


<!-- ═══════════════════════ PAGE 4 — CHART OF THE DAY ════════════════════ -->
<div class="page page-tight">
{_hdr("Chart of the Day &nbsp;·&nbsp; PULSE Alpha Top 10 Setup Charts", "Page 4 / 6")}
  <div style="font-size:6.5pt;font-weight:bold;color:var(--charcoal);
              background:var(--gray-bg);border-left:3px solid var(--gold);
              padding:2px 8px;margin:4px 0 3px;text-transform:uppercase;
              letter-spacing:0.8px;flex-shrink:0;">
    📈 10 Alpha Setups &nbsp;·&nbsp;
    <span style="font-weight:normal;color:var(--gray-md);text-transform:none;">
      Candlestick + Volume · Entry · Stop · Target per PULSE Framework
    </span>
  </div>
  {chart_page_inner}
{_ftr("Page 4 / 6",
  "Charts: yfinance 3M daily OHLCV (light mode) &nbsp;·&nbsp; 🔵 Entry · 🔴 Stop · 🟢 Target · Amber = MA20 &nbsp;·&nbsp; "
  "Levels: PULSE estimates — verify via Bloomberg/TradingView before trading &nbsp;·&nbsp; ไม่ใช่คำแนะนำการลงทุน"
)}
</div>


<!-- ═══════════════════════ PAGE 5 — PORTFOLIO MANAGEMENT ════════════════ -->
<div class="page">
{_hdr("AlphaAbsolute Portfolio Management &nbsp;·&nbsp; Fund Manager Briefing", "Page 5 / 6")}

{portfolio_html}

{_ftr("Page 5 / 6",
  "Portfolio data: data/portfolio.json &nbsp;·&nbsp; Live prices: yfinance (15-min delayed) &nbsp;·&nbsp; "
  "P&amp;L = unrealized mark-to-market &nbsp;·&nbsp; Max 10 positions mandate &nbsp;·&nbsp; "
  "Benchmark: QQQ (Nasdaq-100) &nbsp;·&nbsp; ไม่ใช่คำแนะนำการลงทุน"
)}
</div>


<!-- ═══════════════════════ PAGE 6 — METHODOLOGY APPENDIX ════════════════ -->
<div class="page">
{_hdr("Appendix — AlphaAbsolute Scoring Methodology &amp; Framework", "Page 6 / 6")}

<div class="section-title">📐 PULSE Score — How We Rank Every Stock</div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px;">

<div style="background:var(--charcoal);border-radius:6px;padding:10px 12px;border-top:2px solid var(--gold);">
<div style="font-size:8pt;font-weight:bold;color:var(--gold-lt);margin-bottom:6px;">🔢 ALPHA SCORE FORMULA</div>
<div style="font-size:7.5pt;color:var(--gray-lt);line-height:1.7;">
<code style="color:var(--gold);font-size:7pt;">Score = (RS_Pct × 0.50) + (RS_Δ × 0.30) + (PULSE × 4) + (max(0, 25+FromHigh) × 0.3)</code><br><br>
<b>RS_Pct</b> — Percentile rank 0–100 within PULSE universe (6M timeframe). Higher = stronger relative strength vs SPY.<br><br>
<b>RS_Δ</b> — Rank acceleration: RS_Pct_1M minus RS_Pct_3M. Positive = stock is gaining speed vs universe. e.g. moved from 50th → 62nd = +12.<br><br>
<b>PULSE</b> — 5-criteria checklist (0–5 passes): Stage 2 above 150D MA, above 200D MA, 150D &gt; 200D, RS positive, near 52W high.<br><br>
<b>FromHigh</b> — % from 52W high. Closer to ATH = higher contribution. Stocks &gt;30% below ATH score 0 here.<br><br>
<b>Gates (must pass both):</b><br>
• Stage 2 required (price &gt; rising 30W MA)<br>
• RS_Pct ≥ 50th (must be above median of universe)
</div>
</div>

<div style="background:var(--charcoal);border-radius:6px;padding:10px 12px;border-top:2px solid #6FCF97;">
<div style="font-size:8pt;font-weight:bold;color:#6FCF97;margin-bottom:6px;">📊 RS PERCENTILE RANK — How to Read</div>
<div style="font-size:7.5pt;color:var(--gray-lt);line-height:1.7;">
<b>What it is:</b> Where this stock ranks vs all stocks in PULSE universe (22 stocks) by 6M / 3M / 1M return vs SPY.<br><br>
<b>95th</b> = top 5% — strongest RS in universe (Leader territory)<br>
<b>75th</b> = top 25% — OVERWEIGHT zone<br>
<b>50th</b> = median — minimum gate to be considered<br>
<b>25th</b> = bottom quartile — avoid / watch only<br><br>
<b>Δ rank (rank change):</b> 1M rank minus 3M rank.<br>
+12 = stock moved up 12 positions in 1M vs where it was 3M ago → momentum accelerating<br>
−8 = moved down 8 positions → momentum cooling → flag for review<br><br>
<b>Phase Changer threshold:</b> |Δ rank| ≥ 8 positions triggers alert
</div>
</div>

</div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px;">

<div style="background:var(--charcoal);border-radius:6px;padding:10px 12px;border-top:2px solid #F2994A;">
<div style="font-size:8pt;font-weight:bold;color:#F2994A;margin-bottom:6px;">🌀 NRGC — 7-Phase Narrative Cycle</div>
<div style="font-size:7pt;color:var(--gray-lt);line-height:1.7;">
<b>Ph1 Neglect</b> — RS &lt;20th. Nobody cares. Vol dry. Dead chart.<br>
<b>Ph2 Accumulation</b> — 20–35th. Smart money builds quietly. VCP forming.<br>
<b>Ph2→3 Inflection</b> — 35–50th + RS accel. Story starts getting traction. BEST ASYMMETRIC ENTRY.<br>
<b>Ph3→4 Recognition</b> — 50–60th + accel. Proof emerging. Momentum traders notice.<br>
<b>Ph4 Recognition</b> — ≥75th. Consensus forming. Analysts upgrade. Institutional FOMO.<br>
<b>Ph5–6 Consensus/Euphoria</b> — ≥90th near ATH. Everyone owns it. Risk of reversal.<br>
<b>Ph7 Distribution</b> — Wyckoff UTAD/SOW. Good news = price doesn't move. Exit signal.
</div>
</div>

<div style="background:var(--charcoal);border-radius:6px;padding:10px 12px;border-top:2px solid #9B59B6;">
<div style="font-size:8pt;font-weight:bold;color:#C39BD3;margin-bottom:6px;">🎯 WYCKOFF × WEINSTEIN GATE</div>
<div style="font-size:7pt;color:var(--gray-lt);line-height:1.7;">
<b>Mandatory double gate — both must pass for any BUY:</b><br><br>
<b>Weinstein Stage 2:</b> Price &gt; rising 30W MA. MA trending up. RS positive. If Stage 3/4 → automatic fail.<br><br>
<b>Wyckoff Phase:</b><br>
• Accumulation C/D (Spring, LPS, SOS) → GREEN gate ✅<br>
• Mark-Up early (CHoCH, BUEC) → GREEN gate ✅<br>
• Distribution (UTAD, SOW, LPSY) → RED gate 🔴 — no buy regardless of fundamentals<br><br>
<b>Entry signals (WHY this price):</b><br>
• VCP Contraction: vol dry-up &lt;0.6x avg, tight range (&lt;8% depth)<br>
• Wyckoff LPS: pullback after SOS on lower vol = re-entry<br>
• Spring: price dips below support + volume spike + snap back<br>
• SOS Breakout: vol &gt;1.5x avg + price bar closes near high
</div>
</div>

</div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px;">

<div style="background:var(--charcoal);border-radius:6px;padding:10px 12px;border-top:2px solid #60a5fa;">
<div style="font-size:8pt;font-weight:bold;color:#93c5fd;margin-bottom:6px;">🏥 HEALTH CHECK DASHBOARD — Leadership State Score</div>
<div style="font-size:6.8pt;color:var(--gray-lt);line-height:1.65;">
<b style="color:#60a5fa;">8 Indicators (score 0–8):</b><br>
<b>TF Alignment</b> — Monthly/Weekly/Daily/Intraday all Bull? → 4/4 = full marks<br>
<b>Market</b> — Breadth healthy, risk-on confirmed, &lt;5 distribution days<br>
<b>Rel Strength</b> — Outperforms SPX/SET + sector in up AND down moves (Leading)<br>
<b>Volume</b> — Accumulation footprint, breakout vol expansion, dry-up before pivot<br>
<b>Momentum</b> — Strong but not parabolic: "Strong + Ranging" = ideal entry zone<br>
<b>Volatility</b> — Compression → Expansion cycle confirmed (ATR, Bollinger squeeze)<br>
<b>Extension</b> — RSI &lt;80, price &lt;10% above 10EMA = Normal (safe to enter)<br>
<b>Bull Streak</b> — 4+ consecutive bullish bars = sustained demand pressure<br><br>
<b style="color:#6FCF97;">7–8 / 8 = GREEN</b> — full size &nbsp;|&nbsp; <b style="color:#F2994A;">5–6 / 8 = YELLOW</b> — reduced &nbsp;|&nbsp; <b style="color:#ef5350;">&lt;5 = RED</b> — wait
</div>
</div>

<div style="background:var(--charcoal);border-radius:6px;padding:10px 12px;border-top:2px solid #f59e0b;">
<div style="font-size:8pt;font-weight:bold;color:#fcd34d;margin-bottom:6px;">📈 6-PHASE MULTIBAGGER CYCLE</div>
<div style="font-size:6.8pt;color:var(--gray-lt);line-height:1.65;">
<b style="color:#94a3b8;">Ph1 Neglect</b> — Vol dry, no analyst coverage, earnings stabilizing. RS &lt;20th.<br>
<b style="color:#6FCF97;">Ph2 Early Accel ★</b> — Revenue/EPS starts accelerating. First base. Best asymmetric entry.<br>
<b style="color:#6FCF97;">Ph3 Inst. Discovery ★</b> — Smart money enters. Vol expands. ATH breakout. RS surges.<br>
<b style="color:#F2994A;">Ph4 Narrative</b> — PE re-rates. Momentum funds chase. Crowding risk rises.<br>
<b style="color:#ef5350;">Ph5 Euphoria</b> — RSI &gt;85, parabolic, retail FOMO. Climax volume = warning.<br>
<b style="color:#ef5350;">Ph6 Distribution</b> — Good news, price fails. RS decays. Failed pivots. Exit zone.<br><br>
<b>★ Best R/R entry: Ph2–Ph3 (Early Accel → Institutional Discovery)</b><br>
Maps to NRGC Ph1–3 in daily report badges
</div>
</div>

</div>

<div style="background:var(--charcoal);border-radius:6px;padding:10px 12px;border-top:2px solid var(--gold-dk);margin-bottom:10px;">
<div style="font-size:8pt;font-weight:bold;color:var(--gold-lt);margin-bottom:6px;">📋 3-SETUP PULSE FRAMEWORK — Entry Rules by Setup Type</div>
<table style="width:100%;border-collapse:collapse;font-size:6.8pt;">
<thead><tr style="color:var(--gold);font-weight:bold;border-bottom:1px solid var(--charcoal3);">
<th style="padding:3px 6px;text-align:left;">Setup</th><th style="padding:3px 6px;">Max Weight</th><th style="padding:3px 6px;">RS Gate</th><th style="padding:3px 6px;">Health Score</th><th style="padding:3px 6px;">Entry Trigger</th><th style="padding:3px 6px;">Stop Rule</th><th style="padding:3px 6px;">Exit Signal</th>
</tr></thead><tbody style="color:var(--gray-lt);">
<tr style="border-bottom:1px solid var(--charcoal3);">
<td style="padding:3px 6px;font-weight:bold;color:#6FCF97;">Leader / Momentum</td><td style="padding:3px 6px;text-align:center;">12–15%</td><td style="padding:3px 6px;text-align:center;">≥75th</td><td style="padding:3px 6px;text-align:center;">≥7/8</td><td style="padding:3px 6px;">VCP pivot on vol / EMA5 bounce</td><td style="padding:3px 6px;">8% below entry</td><td style="padding:3px 6px;">RS &lt;70th OR Stage 3</td>
</tr>
<tr style="border-bottom:1px solid var(--charcoal3);">
<td style="padding:3px 6px;font-weight:bold;color:#F2994A;">Bottom Fishing</td><td style="padding:3px 6px;text-align:center;">6–8%</td><td style="padding:3px 6px;text-align:center;">RS turning up</td><td style="padding:3px 6px;text-align:center;">≥5/8</td><td style="padding:3px 6px;">SOS confirmed + LPS pullback</td><td style="padding:3px 6px;">Below Spring low</td><td style="padding:3px 6px;">SOW or Stage 4</td>
</tr>
<tr>
<td style="padding:3px 6px;font-weight:bold;color:#AE2012;">Hypergrowth Base 0/1</td><td style="padding:3px 6px;text-align:center;">3–5%</td><td style="padding:3px 6px;text-align:center;">≥50th + accel</td><td style="padding:3px 6px;text-align:center;">Ph2–Ph3</td><td style="padding:3px 6px;">Base 0 = 3% max; Base 1 = 5% max</td><td style="padding:3px 6px;">10% below pivot</td><td style="padding:3px 6px;">Theme RS fade</td>
</tr>
</tbody></table>
</div>

{_ftr("Page 6 / 6",
  "AlphaAbsolute — Framework v2.0 NRGC + PULSE | Health Check Dashboard | Source: Minervini SEPA, Wyckoff, O'Neil CANSLIM | "
  "ข้อมูลและ methodology ใช้เพื่อการศึกษาและวิจัยเท่านั้น ไม่ใช่คำแนะนำการลงทุน"
)}
</div>

</body>
</html>'''

    return html


# ═══════════════════════════════════════════════════════════════════════════════
# PDF GENERATION — Playwright Chromium
# ═══════════════════════════════════════════════════════════════════════════════

def validate_report(data: dict) -> tuple[bool, list[str]]:
    """
    Pre-send QC gate. Returns (ok, issues).
    If ok=False, report must NOT be sent — fix issues and retry.
    """
    issues = []
    warnings = []

    # 1. Alpha picks present
    picks = data.get('alpha_picks', [])
    if not picks:
        issues.append("[FAIL] Alpha picks: NONE -- screener or market data missing")
    else:
        warnings.append(f"[OK] Alpha picks: {len(picks)} picks")

    # 2. Macro rows present
    macro_rows = data.get('macro_rows', [])
    if len(macro_rows) < 3:
        issues.append(f"[FAIL] Macro table: only {len(macro_rows)} rows -- FRED data incomplete")
    else:
        mtd_count = sum(
            1 for row in macro_rows[1:]
            if len(row) > 4 and row[4].strip() not in ('', '—', 'MTD', '--', '-')
        )
        if mtd_count == 0:
            warnings.append("[WARN] MTD/YTD: all empty -- perf_ctx unavailable (SSL/proxy issue). PDF will still generate.")
        else:
            warnings.append(f"[OK] Macro table: {len(macro_rows)-1} rows, {mtd_count} with MTD data")

    # 3. Universe Top 30 present (warning only — screener may not have run today)
    u25 = data.get('universe_top25', [])
    if len(u25) < 5:
        warnings.append(f"[WARN] Universe Top 30: only {len(u25)} tickers -- screener data partial. PDF will still generate.")
    else:
        warnings.append(f"[OK] Universe Top 30: {len(u25)} tickers ranked")

    # 4. Phase changers (warning only, not blocking)
    pc = data.get('phase_changers', {})
    n_accel = len(pc.get('accelerating', []))
    n_fade  = len(pc.get('decelerating', []))
    if n_accel + n_fade == 0:
        warnings.append("[WARN] Phase changers: none detected (not blocking)")
    else:
        warnings.append(f"[OK] Phase changers: {n_accel} accel, {n_fade} fading")

    # 5. Narratives present
    narrs = data.get('narratives', [])
    if not narrs:
        issues.append("[FAIL] Market narratives: NONE -- report narrative section empty")
    else:
        warnings.append(f"[OK] Narratives: {len(narrs)}")

    ok = len(issues) == 0
    status = "REPORT VALIDATED" if ok else "REPORT VALIDATION FAILED"
    all_msgs = ([status] + issues) + warnings
    return ok, all_msgs


def markdown_to_pdf(md_text: str, output_path: str, date_str: str,
                    market_data: dict = None) -> str:
    """Convert markdown daily brief to PDF using Playwright Chromium."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError("Run: pip install playwright && playwright install chromium")

    data = extract_report_data(md_text)
    if date_str:
        data['report_date'] = date_str

    # ── Load portfolio with live prices ───────────────────────────────────────
    portfolio = _load_portfolio(market_data)
    data['portfolio'] = portfolio
    slots_used = len(portfolio.get('holdings', []))
    print(f"  [OK] Portfolio loaded: {slots_used} positions")

    # ── Pre-send validation ────────────────────────────────────────────────────
    ok, qc_msgs = validate_report(data)
    for msg in qc_msgs:
        print(f"  [QC] {msg}")
    if not ok:
        raise ValueError(
            "Report failed QC validation — fix issues above before sending.\n"
            + "\n".join(m for m in qc_msgs if m.startswith('❌'))
        )

    html = build_html(data)

    # Write temp HTML
    html_path = Path(output_path).with_suffix('.html')
    html_path.write_text(html, encoding='utf-8')

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f'file:///{html_path.as_posix()}', wait_until='networkidle')
        page.pdf(
            path=str(output_path),
            format='A4',
            print_background=True,
            margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'},
        )
        browser.close()

    # Clean up temp HTML (optional — keep for debugging)
    # html_path.unlink(missing_ok=True)
    print(f"  [OK] HTML preview: {html_path}")
    print(f"  [OK] PDF saved: {output_path}")
    return output_path


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM DELIVERY
# ═══════════════════════════════════════════════════════════════════════════════

def send_pdf_telegram(pdf_path: str, caption: str,
                      bot_token: str, chat_id: str) -> bool:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    try:
        with open(pdf_path, 'rb') as f:
            resp = requests.post(url, data={
                'chat_id': chat_id,
                'caption': caption[:1024],
                'parse_mode': 'Markdown',
            }, files={'document': f}, timeout=60, verify=False)
        if resp.ok:
            print(f"  [OK] PDF sent to Telegram: {Path(pdf_path).name}")
            return True
        else:
            print(f"  [X] Telegram PDF error: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"  [X] PDF send failed: {str(e)[:100]}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE — generate PDF from today's daily brief
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import sys
    from pathlib import Path

    root = Path(__file__).parent.parent
    today = date_mod.today().strftime('%y%m%d')

    # Find daily brief
    brief_path = root / 'output' / f'daily_brief_{today}.md'
    if not brief_path.exists():
        # Try most recent
        briefs = sorted(root.glob('output/daily_brief_*.md'))
        if briefs:
            brief_path = briefs[-1]
        else:
            print("[X] No daily brief found in output/")
            sys.exit(1)

    print(f"  [i] Reading: {brief_path.name}")
    md = brief_path.read_text(encoding='utf-8')

    out_path = root / 'output' / f'AlphaAbsolute_DailyPulse_{today}.pdf'
    date_label = date_mod.today().strftime('%d %B %Y')

    print(f"  [i] Generating PDF...")
    markdown_to_pdf(md, str(out_path), date_label)
    print(f"  [OK] Done: {out_path}")
