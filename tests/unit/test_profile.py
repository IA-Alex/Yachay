"""Perfilado de columnas (ADR-019, M1; función C7).

El perfil describe lo que hay: nulos, únicos, duplicados, rango y tipo probable.
No corrige ni adivina; informa para que el modelo decida.
"""

from __future__ import annotations

import unittest

from motor.sources.profile import ColumnProfile, profile_table
from motor.sources.tables import Table


class ProfileTests(unittest.TestCase):
    """El perfil resume cada columna con cifras verificables."""

    def test_counts_nulls_uniques_and_duplicates(self):
        table = Table(
            columns=["ciudad", "monto"],
            rows=[
                ["Cusco", 120],
                ["Cusco", 340],
                ["", 340],
                [None, 340],
            ],
        )

        profiles = profile_table(table)
        by_name = {p.name: p for p in profiles}

        ciudad = by_name["ciudad"]
        self.assertEqual(ciudad.nulls, 2)
        self.assertEqual(ciudad.unique, 1)  # solo "Cusco"
        self.assertEqual(ciudad.duplicates, 1)  # la segunda "Cusco"
        self.assertEqual(ciudad.rows, 4)

        monto = by_name["monto"]
        self.assertEqual(monto.nulls, 0)
        self.assertEqual(monto.unique, 2)  # 120 y 340
        self.assertEqual(monto.duplicates, 2)  # 340 aparece tres veces

    def test_reports_numeric_range_when_values_are_numeric(self):
        table = Table(
            columns=["valor"], rows=[["10"], ["20"], ["30"], [""]]
        )

        profile = profile_table(table)[0]

        self.assertEqual(profile.numeric_min, 10.0)
        self.assertEqual(profile.numeric_max, 30.0)
        self.assertEqual(profile.inferred_type, "number")

    def test_infers_text_when_values_are_not_numeric(self):
        table = Table(columns=["nombre"], rows=[["ana"], ["beto"]])

        profile = profile_table(table)[0]

        self.assertEqual(profile.inferred_type, "text")
        self.assertIsNone(profile.numeric_min)

    def test_a_column_named_like_a_date_is_reported_as_text_if_not_parsable(self):
        # La inferencia de fecha por columna pertenece a una fase posterior; aquí
        # solo se declara el tipo probable, sin inventar.
        table = Table(columns=["fecha"], rows=[["no registrada"], ["2026-09-21"]])

        profile = profile_table(table)[0]

        self.assertEqual(profile.inferred_type, "text")

    def test_empty_table_profiles_nothing(self):
        self.assertEqual(profile_table(Table(columns=[], rows=[])), [])

    def test_profile_is_serialisable(self):
        table = Table(columns=["x"], rows=[["1"], ["2"]])
        as_dict = profile_table(table)[0].as_dict()
        self.assertEqual(as_dict["name"], "x")
        self.assertEqual(as_dict["rows"], 2)
        self.assertIn("inferred_type", as_dict)


class ColumnProfileShapeTests(unittest.TestCase):
    """El perfil es un dato plano, estable y comparable."""

    def test_fields_are_stable(self):
        p = ColumnProfile(
            name="c", rows=3, nulls=1, unique=2, duplicates=0,
            inferred_type="text", numeric_min=None, numeric_max=None,
        )
        self.assertEqual(
            set(p.as_dict()),
            {"name", "rows", "nulls", "unique", "duplicates",
             "inferred_type", "numeric_min", "numeric_max"},
        )


if __name__ == "__main__":
    unittest.main()
