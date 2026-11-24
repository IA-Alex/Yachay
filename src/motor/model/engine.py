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

from motor.limits import MAX_TABLE_CELLS, MAX_TABLE_COLUMNS, MAX_TABLE_ROWS
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


def append_tables(tables: list[Table]) -> Table:
    """Apila filas de tablas con columnas idénticas mediante ``UNION ALL``.

    El orden de las tablas y de sus filas se conserva. Las cabeceras deben
    coincidir exactamente y en el mismo orden; no se infieren columnas ni se
    descartan duplicados.
    """
    if len(tables) < 2:
        raise EngineError("append requiere al menos dos tablas")
    expected = tables[0].columns
    if not expected:
        raise EngineError("la tabla no tiene columnas")
    for index, table in enumerate(tables[1:], start=2):
        if table.columns != expected:
            raise EngineError(
                f"la fuente {index} tiene columnas distintas; "
                f"se esperaba {expected!r} y se recibió {table.columns!r}"
            )

    con = duckdb.connect(":memory:")
    try:
        names = [f"append_{index}" for index in range(len(tables))]
        for name, table in zip(names, tables, strict=True):
            _load(con, table, name=name)
        query = " UNION ALL ".join(
            f"SELECT * FROM {_ident(name)}" for name in names
        )
        try:
            rows = [list(row) for row in con.execute(query).fetchall()]
        except duckdb.Error as exc:  # pragma: no cover - defensa en profundidad
            raise EngineError(str(exc)) from exc
        return Table(columns=list(expected), rows=rows)
    finally:
        con.close()


def align_tables(
    tables: list[Table],
    *,
    column_maps: list[dict[str, str]] | None = None,
    columns: list[str] | None = None,
) -> tuple[Table, dict]:
    """Alinea esquemas por nombre y apila filas, declarando ausencias.

    El orden canónico predeterminado es el primer orden de aparición entre las
    fuentes. `column_maps` contiene un mapa por fuente (nombre original → nombre
    canónico); `columns` permite fijar el orden y el conjunto final de columnas.
    A diferencia de :func:`append_tables`, nunca cambia el append estricto.
    """
    if len(tables) < 2:
        raise EngineError("la alineación requiere al menos dos tablas")
    maps = [{} for _ in tables] if column_maps is None else column_maps
    if len(maps) != len(tables):
        raise EngineError("debe existir un mapa de columnas por fuente")

    mapped: list[dict[str, int]] = []
    ordered: list[str] = []
    source_report = []
    for source_index, (table, mapping) in enumerate(zip(tables, maps, strict=True), start=1):
        if not isinstance(mapping, dict):
            raise EngineError(f"el mapa de la fuente {source_index} debe ser un objeto")
        unknown_names = sorted(set(mapping) - set(table.columns))
        if unknown_names:
            raise EngineError(
                f"el mapa de la fuente {source_index} contiene columnas desconocidas: "
                f"{unknown_names!r}"
            )
        source_columns: dict[str, int] = {}
        for index, original in enumerate(table.columns):
            canonical = mapping.get(original, original)
            if not isinstance(canonical, str) or not canonical.strip():
                raise EngineError("los nombres canónicos deben ser texto no vacío")
            if canonical in source_columns:
                raise EngineError(
                    f"la fuente {source_index} mapea más de una columna a {canonical!r}"
                )
            source_columns[canonical] = index
            if canonical not in ordered:
                ordered.append(canonical)
        mapped.append(source_columns)

    if columns is not None and not isinstance(columns, list):
        raise EngineError("el esquema final debe ser una lista de columnas")
    final_columns = list(columns) if columns is not None else ordered
    if any(not isinstance(column, str) or not column.strip() for column in final_columns):
        raise EngineError("los nombres de columna final deben ser texto no vacío")
    if not final_columns or len(set(final_columns)) != len(final_columns):
        raise EngineError("el esquema final debe tener columnas únicas")
    if len(final_columns) > MAX_TABLE_COLUMNS:
        raise EngineError("el esquema final excede el límite de columnas")

    aligned_rows: list[list] = []
    for table, indexes in zip(tables, mapped, strict=True):
        for row in table.rows:
            if len(aligned_rows) + 2 > MAX_TABLE_ROWS:
                raise EngineError("la tabla alineada excede el límite de filas")
            if (len(aligned_rows) + 2) * len(final_columns) > MAX_TABLE_CELLS:
                raise EngineError("la tabla alineada excede el límite de celdas")
            aligned_rows.append([
                row[indexes[column]] if column in indexes and indexes[column] < len(row) else None
                for column in final_columns
            ])
    for source_index, indexes in enumerate(mapped, start=1):
        source_report.append({
            "source": source_index,
            "missing_columns": [column for column in final_columns if column not in indexes],
            "unmapped_columns": sorted(set(indexes) - set(final_columns)),
            "rows": len(tables[source_index - 1].rows),
        })
    return Table(columns=final_columns, rows=aligned_rows), {
        "columns": final_columns,
        "rows": len(aligned_rows),
        "omitted_columns": sorted(set(ordered) - set(final_columns)),
        "sources": source_report,
    }


def deduplicate(
    table: Table, *, keys: list[str], keep: str = "first"
) -> tuple[Table, dict]:
    """Elimina duplicados por clave explícita y conserva el orden de entrada."""
    if not keys:
        raise EngineError("la deduplicación necesita al menos una clave")
    if keep not in ("first", "last"):
        raise EngineError("`keep` debe ser `first` o `last`")
    unknown = [key for key in keys if key not in table.columns]
    if unknown:
        raise EngineError(f"claves desconocidas: {unknown!r}")
    indexes = [table.columns.index(key) for key in keys]
    selected: dict[tuple, int] = {}
    order = range(len(table.rows)) if keep == "first" else range(len(table.rows) - 1, -1, -1)
    for row_index in order:
        row = table.rows[row_index]
        key = tuple(row[index] if index < len(row) else None for index in indexes)
        if any(value is None or value == "" for value in key):
            # A missing key cannot identify a record; preserve it as its own row.
            key = (*key, "__yachay_missing_key__", row_index)
        selected.setdefault(key, row_index)
    retained = sorted(selected.values())
    rows = [table.rows[index] for index in retained]
    return Table(columns=list(table.columns), rows=rows), {
        "rows_in": len(table.rows),
        "rows_out": len(rows),
        "duplicates_removed": len(table.rows) - len(rows),
        "keys": list(keys),
        "kept": keep,
    }


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
