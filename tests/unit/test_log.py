"""Worker structured-logging tests (spec section 9, ADR-018).

The log is what journald captures under systemd, so it must be one JSON object
per line and must never carry data values.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from motor import log
from motor.config import MotorConfig
from motor.storage.db import connect


class LogEmitTests(unittest.TestCase):
    """`log.emit` writes a parseable JSON line."""

    def test_writes_one_json_object_per_line(self):
        stream = io.StringIO()
        line = log.emit("cycle", stream=stream, job_id="job-1")

        self.assertEqual(stream.getvalue(), line + "\n")
        record = json.loads(line)
        self.assertEqual(record["component"], "worker")
        self.assertEqual(record["event"], "cycle")
        self.assertEqual(record["job_id"], "job-1")
        self.assertIn("ts", record)

    def test_extra_fields_are_kept_verbatim(self):
        record = json.loads(log.emit("job", stream=io.StringIO(), rows=3, attempt=1))
        self.assertEqual(record["rows"], 3)
        self.assertEqual(record["attempt"], 1)


class WorkerLogTests(unittest.TestCase):
    """The worker logs a line per cycle and per job, never a data value."""

    def _worker(self, *, log_stream):
        from motor.worker import Worker

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = MotorConfig.from_root(Path(tmp.name))
        return Worker(cfg, log_stream=log_stream), cfg

    def test_a_cycle_logs_a_line(self):
        stream = io.StringIO()
        worker, _ = self._worker(log_stream=stream)

        worker.run_once()

        events = [json.loads(line)["event"] for line in stream.getvalue().splitlines()]
        self.assertIn("cycle", events)

    def test_a_processed_job_logs_identifiers_and_no_data_values(self):
        # A marker in the CSV must never appear in the log: only ids and counts.
        secret = "VALOR-SECRETO-12345"
        stream = io.StringIO()
        worker, cfg = self._worker(log_stream=stream)
        inbox = cfg.paths.inbox / "datos.csv"
        inbox.write_text(f"id,nombre\n1,{secret}\n", encoding="utf-8")

        # Two cycles: the scanner needs a stable file before admitting it.
        worker.run_once()
        worker.run_once()

        output = stream.getvalue()
        self.assertIn('"event": "job"', output)
        self.assertNotIn(secret, output)
        record = json.loads(
            next(line for line in output.splitlines() if '"event": "job"' in line)
        )
        self.assertEqual(record["dataset"], "datos")
        self.assertEqual(record["rows"], 1)
        self.assertEqual(record["state"], "committed")


class SchemaTests(unittest.TestCase):
    """The log module has no hidden required configuration."""

    def test_a_fresh_connection_is_not_needed_to_emit(self):
        record = json.loads(log.emit("startup", stream=io.StringIO()))
        self.assertEqual(record["component"], "worker")
        # Sanity: the SQLite authority is reachable (used elsewhere in the suite).
        with tempfile.TemporaryDirectory() as d:
            connect(Path(d) / "m.sqlite3")


if __name__ == "__main__":
    unittest.main()
