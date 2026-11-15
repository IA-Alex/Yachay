"""Utilidades de formato para la interfaz (todo en español, sin dependencias)."""

from __future__ import annotations

from datetime import datetime


def format_epoch(value: float | None) -> str:
    """Convierte un epoch (segundos, REAL de SQLite) a fecha legible local."""
    if value in (None, 0):
        return "—"
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return "—"


def short_id(value: str | None, length: int = 8) -> str:
    """Abrevia un identificador largo (job_id, run_id) para la tabla."""
    if not value:
        return "—"
    return value if len(value) <= length else value[:length] + "…"


def human_bytes(size: int) -> str:
    """Tamaño en unidades legibles."""
    step = 1024.0
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < step:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} TB"
