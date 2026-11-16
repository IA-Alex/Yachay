"""Tests for the v1 API boundary over a Unix domain socket (ADR-016).

Every test drives the real server over a real socket: the API is a process
boundary, so mocking it would test nothing that matters. Only the CLI wiring
test uses a double, because starting `serve_forever` in-process would block.
"""

from __future__ import annotations

import http.client
import io
import json
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.api.envelope import API_VERSION, ApiError, ErrorCode, error
from motor.api.routes import ApiContext
from motor.api.server import build_server
from motor.api.types import Body
from motor.config import MotorConfig
from motor.storage import jobs
from motor.storage.db import connect

CLOSED_ERROR_CODES = {
    ErrorCode.BAD_REQUEST,
    ErrorCode.UNAUTHORIZED,
    ErrorCode.NOT_FOUND,
    ErrorCode.METHOD_NOT_ALLOWED,
    ErrorCode.INVALID_STATE,
    ErrorCode.PAYLOAD_TOO_LARGE,
    ErrorCode.INTERNAL_ERROR,
    ErrorCode.UNAVAILABLE,
}


class UnixHTTPConnection(http.client.HTTPConnection):
    """HTTP client that connects through an AF_UNIX socket (stdlib only)."""

    def __init__(self, socket_path: Path):
        super().__init__("localhost", timeout=10)
        self._socket_path = str(socket_path)

    def connect(self) -> None:  # noqa: D401 - stdlib hook
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(self._socket_path)
        self.sock = sock


class ApiTestBase(unittest.TestCase):
    """Boots a real API server on a temporary Unix socket."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = MotorConfig.from_root(self.root)
        self.socket_path = self.root / "api.sock"
        self.server = build_server(self.config, self.socket_path)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()

    def tearDown(self) -> None:
        self.stop_server()
        self.tmp.cleanup()

    def stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def restart_server(self) -> None:
        """Bring the server back after a test closed it, so tearDown stays valid."""
        self.server = build_server(self.config, self.socket_path)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()

    def raw(self, request: bytes) -> tuple[int, dict[str, str], bytes]:
        """Send a hand-built request and return status, headers and raw body.

        Some transport cases (a lying `Content-Length`, a HEAD probe, a client
        that stops writing mid-body) cannot be expressed through `http.client`.
        """
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(str(self.socket_path))
        try:
            sock.sendall(request)
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            sock.close()
        head, _, body = b"".join(chunks).partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        status = int(lines[0].split(" ")[1])
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        return status, headers, body

    def call(self, method: str, path: str, *, body: bytes | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, dict]:
        conn = UnixHTTPConnection(self.socket_path)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload
        finally:
            conn.close()

    def db(self):
        conn = connect(self.config.paths.sqlite_path)
        self.addCleanup(conn.close)
        return conn

    def make_job(self, *, state: str = "queued", job_id: str = "job-1",
                 job_key: str = "key-1", attempt: int = 0,
                 with_input: bool = True) -> str:
        conn = self.db()
        jobs.enqueue(conn, job_id=job_id, job_key=job_key)
        if with_input:
            target = self.config.paths.processing / f"{job_id}.csv"
            target.write_text("id,v\n1,a\n", encoding="utf-8")
            conn.execute(
                "INSERT OR REPLACE INTO input_files "
                "(job_id, path, sha256, size_bytes, mtime_ns) VALUES (?,?,?,?,?)",
                (job_id, str(target), "hash", 8, 0),
            )
        conn.execute(
            "UPDATE jobs SET state = ?, attempt = ? WHERE job_id = ?",
            (state, attempt, job_id),
        )
        return job_id


class TransportAndEnvelopeTests(ApiTestBase):
    """Socket transport, envelope shape and the closed error set."""

    def test_configured_bearer_token_is_required(self):
        self.stop_server()
        self.server = build_server(self.config, self.socket_path, api_token="secret")
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()

        status, payload = self.call("GET", "/v1/health")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], ErrorCode.UNAUTHORIZED)

        status, payload = self.call(
            "GET", "/v1/health", headers={"Authorization": "Bearer secret"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_health_returns_ok_envelope(self):
        status, payload = self.call("GET", "/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["api_version"], API_VERSION)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["status"], "ok")
        self.assertNotIn("error", payload)

    def test_unknown_route_returns_not_found(self):
        status, payload = self.call("GET", "/v1/nope")
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], ErrorCode.NOT_FOUND)

    def test_known_path_with_wrong_method_returns_method_not_allowed(self):
        status, payload = self.call("DELETE", "/v1/jobs")
        self.assertEqual(status, 405)
        self.assertEqual(payload["error"]["code"], ErrorCode.METHOD_NOT_ALLOWED)

    def test_a_dynamic_path_with_wrong_method_returns_method_not_allowed(self):
        # `/v1/jobs/{job_id}` exists, but only for GET.
        status, payload = self.call("DELETE", "/v1/jobs/job-1")
        self.assertEqual(status, 405)
        self.assertEqual(payload["error"]["code"], ErrorCode.METHOD_NOT_ALLOWED)

    def test_an_error_after_routing_logs_the_route_template(self):
        """La ruta se loguea aunque el handler falle (API-01).

        Antes, cualquier `ApiError` posterior a `_resolve` dejaba
        `route="<unrouted>"` en el log, impidiendo agregar errores por endpoint.
        """
        import contextlib
        import io

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            status, payload = self.call(
                "POST", "/v1/inputs?name=log.csv", body=b"{}",
                headers={"Content-Type": "application/json"},
            )
        self.assertEqual(status, 400)
        line = captured.getvalue().strip().splitlines()[-1]
        record = json.loads(line)
        self.assertEqual(record["route"], "/v1/inputs")
        self.assertEqual(record["status"], 400)

    def test_a_handler_error_also_logs_its_route(self):
        """Un 404 de un handler nombra el endpoint, no `<unrouted>` (API-01)."""
        import contextlib
        import io

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            status, _ = self.call("GET", "/v1/jobs/job-ausente")
        self.assertEqual(status, 404)
        record = json.loads(captured.getvalue().strip().splitlines()[-1])
        self.assertEqual(record["route"], "/v1/jobs/{job_id}")

    def test_error_codes_belong_to_a_closed_set(self):
        status, payload = self.call("GET", "/v1/unknown")
        self.assertEqual(status, 404)
        self.assertIn(payload["error"]["code"], CLOSED_ERROR_CODES)
        self.assertTrue(payload["error"]["message"])
        self.assertEqual(set(payload), {"api_version", "ok", "error"})

    def test_socket_is_created_with_group_write_permissions(self):
        mode = stat.S_IMODE(self.socket_path.stat().st_mode)
        self.assertEqual(mode, 0o660)

    def test_socket_is_removed_when_the_server_closes(self):
        self.stop_server()
        self.assertFalse(self.socket_path.exists())
        self.restart_server()

    def test_a_declared_json_body_returns_bad_request(self):
        # The API has no JSON request contract: bodies are raw input bytes.
        status, payload = self.call(
            "POST", "/v1/inputs?name=x.csv", body=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], ErrorCode.BAD_REQUEST)
        self.assertFalse((self.config.paths.inbox / "x.csv").exists())

    def test_payload_over_the_hard_limit_returns_payload_too_large(self):
        from motor.limits import MAX_REQUEST_BYTES

        oversized = b"a" * (MAX_REQUEST_BYTES + 1)
        status, payload = self.call("POST", "/v1/inputs?name=big.csv", body=oversized)
        self.assertEqual(status, 413)
        self.assertEqual(payload["error"]["code"], ErrorCode.PAYLOAD_TOO_LARGE)
        self.assertFalse((self.config.paths.inbox / "big.csv").exists())

    def test_server_does_not_bind_a_tcp_port(self):
        # The only listening address is the Unix socket: no TCP anywhere.
        self.assertEqual(self.server.socket.family, socket.AF_UNIX)

    def test_an_unknown_error_code_cannot_be_built(self):
        # The error set is closed: a typo fails at construction, not in a client.
        with self.assertRaises(ValueError):
            error("teapot", "nope")

    def test_head_returns_the_headers_without_a_body(self):
        status, headers, body = self.raw(
            b"HEAD /v1/health HTTP/1.1\r\nHost: localhost\r\n\r\n"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(headers["content-type"], "application/json; charset=utf-8")
        self.assertGreater(int(headers["content-length"]), 0)

    def test_put_patch_and_options_are_method_not_allowed(self):
        for method in (b"PUT", b"PATCH", b"OPTIONS"):
            status, _, body = self.raw(
                method + b" /v1/jobs HTTP/1.1\r\nHost: localhost\r\n\r\n"
            )
            self.assertEqual(status, 405, method)
            self.assertEqual(
                json.loads(body)["error"]["code"], ErrorCode.METHOD_NOT_ALLOWED
            )

    def test_an_invalid_content_length_returns_bad_request(self):
        status, _, body = self.raw(
            b"POST /v1/inputs?name=x.csv HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Length: abc\r\n\r\n"
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], ErrorCode.BAD_REQUEST)

    def test_a_negative_content_length_returns_bad_request(self):
        status, _, body = self.raw(
            b"POST /v1/inputs?name=x.csv HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Length: -5\r\n\r\n"
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], ErrorCode.BAD_REQUEST)

    def test_a_truncated_body_is_stored_exactly_as_received(self):
        # A short read must never be padded: the reported size is what arrived.
        status, _, body = self.raw(
            b"POST /v1/inputs?name=cut.csv HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Length: 100\r\n\r\nid,v\n"
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)["data"]["size_bytes"], 5)
        self.assertEqual((self.config.paths.inbox / "cut.csv").read_bytes(), b"id,v\n")

    def test_a_client_that_stops_sending_still_gets_the_413(self):
        from motor.limits import MAX_REQUEST_BYTES

        status, _, body = self.raw(
            b"POST /v1/inputs?name=big.csv HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Length: " + str(MAX_REQUEST_BYTES + 1).encode() + b"\r\n\r\n"
        )
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["error"]["code"], ErrorCode.PAYLOAD_TOO_LARGE)

    def test_closing_twice_is_harmless(self):
        self.stop_server()
        self.server.server_close()
        self.assertFalse(self.socket_path.exists())
        self.restart_server()

    def test_a_stale_socket_file_is_replaced_on_start(self):
        stale = self.root / "stale.sock"
        leftover = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        leftover.bind(str(stale))
        leftover.close()  # a crashed server leaves the file, not the listener
        self.assertTrue(stale.exists())
        server = build_server(self.config, stale)
        try:
            self.assertEqual(stat.S_IMODE(stale.stat().st_mode), 0o660)
        finally:
            server.server_close()
        self.assertFalse(stale.exists())

    def test_health_reports_an_unreadable_database_as_unavailable(self):
        import sqlite3

        def broken():
            raise sqlite3.OperationalError("unable to open database file")

        self.server.context = ApiContext(paths=self.config.paths, connect=broken)
        status, payload = self.call("GET", "/v1/health")
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], ErrorCode.UNAVAILABLE)

    def test_an_unexpected_failure_returns_internal_error(self):
        # A broken database must not leak a traceback or an internal message.
        def boom():
            raise RuntimeError("database is gone")

        self.server.context = ApiContext(paths=self.config.paths, connect=boom)
        status, payload = self.call("GET", "/v1/jobs")
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"]["code"], ErrorCode.INTERNAL_ERROR)
        self.assertEqual(payload["error"]["message"], "error interno")


class JobsEndpointTests(ApiTestBase):
    """Read and control operations on the job state machine."""

    def test_jobs_list_is_empty_initially(self):
        status, payload = self.call("GET", "/v1/jobs")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["jobs"], [])
        self.assertEqual(payload["data"]["count"], 0)

    def test_jobs_list_reports_jobs_and_filters_by_state(self):
        self.make_job(job_id="job-a", job_key="key-a", state="queued")
        self.make_job(job_id="job-b", job_key="key-b", state="failed")
        _, all_jobs = self.call("GET", "/v1/jobs")
        self.assertEqual(all_jobs["data"]["count"], 2)
        _, failed = self.call("GET", "/v1/jobs?state=failed")
        self.assertEqual([j["job_id"] for j in failed["data"]["jobs"]], ["job-b"])

    def test_jobs_list_rejects_an_unknown_state_filter(self):
        status, payload = self.call("GET", "/v1/jobs?state=bogus")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], ErrorCode.BAD_REQUEST)

    def test_jobs_list_rejects_an_empty_state_filter(self):
        """`?state=` no es «sin filtro»: es un filtro vacío, y se rechaza (API-05).

        Sin `keep_blank_values`, un desplegable sin seleccionar enviaba `?state=`
        y la API lo ignoraba en silencio devolviendo 200.
        """
        status, payload = self.call("GET", "/v1/jobs?state=")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], ErrorCode.BAD_REQUEST)

    def test_jobs_list_rejects_a_non_numeric_limit(self):
        status, payload = self.call("GET", "/v1/jobs?limit=lots")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], ErrorCode.BAD_REQUEST)

    def test_jobs_list_honours_the_limit(self):
        for index in range(3):
            self.make_job(job_id=f"job-{index}", job_key=f"key-{index}")
        status, payload = self.call("GET", "/v1/jobs?limit=2")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["count"], 2)
        self.assertEqual(len(payload["data"]["jobs"]), 2)

    def test_jobs_list_rejects_a_limit_outside_the_allowed_range(self):
        from motor.api.routes import MAX_LIMIT

        for limit in (0, -1, MAX_LIMIT + 1):
            status, payload = self.call("GET", f"/v1/jobs?limit={limit}")
            self.assertEqual(status, 400, limit)
            self.assertEqual(payload["error"]["code"], ErrorCode.BAD_REQUEST)

    def test_job_detail_never_exposes_lease_or_filesystem_paths(self):
        self.make_job(state="running")
        status, payload = self.call("GET", "/v1/jobs/job-1")
        self.assertEqual(status, 200)
        data = payload["data"]
        self.assertEqual(data["job_id"], "job-1")
        self.assertEqual(data["state"], "running")
        self.assertNotIn("lease_until", data)
        self.assertNotIn("worker_id", data)
        self.assertNotIn("path", data)
        self.assertNotIn(str(self.root), json.dumps(payload))

    def test_job_detail_unknown_id_returns_not_found(self):
        status, payload = self.call("GET", "/v1/jobs/job-missing")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], ErrorCode.NOT_FOUND)

    def test_cancel_sets_the_flag_in_sqlite(self):
        self.make_job(state="queued")
        status, payload = self.call("POST", "/v1/jobs/job-1/cancel")
        self.assertEqual(status, 200)
        self.assertTrue(payload["data"]["cancel_requested"])
        self.assertTrue(jobs.is_cancel_requested(self.db(), "job-1"))

    def test_cancel_on_a_terminal_job_returns_invalid_state(self):
        self.make_job(state="committed")
        status, payload = self.call("POST", "/v1/jobs/job-1/cancel")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], ErrorCode.INVALID_STATE)

    def test_cancel_unknown_job_returns_not_found(self):
        status, payload = self.call("POST", "/v1/jobs/job-x/cancel")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], ErrorCode.NOT_FOUND)

    def test_retry_requeues_a_failed_job_and_resets_the_attempt_budget(self):
        self.make_job(state="failed", attempt=3)
        status, payload = self.call("POST", "/v1/jobs/job-1/retry")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["state"], "queued")
        row = self.db().execute(
            "SELECT state, attempt, run_id, worker_id, lease_until, cancel_requested "
            "FROM jobs WHERE job_id = 'job-1'"
        ).fetchone()
        self.assertEqual(row, ("queued", 0, None, None, None, 0))

    def test_retry_unknown_job_returns_not_found(self):
        status, payload = self.call("POST", "/v1/jobs/job-x/retry")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], ErrorCode.NOT_FOUND)

    def test_retry_on_a_committed_job_returns_invalid_state(self):
        self.make_job(state="committed")
        status, payload = self.call("POST", "/v1/jobs/job-1/retry")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], ErrorCode.INVALID_STATE)

    def test_retry_without_a_recoverable_input_returns_invalid_state(self):
        self.make_job(state="failed", with_input=False)
        status, payload = self.call("POST", "/v1/jobs/job-1/retry")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], ErrorCode.INVALID_STATE)

    def test_retry_writes_an_audit_event(self):
        self.make_job(state="failed")
        self.call("POST", "/v1/jobs/job-1/retry")
        actions = [
            row[0]
            for row in self.db().execute("SELECT action FROM audit_events").fetchall()
        ]
        self.assertIn("api_retry_job", actions)


class RetryStateRaceTests(unittest.TestCase):
    """El retry no debe resucitar un job que ya llegó a un estado terminal.

    Regresión (F-03): el `UPDATE` de retry solo filtraba por `job_id`, así que si
    el worker commiteaba el job entre el `SELECT` y el `UPDATE`, el retry lo
    devolvía a `queued`. La guarda de estado se repite en la escritura.
    """

    def test_retry_refuses_when_the_state_changed_underneath(self):
        import io

        from motor.api.routes import ApiContext, Body, dispatch
        from motor.storage.db import connect

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            config = MotorConfig.from_root(root)
            conn = connect(config.paths.sqlite_path)
            jobs.enqueue(conn, job_id="job-1", job_key="key-1")
            target = config.paths.processing / "job-1.csv"
            target.write_text("id,v\n1,a\n", encoding="utf-8")
            conn.execute(
                "INSERT INTO input_files (job_id, path, sha256, size_bytes, mtime_ns) "
                "VALUES ('job-1', ?, 'h', 8, 0)",
                (str(target),),
            )
            conn.execute("UPDATE jobs SET state = 'failed' WHERE job_id = 'job-1'")

            class RacingConn:
                """Delega en la conexión real y simula un cambio de estado previo.

                Entre el `SELECT state` y el `UPDATE` del retry (después de leer el
                input), el estado del job cambia a `committed`: la guarda de estado
                del `UPDATE` debe entonces no tocar ninguna fila. El cambio se hace
                en la misma transacción, así que refleja el caso "la fila ya cambió
                cuando el UPDATE se ejecuta".
                """

                def __init__(self, inner):
                    self._inner = inner
                    self._raced = False

                def execute(self, sql, params=()):
                    cursor = self._inner.execute(sql, params)
                    flat = sql.replace("\n", " ")
                    if not self._raced and "FROM input_files" in flat:
                        self._raced = True
                        self._inner.execute(
                            "UPDATE jobs SET state = 'committed' WHERE job_id = 'job-1'"
                        )
                    return cursor

                def close(self):
                    # The handler closes its connection at the end; the test keeps
                    # the inner one open to assert the final state.
                    pass

            ctx = ApiContext(paths=config.paths, connect=lambda: RacingConn(conn))
            with self.assertRaises(ApiError) as raised:
                dispatch(
                    ctx,
                    method="POST",
                    target="/v1/jobs/job-1/retry",
                    body=Body(stream=io.BytesIO(b""), size=0),
                    headers={},
                )
            self.assertEqual(raised.exception.code, ErrorCode.INVALID_STATE)
            # El retry no resucitó el job: la guarda de estado rechazó el UPDATE.
            final_state = conn.execute(
                "SELECT state FROM jobs WHERE job_id = 'job-1'"
            ).fetchone()[0]
            self.assertNotEqual(final_state, "queued")


class InputUploadTests(ApiTestBase):
    """`POST /v1/inputs` only drops a stable file in `inbox/` (ADR-016)."""

    def test_upload_writes_the_file_atomically_into_inbox(self):
        status, payload = self.call(
            "POST", "/v1/inputs?name=data.csv", body=b"id,v\n1,a\n"
        )
        self.assertEqual(status, 201)
        self.assertTrue(payload["data"]["accepted"])
        target = self.config.paths.inbox / "data.csv"
        self.assertEqual(target.read_bytes(), b"id,v\n1,a\n")
        self.assertEqual(payload["data"]["size_bytes"], 9)

    def test_upload_never_inserts_a_job_row(self):
        self.call("POST", "/v1/inputs?name=data.csv", body=b"id\n1\n")
        self.assertEqual(
            self.db().execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0
        )

    def test_upload_leaves_no_partial_file_behind(self):
        self.call("POST", "/v1/inputs?name=data.csv", body=b"id\n1\n")
        leftovers = [p.name for p in self.config.paths.inbox.iterdir()
                     if p.name.endswith(".part")]
        self.assertEqual(leftovers, [])

    def test_upload_accepts_the_filename_header_as_a_fallback(self):
        status, payload = self.call(
            "POST", "/v1/inputs", body=b"id\n1\n",
            headers={"X-Yachay-Filename": "hdr.csv"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["data"]["name"], "hdr.csv")

    def test_path_parameter_is_percent_decoded(self):
        """Un nombre con `#`/espacio en la ruta llega decodificado al handler (#3).

        El router casa sobre la ruta codificada y decodifica solo el valor
        capturado: así `DELETE .../rules/regla%20%231` alcanza la regla guardada
        como `regla #1`, que antes respondía 404 con la regla presente.
        """
        status, payload = self.call(
            "POST", "/v1/inputs?name=data.csv", body=b"id,estado\n1,Hecho\n"
        )
        self.assertEqual(status, 201)
        status, created = self.call("POST", "/v1/models?name=m&source=data.csv")
        self.assertEqual(status, 201)
        model_id = created["data"]["model_id"]
        status, rule = self.call(
            "POST",
            f"/v1/models/{model_id}/rules?name=regla%20%231&type=filter"
            "&column=estado&equals=Hecho",
        )
        self.assertEqual(status, 201)
        self.assertEqual(rule["data"]["name"], "regla #1")

        status, deleted = self.call(
            "DELETE", f"/v1/models/{model_id}/rules/regla%20%231"
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["data"]["deleted"])
        status, listing = self.call("GET", f"/v1/models/{model_id}/rules")
        self.assertEqual(listing["data"]["rules"], [])

    def test_an_encoded_slash_does_not_create_a_new_segment(self):
        """`%2F` en la ruta NO se decodifica antes de casar la plantilla.

        Si el router decodificase la ruta completa, `/jobs/a%2F..%2Fb` se
        convertiría en un path traversal antes de resolver. Se decodifica
        después y solo el valor, así que el identificador conserva el `%2F`.
        """
        status, payload = self.call("GET", "/v1/jobs/a%2F..%2Fb")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], ErrorCode.NOT_FOUND)

    def test_upload_rejects_path_traversal(self):
        status, payload = self.call(
            "POST", "/v1/inputs?name=../escape.csv", body=b"id\n1\n"
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], ErrorCode.BAD_REQUEST)
        self.assertFalse((self.root / "escape.csv").exists())

    def test_upload_rejects_an_empty_body(self):
        """Un archivo de 0 bytes se rechaza en la frontera, no en el pipeline."""
        status, payload = self.call("POST", "/v1/inputs?name=empty.csv", body=b"")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], ErrorCode.BAD_REQUEST)
        self.assertFalse((self.config.paths.inbox / "empty.csv").exists())

    def test_upload_rejects_a_missing_name(self):
        status, payload = self.call("POST", "/v1/inputs", body=b"id\n1\n")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], ErrorCode.BAD_REQUEST)

    def test_upload_rejects_an_existing_name(self):
        (self.config.paths.inbox / "dup.csv").write_text("id\n1\n", encoding="utf-8")
        status, payload = self.call("POST", "/v1/inputs?name=dup.csv", body=b"id\n2\n")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], ErrorCode.INVALID_STATE)

    def test_upload_of_a_name_being_written_returns_invalid_state(self):
        # The `.part` file is the lock: a second writer fails before writing.
        (self.config.paths.inbox / "data.csv.part").write_bytes(b"")
        status, payload = self.call("POST", "/v1/inputs?name=data.csv", body=b"id\n1\n")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], ErrorCode.INVALID_STATE)
        self.assertFalse((self.config.paths.inbox / "data.csv").exists())
        self.assertEqual(
            (self.config.paths.inbox / "data.csv.part").read_bytes(), b""
        )

    def test_save_atomic_tolerates_part_removed_during_cleanup(self):
        target = self.config.paths.inbox / "race.csv"
        original_unlink = Path.unlink

        def unlink_then_report_race(path, *args, **kwargs):
            original_unlink(path, *args, **kwargs)
            raise FileNotFoundError

        body = Body(stream=io.BytesIO(b"id\n1\n"), size=5)
        with mock.patch.object(Path, "unlink", unlink_then_report_race):
            body.save_atomic(target)

        self.assertEqual(target.read_bytes(), b"id\n1\n")

    def test_save_atomic_does_not_remove_an_unowned_part_file(self):
        target = self.config.paths.inbox / "owned.csv"
        part = target.with_name(target.name + ".part")
        part.write_bytes(b"otro escritor")
        body = Body(stream=io.BytesIO(b"id\n1\n"), size=5)
        with self.assertRaises(FileExistsError):
            body.save_atomic(target)
        self.assertEqual(part.read_bytes(), b"otro escritor")


class DatasetsEndpointTests(ApiTestBase):
    """Read-only access to published datasets and their manifests."""

    def publish(self) -> str:
        import pyarrow as pa

        from motor.publishing import publish

        table = pa.table({"id": [1], "v": ["a"]})
        manifest = publish.publish_run(
            table=table,
            schema_fingerprint="fp",
            dataset="demo",
            run_id="run-1",
            job_key="key-1",
            processed_dir=self.config.paths.processed,
            tmp_dir=self.config.paths.tmp / "run-1",
        )
        self.db().execute(
            "INSERT INTO datasets (run_id, job_key, dataset, manifest_path, committed_at) "
            "VALUES (?,?,?,?,?)",
            ("run-1", "key-1", "demo", str(manifest), 0),
        )
        return "run-1"

    def test_datasets_list_is_empty_initially(self):
        status, payload = self.call("GET", "/v1/datasets")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["datasets"], [])

    def test_datasets_list_reports_committed_datasets(self):
        self.publish()
        status, payload = self.call("GET", "/v1/datasets")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["count"], 1)
        entry = payload["data"]["datasets"][0]
        self.assertEqual(entry["run_id"], "run-1")
        self.assertEqual(entry["dataset"], "demo")
        self.assertNotIn("manifest_path", entry)
        self.assertNotIn(str(self.root), json.dumps(payload))

    def test_dataset_detail_reads_the_manifest(self):
        self.publish()
        status, payload = self.call("GET", "/v1/datasets/run-1")
        self.assertEqual(status, 200)
        data = payload["data"]
        self.assertEqual(data["rows"], 1)
        self.assertEqual(data["parts"][0]["name"], "part-000.parquet")
        self.assertNotIn(str(self.root), json.dumps(payload))

    def test_dataset_manifest_returns_the_raw_manifest(self):
        self.publish()
        status, payload = self.call("GET", "/v1/datasets/run-1/manifest")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["job_key"], "key-1")

    def test_dataset_detail_unknown_run_returns_not_found(self):
        status, payload = self.call("GET", "/v1/datasets/run-x")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], ErrorCode.NOT_FOUND)

    def test_dataset_detail_reports_a_missing_manifest_as_unavailable(self):
        self.db().execute(
            "INSERT INTO datasets (run_id, job_key, dataset, manifest_path, committed_at) "
            "VALUES (?,?,?,?,?)",
            ("run-gone", "key-gone", "demo", str(self.root / "gone" / "manifest.json"), 0),
        )
        status, payload = self.call("GET", "/v1/datasets/run-gone")
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], ErrorCode.UNAVAILABLE)


class ApiBoundaryTests(ApiTestBase):
    """The API must not expose internals (ADR-016 section 2.2)."""

    def test_reconcile_is_not_exposed(self):
        for method in ("GET", "POST"):
            status, payload = self.call(method, "/v1/reconcile")
            self.assertEqual(status, 404, method)
            self.assertEqual(payload["error"]["code"], ErrorCode.NOT_FOUND)

    def test_claim_lease_and_heartbeat_are_not_exposed(self):
        for path in ("/v1/jobs/job-1/claim", "/v1/jobs/job-1/lease",
                     "/v1/jobs/job-1/heartbeat", "/v1/pipelines"):
            status, payload = self.call("POST", path)
            self.assertEqual(status, 404, path)
            self.assertEqual(payload["error"]["code"], ErrorCode.NOT_FOUND)

    def test_health_exposes_no_filesystem_paths(self):
        _, payload = self.call("GET", "/v1/health")
        self.assertNotIn(str(self.root), json.dumps(payload))


class ApiCliTests(unittest.TestCase):
    """`yachay api` wiring (double, because serve_forever blocks)."""

    def test_parser_exposes_the_api_command(self):
        from motor.__main__ import build_parser

        args = build_parser().parse_args(
            ["api", "--root", "/tmp/x", "--socket", "/tmp/s.sock"]
        )
        self.assertEqual(args.command, "api")
        self.assertEqual(args.socket, "/tmp/s.sock")

    def test_main_dispatches_to_the_api_server(self):
        from unittest import mock

        from motor.__main__ import main

        with tempfile.TemporaryDirectory() as d:
            with mock.patch("motor.__main__.serve") as serve:
                code = main(["api", "--root", d, "--socket", str(Path(d) / "s.sock")])
        self.assertEqual(code, 0)
        serve.assert_called_once()


class ApiServiceLifecycleTests(unittest.TestCase):
    """The real service entrypoint, in a subprocess, stopped by a signal."""

    def _start(self, root: Path, socket_path: Path) -> subprocess.Popen:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "motor",
                "api",
                "--root",
                str(root),
                "--socket",
                str(socket_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if socket_path.exists():
                return process
            time.sleep(0.02)
        self.fail("the API socket never appeared")

    def test_sigterm_removes_the_socket(self):
        # A service manager stops the process with SIGTERM; the socket must not
        # be left behind, or the next start has to clean up after a "clean" stop.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            socket_path = root / "motor.sock"
            process = self._start(root, socket_path)
            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=10), 0)
            self.assertFalse(socket_path.exists())


class OpenApiContractTests(unittest.TestCase):
    """La especificación no puede divergir del código (API-04).

    `docs/api/openapi.yaml` es la fuente de contrato; estas comprobaciones la
    atan a las tablas de rutas, a los códigos de error y a los estados reales.
    Se saltan si PyYAML no está disponible: es una dependencia transitiva, no
    una del proyecto, y el gate no debe exigirla.
    """

    def setUp(self):
        try:
            import yaml  # noqa: F401
        except ImportError:  # pragma: no cover - depende del entorno
            raise unittest.SkipTest("PyYAML no está disponible") from None
        self.spec_path = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.yaml"
        if not self.spec_path.exists():  # pragma: no cover - documento ausente
            raise unittest.SkipTest("no existe docs/api/openapi.yaml")
        import yaml

        self.spec = yaml.safe_load(self.spec_path.read_text(encoding="utf-8"))

    def _spec_routes(self) -> set[tuple[str, str]]:
        return {
            (verb.upper(), path)
            for path, item in self.spec["paths"].items()
            for verb in item
            if verb in ("get", "post", "put", "patch", "delete")
        }

    def test_every_route_in_code_is_documented(self):
        from motor.api.routes import _DYNAMIC_ROUTES, _STATIC_ROUTES

        real = {(v, t) for (v, _p), (t, _h) in _STATIC_ROUTES.items()}
        real |= {(v, t) for v, t, _p, _h in _DYNAMIC_ROUTES}
        self.assertEqual(real, self._spec_routes())

    def test_error_codes_match_the_closed_set(self):
        from motor.api.envelope import _HTTP_STATUS

        documented = set(self.spec["components"]["schemas"]["ErrorCode"]["enum"])
        self.assertEqual(documented, set(_HTTP_STATUS))

    def test_job_states_match_the_engine(self):
        from motor.storage.schema import JOB_STATES

        documented = set(self.spec["components"]["schemas"]["JobState"]["enum"])
        self.assertEqual(documented, set(JOB_STATES))

    # No hay excepciones conocidas: las respuestas reales deben ajustarse
    # completamente al contrato OpenAPI.
    KNOWN_CONTRACT_GAPS: tuple[tuple[str, str], ...] = ()

    def _validate_known(self, schema: dict, value: dict) -> None:
        """Valida `value` admitiendo los desvíos declarados en `KNOWN_CONTRACT_GAPS`.

        Un envelope no toca todos los esquemas: `$.data.run_id` solo aparece en
        los que lo declaran. Por eso el desvío se reconoce cuando el error cae en
        su ruta; cualquier otro error se propaga intacto.
        """
        from tests.unit import openapi_min

        try:
            openapi_min.validate(self.spec, schema, value)
        except openapi_min.SpecError as problem:
            fields = [part.split(":", 1)[0].strip() for part in str(problem).split("; ")]
            unknown = [
                field for field in fields
                if not any(re.search(pattern, field) for pattern, _ in self.KNOWN_CONTRACT_GAPS)
            ]
            if not unknown:
                return
            raise

    def new_operation(
        self, verb: str, template: str, response: dict
    ) -> dict:
        """Valida el envelope 2xx real contra el esquema documentado."""
        from tests.unit import openapi_min

        self.assertTrue(response["ok"], response)
        schema = openapi_min.schema_for(self.spec, verb, template)
        self._validate_known(schema, response)
        return response["data"]

    def test_real_responses_match_the_documented_schemas(self):
        """H-04: la forma de la respuesta, no solo que la ruta exista.

        El contrato podía perder o renombrar un campo sin que la suite lo
        notara (`additionalProperties: false` no detecta una ausencia). Aquí se
        levanta el servidor real, se ejercita cada operación y se valida el
        envelope completo contra `docs/api/openapi.yaml`.
        """
        import tempfile
        import threading

        from motor.api.server import build_server
        from motor.config import MotorConfig

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = build_server(MotorConfig.from_root(root), root / "m.sock")
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
            )
            thread.start()
            try:
                self._exercise_contract(root / "m.sock")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)

    def _exercise_contract(self, socket_path: Path) -> None:
        import json
        import socket as _socket

        def call(verb: str, path: str, body: bytes | None = None) -> dict:
            sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            sock.settimeout(20)
            sock.connect(str(socket_path))
            lines = [f"{verb} {path} HTTP/1.1", "Host: localhost"]
            payload = body or b""
            if payload:
                lines.append(f"Content-Length: {len(payload)}")
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + payload)
            chunks = []
            while chunk := sock.recv(65536):
                chunks.append(chunk)
            sock.close()
            return json.loads(b"".join(chunks).partition(b"\r\n\r\n")[2].decode("utf-8"))

        def check(verb: str, template: str, response: dict) -> dict:
            return self.new_operation(verb, template, response)

        check("GET", "/v1/health", call("GET", "/v1/health"))
        check("POST", "/v1/inputs",
              call("POST", "/v1/inputs?name=d.csv", b"id,estado\n1,Hecho\n2,Lima\n"))
        check("GET", "/v1/profile", call("GET", "/v1/profile?name=d.csv"))
        model = check("POST", "/v1/models", call("POST", "/v1/models?name=m&source=d.csv"))
        model_id = model["model_id"]
        check("GET", "/v1/models", call("GET", "/v1/models"))
        check("GET", "/v1/models/{model_id}", call("GET", f"/v1/models/{model_id}"))
        check("POST", "/v1/models/{model_id}/rules", call(
            "POST", f"/v1/models/{model_id}/rules?name=r&type=filter"
                    "&column=estado&equals=Hecho"))
        check("GET", "/v1/models/{model_id}/rules",
              call("GET", f"/v1/models/{model_id}/rules"))
        first = check("POST", "/v1/models/{model_id}/runs",
                      call("POST", f"/v1/models/{model_id}/runs?source=d.csv"))
        second = check("POST", "/v1/models/{model_id}/runs",
                       call("POST", f"/v1/models/{model_id}/runs?source=d.csv"))
        check("GET", "/v1/models/{model_id}/runs",
              call("GET", f"/v1/models/{model_id}/runs"))
        check("GET", "/v1/diff", call(
            "GET", f"/v1/diff?before={first['run_id']}&after={second['run_id']}"))
        check("GET", "/v1/orphans", call("GET", "/v1/orphans?left=d.csv&right=d.csv&on=id"))
        check("GET", "/v1/jobs", call("GET", "/v1/jobs"))
        check("GET", "/v1/datasets", call("GET", "/v1/datasets"))

        # Operaciones que exigen estado del motor: su envelope de error se valida.
        for verb, path in (
            ("GET", "/v1/jobs/nope"),
            ("POST", "/v1/jobs/nope/cancel"),
            ("POST", "/v1/jobs/nope/retry"),
        ):
            response = call(verb, path)
            self.assertFalse(response["ok"], response)
            self._validate_known(
                self.spec["components"]["schemas"]["ErrorEnvelope"], response
            )

    def test_validator_rejects_what_it_cannot_check(self):
        """El validador falla ruidosamente ante algo que no sabe validar.

        Si el contrato usa un constructo nuevo, `openapi_min` debe parar en vez
        de dar por buena la respuesta: un validador que ignora lo que no entiende
        convierte el gate en decorado.
        """
        from tests.unit import openapi_min

        spec = {"components": {"schemas": {"S": {"type": "string"}}}}
        cases = {
            "tipo desconocido": ({"type": "date"}, "2026-01-01"),
            "ref externa": ({"$ref": "https://x/y.json"}, {}),
            "ref rota": ({"$ref": "#/components/schemas/Ausente"}, {}),
        }
        for label, (schema, value) in cases.items():
            with self.subTest(label):
                with self.assertRaises(openapi_min.SpecError):
                    openapi_min.validate(spec, schema, value)

        # Un `$ref` circular no puede colgar el gate.
        spec["components"]["schemas"]["A"] = {"$ref": "#/components/schemas/B"}
        spec["components"]["schemas"]["B"] = {"$ref": "#/components/schemas/A"}
        with self.assertRaises(openapi_min.SpecError):
            openapi_min.validate(spec, {"$ref": "#/components/schemas/A"}, {})

        # `True` es `int` en Python, pero no un entero JSON.
        with self.assertRaises(openapi_min.SpecError):
            openapi_min.validate(spec, {"type": "integer"}, True)
        with self.assertRaises(openapi_min.SpecError):
            openapi_min.validate(spec, {"type": "integer"}, "1")
        with self.assertRaises(openapi_min.SpecError):
            openapi_min.validate(spec, {"type": "string", "pattern": "^a"}, "b")
        with self.assertRaises(openapi_min.SpecError):
            openapi_min.validate(spec, {"enum": ["a"]}, "b")
        with self.assertRaises(openapi_min.SpecError):
            openapi_min.validate(spec, {"type": "string", "const": "a"}, "b")
        with self.assertRaises(openapi_min.SpecError):
            openapi_min.validate(spec, {"type": "object", "required": ["k"]}, {})
        # `additionalProperties: false` es lo que detecta un campo renombrado.
        with self.assertRaises(openapi_min.SpecError):
            openapi_min.validate(
                spec,
                {"type": "object", "properties": {"a": {}}, "additionalProperties": False},
                {"a": 1, "b": 2},
            )
        # `pattern` sin `type`: solo se aplica a texto.
        openapi_min.validate(spec, {"pattern": "^a"}, 1)      # no es texto: no aplica
        openapi_min.validate(spec, {"pattern": "^a"}, "abc")  # casa: pasa
        with self.assertRaises(openapi_min.SpecError):
            openapi_min.validate(spec, {"pattern": "^a"}, "b")  # no casa: falla
        # Un objeto sin restricciones declaradas es válido con cualquier forma.
        openapi_min.validate(spec, {"type": "object"}, {"libre": [1, 2]})
        # Varios desvíos de un mismo objeto se informan juntos.
        with self.assertRaises(openapi_min.SpecError) as both:
            openapi_min.validate(
                spec,
                {"type": "object", "properties": {"a": {"type": "string"},
                                                  "b": {"type": "integer"}}},
                {"a": 1, "b": "x"},
            )
        self.assertIn("$.a", str(both.exception))
        self.assertIn("$.b", str(both.exception))
        # `["string","null"]`: nulo pasa, el resto de restricciones no aplican.
        openapi_min.validate(
            spec, {"type": ["string", "null"], "pattern": "^a"}, None
        )
        # Operación con dos 2xx o sin `application/json` no es validable.
        spec["paths"] = {
            "/x": {"get": {"responses": {
                "200": {"content": {"application/json": {"schema": {}}}},
                "201": {"content": {"application/json": {"schema": {}}}},
            }}},
            "/y": {"get": {"responses": {"200": {"content": {}}}}},
        }
        with self.assertRaises(openapi_min.SpecError):
            openapi_min.schema_for(spec, "GET", "/x")
        with self.assertRaises(openapi_min.SpecError):
            openapi_min.schema_for(spec, "GET", "/y")

    def test_no_known_contract_gaps_remain(self):
        """Los desvíos históricos de tipos ya no se toleran como excepciones."""
        self.assertEqual(self.KNOWN_CONTRACT_GAPS, ())

    def test_every_operation_documents_a_success_code(self):
        for verb, path in self._spec_routes():
            codes = self.spec["paths"][path][verb.lower()]["responses"]
            self.assertTrue(
                any(str(code).startswith("2") for code in codes),
                f"{verb} {path} no declara un 2xx",
            )
