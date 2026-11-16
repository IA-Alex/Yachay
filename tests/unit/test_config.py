import tempfile
import unittest
from pathlib import Path

from motor.config import MotorConfig, MotorPaths


class MotorPathsTests(unittest.TestCase):
    def test_from_root_creates_expected_layout(self):
        with tempfile.TemporaryDirectory() as d:
            paths = MotorPaths.from_root(Path(d))
            self.assertTrue(paths.inbox.is_dir())
            self.assertTrue(paths.model_inputs.is_dir())
            self.assertTrue(paths.processing.is_dir())
            self.assertTrue(paths.tmp.is_dir())
            self.assertEqual(paths.sqlite_path.name, "motor.sqlite3")
            self.assertEqual(paths.processed, Path(d) / "data" / "processed")


class MotorConfigTests(unittest.TestCase):
    def test_defaults_are_explicit_startup_values(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            self.assertEqual(cfg.admision_factor, 2.0)
            self.assertEqual(cfg.lease_ttl_s, 30.0)

    def test_config_hash_is_stable_for_same_content(self):
        with tempfile.TemporaryDirectory() as d:
            toml = Path(d) / "defaults.toml"
            toml.write_text(
                "[motor]\nadmision_factor = 2.0\nlease_ttl_s = 30.0\n",
                encoding="utf-8",
            )
            a = MotorConfig.from_file(toml, root=Path(d))
            b = MotorConfig.from_file(toml, root=Path(d))
            self.assertEqual(a.config_hash, b.config_hash)
            self.assertTrue(a.config_hash)

    def test_config_hash_changes_when_a_value_changes(self):
        with tempfile.TemporaryDirectory() as d:
            toml = Path(d) / "defaults.toml"
            toml.write_text("[motor]\nadmision_factor = 2.0\n", encoding="utf-8")
            before = MotorConfig.from_file(toml, root=Path(d))
            toml.write_text("[motor]\nadmision_factor = 3.0\n", encoding="utf-8")
            after = MotorConfig.from_file(toml, root=Path(d))
            self.assertNotEqual(before.config_hash, after.config_hash)
            self.assertEqual(after.admision_factor, 3.0)

    def test_from_file_reads_values_from_toml(self):
        with tempfile.TemporaryDirectory() as d:
            toml = Path(d) / "defaults.toml"
            toml.write_text(
                "[motor]\n"
                "admision_factor = 1.5\n"
                "scanner_interval_s = 2.0\n"
                "lease_ttl_s = 15.0\n",
                encoding="utf-8",
            )
            cfg = MotorConfig.from_file(toml, root=Path(d))
            self.assertEqual(cfg.admision_factor, 1.5)
            self.assertEqual(cfg.scanner_interval_s, 2.0)
            self.assertEqual(cfg.lease_ttl_s, 15.0)

    def test_from_root_computes_a_config_hash(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = MotorConfig.from_root(Path(d))
            self.assertTrue(cfg.config_hash)

    def test_from_root_hash_matches_packaged_defaults_file(self):
        # One source of truth: the packaged defaults.toml must hash identically
        # whether loaded via from_root or via an explicit path.
        from motor.config import packaged_defaults_path

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a = MotorConfig.from_root(root)
            b = MotorConfig.from_file(packaged_defaults_path(), root=root)
            self.assertEqual(a.config_hash, b.config_hash)
            self.assertEqual(a.admision_factor, b.admision_factor)

    def test_from_root_hash_is_stable(self):
        with tempfile.TemporaryDirectory() as d:
            a = MotorConfig.from_root(Path(d))
            b = MotorConfig.from_root(Path(d))
            self.assertEqual(a.config_hash, b.config_hash)
