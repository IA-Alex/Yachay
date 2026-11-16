# Mantenimiento de dependencias

## Fuente de verdad

- `pyproject.toml` declara dependencias directas y restricciones.
- `uv.lock` fija las versiones resueltas y hashes de los artefactos.
- `.github/workflows/ci.yml` fija las acciones de CI por SHA.
- Este documento registra el proceso; no duplica versiones para evitar que la
  documentación se desactualice respecto al manifiesto y al lockfile.

## Actualizaciones

Dependabot, configurado en `.github/dependabot.yml`, revisa mensualmente el
ecosistema `uv` y GitHub Actions. Agrupa cambios menores y de parche de Python;
los cambios mayores se revisan por separado. Dependabot abre pull requests y no
los integra automáticamente.

Cada actualización debe pasar el workflow `CI`: `uv sync --locked`, build,
Ruff y la suite existente. Antes de aceptar cambios mayores, revisar los ADR y
documentos de operación/API afectados y ejecutar una auditoría de dependencias
con `pip-audit`. Los cambios que modifiquen contratos o comportamiento deben
actualizar la documentación correspondiente en el mismo cambio.

## Reportes locales

Las ejecuciones del modelado conservan su huella en SQLite y el resultado en
Parquet local, con acceso restringido al usuario. La vista **Visualizaciones**
genera un HTML autónomo con SVG y tabla accesible; no incorpora bibliotecas de
gráficos, CDN ni servicios de dashboard. Regenerar y borrar informes requiere
confirmación en escritorio; borrar el HTML no elimina el Parquet.

La vista **Propuestas** es opcional y usa DeepInfra por HTTPS. Requiere
`DEEPINFRA_API_KEY` en el entorno del proceso del motor. Solo envía instrucción,
nombres de columnas y estadísticas de perfil, nunca filas. El proveedor puede
cobrar por uso; el panel visual local funciona sin credenciales.

La coherencia del contrato se comprueba con `python scripts/sync_api_docs.py`.
Si se modifica la tabla de rutas, actualiza el índice generado con
`python scripts/sync_api_docs.py --write`; la verificación local y CI fallan si
OpenAPI o el índice no coinciden.
