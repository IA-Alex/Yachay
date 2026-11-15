"""Idempotent reconciliation of SQLite state against the filesystem.

Implements the reconciliation table of spec section 5. It is safe to run
repeatedly: it never duplicates rows, never re-moves an already moved file, and
never deletes confirmed results.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path

from motor.config import MotorPaths
from motor.limits import MAX_ATTEMPTS
from motor.storage import jobs

# Scratch file DuckDB leaves next to `tmp/<run_id>/` (spec section 9).
_DUCKDB_SUFFIX = ".duckdb"


def _active_run_ids(conn: sqlite3.Connection) -> set[str]:
    """Return the run_ids owned by jobs that are still in flight.

    Single definition of "active run": `_cleanup_orphan_tmp` must not remove
    the scratch space of a run that is still being executed.
    """
    return {
        r[0]
        for r in conn.execute(
            "SELECT run_id FROM jobs WHERE state IN ('claimed','queued','running') "
            "AND run_id IS NOT NULL"
        ).fetchall()
    }


def _audit(conn: sqlite3.Connection, action: str, ref_id: str, ref_hash: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_events (at, action, ref_id, ref_hash) "
        "VALUES (COALESCE(strftime('%s','now'),0), ?, ?, ?)",
        (action, ref_id, ref_hash),
    )


def _rebuild_datasets_from_manifests(conn: sqlite3.Connection, paths: MotorPaths) -> list[str]:
    actions: list[str] = []
    if not paths.processed.exists():
        return actions
    for manifest in sorted(paths.processed.glob("*/run_id=*/manifest.json")):
        run_id = manifest.parent.name.removeprefix("run_id=")
        if conn.execute(
            "SELECT 1 FROM datasets WHERE run_id = ?", (run_id,)
        ).fetchone():
            continue
        data = json.loads(manifest.read_text(encoding="utf-8"))
        # A manifest whose job_key is already committed is a duplicate run of
        # the same result (ADR-014): rebuilding it would violate the unique
        # job_key index and poison every later reconciliation.
        if conn.execute(
            "SELECT 1 FROM datasets WHERE job_key = ?", (data.get("job_key", ""),)
        ).fetchone():
            continue
        conn.execute(
            "INSERT INTO datasets (run_id, job_key, dataset, manifest_path, committed_at) "
            "VALUES (?, ?, ?, ?, COALESCE(strftime('%s','now'),0))",
            (run_id, data.get("job_key", ""), data.get("dataset", ""), str(manifest)),
        )
        _audit(conn, "reconcile_rebuild_dataset", run_id)
        actions.append(f"rebuild dataset {run_id} from manifest")
    return actions


def _mark_missing_datasets(conn: sqlite3.Connection) -> list[str]:
    """Retire dataset rows whose manifest is gone and no job backs them (R2).

    A row without its manifest is not a dataset: the manifest is the physical
    mark of commit (ADR-004). Reporting it every pass spammed the audit and left
    the API answering 503 forever, so the row is retired instead. It is
    reversible: if the manifest reappears, `_rebuild_datasets_from_manifests`
    recreates the row.

    A row is only retired when **no job** still references its `job_key`: a
    `committed` job whose row is removed would leave a published result the API
    can no longer describe. Those rows are left in place; the missing manifest
    stays visible through the API's `manifest_present: false`.
    """
    actions: list[str] = []
    for run_id, job_key, manifest_path in conn.execute(
        "SELECT run_id, job_key, manifest_path FROM datasets"
    ).fetchall():
        if Path(manifest_path).exists():
            continue
        if conn.execute(
            "SELECT 1 FROM jobs WHERE job_key = ? AND state IN "
            "('claimed','queued','running','committed','archived') LIMIT 1",
            (job_key,),
        ).fetchone():
            continue
        _audit(conn, "reconcile_missing_dataset", run_id)
        conn.execute("DELETE FROM datasets WHERE run_id = ?", (run_id,))
        _audit(conn, "reconcile_retire_dataset_row", run_id)
        actions.append(f"retire dataset {run_id}")
    return actions


def _complete_jobs_with_committed_dataset(conn: sqlite3.Connection) -> list[str]:
    """Complete jobs whose job_key already has a committed dataset (ADR-014).

    The manifest is the physical commit mark (ADR-004), so an existing dataset
    row means the result is durable. The state machine only catches up; the
    pipeline is not executed again and the unique job_key index is never hit.
    """
    actions: list[str] = []
    rows = conn.execute(
        "SELECT job_id, job_key FROM jobs "
        "WHERE state IN ('claimed', 'queued', 'running')"
    ).fetchall()
    for job_id, job_key in rows:
        if not conn.execute(
            "SELECT 1 FROM datasets WHERE job_key = ?", (job_key,)
        ).fetchone():
            continue
        conn.execute(
            "UPDATE jobs SET state = 'committed', worker_id = NULL, "
            "lease_until = NULL WHERE job_id = ?",
            (job_id,),
        )
        _audit(conn, "reconcile_commit_from_dataset", job_id, job_key)
        actions.append(f"mark {job_id} committed from dataset {job_key}")
    return actions


def _fail_jobs_without_input(conn: sqlite3.Connection) -> list[str]:
    """Fail active jobs with no row in `input_files` (ADR-014).

    Without that row the source path is unknown, so no retry can succeed. It is
    a deterministic failure, not a lease problem: the job goes straight to
    `failed` with an audit trail instead of staying trapped in an active state
    forever.
    """
    actions: list[str] = []
    rows = conn.execute(
        "SELECT j.job_id FROM jobs AS j "
        "LEFT JOIN input_files AS i ON i.job_id = j.job_id "
        "WHERE j.state IN ('claimed', 'queued', 'running') "
        "AND i.job_id IS NULL"
    ).fetchall()
    for (job_id,) in rows:
        conn.execute(
            "UPDATE jobs SET state = 'failed', worker_id = NULL, "
            "lease_until = NULL WHERE job_id = ?",
            (job_id,),
        )
        _audit(conn, "reconcile_fail_missing_input_row", job_id)
        actions.append(f"fail {job_id}: no input_files row")
    return actions


def _fail_jobs_with_missing_input_file(
    conn: sqlite3.Connection, paths: MotorPaths
) -> list[str]:
    """Fail active jobs whose registered input is no longer on disk (ADR-014).

    A `claimed` job may still have its file in `inbox/` because the move did not
    happen; that crash window is recoverable and belongs to `_reconcile_jobs`.
    Every other active state needs the file where `input_files` says it is. If
    it is gone, retrying only burns attempts until the cap, so the job fails now
    and the reason is recorded in `audit_events`.
    """
    actions: list[str] = []
    rows = conn.execute(
        "SELECT j.job_id, j.state, i.path FROM jobs AS j "
        "JOIN input_files AS i ON i.job_id = j.job_id "
        "WHERE j.state IN ('claimed', 'queued', 'running')"
    ).fetchall()
    for job_id, state, stored_path in rows:
        path = Path(stored_path)
        candidates = [path]
        if state == "claimed":
            candidates.append(paths.inbox / path.name)
        if any(candidate.exists() for candidate in candidates):
            continue
        conn.execute(
            "UPDATE jobs SET state = 'failed', worker_id = NULL, "
            "lease_until = NULL WHERE job_id = ?",
            (job_id,),
        )
        _audit(conn, "reconcile_fail_missing_input_file", job_id)
        actions.append(f"fail {job_id}: input file missing on disk")
    return actions


def _requeue_expired_jobs(conn: sqlite3.Connection, now: float) -> list[str]:
    """Recover jobs whose lease expired (section 5, ADR-014)."""
    actions: list[str] = []
    for job_id, outcome in jobs.requeue_expired(
        conn, now=now, max_attempts=MAX_ATTEMPTS
    ):
        if outcome == "failed":
            _audit(conn, "reconcile_lease_expired_failed", job_id)
            actions.append(f"fail {job_id} after lease expiry")
        else:
            _audit(conn, "reconcile_lease_expired_requeue", job_id)
            actions.append(f"requeue {job_id} after lease expiry")
    return actions


def _reconcile_jobs(conn: sqlite3.Connection, paths: MotorPaths) -> list[str]:
    """Complete the move for jobs whose input is still reachable (section 5).

    Jobs without an input row, or whose file is gone, were failed earlier in the
    same pass, so every job reaching this point has a registered source path.
    """
    actions: list[str] = []
    rows = conn.execute(
        "SELECT j.job_id, j.state, i.path FROM jobs AS j "
        "JOIN input_files AS i ON i.job_id = j.job_id "
        "WHERE j.state IN ('claimed', 'queued', 'running')"
    ).fetchall()
    for job_id, state, stored_path in rows:
        name = Path(stored_path).name
        in_inbox = paths.inbox / name
        in_processing = paths.processing / name
        if state == "claimed" and in_inbox.exists():
            shutil.move(str(in_inbox), str(in_processing))
            jobs.mark_queued(conn, job_id)
            _audit(conn, "reconcile_complete_move", job_id)
            actions.append(f"complete move for {job_id}")
        elif state == "claimed" and in_processing.exists():
            jobs.mark_queued(conn, job_id)
            _audit(conn, "reconcile_advance_queued", job_id)
            actions.append(f"advance {job_id} to queued")
    return actions


def _cleanup_committed_originals(conn: sqlite3.Connection, paths: MotorPaths) -> list[str]:
    """Remove originals left in processing/ after a committed job (section 9, step 8).

    If the worker crashes between the SQLite commit and the final unlink, the
    original file stays in processing/. Once the dataset is committed its
    original is no longer needed, so reconciliation completes the cleanup.
    """
    actions: list[str] = []
    committed = conn.execute(
        "SELECT job_id FROM jobs WHERE state IN ('committed', 'archived')"
    ).fetchall()
    for (job_id,) in committed:
        src = conn.execute(
            "SELECT path FROM input_files WHERE job_id = ?", (job_id,)
        ).fetchone()
        if not src:
            continue
        path = Path(src[0])
        if path.exists():
            path.unlink()
            _audit(conn, "reconcile_cleanup_committed_original", job_id)
            actions.append(f"remove committed original {path.name}")
    return actions


def _cleanup_orphan_tmp(conn: sqlite3.Connection, paths: MotorPaths) -> list[str]:
    actions: list[str] = []
    active = _active_run_ids(conn)
    if not paths.tmp.exists():
        return actions
    for entry in sorted(paths.tmp.iterdir()):
        if entry.is_dir():
            name, is_dir = entry.name, True
        elif entry.name.endswith(_DUCKDB_SUFFIX):
            # A bare `<run_id>.duckdb` left by an interrupted pipeline run.
            name, is_dir = entry.name[: -len(_DUCKDB_SUFFIX)], False
        else:
            continue
        if name in active:
            continue
        # Only remove scratch space not tied to a confirmed result or an
        # in-flight run: `processed/` is never touched here.
        if is_dir:
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink()
        _audit(conn, "reconcile_tmp_cleanup", name)
        actions.append(f"clean tmp {name}")
    return actions


def reconcile(conn: sqlite3.Connection, paths: MotorPaths) -> list[str]:
    """Reconcile state and filesystem; return the actions taken.

    Order matters (ADR-014, ADR-018): manifests are rebuilt into `datasets`
    first, and jobs that already have a committed dataset are completed before
    any unbacked row is retired. That way a job whose result exists is finished
    rather than left to fail, and only then are rows whose manifest is truly
    gone retired.

    The two lost-input branches run before `_requeue_expired_jobs`: a job whose
    input cannot be recovered must fail with its reason instead of being
    requeued into a crash loop that only burns attempts.
    """
    actions: list[str] = []
    actions += _rebuild_datasets_from_manifests(conn, paths)
    actions += _complete_jobs_with_committed_dataset(conn)
    actions += _mark_missing_datasets(conn)
    actions += _fail_jobs_without_input(conn)
    actions += _fail_jobs_with_missing_input_file(conn, paths)
    actions += _requeue_expired_jobs(conn, time.time())
    actions += _reconcile_jobs(conn, paths)
    actions += _cleanup_committed_originals(conn, paths)
    actions += _cleanup_orphan_tmp(conn, paths)
    return actions
