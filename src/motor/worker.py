"""Single-process worker: scan, admit, claim, run, publish, commit.

The worker owns scanning, reconciliation and pipeline execution (spec sections
4, 5 and 6). It writes state before each physical action and reconciles on
start, so every step is resumable.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

import duckdb

from motor import gc, log
from motor.config import MotorConfig
from motor.ingestion import admission, scanner
from motor.ingestion.identity import compute_job_key, new_job_id, new_run_id
from motor.pipelines.csv_pipeline import run_csv_pipeline, schema_fingerprint
from motor.publishing import publish
from motor.reconcile import reconcile
from motor.storage import jobs
from motor.storage.db import connect

# Processing-rule versions. These are fixed engine versions, not section 16
# performance parameters.
PIPELINE_VERSION = "csv-pipeline/0.1"
RULES_VERSION = "rules/0.1"


class Worker:
    """Local single-process worker."""

    def __init__(
        self,
        config: MotorConfig,
        *,
        worker_id: str = "worker-1",
        clock: Callable[[], float] = time.time,
        log_stream: IO[str] | None = None,
    ):
        self.config = config
        self.paths = config.paths
        self.worker_id = worker_id
        self.clock = clock
        # Structured log sink; None means stdout (journald under systemd).
        self._log_stream = log_stream
        self.conn = connect(self.paths.sqlite_path)

    def _scan_inbox(self) -> str | None:
        """Detect one stable file per call; ensure it is registered and queued.

        Stability is persisted in SQLite (`scan_snapshots`), so it survives
        worker restarts. In a single cycle, a file is admitted when either a
        previous reading already matched it, or its snapshot is unchanged
        across this cycle's own read (B2 mode).
        """
        if not self.paths.inbox.exists():
            return None
        for entry in sorted(self.paths.inbox.iterdir()):
            if not entry.is_file():
                continue

            # B2: record the snapshot; a prior match OR an unchanged file in
            # this same cycle both count as stable.
            had_previous = scanner.record_snapshot(self.conn, entry)
            if not had_previous and not scanner.is_stable_persisted(self.conn, entry):
                continue

            snap = scanner.snapshot(entry)
            size = snap[0]
            free = shutil.disk_usage(self.paths.inbox).free
            if not admission.has_space(
                free_bytes=free, input_size=size,
                factor=self.config.admision_factor,
            ):
                continue

            file_hash = scanner.file_hash(entry)
            job_key = compute_job_key(
                file_hash=file_hash,
                pipeline_version=PIPELINE_VERSION,
                rules_version=RULES_VERSION,
                config_hash=self.config.config_hash,
                llm_enabled=False,
            )

            # Reuse an already committed dataset with the same job_key.
            existing = self.conn.execute(
                "SELECT run_id FROM datasets WHERE job_key = ?", (job_key,)
            ).fetchone()
            if existing:
                entry.unlink()
                return None

            job_id = new_job_id()
            jobs.enqueue(self.conn, job_id=job_id, job_key=job_key)
            target = self.paths.processing / entry.name
            self.conn.execute(
                "INSERT OR REPLACE INTO input_files "
                "(job_id, path, sha256, size_bytes, mtime_ns) VALUES (?,?,?,?,?)",
                (job_id, str(target), file_hash, snap[0], snap[1]),
            )
            shutil.move(str(entry), str(target))
            jobs.mark_queued(self.conn, job_id)
            return job_id
        return None

    def run_once(self) -> str | None:
        """Run one cycle: reconcile, scan/admit, claim and process, then collect."""
        reconcile(self.conn, self.paths)
        self._scan_inbox()

        now = self.clock()
        job_id = jobs.claim_next(
            self.conn, worker_id=self.worker_id, now=now,
            ttl=self.config.lease_ttl_s,
        )
        if job_id is not None:
            self._process(job_id)
        self._collect()
        log.emit("cycle", stream=self._log_stream, job_id=job_id or "")
        return job_id

    def _collect(self) -> None:
        """Run the retention pass without letting it break the cycle (ADR-017).

        The GC is maintenance over artifacts no published result needs, so a
        recoverable failure (filesystem or SQLite) must not turn an already
        committed job into a crash. Programming errors are deliberately not
        absorbed: they must surface instead of being masked.
        """
        try:
            gc.collect(self.conn, self.paths, now=self.clock())
        except (OSError, sqlite3.Error):
            pass

    def _heartbeat(self, job_id: str) -> None:
        """Extend the lease with the same clock that claimed the job (ADR-017)."""
        jobs.heartbeat(
            self.conn, job_id=job_id, now=self.clock(), ttl=self.config.lease_ttl_s
        )

    def _fail_job(
        self, job_id: str, run_id: str, *, attempt: int, reason: str, detail: Exception
    ) -> None:
        """Move a job whose input cannot be processed to the terminal `failed`.

        A malformed or unreadable input is a deterministic failure: retrying only
        burns attempts, so the job is failed now, its lease cleared and the
        reason recorded without content (only identifiers and the error class).
        The original stays where it is; reconciliation will not resurrect a
        terminal job (ADR-014).
        """
        self.conn.execute(
            "UPDATE jobs SET state = 'failed', worker_id = NULL, "
            "lease_until = NULL WHERE job_id = ?",
            (job_id,),
        )
        self.conn.execute(
            "INSERT INTO audit_events (at, action, ref_id, ref_hash) "
            "VALUES (COALESCE(strftime('%s','now'),0), 'worker_fail_job', ?, '')",
            (job_id,),
        )
        log.emit(
            "job",
            stream=self._log_stream,
            job_id=job_id,
            run_id=run_id,
            attempt=attempt,
            reason=reason,
            error=type(detail).__name__,
            state="failed",
        )

    def _process(self, job_id: str) -> None:
        row = self.conn.execute(
            "SELECT job_key, attempt FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        job_key, attempt = row
        # Heartbeat at the start: the claim set the lease, and the pipeline is
        # a single shot with no batch boundaries to renew it later (T-011).
        self._heartbeat(job_id)
        src = self.conn.execute(
            "SELECT path FROM input_files WHERE job_id = ?", (job_id,)
        ).fetchone()
        input_path = Path(src[0])

        run_id = new_run_id()
        self.conn.execute(
            "INSERT OR REPLACE INTO job_runs (run_id, job_id, attempt, started_at) "
            "VALUES (?,?,?,COALESCE(strftime('%s','now'),0))",
            (run_id, job_id, attempt),
        )
        self.conn.execute(
            "UPDATE jobs SET run_id = ? WHERE job_id = ?", (run_id, job_id)
        )

        tmp_dir = self.paths.tmp / run_id
        try:
            reader, schema, row_count = run_csv_pipeline(
                input_path=input_path,
                duckdb_path=self.paths.tmp / f"{run_id}.duckdb",
                tmp_dir=tmp_dir,
            )
        except (duckdb.Error, OSError) as exc:
            # Untrusted input, not a programming error: a corrupt CSV, an
            # unreadable encoding or a file that vanished between reconciliation
            # and now must fail the job, never the worker process. The state is
            # set the same way reconciliation fails an unrecoverable job, so the
            # job is terminal and audited instead of crash-looping the service
            # (ADR-014 P1-2, ADR-016).
            self._fail_job(
                job_id, run_id, attempt=attempt, reason="pipeline error", detail=exc
            )
            return
        fingerprint = schema_fingerprint(schema)
        manifest = publish.publish_run(
            table=reader,
            row_count=row_count,
            schema_fingerprint=fingerprint,
            dataset=input_path.stem,
            run_id=run_id,
            job_key=job_key,
            processed_dir=self.paths.processed,
            tmp_dir=tmp_dir,
        )
        # Heartbeat after the pipeline: the expensive work is done, so the
        # lease no longer needs to cover it while the state is committed.
        self._heartbeat(job_id)

        # Short write transaction: insert dataset and mark the run committed.
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                "INSERT INTO datasets (run_id, job_key, dataset, manifest_path, "
                "committed_at) VALUES (?,?,?,?,COALESCE(strftime('%s','now'),0))",
                (run_id, job_key, input_path.stem, str(manifest)),
            )
            self.conn.execute(
                "UPDATE jobs SET state = 'committed' WHERE job_id = ?", (job_id,)
            )
            self.conn.execute("COMMIT")
        except sqlite3.IntegrityError:
            # Defense in depth (ADR-014): the manifest is already the physical
            # commit. Absorb the unique job_key violation only when a committed
            # dataset for this job_key exists; any other integrity failure
            # (trigger, CHECK, foreign key) must still propagate.
            self.conn.execute("ROLLBACK")
            if not self.conn.execute(
                "SELECT 1 FROM datasets WHERE job_key = ?", (job_key,)
            ).fetchone():
                raise
            self.conn.execute(
                "UPDATE jobs SET state = 'committed' WHERE job_id = ?", (job_id,)
            )
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

        # Move the original to processed/ retention (section 9, step 8).
        if input_path.exists():
            input_path.unlink()

        log.emit(
            "job",
            stream=self._log_stream,
            job_id=job_id,
            run_id=run_id,
            dataset=input_path.stem,
            attempt=attempt,
            rows=row_count,
            state="committed",
        )

    def run_forever(self) -> None:  # pragma: no cover - loop entrypoint
        while True:
            self.run_once()
            time.sleep(self.config.scanner_interval_s)
