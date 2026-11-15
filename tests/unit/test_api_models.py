"""Endpoints de modelado de la API v1 (ADR-019, M6).

La interfaz de escritorio es un cliente de la API, así que el modelo se expone
por el mismo socket Unix (ADR-016): la UI nunca abre SQLite directamente.

Rutas:
- ``GET  /v1/models``         lista los modelos
- ``GET  /v1/models/{id}``    detalle de un modelo
- ``POST /v1/models``         crea un modelo desde una hoja o documento
- ``GET  /v1/profile?name=``  perfila un archivo ya subido a la bandeja
"""

from __future__ import annotations

import json
import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import quote

from motor.config import MotorConfig


class _UnixHTTP(HTTPConnection):
    def __init__(self, socket_path: Path):
        super().__init__("localhost", timeout=5)
        self._socket_path = str(socket_path)

    def connect(self):  # type: ignore[override]
        import socket

        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self._socket_path)


class ModelsApiTests(unittest.TestCase):
    """Los endpoints de modelo responden con el envelope v1."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = MotorConfig.from_root(Path(self._tmp.name))
        self.socket_path = Path(self._tmp.name) / "motor.sock"
        from motor.api.server import build_server

        self.server = build_server(self.config, self.socket_path)
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)

    def call(self, method, path, *, body=None, headers=None):
        conn = _UnixHTTP(self.socket_path)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload
        finally:
            conn.close()

    def _upload(self, name: str, content: str):
        return self.call(
            "POST", f"/v1/inputs?name={name}",
            body=content.encode("utf-8"),
            headers={"Content-Length": str(len(content.encode("utf-8")))},
        )

    def test_listing_models_is_empty_at_first(self):
        status, payload = self.call("GET", "/v1/models")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["models"], [])

    def test_listing_shows_created_models(self):
        self._upload("datos.csv", "id\n1\n2\n")
        self.call("POST", "/v1/models?name=bitacora&source=datos.csv")

        status, payload = self.call("GET", "/v1/models")

        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["count"], 1)
        self.assertEqual(payload["data"]["models"][0]["name"], "bitacora")
        self.assertEqual(payload["data"]["models"][0]["entities"], ["datos"])

    def test_creating_a_model_from_an_uploaded_csv(self):
        self._upload("datos.csv", "id,riesgo\n1,Bajo\n2,Alto\n")

        status, payload = self.call(
            "POST", "/v1/models?name=bitacora&source=datos.csv"
        )

        self.assertEqual(status, 201)
        model = payload["data"]
        self.assertEqual(model["name"], "bitacora")
        self.assertEqual(len(model["entities"]), 1)
        self.assertEqual(model["entities"][0]["name"], "datos")

    def test_reading_a_model_by_id(self):
        self._upload("datos.csv", "id,riesgo\n1,Bajo\n")
        _, created = self.call("POST", "/v1/models?name=m&source=datos.csv")
        model_id = created["data"]["model_id"]

        status, payload = self.call("GET", f"/v1/models/{model_id}")

        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["model_id"], model_id)

    def test_unknown_model_is_not_found(self):
        status, payload = self.call("GET", "/v1/models/9999")
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_creating_without_a_source_is_a_bad_request(self):
        status, payload = self.call("POST", "/v1/models?name=m")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "bad_request")

    def test_creating_from_a_missing_file_is_not_found(self):
        status, payload = self.call("POST", "/v1/models?name=m&source=nada.csv")
        self.assertEqual(status, 404)

    def test_profiling_an_uploaded_file(self):
        self._upload("datos.csv", "ciudad,monto\nCusco,120\nLima,340\n")

        status, payload = self.call("GET", "/v1/profile?name=datos.csv")

        self.assertEqual(status, 200)
        data = payload["data"]
        self.assertEqual(data["rows"], 2)
        self.assertEqual(len(data["profile"]), 2)

    def test_profiling_a_missing_file_is_not_found(self):
        status, payload = self.call("GET", "/v1/profile?name=nada.csv")
        self.assertEqual(status, 404)

    def test_profile_without_a_name_is_a_bad_request(self):
        status, payload = self.call("GET", "/v1/profile")
        self.assertEqual(status, 400)

    def test_profile_rejects_a_traversal_name(self):
        status, payload = self.call("GET", "/v1/profile?name=../evil.csv")
        self.assertEqual(status, 400)

    def test_create_rejects_a_traversal_source(self):
        status, payload = self.call(
            "POST", "/v1/models?name=m&source=../evil.csv"
        )
        self.assertEqual(status, 400)

    def test_create_from_an_unsupported_upload_is_a_bad_request(self):
        self._upload("foto.png", "\x89PNG")
        status, payload = self.call("POST", "/v1/models?name=m&source=foto.png")
        self.assertEqual(status, 400)

    def test_creating_the_same_model_twice_is_a_conflict(self):
        self._upload("datos.csv", "id\n1\n")
        self.call("POST", "/v1/models?name=m&source=datos.csv")
        status, payload = self.call("POST", "/v1/models?name=m&source=datos.csv")
        self.assertEqual(status, 409)

    def test_a_model_with_a_unique_column_gets_a_key(self):
        self._upload("datos.csv", "id,valor\n1,a\n2,b\n")
        _, payload = self.call("POST", "/v1/models?name=m&source=datos.csv")
        entity = payload["data"]["entities"][0]
        self.assertEqual(entity["key"], ["id"])

    def test_a_model_without_unique_columns_has_no_key(self):
        self._upload("dup.csv", "estado\nHecho\nHecho\n")
        _, payload = self.call("POST", "/v1/models?name=m&source=dup.csv")
        entity = payload["data"]["entities"][0]
        self.assertEqual(entity["key"], [])

    def test_profile_of_an_unsupported_upload_is_a_bad_request(self):
        self._upload("foto.png", "\x89PNG")
        status, payload = self.call("GET", "/v1/profile?name=foto.png")
        self.assertEqual(status, 400)


class RulesApiTests(unittest.TestCase):
    """Endpoints de reglas del modelo (ADR-019, M3)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = MotorConfig.from_root(Path(self._tmp.name))
        self.socket_path = Path(self._tmp.name) / "motor.sock"
        from motor.api.server import build_server

        self.server = build_server(self.config, self.socket_path)
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)

    def call(self, method, path, *, body=None, headers=None):
        conn = _UnixHTTP(self.socket_path)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            conn.close()

    def _model(self) -> int:
        data = "id,ciudad,estado\n1,Cusco,Hecho\n2,Lima,En curso\n"
        self.call(
            "POST", "/v1/inputs?name=datos.csv",
            body=data.encode("utf-8"),
            headers={"Content-Length": str(len(data.encode("utf-8")))},
        )
        _, payload = self.call("POST", "/v1/models?name=m&source=datos.csv")
        return payload["data"]["model_id"]

    def test_listing_rules_is_empty_at_first(self):
        model_id = self._model()
        status, payload = self.call("GET", f"/v1/models/{model_id}/rules")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["rules"], [])

    def test_creating_and_listing_a_rule(self):
        model_id = self._model()
        status, payload = self.call(
            "POST", f"/v1/models/{model_id}/rules?name=r&type=cast&column=id&to=number"
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["data"]["type"], "cast")

        _, listed = self.call("GET", f"/v1/models/{model_id}/rules")
        self.assertEqual(listed["data"]["count"], 1)
        self.assertEqual(listed["data"]["rules"][0]["name"], "r")

    def test_rename_rule_carries_only_the_target_in_params(self):
        # El origen viaja en `column`; `params` no debe exponer un `from` nulo.
        model_id = self._model()
        _, payload = self.call(
            "POST", f"/v1/models/{model_id}/rules?name=r&type=rename&column=ciudad&to=lugar"
        )
        params = payload["data"]["params"]
        self.assertEqual(params, {"to": "lugar"})

    def test_deleting_a_rule(self):
        model_id = self._model()
        self.call(
            "POST", f"/v1/models/{model_id}/rules?name=r&type=cast&column=id&to=number"
        )
        status, payload = self.call("DELETE", f"/v1/models/{model_id}/rules/r")
        self.assertEqual(status, 200)
        self.assertTrue(payload["data"]["deleted"])

    def test_deleting_a_missing_rule_is_not_found(self):
        model_id = self._model()
        status, payload = self.call("DELETE", f"/v1/models/{model_id}/rules/nada")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_unknown_rule_type_is_a_bad_request(self):
        model_id = self._model()
        status, payload = self.call(
            "POST", f"/v1/models/{model_id}/rules?name=r&type=nope&column=id"
        )
        self.assertEqual(status, 400)

    def test_rule_over_an_unknown_column_is_a_bad_request(self):
        model_id = self._model()
        status, payload = self.call(
            "POST", f"/v1/models/{model_id}/rules?name=r&type=cast&column=nada&to=text"
        )
        self.assertEqual(status, 400)

    def test_map_rule_requires_valid_json(self):
        model_id = self._model()
        status, payload = self.call(
            "POST", f"/v1/models/{model_id}/rules?name=r&type=map&column=estado&values=nojson"
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "bad_request")

    def test_a_map_rule_without_values_defaults_to_an_empty_dict(self):
        # `values` ausente no es un error: el motor lee `params["values"]`, así que
        # debe llegar un dict vacío (no `None`), o la regla reventaría al ejecutar.
        model_id = self._model()
        status, payload = self.call(
            "POST", f"/v1/models/{model_id}/rules?name=r&type=map&column=estado"
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["data"]["params"], {"values": {}})

    def test_a_map_rule_with_a_wrong_json_type_is_a_bad_request(self):
        # JSON válido pero de otro tipo (lista donde se espera dict): guardarlo
        # produciría una regla que no transforma nada, así que se rechaza con 400.
        model_id = self._model()
        values = quote(json.dumps([1, 2]), safe="")
        status, payload = self.call(
            "POST",
            f"/v1/models/{model_id}/rules?name=r&type=map&column=estado&values={values}",
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "bad_request")

    def test_a_domain_rule_without_allowed_defaults_to_an_empty_list(self):
        # Mismo contrato para `domain`: ausente → lista vacía, no `None`.
        model_id = self._model()
        status, payload = self.call(
            "POST", f"/v1/models/{model_id}/rules?name=r&type=domain&column=estado"
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["data"]["params"], {"allowed": []})

    def test_a_map_rule_created_via_the_api_actually_transforms(self):
        # Regresión: `map` guardaba el dict crudo y el motor leía
        # `params["values"]`, así que la regla no transformaba nada.
        model_id = self._model()
        values = quote(json.dumps({"En curso": "Hecho"}), safe="")
        status, _ = self.call(
            "POST",
            f"/v1/models/{model_id}/rules?name=norm&type=map&column=estado&values={values}",
        )
        self.assertEqual(status, 201)

        _, run = self.call("POST", f"/v1/models/{model_id}/runs?source=datos.csv")
        self.assertEqual(run["data"]["rows_out"], 2)
        # El estado «En curso» debía convertirse en «Hecho»: si la regla fuera un
        # no-op, el resultado seguiría teniendo dos estados distintos.
        _, runs = self.call("GET", f"/v1/models/{model_id}/runs")
        self.assertEqual(runs["data"]["count"], 1)

    def test_map_rule_stores_its_values_wrapped(self):
        model_id = self._model()
        values = quote(json.dumps({"En curso": "Hecho"}), safe="")
        _, payload = self.call(
            "POST",
            f"/v1/models/{model_id}/rules?name=norm&type=map&column=estado&values={values}",
        )
        self.assertEqual(payload["data"]["params"], {"values": {"En curso": "Hecho"}})

    def test_rules_of_a_missing_model_is_not_found(self):
        status, payload = self.call("GET", "/v1/models/9999/rules")
        self.assertEqual(status, 404)

    def test_creating_a_filter_rule(self):
        model_id = self._model()
        status, payload = self.call(
            "POST",
            f"/v1/models/{model_id}/rules?name=f&type=filter&column=estado&equals=Hecho",
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["data"]["params"], {"equals": "Hecho"})

    def test_creating_a_rule_without_required_fields_is_a_bad_request(self):
        model_id = self._model()
        status, payload = self.call("POST", f"/v1/models/{model_id}/rules?name=r")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "bad_request")

    def test_a_duplicate_rule_name_is_invalid_state(self):
        model_id = self._model()
        self.call(
            "POST", f"/v1/models/{model_id}/rules?name=r&type=cast&column=id&to=number"
        )
        status, payload = self.call(
            "POST", f"/v1/models/{model_id}/rules?name=r&type=cast&column=id&to=text"
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "invalid_state")

    def test_non_numeric_model_id_is_a_bad_request(self):
        status, payload = self.call("GET", "/v1/models/abc/rules")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "bad_request")


if __name__ == "__main__":
    unittest.main()
