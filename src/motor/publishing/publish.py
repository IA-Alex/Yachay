"""Atomic dataset publication: Parquet parts, manifest and `latest` (spec section 9).

The sequence is: write parts in a temp directory, fsync each part, write and
fsync the manifest, rename the whole directory into place, then swap `latest`
with an atomic rename. The manifest is the commit mark of a dataset (ADR-004).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_manifest(path: Path, **fields) -> None:
    """Write the manifest JSON and fsync it."""
    Path(path).write_text(
        json.dumps(fields, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_run(
    *,
    table,
    schema_fingerprint: str,
    dataset: str,
    run_id: str,
    job_key: str,
    processed_dir: Path,
    tmp_dir: Path,
    row_count: int | None = None,
    extra: dict | None = None,
) -> Path:
    """Publish a table atomically; return the manifest path (spec section 9)."""
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    part_name = "part-000.parquet"
    part_path = tmp_dir / part_name

    # Step 1-2: write the part (zstd) and fsync it. Arrow readers are consumed
    # batch by batch; regular Tables remain supported by the public helper.
    if isinstance(table, pa.RecordBatchReader):
        writer = pq.ParquetWriter(part_path, table.schema, compression="zstd")
        try:
            for batch in table:
                writer.write_batch(batch)
        finally:
            writer.close()
        rows = row_count
    else:
        pq.write_table(table, part_path, compression="zstd")
        rows = table.num_rows
    fd = os.open(part_path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

    # Step 3: sha256 per part and manifest with the minimum fields of section 9.
    part_sha = _sha256_file(part_path)
    manifest_path = tmp_dir / "manifest.json"
    fields = {
        "dataset": dataset,
        "run_id": run_id,
        "job_key": job_key,
        "schema_fingerprint": schema_fingerprint,
        "rows": rows,
        "parts": [{"name": part_name, "sha256": part_sha}],
        "created_at": time.time(),
    }
    if extra:
        fields.update(extra)
    write_manifest(manifest_path, **fields)

    # Step 4: fsync the temporary directory.
    _fsync_dir(tmp_dir)

    # Step 5: atomically move the directory into processed/<dataset>/run_id=<id>.
    dataset_dir = Path(processed_dir) / dataset
    dataset_dir.mkdir(parents=True, exist_ok=True)
    final_dir = dataset_dir / f"run_id={run_id}"
    if final_dir.exists():
        raise FileExistsError(final_dir)
    os.rename(tmp_dir, final_dir)
    _fsync_dir(dataset_dir)

    # Step 6: atomic swap of `latest` via rename (never `ln -sfn`).
    latest = dataset_dir / "latest"
    latest_tmp = dataset_dir / ".latest.tmp"
    if latest_tmp.exists() or latest_tmp.is_symlink():
        latest_tmp.unlink()
    os.symlink(f"run_id={run_id}", latest_tmp)
    os.rename(latest_tmp, latest)
    _fsync_dir(dataset_dir)

    return final_dir / "manifest.json"
