#!/usr/bin/env python3
"""Banco de rendimiento: perfilado, ejecución y persistencia sobre tablas sintéticas.

Mide las tres etapas que el motor recorre con datos reales y reporta tiempo y
memoria pico de la etapa. Pensado para responder "¿aguanta 100k filas?" con una
cifra medida, no con una impresión.

Uso:
    .venv/bin/python scripts/bench.py                 # 100k filas (por defecto)
    .venv/bin/python scripts/bench.py --rows 99999
    .venv/bin/python scripts/bench.py --rows 1000 --json

**Límite real del motor** (`motor/limits.py`): `MAX_TABLE_ROWS = 100_000`
cuenta la **fila de cabecera**, así que el máximo es **99 999 filas de datos**.
`MAX_TABLE_CELLS = 1_000_000` solo muerde antes con muchas columnas (a 5
columnas el techo son 200 000 filas, por lo que el límite de filas manda).
Pedir 100 000 falla con "la tabla excede el límite de filas": es el contrato,
no un defecto. El banco avisa en vez de fallar con una traza.

El CSV se genera en un directorio temporal y se borra al terminar: el banco no
toca la carpeta `data/` del proyecto ni deja artefactos en el repositorio.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from motor.model import run, store  # noqa: E402
from motor.sources import read_any  # noqa: E402
from motor.sources.profile import profile_table  # noqa: E402
from motor.storage.db import connect  # noqa: E402

COLUMNS = ("id", "ciudad", "monto", "estado", "fecha")


def peak_rss_mb() -> float:
    """Memoria residente pico del proceso, en MiB (Linux)."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss viene en KiB en Linux.
    return round(usage / 1024, 1)


def generate_csv(path: Path, rows: int) -> None:
    """Escribe un CSV sintético reproducible con una mezcla de tipos."""
    cities = ("Cusco", "Lima", "Arequipa", "Trujillo", "Piura")
    states = ("Hecho", "En curso", "Pendiente")
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(COLUMNS) + "\n")
        for i in range(rows):
            handle.write(
                f"{i},{cities[i % len(cities)]},{i * 7 % 1000}.50,"
                f"{states[i % len(states)]},2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}\n"
            )


def timed(label: str, operation):
    """Ejecuta `operation` y devuelve (resultado, segundos)."""
    started = time.perf_counter()
    value = operation()
    return value, round(time.perf_counter() - started, 3)


def measure(rows: int) -> dict:
    report: dict = {"rows": rows, "csv_mb": None, "stages": {}}
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        csv_path = base / "bench.csv"
        generate_csv(csv_path, rows)
        report["csv_mb"] = round(csv_path.stat().st_size / (1024 * 1024), 2)

        table, seconds = timed("read", lambda: read_any(csv_path))
        report["stages"]["read_csv"] = {
            "seconds": seconds,
            "rows": len(table.rows),
            "peak_rss_mb": peak_rss_mb(),
        }

        profiles, seconds = timed("profile", lambda: profile_table(table))
        report["stages"]["profile"] = {
            "seconds": seconds,
            "columns": len(profiles),
            "peak_rss_mb": peak_rss_mb(),
        }

        conn = connect(base / "bench.sqlite3")
        try:
            model_id = store.create(conn, name="bench")
            artifacts = base / "artifacts"
            run_id, seconds = timed(
                "record",
                lambda: run.record(
                    conn, model_id, table=table, output="bench",
                    artifact_dir=artifacts,
                    lineage=run.describe_lineage(output="bench", table=table),
                ),
            )
            parquet = artifacts / f"run-{run_id}.parquet"
            report["stages"]["record_and_parquet"] = {
                "seconds": seconds,
                "parquet_mb": round(parquet.stat().st_size / (1024 * 1024), 2),
                "peak_rss_mb": peak_rss_mb(),
            }
            stored = run.get(conn, run_id)
            report["ok"] = stored.fingerprint.rows == rows
            report["hash"] = stored.fingerprint.hash[:16]
        finally:
            conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mide perfilado, ejecución y persistencia sobre datos sintéticos."
    )
    parser.add_argument("--rows", type=int, default=99_999,
                        help="filas de datos a generar (default: 99999, el máximo)")
    parser.add_argument("--repeat", type=int, default=1,
                        help="repeticiones para calcular la mediana (default: 1)")
    parser.add_argument("--json", action="store_true",
                        help="emitir JSON en lugar de una tabla")
    args = parser.parse_args(argv)

    if args.rows < 1:
        parser.error("--rows debe ser positivo")

    from motor.limits import MAX_TABLE_ROWS

    if args.rows >= MAX_TABLE_ROWS:
        parser.error(
            f"--rows debe ser < {MAX_TABLE_ROWS}: el motor cuenta la cabecera "
            f"como fila, así que {MAX_TABLE_ROWS - 1} es el máximo de datos"
        )

    reports = [measure(args.rows) for _ in range(max(1, args.repeat))]

    if args.json:
        print(json.dumps(reports, indent=2, ensure_ascii=False))
        return 0 if all(r.get("ok") for r in reports) else 1

    print(f"Filas: {args.rows:,}  CSV: {reports[0]['csv_mb']} MiB  "
          f"(CPU: {os.cpu_count()})")
    print(f"{'etapa':<22}{'seg':>8}{'mediana':>10}{'RSS MiB':>10}")
    for stage in reports[0]["stages"]:
        times = [r["stages"][stage]["seconds"] for r in reports]
        print(
            f"{stage:<22}{reports[0]['stages'][stage]['seconds']:>8.3f}"
            f"{statistics.median(times):>10.3f}"
            f"{reports[-1]['stages'][stage]['peak_rss_mb']:>10.1f}"
        )
    rows_ok = all(r.get("ok") for r in reports)
    print(f"\nDemo pasada: {'sí' if rows_ok else 'NO'} (huella "
          f"{reports[0]['hash']})")
    return 0 if rows_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
