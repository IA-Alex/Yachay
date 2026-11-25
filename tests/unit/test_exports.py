"""Exportación atómica de CSV, XLSX y ODS."""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from motor.model import exports
from motor.sources import read_any
from motor.sources.tables import Table


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.table = Table(
            columns=["text", "flag", "whole", "fraction", "timestamp", "day", "none"],
            rows=[["=1+1", True, 3, 1.5, dt.datetime(2026, 1, 2, 3, 4), dt.date(2026, 1, 3), None]],
        )

    def test_exports_all_formats_and_preserves_formula_like_text(self):
        for suffix in (".csv", ".xlsx", ".ods"):
            with self.subTest(suffix=suffix):
                destination = exports.export_table(self.table, self.root / f"result{suffix}")
                self.assertTrue(destination.is_file())
                self.assertEqual(read_any(destination).columns, self.table.columns)
                self.assertEqual(read_any(destination).rows[0][0], "=1+1")

    def test_rejects_invalid_destinations_and_sizes(self):
        for path in (self.root / "no-extension", self.root / "result.bin"):
            with self.subTest(path=path), self.assertRaises(exports.ExportError):
                exports.export_table(self.table, path)
        with self.assertRaises(exports.ExportError):
            exports.export_table(self.table, self.root / "missing" / "x.csv")
        existing = self.root / "exists.csv"
        existing.write_text("old", encoding="utf-8")
        with self.assertRaises(exports.ExportError):
            exports.export_table(self.table, existing)
        for constant, limit in (
            ("MAX_TABLE_COLUMNS", 1),
            ("MAX_TABLE_ROWS", 1),
            ("MAX_TABLE_CELLS", 1),
        ):
            with patch.object(exports, constant, limit), self.subTest(limit=constant):
                with self.assertRaises(exports.ExportError):
                    exports.export_table(self.table, self.root / f"{constant}.csv")

    def test_publication_failures_are_translated_and_tempfiles_cleaned(self):
        for patched, exception in (
            ("motor.model.exports.os.link", FileExistsError("race")),
            ("motor.model.exports.os.fsync", OSError("disk")),
            ("motor.model.exports._write_csv", OSError("write")),
        ):
            destination = self.root / f"{patched.replace('.', '-')}.csv"
            with patch(patched, side_effect=exception):
                with self.subTest(patched=patched), self.assertRaises(exports.ExportError):
                    exports.export_table(self.table, destination)
            self.assertFalse(destination.exists())
            self.assertFalse(list(self.root.glob(f".{destination.stem}-*.part")))

    def test_xlsx_value_keeps_strings_as_text_and_numbers_native(self):
        class Cell:
            def __init__(self, _sheet, *, value):
                self.value = value
                self.data_type = "f"

        string = exports._xlsx_value(None, "=1+1", Cell)
        self.assertEqual(string.data_type, "s")
        number = exports._xlsx_value(None, 4, Cell)
        self.assertEqual(number, 4)
