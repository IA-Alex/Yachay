"""Inferencia de entidades desde el perfil (ADR-019, M2).

Propone una entidad a partir de una tabla ya perfilada (M1). **Propone, no
impone**: sugiere atributos con su tipo y nulos, y una clave candidata si alguna
columna es única en todas las filas. El usuario decide qué aceptar.

No hay heurísticas ocultas: la regla de clave es exactamente "la columna no tiene
nulos y todos sus valores son distintos".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from motor.sources.profile import ColumnProfile
from motor.sources.tables import Table


@dataclass(frozen=True)
class ProposedAttribute:
    name: str
    type: str
    nullable: bool


@dataclass(frozen=True)
class ProposedEntity:
    name: str
    attributes: list[ProposedAttribute] = field(default_factory=list)
    suggested_key: list[str] = field(default_factory=list)


def entity_from_table(
    table: Table, profiles: list[ColumnProfile], *, name: str
) -> ProposedEntity:
    """Construye una entidad propuesta desde la tabla y su perfil."""
    attributes = [
        ProposedAttribute(
            name=profile.name,
            type=_type_for(profile),
            nullable=profile.nulls > 0,
        )
        for profile in profiles
    ]
    return ProposedEntity(
        name=name,
        attributes=attributes,
        suggested_key=_suggest_key(profiles),
    )


def _type_for(profile: ColumnProfile) -> str:
    """El tipo del atributo sigue al tipo inferido por el perfil."""
    return "number" if profile.inferred_type == "number" else "text"


def _suggest_key(profiles: list[ColumnProfile]) -> list[str]:
    """Sugiere como clave la primera columna sin nulos y con valores únicos."""
    for profile in profiles:
        if profile.nulls == 0 and profile.unique == profile.rows and profile.rows > 0:
            return [profile.name]
    return []
