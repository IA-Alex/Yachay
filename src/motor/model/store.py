"""Persistencia del modelo de datos (ADR-019, M2).

Un *modelo* es un agregado con nombre y versión: entidades, cada una con sus
atributos y su clave. Vive en SQLite (ADR-005: solo estado y metadatos, nunca
datos tabulares).

Las versiones son **copias**: crear una versión nueva no modifica la anterior, de
modo que un modelo publicado puede reproducirse aunque el actual cambie.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field


class ModelNotFound(Exception):
    """No existe el modelo pedido."""


class DuplicateName(Exception):
    """El nombre ya existe en ese ámbito (modelo o entidad)."""


class UnknownAttribute(Exception):
    """La clave referencia un atributo que no existe."""


@dataclass(frozen=True)
class Attribute:
    name: str
    type: str
    nullable: bool


@dataclass(frozen=True)
class Entity:
    entity_id: int
    name: str
    attributes: list[Attribute] = field(default_factory=list)
    key: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Model:
    model_id: int
    name: str
    version: int
    entities: list[Entity] = field(default_factory=list)


# -- modelos -----------------------------------------------------------------


def create(conn: sqlite3.Connection, *, name: str, now: float | None = None) -> int:
    """Crea un modelo nuevo (versión 1) y devuelve su identificador."""
    _require_name(name)
    at = time.time() if now is None else now
    try:
        cur = conn.execute(
            "INSERT INTO models (name, version, created_at) VALUES (?, 1, ?)",
            (name, at),
        )
    except sqlite3.IntegrityError as exc:
        raise DuplicateName(f"ya existe el modelo {name!r} versión 1") from exc
    conn.commit()
    return int(cur.lastrowid)


def get(conn: sqlite3.Connection, model_id: int, *, version: int | None = None) -> Model:
    """Lee un modelo con sus entidades, atributos y claves.

    La versión se busca por nombre + versión; omitirla devuelve la más alta.
    """
    row = conn.execute(
        "SELECT model_id, name, version FROM models WHERE model_id = ?", (model_id,)
    ).fetchone()
    if row is None:
        raise ModelNotFound(f"modelo {model_id} no existe")
    base_id, name, _ = row

    if version is None:
        chosen = conn.execute(
            "SELECT model_id, version FROM models WHERE name = ? "
            "ORDER BY version DESC LIMIT 1",
            (name,),
        ).fetchone()
    else:
        chosen = conn.execute(
            "SELECT model_id, version FROM models WHERE name = ? AND version = ?",
            (name, version),
        ).fetchone()
    if chosen is None:
        raise ModelNotFound(f"modelo {name!r} versión {version} no existe")

    return _load(conn, int(chosen[0]), name, int(chosen[1]))


def list_models(conn: sqlite3.Connection) -> list[Model]:
    """Devuelve la última versión de cada modelo, por nombre."""
    names = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT name FROM models ORDER BY name"
        ).fetchall()
    ]
    result: list[Model] = []
    for name in names:
        row = conn.execute(
            "SELECT model_id FROM models WHERE name = ? ORDER BY version DESC LIMIT 1",
            (name,),
        ).fetchone()
        result.append(get(conn, int(row[0])))
    return result


def versions(conn: sqlite3.Connection, model_id: int) -> list[int]:
    """Lista las versiones existentes de un modelo."""
    name = conn.execute(
        "SELECT name FROM models WHERE model_id = ?", (model_id,)
    ).fetchone()
    if name is None:
        raise ModelNotFound(f"modelo {model_id} no existe")
    return [
        int(row[0])
        for row in conn.execute(
            "SELECT version FROM models WHERE name = ? ORDER BY version", (name[0],)
        ).fetchall()
    ]


def new_version(
    conn: sqlite3.Connection, model_id: int, *, name: str, now: float | None = None
) -> int:
    """Crea una versión nueva (copia vacía) del modelo y la devuelve."""
    current = get(conn, model_id)
    at = time.time() if now is None else now
    cur = conn.execute(
        "INSERT INTO models (name, version, created_at) VALUES (?, ?, ?)",
        (name, current.version + 1, at),
    )
    conn.commit()
    return int(cur.lastrowid)


def delete(conn: sqlite3.Connection, model_id: int) -> None:
    """Borra un modelo; sus entidades y atributos caen en cascada."""
    conn.execute("DELETE FROM entities WHERE model_id = ?", (model_id,))
    conn.execute("DELETE FROM models WHERE model_id = ?", (model_id,))
    conn.commit()


# -- entidades, atributos y claves -------------------------------------------


def add_entity(conn: sqlite3.Connection, model_id: int, *, name: str) -> int:
    """Añade una entidad a un modelo."""
    _require_name(name)
    if conn.execute(
        "SELECT 1 FROM models WHERE model_id = ?", (model_id,)
    ).fetchone() is None:
        raise ModelNotFound(f"modelo {model_id} no existe")
    try:
        cur = conn.execute(
            "INSERT INTO entities (model_id, name) VALUES (?, ?)", (model_id, name)
        )
    except sqlite3.IntegrityError as exc:
        raise DuplicateName(f"la entidad {name!r} ya existe en el modelo") from exc
    conn.commit()
    return int(cur.lastrowid)


def add_attribute(
    conn: sqlite3.Connection,
    entity_id: int,
    *,
    name: str,
    type: str = "text",
    nullable: bool = True,
) -> int:
    """Añade un atributo a una entidad."""
    _require_name(name)
    try:
        cur = conn.execute(
            "INSERT INTO attributes (entity_id, name, type, nullable) VALUES (?,?,?,?)",
            (entity_id, name, type, 1 if nullable else 0),
        )
    except sqlite3.IntegrityError as exc:
        raise DuplicateName(f"el atributo {name!r} ya existe en la entidad") from exc
    conn.commit()
    return int(cur.lastrowid)


def set_key(conn: sqlite3.Connection, entity_id: int, *, columns: list[str]) -> None:
    """Define la clave de una entidad; los atributos deben existir."""
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM attributes WHERE entity_id = ?", (entity_id,)
        ).fetchall()
    }
    for column in columns:
        if column not in existing:
            raise UnknownAttribute(f"atributo {column!r} no existe en la entidad")
    conn.execute("DELETE FROM entity_keys WHERE entity_id = ?", (entity_id,))
    for position, column in enumerate(columns):
        conn.execute(
            "INSERT INTO entity_keys (entity_id, attribute, position) VALUES (?,?,?)",
            (entity_id, column, position),
        )
    conn.commit()


# -- internos -----------------------------------------------------------------


def _load(conn: sqlite3.Connection, model_id: int, name: str, version: int) -> Model:
    entities: list[Entity] = []
    for entity_id, entity_name in conn.execute(
        "SELECT entity_id, name FROM entities WHERE model_id = ? ORDER BY entity_id",
        (model_id,),
    ).fetchall():
        attributes = [
            Attribute(name=n, type=t, nullable=bool(nul))
            for n, t, nul in conn.execute(
                "SELECT name, type, nullable FROM attributes "
                "WHERE entity_id = ? ORDER BY attribute_id",
                (entity_id,),
            ).fetchall()
        ]
        key = [
            row[0]
            for row in conn.execute(
                "SELECT attribute FROM entity_keys WHERE entity_id = ? "
                "ORDER BY position",
                (entity_id,),
            ).fetchall()
        ]
        entities.append(
            Entity(
                entity_id=int(entity_id),
                name=entity_name,
                attributes=attributes,
                key=key,
            )
        )
    return Model(model_id=model_id, name=name, version=version, entities=entities)


def _require_name(name: str) -> None:
    if not name or not name.strip():
        raise ValueError("el nombre no puede estar vacío")
