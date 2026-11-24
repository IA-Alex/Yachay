"""Exportación local de tablas a archivos nuevos (ADR-021, T-030)."""

from __future__ import annotations

import csv
import datetime as dt
import math
import os
import tempfile
from pathlib import Path

from motor.limits import MAX_TABLE_CELLS, MAX_TABLE_COLUMNS, MAX_TABLE_ROWS
from motor.sources.tables import Table

SUPPORTED = {".csv", ".xlsx", ".ods"}


class ExportError(Exception):
    """El destino no se puede crear sin sobrescribir otro archivo."""


def export_table(table: Table, destination: Path) -> Path:
    """Escribe una tabla y publica el destino de forma atómica sin reemplazo."""
    destination = Path(destination).expanduser()
    suffix = destination.suffix.lower()
    if suffix not in SUPPORTED:
        raise ExportError(f"formato no soportado: {suffix or '(sin extensión)'}")
    if not destination.parent.is_dir():
        raise ExportError("la carpeta de destino no existe")
    if destination.exists():
        raise ExportError("el destino ya existe; elija otro nombre")
    if len(table.columns) > MAX_TABLE_COLUMNS:
        raise ExportError("la tabla excede el límite de columnas")
    if len(table.rows) + 1 > MAX_TABLE_ROWS:
        raise ExportError("la tabla excede el límite de filas")
    if (len(table.rows) + 1) * len(table.columns) > MAX_TABLE_CELLS:
        raise ExportError("la tabla excede el límite de celdas")

    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=".part", dir=destination.parent
    )
    temporary = Path(raw_path)
    try:
        os.fchmod(descriptor, 0o640)
        os.close(descriptor)
        if suffix == ".csv":
            _write_csv(table, temporary)
        elif suffix == ".xlsx":
            _write_xlsx(table, temporary)
        else:
            _write_ods(table, temporary)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        # Hard-link publication is atomic and fails if another process created
        # the destination after the initial existence check.
        os.link(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return destination
    except FileExistsError as exc:
        raise ExportError("el destino ya existe; no se sobrescribió") from exc
    except OSError as exc:
        raise ExportError("no se pudo publicar el archivo de salida") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _write_csv(table: Table, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(table.columns)
        writer.writerows(table.rows)


def _write_xlsx(table: Table, path: Path) -> None:
    import openpyxl
    from openpyxl.cell import WriteOnlyCell

    workbook = openpyxl.Workbook(write_only=True)
    sheet = workbook.create_sheet("Resultado")
    sheet.append([_xlsx_value(sheet, value, WriteOnlyCell) for value in table.columns])
    for row in table.rows:
        sheet.append([_xlsx_value(sheet, value, WriteOnlyCell) for value in row])
    workbook.save(path)


def _xlsx_value(sheet, value, cell_factory):
    if isinstance(value, str):
        cell = cell_factory(sheet, value=value)
        # Keep spreadsheet-like strings as text; do not turn an input beginning
        # with '=' into an executable formula during export.
        cell.data_type = "s"
        return cell
    return value


def _write_ods(table: Table, path: Path) -> None:
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table as OdfTable
    from odf.table import TableCell, TableRow
    from odf.text import P

    document = OpenDocumentSpreadsheet()
    sheet = OdfTable(name="Resultado")
    document.spreadsheet.addElement(sheet)
    for row in [table.columns, *table.rows]:
        table_row = TableRow()
        for value in row:
            attributes = {"valuetype": "string"}
            if isinstance(value, bool):
                attributes = {"valuetype": "boolean", "booleanvalue": str(value).lower()}
            elif isinstance(value, (int, float)) and math.isfinite(float(value)):
                attributes = {"valuetype": "float", "value": str(value)}
            elif isinstance(value, dt.datetime):
                attributes = {"valuetype": "date", "datevalue": value.isoformat()}
            elif isinstance(value, dt.date):
                attributes = {"valuetype": "date", "datevalue": value.isoformat()}
            cell = TableCell(**attributes)
            if value is not None:
                cell.addElement(P(text=str(value)))
            table_row.addElement(cell)
        sheet.addElement(table_row)
    document.save(str(path))
