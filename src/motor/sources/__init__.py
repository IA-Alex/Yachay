"""Ingesta de entradas para el motor de modelado (ADR-019, M1).

Detecta el formato, lee hojas (`.csv`, `.xlsx`, `.ods`) o normaliza documentos
(`.txt`, `.docx`, `.pdf`) a una tabla común, y perfila las columnas.

    from motor.sources import read_any, profile_table

    table = read_any(path)
    for column in profile_table(table):
        print(column.name, column.nulls, column.inferred_type)
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from motor.sources import detect
from motor.sources.profile import ColumnProfile, profile_table
from motor.sources.tables import EmptySheet, SheetNotFound, Table

__all__ = [
    "ColumnProfile",
    "EmptySheet",
    "SheetNotFound",
    "Table",
    "detect",
    "profile_table",
    "read_any",
]


def read_any(path: Path, *, sheet: str | None = None) -> Table:
    """Lee cualquier entrada soportada y devuelve su tabla.

    Hojas con esquema y documentos normalizados entran por el mismo camino, así
    que el resto del motor trata ambos igual.

    Un archivo que no se puede leer es un rechazo de formato, no un fallo del
    motor: los errores de las librerías de parseo (ZIP corrupto, OOXML/ODF sin
    sus partes, PDF ilegible) se traducen a :class:`UnsupportedFormat`, que es la
    excepción que todos los consumidores ya capturan (ADR-019, M1).
    """
    path = Path(path)
    kind = detect.kind_of(path)
    try:
        if kind is detect.Kind.TABLE:
            from motor.sources import tables

            return tables.read(path, sheet=sheet)
        from motor.sources import documents

        return documents.read(path)
    except (EmptySheet, SheetNotFound, detect.UnsupportedFormat):
        raise
    except _READ_ERRORS as exc:
        raise detect.UnsupportedFormat(
            f"{path.name} no se pudo leer: {exc}"
        ) from exc


# Errores previsibles de los lectores ante contenido no confiable: ZIP/OOXML/ODF
# corruptos, DOCX sin sus partes y PDF ilegibles. Se enumeran explícitamente para
# no enmascarar errores de programación (un `Exception` a secas lo haría).
# `RuntimeError` cubre `pymupdf.FileDataError` (PyMuPDF), que no hereda de OSError.
_READ_ERRORS = (
    zipfile.BadZipFile,
    KeyError,
    ValueError,
    UnicodeError,
    OSError,
    RuntimeError,
)
