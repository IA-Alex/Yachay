"""Reglas deterministas de calidad por fila para tablas locales."""

from __future__ import annotations

import math
import re
from collections import defaultdict

from motor.sources.tables import Table


class QualityError(Exception):
    """Una regla de calidad está incompleta o apunta a columnas inexistentes."""


def validate(table: Table, rules: list[dict]) -> dict:
    """Valida una tabla y devuelve un informe sin persistir valores de celdas.

    Tipos admitidos: ``required``, ``unique``, ``range`` y ``format``. Las filas
    se numeran como en una hoja de cálculo (cabecera en la fila 1). Los valores
    vacíos no participan en `unique`; se validan por separado con `required`.
    """
    if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
        raise QualityError("el contrato debe ser una lista de objetos de regla")
    positions = {name: index for index, name in enumerate(table.columns)}
    compiled = [_prepare(rule, positions) for rule in rules]
    issues: dict[int, list[dict]] = defaultdict(list)
    summaries = []

    for rule_index, (rule, kind, indexes, pattern) in enumerate(compiled):
        failed: set[int] = set()
        if kind == "unique":
            seen: dict[tuple, list[int]] = defaultdict(list)
            for row_index, row in enumerate(table.rows):
                key = tuple(_cell(row, index) for index in indexes)
                if any(_empty(value) for value in key):
                    continue
                seen[key].append(row_index)
            for indexes_for_key in seen.values():
                if len(indexes_for_key) > 1:
                    failed.update(indexes_for_key)
        else:
            index = indexes[0]
            for row_index, row in enumerate(table.rows):
                value = _cell(row, index)
                if not _passes(rule, kind, value, pattern):
                    failed.add(row_index)

        for row_index in sorted(failed):
            issues[row_index].append({
                "rule": rule_index,
                "type": kind,
                "columns": [table.columns[index] for index in indexes],
                "message": _message(rule, kind),
            })
        summaries.append({
            "rule": rule_index,
            "type": kind,
            "columns": [table.columns[index] for index in indexes],
            "failed_rows": len(failed),
        })

    invalid = [
        {"row": row_index + 2, "issues": row_issues}
        for row_index, row_issues in sorted(issues.items())
    ]
    return {
        "rows": len(table.rows),
        "valid_rows": len(table.rows) - len(invalid),
        "invalid_rows": len(invalid),
        "rules": summaries,
        "failures": invalid,
    }


def _prepare(rule: dict, positions: dict[str, int]) -> tuple:
    kind = rule.get("type")
    if kind not in ("required", "unique", "range", "format"):
        raise QualityError(f"tipo de validación desconocido: {kind!r}")
    columns = rule.get("columns") if kind == "unique" else [rule.get("column")]
    if not isinstance(columns, list) or not columns or any(not isinstance(c, str) for c in columns):
        raise QualityError("cada regla debe indicar una columna o una lista de columnas")
    missing = [column for column in columns if column not in positions]
    if missing:
        raise QualityError(f"columnas desconocidas: {missing!r}")
    if kind == "range":
        if "min" not in rule and "max" not in rule:
            raise QualityError("range necesita al menos `min` o `max`")
        for bound in (rule.get("min"), rule.get("max")):
            if bound is not None:
                try:
                    valid_bound = (not isinstance(bound, bool)
                                   and isinstance(bound, (int, float))
                                   and math.isfinite(float(bound)))
                except OverflowError:
                    valid_bound = False
                if not valid_bound:
                    raise QualityError("los límites de `range` deben ser numéricos y finitos")
        if rule.get("min") is not None and rule.get("max") is not None:
            if rule["min"] > rule["max"]:
                raise QualityError("el mínimo de `range` no puede superar el máximo")
    pattern = None
    if kind == "format":
        raw = rule.get("pattern")
        if not isinstance(raw, str) or not raw:
            raise QualityError("format necesita un `pattern` no vacío")
        try:
            pattern = re.compile(raw)
        except re.error as exc:
            raise QualityError("el patrón de `format` no es una expresión regular válida") from exc
    return rule, kind, [positions[column] for column in columns], pattern


def _passes(rule: dict, kind: str, value, pattern) -> bool:
    if kind == "required":
        return not _empty(value)
    if kind == "range":
        if _empty(value):
            return True
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(number):
            return False
        return ((rule.get("min") is None or number >= rule["min"])
                and (rule.get("max") is None or number <= rule["max"]))
    if kind == "format":
        return _empty(value) or pattern.fullmatch(str(value)) is not None
    return True


def _cell(row: list, index: int):
    return row[index] if index < len(row) else None


def _empty(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _message(rule: dict, kind: str) -> str:
    if kind == "required":
        return "valor obligatorio ausente"
    if kind == "unique":
        return "clave duplicada"
    if kind == "range":
        return f"valor fuera de rango [{rule.get('min')}, {rule.get('max')}]"
    return "el valor no coincide con el formato declarado"
