"""Modelo de datos: entidades, atributos y claves (ADR-019, M2).

El modelo se guarda, se versiona y se reabre. La inferencia propone entidades a
partir del perfil de M1, pero no decide por el usuario.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from motor.model import infer, store
from motor.sources.profile import ColumnProfile
from motor.sources.tables import Table
from motor.storage.db import connect


class StoreTests(unittest.TestCase):
    """Un modelo se crea, se guarda, se reabre y se versiona."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = connect(Path(self._tmp.name) / "m.sqlite3")

    def test_create_and_read_a_model(self):
        model_id = store.create(self.conn, name="bitacora")

        model = store.get(self.conn, model_id)

        self.assertEqual(model.name, "bitacora")
        self.assertEqual(model.version, 1)
        self.assertEqual(model.entities, [])

    def test_add_entity_with_attributes_and_key(self):
        model_id = store.create(self.conn, name="bitacora")
        entity_id = store.add_entity(self.conn, model_id, name="acciones")
        store.add_attribute(self.conn, entity_id, name="id", type="text", nullable=False)
        store.add_attribute(self.conn, entity_id, name="riesgo", type="text", nullable=True)
        store.set_key(self.conn, entity_id, columns=["id"])

        model = store.get(self.conn, model_id)
        entity = model.entities[0]

        self.assertEqual(entity.name, "acciones")
        self.assertEqual([a.name for a in entity.attributes], ["id", "riesgo"])
        self.assertEqual(entity.key, ["id"])

    def test_reopening_reads_the_same_model(self):
        model_id = store.create(self.conn, name="bitacora")
        entity_id = store.add_entity(self.conn, model_id, name="acciones")
        store.add_attribute(self.conn, entity_id, name="id", type="text", nullable=False)

        # Otra conexión: el modelo vive en SQLite, no en memoria.
        other = connect(Path(self._tmp.name) / "m.sqlite3")
        model = store.get(other, model_id)
        self.assertEqual(model.entities[0].attributes[0].name, "id")

    def test_new_version_keeps_the_previous_one(self):
        model_id = store.create(self.conn, name="bitacora")
        entity_id = store.add_entity(self.conn, model_id, name="acciones")
        store.add_attribute(self.conn, entity_id, name="id", type="text", nullable=False)

        store.new_version(self.conn, model_id, name="bitacora")

        versions = store.versions(self.conn, model_id)
        self.assertEqual(sorted(versions), [1, 2])
        v2 = store.get(self.conn, model_id, version=2)
        self.assertEqual(v2.entities, [])  # la copia nace vacía
        v1 = store.get(self.conn, model_id, version=1)
        self.assertEqual(v1.entities[0].attributes[0].name, "id")

    def test_listing_and_deleting_models(self):
        a = store.create(self.conn, name="a")
        store.create(self.conn, name="b")
        self.assertEqual(sorted(m.name for m in store.list_models(self.conn)), ["a", "b"])

        store.delete(self.conn, a)
        self.assertEqual([m.name for m in store.list_models(self.conn)], ["b"])

    def test_unknown_model_raises(self):
        with self.assertRaises(store.ModelNotFound):
            store.get(self.conn, 9999)

    def test_duplicate_attribute_in_one_entity_is_rejected(self):
        model_id = store.create(self.conn, name="m")
        entity_id = store.add_entity(self.conn, model_id, name="e")
        store.add_attribute(self.conn, entity_id, name="x", type="text", nullable=True)
        with self.assertRaises(store.DuplicateName):
            store.add_attribute(self.conn, entity_id, name="x", type="text", nullable=True)

    def test_key_must_reference_existing_attributes(self):
        model_id = store.create(self.conn, name="m")
        entity_id = store.add_entity(self.conn, model_id, name="e")
        store.add_attribute(self.conn, entity_id, name="id", type="text", nullable=False)
        with self.assertRaises(store.UnknownAttribute):
            store.set_key(self.conn, entity_id, columns=["no_existe"])

    def test_two_models_with_the_same_name_version_are_rejected(self):
        store.create(self.conn, name="dup")
        with self.assertRaises(store.DuplicateName):
            # Crear otra vez el mismo nombre reusa la versión 1.
            store.create(self.conn, name="dup")

    def test_reading_an_unknown_version_raises(self):
        model_id = store.create(self.conn, name="m")
        with self.assertRaises(store.ModelNotFound):
            store.get(self.conn, model_id, version=99)

    def test_entity_in_unknown_model_raises(self):
        with self.assertRaises(store.ModelNotFound):
            store.add_entity(self.conn, 9999, name="e")

    def test_versions_of_unknown_model_raises(self):
        with self.assertRaises(store.ModelNotFound):
            store.versions(self.conn, 9999)

    def test_duplicate_entity_name_is_rejected(self):
        model_id = store.create(self.conn, name="m")
        store.add_entity(self.conn, model_id, name="e")
        with self.assertRaises(store.DuplicateName):
            store.add_entity(self.conn, model_id, name="e")

    def test_empty_name_is_rejected(self):
        with self.assertRaises(ValueError):
            store.create(self.conn, name="   ")

    def test_empty_attribute_name_is_rejected(self):
        model_id = store.create(self.conn, name="m")
        entity_id = store.add_entity(self.conn, model_id, name="e")
        with self.assertRaises(ValueError):
            store.add_attribute(self.conn, entity_id, name="", type="text", nullable=True)

    def test_delete_removes_entities_too(self):
        model_id = store.create(self.conn, name="m")
        entity_id = store.add_entity(self.conn, model_id, name="e")
        store.add_attribute(self.conn, entity_id, name="x", type="text", nullable=True)

        store.delete(self.conn, model_id)

        remaining = self.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        self.assertEqual(remaining, 0)


class InferTests(unittest.TestCase):
    """La inferencia propone una entidad desde el perfil, sin imponerla."""

    def test_infers_an_entity_from_a_table(self):
        table = Table(
            columns=["id", "riesgo"],
            rows=[["INFRA-001", "Bajo"], ["INFRA-002", "Alto"]],
        )
        profiles = _profile(table)

        proposal = infer.entity_from_table(table, profiles, name="acciones")

        self.assertEqual(proposal.name, "acciones")
        self.assertEqual([a.name for a in proposal.attributes], ["id", "riesgo"])
        # 'id' es único en todas las filas → candidata a clave primaria.
        self.assertEqual(proposal.suggested_key, ["id"])

    def test_no_key_suggested_when_no_column_is_unique(self):
        table = Table(columns=["estado"], rows=[["Hecho"], ["Hecho"]])
        proposal = infer.entity_from_table(table, _profile(table), name="e")
        self.assertEqual(proposal.suggested_key, [])

    def test_attribute_is_nullable_when_the_column_has_nulls(self):
        table = Table(columns=["x"], rows=[["a"], [""]])
        proposal = infer.entity_from_table(table, _profile(table), name="e")
        self.assertTrue(proposal.attributes[0].nullable)
        self.assertEqual(proposal.attributes[0].type, "text")


def _profile(table: Table) -> list[ColumnProfile]:
    from motor.sources.profile import profile_table

    return profile_table(table)


class ModelCliTests(unittest.TestCase):
    """`yachay model new/show` crea y muestra un modelo desde un archivo real."""

    def _run(self, argv):
        import contextlib
        import io

        from motor.__main__ import main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_new_infers_and_persists_a_model(self):
        with tempfile.TemporaryDirectory() as d:
            csv = Path(d) / "acciones.csv"
            csv.write_text("id,riesgo\nINFRA-001,Bajo\nINFRA-002,Alto\n", encoding="utf-8")

            code, out = self._run(["model", "new", "bitacora", str(csv), "--root", d])

            self.assertEqual(code, 0)
            self.assertIn("acciones", out)
            self.assertIn('"key"', out)

    def test_show_reads_a_persisted_model(self):
        with tempfile.TemporaryDirectory() as d:
            csv = Path(d) / "x.csv"
            csv.write_text("a,b\n1,2\n", encoding="utf-8")
            self._run(["model", "new", "m", str(csv), "--root", d])

            code, out = self._run(["model", "show", "1", "--root", d])

            self.assertEqual(code, 0)
            self.assertIn('"name": "m"', out)

    def test_show_reports_an_unknown_model(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = self._run(["model", "show", "9999", "--root", d])
            self.assertEqual(code, 1)
            self.assertIn("no existe", out)

    def test_new_reports_an_unsupported_file(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "foto.png"
            bad.write_bytes(b"\x89PNG")
            code, out = self._run(["model", "new", "m", str(bad), "--root", d])
            self.assertEqual(code, 1)
            self.assertIn("no soportado", out)

    def test_new_without_a_unique_column_suggests_no_key(self):
        with tempfile.TemporaryDirectory() as d:
            csv = Path(d) / "dup.csv"
            csv.write_text("estado\nHecho\nHecho\n", encoding="utf-8")
            code, out = self._run(["model", "new", "m", str(csv), "--root", d])
            self.assertEqual(code, 0)
            self.assertIn('"key": []', out)


class ModelRunCliTests(unittest.TestCase):
    """`yachay model run` aplica las reglas del modelo a un archivo."""

    def _run(self, argv):
        import contextlib
        import io

        from motor.__main__ import main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def _model_with_a_cast(self, d: str) -> None:
        csv = Path(d) / "m.csv"
        csv.write_text("monto\n120\n340\n", encoding="utf-8")
        self._run(["model", "new", "m", str(csv), "--root", d])
        from motor.config import MotorConfig
        from motor.model import rules
        from motor.storage.db import connect

        cfg = MotorConfig.from_root(Path(d))
        conn = connect(cfg.paths.sqlite_path)
        rules.add(conn, 1, name="cast_monto", type="cast",
                  column="monto", params={"to": "number"})
        conn.close()

    def test_run_applies_the_model_rules(self):
        with tempfile.TemporaryDirectory() as d:
            self._model_with_a_cast(d)
            code, out = self._run(["model", "run", "1", str(Path(d) / "m.csv"), "--root", d])
            self.assertEqual(code, 0)
            self.assertIn('"rows_out": 2', out)

    def test_run_rejects_an_unknown_model(self):
        with tempfile.TemporaryDirectory() as d:
            csv = Path(d) / "x.csv"
            csv.write_text("a\n1\n", encoding="utf-8")
            code, out = self._run(["model", "run", "9999", str(csv), "--root", d])
            self.assertEqual(code, 1)
            self.assertIn("no existe", out)

    def test_run_reports_an_unsupported_file(self):
        with tempfile.TemporaryDirectory() as d:
            self._model_with_a_cast(d)
            bad = Path(d) / "foto.png"
            bad.write_bytes(b"\x89PNG")
            code, out = self._run(["model", "run", "1", str(bad), "--root", d])
            self.assertEqual(code, 1)
            self.assertIn("no soportado", out)

    def test_run_reports_an_engine_error(self):
        with tempfile.TemporaryDirectory() as d:
            # Modelo sin reglas válidas para el archivo: una regla sobre una
            # columna que el archivo no trae → EngineError, no traza cruda.
            csv = Path(d) / "m.csv"
            csv.write_text("monto\n120\n", encoding="utf-8")
            self._run(["model", "new", "m", str(csv), "--root", d])
            from motor.config import MotorConfig
            from motor.model import rules
            from motor.storage.db import connect

            cfg = MotorConfig.from_root(Path(d))
            conn = connect(cfg.paths.sqlite_path)
            rules.add(conn, 1, name="c", type="cast",
                      column="monto", params={"to": "number"})
            conn.close()

            other = Path(d) / "otro.csv"
            other.write_text("ciudad\nCusco\n", encoding="utf-8")
            code, out = self._run(["model", "run", "1", str(other), "--root", d])
            self.assertEqual(code, 1)
            self.assertIn("no existe en la tabla", out)


if __name__ == "__main__":
    unittest.main()
