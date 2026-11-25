"""Input bounds and failure handling for the isolated local gateway."""

from __future__ import annotations

import io
import json
import os
import socketserver
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from motor import assistant
from motor import deepinfra_gateway as gateway


class Connection:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)


class GatewayHandlerTests(unittest.TestCase):
    def invoke(self, raw, proposal=None, error=None):
        handler = object.__new__(gateway._GatewayHandler)
        handler.connection = Connection()
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        with patch.object(assistant, "propose", return_value=proposal) as propose:
            if error is not None:
                propose.side_effect = error
            handler.handle()
        response = json.loads(handler.wfile.getvalue())
        return handler, response

    def test_handler_valid_and_invalid_requests(self):
        request = json.dumps({"instruction": "sum", "schema": []}).encode() + b"\n"
        handler, response = self.invoke(request, {"summary": "ok", "rules": []})
        self.assertEqual(response, {"proposal": {"summary": "ok", "rules": []}})
        self.assertEqual(handler.connection.timeouts, [5])
        invalids = (
            b"",
            b"{" * (gateway._MAX_REQUEST + 1),
            b'{"bad": 1}',
            b"{}\n",
            b'{"instruction":"x","schema":[]} garbage\n',
        )
        for raw in invalids:
            with self.subTest(length=len(raw)):
                _, response = self.invoke(raw)
                self.assertIn("error", response)

    def test_handler_sanitizes_provider_exceptions_and_reply_size(self):
        request = b'{"instruction":"x","schema":[]}\n'
        _, response = self.invoke(request, error=assistant.AssistantError("safe provider error"))
        self.assertEqual(response, {"error": "safe provider error"})
        _, response = self.invoke(request, error=RuntimeError("private detail"))
        self.assertEqual(response, {"error": "no se pudo generar la propuesta"})
        handler = object.__new__(gateway._GatewayHandler)
        handler.wfile = io.BytesIO()
        handler._reply({"value": "x" * gateway._MAX_RESPONSE})
        self.assertEqual(
            json.loads(handler.wfile.getvalue()), {"error": "respuesta demasiado grande"}
        )

    def test_thread_server_slot_rejection_and_release_paths(self):
        server = object.__new__(gateway._ThreadedUnixServer)
        request = object()
        slots = Mock()
        slots.acquire.return_value = False
        with (
            patch.object(server, "_slots", slots),
            patch.object(server, "shutdown_request") as stop,
        ):
            server.process_request(request, None)
        stop.assert_called_once_with(request)

        slots.acquire.return_value = True
        with (
            patch.object(server, "_slots", slots),
            patch.object(
                socketserver.ThreadingMixIn, "process_request", side_effect=RuntimeError("stop")
            ),
        ):
            with self.assertRaises(RuntimeError):
                server.process_request(request, None)
        slots.release.assert_called_once()

        slots.reset_mock()
        slots.acquire.return_value = True
        with (
            patch.object(server, "_slots", slots),
            patch.object(socketserver.ThreadingMixIn, "process_request") as parent,
        ):
            server.process_request(request, None)
            parent.assert_called_once_with(request, None)
            with patch.object(socketserver.ThreadingMixIn, "process_request_thread"):
                server.process_request_thread(request, None)
        slots.release.assert_called_once()

    def test_default_socket_path_needs_runtime_directory(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError):
            gateway.default_socket_path()
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/42"}):
            self.assertEqual(
                gateway.default_socket_path(),
                Path("/run/user/42/motor-deepinfra/deepinfra.sock"),
            )
