"""
AlphaAbsolute — OHLCV Persistent Store (SQLite)
================================================
แทนที่การเก็บข้อมูลใน JSON files ที่กระจาย 1,700+ ไฟล์
ด้วย SQLite database ไฟล์เดียว — ไม่ expire, backup ง่าย, query เร็ว

Features:
- ข้อมูลไม่มีวันหาย: SQLite ไม่มี MAX_AGE_HRS — เก็บไว้ตลอด
- อัปเดตแบบ append: ได้ข้อมูลใหม่เพิ่มเข้าไป ข้อมูลเก่ายังอยู่
- JSON cache ยังคงอยู่: ทำงานร่วมกัน, SQLite เป็น source of truth
- Backup: copy data/ohlcv.db ไฟล์เดียว = ได้ข้อมูลทั้งหมด

Usage:
    from scripts.utils.ohlcv_store import OHLCVStore
    store = OHLCVStore()

    # อ่าน
    closes = store.get_closes("NVDA", days=252)

    # เขียน
    store.upsert("NVDA", dates, closes, volumes, source="polygon")

    # สถิติ
    store.summary()

Migrate จาก JSON cache:
    python scripts/utils/ohlcv_store.py --migrate
"""

from __future__ import annotations
import json
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR  = Path(__file__).resolve().parents[2]
DB_PATH   = BASE_DIR / "data" / "ohlcv.db"
CACHE_DIR = BASE_DIR / "data" / "ohlcv_cache"


# ─── Schema ────────────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS ohlcv (
    ticker      TEXT    NOT NULL,
    date        TEXT    NOT NULL,   -- YYYY-MM-DD
    close       REAL    NOT NULL,
    volume      INTEGER,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS ticker_meta (
    ticker          TEXT PRIMARY KEY,
    first_date      TEXT,           -- oldest bar in DB
    last_date       TEXT,           -- newest bar in DB
    n_bars          INTEGER,
    source          TEXT,           -- polygon / tiingo / stooq
    last_updated    TEXT,           -- ISO datetime
    adtv_6m_usd     REAL,
    last_close      REAL
);

-- ── Company info (name, description, theme) — updated monthly via theme_labeler ──
CREATE TABLE IF NOT EXISTS company_info (
    ticker          TEXT PRIMARY KEY,
    name            TEXT,
    description     TEXT,
    sic_code        TEXT,
    sector          TEXT,
    industry        TEXT,
    theme           TEXT,           -- one of 14 AlphaAbsolute themes or NULL
    theme_source    TEXT,           -- manual / keyword / sic / none
    last_updated    TEXT            -- ISO datetime
);

-- ── Daily RS percentiles — written by rs_ranker each run, never deleted ──────
CREATE TABLE IF NOT EXISTS rs_daily (
    ticker          TEXT    NOT NULL,
    date            TEXT    NOT NULL,   -- YYYY-MM-DD
    rs_1m_pct       REAL,
    rs_3m_pct       REAL,
    rs_6m_pct       REAL,
    rs_composite    REAL,
    phase           TEXT,               -- Leader / Emerging / Recovering / Weak
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker  ON ohlcv(ticker);
CREATE INDEX IF NOT EXISTS idx_ohlcv_date    ON ohlcv(date);
CREATE INDEX IF NOT EXISTS idx_rs_ticker     ON rs_daily(ticker);
CREATE INDEX IF NOT EXISTS idx_rs_date       ON rs_daily(date);
"""


class OHLCVStore:
    """Persistent OHLCV store backed by SQLite."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # ── Connection ─────────────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")   # safe concurrent writes
            self._conn.execute("PRAGMA synchronous=NORMAL") # fast but safe
        return self._conn

    def _init_db(self):
        conn = self._connect()
        conn.executescript(_SCHEMA)
        conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Write ──────────────────────────────────────────────────────────────────
    def upsert(self, ticker: str, dates: list[str], closes: list[float],
               volumes: list[int], source: str = "polygon") -> int:
        """
        Insert or update OHLCV rows for a ticker.
        Existing dates are overwritten with new data (upsert).
        Returns number of rows written.
        """
        if not dates:
            return 0

        conn = self._connect()
        rows = list(zip(
            [ticker] * len(dates), dates, closes,
            volumes if volumes else [None] * len(dates)
        ))

        conn.executemany(
            "INSERT OR REPLACE INTO ohlcv (ticker, date, close, volume) VALUES (?,?,?,?)",
            rows
        )

        # Update metadata
        adtv = None
        if closes and volumes and len(closes) >= 2:
            n = min(126, len(closes))
            adtv = sum(c * v for c, v in zip(closes[-n:], volumes[-n:])) / n

        conn.execute("""
            INSERT OR REPLACE INTO ticker_meta
                (ticker, first_date, last_date, n_bars, source, last_updated, adtv_6m_usd, last_close)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            ticker, dates[0], dates[-1], len(dates), source,
            datetime.now().isoformat(), adtv, closes[-1] if closes else None
        ))
        conn.commit()
        return len(rows)

    # ── Read ───────────────────────────────────────────────────────────────────
    def get_closes(self, ticker: str, days: int = 252) -> list[float]:
        """Return last N days of closing prices (oldest first)."""
        conn = self._connect()
        rows = conn.execute("""
            SELECT close FROM ohlcv
            WHERE ticker = ?
            ORDER BY date DESC LIMIT ?
        """, (ticker, days)).fetchall()
        return [r[0] for r in reversed(rows)]

    def get_ohlcv(self, ticker: str, days: int = 290) -> dict | None:
        """Return dict with dates/close/volume lists. None if not found."""
        conn = self._connect()
        rows = conn.execute("""
            SELECT date, close, volume FROM ohlcv
            WHERE ticker = ?
            ORDER BY date ASC
        """, (ticker,)).fetchall()

        if not rows:
            return None

        # Trim to last N bars
        rows = rows[-days:]
        return {
            "ticker":  ticker,
            "dates":   [r["date"]   for r in rows],
            "close":   [r["close"]  for r in rows],
            "volume":  [r["volume"] for r in rows],
            "n_bars":  len(rows),
            "source":  "sqlite",
        }

    def get_meta(self, ticker: str) -> dict | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM ticker_meta WHERE ticker=?", (ticker,)
        ).fetchone()
        return dict(row) if row else None

    def all_tickers(self) -> list[str]:
        conn = self._connect()
        rows = conn.execute("SELECT ticker FROM ticker_meta ORDER BY ticker").fetchall()
        return [r[0] for r in rows]

    def is_fresh(self, ticker: str, max_age_hours: float = 20.0) -> bool:
        """True if ticker was updated within max_age_hours."""
        meta = self.get_meta(ticker)
        if not meta or not meta.get("last_updated"):
            return False
        updated = datetime.fromisoformat(meta["last_updated"])
        return (datetime.now() - updated).total_seconds() / 3600 < max_age_hours

    # ── Company Info ───────────────────────────────────────────────────────────
    def upsert_company_info(self, ticker: str, name: str = "", description: str = "",
                            sic_code: str = "", sector: str = "", industry: str = "",
                            theme: Optional[str] = None, theme_source: str = "none") -> None:
        """Insert or update company metadata. Called by theme_labeler monthly."""
        conn = self._connect()
        conn.execute("""
            INSERT OR REPLACE INTO company_info
                (ticker, name, description, sic_code, sector, industry,
                 theme, theme_source, last_updated)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (ticker, name, description, sic_code, sector, industry,
              theme, theme_source, datetime.now().isoformat()))
        conn.commit()

    def get_company_info(self, ticker: str) -> dict | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM company_info WHERE ticker=?", (ticker,)
        ).fetchone()
        return dict(row) if row else None

    def all_company_info(self) -> list[dict]:
        """Return all company_info rows as list of dicts."""
        conn = self._connect()
        rows = conn.execute("SELECT * FROM company_info ORDER BY ticker").fetchall()
        return [dict(r) for r in rows]

    def get_theme_map(self) -> dict[str, str]:
        """Return {ticker: theme} for all tickers with a theme. Fast lookup."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT ticker, theme FROM company_info WHERE theme IS NOT NULL"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    # ── RS Daily ───────────────────────────────────────────────────────────────
    def upsert_rs(self, ticker: str, date: str,
                  rs_1m: float, rs_3m: float, rs_6m: float,
                  rs_composite: float, phase: str = "") -> None:
        """Persist one day of RS scores. Called by rs_ranker daily.
        Never deletes history — even if Polygon fails today, yesterday's RS is intact.
        """
        conn = self._connect()
        conn.execute("""
            INSERT OR REPLACE INTO rs_daily
                (ticker, date, rs_1m_pct, rs_3m_pct, rs_6m_pct, rs_composite, phase)
            VALUES (?,?,?,?,?,?,?)
        """, (ticker, date, rs_1m, rs_3m, rs_6m, rs_composite, phase))
        conn.commit()

    def upsert_rs_batch(self, rows: list[tuple]) -> int:
        """Batch upsert RS scores. rows = [(ticker,date,rs1m,rs3m,rs6m,composite,phase),...]"""
        conn = self._connect()
        conn.executemany("""
            INSERT OR REPLACE INTO rs_daily
                (ticker, date, rs_1m_pct, rs_3m_pct, rs_6m_pct, rs_composite, phase)
            VALUES (?,?,?,?,?,?,?)
        """, rows)
        conn.commit()
        return len(rows)

    def get_rs_latest(self, ticker: str) -> dict | None:
        """Return most recent RS row for a ticker (even if from a prior day)."""
        conn = self._connect()
        row = conn.execute("""
            SELECT * FROM rs_daily WHERE ticker=?
            ORDER BY date DESC LIMIT 1
        """, (ticker,)).fetchone()
        return dict(row) if row else None

    def get_rs_history(self, ticker: str, days: int = 60) -> list[dict]:
        """Return last N days of RS history."""
        conn = self._connect()
        rows = conn.execute("""
            SELECT * FROM rs_daily WHERE ticker=?
            ORDER BY date DESC LIMIT ?
        """, (ticker, days)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_rs_universe_latest(self, date: Optional[str] = None) -> list[dict]:
        """Return the most recent RS snapshot for every ticker.
        Uses MAX(date) per ticker, so survives even if latest.json is stale.
        """
        conn = self._connect()
        if date:
            rows = conn.execute("""
                SELECT r.* FROM rs_daily r
                WHERE r.date = ?
                ORDER BY r.rs_composite DESC
            """, (date,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT r.* FROM rs_daily r
                INNER JOIN (
                    SELECT ticker, MAX(date) as max_date FROM rs_daily GROUP BY ticker
                ) m ON r.ticker = m.ticker AND r.date = m.max_date
                ORDER BY r.rs_composite DESC
            """).fetchall()
        return [dict(r) for r in rows]

    # ── Summary ────────────────────────────────────────────────────────────────
    def summary(self) -> dict:
        conn = self._connect()
        n_tickers   = conn.execute("SELECT COUNT(*) FROM ticker_meta").fetchone()[0]
        n_rows      = conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        n_companies = conn.execute("SELECT COUNT(*) FROM company_info").fetchone()[0]
        n_themed    = conn.execute("SELECT COUNT(*) FROM company_info WHERE theme IS NOT NULL").fetchone()[0]
        n_rs_rows   = conn.execute("SELECT COUNT(*) FROM rs_daily").fetchone()[0]
        n_rs_tickers= conn.execute("SELECT COUNT(DISTINCT ticker) FROM rs_daily").fetchone()[0]
        db_mb       = self.db_path.stat().st_size / 1e6 if self.db_path.exists() else 0

        print(f"=== OHLCVStore Summary ===")
        print(f"  DB file:    {self.db_path}")
        print(f"  Size:       {db_mb:.1f} MB")
        print(f"  OHLCV:      {n_tickers:,} tickers | {n_rows:,} rows")
        print(f"  Companies:  {n_companies:,} rows | {n_themed:,} with theme")
        print(f"  RS history: {n_rs_tickers:,} tickers | {n_rs_rows:,} daily rows")

        if n_tickers > 0:
            fresh = conn.execute("""
                SELECT COUNT(*) FROM ticker_meta
                WHERE last_updated > datetime('now', '-20 hours')
            """).fetchone()[0]
            print(f"  OHLCV fresh (<20h): {fresh}/{n_tickers}")

        if n_themed > 0:
            print("  Themes:")
            rows = conn.execute("""
                SELECT theme, COUNT(*) as n FROM company_info
                WHERE theme IS NOT NULL GROUP BY theme ORDER BY n DESC
            """).fetchall()
            for r in rows:
                print(f"    {r[0]:<22} {r[1]:>4}")

        return {"n_tickers": n_tickers, "n_rows": n_rows, "db_mb": db_mb,
                "n_companies": n_companies, "n_themed": n_themed,
                "n_rs_rows": n_rs_rows}

    # ── Migration from JSON cache ──────────────────────────────────────────────
    def migrate_from_json_cache(self, cache_dir: Path = CACHE_DIR,
                                 verbose: bool = True) -> dict:
        """
        One-time migration: read all *.json in ohlcv_cache/ → write to SQLite.
        Safe to run multiple times (upsert = idempotent).
        """
        files = sorted(cache_dir.glob("[A-Z]*.json"))
        n_ok = n_skip = n_err = 0
        t0 = time.time()

        if verbose:
            print(f"Migrating {len(files)} JSON files → {self.db_path.name} ...")

        for i, f in enumerate(files, 1):
            ticker = f.stem
            if ticker.startswith("_"):
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                dates   = d.get("dates", [])
                closes  = d.get("close", [])
                volumes = d.get("volume", [])
                source  = d.get("source", "json_cache")

                if len(closes) < 20:
                    n_skip += 1
                    continue

                self.upsert(ticker, dates, closes, volumes, source=source)
                n_ok += 1

                if verbose and i % 100 == 0:
                    elapsed = time.time() - t0
                    print(f"  [{i}/{len(files)}] {n_ok} ok | {n_skip} skip | "
                          f"{n_err} err | {elapsed:.0f}s")
            except Exception as e:
                n_err += 1
                if verbose:
                    print(f"  [WARN] {ticker}: {e}")

        elapsed = time.time() - t0
        if verbose:
            print(f"\nMigration complete: {n_ok} tickers in {elapsed:.1f}s")
            self.summary()

        return {"ok": n_ok, "skip": n_skip, "err": n_err, "seconds": elapsed}


# ─── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OHLCV SQLite Store")
    parser.add_argument("--migrate",  action="store_true",
                        help="Migrate all JSON cache files to SQLite")
    parser.add_argument("--summary",  action="store_true",
                        help="Show database summary")
    parser.add_argument("--ticker",   type=str,
                        help="Show last 10 bars for a ticker")
    args = parser.parse_args()

    store = OHLCVStore()

    if args.migrate:
        store.migrate_from_json_cache()
    elif args.summary:
        store.summary()
    elif args.ticker:
        data = store.get_ohlcv(args.ticker.upper(), days=10)
        if data:
            print(f"\n{args.ticker.upper()} — last {data['n_bars']} bars in DB:")
            for dt, c, v in zip(data["dates"], data["close"], data["volume"]):
                print(f"  {dt}  close={c}  vol={v:,}")
        else:
            print(f"{args.ticker.upper()} not in SQLite store yet")
            print("Run --migrate first")
    else:
        store.summary()
