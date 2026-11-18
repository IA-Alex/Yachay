# Evaluación experimental de dlt + DuckDB para Yachay

**Fecha:** 2026-11-11  
**Alcance:** evaluación aislada; no autoriza incorporar dlt como dependencia
oficial ni modifica la arquitectura, los ADRs o los contratos de Yachay.

## 1. Resumen ejecutivo

dlt es técnicamente viable como componente acotado de ingestión y
normalización dentro del worker, pero no es un reemplazo directo del motor de
control de Yachay.

La prueba confirmó ventajas concretas para leer CSV, JSONL y Parquet, inferir y
normalizar esquemas, aplanar estructuras anidadas, evolucionar columnas y
realizar cargas `merge` con claves. También confirmó que dlt introduce su
propio estado, staging, trazas, esquemas y tablas internas en DuckDB. Eso entra
en tensión con las decisiones de Yachay: SQLite WAL como autoridad del estado,
Parquet inmutable, `manifest.json` como commit, y reconciliación explícita del
worker.

**Dictamen: APTO CON CONDICIONES.** dlt puede evaluarse como adaptador interno
de lectura/normalización, siempre que Yachay conserve el control de trabajos,
estado, publicación, deduplicación exacta y recuperación. No se recomienda
permitir que dlt sea el orquestador o la autoridad de los datasets.

## 2. Línea base arquitectónica revisada

La fuente canónica es el documento embebido en
`state.json_schema.properties.documento_arquitectura.default` de
`src/documentacion_motor_datos_local/flow.json`; no se modificó.

> **Retirado (2026-11-18):** ese `flow.json` ya no está en el repositorio. Para
> la evaluación se usó tal cual; hoy la referencia arquitectónica vigente son los
> ADR de `docs/adr/` y `docs/modelado/ARQUITECTURA-MODELADO.md`.

Responsabilidades actuales de Yachay:

| Componente | Responsabilidad contractual |
|---|---|
| DuckDB | Único motor de transformación: validación, perfilado, inferencia por columna, limpieza, joins, deduplicación y exportación. |
| SQLite WAL | Solo estado y metadatos: jobs, runs, archivos, esquemas, transformaciones, cache semántica, datasets y auditoría. |
| Parquet | Salida inmutable y versionada, con compresión zstd y partes identificables. |
| Worker | Scanner, admisión de disco, claim atómico, lease/heartbeat, ejecución, GC y reconciliación. |
| Manifest | Marca de commit del dataset; contiene hashes, `job_key`, versiones, esquema, filas, partes y trazabilidad. Es la fuente de verdad publicada. |
| Gestión de trabajos | Máquina `claimed → queued → running → committed → archived`, con ramas `failed`, `quarantined`, `cancelled` y `deferred`; `job_id`, `job_key`, `run_id` y `attempt` son distintos. |

Los ADRs 001–013 mantienen estas fronteras: DuckDB único (ADR 002), Parquet y
manifest como publicación (ADR 004), SQLite reconstruible desde manifests
(ADR 005), cola y lease en SQLite sin broker (ADR 007), evolución por huella
(ADR 012), y límites/admisión/recuperación ante presión (ADR 013).

## 3. Compatibilidad verificada

### Python y dependencias

- Yachay declara `>=3.10,<3.14`.
- dlt declara `>=3.10,<3.15`, por lo que existe intersección para las versiones
  soportadas por Yachay. La compatibilidad sintáctica no garantiza que toda la
  resolución de dependencias sea estable.
- La prueba aislada instaló `dlt==1.32.0`, `duckdb==1.5.6`, `pyarrow==26.0.1`
  y `psutil==7.2.2` en `/tmp/yachay-dlt-eval.7Endwh/.venv`. No se modificaron
  `pyproject.toml`, `uv.lock` ni el entorno oficial.
- El repositorio de dlt marca el proyecto como Apache-2.0 y producción estable.
  dltHub es un producto comercial separado; el experimento usó únicamente OSS
  dlt.

Fuentes primarias: [pyproject.toml de dlt](https://github.com/dlt-hub/dlt/blob/devel/pyproject.toml),
[repositorio dlt](https://github.com/dlt-hub/dlt) y
[guía oficial de instalación](https://github.com/dlt-hub/dlt/blob/devel/docs/website/docs/hub/getting-started/installation.md).

### DuckDB y operación local

dlt acepta una ruta local de DuckDB o una conexión DuckDB existente. La prueba
usó rutas locales, sin red, credenciales ni servicios persistentes. La
documentación oficial muestra que el destino crea tablas internas como
`_dlt_loads`, `_dlt_pipeline_state` y `_dlt_version`.

Fuente: [destino DuckDB de dlt](https://dlthub.com/docs/dlt-ecosystem/destinations/duckdb).

### Formatos y normalización

La fuente `filesystem` de dlt ofrece lectores para CSV, JSONL y Parquet. En la
prueba:

- CSV: se cargaron 3 filas y se infirió `id` como `BIGINT`, `value` como
  `VARCHAR` y `nullable` como `BOOLEAN`; un valor vacío llegó como `NULL`.
- JSONL: se cargaron 2 filas; `meta.kind` se normalizó a `meta__kind` y el
  `NULL` se conservó.
- Parquet: se cargaron 2 filas desde un archivo Parquet generado localmente.
- JSON probado significa JSON Lines (`.jsonl`). No se probó un documento JSON
  único con un array raíz; no se debe extender esta evidencia a ese formato.

La normalización y evolución de esquema son capacidades nativas de dlt, pero
su política no es automáticamente la política de Yachay. dlt gestiona hashes y
versiones de esquema propios; Yachay exige huella de esquema, política de
compatibilidad por dataset y manifest versionado.

Fuente: [schema y evolución de dlt](https://dlthub.com/docs/general-usage/schema),
[filesystem source](https://dlthub.com/docs/dlt-ecosystem/verified-sources/filesystem).

## 4. Pruebas aisladas y evidencia

### Procedimiento

Se utilizó un entorno temporal fuera del repositorio. Los datasets fueron
pequeños y sintéticos. La ejecución fue local y no invocó APIs, LLMs ni
servicios externos.

Comando conceptual ejecutado:

```text
uv venv /tmp/yachay-dlt-eval.7Endwh/.venv --python 3.12
uv pip install --python /tmp/yachay-dlt-eval.7Endwh/.venv/bin/python \
  'dlt[duckdb]' pyarrow psutil
python experiment.py
```

### Resultados observados

| Caso | Resultado observado |
|---|---|
| CSV | 3 filas; tipos `BIGINT`, `VARCHAR`, `BOOLEAN`; 1 `NULL`. |
| JSONL | 2 filas; objeto anidado aplanado como `meta__kind`; `NULL` conservado. |
| Parquet | 2 filas cargadas correctamente. |
| Evolución | Segunda carga añadió `new_number BIGINT`; quedaron 2 filas. |
| Duplicados exactos con `append` | 2 filas; no hay deduplicación implícita por contenido. |
| Duplicados exactos con `merge` + `primary_key=id` | 1 fila en la primera carga y 1 tras repetir la misma carga. |
| Duplicados con valores distintos | `primary_key` conserva una fila; la elección por defecto es arbitraria según la documentación. Con `dedup_sort=desc` se conservó el valor con la fecha mayor. |
| Fallo controlado de extracción | La carga fallida produjo `PipelineStepFailed`; tras reintentar con otro lote solo quedó el lote exitoso (`id=2`). No se verificaron todos los puntos de crash. |

El comportamiento de `merge`, `primary_key` y `dedup_sort` coincide con la
documentación oficial: [merge loading](https://dlthub.com/docs/general-usage/merge-loading).

### Rendimiento observado

Las mediciones son exploratorias, no benchmarks:

- Tiempo total del proceso de prueba: aproximadamente **4.77 s** (`real`), con
  **1.23 s** de usuario y **0.23 s** de sistema.
- Tiempo por carga individual: aproximadamente **0.12–0.24 s** para 2–3 filas.
- RSS máxima del proceso, acumulada durante el mismo proceso: aproximadamente
  **179–276 MB** según el caso y el momento de medición.
- El RSS no es una medición aislada por caso y los datasets no representan una
  carga productiva. No se derivan límites para `MemoryMax`,
  `duckdb.memory_limit`, CPU, concurrencia ni spill.

La documentación de rendimiento de dlt advierte que no se deben ejecutar en
paralelo pipelines con el mismo nombre y directorio de trabajo, y que cargas
concurrentes sobre el mismo dataset requieren coordinación de staging.

Fuente: [optimización y limitaciones de rendimiento de dlt](https://dlthub.com/docs/reference/performance).

## 5. Funcionalidades reutilizables

### Candidatas a complementar Yachay

1. Lectores por lotes para CSV, JSONL y Parquet.
2. Normalización de registros estructurados y aplanado de estructuras anidadas.
3. Inferencia inicial de tipos y creación/migración de tablas DuckDB.
4. Cargas `append`, `replace` y `merge` con claves declaradas.
5. Persistencia de estado de pipeline y reanudación de load packages cuando
   falla la conexión durante la fase de carga.

### No deben delegarse a dlt

- Admisión por espacio, estabilidad del archivo y cálculo del hash de entrada.
- Claim atómico, leases, heartbeat, cancelación y reencolado.
- `job_key`, `job_id`, `run_id`, `attempt` y reglas de retry de Yachay.
- Deduplificación exacta por contenido normalizado y reglas versionadas.
- Política de huella de esquema, compatibilidad mayor/menor y cuarentena.
- Publicación Parquet con `fsync`, rename atómico, `latest` y `manifest.json`.
- Sincronización SQLite/manifest y reconstrucción tras crash.
- GC, retención, auditoría, límites de recursos y hardening systemd.
- Gateway LLM, cache semántica, revisión manual y configuración de red.

## 6. Conflictos arquitectónicos

### Estado duplicado

dlt mantiene estado en un directorio de pipeline (`state.json`, esquemas,
trazas y carpetas de extract/normalize/load) y en tablas DuckDB `_dlt_*`.
Yachay exige que SQLite WAL sea el único estado operativo y que el manifest sea
la fuente de verdad del dataset publicado. Permitir que ambos sistemas decidan
el estado produciría dos máquinas de verdad y complicaría la reconciliación.

### Publicación y atomicidad

La prueba verificó cargas en tablas DuckDB, no la secuencia de publicación
`tmp → fsync → manifest → rename → latest → SQLite`. dlt no demuestra por sí
solo la atomicidad cruzada que Yachay requiere entre filesystem y SQLite.

### Deduplicación

`primary_key` no equivale a deduplicación exacta por contenido. Además, la
elección por defecto entre varias filas con la misma clave es arbitraria; un
`dedup_sort` puede hacerla determinista respecto a una columna, pero no sustituye
la huella completa y las reglas de normalización de Yachay.

### Concurrencia y staging

El worker puede evolucionar su `worker_concurrency`, mientras que dlt exige
evitar pipelines con el mismo nombre/directorio en paralelo y advierte sobre
staging compartido. La integración tendría que usar un pipeline y directorio
de trabajo únicos por `run_id`, o imponer serialización explícita.

### Evolución de esquema

dlt puede migrar tablas cuando cambia el esquema, pero Yachay necesita decidir
si el cambio es compatible, una nueva versión menor, una nueva versión mayor o
un rechazo a `quarantine/`. Esa decisión debe seguir siendo de Yachay y quedar
registrada en SQLite y manifest.

## 7. Comparación de alternativas

| Criterio | A. DuckDB nativo | B. dlt + DuckDB acotado |
|---|---|---|
| Complejidad inicial | Más código propio para lectores y normalización. | Menos código para formatos y cargas básicas; más adaptación de límites. |
| Dependencias | Alineada con la arquitectura y menor superficie. | Añade dlt, fsspec y su grafo; Parquet puede requerir pyarrow. |
| Estado | Un solo estado operativo en SQLite + manifest. | Estado Yachay más estado dlt en archivos/DuckDB. |
| Integridad | Contratos diseñados directamente para Yachay. | Debe aislarse dlt para no asumir sus garantías como las de Yachay. |
| Deduplicación | Control exacto por reglas y huella propias. | `primary_key`/`dedup_sort` útiles, pero semánticamente distintos. |
| Recuperación | Se implementa conforme a los puntos de crash de Yachay. | Reintento de load packages probado solo para un fallo de extracción; falta verificar crash/reconciliación completa. |
| Parquet versionado | Publicación nativa con manifest y `latest`. | El destino DuckDB carga tablas; dlt no reemplaza el contrato de publicación. |
| RAM/CPU | Control directo sobre DuckDB y worker. | Medición pequeña; dlt agrega buffers, normalizador y archivos temporales. |
| Mantenibilidad | Menos piezas y más control. | Reduce código repetitivo, pero añade una dependencia madura que requiere pinning y pruebas de upgrades. |
| Valor | Máxima fidelidad al diseño actual. | Valor claro solo en un adaptador de ingestión/normalización. |

## 8. Condiciones para una futura integración experimental

Si se retoma la opción B, las condiciones mínimas serían:

1. dlt queda detrás de una interfaz de Yachay para lectura/normalización; no
   controla jobs ni publica datasets canónicos.
2. Cada `run_id` usa un `pipeline_name` y `pipelines_dir` únicos, o el worker
   serializa explícitamente el acceso.
3. El destino intermedio no es el dataset publicado final. Yachay valida la
   salida, calcula su huella, escribe Parquet, genera el manifest y ejecuta la
   secuencia de publicación.
4. El estado dlt se trata como artefacto temporal/auxiliar y nunca sustituye
   SQLite WAL, `job_key`, leases, auditoría ni reconciliación.
5. La deduplicación exacta de Yachay se ejecuta después de normalizar, con sus
   reglas y versiones; `primary_key` de dlt solo se usa cuando la semántica del
   dataset lo autoriza.
6. Se fijan versiones compatibles de dlt, DuckDB, pyarrow y Python, con pruebas
   de upgrade y sin aceptar dependencias transitivas sin revisión.
7. Antes de cualquier integración definitiva se ejecutan pruebas de crash en
   cada frontera de publicación, presión de memoria, concurrencia y
   reconstrucción desde manifest.

## 9. Limitaciones de la evidencia

- No se midieron cargas representativas ni escenarios de presión/OOM.
- No se probó JSON con array raíz; solo JSONL.
- No se probó una caída del proceso en cada paso de extract, normalize, load,
  fsync, rename o transacción SQLite.
- No se probó concurrencia entre pipelines ni worker concurrency mayor que uno.
- No se comparó estadísticamente con una implementación DuckDB nativa; la
  alternativa A se evaluó por responsabilidades y complejidad arquitectónica,
  no por un benchmark equivalente.
- No se verificaron todas las combinaciones de tipos, zonas horarias, fechas,
  encoding, columnas incompatibles o archivos corruptos.
- Los resultados no garantizan atomicidad, deduplicación exacta, durabilidad ni
  recuperación completa para Yachay.

## 10. Dictamen final

**APTO CON CONDICIONES.**

dlt aporta valor real para ingestión por lotes, lectura de formatos y
normalización, y funciona localmente con DuckDB dentro del rango de Python de
Yachay. La integración solo es compatible si se limita a un adaptador interno
y Yachay conserva todas las responsabilidades que forman sus contratos
esenciales: estado SQLite, máquina de trabajos, deduplicación exacta,
publicación Parquet, manifest, reconciliación y límites operativos.

Este dictamen no autoriza añadir dlt al proyecto oficial ni iniciar la
implementación del motor con dlt. La interfaz de adaptación y la prueba inicial
de concurrencia ya están realizadas; antes de una integración definitiva aún se
requieren pruebas de crash consistency, presión de memoria, cargas
representativas y reconstrucción desde manifests.

## 11. Segundo experimento: frontera del adaptador y concurrencia

### Contrato local añadido

Se añadió `src/documentacion_motor_datos_local/ingestion_contract.py` como
frontera de tipos, sin importar dlt. Define:

> **Retirado (2026-11-18):** el módulo ya no existe en el repositorio. Se
> conserva la descripción del experimento; el contrato vigente está en
> `src/motor/`.

- `InputArtifact`: metadatos estables calculados por Yachay (`sha256`, tamaño,
  `job_key` y `run_id`).
- `NormalizedBatch`: resultado temporal con ruta de staging, filas, esquema,
  huella y versión del adaptador.
- `IngestionAdapter`: protocolo que normaliza un artefacto sin asumir control
  sobre SQLite, manifests o publicación.

El contrato no implementa ingesta, no publica datasets y no añade una decisión
de arquitectura nueva.

### Concurrencia aislada

Se ejecutaron dos procesos pequeños en paralelo, ambos con el mismo
`pipeline_name` y el mismo `pipelines_dir`, contra un DuckDB local:

- trabajador 1: `ok`, 1,000 filas, RSS máxima aproximada de 134.58 MiB;
- trabajador 2: `FileExistsError` al crear el directorio compartido del
  pipeline, RSS máxima aproximada de 70.49 MiB;
- base final: 1,000 filas, no 2,000.

Esto confirma experimentalmente que dlt no debe compartir ese directorio de
trabajo entre ejecuciones concurrentes. No demuestra que todos los patrones de
concurrencia fallen igual, pero sí que el patrón compartido no es aceptable para
el worker de Yachay.

La prueba de recuperación anterior sigue siendo limitada al fallo controlado
de extracción; no se simuló la terminación del proceso en cada frontera de
normalización, escritura, `fsync`, rename o transacción SQLite.

### Estado de validación

Las pruebas locales del repositorio pasan: **6 tests OK**. La interfaz queda
validada como contrato de tipos, no como implementación funcional del motor.
