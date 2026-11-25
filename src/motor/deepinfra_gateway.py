"""Narrow local bridge from the network-isolated API to DeepInfra."""

from __future__ import annotations

import json
import os
import signal
import socket
import socketserver
import sys
import threading
from pathlib import Path

from motor import assistant

_MAX_REQUEST = 128 * 1024
_MAX_RESPONSE = 64 * 1024
_SOCKET_MODE = 0o660


class _GatewayHandler(socketserver.StreamRequestHandler):
    """Accept one bounded JSON request and return one sanitized response."""

    def handle(self) -> None:
        self.connection.settimeout(5)
        raw = self.rfile.readline(_MAX_REQUEST + 1)
        if not raw or len(raw) > _MAX_REQUEST or not raw.endswith(b"\n"):
            self._reply({"error": "solicitud inválida o demasiado grande"})
            return
        try:
            request = json.loads(raw)
            if not isinstance(request, dict) or set(request) != {"instruction", "schema"}:
                raise ValueError
            proposal = assistant.propose(request["instruction"], request["schema"])
            self._reply({"proposal": proposal})
        except assistant.AssistantError as exc:
            self._reply({"error": str(exc)[:300]})
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
            self._reply({"error": "solicitud inválida"})
        except Exception:  # noqa: BLE001 - keep provider and process details private
            self._reply({"error": "no se pudo generar la propuesta"})

    def _reply(self, value: dict) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(raw) > _MAX_RESPONSE:
            raw = b'{"error":"respuesta demasiado grande"}\n'
        self.wfile.write(raw)


class _ThreadedUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 8
    _slots = threading.BoundedSemaphore(4)

    def process_request(self, request: socket.socket, client_address: object) -> None:
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(
        self, request: socket.socket, client_address: object
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def default_socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise RuntimeError("XDG_RUNTIME_DIR no está definido")
    return Path(runtime) / "motor-deepinfra" / "deepinfra.sock"


def serve(socket_path: Path | None = None) -> None:  # pragma: no cover - service entrypoint
    """Serve only the fixed proposal operation over a local Unix socket."""
    configured_path = os.environ.get("YACHAY_DEEPINFRA_SOCKET")
    path = Path(socket_path or configured_path or default_socket_path())
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if path.exists():
        if not path.is_socket():
            raise RuntimeError("La ruta del adaptador existe y no es un socket")
        path.unlink()
    server = _ThreadedUnixServer(str(path), _GatewayHandler)
    os.chmod(path, _SOCKET_MODE)

    def _terminate(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    previous = signal.signal(signal.SIGTERM, _terminate)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        signal.signal(signal.SIGTERM, previous)
        server.server_close()
        sys.stdout.write('{"component":"deepinfra-adapter","event":"stopped"}\n')
        sys.stdout.flush()
