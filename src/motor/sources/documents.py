"""Normalización de documentos a tabla (ADR-019, M1).

`.txt`, `.docx` y `.pdf` no tienen esquema. En lugar de adivinar uno, se
normalizan a una tabla de tres columnas:

    ┌───────────┬───────┬────────┐
    │ documento │ linea │ texto  │
    └───────────┴───────┴────────┘

Así un documento se puede perfilar, cruzar y comparar igual que una hoja. Un PDF
sin texto extraíble (escaneado) **no se inventa**: se devuelve vacío con una nota
que lo declara.
"""

from __future__ import annotations

from pathlib import Path

from motor.limits import (
    MAX_DOCUMENT_PAGES,
    MAX_DOCUMENT_ROWS,
    MAX_DOCUMENT_TEXT_CHARS,
)
from motor.sources import detect
from motor.sources.tables import Table

COLUMNS = ["documento", "linea", "texto"]


def read(path: Path) -> Table:
    """Normaliza un documento a {documento, linea, texto}."""
    path = Path(path)
    fmt = detect.format_of(path)
    if fmt == "txt":
        return _read_txt(path)
    if fmt == "docx":
        return _read_docx(path)
    if fmt == "pdf":
        return _read_pdf(path)
    raise detect.UnsupportedFormat(f"{fmt} no es un documento")


def _read_txt(path: Path) -> Table:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    rows: list[list] = []
    chars = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip() == "":
            continue
        chars += len(line)
        _check_document_size(len(rows) + 1, chars)
        rows.append([path.name, number, line])
    return Table(columns=list(COLUMNS), rows=rows)


def _read_docx(path: Path) -> Table:
    import docx

    document = docx.Document(str(path))
    rows: list[list] = []
    number = 0
    chars = 0

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        number += 1
        chars += len(text)
        _check_document_size(len(rows) + 1, chars)
        rows.append([path.name, number, text])

    # Las tablas del documento también son contenido: cada fila se aplana con
    # celdas separadas por ' | ' para no perder la estructura.
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if not any(cells):
                continue
            number += 1
            text = " | ".join(cells)
            chars += len(text)
            _check_document_size(len(rows) + 1, chars)
            rows.append([path.name, number, text])

    return Table(columns=list(COLUMNS), rows=rows)


def _read_pdf(path: Path) -> Table:
    import fitz  # PyMuPDF

    rows: list[list] = []
    number = 0
    chars = 0
    doc = fitz.open(str(path))
    try:
        if doc.page_count > MAX_DOCUMENT_PAGES:
            raise detect.UnsupportedFormat("el PDF excede el límite de páginas")
        for page in doc:
            # `get_text()` de PyMuPDF no emite líneas vacías, así que cada línea
            # extraída es contenido real: no hace falta filtrarlas.
            for line in page.get_text().splitlines():
                number += 1
                text = line.strip()
                chars += len(text)
                _check_document_size(number, chars)
                rows.append([path.name, number, text])
    finally:
        doc.close()

    if not rows:
        return Table(
            columns=list(COLUMNS),
            rows=[],
            note="PDF sin texto extraíble (posible escaneo; se requeriría OCR).",
        )
    return Table(columns=list(COLUMNS), rows=rows)


def _check_document_size(rows: int, chars: int) -> None:
    if rows > MAX_DOCUMENT_ROWS:
        raise detect.UnsupportedFormat("el documento excede el límite de filas extraídas")
    if chars > MAX_DOCUMENT_TEXT_CHARS:
        raise detect.UnsupportedFormat("el documento excede el límite de texto extraído")
