"""Detección de formato de entrada para el motor de modelado (ADR-019, M1).

El formato se decide por la extensión y, cuando importa, por el contenido real
(firma de archivo), nunca por lo que el usuario declare. Dos familias:

- ``TABLE``: hojas con esquema (`.csv`, `.xlsx`, `.ods`) → tabla directa.
- ``DOCUMENT``: contenido sin esquema (`.txt`, `.docx`, `.pdf`) → se normaliza.

Formatos no soportados se rechazan con :class:`UnsupportedFormat` en lugar de
intentar adivinar.
"""

from __future__ import annotations

import enum
import zipfile
from pathlib import Path


class Kind(enum.Enum):
    """Familia de una entrada."""

    TABLE = "table"
    DOCUMENT = "document"


class UnsupportedFormat(Exception):
    """El archivo no es de un formato que la ingesta sepa leer."""


_TABLE_EXT = {".csv": "csv", ".xlsx": "xlsx", ".ods": "ods"}
_DOCUMENT_EXT = {".txt": "txt", ".docx": "docx", ".pdf": "pdf"}


def format_of(path: Path) -> str:
    """Devuelve el formato lógico ('csv', 'xlsx', …, 'docx', 'pdf')."""
    suffix = Path(path).suffix.lower()
    if suffix in _TABLE_EXT:
        return _TABLE_EXT[suffix]
    if suffix in _DOCUMENT_EXT:
        return _DOCUMENT_EXT[suffix]
    raise UnsupportedFormat(f"formato no soportado: {suffix or '(sin extensión)'}")


def kind_of(path: Path) -> Kind:
    """Indica si el archivo es una tabla o un documento."""
    path = Path(path)
    if not path.is_file():
        raise UnsupportedFormat(f"no existe el archivo: {path}")
    fmt = format_of(path)
    if fmt in ("xlsx", "ods"):
        _check_container(path, fmt)
        return Kind.TABLE
    if fmt == "csv":
        return Kind.TABLE
    if fmt == "docx":
        _check_container(path, fmt)
    return Kind.DOCUMENT


# Partes mínimas del contenedor: un ZIP válido que no las trae no es el formato
# que su extensión declara, y el lector lanzaría un error críptico (KeyError de
# zipfile) en lugar de un rechazo legible.
_REQUIRED_PART = {
    "xlsx": ("xl/workbook.xml",),
    "ods": ("content.xml",),
    "docx": ("word/document.xml",),
}


def _check_container(path: Path, fmt: str) -> None:
    """Verifica que un `.xlsx`/`.ods`/`.docx` sea el contenedor que declara.

    La extensión elige el lector, pero un archivo corrupto o truncado debe
    fallar aquí y no dentro del lector: abrir el ZIP y comprobar su parte mínima
    son condiciones baratas que evitan un `KeyError` críptico más abajo.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError) as exc:
        raise UnsupportedFormat(
            f"{path.name} no es un archivo {fmt} válido"
        ) from exc
    missing = [part for part in _REQUIRED_PART[fmt] if part not in names]
    if missing:
        raise UnsupportedFormat(
            f"{path.name} no es un archivo {fmt} válido "
            f"(falta {missing[0]})"
        )
