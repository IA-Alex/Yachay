"""Retention and garbage collection (spec section 12, ADR-017).

The worker runs the GC inside its own loop, with no extra process. Every
function receives `now` from the caller: the module never reads the wall clock,
so a pass is reproducible in tests and the worker keeps one single clock.

Nothing here participates in `config_hash`/`job_key` (ADR-006): deleting an old
version never changes a published result, so retention is not part of result
equivalence.

Hard rules, all of them enforced below:

- `latest` and the version it points to are never deleted.
- A version with a live row in `datasets` is never deleted.
- An entry whose mtime is newer than `now` is never deleted.
- Every deletion is recorded in `audit_events` with an identifier and a hash,
  never with content.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from pathlib import Path

from motor.config import MotorPaths
from motor.limits import (
    DATASET_VERSIONS_TO_KEEP,
    FAILED_RETENTION_DAYS,
    QUARANTINE_RETENTION_DAYS,
)
from motor.reconcile import _audit

_SECONDS_PER_DAY = 86_400.0
_VERSION_PREFIX = "run_id="
_LATEST = "latest"
_MANIFEST = "manifest.json"


def _live_version_dirs(conn: sqlite3.Connection) -> set[Path]:
    """Return the version directories backed by a live `datasets` row."""
    return {
        Path(manifest_path).parent
        for (manifest_path,) in conn.execute(
            "SELECT manifest_path FROM datasets"
        ).fetchall()
    }


def _latest_target(dataset_dir: Path) -> Path | None:
    """Return the version directory `latest` resolves to, if it is a symlink."""
    latest = dataset_dir / _LATEST
    if not latest.is_symlink():
        return None
    return (dataset_dir / os.readlink(latest)).resolve()


def _manifest_hash(version_dir: Path) -> str:
    """Return the sha256 of the version manifest, or "" when it is missing."""
    manifest = version_dir / _MANIFEST
    if not manifest.is_file():
        return ""
    return hashlib.sha256(manifest.read_bytes()).hexdigest()


def _versions(dataset_dir: Path) -> list[Path]:
    """Return the version directories of one dataset, newest first.

    Ordering is by mtime with the name as a deterministic tie-break: `run_id`
    values carry no time, so the filesystem is the only ordering signal.
    """
    return sorted(
        (
            entry
            for entry in dataset_dir.iterdir()
            if entry.name.startswith(_VERSION_PREFIX)
            and entry.is_dir()
            and not entry.is_symlink()
        ),
        key=lambda entry: (entry.stat().st_mtime, entry.name),
        reverse=True,
    )


def prune_dataset_versions(
    conn: sqlite3.Connection, paths: MotorPaths, *, keep: int, now: float
) -> list[str]:
    """Keep the newest `keep` versions per dataset, retiring the rest (ADR-018).

    A version beyond `keep` is retired: if it has a live row in `datasets`, the
    row and the version go together (audit `gc_retire_dataset`, with the
    manifest hash taken before the removal); if it has no row, only the version
    is removed (audit `gc_prune_dataset_version`). The two protected versions —
    the one `latest` points to, and any version touched after `now` — are never
    retired.
    """
    actions: list[str] = []
    if not paths.processed.exists():
        return actions
    live = {version.resolve() for version in _live_version_dirs(conn)}
    for dataset_dir in sorted(entry for entry in paths.processed.iterdir() if entry.is_dir()):
        latest = _latest_target(dataset_dir)
        for index, version in enumerate(_versions(dataset_dir)):
            resolved = version.resolve()
            if index < keep or (latest is not None and resolved == latest):
                continue
            if version.stat().st_mtime > now:
                continue
            run_id = version.name.removeprefix(_VERSION_PREFIX)
            ref_hash = _manifest_hash(version)  # taken before the removal
            has_row = resolved in live
            _retire_version(conn, version, run_id, ref_hash, has_row)
            if has_row:
                actions.append(f"retire dataset version {run_id}")
            else:
                actions.append(f"prune dataset version {run_id}")
    return actions


def _retire_version(
    conn: sqlite3.Connection,
    version: Path,
    run_id: str,
    ref_hash: str,
    has_row: bool,
) -> None:
    """Remove one version, and its `datasets` row when present (ADR-018, R1).

    The order matters: the version directory is removed first, then the row is
    deleted, all within one transaction. If the row were deleted first and the
    version left behind, a later reconcile could rebuild the row from the still
    present manifest; removing the directory first makes the retire final.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        shutil.rmtree(version)
        if has_row:
            conn.execute("DELETE FROM datasets WHERE run_id = ?", (run_id,))
            _audit(conn, "gc_retire_dataset", run_id, ref_hash)
        else:
            _audit(conn, "gc_prune_dataset_version", run_id, ref_hash)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def purge_old_paths(
    conn: sqlite3.Connection, root: Path, *, now: float, days: float, action: str
) -> list[str]:
    """Delete the entries of `root` older than `days`, auditing each deletion."""
    actions: list[str] = []
    if not root.exists():
        return actions
    cutoff = now - days * _SECONDS_PER_DAY
    for entry in sorted(root.iterdir()):
        if entry.stat().st_mtime > cutoff:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
        _audit(conn, action, entry.name)
        actions.append(f"purge {entry.name}")
    return actions


def collect(
    conn: sqlite3.Connection,
    paths: MotorPaths,
    *,
    now: float,
    keep: int = DATASET_VERSIONS_TO_KEEP,
    failed_days: float = FAILED_RETENTION_DAYS,
    quarantine_days: float = QUARANTINE_RETENTION_DAYS,
) -> list[str]:
    """Run one retention pass: dataset versions, `failed/` and `quarantine/`."""
    actions = prune_dataset_versions(conn, paths, keep=keep, now=now)
    actions += purge_old_paths(
        conn, paths.failed, now=now, days=failed_days, action="gc_purge_failed"
    )
    actions += purge_old_paths(
        conn,
        paths.quarantine,
        now=now,
        days=quarantine_days,
        action="gc_purge_quarantine",
    )
    return actions
