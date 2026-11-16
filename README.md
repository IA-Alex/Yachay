# Yachay — Motor local de modelado de información

Yachay es una herramienta local para **perfilar, transformar, validar y cruzar
información** de hojas de cálculo y documentos. Permite definir y guardar un
modelo para aplicarlo de forma repetible a nuevas fuentes, comparar ejecuciones
y revisar los resultados desde una aplicación de escritorio, la CLI o una API
local.

## Estado

**Implementado y verificado** (535 pruebas, 7 omitidas, 100% de cobertura sobre
`src/` — gate con `fail_under=100` —, ejecutable con `./scripts/test.sh`):

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
ejecuciones versionadas, diff y vistas de modelo/resultados. La arquitectura
mantiene 13 capacidades de alcance: 11 funciones de modelado y 2 capacidades
operativas de ingesta/publicación.

La asistencia usa DeepInfra opcional (`DEEPINFRA_API_KEY`) y solo envía la instrucción,
nombres de columna y estadísticas del perfil; nunca filas. El informe visual se genera
localmente y no requiere proveedor ni dependencia de gráficos. El uso del proveedor de IA
puede tener costo según sus tarifas.

**Fuera de alcance:** gateway LLM general, generación arbitraria de código o funciones,
normalización formal, modelo dimensional, linaje y gobernanza.

## Documentación

| Documento | Contenido |
| --- | --- |
| `docs/modelado/ARQUITECTURA-MODELADO.md` | Arquitectura del motor de modelado (13 capacidades, stack, plan) |
| `docs/modelado/DIAGRAMAS-ARQUITECTURA.md` | Diagramas (componentes, secuencia, ERD, estados, dependencias) |
| `docs/adr/ADR-014…020.md` | Decisiones: leases, `attempt`, API, GC, modelado, propuestas y reportes |
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

# Ejecutar el modelo sobre datos nuevos (M3)
.venv/bin/python -m motor model run 1 hoja.csv

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

## Contrato visual

La identidad cromática oficial de Yachay y su contrato de accesibilidad están en
la **sección 18** del documento canónico embebido. Es la única fuente de tokens
visuales para el escritorio y las salidas de CLI.
