"""Yachay command-line entrypoint.

Run the local worker: `yachay run [--root DIR] [--config defaults.toml] [--once]`.
Run the local API: `yachay api [--root DIR] [--config defaults.toml] [--socket PATH]`.
Inspect an input: `yachay inspect ARCHIVO [--sheet HOJA]` (ADR-019, M1).
"""

from __future__ import annotations

import argparse
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
    m_run = model_sub.add_parser("run", help="ejecutar un modelo sobre un archivo")
    m_run.add_argument("model_id", type=int, help="identificador del modelo")
    m_run.add_argument("file", help="archivo de entrada")
    m_run.add_argument("--root", default=".", help="raíz del proyecto (default: .)")
    m_run.add_argument("--sheet", default=None, help="hoja a leer (.xlsx/.ods)")
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
    m_exec = model_sub.add_parser("exec", help="ejecutar un modelo y guardar su huella (M5)")
    m_exec.add_argument("model_id", type=int, help="identificador del modelo")
    m_exec.add_argument("file", help="archivo de entrada (fuente principal)")
    m_exec.add_argument("--root", default=".", help="raíz del proyecto (default: .)")
    m_exec.add_argument("--sheet", default=None, help="hoja a leer (.xlsx/.ods)")
    m_diff = model_sub.add_parser("diff", help="comparar dos ejecuciones (M5)")
    m_diff.add_argument("run_a", type=int, help="ejecución anterior")
    m_diff.add_argument("run_b", type=int, help="ejecución posterior")
    m_diff.add_argument("--root", default=".", help="raíz del proyecto (default: .)")
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

    if args.model_command == "run":
        return _model_run(args, conn)

    if args.model_command == "join":
        return _model_join(args)

    if args.model_command == "exec":
        return _model_exec(args, conn)

    if args.model_command == "diff":
        return _model_diff(args, conn)

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


def _model_run(args, conn) -> int:
    """Ejecuta un modelo sobre un archivo y muestra el resultado (M3)."""
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
        "columns": result.columns,
        "rows_in": {"izquierda": len(left.rows), "derecha": len(right.rows)},
        "rows_out": len(result.rows),
        "sample": result.rows[:5],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _model_exec(args, conn) -> int:
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

    run_id = run.record(conn, args.model_id, table=result, output=entity.name)
    fp = run.fingerprint(result)
    print(json.dumps({
        "ok": True, "run_id": run_id, "model_id": args.model_id,
        "rules": len(plan), "columns": result.columns,
        "rows_out": len(result.rows), "hash": fp.hash[:16],
    }, ensure_ascii=False, indent=2))
    return 0


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
