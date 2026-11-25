"""Transformaciones nativas declarativas (ADR-021)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import duckdb

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
        pivoted = transforms.pivot_table(self.table, index=["group"], pivot="month", value="amount")
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

    def test_derive_rejects_unsafe_expressions_and_replaces_existing_column(self):
        for name, expression in (
            ("", "1"),
            ("x", ""),
            ("x", "True"),
            ("x", "10 ** 1000"),
            ("x", 'col("missing")'),
            ("x", 'abs(col("group"))'),
            ("x", "text(value='x')"),
            ("x", "obj.attribute"),
            ("x", "unknown(1)"),
            ("x", "col[0]"),
            ("x", "".join(["1"] * 501)),
        ):
            with (
                self.subTest(name=name, expression=expression),
                self.assertRaises(transforms.TransformError),
            ):
                transforms.derive_column(self.table, name=name, expression=expression)
        replaced = transforms.derive_column(
            self.table, name="amount", expression='number(col("amount")) + 1'
        )
        self.assertEqual(replaced.columns, self.table.columns)
        self.assertEqual(replaced.rows[0][2], 11.0)

    def test_derive_translates_duckdb_errors_and_checks_all_size_limits(self):
        with patch.object(transforms, "_as_table", side_effect=duckdb.Error("bad query")):
            with self.assertRaises(transforms.TransformError):
                transforms.derive_column(self.table, name="x", expression="1")
        with patch.object(transforms, "MAX_TABLE_COLUMNS", 4):
            with self.assertRaises(transforms.TransformError):
                transforms.derive_column(self.table, name="extra", expression="1")
        with patch.object(transforms, "MAX_TABLE_ROWS", 3):
            with self.assertRaises(transforms.TransformError):
                transforms.derive_column(self.table, name="x", expression="1")
        with patch.object(transforms, "MAX_TABLE_CELLS", 8):
            with self.assertRaises(transforms.TransformError):
                transforms.derive_column(self.table, name="x", expression="1")

    def test_aggregate_validates_contract_and_computes_without_grouping(self):
        bad_inputs = [
            (None, []),
            (["group", "group"], [{"function": "count", "name": "n"}]),
            ([1], [{"function": "count", "name": "n"}]),
            ([], None),
            ([], [1]),
            ([], []),
            (["unknown"], [{"function": "count", "name": "n"}]),
            ([], [{"function": "median", "name": "x"}]),
            ([], [{"function": "count", "name": ""}]),
            (["group"], [{"function": "count", "name": "group"}]),
            ([], [{"function": "max", "column": "unknown", "name": "x"}]),
        ]
        for by, metrics in bad_inputs:
            with self.subTest(by=by, metrics=metrics), self.assertRaises(transforms.TransformError):
                transforms.aggregate_table(self.table, by=by, metrics=metrics)
        for function, value in (("sum", "bad"), ("avg", "inf")):
            table = Table(columns=["value"], rows=[[value]])
            with self.subTest(function=function), self.assertRaises(transforms.TransformError):
                transforms.aggregate_table(
                    table, by=[], metrics=[{"function": function, "column": "value", "name": "x"}]
                )
        sparse = Table(columns=["value"], rows=[[None], [""]])
        self.assertEqual(
            transforms.aggregate_table(
                sparse, by=[], metrics=[{"function": "sum", "column": "value", "name": "total"}]
            ).rows,
            [[None]],
        )
        result = transforms.aggregate_table(
            self.table,
            by=[],
            metrics=[
                {"function": "count", "name": "n"},
                {"function": "min", "column": "group", "name": "first"},
            ],
        )
        self.assertEqual(result.rows, [[3, "A"]])
        with patch.object(transforms, "_load", side_effect=duckdb.Error("bad query")):
            with self.assertRaises(transforms.TransformError):
                transforms.aggregate_table(
                    self.table, by=[], metrics=[{"function": "count", "name": "n"}]
                )

    def test_pivot_validates_inputs_and_supports_all_aggregates(self):
        invalid = [
            {"index": ["group", "group"], "pivot": "month", "value": "amount"},
            {"index": [1], "pivot": "month", "value": "amount"},
            {"index": [], "pivot": "missing", "value": "amount"},
            {"index": [], "pivot": "month", "value": "amount", "function": "bad"},
            {"index": [], "pivot": "month", "value": "month"},
            {"index": ["month"], "pivot": "month", "value": "amount"},
            {"index": [], "pivot": "month", "value": "amount", "max_columns": 0},
            {"index": [], "pivot": "month", "value": "amount", "max_columns": 1},
        ]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(transforms.TransformError):
                transforms.pivot_table(self.table, **kwargs)
        # Different Python values with the same string representation cannot form headers.
        collision = Table(columns=["p", "v"], rows=[[1, 1], ["1", 2]])
        with self.assertRaises(transforms.TransformError):
            transforms.pivot_table(collision, index=[], pivot="p", value="v")
        for function, expected in (
            ("count", 2),
            ("sum", 3.0),
            ("avg", 1.5),
            ("min", "1"),
            ("max", "2"),
        ):
            table = Table(columns=["i", "p", "v"], rows=[["a", "x", "1"], ["a", "x", "2"]])
            result = transforms.pivot_table(
                table, index=["i"], pivot="p", value="v", function=function
            )
            self.assertEqual(result.rows[0][1], expected)
        no_pivot = Table(columns=["i", "p", "v"], rows=[["a", None, "1"]])
        self.assertEqual(
            transforms.pivot_table(no_pivot, index=["i"], pivot="p", value="v").rows, []
        )
        null_values = Table(columns=["i", "p", "v"], rows=[["a", "x", None]])
        self.assertIsNone(
            transforms.pivot_table(null_values, index=["i"], pivot="p", value="v").rows[0][1]
        )

    def test_unpivot_checks_contract_limits_and_short_rows(self):
        invalid = [
            {"index": ["group", "group"], "columns": ["amount"]},
            {"index": [1], "columns": ["amount"]},
            {"index": [], "columns": ["amount", "amount"]},
            {"index": [], "columns": ["unknown"]},
            {"index": [], "columns": []},
            {"index": [], "columns": ["amount"], "name_column": "value", "value_column": "value"},
            {"index": ["group"], "columns": ["amount"], "name_column": "group"},
        ]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(transforms.TransformError):
                transforms.unpivot_table(self.table, **kwargs)
        with patch.object(transforms, "MAX_TABLE_CELLS", 3):
            with self.assertRaises(transforms.TransformError):
                transforms.unpivot_table(self.table, index=[], columns=["amount"])
        short = Table(columns=["i", "v"], rows=[["a"]])
        result = transforms.unpivot_table(short, index=["i"], columns=["v"])
        self.assertEqual(result.rows, [["a", "v", None]])

    def test_normalize_rejects_bad_specs_and_reports_parse_errors(self):
        invalid = [
            {"numbers": ["x"]},
            {"dates": ["x"]},
            {"numbers": {"missing": {}}},
            {"numbers": {"amount": []}},
            {"numbers": {"amount": {"extra": 1}}},
            {"numbers": {"amount": {"decimal": ";"}}},
            {"numbers": {"amount": {"decimal": ".", "thousands": "."}}},
            {"dates": {"date": 1}},
            {"dates": {"date": ""}},
        ]
        for config in invalid:
            with (
                self.subTest(config=config),
                self.assertRaises(transforms.TransformError),
            ):
                transforms.normalize_table(self.table, **config)
        bad = Table(columns=["n", "d"], rows=[["bad", "not-a-date"], [None, ""]])
        normalized, report = transforms.normalize_table(
            bad, numbers={"n": {"decimal": ","}}, dates={"d": "%Y-%m-%d"}
        )
        self.assertEqual(report["failure_count"], 2)
        self.assertEqual(normalized.rows[1], [None, ""])
        for text in ("1,23", "1,234.5", "1e999", "-7.2"):
            with self.subTest(text=text):
                try:
                    transforms._parse_number(text, decimal=".", thousands=",")
                except ValueError:
                    self.assertNotIn(text, ("1,234.5", "-7.2"))
        self.assertEqual(transforms._parse_number("1.234,50", decimal=",", thousands="."), 1234.5)
        self.assertEqual(transforms._parse_number("1.23", decimal=".", thousands=""), 1.23)
        timed = Table(columns=["when"], rows=[["2026-01-02 03:04:05"]])
        timed_result, _ = transforms.normalize_table(timed, dates={"when": "%Y-%m-%d %H:%M:%S"})
        self.assertEqual(timed_result.rows, [["2026-01-02T03:04:05"]])
        for text, decimal, thousands in (("x", ";", ""), ("x", ".", ".")):
            with (
                self.subTest(decimal=decimal, thousands=thousands),
                self.assertRaises(transforms.TransformError),
            ):
                transforms._parse_number(text, decimal=decimal, thousands=thousands)

    def test_expression_helpers_check_operators_and_literals(self):
        for expr in (
            'col("group") + 1',
            "-1",
            "+1",
            'text(col("group"))',
            'lower(col("group"))',
            'upper(col("group"))',
            'trim(col("group"))',
            'concat(col("group"), "!")',
            'coalesce(col("group"), "ok")',
            'round(number(col("amount")), 0)',
            'abs(number(col("amount")))',
            "'quoted'",
            "2.5",
        ):
            transforms._compile_expression(expr, set(self.table.columns))
        for expr in ("1e999", "9" * 400):
            with self.subTest(expr=expr), self.assertRaises(transforms.TransformError):
                transforms._compile_expression(expr, set(self.table.columns))
        with self.assertRaises(transforms.TransformError):
            transforms._compile_expression("(", set(self.table.columns))
        with self.assertRaises(transforms.TransformError):
            transforms._aggregate_values(["bad"], "sum")
        with self.assertRaises(transforms.TransformError):
            transforms._aggregate_values(["inf"], "avg")
        self.assertEqual(transforms._aggregate_values([], "sum"), None)
        self.assertEqual(transforms._aggregate_values([None, "a"], "count"), 1)
        self.assertEqual(transforms._aggregate_values([], "count"), 0)
        self.assertTrue(transforms._aggregate_values({"x": "y"}, "unknown"))


if __name__ == "__main__":
    unittest.main()
