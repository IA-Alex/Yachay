import sqlite3
import tempfile
import unittest
from pathlib import Path

from motor.storage import jobs
from motor.storage.db import connect


class JobsStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "m.sqlite3")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_claim_next_is_atomic_and_never_returns_same_job_twice(self):
        jobs.enqueue(self.conn, job_id="j1", job_key="k1")
        jobs.mark_queued(self.conn, "j1")
        first = jobs.claim_next(self.conn, worker_id="w", now=0.0, ttl=10.0)
        second = jobs.claim_next(self.conn, worker_id="w", now=0.0, ttl=10.0)
        self.assertEqual(first, "j1")
        self.assertIsNone(second)

    def test_heartbeat_extends_lease(self):
        jobs.enqueue(self.conn, job_id="j1", job_key="k1")
        jobs.mark_queued(self.conn, "j1")
        jobs.claim_next(self.conn, worker_id="w", now=0.0, ttl=10.0)
        jobs.heartbeat(self.conn, job_id="j1", now=5.0, ttl=10.0)
        lease = self.conn.execute(
            "SELECT lease_until FROM jobs WHERE job_id='j1'").fetchone()[0]
        self.assertEqual(lease, 15.0)

    def test_cancel_flag_is_set_and_read_back(self):
        jobs.enqueue(self.conn, job_id="j1", job_key="k1")
        self.assertFalse(jobs.is_cancel_requested(self.conn, "j1"))
        jobs.request_cancel(self.conn, "j1")
        self.assertTrue(jobs.is_cancel_requested(self.conn, "j1"))

    def test_cancel_flag_is_false_for_unknown_job(self):
        self.assertFalse(jobs.is_cancel_requested(self.conn, "missing"))

    def test_mark_queued_only_advances_claimed_jobs(self):
        jobs.enqueue(self.conn, job_id="j1", job_key="k1")
        jobs.mark_queued(self.conn, "j1")
        # A second call must not regress or duplicate the transition.
        jobs.mark_queued(self.conn, "j1")
        state = self.conn.execute(
            "SELECT state FROM jobs WHERE job_id='j1'").fetchone()[0]
        self.assertEqual(state, "queued")


class ExpiredLeaseRequeueTests(unittest.TestCase):
    """Lease recovery and attempt accounting (ADR-014/015)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "m.sqlite3")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _row(self, job_id: str) -> tuple:
        return self.conn.execute(
            "SELECT state, worker_id, lease_until, run_id, attempt FROM jobs "
            "WHERE job_id = ?",
            (job_id,),
        ).fetchone()

    def test_claim_next_increments_attempt_for_each_execution(self):
        jobs.enqueue(self.conn, job_id="j1", job_key="k1")
        jobs.mark_queued(self.conn, "j1")
        jobs.claim_next(self.conn, worker_id="w", now=0.0, ttl=10.0)
        self.assertEqual(self._row("j1")[4], 1)

        jobs.requeue_expired(self.conn, now=100.0, max_attempts=3)
        jobs.claim_next(self.conn, worker_id="w", now=100.0, ttl=10.0)
        self.assertEqual(self._row("j1")[4], 2)

    def test_requeue_expired_requeues_a_running_job_and_clears_the_owner(self):
        jobs.enqueue(self.conn, job_id="j1", job_key="k1")
        jobs.mark_queued(self.conn, "j1")
        jobs.claim_next(self.conn, worker_id="w", now=0.0, ttl=10.0)
        self.conn.execute("UPDATE jobs SET run_id = 'run-1' WHERE job_id = 'j1'")
        # A job that never started must not be reported as recovered.
        jobs.enqueue(self.conn, job_id="j2", job_key="k2")
        jobs.mark_queued(self.conn, "j2")
        self.conn.execute("UPDATE jobs SET lease_until = 0 WHERE job_id = 'j2'")

        outcomes = jobs.requeue_expired(self.conn, now=100.0, max_attempts=3)

        self.assertEqual(outcomes, [("j1", "requeued")])
        self.assertEqual(self._row("j1"), ("queued", None, None, None, 1))
        self.assertEqual(self._row("j2")[0], "queued")

    def test_requeue_expired_leaves_a_live_lease_untouched(self):
        jobs.enqueue(self.conn, job_id="j1", job_key="k1")
        jobs.mark_queued(self.conn, "j1")
        jobs.claim_next(self.conn, worker_id="w", now=0.0, ttl=100.0)

        self.assertEqual(
            jobs.requeue_expired(self.conn, now=50.0, max_attempts=3), []
        )
        self.assertEqual(self._row("j1"), ("running", "w", 100.0, None, 1))

    def test_requeue_expired_recovers_a_running_job_without_a_lease(self):
        # `claim_next` always writes a lease, so a `running` job without one
        # cannot be alive. It must be recovered instead of staying trapped.
        jobs.enqueue(self.conn, job_id="j1", job_key="k1")
        jobs.mark_queued(self.conn, "j1")
        self.conn.execute(
            "UPDATE jobs SET state = 'running', lease_until = NULL WHERE job_id = 'j1'"
        )

        outcomes = jobs.requeue_expired(self.conn, now=100.0, max_attempts=3)

        self.assertEqual(outcomes, [("j1", "requeued")])
        self.assertEqual(self._row("j1")[0], "queued")

    def test_requeue_expired_fails_the_job_at_the_attempt_cap(self):
        jobs.enqueue(self.conn, job_id="j1", job_key="k1")
        jobs.mark_queued(self.conn, "j1")
        jobs.claim_next(self.conn, worker_id="w", now=0.0, ttl=10.0)
        self.conn.execute(
            "UPDATE jobs SET attempt = 3, lease_until = 0 WHERE job_id = 'j1'"
        )

        outcomes = jobs.requeue_expired(self.conn, now=100.0, max_attempts=3)

        self.assertEqual(outcomes, [("j1", "failed")])
        self.assertEqual(self._row("j1"), ("failed", None, None, None, 3))

    def test_mark_queued_does_not_recover_a_running_job(self):
        # Recovery is a separate operation: widening mark_queued would let a
        # normal move silently resurrect a crashed run.
        jobs.enqueue(self.conn, job_id="j1", job_key="k1")
        jobs.mark_queued(self.conn, "j1")
        jobs.claim_next(self.conn, worker_id="w", now=0.0, ttl=10.0)
        self.conn.execute("UPDATE jobs SET lease_until = 0 WHERE job_id = 'j1'")

        jobs.mark_queued(self.conn, "j1")

        self.assertEqual(self._row("j1")[0], "running")


class ClaimRollbackTests(unittest.TestCase):
    def test_claim_next_rolls_back_and_propagates_when_the_update_fails(self):
        calls: list[str] = []

        class FailingConnection:
            """Minimal connection double that fails on the claiming UPDATE."""

            def execute(self, sql, params=None):
                statement = sql.strip().split()[0].upper()
                calls.append(statement)
                if statement == "UPDATE":
                    raise sqlite3.OperationalError("boom")
                return self

            def fetchone(self):
                return None

        with self.assertRaises(sqlite3.OperationalError):
            jobs.claim_next(FailingConnection(), worker_id="w", now=0.0, ttl=1.0)

        # The claim is atomic: a failed UPDATE leaves no open transaction.
        self.assertEqual(calls, ["BEGIN", "UPDATE", "ROLLBACK"])
