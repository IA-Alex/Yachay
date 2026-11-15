"""v1 HTTP API over a Unix domain socket (ADR-016).

Transport: stdlib `http.server` on an `AF_UNIX` socket. FastAPI is not a
dependency of this project and the target environment is offline, so an ASGI
stack would need a new dependency for the same contract. The socket is the only
listener: no TCP port, so the API is not reachable from the network.

The server is stateless. It opens one SQLite connection per request, touches no
lease, and never runs a pipeline; the worker stays the only process that
mutates job execution (ADR-014).
"""

from __future__ import annotations

import hmac
import json
import os
import signal
import socket
import socketserver
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from motor.api.envelope import ApiError, ErrorCode, error, status_for
from motor.api.routes import ApiContext, Body, dispatch
from motor.config import MotorConfig
from motor.limits import MAX_REQUEST_BYTES, MAX_REQUEST_DRAIN
from motor.storage.db import connect

# Group-writable socket: the operator and the service group share it.
SOCKET_MODE = 0o660

_SPOOL = 1024 * 1024
_REQUEST_TIMEOUT_SECONDS = 10


class UnixHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server bound to a Unix stream socket."""

    address_family = socket.AF_UNIX
    daemon_threads = True

    def server_bind(self) -> None:
        # `HTTPServer.server_bind` resolves the host name and stores a port,
        # which is meaningless for an AF_UNIX path; bind the path directly.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = 0

    def server_close(self) -> None:
        super().server_close()
        try:
            os.unlink(self.server_address)
        except FileNotFoundError:
            pass


class _Handler(BaseHTTPRequestHandler):
    """Translate one HTTP request into a `dispatch` call and an envelope."""

    protocol_version = "HTTP/1.1"
    server_version = "yachay-api/0.1"
    sys_version = ""

    def setup(self) -> None:
        """Bound idle request reads so a local client cannot hold a thread forever."""
        super().setup()
        self.connection.settimeout(_REQUEST_TIMEOUT_SECONDS)

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def do_OPTIONS(self) -> None:
        self._handle()

    def do_HEAD(self) -> None:
        self._handle()

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib hook
        """Silence the default stderr line; `_log` is the single log path."""

    def log_request(self, code="-", size="-") -> None:
        """Silence per-request stderr lines; `_log` records the structured one."""

    def _handle(self) -> None:
        started = time.monotonic()
        body = None
        route = "<unrouted>"
        self._status = 500
        try:
            expected_token = self.server.context.api_token
            if expected_token is not None and not hmac.compare_digest(
                self.headers.get("Authorization", ""), f"Bearer {expected_token}"
            ):
                raise ApiError(ErrorCode.UNAUTHORIZED, "autorización requerida")
            body = self._read_body()
            response = dispatch(
                self.server.context,
                method=self.command,
                target=self.path,
                body=body,
                headers=self.headers,
            )
            route = response.route
            # Emit the structured record before releasing the response to the
            # client. Otherwise a client can finish reading while the handler
            # thread still has not written the request log.
            self._status = response.status
            self._log(route, started)
            self._send(response.status, response.payload)
        except ApiError as exc:
            # `dispatch` stamps the resolved template onto the error, so a
            # failure is logged against its endpoint, not `<unrouted>`.
            route = exc.route
            status = status_for(exc.code)
            self._status = status
            self._log(route, started)
            self._send(status, error(exc.code, exc.message))
        except Exception:  # noqa: BLE001 - never leak internals to a client
            self._status = 500
            self._log(route, started)
            self._send(500, error(ErrorCode.INTERNAL_ERROR, "error interno"))
        finally:
            if body is not None:
                body.stream.close()

    def _read_body(self) -> Body:
        """Spool the body to disk, refusing anything above the hard bound."""
        raw = self.headers.get("Content-Length")
        if raw is None:
            return Body(stream=tempfile.SpooledTemporaryFile(max_size=_SPOOL), size=0)
        try:
            length = int(raw)
        except ValueError:
            raise ApiError(ErrorCode.BAD_REQUEST, "Content-Length no válido") from None
        if length < 0:
            raise ApiError(ErrorCode.BAD_REQUEST, "Content-Length no válido")
        stream = tempfile.SpooledTemporaryFile(max_size=_SPOOL)
        if length > MAX_REQUEST_BYTES:
            # Drain first: closing while the client is still writing would turn
            # the refusal into a broken pipe instead of a readable 413.
            self._drain(length)
            stream.close()
            raise ApiError(
                ErrorCode.PAYLOAD_TOO_LARGE,
                "el cuerpo de la petición es demasiado grande",
            )
        remaining = length
        while remaining:
            chunk = self.rfile.read(min(remaining, _SPOOL))
            if not chunk:
                break
            stream.write(chunk)
            remaining -= len(chunk)
        stream.seek(0)
        return Body(stream=stream, size=length - remaining)

    def _drain(self, length: int) -> None:
        remaining = min(length, MAX_REQUEST_DRAIN)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, _SPOOL))
            if not chunk:
                return
            remaining -= len(chunk)

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._status = status
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(data)

    def _log(self, route: str, started: float) -> None:
        """Emit one JSON line per request: route template, never a data value."""
        record = {
            "ts": round(time.time(), 3),
            "component": "api",
            "method": self.command,
            "route": route,
            "status": self._status,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
        }
        sys.stdout.write(json.dumps(record, sort_keys=True) + "\n")
        sys.stdout.flush()


class ApiServer(UnixHTTPServer):
    """`UnixHTTPServer` carrying the request context handlers read from."""

    context: ApiContext


def build_server(
    config: MotorConfig,
    socket_path: Path,
    *,
    api_token: str | None = None,
) -> ApiServer:
    """Create (but do not start) the API server bound to `socket_path`."""
    socket_path = Path(socket_path)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists() and socket_path.is_socket():
        # A socket file left behind by a dead process cannot be reused.
        socket_path.unlink()
    if api_token is None:
        api_token = os.environ.get("YACHAY_API_TOKEN") or None
    context = ApiContext(
        paths=config.paths,
        connect=lambda: connect(config.paths.sqlite_path),
        api_token=api_token,
    )
    server = ApiServer(str(socket_path), _Handler)
    server.context = context
    os.chmod(socket_path, SOCKET_MODE)
    return server


def serve(config: MotorConfig, socket_path: Path) -> None:  # pragma: no cover
    """Serve the v1 API until interrupted (service entrypoint).

    `SIGTERM` is translated into the same clean shutdown as `SIGINT`: a service
    manager stops the process with `SIGTERM`, and the default action would leave
    the socket file behind. The stale socket is replaced on the next start, but
    a clean stop should not leave one at all.
    """
    server = build_server(config, socket_path)

    def _terminate(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    installed = threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGTERM, _terminate) if installed else None
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if installed:
            signal.signal(signal.SIGTERM, previous)
        server.server_close()


