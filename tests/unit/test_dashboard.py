from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from motor.dashboard import generate_report


class DashboardTests(unittest.TestCase):
    def test_numeric_report_is_offline_accessible_and_escapes_title(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "result.parquet"
            pq.write_table(pa.table({"amount": [1.0, 2.5], "label": ["<x>", "ok"]}), source)
            report = generate_report(source, root / "report.html", title="<script>unsafe</script>")
            content = report.read_text(encoding="utf-8")
            self.assertIn("&lt;script&gt;unsafe&lt;/script&gt;", content)
            self.assertIn("aria-label=\"Serie de valores: amount\"", content)
            self.assertIn("&lt;x&gt;", content)
            self.assertIn("default-src 'none'", content)
            self.assertNotIn("https://", content)
            self.assertEqual(report.stat().st_mode & 0o777, 0o640)

    def test_categorical_report_groups_and_limits_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "result.parquet"
            pq.write_table(pa.table({"kind": ["a", "a", "b"]}), source)
            content = generate_report(source, root / "report.html", title="Catálogo")
            text = content.read_text(encoding="utf-8")
            self.assertIn("Conteo por kind", text)
            self.assertIn("3 filas.", text)

    def test_report_notes_when_more_than_500_rows_are_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "many.parquet"
            pq.write_table(pa.table({"label": list(range(501))}), source)
            content = generate_report(source, root / "report.html", title="Grande")
            self.assertIn("Se muestran 500 de 501 filas.", content.read_text())

    def test_empty_report_has_no_chart_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "empty.parquet"
            pq.write_table(pa.table({"x": pa.array([], type=pa.string())}), source)
            content = generate_report(source, root / "report.html", title="Vacío")
            self.assertIn("No hay datos para representar", content.read_text(encoding="utf-8"))
