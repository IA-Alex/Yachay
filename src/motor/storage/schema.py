"""SQLite schema definition for the local data motor.

Only state and metadata live here; no tabular data (spec section 11, ADR-005).
"""

from __future__ import annotations

import sqlite3

# States of the job machine (spec section 6).
JOB_STATES = (
    "claimed",
    "queued",
    "running",
    "committed",
    "archived",
    "failed",
    "quarantined",
    "cancelled",
    "deferred",
)

CURRENT_SCHEMA_VERSION = 4

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS jobs (
    job_id           TEXT PRIMARY KEY,
    job_key          TEXT NOT NULL,
    state            TEXT NOT NULL CHECK (state IN ({",".join(f"'{s}'" for s in JOB_STATES)})),
    attempt          INTEGER NOT NULL DEFAULT 0,
    run_id           TEXT,
    worker_id        TEXT,
    lease_until      REAL,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    deferred_reason  TEXT,
    priority         INTEGER NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL DEFAULT 0,
    started_at       REAL
);

CREATE TABLE IF NOT EXISTS job_runs (
    run_id     TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL REFERENCES jobs(job_id),
    attempt    INTEGER NOT NULL,
    started_at REAL NOT NULL DEFAULT 0,
    finished_at REAL
);

CREATE TABLE IF NOT EXISTS input_files (
    job_id     TEXT NOT NULL REFERENCES jobs(job_id),
    path       TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime_ns   INTEGER NOT NULL,
    PRIMARY KEY (job_id)
);

CREATE TABLE IF NOT EXISTS datasets (
    run_id        TEXT PRIMARY KEY,
    job_key       TEXT NOT NULL,
    dataset       TEXT NOT NULL,
    manifest_path TEXT NOT NULL,
    committed_at  REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);

-- Partial unique index: at most one committed dataset per job_key (section 7).
CREATE UNIQUE INDEX IF NOT EXISTS idx_datasets_committed_key
    ON datasets(job_key);

CREATE TABLE IF NOT EXISTS audit_events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       REAL NOT NULL,
    action   TEXT NOT NULL,
    ref_id   TEXT,
    ref_hash TEXT
);

CREATE TABLE IF NOT EXISTS scan_snapshots (
    path       TEXT PRIMARY KEY,
    size_bytes INTEGER NOT NULL,
    mtime_ns   INTEGER NOT NULL,
    seen_at    REAL NOT NULL DEFAULT 0
);
"""


def create_schema(conn: sqlite3.Connection) -> None:
    """Create and migrate the motor schema idempotently."""
    conn.executescript(_SCHEMA)
    conn.executescript(_MODEL_SCHEMA)
    conn.executescript(_SCHEMA_VERSION)
    _migrate_run_artifacts(conn)
    _migrate_schema(conn)


def _migrate_run_artifacts(conn: sqlite3.Connection) -> None:
    """Add optional artifact metadata without rewriting existing runs."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    for name, kind in (
        ("artifact_path", "TEXT"),
        ("source_name", "TEXT"),
        ("source_sha256", "TEXT"),
        # H-05: huella de linaje del resultado. Texto libre (JSON serializado)
        # para no atar el esquema al formato mientras el contrato se asienta.
        # Las filas anteriores quedan en '' y se leen como "linaje desconocido".
        ("lineage", "TEXT"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {kind}")


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Advance the schema marker without destructive changes.

    All current DDL is additive and idempotent, so it is safe to run before
    advancing an older marker. Future incompatible changes must add an explicit
    numbered migration here instead of silently changing the marker.
    """
    row = conn.execute(
        "SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) "
            "VALUES (?, COALESCE(strftime('%s','now'),0))",
            (CURRENT_SCHEMA_VERSION,),
        )
        return
    current = int(row[0])
    if current > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"la base requiere una versión de código más nueva ({current} > "
            f"{CURRENT_SCHEMA_VERSION})"
        )
    if current < CURRENT_SCHEMA_VERSION:
        conn.execute("DELETE FROM schema_version")
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) "
            "VALUES (?, COALESCE(strftime('%s','now'),0))",
            (CURRENT_SCHEMA_VERSION,),
        )


# ---------------------------------------------------------------------------
# Modelado de datos (ADR-019, M2). Estas tablas describen el *modelo* del
# usuario (entidades, atributos, claves), no los datos tabulares: SQLite sigue
# guardando solo estado y metadatos (ADR-005).
# ---------------------------------------------------------------------------

_MODEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
    model_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL DEFAULT 0,
    UNIQUE (name, version)
);

CREATE TABLE IF NOT EXISTS entities (
    entity_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id   INTEGER NOT NULL REFERENCES models(model_id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    UNIQUE (model_id, name)
);

CREATE TABLE IF NOT EXISTS attributes (
    attribute_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id    INTEGER NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    type         TEXT NOT NULL DEFAULT 'text',
    nullable     INTEGER NOT NULL DEFAULT 1,
    UNIQUE (entity_id, name)
);

CREATE TABLE IF NOT EXISTS entity_keys (
    entity_id  INTEGER NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    attribute  TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (entity_id, attribute)
);

CREATE INDEX IF NOT EXISTS idx_entities_model ON entities(model_id);
CREATE INDEX IF NOT EXISTS idx_attributes_entity ON attributes(entity_id);

CREATE TABLE IF NOT EXISTS rules (
    rule_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id   INTEGER NOT NULL REFERENCES models(model_id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    type       TEXT NOT NULL,
    column     TEXT NOT NULL,
    params     TEXT NOT NULL DEFAULT '{}',
    position   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (model_id, name)
);

CREATE INDEX IF NOT EXISTS idx_rules_model ON rules(model_id);

CREATE TABLE IF NOT EXISTS joins (
    join_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id   INTEGER NOT NULL REFERENCES models(model_id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    left_ref   TEXT NOT NULL,
    right_ref  TEXT NOT NULL,
    on_pairs   TEXT NOT NULL DEFAULT '[]',
    type       TEXT NOT NULL DEFAULT 'inner',
    position   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (model_id, name)
);

CREATE INDEX IF NOT EXISTS idx_joins_model ON joins(model_id);

CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id    INTEGER NOT NULL REFERENCES models(model_id) ON DELETE CASCADE,
    created_at  REAL NOT NULL DEFAULT 0,
    output      TEXT NOT NULL DEFAULT '',
    columns     TEXT NOT NULL DEFAULT '[]',
    rows_count  INTEGER NOT NULL DEFAULT 0,
    hash        TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_runs_model ON runs(model_id);
"""

# Versión del esquema, para que una futura migración sepa de dónde parte.
# v1: ingesta. v2: modelado. v3: ejecuciones. v4: metadatos de Parquet local.
_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL,
    applied_at REAL NOT NULL DEFAULT 0
);
INSERT INTO schema_version (version, applied_at)
SELECT 4, COALESCE(strftime('%s','now'),0)
WHERE NOT EXISTS (SELECT 1 FROM schema_version);
"""
