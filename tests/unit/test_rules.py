"""Reglas de transformación y ejecución del modelo (ADR-019, M3).

Cinco tipos deterministas: `rename`, `cast`, `map`, `domain`, `filter`. Se
guardan con nombre y se reejecutan. La ejecución produce una tabla resultado; no
toca la entrada original.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from motor.model import engine, rules, store
from motor.sources.tables import Table
from motor.storage.db import connect


class RuleApplicationTests(unittest.TestCase):
    """Cada tipo de regla transforma la tabla como dice su contrato."""

    def setUp(self):
        self.con = duckdb.connect(":memory:")
        self.con.execute("CREATE TABLE t (ciudad VARCHAR, monto VARCHAR, estado VARCHAR)")
        self.con.execute(
            "INSERT INTO t VALUES (' Cusco ', '120', 'Hecho'), "
            "('Cusco', '340', 'Bloqueado'), ('Lima', 'x', 'Hecho')"
        )

    def _result(self, sql):
        return self.con.execute(sql).fetchall()

    def test_rename_changes_a_column_name(self):
        sql = rules.render({"type": "rename", "from": "ciudad", "to": "lugar"})
        self.con.execute("CREATE TABLE r AS SELECT * FROM t")
        self.con.execute(f"ALTER TABLE r RENAME COLUMN {sql}")
        cols = [c[0] for c in self.con.execute("SELECT * FROM r LIMIT 0").description]
        self.assertIn("lugar", cols)

    def test_cast_converts_a_column_to_number(self):
        expr = rules.render({"type": "cast", "column": "monto", "to": "number"})
        self.assertIn("TRY_CAST", expr)

    def test_map_replaces_values(self):
        expr = rules.render(
            {"type": "map", "column": "ciudad", "values": {"Cusco": "Cusco", "Lima": "Lima"}}
        )
        self.assertIn("CASE", expr)
        self.assertIn("Cusco", expr)

    def test_domain_flags_values_outside_the_allowed_set(self):
        expr = rules.render(
            {"type": "domain", "column": "estado", "allowed": ["Hecho", "En curso"]}
        )
        self.assertIn("IN (", expr)
        self.assertIn("ELSE NULL", expr)

    def test_filter_keeps_matching_rows(self):
        expr = rules.render({"type": "filter", "column": "estado", "equals": "Hecho"})
        self.assertIn("estado", expr)


class RulesStoreTests(unittest.TestCase):
    """Las reglas se guardan con nombre en el modelo y se reejecutan."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = connect(Path(self._tmp.name) / "m.sqlite3")
        self.model_id = store.create(self.conn, name="m")
        self.entity_id = store.add_entity(self.conn, self.model_id, name="e")
        store.add_attribute(self.conn, self.entity_id, name="ciudad", type="text", nullable=True)

    def test_add_and_list_rules(self):
        rules.add(
            self.conn, self.model_id, name="norm", type="map",
            column="ciudad", params={"values": {"Cusco": "Cusco"}},
        )
        stored = rules.list_for(self.conn, self.model_id)
        self.assertEqual(stored[0].name, "norm")
        self.assertEqual(stored[0].column, "ciudad")

    def test_rule_with_unknown_type_is_rejected(self):
        with self.assertRaises(rules.UnknownRuleType):
            rules.add(
                self.conn, self.model_id, name="x", type="explode",
                column="ciudad", params={},
            )

    def test_duplicate_rule_name_is_rejected(self):
        rules.add(self.conn, self.model_id, name="r", type="cast",
                  column="ciudad", params={"to": "text"})
        with self.assertRaises(rules.DuplicateRule):
            rules.add(self.conn, self.model_id, name="r", type="cast",
                      column="ciudad", params={"to": "text"})

    def test_rule_targeting_an_unknown_column_is_rejected(self):
        with self.assertRaises(rules.UnknownColumn):
            rules.add(self.conn, self.model_id, name="r", type="cast",
                      column="no_existe", params={"to": "text"})


class EngineTests(unittest.TestCase):
    """El motor aplica las reglas a una tabla y devuelve el resultado."""

    def test_applies_rename_cast_map_and_returns_values(self):
        table = Table(
            columns=["ciudad", "monto"],
            rows=[[" Cusco ", "120"], ["Cusco", "340"], ["Lima", "x"]],
        )
        # Las reglas operan sobre las columnas de la ENTRADA; el rename es una
        # etapa de proyección. El motor NO limpia solo: normalizar los espacios
        # es una regla `map` explícita, que aquí se declara.
        plan = [
            rules.Rule(name="r2", type="map", column="ciudad",
                       params={"values": {" Cusco ": "Cusco"}}),
            rules.Rule(name="r3", type="cast", column="monto",
                       params={"to": "number"}),
            rules.Rule(name="r4", type="filter", column="ciudad",
                       params={"equals": "Cusco"}),
            rules.Rule(name="r1", type="rename", column="ciudad",
                       params={"to": "lugar"}),
        ]

        result = engine.run(table, plan)

        self.assertEqual(result.columns, ["lugar", "monto"])
        # 'x' → NULL por TRY_CAST; 'Lima' filtrado; los dos Cusco quedan iguales.
        self.assertEqual(result.rows, [["Cusco", 120.0], ["Cusco", 340.0]])

    def test_two_projection_rules_on_the_same_column_apply_in_order(self):
        # Regresión BH-001: el motor colapsaba las reglas de proyección en un
        # diccionario por columna, así que solo se aplicaba la última y el resto
        # se descartaba en silencio. `map` + `cast` sobre la misma columna debe
        # normalizar y luego convertir, en el orden declarado.
        table = Table(columns=["monto"], rows=[["1.5"], ["2,5"], ["x"]])
        plan = [
            rules.Rule(name="normaliza", type="map", column="monto",
                       params={"values": {"2,5": "2.5"}}),
            rules.Rule(name="convierte", type="cast", column="monto",
                       params={"to": "number"}),
        ]

        result = engine.run(table, plan)

        self.assertEqual(result.columns, ["monto"])
        # El `map` corrió antes que el `cast`: '2,5' se normaliza y convierte.
        # 'x' → NULL por TRY_CAST. Antes del arreglo esto devolvía [None, None].
        self.assertEqual(result.rows, [[1.5], [2.5], [None]])

    def test_domain_before_map_on_the_same_column_keeps_both(self):
        # Regresión BH-001: `domain` seguido de `map` sobre la misma columna
        # descartaba el `domain`; el valor fuera del conjunto debe anularse.
        table = Table(columns=["estado"], rows=[["Hecho"], ["Pendiente"], ["Otro"]])
        plan = [
            rules.Rule(name="dominio", type="domain", column="estado",
                       params={"allowed": ["Hecho", "Pendiente"]}),
            rules.Rule(name="renombra_valor", type="map", column="estado",
                       params={"values": {"Pendiente": "Falta"}}),
        ]

        result = engine.run(table, plan)

        # 'Otro' queda NULL por el `domain`; 'Pendiente' pasa a 'Falta' por el
        # `map`, que se aplica después.
        self.assertEqual(result.rows, [["Hecho"], ["Falta"], [None]])

    def test_cast_before_filter_on_the_same_column_filters_converted_value(self):
        # Regresión BH-001: `cast` + `filter` sobre la misma columna devolvía 0
        # filas porque el `cast` se descartaba y el `filter` comparaba el texto.
        table = Table(columns=["monto"], rows=[["1.5"], ["2.5"]])
        plan = [
            rules.Rule(name="convierte", type="cast", column="monto",
                       params={"to": "number"}),
            rules.Rule(name="filtra", type="filter", column="monto",
                       params={"equals": "1.5"}),
        ]

        result = engine.run(table, plan)

        self.assertEqual(result.rows, [[1.5]])

    def test_domain_rule_reports_values_outside_the_set(self):
        table = Table(columns=["estado"], rows=[["Hecho"], ["Bloqueado"]])
        plan = [
            rules.Rule(name="d", type="domain", column="estado",
                       params={"allowed": ["Hecho"]}),
            rules.Rule(name="f", type="filter", column="estado",
                       params={"equals": "Hecho"}),
        ]
        result = engine.run(table, plan)
        self.assertEqual(result.rows, [["Hecho"]])

    def test_running_twice_is_idempotent(self):
        table = Table(columns=["x"], rows=[["a"], ["b"]])
        plan = [rules.Rule(name="r", type="rename", column="x", params={"to": "y"})]
        first = engine.run(table, plan)
        second = engine.run(table, plan)
        self.assertEqual(first.columns, second.columns)
        self.assertEqual(first.rows, second.rows)

    def test_unknown_column_in_a_plan_is_rejected(self):
        table = Table(columns=["x"], rows=[["a"]])
        plan = [rules.Rule(name="r", type="cast", column="z", params={"to": "number"})]
        with self.assertRaises(engine.EngineError):
            engine.run(table, plan)

    def test_empty_table_is_rejected(self):
        with self.assertRaises(engine.EngineError):
            engine.run(Table(columns=[], rows=[]), [])

    def test_invalid_sql_in_a_rule_is_reported(self):
        # `render` acepta el dict; un tipo desconocido en el plan revienta con
        # EngineError, no con una traza cruda.
        table = Table(columns=["x"], rows=[["a"]])
        plan = [rules.Rule(name="r", type="explode", column="x", params={})]
        with self.assertRaises(engine.EngineError):
            engine.run(table, plan)

    def test_a_malicious_column_name_cannot_inject_sql(self):
        # Regresión de seguridad: un nombre de columna viene del archivo del
        # usuario; citado, no puede alterar la consulta.
        table = Table(
            columns=['x" INTEGER); DROP TABLE t; --'],
            rows=[["a"]],
        )
        result = engine.run(table, [])
        self.assertEqual(result.columns, ['x" INTEGER); DROP TABLE t; --'])
        self.assertEqual(result.rows, [["a"]])

    def test_a_quote_in_a_rename_target_is_neutralised(self):
        table = Table(columns=["x"], rows=[["a"]])
        plan = [rules.Rule(name="r", type="rename", column="x",
                           params={"to": 'n" ; DROP TABLE t; --'})]
        result = engine.run(table, plan)
        self.assertEqual(result.columns, ['n" ; DROP TABLE t; --'])


class RuleRenderTests(unittest.TestCase):
    """Cada tipo renderiza su SQL; los casos vacíos se comportan."""

    def test_render_cast_to_text(self):
        from motor.model.rules import render

        self.assertIn("CAST", render({"type": "cast", "column": "x", "to": "text"}))

    def test_render_map_without_values_returns_the_column(self):
        from motor.model.rules import render

        self.assertEqual(
            render({"type": "map", "column": "x", "values": {}}), '"x"'
        )

    def test_render_domain_without_allowed_returns_the_column(self):
        from motor.model.rules import render

        self.assertEqual(
            render({"type": "domain", "column": "x", "allowed": []}), '"x"'
        )

    def test_render_unknown_type_raises(self):
        from motor.model.rules import render

        with self.assertRaises(rules.UnknownRuleType):
            render({"type": "nope", "column": "x"})

    def test_expression_for_rename_and_text_cast(self):
        from motor.model.rules import Rule, expression_for

        self.assertEqual(
            expression_for(Rule(name="r", type="rename", column="x", params={}), "x"),
            '"x"',
        )
        self.assertIn(
            "VARCHAR",
            expression_for(
                Rule(name="r", type="cast", column="x", params={"to": "text"}), "x"
            ),
        )

    def test_expression_for_domain_column_restricts_values(self):
        from motor.model.rules import Rule, expression_for

        # Un `domain` con valores permitidos restringe con `IN (...)`; antes
        # devolvía la columna tal cual (no-op silencioso) y la regla no se
        # aplicaba aunque estuviera en el plan.
        expr = expression_for(
            Rule(name="r", type="domain", column="x", params={"allowed": ["a", "b"]}),
            "x",
        )
        self.assertIn("IN ('a', 'b')", expr)

    def test_expression_for_domain_without_allowed_returns_the_column(self):
        from motor.model.rules import Rule, expression_for

        self.assertEqual(
            expression_for(Rule(name="r", type="domain", column="x", params={}), "x"),
            '"x"',
        )

    def test_expression_for_a_type_without_projection_raises(self):
        from motor.model.rules import Rule, UnknownRuleType, expression_for

        # `filter` va al WHERE, no a la proyección: pedir su expresión es un
        # error de programación, no un no-op.
        with self.assertRaises(UnknownRuleType):
            expression_for(
                Rule(name="r", type="filter", column="x", params={"equals": "a"}), "x"
            )

    def test_delete_a_rule(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = connect(Path(tmp.name) / "m.sqlite3")
        model_id = store.create(conn, name="m")
        entity_id = store.add_entity(conn, model_id, name="e")
        store.add_attribute(conn, entity_id, name="x", type="text", nullable=True)
        rules.add(conn, model_id, name="r", type="cast",
                  column="x", params={"to": "text"})

        rules.delete(conn, model_id, name="r")

        self.assertEqual(rules.list_for(conn, model_id), [])


if __name__ == "__main__":
    unittest.main()
