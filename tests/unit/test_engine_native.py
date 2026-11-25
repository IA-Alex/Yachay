"""Boundaries and row behavior for native table-engine operations."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from motor.limits import MAX_TABLE_CELLS, MAX_TABLE_COLUMNS, MAX_TABLE_ROWS
from motor.model import engine
from motor.model.rules import Rule
from motor.sources.tables import Table


class NativeEngineTests(unittest.TestCase):
    def setUp(self):
        self.left = Table(columns=["id", "name"], rows=[[1, "a"], [2]])
        self.right = Table(columns=["key", "value"], rows=[[1, "x"]])

    def test_append_requires_matching_nonempty_schemas_and_preserves_order(self):
        with self.assertRaises(engine.EngineError):
            engine.append_tables([self.left])
        with self.assertRaises(engine.EngineError):
            engine.append_tables([Table(columns=[], rows=[]), Table(columns=[], rows=[])])
        with self.assertRaises(engine.EngineError):
            engine.append_tables([self.left, self.right])
        appended = engine.append_tables(
            [Table(columns=["x"], rows=[[1]]), Table(columns=["x"], rows=[[2]])]
        )
        self.assertEqual(appended.rows, [["1"], ["2"]])

    def test_alignment_rejects_bad_maps_and_final_schema(self):
        for tables, maps, columns in (
            ([self.left], None, None),
            ([self.left, self.right], [{}], None),
            ([self.left, self.right], [None, {}], None),
            ([self.left, self.right], [{"missing": "x"}, {}], None),
            ([self.left, self.right], [{"id": " "}, {}], None),
            ([self.left, self.right], [{"id": "x", "name": "x"}, {}], None),
            ([self.left, self.right], None, "not-a-list"),
            ([self.left, self.right], None, [""]),
            ([self.left, self.right], None, ["x", "x"]),
        ):
            with self.subTest(tables=len(tables), maps=maps, columns=columns):
                with self.assertRaises(engine.EngineError):
                    engine.align_tables(tables, column_maps=maps, columns=columns)
        with patch("motor.model.engine.MAX_TABLE_COLUMNS", 1):
            with self.assertRaises(engine.EngineError):
                engine.align_tables([self.left, self.right])
        with patch("motor.model.engine.MAX_TABLE_ROWS", 2):
            with self.assertRaises(engine.EngineError):
                engine.align_tables([self.left, self.right])
        with patch("motor.model.engine.MAX_TABLE_CELLS", 2):
            with self.assertRaises(engine.EngineError):
                engine.align_tables([self.left, self.right])
        self.assertGreater(MAX_TABLE_COLUMNS, 0)
        self.assertGreater(MAX_TABLE_ROWS, 0)
        self.assertGreater(MAX_TABLE_CELLS, 0)

    def test_alignment_covers_unmapped_columns_and_short_rows(self):
        table, report = engine.align_tables(
            [self.left, self.right],
            column_maps=[{}, {"key": "id", "value": "unused"}],
            columns=["id", "name"],
        )
        self.assertEqual(table.rows, [[1, "a"], [2, None], [1, None]])
        self.assertEqual(report["omitted_columns"], ["unused"])

    def test_deduplicate_validates_keys_and_keeps_first_or_last(self):
        for keys, keep in (([], "first"), (["missing"], "first"), (["id"], "other")):
            with self.subTest(keys=keys, keep=keep), self.assertRaises(engine.EngineError):
                engine.deduplicate(self.left, keys=keys, keep=keep)
        table = Table(
            columns=["id", "v"], rows=[[1, "first"], [1, "last"], [None, "n1"], [None, "n2"]]
        )
        first, first_report = engine.deduplicate(table, keys=["id"])
        last, last_report = engine.deduplicate(table, keys=["id"], keep="last")
        self.assertEqual(first.rows, [[1, "first"], [None, "n1"], [None, "n2"]])
        self.assertEqual(last.rows, [[1, "last"], [None, "n1"], [None, "n2"]])
        self.assertEqual(first_report["duplicates_removed"], 1)
        self.assertEqual(last_report["duplicates_removed"], 1)

    def test_rule_pipeline_carries_unmodified_columns_through_later_stages(self):
        table = Table(columns=["amount", "label"], rows=[["2", "keep"]])
        result = engine.run(
            table,
            [Rule(name="to_number", type="cast", column="amount", params={"to": "number"})],
        )
        self.assertEqual(result.rows, [[2.0, "keep"]])
