"""
pulse_db.py — Shared SQLite backend for PULSE-TH and PULSE-US daily signals
=============================================================================
Public API:
  init_db(db_path)                    — CREATE tables + indexes
  upsert_pulse_signals(rows, db_path) — INSERT OR REPLACE list of dicts
  write_shortlist_json(out_path)      — read DB for today → shortlist_today.json
  purge_old_records(db_path, days=90) — DELETE rows older than N days (weekly)

Schema (pulse_signals table):
  date, system, ticker — PRIMARY KEY
  breadth, breadth_pct, avg_h3, n_signals_fired — PULSE scores
  rs_pct, regime, theme — context
  adtv_thb, adtv_usd, mktcap — quality gate inputs
  passes_quality — 1 if passes quality gate
  dr_ticker — Thai DR equivalent (from dr_map.json), NULL if none
  top_signals — JSON array of top-3 signal labels
  thesis_hint — 1-sentence natural language hint
  created_at — UTC timestamp

Usage:
  from scripts.research.pulse_db import init_db, upsert_pulse_signals, write_shortlist_json
"""

import json
import sqlite3
from datetime import datetime, date as _date
from pathlib import Path
from typing import Optional

ROOT    = Path(__file__).resolve().parents[2]
_DB_DEFAULT = ROOT / "data" / "research" / "pulse_signals.db"
_DR_MAP_PATH = ROOT / "data" / "research" / "dr_map.json"
_SHORTLIST_DEFAULT = ROOT / "data" / "research" / "shortlist_today.json"
_MARKET_HEALTH = ROOT / "data" / "regime" / "market_health.json"


# ── Schema ───────────────────────────────────────────────────────────────────
_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS pulse_signals (
    date             TEXT    NOT NULL,
    system           TEXT    NOT NULL,
    ticker           TEXT    NOT NULL,
    breadth          INTEGER NOT NULL DEFAULT 0,
    breadth_pct      REAL    NOT NULL DEFAULT 0.0,
    avg_h3           REAL    NOT NULL DEFAULT 0.0,
    n_signals_fired  INTEGER NOT NULL DEFAULT 0,
    rs_pct           REAL,
    regime           TEXT,
    theme            TEXT,
    adtv_thb         REAL,
    adtv_usd         REAL,
    mktcap           REAL,
    passes_quality   INTEGER NOT NULL DEFAULT 0,
    dr_ticker        TEXT,
    top_signals      TEXT,
    thesis_hint      TEXT,
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (date, system, ticker)
);

CREATE INDEX IF NOT EXISTS idx_ps_date_system
    ON pulse_signals (date, system);

CREATE INDEX IF NOT EXISTS idx_ps_date_system_h3
    ON pulse_signals (date, system, avg_h3 DESC);
"""


def _get_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    p = Path(db_path) if db_path else _DB_DEFAULT
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Create tables and indexes if they don't exist."""
    conn = _get_conn(db_path)
    conn.executescript(_CREATE_SQL)
    conn.commit()
    conn.close()


# ── DR Map ───────────────────────────────────────────────────────────────────
def _load_dr_map() -> dict:
    if _DR_MAP_PATH.exists():
        return json.loads(_DR_MAP_PATH.read_text()).get("mappings", {})
    return {}


def _get_regime() -> str:
    if _MARKET_HEALTH.exists():
        try:
            return json.loads(_MARKET_HEALTH.read_text()).get("regime", "")
        except Exception:
            pass
    return ""


# ── Quality Gate ─────────────────────────────────────────────────────────────
def _apply_quality_gate(row: dict) -> int:
    """Return 1 if row passes quality gate, 0 otherwise.
    Gate is permissive (passes_quality=0 = show but CIO applies judgment)
    until full ADTV/mktcap data is wired.
    """
    sys = row.get("system", "")
    if sys == "TH":
        adtv = row.get("adtv_thb")
        mktcap = row.get("mktcap")
        # Require ADTV >= 50M THB and mktcap >= 3B THB if data present
        if adtv is not None and mktcap is not None:
            return 1 if (adtv >= 50_000_000 and mktcap >= 3_000_000_000) else 0
        # Data not yet wired — default pass (CIO judgment)
        return 1
    elif sys == "US":
        adtv = row.get("adtv_usd")
        mktcap = row.get("mktcap")
        rs_pct = row.get("rs_pct", 0) or 0
        if adtv is not None and mktcap is not None:
            return 1 if (adtv >= 50_000_000 and mktcap >= 2_000_000_000) else 0
        # Fallback: pass if RS > 50
        return 1 if rs_pct > 50 else 0
    return 0


# ── Upsert ───────────────────────────────────────────────────────────────────
def upsert_pulse_signals(rows: list[dict], db_path: Optional[Path] = None) -> int:
    """INSERT OR REPLACE signal rows.
    Each dict must have: date, system, ticker, breadth, breadth_pct, avg_h3, n_signals_fired
    Optional: rs_pct, theme, adtv_thb, adtv_usd, mktcap, top_signals (list), thesis_hint
    Returns number of rows written.
    """
    if not rows:
        return 0
    init_db(db_path)
    conn = _get_conn(db_path)

    dr_map = _load_dr_map()
    regime = _get_regime()

    insert_rows = []
    for r in rows:
        ticker = r.get("ticker", "")
        system = r.get("system", "")
        dr_ticker = dr_map.get(ticker, {}).get("dr_ticker") if system == "US" else None
        top_sigs = r.get("top_signals", [])
        passes = _apply_quality_gate(r)
        insert_rows.append((
            str(r.get("date", ""))[:10],
            system,
            ticker,
            int(r.get("breadth", 0)),
            float(r.get("breadth_pct", 0.0)),
            float(r.get("avg_h3", 0.0)),
            int(r.get("n_signals_fired", 0)),
            r.get("rs_pct"),
            r.get("regime") or regime or None,
            r.get("theme"),
            r.get("adtv_thb"),
            r.get("adtv_usd"),
            r.get("mktcap"),
            passes,
            dr_ticker,
            json.dumps(top_sigs) if top_sigs else None,
            r.get("thesis_hint"),
        ))

    conn.executemany("""
        INSERT OR REPLACE INTO pulse_signals
        (date, system, ticker, breadth, breadth_pct, avg_h3, n_signals_fired,
         rs_pct, regime, theme, adtv_thb, adtv_usd, mktcap, passes_quality,
         dr_ticker, top_signals, thesis_hint)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, insert_rows)
    conn.commit()
    conn.close()
    return len(insert_rows)


# ── Write shortlist_today.json ────────────────────────────────────────────────
def write_shortlist_json(
    today: Optional[str] = None,
    out_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
    meta: Optional[dict] = None,
) -> dict:
    """Read today's pulse_signals and write shortlist_today.json.
    Returns the shortlist dict.
    """
    td = today or str(_date.today())
    out = Path(out_path) if out_path else _SHORTLIST_DEFAULT
    out.parent.mkdir(parents=True, exist_ok=True)

    init_db(db_path)
    conn = _get_conn(db_path)

    rows = conn.execute("""
        SELECT system, ticker, breadth, breadth_pct, avg_h3, n_signals_fired,
               rs_pct, adtv_thb, adtv_usd, mktcap, passes_quality, dr_ticker,
               top_signals, thesis_hint, regime, theme
        FROM pulse_signals
        WHERE date = ?
        ORDER BY avg_h3 DESC
    """, (td,)).fetchall()
    conn.close()

    th_candidates = []
    us_candidates = []
    watchlist = []

    for row in rows:
        (system, ticker, breadth, breadth_pct, avg_h3, n_sigs,
         rs_pct, adtv_thb, adtv_usd, mktcap, passes_q, dr_ticker,
         top_sigs_json, thesis_hint, regime, theme) = row

        top_sigs = json.loads(top_sigs_json) if top_sigs_json else []
        entry = {
            "ticker":         ticker,
            "system":         system,
            "avg_h3":         avg_h3,
            "breadth":        breadth,
            "breadth_pct":    breadth_pct,
            "n_signals_fired": n_sigs,
            "rs_pct":         rs_pct,
            "passes_quality": bool(passes_q),
            "dr_ticker":      dr_ticker,
            "theme":          theme,
            "top_signals":    top_sigs,
            "thesis_hint":    thesis_hint,
        }
        if system == "TH":
            entry["adtv_thb"] = adtv_thb
            entry["mktcap_thb"] = mktcap
        else:
            entry["adtv_usd"] = adtv_usd
            entry["mktcap_usd"] = mktcap

        if passes_q and avg_h3 > 0:
            if system == "TH":
                th_candidates.append(entry)
            else:
                us_candidates.append(entry)
        else:
            watchlist.append(entry)

    weekly_mode = _date.fromisoformat(td).weekday() == 0  # Monday
    regime_now = _get_regime()

    shortlist = {
        "date":           td,
        "weekly_mode":    weekly_mode,
        "regime":         regime_now,
        "th_candidates":  th_candidates,
        "us_candidates":  us_candidates,
        "watchlist":      watchlist,
        "meta":           meta or {},
    }

    out.write_text(json.dumps(shortlist, indent=2, default=str))
    print(f"[shortlist] {td}: TH={len(th_candidates)}, US={len(us_candidates)}, "
          f"watchlist={len(watchlist)} → {out}")
    return shortlist


# ── Purge old records ─────────────────────────────────────────────────────────
def purge_old_records(db_path: Optional[Path] = None, days: int = 90) -> int:
    """Delete records older than N days. Run weekly (Sunday runner)."""
    init_db(db_path)
    conn = _get_conn(db_path)
    cur = conn.execute(
        "DELETE FROM pulse_signals WHERE date < date('now', ?)",
        (f"-{days} days",)
    )
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted:
        print(f"[purge] Deleted {deleted} rows older than {days} days")
    return deleted


if __name__ == "__main__":
    init_db()
    print(f"pulse_signals.db initialized at {_DB_DEFAULT}")
