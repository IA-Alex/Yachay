"""Transformaciones nativas declarativas (ADR-021)."""

from __future__ import annotations

import unittest

from motor.model import transforms
from motor.sources.tables import Table


class NativeTransformTests(unittest.TestCase):
    def setUp(self):
        self.table = Table(
            columns=["group", "month", "amount", "date"],
            rows=[
                ["A", "Jan", "10", "2026-01-02"],
                ["A", "Feb", "20", "2026-02-03"],
                ["B", "Jan", "5", "2026-03-04"],
            ],
        )

    def test_derive_column_materializes_query_results(self):
        result = transforms.derive_column(
            self.table, name="double", expression='number(col("amount")) * 2'
        )
        self.assertEqual(result.columns[-1], "double")
        self.assertEqual([row[-1] for row in result.rows], [20.0, 40.0, 10.0])

    def test_aggregate_table_materializes_grouped_results(self):
        result = transforms.aggregate_table(
            self.table,
            by=["group"],
            metrics=[{"function": "sum", "column": "amount", "name": "total"}],
        )
        self.assertEqual(result.columns, ["group", "total"])
        self.assertEqual(result.rows, [["A", 30.0], ["B", 5.0]])

    def test_pivot_and_unpivot_keep_declared_values(self):
        pivoted = transforms.pivot_table(
            self.table, index=["group"], pivot="month", value="amount"
        )
        self.assertEqual(pivoted.columns, ["group", "Feb", "Jan"])
        self.assertEqual(pivoted.rows, [["A", 20.0, 10.0], ["B", None, 5.0]])
        unpivoted = transforms.unpivot_table(
            self.table, index=["group"], columns=["month", "amount"]
        )
        self.assertEqual(len(unpivoted.rows), 6)
        self.assertEqual(unpivoted.rows[0], ["A", "month", "Jan"])

    def test_normalize_uses_only_declared_formats_and_reports_failures(self):
        result, report = transforms.normalize_table(
            self.table,
            numbers={"amount": {"decimal": ".", "thousands": ","}},
            dates={"date": "%Y-%m-%d"},
        )
        self.assertEqual(result.rows[0][2:], [10.0, "2026-01-02"])
        self.assertEqual(report["failure_count"], 0)


if __name__ == "__main__":
    unittest.main()
