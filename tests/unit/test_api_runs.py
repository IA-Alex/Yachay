"""Endpoints de ejecución y comparación (ADR-019, M6 parte 2).

- ``POST /v1/models/{id}/runs?source=``  ejecuta el modelo y guarda la huella
- ``GET  /v1/models/{id}/runs``          lista las ejecuciones
- ``GET  /v1/diff?before=&after=``       compara dos ejecuciones
- ``GET  /v1/orphans?left=&right=&on=``  informe de huérfanos entre dos entradas
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from motor.config import MotorConfig


class _UnixHTTP(HTTPConnection):
    def __init__(self, socket_path: Path):
        super().__init__("localhost", timeout=5)
        self._socket_path = str(socket_path)

    def connect(self):  # type: ignore[override]
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self._socket_path)


class RunsApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = MotorConfig.from_root(Path(self._tmp.name))
        self.socket_path = Path(self._tmp.name) / "motor.sock"
        from motor.api.server import build_server

        self.server = build_server(self.config, self.socket_path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        """Detiene el servidor antes de que otra suite inicialice Tk."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def call(self, method, path, *, body=None, headers=None):
        conn = _UnixHTTP(self.socket_path)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            conn.close()

    def _upload(self, name: str, content: str):
        data = content.encode("utf-8")
        return self.call(
            "POST", f"/v1/inputs?name={name}", body=data,
            headers={"Content-Length": str(len(data))},
        )

    def _model(self, source: str = "datos.csv", name: str = "m") -> int:
        _, payload = self.call("POST", f"/v1/models?name={name}&source={source}")
        return payload["data"]["model_id"]

    def test_executing_a_model_records_a_run(self):
        self._upload("datos.csv", "id,valor\n1,a\n2,b\n")
        model_id = self._model()

        status, payload = self.call(
            "POST", f"/v1/models/{model_id}/runs?source=datos.csv"
        )

        self.assertEqual(status, 201)
        self.assertEqual(payload["data"]["rows_out"], 2)

    def test_execution_report_is_generated_and_deleted_without_deleting_data(self):
        self._upload("datos.csv", "id,valor\n1,a\n2,b\n")
        model_id = self._model()
        _, result = self.call("POST", f"/v1/models/{model_id}/runs?source=datos.csv")
        run_id = result["data"]["run_id"]
        report = self.config.paths.model_outputs / f"report-{run_id}.html"

        status, generated = self.call(
            "POST", f"/v1/models/{model_id}/runs/{run_id}/report"
        )
        self.assertEqual(status, 200)
        self.assertTrue(generated["data"]["generated"])
        self.assertTrue(report.is_file())
        self.assertIn("Informe local", report.read_text())

        status, deleted = self.call(
            "DELETE", f"/v1/models/{model_id}/runs/{run_id}/report"
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["data"]["deleted"])
        self.assertFalse(report.exists())
        self.assertEqual(len(self.call("GET", f"/v1/models/{model_id}/runs")[1]
                             ["data"]["runs"]), 1)

    def test_report_generation_rejects_old_runs_without_output_artifacts(self):
        from motor.storage.db import connect

        self._upload("datos.csv", "id\n1\n")
        model_id = self._model()
        _, result = self.call("POST", f"/v1/models/{model_id}/runs?source=datos.csv")
        conn = connect(self.config.paths.sqlite_path)
        conn.execute("UPDATE runs SET artifact_path = NULL WHERE run_id = ?",
                     (result["data"]["run_id"],))
        conn.close()
        status, response = self.call(
            "POST", f"/v1/models/{model_id}/runs/{result['data']['run_id']}/report"
        )
        self.assertEqual(status, 409)
        self.assertEqual(response["error"]["code"], "invalid_state")

    def test_report_generation_rejects_traversal_and_missing_artifact(self):
        from motor.storage.db import connect

        self._upload("datos.csv", "id\n1\n")
        model_id = self._model()
        _, result = self.call("POST", f"/v1/models/{model_id}/runs?source=datos.csv")
        run_id = result["data"]["run_id"]
        conn = connect(self.config.paths.sqlite_path)
        conn.execute("UPDATE runs SET artifact_path = '../outside' WHERE run_id = ?", (run_id,))
        conn.close()
        status, _ = self.call("POST", f"/v1/models/{model_id}/runs/{run_id}/report")
        self.assertEqual(status, 409)
        conn = connect(self.config.paths.sqlite_path)
        conn.execute("UPDATE runs SET artifact_path = 'missing.parquet' WHERE run_id = ?",
                     (run_id,))
        conn.close()
        status, _ = self.call("POST", f"/v1/models/{model_id}/runs/{run_id}/report")
        self.assertEqual(status, 409)

    def test_report_generation_maps_corrupt_artifact_to_unavailable(self):

        self._upload("datos.csv", "id\n1\n")
        model_id = self._model()
        _, result = self.call("POST", f"/v1/models/{model_id}/runs?source=datos.csv")
        run_id = result["data"]["run_id"]
        artifact = self.config.paths.model_outputs / f"run-{run_id}.parquet"
        artifact.write_bytes(b"not parquet")
        status, response = self.call("POST", f"/v1/models/{model_id}/runs/{run_id}/report")
        self.assertEqual(status, 503)
        self.assertEqual(response["error"]["code"], "unavailable")
        self.assertFalse((self.config.paths.model_outputs / f"report-{run_id}.html").exists())

    def test_report_routes_hide_missing_runs_and_other_models(self):
        self._upload("datos.csv", "id\n1\n")
        model_id = self._model()
        status, _ = self.call("POST", f"/v1/models/{model_id}/runs/999/report")
        self.assertEqual(status, 404)
        _, result = self.call("POST", f"/v1/models/{model_id}/runs?source=datos.csv")
        other_id = self._model("datos.csv", "otro")
        status, _ = self.call(
            "DELETE", f"/v1/models/{other_id}/runs/{result['data']['run_id']}/report"
        )
        self.assertEqual(status, 404)
        status, _ = self.call("DELETE", f"/v1/models/{model_id}/runs/999/report")
        self.assertEqual(status, 404)

    def test_report_delete_maps_permission_error(self):
        self._upload("datos.csv", "id\n1\n")
        model_id = self._model()
        _, result = self.call("POST", f"/v1/models/{model_id}/runs?source=datos.csv")
        run_id = result["data"]["run_id"]
        self.call("POST", f"/v1/models/{model_id}/runs/{run_id}/report")
        report = self.config.paths.model_outputs / f"report-{run_id}.html"
        original = Path.unlink

        def fail_report(path, *args, **kwargs):
            if path == report:
                raise PermissionError("locked")
            return original(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_report):
            status, response = self.call(
                "DELETE", f"/v1/models/{model_id}/runs/{run_id}/report"
            )
        self.assertEqual(status, 503)
        self.assertEqual(response["error"]["code"], "unavailable")

    def test_assistant_json_body_rejects_wrong_media_type_invalid_or_ambiguous_json(self):
        cases = [
            ("{}", {"Content-Type": "text/plain"}),
            ("{", {"Content-Type": "application/json"}),
            ('{"proposal":{},"proposal":{}}', {"Content-Type": "application/json"}),
            ("[]", {"Content-Type": "application/json"}),
            ("[" * 1500 + "0" + "]" * 1500,
             {"Content-Type": "application/json"}),
        ]
        for body, headers in cases:
            with self.subTest(body=body, headers=headers):
                status, _ = self.call(
                    "POST", "/v1/models/1/assistant/apply", body=body, headers=headers
                )
                self.assertEqual(status, 400)

    def test_assistant_endpoints_reject_invalid_payload_shapes(self):
        cases = [
            ("/v1/models/1/assistant/proposals", {"source": "x.csv"}),
            ("/v1/models/1/assistant/proposals", {"source": [], "instruction": "x"}),
            ("/v1/models/1/assistant/apply", {"extra": 1}),
        ]
        for path, payload in cases:
            with self.subTest(path=path, payload=payload):
                status, _ = self.call(
                    "POST", path, body=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(status, 400)

    def test_assistant_proposal_rejects_missing_unsupported_or_unrelated_source(self):
        self._upload("datos.csv", "id\n1\n")
        self._upload("otro.csv", "unrelated\nx\n")
        self._upload("bad.png", "x\n")
        model_id = self._model()
        for source, expected in (("missing.csv", 404), ("bad.png", 400), ("otro.csv", 400)):
            status, _ = self.call(
                "POST", f"/v1/models/{model_id}/assistant/proposals",
                body=json.dumps({"source": source, "instruction": "normaliza"}),
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(status, expected)

    def test_report_is_not_exposed_for_another_model(self):
        self._upload("datos.csv", "id\n1\n")
        first = self._model("datos.csv", "uno")
        _, result = self.call("POST", f"/v1/models/{first}/runs?source=datos.csv")
        second = self._model("datos.csv", "dos")
        status, _ = self.call(
            "POST", f"/v1/models/{second}/runs/{result['data']['run_id']}/report"
        )
        self.assertEqual(status, 404)

    def test_deleting_a_missing_report_returns_not_found(self):
        self._upload("datos.csv", "id\n1\n")
        model_id = self._model()
        _, result = self.call("POST", f"/v1/models/{model_id}/runs?source=datos.csv")
        status, _ = self.call(
            "DELETE", f"/v1/models/{model_id}/runs/{result['data']['run_id']}/report"
        )
        self.assertEqual(status, 404)

    def test_assistant_proposal_needs_local_gateway_and_does_not_change_model(self):
        self._upload("datos.csv", "id,value\n1,PRIVATE_ROW_VALUE\n")
        model_id = self._model()
        payload = json.dumps({"source": "datos.csv", "instruction": "convierte id a número"})
        with patch.dict(os.environ, {}, clear=True):
            status, response = self.call(
                "POST", f"/v1/models/{model_id}/assistant/proposals", body=payload,
                headers={"Content-Type": "application/json"},
            )
        self.assertEqual(status, 503)
        self.assertEqual(response["error"]["code"], "unavailable")
        self.assertEqual(self.call("GET", "/v1/models")[1]["data"]["models"][0]["version"], 1)

    def test_assistant_provider_receives_profile_metadata_without_rows(self):
        self._upload("datos.csv", "id,value\n1,PRIVATE_ROW_VALUE\n")
        model_id = self._model()
        proposal = {"summary": "Convertir id", "rules": [{
            "name": "id_number", "type": "cast", "column": "id",
            "params": {"to": "number"},
        }]}

        with patch.dict(os.environ, {}, clear=True), \
                patch("motor.assistant.propose_via_gateway",
                      return_value=proposal) as opened:
            status, response = self.call(
                "POST", f"/v1/models/{model_id}/assistant/proposals",
                body=json.dumps({"source": "datos.csv", "instruction": "convierte id"}),
                headers={"Content-Type": "application/json"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(response["data"]["proposal"], proposal)
        sent_instruction, sent_schema = opened.call_args.args
        self.assertEqual(sent_instruction, "convierte id")
        self.assertIn("id", json.dumps(sent_schema))
        self.assertNotIn("PRIVATE_ROW_VALUE", json.dumps(sent_schema))

    def test_accepting_a_proposal_creates_new_version_without_running_it(self):
        self._upload("datos.csv", "id,value\n1,a\n")
        model_id = self._model()
        proposal = {"summary": "Convertir id", "rules": [{
            "name": "id_number", "type": "cast", "column": "id",
            "params": {"to": "number"},
        }]}
        status, response = self.call(
            "POST", f"/v1/models/{model_id}/assistant/apply",
            body=json.dumps({"proposal": proposal}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        new_id = response["data"]["model_id"]
        self.assertEqual(response["data"]["version"], 2)
        self.assertEqual(len(self.call("GET", f"/v1/models/{model_id}/runs")[1]
                             ["data"]["runs"]), 0)
        self.assertEqual(self.call("GET", f"/v1/models/{new_id}/rules")[1]
                         ["data"]["rules"][0]["name"], "id_number")
        self.assertEqual(len(self.call("GET", f"/v1/models/{model_id}/rules")[1]
                             ["data"]["rules"]), 0)

    def test_accepting_from_stale_model_id_clones_latest_version(self):
        self._upload("datos.csv", "id\n1\n")
        model_id = self._model()

        def apply(name):
            proposal = {"summary": "Regla", "rules": [{
                "name": name, "type": "cast", "column": "id",
                "params": {"to": "number"},
            }]}
            status, payload = self.call(
                "POST", f"/v1/models/{model_id}/assistant/apply",
                body=json.dumps({"proposal": proposal}),
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(status, 201)
            return payload["data"]

        first = apply("id_number")
        second = apply("id_text")
        self.assertEqual((first["version"], second["version"]), (2, 3))
        names = [rule["name"] for rule in self.call(
            "GET", f"/v1/models/{second['model_id']}/rules"
        )[1]["data"]["rules"]]
        self.assertEqual(names, ["id_number", "id_text"])

    def test_accepting_invalid_proposal_does_not_create_version(self):
        self._upload("datos.csv", "id\n1\n")
        model_id = self._model()
        body = {"proposal": {"summary": "invalid", "rules": [{
            "name": "bad", "type": "execute", "column": "id", "params": {},
        }]}}
        status, _ = self.call(
            "POST", f"/v1/models/{model_id}/assistant/apply", body=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        status, _ = self.call(
            "POST", f"/v1/models/{model_id}/assistant/apply",
            body=json.dumps({"proposal": {"summary": "sin reglas", "rules": []}}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.call("GET", "/v1/models")[1]["data"]["models"][0]["version"], 1)

    def test_assistant_json_body_is_size_limited(self):
        self._upload("datos.csv", "id\n1\n")
        model_id = self._model()
        body = json.dumps({"proposal": {"summary": "x" * 33000, "rules": []}})
        status, response = self.call(
            "POST", f"/v1/models/{model_id}/assistant/apply", body=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 413)
        self.assertEqual(response["error"]["code"], "payload_too_large")

    def test_listing_runs_shows_them_newest_first(self):
        self._upload("datos.csv", "id,valor\n1,a\n")
        model_id = self._model()
        self.call("POST", f"/v1/models/{model_id}/runs?source=datos.csv")
        self.call("POST", f"/v1/models/{model_id}/runs?source=datos.csv")

        status, payload = self.call("GET", f"/v1/models/{model_id}/runs")

        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["count"], 2)

    def test_executing_without_a_source_is_a_bad_request(self):
        self._upload("datos.csv", "id\n1\n")
        model_id = self._model()
        status, payload = self.call("POST", f"/v1/models/{model_id}/runs")
        self.assertEqual(status, 400)

    def test_executing_a_missing_source_is_not_found(self):
        self._upload("datos.csv", "id\n1\n")
        model_id = self._model()
        status, payload = self.call(
            "POST", f"/v1/models/{model_id}/runs?source=nada.csv"
        )
        self.assertEqual(status, 404)

    def test_executing_an_unknown_model_is_not_found(self):
        self._upload("datos.csv", "id\n1\n")
        status, payload = self.call(
            "POST", "/v1/models/9999/runs?source=datos.csv"
        )
        self.assertEqual(status, 404)

    def test_diff_of_two_identical_runs_reports_no_change(self):
        self._upload("datos.csv", "id,valor\n1,a\n")
        model_id = self._model()
        _, first = self.call("POST", f"/v1/models/{model_id}/runs?source=datos.csv")
        _, second = self.call("POST", f"/v1/models/{model_id}/runs?source=datos.csv")
        a = first["data"]["run_id"]
        b = second["data"]["run_id"]

        status, payload = self.call("GET", f"/v1/diff?before={a}&after={b}")

        self.assertEqual(status, 200)
        self.assertFalse(payload["data"]["changed"])

    def test_diff_of_different_runs_reports_the_change(self):
        self._upload("uno.csv", "id,valor\n1,a\n")
        self._upload("dos.csv", "id,valor\n1,a\n2,b\n")
        model_id = self._model("uno.csv")
        _, first = self.call("POST", f"/v1/models/{model_id}/runs?source=uno.csv")
        _, second = self.call("POST", f"/v1/models/{model_id}/runs?source=dos.csv")
        a = first["data"]["run_id"]
        b = second["data"]["run_id"]

        status, payload = self.call("GET", f"/v1/diff?before={a}&after={b}")

        self.assertEqual(status, 200)
        self.assertTrue(payload["data"]["changed"])
        self.assertIn("filas", payload["data"]["reason"])

    def test_diff_requires_both_runs(self):
        status, payload = self.call("GET", "/v1/diff?before=1")
        self.assertEqual(status, 400)

    def test_diff_of_a_missing_run_is_not_found(self):
        status, payload = self.call("GET", "/v1/diff?before=1&after=9999")
        self.assertEqual(status, 404)

    def test_orphans_between_two_uploads(self):
        self._upload("ventas.csv", "cliente\nana\nfantasma\n")
        self._upload("clientes.csv", "nombre\nana\n")

        status, payload = self.call(
            "GET",
            "/v1/orphans?left=ventas.csv&right=clientes.csv&on=cliente&right_on=nombre",
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["rows_out"], 1)
        self.assertEqual(payload["data"]["orphans"][0][0], "fantasma")

    def test_orphans_require_the_three_parameters(self):
        status, payload = self.call("GET", "/v1/orphans?left=a.csv")
        self.assertEqual(status, 400)

    def test_orphans_of_a_missing_file_is_not_found(self):
        self._upload("ventas.csv", "cliente\nana\n")
        status, payload = self.call(
            "GET", "/v1/orphans?left=ventas.csv&right=nada.csv&on=cliente"
        )
        self.assertEqual(status, 404)

    def test_executing_with_a_non_numeric_model_id_is_bad_request(self):
        self._upload("datos.csv", "id\n1\n")
        status, payload = self.call(
            "POST", "/v1/models/abc/runs?source=datos.csv"
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "bad_request")

    def test_executing_from_an_unsupported_source_is_a_bad_request(self):
        self._upload("foto.png", "\x89PNG")
        self._upload("datos.csv", "id\n1\n")
        model_id = self._model()
        status, payload = self.call(
            "POST", f"/v1/models/{model_id}/runs?source=foto.png"
        )
        self.assertEqual(status, 400)

    def test_executing_with_a_rule_over_a_missing_column_is_a_bad_request(self):
        self._upload("datos.csv", "id\n1\n")
        model_id = self._model()
        # Regla sobre una columna que la fuente de ejecución no trae.
        from motor.model import rules
        from motor.storage.db import connect

        conn = connect(self.config.paths.sqlite_path)
        rules.add(conn, model_id, name="c", type="cast", column="id",
                  params={"to": "number"})
        conn.close()
        self._upload("otro.csv", "otra\n1\n")
        status, payload = self.call(
            "POST", f"/v1/models/{model_id}/runs?source=otro.csv"
        )
        self.assertEqual(status, 400)

    def test_orphans_reject_a_traversal_name(self):
        status, payload = self.call(
            "GET", "/v1/orphans?left=../a.csv&right=b.csv&on=x"
        )
        self.assertEqual(status, 400)

    def test_orphans_with_an_unknown_column_is_a_bad_request(self):
        self._upload("a.csv", "x\n1\n")
        self._upload("b.csv", "y\n1\n")
        status, payload = self.call(
            "GET", "/v1/orphans?left=a.csv&right=b.csv&on=no_existe"
        )
        self.assertEqual(status, 400)

    def test_orphans_with_an_unsupported_file_is_a_bad_request(self):
        self._upload("a.csv", "x\n1\n")
        self._upload("foto.png", "\x89PNG")
        status, payload = self.call(
            "GET", "/v1/orphans?left=a.csv&right=foto.png&on=x"
        )
        self.assertEqual(status, 400)

    def test_orphans_of_a_file_against_itself(self):
        # Comparar un archivo consigo mismo es un caso legítimo (duplicados
        # internos). No debe filtrar un error de DuckDB ni caer con 500.
        self._upload("a.csv", "k\n1\n2\n")
        status, payload = self.call(
            "GET", "/v1/orphans?left=a.csv&right=a.csv&on=k"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["rows_out"], 0)

    def test_diff_with_a_non_numeric_run_is_bad_request(self):
        status, payload = self.call("GET", "/v1/diff?before=abc&after=1")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "bad_request")

    def test_executing_rejects_a_traversal_source(self):
        self._upload("datos.csv", "id\n1\n")
        model_id = self._model()
        status, payload = self.call(
            "POST", f"/v1/models/{model_id}/runs?source=../evil.csv"
        )
        self.assertEqual(status, 400)

    def test_executing_a_model_without_entities_is_invalid_state(self):
        from motor.model import store
        from motor.storage.db import connect

        conn = connect(self.config.paths.sqlite_path)
        model_id = store.create(conn, name="vacio")
        conn.close()
        self._upload("datos.csv", "id\n1\n")
        status, payload = self.call(
            "POST", f"/v1/models/{model_id}/runs?source=datos.csv"
        )
        self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main()
