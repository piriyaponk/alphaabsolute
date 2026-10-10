"""
entry_screen_db.py — SQLite backend for thai_entry_screen_results
=================================================================
Replaces the 877MB CSV with two tables inside thai_ohlcv.db.

Schema:
  entry_screen_column_order (idx, col_name)  — ordered signal column registry
  entry_screen_signals (date, ticker, regime, n_cols, signals_packed)
    signals_packed = numpy.packbits(bool_array) — 8 booleans per byte
    n_cols         = number of signal columns at write time

Storage math (2613 columns, 39k rows):
  2613 bits = 327 bytes per row
  39k rows × 327 bytes = ~12.8 MB  (vs 877 MB CSV)

Why packed blob vs wide schema:
  - SQLite SQLITE_MAX_COLUMN defaults to 2000 — 2613 cols would fail CREATE TABLE
  - Packed blob: numpy.unpackbits() → full bool array → DataFrame in one vectorized step
  - Columnar access is fully preserved after decode (same as CSV path)
  - Backward compatible: new columns added later → old rows get False for those columns

Public API (drop-in replacements for pd.read_csv / df.to_csv):
  read_entry_screen(date=None, db_path=None)   → pd.DataFrame (wide, bool columns)
  write_entry_screen(df, db_path=None)          → upsert rows, returns int count
  get_max_date(db_path=None)                    → 'YYYY-MM-DD' or None
  get_row_count(db_path=None)                   → int
  get_column_count(db_path=None)                → int
  migrate_from_csv(csv_path, db_path=None)      → one-time import, returns row count
  init_tables(db_path=None)                     → CREATE IF NOT EXISTS
"""
import json
import sqlite3
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT    = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "research" / "thai_ohlcv.db"

_META_COLS = {"date", "ticker", "regime"}   # not stored as signals


# ── Connection ──────────────────────────────────────────────────────────────────
def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    p = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(str(p), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ── Schema ──────────────────────────────────────────────────────────────────────
def init_tables(db_path: Optional[Path] = None) -> None:
    conn = get_connection(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS entry_screen_column_order (
            idx      INTEGER PRIMARY KEY AUTOINCREMENT,
            col_name TEXT    NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS entry_screen_signals (
            date            TEXT    NOT NULL,
            ticker          TEXT    NOT NULL,
            regime          TEXT,
            n_cols          INTEGER NOT NULL,
            signals_packed  BLOB    NOT NULL,
            PRIMARY KEY (date, ticker)
        );
        CREATE INDEX IF NOT EXISTS idx_ess_date
            ON entry_screen_signals(date);
    """)
    conn.commit()
    conn.close()


# ── Column registry ─────────────────────────────────────────────────────────────
def _get_col_list(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT col_name FROM entry_screen_column_order ORDER BY idx"
    ).fetchall()
    return [r[0] for r in rows]


def _ensure_cols(conn: sqlite3.Connection, new_cols: list[str]) -> list[str]:
    """Register any new columns. Returns full ordered column list after insert."""
    conn.executemany(
        "INSERT OR IGNORE INTO entry_screen_column_order(col_name) VALUES(?)",
        [(c,) for c in new_cols]
    )
    return _get_col_list(conn)


# ── Pack / unpack ───────────────────────────────────────────────────────────────
def _pack(bool_array: np.ndarray) -> bytes:
    """Pack boolean array to bytes with numpy.packbits (big-endian bit order)."""
    return np.packbits(bool_array.astype(bool)).tobytes()


def _unpack(blob: bytes, n_cols: int, target_n: int) -> np.ndarray:
    """Unpack bytes → bool array of length target_n.
    n_cols: how many signals were stored in this blob.
    target_n: current total column count (may be larger if columns were added later).
    Old rows have n_cols < target_n → pad with False for new columns.
    """
    raw = np.unpackbits(np.frombuffer(blob, dtype=np.uint8))
    bits = raw[:n_cols]                       # trim padding bits within blob
    if len(bits) < target_n:
        # Columns added after this row was written → implicitly False
        bits = np.concatenate([bits, np.zeros(target_n - len(bits), dtype=np.uint8)])
    return bits[:target_n].astype(bool)


# ── Write (upsert) ──────────────────────────────────────────────────────────────
def write_entry_screen(df: pd.DataFrame,
                       db_path: Optional[Path] = None) -> int:
    """Upsert all rows from a wide DataFrame into SQLite.
    Signal columns: all columns except date, ticker, regime.
    Returns number of rows written.
    """
    if df.empty:
        return 0

    init_tables(db_path)
    conn = get_connection(db_path)

    # Signal columns in DataFrame order
    sig_cols = [c for c in df.columns if c not in _META_COLS]
    if not sig_cols:
        conn.close()
        return 0

    # Register columns and get canonical ordered list
    all_cols = _ensure_cols(conn, sig_cols)

    # Build index map: sig_col → position in all_cols
    all_col_idx = {c: i for i, c in enumerate(all_cols)}
    n_all = len(all_cols)

    # Reindex: build bool matrix aligned to canonical column order
    sig_arr = df[sig_cols].fillna(False).astype(bool).values  # (N, len(sig_cols))
    aligned = np.zeros((len(df), n_all), dtype=bool)
    for j, col in enumerate(sig_cols):
        aligned[:, all_col_idx[col]] = sig_arr[:, j]

    # Prepare rows
    dates   = df["date"].astype(str).str[:10].tolist()
    tickers = df["ticker"].astype(str).tolist()
    if "regime" in df.columns:
        regimes = df["regime"].fillna("").astype(str).tolist()
    else:
        regimes = [""] * len(df)

    rows = []
    for i in range(len(df)):
        blob = _pack(aligned[i])
        rows.append((dates[i], tickers[i], regimes[i], n_all, blob))

    conn.executemany(
        "INSERT OR REPLACE INTO entry_screen_signals"
        "(date, ticker, regime, n_cols, signals_packed) VALUES(?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return len(rows)


# ── Read ────────────────────────────────────────────────────────────────────────
def read_entry_screen(date: Optional[str] = None,
                      db_path: Optional[Path] = None) -> pd.DataFrame:
    """Read entry screen signals as a wide DataFrame.
    date: 'YYYY-MM-DD' for one day (fast point query); None for all rows.
    Returns DataFrame: date, ticker, regime, <signal_cols...> as bool.
    """
    init_tables(db_path)
    conn = get_connection(db_path)

    col_names = _get_col_list(conn)
    n_cols = len(col_names)

    if not col_names:
        conn.close()
        return pd.DataFrame()

    if date:
        rows = conn.execute(
            "SELECT date, ticker, regime, n_cols, signals_packed "
            "FROM entry_screen_signals WHERE date = ? ORDER BY ticker",
            (str(date)[:10],)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT date, ticker, regime, n_cols, signals_packed "
            "FROM entry_screen_signals ORDER BY date, ticker"
        ).fetchall()
    conn.close()

    if not rows:
        return pd.DataFrame()

    n_rows = len(rows)
    sig_matrix = np.zeros((n_rows, n_cols), dtype=bool)
    dates   = []
    tickers = []
    regimes = []

    for i, (d, t, r, nc, blob) in enumerate(rows):
        dates.append(d)
        tickers.append(t)
        regimes.append(r or "")
        sig_matrix[i] = _unpack(blob, nc, n_cols)

    result = pd.DataFrame(sig_matrix, columns=col_names)
    result.insert(0, "regime", regimes)
    result.insert(0, "ticker", tickers)
    result.insert(0, "date",   dates)
    result["date"] = pd.to_datetime(result["date"])
    return result


# ── Helpers ─────────────────────────────────────────────────────────────────────
def get_max_date(db_path: Optional[Path] = None) -> Optional[str]:
    init_tables(db_path)
    conn = get_connection(db_path)
    row = conn.execute("SELECT MAX(date) FROM entry_screen_signals").fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def get_row_count(db_path: Optional[Path] = None) -> int:
    init_tables(db_path)
    conn = get_connection(db_path)
    n = conn.execute("SELECT COUNT(*) FROM entry_screen_signals").fetchone()[0]
    conn.close()
    return n


def get_column_count(db_path: Optional[Path] = None) -> int:
    init_tables(db_path)
    conn = get_connection(db_path)
    n = conn.execute("SELECT COUNT(*) FROM entry_screen_column_order").fetchone()[0]
    conn.close()
    return n


# ── Migration ───────────────────────────────────────────────────────────────────
def migrate_from_csv(csv_path: Path,
                     db_path: Optional[Path] = None,
                     chunk_size: int = 5000) -> int:
    """One-time import: read CSV → write to SQLite in chunks.
    Returns total rows written.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    init_tables(db_path)
    total = 0
    print(f"[migrate] Reading {csv_path.name} in chunks of {chunk_size}...")

    for chunk in pd.read_csv(csv_path, low_memory=False, chunksize=chunk_size):
        chunk["date"] = pd.to_datetime(chunk["date"]).dt.strftime("%Y-%m-%d")
        n = write_entry_screen(chunk, db_path)
        total += n
        print(f"  {total:>7,} rows written ...", end="\r")

    n_rows = get_row_count(db_path)
    n_cols = get_column_count(db_path)
    print(f"\n[migrate] Done — {total:,} rows | {n_cols} signal cols in SQLite")
    return total


# ── CLI ──────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "migrate":
        csv = ROOT / "data" / "research" / "thai_entry_screen_results.csv"
        migrate_from_csv(csv)

    elif cmd == "status":
        n_rows = get_row_count()
        n_cols = get_column_count()
        max_d  = get_max_date()
        print(f"entry_screen_signals: {n_rows:,} rows | {n_cols} signal cols | max_date={max_d}")

    elif cmd == "test":
        test_df = pd.DataFrame({
            "date":   ["2026-01-01", "2026-01-01"],
            "ticker": ["AAA.BK",      "BBB.BK"],
            "regime": ["bull",         "bear"],
            "sig_a":  [True,           False],
            "sig_b":  [False,          True],
            "sig_c":  [True,           True],
        })
        test_db = ROOT / "data" / "research" / "_test_entry_screen.db"
        test_db.unlink(missing_ok=True)
        n = write_entry_screen(test_df, test_db)
        out = read_entry_screen(db_path=test_db)
        assert len(out) == 2, f"Expected 2 rows, got {len(out)}"
        row_a = out[out["ticker"] == "AAA.BK"].iloc[0]
        assert row_a["sig_a"] == True
        assert row_a["sig_b"] == False
        assert row_a["sig_c"] == True
        row_b = out[out["ticker"] == "BBB.BK"].iloc[0]
        assert row_b["sig_a"] == False
        assert row_b["sig_b"] == True
        # Test column addition: write new column for one row
        test_df2 = pd.DataFrame({
            "date":   ["2026-01-02"],
            "ticker": ["CCC.BK"],
            "regime": ["bull"],
            "sig_a":  [True],
            "sig_d":  [True],   # new column not in original write
        })
        write_entry_screen(test_df2, test_db)
        out2 = read_entry_screen(db_path=test_db)
        # Old rows must have sig_d = False (column added after)
        assert out2[out2["ticker"] == "AAA.BK"].iloc[0]["sig_d"] == False
        assert out2[out2["ticker"] == "CCC.BK"].iloc[0]["sig_d"] == True
        test_db.unlink(missing_ok=True)
        print(f"[test] PASS — {n} rows written, pack/unpack correct, column extension correct")

    elif cmd == "verify":
        # Post-migration sanity check
        n_rows = get_row_count()
        n_cols = get_column_count()
        max_d  = get_max_date()
        print(f"[verify] {n_rows:,} rows | {n_cols} cols | max={max_d}")
        # Spot-check one row
        df = read_entry_screen(date=max_d)
        print(f"[verify] Read {len(df)} tickers for {max_d}")
        print(f"[verify] Columns: {list(df.columns[:5])} ...")
        sig_cols = [c for c in df.columns if c not in {"date", "ticker", "regime"}]
        true_rate = df[sig_cols].values.mean()
        print(f"[verify] Signal true rate: {true_rate:.1%}")
    else:
        print("Usage: entry_screen_db.py [migrate|status|test|verify]")
