"""Error and dispatch edges for the local CLI coverage gate."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from motor.__main__ import main


class CliEdgeTests(unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(list(args))
        return code, output.getvalue()

    def test_deepinfra_dispatch(self):
        with patch("motor.__main__.serve_deepinfra") as serve:
            self.assertEqual(self.run_cli("deepinfra"), (0, ""))
        serve.assert_called_once_with(None)

    def test_align_rejects_bad_maps_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "a.csv"
            b = root / "b.csv"
            a.write_text("a\n1\n", encoding="utf-8")
            b.write_text("b\n2\n", encoding="utf-8")
            maps = root / "maps.json"
            maps.write_text("{}", encoding="utf-8")
            code, out = self.run_cli("model", "align", str(a), str(b), "--column-maps", str(maps))
            self.assertEqual(code, 1)
            self.assertIn("column-maps", out)
            maps.write_text(json.dumps([{}, []]), encoding="utf-8")
            code, out = self.run_cli("model", "align", str(a), str(b), "--column-maps", str(maps))
            self.assertEqual(code, 1)
            self.assertIn("objeto JSON", out)
            code, out = self.run_cli(
                "model", "align", str(a), str(b), "--output", str(root / "bad.txt")
            )
            self.assertEqual(code, 1)
            self.assertIn("formato no soportado", out)

    def test_validate_and_dedupe_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "rows.csv"
            source.write_text("id\n1\n1\n", encoding="utf-8")
            rules = root / "rules.json"
            rules.write_text("{}", encoding="utf-8")
            code, out = self.run_cli("model", "validate", str(source), str(rules))
            self.assertEqual(code, 1)
            self.assertIn("lista de reglas", out)
            rules.write_text("[]", encoding="utf-8")
            code, out = self.run_cli(
                "model",
                "validate",
                str(source),
                str(rules),
                "--errors-output",
                str(root / "bad.txt"),
            )
            self.assertEqual(code, 1)
            self.assertIn("formato no soportado", out)
            code, out = self.run_cli("model", "dedupe", str(source), "--key", "missing")
            self.assertEqual(code, 1)
            self.assertIn("claves desconocidas", out)
            code, out = self.run_cli(
                "model", "dedupe", str(source), "--key", "id", "--output", str(root / "bad.txt")
            )
            self.assertEqual(code, 1)
            self.assertIn("formato no soportado", out)

    def test_transform_rejects_unknown_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "rows.csv"
            source.write_text("id\n1\n", encoding="utf-8")
            spec = root / "spec.json"
            spec.write_text(json.dumps({"operation": "unknown"}), encoding="utf-8")
            code, out = self.run_cli(
                "model", "transform", str(source), str(spec), "--output", str(root / "out.csv")
            )
            self.assertEqual(code, 1)
            self.assertIn("operation debe ser", out)
            spec.write_text("[]", encoding="utf-8")
            code, out = self.run_cli(
                "model",
                "transform",
                str(source),
                str(spec),
                "--output",
                str(root / "out.csv"),
            )
            self.assertEqual(code, 1)
            self.assertIn("objeto JSON", out)

    def test_successful_optional_cli_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / "a.csv", root / "b.csv"
            a.write_text("id\n1\n", encoding="utf-8")
            b.write_text("id\n2\n", encoding="utf-8")
            code, out = self.run_cli(
                "model", "align", str(a), str(b), "--output", str(root / "aligned.csv")
            )
            self.assertEqual(code, 0, out)
            self.assertTrue((root / "aligned.csv").is_file())
            rules = root / "rules.json"
            rules.write_text(json.dumps([{"type": "required", "column": "id"}]))
            code, out = self.run_cli(
                "model",
                "validate",
                str(a),
                str(rules),
                "--errors-output",
                str(root / "errors.csv"),
            )
            self.assertEqual(code, 0, out)
            self.assertTrue((root / "errors.csv").is_file())
            code, out = self.run_cli(
                "model",
                "dedupe",
                str(a),
                "--key",
                "id",
                "--output",
                str(root / "deduped.csv"),
            )
            self.assertEqual(code, 0, out)
            self.assertTrue((root / "deduped.csv").is_file())
            self.assertEqual(self.run_cli("model", "align", str(a), str(b))[0], 0)
            self.assertEqual(self.run_cli("model", "validate", str(a), str(rules))[0], 0)
            self.assertEqual(self.run_cli("model", "dedupe", str(a), "--key", "id")[0], 0)

    def test_exec_failure_edges_and_export_run_record_edges(self):
        from types import SimpleNamespace

        from motor.config import MotorConfig
        from motor.model import store
        from motor.storage.db import connect

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "rows.csv"
            source.write_text("id\n1\n", encoding="utf-8")
            self.run_cli("model", "new", "m", str(source), "--root", tmp)
            with patch("motor.__main__._file_sha256", side_effect=OSError("unreadable")):
                code, out = self.run_cli("model", "exec", "1", str(source), "--root", tmp)
            self.assertEqual(code, 1)
            self.assertIn("no se pudo leer", out)
            with patch("motor.model.run.record", side_effect=OSError("disk")):
                code, out = self.run_cli("model", "exec", "1", str(source), "--root", tmp)
            self.assertEqual(code, 1)
            self.assertIn("no se pudo guardar", out)

            config = MotorConfig.from_root(root)
            conn = connect(config.paths.sqlite_path)
            empty_id = store.create(conn, name="empty")
            conn.close()
            self.assertEqual(
                self.run_cli("model", "exec", str(empty_id), str(source), "--root", tmp)[0], 1
            )

            for artifact_path in (None, "../outside.parquet", "missing.parquet"):
                record = SimpleNamespace(artifact_path=artifact_path)
                with patch("motor.model.run.get", return_value=record):
                    code, out = self.run_cli(
                        "model", "export-run", "1", str(root / "out.csv"), "--root", tmp
                    )
                self.assertEqual(code, 1)
                self.assertIn("artefacto", out)

    def test_incremental_rejects_base_version_and_artifact_edges(self):
        from types import SimpleNamespace

        from motor.config import MotorConfig
        from motor.model import run, store
        from motor.storage.db import connect

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "rows.csv"
            source.write_text("id\n1\n", encoding="utf-8")
            self.run_cli("model", "new", "m", str(source), "--root", tmp)
            config = MotorConfig.from_root(root)
            conn = connect(config.paths.sqlite_path)
            empty_id = store.create(conn, name="empty")
            model = store.get(conn, 1)
            conn.close()
            cases = (
                (
                    SimpleNamespace(
                        model_id=2,
                        lineage={"model_version": model.version},
                        artifact_path="x.parquet",
                    ),
                    "otro modelo",
                ),
                (
                    SimpleNamespace(
                        model_id=1, lineage={"model_version": -1}, artifact_path="x.parquet"
                    ),
                    "otra versión",
                ),
                (
                    SimpleNamespace(
                        model_id=1, lineage={"model_version": model.version}, artifact_path=None
                    ),
                    "no tiene artefacto",
                ),
                (
                    SimpleNamespace(
                        model_id=1,
                        lineage={"model_version": model.version},
                        artifact_path="missing.parquet",
                    ),
                    "no se encuentra",
                ),
            )
            for previous, expected in cases:
                with patch("motor.model.run.get", return_value=previous):
                    code, out = self.run_cli(
                        "model", "incremental", "1", "7", str(source), "--key", "id", "--root", tmp
                    )
                self.assertEqual(code, 1)
                self.assertIn(expected, out)
            previous_path = config.paths.model_outputs / "exists.parquet"
            previous_path.parent.mkdir(parents=True, exist_ok=True)
            previous_path.write_bytes(b"x")
            empty_model = store.get(connect(config.paths.sqlite_path), empty_id)
            previous = SimpleNamespace(
                model_id=empty_id,
                lineage={"model_version": empty_model.version},
                artifact_path=previous_path.name,
            )
            with (
                patch("motor.model.store.get", return_value=empty_model),
                patch("motor.model.run.get", return_value=previous),
            ):
                code, out = self.run_cli(
                    "model",
                    "incremental",
                    str(empty_id),
                    "7",
                    str(source),
                    "--key",
                    "id",
                    "--root",
                    tmp,
                )
            self.assertEqual(code, 1)
            self.assertIn("no tiene entidades", out)

            conn = connect(config.paths.sqlite_path)
            try:
                current_model = store.get(conn, 1)
            finally:
                conn.close()
            previous = SimpleNamespace(
                model_id=1,
                lineage={"model_version": current_model.version},
                artifact_path=previous_path.name,
            )
            with (
                patch("motor.model.run.get", return_value=previous),
                patch("motor.model.run.read_artifact", side_effect=run.RunError("broken artifact")),
            ):
                code, out = self.run_cli(
                    "model", "incremental", "1", "7", str(source), "--key", "id", "--root", tmp
                )
            self.assertEqual(code, 1)
            self.assertIn("broken artifact", out)

    def test_export_run_reports_corrupt_parquet(self):
        from types import SimpleNamespace

        from motor.config import MotorConfig

        with tempfile.TemporaryDirectory() as tmp:
            config = MotorConfig.from_root(Path(tmp))
            artifact = config.paths.model_outputs / "corrupt.parquet"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"not parquet")
            with patch(
                "motor.model.run.get",
                return_value=SimpleNamespace(artifact_path=artifact.name),
            ):
                code, out = self.run_cli(
                    "model", "export-run", "1", str(Path(tmp) / "out.csv"), "--root", tmp
                )
            self.assertEqual(code, 1)
            self.assertIn("ok", out)

    def test_assistant_profile_validation_and_provider_cleanup_edges(self):
        from motor import assistant

        for schema in (
            [{"name": "x", "rows": True}],
            [{"name": "x", "inferred_type": "date"}],
            [{"name": "x", "numeric_min": "1"}],
        ):
            with self.assertRaises(assistant.AssistantError):
                assistant._validate_profile_schema(schema)
        with (
            patch.dict("os.environ", {"DEEPINFRA_API_KEY": "secret"}, clear=True),
            patch("motor.assistant._PinnedHTTPSConnection", side_effect=OSError("setup")),
            self.assertRaises(assistant.AssistantError),
        ):
            assistant.propose("orden", [{"name": "x"}])

    def test_missing_exec_incremental_and_export_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "rows.csv"
            source.write_text("id\n1\n", encoding="utf-8")
            self.assertEqual(self.run_cli("model", "exec", "999", str(source), "--root", tmp)[0], 1)
            self.assertEqual(
                self.run_cli(
                    "model", "incremental", "999", "999", str(source), "--key", "id", "--root", tmp
                )[0],
                1,
            )
            self.assertEqual(
                self.run_cli("model", "export-run", "999", str(root / "out.csv"), "--root", tmp)[0],
                1,
            )
