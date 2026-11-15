import json
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from motor.pipelines.csv_pipeline import schema_fingerprint
from motor.publishing import publish


class PublishTests(unittest.TestCase):
    def test_publish_writes_parquet_manifest_and_atomic_latest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            table = pa.table({"id": [1, 2], "v": ["a", "b"]})
            manifest = publish.publish_run(
                table=table,
                schema_fingerprint=schema_fingerprint({"id": "BIGINT"}),
                dataset="demo", run_id="run-1", job_key="k1",
                processed_dir=root / "processed", tmp_dir=root / "tmp")
            self.assertTrue(manifest.is_file())
            data = json.loads(manifest.read_text())
            self.assertEqual(data["run_id"], "run-1")
            self.assertEqual(data["rows"], 2)
            self.assertTrue(data["parts"][0]["sha256"])
            latest = manifest.parents[1] / "latest"
            self.assertTrue(latest.is_symlink())
            self.assertEqual(latest.resolve(), manifest.parent.resolve())

    def test_publish_merges_extra_manifest_fields(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            table = pa.table({"id": [1]})
            manifest = publish.publish_run(
                table=table,
                schema_fingerprint=schema_fingerprint({"id": "BIGINT"}),
                dataset="demo", run_id="run-1", job_key="k1",
                processed_dir=root / "processed", tmp_dir=root / "tmp",
                extra={"base_run_id": "run-0"})
            data = json.loads(manifest.read_text())
            self.assertEqual(data["base_run_id"], "run-0")

    def test_publish_consumes_record_batch_reader_without_materializing_table(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            batches = [
                pa.record_batch({"id": [1], "v": ["a"]}),
                pa.record_batch({"id": [2], "v": ["b"]}),
            ]
            reader = pa.RecordBatchReader.from_batches(batches[0].schema, batches)
            manifest = publish.publish_run(
                table=reader,
                row_count=2,
                schema_fingerprint=schema_fingerprint({"id": "BIGINT"}),
                dataset="demo", run_id="run-reader", job_key="k-reader",
                processed_dir=root / "processed", tmp_dir=root / "tmp",
            )
            data = json.loads(manifest.read_text())
            self.assertEqual(data["rows"], 2)
            self.assertEqual(
                pq.read_table(manifest.parent / "part-000.parquet").num_rows,
                2,
            )

    def test_publish_refuses_to_overwrite_an_existing_run(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            table = pa.table({"id": [1]})
            args = dict(
                schema_fingerprint=schema_fingerprint({"id": "BIGINT"}),
                dataset="demo", run_id="run-1", job_key="k1",
                processed_dir=root / "processed", tmp_dir=root / "tmp")
            publish.publish_run(table=table, **args)
            # A second publish under the same run_id must fail loudly.
            with self.assertRaises(FileExistsError):
                publish.publish_run(table=table, **{**args, "tmp_dir": root / "tmp2"})

    def test_publish_clears_stale_latest_tmp(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            table = pa.table({"id": [1]})
            # A leftover .latest.tmp from a crashed prior run must be replaced.
            dataset_dir = root / "processed" / "demo"
            dataset_dir.mkdir(parents=True)
            (dataset_dir / ".latest.tmp").write_text("stale")
            manifest = publish.publish_run(
                table=table,
                schema_fingerprint=schema_fingerprint({"id": "BIGINT"}),
                dataset="demo", run_id="run-1", job_key="k1",
                processed_dir=root / "processed", tmp_dir=root / "tmp")
            latest = manifest.parents[1] / "latest"
            self.assertTrue(latest.is_symlink())
            self.assertFalse((dataset_dir / ".latest.tmp").exists())
