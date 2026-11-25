"""Contrato y comportamiento de validaciones de calidad locales."""

from __future__ import annotations

import unittest

from motor.model import quality
from motor.sources.tables import Table


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.table = Table(
            columns=["id", "age", "code"],
            rows=[[1, 20, "AB"], [1, "bad", "bad"], [None, float("inf"), None], ["", "", ""]],
        )

    def test_all_rules_report_failures_without_cell_values(self):
        report = quality.validate(
            self.table,
            [
                {"type": "required", "column": "id"},
                {"type": "unique", "columns": ["id", "code"]},
                {"type": "range", "column": "age", "min": 0, "max": 30},
                {"type": "format", "column": "code", "pattern": "[A-Z]{2}"},
            ],
        )
        self.assertEqual(report["rows"], 4)
        self.assertEqual(report["invalid_rows"], 3)
        self.assertNotIn("bad", str(report))

    def test_contract_shape_columns_and_rule_kind_errors(self):
        for rules in (
            None,
            [None],
            {},
            [{"type": "unknown", "column": "id"}],
            [{"type": "unique", "columns": []}],
            [{"type": "unique", "columns": [1]}],
            [{"type": "required"}],
            [{"type": "required", "column": "missing"}],
        ):
            with self.subTest(rules=rules), self.assertRaises(quality.QualityError):
                quality.validate(self.table, rules)

    def test_range_bounds_reject_invalid_values_and_order(self):
        invalid = [
            {"type": "range", "column": "age"},
            {"type": "range", "column": "age", "min": True},
            {"type": "range", "column": "age", "max": "3"},
            {"type": "range", "column": "age", "min": float("inf")},
            {"type": "range", "column": "age", "min": 2**2000},
            {"type": "range", "column": "age", "min": 4, "max": 3},
        ]
        for rule in invalid:
            with self.subTest(rule=rule), self.assertRaises(quality.QualityError):
                quality.validate(self.table, [rule])

    def test_format_patterns_and_value_failures(self):
        for rule in (
            {"type": "format", "column": "code"},
            {"type": "format", "column": "code", "pattern": ""},
            {"type": "format", "column": "code", "pattern": "["},
        ):
            with self.subTest(rule=rule), self.assertRaises(quality.QualityError):
                quality.validate(self.table, [rule])
        result = quality.validate(
            self.table,
            [
                {"type": "range", "column": "age", "min": 0},
                {"type": "format", "column": "code", "pattern": "[A-Z]+"},
            ],
        )
        self.assertEqual(result["invalid_rows"], 2)
        self.assertEqual(result["valid_rows"], 2)

    def test_missing_cells_and_empty_unique_values(self):
        table = Table(columns=["id", "other"], rows=[[1], [1, None], [None, None]])
        report = quality.validate(
            table,
            [
                {"type": "unique", "columns": ["id", "other"]},
                {"type": "required", "column": "other"},
            ],
        )
        self.assertEqual(report["invalid_rows"], 3)

    def test_private_unknown_kind_fallback_is_true(self):
        self.assertTrue(quality._passes({}, "unknown", "value", None))
