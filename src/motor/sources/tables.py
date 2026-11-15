"""Lectura de hojas de cálculo a una tabla común (ADR-019, M1).

`.csv`, `.xlsx` y `.ods` producen el mismo :class:`Table`: nombres de columna y
filas de valores. El resto del motor no distingue de qué formato vino.

Nada se inventa: una hoja vacía o una hoja inexistente se reportan con su propia
excepción, y los nombres de columna duplicados se desambiguan de forma explícita.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from motor.sources import detect


@dataclass(frozen=True)
class Table:
    """Una tabla en memoria: columnas y filas, sin esquema todavía."""

    columns: list[str]
    rows: list[list]
    note: str = ""


class SheetNotFound(Exception):
    """La hoja pedida no existe; el mensaje lista las disponibles."""


class EmptySheet(Exception):
    """La hoja no tiene filas de datos."""


def read(path: Path, *, sheet: str | None = None) -> Table:
    """Lee una hoja y devuelve su tabla."""
    path = Path(path)
    fmt = detect.format_of(path)
    if fmt == "csv":
        return _read_csv(path)
    if fmt == "xlsx":
        return _read_xlsx(path, sheet)
    if fmt == "ods":
        return _read_ods(path, sheet)
    raise detect.UnsupportedFormat(f"{fmt} no es una hoja de cálculo")


def read_bytes(data: bytes, *, name: str, sheet: str | None = None) -> Table:
    """Lee una hoja desde memoria (útil para la API, que recibe un cuerpo)."""
    suffix = Path(name).suffix.lower()
    if suffix == ".csv":
        return _read_csv_text(data.decode("utf-8-sig", errors="replace"))
    # `.xlsx`/`.ods` son ZIP: se escriben a temporales vía el lector de ruta.
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / name
        p.write_bytes(data)
        return read(p, sheet=sheet)


# -- CSV --------------------------------------------------------------------


def _read_csv(path: Path) -> Table:
    raw = path.read_bytes()
    # UTF-8 con BOM es lo que emiten Excel y LibreOffice; si falla, latin-1.
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return _read_csv_text(text)


def _read_csv_text(text: str) -> Table:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise EmptySheet("el CSV no tiene filas")
    columns = _unique_columns(rows[0])
    body = [row for row in rows[1:] if any(cell != "" for cell in row)]
    return Table(columns=columns, rows=body)


# -- XLSX -------------------------------------------------------------------


def _read_xlsx(path: Path, sheet: str | None) -> Table:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet is not None:
            if sheet not in wb.sheetnames:
                raise SheetNotFound(
                    f"hoja {sheet!r} no existe; disponibles: {wb.sheetnames}"
                )
            ws = wb[sheet]
        else:
            ws = wb[wb.sheetnames[0]]
        rows = [list(row) for row in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    return _rows_to_table(rows)


# -- ODS --------------------------------------------------------------------


def _read_ods(path: Path, sheet: str | None) -> Table:
    from odf.opendocument import load
    from odf.table import Table as OdfTable
    from odf.table import TableCell, TableRow
    from odf.text import P

    doc = load(str(path))
    sheets = doc.spreadsheet.getElementsByType(OdfTable)
    names = [t.getAttribute("name") for t in sheets]
    if sheet is not None:
        if sheet not in names:
            raise SheetNotFound(f"hoja {sheet!r} no existe; disponibles: {names}")
        chosen = sheets[names.index(sheet)]
    else:
        chosen = sheets[0]

    rows: list[list] = []
    for tr in chosen.getElementsByType(TableRow):
        values: list = []
        for cell in tr.getElementsByType(TableCell):
            repeat = int(cell.getAttribute("numbercolumnsrepeated") or 1)
            text = "".join(
                str(p) for p in cell.getElementsByType(P)
            )
            for _ in range(repeat):
                values.append(_coerce_ods(text))
        rows.append(values)
    return _rows_to_table(rows)


def _coerce_ods(text: str):
    """ODS guarda todo como texto; se intenta número y se deja texto si no."""
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return text


# -- común ------------------------------------------------------------------


def _rows_to_table(rows: list[list]) -> Table:
    if not rows:
        raise EmptySheet("la hoja no tiene filas")
    header = list(rows[0])
    if all(cell is None or cell == "" for cell in header):
        raise EmptySheet("la hoja no tiene cabecera en la primera fila")
    columns = _unique_columns([str(c) if c is not None else "" for c in header])
    body = [
        list(row)
        for row in rows[1:]
        if any(cell is not None and cell != "" for cell in row)
    ]
    return Table(columns=columns, rows=body)


def _unique_columns(names: list[str]) -> list[str]:
    """Desambigua nombres repetidos o vacíos sin perder columnas.

    Una cabecera con dos 'monto' produce 'monto' y 'monto_2'; una columna sin
    nombre recibe 'columna_N'. Se conserva el orden.
    """
    seen: dict[str, int] = {}
    result: list[str] = []
    for index, raw in enumerate(names, start=1):
        base = raw.strip() or f"columna_{index}"
        if base in seen:
            seen[base] += 1
            result.append(f"{base}_{seen[base]}")
        else:
            seen[base] = 1
            result.append(base)
    return result
