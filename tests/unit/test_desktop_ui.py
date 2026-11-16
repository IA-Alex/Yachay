"""Pruebas de humo de la interfaz de escritorio (Yachay Desktop).

Verifican que la aplicación se construye y que su lógica respeta el contrato del
motor, sin depender de un servidor X ni de un motor en ejecución:

- las siete vistas se construyen y navegan sin error;
- los botones de acción se habilitan según CANCELLABLE_STATES / RETRYABLE_STATES;
- el cliente habla el envelope real y ramifica por el código de error;
- el formateo de fechas, identificadores y tamaños es correcto.

Si no hay display disponible, las pruebas que requieren Tk se saltan
(``unittest.SkipTest``) en lugar de fallar: el gate debe ser ejecutable en un
entorno sin sesión gráfica.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import quote

# La interfaz no está instalada como paquete: vive en `desktop/`. El gate
# descubre desde la raíz del repo, así que añadimos esa carpeta al path.
_DESKTOP = Path(__file__).resolve().parents[2] / "desktop"
if str(_DESKTOP) not in sys.path:
    sys.path.insert(0, str(_DESKTOP))

from yachay_desktop import formatting  # noqa: E402
from yachay_desktop.app import YachayApp  # noqa: E402
from yachay_desktop.client import (  # noqa: E402
    ApiClient,
    ApiError,
    ApiUnreachable,
    Job,
    _dataset_from,
    _job_from,
    _UnixHTTPConnection,
)
from yachay_desktop.theme import (  # noqa: E402
    CANCELLABLE_STATES,
    JOB_STATE_LABELS,
    RETRYABLE_STATES,
)


def _tk_available() -> bool:
    """Indica si hay un servidor gráfico utilizable."""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except Exception:  # pragma: no cover - depende del entorno
        return False


requires_display = unittest.skipUnless(
    _tk_available(), "no hay servidor gráfico (DISPLAY) disponible"
)


class FormattingTests(unittest.TestCase):
    """Formateo de valores para la interfaz."""

    def test_format_epoch_renders_a_date(self):
        self.assertRegex(formatting.format_epoch(1794462058.0), r"^\d{4}-\d{2}-\d{2} ")

    def test_format_epoch_handles_missing_values(self):
        self.assertEqual(formatting.format_epoch(None), "—")
        self.assertEqual(formatting.format_epoch(0), "—")

    def test_short_id_abbreviates_long_values(self):
        self.assertEqual(formatting.short_id("abcdef1234567890", 8), "abcdef12…")
        self.assertEqual(formatting.short_id("short", 8), "short")
        self.assertEqual(formatting.short_id(None), "—")

    def test_human_bytes_scales_units(self):
        self.assertEqual(formatting.human_bytes(512), "512 B")
        self.assertEqual(formatting.human_bytes(1536), "1.5 KB")
        self.assertEqual(formatting.human_bytes(16 * 1024 * 1024), "16.0 MB")

    def test_desktop_entrypoint_and_package_are_declared(self):
        import tomllib

        root = Path(__file__).resolve().parents[2]
        with (root / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)
        self.assertEqual(
            project["project"]["scripts"]["yachay-desktop"],
            "yachay_desktop.app:main",
        )
        self.assertIn(
            "desktop/yachay_desktop",
            project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"],
        )


class ClientQueryEncodingTests(unittest.TestCase):
    """El valor de un parámetro no puede romper ni partir la query.

    Cubre la regresión de H-01/H-02: `upload("a&b.csv")` terminaba depositando
    `a` con un 201 que mentía, y un nombre con espacio o acento moría en
    `http.client` con `InvalidURL`, fuera de todo `except` de la vista.
    """

    def test_quote_target_encodes_what_http_client_would_reject(self):
        from yachay_desktop.client import _quote_target

        self.assertEqual(
            _quote_target("/v1/profile?name=mi%20informe.csv"),
            "/v1/profile?name=mi%20informe.csv",
        )
        self.assertEqual(
            _quote_target("/v1/profile?name=ventas año.csv"),
            "/v1/profile?name=ventas%20a%C3%B1o.csv",
        )

    def test_quote_target_preserves_prequoted_values_and_structure(self):
        from yachay_desktop.client import _quote_target

        # `%` no se vuelve a codificar y `&`/`=` separan, no pertenecen al valor.
        self.assertEqual(
            _quote_target("/v1/models/1/rules?name=a%26b&values=%7B%22a%22%3A%22b%22%7D"),
            "/v1/models/1/rules?name=a%26b&values=%7B%22a%22%3A%22b%22%7D",
        )
        self.assertEqual(_quote_target("/v1/health"), "/v1/health")

    def test_error_codes_come_from_the_provider(self):
        """#1: el conjunto no se copia; se deriva de `envelope.ErrorCode`."""
        from yachay_desktop.client import ERROR_CODES, ERROR_LABELS

        from motor.api.envelope import ErrorCode

        self.assertEqual(set(ERROR_CODES), set(ErrorCode.codes()))
        self.assertEqual(set(ERROR_LABELS), set(ErrorCode.codes()))
        for code in ErrorCode.codes():
            self.assertNotEqual(ERROR_CODES[code], code, f"{code} sin etiqueta")
        self.assertEqual(ApiError("teapot", "x").label, "teapot")
        self.assertFalse(ApiError("teapot", "x").is_known)

    def test_empty_body_is_an_error_not_success(self):
        """#2: el servidor nunca emite 204; un cuerpo vacío es una respuesta rota."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sock_path = Path(tmp.name) / "motor.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        server.listen(1)

        def serve():
            conn, _ = server.accept()
            with conn:
                conn.recv(65536)
                conn.sendall(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.addCleanup(server.close)
        client = ApiClient(sock_path)
        with self.assertRaises(ApiError) as ctx:
            client.health()
        self.assertEqual(ctx.exception.code, "internal_error")
        thread.join(2)

    def test_check_name_matches_the_provider_contract(self):
        from yachay_desktop.client import check_name

        for good in ("a.csv", "informe.final_2.csv", "A1_-x.ods", "a" * 200):
            check_name(good)
        for bad in ("a&b.csv", "datos#1.csv", "datos+uno.csv", "Sin título.csv",
                    "año.csv", "a" * 201, ".oculto.csv", "a/b.csv", ""):
            with self.subTest(name=bad):
                with self.assertRaises(ApiError) as ctx:
                    check_name(bad)
                self.assertEqual(ctx.exception.code, "bad_request")

    def test_a_query_space_never_reaches_http_client_raw(self):
        """`_request` codifica antes de enviar; `_quote_target` es el guardián.

        Se fija directamente sobre `_quote_target` el espacio de la URL, el valor
        que `http.client` rechaza con `InvalidURL` antes de salir: así el test no
        depende de que un socket acepte o rechace la petición.
        """
        from yachay_desktop.client import _quote_target

        self.assertEqual(_quote_target("/v1/inputs?name=a%20b.csv"),
                         "/v1/inputs?name=a%20b.csv")
        self.assertEqual(_quote_target("/v1/inputs?name=a b.csv"),
                         "/v1/inputs?name=a%20b.csv")

    def test_an_unencodable_header_is_reported_as_bad_request(self):
        """Si algo escapa a la codificación, el error es `ApiError`, no una traza."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sock_path = Path(tmp.name) / "motor.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        server.listen(1)
        self.addCleanup(server.close)
        client = ApiClient(sock_path)
        # `\n` es una cabecera ilegal: `http.client` la rechaza con ValueError.
        with self.assertRaises(ApiError) as ctx:
            client._request("GET", "/v1/health", headers={"X-Yachay-Filename": "a\nb"})
        self.assertEqual(ctx.exception.code, "bad_request")


class ClientModelTests(unittest.TestCase):
    """Conversión de filas del motor a modelos de la interfaz."""

    def test_job_from_row_maps_every_field(self):
        job = _job_from(
            {
                "job_id": "job-1",
                "state": "running",
                "attempt": 2,
                "created_at": 1.0,
                "started_at": 2.0,
                "cancel_requested": True,
                "deferred_reason": None,
                "run_id": "run-1",
            }
        )
        self.assertEqual(job.state, "running")
        self.assertEqual(job.attempt, 2)
        self.assertTrue(job.cancel_requested)
        self.assertEqual(job.run_id, "run-1")

    def test_dataset_from_row_maps_integrity_flag(self):
        dataset = _dataset_from(
            {
                "run_id": "run-1",
                "dataset": "ventas",
                "committed_at": 3.0,
                "manifest_present": True,
            }
        )
        self.assertEqual(dataset.dataset, "ventas")
        self.assertTrue(dataset.manifest_present)


class ActionRulesTests(unittest.TestCase):
    """Los conjuntos de acciones coinciden con el contrato del motor."""

    def test_cancellable_states_match_the_api(self):
        self.assertEqual(
            CANCELLABLE_STATES, ("claimed", "queued", "running", "deferred")
        )

    def test_retryable_states_match_the_api(self):
        self.assertEqual(RETRYABLE_STATES, ("failed", "quarantined", "cancelled"))

    def test_every_job_state_has_a_label_and_a_unique_symbol(self):
        from motor.storage.schema import JOB_STATES

        self.assertEqual(set(JOB_STATES), set(JOB_STATE_LABELS))
        symbols = [value[2] for value in JOB_STATE_LABELS.values()]
        self.assertEqual(len(symbols), len(set(symbols)), "símbolos duplicados")


@requires_display
class SymbolFallbackTests(unittest.TestCase):
    """El contrato «nunca solo color» sobrevive sin los glifos (FE-001).

    Si la familia resuelta por Tk no dibuja `✓ ✕ ⚠ ⏸`, `resolve_symbol` debe
    devolver una forma ASCII. Los símbolos alternativos tampoco pueden repetirse
    entre estados, o la señal de forma se perdería.
    """

    @classmethod
    def setUpClass(cls):
        import tkinter as tk

        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_resolved_family_is_a_real_tk_family(self):
        from tkinter import font as tkfont

        from yachay_desktop.theme import TYPE

        self.assertIn(TYPE.family.lower(), {f.lower() for f in tkfont.families()})

    def test_symbols_are_unique_whether_or_not_the_font_has_them(self):
        from yachay_desktop.theme import state_label

        rendered = [state_label(state)[2] for state in JOB_STATE_LABELS]
        self.assertEqual(
            len(rendered), len(set(rendered)), "símbolos renderizados duplicados"
        )

    def test_resolve_symbol_returns_a_nonempty_form(self):
        from yachay_desktop.theme import resolve_symbol

        for symbol in ("✓", "✕", "⚠", "⏸", "→", "▶", "▤", "⊘", "…"):
            self.assertTrue(resolve_symbol(symbol))

    def test_filter_options_map_spanish_labels_to_raw_states(self):
        from yachay_desktop.theme import filter_options

        options = filter_options()
        self.assertIsNone(options["Todos"])
        self.assertEqual(set(options.values()) - {None}, set(JOB_STATE_LABELS))
        # Ninguna etiqueta muestra el estado crudo en inglés (FE-007).
        for label in options:
            if label != "Todos":
                for raw in JOB_STATE_LABELS:
                    self.assertNotIn(raw, label)


class FunctionCatalogTests(unittest.TestCase):
    """El resumen de funciones debe reflejar el alcance documentado."""

    def test_catalog_has_modeling_and_operational_capabilities(self):
        from yachay_desktop.app import FUNCTION_CATALOG

        phases = {row[0] for row in FUNCTION_CATALOG}
        self.assertTrue({"M1", "M2", "M3", "M4", "M5", "I", "P"} <= phases)
        self.assertEqual(len(FUNCTION_CATALOG), 7)
        for row in FUNCTION_CATALOG:
            self.assertEqual(len(row), 4)
            self.assertTrue(all(value.strip() for value in row))


class ClientTransportTests(unittest.TestCase):
    """El cliente habla el envelope real y ramifica por el código cerrado."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.tmp.name) / "motor.sock"
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.socket_path))
        self.server.listen(1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def tearDown(self):
        self._stop.set()
        try:
            self.server.close()
        except OSError:
            pass
        self._thread.join(timeout=2)
        self.tmp.cleanup()

    def _serve(self):
        """Responde una única petición con el cuerpo preparado en self._body."""
        self.server.settimeout(0.5)
        # Acepta conexiones hasta que se detenga; cada una recibe self._body.
        while not self._stop.is_set():
            try:
                conn, _ = self.server.accept()
            except (TimeoutError, OSError):
                continue
            with conn:
                self._request = conn.recv(65536).decode("latin-1")
                payload = json.dumps(getattr(self, "_body", {})).encode()
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + b"Content-Length: " + str(len(payload)).encode()
                    + b"\r\nConnection: close\r\n\r\n" + payload
                )

    def test_success_envelope_is_unwrapped(self):
        self._body = {
            "api_version": "v1",
            "ok": True,
            "data": {"status": "ok", "api_version": "v1", "database": "ok"},
        }
        client = ApiClient(self.socket_path)
        self.assertEqual(client.health()["database"], "ok")

    def test_error_envelope_raises_with_the_closed_code(self):
        self._body = {
            "api_version": "v1",
            "ok": False,
            "error": {"code": "not_found", "message": "job not found"},
        }
        client = ApiClient(self.socket_path)
        with self.assertRaises(ApiError) as ctx:
            client.job("nope")
        self.assertEqual(ctx.exception.code, "not_found")
        self.assertEqual(ctx.exception.label, "No encontrado")

    def test_missing_socket_raises_unreachable(self):
        client = ApiClient(Path(self.tmp.name) / "absent.sock")
        with self.assertRaises(ApiUnreachable):
            client.health()

    def test_client_reads_bearer_token_from_environment(self):
        self._body = {
            "api_version": "v1",
            "ok": True,
            "data": {"status": "ok", "api_version": "v1", "database": "ok"},
        }
        with patch.dict(os.environ, {"YACHAY_API_TOKEN": "test-token"}):
            client = ApiClient(self.socket_path)
        self.assertEqual(client.api_token, "test-token")
        self.assertEqual(client.health()["status"], "ok")
        self.assertIn("Authorization: Bearer test-token", self._request)


class WorkerCoordinationTests(unittest.TestCase):
    """Periodic refreshes must not fan out overlapping background workers."""

    def test_refresh_starts_at_most_one_worker_at_a_time(self):
        app = YachayApp.__new__(YachayApp)
        app._auto_refresh = True
        app._closed = threading.Event()
        app._refresh_lock = threading.Lock()
        app._refresh_generation = 0
        app._start_worker = Mock(return_value=True)

        app.refresh()
        app.refresh()

        self.assertEqual(app._start_worker.call_count, 1)
        app._refresh_lock.release()


class ClientAgainstRealApiTests(unittest.TestCase):
    """El conector contra el servidor real: la correspondencia es verificable.

    Los stubs prueban el envelope; solo un servidor real prueba que la ruta, el
    método y la forma que el conector declara existen de verdad (auditoría
    conector-fullstack). Cubre también las reglas (M3).
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from motor.api.server import build_server
        from motor.config import MotorConfig

        root = Path(self.tmp.name)
        self.config = MotorConfig.from_root(root)
        self.socket_path = root / "motor.sock"
        self.server = build_server(self.config, self.socket_path)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()
        self.addCleanup(self._stop)
        self.client = ApiClient(self.socket_path)

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def _model(self, csv: bytes) -> int:
        inbox = self.config.paths.inbox
        if (inbox / "datos.csv").exists():
            (inbox / "datos.csv").unlink()
        (self.config.paths.model_inputs / "datos.csv").unlink(missing_ok=True)
        if not (inbox / "datos.csv").exists():
            self.client.upload("datos.csv", csv)
        from motor.model import store
        from motor.storage.db import connect
        conn = connect(self.config.paths.sqlite_path)
        try:
            used = {m.name for m in store.list_models(conn)}
        finally:
            conn.close()
        name = next(f"m{i}" for i in range(1_000) if f"m{i}" not in used)
        return self.client.create_model(name, "datos.csv")["model_id"]

    def test_client_works_when_server_requires_environment_token(self):
        from motor.api.server import build_server

        self._stop()
        self.server = build_server(
            self.config, self.socket_path, api_token="connector-token"
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()
        with patch.dict(os.environ, {"YACHAY_API_TOKEN": "connector-token"}):
            client = ApiClient(self.socket_path)

        self.assertEqual(client.health()["status"], "ok")

    def test_reads_match_the_provider(self):
        self.client.upload("datos.csv", b"id,ciudad\n1,Cusco\n")
        self.assertEqual(self.client.health()["status"], "ok")
        self.assertEqual(self.client.jobs(), [])
        self.assertEqual(self.client.datasets(), [])
        self.assertEqual(self.client.models(), [])
        profile = self.client.profile("datos.csv")
        self.assertEqual(profile["rows"], 1)

    def test_dataset_detail_and_manifest_match_the_provider(self):
        import pyarrow as pa

        from motor.publishing import publish
        from motor.storage.db import connect

        manifest = publish.publish_run(
            table=pa.table({"id": [1]}),
            schema_fingerprint="fp",
            dataset="demo",
            run_id="run-1",
            job_key="key-1",
            processed_dir=self.config.paths.processed,
            tmp_dir=self.config.paths.tmp / "run-1",
        )
        conn = connect(self.config.paths.sqlite_path)
        try:
            conn.execute(
                "INSERT INTO datasets "
                "(run_id, job_key, dataset, manifest_path, committed_at) "
                "VALUES (?,?,?,?,?)",
                ("run-1", "key-1", "demo", str(manifest), 0),
            )
        finally:
            conn.close()

        self.assertEqual(self.client.dataset("run-1")["run_id"], "run-1")
        self.assertEqual(self.client.manifest("run-1")["job_key"], "key-1")

    def test_model_runs_and_diff_round_trip_through_the_connector(self):
        model_id = self._model(b"id,estado\n1,Hecho\n")

        self.assertEqual(self.client.model(model_id)["model_id"], model_id)
        first = self.client.execute(model_id, "datos.csv")
        second = self.client.execute(model_id, "datos.csv")
        listed = self.client.runs(model_id)

        self.assertEqual(len(listed), 2)
        self.assertEqual(listed[0]["run_id"], second["run_id"])
        self.assertEqual(self.client.diff(first["run_id"], second["run_id"])["changed"], False)

    def test_job_detail_cancel_and_retry_round_trip_through_the_connector(self):
        from motor.storage.db import connect

        input_path = self.config.paths.inbox / "retry.csv"
        input_path.write_bytes(b"id\n1\n")
        conn = connect(self.config.paths.sqlite_path)
        try:
            conn.execute(
                "INSERT INTO jobs (job_id, job_key, state, created_at) "
                "VALUES (?,?,?,?)",
                ("job-cancel", "key-cancel", "queued", 0),
            )
            conn.execute(
                "INSERT INTO jobs (job_id, job_key, state, created_at) "
                "VALUES (?,?,?,?)",
                ("job-retry", "key-retry", "failed", 0),
            )
            conn.execute(
                "INSERT INTO input_files (job_id, path, sha256, size_bytes, mtime_ns) "
                "VALUES (?,?,?,?,?)",
                ("job-retry", str(input_path), "sha", input_path.stat().st_size, 0),
            )
        finally:
            conn.close()

        self.assertEqual(self.client.job("job-cancel").job_id, "job-cancel")
        self.assertTrue(self.client.cancel("job-cancel")["cancel_requested"])
        self.assertEqual(self.client.retry("job-retry")["state"], "queued")

    def test_rules_round_trip_through_the_connector(self):
        model_id = self._model(b"id,ciudad,estado\n1,Cusco,Hecho\n2,Lima,En curso\n")

        self.assertEqual(self.client.rules(model_id), [])

        created = self.client.create_rule(
            model_id, name="renombrar", type="rename", column="ciudad", to="lugar"
        )
        self.assertEqual(created["type"], "rename")
        self.assertEqual(created["params"], {"to": "lugar"})

        listed = self.client.rules(model_id)
        self.assertEqual([r["name"] for r in listed], ["renombrar"])

        deleted = self.client.delete_rule(model_id, "renombrar")
        self.assertTrue(deleted["deleted"])
        self.assertEqual(self.client.rules(model_id), [])

    def test_rule_types_send_only_their_own_params(self):
        model_id = self._model(b"id,estado\n1,Hecho\n")

        mapped = self.client.create_rule(
            model_id, name="m", type="map", column="estado",
            values={"Hecho": "OK"},
        )
        self.assertEqual(mapped["params"], {"values": {"Hecho": "OK"}})

        domains = self.client.create_rule(
            model_id, name="d", type="domain", column="estado", allowed=["Hecho"]
        )
        self.assertEqual(domains["params"], {"allowed": ["Hecho"]})

    def test_upload_keeps_the_name_the_provider_reports(self):
        """Regresión H-01: el nombre que el motor guarda es el que se pidió.

        `a&b.csv` se partía en `a` y el 201 se daba por bueno; con el alfabeto
        estricto del contrato, el conector lo rechaza antes de enviar y no deja
        ningún archivo en la bandeja ni un nombre renombrado a escondidas.
        """
        for name in ("a&b.csv", "datos#1.csv", "ventas año.csv"):
            with self.subTest(name=name):
                with self.assertRaises(ApiError) as ctx:
                    self.client.upload(name, b"id\n1\n")
                self.assertEqual(ctx.exception.code, "bad_request")
        self.assertEqual(list(self.config.paths.inbox.iterdir()), [])

        result = self.client.upload("informe.final_2.csv", b"id\n1\n")
        self.assertEqual(result["name"], "informe.final_2.csv")
        self.assertTrue((self.config.paths.inbox / "informe.final_2.csv").is_file())

    def test_ui_and_provider_agree_on_the_name_alphabet(self):
        """Lo que la UI bloquea es exactamente lo que el proveedor rechaza.

        Coherencia en los dos sentidos: un nombre que pasa `check_name` lo acepta
        el servidor con el mismo valor, y uno rechazado por `check_name` recibe
        del servidor `bad_request`. Sin esto, `a&b.csv` se guardaba como `a`.
        """
        from yachay_desktop.client import check_name

        names = ["a&b.csv", "datos#1.csv", "datos+uno.csv", "ventas año.csv",
                 "Sin título.csv", "100% real.csv", "informe.final_2.csv", "A1_-x.ods"]
        for name in names:
            with self.subTest(name=name):
                try:
                    check_name(name)
                except ApiError:
                    status, payload = self.call_raw_post(name)
                    self.assertEqual(status, 400)
                    self.assertEqual(payload["error"]["code"], "bad_request")
                else:
                    result = self.client.upload(name, b"id\n1\n")
                    self.assertEqual(result["name"], name)
                    self.assertTrue((self.config.paths.inbox / name).is_file())
                    (self.config.paths.inbox / name).unlink()

    def call_raw_post(self, name: str) -> tuple[int, dict]:
        """POST crudo (`/v1/inputs?name=`), sin pasar por `check_name`."""
        conn = _UnixHTTPConnection(str(self.socket_path), timeout=5)
        try:
            conn.request("POST", f"/v1/inputs?name={quote(name, safe='')}",
                         body=b"id\n1\n", headers={"Content-Length": "5"})
            response = conn.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            conn.close()

    def test_rule_name_with_query_specials_round_trips(self):
        """Regresión: un nombre con `&` o `#` ya no parte ni trunca la query.

        Antes, `create_rule` con `quote` por defecto dejaba `&` intacto: la regla
        nacía truncada y `delete_rule` buscaba otro nombre. El nombre viaja
        ahora codificado una sola vez y el servidor `parse_qs` lo decodifica.

        No se encadena `delete_rule`: lleva el nombre en la **ruta** y el router no decodifica
        parámetros de ruta (limitación preexistente del proveedor, fuera de este
        cambio): se prueba con un nombre que no necesita escaparse en la ruta y
        el resto de la coherencia queda en la tabla de impacto.
        """
        for name in ("a&b", "a&b=", "#", "a&b=c"):
            with self.subTest(name=name):
                model_id = self._model(b"id,estado\n1,Hecho\n")
                self.client.create_rule(
                    model_id, name=name, type="filter", column="estado", equals="Hecho"
                )
                self.assertEqual([r["name"] for r in self.client.rules(model_id)], [name])
                created_id = self.client.rules(model_id)[0]
                self.assertEqual(created_id["type"], "filter")
                self.assertEqual(created_id["params"], {"equals": "Hecho"})

    def test_orphans_of_two_files(self):
        self.client.upload("ventas.csv", b"cliente\nana\nfantasma\n")
        self.client.upload("clientes.csv", b"nombre\nana\n")
        result = self.client.orphans("ventas.csv", "clientes.csv", "cliente", "nombre")
        self.assertEqual(result["rows_out"], 1)
        self.assertEqual(result["orphans"][0][0], "fantasma")


@requires_display
class AppRefreshBridgeTests(unittest.TestCase):
    """Regresión: el resultado de un worker llega a la UI.

    `after()` llamado desde un hilo de trabajo no ejecutaba el callback: el
    panel se quedaba en «—» para siempre y todos los tests de humo lo ocultaban
    porque construyen la app con `auto_start=False`. Aquí se usa el puente real
    con un motor de verdad.
    """

    def test_worker_result_reaches_the_ui(self):
        """El resultado de un worker en hilo real acaba en los widgets.

        Usa un cliente falso (no un servidor): abrir y cerrar un motor real
        mientras un worker llama a la API es una carrera del propio test que
        aborta el proceso. Lo que aquí se prueba es el puente, que era el
        defecto: `after()` desde un hilo perdía el callback.
        """
        import time

        from yachay_desktop.app import YachayApp

        with tempfile.TemporaryDirectory() as tmp:
            app = YachayApp(Path(tmp) / "motor.sock", auto_start=False)
            app.withdraw()
            try:
                app.client.health = lambda: {
                    "status": "ok", "api_version": "v1", "database": "ok"
                }
                app.client.jobs = lambda **k: []
                app.client.datasets = lambda **k: []
                app._auto_refresh = True
                app.refresh()

                end = time.time() + 3
                while time.time() < end:
                    app.update()
                    if app.metric_labels["service"].cget("text") == "En línea":
                        break
                    time.sleep(0.02)
                self.assertEqual(app.metric_labels["service"].cget("text"), "En línea")
                self.assertEqual(app.metric_labels["database"].cget("text"), "Operativa")
            finally:
                app._auto_refresh = False
                for attr in ("_after_id", "_drain_id"):
                    pending = getattr(app, attr)
                    if pending is not None:
                        app.after_cancel(pending)
                        setattr(app, attr, None)
                app._on_close()

    def test_run_on_ui_executes_from_a_worker_thread(self):
        """El puente entrega lo que un hilo distinto encola."""
        import time

        from yachay_desktop.app import YachayApp

        with tempfile.TemporaryDirectory() as tmp:
            app = YachayApp(Path(tmp) / "motor.sock", auto_start=False)
            app.withdraw()
            seen = []
            worker = threading.Thread(
                target=lambda: app._run_on_ui(seen.append, "desde-hilo"), daemon=True
            )
            worker.start()
            try:
                end = time.time() + 3
                while time.time() < end and not seen:
                    app.update()
                    time.sleep(0.02)
                self.assertEqual(seen, ["desde-hilo"])
            finally:
                app._on_close()

@requires_display
class AppCloseTests(unittest.TestCase):
    """DF-001: cerrar cancela el refresco pendiente en vez de dejarlo armado.

    Una raíz propia: Tk no tolera dos instancias vivas en el mismo proceso.
    """

    def test_close_cancels_the_pending_refresh(self):
        import tkinter as tk

        from yachay_desktop.app import YachayApp

        with tempfile.TemporaryDirectory() as tmp:
            app = YachayApp(Path(tmp) / "motor.sock", auto_start=False)
            app.withdraw()
            app._after_id = app.after(60_000, lambda: None)
            queued = app._after_id
            self.assertIsNotNone(queued)

            app._on_close()

            self.assertIsNone(app._after_id)
            # La interpretación ya no existe: el id cancelado no puede seguir vivo.
            with self.assertRaises(tk.TclError):
                app.tk.call("after", "info", queued)

    def test_close_is_registered_as_the_window_protocol(self):
        from yachay_desktop.app import YachayApp

        with tempfile.TemporaryDirectory() as tmp:
            app = YachayApp(Path(tmp) / "motor.sock", auto_start=False)
            app.withdraw()
            try:
                self.assertTrue(app.protocol("WM_DELETE_WINDOW"))
            finally:
                app._on_close()


@requires_display
class AppSmokeTests(unittest.TestCase):
    """La aplicación se construye y sus vistas respetan el contrato.

    Tk no admite varias raíces en el mismo proceso de forma segura, así que se
    construye la aplicación UNA vez para toda la clase y cada prueba la reutiliza.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        from yachay_desktop.app import YachayApp

        cls.app = YachayApp(Path(cls.tmp.name) / "motor.sock", auto_start=False)
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app._on_close()
        cls.tmp.cleanup()

    def test_builds_the_nine_views(self):
        self.assertEqual(
            set(self.app.views),
            {"status", "files", "model", "execute", "results", "visualizations",
             "assistant", "jobs", "datasets"},
        )

    def test_every_view_has_a_freshness_indicator(self):
        self.assertEqual(set(self.app.titles), set(self.app.views))
        for title in self.app.titles.values():
            self.assertTrue(title.freshness.cget("text"))

    def test_status_explains_the_job_count_window(self):
        self.app._render_status({"database": "ok"}, [], [])

        self.assertEqual(
            self.app.metric_hints["jobs_total"].cget("text"),
            "últimos 100 trabajos",
        )

    def test_navigating_every_view_does_not_raise(self):
        for name in ("status", "files", "model", "execute", "results",
                     "jobs", "datasets"):
            self.app.show_view(name)
            self.app.update()

    def test_selecting_dataset_enables_manifest_button_without_opening_it(self):
        from types import SimpleNamespace
        from unittest import mock

        app = self.app
        original_command = app._show_manifest
        show_manifest = mock.Mock()
        app.btn_manifest.configure(command=show_manifest)
        try:
            app._render_datasets([
                SimpleNamespace(
                    dataset="datos",
                    run_id="run-1",
                    committed_at=1.0,
                    manifest_present=True,
                )
            ])
            item = app.datasets_tree.get_children()[0]
            app.datasets_tree.selection_set(item)
            app.update()

            self.assertEqual(app.btn_manifest.cget("state"), "normal")
            self.assertIn("Íntegro", app.datasets_hint.cget("text"))
            show_manifest.assert_not_called()

            app.btn_manifest.invoke()
            show_manifest.assert_called_once_with()
        finally:
            app.btn_manifest.configure(command=original_command)
            app.datasets_tree.selection_remove(*app.datasets_tree.selection())
            app._render_datasets([])
            app.update()

    def test_model_creation_sends_only_the_filename_from_a_pasted_path(self):
        import threading as _threading
        from unittest import mock

        class InlineThread:
            def __init__(self, *, target=None, daemon=None):
                self._target = target

            def start(self):
                self._target()

        original_create = self.app.client.create_model
        original_run_on_ui = self.app._run_on_ui
        create_model = mock.Mock()
        self.app.client.create_model = create_model
        self.app._run_on_ui = mock.Mock()
        self.app.model_source.set(str(Path(self.tmp.name) / "Informe_Tecnico.xlsx"))
        self.app.model_name.set("")
        try:
            with mock.patch.object(_threading, "Thread", InlineThread):
                self.app._do_create_model()
        finally:
            self.app.client.create_model = original_create
            self.app._run_on_ui = original_run_on_ui

        create_model.assert_called_once_with("Informe_Tecnico", "Informe_Tecnico.xlsx")

    def test_created_model_context_reaches_execution_and_results(self):
        self.app._set_model_context(7, "Informe_Tecnico.xlsx")

        self.assertEqual(self.app.exec_model.get(), "7")
        self.assertEqual(self.app.exec_source.get(), "Informe_Tecnico.xlsx")
        self.assertEqual(self.app.results_model.get(), "7")

    def test_profile_rejects_files_over_the_api_limit_before_reading(self):
        from yachay_desktop.app import MAX_REQUEST_BYTES

        oversized = Path(self.tmp.name) / "grande.csv"
        with oversized.open("wb") as handle:
            handle.truncate(MAX_REQUEST_BYTES + 1)
        self.app.files_path.set(str(oversized))

        self.app._do_profile()

        self.assertEqual(self.app.profile_button.cget("state"), "normal")
        self.assertIn("supera el límite", self.app.files_status.cget("text"))

    def test_stale_refresh_generation_cannot_overwrite_current_ui(self):
        self.app._refresh_generation = 2
        self.app.metric_labels["service"].configure(text="En línea")

        self.app._apply_refresh(
            {"status": "ok", "database": "ok"},
            [],
            [],
            None,
            generation=1,
        )

        self.assertEqual(self.app.metric_labels["service"].cget("text"), "En línea")

    def test_model_load_error_is_visible(self):
        from unittest import mock

        from yachay_desktop.app import ApiUnreachable

        class InlineThread:
            def __init__(self, *, target=None, daemon=None):
                self._target = target

            def start(self):
                self._target()

        original_models = self.app.client.models
        self.app.client.models = mock.Mock(
            side_effect=ApiUnreachable("motor no disponible")
        )
        try:
            with mock.patch("threading.Thread", InlineThread):
                self.app._load_models()
        finally:
            self.app.client.models = original_models

        self.assertIn("No se pudieron cargar los modelos", self.app.model_status.cget("text"))
        self.assertIn("motor no disponible", self.app.model_status.cget("text"))

    def test_status_counts_are_unavailable_without_connection(self):
        self.app._render_status(None, [], [])
        self.app._render_jobs([], unavailable=True)
        self.app._render_datasets([], unavailable=True)

        for key in ("jobs_total", "running", "failed", "datasets"):
            self.assertEqual(
                self.app.metric_labels[key].cget("text"), "No disponible"
            )
        self.assertIn(
            "Sin conexión",
            self.app.jobs_tree.item(self.app.jobs_tree.get_children()[0], "values")[1],
        )
        self.assertIn(
            "Sin conexión",
            self.app.datasets_tree.item(
                self.app.datasets_tree.get_children()[0], "values"
            )[0],
        )

    def test_job_actions_follow_the_state_contract(self):
        # Inyecta un trabajo por estado y comprueba la habilitación de botones
        # contra los conjuntos reales del motor.
        observations = []
        for state in JOB_STATE_LABELS:
            job = Job(
                job_id=f"job-{state}",
                state=state,
                attempt=1,
                created_at=1.0,
                started_at=None,
                cancel_requested=False,
                deferred_reason=None,
            )
            self.app._jobs_by_item = {"item": job}
            # `_selected_job` lee `jobs_tree.selection()`; se sustituye por una
            # función que devuelve el item inyectado.
            selection = lambda *_a, **_k: ("item",)  # noqa: E731
            self.app.jobs_tree.selection = selection
            self.app._update_job_actions()
            observations.append(
                (
                    state,
                    self.app.btn_retry.cget("state") == "normal",
                    self.app.btn_cancel.cget("state") == "normal",
                )
            )
        for state, retry, cancel in observations:
            self.assertEqual(retry, state in RETRYABLE_STATES, f"retry en {state}")
            self.assertEqual(cancel, state in CANCELLABLE_STATES, f"cancel en {state}")

    def test_unreachable_motor_shows_the_error_banner_and_freshness(self):
        # Aplica el resultado de una pasada de refresco sin hilos ni `after`,
        # igual que haría el hilo, para no dejar callbacks sobre Tk.
        app = self.app
        try:
            app.client.health()
            raise AssertionError("se esperaba un fallo de conexión")
        except ApiUnreachable as exc:
            app._apply_refresh(None, [], [], exc)
        app.update()
        self.assertIsInstance(app._last_error, ApiUnreachable)
        for title in app.titles.values():
            # Con el auto-arranque desactivado, el indicador antepone la pausa
            # al estado de conexión.
            self.assertIn("Sin conexión con el motor", title.freshness.cget("text"))

    def test_job_filter_error_shows_the_error_banner_and_freshness(self):
        from unittest import mock

        app = self.app
        original_jobs = app.client.jobs
        original_after = app.after
        pending = []

        def fail_jobs(*_args, **_kwargs):
            raise ApiUnreachable("motor no disponible")

        def capture_after(_ms, fn=None, *args):
            if fn is not None:
                pending.append(lambda: fn(*args))
            return "fake"

        class InlineThread:
            def __init__(self, *, target=None, daemon=None):
                self._target = target

            def start(self):
                self._target()

        app.client.jobs = fail_jobs
        app.after = capture_after
        try:
            with mock.patch("threading.Thread", InlineThread):
                app._load_jobs()
        finally:
            app.client.jobs = original_jobs
            app.after = original_after
        for step in pending:
            step()
        app.update()
        self.assertIsInstance(app._last_error, ApiUnreachable)
        self.assertIn("motor no disponible", app.banner.label.cget("text"))
        for title in app.titles.values():
            self.assertIn("Sin conexión con el motor", title.freshness.cget("text"))


if __name__ == "__main__":
    unittest.main()
