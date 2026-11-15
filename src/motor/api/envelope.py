"""Response envelope and closed error set of the v1 API (ADR-016).

The envelope is the only shape the API returns: `{api_version, ok, data}` on
success and `{api_version, ok, error}` on failure. Error codes are a closed set,
so a client branches on the code and never parses the message.
"""

from __future__ import annotations

API_VERSION = "v1"


class ErrorCode:
    """Closed set of API error codes (ADR-016, section 2.4)."""

    BAD_REQUEST = "bad_request"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    INVALID_STATE = "invalid_state"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    INTERNAL_ERROR = "internal_error"
    UNAVAILABLE = "unavailable"

    @classmethod
    def codes(cls) -> tuple[str, ...]:
        """El conjunto cerrado, en el orden de este contrato.

        Fuente única para consumidores que necesiten enumerarlo (el cliente de
        escritorio deriva de aquí sus etiquetas) sin copiar la lista a mano.
        """
        return tuple(
            value
            for key, value in vars(cls).items()
            if not key.startswith("_") and isinstance(value, str)
        )


_HTTP_STATUS = {
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.INVALID_STATE: 409,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.UNAVAILABLE: 503,
}


class ApiError(Exception):
    """Raised by a handler to produce a closed-set error envelope.

    `route` is the route template the request resolved to, when resolution got
    that far. It exists so the request log can name the endpoint even on a
    failure: an error raised after routing used to be logged as `<unrouted>`,
    which made per-route error aggregation impossible (ADR-016 section 15).
    """

    def __init__(self, code: str, message: str, route: str = "<unrouted>"):
        super().__init__(message)
        self.code = code
        self.message = message
        self.route = route


def status_for(code: str) -> int:
    """Return the HTTP status bound to an error code."""
    return _HTTP_STATUS[code]


def ok(data) -> dict:
    """Build a success envelope."""
    return {"api_version": API_VERSION, "ok": True, "data": data}


def error(code: str, message: str) -> dict:
    """Build a failure envelope; `code` must belong to the closed set."""
    if code not in _HTTP_STATUS:
        raise ValueError(f"unknown error code: {code}")
    return {
        "api_version": API_VERSION,
        "ok": False,
        "error": {"code": code, "message": message},
    }
