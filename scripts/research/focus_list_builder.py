"""
focus_list_builder.py — Daily Strategist Universe Builder

Builds a combined focus list for AI strategist agents:
  - th_pulse:  PULSE-TH active signals (breadth>0), sorted by h3 DESC
  - th_set100: SET100 proxy ≥10 (top tickers by 60d avg daily value from thai_ohlcv.db)
  - DR section:  ≥10 DR picks     (PULSE-DR → AA Focus List DRs → RS fill)
  - PULSE section: US top signals with active breadth + AA-US watchlist

Output:
  data/research/daily_focus_list.json   — master file read by strategist agents
  pulse_signals.db `focus_list` table  — SQL queryable by downstream agents

Run: after thai_pulse_daily.py + dr_daily_screen.py complete
"""
import json
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "research"))  # for constants.py (PULSE-TH SSOT)
from constants import RS_LOOKBACK_DAYS  # 63 — single source of truth for TH RS window

DB_PATH      = ROOT / "data/research/pulse_signals.db"
TH_OHLCV_DB  = ROOT / "data/research/thai_ohlcv.db"
TH_SIGNALS   = ROOT / "data/research/thai_pulse/pulse_th_daily_signals.json"
US_SIGNALS   = ROOT / "data/research/pulse_us/pulse_us_daily_signals.json"
DR_SIGNALS   = ROOT / "data/research/dr/dr_daily_signals.json"
DR_MAP_PATH  = ROOT / "data/research/dr/dr_map.json"
RS_LATEST    = ROOT / "data/rs_universe/latest.json"
SHORTLIST    = ROOT / "data/research/shortlist_today.json"
OUT_PATH     = ROOT / "data/research/daily_focus_list.json"

TH_SET100_MIN = 10   # min SET100-proxy tickers
DR_MIN        = 10
PULSE_US_MIN  = 10
SET100_TOP_N  = 100  # how many top-liquidity TH tickers = "SET100 proxy"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_us_rs() -> dict:
    """Load ticker → rs_composite_pct from data/rs_universe/latest.json (universe key)."""
    if not RS_LATEST.exists():
        return {}
    try:
        data = json.loads(RS_LATEST.read_text(encoding="utf-8"))
        # latest.json has "universe": {ticker: {rs_composite_pct, rs_1m_pct, ...}}
        universe = data.get("universe", {})
        if not isinstance(universe, dict):
            return {}
        rs_map = {}
        for ticker, vals in universe.items():
            if isinstance(vals, dict):
                # prefer composite, fallback to 3m
                pct = vals.get("rs_composite_pct", vals.get("rs_pct_3m", vals.get("rs_pct", 0)))
                rs_map[ticker] = float(pct or 0)
            elif isinstance(vals, (int, float)):
                rs_map[ticker] = float(vals)
        return rs_map
    except Exception as e:
        print(f"  [WARN] rs_latest load failed: {e}")
        return {}


def _load_set100_proxy() -> list[str]:
    """
    Return top SET100_TOP_N tickers by avg daily value (volume*close) over last 60 days
    from thai_ohlcv.db — used as SET100 proxy since no official SET100 list exists.
    """
    if not TH_OHLCV_DB.exists():
        return []
    try:
        con = sqlite3.connect(TH_OHLCV_DB)
        rows = con.execute("""
            SELECT ticker, AVG(CAST(volume AS REAL) * close) AS avg_val
            FROM thai_ohlcv
            WHERE date >= date('now', '-60 days')
              AND volume IS NOT NULL AND close IS NOT NULL AND close > 0
            GROUP BY ticker
            ORDER BY avg_val DESC
            LIMIT ?
        """, (SET100_TOP_N,)).fetchall()
        con.close()
        return [r[0] for r in rows]
    except Exception as e:
        print(f"  [WARN] SET100 proxy query failed: {e}")
        return []


def _compute_th_rs_full(tickers: list, as_of_date: str) -> dict:
    """Compute 63-day RS percentile from thai_ohlcv.db for any ticker list.

    Ranks against FULL universe (all tickers in thai_ohlcv), returns only
    the requested tickers.  Uses RS_LOOKBACK_DAYS from constants.py (63d).

    Root cause fix (2026-10-16): focus_list_builder used rs_map from
    pulse_th_daily_signals.json which only contains the 25 PULSE-TH screen
    tickers.  All other SET100-proxy tickers got rs_pct=0.  This function
    fills the gap by computing RS directly from ohlcv for any missing ticker.
    """
    if not TH_OHLCV_DB.exists() or not tickers:
        return {}
    try:
        import numpy as np
        import pandas as pd
        con = sqlite3.connect(str(TH_OHLCV_DB))
        rows = con.execute(
            "SELECT ticker, date, close FROM thai_ohlcv "
            "WHERE date <= ? AND close > 0 ORDER BY ticker, date",
            (as_of_date,),
        ).fetchall()
        con.close()
        if not rows:
            return {}
        df = pd.DataFrame(rows, columns=["ticker", "date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        returns = {}
        for tkr, grp in df.groupby("ticker"):
            grp = grp.sort_values("date")
            if len(grp) < 10:
                continue
            latest  = grp["close"].iloc[-1]
            lookback = grp.iloc[max(0, len(grp) - RS_LOOKBACK_DAYS)]["close"]
            if lookback > 0:
                returns[tkr] = (latest / lookback - 1) * 100
        if not returns:
            return {}
        all_t  = list(returns.keys())
        vals   = list(returns.values())
        ranks  = np.argsort(np.argsort(vals)) + 1  # 1-based rank
        n      = len(vals)
        universe_rs = {t: round(float(r / n * 100), 1) for t, r in zip(all_t, ranks)}
        result = {t: universe_rs[t] for t in tickers if t in universe_rs}
        n_filled = len(result)
        if n_filled:
            print(f"  [rs_fill] Computed ohlcv RS for {n_filled}/{len(tickers)} SET100 tickers")
        return result
    except Exception as e:
        print(f"  [WARN] _compute_th_rs_full failed: {e}")
        return {}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _ensure_table(con: sqlite3.Connection):
    con.execute("""
        CREATE TABLE IF NOT EXISTS focus_list (
            date        TEXT NOT NULL,
            category    TEXT NOT NULL,   -- TH-PULSE | TH-SET100 | DR | PULSE-US
            ticker      TEXT NOT NULL,   -- primary ticker (TH: BK suffix, DR: US ticker, US: US ticker)
            set_symbol  TEXT,            -- SET symbol (DR only)
            avg_h3      REAL DEFAULT 0,
            breadth     INTEGER DEFAULT 0,
            rs_pct      REAL DEFAULT 0,
            source      TEXT,            -- PULSE | AA | RS-FILL | LIQUIDITY-FILL
            top_signals TEXT,
            dr_ticker   TEXT,            -- alias for set_symbol (for backwards compat)
            created_at  TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (date, category, ticker)
        )
    """)
    # Purge stale category='TH' rows written by an old version of this script
    con.execute("DELETE FROM focus_list WHERE category = 'TH'")
    # Prevent unbounded growth — keep only last 30 days
    con.execute("DELETE FROM focus_list WHERE date < date('now', '-30 days')")
    con.commit()


def _upsert(con: sqlite3.Connection, rows: list[dict]):
    if not rows:
        return
    con.executemany("""
        INSERT INTO focus_list (date, category, ticker, set_symbol, avg_h3, breadth, rs_pct, source, top_signals, dr_ticker)
        VALUES (:date, :category, :ticker, :set_symbol, :avg_h3, :breadth, :rs_pct, :source, :top_signals, :dr_ticker)
        ON CONFLICT(date, category, ticker) DO UPDATE SET
            set_symbol  = excluded.set_symbol,
            avg_h3      = excluded.avg_h3,
            breadth     = excluded.breadth,
            rs_pct      = excluded.rs_pct,
            source      = excluded.source,
            top_signals = excluded.top_signals,
            dr_ticker   = excluded.dr_ticker
    """, rows)
    con.commit()


# ── Section builders ──────────────────────────────────────────────────────────

def _build_th_section(today: str) -> tuple[list[dict], list[dict]]:
    """
    Returns (th_pulse, th_set100):
      th_pulse  — PULSE-TH active signals (breadth>0) sorted by avg_h3 DESC
      th_set100 — SET100 proxy ≥TH_SET100_MIN: top-liquidity TH tickers that are NOT
                  already in th_pulse (fills with RS-ranked if needed)
    """
    pulse_scores: dict = {}
    rs_map: dict = {}

    if TH_SIGNALS.exists():
        data = json.loads(TH_SIGNALS.read_text(encoding="utf-8"))
        pulse_scores = data.get("pulse_scores", {})
        rs_map = data.get("rs_map", {})
    else:
        print("  [WARN] TH signals not found — TH sections will be empty")

    set100 = _load_set100_proxy()  # ordered list by liquidity DESC

    # Augment rs_map with full-universe RS for SET100 tickers missing from the
    # PULSE-TH screen.  pulse_th_daily_signals.json rs_map only contains the
    # 25 RS-filtered screen tickers.  Any SET100 ticker not in that screen gets
    # rs_pct=0 without this fill — the bug reported 2026-10-13 and 2026-10-16.
    missing_rs = [t for t in set100 if t not in rs_map]
    if missing_rs:
        # as_of_date = max date from the TH signals file (same as ohlcv max)
        _signals_date = ""
        if TH_SIGNALS.exists():
            try:
                _signals_date = json.loads(TH_SIGNALS.read_text(encoding="utf-8")).get("date", "")
            except Exception:
                pass
        if not _signals_date:
            from datetime import date as _d
            _signals_date = str(_d.today())
        filled = _compute_th_rs_full(missing_rs, _signals_date)
        rs_map.update(filled)

    pulse_seen: set[str] = set()

    # ── Tier 1: th_pulse ───────────────────────────────────────────────────────
    th_pulse: list[dict] = []
    pulse_ranked = sorted(
        [(t, sc) for t, sc in pulse_scores.items() if sc.get("breadth", 0) > 0],
        key=lambda x: (-x[1].get("avg_h3", 0), -x[1].get("breadth", 0))
    )
    for ticker, sc in pulse_ranked:
        pulse_seen.add(ticker)
        rs_val = rs_map.get(ticker, 0)
        th_pulse.append({
            "date": today, "category": "TH-PULSE", "ticker": ticker,
            "set_symbol": None,
            "avg_h3": sc.get("avg_h3", 0),
            "breadth": sc.get("breadth", 0),
            "rs_pct": rs_val if isinstance(rs_val, (int, float)) else 0,
            "source": "PULSE",
            "top_signals": json.dumps(sc.get("top_signals", [])[:3]),
            "dr_ticker": None,
        })

    # ── Tier 2: th_set100 ──────────────────────────────────────────────────────
    th_set100: list[dict] = []
    set100_seen: set[str] = set(pulse_seen)  # don't duplicate PULSE tickers

    # Pass 1: SET100 proxy tickers (already ranked by liquidity)
    for ticker in set100:
        if ticker in set100_seen:
            continue
        set100_seen.add(ticker)
        sc = pulse_scores.get(ticker, {})
        rs_val = rs_map.get(ticker, 0)
        th_set100.append({
            "date": today, "category": "TH-SET100", "ticker": ticker,
            "set_symbol": None,
            "avg_h3": sc.get("avg_h3", 0),
            "breadth": sc.get("breadth", 0),
            "rs_pct": rs_val if isinstance(rs_val, (int, float)) else 0,
            "source": "PULSE" if sc.get("breadth", 0) > 0 else "AA",
            "top_signals": json.dumps(sc.get("top_signals", [])[:3]),
            "dr_ticker": None,
        })

    # Pass 2: RS fill if SET100 proxy gives < TH_SET100_MIN
    if len(th_set100) < TH_SET100_MIN:
        rs_ranked = sorted(
            [(t, v) for t, v in rs_map.items() if t not in set100_seen],
            key=lambda x: -(x[1] if isinstance(x[1], (int, float)) else 0)
        )
        for ticker, rs_val in rs_ranked:
            if len(th_set100) >= TH_SET100_MIN:
                break
            set100_seen.add(ticker)
            sc = pulse_scores.get(ticker, {})
            th_set100.append({
                "date": today, "category": "TH-SET100", "ticker": ticker,
                "set_symbol": None,
                "avg_h3": sc.get("avg_h3", 0),
                "breadth": sc.get("breadth", 0),
                "rs_pct": rs_val if isinstance(rs_val, (int, float)) else 0,
                "source": "RS-FILL",
                "top_signals": json.dumps(sc.get("top_signals", [])[:3]),
                "dr_ticker": None,
            })

    print(f"  TH-PULSE: {len(th_pulse)} tickers (all PULSE active)")
    s1 = sum(1 for r in th_set100 if r["source"] == "PULSE")
    s2 = sum(1 for r in th_set100 if r["source"] == "AA")
    s3 = sum(1 for r in th_set100 if r["source"] == "RS-FILL")
    print(f"  TH-SET100: {len(th_set100)} tickers ({s1} PULSE + {s2} AA + {s3} RS-fill)")
    return th_pulse, th_set100


def _build_dr_section(today: str) -> list[dict]:
    """
    DR ≥10:
      1. PULSE-DR top picks (dr_daily_signals.json), sorted by h3_score DESC
      2. Fill with AA Focus List stocks that have DRs (shortlist watchlist + us_candidates)
      3. Fill remaining with DR map tickers sorted by val_thb
    """
    if not DR_MAP_PATH.exists():
        print("  [WARN] DR map not found — skipping DR section")
        return []

    try:
        dr_map: dict = json.loads(DR_MAP_PATH.read_text(encoding="utf-8"))["map"]
    except Exception as e:
        print(f"  [WARN] DR map load failed: {e} — skipping DR section")
        return []
    rows: list[dict] = []
    seen: set[str] = set()  # keyed by US ticker

    # Pass 1: PULSE-DR picks
    if DR_SIGNALS.exists():
        dr_data = json.loads(DR_SIGNALS.read_text(encoding="utf-8"))
        for p in sorted(dr_data.get("picks", []), key=lambda x: (-x.get("h3_score", 0), -x.get("breadth", 0))):
            us_ticker = p["us_ticker"]
            if us_ticker in seen:
                continue
            seen.add(us_ticker)
            rows.append({
                "date": today, "category": "DR", "ticker": us_ticker,
                "set_symbol": p.get("set_symbol"),
                "avg_h3": p.get("h3_score", 0),
                "breadth": p.get("breadth", 0),
                "rs_pct": 0,
                "source": "PULSE",
                "top_signals": json.dumps(p.get("top_signal_labels", [])[:3]),
                "dr_ticker": p.get("set_symbol"),
            })

    # Pass 2: US candidates from shortlist that have DRs
    shortlist_path = ROOT / "data/research/shortlist_today.json"
    if shortlist_path.exists() and len(rows) < DR_MIN:
        sl = json.loads(shortlist_path.read_text(encoding="utf-8"))
        for c in sl.get("us_candidates", []) + sl.get("watchlist", []):
            if len(rows) >= DR_MIN:
                break
            us_ticker = c.get("ticker", "") if isinstance(c, dict) else str(c)
            if us_ticker in seen or us_ticker not in dr_map:
                continue
            seen.add(us_ticker)
            dr_info = dr_map[us_ticker]
            rows.append({
                "date": today, "category": "DR", "ticker": us_ticker,
                "set_symbol": dr_info["set_symbol"],
                "avg_h3": c.get("avg_h3", 0) if isinstance(c, dict) else 0,
                "breadth": c.get("breadth", 0) if isinstance(c, dict) else 0,
                "rs_pct": c.get("rs_pct", 0) if isinstance(c, dict) else 0,
                "source": "AA",
                "top_signals": json.dumps((c.get("top_signals", []) if isinstance(c, dict) else [])[:3]),
                "dr_ticker": dr_info["set_symbol"],
            })

    # Pass 3: Fill remaining from DR map sorted by liquidity
    if len(rows) < DR_MIN:
        dr_by_val = sorted(
            [(t, info) for t, info in dr_map.items() if t not in seen],
            key=lambda x: -x[1].get("val_thb", 0)
        )
        us_signals = {}
        if US_SIGNALS.exists():
            us_data = json.loads(US_SIGNALS.read_text(encoding="utf-8"))
            us_signals = us_data.get("pulse_scores", {})

        for us_ticker, dr_info in dr_by_val:
            if len(rows) >= DR_MIN:
                break
            seen.add(us_ticker)
            sc = us_signals.get(us_ticker, {})
            rows.append({
                "date": today, "category": "DR", "ticker": us_ticker,
                "set_symbol": dr_info["set_symbol"],
                "avg_h3": sc.get("avg_h3", 0),
                "breadth": sc.get("breadth", 0),
                "rs_pct": 0,
                "source": "LIQUIDITY-FILL",
                "top_signals": json.dumps(sc.get("top_signals", [])[:3]),
                "dr_ticker": dr_info["set_symbol"],
            })

    print(f"  DR section: {len(rows)} tickers ({sum(1 for r in rows if r['source']=='PULSE')} PULSE + {sum(1 for r in rows if r['source']=='AA')} AA + {sum(1 for r in rows if r['source']=='LIQUIDITY-FILL')} liq-fill)")
    return rows


def _build_pulse_us_section(today: str, dr_tickers_seen: set[str]) -> list[dict]:
    """
    PULSE-US ≥10:
      1. Top active PULSE-US signals (breadth>0), excl DR section tickers
      2. AA-US: shortlist_today.json watchlist (source="AA"), if not already included
      rs_pct pulled from data/rs_universe/latest.json (pulse_scores has no rs_pct)
    """
    rs_index = _load_us_rs()  # ticker → rs_pct_3m

    pulse_scores: dict = {}
    if US_SIGNALS.exists():
        us_data = json.loads(US_SIGNALS.read_text(encoding="utf-8"))
        pulse_scores = us_data.get("pulse_scores", {})

    seen: set[str] = set(dr_tickers_seen)
    rows: list[dict] = []

    # Pass 1: PULSE active signals
    ranked = sorted(
        [(t, sc) for t, sc in pulse_scores.items()
         if sc.get("breadth", 0) > 0 and t not in seen],
        key=lambda x: (-x[1].get("avg_h3", 0), -x[1].get("breadth", 0))
    )
    for ticker, sc in ranked[:PULSE_US_MIN]:
        seen.add(ticker)
        rows.append({
            "date": today, "category": "PULSE-US", "ticker": ticker,
            "set_symbol": None,
            "avg_h3": sc.get("avg_h3", 0),
            "breadth": sc.get("breadth", 0),
            "rs_pct": rs_index.get(ticker, 0),
            "source": "PULSE",
            "top_signals": json.dumps(sc.get("top_signals", [])[:3]),
            "dr_ticker": None,
        })

    # Pass 2: AA-US from shortlist watchlist (not already in rows)
    if SHORTLIST.exists():
        sl = json.loads(SHORTLIST.read_text(encoding="utf-8"))
        aa_tickers = []
        for item in sl.get("watchlist", []):
            t = item.get("ticker", item) if isinstance(item, dict) else str(item)
            aa_tickers.append(t.upper())
        for item in sl.get("us_candidates", []):
            t = item.get("ticker", item) if isinstance(item, dict) else str(item)
            if t not in aa_tickers:
                aa_tickers.append(t.upper())

        for ticker in aa_tickers:
            if ticker in seen:
                continue
            seen.add(ticker)
            sc = pulse_scores.get(ticker, {})
            rows.append({
                "date": today, "category": "PULSE-US", "ticker": ticker,
                "set_symbol": None,
                "avg_h3": sc.get("avg_h3", 0),
                "breadth": sc.get("breadth", 0),
                "rs_pct": rs_index.get(ticker, 0),
                "source": "AA",
                "top_signals": json.dumps(sc.get("top_signals", [])[:3]),
                "dr_ticker": None,
            })

    n_pulse = sum(1 for r in rows if r["source"] == "PULSE")
    n_aa    = sum(1 for r in rows if r["source"] == "AA")
    print(f"  PULSE-US section: {len(rows)} tickers ({n_pulse} PULSE + {n_aa} AA)")
    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    today = str(date.today())
    print(f"[FocusListBuilder] Building daily strategist universe for {today}")

    th_pulse, th_set100 = _build_th_section(today)
    dr_rows              = _build_dr_section(today)
    dr_seen              = {r["ticker"] for r in dr_rows}
    pulse_rows           = _build_pulse_us_section(today, dr_seen)

    all_rows = th_pulse + th_set100 + dr_rows + pulse_rows

    # Write to DB (non-fatal — JSON must still be written even if DB is locked)
    try:
        con = sqlite3.connect(DB_PATH, timeout=10)
        _ensure_table(con)
        _upsert(con, all_rows)
        con.close()
        print(f"  Upserted {len(all_rows)} rows → focus_list table")
    except Exception as e:
        print(f"  [WARN] DB upsert failed (DB may be locked): {e} — continuing to JSON write")

    # ── Label helper ──────────────────────────────────────────────────────────
    # source:     PULSE | AA | RS-FILL | LIQUIDITY-FILL
    # avg_h3:     PULSE forward 3h hit rate (%) — higher = stronger
    # breadth:    independent PULSE signal families firing today
    # rs_pct:     RS percentile vs universe (0-100)
    # conviction: HIGH / MEDIUM / WATCH for strategist
    def _conviction(avg_h3: float, breadth: int, source: str) -> str:
        if source in ("RS-FILL", "LIQUIDITY-FILL"):
            return "WATCH"
        if avg_h3 >= 70 and breadth >= 2:
            return "HIGH"
        if avg_h3 >= 60 or breadth >= 1:
            return "MEDIUM"
        return "WATCH"

    def _entry(r: dict, key: str = "ticker") -> dict:
        return {
            key: r["ticker"],
            "label": {
                "source": r["source"],
                "conviction": _conviction(r["avg_h3"], r["breadth"], r["source"]),
                "pulse_h3_hit_rate_pct": r["avg_h3"],
                "pulse_breadth": r["breadth"],
                "rs_pct": r["rs_pct"],
            },
            "top_signals": json.loads(r["top_signals"] or "[]"),
        }

    output = {
        "date": today,
        "built_at": datetime.now().isoformat(),
        "summary": {
            "th_pulse_count": len(th_pulse),
            "th_set100_count": len(th_set100),
            "dr_count": len(dr_rows),
            "pulse_us_count": len(pulse_rows),
            "total": len(all_rows),
        },
        # Tier 1 TH: active PULSE signals only — best alpha triggers
        "th_pulse": [_entry(r) for r in th_pulse],
        # Tier 2 TH: SET100-proxy institutional universe (≥10)
        # source=PULSE → active signal today | source=AA → no active signal but SET100 member
        "th_set100": [_entry(r) for r in th_set100],
        # DR: US stocks with Thai SET DRs, ranked by PULSE signal quality
        "dr": [
            {
                "us_ticker": r["ticker"],
                "set_symbol": r["set_symbol"],
                "label": {
                    "source": r["source"],
                    "conviction": _conviction(r["avg_h3"], r["breadth"], r["source"]),
                    "pulse_h3_hit_rate_pct": r["avg_h3"],
                    "pulse_breadth": r["breadth"],
                    "rs_pct": r["rs_pct"],
                },
                "top_signals": json.loads(r["top_signals"] or "[]"),
            }
            for r in dr_rows
        ],
        # PULSE-US: active US signals + AA-screened watchlist
        # source=PULSE → active breadth signal | source=AA → AA screened (may have no active signal)
        "pulse_us": [_entry(r) for r in pulse_rows],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to .tmp first, then os.replace() so downstream agents
    # never read a partially-written file if this process is killed mid-write.
    _tmp = OUT_PATH.with_suffix(".tmp")
    _tmp.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(_tmp, OUT_PATH)
    print(f"  Saved → {OUT_PATH}")
    print(f"  Total: {len(th_pulse)} TH-PULSE + {len(th_set100)} TH-SET100 + {len(dr_rows)} DR + {len(pulse_rows)} PULSE-US = {len(all_rows)}")

    return output


def _send_telegram(text: str) -> bool:
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        # Try .env file
        env_path = ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("TELEGRAM_BOT_TOKEN=") and not token:
                    token = line.split("=", 1)[1].strip()
                elif line.startswith("TELEGRAM_CHAT_ID=") and not chat_id:
                    chat_id = line.split("=", 1)[1].strip()
    if not token or not chat_id:
        print("  [WARN] TELEGRAM creds not found — skipping push")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, json=payload, timeout=10, verify=False)
        if resp.status_code == 400:
            payload.pop("parse_mode")
            resp = requests.post(url, json=payload, timeout=10, verify=False)
        resp.raise_for_status()
        print(f"  [OK] Telegram sent ({len(text)} chars)")
        return True
    except Exception as e:
        print(f"  [ERROR] Telegram failed: {e}")
        return False


def format_telegram(output: dict) -> str:
    today = output["date"]
    s = output["summary"]
    lines = [f"📋 *Focus List* [{today}]"]
    lines.append(f"Total: {s['total']} | TH-PULSE:{s['th_pulse_count']} SET100:{s['th_set100_count']} DR:{s['dr_count']} US:{s['pulse_us_count']}")
    lines.append("")

    # TH-PULSE section — up to 5
    th_pulse = output.get("th_pulse", [])
    if th_pulse:
        lines.append(f"🇹🇭 *TH PULSE* ({len(th_pulse)} active signals)")
        for e in th_pulse[:5]:
            lbl = e["label"]
            cv  = "🔴" if lbl["conviction"] == "HIGH" else "🟡"
            sig = e["top_signals"][0] if e.get("top_signals") else ""
            lines.append(f"{cv} {e['ticker']} h3={lbl['pulse_h3_hit_rate_pct']:.0f}% brd={lbl['pulse_breadth']} rs={lbl['rs_pct']:.0f}")
            if sig:
                lines.append(f"   └ {sig}")
        if len(th_pulse) > 5:
            lines.append(f"   +{len(th_pulse)-5} more")
    else:
        lines.append("🇹🇭 *TH PULSE*: no active signals today")
    lines.append("")

    # TH-SET100 — show 10 tickers (PULSE-active first, then by liquidity)
    th_set = output.get("th_set100", [])
    pulse_in_set = [e for e in th_set if e["label"]["source"] == "PULSE"]
    non_pulse    = [e for e in th_set if e["label"]["source"] != "PULSE"]
    show10 = (pulse_in_set + non_pulse)[:10]
    if show10:
        lines.append(f"🇹🇭 *SET100* top10 ({len(pulse_in_set)} with active signal)")
        for e in show10:
            lbl = e["label"]
            sym = e["ticker"].replace(".BK", "")
            if lbl["source"] == "PULSE":
                lines.append(f"🟡 {sym} h3={lbl['pulse_h3_hit_rate_pct']:.0f}% brd={lbl['pulse_breadth']}")
            else:
                lines.append(f"⚪ {sym}")
    lines.append("")
    lines.append("")

    # DR section
    dr = output.get("dr", [])
    if dr:
        lines.append("🔗 *DR* (US→SET, top 5)")
        for e in dr[:5]:
            lbl = e["label"]
            cv  = "🔴" if lbl["conviction"] == "HIGH" else ("🟡" if lbl["conviction"] == "MEDIUM" else "⚪")
            set_sym = e.get("set_symbol") or "-"
            lines.append(f"{cv} ${e['us_ticker']} → {set_sym} h3={lbl['pulse_h3_hit_rate_pct']:.0f}% brd={lbl['pulse_breadth']}")
    lines.append("")

    # PULSE-US — active signals only
    pulse_us = [e for e in output.get("pulse_us", []) if e["label"]["source"] == "PULSE"]
    if pulse_us:
        lines.append(f"🇺🇸 *PULSE-US* ({len(pulse_us)} active)")
        for e in pulse_us[:5]:
            lbl = e["label"]
            cv  = "🔴" if lbl["conviction"] == "HIGH" else "🟡"
            lines.append(f"{cv} ${e['ticker']} h3={lbl['pulse_h3_hit_rate_pct']:.0f}% brd={lbl['pulse_breadth']} rs={lbl['rs_pct']:.0f}")
        if len(pulse_us) > 5:
            more = " ".join(f"${e['ticker']}" for e in pulse_us[5:])
            lines.append(f"   +more: {more}")

    return "\n".join(lines)


if __name__ == "__main__":
    result = run()
    print(f"\nSummary: {result['summary']}")
    msg = format_telegram(result)
    print("\n--- Telegram preview ---")
    print(msg)
    print("------------------------")
    _send_telegram(msg)
