"""End-to-end acceptance for the ADR-021 local CLI features."""

from __future__ import annotations

import runpy
import unittest
from pathlib import Path


class T022AcceptanceTests(unittest.TestCase):
    def test_all_deliveries_against_temporary_fixtures(self):
        root = Path(__file__).resolve().parents[2]
        runpy.run_path(str(root / "scripts" / "validate_t022.py"))
