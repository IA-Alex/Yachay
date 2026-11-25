"""Transformaciones tabulares declarativas y deterministas (ADR-021)."""

from __future__ import annotations

import ast
import datetime as dt
import math
import re
from collections import defaultdict

import duckdb

from motor.limits import MAX_TABLE_CELLS, MAX_TABLE_COLUMNS, MAX_TABLE_ROWS
from motor.model.engine import _as_table, _ident, _load
from motor.sources.tables import Table


class TransformError(Exception):
    """Transformación mal definida o no aplicable a la tabla."""


def derive_column(table: Table, *, name: str, expression: str) -> Table:
    """Calcula una columna con un subconjunto seguro de expresiones.

    La gramática permite aritmética, literales y `col("nombre")`; funciones
    disponibles: `number`, `text`, `concat`, `coalesce`, `lower`, `upper`,
    `trim`, `abs` y `round`. No admite atributos, SQL ni llamadas dinámicas.
    """
    if not isinstance(name, str) or not name.strip():
        raise TransformError("el nombre de la columna calculada no puede estar vacío")
    sql = _compile_expression(expression, set(table.columns))
    output_columns = len(table.columns) + (name not in table.columns)
    _check_size(len(table.rows), output_columns)
    con = duckdb.connect(":memory:")
    try:
        _load(con, table)
        select = []
        replaced = name in table.columns
        for column in table.columns:
            select.append(f"({sql}) AS {_ident(column)}" if column == name else _ident(column))
        if not replaced:
            select.append(f"({sql}) AS {_ident(name)}")
        try:
            query = f"SELECT {', '.join(select)} FROM {_ident('t')}"
            return _as_table(con, f"({query}) AS {_ident('derived')}")
        except duckdb.Error as exc:
            raise TransformError("la expresión no se pudo evaluar sobre estos datos") from exc
    finally:
        con.close()


def aggregate_table(table: Table, *, by: list[str], metrics: list[dict]) -> Table:
    """Agrupa filas y calcula count/sum/avg/min/max con SQL citado."""
    if (
        not isinstance(by, list)
        or any(not isinstance(column, str) for column in by)
        or len(set(by)) != len(by)
    ):
        raise TransformError("by debe ser una lista de columnas distintas")
    if not isinstance(metrics, list) or any(not isinstance(metric, dict) for metric in metrics):
        raise TransformError("metrics debe ser una lista de objetos")
    if not metrics:
        raise TransformError("se requiere al menos una métrica")
    _check_size(len(table.rows), len(by) + len(metrics))
    _require_columns(table, by)
    aliases: set[str] = set(by)
    selections = [_ident(column) for column in by]
    for metric in metrics:
        function = metric.get("function")
        name = metric.get("name")
        column = metric.get("column")
        if function not in ("count", "sum", "avg", "min", "max"):
            raise TransformError(f"métrica no soportada: {function!r}")
        if not isinstance(name, str) or not name.strip() or name in aliases:
            raise TransformError("cada métrica debe tener un nombre único")
        if function != "count" or column is not None:
            _require_columns(table, [column])
        if function in ("sum", "avg"):
            position = table.columns.index(column)
            for row in table.rows:
                value = row[position] if position < len(row) else None
                if value is None or value == "":
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    raise TransformError(
                        f"{function} encontró un valor no numérico en {column!r}"
                    ) from None
                if not math.isfinite(number):
                    raise TransformError(f"{function} encontró un número no finito")
        ref = "*" if column is None else _ident(column)
        if function in ("sum", "avg"):
            ref = f"TRY_CAST({ref} AS DOUBLE)"
        selections.append(f"{function.upper()}({ref}) AS {_ident(name)}")
        aliases.add(name)
    group = f" GROUP BY {', '.join(_ident(column) for column in by)}" if by else ""
    order = f" ORDER BY {', '.join(_ident(column) for column in by)}" if by else ""
    con = duckdb.connect(":memory:")
    try:
        _load(con, table)
        query = f"SELECT {', '.join(selections)} FROM {_ident('t')}{group}{order}"
        return _as_table(con, f"({query}) AS {_ident('aggregate')}")
    except duckdb.Error as exc:
        raise TransformError("no se pudo calcular la agregación") from exc
    finally:
        con.close()


def pivot_table(
    table: Table,
    *,
    index: list[str],
    pivot: str,
    value: str,
    function: str = "sum",
    max_columns: int = 100,
) -> Table:
    """Pivotea valores observados, con orden determinista y tope de columnas."""
    if (
        not isinstance(index, list)
        or any(not isinstance(column, str) for column in index)
        or len(set(index)) != len(index)
    ):
        raise TransformError("index debe ser una lista de columnas distintas")
    if not isinstance(max_columns, int) or max_columns < 1:
        raise TransformError("max_columns debe ser un entero positivo")
    _require_columns(table, [*index, pivot, value])
    if function not in ("count", "sum", "avg", "min", "max"):
        raise TransformError(f"agregación no soportada: {function!r}")
    if pivot == value or pivot in index or value in index:
        raise TransformError("index, pivot y value deben ser columnas distintas")
    pivot_index = table.columns.index(pivot)
    pivot_values = sorted(
        {
            str(row[pivot_index])
            for row in table.rows
            if pivot_index < len(row) and row[pivot_index] is not None
        }
    )
    observed = {
        row[pivot_index]
        for row in table.rows
        if pivot_index < len(row) and row[pivot_index] is not None
    }
    if len({str(value) for value in observed}) != len(observed):
        raise TransformError("valores del pivote distintos producen el mismo nombre de columna")
    if len(pivot_values) > max_columns:
        raise TransformError(f"el pivote excede el máximo de {max_columns} columnas")
    _check_size(len(table.rows), len(index) + len(pivot_values))
    value_index = table.columns.index(value)
    index_positions = [table.columns.index(column) for column in index]
    groups: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for row in table.rows:
        group = tuple(row[pos] if pos < len(row) else None for pos in index_positions)
        pivot_value = row[pivot_index] if pivot_index < len(row) else None
        cell = row[value_index] if value_index < len(row) else None
        if pivot_value is not None:
            groups[group][str(pivot_value)].append(cell)
    output_columns = [*index, *pivot_values]
    rows = []
    for group in sorted(groups, key=lambda item: tuple(str(value) for value in item)):
        values = []
        for pivot_value in pivot_values:
            cells = groups[group].get(pivot_value, [])
            values.append(_aggregate_values(cells, function))
        rows.append([*group, *values])
    return Table(columns=output_columns, rows=rows)


def unpivot_table(
    table: Table,
    *,
    index: list[str],
    columns: list[str],
    name_column: str = "variable",
    value_column: str = "value",
) -> Table:
    """Convierte columnas indicadas en pares nombre/valor y conserva dimensiones."""
    if (
        not isinstance(index, list)
        or not isinstance(columns, list)
        or any(not isinstance(column, str) for column in [*index, *columns])
        or len(set(index)) != len(index)
        or len(set(columns)) != len(columns)
    ):
        raise TransformError("index y columns deben ser listas de nombres distintos")
    _require_columns(table, [*index, *columns])
    if not columns:
        raise TransformError("unpivot necesita al menos una columna de valor")
    if (
        not isinstance(name_column, str)
        or not isinstance(value_column, str)
        or name_column == value_column
        or name_column in index
        or value_column in index
    ):
        raise TransformError("los nombres de salida deben ser distintos de las dimensiones")
    _check_size(len(table.rows) * len(columns), len(index) + 2)
    index_positions = [table.columns.index(column) for column in index]
    value_positions = [(column, table.columns.index(column)) for column in columns]
    rows = []
    for row in table.rows:
        dimensions = [row[pos] if pos < len(row) else None for pos in index_positions]
        for column, position in value_positions:
            rows.append([*dimensions, column, row[position] if position < len(row) else None])
    return Table(columns=[*index, name_column, value_column], rows=rows)


def normalize_table(
    table: Table,
    *,
    numbers: dict[str, dict] | None = None,
    dates: dict[str, str] | None = None,
) -> tuple[Table, dict]:
    """Normaliza formatos solo en columnas configuradas explícitamente."""
    numbers = numbers or {}
    dates = dates or {}
    if not isinstance(numbers, dict) or not isinstance(dates, dict):
        raise TransformError("numbers y dates deben ser objetos")
    _require_columns(table, [*numbers, *dates])
    for column, spec in numbers.items():
        if not isinstance(spec, dict):
            raise TransformError(f"la configuración numérica de {column!r} debe ser objeto")
        if set(spec) - {"decimal", "thousands"}:
            raise TransformError(
                f"la configuración numérica de {column!r} tiene campos desconocidos"
            )
        decimal, thousands = spec.get("decimal", "."), spec.get("thousands", "")
        if decimal not in (".", ",") or thousands not in ("", ".", ","):
            raise TransformError("separadores numéricos deben ser punto o coma")
        if decimal == thousands and thousands:
            raise TransformError("separador decimal y de miles deben ser distintos")
    for column, fmt in dates.items():
        if not isinstance(fmt, str) or not fmt:
            raise TransformError(f"el formato de fecha de {column!r} debe ser texto")
    number_positions = {table.columns.index(name): spec for name, spec in numbers.items()}
    date_positions = {table.columns.index(name): fmt for name, fmt in dates.items()}
    rows = []
    failures = []
    for row_number, source in enumerate(table.rows, start=2):
        row = list(source)
        for index, spec in number_positions.items():
            value = row[index] if index < len(row) else None
            if value is None or value == "":
                continue
            try:
                row[index] = _parse_number(
                    value,
                    decimal=spec.get("decimal", "."),
                    thousands=spec.get("thousands", ""),
                )
            except ValueError:
                failures.append(
                    {"row": row_number, "column": table.columns[index], "kind": "number"}
                )
        for index, fmt in date_positions.items():
            value = row[index] if index < len(row) else None
            if value is None or value == "":
                continue
            try:
                parsed = dt.datetime.strptime(str(value), fmt)
                has_time = any(token in fmt for token in ("%H", "%I", "%M", "%S", "%f"))
                if has_time:
                    row[index] = parsed.isoformat(timespec="seconds")
                else:
                    row[index] = parsed.date().isoformat()
            except ValueError:
                failures.append({"row": row_number, "column": table.columns[index], "kind": "date"})
        rows.append(row)
    return Table(columns=list(table.columns), rows=rows), {
        "rows": len(rows),
        "parse_failures": failures,
        "failure_count": len(failures),
    }


def _compile_expression(expression: str, columns: set[str]) -> str:
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 500:
        raise TransformError("la expresión debe tener entre 1 y 500 caracteres")
    try:
        tree = ast.parse(expression, mode="eval")
        return _node_sql(tree.body, columns)
    except (SyntaxError, TypeError, ValueError) as exc:
        raise TransformError("expresión inválida o con operaciones no permitidas") from exc


def _node_sql(node: ast.AST, columns: set[str]) -> str:
    operators = {
        ast.Add: "+",
        ast.Sub: "-",
        ast.Mult: "*",
        ast.Div: "/",
        ast.Mod: "%",
    }
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float)):
        if isinstance(node.value, str):
            return "'" + node.value.replace("'", "''") + "'"
        if isinstance(node.value, bool):
            raise TransformError("no se permiten valores booleanos en las expresiones")
        try:
            finite = math.isfinite(float(node.value))
        except OverflowError as exc:
            raise TransformError("valor numérico fuera de rango") from exc
        if not finite:
            raise TransformError("no se permiten valores numéricos no finitos")
        return str(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in operators:
        left = _node_sql(node.left, columns)
        right = _node_sql(node.right, columns)
        return (
            f"(TRY_CAST({left} AS DOUBLE) {operators[type(node.op)]} TRY_CAST({right} AS DOUBLE))"
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        sign = "+" if isinstance(node.op, ast.UAdd) else "-"
        return f"({sign}TRY_CAST({_node_sql(node.operand, columns)} AS DOUBLE))"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id
        if node.keywords:
            raise TransformError("no se permiten argumentos nombrados")
        if name == "col" and len(node.args) == 1 and isinstance(node.args[0], ast.Constant):
            column = node.args[0].value
            if not isinstance(column, str) or column not in columns:
                raise TransformError(f"columna desconocida en la expresión: {column!r}")
            return _ident(column)
        if name == "number" and len(node.args) == 1:
            return f"TRY_CAST({_node_sql(node.args[0], columns)} AS DOUBLE)"
        if name == "text" and len(node.args) == 1:
            return f"CAST({_node_sql(node.args[0], columns)} AS VARCHAR)"
        if name in ("lower", "upper", "trim", "abs") and len(node.args) == 1:
            return f"{name.upper()}({_node_sql(node.args[0], columns)})"
        if name == "round" and 1 <= len(node.args) <= 2:
            args = [_node_sql(arg, columns) for arg in node.args]
            args[0] = f"TRY_CAST({args[0]} AS DOUBLE)"
            return f"ROUND({', '.join(args)})"
        if name in ("concat", "coalesce") and len(node.args) >= 1:
            args = [f"CAST({_node_sql(arg, columns)} AS VARCHAR)" for arg in node.args]
            function = "CONCAT" if name == "concat" else "COALESCE"
            return f"{function}({', '.join(args)})"
    raise TransformError(f"nodo no permitido en la expresión: {type(node).__name__}")


def _require_columns(table: Table, columns: list[str]) -> None:
    unknown = [column for column in columns if column not in table.columns]
    if unknown:
        raise TransformError(f"columnas desconocidas: {unknown!r}")


def _check_size(rows: int, columns: int) -> None:
    if columns > MAX_TABLE_COLUMNS:
        raise TransformError("la salida excede el límite de columnas")
    if rows + 1 > MAX_TABLE_ROWS:
        raise TransformError("la salida excede el límite de filas")
    if (rows + 1) * columns > MAX_TABLE_CELLS:
        raise TransformError("la salida excede el límite de celdas")


def _aggregate_values(values: list, function: str):
    present = [value for value in values if value is not None]
    if function == "count":
        return len(present)
    if not present:
        return None
    if function in ("sum", "avg"):
        numbers = []
        for value in present:
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise TransformError("sum/avg encontró un valor que no es numérico") from None
            if not math.isfinite(number):
                raise TransformError("sum/avg encontró un número no finito")
            numbers.append(number)
        return sum(numbers) if function == "sum" else sum(numbers) / len(numbers)
    chooser = min if function == "min" else max
    return chooser(present, key=lambda value: str(value))


def _parse_number(value, *, decimal: str, thousands: str) -> float:
    if decimal not in (".", ",") or (thousands and thousands not in (".", ",")):
        raise TransformError("separadores numéricos deben ser punto o coma")
    if decimal == thousands and thousands:
        raise TransformError("separador decimal y de miles deben ser distintos")
    text = str(value).strip()
    group_pattern = (
        rf"(?:[0-9]+|[0-9]{{1,3}}(?:{re.escape(thousands)}[0-9]{{3}})+)" if thousands else r"[0-9]+"
    )
    decimal_pattern = re.escape(decimal)
    numeric_pattern = rf"[+-]?{group_pattern}(?:{decimal_pattern}[0-9]+)?(?:[eE][+-]?[0-9]+)?"
    if not re.fullmatch(numeric_pattern, text):
        raise ValueError("formato numérico incompatible")
    if thousands:
        text = text.replace(thousands, "")
    if decimal != ".":
        text = text.replace(decimal, ".")
    number = float(text)
    if not math.isfinite(number):
        raise ValueError("número no finito")
    return number
