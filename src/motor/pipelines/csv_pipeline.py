"""Minimal DuckDB CSV pipeline (spec section 8, ADR-002).

DuckDB is the single transformation engine. Type and date inference happens at
column level, never row by row.
"""

from __future__ import annotations

import codecs
import hashlib
from pathlib import Path

import duckdb

_SEP = "\x1f"
_ENCODING_SCAN_BYTES = 1024 * 1024


def _csv_encoding(path: Path) -> str:
    """Match the source reader's UTF-8-sig, then Latin-1 fallback policy.

    Validate incrementally so large CSVs stay batch-backed instead of being
    copied into memory just to choose the decoder.
    """
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    try:
        with Path(path).open("rb") as source:
            for chunk in iter(
                lambda: source.read(_ENCODING_SCAN_BYTES), b""
            ):
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return "latin-1"
    return "utf-8"


def schema_fingerprint(schema: dict[str, str]) -> str:
    """Return a deterministic fingerprint of normalized names plus types."""
    canonical = _SEP.join(f"{name}:{schema[name]}" for name in sorted(schema))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_csv_pipeline(*, input_path: Path, duckdb_path: Path, tmp_dir: Path):
    """Read a CSV at column level and return `(reader, schema, row_count)`.

    The Arrow reader is batch-backed: callers must consume it before the
    returned DuckDB connection is reclaimed. This avoids materializing the
    complete CSV in Python memory before Parquet publication.
    """
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    encoding = _csv_encoding(input_path)
    connection = duckdb.connect(str(duckdb_path))
    row_count = connection.execute(
        "SELECT COUNT(*) FROM read_csv_auto(?, encoding = ?)",
        [str(input_path), encoding],
    ).fetchone()[0]
    relation = connection.execute(
        "SELECT * FROM read_csv_auto(?, encoding = ?)",
        [str(input_path), encoding],
    )
    reader = relation.to_arrow_reader(batch_size=65_536)
    schema = {name: str(dtype) for name, dtype in zip(
        reader.schema.names, reader.schema.types, strict=True
    )}
    return reader, schema, int(row_count)
