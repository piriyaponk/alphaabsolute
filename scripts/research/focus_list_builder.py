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
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

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
    OUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved → {OUT_PATH}")
    print(f"  Total: {len(th_pulse)} TH-PULSE + {len(th_set100)} TH-SET100 + {len(dr_rows)} DR + {len(pulse_rows)} PULSE-US = {len(all_rows)}")

    return output


if __name__ == "__main__":
    result = run()
    print(f"\nSummary: {result['summary']}")
