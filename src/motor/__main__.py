"""Yachay command-line entrypoint.

Run the local worker: `yachay run [--root DIR] [--config defaults.toml] [--once]`.
Run the local API: `yachay api [--root DIR] [--config defaults.toml] [--socket PATH]`.
Inspect an input: `yachay inspect ARCHIVO [--sheet HOJA]` (ADR-019, M1).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from motor.api.server import serve
from motor.config import MotorConfig
from motor.worker import Worker

# Default socket location, relative to the workspace root (ADR-016).
SOCKET_RELATIVE_PATH = Path("data") / "motor.sock"


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(prog="yachay", description="Yachay data motor")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the local worker")
    run.add_argument("--root", default=".", help="workspace root (default: .)")
    run.add_argument(
        "--config", default=None, help="path to defaults.toml (default: built-in)"
    )
    run.add_argument(
        "--once", action="store_true", help="run a single cycle and exit"
    )

    api = sub.add_parser("api", help="serve the v1 API on a Unix socket")
    api.add_argument("--root", default=".", help="workspace root (default: .)")
    api.add_argument(
        "--config", default=None, help="path to defaults.toml (default: built-in)"
    )
    api.add_argument(
        "--socket",
        default=None,
        help="Unix socket path (default: <root>/data/motor.sock)",
    )

    inspect = sub.add_parser(
        "inspect", help="profile an input file (M1, ADR-019)"
    )
    inspect.add_argument("file", help="archivo a perfilar")
    inspect.add_argument(
        "--sheet", default=None, help="hoja a leer en .xlsx/.ods (default: la primera)"
    )

    model = sub.add_parser("model", help="gestionar modelos de datos (M2, ADR-019)")
    model_sub = model.add_subparsers(dest="model_command", required=True)
    m_new = model_sub.add_parser("new", help="crear un modelo desde un archivo")
    m_new.add_argument("name", help="nombre del modelo")
    m_new.add_argument("file", help="archivo del que inferir la primera entidad")
    m_new.add_argument("--root", default=".", help="raíz del proyecto (default: .)")
    m_new.add_argument("--sheet", default=None, help="hoja a leer (.xlsx/.ods)")
    m_show = model_sub.add_parser("show", help="mostrar un modelo guardado")
    m_show.add_argument("model_id", type=int, help="identificador del modelo")
    m_show.add_argument("--root", default=".", help="raíz del proyecto (default: .)")
    m_show.add_argument("--version", type=int, default=None, help="versión a mostrar")
    m_preview = model_sub.add_parser(
        "preview",
        help="aplicar un modelo y ver el resultado sin persistir (M3)",
        description=(
            "Aplica las reglas del modelo al archivo y muestra el resultado por "
            "stdout. No guarda ejecución, huella ni Parquet. Para conservar una "
            "ejecución comparable usa `yachay model exec`."
        ),
    )
    m_preview.add_argument("model_id", type=int, help="identificador del modelo")
    m_preview.add_argument("file", help="archivo de entrada")
    m_preview.add_argument("--root", default=".", help="raíz del proyecto (default: .)")
    m_preview.add_argument("--sheet", default=None, help="hoja a leer (.xlsx/.ods)")
    m_join = model_sub.add_parser("join", help="cruzar dos archivos (M4)")
    m_join.add_argument("left", help="archivo de la izquierda")
    m_join.add_argument("right", help="archivo de la derecha")
    m_join.add_argument("--on", required=True, help="columna de unión (mismo nombre)")
    m_join.add_argument("--right-on", default=None,
                        help="columna de la derecha (si difiere)")
    m_join.add_argument("--type", default="inner",
                        choices=["inner", "left", "anti"],
                        help="tipo de cruce (anti = huérfanos)")
    m_join.add_argument("--root", default=".", help="raíz del proyecto (default: .)")
    m_align = model_sub.add_parser(
        "align", help="alinear esquemas distintos y apilar tablas locales"
    )
    m_align.add_argument("files", nargs="+", help="dos o más fuentes tabulares")
    m_align.add_argument(
        "--column-maps", default=None,
        help="JSON con un objeto de mapeo por archivo, en el mismo orden",
    )
    m_align.add_argument(
        "--columns", default=None,
        help="orden y columnas finales separados por coma (por defecto, unión estable)",
    )
    m_align.add_argument("--output", default=None, help="archivo nuevo de salida (.csv/.xlsx/.ods)")
    m_align.add_argument("--sheet", default=None, help="hoja a leer en cada fuente")
    m_align.add_argument("--root", default=".", help="raíz del proyecto (default: .)")
    m_validate = model_sub.add_parser(
        "validate", help="validar filas con un contrato JSON local"
    )
    m_validate.add_argument("file", help="fuente tabular local")
    m_validate.add_argument("rules", help="archivo JSON con reglas de calidad")
    m_validate.add_argument("--errors-output", default=None,
                            help="archivo nuevo con filas y motivos de error")
    m_validate.add_argument("--sheet", default=None, help="hoja a leer en XLSX/ODS")
    m_validate.add_argument("--root", default=".", help="raíz del proyecto (default: .)")
    m_dedupe = model_sub.add_parser(
        "dedupe", help="eliminar duplicados por clave declarada"
    )
    m_dedupe.add_argument("file", help="fuente tabular local")
    m_dedupe.add_argument("--key", required=True, help="columnas clave separadas por coma")
    m_dedupe.add_argument("--keep", choices=["first", "last"], default="first")
    m_dedupe.add_argument("--output", default=None, help="archivo nuevo deduplicado")
    m_dedupe.add_argument("--sheet", default=None, help="hoja a leer en XLSX/ODS")
    m_dedupe.add_argument("--root", default=".", help="raíz del proyecto (default: .)")
    m_transform = model_sub.add_parser(
        "transform", help="aplicar una transformación local declarada en JSON"
    )
    m_transform.add_argument("file", help="fuente tabular local")
    m_transform.add_argument("spec", help="archivo JSON con una operación declarativa")
    m_transform.add_argument("--output", required=True,
                             help="archivo local nuevo (.csv/.xlsx/.ods)")
    m_transform.add_argument("--sheet", default=None, help="hoja a leer en XLSX/ODS")
    m_transform.add_argument("--root", default=".", help="raíz del proyecto (default: .)")
    m_exec = model_sub.add_parser(
        "exec",
        help="aplicar un modelo y persistir la ejecución (M5)",
        description=(
            "Aplica las reglas del modelo, guarda una ejecución con su huella y "
            "escribe el Parquet del resultado. Es la vía para comparar "
            "ejecuciones con `yachay model diff` y para generar informes."
        ),
    )
    m_exec.add_argument("model_id", type=int, help="identificador del modelo")
    m_exec.add_argument("file", help="archivo de entrada (fuente principal)")
    m_exec.add_argument("--root", default=".", help="raíz del proyecto (default: .)")
    m_exec.add_argument("--sheet", default=None, help="hoja a leer (.xlsx/.ods)")
    m_diff = model_sub.add_parser("diff", help="comparar dos ejecuciones (M5)")
    m_diff.add_argument("run_a", type=int, help="ejecución anterior")
    m_diff.add_argument("run_b", type=int, help="ejecución posterior")
    m_diff.add_argument("--root", default=".", help="raíz del proyecto (default: .)")
    m_export = model_sub.add_parser(
        "export-run", help="exportar el resultado de una ejecución a un archivo nuevo"
    )
    m_export.add_argument("run_id", type=int, help="identificador de ejecución")
    m_export.add_argument("output", help="destino local nuevo (.csv/.xlsx/.ods)")
    m_export.add_argument("--root", default=".", help="raíz del proyecto (default: .)")
    m_incremental = model_sub.add_parser(
        "incremental", help="incorporar un archivo delta a una ejecución anterior"
    )
    m_incremental.add_argument("model_id", type=int, help="identificador del modelo")
    m_incremental.add_argument("previous_run_id", type=int, help="ejecución base")
    m_incremental.add_argument("file", help="archivo delta local")
    m_incremental.add_argument("--key", required=True,
                               help="claves de salida separadas por coma")
    m_incremental.add_argument("--sheet", default=None, help="hoja a leer en XLSX/ODS")
    m_incremental.add_argument("--root", default=".", help="raíz del proyecto (default: .)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entrypoint: parse arguments and run the selected service."""
    # Match the systemd units for standalone runs too; new data files are not
    # world-readable by default.
    previous_umask = os.umask(0o027)
    try:
        return _main(argv)
    finally:
        os.umask(previous_umask)


def _main(argv: list[str] | None = None) -> int:
    """Dispatch one CLI command while the private data umask is active."""
    args = build_parser().parse_args(argv)

    if args.command == "inspect":
        return _inspect(Path(args.file), sheet=args.sheet)

    if args.command == "model":
        return _model(args)

    root = Path(args.root)
    if args.config:
        config = MotorConfig.from_file(Path(args.config), root=root)
    else:
        config = MotorConfig.from_root(root)

    if args.command == "api":
        socket_path = Path(args.socket) if args.socket else root / SOCKET_RELATIVE_PATH
        serve(config, socket_path)
        return 0

    worker = Worker(config)
    if args.once:
        worker.run_once()
    else:
        worker.run_forever()
    return 0


def _inspect(path: Path, *, sheet: str | None) -> int:
    """Perfila una entrada y escribe el perfil como JSON en stdout."""
    from motor.sources import EmptySheet, SheetNotFound, read_any
    from motor.sources.detect import UnsupportedFormat
    from motor.sources.profile import profile_table

    try:
        table = read_any(path, sheet=sheet)
    except (UnsupportedFormat, SheetNotFound, EmptySheet) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    report = {
        "ok": True,
        "file": path.name,
        "columns": table.columns,
        "rows": len(table.rows),
        "note": table.note,
        "profile": [column.as_dict() for column in profile_table(table)],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _model(args) -> int:
    """Crea o muestra un modelo de datos (M2, ADR-019)."""
    from motor.config import MotorConfig
    from motor.model import infer, store
    from motor.sources import EmptySheet, SheetNotFound, read_any
    from motor.sources.detect import UnsupportedFormat
    from motor.sources.profile import profile_table
    from motor.storage.db import connect

    root = Path(args.root)
    config = MotorConfig.from_root(root)
    conn = connect(config.paths.sqlite_path)

    if args.model_command == "show":
        try:
            model = store.get(conn, args.model_id, version=args.version)
        except store.ModelNotFound as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 1
        print(json.dumps(_model_as_dict(model), ensure_ascii=False, indent=2))
        return 0

    if args.model_command == "preview":
        return _model_preview(args, conn)

    if args.model_command == "join":
        return _model_join(args)

    if args.model_command == "align":
        return _model_align(args)

    if args.model_command == "validate":
        return _model_validate(args)

    if args.model_command == "dedupe":
        return _model_dedupe(args)

    if args.model_command == "transform":
        return _model_transform(args)

    if args.model_command == "exec":
        return _model_exec(args, conn, config)

    if args.model_command == "diff":
        return _model_diff(args, conn)

    if args.model_command == "export-run":
        return _model_export_run(args, conn, config)

    if args.model_command == "incremental":
        return _model_incremental(args, conn, config)

    # `new`: infiere una entidad desde el archivo y la guarda.
    try:
        table = read_any(Path(args.file), sheet=args.sheet)
    except (UnsupportedFormat, SheetNotFound, EmptySheet) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    profiles = profile_table(table)
    proposal = infer.entity_from_table(table, profiles, name=Path(args.file).stem)

    model_id = store.create(conn, name=args.name)
    entity_id = store.add_entity(conn, model_id, name=proposal.name)
    for attribute in proposal.attributes:
        store.add_attribute(
            conn, entity_id, name=attribute.name,
            type=attribute.type, nullable=attribute.nullable,
        )
    if proposal.suggested_key:
        store.set_key(conn, entity_id, columns=proposal.suggested_key)

    model = store.get(conn, model_id)
    print(json.dumps(_model_as_dict(model), ensure_ascii=False, indent=2))
    return 0


def _model_preview(args, conn) -> int:
    """Aplica un modelo sobre un archivo y muestra el resultado, sin persistir (M3).

    Contrato: este comando nunca escribe en `runs` ni crea Parquet. Persistir es
    la responsabilidad de `model exec` (M5), que sí registra huella y artefacto.
    """
    from motor.model import engine, rules, store
    from motor.sources import EmptySheet, SheetNotFound, read_any
    from motor.sources.detect import UnsupportedFormat

    try:
        store.get(conn, args.model_id)
    except store.ModelNotFound as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    try:
        table = read_any(Path(args.file), sheet=args.sheet)
    except (UnsupportedFormat, SheetNotFound, EmptySheet) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    plan = rules.list_for(conn, args.model_id)
    try:
        result = engine.run(table, plan)
    except engine.EngineError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    report = {
        "ok": True,
        "model_id": args.model_id,
        "rules": len(plan),
        "columns": result.columns,
        "rows_in": len(table.rows),
        "rows_out": len(result.rows),
        "sample": result.rows[:5],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _model_join(args) -> int:
    """Cruza dos archivos por una columna (M4; `anti` da el informe de huérfanos)."""
    from motor.model import engine, joins
    from motor.sources import EmptySheet, SheetNotFound, read_any
    from motor.sources.detect import UnsupportedFormat

    try:
        left = read_any(Path(args.left))
        right = read_any(Path(args.right))
    except (UnsupportedFormat, SheetNotFound, EmptySheet) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    right_column = args.right_on or args.on
    join = joins.Join(
        name="cli",
        left="izquierda",
        right="derecha",
        on=[{"left": args.on, "right": right_column}],
        type=args.type,
    )
    try:
        result = engine.run_chain({"izquierda": left, "derecha": right}, [join])
    except engine.EngineError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    report = {
        "ok": True,
        "type": args.type,
        "diagnostics": joins.diagnose(left, right, join),
        "columns": result.columns,
        "rows_in": {"izquierda": len(left.rows), "derecha": len(right.rows)},
        "rows_out": len(result.rows),
        "sample": result.rows[:5],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _model_align(args) -> int:
    """Alinea y concatena fuentes de esquema distinto (ADR-021, T-024)."""
    from motor.model import engine
    from motor.sources import EmptySheet, SheetNotFound, read_any
    from motor.sources.detect import UnsupportedFormat

    try:
        tables = [read_any(Path(path), sheet=args.sheet) for path in args.files]
        maps = [{} for _ in tables]
        if args.column_maps:
            loaded = json.loads(Path(args.column_maps).read_text(encoding="utf-8"))
            if not isinstance(loaded, list) or len(loaded) != len(tables):
                raise ValueError("column-maps debe contener un objeto por archivo")
            if any(not isinstance(item, dict) for item in loaded):
                raise ValueError("cada mapa debe ser un objeto JSON")
            maps = loaded
        columns = args.columns.split(",") if args.columns else None
        result, report = engine.align_tables(tables, column_maps=maps, columns=columns)
    except (UnsupportedFormat, SheetNotFound, EmptySheet, OSError, json.JSONDecodeError,
            ValueError, engine.EngineError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    report.update({
        "ok": True,
        "files": [Path(path).name for path in args.files],
        "sample": result.rows[:5],
    })
    if args.output:
        from motor.model.exports import ExportError, export_table

        try:
            report["output_file"] = str(export_table(result, Path(args.output)))
        except ExportError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _model_validate(args) -> int:
    """Valida filas contra un contrato JSON sin persistir datos (T-025)."""
    from motor.model import quality
    from motor.sources import EmptySheet, SheetNotFound, read_any
    from motor.sources.detect import UnsupportedFormat

    try:
        table = read_any(Path(args.file), sheet=args.sheet)
        rules = json.loads(Path(args.rules).read_text(encoding="utf-8"))
        if not isinstance(rules, list):
            raise ValueError("el contrato JSON debe ser una lista de reglas")
        report = quality.validate(table, rules)
    except (UnsupportedFormat, SheetNotFound, EmptySheet, OSError, json.JSONDecodeError,
            ValueError, quality.QualityError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    report.update({"ok": True, "file": Path(args.file).name})
    if args.errors_output:
        from motor.model.exports import ExportError, export_table
        from motor.sources.tables import Table

        error_table = Table(
            columns=["row", "rule", "type", "columns", "message"],
            rows=[
                [failure["row"], issue["rule"], issue["type"],
                 ", ".join(issue["columns"]), issue["message"]]
                for failure in report["failures"] for issue in failure["issues"]
            ],
        )
        try:
            report["errors_file"] = str(export_table(error_table, Path(args.errors_output)))
        except ExportError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _model_dedupe(args) -> int:
    """Previsualiza deduplicación estable por clave explícita (T-027)."""
    from motor.model import engine
    from motor.sources import EmptySheet, SheetNotFound, read_any
    from motor.sources.detect import UnsupportedFormat

    try:
        table = read_any(Path(args.file), sheet=args.sheet)
        result, report = engine.deduplicate(
            table, keys=[key.strip() for key in args.key.split(",") if key.strip()],
            keep=args.keep,
        )
    except (UnsupportedFormat, SheetNotFound, EmptySheet, engine.EngineError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    report.update({"ok": True, "file": Path(args.file).name, "columns": result.columns,
                   "sample": result.rows[:5]})
    if args.output:
        from motor.model.exports import ExportError, export_table

        try:
            report["output_file"] = str(export_table(result, Path(args.output)))
        except ExportError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _model_transform(args) -> int:
    """Aplica una operación segura declarada y exporta una tabla local."""
    from motor.model import exports, transforms
    from motor.sources import EmptySheet, SheetNotFound, read_any
    from motor.sources.detect import UnsupportedFormat

    try:
        table = read_any(Path(args.file), sheet=args.sheet)
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        if not isinstance(spec, dict):
            raise ValueError("la especificación debe ser un objeto JSON")
        operation = spec.get("operation")
        if operation == "derive":
            result = transforms.derive_column(
                table, name=spec.get("name"), expression=spec.get("expression"),
            )
            details = {"operation": operation, "column": spec.get("name")}
        elif operation == "aggregate":
            result = transforms.aggregate_table(
                table, by=spec.get("by", []), metrics=spec.get("metrics", []),
            )
            details = {"operation": operation, "group_by": spec.get("by", []),
                       "metrics": len(spec.get("metrics", []))}
        elif operation == "pivot":
            result = transforms.pivot_table(
                table, index=spec.get("index", []), pivot=spec.get("pivot"),
                value=spec.get("value"), function=spec.get("function", "sum"),
            )
            details = {"operation": operation, "index": spec.get("index", [])}
        elif operation == "unpivot":
            result = transforms.unpivot_table(
                table, index=spec.get("index", []), columns=spec.get("columns", []),
                name_column=spec.get("name_column", "variable"),
                value_column=spec.get("value_column", "value"),
            )
            details = {"operation": operation, "columns": spec.get("columns", [])}
        elif operation == "normalize":
            result, details = transforms.normalize_table(
                table, numbers=spec.get("numbers", {}), dates=spec.get("dates", {}),
            )
            details["operation"] = operation
        else:
            raise ValueError("operation debe ser derive, aggregate, pivot, unpivot o normalize")
        output_path = exports.export_table(result, Path(args.output))
    except (UnsupportedFormat, SheetNotFound, EmptySheet, OSError, json.JSONDecodeError,
            ValueError, transforms.TransformError, exports.ExportError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({
        "ok": True, **details, "rows_in": len(table.rows), "rows_out": len(result.rows),
        "columns": result.columns, "output_file": str(output_path),
        "sample": result.rows[:5],
    }, ensure_ascii=False, indent=2))
    return 0


def _model_exec(args, conn, config) -> int:
    """Ejecuta el modelo (reglas) y guarda la huella del resultado (M5)."""
    from motor.model import rules, run, store
    from motor.sources import EmptySheet, SheetNotFound, read_any
    from motor.sources.detect import UnsupportedFormat

    try:
        model = store.get(conn, args.model_id)
    except store.ModelNotFound as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    try:
        table = read_any(Path(args.file), sheet=args.sheet)
    except (UnsupportedFormat, SheetNotFound, EmptySheet) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    if not model.entities:
        print(json.dumps({"ok": False, "error": "el modelo no tiene entidades"},
                         ensure_ascii=False))
        return 1
    entity = model.entities[0]

    plan = rules.list_for(conn, args.model_id)
    try:
        result = run.execute(
            {entity.name: table},
            rules_by_source={entity.name: plan},
            join_plan=[],
            output=entity.name,
        )
    except run.RunError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    try:
        source_hash = _file_sha256(Path(args.file))
    except OSError as exc:
        print(json.dumps({"ok": False, "error": f"no se pudo leer la fuente: {exc}"},
                         ensure_ascii=False))
        return 1
    source_metadata = [{"name": Path(args.file).name, "sha256": source_hash}]
    lineage = run.describe_lineage(
        output=entity.name, rules_applied=plan, table=result,
        model_version=model.version, sources=source_metadata,
        parameters={"operation": "full"},
    )
    try:
        run_id = run.record(
            conn, args.model_id, table=result, output=entity.name,
            artifact_dir=config.paths.model_outputs,
            source_name=Path(args.file).name, source_sha256=source_hash,
            lineage=lineage,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"no se pudo guardar la ejecución: {exc}"},
                         ensure_ascii=False))
        return 1
    fp = run.fingerprint(result)
    print(json.dumps({
        "ok": True, "run_id": run_id, "model_id": args.model_id,
        "rules": len(plan), "columns": result.columns,
        "rows_out": len(result.rows), "hash": fp.hash[:16],
    }, ensure_ascii=False, indent=2))
    return 0


def _model_incremental(args, conn, config) -> int:
    """Aplica solo el archivo delta y sustituye claves previas de forma estable."""
    from motor.model import engine, rules, run, store
    from motor.sources import EmptySheet, SheetNotFound, read_any
    from motor.sources.detect import UnsupportedFormat

    try:
        model = store.get(conn, args.model_id)
        previous = run.get(conn, args.previous_run_id)
    except (store.ModelNotFound, run.RunNotFound) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    if previous.model_id != args.model_id:
        print(json.dumps({"ok": False, "error": "la ejecución base pertenece a otro modelo"},
                         ensure_ascii=False))
        return 1
    prior_model_version = (previous.lineage or {}).get("model_version")
    if prior_model_version != model.version:
        print(json.dumps({"ok": False,
                          "error": "la ejecución base usa otra versión del modelo"},
                         ensure_ascii=False))
        return 1
    if not previous.artifact_path or Path(previous.artifact_path).name != previous.artifact_path:
        print(json.dumps({"ok": False, "error": "la ejecución base no tiene artefacto"},
                         ensure_ascii=False))
        return 1
    previous_path = config.paths.model_outputs / previous.artifact_path
    if not previous_path.is_file():
        print(json.dumps({"ok": False, "error": "no se encuentra el artefacto base"},
                         ensure_ascii=False))
        return 1
    if not model.entities:
        print(json.dumps({"ok": False, "error": "el modelo no tiene entidades"},
                         ensure_ascii=False))
        return 1
    try:
        prior_table = run.read_artifact(previous_path)
        source_table = read_any(Path(args.file), sheet=args.sheet)
        plan = rules.list_for(conn, args.model_id)
        entity = model.entities[0]
        delta = run.execute(
            {entity.name: source_table}, rules_by_source={entity.name: plan},
            join_plan=[], output=entity.name,
        )
        keys = [key.strip() for key in args.key.split(",") if key.strip()]
        combined, _ = engine.align_tables([prior_table, delta])
        result, dedupe_report = engine.deduplicate(combined, keys=keys, keep="last")
    except (UnsupportedFormat, SheetNotFound, EmptySheet, engine.EngineError,
            run.RunError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    source_hash = _file_sha256(Path(args.file))
    source_metadata = [{"name": previous.source_name,
                        "sha256": previous.source_sha256,
                        "run_id": previous.run_id},
                       {"name": Path(args.file).name, "sha256": source_hash}]
    lineage = run.describe_lineage(
        output=previous.output, rules_applied=plan, table=result,
        model_version=model.version, sources=source_metadata,
        parameters={"operation": "incremental", "keys": keys,
                    "previous_run_id": previous.run_id,
                    "keep": "last"},
    )
    run_id = run.record(
        conn, args.model_id, table=result, output=previous.output,
        artifact_dir=config.paths.model_outputs,
        source_name=Path(args.file).name, source_sha256=source_hash,
        lineage=lineage,
    )
    print(json.dumps({
        "ok": True, "run_id": run_id, "previous_run_id": previous.run_id,
        "rows_in_delta": len(source_table.rows), "rows_out": len(result.rows),
        "duplicates_replaced_or_removed": dedupe_report["duplicates_removed"],
        "columns": result.columns,
    }, ensure_ascii=False, indent=2))
    return 0


def _file_sha256(path: Path) -> str:
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _model_diff(args, conn) -> int:
    """Compara dos ejecuciones y describe qué cambió (M5)."""
    from motor.model import diff, run

    try:
        before = run.get(conn, args.run_a)
        after = run.get(conn, args.run_b)
    except run.RunNotFound as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    result = diff.compare(before.fingerprint, after.fingerprint)
    print(json.dumps({
        "ok": True,
        "changed": result.changed,
        "reason": result.reason,
        "run_a": args.run_a,
        "run_b": args.run_b,
        "rows_a": before.fingerprint.rows,
        "rows_b": after.fingerprint.rows,
    }, ensure_ascii=False, indent=2))
    return 0


def _model_export_run(args, conn, config) -> int:
    """Exporta el Parquet inmutable de una ejecución a un archivo local nuevo."""
    from motor.model import exports, run
    from motor.sources.tables import Table

    try:
        record = run.get(conn, args.run_id)
    except run.RunNotFound as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    if not record.artifact_path or Path(record.artifact_path).name != record.artifact_path:
        print(json.dumps({"ok": False, "error": "la ejecución no tiene artefacto"},
                         ensure_ascii=False))
        return 1
    artifact = config.paths.model_outputs / record.artifact_path
    if not artifact.is_file():
        print(json.dumps({"ok": False, "error": "no se encuentra el artefacto"},
                         ensure_ascii=False))
        return 1
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        arrow_table = pq.read_table(artifact)
        columns = list(arrow_table.column_names)
        rows = [[row.get(column) for column in columns] for row in arrow_table.to_pylist()]
        output_path = exports.export_table(Table(columns=columns, rows=rows), Path(args.output))
    except (OSError, ValueError, pa.ArrowException, exports.ExportError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "run_id": args.run_id,
                      "rows": len(rows), "columns": columns,
                      "output_file": str(output_path)}, ensure_ascii=False, indent=2))
    return 0


def _model_as_dict(model) -> dict:
    return {
        "ok": True,
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
