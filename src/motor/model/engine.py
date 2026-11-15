"""Ejecución de un plan de reglas sobre una tabla (ADR-019, M3).

El motor construye una consulta de DuckDB que aplica las reglas en orden y
devuelve una :class:`~motor.sources.tables.Table`. La tabla de entrada **no se
modifica**: la transformación es una proyección, de modo que reejecutar el mismo
plan sobre los mismos datos da el mismo resultado (idempotencia).

DuckDB sigue siendo el único motor de transformación (ADR-002); aquí no se añade
ninguno.
"""

from __future__ import annotations

import duckdb

from motor.model import joins as joins_module
from motor.model.rules import RULE_TYPES, Rule, expression_for
from motor.sources.tables import Table


def _ident(name: str) -> str:
    """Cita un identificador para SQL, escapando comillas dobles.

    Los nombres de columna llegan del archivo del usuario; citarlos evita que un
    nombre como ``x" ; DROP TABLE t --`` altere la consulta.
    """
    return '"' + str(name).replace('"', '""') + '"'


class EngineError(Exception):
    """El plan no se puede aplicar a la tabla (columna desconocida, SQL inválido)."""


def run(table: Table, plan: list[Rule]) -> Table:
    """Aplica el plan de reglas a la tabla y devuelve el resultado."""
    con = duckdb.connect(":memory:")
    try:
        _load(con, table)
        select, columns = _build_select(table, plan)
        try:
            rows = [list(row) for row in con.execute(select).fetchall()]
        except duckdb.Error as exc:  # pragma: no cover - defensa en profundidad
            # Todos los caminos de error del plan se validan antes de llegar aquí
            # (tipo de regla, columna inexistente) y `_ident` cita los
            # identificadores, así que DuckDB no debería fallar. Este guardia
            # protege ante un tipo de regla futuro que genere SQL inválido.
            raise EngineError(str(exc)) from exc
        return Table(columns=columns, rows=rows)
    finally:
        con.close()


def _load(con: duckdb.DuckDBPyConnection, table: Table, name: str = "t") -> None:
    """Carga la tabla en DuckDB con la primera fila como cabecera.

    Los nombres de columna vienen del archivo del usuario, así que se **citan**
    siempre: un nombre como ``x" ; DROP TABLE t --`` no debe poder romper la
    consulta. Los valores van como parámetros, nunca interpolados.
    """
    if not table.columns:
        raise EngineError("la tabla no tiene columnas")
    cols = ", ".join(f"{_ident(column)} VARCHAR" for column in table.columns)
    con.execute(f"CREATE TABLE {_ident(name)} ({cols})")
    placeholders = ", ".join("?" for _ in table.columns)
    insert = f"INSERT INTO {_ident(name)} VALUES ({placeholders})"
    for row in table.rows:
        values = [None if v is None else str(v) for v in row][: len(table.columns)]
        values += [None] * (len(table.columns) - len(values))
        con.execute(insert, values)


def _as_table(con: duckdb.DuckDBPyConnection, relation: str) -> Table:
    """Materializa una relación de DuckDB como :class:`Table`."""
    cursor = con.execute(f"SELECT * FROM {relation}")
    columns = [description[0] for description in cursor.description]
    rows = [list(row) for row in cursor.fetchall()]
    return Table(columns=columns, rows=rows)


def _build_select(table: Table, plan: list[Rule]) -> tuple[str, list[str]]:
    """Construye el SELECT que aplica `rename`, `cast`, `map` y `domain`.

    Varias reglas de proyección sobre la **misma** columna se aplican **en
    orden**, encadenando una subconsulta por regla: el `SELECT` final proyecta la
    columna tal como la dejó la última etapa. Colapsarlas en un diccionario (una
    regla por columna) descartaba en silencio todas menos la última, así que
    `map` + `cast` sobre una columna anulaba los valores o devolvía texto crudo
    según el orden.

    `filter` no entra en la proyección: se aplica como cláusula WHERE sobre el
    nombre final de la columna (ya renombrada y transformada). Un tipo de regla
    desconocido **falla**: ignorarlo en silencio cambiaría el resultado sin
    avisar.
    """
    for rule in plan:
        if rule.type not in RULE_TYPES:
            raise EngineError(f"tipo de regla desconocido: {rule.type!r}")
        if rule.column not in table.columns:
            raise EngineError(f"la columna {rule.column!r} no existe en la tabla")

    renames = {r.column: r.params.get("to", r.column) for r in plan if r.type == "rename"}

    # Etapas de proyección por columna, en el orden del plan. Una columna con N
    # reglas produce N etapas; la etapa i lee el resultado de la i-1.
    stages_by_column: dict[str, list[Rule]] = {}
    for rule in plan:
        if rule.type in ("cast", "map", "domain"):
            stages_by_column.setdefault(rule.column, []).append(rule)

    depth = max((len(s) for s in stages_by_column.values()), default=0)

    # `current` mapea cada columna lógica al alias que la expone en la relación
    # actual. Empieza siendo el nombre de la columna en `t`; tras cada etapa, la
    # columna transformada pasa a exponerse con un alias interno único.
    current = {column: column for column in table.columns}
    relation = _ident("t")

    for step in range(depth):
        projections: list[str] = []
        later: dict[str, str] = dict(current)
        for column in table.columns:
            stages = stages_by_column.get(column, [])
            exposed = current[column]
            if step < len(stages):
                alias = f"__step_{step}_{column}"
                projections.append(f"{expression_for(stages[step], exposed)} AS {_ident(alias)}")
                later[column] = alias
            else:
                # Sin más etapas para esta columna: se arrastra tal cual.
                projections.append(f"{_ident(exposed)} AS {_ident(exposed)}")
        relation = f"(SELECT {', '.join(projections)} FROM {relation}) AS s{step}"
        current = later

    # Proyección final: cada columna lógica toma el alias que dejó su última
    # etapa, y se renombra a su destino (`rename`) o conserva su nombre.
    final: list[str] = []
    columns: list[str] = []
    for column in table.columns:
        name = renames.get(column, column)
        final.append(f"{_ident(current[column])} AS {_ident(name)}")
        columns.append(name)

    select = f"SELECT {', '.join(final)} FROM {relation}"
    filters = _filter_clause(plan, renames)
    if filters:
        select += " WHERE " + " AND ".join(filters)
    return select, columns


def _filter_clause(plan: list[Rule], renames: dict[str, str]) -> list[str]:
    """Cláusulas WHERE de las reglas `filter`, sobre el nombre final de columna."""
    filters: list[str] = []
    for rule in plan:
        if rule.type == "filter":
            target = renames.get(rule.column, rule.column)
            value = str(rule.params.get("equals", "")).replace("'", "''")
            filters.append(f"{_ident(target)} = '{value}'")
    return filters


# -- cruces (M4) -------------------------------------------------------------


def run_join(left: Table, right: Table, join: joins_module.Join) -> Table:
    """Cruza dos tablas por el cruce dado y devuelve el resultado.

    Para ``anti`` devuelve solo las **huérfanas** de la izquierda (las que no
    tienen pareja), que es el informe de integridad referencial (C5).

    Las referencias del cruce (``join.left`` / ``join.right``) son los nombres con
    los que se registran las tablas, de modo que el mismo cruce sirve para una
    ejecución suelta o encadenada.
    """
    return run_chain({join.left: left, join.right: right}, [join])


def run_chain(
    sources: dict,
    plan: list[joins_module.Join],
) -> Table:
    """Ejecuta una secuencia de cruces, encadenando la salida de cada uno.

    ``sources`` es un mapa ``nombre -> Table``: el nombre es el que usan los
    cruces en sus campos ``left`` y ``right``. La salida de un cruce se registra
    con el nombre del cruce y puede usarse como referencia en un cruce posterior.
    """
    if not plan:
        raise EngineError("no hay cruces que ejecutar")

    con = duckdb.connect(":memory:")
    try:
        # `relations` mapea nombre lógico -> alias SEGURO en DuckDB. El nombre
        # lógico puede ser un nombre de archivo ("ventas.csv"), y DuckDB
        # interpretaría un identificador con punto como una ruta de archivo, así
        # que cada tabla se registra con un alias interno sin puntos.
        relations: dict[str, str] = {}
        for index, (name, table) in enumerate(sources.items()):
            alias = f"src_{index}"
            _load(con, table, alias)
            relations[name] = alias

        # Contador de alias físicos. Cada lado de cada cruce estrena uno, de modo
        # que un cruce de una tabla consigo misma (`left == right`) no reutiliza
        # el mismo alias: DuckDB rechaza «duplicate alias».
        next_alias = len(sources)

        def _materialize(base: str) -> str:
            """Copia un alias ya cargado bajo un alias físico nuevo y lo devuelve."""
            nonlocal next_alias
            alias = f"join_{next_alias}"
            next_alias += 1
            con.execute(f"CREATE TABLE {_ident(alias)} AS SELECT * FROM {_ident(base)}")
            return alias

        for index, join in enumerate(plan):
            left_base = relations.get(join.left)
            right_base = relations.get(join.right)
            if left_base is None:
                raise EngineError(
                    f"la referencia izquierda {join.left!r} no existe "
                    f"(disponibles: {sorted(relations)})"
                )
            if right_base is None:
                raise EngineError(
                    f"la referencia derecha {join.right!r} no existe "
                    f"(disponibles: {sorted(relations)})"
                )
            # Cada lado estrena alias físico: un self-join no colisiona.
            left_alias = _materialize(left_base)
            right_alias = _materialize(right_base)
            _check_pair_columns(con, left_alias, right_alias, join)

            clause = joins_module.render_with_refs(
                join, left_alias=left_alias, right_alias=right_alias
            )
            left_q = _ident(left_alias)
            right_q = _ident(right_alias)
            if join.type == "anti":
                condition = joins_module.orphan_condition(
                    {"on": join.on, "type": join.type, "right_ref": right_alias}
                )
                select = (
                    f"SELECT {left_q}.* FROM {left_q} {clause} WHERE {condition}"
                )
            else:
                select = f"SELECT {left_q}.*, {right_q}.* FROM {left_q} {clause}"

            alias = f"step_{index}"
            con.execute(f"CREATE TABLE {_ident(alias)} AS {select}")
            relations[join.name] = alias

        return _as_table(con, _ident(relations[plan[-1].name]))
    except duckdb.Error as exc:  # pragma: no cover - defensa en profundidad
        raise EngineError(str(exc)) from exc
    finally:
        con.close()


def _check_pair_columns(
    con: duckdb.DuckDBPyConnection, left_ref: str, right_ref: str, join
) -> None:
    """Comprueba que las columnas del cruce existen en cada lado."""
    left_cols = _columns_of(con, left_ref)
    right_cols = _columns_of(con, right_ref)
    for pair in join.on:
        if pair.get("left") not in left_cols:
            raise EngineError(
                f"la columna {pair.get('left')!r} no existe en {join.left!r}"
            )
        if pair.get("right") not in right_cols:
            raise EngineError(
                f"la columna {pair.get('right')!r} no existe en {join.right!r}"
            )


def _columns_of(con: duckdb.DuckDBPyConnection, relation: str) -> set[str]:
    cursor = con.execute(f"SELECT * FROM {relation} LIMIT 0")
    return {description[0] for description in cursor.description}
