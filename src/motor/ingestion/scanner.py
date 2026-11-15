"""Scanner helpers: file stability and content hashing (spec section 5).

A file is a valid input once it arrives by atomic rename from `.part`, or once
its size and mtime are identical across two consecutive reads.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_BLOCK = 1024 * 1024  # 1 MiB


def snapshot(path: Path) -> tuple[int, int]:
    """Return `(size_bytes, mtime_ns)` for a path."""
    stat = Path(path).stat()
    return (stat.st_size, stat.st_mtime_ns)


def is_stable(path: Path, *, previous: tuple[int, int] | None) -> bool:
    """Return True when the current snapshot matches the previous one."""
    if previous is None:
        return False
    return snapshot(path) == previous


def file_hash(path: Path) -> str:
    """Return the sha256 of a file's contents, read in 1 MiB blocks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def record_snapshot(conn, path: Path) -> bool:
    """Persist the current `(size, mtime_ns)` for a path.

    Returns True when a previous snapshot existed (i.e. this is at least the
    second reading), False on the first reading. Persisting the snapshot makes
    stability survive worker restarts (spec section 1, principle 2).
    """
    size, mtime_ns = snapshot(path)
    key = str(path)
    existed = conn.execute(
        "SELECT 1 FROM scan_snapshots WHERE path = ?", (key,)
    ).fetchone() is not None
    conn.execute(
        "INSERT INTO scan_snapshots (path, size_bytes, mtime_ns, seen_at) "
        "VALUES (?, ?, ?, COALESCE(strftime('%s','now'),0)) "
        "ON CONFLICT(path) DO UPDATE SET "
        "size_bytes = excluded.size_bytes, mtime_ns = excluded.mtime_ns, "
        "seen_at = excluded.seen_at",
        (key, size, mtime_ns),
    )
    return existed


def is_stable_persisted(conn, path: Path) -> bool:
    """Return True when the stored snapshot matches the file's current state."""
    size, mtime_ns = snapshot(path)
    row = conn.execute(
        "SELECT size_bytes, mtime_ns FROM scan_snapshots WHERE path = ?",
        (str(path),),
    ).fetchone()
    return row is not None and (row[0], row[1]) == (size, mtime_ns)
