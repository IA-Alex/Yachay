"""v1 route table and handlers (ADR-016).

The API is a thin, stateless projection over SQLite and the filesystem: it
reads state, asks the job queue to flip a flag, and publishes inputs for the
worker in `inbox/` while retaining a hard-linked source for model operations.
It never reconciles, never claims a lease, never runs a pipeline, and never
returns filesystem paths or data values (ADR-016, section 2.2).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, unquote

from motor.api.envelope import API_VERSION, ApiError, ErrorCode, ok
from motor.api.types import ApiContext, Body, Request, Response
from motor.storage import jobs
from motor.storage.schema import JOB_STATES

# A plain file name: no separators, no leading dot, no traversal.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")

# States the API may act on. `cancel` is cooperative, so it is only meaningful
# while the job can still run; `retry` is the explicit §7 retry path.
CANCELLABLE_STATES = ("claimed", "queued", "running", "deferred")
RETRYABLE_STATES = ("failed", "quarantined", "cancelled")

DEFAULT_LIMIT = 50
MAX_LIMIT = 500

_JOB_COLUMNS = (
    "job_id, state, attempt, created_at, started_at, cancel_requested, deferred_reason"
)



def _one(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _json_param(
    query: dict[str, list[str]], key: str, default
):
    """Lee un parámetro de query cuyo valor es JSON (dict o lista).

    ADR-016 §10 no admite cuerpo JSON: los `params` de una regla viajan como
    valores JSON codificados en la query. Ausente → `default`; mal formado o de
    tipo distinto → 400, para que un error de codificación no se guarde como una
    regla silenciosamente equivocada.
    """
    raw = _one(query, key)
    if raw is None:
        return default
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(
            ErrorCode.BAD_REQUEST, f"`{key}` debe ser JSON válido"
        ) from None
    if not isinstance(value, type(default)):
        raise ApiError(
            ErrorCode.BAD_REQUEST, f"`{key}` debe ser un {type(default).__name__}"
        )
    return value


def _limit(query: dict[str, list[str]]) -> int:
    raw = _one(query, "limit")
    if raw is None:
        return DEFAULT_LIMIT
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(ErrorCode.BAD_REQUEST, "`limit` debe ser un entero") from None
    if value < 1 or value > MAX_LIMIT:
        raise ApiError(ErrorCode.BAD_REQUEST, f"`limit` debe estar entre 1 y {MAX_LIMIT}")
    return value


def _audit(conn: sqlite3.Connection, action: str, ref_id: str) -> None:
    conn.execute(
        "INSERT INTO audit_events (at, action, ref_id, ref_hash) "
        "VALUES (COALESCE(strftime('%s','now'),0), ?, ?, '')",
        (action, ref_id),
    )


@contextmanager
def _transaction(conn: sqlite3.Connection):
    """Run the block as one write transaction (the ADR-014 pattern).

    Any failure rolls the block back, so a refused request leaves no partial
    write behind. The connection uses `isolation_level=None`, hence the explicit
    `BEGIN IMMEDIATE`: it takes the write lock up front instead of failing on the
    first upgrade attempt.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _job_row(row) -> dict:
    job_id, state, attempt, created_at, started_at, cancel_requested, reason = row
    return {
        "job_id": job_id,
        "state": state,
        "attempt": attempt,
        "created_at": created_at,
        "started_at": started_at,
        "cancel_requested": bool(cancel_requested),
        "deferred_reason": reason,
    }


def _health(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Report whether the service can read its authority.

    A database that does not answer is `unavailable` (503), not an internal
    error: the client cannot fix it, but the operator must be able to tell the
    two apart when probing health.
    """
    try:
        conn = ctx.connect()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ApiError(ErrorCode.UNAVAILABLE, "la base de datos no está disponible") from exc
    return 200, {"status": "ok", "api_version": API_VERSION, "database": "ok"}


def _jobs_list(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    state = _one(request.query, "state")
    if state is not None and state not in JOB_STATES:
        raise ApiError(ErrorCode.BAD_REQUEST, "estado desconocido en el filtro")
    limit = _limit(request.query)
    sql = f"SELECT {_JOB_COLUMNS} FROM jobs"
    args: list = []
    if state:
        sql += " WHERE state = ?"
        args.append(state)
    sql += " ORDER BY created_at DESC, job_id LIMIT ?"
    args.append(limit)
    conn = ctx.connect()
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    jobs_ = [_job_row(row) for row in rows]
    return 200, {"jobs": jobs_, "count": len(jobs_)}


def _job_detail(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    job_id = request.params["job_id"]
    conn = ctx.connect()
    try:
        row = conn.execute(
            f"SELECT {_JOB_COLUMNS}, run_id FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "trabajo no encontrado")
    data = _job_row(row[:7])
    data["run_id"] = row[7]
    return 200, data


def _job_cancel(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    job_id = request.params["job_id"]
    conn = ctx.connect()
    try:
        with _transaction(conn):
            row = conn.execute(
                "SELECT state FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise ApiError(ErrorCode.NOT_FOUND, "trabajo no encontrado")
            if row[0] not in CANCELLABLE_STATES:
                raise ApiError(
                    ErrorCode.INVALID_STATE, f"un trabajo en estado «{row[0]}» no se puede cancelar"
                )
            jobs.request_cancel(conn, job_id)
    finally:
        conn.close()
    return 200, {"job_id": job_id, "cancel_requested": True}


def _job_retry(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Requeue a terminal job with a fresh attempt budget (spec §7, ADR-016).

    `attempt` resets to 0 because this is an explicit operator decision, not the
    automatic recovery of ADR-014: the retry must be able to succeed even when
    the job already exhausted `MAX_ATTEMPTS`.
    """
    job_id = request.params["job_id"]
    conn = ctx.connect()
    try:
        with _transaction(conn):
            row = conn.execute(
                "SELECT state FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise ApiError(ErrorCode.NOT_FOUND, "trabajo no encontrado")
            if row[0] not in RETRYABLE_STATES:
                raise ApiError(
                    ErrorCode.INVALID_STATE,
                    f"un trabajo en estado «{row[0]}» no se puede reintentar",
                )
            source = conn.execute(
                "SELECT path FROM input_files WHERE job_id = ?", (job_id,)
            ).fetchone()
            if source is None or not Path(source[0]).exists():
                raise ApiError(
                    ErrorCode.INVALID_STATE,
                    "el archivo de entrada de este trabajo no es recuperable",
                )
            # The state guard is repeated on the write (not just the read above):
            # the worker can commit the job between both statements, and a retry
            # must never move a job that already reached a terminal state back to
            # `queued` (ADR-016, section 9).
            placeholders = ", ".join("?" for _ in RETRYABLE_STATES)
            cursor = conn.execute(
                "UPDATE jobs SET state = 'queued', attempt = 0, run_id = NULL, "
                "worker_id = NULL, lease_until = NULL, cancel_requested = 0 "
                f"WHERE job_id = ? AND state IN ({placeholders})",
                (job_id, *RETRYABLE_STATES),
            )
            if cursor.rowcount == 0:
                raise ApiError(
                    ErrorCode.INVALID_STATE,
                    "el trabajo cambió de estado y ya no se puede reintentar",
                )
            _audit(conn, "api_retry_job", job_id)
    finally:
        conn.close()
    return 200, {"job_id": job_id, "state": "queued"}


def _inputs_upload(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Retain an uploaded input and publish its worker-facing inbox link.

    The API never creates a job: the worker's scanner admits the file on its own
    cycle, so there is exactly one admission path (ADR-008) and the API cannot
    bypass admission, identity or the `job_key`.
    """
    name = _one(request.query, "name") or request.headers.get("X-Yachay-Filename")
    if not name:
        raise ApiError(
            ErrorCode.BAD_REQUEST, "se requiere un nombre de archivo en ?name= o X-Yachay-Filename"
        )
    if not _SAFE_NAME.match(name):
        raise ApiError(ErrorCode.BAD_REQUEST, "el nombre debe ser un nombre de archivo simple")
    target = ctx.paths.inbox / name
    model_target = ctx.paths.model_inputs / name
    if target.exists() or model_target.exists():
        raise ApiError(ErrorCode.INVALID_STATE, "ya existe una entrada con ese nombre")
    if request.body.size == 0:
        # An empty file would be admitted by the scanner and fail later in the
        # pipeline, spending an attempt on an avoidable error. Refuse it at the
        # boundary, where the client can still act on the message (ADR-016 §10).
        raise ApiError(ErrorCode.BAD_REQUEST, "el archivo está vacío")
    try:
        # Keep the inbox's existing per-name upload lock. The source is staged
        # elsewhere, but concurrent/external writers still coordinate here.
        inbox_part = target.with_name(target.name + ".part")
        lock_fd = os.open(
            inbox_part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640
        )
        os.close(lock_fd)
        # Keep the model input available after the worker consumes inbox/.
        # inbox/ remains the sole job-admission path (ADR-008/016).
        try:
            request.body.save_atomic(model_target)
            # Same filesystem: one inode, so worker cleanup of inbox/ does not
            # remove the retained model input or duplicate its disk usage.
            os.link(model_target, target)
        except OSError:
            model_target.unlink(missing_ok=True)
            raise
        finally:
            inbox_part.unlink(missing_ok=True)
    except FileExistsError:
        raise ApiError(
            ErrorCode.INVALID_STATE, "hay otra subida de ese nombre en curso"
        ) from None
    return 201, {"accepted": True, "name": name, "size_bytes": request.body.size}


def _model_input_path(ctx: ApiContext, name: str) -> Path:
    """Resolve retained API uploads first, preserving legacy inbox inputs."""
    retained = ctx.paths.model_inputs / name
    return retained if retained.is_file() else ctx.paths.inbox / name


def _datasets_list(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    limit = _limit(request.query)
    conn = ctx.connect()
    try:
        rows = conn.execute(
            "SELECT run_id, dataset, committed_at, manifest_path FROM datasets "
            "ORDER BY committed_at DESC, run_id LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    items = [
        {
            "run_id": run_id,
            "dataset": dataset,
            "committed_at": committed_at,
            "manifest_present": Path(manifest_path).exists(),
        }
        for run_id, dataset, committed_at, manifest_path in rows
    ]
    return 200, {"datasets": items, "count": len(items)}


def _load_manifest(ctx: ApiContext, run_id: str) -> dict:
    """Return the committed manifest of a dataset, or a closed-set error.

    The manifest is the physical commit (ADR-014), so it is the only dataset
    artefact the API serves. A registered dataset whose manifest is gone is not
    a client error: the service cannot answer, hence 503.
    """
    conn = ctx.connect()
    try:
        row = conn.execute(
            "SELECT manifest_path FROM datasets WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "dataset no encontrado")
    path = Path(row[0])
    if not path.exists():
        raise ApiError(ErrorCode.UNAVAILABLE, "el manifest no está en disco")
    return json.loads(path.read_text(encoding="utf-8"))


def _dataset_detail(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    return 200, _load_manifest(ctx, request.params["run_id"])


def _dataset_manifest(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    return 200, _load_manifest(ctx, request.params["run_id"])


# -- modelado (ADR-019, M6) --------------------------------------------------


def _models_list(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Lista los modelos guardados."""
    from motor.model import store

    conn = ctx.connect()
    try:
        models = store.list_models(conn)
    finally:
        conn.close()
    return 200, {"models": [_model_summary(m) for m in models], "count": len(models)}


def _model_detail(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Detalle de un modelo por su identificador."""
    from motor.model import store

    model_id = request.params["model_id"]
    conn = ctx.connect()
    try:
        try:
            model = store.get(conn, int(model_id))
        except (store.ModelNotFound, ValueError):
            raise ApiError(ErrorCode.NOT_FOUND, "modelo no encontrado") from None
    finally:
        conn.close()
    return 200, _model_full(model)


def _model_create(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Crea un modelo perfilando una fuente cargada o legacy en `inbox/`."""
    from motor.model import infer, store
    from motor.sources import EmptySheet, SheetNotFound, profile_table, read_any
    from motor.sources.detect import UnsupportedFormat

    name = _one(request.query, "name")
    source = _one(request.query, "source")
    if not name or not source:
        raise ApiError(
            ErrorCode.BAD_REQUEST, "se requieren `name` y `source`"
        )
    if not _SAFE_NAME.match(source):
        raise ApiError(ErrorCode.BAD_REQUEST, "el nombre debe ser un nombre simple")

    path = _model_input_path(ctx, source)
    if not path.is_file():
        raise ApiError(ErrorCode.NOT_FOUND, f"no existe el archivo {source!r}")

    try:
        table = read_any(path)
        profiles = profile_table(table)
    except (UnsupportedFormat, SheetNotFound, EmptySheet) as exc:
        raise ApiError(ErrorCode.BAD_REQUEST, str(exc)) from None

    proposal = infer.entity_from_table(table, profiles, name=path.stem)

    conn = ctx.connect()
    try:
        model_id = store.create(conn, name=name)
        entity_id = store.add_entity(conn, model_id, name=proposal.name)
        for attribute in proposal.attributes:
            store.add_attribute(
                conn, entity_id, name=attribute.name,
                type=attribute.type, nullable=attribute.nullable,
            )
        if proposal.suggested_key:
            store.set_key(conn, entity_id, columns=proposal.suggested_key)
        model = store.get(conn, model_id)
    except store.DuplicateName as exc:
        raise ApiError(ErrorCode.INVALID_STATE, str(exc)) from None
    finally:
        conn.close()
    return 201, _model_full(model)


def _profile_input(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Perfila un archivo cargado sin crear nada."""
    from motor.sources import EmptySheet, SheetNotFound, profile_table, read_any
    from motor.sources.detect import UnsupportedFormat

    name = _one(request.query, "name")
    if not name:
        raise ApiError(ErrorCode.BAD_REQUEST, "se requiere `name`")
    if not _SAFE_NAME.match(name):
        raise ApiError(ErrorCode.BAD_REQUEST, "el nombre debe ser un nombre simple")

    path = _model_input_path(ctx, name)
    if not path.is_file():
        raise ApiError(ErrorCode.NOT_FOUND, f"no existe el archivo {name!r}")

    try:
        table = read_any(path)
        profiles = profile_table(table)
    except (UnsupportedFormat, SheetNotFound, EmptySheet) as exc:
        raise ApiError(ErrorCode.BAD_REQUEST, str(exc)) from None

    return 200, {
        "file": name,
        "columns": table.columns,
        "rows": len(table.rows),
        "note": table.note,
        "profile": [column.as_dict() for column in profiles],
    }


def _runs_list(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Lista las ejecuciones de un modelo (M6)."""
    from motor.model import run, store

    model_id = _int_param(request, "model_id")
    conn = ctx.connect()
    try:
        _require_model(conn, store, model_id)
        runs = run.list_for(conn, model_id)
    finally:
        conn.close()
    return 200, {
        "runs": [
            {
                "run_id": r.run_id,
                "created_at": r.created_at,
                "output": r.output,
                "rows": r.fingerprint.rows,
                "columns": r.fingerprint.columns,
                "hash": r.fingerprint.hash,
                # H-05: qué se aplicó al resultado; `None` = ejecución anterior
                # a la columna `lineage` (linaje desconocido).
                "lineage": r.lineage,
                "artifact_available": bool(
                    r.artifact_path
                    and (ctx.paths.model_outputs / r.artifact_path).is_file()
                ),
            }
            for r in runs
        ],
        "count": len(runs),
    }


def _run_create(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Ejecuta un modelo con una o varias fuentes y guarda la huella (B2/M6)."""
    from motor.model import engine, rules, run, store
    from motor.sources import EmptySheet, SheetNotFound, read_any
    from motor.sources.detect import UnsupportedFormat

    model_id = _int_param(request, "model_id")
    sources = request.query.get("source", [])
    if not sources or any(not source for source in sources):
        raise ApiError(
            ErrorCode.BAD_REQUEST, "se requiere al menos un parámetro `source` no vacío"
        )
    if len(sources) > 20:
        raise ApiError(
            ErrorCode.BAD_REQUEST, "se permiten como máximo 20 fuentes por ejecución"
        )
    if len(set(sources)) != len(sources):
        raise ApiError(ErrorCode.BAD_REQUEST, "no se permiten fuentes duplicadas")
    if any(not _SAFE_NAME.match(source) for source in sources):
        raise ApiError(ErrorCode.BAD_REQUEST, "cada fuente debe ser un nombre simple")

    conn = ctx.connect()
    try:
        model = _require_model(conn, store, model_id)
        if not model.entities:
            raise ApiError(ErrorCode.INVALID_STATE, "el modelo no tiene entidades")
        entity = model.entities[0]

        paths = [_model_input_path(ctx, source) for source in sources]
        for source, path in zip(sources, paths, strict=True):
            if not path.is_file():
                raise ApiError(ErrorCode.NOT_FOUND, f"no existe el archivo {source!r}")

        tables = []
        try:
            for path in paths:
                tables.append(read_any(path))
        except (UnsupportedFormat, SheetNotFound, EmptySheet) as exc:
            raise ApiError(ErrorCode.BAD_REQUEST, str(exc)) from None

        if len(tables) > 1:
            try:
                input_table = engine.append_tables(tables)
            except engine.EngineError as exc:
                raise ApiError(ErrorCode.BAD_REQUEST, str(exc)) from None
        else:
            input_table = tables[0]

        plan = rules.list_for(conn, model_id)
        try:
            result = run.execute(
                {entity.name: input_table},
                rules_by_source={entity.name: plan},
                join_plan=[],
                output=entity.name,
            )
        except run.RunError as exc:
            raise ApiError(ErrorCode.BAD_REQUEST, str(exc)) from None

        source_hashes = []
        for source, path in zip(sources, paths, strict=True):
            with path.open("rb") as source_file:
                source_hashes.append(
                    {
                        "name": source,
                        "sha256": hashlib.file_digest(source_file, "sha256").hexdigest(),
                    }
                )
        if len(source_hashes) == 1:
            source_hash = source_hashes[0]["sha256"]
            stored_source_name = sources[0]
        else:
            source_hash = hashlib.sha256(
                json.dumps(
                    source_hashes, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            stored_source_name = json.dumps(sources, ensure_ascii=False)
        lineage = run.describe_lineage(
            output=entity.name, rules_applied=plan, table=result,
            model_version=model.version,
            sources=source_hashes,
            parameters={"operation": "append" if len(sources) > 1 else "full"},
        )
        if len(sources) > 1:
            lineage["operation"] = "append"
        run_id = run.record(
            conn,
            model_id,
            table=result,
            output=entity.name,
            artifact_dir=ctx.paths.model_outputs,
            source_name=stored_source_name,
            source_sha256=source_hash,
            lineage=lineage,
        )
        fp = run.fingerprint(result)
    finally:
        conn.close()
    return 201, {
        "run_id": run_id,
        "model_id": model_id,
        "columns": fp.columns,
        "rows_out": fp.rows,
        "hash": fp.hash,
        "sources": sources,
        "operation": "append" if len(sources) > 1 else "single_source",
    }


def _report_generate(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Create or regenerate an offline HTML report for one persisted run."""
    import pyarrow as pa

    from motor import dashboard
    from motor.model import run, store

    model_id = _int_param(request, "model_id")
    run_id = _int_param(request, "run_id")
    conn = ctx.connect()
    try:
        _require_model(conn, store, model_id)
        try:
            record = run.get(conn, run_id)
        except run.RunNotFound:
            raise ApiError(ErrorCode.NOT_FOUND, "ejecución no encontrada") from None
    finally:
        conn.close()
    if record.model_id != model_id:
        raise ApiError(ErrorCode.NOT_FOUND, "ejecución no encontrada")
    if not record.artifact_path or Path(record.artifact_path).name != record.artifact_path:
        raise ApiError(ErrorCode.INVALID_STATE, "esta ejecución no tiene datos persistidos")
    data_path = ctx.paths.model_outputs / record.artifact_path
    if not data_path.is_file():
        raise ApiError(ErrorCode.INVALID_STATE, "no se encuentra el resultado de la ejecución")
    report_path = ctx.paths.model_outputs / f"report-{run_id}.html"
    try:
        dashboard.generate_report(data_path, report_path, title=f"Ejecución {run_id}")
    except (OSError, ValueError, pa.ArrowException) as exc:
        raise ApiError(ErrorCode.UNAVAILABLE, "no se pudo generar el informe") from exc
    return 200, {"run_id": run_id, "generated": True}


def _report_delete(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Delete only the generated HTML report; retain the source result."""
    from motor.model import run, store

    model_id = _int_param(request, "model_id")
    run_id = _int_param(request, "run_id")
    conn = ctx.connect()
    try:
        _require_model(conn, store, model_id)
        try:
            record = run.get(conn, run_id)
        except run.RunNotFound:
            raise ApiError(ErrorCode.NOT_FOUND, "ejecución no encontrada") from None
    finally:
        conn.close()
    if record.model_id != model_id:
        raise ApiError(ErrorCode.NOT_FOUND, "ejecución no encontrada")
    report_path = ctx.paths.model_outputs / f"report-{run_id}.html"
    try:
        report_path.unlink()
    except FileNotFoundError:
        raise ApiError(ErrorCode.NOT_FOUND, "el informe no existe") from None
    except OSError as exc:
        raise ApiError(ErrorCode.UNAVAILABLE, "no se pudo borrar el informe") from exc
    return 200, {"run_id": run_id, "deleted": True}


def _assistant_proposal(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Return a validated rule proposal; no database writes or raw rows sent."""
    from motor import assistant
    from motor.model import store
    from motor.sources import EmptySheet, SheetNotFound, read_any
    from motor.sources.detect import UnsupportedFormat
    from motor.sources.profile import profile_table

    model_id = _int_param(request, "model_id")
    body = _json_body(request)
    if set(body) != {"source", "instruction"}:
        raise ApiError(ErrorCode.BAD_REQUEST, "se requieren source e instruction")
    source = body["source"]
    if not isinstance(source, str) or not _SAFE_NAME.fullmatch(source):
        raise ApiError(ErrorCode.BAD_REQUEST, "source debe ser un nombre simple")
    conn = ctx.connect()
    try:
        model = _require_model(conn, store, model_id)
        allowed = {attribute.name for entity in model.entities for attribute in entity.attributes}
    finally:
        conn.close()
    path = _model_input_path(ctx, source)
    if not path.is_file():
        raise ApiError(ErrorCode.NOT_FOUND, f"no existe el archivo {source!r}")
    try:
        table = read_any(path)
    except (UnsupportedFormat, SheetNotFound, EmptySheet) as exc:
        raise ApiError(ErrorCode.BAD_REQUEST, str(exc)) from None
    schema = [item.as_dict() for item in profile_table(table) if item.name in allowed]
    if not schema:
        raise ApiError(ErrorCode.BAD_REQUEST, "la fuente no contiene columnas del modelo")
    try:
        proposal = assistant.propose(body["instruction"], schema)
    except assistant.AssistantError as exc:
        raise ApiError(ErrorCode.UNAVAILABLE, str(exc)) from None
    return 200, {"proposal": proposal, "operations": assistant.operations()}


def _assistant_apply(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Validate accepted operations again, then clone a new model version."""
    from motor import assistant
    from motor.model import store

    model_id = _int_param(request, "model_id")
    body = _json_body(request)
    if set(body) != {"proposal"}:
        raise ApiError(ErrorCode.BAD_REQUEST, "se requiere proposal")
    conn = ctx.connect()
    try:
        model = _require_model(conn, store, model_id)
        schema = [
            {"name": attribute.name}
            for entity in model.entities for attribute in entity.attributes
        ]
        try:
            proposal = assistant.validate_proposal(body["proposal"], schema)
            if not proposal["rules"]:
                raise assistant.AssistantError("No hay reglas que aplicar.")
            new_id, version = assistant.apply_as_new_version(
                conn, model.model_id, proposal["rules"]
            )
        except assistant.AssistantError as exc:
            raise ApiError(ErrorCode.BAD_REQUEST, str(exc)) from None
    finally:
        conn.close()
    return 201, {"model_id": new_id, "version": version, "applied": True}


def _json_body(request: Request) -> dict:
    content_type = next((value for key, value in request.headers.items()
                         if key.lower() == "content-type"), "")
    if (content_type.split(";")[0].strip().lower()
            != "application/json"):
        raise ApiError(ErrorCode.BAD_REQUEST, "se requiere Content-Type: application/json")
    if request.body.size > 32 * 1024:
        raise ApiError(ErrorCode.PAYLOAD_TOO_LARGE, "la propuesta supera 32 KiB")
    request.body.stream.seek(0)
    try:
        value = json.loads(
            request.body.stream.read(32 * 1024 + 1), object_pairs_hook=_unique_json_object
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError):
        raise ApiError(ErrorCode.BAD_REQUEST, "el cuerpo JSON no es válido") from None
    if not isinstance(value, dict):
        raise ApiError(ErrorCode.BAD_REQUEST, "el cuerpo debe ser un objeto JSON")
    return value


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("clave JSON duplicada")
        result[key] = value
    return result


# -- reglas del modelo (M3) --------------------------------------------------


def _rules_params(request: Request) -> dict:
    """Construye `params` a partir de la query, según el tipo de regla.

    Solo se aceptan los campos que el tipo define (motor/model/rules.py): así una
    clave ajena no queda guardada como un parámetro que nadie lee.
    """
    kind = _one(request.query, "type")
    if kind == "rename":
        # La columna origen viaja en `column` (igual que en `cast`/`map`/…), así
        # que `params` solo aporta el destino. No se emite un `from: null`: una
        # clave que nadie lee y que aparenta un valor ausente es una trampa.
        return {"to": _one(request.query, "to")}
    if kind == "cast":
        return {"to": _one(request.query, "to") or "text"}
    if kind == "map":
        # El motor lee `params["values"]` (rules._map_expr): el dict debe ir
        # envuelto. Devolverlo crudo guardaba una regla que no transformaba nada.
        return {"values": _json_param(request.query, "values", {})}
    if kind == "domain":
        return {"allowed": _json_param(request.query, "allowed", [])}
    if kind == "filter":
        return {"equals": _one(request.query, "equals")}
    # `_rule_create` valida `kind` contra `rules.RULE_TYPES` antes de llamar aquí,
    # así que este camino es inalcanzable hoy. Es un guardia, no un valor por
    # defecto: un tipo de regla futuro sin rama debe fallar ruidosamente en vez de
    # guardarse con `params` vacíos (una regla que no transforma nada).
    raise ApiError(  # pragma: no cover - defensa en profundidad
        ErrorCode.INTERNAL_ERROR, f"tipo de regla sin parámetros: {kind!r}"
    )


def _rules_list(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Lista las reglas de un modelo en su orden de aplicación (M3)."""
    from motor.model import rules, store

    model_id = _int_param(request, "model_id")
    conn = ctx.connect()
    try:
        _require_model(conn, store, model_id)
        plan = rules.list_for(conn, model_id)
    finally:
        conn.close()
    return 200, {
        "rules": [
            {"name": r.name, "type": r.type, "column": r.column, "params": r.params}
            for r in plan
        ],
        "count": len(plan),
    }


def _rule_create(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Añade una regla con nombre a un modelo (M3).

    La regla se expresa por query (ADR-016 §10: sin cuerpo JSON). El motor valida
    el tipo y que la columna exista; aquí se traduce cada fallo a su código
    cerrado.
    """
    from motor.model import rules, store

    model_id = _int_param(request, "model_id")
    name = _one(request.query, "name")
    kind = _one(request.query, "type")
    column = _one(request.query, "column")
    if not name or not kind or not column:
        raise ApiError(
            ErrorCode.BAD_REQUEST, "se requieren `name`, `type` y `column`"
        )
    if kind not in rules.RULE_TYPES:
        raise ApiError(
            ErrorCode.BAD_REQUEST,
            f"tipo desconocido; válidos: {', '.join(rules.RULE_TYPES)}",
        )
    params = _rules_params(request)

    conn = ctx.connect()
    try:
        _require_model(conn, store, model_id)
        try:
            rule_id = rules.add(
                conn, model_id, name=name, type=kind, column=column, params=params
            )
        except rules.UnknownColumn as exc:
            raise ApiError(ErrorCode.BAD_REQUEST, str(exc)) from None
        except rules.DuplicateRule as exc:
            raise ApiError(ErrorCode.INVALID_STATE, str(exc)) from None
    finally:
        conn.close()
    return 201, {
        "rule_id": rule_id,
        "model_id": model_id,
        "name": name,
        "type": kind,
        "column": column,
        "params": params,
    }


def _rule_delete(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Borra una regla de un modelo por su nombre (M3)."""
    from motor.model import rules, store

    model_id = _int_param(request, "model_id")
    name = request.params["name"]
    conn = ctx.connect()
    try:
        _require_model(conn, store, model_id)
        before = {r.name for r in rules.list_for(conn, model_id)}
        if name not in before:
            raise ApiError(ErrorCode.NOT_FOUND, "la regla no existe en el modelo")
        rules.delete(conn, model_id, name=name)
    finally:
        conn.close()
    return 200, {"model_id": model_id, "name": name, "deleted": True}


def _diff(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Compara dos ejecuciones por su huella (M6).

    Un identificador no numérico es una petición malformada (`bad_request`); un
    id numérico que no existe es `not_found`. El cliente ramifica por el código.
    """
    from motor.model import diff, run

    before = _one(request.query, "before")
    after = _one(request.query, "after")
    if not before or not after:
        raise ApiError(ErrorCode.BAD_REQUEST, "se requieren `before` y `after`")
    try:
        before_id = int(before)
        after_id = int(after)
    except ValueError:
        raise ApiError(
            ErrorCode.BAD_REQUEST, "`before` y `after` deben ser enteros"
        ) from None

    conn = ctx.connect()
    try:
        try:
            first = run.get(conn, before_id)
            second = run.get(conn, after_id)
        except run.RunNotFound:
            raise ApiError(ErrorCode.NOT_FOUND, "la ejecución no existe") from None
    finally:
        conn.close()

    result = diff.compare(first.fingerprint, second.fingerprint)
    return 200, {
        "changed": result.changed,
        "reason": result.reason,
        "rows_before": first.fingerprint.rows,
        "rows_after": second.fingerprint.rows,
    }


def _orphans(ctx: ApiContext, request: Request) -> tuple[int, dict]:
    """Informe de huérfanos entre dos fuentes cargadas (M6, C5)."""
    from motor.model import engine, joins
    from motor.sources import EmptySheet, SheetNotFound, read_any
    from motor.sources.detect import UnsupportedFormat

    left = _one(request.query, "left")
    right = _one(request.query, "right")
    on = _one(request.query, "on")
    right_on = _one(request.query, "right_on") or on
    if not left or not right or not on:
        raise ApiError(
            ErrorCode.BAD_REQUEST, "se requieren `left`, `right` y `on`"
        )
    for name in (left, right):
        if not _SAFE_NAME.match(name):
            raise ApiError(ErrorCode.BAD_REQUEST, "el nombre debe ser un nombre simple")

    paths = {}
    for name in (left, right):
        path = _model_input_path(ctx, name)
        if not path.is_file():
            raise ApiError(ErrorCode.NOT_FOUND, f"no existe el archivo {name!r}")
        paths[name] = path

    try:
        tables = {name: read_any(path) for name, path in paths.items()}
        result = engine.run_chain(
            tables,
            [joins.Join(name="orphans", left=left, right=right,
                        on=[{"left": on, "right": right_on}], type="anti")],
        )
    except (UnsupportedFormat, SheetNotFound, EmptySheet) as exc:
        raise ApiError(ErrorCode.BAD_REQUEST, str(exc)) from None
    except engine.EngineError as exc:
        raise ApiError(ErrorCode.BAD_REQUEST, str(exc)) from None

    return 200, {
        "columns": result.columns,
        "rows_out": len(result.rows),
        "orphans": result.rows[:50],
    }


def _int_param(request: Request, name: str) -> int:
    """Parse a numeric path parameter.

    A non-numeric value is a malformed request (`bad_request`), not a missing
    resource: the client must be able to tell "fix your input" from "this id
    does not exist".
    """
    raw = request.params[name]
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ApiError(
            ErrorCode.BAD_REQUEST, f"`{name}` debe ser un entero"
        ) from None


def _require_model(conn, store, model_id: int):
    try:
        return store.get(conn, model_id)
    except store.ModelNotFound:
        raise ApiError(ErrorCode.NOT_FOUND, "modelo no encontrado") from None


def _model_summary(model) -> dict:
    return {
        "model_id": model.model_id,
        "name": model.name,
        "version": model.version,
        "entities": [entity.name for entity in model.entities],
    }


def _model_full(model) -> dict:
    return {
        "model_id": model.model_id,
        "name": model.name,
        "version": model.version,
        "entities": [
            {
                "entity_id": entity.entity_id,
                "name": entity.name,
                "key": entity.key,
                "attributes": [
                    {"name": a.name, "type": a.type, "nullable": a.nullable}
                    for a in entity.attributes
                ],
            }
            for entity in model.entities
        ],
    }


Handler = Callable[[ApiContext, Request], tuple[int, dict]]

_STATIC_ROUTES: dict[tuple[str, str], tuple[str, Handler]] = {
    ("GET", "/v1/health"): ("/v1/health", _health),
    ("GET", "/v1/jobs"): ("/v1/jobs", _jobs_list),
    ("POST", "/v1/inputs"): ("/v1/inputs", _inputs_upload),
    ("GET", "/v1/datasets"): ("/v1/datasets", _datasets_list),
    ("GET", "/v1/models"): ("/v1/models", _models_list),
    ("POST", "/v1/models"): ("/v1/models", _model_create),
    ("GET", "/v1/profile"): ("/v1/profile", _profile_input),
    ("GET", "/v1/diff"): ("/v1/diff", _diff),
    ("GET", "/v1/orphans"): ("/v1/orphans", _orphans),
}

_DYNAMIC_ROUTES: tuple[tuple[str, str, str, Handler], ...] = (
    ("GET", "/v1/jobs/{job_id}", r"^/v1/jobs/(?P<job_id>[^/]+)$", _job_detail),
    ("POST", "/v1/jobs/{job_id}/cancel",
     r"^/v1/jobs/(?P<job_id>[^/]+)/cancel$", _job_cancel),
    ("POST", "/v1/jobs/{job_id}/retry",
     r"^/v1/jobs/(?P<job_id>[^/]+)/retry$", _job_retry),
    ("GET", "/v1/datasets/{run_id}", r"^/v1/datasets/(?P<run_id>[^/]+)$",
     _dataset_detail),
    ("GET", "/v1/datasets/{run_id}/manifest",
     r"^/v1/datasets/(?P<run_id>[^/]+)/manifest$", _dataset_manifest),
    ("GET", "/v1/models/{model_id}", r"^/v1/models/(?P<model_id>[^/]+)$",
     _model_detail),
    ("GET", "/v1/models/{model_id}/runs",
     r"^/v1/models/(?P<model_id>[^/]+)/runs$", _runs_list),
    ("POST", "/v1/models/{model_id}/runs",
     r"^/v1/models/(?P<model_id>[^/]+)/runs$", _run_create),
    ("POST", "/v1/models/{model_id}/runs/{run_id}/report",
     r"^/v1/models/(?P<model_id>[^/]+)/runs/(?P<run_id>[^/]+)/report$",
     _report_generate),
    ("DELETE", "/v1/models/{model_id}/runs/{run_id}/report",
     r"^/v1/models/(?P<model_id>[^/]+)/runs/(?P<run_id>[^/]+)/report$",
     _report_delete),
    ("POST", "/v1/models/{model_id}/assistant/proposals",
     r"^/v1/models/(?P<model_id>[^/]+)/assistant/proposals$", _assistant_proposal),
    ("POST", "/v1/models/{model_id}/assistant/apply",
     r"^/v1/models/(?P<model_id>[^/]+)/assistant/apply$", _assistant_apply),
    ("GET", "/v1/models/{model_id}/rules",
     r"^/v1/models/(?P<model_id>[^/]+)/rules$", _rules_list),
    ("POST", "/v1/models/{model_id}/rules",
     r"^/v1/models/(?P<model_id>[^/]+)/rules$", _rule_create),
    ("DELETE", "/v1/models/{model_id}/rules/{name}",
     r"^/v1/models/(?P<model_id>[^/]+)/rules/(?P<name>[^/]+)$", _rule_delete),
)


def _resolve(method: str, path: str) -> tuple[str, Handler, dict[str, str]]:
    """Map `(method, path)` to `(route template, handler, params)`.

    The route template is what gets logged: it identifies the endpoint without
    recording the job or run identifier a client asked about. `HEAD` resolves as
    `GET` (RFC 9110): the handler suppresses the body, the headers stay exact.
    """
    lookup = "GET" if method == "HEAD" else method
    static = _STATIC_ROUTES.get((lookup, path))
    if static is not None:
        return static[0], static[1], {}
    allowed = {
        verb for verb, (known, _handler) in _STATIC_ROUTES.items() if known == path
    }
    for verb, template, pattern, handler in _DYNAMIC_ROUTES:
        match = re.match(pattern, path)
        if match is None:
            continue
        if verb != lookup:
            allowed.add(verb)
            continue
        # El router casa sobre la ruta codificada (así `%2F` no puede crear un
        # segmento nuevo), y solo el valor capturado se decodifica. Sin esto, un
        # nombre de regla con `#`/espacio/acento viajaba como `regla%20%231` y
        # nunca coincidía con el guardado: `DELETE` respondía 404 con la regla
        # presente (H-02 de la auditoría conector-fullstack).
        params = {
            key: unquote(value, encoding="utf-8") for key, value in match.groupdict().items()
        }
        return template, handler, params
    if allowed:
        raise ApiError(
            ErrorCode.METHOD_NOT_ALLOWED,
            f"el método {method} no se admite en esta ruta",
        )
    raise ApiError(ErrorCode.NOT_FOUND, "ruta inexistente")


def dispatch(ctx: ApiContext, *, method: str, target: str, body: Body,
             headers: Mapping[str, str]) -> Response:
    """Resolve and run one request, returning a success envelope.

    Any `ApiError` raised after resolution is stamped with the route template, so
    the request log can name the endpoint on a failure instead of `<unrouted>`
    (ADR-016 section 15).
    """
    path, _, raw_query = target.partition("?")
    route, handler, params = _resolve(method, path)
    try:
        _reject_json_body(headers, allow=route in {
            "/v1/models/{model_id}/assistant/proposals",
            "/v1/models/{model_id}/assistant/apply",
        })
        request = Request(
            params=params,
            # `keep_blank_values` preserves `?state=` as "". Without it, an empty
            # filter would look absent and be silently ignored instead of
            # rejected: a typo must fail loudly (API-05).
            query=parse_qs(raw_query, keep_blank_values=True),
            body=body,
            headers=headers,
        )
        status, data = handler(ctx, request)
    except ApiError as exc:
        exc.route = route
        raise
    return Response(status=status, payload=ok(data), route=route)


def _reject_json_body(headers: Mapping[str, str], *, allow: bool = False) -> None:
    """Refuse a declared JSON body: this API has no JSON request contract.

    Mutations live in the path and query string, and the only body is the raw
    input file. A client that sends JSON is told so, instead of having its
    payload silently stored as if it were a dataset.
    """
    content_type = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if content_type == "application/json" and not allow:
        raise ApiError(ErrorCode.BAD_REQUEST, "esta API no acepta un cuerpo JSON")

