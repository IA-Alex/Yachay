"""SQLite connection helper with the pragmas required by spec section 11."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from motor.storage.schema import create_schema


def connect(sqlite_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with motor pragmas and an ensured schema."""
    conn = sqlite3.connect(
        sqlite_path, isolation_level=None, check_same_thread=False
    )
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    create_schema(conn)
    return conn
