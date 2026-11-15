"""Retention and garbage-collection tests (spec section 12, ADR-017).

Every test runs against a real SQLite database and a real filesystem root, and
passes `now` explicitly: the outcome never depends on the wall clock.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from motor import gc
from motor.config import MotorConfig
from motor.reconcile import reconcile
from motor.storage.db import connect

_DAY = 86_400.0
# Reference instant for every test: far newer than the mtimes used below.
_NOW = 10_000_000.0


def _manifest_bytes(dataset: str, run_id: str) -> bytes:
    """Return the exact manifest content written by `_GcFixture.add_version`."""
    return json.dumps({"dataset": dataset, "run_id": run_id}).encode("utf-8")


class _GcFixture(unittest.TestCase):
    """Real root, real SQLite and builders for published versions."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = MotorConfig.from_root(Path(self._tmp.name))
        self.paths = self.cfg.paths
        self.conn = connect(self.paths.sqlite_path)

    def add_version(
        self,
        dataset: str,
        run_id: str,
        *,
        mtime: float,
        latest: bool = False,
        live: bool = False,
    ) -> Path:
        """Create one published version directory, as `publish_run` leaves it."""
        dataset_dir = self.paths.processed / dataset
        version = dataset_dir / f"run_id={run_id}"
        version.mkdir(parents=True)
        (version / "part-000.parquet").write_bytes(b"x")
        (version / "manifest.json").write_bytes(_manifest_bytes(dataset, run_id))
        if latest:
            (dataset_dir / "latest").symlink_to(f"run_id={run_id}")
        if live:
            self.conn.execute(
                "INSERT INTO datasets (run_id, job_key, dataset, manifest_path, "
                "committed_at) VALUES (?,?,?,?,0)",
                (run_id, f"key-{run_id}", dataset, str(version / "manifest.json")),
            )
        os.utime(version, (mtime, mtime))
        return version

    def versions(self, dataset: str) -> list[str]:
        dataset_dir = self.paths.processed / dataset
        return sorted(
            entry.name
            for entry in dataset_dir.iterdir()
            if entry.is_dir() and not entry.is_symlink()
        )

    def audit(self, action: str) -> list[tuple[str, str]]:
        return self.conn.execute(
            "SELECT ref_id, ref_hash FROM audit_events WHERE action = ? ORDER BY id",
            (action,),
        ).fetchall()


class DatasetVersionPruneTests(_GcFixture):
    """Versions of a dataset are pruned by count, never by accident."""

    def test_prunes_the_oldest_versions_beyond_keep(self):
        for index in range(1, 6):
            self.add_version("sales", f"r{index}", mtime=1_000.0 * index, latest=index == 5)

        deleted = gc.prune_dataset_versions(self.conn, self.paths, keep=3, now=_NOW)

        self.assertEqual(self.versions("sales"), ["run_id=r3", "run_id=r4", "run_id=r5"])
        self.assertEqual(len(deleted), 2)

    def test_never_deletes_the_version_latest_points_to(self):
        for index in range(1, 6):
            self.add_version("sales", f"r{index}", mtime=1_000.0 * index)
        # `latest` deliberately points at the oldest version: it is still the
        # published result and must survive the count-based prune.
        (self.paths.processed / "sales" / "latest").symlink_to("run_id=r1")

        gc.prune_dataset_versions(self.conn, self.paths, keep=2, now=_NOW)

        self.assertIn("run_id=r1", self.versions("sales"))
        self.assertIn("run_id=r5", self.versions("sales"))
        self.assertNotIn("run_id=r2", self.versions("sales"))

    def test_retires_a_version_beyond_keep_together_with_its_dataset_row(self):
        # Contract change (ADR-018): a version beyond `keep` is no longer
        # protected forever by its row. The version and its `datasets` row are
        # retired together, so the prune is effective instead of inert.
        for index in range(1, 6):
            self.add_version(
                "sales",
                f"r{index}",
                mtime=1_000.0 * index,
                latest=index == 5,
                live=index == 1,
            )

        actions = gc.prune_dataset_versions(self.conn, self.paths, keep=2, now=_NOW)

        # r1 was live but exceeds keep: retired with its row, audited.
        self.assertTrue(any("retire dataset version r1" in a for a in actions))
        self.assertNotIn("run_id=r1", self.versions("sales"))
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM datasets WHERE run_id = 'r1'"
            ).fetchone()
        )
        self.assertIn("r1", [row[0] for row in self.audit("gc_retire_dataset")])
        # r5 is `latest`, r4 is within keep: both stay.
        self.assertIn("run_id=r5", self.versions("sales"))
        self.assertIn("run_id=r4", self.versions("sales"))

    def test_retires_the_row_before_deleting_the_version(self):
        # Order (R1): if the version were deleted first and the row left, a later
        # reconcile could resurrect the row from a manifest that no longer
        # exists — or worse, keep a row pointing nowhere. After a retire the row
        # is gone and no manifest remains to rebuild from.
        self.add_version("sales", "r1", mtime=1_000.0, live=True)
        self.add_version("sales", "r2", mtime=2_000.0, latest=True)

        gc.prune_dataset_versions(self.conn, self.paths, keep=1, now=_NOW)

        rebuilt = reconcile(self.conn, self.paths)
        self.assertEqual(
            [a for a in rebuilt if "rebuild dataset r1" in a], [],
            "r1 must not be resurrected after being retired",
        )
        self.assertIsNone(
            self.conn.execute("SELECT 1 FROM datasets WHERE run_id = 'r1'").fetchone()
        )

    def test_a_retired_version_is_not_pruned_twice(self):
        for index in range(1, 4):
            self.add_version("sales", f"r{index}", mtime=1_000.0 * index, live=index == 1)
        gc.prune_dataset_versions(self.conn, self.paths, keep=1, now=_NOW)

        second = gc.prune_dataset_versions(self.conn, self.paths, keep=1, now=_NOW)

        self.assertEqual(second, [])
        self.assertEqual(len(self.audit("gc_retire_dataset")), 1)

    def test_never_deletes_a_version_touched_after_now(self):
        for index in range(1, 4):
            self.add_version("sales", f"r{index}", mtime=1_000.0 * index, latest=index == 3)
        # A version touched after the reference instant is in flight or the
        # clock moved: the GC does not guess, it skips it.
        future = self.paths.processed / "sales" / "run_id=r1"
        os.utime(future, (_NOW + 60, _NOW + 60))

        gc.prune_dataset_versions(self.conn, self.paths, keep=0, now=_NOW)

        self.assertTrue(future.exists())
        self.assertNotIn("run_id=r2", self.versions("sales"))

    def test_each_deletion_is_audited_with_the_manifest_hash(self):
        for index in range(1, 5):
            self.add_version("sales", f"r{index}", mtime=1_000.0 * index, latest=index == 4)

        gc.prune_dataset_versions(self.conn, self.paths, keep=2, now=_NOW)

        rows = self.audit("gc_prune_dataset_version")
        self.assertEqual(sorted(row[0] for row in rows), ["r1", "r2"])
        hashes = dict(rows)
        self.assertEqual(
            hashes["r1"], hashlib.sha256(_manifest_bytes("sales", "r1")).hexdigest()
        )

    def test_a_second_pass_deletes_nothing(self):
        for index in range(1, 5):
            self.add_version("sales", f"r{index}", mtime=1_000.0 * index, latest=index == 4)

        gc.prune_dataset_versions(self.conn, self.paths, keep=2, now=_NOW)
        audited = len(self.audit("gc_prune_dataset_version"))
        again = gc.prune_dataset_versions(self.conn, self.paths, keep=2, now=_NOW)

        self.assertEqual(again, [])
        self.assertEqual(len(self.audit("gc_prune_dataset_version")), audited)

    def test_a_root_without_published_datasets_is_not_an_error(self):
        self.assertEqual(
            gc.prune_dataset_versions(self.conn, self.paths, keep=3, now=_NOW), []
        )

    def test_a_root_without_processed_directory_is_not_an_error(self):
        shutil.rmtree(self.paths.processed)

        self.assertEqual(
            gc.prune_dataset_versions(self.conn, self.paths, keep=1, now=_NOW), []
        )

    def test_a_dataset_without_latest_is_pruned_by_count(self):
        for index in range(1, 4):
            self.add_version("sales", f"r{index}", mtime=1_000.0 * index)

        gc.prune_dataset_versions(self.conn, self.paths, keep=2, now=_NOW)

        self.assertEqual(self.versions("sales"), ["run_id=r2", "run_id=r3"])

    def test_a_version_without_a_manifest_is_audited_without_a_hash(self):
        for index in range(1, 3):
            self.add_version("sales", f"r{index}", mtime=1_000.0 * index, latest=index == 2)
        stale = self.paths.processed / "sales" / "run_id=r1"
        (stale / "manifest.json").unlink()
        # Removing the manifest bumps the directory mtime: restore it so the
        # version stays older than `now` and the prune reaches it.
        os.utime(stale, (1_000.0, 1_000.0))

        gc.prune_dataset_versions(self.conn, self.paths, keep=1, now=_NOW)

        self.assertEqual(self.audit("gc_prune_dataset_version"), [("r1", "")])

    def test_a_failed_retire_rolls_back_and_propagates(self):
        # A retire that cannot delete its row (a SQLite trigger aborts it) must
        # roll back and re-raise, leaving the database consistent.
        self.add_version("sales", "r1", mtime=1_000.0, live=True)
        self.add_version("sales", "r2", mtime=2_000.0, latest=True)
        self.conn.execute(
            "CREATE TRIGGER block_delete BEFORE DELETE ON datasets "
            "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            gc.prune_dataset_versions(self.conn, self.paths, keep=1, now=_NOW)

        # The row survives the rollback; the audit for the retire is not written.
        self.assertIsNotNone(
            self.conn.execute("SELECT 1 FROM datasets WHERE run_id = 'r1'").fetchone()
        )
        self.assertEqual(self.audit("gc_retire_dataset"), [])


class PathPurgeTests(_GcFixture):
    """`failed/` and `quarantine/` are purged by age, nothing else."""

    def test_purges_only_entries_older_than_the_retention_window(self):
        old = self.paths.failed / "old.csv"
        old.write_text("id,v\n1,a\n", encoding="utf-8")
        os.utime(old, (_NOW - 40 * _DAY, _NOW - 40 * _DAY))
        recent = self.paths.failed / "recent.csv"
        recent.write_text("id,v\n1,a\n", encoding="utf-8")
        os.utime(recent, (_NOW - 1 * _DAY, _NOW - 1 * _DAY))

        deleted = gc.purge_old_paths(
            self.conn, self.paths.failed, now=_NOW, days=30, action="gc_purge_failed"
        )

        self.assertEqual(deleted, ["purge old.csv"])
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertEqual([row[0] for row in self.audit("gc_purge_failed")], ["old.csv"])

    def test_purges_quarantine_directories_by_age(self):
        stale = self.paths.quarantine / "case-1"
        stale.mkdir()
        (stale / "a.csv").write_text("id,v\n1,a\n", encoding="utf-8")
        os.utime(stale, (_NOW - 40 * _DAY, _NOW - 40 * _DAY))
        fresh = self.paths.quarantine / "case-2"
        fresh.mkdir()

        gc.purge_old_paths(
            self.conn, self.paths.quarantine, now=_NOW, days=30, action="gc_purge_quarantine"
        )

        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertEqual([row[0] for row in self.audit("gc_purge_quarantine")], ["case-1"])

    def test_a_missing_root_is_not_an_error(self):
        deleted = gc.purge_old_paths(
            self.conn,
            self.paths.root / "absent",
            now=_NOW,
            days=30,
            action="gc_purge_failed",
        )

        self.assertEqual(deleted, [])
