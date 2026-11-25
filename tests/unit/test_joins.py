"""Cruces entre entidades (ADR-019, M4; funciones B3, C5).

Un cruce une dos tablas por una o varias columnas. Tres tipos:

- ``inner``: solo las filas que coinciden en ambas.
- ``left``:  todas las de la izquierda, con la derecha o vacío.
- ``anti``:  **huérfanas**: las de la izquierda que NO están en la derecha.

Los huérfanos se piden con ``anti`` explícito y se entregan como informe, no se
mezclan con el resultado del cruce.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from motor.model import engine, joins, store
from motor.sources.tables import Table
from motor.storage.db import connect


class JoinRenderTests(unittest.TestCase):
    """Cada tipo de cruce renderiza el SQL correcto."""

    def test_inner_join(self):
        sql = joins.render(
            {
                "left": "ventas",
                "right": "clientes",
                "on": [{"left": "cliente", "right": "nombre"}],
                "type": "inner",
            }
        )
        self.assertIn("JOIN", sql)
        self.assertIn("clientes", sql)

    def test_left_join(self):
        sql = joins.render(
            {"left": "v", "right": "c", "on": [{"left": "a", "right": "b"}], "type": "left"}
        )
        self.assertIn("LEFT JOIN", sql)

    def test_anti_join_selects_orphans(self):
        sql = joins.render(
            {"left": "v", "right": "c", "on": [{"left": "a", "right": "b"}], "type": "anti"}
        )
        # Los huérfanos son las filas de la izquierda sin pareja: el cruce es un
        # LEFT JOIN y la condición de huérfano la añade el motor aparte.
        self.assertIn("LEFT JOIN", sql)
        condition = joins.orphan_condition({"on": [{"left": "a", "right": "b"}]})
        self.assertIn("IS NULL", condition)

    def test_join_on_multiple_columns(self):
        sql = joins.render(
            {
                "left": "v",
                "right": "c",
                "on": [{"left": "a", "right": "x"}, {"left": "b", "right": "y"}],
                "type": "inner",
            }
        )
        self.assertIn('"a"', sql)
        self.assertIn('"y"', sql)

    def test_unknown_type_raises(self):
        with self.assertRaises(joins.UnknownJoinType):
            joins.render(
                {"left": "v", "right": "c", "on": [{"left": "a", "right": "b"}], "type": "cross"}
            )

    def test_join_without_columns_raises(self):
        with self.assertRaises(joins.UnknownJoinType):
            joins.render({"left": "v", "right": "c", "on": [], "type": "inner"})

    def test_orphan_condition_without_columns_raises(self):
        with self.assertRaises(joins.UnknownJoinType):
            joins.orphan_condition({"on": []})

    def test_delete_a_join(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = connect(Path(tmp.name) / "m.sqlite3")
        model_id = store.create(conn, name="m")
        joins.add(
            conn,
            model_id,
            name="j",
            left="a",
            right="b",
            on=[{"left": "x", "right": "y"}],
            type="inner",
        )
        joins.delete(conn, model_id, name="j")
        self.assertEqual(joins.list_for(conn, model_id), [])


class JoinStoreTests(unittest.TestCase):
    """Los cruces se guardan con nombre en el modelo."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = connect(Path(self._tmp.name) / "m.sqlite3")
        self.model_id = store.create(self.conn, name="m")

    def test_add_and_list_a_join(self):
        joins.add(
            self.conn,
            self.model_id,
            name="v_c",
            left="ventas",
            right="clientes",
            on=[{"left": "cliente", "right": "nombre"}],
            type="inner",
        )
        stored = joins.list_for(self.conn, self.model_id)
        self.assertEqual(stored[0].name, "v_c")
        self.assertEqual(stored[0].type, "inner")
        self.assertEqual(stored[0].on, [{"left": "cliente", "right": "nombre"}])

    def test_multiple_joins_keep_their_order(self):
        joins.add(
            self.conn,
            self.model_id,
            name="j1",
            left="a",
            right="b",
            on=[{"left": "x", "right": "y"}],
            type="inner",
        )
        joins.add(
            self.conn,
            self.model_id,
            name="j2",
            left="c",
            right="d",
            on=[{"left": "x", "right": "y"}],
            type="left",
        )
        self.assertEqual([j.name for j in joins.list_for(self.conn, self.model_id)], ["j1", "j2"])

    def test_duplicate_name_is_rejected(self):
        joins.add(
            self.conn,
            self.model_id,
            name="j",
            left="a",
            right="b",
            on=[{"left": "x", "right": "y"}],
            type="inner",
        )
        with self.assertRaises(joins.DuplicateJoin):
            joins.add(
                self.conn,
                self.model_id,
                name="j",
                left="c",
                right="d",
                on=[{"left": "x", "right": "y"}],
                type="inner",
            )

    def test_unknown_type_is_rejected(self):
        with self.assertRaises(joins.UnknownJoinType):
            joins.add(
                self.conn,
                self.model_id,
                name="j",
                left="a",
                right="b",
                on=[{"left": "x", "right": "y"}],
                type="bad",
            )


class JoinEngineTests(unittest.TestCase):
    """El motor cruza dos tablas y devuelve el resultado o los huérfanos."""

    def _ventas(self):
        return Table(
            columns=["cliente", "monto"],
            rows=[["ana", "120"], ["beto", "340"], ["fantasma", "99"]],
        )

    def _clientes(self):
        return Table(
            columns=["nombre", "ciudad"],
            rows=[["ana", "Cusco"], ["beto", "Lima"]],
        )

    def test_inner_join_keeps_matching_rows(self):
        result = engine.run_join(
            self._ventas(),
            self._clientes(),
            joins.Join(
                name="j",
                left="ventas",
                right="clientes",
                on=[{"left": "cliente", "right": "nombre"}],
                type="inner",
            ),
        )
        # El cruce trae TODAS las columnas de ambos lados (izquierda primero); el
        # usuario decide después qué conservar con las reglas (M3).
        self.assertEqual(result.columns, ["cliente", "monto", "nombre", "ciudad"])
        self.assertEqual(
            result.rows,
            [["ana", "120", "ana", "Cusco"], ["beto", "340", "beto", "Lima"]],
        )

    def test_left_join_keeps_unmatched_rows_with_nulls(self):
        result = engine.run_join(
            self._ventas(),
            self._clientes(),
            joins.Join(
                name="j",
                left="ventas",
                right="clientes",
                on=[{"left": "cliente", "right": "nombre"}],
                type="left",
            ),
        )
        self.assertEqual(len(result.rows), 3)  # incluida 'fantasma'
        fila = next(r for r in result.rows if r[0] == "fantasma")
        self.assertIsNone(fila[3])  # sin ciudad

    def test_anti_join_returns_orphans(self):
        result = engine.run_join(
            self._ventas(),
            self._clientes(),
            joins.Join(
                name="j",
                left="ventas",
                right="clientes",
                on=[{"left": "cliente", "right": "nombre"}],
                type="anti",
            ),
        )
        # 'fantasma' está en ventas y no en clientes.
        self.assertEqual([row[0] for row in result.rows], ["fantasma"])

    def test_join_on_an_unknown_column_fails(self):
        with self.assertRaises(engine.EngineError):
            engine.run_join(
                self._ventas(),
                self._clientes(),
                joins.Join(
                    name="j",
                    left="ventas",
                    right="clientes",
                    on=[{"left": "no_existe", "right": "nombre"}],
                    type="inner",
                ),
            )

    def test_join_result_is_idempotent(self):
        join = joins.Join(
            name="j", left="v", right="c", on=[{"left": "cliente", "right": "nombre"}], type="inner"
        )
        first = engine.run_join(self._ventas(), self._clientes(), join)
        second = engine.run_join(self._ventas(), self._clientes(), join)
        self.assertEqual(first.rows, second.rows)

    def test_sources_named_like_files_are_accepted(self):
        # Regresión: un nombre con punto ("ventas.csv") hacía que DuckDB lo
        # tratara como una ruta de archivo y fallara. Se registran con alias.
        left = Table(columns=["k"], rows=[["1"]])
        right = Table(columns=["k", "v"], rows=[["1", "a"]])
        result = engine.run_chain(
            {"ventas.csv": left, "clientes.csv": right},
            [
                joins.Join(
                    name="j",
                    left="ventas.csv",
                    right="clientes.csv",
                    on=[{"left": "k", "right": "k"}],
                    type="inner",
                )
            ],
        )
        self.assertEqual(result.rows, [["1", "1", "a"]])

    def test_diagnose_rejects_missing_columns_and_reports_cardinality(self):
        left = Table(columns=["k"], rows=[["a"], ["a"], [None]])
        right = Table(columns=["r"], rows=[["a"], ["a"], ["b"]])
        cases = [
            joins.Join(name="j", left="l", right="r", on=[]),
            joins.Join(name="j", left="l", right="r", on=[{"left": "x", "right": "r"}]),
            joins.Join(name="j", left="l", right="r", on=[{"left": "k", "right": "x"}]),
        ]
        for join in cases:
            with self.subTest(join=join), self.assertRaises(joins.UnknownJoinType):
                joins.diagnose(left, right, join)
        both = joins.diagnose(
            left,
            right,
            joins.Join(name="j", left="l", right="r", on=[{"left": "k", "right": "r"}]),
        )
        self.assertEqual(both["cardinality"], "many_to_many")
        one_to_many = joins.diagnose(
            Table(columns=["k"], rows=[["a"], ["b"]]),
            right,
            joins.Join(name="j", left="l", right="r", on=[{"left": "k", "right": "r"}]),
        )
        self.assertEqual(one_to_many["cardinality"], "one_to_many")
        empty = joins.diagnose(
            Table(columns=["k"], rows=[]),
            Table(columns=["r"], rows=[]),
            joins.Join(name="j", left="l", right="r", on=[{"left": "k", "right": "r"}]),
        )
        self.assertEqual(empty["left_match_rate"], 0.0)


class JoinChainTests(unittest.TestCase):
    """Varios cruces se encadenan: la salida de uno alimenta al siguiente."""

    def test_two_joins_in_sequence(self):
        ventas = Table(columns=["cliente", "monto"], rows=[["ana", "120"], ["beto", "340"]])
        clientes = Table(columns=["nombre", "ciudad"], rows=[["ana", "Cusco"], ["beto", "Lima"]])
        ciudades = Table(columns=["ciudad", "pais"], rows=[["Cusco", "Peru"], ["Lima", "Peru"]])

        result = engine.run_chain(
            {"ventas": ventas, "clientes": clientes, "ciudades": ciudades},
            [
                joins.Join(
                    name="j1",
                    left="ventas",
                    right="clientes",
                    on=[{"left": "cliente", "right": "nombre"}],
                    type="inner",
                ),
                joins.Join(
                    name="j2",
                    left="j1",
                    right="ciudades",
                    on=[{"left": "ciudad", "right": "ciudad"}],
                    type="inner",
                ),
            ],
        )
        self.assertIn("pais", result.columns)
        self.assertEqual(len(result.rows), 2)

    def test_self_join_on_the_same_source(self):
        # Cruzar una tabla consigo misma es legítimo (duplicados, huérfanos
        # dentro de un mismo archivo). `left == right` no debe colapsar a un
        # único alias: cada lado necesita su propio alias físico o DuckDB falla
        # con «duplicate alias src_0».
        tabla = Table(columns=["k", "v"], rows=[["1", "a"], ["2", "b"]])
        result = engine.run_chain(
            {"datos.csv": tabla},
            [
                joins.Join(
                    name="j",
                    left="datos.csv",
                    right="datos.csv",
                    on=[{"left": "k", "right": "k"}],
                    type="inner",
                )
            ],
        )
        self.assertEqual(len(result.rows), 2)

    def test_chain_referencing_an_unknown_step_fails(self):
        ventas = Table(columns=["cliente"], rows=[["ana"]])
        with self.assertRaises(engine.EngineError):
            engine.run_chain(
                {"ventas": ventas},
                [
                    joins.Join(
                        name="j1",
                        left="no_existe",
                        right="ventas",
                        on=[{"left": "a", "right": "cliente"}],
                        type="inner",
                    )
                ],
            )

    def test_chain_without_plan_fails(self):
        with self.assertRaises(engine.EngineError):
            engine.run_chain({"a": Table(columns=["x"], rows=[["1"]])}, [])

    def test_chain_with_unknown_right_reference_fails(self):
        with self.assertRaises(engine.EngineError):
            engine.run_chain(
                {"a": Table(columns=["x"], rows=[["1"]])},
                [
                    joins.Join(
                        name="j",
                        left="a",
                        right="no_existe",
                        on=[{"left": "x", "right": "y"}],
                        type="inner",
                    )
                ],
            )

    def test_right_column_missing_fails(self):
        left = Table(columns=["a"], rows=[["1"]])
        right = Table(columns=["b"], rows=[["1"]])
        with self.assertRaises(engine.EngineError):
            engine.run_join(
                left,
                right,
                joins.Join(
                    name="j",
                    left="l",
                    right="r",
                    on=[{"left": "a", "right": "no_existe"}],
                    type="inner",
                ),
            )


class JoinCliTests(unittest.TestCase):
    """`yachay model join` cruza dos archivos desde la línea de comandos."""

    def _run(self, argv):
        import contextlib
        import io

        from motor.__main__ import main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def _pair(self, d: str):
        left = Path(d) / "ventas.csv"
        left.write_text("cliente,monto\nana,120\nfantasma,99\n", encoding="utf-8")
        right = Path(d) / "clientes.csv"
        right.write_text("nombre,ciudad\nana,Cusco\n", encoding="utf-8")
        return left, right

    def test_inner_join_from_the_cli(self):
        with tempfile.TemporaryDirectory() as d:
            left, right = self._pair(d)
            code, out = self._run(
                [
                    "model",
                    "join",
                    str(left),
                    str(right),
                    "--on",
                    "cliente",
                    "--right-on",
                    "nombre",
                    "--root",
                    d,
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn('"rows_out": 1', out)

    def test_anti_join_reports_orphans(self):
        with tempfile.TemporaryDirectory() as d:
            left, right = self._pair(d)
            code, out = self._run(
                [
                    "model",
                    "join",
                    str(left),
                    str(right),
                    "--on",
                    "cliente",
                    "--right-on",
                    "nombre",
                    "--type",
                    "anti",
                    "--root",
                    d,
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("fantasma", out)

    def test_join_reports_an_unknown_column(self):
        with tempfile.TemporaryDirectory() as d:
            left, right = self._pair(d)
            code, out = self._run(
                ["model", "join", str(left), str(right), "--on", "no_existe", "--root", d]
            )
            self.assertEqual(code, 1)
            self.assertIn("no existe", out)

    def test_join_reports_an_unsupported_file(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "foto.png"
            bad.write_bytes(b"\x89PNG")
            good = Path(d) / "x.csv"
            good.write_text("a\n1\n", encoding="utf-8")
            code, out = self._run(["model", "join", str(bad), str(good), "--on", "a", "--root", d])
            self.assertEqual(code, 1)
            self.assertIn("no soportado", out)


if __name__ == "__main__":
    unittest.main()
