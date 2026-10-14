"""
Centralized SQLite connection helper.

Usage:
    from scripts.utils.db_connect import connect

    with connect(DB_PATH) as conn:
        ...

Applies WAL journal mode + busy_timeout on every connection so concurrent
OneDrive sync + pipeline writes don't produce OperationalError: database is locked.
"""
import sqlite3
from pathlib import Path
from typing import Union


def connect(path: Union[str, Path], timeout: float = 30, read_only: bool = False) -> sqlite3.Connection:
    """Return a sqlite3 connection with WAL mode and busy timeout enabled."""
    conn = sqlite3.connect(str(path), timeout=timeout, check_same_thread=False)
    if not read_only:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")   # safe with WAL, ~2x faster than FULL
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    return conn
