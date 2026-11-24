"""Cruces entre entidades con nombre (ADR-019, M4; funciones B3, C5).

Un cruce une dos referencias —entidades o el resultado de un cruce anterior— por
una o varias columnas:

- ``inner``: solo las filas que coinciden en ambas partes.
- ``left``:  todas las de la izquierda, con la derecha o vacío.
- ``anti``:  **huérfanas**: las de la izquierda que no están en la derecha. Es el
  informe de integridad referencial del modelo (C5).

El SQL se renderiza con identificadores citados, porque los nombres de columna
llegan del archivo del usuario (misma regla que `engine`).
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from motor.sources.tables import Table

JOIN_TYPES = ("inner", "left", "anti")


class UnknownJoinType(Exception):
    """El tipo de cruce no está entre los soportados."""


class DuplicateJoin(Exception):
    """Ya existe un cruce con ese nombre en el modelo."""


@dataclass(frozen=True)
class Join:
    name: str
    left: str
    right: str
    on: list[dict] = field(default_factory=list)
    type: str = "inner"


def _ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def render(join: dict) -> str:
    """Renderiza la cláusula ``JOIN`` (sin el ``SELECT``) para DuckDB.

    Los identificadores se **cualifican** con las referencias (``left_ref``/
    ``right_ref``), porque dos tablas pueden compartir el nombre de la columna de
    unión y ``ON "ciudad" = "ciudad"`` sería ambiguo.

    Para ``anti`` devuelve un ``LEFT JOIN``; la condición de huérfano la añade el
    motor por separado, de modo que se ejecuta como un anti-join.
    """
    kind = join["type"]
    if kind not in JOIN_TYPES:
        raise UnknownJoinType(
            f"tipo {kind!r} no soportado; válidos: {', '.join(JOIN_TYPES)}"
        )
    pairs = join.get("on") or []
    if not pairs:
        raise UnknownJoinType("un cruce necesita al menos una columna en `on`")
    left_ref = join.get("left_ref") or join.get("left")
    right_ref = join.get("right_ref") or join.get("right")
    condition = " AND ".join(
        f"{_ident(left_ref)}.{_ident(pair['left'])} = "
        f"{_ident(right_ref)}.{_ident(pair['right'])}"
        for pair in pairs
    )
    keyword = "LEFT JOIN" if kind in ("left", "anti") else "JOIN"
    return f"{keyword} {_ident(right_ref)} ON {condition}"


def render_with_refs(join, *, left_alias: str, right_alias: str) -> str:
    """Como ``render`` pero usando los alias reales de cada lado.

    El motor encadena pasos y el alias de la izquierda puede ser ``step_0`` en
    lugar del nombre lógico; esta variante permite cualificar el ``ON`` con el
    alias efectivo sin perder el nombre de las columnas.
    """
    return render(
        {
            "type": join.type,
            "on": join.on,
            "left_ref": left_alias,
            "right_ref": right_alias,
        }
    )


def orphan_condition(join: dict) -> str:
    """Condición que deja solo las filas sin pareja (para ``anti``)."""
    pairs = join.get("on") or []
    if not pairs:
        raise UnknownJoinType("un cruce necesita al menos una columna en `on`")
    # Basta con que la primera columna de la derecha sea NULL: en un LEFT JOIN
    # sin coincidencia, todas las de la derecha son NULL.
    first = pairs[0]
    right_ref = join.get("right_ref") or join.get("right")
    return f"{_ident(right_ref)}.{_ident(first['right'])} IS NULL"


def diagnose(left: Table, right: Table, join: Join) -> dict:
    """Mide cobertura y cardinalidad de un cruce sin materializar el resultado."""
    if not join.on:
        raise UnknownJoinType("un cruce necesita al menos una columna en `on`")
    left_indexes = []
    right_indexes = []
    for pair in join.on:
        left_name, right_name = pair.get("left"), pair.get("right")
        if left_name not in left.columns:
            raise UnknownJoinType(f"columna izquierda desconocida: {left_name!r}")
        if right_name not in right.columns:
            raise UnknownJoinType(f"columna derecha desconocida: {right_name!r}")
        left_indexes.append(left.columns.index(left_name))
        right_indexes.append(right.columns.index(right_name))

    def counts(table: Table, indexes: list[int]) -> Counter:
        result: Counter = Counter()
        for row in table.rows:
            key = tuple(row[index] if index < len(row) else None for index in indexes)
            if all(value is not None and value != "" for value in key):
                result[key] += 1
        return result

    left_counts = counts(left, left_indexes)
    right_counts = counts(right, right_indexes)
    matched_left = sum(count for key, count in left_counts.items() if key in right_counts)
    matched_right = sum(count for key, count in right_counts.items() if key in left_counts)
    left_duplicate_rows = sum(count - 1 for count in left_counts.values() if count > 1)
    right_duplicate_rows = sum(count - 1 for count in right_counts.values() if count > 1)
    left_duplicate_keys = sum(1 for count in left_counts.values() if count > 1)
    right_duplicate_keys = sum(1 for count in right_counts.values() if count > 1)
    if left_duplicate_keys and right_duplicate_keys:
        cardinality = "many_to_many"
    elif left_duplicate_keys:
        cardinality = "many_to_one"
    elif right_duplicate_keys:
        cardinality = "one_to_many"
    else:
        cardinality = "one_to_one"
    return {
        "cardinality": cardinality,
        "left_rows": len(left.rows),
        "right_rows": len(right.rows),
        "left_distinct_keys": len(left_counts),
        "right_distinct_keys": len(right_counts),
        "left_duplicate_keys": left_duplicate_keys,
        "right_duplicate_keys": right_duplicate_keys,
        "left_duplicate_rows": left_duplicate_rows,
        "right_duplicate_rows": right_duplicate_rows,
        "left_matched_rows": matched_left,
        "left_unmatched_rows": len(left.rows) - matched_left,
        "right_matched_rows": matched_right,
        "right_unmatched_rows": len(right.rows) - matched_right,
        "left_match_rate": matched_left / len(left.rows) if left.rows else 0.0,
        "right_match_rate": matched_right / len(right.rows) if right.rows else 0.0,
        "null_key_rows": {
            "left": len(left.rows) - sum(left_counts.values()),
            "right": len(right.rows) - sum(right_counts.values()),
        },
    }


# -- persistencia ------------------------------------------------------------


def add(
    conn: sqlite3.Connection,
    model_id: int,
    *,
    name: str,
    left: str,
    right: str,
    on: list[dict],
    type: str = "inner",
) -> int:
    """Guarda un cruce con nombre en el modelo."""
    if type not in JOIN_TYPES:
        raise UnknownJoinType(
            f"tipo {type!r} no soportado; válidos: {', '.join(JOIN_TYPES)}"
        )
    try:
        cur = conn.execute(
            "INSERT INTO joins (model_id, name, left_ref, right_ref, on_pairs, "
            "type, position) VALUES (?,?,?,?,?,?, "
            "(SELECT COALESCE(MAX(position),-1)+1 FROM joins WHERE model_id = ?))",
            (model_id, name, left, right, json.dumps(on), type, model_id),
        )
    except sqlite3.IntegrityError as exc:
        raise DuplicateJoin(f"el cruce {name!r} ya existe en el modelo") from exc
    conn.commit()
    return int(cur.lastrowid)


def list_for(conn: sqlite3.Connection, model_id: int) -> list[Join]:
    """Devuelve los cruces del modelo en su orden de aplicación."""
    return [
        Join(name=n, left=left, right=right, on=json.loads(pairs), type=t)
        for n, left, right, pairs, t in conn.execute(
            "SELECT name, left_ref, right_ref, on_pairs, type FROM joins "
            "WHERE model_id = ? ORDER BY position, join_id",
            (model_id,),
        ).fetchall()
    ]


def delete(conn: sqlite3.Connection, model_id: int, *, name: str) -> None:
    """Borra un cruce por nombre."""
    conn.execute("DELETE FROM joins WHERE model_id = ? AND name = ?", (model_id, name))
    conn.commit()
