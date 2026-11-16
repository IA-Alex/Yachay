"""Ingesta de hojas y documentos para el motor de modelado (ADR-019, M1).

Cada prueba construye su propio archivo temporal con la librería que lo escribe y
comprueba la tabla resultante: la ingesta no adivina, declara lo que encuentra.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.sources import detect, documents, tables


def _write_xlsx(path: Path, rows: list[list], sheets: dict | None = None) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Hoja1"
    for row in rows:
        ws.append(row)
    for name, extra in (sheets or {}).items():
        ws2 = wb.create_sheet(title=name)
        for row in extra:
            ws2.append(row)
    wb.save(path)


def _write_ods(path: Path, rows: list[list]) -> None:
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentSpreadsheet()
    table = Table(name="Hoja1")
    for row in rows:
        tr = TableRow()
        for value in row:
            cell = TableCell()
            cell.addElement(P(text="" if value is None else str(value)))
            tr.addElement(cell)
        table.addElement(tr)
    doc.spreadsheet.addElement(table)
    doc.save(str(path))


class DetectTests(unittest.TestCase):
    """El formato se decide por el contenido, no solo por la extensión."""

    def test_csv_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "datos.csv"
            p.write_text("a,b\n1,2\n", encoding="utf-8")
            self.assertEqual(detect.kind_of(p), detect.Kind.TABLE)

    def test_xlsx_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "datos.xlsx"
            _write_xlsx(p, [["a", "b"], [1, 2]])
            self.assertEqual(detect.kind_of(p), detect.Kind.TABLE)

    def test_rejects_archive_expansion_above_limit(self):
        import zipfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "datos.xlsx"
            with zipfile.ZipFile(p, "w") as archive:
                archive.writestr("xl/workbook.xml", "ok")
                archive.writestr("bulk.xml", "x" * 100)
            with mock.patch("motor.sources.detect.MAX_ARCHIVE_EXPANDED_BYTES", 50):
                with self.assertRaises(detect.UnsupportedFormat):
                    detect.kind_of(p)

    def test_rejects_too_many_archive_entries(self):
        import zipfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "datos.xlsx"
            with zipfile.ZipFile(p, "w") as archive:
                archive.writestr("xl/workbook.xml", "ok")
                archive.writestr("extra.xml", "ok")
            with mock.patch("motor.sources.detect.MAX_ARCHIVE_ENTRIES", 1):
                with self.assertRaises(detect.UnsupportedFormat):
                    detect.kind_of(p)

    def test_rejects_source_above_size_limit(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "datos.csv"
            p.write_text("a,b\n", encoding="utf-8")
            with mock.patch("motor.sources.detect.MAX_SOURCE_BYTES", 2):
                with self.assertRaises(detect.UnsupportedFormat):
                    detect.kind_of(p)

    def test_docx_is_a_document(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "notas.docx"
            import docx

            doc = docx.Document()
            doc.add_paragraph("hola")
            doc.save(p)
            self.assertEqual(detect.kind_of(p), detect.Kind.DOCUMENT)

    def test_txt_is_a_document(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "notas.txt"
            p.write_text("hola\nmundo\n", encoding="utf-8")
            self.assertEqual(detect.kind_of(p), detect.Kind.DOCUMENT)

    def test_unknown_extension_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "foto.png"
            p.write_bytes(b"\x89PNG")
            with self.assertRaises(detect.UnsupportedFormat):
                detect.kind_of(p)


class ExcelTableTests(unittest.TestCase):
    """`.xlsx` produce una tabla con nombres de columna y valores."""

    def test_reads_the_first_sheet_as_a_table(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ventas.xlsx"
            _write_xlsx(p, [["ciudad", "monto"], ["Cusco", 120], ["Lima", 340]])

            table = tables.read(p)

            self.assertEqual(table.columns, ["ciudad", "monto"])
            self.assertEqual(table.rows, [["Cusco", 120], ["Lima", 340]])

    def test_can_select_a_named_sheet(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "libro.xlsx"
            _write_xlsx(
                p,
                [["a"], [1]],
                sheets={"Otra": [["x", "y"], [9, 8]]},
            )
            table = tables.read(p, sheet="Otra")
            self.assertEqual(table.columns, ["x", "y"])

    def test_missing_sheet_reports_the_available_names(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "libro.xlsx"
            _write_xlsx(p, [["a"], [1]])
            with self.assertRaises(tables.SheetNotFound) as ctx:
                tables.read(p, sheet="NoExiste")
            self.assertIn("Hoja1", str(ctx.exception))

    def test_empty_sheet_is_reported_not_invented(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "vacio.xlsx"
            _write_xlsx(p, [])
            with self.assertRaises(tables.EmptySheet):
                tables.read(p)


class OdsTableTests(unittest.TestCase):
    """`.ods` produce la misma tabla que `.xlsx`."""

    def test_reads_an_ods_table(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "datos.ods"
            _write_ods(p, [["id", "nombre"], [1, "ana"], [2, "beto"]])

            table = tables.read(p)

            self.assertEqual(table.columns, ["id", "nombre"])
            self.assertEqual(table.rows, [[1.0, "ana"], [2.0, "beto"]])

    def test_rejects_repeated_columns_before_expanding_them(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "repeated.ods"
            from odf.opendocument import OpenDocumentSpreadsheet
            from odf.table import Table, TableCell, TableRow
            from odf.text import P

            doc = OpenDocumentSpreadsheet()
            sheet = Table(name="Hoja1")
            header = TableRow()
            for text in ("a", "b"):
                cell = TableCell()
                cell.addElement(P(text=text))
                header.addElement(cell)
            repeated = TableRow()
            cell = TableCell(numbercolumnsrepeated="100000000")
            cell.addElement(P(text="x"))
            repeated.addElement(cell)
            sheet.addElement(header)
            sheet.addElement(repeated)
            doc.spreadsheet.addElement(sheet)
            doc.save(str(p))

            with self.assertRaises(detect.UnsupportedFormat):
                tables.read(p)

    def test_rejects_ods_cell_and_row_limits(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "datos.ods"
            _write_ods(p, [["a", "b"], [1, 2]])
            with mock.patch("motor.sources.tables.MAX_TABLE_CELLS", 1):
                with self.assertRaises(detect.UnsupportedFormat):
                    tables.read(p)
            with mock.patch("motor.sources.tables.MAX_TABLE_ROWS", 1):
                with self.assertRaises(detect.UnsupportedFormat):
                    tables.read(p)

    def test_rejects_nonpositive_ods_column_repeat(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "repeat-zero.ods"
            from odf.opendocument import OpenDocumentSpreadsheet
            from odf.table import Table, TableCell, TableRow
            from odf.text import P

            doc = OpenDocumentSpreadsheet()
            sheet = Table(name="Hoja1")
            row = TableRow()
            for value, repeat in (("header", None), ("value", "0")):
                cell = TableCell()
                if repeat:
                    cell.setAttribute("numbercolumnsrepeated", repeat)
                cell.addElement(P(text=value))
                row.addElement(cell)
            sheet.addElement(row)
            doc.spreadsheet.addElement(sheet)
            doc.save(str(p))

            with self.assertRaises(detect.UnsupportedFormat):
                tables.read(p)


class CsvTableTests(unittest.TestCase):
    """`.csv` produce la misma tabla mediante el motor existente."""

    def test_reads_a_csv_table(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "datos.csv"
            p.write_text("id,valor\n1,100\n2,200\n", encoding="utf-8")

            table = tables.read(p)

            self.assertEqual(table.columns, ["id", "valor"])
            self.assertEqual(len(table.rows), 2)

    def test_rejects_csv_over_cell_limit(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "datos.csv"
            p.write_text("a,b\n1,2\n", encoding="utf-8")
            with mock.patch("motor.sources.tables.MAX_TABLE_CELLS", 3):
                with self.assertRaises(detect.UnsupportedFormat):
                    tables.read(p)

    def test_rejects_cumulative_cells_and_shape_limits(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "datos.csv"
            p.write_text("a\n1\n2\n", encoding="utf-8")
            with mock.patch("motor.sources.tables.MAX_TABLE_CELLS", 2):
                with self.assertRaises(detect.UnsupportedFormat):
                    tables.read(p)
            p.write_text("a,b\n", encoding="utf-8")
            with mock.patch("motor.sources.tables.MAX_TABLE_COLUMNS", 1):
                with self.assertRaises(detect.UnsupportedFormat):
                    tables.read(p)
            with mock.patch("motor.sources.tables.MAX_TABLE_CELLS", 1):
                with self.assertRaises(detect.UnsupportedFormat):
                    tables.read(p)
            p.write_text("a\n1\n", encoding="utf-8")
            with mock.patch("motor.sources.tables.MAX_TABLE_ROWS", 1):
                with self.assertRaises(detect.UnsupportedFormat):
                    tables.read(p)

    def test_rejects_csv_cell_sum_when_rows_have_different_widths(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "irregular.csv"
            p.write_text("a,b\n1,2\n3\n4\n", encoding="utf-8")
            with mock.patch("motor.sources.tables.MAX_TABLE_CELLS", 5):
                with self.assertRaises(detect.UnsupportedFormat):
                    tables.read(p)

    def test_empty_and_blank_csv_rows_are_bounded_and_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "datos.csv"
            p.write_text("a\n\n1\n", encoding="utf-8")
            table = tables.read(p)
            self.assertEqual(table.rows, [["1"]])
            p.write_text("", encoding="utf-8")
            with self.assertRaises(tables.EmptySheet):
                tables.read(p)


class DocumentTableTests(unittest.TestCase):
    """Los documentos se normalizan a {documento, linea, texto}, sin adivinar."""

    def test_txt_becomes_one_row_per_non_empty_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "notas.txt"
            p.write_text("primera\n\nsegunda\n", encoding="utf-8")

            table = documents.read(p)

            self.assertEqual(table.columns, ["documento", "linea", "texto"])
            self.assertEqual(
                table.rows, [["notas.txt", 1, "primera"], ["notas.txt", 3, "segunda"]]
            )

    def test_docx_includes_paragraphs_and_tables(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "informe.docx"
            import docx

            doc = docx.Document()
            doc.add_paragraph("Titulo del informe")
            t = doc.add_table(rows=2, cols=2)
            t.cell(0, 0).text = "id"
            t.cell(0, 1).text = "valor"
            t.cell(1, 0).text = "1"
            t.cell(1, 1).text = "100"
            doc.save(p)

            table = documents.read(p)

            texts = [row[2] for row in table.rows]
            self.assertIn("Titulo del informe", texts)
            self.assertTrue(any("100" in t for t in texts), "la tabla debe extraerse")

    def test_pdf_with_selectable_text_is_extracted(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "documento.pdf"
            _write_pdf(p, ["Linea uno del PDF", "Linea dos del PDF"])

            table = documents.read(p)

            joined = " ".join(row[2] for row in table.rows)
            self.assertIn("Linea uno del PDF", joined)
            self.assertIn("Linea dos del PDF", joined)

    def test_pdf_without_text_is_declared_not_invented(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "escaneado.pdf"
            _write_pdf(p, [])  # página sin texto (simula un escaneo)

            table = documents.read(p)

            self.assertEqual(table.rows, [])
            self.assertIn("sin texto extraíble", table.note.lower())

    def test_pdf_page_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "documento.pdf"
            _write_pdf(p, ["texto"])
            with mock.patch("motor.sources.documents.MAX_DOCUMENT_PAGES", 0):
                with self.assertRaises(detect.UnsupportedFormat):
                    documents.read(p)

    def test_document_row_and_text_limits_are_enforced(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "notas.txt"
            p.write_text("una línea", encoding="utf-8")
            with mock.patch("motor.sources.documents.MAX_DOCUMENT_ROWS", 0):
                with self.assertRaises(detect.UnsupportedFormat):
                    documents.read(p)
            with mock.patch("motor.sources.documents.MAX_DOCUMENT_TEXT_CHARS", 0):
                with self.assertRaises(detect.UnsupportedFormat):
                    documents.read(p)


def _write_pdf(path: Path, lines: list[str]) -> None:
    """Escribe un PDF de una página con las líneas dadas (texto seleccionable)."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=12)
        y += 18
    doc.save(str(path))
    doc.close()


class InspectCliTests(unittest.TestCase):
    """`yachay inspect` perfila una entrada y escribe JSON."""

    def _run(self, argv):
        import contextlib
        import io

        from motor.__main__ import main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_inspect_profiles_a_csv(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "datos.csv"
            p.write_text("ciudad,monto\nCusco,120\nLima,340\n", encoding="utf-8")

            code, out = self._run(["inspect", str(p)])

            self.assertEqual(code, 0)
            self.assertIn("ciudad", out)
            self.assertIn("monto", out)

    def test_inspect_reports_an_unsupported_format_without_crashing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "foto.png"
            p.write_bytes(b"\x89PNG")

            code, out = self._run(["inspect", str(p)])

            self.assertEqual(code, 1)
            self.assertIn("no soportado", out)

    def test_inspect_reports_a_missing_sheet(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "libro.xlsx"
            _write_xlsx(p, [["a"], [1]])

            code, out = self._run(["inspect", str(p), "--sheet", "NoExiste"])

            self.assertEqual(code, 1)
            self.assertIn("Hoja1", out)


class ReadAnyTests(unittest.TestCase):
    """`read_any` enruta tabla o documento sin que el llamador lo decida."""

    def test_routes_a_table(self):
        from motor.sources import read_any

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "datos.csv"
            p.write_text("a,b\n1,2\n", encoding="utf-8")
            self.assertEqual(read_any(p).columns, ["a", "b"])

    def test_routes_a_document(self):
        from motor.sources import read_any

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "notas.txt"
            p.write_text("hola\n", encoding="utf-8")
            self.assertEqual(read_any(p).columns, ["documento", "linea", "texto"])

    def test_a_zip_without_ooxml_parts_is_rejected_by_format(self):
        """Un ZIP válido que no es un libro/documento es rechazo de formato.

        Regresión: un `.xlsx`/`.docx` que es ZIP pero no trae sus partes hacía
        que el lector lanzara un `KeyError` de `zipfile` fuera del conjunto
        cerrado de excepciones de la ingesta (ADR-019, M1).
        """
        import zipfile

        from motor.sources import read_any

        with tempfile.TemporaryDirectory() as d:
            for name in ("roto.xlsx", "roto.docx"):
                p = Path(d) / name
                with zipfile.ZipFile(p, "w") as archive:
                    archive.writestr("hello.txt", "no es un libro")
                with self.assertRaises(detect.UnsupportedFormat):
                    read_any(p)

    def test_an_unreadable_pdf_is_rejected_by_format(self):
        """Un PDF ilegible es rechazo de formato, no un fallo del motor."""
        from motor.sources import read_any

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "roto.pdf"
            p.write_bytes(b"%PDF-1.4 no es un PDF real")
            with self.assertRaises(detect.UnsupportedFormat):
                read_any(p)

    def test_a_truncated_xlsx_inside_a_valid_zip_is_rejected_by_format(self):
        """Un contenedor correcto con una parte corrupta no tumba la ingesta."""
        import zipfile

        from motor.sources import read_any

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "truncado.xlsx"
            with zipfile.ZipFile(p, "w") as archive:
                archive.writestr("xl/workbook.xml", "<workbook")  # XML truncado
            with self.assertRaises(detect.UnsupportedFormat):
                read_any(p)


class ReadBytesTests(unittest.TestCase):
    """Lectura desde memoria (lo que usará la API al recibir un cuerpo)."""

    def test_reads_csv_bytes(self):
        from motor.sources import tables

        table = tables.read_bytes(b"id,x\n1,a\n", name="datos.csv")
        self.assertEqual(table.columns, ["id", "x"])

    def test_rejects_bytes_above_source_limit(self):
        with mock.patch("motor.sources.tables.MAX_SOURCE_BYTES", 2):
            with self.assertRaises(detect.UnsupportedFormat):
                tables.read_bytes(b"a,b", name="datos.csv")

    def test_reads_xlsx_bytes(self):
        from motor.sources import tables

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.xlsx"
            _write_xlsx(p, [["a"], [1]])
            data = p.read_bytes()
        table = tables.read_bytes(data, name="s.xlsx")
        self.assertEqual(table.columns, ["a"])


class ErrorBranchTests(unittest.TestCase):
    """Los caminos de error se reportan, no se adivinan."""

    def test_unreadable_xlsx_is_rejected_by_format(self):
        from motor.sources import detect

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "roto.xlsx"
            p.write_bytes(b"no soy un zip")
            with self.assertRaises(detect.UnsupportedFormat):
                detect.kind_of(p)

    def test_missing_file_is_reported(self):
        from motor.sources import detect

        with self.assertRaises(detect.UnsupportedFormat):
            detect.kind_of(Path("/no/existe/x.csv"))

    def test_non_supported_extension_has_no_format(self):
        from motor.sources import detect

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.bin"
            p.write_bytes(b"x")
            with self.assertRaises(detect.UnsupportedFormat):
                detect.format_of(p)

    def test_csv_with_latin1_bytes_is_read(self):
        from motor.sources import tables

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "latin.csv"
            p.write_bytes("ciudad\nArequipa\n".encode("latin-1"))
            table = tables.read(p)
            self.assertEqual(table.rows, [["Arequipa"]])

    def test_document_with_latin1_bytes_is_read(self):
        from motor.sources import documents

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "latin.txt"
            p.write_bytes("Arequipa\n".encode("latin-1"))
            table = documents.read(p)
            self.assertEqual(table.rows[0][2], "Arequipa")

    def test_txt_skips_blank_lines(self):
        from motor.sources import documents

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.txt"
            p.write_text("a\n\n\nb\n", encoding="utf-8")
            table = documents.read(p)
            self.assertEqual([row[2] for row in table.rows], ["a", "b"])

    def test_docx_skips_blank_paragraphs(self):
        from motor.sources import documents

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.docx"
            import docx

            doc = docx.Document()
            doc.add_paragraph("")
            doc.add_paragraph("contenido")
            doc.save(p)
            table = documents.read(p)
            self.assertEqual([row[2] for row in table.rows], ["contenido"])

    def test_docx_skips_empty_table_rows(self):
        from motor.sources import documents

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.docx"
            import docx

            doc = docx.Document()
            t = doc.add_table(rows=2, cols=2)
            t.cell(0, 0).text = "a"
            t.cell(0, 1).text = "b"
            doc.save(p)
            table = documents.read(p)
            self.assertEqual([row[2] for row in table.rows], ["a | b"])


class ProfileEdgeTests(unittest.TestCase):
    """Casos límite del perfilado: tipos mixtos y booleanos."""

    def test_mixed_values_are_text(self):
        from motor.sources.profile import profile_table
        from motor.sources.tables import Table

        table = Table(columns=["x"], rows=[["10"], ["abc"]])
        self.assertEqual(profile_table(table)[0].inferred_type, "text")

    def test_booleans_are_not_numbers(self):
        from motor.sources.profile import profile_table
        from motor.sources.tables import Table

        table = Table(columns=["x"], rows=[[True], [False]])
        self.assertEqual(profile_table(table)[0].inferred_type, "text")

    def test_whitespace_counts_as_null(self):
        from motor.sources.profile import profile_table
        from motor.sources.tables import Table

        table = Table(columns=["x"], rows=[["  "], ["a"]])
        self.assertEqual(profile_table(table)[0].nulls, 1)


class DefensiveBranchTests(unittest.TestCase):
    """Ramas de error y casos límite que deben fallar con mensaje, no en silencio."""

    def test_empty_csv_is_reported(self):
        from motor.sources import tables

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "v.csv"
            p.write_text("", encoding="utf-8")
            with self.assertRaises(tables.EmptySheet):
                tables.read(p)

    def test_ods_missing_sheet_lists_names(self):
        from motor.sources import tables

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "l.xlsx"
            _write_xlsx(p, [["a"], [1]])
            with self.assertRaises(tables.SheetNotFound) as ctx:
                tables.read(p, sheet="Nope")
            self.assertIn("Hoja1", str(ctx.exception))

    def test_ods_multi_sheet_selection(self):
        from motor.sources import tables

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.ods"
            _write_ods(p, [["a", "b"], [1, 2]])
            # Sin hoja explícita toma la primera.
            self.assertEqual(tables.read(p).columns, ["a", "b"])

    def test_document_format_rejected_for_table_reader(self):
        from motor.sources import detect, documents, tables

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.txt"
            p.write_text("x\n", encoding="utf-8")
            with self.assertRaises(detect.UnsupportedFormat):
                tables.read(p)  # un .txt no es hoja de cálculo
            with self.assertRaises(detect.UnsupportedFormat):
                documents.read(Path(d) / "no.xlsx")  # un .xlsx no es documento

    def test_duplicate_column_names_are_disambiguated(self):
        from motor.sources import tables

        table = tables.read_bytes(b"monto,monto,monto\n1,2,3\n", name="d.csv")
        self.assertEqual(table.columns, ["monto", "monto_2", "monto_3"])

    def test_unnamed_column_gets_a_generated_name(self):
        from motor.sources import tables

        table = tables.read_bytes(b"a,,c\n1,2,3\n", name="d.csv")
        self.assertEqual(table.columns, ["a", "columna_2", "c"])

    def test_profile_none_for_non_numeric_unknown_type(self):
        from motor.sources.profile import _as_number

        self.assertIsNone(_as_number(object()))

    def test_ods_with_two_sheets_can_select_the_second(self):
        from motor.sources import tables

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "dos.ods"
            _write_ods(p, [["a"], [1]])
            # Añade una segunda hoja al mismo documento ODS.
            from odf.opendocument import load
            from odf.table import Table, TableCell, TableRow
            from odf.text import P

            doc = load(str(p))
            t = Table(name="Segunda")
            tr = TableRow()
            cell = TableCell()
            cell.addElement(P(text="z"))
            tr.addElement(cell)
            t.addElement(tr)
            doc.spreadsheet.addElement(t)
            doc.save(str(p))

            self.assertEqual(tables.read(p, sheet="Segunda").columns, ["z"])

    def test_xlsx_without_header_row_is_empty_sheet(self):
        from motor.sources import tables

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sin_cabecera.xlsx"
            _write_xlsx(p, [[None, None], [1, 2]])
            with self.assertRaises(tables.EmptySheet):
                tables.read(p)

    def test_document_txt_latin1_fallback(self):
        from motor.sources import documents

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "acentos.txt"
            # 0xFF no es UTF-8 válido: fuerza el fallback a latin-1.
            p.write_bytes(b"Arequipa\n\xff\n")
            self.assertEqual(documents.read(p).rows[0][2], "Arequipa")

    def test_csv_latin1_fallback(self):
        from motor.sources import tables

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "latin.csv"
            p.write_bytes(b"ciudad\nCusco\n\xff\n")
            self.assertEqual(tables.read(p).rows[0][0], "Cusco")

    def test_pdf_with_blank_line_skips_it(self):
        from motor.sources import documents

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "con_blanco.pdf"
            _write_pdf(p, ["uno", "", "dos"])
            texts = [row[2] for row in documents.read(p).rows]
            self.assertEqual(texts, ["uno", "dos"])

    def test_ods_missing_second_sheet_lists_names(self):
        from motor.sources import tables

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "uno.ods"
            _write_ods(p, [["a"], [1]])
            with self.assertRaises(tables.SheetNotFound):
                tables.read(p, sheet="NoExiste")

    def test_ods_empty_cell_becomes_none(self):
        from motor.sources import tables

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "hueco.ods"
            _write_ods(p, [["a", "b"], [1, ""]])
            table = tables.read(p)
            self.assertIsNone(table.rows[0][1])


if __name__ == "__main__":
    unittest.main()
