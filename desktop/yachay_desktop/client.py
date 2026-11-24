"""Cliente de la API v1 de Yachay sobre su socket Unix.

Contrato (extraído de `motor/api/envelope.py` y `motor/api/routes.py`):

- Envelope de éxito:  ``{"api_version": "v1", "ok": true,  "data": {...}}``
- Envelope de error:  ``{"api_version": "v1", "ok": false, "error": {"code", "message"}}``

Los códigos de error son un conjunto cerrado; este cliente ramifica por
``code`` y nunca por el texto.

El transporte es un socket Unix ``AF_UNIX`` con modo ``0660``. Este cliente NO
abre TCP: usa ``socket.AF_UNIX`` directamente, igual que el servidor.
"""

from __future__ import annotations

import json
import os
import re
import socket
from dataclasses import dataclass
from http.client import HTTPConnection
from pathlib import Path
from typing import Any
from urllib.parse import quote

from motor.api.envelope import ErrorCode

# Alfabeto de nombres que la API acepta (ADR-016 §12, `motor/api/routes.py`
# `_SAFE_NAME`). Se replica aquí para rechazar en la UI, con un mensaje que
# explica qué está mal, lo que el servidor rechazaría con un 400 genérico: un
# nombre con espacio o acento no tiene forma de viajar intacto en una query
# string ni en una cabecera HTTP sin codificaciones que el servidor no decodifica.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")

# Etiquetas humanas de los códigos de error. El CONJUNTO no se copia: se lee de
# `motor.api.envelope.ErrorCode`, la fuente única del contrato, para que un
# código nuevo en el servidor no aparezca en la UI sin traducción ni uno retirado
# se siga mostrando. Esta tabla solo añade texto; no define qué códigos existen.
ERROR_LABELS = {
    "bad_request": "Solicitud inválida",
    "not_found": "No encontrado",
    "method_not_allowed": "Método no permitido",
    "invalid_state": "Estado no válido para la acción",
    "payload_too_large": "El archivo es demasiado grande",
    "unauthorized": "Autorización requerida",
    "internal_error": "Error interno del motor",
    "unavailable": "El servicio no está disponible",
}

ERROR_CODES: dict[str, str] = {
    code: ERROR_LABELS.get(code, code) for code in ErrorCode.codes()
}


class ApiError(Exception):
    """Error de la API con su código cerrado y su mensaje."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def label(self) -> str:
        """Etiqueta humana en español para el código de error."""
        return ERROR_CODES.get(self.code, self.code)

    @property
    def is_known(self) -> bool:
        """`False` si el código no pertenece al conjunto cerrado del proveedor."""
        return self.code in ERROR_CODES


class ApiUnreachable(Exception):
    """El socket no existe o la conexión falló (equivale a un 503 local)."""


def check_name(name: str) -> None:
    """Rechaza en la UI un nombre que la API no admite (ADR-016 §12).

    El servidor lo rechazaría igual con un 400 genérico, pero aquí el mensaje
    dice qué corregir. Se valida el nombre tal cual, sin normalizar: renombrar
    a escondidas sería subir un archivo distinto del que el usuario eligió.
    """
    if not _SAFE_NAME.fullmatch(name):
        raise ApiError(
            "bad_request",
            f"«{name}» no es un nombre admitido: usa solo letras sin tilde, "
            "dígitos, punto, guion o guion bajo, y empieza por letra o dígito.",
        )


@dataclass(frozen=True)
class Job:
    """Un trabajo de la cola."""

    job_id: str
    state: str
    attempt: int
    created_at: float | None
    started_at: float | None
    cancel_requested: bool
    deferred_reason: str | None
    run_id: str | None = None


@dataclass(frozen=True)
class Dataset:
    """Un dataset publicado en el catálogo."""

    run_id: str
    dataset: str
    committed_at: float | None
    manifest_present: bool


class _UnixHTTPConnection(HTTPConnection):
    """HTTP sobre un socket Unix, con `http.client` de la biblioteca estándar.

    El socket es propiedad del proceso; no hay red involucrada.
    """

    def __init__(self, socket_path: str, timeout: float = 5.0):
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:  # type: ignore[override]
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._socket_path)


class ApiClient:
    """Cliente de solo lectura + tres acciones acotadas (ADR-016)."""

    def __init__(
        self, socket_path: Path, *, timeout: float = 5.0, api_token: str | None = None
    ):
        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self.api_token = (
            api_token if api_token is not None else os.environ.get("YACHAY_API_TOKEN") or None
        )

    # -- transporte ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        if not self.socket_path.exists():
            raise ApiUnreachable(
                f"El socket no existe: {self.socket_path}\n"
                "¿Está el motor en ejecución? Inícialo con: yachay api"
            )
        conn = _UnixHTTPConnection(str(self.socket_path), timeout=self.timeout)
        try:
            request_headers = dict(headers or {})
            has_authorization = any(
                key.lower() == "authorization" for key in request_headers
            )
            if self.api_token and not has_authorization:
                request_headers["Authorization"] = f"Bearer {self.api_token}"
            conn.request(method, _quote_target(path), body=body, headers=request_headers)
            response = conn.getresponse()
            raw = response.read()
        except (TimeoutError, OSError) as exc:
            raise ApiUnreachable(f"No se pudo hablar con el motor: {exc}") from exc
        except (ValueError, UnicodeError) as exc:
            # `http.client` rechaza una URL o una cabecera que no puede enviar en
            # ASCII (`InvalidURL`, `UnicodeEncodeError`). Sin este mapeo la
            # excepción escape `ApiError`/`ApiUnreachable` y revienta el hilo de
            # la vista sin mensaje: el fallo es del nombre que se pidió, no del
            # motor, así que viaja como `bad_request`.
            raise ApiError("bad_request", f"la petición no se pudo codificar: {exc}") from exc
        finally:
            conn.close()

        if not raw:
            # El servidor envuelve SIEMPRE en `{api_version, ok, data|error}` y
            # cada respuesta lleva `Content-Length` (server.py `_send`): nunca
            # emite 204 ni cuerpo vacío. Un vacío es una respuesta truncada o un
            # intermediario que rompió el contrato, no un éxito: devolverlo como
            # `None` disfrazaba el fallo (p. ej. `health()` reventaba después).
            raise ApiError("internal_error", "el motor respondió sin cuerpo")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError("internal_error", "Respuesta ilegible del motor") from exc

        if payload.get("ok") is True:
            return payload.get("data")
        error = payload.get("error") or {}
        raise ApiError(error.get("code", "internal_error"), error.get("message", ""))

    # -- lecturas -----------------------------------------------------------

    def health(self) -> dict:
        """`GET /v1/health` → {status, api_version, database}."""
        return self._request("GET", "/v1/health")

    def jobs(self, *, state: str | None = None, limit: int = 50) -> list[Job]:
        """`GET /v1/jobs` → lista de trabajos."""
        query = [f"limit={int(limit)}"]
        if state:
            query.append(f"state={state}")
        data = self._request("GET", "/v1/jobs?" + "&".join(query))
        return [_job_from(row) for row in data.get("jobs", [])]

    def job(self, job_id: str) -> Job:
        """`GET /v1/jobs/{job_id}` → un trabajo, con `run_id`."""
        return _job_from(self._request("GET", f"/v1/jobs/{job_id}"))

    def datasets(self, *, limit: int = 50) -> list[Dataset]:
        """`GET /v1/datasets` → catálogo de datasets."""
        data = self._request("GET", f"/v1/datasets?limit={int(limit)}")
        return [_dataset_from(row) for row in data.get("datasets", [])]

    def dataset(self, run_id: str) -> dict:
        """`GET /v1/datasets/{run_id}` → manifest de un dataset."""
        return self._request("GET", f"/v1/datasets/{quote(run_id, safe='')}")

    def manifest(self, run_id: str) -> dict:
        """`GET /v1/datasets/{run_id}/manifest` → el manifest JSON."""
        return self._request("GET", f"/v1/datasets/{run_id}/manifest")

    # -- acciones -----------------------------------------------------------

    def cancel(self, job_id: str) -> dict:
        """`POST /v1/jobs/{job_id}/cancel` — cancelación cooperativa."""
        return self._request("POST", f"/v1/jobs/{job_id}/cancel")

    def retry(self, job_id: str) -> dict:
        """`POST /v1/jobs/{job_id}/retry` — reencola un trabajo terminal."""
        return self._request("POST", f"/v1/jobs/{job_id}/retry")

    def upload(self, name: str, data: bytes) -> dict:
        """`POST /v1/inputs?name=` — deposita un CSV en la bandeja `inbox/`.

        No crea un trabajo: el escáner del motor lo admitirá en su ciclo.

        El nombre viaja codificado en la query. Antes iba en crudo y un `&` lo
        partía (`a&b.csv` se guardaba como `a`, con un 201 que mentía), mientras
        un espacio o un acento morían en `http.client` antes de salir.
        """
        check_name(name)
        return self._request(
            "POST",
            f"/v1/inputs?name={quote(name, safe='')}",
            body=data,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(data)),
                "X-Yachay-Filename": name,
            },
        )

    # -- modelado (ADR-019, M6) --------------------------------------------

    def models(self) -> list[dict]:
        """`GET /v1/models` → modelos guardados (resumen)."""
        data = self._request("GET", "/v1/models")
        return data.get("models", [])

    def model(self, model_id: int) -> dict:
        """`GET /v1/models/{id}` → detalle del modelo."""
        return self._request("GET", f"/v1/models/{model_id}")

    def create_model(self, name: str, source: str) -> dict:
        """`POST /v1/models?name=&source=` → crea un modelo desde un archivo."""
        return self._request(
            "POST", f"/v1/models?name={quote(name, safe='')}&source={quote(source, safe='')}"
        )

    def profile(self, name: str) -> dict:
        """`GET /v1/profile?name=` → perfil de un archivo de la bandeja."""
        return self._request("GET", f"/v1/profile?name={quote(name, safe='')}")

    def runs(self, model_id: int) -> list[dict]:
        """`GET /v1/models/{id}/runs` → ejecuciones del modelo."""
        data = self._request("GET", f"/v1/models/{model_id}/runs")
        return data.get("runs", [])

    def execute(self, model_id: int, source: str | list[str]) -> dict:
        """`POST /v1/models/{id}/runs?source=`; repetir `source` habilita append.

        Una cadena conserva compatibilidad con las llamadas existentes.
        """
        sources = [source] if isinstance(source, str) else list(source)
        query = "&".join(
            f"source={quote(item)}" for item in sources
        )
        return self._request("POST", f"/v1/models/{model_id}/runs?{query}")

    def generate_report(self, model_id: int, run_id: int) -> dict:
        """Generate a local self-contained visual report for a stored run."""
        return self._request("POST", f"/v1/models/{model_id}/runs/{run_id}/report")

    def delete_report(self, model_id: int, run_id: int) -> dict:
        """Delete the report file while retaining its execution data."""
        return self._request("DELETE", f"/v1/models/{model_id}/runs/{run_id}/report")

    def propose_model_rules(self, model_id: int, source: str, instruction: str) -> dict:
        """Ask the bounded assistant for a preview; only schema/profile is sent out."""
        body = json.dumps({"source": source, "instruction": instruction}).encode("utf-8")
        return self._request(
            "POST", f"/v1/models/{model_id}/assistant/proposals", body=body,
            headers={"Content-Type": "application/json"},
        )

    def apply_model_proposal(self, model_id: int, proposal: dict) -> dict:
        """Accept a reviewed proposal into a new, non-executed model version."""
        body = json.dumps({"proposal": proposal}, ensure_ascii=False).encode("utf-8")
        return self._request(
            "POST", f"/v1/models/{model_id}/assistant/apply", body=body,
            headers={"Content-Type": "application/json"},
        )

    def diff(self, before: int, after: int) -> dict:
        """`GET /v1/diff?before=&after=` → compara dos ejecuciones."""
        return self._request("GET", f"/v1/diff?before={before}&after={after}")

    # -- reglas del modelo (ADR-019, M3) -----------------------------------

    def rules(self, model_id: int) -> list[dict]:
        """`GET /v1/models/{id}/rules` → reglas del modelo en orden de aplicación."""
        data = self._request("GET", f"/v1/models/{model_id}/rules")
        return data.get("rules", [])

    def create_rule(
        self,
        model_id: int,
        *,
        name: str,
        type: str,  # noqa: A002 - nombre del parámetro del contrato
        column: str,
        to: str | None = None,
        values: dict | None = None,
        allowed: list | None = None,
        equals: str | None = None,
    ) -> dict:
        """`POST /v1/models/{id}/rules` → añade una regla con nombre (M3).

        Los parámetros viajan por query (ADR-016 §10: sin cuerpo JSON). Solo se
        envía el `param` que el tipo define, igual que el proveedor: una clave
        ajena no se manda. Los valores se codifican con `safe=''`: `quote` deja
        `&` intacto por defecto y un nombre como `a&b` partía la query.
        """
        query = [f"name={quote(name, safe='')}", f"type={quote(type, safe='')}",
                 f"column={quote(column, safe='')}"]
        if type == "rename" and to is not None:
            query.append(f"to={quote(to, safe='')}")
        elif type == "cast":
            query.append(f"to={quote(to or 'text', safe='')}")
        elif type == "map" and values is not None:
            query.append(f"values={quote(json.dumps(values), safe='')}")
        elif type == "domain" and allowed is not None:
            query.append(f"allowed={quote(json.dumps(allowed), safe='')}")
        elif type == "filter" and equals is not None:
            query.append(f"equals={quote(equals, safe='')}")
        return self._request("POST", f"/v1/models/{model_id}/rules?" + "&".join(query))

    def delete_rule(self, model_id: int, name: str) -> dict:
        """`DELETE /v1/models/{id}/rules/{name}` → borra una regla por nombre.

        El nombre va en la ruta, no en la query: con `safe=''` codifica `#`, `?`
        y el espacio, que romperían la ruta o la cortarían en un fragmento;
        `quote` por defecto dejaba pasar `#`.
        """
        return self._request(
            "DELETE", f"/v1/models/{model_id}/rules/{quote(name, safe='')}"
        )

    def orphans(self, left: str, right: str, on: str, right_on: str | None = None) -> dict:
        """`GET /v1/orphans` → informe de huérfanos entre dos entradas."""
        query = (f"left={quote(left, safe='')}&right={quote(right, safe='')}"
                 f"&on={quote(on, safe='')}")
        if right_on:
            query += f"&right_on={quote(right_on, safe='')}"
        return self._request("GET", f"/v1/orphans?{query}")


def _quote_target(target: str) -> str:
    """Codifica una vez el valor de cada parámetro de la query y lo deja intacto.

    `target` se arma con valores ya pasados por `quote(..., safe='')` (por eso
    los `%` se preservan), así que lo único que queda por codificar son los
    caracteres que `http.client` rechaza antes de enviar: espacio y todo lo que
    no es ASCII imprimible. `+` se preserva porque `parse_qs` del servidor lo
    decodifica a espacio y, cuando viaja dentro de un valor JSON ya codificado,
    forma parte de la representación.

    Sin esto, `POST /v1/inputs?name=mi informe.csv` moría con `InvalidURL` antes
    de llegar al motor, y `name=a&b.csv` partía el parámetro en dos.
    """
    path, sep, raw_query = target.partition("?")
    if not sep:
        return path
    pairs = [pair.partition("=")[::2] for pair in raw_query.split("&")]
    return path + "?" + "&".join(
        f"{key}={quote(value, safe='%+', encoding='utf-8')}" for key, value in pairs
    )


def _job_from(row: dict) -> Job:
    return Job(
        job_id=row.get("job_id", ""),
        state=row.get("state", ""),
        attempt=int(row.get("attempt") or 0),
        created_at=row.get("created_at"),
        started_at=row.get("started_at"),
        cancel_requested=bool(row.get("cancel_requested")),
        deferred_reason=row.get("deferred_reason"),
        run_id=row.get("run_id"),
    )


def _dataset_from(row: dict) -> Dataset:
    return Dataset(
        run_id=row.get("run_id", ""),
        dataset=row.get("dataset", ""),
        committed_at=row.get("committed_at"),
        manifest_present=bool(row.get("manifest_present")),
    )
