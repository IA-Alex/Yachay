import tempfile
import unittest
from pathlib import Path

from motor.storage.db import connect
from motor.storage.schema import _migrate_schema, create_schema


class StorageSchemaTests(unittest.TestCase):
    def test_pragmas_are_applied(self):
        with tempfile.TemporaryDirectory() as d:
            conn = connect(Path(d) / "motor.sqlite3")
            self.assertEqual(
                conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(
                conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(
                conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)

    def test_schema_is_idempotent_and_declares_required_jobs_columns(self):
        with tempfile.TemporaryDirectory() as d:
            conn = connect(Path(d) / "motor.sqlite3")
            create_schema(conn)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
            for required in {"job_id", "job_key", "state", "attempt",
                             "run_id", "worker_id", "lease_until",
                             "cancel_requested", "priority", "created_at"}:
                self.assertIn(required, cols)

    def test_schema_marker_migrates_an_older_database(self):
        with tempfile.TemporaryDirectory() as d:
            conn = connect(Path(d) / "motor.sqlite3")
            conn.execute("UPDATE schema_version SET version = 1")
            create_schema(conn)
            self.assertEqual(
                conn.execute("SELECT version FROM schema_version").fetchone()[0], 4
            )

    def test_schema_marker_is_restored_when_empty(self):
        with tempfile.TemporaryDirectory() as d:
            conn = connect(Path(d) / "motor.sqlite3")
            conn.execute("DELETE FROM schema_version")
            _migrate_schema(conn)
            self.assertEqual(
                conn.execute("SELECT version FROM schema_version").fetchone()[0], 4
            )

    def test_schema_rejects_a_database_newer_than_the_code(self):
        with tempfile.TemporaryDirectory() as d:
            conn = connect(Path(d) / "motor.sqlite3")
            conn.execute("UPDATE schema_version SET version = 99")
            with self.assertRaises(RuntimeError):
                create_schema(conn)
