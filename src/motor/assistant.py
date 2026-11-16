"""Bounded natural-language proposals for the existing model operations."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request

from motor.model.rules import RULE_TYPES

_ENDPOINT = "https://api.deepinfra.com/v1/openai/chat/completions"
_MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"
_MAX_INSTRUCTION = 2000
_MAX_RULES = 8
_RULE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")


class AssistantError(Exception):
    """A request or model proposal could not be safely processed."""


def operations() -> list[dict[str, str]]:
    """Describe the deterministic operation registry exposed to the model."""
    return [
        {"type": "rename", "params": "to: string"},
        {"type": "cast", "params": "to: number | text"},
        {"type": "map", "params": "values: object of explicit replacements"},
        {"type": "domain", "params": "allowed: list of explicit values"},
        {"type": "filter", "params": "equals: explicit string"},
    ]


def propose(instruction: str, schema: list[dict]) -> dict:
    """Ask the configured provider for a JSON proposal using schema only."""
    instruction = _validate_instruction(instruction)
    key = os.environ.get("DEEPINFRA_API_KEY", "").strip()
    if not key:
        raise AssistantError("Configura DEEPINFRA_API_KEY en el entorno del motor.")
    if len(schema) > 128:
        raise AssistantError("El modelo tiene más de 128 columnas; acota el modelo.")
    content = {
        "instruction": instruction,
        "schema": schema,
        "available_operations": operations(),
        "constraints": [
            "No inventes valores de filas ni transformaciones no solicitadas.",
            "Solo devuelve reglas de la lista disponible.",
            "Los mapas, dominios y filtros requieren valores escritos en la instrucción.",
            "No incluyas valores de datos; usa nombres de columna y tipos solamente.",
        ],
    }
    body = json.dumps({
        "model": _MODEL,
        "temperature": 0,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": (
                "Propón operaciones de modelado. Responde solo JSON con las claves "
                "summary y rules. Cada regla contiene name, type, column, params. "
                "Si faltan datos necesarios, devuelve rules vacío y dilo en summary."
            )},
            {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
        ],
    }).encode("utf-8")
    request = urllib.request.Request(
        _ENDPOINT,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status != 200:
                raise AssistantError("El proveedor no pudo procesar la propuesta.")
            raw = response.read(64 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AssistantError("No se pudo conectar con el proveedor de IA.") from exc
    if len(raw) > 64 * 1024:
        raise AssistantError("La respuesta del proveedor excede el límite permitido.")
    try:
        envelope = json.loads(raw)
        result = json.loads(envelope["choices"][0]["message"]["content"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, RecursionError):
        raise AssistantError("El proveedor devolvió una propuesta inválida.") from None
    return validate_proposal(result, schema)


def validate_proposal(value: object, schema: list[dict]) -> dict:
    """Enforce the allowlist and exact parameter contract before preview/apply."""
    if not isinstance(value, dict) or set(value) != {"summary", "rules"}:
        raise AssistantError("La propuesta debe contener solo summary y rules.")
    summary, proposals = value["summary"], value["rules"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 500:
        raise AssistantError("El resumen de la propuesta no es válido.")
    if not isinstance(proposals, list) or len(proposals) > _MAX_RULES:
        raise AssistantError("La propuesta debe contener entre 0 y 8 reglas.")
    columns = {item.get("name") for item in schema if isinstance(item, dict)}
    names: set[str] = set()
    clean = []
    for item in proposals:
        if not isinstance(item, dict) or set(item) != {"name", "type", "column", "params"}:
            raise AssistantError("Una regla contiene campos no permitidos.")
        name, kind, column, params = item["name"], item["type"], item["column"], item["params"]
        if (not isinstance(name, str) or not _RULE_NAME.fullmatch(name)
                or name in names):
            raise AssistantError("Nombre de regla inválido o duplicado.")
        if (not isinstance(kind, str) or kind not in RULE_TYPES
                or not isinstance(column, str) or column not in columns):
            raise AssistantError("La regla usa una operación o columna desconocida.")
        if not isinstance(params, dict):
            raise AssistantError("Los parámetros de la regla deben ser un objeto.")
        _validate_params(kind, params)
        names.add(name)
        clean.append({"name": name, "type": kind, "column": column, "params": params})
    return {"summary": summary.strip(), "rules": clean}


def _validate_instruction(instruction: object) -> str:
    if not isinstance(instruction, str) or not instruction.strip():
        raise AssistantError("Escribe qué transformación necesitas.")
    if len(instruction) > _MAX_INSTRUCTION:
        raise AssistantError("La instrucción no debe superar 2.000 caracteres.")
    return instruction.strip()


def _validate_params(kind: str, params: dict) -> None:
    expected = {
        "rename": {"to"}, "cast": {"to"}, "map": {"values"},
        "domain": {"allowed"}, "filter": {"equals"},
    }[kind]
    if set(params) != expected:
        raise AssistantError(f"Parámetros inválidos para la operación {kind}.")
    if kind == "rename" and not _short_text(params["to"], 100):
        raise AssistantError("El nuevo nombre debe ser texto de hasta 100 caracteres.")
    if kind == "cast" and params["to"] not in ("number", "text"):
        raise AssistantError("La conversión solo admite number o text.")
    if kind == "map":
        values = params["values"]
        if (not isinstance(values, dict) or len(values) > 50
                or not all(_short_text(k, 200) and _short_text(v, 200)
                           for k, v in values.items())):
            raise AssistantError("El mapa debe usar hasta 50 pares de texto explícitos.")
    if kind == "domain":
        values = params["allowed"]
        if (not isinstance(values, list) or not values or len(values) > 100
                or not all(_short_text(value, 200) for value in values)):
            raise AssistantError("El dominio debe contener entre 1 y 100 valores de texto.")
    if kind == "filter" and not _short_text(params["equals"], 200):
        raise AssistantError("El filtro requiere un valor de texto de hasta 200 caracteres.")


def _short_text(value: object, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def apply_as_new_version(
    conn: sqlite3.Connection, model_id: int, proposals: list[dict], *, now: float | None = None
) -> tuple[int, int]:
    """Clone the chosen model and add rules atomically; never edits its source."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        base = conn.execute(
            "SELECT name, version FROM models WHERE model_id = ?", (model_id,)
        ).fetchone()
        if base is None:
            raise AssistantError("El modelo no existe.")
        name, _version = base
        new_version = int(conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM models WHERE name = ?", (name,)
        ).fetchone()[0])
        at = time.time() if now is None else now
        new_id = int(conn.execute(
            "INSERT INTO models (name, version, created_at) VALUES (?,?,?)",
            (name, new_version, at),
        ).lastrowid)
        entity_ids: dict[int, int] = {}
        for entity_id, entity_name in conn.execute(
            "SELECT entity_id, name FROM entities WHERE model_id = ? ORDER BY entity_id",
            (model_id,),
        ).fetchall():
            entity_ids[entity_id] = int(conn.execute(
                "INSERT INTO entities (model_id, name) VALUES (?,?)",
                (new_id, entity_name),
            ).lastrowid)
        for old_id, new_entity_id in entity_ids.items():
            conn.execute(
                "INSERT INTO attributes (entity_id, name, type, nullable) "
                "SELECT ?, name, type, nullable FROM attributes WHERE entity_id = ?",
                (new_entity_id, old_id),
            )
            conn.execute(
                "INSERT INTO entity_keys (entity_id, attribute, position) "
                "SELECT ?, attribute, position FROM entity_keys WHERE entity_id = ?",
                (new_entity_id, old_id),
            )
        conn.execute(
            "INSERT INTO rules (model_id, name, type, column, params, position) "
            "SELECT ?, name, type, column, params, position FROM rules "
            "WHERE model_id = ? ORDER BY position, rule_id", (new_id, model_id),
        )
        conn.execute(
            "INSERT INTO joins (model_id, name, left_ref, right_ref, on_pairs, type, position) "
            "SELECT ?, name, left_ref, right_ref, on_pairs, type, position FROM joins "
            "WHERE model_id = ? ORDER BY position, join_id", (new_id, model_id),
        )
        existing = {row[0] for row in conn.execute(
            "SELECT name FROM rules WHERE model_id = ?", (new_id,)
        ).fetchall()}
        columns = {row[0] for row in conn.execute(
            "SELECT a.name FROM attributes a JOIN entities e ON e.entity_id = a.entity_id "
            "WHERE e.model_id = ?", (new_id,),
        ).fetchall()}
        validated = validate_proposal(
            {"summary": "accepted", "rules": proposals},
            [{"name": column} for column in columns],
        )
        proposals = validated["rules"]
        if not proposals:
            raise AssistantError("No hay reglas que aplicar.")
        position = int(conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM rules WHERE model_id = ?", (new_id,)
        ).fetchone()[0])
        for rule in proposals:
            if rule["name"] in existing or rule["column"] not in columns:
                raise AssistantError("La regla ya existe o usa una columna ajena al modelo.")
            if rule["type"] not in RULE_TYPES:  # pragma: no cover - validación previa
                raise AssistantError("La operación propuesta ya no está disponible.")
            conn.execute(
                "INSERT INTO rules (model_id, name, type, column, params, position) "
                "VALUES (?,?,?,?,?,?)",
                (new_id, rule["name"], rule["type"], rule["column"],
                 json.dumps(rule["params"], ensure_ascii=False), position),
            )
            existing.add(rule["name"])
            position += 1
        conn.commit()
        return new_id, new_version
    except BaseException:
        conn.rollback()
        raise
