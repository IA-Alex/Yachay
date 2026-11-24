# Arquitectura — Motor de modelado de datos (Yachay v2)

> Estado: **propuesta aceptada** (ADR-019). Sustituye al encuadre de «motor de
> ingesta» del MVP v1. La ingesta **se conserva** como capacidad.

## 1. Objetivo

Acomodar, validar, comparar y cruzar **unas pocas hojas de cálculo** (2-3),
de forma **repetible**, desde una **app de escritorio**, para uso frecuente del
propio operador.

El activo no es el resultado de hoy: es el **modelo guardado** que se reaplica
mañana con las hojas nuevas.

## 2. Alcance funcional — 13 capacidades

Ordenadas de **menor a mayor complejidad**. Cada una con su fuente.

La taxonomía contiene **11 funciones de modelado** (las tablas siguientes) y
**2 capacidades operativas transversales** ya existentes: ingesta/admisión y
publicación/versionado de datasets. Estas últimas pertenecen al plano de
ingesta, no al catálogo del modelo.

### Nivel 1 — Triviales (solo leer y reescribir columnas)

| ID | Función | Qué hace | Implementación |
| --- | --- | --- | --- |
| **B1** | Transform & shape | Renombrar, filtrar, reordenar, tipar columnas | DuckDB `SELECT`/`CAST` |
| **C7** | Perfilado de datos | Nulos, duplicados, rangos, cardinalidad por columna | DuckDB agrega y cuenta |
| **C3** | Estándares de nombres | Unificar vocabulario (mapa de valores: `Cuzco→Cusco`) | Tabla de mapeo en SQLite |
| **C4** | Dominios / valores permitidos | Listas cerradas válidas por columna | CHECK declarado en el modelo |
| **B2** | Append vertical | Apilar filas de varias fuentes con los mismos nombres de columna y en el mismo orden; conserva duplicados | DuckDB `UNION ALL` |

La implementación B2 admite append vertical. La combinación horizontal por clave queda dentro de B3 (joins); alinear fuentes con esquemas distintos requiere una definición funcional aparte.

### Nivel 2 — Simples (definición de esquema)

| ID | Función | Qué hace | Implementación |
| --- | --- | --- | --- |
| **A1** | Entidades y atributos | Declarar tabla + columnas con nombre y tipo | Metadatos en SQLite |
| **A2** | Claves (PK / FK) | Marcar identificador y referencia | Metadatos en SQLite |

### Nivel 3 — Medias (relación y repetibilidad)

| ID | Función | Qué hace | Implementación |
| --- | --- | --- | --- |
| **B3** | Joins | Cruzar tablas por clave (inner/left/…) | DuckDB `JOIN` |
| **C5** | Integridad referencial | Detectar claves foráneas huérfanas | `LEFT JOIN … WHERE … IS NULL` |
| **D1** | Diff de versiones | Qué cambió entre dos ejecuciones del modelo | Comparación de resultados + hash |
| **E4** | Automatización | Reejecutar el modelo guardado con datos nuevos | El «modelo» como artefacto versionado |

**Fuera de alcance (Niveles 4-5):** normalización formal, modelo dimensional,
linaje, capa semántica, gobernanza. Pertenecen a un ERwin/PowerDesigner ligero.

## 3. Modelo de dominio

Cuatro conceptos. Todos se guardan en SQLite (el estado es autoridad, ADR-005).

```
Fuente (source)
  ├── nombre        "ventas_enero"
  ├── archivo       ventas.xlsx
  ├── hoja          "Enero"
  └── perfil        columnas detectadas + tipos

Entidad (entity)                    ← A1, A2
  ├── nombre        "ventas"
  ├── atributos     [(columna, tipo, nulo?) …]
  └── clave         PK (y FKs si aplica)

Regla (rule)                        ← B1, C3, C4
  ├── nombre        "normalizar_ciudad"
  ├── tipo          rename | cast | map | domain | filter
  ├── destino       columna
  └── parámetros    {Cuzco→Cusco, Qosqo→Cusco}

Cruce (join)                        ← B3, C5
  ├── nombre        "ventas_sin_cliente"
  ├── izquierda     entidad.ventas
  ├── derecha       entidad.clientes
  ├── por           [nombre]
  └── tipo          inner | left | anti (huérfanos)
```

**Modelo (model):** el agregado `fuentes + entidades + reglas + cruces`, con
nombre y versión. Las entidades y reglas se guardan y se reejecutan desde CLI,
API y escritorio; los cruces se pueden guardar en el catálogo y se ejecutan
con fuentes explícitas mediante el motor/CLI. La API actual expone el informe
de huérfanos, no un editor de cruces persistidos.

## 4. Flujo de trabajo

```
1. Cargar hojas        (Excel/CSV elegidas por el usuario)
2. Definir el modelo   (entidades + reglas + cruces)      ← se GUARDA
3. Ejecutar            (DuckDB: shape → merge → join cuando se proveen fuentes)
4. Ver resultados      (perfilado, inconsistencias, cruces, diff)
5. Repetir             (hojas nuevas + el MISMO modelo)
```

## 5. Stack

| Capa | Tecnología | Estado | Motivo |
| --- | --- | --- | --- |
| Lenguaje | Python `>=3.11,<3.14` | ✅ existe | El proyecto ya es Python |
| Motor de transformación | **DuckDB** | ✅ existe | Joins, agregados, pivot nativos (ADR-002) |
| Estado / catálogo | **SQLite WAL** | ✅ existe | Autoridad de estado (ADR-005) |
| Salida | **Parquet + manifiesto** | ✅ existe | Inmutable, versionado (ADR-004) |
| Lectura Excel | `openpyxl` (`.xlsx`) | ✅ existe | Volcar hoja → DuckDB |
| Lectura ODS | `odfpy` | ✅ existe | Volcar hoja → DuckDB |
| Entrada CSV | `read_csv_auto` | ✅ existe | Sin cambios |
| API | `http.server` sobre AF_UNIX | ✅ existe | 24 operaciones (registro + OpenAPI) |
| UI | tkinter/ttk | ✅ existe | 9 vistas: operaciones, propuestas y visualizaciones |
| Supervisión | systemd `--user` | ✅ existe | `Type=exec`, sin watchdog |
| Logs | JSON a stdout → journald | ✅ existe | `log.py` |
| Tests | `unittest` + `coverage` | ✅ existe | 535 pruebas, 7 omitidas, gate de cobertura 100% |

### Asistencia y salidas visuales

Las propuestas usan una allowlist de las cinco reglas deterministas existentes
(`rename`, `cast`, `map`, `domain`, `filter`). DeepInfra recibe solo la
instrucción y metadatos del perfil; la API valida la respuesta, la interfaz
muestra la vista previa y la aceptación clona entidades, atributos, claves,
reglas y cruces antes de añadir cambios. No se ejecuta automáticamente.

Las ejecuciones nuevas guardan resultado Parquet en `data/model-outputs/` y
metadatos opcionales en SQLite (migración aditiva v4). Los informes son HTML
estáticos locales, con CSP sin recursos de red, SVG y tabla de datos. Se
recomienda el informe para inspección; la salida tabular sigue disponible para
análisis posterior. Informes y artefactos quedan ligados al operador local.

**Dependencias de entrada:** `openpyxl` para `.xlsx` y `odfpy` para `.ods` ya
están declaradas en `pyproject.toml`. No se añade ninguna dependencia de
transformación: **DuckDB sigue siendo el único motor** (ADR-002).

## 6. Encaje de las 13 capacidades en el stack

| Función | Dónde se ejecuta | Pieza |
| --- | --- | --- |
| B1, B2 (append vertical), B3, B4* | SQL de transformación | DuckDB |
| C7 | SQL de agregación | DuckDB |
| C3, C4 | Mapa + CHECK en el modelo | SQLite + SQL |
| C5 | Anti-join | DuckDB |
| A1, A2 | Metadatos de entidad | SQLite |
| D1 | Hash de resultados + comparación | SQLite + DuckDB |
| E4 | Orquestación del modelo | worker/CLI |

\* B4 (pivot/unpivot) es Nivel 2 pero **opcional**; se evalúa tras Nivel 1.

## 7. Lo que se reutiliza y lo que es nuevo

| Pieza | Reutiliza | Nuevo |
| --- | --- | --- |
| Persistencia (SQLite, Parquet, manifiesto) | ✅ | — |
| Ingesta (scanner, cola, estados, leases, reconcile, GC) | ✅ | — |
| API (envelope, rutas, servidor) | ✅ | Endpoints de modelo |
| UI (9 vistas, tokens §18) | ✅ | Estado, carga, modelo, ejecución, resultados, visualizaciones, propuestas, trabajos y datasets |
| Infra (systemd, logging, límites) | ✅ | — |
| **Motor de transformación con reglas** | ✅ | — |
| **Catálogo de modelo (entidades, reglas, cruces)** | ✅ | — |
| **Lectura Excel** | ✅ | — |
| **Diff de versiones** | ✅ | — |

## 8. Plan por fases

| Fase | Contenido | Funciones | Prerrequisito | Estado |
| --- | --- | --- | --- | --- |
| **M1** | Lectura de Excel/CSV + perfilar | C7 | — | ✅ entregada |
| **M2** | Definir el modelo: entidades, atributos, claves | A1, A2 | M1 | ✅ entregada |
| **M3** | Transformar: shape, merge, estándares, dominios | B1, B2 (append vertical), C3, C4 | M2 | ✅ entregada |
| **M4** | Cruzar: joins + integridad referencial | B3, C5 | M2 | ✅ entregada |
| **M5** | Repetir: automatización + diff de versiones | E4, D1 | M3, M4 | ✅ entregada |
| **M6** | UI: modelo y resultados | todas | M5 | ✅ entregada |

Cada fase: tests en rojo → implementación → gate verde → ADR/D-00x si toca
contrato. El patrón de trabajo es el ya establecido.

## 9. Riesgos y límites declarados

| Riesgo | Impacto | Mitigación |
| --- | --- | --- |
| Excel con celdas combinadas / cabeceras sucias | Perfilado incorrecto | M1 debe **declarar** esos casos, no adivinar |
| Varias hojas con esquemas distintos | Join imposible | La entidad puede fallar; se reporta |
| Reglas incompletas | Resultado parcial | Perfilado (C7) muestra qué quedó sin cubrir |
| `openpyxl` con `.ods` | No lee ODS | Declarar; evaluar `odfpy` solo si hace falta |
| Alcance se desborda a Niveles 4-5 | Proyecto sin final | El baseline M1–M6 es de 13 capacidades; las ampliaciones aprobadas se enumeran en ADR-021 y el tracker |

## 10. Documentos relacionados

- `docs/adr/ADR-019.md` — la decisión de reencuadre y este alcance.
- Este documento y los ADRs del repositorio son la referencia arquitectónica
  mantenida; ya no existe un generador CrewAI dentro del producto.
- `docs/seguimiento-local.md` — tracker (T-015 en adelante).

## 11. Ampliación nativa local (ADR-021)

El baseline de 13 capacidades permanece como referencia de M1–M6. ADR-021
aprueba una ampliación por etapas para alinear esquemas, validar por fila,
diagnosticar cruces, deduplicar, derivar/agrupar/pivotear columnas, normalizar
formatos y exportar resultados tabulares locales. El estado y validación de cada
acción se reflejan en `docs/seguimiento-local.md`; el uso y los límites están en
`docs/modelado/ACCIONES-NATIVAS.md`. No se incorpora Workspace/MCP, SQL libre ni
ejecución de código.
