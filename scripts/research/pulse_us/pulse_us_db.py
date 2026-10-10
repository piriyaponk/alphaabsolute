"""pulse_us_db.py -- SQLite backend for PULSE-US signal storage.
Modeled on entry_screen_db.py (Thai). No regime col. numpy.packbits.
AUTOINCREMENT on col_idx (never delete from column_order).
DB: data/research/pulse_us_signals.db
"""
import sqlite3, warnings
from pathlib import Path
from typing import Optional
import numpy as np
import pandas as pd

ROOT    = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "data" / "research" / "pulse_us_signals.db"
_META_COLS = {"date", "ticker"}

def get_connection(db_path=None):
    q = Path(db_path) if db_path else DB_PATH
    q.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(q), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db(db_path=None):
    """Create tables. db_path can be a file path OR an existing sqlite3.Connection."""
    import sqlite3 as _sq
    if isinstance(db_path, _sq.Connection):
        conn = db_path
        _close = False
    else:
        conn = get_connection(db_path)
        _close = True
    conn.execute("CREATE TABLE IF NOT EXISTS entry_screen_column_order "
                 "(col_idx INTEGER PRIMARY KEY AUTOINCREMENT, col_name TEXT NOT NULL UNIQUE)")
    conn.execute("CREATE TABLE IF NOT EXISTS entry_screen_signals "
                 "(date TEXT NOT NULL, ticker TEXT NOT NULL, n_cols INTEGER NOT NULL, "
                 "signals_packed BLOB NOT NULL, PRIMARY KEY (date, ticker))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ess_date ON entry_screen_signals(date)")
    conn.commit()
    if _close:
        conn.close()

init_tables = init_db

def _get_col_list(conn):
    rows = conn.execute("SELECT col_name FROM entry_screen_column_order ORDER BY col_idx").fetchall()
    return [r[0] for r in rows]

def _ensure_cols(conn, new_cols):
    """Register new cols. INSERT OR IGNORE preserves existing col_idx."""
    conn.executemany("INSERT OR IGNORE INTO entry_screen_column_order(col_name) VALUES(?)", [(c,) for c in new_cols])
    return _get_col_list(conn)

def _pack(bool_array):
    return np.packbits(bool_array.astype(bool)).tobytes()

def _unpack(blob, n_cols, target_n):
    raw = np.unpackbits(np.frombuffer(blob, dtype=np.uint8))
    bits = raw[:n_cols]
    if len(bits) < target_n:
        bits = np.concatenate([bits, np.zeros(target_n - len(bits), dtype=np.uint8)])
    return bits[:target_n].astype(bool)

def upsert_signals(conn, date, signals_df):
    """Batch upsert for one date. Raises ValueError on -1 values."""
    if signals_df is None or signals_df.empty:
        return 0
    sig_cols = [c for c in signals_df.columns if c not in _META_COLS and c != "date"]
    if not sig_cols:
        return 0
    for col in sig_cols:
        s = signals_df[col]
        if s.dtype.kind in ("i", "f") and (s == -1).any():
            raise ValueError(
                f"[pulse_us_db] Column {col!r} has -1 values. "
                "Resolve tri-state gate outputs before writing. -1.astype(bool)=True."
            )
    bool_block = signals_df[sig_cols].fillna(False)
    if not bool_block.any(axis=None):
        warnings.warn(f"[pulse_us_db] All signals False for {date}. Suspect run?", UserWarning, stacklevel=2)
    all_cols    = _ensure_cols(conn, sig_cols)
    all_col_idx = {c: i for i, c in enumerate(all_cols)}
    n_all       = len(all_cols)
    sig_arr = bool_block.astype(bool).values
    aligned = np.zeros((len(signals_df), n_all), dtype=bool)
    for j, col in enumerate(sig_cols):
        aligned[:, all_col_idx[col]] = sig_arr[:, j]
    date_str = str(date)[:10]
    tickers  = signals_df["ticker"].astype(str).tolist()
    rows = []
    for i in range(len(signals_df)):
        rows.append((date_str, tickers[i], n_all, _pack(aligned[i])))
    conn.executemany("INSERT OR REPLACE INTO entry_screen_signals(date,ticker,n_cols,signals_packed) VALUES(?,?,?,?)", rows)
    conn.commit()
    return len(rows)

def write_entry_screen(df, db_path=None):
    """Convenience: open conn, upsert all rows grouped by date, close."""
    if df is None or df.empty:
        return 0
    init_db(db_path)
    conn = get_connection(db_path)
    total = 0
    try:
        for date_val, grp in df.groupby("date"):
            grp_clean = grp.drop(columns=["date"], errors="ignore").copy()
            total += upsert_signals(conn, str(date_val)[:10], grp_clean)
    finally:
        conn.close()
    return total

def get_signals_df(conn, date=None):
    """Read wide bool DataFrame. date=None returns all rows."""
    col_names = _get_col_list(conn)
    n_cols    = len(col_names)
    if not col_names:
        return pd.DataFrame()
    if date:
        rows = conn.execute("SELECT date,ticker,n_cols,signals_packed FROM entry_screen_signals WHERE date=? ORDER BY ticker", (str(date)[:10],)).fetchall()
    else:
        rows = conn.execute("SELECT date,ticker,n_cols,signals_packed FROM entry_screen_signals ORDER BY date,ticker").fetchall()
    if not rows:
        return pd.DataFrame()
    n_rows = len(rows)
    sig_matrix = np.zeros((n_rows, n_cols), dtype=bool)
    dates, tickers = [], []
    for i, (d, t, nc, blob) in enumerate(rows):
        dates.append(d); tickers.append(t)
        sig_matrix[i] = _unpack(blob, nc, n_cols)
    result = pd.DataFrame(sig_matrix, columns=col_names)
    result.insert(0, "ticker", tickers)
    result.insert(0, "date",   dates)
    result["date"] = pd.to_datetime(result["date"])
    return result

def read_entry_screen(date=None, db_path=None):
    """Convenience: open conn, read, close."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        return get_signals_df(conn, date)
    finally:
        conn.close()

def get_signals(conn, date, ticker):
    """Single (date,ticker) row as dict. Returns {} if not found."""
    col_names = _get_col_list(conn)
    if not col_names:
        return {}
    row = conn.execute("SELECT n_cols,signals_packed FROM entry_screen_signals WHERE date=? AND ticker=?", (str(date)[:10], str(ticker))).fetchone()
    if not row:
        return {}
    nc, blob = row
    bits = _unpack(blob, nc, len(col_names))
    return {col: bool(bits[i]) for i, col in enumerate(col_names)}

def get_latest_date(conn):
    row = conn.execute("SELECT MAX(date) FROM entry_screen_signals").fetchone()
    return row[0] if row and row[0] else None

def get_row_count(db_path=None):
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM entry_screen_signals").fetchone()[0]
    finally:
        conn.close()

def get_column_count(db_path=None):
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM entry_screen_column_order").fetchone()[0]
    finally:
        conn.close()

def check_integrity(db_path=None):
    q = Path(db_path) if db_path else DB_PATH
    size_bytes = q.stat().st_size if q.exists() else 0
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        res = conn.execute("PRAGMA integrity_check").fetchone()
        msg = res[0] if res else "no result"
        rows = conn.execute("SELECT COUNT(*) FROM entry_screen_signals").fetchone()[0]
        cols = conn.execute("SELECT COUNT(*) FROM entry_screen_column_order").fetchone()[0]
        maxd = conn.execute("SELECT MAX(date) FROM entry_screen_signals").fetchone()[0]
    finally:
        conn.close()
    return {"integrity_ok": msg=="ok", "integrity_msg": msg, "size_bytes": size_bytes, "row_count": rows, "col_count": cols, "max_date": maxd}

def migrate_from_csv(csv_path, db_path=None, chunk_size=5000):
    """One-time CSV -> SQLite. Returns rows written."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    init_db(db_path)
    total = 0
    for chunk in __import__("pandas").read_csv(csv_path, low_memory=False, chunksize=chunk_size, encoding="utf-8"):
        chunk["date"] = __import__("pandas").to_datetime(chunk["date"]).dt.strftime("%Y-%m-%d")
        total += write_entry_screen(chunk, db_path)
        print(f"  {total:>7,} rows written", end="")
    print(); print(f"[migrate] Done -- {total:,} rows | {get_column_count(db_path)} cols")
    return total

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        nr = get_row_count()
        nc = get_column_count()
        conn = get_connection(); md = get_latest_date(conn); conn.close()
        print(f"entry_screen_signals: {nr:,} rows | {nc} cols | max_date={md}")
    elif cmd == "test":
        import tempfile
        tdb = Path(tempfile.mkdtemp()) / "_test_pulse_us.db"
        try:
            init_db(tdb)
            conn = get_connection(tdb)
            import pandas as pd
            df1 = pd.DataFrame({"ticker":["NVDA","AAPL"],"sig_a":[True,False],"sig_b":[False,True],"sig_c":[True,True]})
            assert upsert_signals(conn,"2026-01-01",df1)==2
            out = get_signals_df(conn,"2026-01-01")
            assert len(out)==2
            assert out[out["ticker"]=="NVDA"].iloc[0]["sig_a"]==True
            assert out[out["ticker"]=="NVDA"].iloc[0]["sig_b"]==False
            df2 = pd.DataFrame({"ticker":["MSFT"],"sig_a":[True],"sig_d":[True]})
            upsert_signals(conn,"2026-01-02",df2)
            out2 = get_signals_df(conn)
            assert out2[out2["ticker"]=="NVDA"].iloc[0]["sig_d"]==False
            assert out2[out2["ticker"]=="MSFT"].iloc[0]["sig_d"]==True
            d = get_signals(conn,"2026-01-01","NVDA")
            assert d["sig_a"]==True and d["sig_b"]==False
            df_bad = pd.DataFrame({"ticker":["X"],"sig_a":[-1]})
            try:
                upsert_signals(conn,"2026-01-03",df_bad)
                assert False,"Should raise"
            except ValueError:
                pass
            conn.close()
            print("[test] PASS")
        finally:
            try: tdb.unlink(missing_ok=True)
            except Exception: pass
    elif cmd == "integrity":
        r = check_integrity()
        imsg = r["integrity_msg"]; mb = r["size_bytes"]/1048576
        rc = r["row_count"]; md2 = r["max_date"]
        s = "OK" if r["integrity_ok"] else ("FAIL:"+imsg)
        print(f"[integrity] {s} | {mb:.1f}MB | {rc:,}rows | max={md2}")
        if not r["integrity_ok"]: sys.exit(1)
    elif cmd == "migrate":
        if len(sys.argv)<3: print("Usage: pulse_us_db.py migrate <csv>"); sys.exit(1)
        migrate_from_csv(Path(sys.argv[2]))
    else:
        print("Usage: pulse_us_db.py [status|test|integrity|migrate <csv>]")
