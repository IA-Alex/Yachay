"""Perfilado de columnas (ADR-019, M1; función C7).

Describe lo que hay —nulos, únicos, duplicados, rango, tipo probable— sin
corregir nada. Es la entrada del modelado: el perfil informa, el modelo decide.

Los valores vacíos se cuentan igual sean `None` o cadena vacía, porque una hoja
real trae los dos casos. La inferencia de tipo es deliberadamente conservadora:
solo dice 'number' cuando **todos** los valores no vacíos son numéricos; si hay
duda, dice 'text'.
"""

from __future__ import annotations

from dataclasses import dataclass

from motor.sources.tables import Table


@dataclass(frozen=True)
class ColumnProfile:
    """Resumen verificable de una columna."""

    name: str
    rows: int
    nulls: int
    unique: int
    duplicates: int
    inferred_type: str  # 'number' | 'text'
    numeric_min: float | None
    numeric_max: float | None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "rows": self.rows,
            "nulls": self.nulls,
            "unique": self.unique,
            "duplicates": self.duplicates,
            "inferred_type": self.inferred_type,
            "numeric_min": self.numeric_min,
            "numeric_max": self.numeric_max,
        }


def profile_table(table: Table) -> list[ColumnProfile]:
    """Perfila cada columna de la tabla."""
    profiles: list[ColumnProfile] = []
    for index, name in enumerate(table.columns):
        values = [row[index] if index < len(row) else None for row in table.rows]
        profiles.append(_profile_column(name, values))
    return profiles


def _profile_column(name: str, values: list) -> ColumnProfile:
    rows = len(values)
    present = [value for value in values if not _is_empty(value)]
    nulls = rows - len(present)

    seen: dict = {}
    for value in present:
        seen[value] = seen.get(value, 0) + 1
    unique = len(seen)
    # Duplicados = ocurrencias extra más allá de la primera de cada valor.
    duplicates = sum(count - 1 for count in seen.values() if count > 1)

    numbers = [_as_number(value) for value in present]
    numeric = [n for n in numbers if n is not None]

    if present and len(numeric) == len(present):
        inferred = "number"
        numeric_min = min(numeric)
        numeric_max = max(numeric)
    else:
        inferred = "text"
        numeric_min = None
        numeric_max = None

    return ColumnProfile(
        name=name,
        rows=rows,
        nulls=nulls,
        unique=unique,
        duplicates=duplicates,
        inferred_type=inferred,
        numeric_min=numeric_min,
        numeric_max=numeric_max,
    )


def _is_empty(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _as_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None
