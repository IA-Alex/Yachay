# Yachay — Motor local de modelado de información

Yachay es una herramienta local para **perfilar, transformar, validar y cruzar
información** de hojas de cálculo y documentos. Permite definir y guardar un
modelo para aplicarlo de forma repetible a nuevas fuentes, comparar ejecuciones
y revisar los resultados desde una aplicación de escritorio, la CLI o una API
local.

## Estado

**Baseline implementado y verificado antes de la ampliación ADR-021** (535
pruebas, 0 omitidas, 100% de cobertura sobre `src/` — gate con
`fail_under=100` —, ejecutable con `./scripts/test.sh`):

- Ingesta local: scanner, admisión de disco, cola con 9 estados, lease atómico.
- Pipeline CSV → **DuckDB** → **Parquet** versionado con `manifest.json`.
- Publicación atómica, reconciliación, recuperación de leases, commit idempotente.
- Retención y GC efectivos (versiones de dataset, `failed/`, `quarantine/`, `tmp/`).
- **API v1** sobre socket Unix (24 operaciones, envelope con conjunto cerrado de errores).
- **App de escritorio** (tkinter/ttk): nueve vistas para estado, carga, modelado, ejecución,
  resultados, visualizaciones, propuestas, trabajos y datasets.
- **Resultados locales y visualización**: Parquet con permiso privado por ejecución, e
  informes HTML autónomos con SVG accesible, tabla y sin llamadas a CDN.
- **Propuestas de modelado acotadas**: lenguaje natural → reglas existentes → vista previa
  → aceptación explícita → nueva versión; la ejecución sigue siendo manual.
- **systemd** de usuario (`Type=exec`) y logging JSON a journald.

El reencuadre a **motor de modelado** (ADR-019) está entregado en M1–M6:
perfilado, modelo con entidades y claves, reglas de transformación, cruces,
ejecuciones versionadas, diff y vistas de modelo/resultados. ADR-021 amplía ese
baseline con funciones nativas locales de alineación de esquemas, calidad,
transformación y exportación, por etapas. T-024–T-033 pasaron una aceptación
funcional CLI con fixtures temporales y reconciliación independiente; ver
`audit/validacion-T022-2026-11-24.md`. El gate de pruebas unitarias no presenta
fallos, pero su umbral de cobertura global aún falla (80% frente a 100%); está
registrado como T-035.

La asistencia usa DeepInfra desde un adaptador aislado y solo envía instrucción,
nombres de columna y estadísticas del perfil; nunca filas. La API local no recibe
la credencial ni tiene red IP. El informe visual se genera localmente sin proveedor ni
dependencia de gráficos. El uso de IA puede tener costo según las tarifas vigentes.

**Fuera de alcance:** gateway LLM general, generación arbitraria de código o funciones,
normalización formal, modelo dimensional, linaje y gobernanza.

## Documentación

| Documento | Contenido |
| --- | --- |
| `docs/modelado/ARQUITECTURA-MODELADO.md` | Arquitectura del motor de modelado (13 capacidades, stack, plan) |
| `docs/modelado/DIAGRAMAS-ARQUITECTURA.md` | Diagramas (componentes, secuencia, ERD, estados, dependencias) |
| `docs/adr/ADR-014…020.md` | Decisiones: leases, `attempt`, API, GC, modelado, propuestas y reportes |
| `docs/adr/ADR-021-ampliacion-motor-local.md` | Alcance aprobado para ampliar las funciones nativas locales |
| `docs/modelado/ACCIONES-NATIVAS.md` | Uso, formatos y límites de las nuevas acciones locales |
| `docs/seguimiento-local.md` | Tracker de tareas y decisiones |
| `docs/mantenimiento-dependencias.md` | Fuente de verdad y proceso de actualización de dependencias y documentación |
| `docs/api/REVISION-API-V1.md` | Revisión del contrato de la API v1 |
| `docs/api/RUTAS-API.md` | Índice de rutas sincronizado desde el registro de API |
| `docs/systemd/README.md` | Instalación y operación de las unidades |
| `docs/evaluacion-dlt-duckdb.md` | Evaluación de dlt (no adoptado) |
| `audit/hallazgos.md` | Auditoría de la UI de escritorio (tkinter) |

El documento canónico (arquitectura v2, §1-§18) sigue siendo la fuente de los
**principios y contratos**; el estado **real** de cada función está en este README
y en el tracker.

## Install

```bash
uv sync --frozen
```

## Uso

```bash
# Perfilar una hoja o documento (M1)
.venv/bin/python -m motor inspect hoja.xlsx

# Crear un modelo desde un archivo y verlo (M2)
.venv/bin/python -m motor model new bitacora hoja.xlsx
.venv/bin/python -m motor model show 1

# Aplicar el modelo sobre datos nuevos (M3, muestra el resultado sin guardar)
.venv/bin/python -m motor model preview 1 hoja.csv

# Ejecutar y persistir la ejecución (M5: huella, Parquet e informe)
.venv/bin/python -m motor model exec 1 hoja.csv

# Alinear esquemas distintos y exportar un archivo nuevo
.venv/bin/python -m motor model align enero.csv febrero.csv --output combinado.xlsx

# Validar filas con un contrato JSON local
.venv/bin/python -m motor model validate datos.csv calidad.json

# Transformar y exportar localmente
.venv/bin/python -m motor model transform datos.csv operacion.json --output salida.ods

# Incorporar un archivo delta a una ejecución anterior
.venv/bin/python -m motor model incremental 1 17 delta.csv --key id

# Worker: procesa la bandeja de entrada
.venv/bin/python -m motor run --root .

# API v1 sobre socket Unix
.venv/bin/python -m motor api --root .

# Interfaz de escritorio
python -m yachay_desktop --root .     # desde desktop/
```

**Formatos de entrada:** `.csv`, `.xlsx`, `.ods` (tablas) y `.docx`, `.txt`,
`.pdf` con texto seleccionable (se normalizan a `documento, linea, texto`).

## Gate

```bash
./scripts/test.sh          # ruff + unittest + coverage
```

## Rendimiento

```bash
.venv/bin/python scripts/bench.py --repeat 3   # perfilado + ejecución + Parquet
```

Mide sobre datos sintéticos en un directorio temporal (no toca `data/`).
El motor limita a `MAX_TABLE_ROWS = 100_000` **incluyendo la cabecera**, así que
el máximo son 99 999 filas de datos; el banco rechaza valores mayores con un
mensaje explícito.

## Contrato visual

La identidad cromática oficial de Yachay y su contrato de accesibilidad están en
la **sección 18** del documento canónico embebido. Es la única fuente de tokens
visuales para el escritorio y las salidas de CLI.
