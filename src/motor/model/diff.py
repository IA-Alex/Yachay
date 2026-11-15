"""Comparación de dos ejecuciones por su huella (ADR-019, M5; función D1).

El diff dice **si** cambió el resultado y **por qué**: columnas distintas,
número de filas distinto o mismo perfil con contenido distinto. No conserva ni
muestra los datos: trabaja sobre las huellas.
"""

from __future__ import annotations

from dataclasses import dataclass

from motor.model.run import Fingerprint


@dataclass(frozen=True)
class DiffResult:
    changed: bool
    reason: str


def compare(before: Fingerprint, after: Fingerprint) -> DiffResult:
    """Compara dos huellas y describe la diferencia, si la hay."""
    if before.columns != after.columns:
        perdidas = [c for c in before.columns if c not in after.columns]
        nuevas = [c for c in after.columns if c not in before.columns]
        partes = []
        if nuevas:
            partes.append("columnas nuevas: " + ", ".join(nuevas))
        if perdidas:
            partes.append("columnas perdidas: " + ", ".join(perdidas))
        if not partes:
            # Mismo conjunto de columnas en otro orden: sin este caso el motivo
            # salía vacío («cambiaron las columnas ()») y no explicaba el cambio.
            partes.append("mismo conjunto en otro orden")
        return DiffResult(True, "cambiaron las columnas (" + "; ".join(partes) + ")")

    if before.rows != after.rows:
        delta = after.rows - before.rows
        signo = "+" if delta > 0 else ""
        return DiffResult(
            True, f"cambió el número de filas: {before.rows} → {after.rows} ({signo}{delta})"
        )

    if before.hash != after.hash:
        return DiffResult(True, "mismo número de filas pero distinto contenido")

    return DiffResult(False, "")
