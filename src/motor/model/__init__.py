"""Modelo de datos del usuario (ADR-019, M2).

    from motor.model import store, infer

    model_id = store.create(conn, name="bitacora")
    entity_id = store.add_entity(conn, model_id, name="acciones")
    store.add_attribute(conn, entity_id, name="id", type="text", nullable=False)
    store.set_key(conn, entity_id, columns=["id"])
"""

from __future__ import annotations

from motor.model import diff, engine, infer, joins, rules, run, store

__all__ = ["diff", "engine", "infer", "joins", "rules", "run", "store"]
