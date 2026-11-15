"""Reglas de transformación con nombre (ADR-019, M3; funciones B1, C3, C4).

Cinco tipos deterministas:

- ``rename``: cambia el nombre de una columna.
- ``cast``:   convierte el tipo de una columna (``number`` o ``text``).
- ``map``:    reemplaza valores concretos por otros (estándar de nombres).
- ``domain``: declara valores permitidos; lo demás queda fuera.
- ``filter``: conserva solo las filas que cumplen una igualdad.

Cada regla sabe **renderizar** su parte del SQL de DuckDB. El motor (``engine``)
las encadena. Nada aquí decide por el usuario: la regla dice exactamente qué hace.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

RULE_TYPES = ("rename", "cast", "map", "domain", "filter")


class UnknownRuleType(Exception):
    """El tipo de regla no está entre los soportados."""


class DuplicateRule(Exception):
    """Ya existe una regla con ese nombre en el modelo."""


class UnknownColumn(Exception):
    """La regla apunta a una columna que no está declarada en la entidad."""


@dataclass(frozen=True)
class Rule:
    name: str
    type: str
    column: str
    params: dict = field(default_factory=dict)


# -- persistencia ------------------------------------------------------------


def add(
    conn: sqlite3.Connection,
    model_id: int,
    *,
    name: str,
    type: str,
    column: str,
    params: dict | None = None,
) -> int:
    """Guarda una regla con nombre en el modelo."""
    if type not in RULE_TYPES:
        raise UnknownRuleType(
            f"tipo {type!r} no soportado; válidos: {', '.join(RULE_TYPES)}"
        )
    if not _column_exists(conn, model_id, column):
        raise UnknownColumn(f"la columna {column!r} no existe en el modelo")
    try:
        cur = conn.execute(
            "INSERT INTO rules (model_id, name, type, column, params, position) "
            "VALUES (?,?,?,?,?, (SELECT COALESCE(MAX(position),-1)+1 FROM rules "
            "WHERE model_id = ?))",
            (model_id, name, type, column, json.dumps(params or {}), model_id),
        )
    except sqlite3.IntegrityError as exc:
        raise DuplicateRule(f"la regla {name!r} ya existe en el modelo") from exc
    conn.commit()
    return int(cur.lastrowid)


def list_for(conn: sqlite3.Connection, model_id: int) -> list[Rule]:
    """Devuelve las reglas de un modelo en su orden de aplicación."""
    return [
        Rule(name=n, type=t, column=c, params=json.loads(p))
        for n, t, c, p in conn.execute(
            "SELECT name, type, column, params FROM rules "
            "WHERE model_id = ? ORDER BY position, rule_id",
            (model_id,),
        ).fetchall()
    ]


def delete(conn: sqlite3.Connection, model_id: int, *, name: str) -> None:
    """Borra una regla por nombre."""
    conn.execute("DELETE FROM rules WHERE model_id = ? AND name = ?", (model_id, name))
    conn.commit()


# -- renderizado de SQL ------------------------------------------------------


def render(rule: dict) -> str:
    """Devuelve la expresión SQL (o fragmento) que aplica la regla.

    Para ``rename`` devuelve solo ``viejo TO nuevo`` (se usa con ``ALTER TABLE
    RENAME COLUMN``); el resto devuelve una expresión de ``SELECT``.
    """
    kind = rule["type"]
    if kind == "rename":
        # La columna origen puede venir en `from` (regla estilo worker) o en
        # `column` (forma que produce la API): ambas apuntan al mismo dato.
        source = rule.get("from") or rule.get("column")
        return f"{_q(source)} TO {_q(rule['to'])}"
    if kind == "cast":
        return _cast_expr(rule)
    if kind == "map":
        return _map_expr(rule)
    if kind == "domain":
        return _domain_expr(rule)
    if kind == "filter":
        return f'{_q(rule["column"])} = {_lit(rule["equals"])}'
    raise UnknownRuleType(kind)


def expression_for(rule: Rule, column: str) -> str:
    """Devuelve la expresión de ``SELECT`` que produce el valor de la columna."""
    if rule.type == "rename":
        return _q(column)
    if rule.type == "cast":
        target = rule.params.get("to", "text")
        if target == "number":
            return f"TRY_CAST({_q(column)} AS DOUBLE)"
        return f"CAST({_q(column)} AS VARCHAR)"
    if rule.type == "map":
        return _map_expr({"column": column, "values": rule.params.get("values", {})})
    if rule.type == "domain":
        return _domain_expr({"column": column, "allowed": rule.params.get("allowed", [])})
    # Un tipo sin expresión de proyección (p. ej. `filter`, que va al WHERE, o
    # un tipo futuro sin rama) **falla**: devolver la columna tal cual sería un
    # no-op silencioso, que es justo el defecto que este módulo evita.
    raise UnknownRuleType(rule.type)


# -- expresiones concretas ---------------------------------------------------


def _cast_expr(rule: dict) -> str:
    target = rule.get("to", "number")
    if target == "number":
        return f"TRY_CAST({_q(rule['column'])} AS DOUBLE)"
    return f"CAST({_q(rule['column'])} AS VARCHAR)"


def _map_expr(rule: dict) -> str:
    values = rule.get("values", {})
    column = _q(rule["column"])
    if not values:
        return column
    cases = " ".join(
        f"WHEN {column} = {_lit(k)} THEN {_lit(v)}" for k, v in values.items()
    )
    return f"CASE {cases} ELSE {column} END"


def _domain_expr(rule: dict) -> str:
    allowed = rule.get("allowed", [])
    column = _q(rule["column"])
    if not allowed:
        return column
    literals = ", ".join(_lit(v) for v in allowed)
    return f"CASE WHEN {column} IN ({literals}) THEN {column} ELSE NULL END"


def _q(name: str) -> str:
    """Cita un identificador para SQL."""
    return '"' + str(name).replace('"', '""') + '"'


def _lit(value) -> str:
    """Cita un literal para SQL."""
    return "'" + str(value).replace("'", "''") + "'"


def _column_exists(conn: sqlite3.Connection, model_id: int, column: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM attributes a JOIN entities e ON e.entity_id = a.entity_id "
        "WHERE e.model_id = ? AND a.name = ? LIMIT 1",
        (model_id, column),
    ).fetchone()
    return row is not None
