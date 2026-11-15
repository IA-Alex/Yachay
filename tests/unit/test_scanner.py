import tempfile
import unittest
from pathlib import Path

from motor.ingestion import admission, scanner


class ScannerTests(unittest.TestCase):
    def test_unstable_file_is_not_admitted(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.csv"
            p.write_bytes(b"x")
            first = scanner.snapshot(p)
            p.write_bytes(b"yy")          # cambió entre lecturas
            second = scanner.snapshot(p)
            self.assertFalse(scanner.is_stable(p, previous=first))
            self.assertTrue(scanner.is_stable(p, previous=second))

    def test_hash_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.csv"
            p.write_bytes(b"a,b\n1,2\n")
            self.assertEqual(scanner.file_hash(p), scanner.file_hash(p))

    def test_without_a_previous_reading_the_file_is_not_stable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.csv"
            p.write_bytes(b"x")
            # First sighting: no previous snapshot, so stability is unknown.
            self.assertFalse(scanner.is_stable(p, previous=None))


class SnapshotPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        from motor.storage.db import connect

        self.conn = connect(Path(self.tmp.name) / "m.sqlite3")
        self.path = Path(self.tmp.name) / "a.csv"

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_first_reading_records_and_is_not_stable(self):
        self.path.write_bytes(b"x")
        # No prior snapshot: nothing recorded yet, so not stable.
        self.assertFalse(scanner.is_stable_persisted(self.conn, self.path))
        # The first recording reports there was no previous snapshot.
        self.assertFalse(scanner.record_snapshot(self.conn, self.path))
        # After recording, the file is stable (unchanged since that reading).
        self.assertTrue(scanner.is_stable_persisted(self.conn, self.path))

    def test_unchanged_file_is_stable_across_workers(self):
        self.path.write_bytes(b"data")
        scanner.record_snapshot(self.conn, self.path)
        # A different connection simulates a restarted worker process.
        from motor.storage.db import connect

        other = connect(Path(self.tmp.name) / "m.sqlite3")
        try:
            self.assertTrue(scanner.is_stable_persisted(other, self.path))
        finally:
            other.close()

    def test_changed_file_is_not_stable(self):
        self.path.write_bytes(b"one")
        scanner.record_snapshot(self.conn, self.path)
        self.path.write_bytes(b"a much longer body")
        self.assertFalse(scanner.is_stable_persisted(self.conn, self.path))


class AdmissionTests(unittest.TestCase):
    def test_admission_requires_free_space_over_size_times_factor(self):
        self.assertTrue(admission.has_space(
            free_bytes=300, input_size=100, factor=2.0))
        self.assertFalse(admission.has_space(
            free_bytes=199, input_size=100, factor=2.0))
