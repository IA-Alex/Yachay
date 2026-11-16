"""Ejecución repetible y comparación de ejecuciones (ADR-019, M5; E4, D1).

Una *ejecución* guarda la **huella** de su resultado: columnas, número de filas y
un hash. Comparar dos huellas dice si el resultado cambió; el hash detecta
cualquier cambio sin guardar los datos (SQLite guarda solo estado, ADR-005).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow.parquet as pq

from motor.model import diff, run, store
from motor.sources.tables import Table
from motor.storage.db import connect


class FingerprintTests(unittest.TestCase):
    """La huella resume un resultado de forma estable."""

    def test_same_table_gives_the_same_fingerprint(self):
        a = Table(columns=["x", "y"], rows=[["1", "a"], ["2", "b"]])
        b = Table(columns=["x", "y"], rows=[["1", "a"], ["2", "b"]])
        self.assertEqual(run.fingerprint(a), run.fingerprint(b))

    def test_different_rows_give_a_different_hash(self):
        a = Table(columns=["x"], rows=[["1"]])
        b = Table(columns=["x"], rows=[["2"]])
        self.assertNotEqual(run.fingerprint(a).hash, run.fingerprint(b).hash)

    def test_row_order_matters_for_the_hash(self):
        a = Table(columns=["x"], rows=[["1"], ["2"]])
        b = Table(columns=["x"], rows=[["2"], ["1"]])
        self.assertNotEqual(run.fingerprint(a).hash, run.fingerprint(b).hash)

    def test_fingerprint_has_columns_and_rows(self):
        fp = run.fingerprint(Table(columns=["x", "y"], rows=[["1", "a"]]))
        self.assertEqual(fp.columns, ["x", "y"])
        self.assertEqual(fp.rows, 1)
        self.assertEqual(len(fp.hash), 64)  # sha256

    def test_null_and_empty_string_are_different_content(self):
        # Un CSV lee la celda vacía como '' y un ODS como None (tables.py:144):
        # son contenidos distintos y la huella debe distinguirlos, o el diff
        # diría «no cambió» al reejecutar el mismo modelo sobre el otro formato.
        a = Table(columns=["x"], rows=[[None]])
        b = Table(columns=["x"], rows=[[""]])
        self.assertNotEqual(run.fingerprint(a).hash, run.fingerprint(b).hash)

    def test_a_separator_inside_a_cell_does_not_collide(self):
        # Dos filas de distinta forma no pueden compartir huella solo porque un
        # valor lleve incrustado el separador interno (0x1f).
        a = Table(columns=["x"], rows=[["a", "b"]])
        b = Table(columns=["x"], rows=[["a\x1fb"]])
        self.assertNotEqual(run.fingerprint(a).hash, run.fingerprint(b).hash)


class RunStoreTests(unittest.TestCase):
    """Una ejecución se guarda y se reabre."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = connect(Path(self._tmp.name) / "m.sqlite3")
        self.model_id = store.create(self.conn, name="m")

    def _table(self, value="a"):
        return Table(columns=["x"], rows=[[value]])

    def test_record_and_read_a_run(self):
        run_id = run.record(self.conn, self.model_id, table=self._table(), now=1.0)
        stored = run.get(self.conn, run_id)
        self.assertEqual(stored.model_id, self.model_id)
        self.assertEqual(stored.fingerprint.rows, 1)

    def test_runs_are_listed_newest_first(self):
        run.record(self.conn, self.model_id, table=self._table("a"), now=1.0)
        run.record(self.conn, self.model_id, table=self._table("b"), now=2.0)
        runs = run.list_for(self.conn, self.model_id)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0].created_at, 2.0)

    def test_unknown_run_raises(self):
        with self.assertRaises(run.RunNotFound):
            run.get(self.conn, 9999)

    def test_record_persists_private_parquet_and_source_metadata(self):
        output = Path(self._tmp.name) / "outputs"
        run_id = run.record(
            self.conn, self.model_id,
            table=Table(columns=["a", "b"], rows=[[1, "x"], [2, "y"]]),
            artifact_dir=output, source_name="input.csv", source_sha256="f" * 64,
            now=3.0,
        )
        saved = run.get(self.conn, run_id)
        path = output / saved.artifact_path
        self.assertEqual(saved.source_name, "input.csv")
        self.assertEqual(saved.source_sha256, "f" * 64)
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(pq.read_table(path).to_pylist(), [{"a": 1, "b": "x"},
                                                           {"a": 2, "b": "y"}])

    def test_record_stringifies_a_mixed_arrow_column(self):
        output = Path(self._tmp.name) / "mixed"
        run_id = run.record(
            self.conn, self.model_id,
            table=Table(columns=["v"], rows=[[1], ["uno"]]), artifact_dir=output,
        )
        saved = run.get(self.conn, run_id)
        self.assertEqual(
            pq.read_table(output / saved.artifact_path).to_pylist(),
            [{"v": "1"}, {"v": "uno"}],
        )

    def test_record_rolls_back_and_removes_partial_output_when_write_fails(self):
        output = Path(self._tmp.name) / "failed"
        with patch("motor.model.run._write_parquet", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                run.record(self.conn, self.model_id, table=self._table(), artifact_dir=output)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)
        self.assertEqual(list(output.iterdir()), [])

    def test_record_closes_descriptor_and_removes_temp_if_permission_setting_fails(self):
        output = Path(self._tmp.name) / "permission"
        with patch("motor.model.run.os.fchmod", side_effect=OSError("permission")):
            with self.assertRaisesRegex(OSError, "permission"):
                run.record(self.conn, self.model_id, table=self._table(), artifact_dir=output)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)
        self.assertEqual(list(output.iterdir()), [])

    def test_record_rollback_without_artifact_when_commit_fails(self):
        class CommitFailure:
            def execute(_self, *args):
                return self.conn.execute(*args)

            def commit(_self):
                raise OSError("commit failed")

            def rollback(_self):
                self.conn.rollback()

        with self.assertRaisesRegex(OSError, "commit failed"):
            run.record(CommitFailure(), self.model_id, table=self._table())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)


class DiffTests(unittest.TestCase):
    """Comparar dos huellas dice qué cambió."""

    def _fp(self, columns, rows):
        return run.fingerprint(Table(columns=columns, rows=rows))

    def test_identical_fingerprints_report_no_change(self):
        fp = self._fp(["x"], [["1"]])
        result = diff.compare(fp, fp)
        self.assertFalse(result.changed)
        self.assertEqual(result.reason, "")

    def test_row_count_change_is_reported(self):
        a = self._fp(["x"], [["1"]])
        b = self._fp(["x"], [["1"], ["2"]])
        result = diff.compare(a, b)
        self.assertTrue(result.changed)
        self.assertIn("filas", result.reason)

    def test_column_change_is_reported(self):
        a = self._fp(["x"], [["1"]])
        b = self._fp(["x", "y"], [["1", "a"]])
        result = diff.compare(a, b)
        self.assertTrue(result.changed)
        self.assertIn("columnas", result.reason)

    def test_reordered_columns_are_reported_with_a_reason(self):
        # Mismas columnas en otro orden: es un cambio real y el motivo no puede
        # quedar vacío («cambiaron las columnas ()»).
        a = self._fp(["x", "y"], [["1", "a"]])
        b = self._fp(["y", "x"], [["a", "1"]])
        result = diff.compare(a, b)
        self.assertTrue(result.changed)
        self.assertIn("orden", result.reason)

    def test_added_and_removed_columns_are_both_reported(self):
        a = self._fp(["x", "viejo"], [["1", "a"]])
        b = self._fp(["x", "nuevo"], [["1", "b"]])
        result = diff.compare(a, b)
        self.assertTrue(result.changed)
        self.assertIn("columnas nuevas: nuevo", result.reason)
        self.assertIn("columnas perdidas: viejo", result.reason)

    def test_only_removed_columns_are_reported(self):
        a = self._fp(["x", "extra"], [["1", "a"]])
        b = self._fp(["x"], [["1"]])
        result = diff.compare(a, b)
        self.assertTrue(result.changed)
        self.assertNotIn("columnas nuevas", result.reason)
        self.assertIn("columnas perdidas: extra", result.reason)

    def test_only_added_columns_are_reported(self):
        a = self._fp(["x"], [["1"]])
        b = self._fp(["x", "extra"], [["1", "a"]])
        result = diff.compare(a, b)
        self.assertTrue(result.changed)
        self.assertIn("columnas nuevas: extra", result.reason)
        self.assertNotIn("columnas perdidas", result.reason)

    def test_same_shape_different_values_is_reported_as_content(self):
        a = self._fp(["x"], [["1"]])
        b = self._fp(["x"], [["2"]])
        result = diff.compare(a, b)
        self.assertTrue(result.changed)
        self.assertIn("contenido", result.reason)

    def test_null_versus_empty_string_is_reported_as_a_change(self):
        # Escenario real: el mismo modelo reejecutado sobre el CSV ('' ) y sobre
        # el ODS (None) de la misma hoja. El diff debe decir que cambió.
        a = self._fp(["x"], [[None]])
        b = self._fp(["x"], [[""]])
        result = diff.compare(a, b)
        self.assertTrue(result.changed)
        self.assertIn("contenido", result.reason)


class RunExecuteTests(unittest.TestCase):
    """`run.execute` aplica reglas y cruces y devuelve el resultado."""

    def test_execute_applies_rules_then_joins(self):
        from motor.model.rules import Rule

        ventas = Table(columns=["cliente", "monto"], rows=[["ana", "120"]])
        clientes = Table(columns=["nombre", "ciudad"], rows=[["ana", "Cusco"]])

        result = run.execute(
            {"ventas": ventas, "clientes": clientes},
            rules_by_source={"ventas": [Rule(name="c", type="cast",
                                             column="monto", params={"to": "number"})]},
            join_plan=[],
            output="ventas",
        )
        self.assertEqual(result.columns, ["cliente", "monto"])
        self.assertEqual(result.rows, [["ana", 120.0]])

    def test_execute_with_a_join(self):
        from motor.model.joins import Join

        ventas = Table(columns=["cliente"], rows=[["ana"], ["beto"]])
        clientes = Table(columns=["nombre", "ciudad"], rows=[["ana", "Cusco"]])

        result = run.execute(
            {"ventas": ventas, "clientes": clientes},
            rules_by_source={},
            join_plan=[Join(name="j", left="ventas", right="clientes",
                            on=[{"left": "cliente", "right": "nombre"}],
                            type="inner")],
            output="j",
        )
        self.assertEqual(result.rows, [["ana", "ana", "Cusco"]])

    def test_execute_reports_an_unknown_output(self):
        with self.assertRaises(run.RunError):
            run.execute(
                {"a": Table(columns=["x"], rows=[["1"]])},
                rules_by_source={}, join_plan=[], output="no_existe",
            )

    def test_execute_with_a_join_plan_but_output_is_a_source(self):
        from motor.model.joins import Join

        # `output` es una fuente, con un plan de cruces presente: se devuelve la
        # fuente tal cual (no se ejecuta el cruce).
        result = run.execute(
            {"a": Table(columns=["x"], rows=[["1"]]),
             "b": Table(columns=["x"], rows=[["1"]])},
            rules_by_source={},
            join_plan=[Join(name="j", left="a", right="b",
                            on=[{"left": "x", "right": "x"}], type="inner")],
            output="a",
        )
        self.assertEqual(result.columns, ["x"])

    def test_execute_wraps_a_join_execution_error(self):
        from motor.model.joins import Join

        # El cruce nombra una columna que no existe: falla al ejecutar y se
        # envuelve como RunError.
        with self.assertRaises(run.RunError):
            run.execute(
                {"a": Table(columns=["x"], rows=[["1"]]),
                 "b": Table(columns=["y"], rows=[["1"]])},
                rules_by_source={},
                join_plan=[Join(name="j", left="a", right="b",
                                on=[{"left": "x", "right": "no_existe"}],
                                type="inner")],
                output="j",
            )

    def test_execute_reports_a_bad_rule(self):
        from motor.model.rules import Rule

        with self.assertRaises(run.RunError):
            run.execute(
                {"a": Table(columns=["x"], rows=[["1"]])},
                rules_by_source={"a": [Rule(name="r", type="cast",
                                            column="no_existe",
                                            params={"to": "number"})]},
                join_plan=[], output="a",
            )

    def test_execute_reports_an_unknown_output_with_a_join_plan(self):
        from motor.model.joins import Join

        with self.assertRaises(run.RunError):
            run.execute(
                {"a": Table(columns=["x"], rows=[["1"]]),
                 "b": Table(columns=["x"], rows=[["1"]])},
                rules_by_source={},
                join_plan=[Join(name="j", left="a", right="b",
                                on=[{"left": "x", "right": "x"}], type="inner")],
                output="no_existe",
            )

    def test_execute_can_output_an_intermediate_join(self):
        from motor.model.joins import Join

        a = Table(columns=["k"], rows=[["1"]])
        b = Table(columns=["k", "b"], rows=[["1", "x"]])
        c = Table(columns=["b", "c"], rows=[["x", "y"]])
        result = run.execute(
            {"a": a, "b": b, "c": c},
            rules_by_source={},
            join_plan=[
                Join(name="j1", left="a", right="b",
                     on=[{"left": "k", "right": "k"}], type="inner"),
                Join(name="j2", left="j1", right="c",
                     on=[{"left": "b", "right": "b"}], type="inner"),
            ],
            output="j1",
        )
        self.assertIn("b", result.columns)
        self.assertEqual(len(result.columns), 3)  # k, k, b de j1


class RunCliTests(unittest.TestCase):
    """`yachay model exec` y `model diff` desde la línea de comandos."""

    def _run(self, argv):
        import contextlib
        import io

        from motor.__main__ import main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def _model(self, d: str, value: str) -> Path:
        csv = Path(d) / f"datos_{value}.csv"
        csv.write_text(f"id,valor\n1,{value}\n", encoding="utf-8")
        self._run(["model", "new", "m", str(csv), "--root", d])
        return csv

    def test_exec_records_a_run(self):
        with tempfile.TemporaryDirectory() as d:
            csv = self._model(d, "100")
            code, out = self._run(["model", "exec", "1", str(csv), "--root", d])
            self.assertEqual(code, 0)
            self.assertIn('"run_id": 1', out)

    def test_diff_between_two_identical_runs_reports_no_change(self):
        with tempfile.TemporaryDirectory() as d:
            csv = self._model(d, "100")
            self._run(["model", "exec", "1", str(csv), "--root", d])
            self._run(["model", "exec", "1", str(csv), "--root", d])
            code, out = self._run(["model", "diff", "1", "2", "--root", d])
            self.assertEqual(code, 0)
            self.assertIn('"changed": false', out)

    def test_diff_between_different_runs_reports_a_change(self):
        with tempfile.TemporaryDirectory() as d:
            first = Path(d) / "a.csv"
            first.write_text("id,valor\n1,10\n", encoding="utf-8")
            self._run(["model", "new", "m", str(first), "--root", d])
            self._run(["model", "exec", "1", str(first), "--root", d])
            second = Path(d) / "b.csv"
            second.write_text("id,valor\n1,10\n2,20\n", encoding="utf-8")
            self._run(["model", "exec", "1", str(second), "--root", d])
            code, out = self._run(["model", "diff", "1", "2", "--root", d])
            self.assertEqual(code, 0)
            self.assertIn('"changed": true', out)
            self.assertIn("filas", out)

    def test_diff_reports_an_unknown_run(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = self._run(["model", "diff", "1", "2", "--root", d])
            self.assertEqual(code, 1)
            self.assertIn("no existe", out)

    def test_exec_reports_an_unknown_model(self):
        with tempfile.TemporaryDirectory() as d:
            csv = Path(d) / "x.csv"
            csv.write_text("a\n1\n", encoding="utf-8")
            code, out = self._run(["model", "exec", "9999", str(csv), "--root", d])
            self.assertEqual(code, 1)
            self.assertIn("no existe", out)

    def test_exec_reports_an_unsupported_file(self):
        with tempfile.TemporaryDirectory() as d:
            csv = Path(d) / "m.csv"
            csv.write_text("a\n1\n", encoding="utf-8")
            self._run(["model", "new", "m", str(csv), "--root", d])
            bad = Path(d) / "foto.png"
            bad.write_bytes(b"\x89PNG")
            code, out = self._run(["model", "exec", "1", str(bad), "--root", d])
            self.assertEqual(code, 1)
            self.assertIn("no soportado", out)

    def test_exec_reports_a_model_without_entities(self):
        with tempfile.TemporaryDirectory() as d:
            from motor.config import MotorConfig
            from motor.model import store
            from motor.storage.db import connect

            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            store.create(conn, name="vacio")
            conn.close()
            csv = Path(d) / "x.csv"
            csv.write_text("a\n1\n", encoding="utf-8")
            code, out = self._run(["model", "exec", "1", str(csv), "--root", d])
            self.assertEqual(code, 1)
            self.assertIn("no tiene entidades", out)

    def test_exec_reports_a_rule_over_a_missing_column(self):
        with tempfile.TemporaryDirectory() as d:
            csv = Path(d) / "m.csv"
            csv.write_text("a\n1\n", encoding="utf-8")
            self._run(["model", "new", "m", str(csv), "--root", d])
            # Regla sobre una columna que el archivo de ejecución no trae.
            from motor.config import MotorConfig
            from motor.model import rules
            from motor.storage.db import connect

            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            # La regla apunta a 'a' (existe en el modelo) pero se ejecuta sobre
            # un archivo con otra columna.
            rules.add(conn, 1, name="c", type="cast", column="a",
                      params={"to": "number"})
            conn.close()
            other = Path(d) / "otro.csv"
            other.write_text("b\n1\n", encoding="utf-8")
            code, out = self._run(["model", "exec", "1", str(other), "--root", d])
            self.assertEqual(code, 1)
            self.assertIn("no existe en la tabla", out)


if __name__ == "__main__":
    unittest.main()
