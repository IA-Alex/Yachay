import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.config import MotorConfig
from motor.publishing import publish
from motor.reconcile import reconcile
from motor.storage import jobs
from motor.storage.db import connect
from motor.worker import Worker


class WorkerTests(unittest.TestCase):
    def test_run_once_processes_one_csv_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            (cfg.paths.inbox / "a.csv").write_text(
                "id,v\n1,a\n2,b\n", encoding="utf-8")
            worker = Worker(cfg)
            # B2: a single cycle processes a stable file.
            self.assertIsNotNone(worker.run_once())
            conn = connect(cfg.paths.sqlite_path)
            committed = conn.execute(
                "SELECT COUNT(*) FROM datasets").fetchone()[0]
            self.assertEqual(committed, 1)

    def test_reconcile_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            first = reconcile(conn, cfg.paths)
            second = reconcile(conn, cfg.paths)
            self.assertEqual(second, [])
            self.assertIsInstance(first, list)

    def test_reconcile_cleans_original_left_after_committed_job(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            # Simulate a crash between the SQLite commit and the final unlink:
            # the job is committed, but its original is still in processing/.
            leftover = cfg.paths.processing / "a.csv"
            leftover.write_text("id,v\n1,a\n", encoding="utf-8")
            conn.execute(
                "INSERT INTO jobs (job_id, job_key, state) VALUES ('j1','k1','committed')"
            )
            conn.execute(
                "INSERT INTO input_files (job_id, path, sha256, size_bytes, mtime_ns) "
                "VALUES ('j1', ?, 'h', 8, 1)",
                (str(leftover),),
            )
            actions = reconcile(conn, cfg.paths)
            self.assertFalse(leftover.exists())
            self.assertTrue(any("committed original" in a for a in actions))
            # Idempotent: a second pass removes nothing more.
            self.assertEqual(reconcile(conn, cfg.paths), [])

    def test_reconcile_completes_move_for_claimed_job_still_in_inbox(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            # Crash after enqueue(claimed) but before the move to processing/.
            leftover = cfg.paths.inbox / "a.csv"
            leftover.write_text("id,v\n1,a\n", encoding="utf-8")
            conn.execute(
                "INSERT INTO jobs (job_id, job_key, state) VALUES ('j1','k1','claimed')"
            )
            conn.execute(
                "INSERT INTO input_files (job_id, path, sha256, size_bytes, mtime_ns) "
                "VALUES ('j1', ?, 'h', 8, 1)",
                (str(cfg.paths.processing / "a.csv"),),
            )
            actions = reconcile(conn, cfg.paths)
            self.assertFalse(leftover.exists())
            self.assertTrue((cfg.paths.processing / "a.csv").exists())
            state = conn.execute(
                "SELECT state FROM jobs WHERE job_id='j1'").fetchone()[0]
            self.assertEqual(state, "queued")
            self.assertTrue(any("complete move" in a for a in actions))

    def test_reconcile_advances_claimed_job_already_in_processing(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            moved = cfg.paths.processing / "a.csv"
            moved.write_text("id,v\n1,a\n", encoding="utf-8")
            conn.execute(
                "INSERT INTO jobs (job_id, job_key, state) VALUES ('j1','k1','claimed')"
            )
            conn.execute(
                "INSERT INTO input_files (job_id, path, sha256, size_bytes, mtime_ns) "
                "VALUES ('j1', ?, 'h', 8, 1)",
                (str(moved),),
            )
            actions = reconcile(conn, cfg.paths)
            state = conn.execute(
                "SELECT state FROM jobs WHERE job_id='j1'").fetchone()[0]
            self.assertEqual(state, "queued")
            self.assertTrue(any("advance" in a for a in actions))

    def test_reconcile_rebuilds_dataset_from_orphan_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            # A published dataset on disk with no row in the DB (crash 5..7).
            run_dir = cfg.paths.processed / "demo" / "run_id=orphan"
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                json.dumps({"run_id": "orphan", "job_key": "k9", "dataset": "demo"}),
                encoding="utf-8",
            )
            actions = reconcile(conn, cfg.paths)
            row = conn.execute(
                "SELECT job_key FROM datasets WHERE run_id='orphan'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "k9")
            self.assertTrue(any("rebuild dataset" in a for a in actions))

    def test_reconcile_retires_a_dataset_row_missing_on_disk(self):
        # Contract change (ADR-018, R2): a row whose manifest is gone and that no
        # job backs is not just reported every pass (which spammed the audit and
        # left the API at a permanent 503); the row is retired. It is reversible:
        # if the manifest reappears, the rebuild recreates the row.
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            conn.execute(
                "INSERT INTO datasets (run_id, job_key, dataset, manifest_path, committed_at) "
                "VALUES ('gone','k1','demo','/nonexistent/manifest.json',0)"
            )
            actions = reconcile(conn, cfg.paths)
            self.assertTrue(any("retire dataset gone" in a for a in actions))
            self.assertIsNone(
                conn.execute("SELECT 1 FROM datasets WHERE run_id = 'gone'").fetchone()
            )
            rows = conn.execute(
                "SELECT action, ref_id FROM audit_events "
                "WHERE action IN ('reconcile_missing_dataset', 'reconcile_retire_dataset_row')"
            ).fetchall()
            self.assertIn(("reconcile_retire_dataset_row", "gone"), rows)

    def test_reconcile_keeps_a_row_a_committed_job_still_needs(self):
        # A row without a manifest is left alone when a committed job still
        # references its job_key: retiring it would leave a published result the
        # API can no longer describe.
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            conn.execute(
                "INSERT INTO datasets (run_id, job_key, dataset, manifest_path, committed_at) "
                "VALUES ('kept','k1','demo','/nonexistent/manifest.json',0)"
            )
            conn.execute(
                "INSERT INTO jobs (job_id, job_key, state, attempt) "
                "VALUES ('j1','k1','committed',1)"
            )
            reconcile(conn, cfg.paths)
            self.assertIsNotNone(
                conn.execute("SELECT 1 FROM datasets WHERE run_id = 'kept'").fetchone()
            )

    def test_reconcile_retires_the_row_only_once(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            conn.execute(
                "INSERT INTO datasets (run_id, job_key, dataset, manifest_path, committed_at) "
                "VALUES ('gone','k1','demo','/nonexistent/manifest.json',0)"
            )
            reconcile(conn, cfg.paths)
            second = reconcile(conn, cfg.paths)
            self.assertEqual([a for a in second if "retire dataset" in a], [])
            count = conn.execute(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE action = 'reconcile_retire_dataset_row'"
            ).fetchone()[0]
            self.assertEqual(count, 1)

    def test_reconcile_without_processed_directory(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            orphan = cfg.paths.tmp / "run_id=orphan"
            orphan.mkdir(parents=True)
            (orphan / "part-000.parquet").write_bytes(b"x")
            actions = reconcile(conn, cfg.paths)
            self.assertFalse(orphan.exists())
            self.assertTrue(any("clean tmp" in a for a in actions))

    def test_reconcile_preserves_tmp_of_an_active_run(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            active = cfg.paths.tmp / "run-1"
            active.mkdir(parents=True)
            # An active run is a run whose lease is still alive: a `running`
            # job without a lease is abandoned and recovered as such.
            input_path = cfg.paths.processing / "a.csv"
            input_path.write_text("id,v\n1,a\n", encoding="utf-8")
            conn.execute(
                "INSERT INTO jobs (job_id, job_key, state, run_id, lease_until) "
                "VALUES ('j1','k1','running','run-1', ?)",
                (time.time() + 3600,),
            )
            conn.execute(
                "INSERT INTO input_files (job_id, path, sha256, size_bytes, mtime_ns) "
                "VALUES ('j1', ?, 'h', 8, 1)",
                (str(input_path),),
            )
            reconcile(conn, cfg.paths)
            self.assertTrue(active.exists())

    def test_scan_skips_directories_and_recovers_existing_dataset(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            worker = Worker(cfg)
            # A directory in inbox/ must be ignored, not treated as input.
            (cfg.paths.inbox / "subdir").mkdir()
            self.assertIsNone(worker.run_once())

    def test_second_identical_input_reuses_committed_dataset(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            payload = "id,v\n1,a\n2,b\n"
            (cfg.paths.inbox / "a.csv").write_text(payload, encoding="utf-8")
            worker = Worker(cfg)
            self.assertIsNotNone(worker.run_once())
            conn = connect(cfg.paths.sqlite_path)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 1
            )
            # Same content again: reuse, do not create a second dataset.
            (cfg.paths.inbox / "b.csv").write_text(payload, encoding="utf-8")
            worker.run_once()
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 1
            )

    def test_detection_survives_worker_restart(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            path = cfg.paths.inbox / "a.csv"
            path.write_text("id,v\n1,a\n", encoding="utf-8")
            # A previous process read the file once, then exited.
            from motor.ingestion import scanner

            conn = connect(cfg.paths.sqlite_path)
            scanner.record_snapshot(conn, path)
            conn.close()
            # A fresh worker must still detect stability from persisted state.
            self.assertIsNotNone(Worker(cfg).run_once())
            fresh = connect(cfg.paths.sqlite_path)
            self.assertEqual(
                fresh.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 1
            )

    def test_run_once_b2_processes_first_stable_file_in_one_cycle(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            (cfg.paths.inbox / "a.csv").write_text("id,v\n1,a\n", encoding="utf-8")
            # B2: a single cycle processes a file whose snapshot is fresh.
            self.assertIsNotNone(Worker(cfg).run_once())


class ReconcileLostInputTests(unittest.TestCase):
    """Active jobs whose input cannot be recovered (ADR-014).

    A job that cannot be executed must end in a declared terminal state with an
    audit trail. Leaving it in `claimed`/`queued`/`running` traps the state
    forever; requeueing it only burns attempts until the cap.
    """

    def _job(self, conn, *, job_id="j1", state="running", attempt=1, lease=0.0):
        conn.execute(
            "INSERT INTO jobs (job_id, job_key, state, attempt, lease_until) "
            "VALUES (?, 'k1', ?, ?, ?)",
            (job_id, state, attempt, lease),
        )

    def _register_input(self, cfg, conn, job_id="j1", name="a.csv"):
        conn.execute(
            "INSERT INTO input_files (job_id, path, sha256, size_bytes, mtime_ns) "
            "VALUES (?, ?, 'h', 8, 1)",
            (job_id, str(cfg.paths.processing / name)),
        )

    def _state(self, conn, job_id="j1"):
        return conn.execute(
            "SELECT state, attempt FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()

    def test_job_without_an_input_file_row_fails_instead_of_staying_trapped(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            for job_id, state in (
                ("j1", "claimed"),
                ("j2", "queued"),
                ("j3", "running"),
            ):
                self._job(conn, job_id=job_id, state=state)

            actions = reconcile(conn, cfg.paths)

            for job_id in ("j1", "j2", "j3"):
                self.assertEqual(self._state(conn, job_id)[0], "failed")
            self.assertTrue(any("no input_files row" in a for a in actions))
            # Terminal: a second pass has nothing left to do.
            self.assertEqual(reconcile(conn, cfg.paths), [])

    def test_running_job_whose_input_file_is_gone_fails_without_requeue_churn(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            self._job(conn, state="running", attempt=1, lease=0.0)
            self._register_input(cfg, conn)

            actions = reconcile(conn, cfg.paths)

            # A retry cannot succeed: fail now, not after three lease cycles.
            self.assertEqual(self._state(conn), ("failed", 1))
            self.assertTrue(any("input file missing" in a for a in actions))
            self.assertFalse(any("lease expiry" in a for a in actions))
            self.assertEqual(reconcile(conn, cfg.paths), [])

    def test_queued_job_whose_input_file_is_gone_fails(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            self._job(conn, state="queued", attempt=0, lease=None)
            self._register_input(cfg, conn)

            actions = reconcile(conn, cfg.paths)

            self.assertEqual(self._state(conn)[0], "failed")
            self.assertTrue(any("input file missing" in a for a in actions))

    def test_claimed_job_with_input_in_inbox_is_moved_not_failed(self):
        # The fail branch must not swallow the normal crash window: a claimed
        # job whose file is still in inbox/ is recovered by completing the move.
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            (cfg.paths.inbox / "a.csv").write_text("id,v\n1,a\n", encoding="utf-8")
            self._job(conn, state="claimed", attempt=0, lease=None)
            self._register_input(cfg, conn)

            actions = reconcile(conn, cfg.paths)

            self.assertEqual(self._state(conn)[0], "queued")
            self.assertFalse(any("input file missing" in a for a in actions))


class WorkerAdmissionGateTests(unittest.TestCase):
    """Admission gates of section 5: nothing is lost and nothing is queued."""

    def test_scan_returns_none_when_inbox_is_absent(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            cfg.paths.inbox.rmdir()
            worker = Worker(cfg)
            self.assertIsNone(worker.run_once())
            self.assertEqual(
                worker.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0
            )

    def test_unstable_file_is_not_admitted_in_this_cycle(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            src = cfg.paths.inbox / "a.csv"
            src.write_text("id,v\n1,a\n", encoding="utf-8")
            worker = Worker(cfg)
            # The file changed between the two readings of the cycle: it must
            # stay in inbox/ and produce no job.
            with (
                mock.patch(
                    "motor.ingestion.scanner.record_snapshot", return_value=False
                ),
                mock.patch(
                    "motor.ingestion.scanner.is_stable_persisted", return_value=False
                ),
            ):
                self.assertIsNone(worker.run_once())
            self.assertTrue(src.exists())
            self.assertEqual(
                worker.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0
            )

    def test_insufficient_disk_space_defers_the_file_without_side_effects(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            src = cfg.paths.inbox / "a.csv"
            src.write_text("id,v\n1,a\n", encoding="utf-8")
            worker = Worker(cfg)
            with mock.patch("motor.worker.admission.has_space", return_value=False):
                self.assertIsNone(worker.run_once())
            # Admission denied: the input is neither moved nor lost, and no
            # job or dataset is created.
            self.assertTrue(src.exists())
            self.assertFalse((cfg.paths.processing / "a.csv").exists())
            self.assertEqual(
                worker.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0
            )
            self.assertEqual(
                worker.conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 0
            )


class WorkerCommitTransactionTests(unittest.TestCase):
    """The SQLite commit is one short transaction and never half-applied."""

    def test_failed_commit_rolls_back_and_reconcile_rebuilds_from_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            (cfg.paths.inbox / "a.csv").write_text("id,v\n1,a\n", encoding="utf-8")
            worker = Worker(cfg)
            blocker = connect(cfg.paths.sqlite_path)
            blocker.execute(
                "CREATE TRIGGER fail_dataset_insert BEFORE INSERT ON datasets "
                "BEGIN SELECT RAISE(ABORT, 'boom'); END"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                worker.run_once()

            # Rolled back: no dataset row and the job is not marked committed.
            self.assertEqual(
                worker.conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 0
            )
            state = worker.conn.execute("SELECT state FROM jobs").fetchone()[0]
            self.assertEqual(state, "running")

            # The manifest is already the physical commit, so reconciliation
            # completes the state side (crash window between steps 5 and 7).
            blocker.execute("DROP TRIGGER fail_dataset_insert")
            actions = reconcile(worker.conn, cfg.paths)
            self.assertTrue(any("rebuild dataset" in a for a in actions))
            self.assertEqual(
                worker.conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 1
            )

    def test_unexpected_commit_failure_rolls_back_and_propagates(self):
        # C9 absorbs only the duplicate job_key. Any other failure (disk, lock,
        # trigger) must still roll back and propagate.
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            (cfg.paths.inbox / "a.csv").write_text(
                "id,v\n1,a\n", encoding="utf-8")
            worker = Worker(cfg)

            def boom():
                raise RuntimeError("boom")

            worker.conn.create_function("boom", 0, boom)
            worker.conn.execute(
                "CREATE TRIGGER fail_dataset_insert BEFORE INSERT ON datasets "
                "BEGIN SELECT boom(); END"
            )

            with self.assertRaises(sqlite3.OperationalError):
                worker.run_once()

            self.assertEqual(
                worker.conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 0
            )
            self.assertEqual(
                worker.conn.execute("SELECT state FROM jobs").fetchone()[0],
                "running",
            )

    def test_original_already_gone_after_publish_does_not_fail_the_job(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            src = cfg.paths.inbox / "a.csv"
            src.write_text("id,v\n1,a\n", encoding="utf-8")
            worker = Worker(cfg)
            real_publish = publish.publish_run

            def publish_then_remove(**kwargs):
                manifest = real_publish(**kwargs)
                # The original disappears before the retention unlink.
                (cfg.paths.processing / "a.csv").unlink()
                return manifest

            with mock.patch(
                "motor.worker.publish.publish_run", side_effect=publish_then_remove
            ):
                self.assertIsNotNone(worker.run_once())
            self.assertEqual(
                worker.conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 1
            )


class ReconcileEdgeCaseTests(unittest.TestCase):
    """Reconciliation is a no-op when the directories it expects are gone."""

    def test_reconcile_without_processed_directory(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            cfg.paths.processed.rmdir()
            self.assertEqual(reconcile(conn, cfg.paths), [])

    def test_reconcile_without_tmp_directory(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            cfg.paths.tmp.rmdir()
            self.assertEqual(reconcile(conn, cfg.paths), [])

    def test_reconcile_skips_committed_job_without_input_file_row(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            conn.execute(
                "INSERT INTO jobs (job_id, job_key, state) "
                "VALUES ('j1','k1','committed')"
            )
            # No input_files row: nothing to clean up, and no crash.
            self.assertEqual(reconcile(conn, cfg.paths), [])

    def test_reconcile_fails_a_claimed_job_whose_file_is_nowhere(self):
        # A claimed job whose input is neither in inbox/ nor in processing/ can
        # never run. It must fail with a reason, not stay `claimed` forever.
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            conn.execute(
                "INSERT INTO jobs (job_id, job_key, state) VALUES ('j1','k1','claimed')"
            )
            conn.execute(
                "INSERT INTO input_files (job_id, path, sha256, size_bytes, mtime_ns) "
                "VALUES ('j1', ?, 'h', 8, 1)",
                (str(cfg.paths.processing / "gone.csv"),),
            )
            actions = reconcile(conn, cfg.paths)
            state = conn.execute(
                "SELECT state FROM jobs WHERE job_id='j1'"
            ).fetchone()[0]
            self.assertEqual(state, "failed")
            self.assertTrue(any("input file missing" in a for a in actions))


class ReconcileLeaseRecoveryTests(unittest.TestCase):
    """A dead worker must not trap a job whose result already exists (ADR-014)."""

    def test_reconcile_commits_expired_running_job_whose_dataset_exists(self):
        # Test 6 (red first): the dataset row was rebuilt from its manifest, but
        # the worker died before marking the job committed. The expired lease
        # must not requeue a job whose job_key already has a committed dataset:
        # doing so would re-run the pipeline and hit the unique job_key index.
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            conn.execute(
                "INSERT INTO datasets (run_id, job_key, dataset, manifest_path, "
                "committed_at) VALUES ('r1','k1','demo','/nonexistent/manifest.json',0)"
            )
            conn.execute(
                "INSERT INTO jobs (job_id, job_key, state, attempt, lease_until) "
                "VALUES ('j1','k1','running',1,0)"
            )

            actions = reconcile(conn, cfg.paths)

            state = conn.execute(
                "SELECT state FROM jobs WHERE job_id='j1'").fetchone()[0]
            self.assertEqual(state, "committed")
            self.assertTrue(any("committed" in action for action in actions))
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 1)


    def test_reconcile_commits_after_rebuilding_the_manifest(self):
        # Order matters: rebuild the physical commit first, then complete the
        # job. Requeueing before the rebuild would re-execute committed work.
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            run_dir = cfg.paths.processed / "demo" / "run_id=orphan"
            run_dir.mkdir(parents=True)
            manifest = run_dir / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {"run_id": "orphan", "job_key": "k1", "dataset": "demo"}
                ),
                encoding="utf-8",
            )
            conn.execute(
                "INSERT INTO jobs (job_id, job_key, state, attempt, lease_until) "
                "VALUES ('j1','k1','running',1,0)"
            )

            actions = reconcile(conn, cfg.paths)

            state = conn.execute(
                "SELECT state FROM jobs WHERE job_id='j1'").fetchone()[0]
            self.assertEqual(state, "committed")
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 1
            )
            self.assertTrue(any("rebuild dataset" in a for a in actions))
            self.assertTrue(any("committed" in a for a in actions))

    def test_reconcile_requeues_an_expired_running_job_without_a_dataset(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            # The input is still on disk: this is a lease problem, not a lost
            # input, so the job is recovered by requeueing it.
            (cfg.paths.processing / "a.csv").write_text(
                "id,v\n1,a\n", encoding="utf-8"
            )
            conn.execute(
                "INSERT INTO jobs (job_id, job_key, state, attempt, lease_until) "
                "VALUES ('j1','k1','running',1,0)"
            )
            conn.execute(
                "INSERT INTO input_files (job_id, path, sha256, size_bytes, mtime_ns) "
                "VALUES ('j1', ?, 'h', 8, 1)",
                (str(cfg.paths.processing / "a.csv"),),
            )

            actions = reconcile(conn, cfg.paths)

            row = conn.execute(
                "SELECT state, worker_id, lease_until, attempt FROM jobs "
                "WHERE job_id='j1'"
            ).fetchone()
            self.assertEqual(row, ("queued", None, None, 1))
            self.assertTrue(any("requeue" in a for a in actions))

    def test_reconcile_fails_an_expired_running_job_at_the_attempt_cap(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            (cfg.paths.processing / "a.csv").write_text(
                "id,v\n1,a\n", encoding="utf-8"
            )
            conn.execute(
                "INSERT INTO jobs (job_id, job_key, state, attempt, lease_until) "
                "VALUES ('j1','k1','running',3,0)"
            )
            conn.execute(
                "INSERT INTO input_files (job_id, path, sha256, size_bytes, mtime_ns) "
                "VALUES ('j1', ?, 'h', 8, 1)",
                (str(cfg.paths.processing / "a.csv"),),
            )

            actions = reconcile(conn, cfg.paths)

            state = conn.execute(
                "SELECT state FROM jobs WHERE job_id='j1'").fetchone()[0]
            self.assertEqual(state, "failed")
            self.assertTrue(any("fail j1" in a for a in actions))

    def test_reconcile_skips_a_manifest_whose_job_key_is_already_committed(self):
        # A duplicate run directory must not poison reconciliation: the second
        # insert would violate the unique job_key index.
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            base = cfg.paths.processed / "demo"
            first = base / "run_id=first"
            first.mkdir(parents=True)
            first_manifest = first / "manifest.json"
            first_manifest.write_text(
                json.dumps(
                    {"run_id": "first", "job_key": "k1", "dataset": "demo"}
                ),
                encoding="utf-8",
            )
            conn.execute(
                "INSERT INTO datasets (run_id, job_key, dataset, manifest_path, "
                "committed_at) VALUES ('first','k1','demo',?,0)",
                (str(first_manifest),),
            )
            duplicate = base / "run_id=dup"
            duplicate.mkdir(parents=True)
            (duplicate / "manifest.json").write_text(
                json.dumps({"run_id": "dup", "job_key": "k1", "dataset": "demo"}),
                encoding="utf-8",
            )

            self.assertEqual(reconcile(conn, cfg.paths), [])
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT run_id FROM datasets").fetchone()[0], "first"
            )


class WorkerCrashRecoveryTests(unittest.TestCase):
    """Crash windows between the physical commit and the SQLite commit."""

    def test_crash_after_publish_recovers_on_the_next_cycle(self):
        # Test 11 (red first): the process dies after the manifest is published
        # but before the SQLite transaction. The next worker must finish the
        # state side without rerunning the pipeline and without IntegrityError.
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            (cfg.paths.inbox / "a.csv").write_text(
                "id,v\n1,a\n", encoding="utf-8")
            worker = Worker(cfg)
            real_publish = publish.publish_run

            def publish_then_crash(**kwargs):
                real_publish(**kwargs)
                raise RuntimeError("crash after publish")

            with mock.patch(
                "motor.worker.publish.publish_run", side_effect=publish_then_crash
            ):
                with self.assertRaises(RuntimeError):
                    worker.run_once()

            # The process is gone: its lease is now expired.
            worker.conn.execute("UPDATE jobs SET lease_until = 0")

            fresh = Worker(cfg)
            self.assertIsNone(fresh.run_once())

            self.assertEqual(
                fresh.conn.execute("SELECT state FROM jobs").fetchone()[0],
                "committed",
            )
            self.assertEqual(
                fresh.conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 1
            )
            # One run only: recovery completes state, it does not re-execute.
            self.assertEqual(
                fresh.conn.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0], 1
            )

    def test_commit_absorbs_a_concurrent_commit_for_the_same_job_key(self):
        # C9 (ADR-014): the manifest is the physical commit. If another writer
        # commits the same job_key first, the unique-index failure must end as
        # `committed`, not as a crashed job.
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            (cfg.paths.inbox / "a.csv").write_text(
                "id,v\n1,a\n", encoding="utf-8")
            worker = Worker(cfg)
            real_publish = publish.publish_run

            def publish_with_competing_commit(**kwargs):
                manifest = real_publish(**kwargs)
                worker.conn.execute(
                    "INSERT INTO datasets (run_id, job_key, dataset, "
                    "manifest_path, committed_at) VALUES ('r-other', ?, "
                    "'other', ?, 0)",
                    (kwargs["job_key"], str(manifest)),
                )
                return manifest

            with mock.patch(
                "motor.worker.publish.publish_run",
                side_effect=publish_with_competing_commit,
            ):
                self.assertIsNotNone(worker.run_once())

            self.assertEqual(
                worker.conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 1
            )
            self.assertEqual(
                worker.conn.execute("SELECT state FROM jobs").fetchone()[0],
                "committed",
            )


class ReconcileOrphanTmpFileTests(unittest.TestCase):
    """A stray `tmp/<run_id>.duckdb` is cleaned only when no job owns it."""

    def test_reconcile_cleans_an_orphan_duckdb_file(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            orphan = cfg.paths.tmp / "run-orphan.duckdb"
            orphan.write_bytes(b"x")
            # A file the GC does not recognise is left alone: tmp/ is never
            # swept blindly.
            foreign = cfg.paths.tmp / "notes.txt"
            foreign.write_text("x", encoding="utf-8")

            actions = reconcile(conn, cfg.paths)

            self.assertFalse(orphan.exists())
            self.assertTrue(foreign.exists())
            self.assertTrue(any("clean tmp" in a for a in actions))
            ref_id = conn.execute(
                "SELECT ref_id FROM audit_events WHERE action = 'reconcile_tmp_cleanup'"
            ).fetchone()[0]
            self.assertEqual(ref_id, "run-orphan")

    def test_reconcile_preserves_the_duckdb_file_of_an_active_run(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            active = cfg.paths.tmp / "run-1.duckdb"
            active.write_bytes(b"x")
            input_path = cfg.paths.processing / "a.csv"
            input_path.write_text("id,v\n1,a\n", encoding="utf-8")
            conn.execute(
                "INSERT INTO jobs (job_id, job_key, state, run_id, lease_until) "
                "VALUES ('j1','k1','running','run-1', ?)",
                (time.time() + 3600,),
            )
            conn.execute(
                "INSERT INTO input_files (job_id, path, sha256, size_bytes, mtime_ns) "
                "VALUES ('j1', ?, 'h', 8, 1)",
                (str(input_path),),
            )

            reconcile(conn, cfg.paths)

            self.assertTrue(active.exists())


class WorkerHeartbeatTests(unittest.TestCase):
    """The lease is extended at the start of `_process` and after the pipeline."""

    def test_process_heartbeats_at_start_and_after_the_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            (cfg.paths.inbox / "a.csv").write_text("id,v\n1,a\n", encoding="utf-8")
            ticks = iter([100.0, 110.0, 120.0, 130.0])
            worker = Worker(cfg, clock=lambda: next(ticks))
            real_heartbeat = jobs.heartbeat
            calls = []

            def record(conn, *, job_id, now, ttl):
                calls.append((job_id, now, ttl))
                real_heartbeat(conn, job_id=job_id, now=now, ttl=ttl)

            with mock.patch("motor.worker.jobs.heartbeat", side_effect=record):
                job_id = worker.run_once()

            self.assertIsNotNone(job_id)
            self.assertEqual([call[1] for call in calls], [110.0, 120.0])
            self.assertEqual({call[2] for call in calls}, {cfg.lease_ttl_s})
            lease = worker.conn.execute(
                "SELECT lease_until FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()[0]
            self.assertEqual(lease, 120.0 + cfg.lease_ttl_s)


class WorkerGcHookTests(unittest.TestCase):
    """The GC runs at the end of the cycle and never breaks it."""

    def test_run_once_runs_the_gc_after_the_job_is_committed(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            (cfg.paths.inbox / "a.csv").write_text("id,v\n1,a\n", encoding="utf-8")
            worker = Worker(cfg)
            seen = {}

            def fake_collect(conn, paths, *, now, **kwargs):
                seen["datasets"] = conn.execute(
                    "SELECT COUNT(*) FROM datasets"
                ).fetchone()[0]
                return []

            with mock.patch("motor.worker.gc.collect", side_effect=fake_collect):
                self.assertIsNotNone(worker.run_once())

            self.assertEqual(seen["datasets"], 1)

    def test_the_gc_also_runs_when_no_job_is_claimed(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            worker = Worker(cfg)

            with mock.patch("motor.worker.gc.collect", return_value=[]) as collect:
                self.assertIsNone(worker.run_once())

            collect.assert_called_once()

    def test_a_recoverable_gc_failure_does_not_break_the_cycle(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            (cfg.paths.inbox / "a.csv").write_text("id,v\n1,a\n", encoding="utf-8")
            worker = Worker(cfg)

            with mock.patch("motor.worker.gc.collect", side_effect=OSError("disk")):
                self.assertIsNotNone(worker.run_once())

            self.assertEqual(
                worker.conn.execute("SELECT state FROM jobs").fetchone()[0],
                "committed",
            )


class WorkerUnreadableInputTests(unittest.TestCase):
    """Contenido no confiable que el pipeline no puede leer.

    Un CSV corrupto o un archivo que desaparece entre la reconciliación y el
    procesado es un fallo determinista del trabajo, no del proceso: el worker
    debe fallar el job y seguir vivo (ADR-014 P1-2, ADR-016). Regresión: antes el
    error de DuckDB propagaba fuera de `run_once` y tumbaba el proceso.
    """

    def test_a_corrupt_csv_fails_the_job_instead_of_crashing_the_worker(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            # Bytes binarios: `read_csv_auto` no puede olfatear el formato.
            (cfg.paths.inbox / "roto.csv").write_bytes(bytes(range(256)) * 100)
            worker = Worker(cfg)

            # The cycle must not raise.
            self.assertIsNotNone(worker.run_once())

            self.assertEqual(
                worker.conn.execute("SELECT state FROM jobs").fetchone()[0],
                "failed",
            )
            audited = [
                row[0] for row in worker.conn.execute(
                    "SELECT action FROM audit_events ORDER BY id"
                ).fetchall()
            ]
            self.assertIn("worker_fail_job", audited)

    def test_the_worker_keeps_working_after_failing_a_bad_job(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            (cfg.paths.inbox / "roto.csv").write_bytes(bytes(range(256)) * 100)
            worker = Worker(cfg)
            worker.run_once()

            (cfg.paths.inbox / "bueno.csv").write_text(
                "id,v\n1,a\n", encoding="utf-8")
            worker.run_once()
            worker.run_once()

            states = sorted(
                row[0] for row in worker.conn.execute("SELECT state FROM jobs")
            )
            self.assertEqual(states, ["committed", "failed"])

    def test_a_vanished_input_fails_the_job_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            worker = Worker(cfg)
            # A running job whose registered input is gone: the pipeline must not
            # take the process down with it.
            worker.conn.execute(
                "INSERT INTO jobs (job_id, job_key, state, attempt) "
                "VALUES ('job-x', 'k', 'running', 1)"
            )
            worker.conn.execute(
                "INSERT INTO input_files (job_id, path, sha256, size_bytes, mtime_ns) "
                "VALUES ('job-x', ?, 'h', 3, 0)",
                (str(cfg.paths.processing / "fantasma.csv"),),
            )

            worker._process("job-x")

            self.assertEqual(
                worker.conn.execute(
                    "SELECT state FROM jobs WHERE job_id = 'job-x'"
                ).fetchone()[0],
                "failed",
            )
