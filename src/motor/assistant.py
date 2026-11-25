"""Bounded natural-language proposals for the existing model operations."""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
import os
import re
import socket
import sqlite3
import ssl
import time
from pathlib import Path

from motor.model.rules import RULE_TYPES

_MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"
_MAX_INSTRUCTION = 2000
_MAX_RULES = 8
_RULE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
_GATEWAY_TIMEOUT = 20
_GATEWAY_MAX_RESPONSE = 64 * 1024
_PROVIDER_HOST = "api.deepinfra.com"
_PROVIDER_PATH = "/v1/openai/chat/completions"
_PROFILE_FIELDS = {
    "name", "rows", "nulls", "unique", "duplicates", "inferred_type",
    "numeric_min", "numeric_max",
}


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect only to globally routable DNS answers while preserving TLS SNI."""

    def connect(self) -> None:
        try:
            answers = socket.getaddrinfo(
                self.host, self.port, type=socket.SOCK_STREAM
            )
            addresses = list(dict.fromkeys(item[4][0] for item in answers))
            if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
                raise OSError("DNS returned a non-public address")
            last_error = None
            for address in addresses:
                raw = None
                try:
                    raw = socket.create_connection((address, self.port), self.timeout)
                    self.sock = self._context.wrap_socket(
                        raw, server_hostname=self.host
                    )
                    return
                except OSError as exc:
                    if raw is not None:
                        raw.close()
                    last_error = exc
            raise last_error or OSError("no usable DNS address")
        except (ValueError, OSError) as exc:
            raise AssistantError(
                "No se pudo establecer una conexión segura con DeepInfra."
            ) from exc


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
    _validate_profile_schema(schema)
    credentials_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if credentials_dir:
        try:
            key = (Path(credentials_dir) / "deepinfra_api_key").read_text(
                encoding="utf-8"
            ).strip()
        except OSError as exc:
            raise AssistantError("Configura la credencial del adaptador DeepInfra.") from exc
    else:
        # Direct invocation is retained for local development; systemd uses a
        # credential file and never passes the provider key in the environment.
        key = os.environ.get("DEEPINFRA_API_KEY", "").strip()
    if not key:
        raise AssistantError("Configura la credencial del adaptador DeepInfra.")
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
    connection = None
    try:
        connection = _PinnedHTTPSConnection(
            _PROVIDER_HOST, port=443, timeout=15, context=ssl.create_default_context()
        )
        connection.request("POST", _PROVIDER_PATH, body=body, headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Connection": "close",
        })
        response = connection.getresponse()
        if response.status != 200:
            raise AssistantError("El proveedor no pudo procesar la propuesta.")
        raw = response.read(64 * 1024 + 1)
    except AssistantError:
        raise
    except (http.client.HTTPException, TimeoutError, OSError) as exc:
        raise AssistantError("No se pudo conectar con el proveedor de IA.") from exc
    finally:
        if connection is not None:
            connection.close()
    if len(raw) > 64 * 1024:
        raise AssistantError("La respuesta del proveedor excede el límite permitido.")
    try:
        envelope = json.loads(raw)
        result = json.loads(envelope["choices"][0]["message"]["content"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, RecursionError):
        raise AssistantError("El proveedor devolvió una propuesta inválida.") from None
    return validate_proposal(result, schema)


def propose_via_gateway(instruction: str, schema: list[dict]) -> dict:
    """Request a proposal from the isolated DeepInfra process over AF_UNIX."""
    instruction = _validate_instruction(instruction)
    _validate_profile_schema(schema)
    socket_path = os.environ.get("YACHAY_DEEPINFRA_SOCKET", "").strip()
    if not socket_path:
        runtime = os.environ.get("XDG_RUNTIME_DIR", "")
        if runtime:
            socket_path = os.path.join(runtime, "motor-deepinfra", "deepinfra.sock")
    if not socket_path:
        raise AssistantError("Configura el socket local del adaptador DeepInfra.")
    payload = json.dumps(
        {"instruction": instruction, "schema": schema}, ensure_ascii=False
    ).encode("utf-8") + b"\n"
    if len(payload) > 128 * 1024:
        raise AssistantError("El perfil excede el límite permitido para el adaptador.")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(_GATEWAY_TIMEOUT)
            connection.connect(socket_path)
            connection.sendall(payload)
            chunks = bytearray()
            while len(chunks) <= _GATEWAY_MAX_RESPONSE:
                chunk = connection.recv(min(4096, _GATEWAY_MAX_RESPONSE + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
                if b"\n" in chunk:
                    break
    except (OSError, TimeoutError) as exc:
        raise AssistantError("No se pudo contactar el adaptador local de DeepInfra.") from exc
    if len(chunks) > _GATEWAY_MAX_RESPONSE or b"\n" not in chunks:
        raise AssistantError("Respuesta inválida del adaptador local de DeepInfra.")
    try:
        envelope = json.loads(bytes(chunks).split(b"\n", 1)[0])
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise AssistantError("Respuesta inválida del adaptador local de DeepInfra.") from None
    if not isinstance(envelope, dict) or set(envelope) != {"proposal"}:
        message = envelope.get("error") if isinstance(envelope, dict) else None
        if isinstance(message, str) and len(message) <= 300:
            raise AssistantError(message)
        raise AssistantError("El adaptador local de DeepInfra devolvió un error.")
    return validate_proposal(envelope["proposal"], schema)


def _validate_profile_schema(schema: object) -> None:
    """Accept only bounded column-profile metadata, never arbitrary row data."""
    if not isinstance(schema, list) or len(schema) > 128:
        raise AssistantError("El modelo tiene más de 128 columnas; acota el modelo.")
    for item in schema:
        if (not isinstance(item, dict) or not set(item) <= _PROFILE_FIELDS
                or not isinstance(item.get("name"), str)
                or not item["name"] or len(item["name"]) > 200):
            raise AssistantError("El perfil contiene metadatos de columna inválidos.")
        for field in ("rows", "nulls", "unique", "duplicates"):
            if field in item and (type(item[field]) is not int or item[field] < 0):
                raise AssistantError("El perfil contiene estadísticas inválidas.")
        if "inferred_type" in item and item["inferred_type"] not in ("number", "text"):
            raise AssistantError("El perfil contiene un tipo inferido inválido.")
        for field in ("numeric_min", "numeric_max"):
            value = item.get(field)
            if value is not None and (
                (type(value) is int and abs(value) > 2**53)
                or (type(value) is float and not math.isfinite(value))
                or type(value) not in (int, float)
            ):
                raise AssistantError("El perfil contiene un rango numérico inválido.")


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
