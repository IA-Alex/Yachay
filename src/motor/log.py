"""Minimal structured logging for the worker (spec section 9, ADR-018).

One JSON object per line on stdout, so journald captures it verbatim. It never
logs data values: only identifiers, states, counts and durations. The API logs
requests in the same shape (`{"component": "api", ...}`), so a single query over
journald sees both.

Nothing here depends on the logging module configuration: the worker prints one
line per cycle and per job, and flushes after each, so nothing is lost when the
process is stopped.
"""

from __future__ import annotations

import json
import sys
import time
from typing import IO, Any

# Keys that are always present, in a stable order, for log parsing.
_RESERVED = ("ts", "component", "event")


def emit(event: str, *, stream: IO[str] | None = None, **fields: Any) -> str:
    """Write one JSON line for `event` and return it.

    `fields` must contain only non-sensitive values: identifiers, states, counts,
    durations. Callers must never pass data values (cells, file contents).
    """
    record: dict[str, Any] = {
        "ts": time.time(),
        "component": "worker",
        "event": event,
    }
    record.update(fields)
    line = json.dumps(record, ensure_ascii=False, sort_keys=False)
    out = stream if stream is not None else sys.stdout
    out.write(line + "\n")
    out.flush()
    return line
