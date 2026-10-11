"""
focus_list_builder.py — Daily Strategist Universe Builder

Builds a combined focus list for AI strategist agents:
  - TH section:  ≥15 Thai stocks  (PULSE-TH top signals → fill with RS-ranked)
  - DR section:  ≥10 DR picks     (PULSE-DR → AA Focus List DRs → RS fill)
  - PULSE section: US top signals with active breadth (no DR required)

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

DB_PATH    = ROOT / "data/research/pulse_signals.db"
TH_SIGNALS = ROOT / "data/research/thai_pulse/pulse_th_daily_signals.json"
US_SIGNALS = ROOT / "data/research/pulse_us/pulse_us_daily_signals.json"
DR_SIGNALS = ROOT / "data/research/dr/dr_daily_signals.json"
DR_MAP_PATH= ROOT / "data/research/dr/dr_map.json"
OUT_PATH   = ROOT / "data/research/daily_focus_list.json"

TH_MIN = 15
DR_MIN = 10
PULSE_US_MIN = 10


# ── DB helpers ────────────────────────────────────────────────────────────────

def _ensure_table(con: sqlite3.Connection):
    con.execute("""
        CREATE TABLE IF NOT EXISTS focus_list (
            date        TEXT NOT NULL,
            category    TEXT NOT NULL,   -- TH | DR | PULSE-US
            ticker      TEXT NOT NULL,   -- primary ticker (TH: BK suffix, DR: US ticker, US: US ticker)
            set_symbol  TEXT,            -- SET symbol (DR only)
            avg_h3      REAL DEFAULT 0,
            breadth     INTEGER DEFAULT 0,
            rs_pct      REAL DEFAULT 0,
            source      TEXT,            -- PULSE | AA | RS-FILL
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

def _build_th_section(today: str) -> list[dict]:
    """
    TH ≥15:
      1. PULSE-TH top signals (avg_h3 > 0), sorted by avg_h3 DESC
      2. Fill remaining slots with all pulse_scores tickers sorted by RS (rs_map)
    """
    if not TH_SIGNALS.exists():
        print("  [WARN] TH signals not found — skipping TH section")
        return []

    data = json.loads(TH_SIGNALS.read_text(encoding="utf-8"))
    pulse_scores: dict = data.get("pulse_scores", {})
    rs_map: dict = data.get("rs_map", {})  # ticker -> rs_pct (float)

    rows: list[dict] = []
    seen: set[str] = set()

    # Pass 1: PULSE signals (breadth > 0)
    pulse_ranked = sorted(
        [(t, sc) for t, sc in pulse_scores.items() if sc.get("breadth", 0) > 0],
        key=lambda x: (-x[1].get("avg_h3", 0), -x[1].get("breadth", 0))
    )
    for ticker, sc in pulse_ranked:
        if ticker in seen:
            continue
        seen.add(ticker)
        rows.append({
            "date": today, "category": "TH", "ticker": ticker,
            "set_symbol": None, "avg_h3": sc.get("avg_h3", 0),
            "breadth": sc.get("breadth", 0),
            "rs_pct": rs_map.get(ticker, 0) if isinstance(rs_map.get(ticker), (int, float)) else 0,
            "source": "PULSE",
            "top_signals": json.dumps(sc.get("top_signals", [])[:3]),
            "dr_ticker": None,
        })

    # Pass 2: RS fill to TH_MIN
    if len(rows) < TH_MIN:
        rs_ranked = sorted(
            [(t, v) for t, v in rs_map.items() if t not in seen],
            key=lambda x: -(x[1] if isinstance(x[1], (int, float)) else 0)
        )
        for ticker, rs_val in rs_ranked:
            if len(rows) >= TH_MIN:
                break
            sc = pulse_scores.get(ticker, {})
            seen.add(ticker)
            rows.append({
                "date": today, "category": "TH", "ticker": ticker,
                "set_symbol": None, "avg_h3": sc.get("avg_h3", 0),
                "breadth": sc.get("breadth", 0),
                "rs_pct": rs_val if isinstance(rs_val, (int, float)) else 0,
                "source": "RS-FILL",
                "top_signals": json.dumps(sc.get("top_signals", [])[:3]),
                "dr_ticker": None,
            })

    print(f"  TH section: {len(rows)} tickers ({sum(1 for r in rows if r['source']=='PULSE')} PULSE + {sum(1 for r in rows if r['source']=='RS-FILL')} RS-fill)")
    return rows


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

    dr_map: dict = json.loads(DR_MAP_PATH.read_text(encoding="utf-8"))["map"]
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
    PULSE-US ≥10: top active US signals (breadth > 0), excluding those already in DR section.
    """
    if not US_SIGNALS.exists():
        return []

    us_data = json.loads(US_SIGNALS.read_text(encoding="utf-8"))
    pulse_scores: dict = us_data.get("pulse_scores", {})

    ranked = sorted(
        [(t, sc) for t, sc in pulse_scores.items()
         if sc.get("breadth", 0) > 0 and t not in dr_tickers_seen],
        key=lambda x: (-x[1].get("avg_h3", 0), -x[1].get("breadth", 0))
    )

    rows = []
    for ticker, sc in ranked[:PULSE_US_MIN]:
        rows.append({
            "date": today, "category": "PULSE-US", "ticker": ticker,
            "set_symbol": None, "avg_h3": sc.get("avg_h3", 0),
            "breadth": sc.get("breadth", 0),
            "rs_pct": sc.get("rs_pct", 0),
            "source": "PULSE",
            "top_signals": json.dumps(sc.get("top_signals", [])[:3]),
            "dr_ticker": None,
        })

    print(f"  PULSE-US section: {len(rows)} tickers")
    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    today = str(date.today())
    print(f"[FocusListBuilder] Building daily strategist universe for {today}")

    th_rows    = _build_th_section(today)
    dr_rows    = _build_dr_section(today)
    dr_seen    = {r["ticker"] for r in dr_rows}
    pulse_rows = _build_pulse_us_section(today, dr_seen)

    all_rows = th_rows + dr_rows + pulse_rows

    # Write to DB
    con = sqlite3.connect(DB_PATH)
    _ensure_table(con)
    _upsert(con, all_rows)
    con.close()
    print(f"  Upserted {len(all_rows)} rows → focus_list table")

    # Write JSON — structured for strategist agent consumption
    # Labels:
    #   source:      PULSE | AA | RS-FILL | LIQUIDITY-FILL
    #   avg_h3:      PULSE forward 3-hour hit rate (%) — higher = stronger signal
    #   breadth:     number of independent PULSE signal families firing today
    #   rs_pct:      RS percentile vs universe (0-100)
    #   conviction:  derived label for strategist: HIGH / MEDIUM / WATCH
    def _conviction(avg_h3: float, breadth: int, source: str) -> str:
        if source in ("RS-FILL", "LIQUIDITY-FILL"):
            return "WATCH"
        if avg_h3 >= 70 and breadth >= 2:
            return "HIGH"
        if avg_h3 >= 60 or breadth >= 1:
            return "MEDIUM"
        return "WATCH"

    output = {
        "date": today,
        "built_at": datetime.now().isoformat(),
        "summary": {
            "th_count": len(th_rows),
            "dr_count": len(dr_rows),
            "pulse_us_count": len(pulse_rows),
            "total": len(all_rows),
        },
        "th": [
            {
                "ticker": r["ticker"],
                "label": {
                    "source": r["source"],           # PULSE | RS-FILL
                    "conviction": _conviction(r["avg_h3"], r["breadth"], r["source"]),
                    "pulse_h3_hit_rate_pct": r["avg_h3"],   # fwd 3h hit rate
                    "pulse_breadth": r["breadth"],           # signal families firing
                    "rs_pct": r["rs_pct"],                   # RS percentile
                },
                "top_signals": json.loads(r["top_signals"] or "[]"),
            }
            for r in th_rows
        ],
        "dr": [
            {
                "us_ticker": r["ticker"],
                "set_symbol": r["set_symbol"],
                "label": {
                    "source": r["source"],           # PULSE | AA | LIQUIDITY-FILL
                    "conviction": _conviction(r["avg_h3"], r["breadth"], r["source"]),
                    "pulse_h3_hit_rate_pct": r["avg_h3"],
                    "pulse_breadth": r["breadth"],
                    "rs_pct": r["rs_pct"],
                },
                "top_signals": json.loads(r["top_signals"] or "[]"),
            }
            for r in dr_rows
        ],
        "pulse_us": [
            {
                "ticker": r["ticker"],
                "label": {
                    "source": r["source"],           # PULSE
                    "conviction": _conviction(r["avg_h3"], r["breadth"], r["source"]),
                    "pulse_h3_hit_rate_pct": r["avg_h3"],
                    "pulse_breadth": r["breadth"],
                    "rs_pct": r["rs_pct"],
                },
                "top_signals": json.loads(r["top_signals"] or "[]"),
            }
            for r in pulse_rows
        ],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved → {OUT_PATH}")
    print(f"  Total: {len(th_rows)} TH + {len(dr_rows)} DR + {len(pulse_rows)} PULSE-US = {len(all_rows)}")

    return output


if __name__ == "__main__":
    result = run()
    print(f"\nSummary: {result['summary']}")
