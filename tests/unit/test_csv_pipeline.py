import tempfile
import unittest
from pathlib import Path

from motor.pipelines import csv_pipeline


class CsvPipelineTests(unittest.TestCase):
    def test_fingerprint_is_stable_and_type_sensitive(self):
        a = csv_pipeline.schema_fingerprint({"id": "BIGINT", "v": "VARCHAR"})
        b = csv_pipeline.schema_fingerprint({"id": "BIGINT", "v": "VARCHAR"})
        c = csv_pipeline.schema_fingerprint({"id": "BIGINT", "v": "BIGINT"})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_runs_csv_pipeline_at_column_level(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "in.csv"
            src.write_text("id,value\n1,a\n2,\n", encoding="utf-8")
            reader, schema, rows = csv_pipeline.run_csv_pipeline(
                input_path=src,
                duckdb_path=Path(d) / "d.duckdb",
                tmp_dir=Path(d) / "tmp")
            self.assertEqual(rows, 2)
            self.assertIn("id", schema)
            batches = list(reader)
            self.assertEqual(sum(batch.num_rows for batch in batches), 2)

    def test_large_csv_stays_batch_backed(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "large.csv"
            src.write_text(
                "id,value\n" + "".join(f"{i},value-{i}\n" for i in range(100_000)),
                encoding="utf-8",
            )
            reader, _, rows = csv_pipeline.run_csv_pipeline(
                input_path=src,
                duckdb_path=Path(d) / "d.duckdb",
                tmp_dir=Path(d) / "tmp",
            )
            batches = list(reader)
            self.assertEqual(rows, 100_000)
            self.assertGreater(len(batches), 1)
            self.assertEqual(sum(batch.num_rows for batch in batches), rows)

    def test_pipeline_matches_source_reader_latin1_fallback(self):
        from motor.sources import read_any

        with tempfile.TemporaryDirectory() as d:
            cases = (
                ("legacy.csv", b"nombre\nAndr\xe9\n", [["André"]]),
                (
                    "utf8_bom.csv",
                    b"\xef\xbb\xbf" + b"nombre\nAndr\xc3\xa9\n",
                    [["André"]],
                ),
                ("undecodable.csv", bytes([255, 254, 253, 10]), []),
            )
            for name, content, expected_rows in cases:
                with self.subTest(name=name):
                    src = Path(d) / name
                    src.write_bytes(content)

                    expected = read_any(src)
                    reader, _, rows = csv_pipeline.run_csv_pipeline(
                        input_path=src,
                        duckdb_path=Path(d) / f"{name}.duckdb",
                        tmp_dir=Path(d) / f"tmp-{name}",
                    )

                    self.assertEqual(expected.rows, expected_rows)
                    self.assertEqual(rows, len(expected.rows))
                    self.assertEqual(
                        [
                            value
                            for batch in reader
                            for value in batch.column(0).to_pylist()
                        ],
                        [row[0] for row in expected.rows],
                    )
