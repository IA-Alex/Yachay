#!/usr/bin/env python3
"""Regenerate the route index and verify route parity with OpenAPI."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "desktop")]

from motor.api.routes import _DYNAMIC_ROUTES, _STATIC_ROUTES  # noqa: E402

OPENAPI = ROOT / "docs/api/openapi.yaml"
ROUTE_DOC = ROOT / "docs/api/RUTAS-API.md"


def route_pairs() -> list[tuple[str, str]]:
    pairs = [(method, template) for (method, _path), (template, _handler)
             in _STATIC_ROUTES.items()]
    pairs.extend((method, template)
                 for method, template, _pattern, _handler in _DYNAMIC_ROUTES)
    return sorted(pairs)


def openapi_pairs() -> set[tuple[str, str]]:
    text = OPENAPI.read_text(encoding="utf-8")
    lines = text.splitlines()
    result: set[tuple[str, str]] = set()
    current: str | None = None
    for line in lines:
        path = re.match(r"^  (/v1/[^:]+):\s*$", line)
        if path:
            current = path.group(1)
            continue
        if current is not None:
            operation = re.match(r"^    (get|post|put|patch|delete|head|options):\s*$", line)
            if operation:
                result.add((operation.group(1).upper(), current))
            elif line and not line.startswith(" "):
                current = None
    return result


def render() -> str:
    rows = "\n".join(f"| `{method}` | `{path}` |" for method, path in route_pairs())
    return (
        "# Rutas de la API v1\n\n"
        "Generado desde `motor.api.routes`; no editar a mano. Ejecuta "
        "`python scripts/sync_api_docs.py --write` para actualizar este índice.\n\n"
        "| Método | Ruta |\n| --- | --- |\n"
        f"{rows}\n"
    )


def main() -> int:
    write = "--write" in sys.argv[1:]
    actual = set(route_pairs())
    documented = openapi_pairs()
    if actual != documented:
        missing = sorted(actual - documented)
        stale = sorted(documented - actual)
        print(f"OpenAPI desalineado. Falta: {missing}; sobra: {stale}", file=sys.stderr)
        return 1
    expected = render()
    if write:
        ROUTE_DOC.write_text(expected, encoding="utf-8")
        print(f"Actualizado: {ROUTE_DOC.relative_to(ROOT)}")
        return 0
    if not ROUTE_DOC.is_file() or ROUTE_DOC.read_text(encoding="utf-8") != expected:
        print("RUTAS-API.md desactualizado; ejecuta el comando --write.", file=sys.stderr)
        return 1
    print(f"Rutas coherentes: {len(actual)} operaciones verificadas contra OpenAPI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
