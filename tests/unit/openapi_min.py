"""Validador mínimo de OpenAPI 3.1 para atar las respuestas reales al contrato.

No es un validador completo: cubre solo los constructos que `docs/api/openapi.yaml`
usa de verdad, y falla ruidosamente ante cualquier otro para que un contrato más
rico no pase sin validar en silencio. Sin dependencias nuevas (el proyecto no
declara PyYAML ni `jsonschema`).

Cubre: `$ref` local, `allOf`, `type` (también lista, p. ej. `[string, "null"]`),
`const`, `enum`, `required`, `additionalProperties: false`, `items` y `pattern`.
"""

from __future__ import annotations

import re

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "null": type(None),
}


class SpecError(ValueError):
    """El documento no respeta el contrato declarado."""


def resolve(spec: dict, node: dict) -> dict:
    """Sigue un `$ref` local (`#/components/...`) hasta el esquema concreto."""
    seen: set[str] = set()
    while "$ref" in node:
        ref = node["$ref"]
        if ref in seen:
            raise SpecError(f"$ref circular: {ref}")
        seen.add(ref)
        if not ref.startswith("#/"):
            raise SpecError(f"solo se admiten $ref locales: {ref}")
        target: object = spec
        for part in ref[2:].split("/"):
            try:
                target = target[part]  # type: ignore[index]
            except (KeyError, TypeError):
                raise SpecError(f"$ref inexistente: {ref}") from None
        node = target  # type: ignore[assignment]
    return node


def _type_ok(value: object, name: str) -> bool:
    if name not in _TYPES:
        raise SpecError(f"tipo no soportado por el validador: {name!r}")
    if name != "boolean" and isinstance(value, bool) and name in ("integer", "number"):
        return False  # `True` es int en Python, pero no un entero JSON
    return isinstance(value, _TYPES[name])


def validate(spec: dict, schema: dict, value: object, path: str = "$") -> None:
    """Lanza `SpecError` con la ruta exacta del primer desvío encontrado."""
    schema = resolve(spec, schema)

    for part in schema.get("allOf", ()):
        validate(spec, part, value, path)

    expected = schema.get("type")
    if expected is not None:
        names = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(value, name) for name in names):
            raise SpecError(f"{path}: se esperaba {expected}, llegó {type(value).__name__}")
        # Con `["string","null"]` el resto de restricciones solo aplican al no-nulo.
        if value is None and "null" in names:
            return

    if "const" in schema and value != schema["const"]:
        raise SpecError(f"{path}: se esperaba const {schema['const']!r}, llegó {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise SpecError(f"{path}: {value!r} no está en el enum {schema['enum']}")
    if "pattern" in schema and isinstance(value, str):
        if re.search(schema["pattern"], value) is None:
            raise SpecError(f"{path}: {value!r} no cumple {schema['pattern']}")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", ()):
            if key not in value:
                raise SpecError(f"{path}: falta el campo obligatorio {key!r}")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise SpecError(f"{path}: campos no declarados {sorted(extra)}")
        problems: list[str] = []
        for key, sub in properties.items():
            if key in value:
                try:
                    validate(spec, sub, value[key], f"{path}.{key}")
                except SpecError as problem:
                    problems.append(str(problem))
        if problems:
            # Todos los desvíos del objeto, para que una pasada los muestre.
            raise SpecError("; ".join(problems))

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            validate(spec, schema["items"], item, f"{path}[{index}]")


def schema_for(spec: dict, verb: str, template: str) -> dict:
    """Devuelve el esquema del envelope de éxito declarado para esa operación."""
    model = spec["paths"][template][verb.lower()]["responses"]
    success = [code for code in model if str(code).startswith("2")]
    if len(success) != 1:
        raise SpecError(f"{verb} {template}: se esperaba un único 2xx, hay {sorted(success)}")
    content = model[success[0]].get("content", {}).get("application/json")
    if content is None:
        raise SpecError(f"{verb} {template}: el 2xx no declara application/json")
    return content["schema"]
