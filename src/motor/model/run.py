"""Ejecución de un modelo y huella del resultado (ADR-019, M5; función E4).

Una ejecución aplica **reglas por fuente y luego los cruces** y devuelve el
resultado. De cada resultado se guarda una **huella**: columnas, número de filas
y un sha256 del contenido.

La huella **no guarda los datos**: SQLite solo almacena estado y metadatos
(ADR-005). El hash permite comparar dos ejecuciones sin conservar ninguna celda.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass

from motor.model import engine
from motor.model.joins import Join
from motor.model.rules import Rule
from motor.sources.tables import Table

_SEP = "\x1f"
_ROW = "\x1e"


class RunError(Exception):
    """La ejecución no se puede completar (salida desconocida, reglas inválidas)."""


class RunNotFound(Exception):
    """No existe la ejecución pedida."""


@dataclass(frozen=True)
class Fingerprint:
    columns: list[str]
    rows: int
    hash: str


@dataclass(frozen=True)
class RunRecord:
    run_id: int
    model_id: int
    created_at: float
    output: str
    fingerprint: Fingerprint


def fingerprint(table: Table) -> Fingerprint:
    """Calcula la huella de un resultado.

    El hash incluye las columnas y el contenido en orden, de modo que un cambio
    de filas, de orden, de columnas o de contenido produce una huella distinta.

    La serialización no puede ser ambigua: `None` (celda nula) y `""` (celda
    vacía) son contenidos distintos —un CSV los lee como `""` y un ODS como
    `None` (ver `sources/tables.py`)— y un separador incrustado en un valor no
    puede fusionar dos celdas. Por eso cada valor se codifica con JSON, que cita
    las cadenas y escribe `null` aparte, y los separadores quedan siempre fuera
    de las citas.

    Nota de compatibilidad: las huellas guardadas antes de este cambio (bytes
    crudos unidos por separador) no coinciden con las nuevas, así que un diff
    entre una ejecución antigua y una nueva reporta «cambió» una vez. Es un
    corte único y se autocorrige al reejecutar; el motor no migra las huellas
    porque SQLite solo guarda estado (ADR-005).
    """
    digest = hashlib.sha256()
    digest.update(_SEP.join(_cell(column) for column in table.columns).encode("utf-8"))
    digest.update(_ROW.encode("utf-8"))
    for row in table.rows:
        digest.update(_SEP.join(_cell(value) for value in row).encode("utf-8"))
        digest.update(_ROW.encode("utf-8"))
    return Fingerprint(columns=list(table.columns), rows=len(table.rows),
                       hash=digest.hexdigest())


def _cell(value) -> str:
    """Codifica una celda sin ambigüedad: `None` ≠ `""`, separadores citados."""
    return json.dumps(value, ensure_ascii=False, default=str)


# -- ejecución ---------------------------------------------------------------


def execute(
    sources: dict,
    *,
    rules_by_source: dict[str, list[Rule]],
    join_plan: list[Join],
    output: str,
) -> Table:
    """Aplica reglas a cada fuente, luego los cruces, y devuelve `output`.

    `output` nombra el resultado final: el nombre de un cruce, o una fuente sin
    cruces. Los nombres de los pasos de cruce pueden reutilizarse como referencia.
    """
    prepared: dict[str, Table] = {}
    for name, table in sources.items():
        plan = rules_by_source.get(name, [])
        try:
            prepared[name] = engine.run(table, plan) if plan else table
        except engine.EngineError as exc:
            raise RunError(f"reglas de {name!r}: {exc}") from exc

    if join_plan:
        # La salida pedida puede ser un paso intermedio, así que se ejecuta solo
        # la parte de la cadena que la produce.
        return _run_to_output(prepared, join_plan, output)

    if output not in prepared:
        raise RunError(
            f"la salida {output!r} no existe (disponibles: {sorted(prepared)})"
        )
    return prepared[output]


def _run_to_output(sources: dict, join_plan: list[Join], output: str) -> Table:
    """Ejecuta solo los cruces necesarios para producir `output`.

    Recorre el plan **en orden** y se detiene al llegar a la salida pedida: los
    cruces posteriores no se ejecutan. Así `output` puede ser un paso intermedio.
    """
    needed: list[Join] = []
    for join in join_plan:
        needed.append(join)
        if join.name == output:
            break
    else:
        # `output` no es ningún cruce del plan: o es una fuente, o no existe.
        if output in sources:
            return sources[output]
        raise RunError(
            f"la salida {output!r} no existe "
            f"(cruces: {[j.name for j in join_plan]}, fuentes: {sorted(sources)})"
        )
    try:
        return engine.run_chain(sources, needed)
    except engine.EngineError as exc:
        raise RunError(str(exc)) from exc


# -- persistencia ------------------------------------------------------------


def record(
    conn: sqlite3.Connection,
    model_id: int,
    *,
    table: Table,
    output: str = "",
    now: float | None = None,
) -> int:
    """Guarda la huella de una ejecución y devuelve su identificador."""
    fp = fingerprint(table)
    at = time.time() if now is None else now
    cur = conn.execute(
        "INSERT INTO runs (model_id, created_at, output, columns, rows_count, hash) "
        "VALUES (?,?,?,?,?,?)",
        (model_id, at, output, json.dumps(fp.columns), fp.rows, fp.hash),
    )
    conn.commit()
    return int(cur.lastrowid)


def get(conn: sqlite3.Connection, run_id: int) -> RunRecord:
    """Lee una ejecución por su identificador."""
    row = conn.execute(
        "SELECT run_id, model_id, created_at, output, columns, rows_count, hash "
        "FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise RunNotFound(f"la ejecución {run_id} no existe")
    return _record_from(row)


def list_for(conn: sqlite3.Connection, model_id: int) -> list[RunRecord]:
    """Lista las ejecuciones de un modelo, la más reciente primero."""
    return [
        _record_from(row)
        for row in conn.execute(
            "SELECT run_id, model_id, created_at, output, columns, rows_count, hash "
            "FROM runs WHERE model_id = ? ORDER BY created_at DESC, run_id DESC",
            (model_id,),
        ).fetchall()
    ]


def _record_from(row) -> RunRecord:
    run_id, model_id, created_at, output, columns, rows_count, hash_ = row
    return RunRecord(
        run_id=int(run_id),
        model_id=int(model_id),
        created_at=float(created_at),
        output=output,
        fingerprint=Fingerprint(
            columns=json.loads(columns), rows=int(rows_count), hash=hash_
        ),
    )
