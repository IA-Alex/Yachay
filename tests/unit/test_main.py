import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.__main__ import build_parser
from motor.config import MotorConfig
from motor.storage.db import connect
from motor.worker import Worker


class MainEntrypointTests(unittest.TestCase):
    def test_parser_exposes_run_command_and_root(self):
        parser = build_parser()
        args = parser.parse_args(["run", "--root", "/tmp/x", "--once"])
        self.assertEqual(args.command, "run")
        self.assertEqual(args.root, "/tmp/x")
        self.assertTrue(args.once)

    def test_run_once_end_to_end_via_config_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            toml = root / "defaults.toml"
            toml.write_text("[motor]\nadmision_factor = 2.0\n", encoding="utf-8")
            cfg = MotorConfig.from_file(toml, root=root)
            (cfg.paths.inbox / "a.csv").write_text("id,v\n1,a\n", encoding="utf-8")
            worker = Worker(cfg)
            self.assertIsNotNone(worker.run_once())  # B2: one cycle processes
            conn = connect(cfg.paths.sqlite_path)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 1
            )

    def test_main_runs_one_cycle_and_returns_zero(self):
        from motor.__main__ import main

        with tempfile.TemporaryDirectory() as d:
            code = main(["run", "--root", d, "--once"])
            self.assertEqual(code, 0)

    def test_main_loads_explicit_config_file(self):
        from motor.__main__ import main

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            toml = root / "defaults.toml"
            toml.write_text("[motor]\nadmision_factor = 1.5\n", encoding="utf-8")
            (root / "data" / "inbox").mkdir(parents=True, exist_ok=True)
            code = main(["run", "--root", d, "--config", str(toml), "--once"])
            self.assertEqual(code, 0)

    def test_without_once_the_worker_runs_the_forever_loop(self):
        from motor.__main__ import main

        with tempfile.TemporaryDirectory() as d:
            with mock.patch("motor.__main__.Worker") as worker_cls:
                code = main(["run", "--root", d])
            self.assertEqual(code, 0)
            worker_cls.return_value.run_forever.assert_called_once_with()
            worker_cls.return_value.run_once.assert_not_called()
