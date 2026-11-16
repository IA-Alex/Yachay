"""Offline, dependency-free HTML reports for persisted model runs."""

from __future__ import annotations

import html
import math
from pathlib import Path

import pyarrow.parquet as pq


def generate_report(data_path: Path, report_path: Path, *, title: str) -> Path:
    """Render a self-contained report with an SVG chart and accessible table."""
    table = pq.read_table(data_path)
    rows = table.to_pylist()
    columns = table.column_names
    chart = _chart(rows, columns)
    visible_columns = columns[:50]
    headers = "".join(
        f"<th scope=\"col\">{html.escape(str(c))}</th>" for c in visible_columns
    )
    body = "".join(
        "<tr>" + "".join(
            f"<td>{html.escape(_display(row.get(column)))}</td>" for column in visible_columns
        ) + "</tr>"
        for row in rows[:500]
    )
    omitted = len(rows) - min(len(rows), 500)
    row_note = f"Se muestran 500 de {len(rows)} filas." if omitted else f"{len(rows)} filas."
    column_note = (
        f" Se muestran 50 de {len(columns)} columnas."
        if len(columns) > len(visible_columns) else ""
    )
    note = row_note + column_note
    document = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta http-equiv="Content-Security-Policy"
 content="default-src 'none'; style-src 'unsafe-inline'; img-src data:;">
<title>{html.escape(title)}</title><style>
body{{font:16px system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 20px;
color:#17212b;background:#fff}}
h1{{font-size:1.7rem}}.chart{{max-width:100%;overflow:auto;margin:28px 0}}
svg{{max-width:100%;height:auto}}
table{{border-collapse:collapse;width:100%;font-size:.9rem}}
th,td{{text-align:left;border-bottom:1px solid #d8dee5;padding:8px}}
th{{position:sticky;top:0;background:#f3f6f8}}.muted{{color:#536171}}
</style></head><body><main><h1>{html.escape(title)}</h1>
<p class="muted">{note} Informe local; no carga recursos externos.</p>
<section aria-label="Visualización de datos" class="chart">{chart}</section>
<section aria-label="Datos de la ejecución"><h2>Datos</h2><div class="chart">
<table><thead><tr>{headers}</tr></thead>
<tbody>{body}</tbody></table></div></section></main></body></html>"""
    report_path = Path(report_path)
    report_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    temporary = report_path.with_name(f".{report_path.name}.part")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(document)
            stream.flush()
        temporary.chmod(0o640)
        temporary.replace(report_path)
    finally:
        temporary.unlink(missing_ok=True)
    return report_path


def _chart(rows: list[dict], columns: list[str]) -> str:
    if not rows or not columns:
        return '<p class="muted">No hay datos para representar.</p>'
    numeric = next((name for name in columns if _is_numeric(rows, name)), None)
    if numeric is not None:
        values = [float(row[numeric]) for row in rows if _finite(row.get(numeric))]
        if not values:
            return '<p class="muted">No hay valores numéricos válidos.</p>'  # pragma: no cover
        low, high = min(values), max(values)
        span = high - low or 1.0
        points = " ".join(
            f"{20 + index * 760 / max(1, len(values) - 1):.1f},"
            f"{180 - (value - low) * 150 / span:.1f}"
            for index, value in enumerate(values[:100])
        )
        return (
            f'<svg viewBox="0 0 800 220" role="img" aria-label="Serie de valores: '
            f'{html.escape(str(numeric), quote=True)}"><title>{html.escape(str(numeric))}</title>'
            f'<polyline fill="none" stroke="#2563eb" stroke-width="3" points="{points}"/>'
            f'<text x="20" y="210">{low:g}</text><text x="740" y="210">{high:g}</text></svg>'
        )
    category = columns[0]
    counts: dict[str, int] = {}
    for row in rows:
        key = _display(row.get(category)) or "(vacío)"
        counts[key] = counts.get(key, 0) + 1
    top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:12]
    maximum = max(value for _, value in top)
    bars = []
    for index, (label, value) in enumerate(top):
        y = 12 + index * 28
        width = value * 540 / maximum
        bars.append(
            f'<text x="0" y="{y + 15}">{html.escape(label[:24])}</text>'
            f'<rect x="190" y="{y}" width="{width:.1f}" height="20" fill="#2563eb"/>'
            f'<text x="{195 + width:.1f}" y="{y + 15}">{value}</text>'
        )
    height = 24 + len(bars) * 28
    return (f'<svg viewBox="0 0 800 {height}" role="img" aria-label="Conteo por '
            f'{html.escape(str(category), quote=True)}"><title>Conteo por '
            f'{html.escape(str(category))}</title>{"".join(bars)}</svg>')


def _is_numeric(rows: list[dict], name: str) -> bool:
    values = [row.get(name) for row in rows if row.get(name) is not None]
    return bool(values) and all(_finite(value) for value in values)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _display(value: object) -> str:
    return "" if value is None else str(value)
