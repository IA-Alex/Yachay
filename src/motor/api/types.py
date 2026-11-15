"""Shared request/response types for the v1 API boundary."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from motor.config import MotorPaths

_BLOCK = 1024 * 1024


@dataclass
class Body:
    """Request body spooled to disk, so no handler holds it all in memory."""

    stream: BinaryIO
    size: int

    def save_atomic(self, target: Path) -> None:
        """Publish a complete upload atomically without replacing a file."""
        target = Path(target)
        part = target.with_name(target.name + ".part")
        owns_part = False
        try:
            descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            owns_part = True
            handle = os.fdopen(descriptor, "wb")
            with handle:
                self.stream.seek(0)
                for block in iter(lambda: self.stream.read(_BLOCK), b""):
                    handle.write(block)
                handle.flush()
                os.fsync(handle.fileno())
            # Hard-link creation is atomic and fails if target already exists.
            # Both paths are siblings, so they are on the same filesystem.
            os.link(part, target)
        finally:
            if owns_part:
                try:
                    part.unlink()
                except FileNotFoundError:
                    pass


@dataclass(frozen=True)
class Request:
    """Everything a handler may read from the request."""

    params: dict[str, str]
    query: dict[str, list[str]]
    body: Body
    headers: Mapping[str, str]


@dataclass(frozen=True)
class ApiContext:
    """The only state a handler may touch."""

    paths: MotorPaths
    connect: Callable[[], sqlite3.Connection]
    api_token: str | None = None


@dataclass(frozen=True)
class Response:
    """A routed response: HTTP status, envelope payload and route template."""

    status: int
    payload: dict
    route: str
