# Seguimiento local de Yachay

Este registro sustituye temporalmente a un tracker externo. Se versiona junto
con el código y la documentación, y debe evolucionar con el proyecto; no es
un contrato cerrado.

## Tareas

| ID | Estado | Tarea | Criterio de salida |
|---|---|---|---|
| T-001 | Parcialmente cumplida | Validar el contrato declarativo del Flow y su ejecución local | Validación y carga OK; la ejecución funcional de las etapas sigue pendiente de un proveedor de modelo |
| T-002 | Cumplida | Definir pruebas mínimas para el Flow y sus contratos de salida | `tests/test_flow_contract.py` cubre el camino feliz, los errores de contrato y el documento canónico embebido, sin invocar modelos |
| T-003 | Parcialmente cumplida | Separar la documentación canónica de los artefactos generados | La fuente canónica sigue en `flow.json` y `docs/generated/` está ignorado; falta el generador de artefactos que escriba ahí |
| T-004 | Cumplida | Definir la frontera del adaptador de ingestión/normalización sin añadir dlt | Existe un contrato Python aislado y probado |
| T-005 | Parcialmente cumplida | Evaluar concurrencia y recursos de dlt en entorno temporal | Se conocen los límites de directorio compartido; faltan crash consistency y cargas representativas |
| T-006 | Cumplida | Implementar la recuperación de leases vencidos y el commit idempotente por `job_key` (D-005) | `jobs.requeue_expired`, detección por `job_key`, reorden de `reconcile` y C9 en el worker; ADR-014 y ADR-015 |
| T-007 | Cumplida | Cubrir la recuperación con pruebas de crash y de los tres estados del lease | 13 pruebas nuevas: 5 de `jobs`, 5 de reconciliación y 3 de commit del worker; las dos pruebas de crash se escribieron antes de implementar y fallaron con el job atrapado en `running` |
| T-008 | Cumplida | Cablear `jobs.heartbeat` en el bucle del worker (P1-1) | `_process` late al inicio y tras el pipeline con el reloj del claim; límite declarado: un CSV que por sí solo exceda el TTL sigue pareciendo vencido (T-011); `ADR-017` |
| T-009 | Cumplida | Cerrar los estados atrapados por input irrecuperable (hallazgo de revisión) | Sin fila en `input_files` o con archivo ausente → `failed` con auditoría; `running` sin lease se recupera como vencido; 5 pruebas nuevas y 4 corregidas, 101 pruebas y 100% de cobertura |
| T-010 | Cumplida | API v1 sobre socket Unix (fase B, D-008) | Veinte operaciones, envelope con conjunto cerrado de errores, `yachay api`; pruebas sobre el socket real (incluida la parada limpia por `SIGTERM`) y 100% de cobertura (`ADR-016`) |
| T-011 | Pendiente | Heartbeat real entre lotes (P1) | Hoy solo hay dos puntos de latido porque `run_csv_pipeline` es de un solo disparo; exige que el pipeline tenga lotes |
| T-012 | Cumplida | Retirada de filas de `datasets` para hacer efectivo el prune (D-005, hallazgo P2) | `_mark_missing_datasets` retira la fila sin respaldo (salvo si un job la respalda) y `prune_dataset_versions` retira fila+versión en una transacción; ADR-018 |
| T-013 | Pendiente | `WatchdogSec` + `sd_notify` en las unidades systemd (P1) | Hoy `Type=exec` sin watchdog porque el pipeline es de un solo disparo; un watchdog mataría CSVs lentos y, con `MAX_ATTEMPTS=3`, los volvería fallos deterministas |
| T-014 | Cumplida | Logging estructurado del worker para journald (H1) | `src/motor/log.py` emite una línea JSON por ciclo y por job; nunca valores de datos (prueba con valor centinela); `motor-worker` y `motor-api` verificados en systemd real |
| T-015 | **Cumplida** | **M1 — Lectura de Excel/CSV y perfilado** (ADR-019) | `yachay inspect` lee `.xlsx/.csv/.ods/.txt/.docx/.pdf` y perfila cada columna (nulos, duplicados, rango, tipo); probado con la bitácora real (96 filas, 11 columnas) |
| T-016 | **Cumplida** | **M2 — Definir el modelo**: entidades, atributos y claves | `models/entities/attributes/entity_keys` en SQLite con versión; `yachay model new/show`; la clave se infiere si hay columna única |
| T-017 | **Cumplida** | **M3 — Transformar**: shape, merge, estándares y dominios | Reglas `rename/cast/map/domain/filter` con nombre; el comando aplica el plan con DuckDB y devuelve el resultado (96→68 filas en la prueba real). Desde H-04 el comando se llama `yachay model preview` y no persiste: lo llamaba `model run`, que parecía guardar y no lo hacía |
| T-018 | **Cumplida** | **M4 — Cruzar**: joins e integridad referencial | Cruces `inner/left/anti` encadenables, guardados en `joins`; `yachay model join` reporta huérfanos (6 sin fecha en la bitácora real) |
| T-019 | **Cumplida** | **M5 — Repetir**: automatización y diff de versiones | `yachay model exec` guarda la huella (columnas+filas+sha256, sin datos) y `model diff` dice qué cambió; probado 96→68 con la bitácora real |
| T-020 | **Cumplida** | **M6 — UI de modelado y resultados** | Siete vistas: Cargar archivo, Modelo, Ejecutar, Resultados (+ estado, cola y catálogo). Endpoints `models`, `profile`, `runs`, `diff`, `orphans`. Flujo completo verificado: perfil → modelo → ejecución → diff |
| T-021 | **Cumplida** | Robustez ante entrada no confiable (auditoría de backend F-01/F-02/F-03) | El worker falla el job en vez de caer ante contenido no confiable; los lectores traducen errores de parseo a `UnsupportedFormat` (400, no 500); el retry no resucita un job terminal; 7 pruebas de regresión, 412 pruebas y 100% de cobertura |
| T-022 | En curso — prioridad | Ampliar funciones nativas del motor local (ADR-021) | Completar T-024–T-033 por entregas; fuentes y resultados locales, sin Workspace/MCP; DeepInfra se mantiene como dependencia remota solo para asistencia IA |
| T-023 | Pendiente — posterior a T-022 | Ampliar la asistencia de DeepInfra con interacción conversacional | Permitir describir necesidades en lenguaje natural y recibir propuestas dentro de las capacidades del motor; mantener vista previa y aceptación humana, sin ejecutar ni aplicar cambios automáticamente. Requiere conexión a DeepInfra |
| T-024 | Implementación añadida; validación pendiente | Alinear esquemas entre fuentes | CLI `model align`; mapeo explícito, unión estable, reporte de columnas ausentes y exportación opcional; append estricto conservado |
| T-025 | Implementación añadida; validación pendiente | Validaciones de calidad por fila | CLI `model validate`; reglas required/unique/range/format y salida de fila/campo/motivo, sin valores en SQLite |
| T-026 | Implementación añadida; validación pendiente | Diagnóstico de cruces | CLI `model join` incluye claves duplicadas, tasas por filas coincidentes/no coincidentes y filas sin clave |
| T-027 | Implementación añadida; validación pendiente | Deduplicación determinista | CLI `model dedupe`; claves explícitas, first/last, orden preservado y claves ausentes preservadas como filas independientes |
| T-028 | Implementación añadida; validación pendiente | Columnas derivadas seguras | `model transform` admite expresiones declarativas acotadas; sin SQL libre ni ejecución de código |
| T-029 | Implementación añadida; validación pendiente | Agregaciones y pivoteo | `model transform` admite count/sum/avg/min/max, pivot y unpivot con límites |
| T-030 | Implementación añadida; validación pendiente | Exportación tabular local | Crear archivos nuevos CSV/XLSX/ODS para resultados y errores; `model export-run` exporta Parquet guardado |
| T-031 | Implementación añadida; validación pendiente | Formatos de fecha y número por fuente | `model transform` normaliza solo columnas y formatos declarados; informa errores sin adivinar |
| T-032 | Implementación añadida; validación pendiente | Ejecución incremental | CLI `model incremental` transforma un delta, alinea columnas, reemplaza por clave y exige misma versión del modelo |
| T-033 | Implementación añadida; validación pendiente | Metadatos de reproducibilidad | Ejecuciones nuevas registran versión de modelo/motor, fuentes y SHA-256, parámetros y huella; sin linaje empresarial |

## Decisiones

### D-001 — Seguimiento local sin tracker externo

- **Fecha:** 2026-11-11
- **Decisión:** No se configura Jira, Linear, GitHub Issues ni Azure DevOps.
- **Motivo:** Yachay es un proyecto personal y no tiene equipo ni gestor de
  incidencias externo.
- **Consecuencia:** Las tareas y decisiones se mantienen en este archivo y se
  revisan mediante Git.

### D-002 — La arquitectura existente es baseline evolutiva

- **Fecha:** 2026-11-11
- **Decisión:** El documento de arquitectura embebido en `flow.json` se trata
  como la línea base versionada actual, no como una especificación congelada.
- **Motivo:** El README lo identifica como fuente canónica y el diseño debe
  poder cambiar mediante commits trazables.
- **Consecuencia:** Toda modificación arquitectónica debe actualizar la fuente
  canónica y registrar aquí el motivo y el impacto.

### D-003 — El runner de pruebas es `unittest`, no `pytest`

- **Fecha:** 2026-11-11
- **Decisión:** La suite se ejecuta con `unittest` desde el virtualenv del
  proyecto. `pytest` no se declara en el grupo `dev` de `pyproject.toml`.
- **Motivo:** `pytest` no es resoluble desde la caché offline de `uv`
  (`uv pip install --offline` falla), por lo que declararlo rompería la
  instalación reproducible del entorno.
- **Consecuencia:** `scripts/test.sh` es el gate canónico (ruff + cobertura +
  `unittest discover -s tests -t .`). Si en el futuro el entorno tiene red, la
  decisión puede revisarse, pero no antes de demostrar que `uv sync` funciona
  con `pytest` declarado.

### D-004 — Layout de pruebas: paquete `tests` con `tests/unit`

- **Fecha:** 2026-11-11
- **Decisión:** Las pruebas del paquete `motor` viven en `tests/unit/` (antes
  `tests/motor/`) y el descubrimiento se hace con
  `unittest discover -s tests -t .`.
- **Motivo:** `tests/__init__.py` obliga a que `tests` sea un paquete y fuerza
  `top_level_dir=tests`. Con el directorio llamado `tests/motor/`, ese paquete
  sombreaba al paquete real `motor` y producía `ModuleNotFoundError:
  motor.config`. Renombrar el directorio elimina la colisión sin tocar
  `sys.path` ni los imports del código de producción.
- **Consecuencia:** `scripts/test.sh` fija `-t .` para que el descubrimiento
  use la raíz del repositorio y las pruebas se importen como `tests.*`. Tras el
  renombrado, `discover -s tests` también pasa (verificado: 84 pruebas), pero la
  raíz explícita evita que un futuro directorio de pruebas vuelva a sombrear un
  paquete real.

### D-005 — Hueco de lease/requeue en `reconcile`: implementado

- **Fecha:** 2026-11-11
- **Estado:** Implementada (cierra el hueco; ver D-006 y D-007).
- **Decisión original:** No se modifica `src/motor/reconcile.py` para reclamar
  leases vencidos ni para reencolar jobs en `running` tras un crash. El hueco
  quedaba documentado aquí como deuda conocida.
- **Motivo original:** La semántica de `job_key`/lease es un contrato sensible
  (ADR-007: claim atómico con lease) y un cambio de comportamiento en
  reconciliación puede duplicar trabajo o publicar dos veces si se implementa
  sin diseño explícito y sin pruebas de crash reales.
- **Resolución (2026-11-11):** se implementa con ADR-014 y ADR-015:
  `jobs.requeue_expired`, detección por `job_key`, reorden de `reconcile` y C9
  (commit defensivo). `mark_queued` no se amplía. Se cumplieron las condiciones
  que la decisión original exigía: se respeta `lease_until`, la recuperación es
  idempotente frente a `job_key`, hay pruebas de los tres estados (`claimed`,
  `queued`, `running`) y de dos ventanas de crash reales, y este registro queda
  actualizado.
- **Consecuencia:** un job cuyo worker murió después del claim ya se recupera
  solo: si el resultado está publicado pasa a `committed` sin reejecutar; si no,
  vuelve a `queued` hasta `MAX_ATTEMPTS` y después queda `failed`. Queda abierto
  el cableado del heartbeat (T-008).

### D-006 — Recuperación de leases y commit idempotente (ADR-014)

- **Fecha:** 2026-11-11
- **Estado:** Aceptada e implementada.
- **Decisión:** recuperar leases vencidos con `jobs.requeue_expired` (nueva
  operación, `mark_queued` intacto), reordenar `reconcile` para reconstruir
  manifests antes de mover o reencolar jobs, completar por `job_key` cualquier
  job cuyo dataset ya esté comprometido, ignorar manifests duplicados por
  `job_key` y absorber en C9 el `IntegrityError` de duplicado solo cuando el
  dataset ya existe.
- **Motivo:** cerraba el P0-3 (job atrapado en `running` y `IntegrityError` al
  reejecutar trabajo ya publicado) sin migración, sin dependencias y sin tocar
  el `CHECK` de estados.
- **Consecuencia:** el estado se reconstruye desde el commit físico; el detalle
  completo, el coste asumido y las alternativas descartadas están en
  `docs/adr/ADR-014.md`.

### D-007 — Semántica de `attempt` y tope de reintentos (ADR-015)

- **Fecha:** 2026-11-11
- **Estado:** Aceptada e implementada.
- **Decisión:** `attempt` cuenta ejecuciones iniciadas y se incrementa en el
  mismo `UPDATE` atómico del claim; `job_runs.attempt` registra ese valor;
  `MAX_ATTEMPTS = 3` en `src/motor/limits.py` acota los reintentos y
  `requeue_expired` marca `failed` al agotarlos.
- **Motivo:** ADR-011 separaba los identificadores pero no fijaba cuándo
  incrementa el contador ni su máximo; sin tope, un fallo determinista produce
  livelock.
- **Consecuencia:** extiende ADR-011 sin sustituirlo; el detalle está en
  `docs/adr/ADR-015.md`.

### D-008 — API v1 sobre socket Unix, sin dependencias nuevas (ADR-016)

- **Fecha:** 2026-11-11
- **Estado:** Aceptada e implementada.
- **Decisión:** servir la API v1 con `http.server` de la stdlib sobre un socket
  `AF_UNIX` (`<root>/data/motor.sock`, modo `0o660`, sin listener TCP), con un
  envelope único `{api_version, ok, data|error}` y un conjunto cerrado de
  códigos de error. La superficie actual son 20 operaciones de lectura y control:
  `health`, lista y detalle de jobs, `cancel`, `retry`, `POST /v1/inputs`,
  lista, detalle y manifest de datasets.
- **Motivo:** el entorno es offline y no admite dependencias nuevas (FastAPI no
  se puede resolver), y el contrato del §2.2/§2.4 no existía en el repositorio:
  había que fijarlo antes de poder probarlo.
- **Consecuencia:** la API no reconcilia, no reclama leases, no ejecuta
  pipelines y no crea jobs; `POST /v1/inputs` solo deja el fichero en `inbox/`,
  así que el escáner sigue siendo la única admisión (ADR-008). `retry` cubre el
  reintento explícito del §7 (resetea `attempt` a 0 y audita
  `api_retry_job`). El detalle, los riesgos declarados (autenticación opcional, sin
  cursor) y las alternativas descartadas están en `docs/adr/ADR-016.md`.

### D-009 — Parámetros de GC fuera de `config_hash` (ADR-017)

- **Fecha:** 2026-11-11
- **Estado:** Aceptada e implementada.
- **Decisión:** `DATASET_VERSIONS_TO_KEEP`, `FAILED_RETENTION_DAYS` y
  `QUARANTINE_RETENTION_DAYS` viven en `src/motor/limits.py`, no en
  `defaults.toml`, junto a `MAX_ATTEMPTS`.
- **Motivo:** `defaults.toml` entra en `config_hash` y `config_hash` entra en
  `job_key` (ADR-006/ADR-011); mover allí un parámetro de retención cambiaría el
  `job_key` de todos los inputs y rompería la deduplicación por un valor que no
  altera ningún resultado publicado.
- **Consecuencia:** cambiar la retención no reejecuta nada ni invalida
  resultados; el detalle de la política está en `docs/adr/ADR-017.md`.

## Próximo paso

La deuda de arnés (lint, runner, layout, cobertura) está cerrada. La
recuperación de leases vencidos (D-005) también: quedó implementada con ADR-014
y ADR-015. La API v1 (D-008) cierra además el reintento explícito del §7, que
reutiliza el contador de `MAX_ATTEMPTS` sin campo nuevo. La retención del §12 y
el heartbeat (D-009, ADR-017) cierran T-008. Lo que queda abierto es funcional:

- ejecución real del Flow con un proveedor de modelo (T-001);
- heartbeat real entre lotes (T-011), que exige que el pipeline tenga lotes;
- `semantic_cache` y retención de `audit_events`, bloqueadas por la política de
  datos personales que aún no existe (§12, fuera de alcance en ADR-017).

Ver `audit/validacion-pendientes.md`: los pendientes T-001/T-003/T-005/T-011/
T-013 **no son bloqueos técnicos** (los cinco son corregibles); son **fuera de
alcance** de esta versión y ninguno causa fallo funcional observable hoy.

## Auditoría técnica de arranque — 2026-11-11

### CrewAI Flow

- CrewAI instalado: `1.15.39`; coincide con la restricción de
  `pyproject.toml` y con `uv.lock`.
- `flow.json` carga correctamente con `Flow.from_declaration`.
- Las cinco etapas declaradas son, en orden:
  `planificar_documentacion` → `redactar_arquitectura` →
  `redactar_operacion` → `integrar_y_verificar` →
  `entregar_documentacion`.
- La entrada declarada es `state.documento_arquitectura`, con esquema JSON
  válido y valor por defecto en la propiedad.
- Las referencias `${outputs.<etapa>.raw}` apuntan únicamente a etapas ya
  completadas; la cadena de `listen` es lineal y alcanzable desde la única
  etapa `start`.
- Las cinco etapas son `call: agent`, no expresiones deterministas ni crews.
  Cada una declara `openai/gpt-4o`; por tanto, el Flow tiene ejecución real
  mediante la CLI de CrewAI, pero su ejecución funcional requiere un proveedor
  de modelo y no se ejecutó durante esta auditoría.
- Cargar/validar la declaración no equivale a ejecutar las etapas ni a validar
  la calidad de sus salidas. No existe una implementación Python alternativa ni
  pruebas de comportamiento en el repositorio.

### Fullstack Dev Kit

- El archivo `.claude/dev-kit.json` es un bootstrap requerido cuando se usan
  capacidades de tracker: `issue-fetch`, `issue-update`, `plan-backlog` y
  `work-story`.
- El archivo no es necesario para el perfil de stack Python ni para las
  comprobaciones estáticas locales.
- El esquema documentado del kit exige un `tracker.type` externo (`jira`,
  `linear`, `github` o `azure`); no define un backend local ni un adaptador de
  registro en Git.
- El seguimiento local de este archivo es compatible como proceso del proyecto,
  pero no será consumido automáticamente por las capacidades de tracker del
  kit. No se creó `.claude/dev-kit.json` para evitar una integración inventada.

### Evidencia ejecutada

- Parseo JSON, validación del JSON Schema de `state` y parseo TOML: OK.
- Import de `documentacion_motor_datos_local` y `crewai`: OK.
  > **Retirado (2026-11-18):** el paquete se eliminó del repositorio y solo
  > quedaban `.pyc` huérfanos. Esta línea conserva el registro de lo que se
  > verificó en su momento; no describe el estado actual.
- `compileall` sobre `src`: OK.
- Resolución de `[tool.crewai].definition`: OK.
- Carga declarativa con `Flow.from_declaration`: OK.
- Ayuda de `crewai run`: confirma soporte de ejecución de Flow declarativo: OK.
- `pytest`: descartado como runner; no es instalable offline (D-003).
- `unittest discover -s tests -t .`: 84 pruebas, todas OK (D-004).
- `./scripts/test.sh`: ruff 0 errores y cobertura 100% de `src`
  (413 sentencias, 68 ramas).
- Ejecución funcional del Flow y `crewai eval`: deliberadamente no ejecutados;
  requerirían invocar el modelo y/o una traza de ejecución.

### Gate local

`scripts/test.sh` es el único comando necesario para validar el repositorio:

1. `ruff check src tests`
2. `coverage run -m unittest discover -s tests -t .`
3. `coverage report`

Antes de este trabajo el gate no existía: `unittest discover -s tests` sin `-t .`
fallaba con `ModuleNotFoundError: motor.config`, y la cobertura era del 95% con
ramas sin cubrir en `storage/jobs.py`, `worker.py`, `reconcile.py`, `__main__.py`
y `scanner.py`. Ahora esas ramas tienen prueba, el alcance de cobertura incluye
todo `src` y el total es 100%.

### Pruebas deterministas añadidas

`tests/test_flow_contract.py` (24 pruebas, T-002) no invoca agentes ni
proveedores externos:

- **Camino feliz:** la declaración carga con el loader instalado, la cadena
  `start`/`listen` es lineal y alcanzable, y cada etapa declara una única acción
  `call: agent` con `role`, `goal`, `backstory`, `input` y `llm`.
- **Contrato ejecutable:** `contract_violations()` comprueba las reglas
  cruzadas del formato `crewai.flow/v1` (un solo `start`, `emit` solo con
  `router`, `listen` existente y nunca a sí mismo, referencias
  `${outputs.<etapa>}` solo a etapas ya completadas, referencias
  `${state.<campo>}` solo a propiedades declaradas).
- **Errores de contrato:** ocho mutaciones de la declaración real demuestran que
  el validador falla cuando debe (autolisten, destino inexistente, doble
  `start`, referencia hacia adelante, `emit` sin `router`, `state` no declarado,
  `do` en lista, agente sin `input`).
- **Loader real:** cuatro mutaciones confirman qué rechaza `Flow.from_declaration`
  por sí mismo y una prueba documenta el hueco: el loader acepta un `listen`
  colgante, por lo que esa regla solo la cubre el validador del repositorio.
- **Entrada:** el JSON Schema es válido, `documento_arquitectura` es la única
  propiedad requerida, y se rechazan la entrada ausente, la de tipo inválido y
  un esquema inválido.
- **Documento canónico:** el valor por defecto embebido tiene ≥29 000
  caracteres, contiene la sección 15 con las filas `001`–`013` de ADRs, la
  sección 18 del contrato visual y los invariantes `SQLite`, `DuckDB`,
  `manifest.json` y `WAL`.

T-001 sigue parcialmente cumplida: la validación del contrato está probada, pero
la ejecución funcional de las etapas depende de un proveedor de modelo.

### Intervención 2026-11-11 — recuperación de leases (D-005, ADR-014/015)

**Antes** (`./scripts/test.sh`): 84 pruebas OK, `ruff` sin errores, cobertura
100% (413 sentencias, 68 ramas). Un job `running` con lease vencido y dataset ya
publicado quedaba atrapado en `running` para siempre.

**Rojo registrado antes de tocar producción** (dos pruebas de crash escritas
primero, tal como exige la decisión original de D-005):

```
FAIL: test_reconcile_commits_expired_running_job_whose_dataset_exists
AssertionError: 'running' != 'committed'
FAIL: test_crash_after_publish_recovers_on_the_next_cycle
AssertionError: 'running' != 'committed'
Ran 2 tests ... FAILED (failures=2)
```

**Después** (`./scripts/test.sh`): 97 pruebas OK, `ruff` sin errores, cobertura
100% (448 sentencias, 80 ramas). 13 pruebas nuevas: 5 de `jobs`, 5 de
reconciliación y 3 de commit del worker. El plan preveía 12; la decimotercera
cubre la rama genérica de rollback (`except Exception`), que C9 dejó de
ejercitar y sin la cual la cobertura bajaba a 99%.

**Cambios:**

- `src/motor/limits.py`: `MAX_ATTEMPTS = 3` (límite de corrección, no parámetro
  del §16).
- `src/motor/storage/jobs.py`: `claim_next` incrementa `attempt` en el claim;
  nueva `requeue_expired` (un solo `UPDATE ... RETURNING` atómico).
- `src/motor/reconcile.py`: orden manifests → datasets faltantes → jobs con
  dataset comprometido → leases vencidos → movimientos `claimed` → limpieza;
  detección por `job_key`; reconstrucción que ignora manifests duplicados.
- `src/motor/worker.py`: `job_runs.attempt` real y C9 (absorbe solo el
  `IntegrityError` de `job_key` duplicado; el resto se propaga).
- `docs/adr/ADR-014.md`, `docs/adr/ADR-015.md`: decisiones completas.
- `src/documentacion_motor_datos_local/flow.json`: filas `014` y `015` en §15 y
  bullet de calibración en §16 (`git diff --numstat` = `1 1`, una línea física).
  > **Retirado (2026-11-18):** el archivo ya no existe. El cambio descrito se
  > aplicó al documento canónico embebido en ese `flow.json`, que hoy no está en
  > el repositorio; la fuente de los principios es el ADR correspondiente.
- Sin migraciones, sin dependencias nuevas, sin cambios en el `CHECK` de
  estados.

**Riesgos que quedan:** P1-1 heartbeat sin cablear (T-008); si C9 detecta el
duplicado en el commit, el run publicado de más queda sin fila y su recolección
es parte del GC del §12, fuera de alcance; el reintento manual del §7 sigue sin
implementarse.


## Intervención 2026-11-11 (segunda) — estados atrapados por input irrecuperable

**Hallazgo.** El plan aprobado incluía, dentro de la rama `running` del §10, dos
pasos que la implementación no escribió y que el ADR-014 no declaró: un job
activo sin fila en `input_files` y un job cuya ruta registrada ya no existe. El
dictamen anterior afirmó que "ningún job puede quedar atrapado", lo que era más
amplio que lo implementado. Además, `requeue_expired` filtraba
`lease_until IS NOT NULL`, así que un `running` con `lease_until` nulo tampoco se
recuperaba.

**Evidencia (reproducida antes de tocar código, tres ciclos de `reconcile`).**

| Escenario | Antes | Ahora |
| --- | --- | --- |
| `claimed` sin fila en `input_files` | 3 ciclos sin cambio de estado ni acción | `failed` + `reconcile_fail_missing_input_row` en el primer ciclo |
| `running`, lease vencido, archivo ausente | reencolado; el worker moría en DuckDB y gastaba intentos | `failed` + `reconcile_fail_missing_input_file`, `attempt` intacto |
| `running` con `lease_until IS NULL` | 2 ciclos sin cambio | `queued` (`requeue_expired`) en el primer ciclo |

**Rojo primero.** Cuatro pruebas fallaron antes de la implementación:
`test_job_without_an_input_file_row_fails_instead_of_staying_trapped`,
`test_running_job_whose_input_file_is_gone_fails_without_requeue_churn`,
`test_queued_job_whose_input_file_is_gone_fails` y
`test_requeue_expired_recovers_a_running_job_without_a_lease`.

**Verde.** `./scripts/test.sh`: 101 pruebas, `ruff` limpio, cobertura 100%
(469 sentencias, 86 ramas).

**Cambios.**

- `src/motor/reconcile.py`: nuevas `_fail_jobs_without_input` y
  `_fail_jobs_with_missing_input_file`; `_reconcile_jobs` se queda solo con los
  movimientos `claimed` (usa `JOIN` contra `input_files`, sin `continue`
  silencioso); ambas ramas `fail` se ejecutan antes de `_requeue_expired_jobs`.
- `src/motor/storage/jobs.py`: `requeue_expired` acepta
  `lease_until IS NULL OR lease_until < now`.
- `tests/unit/test_worker.py`: 4 pruebas nuevas, la prueba que fijaba el estado
  atrapado se invierte y pasa a exigir `failed`, y tres fixtures
  (`test_reconcile_preserves_tmp_of_an_active_run`,
  `test_reconcile_requeues_an_expired_running_job_without_a_dataset`,
  `test_reconcile_fails_an_expired_running_job_at_the_attempt_cap`) registran
  ahora un input real para seguir midiendo lo que su nombre dice.
- `tests/unit/test_storage_jobs.py`: prueba nueva del lease nulo.
- `docs/adr/ADR-014.md`: decisiones 7 y 8, orden actualizado, regla del lease
  nulo y sección de registro de la desviación. `docs/adr/ADR-015.md`: nota de la
  consecuencia.

**Nota sobre la sección anterior.** La línea que describe el orden de
`reconcile.py` como "manifests → datasets faltantes → jobs con dataset
comprometido → leases vencidos → movimientos `claimed` → limpieza" refleja el
estado de aquella intervención; el orden vigente es el del ADR-014 (con las dos
ramas `fail` antes de los leases vencidos).

**Riesgos que quedan:** si el archivo desaparece entre la reconciliación y
`_process`, el pipeline sigue lanzando y el worker no lo captura (el tope de
intentos lo contiene y el ciclo siguiente lo declara `failed`); P1-1 sigue
abierto (T-008) y el reintento manual del §7 sigue sin implementarse.

## Intervención 2026-11-11 (tercera) — retención/GC (§12) y heartbeat (T-008)

**Alcance.** Implementar la política de retención del §12 dentro del bucle del
worker y cablear `jobs.heartbeat` (T-008, P1-1). Sin dependencias nuevas, sin
cambios de esquema y sin tocar el `CHECK` de estados.

**Rojo primero.** Se escribieron 8 pruebas antes del código
(`tests/unit/test_gc.py` y tres clases nuevas en `tests/unit/test_worker.py`) y
fallaron por la razón esperada: `ImportError: cannot import name 'gc'`,
`TypeError: Worker.__init__() got an unexpected keyword argument 'clock'`,
`AttributeError: module 'motor.worker' has no attribute 'gc'` y
`AssertionError: True is not false` sobre el `.duckdb` huérfano.

**Verde.** `./scripts/test.sh`: 177 pruebas, `ruff` limpio, cobertura 100%
(938 sentencias, 180 ramas), `EXIT=0`. La línea base era 158 pruebas y 849
sentencias.

**Cambios.**

- `src/motor/gc.py` (nuevo): `prune_dataset_versions`, `purge_old_paths` y
  `collect`, con `now` inyectado. Protege `latest` y su destino, las versiones
  con fila viva en `datasets` y todo lo tocado después de `now`; audita
  `gc_prune_dataset_version` (con sha256 del manifest), `gc_purge_failed` y
  `gc_purge_quarantine`.
- `src/motor/limits.py`: `DATASET_VERSIONS_TO_KEEP`, `FAILED_RETENTION_DAYS`,
  `QUARANTINE_RETENTION_DAYS` (D-009).
- `src/motor/reconcile.py`: `_active_run_ids` como definición única de "run
  activo"; `_cleanup_orphan_tmp` limpia además `tmp/<run_id>.duckdb` y deja
  intacto cualquier fichero que no reconoce.
- `src/motor/worker.py`: `clock` inyectable, dos latidos en `_process` y
  `_collect` al final de `run_once`, que absorbe `OSError`/`sqlite3.Error`.
- `tests/unit/test_gc.py` (nuevo, 13 pruebas) y `tests/unit/test_worker.py`
  (6 pruebas nuevas).
- `docs/adr/ADR-017.md`: decisiones, riesgos declarados y alternativas
  descartadas.

**Desviaciones del encargo, declaradas.**

- `purge_old_paths` recibe `conn`: sin conexión no puede auditar, y §12 exige
  registrar toda eliminación.
- §15 del documento embebido no tenía fila `016` (la API v1 nunca se añadió a la
  tabla). Se añaden `016` y `017`; como el documento embebido ocupa una sola
  línea física en `flow.json`, el diff sigue siendo `1 1`. §16 no se toca: el
  bullet "N de versiones por dataset y días de retención de `failed/`,
  `quarantine/` y originales" ya declaraba estos valores como pendientes.
- El guard de frescura es `mtime > now`, sin constante de gracia nueva: la lista
  de constantes aprobada era cerrada y el orden por `mtime` ya protege las `keep`
  más recientes.
- Las purgas de `failed/` y `quarantine/` auditan solo el nombre, sin hash.

**Smoke real** (root temporal, worker real, SQLite real, sin mocks): un
`failed/old.csv` con mtime de hace 40 días se purga y `failed/recent.csv`
sobrevive (`gc_purge_failed` auditado con su nombre); cuatro publicaciones del
mismo dataset dejan cuatro versiones, todas con fila viva en `datasets`, y el
`tmp/<run_id>.duckdb` de cada ciclo se barre en el siguiente (5.º ciclo sin
input → `tmp/` vacío); al retirar una fila de `datasets` fuera del motor, el
pase siguiente borra exactamente esa versión y la audita con el sha256 de su
manifest, con `latest` resolviendo a un directorio existente.

**Hallazgo P2 (declarado, no maquillado).** El prune por conteo es inerte en
producción: cada versión publicada conserva su fila en `datasets`, y la regla
"nunca borrar una versión con fila viva" la protege. La política "últimas N"
está implementada y probada, pero su efecto real exige una retención/archivado
de filas de `datasets` que el motor no tiene. Queda como riesgo P2 en ADR-017.

**Riesgos que quedan.** P1: heartbeat de un solo disparo (T-011), un CSV que por
sí solo exceda el TTL sigue pareciendo vencido. P2: el GC corre en el hilo del
worker y un barrido grande retrasa el siguiente claim; `base_run_id` no existe y
se sustituye por `datasets`; el prune por conteo no actúa mientras la fila viva
exista. P3: `semantic_cache` y retención de `audit_events` fuera de alcance;
"originales en `processed/`" se cumple por borrado al commitear, no por
retención.


### D-010 — Retirada de datos, systemd y logging (ADR-018)

- **Fecha:** 2026-11-12
- **Decisión:** (a) una versión de dataset se retira con su fila de `datasets`
  cuando excede `keep` o cuando su manifest desaparece (salvo si un job la
  respalda); (b) el worker y la API se supervisan con unidades systemd de
  usuario, `Type=exec`, sin watchdog; (c) el worker emite una línea JSON por
  ciclo y por job a stdout (journald), nunca con valores de datos.
- **Motivo:** el prune por conteo era inerte (P2 de ADR-017), la fila huérfana
  se re-auditaba en cada pasada y dejaba un `503` permanente, y sin logs la
  unidad systemd no era verificable.
- **Consecuencia:** el prune actúa de verdad; la retirada es reversible desde el
  manifest; `journalctl --user -u motor-worker` muestra progreso real; el
  endurecimiento systemd queda verificado en el host (reinicio tras `kill -9` y
  sandbox que bloquea escrituras fuera de `data/`). `WatchdogSec` queda en T-013.
### D-011 — Reencuadre a motor de modelado de datos (ADR-019)

- **Fecha:** 2026-11-12
- **Decisión:** Yachay pasa de «motor de ingesta» a **motor de modelado de datos
  local**. Alcance funcional cerrado en **13 capacidades** por nivel de
  complejidad: Nivel 1 completo (transform, perfilado, estándares, dominios,
  merge), Nivel 2 (entidades, claves) y Nivel 3 (joins, integridad referencial,
  diff de versiones, automatización). La ingesta se conserva como capacidad.
- **Motivo:** el uso real es «acomodar, detectar inconsistencias, comparar y
  cruzar 2-3 hojas», que es modelado, no ingesta. El §8 del documento canónico
  declaraba 5 pasos de transformación y solo 1 estaba implementado.
- **Fuentes de la taxonomía:** IBM (*What is data modeling*), DAMA-DMBOK Cap. 5,
  Metadata Matters (*Data Modelling Tool Requirements*), Microsoft (transform &
  model), Kimball (dimensional).
- **Consecuencia:** el activo principal deja de ser el dataset publicado y pasa a
  ser el **modelo guardado** (esquema + reglas + cruces) que se reejecuta. Se
  reutiliza ~40% del código (persistencia, ingesta, API, UI, infraestructura); el
  60% nuevo (modelo, reglas, cruces, diff, Excel) es el que da el valor. Fuera de
  alcance: normalización formal, modelo dimensional, linaje, capa semántica y
  gobernanza.
### D-012 — M1+M2+M3 del motor de modelado (ADR-019)

- **Fecha:** 2026-11-12
- **Decisión:** implementadas las tres primeras fases del motor de modelado:
  M1 (ingesta de `.xlsx`, `.csv`, `.ods`, `.txt`, `.docx`, `.pdf` + perfilado),
  M2 (modelo: entidades, atributos, claves, versionado) y M3 (reglas
  `rename`/`cast`/`map`/`domain`/`filter` + motor de ejecución con DuckDB).
- **Motivo:** el caso de uso real es modelar hojas, no solo ingerirlas (ADR-019).
- **Consecuencia:** `yachay inspect`, `yachay model new/show/run`. La única
  dependencia nueva es `odfpy` (formato `.ods`); el resto ya llegaba con crewai.
  DuckDB sigue siendo el único motor de transformación. Las reglas operan sobre
  las columnas de la **entrada** y el plan es una proyección idempotente.
- **Límite declarado:** un PDF sin texto extraíble se reporta (no se inventa;
  ningún OCR). Las reglas no encadenan por nombre intermedio.

### D-013 — Cruces encadenados con informe de huérfanos (M4, ADR-019)

- **Fecha:** 2026-11-12
- **Decisión:** los cruces se guardan con nombre y se **encadenan** (la salida de
  uno alimenta al siguiente); tres tipos (`inner`, `left`, `anti`). El tipo
  `anti` produce el **informe de huérfanos** (integridad referencial, C5) como
  salida separada. `schema_version` sube a 2.
- **Motivo:** el caso de uso incluye "comparaciones y cruces"; los huérfanos son
  la inconsistencia más común entre dos hojas.
- **Consecuencia:** `yachay model join`. Los identificadores del `ON` se
  cualifican con el alias real de cada lado, porque dos tablas pueden compartir
  el nombre de la columna de unión. El cruce trae todas las columnas de ambos
  lados; el usuario decide después con las reglas de M3.

### D-014 — Ejecuciones con huella y diff (M5, ADR-019)

- **Fecha:** 2026-11-12
- **Decisión:** cada ejecución guarda una **huella** del resultado (columnas,
  número de filas y sha256 del contenido) en la tabla `runs`; `model diff` compara
  dos huellas y describe la diferencia. `schema_version` sube a 3.
- **Motivo:** el uso es repetido; saber *si* cambió y *por qué* evita reexaminar
  todo a mano cada vez.
- **Consecuencia:** la huella **no guarda los datos** (ADR-005: SQLite solo
  estado). El hash detecta cualquier cambio; columnas y conteo explican cuál.
  Un cambio de orden de filas también cambia el hash (se declara).

### D-015 — El modelo se expone por la API (M6, ADR-019/016)

- **Fecha:** 2026-11-12
- **Decisión:** la UI de escritorio accede al modelo por la API v1, no por
  SQLite. Nuevas rutas: `GET/POST /v1/models`, `GET /v1/models/{id}` y
  `GET /v1/profile` (perfila un archivo de la bandeja sin crear nada). La UI
  gana dos vistas: **Cargar** y **Modelo**.
- **Motivo:** ADR-016 fija que la UI es un cliente; abrir SQLite desde la UI
  rompería la única autoridad de estado.
- **Consecuencia:** toda operación de modelado es reproducible desde la API y
  desde la CLI. M6 se entrega en dos partes: (1) cargar + modelo, (2) ejecución
  + resultados.

### D-016 — M6 completa: ejecución y resultados en la UI (ADR-019)

- **Fecha:** 2026-11-12
- **Decisión:** la UI gana las vistas **Ejecutar** (aplica reglas y guarda huella)
  y **Resultados** (historial de ejecuciones y diff entre dos). La API expone
  `GET/POST /v1/models/{id}/runs`, `GET /v1/diff` y `GET /v1/orphans`.
- **Motivo:** completar las 13 capacidades del motor de modelado (E4 y D1 en UI).
- **Consecuencia:** con M6, las capacidades de modelado están disponibles según
  su superficie: CLI y API para operaciones de motor, y escritorio para las
  vistas de carga, modelo, ejecución y resultados. El panel de estado resume
  esa distribución sin atribuir acciones inexistentes a la UI. **Defecto corregido (M4):** si el nombre de una fuente tenía
  punto (`ventas.csv`), DuckDB lo interpretaba como ruta de archivo; ahora cada
  tabla se registra con alias interno (`src_0`), con prueba de regresión.

## Intervención 2026-11-12 — entrada no confiable y robustez del worker

**Hallazgo.** Auditoría de backend sobre `src/motor` (evidencia en runtime, no
solo estática). Dos defectos confirmados con la misma raíz —la ingesta y el
pipeline tratan contenido no confiable como si estuviera validado— y un tercero
potencial por carrera:

1. **F-01 (confirmado).** `Worker._process` ejecutaba `run_csv_pipeline` sin
   guarda. Un `.csv` binario en `inbox/` hacía que DuckDB lanzara
   `InvalidInputException`, que propagaba fuera de `run_once` y **tumbaba el
   proceso**; con `Restart=on-failure` el worker entraba en bucle
   reinicio–caída. Mismo caso ya declarado como riesgo P1-2 en ADR-014 (input que
   desaparece entre reconciliación y proceso → `IOException`).
2. **F-02 (confirmado).** Los lectores de `.xlsx`/`.docx`/`.pdf` lanzaban
   excepciones no capturadas ante contenedores malformados: un ZIP válido sin las
   partes OOXML (`KeyError` de `zipfile`) y un PDF ilegible (`FileDataError` de
   PyMuPDF). Las cuatro rutas de modelado de la API solo capturaban
   `UnsupportedFormat/SheetNotFound/EmptySheet`, así que la excepción llegaba al
   `except Exception` del servidor → **500** (sin fuga, pero código de error
   incorrecto) y traceback en la CLI.
3. **F-03 (potencial).** `_job_retry` filtraba el `UPDATE` solo por `job_id`: si
   el worker commiteaba el job entre el `SELECT` y el `UPDATE`, el retry podía
   devolver a `queued` un job ya terminal.

**Evidencia (reproducida antes de tocar código).**

| Escenario | Antes | Ahora |
| --- | --- | --- |
| CSV binario en `inbox/` | `run_once` lanza `InvalidInputException`; el worker muere | job → `failed` + `worker_fail_job`; el worker sigue procesando |
| Input ausente entre reconciliación y proceso | `_process` lanza `IOException` | job → `failed` + auditoría; sin caída |
| `.xlsx`/`.docx` ZIP sin partes OOXML | `KeyError` no capturado → 500 / traceback | `UnsupportedFormat` → **400 `bad_request`** |
| `.pdf` ilegible | `FileDataError` no capturado → 500 / traceback | `UnsupportedFormat` → **400 `bad_request`** |
| `retry` con estado cambiado por el worker | `UPDATE` sin condición de estado | `UPDATE ... WHERE state IN (...)`; 409 si cambió |

**Rojo primero.** Siete pruebas nuevas fallan antes de la implementación:
`test_a_corrupt_csv_fails_the_job_instead_of_crashing_the_worker`,
`test_the_worker_keeps_working_after_failing_a_bad_job`,
`test_a_vanished_input_fails_the_job_instead_of_crashing`,
`test_a_zip_without_ooxml_parts_is_rejected_by_format`,
`test_an_unreadable_pdf_is_rejected_by_format`,
`test_a_truncated_xlsx_inside_a_valid_zip_is_rejected_by_format` y
`test_retry_refuses_when_the_state_changed_underneath`.

**Verde.** `./scripts/test.sh`: 412 pruebas, `ruff` limpio, cobertura 100%
(2085 sentencias, 458 ramas).

**Cambios.**

- `src/motor/worker.py`: la ejecución del pipeline en `_process` queda envuelta
  en `except (duckdb.Error, OSError)`; el fallo determinista llama al nuevo
  `_fail_job`, que marca `failed` (limpia `worker_id`/`lease_until`) y audita
  `worker_fail_job` sin registrar contenido. `duckdb` pasa a import de módulo.
- `src/motor/sources/detect.py`: `_check_ooxml` se generaliza a `_check_container`;
  abre el ZIP y exige la parte mínima (`xl/workbook.xml`, `content.xml`,
  `word/document.xml`), validando también `.docx`, que antes no se comprobaba.
- `src/motor/sources/__init__.py`: `read_any` traduce los errores previsibles de
  los lectores (`BadZipFile`, `KeyError`, `ValueError`, `UnicodeError`, `OSError`,
  `RuntimeError`) a `UnsupportedFormat`, el conjunto que todos los consumidores ya
  capturan. No se usa `except Exception`: los errores de programación siguen
  aflorando.
- `src/motor/api/routes.py`: `_job_retry` repite la guarda de estado en el
  `UPDATE` (`WHERE state IN (...)`) y responde 409 si no actualizó ninguna fila.
- `tests/unit/test_worker.py`, `tests/unit/test_sources.py`,
  `tests/unit/test_api.py`: las siete pruebas de regresión.

**Nota histórica (revisada el 2026-11-15).** En aquella intervención
`pyproject.toml` conservaba `crewai` como dependencia del proyecto Flow. El
manifiesto actual ya no la declara; las hojas usan `openpyxl` y `odfpy`. Las
rutas actuales de API se sincronizan con `docs/api/openapi.yaml`.

**Pendiente de aquella intervención (resuelto el 2026-11-15).** La carga limita
fuentes a 16 MiB en HTTP y CLI; ahora los lectores también acotan expansión ZIP,
filas, columnas, celdas, páginas y texto extraído. Los límites se documentan en
`src/motor/limits.py` y devuelven un rechazo de formato controlado.

## Intervención 2026-11-12 (cuarta) — cierre de verificación y cadena de suministro

**Alcance.** Tres frentes sobre el estado entregado: cobertura del gate, drift
documental y cadena de suministro de dependencias.

**Hallazgos y correcciones.**

1. **El gate no cubría el 100%.** `./scripts/test.sh` pasaba con 99%: tres líneas
   sin cubrir en `src/motor/api/routes.py` (122, 130, 609) mientras `fail_under`
   era 95. La cobertura real es 100%; el umbral laxo admitía la regresión sin
   avisar. Se añaden cuatro pruebas de regresión en `tests/unit/test_api_models.py`
   (`_json_param` con parámetro ausente → default `{}`/`[]`, y con tipo JSON
   distinto → 400) y se sube `fail_under` a 100. La línea 609 era un `return {}`
   **inalcanzable** (`_rules_params` solo se llama tras validar `kind` contra
   `RULE_TYPES`, cuyos cinco tipos ya tienen rama), así que se sustituye por un
   guardia defensivo con `# pragma: no cover - defensa en profundidad`, la misma
   convención que `src/motor/model/engine.py:42`. Verificado: con dos de las
   pruebas nuevas omitidas el gate falla (exit 2) al bajar a 99%, o sea que el
   umbral ahora muerde.

2. **`odfpy` no estaba en `uv.lock`.** Declarado en `pyproject.toml` y presente en
   el venv, pero ausente del lock, así que `uv sync --frozen` (el install
   documentado en el README) **lo desinstalaba** y el soporte `.ods` de M1
   (`src/motor/sources/tables.py:113`) se rompía en un checkout limpio. `uv lock`
   lo añade con su sdist fijado por hash y su dependencia `defusedxml`.

3. **Cadena de suministro: 36 avisos en 2 paquetes.** `pip-audit` sobre el venv
   del proyecto reportó 31 avisos en `litellm 1.82.6` y 5 en `chromadb 1.5.9`. El
   pin `litellm<=1.82.6` (override sin motivo escrito, de la línea base) era lo
   que lo retenía en una versión vulnerable; el extra `litellm` de crewai resuelve
   1.105.3 sin avisos. Se elimina el override (en vez de subirlo a un número
   concreto, para no volver a congelar) y se fuerza `filelock>=4.2.1` (transitiva
   vía huggingface-hub, 13 avisos, corregidos en 4.2.1). Resultado: **36 → 5
   avisos**, y los 5 restantes son todos de `chromadb 1.5.9`, dependencia dura de
   `crewai==1.15.39` **sin versión de corrección publicada**; queda como riesgo
   aceptado, no corregible sin que crewai cambie. Ni `litellm` ni `chromadb` los
   importa `src/motor`.

**Verde.** `./scripts/test.sh`: 448 pruebas, `ruff` limpio, cobertura 100%
(2174 sentencias, 478 ramas), `fail_under=100`, `EXIT=0`. `uv lock --check` pasa.
`pip-audit` sobre el venv: 5 avisos (solo `chromadb`).

**Riesgos que quedan.**

- `chromadb 1.5.9` con 5 avisos sin corrección disponible (transitiva de crewai).
- T-001 (ejecución real del Flow), T-011 (heartbeat entre lotes), T-013
  (`WatchdogSec`) y FE-011 (tooltip accesible) siguen abiertos, fuera de alcance
  aquí.

## Intervención 2026-11-12 (quinta) — reglas de proyección por columna

**Alcance.** Investigación de bugs (bug-hunter) sobre `src/motor/` y verificación
de la cadena de transformación del motor de modelado (M3).

**Hallazgos y correcciones.**

1. **El motor descartaba reglas en silencio (BH-001, crítico).**
   `engine._build_select` colapsaba las reglas de proyección en un diccionario
   por columna (`by_column = {r.column: r for r in plan ...}`), así que con dos
   reglas `cast`/`map`/`domain` sobre la **misma** columna solo se aplicaba la
   última. Según el orden: `map`+`cast` anulaba los valores (`[[None],[None]]`),
   `cast`+`map` devolvía texto crudo, `domain`+`map` perdía el `domain`, y
   `cast`+`filter` daba 0 filas — todo **sin error**. Contradecía el contrato
   (`openapi.yaml` §listRules «en su orden de aplicación»; la arquitectura define
   la regla sin restricción de una por columna). Se corrige encadenando las
   reglas **en orden** con una subconsulta por etapa; `filter` sigue en el
   `WHERE` sobre el nombre final de la columna.

2. **`expression_for` no manejaba `domain` (BH-004, medio).** Caía en
   `return _q(column)` (identidad): un `domain` era un no-op silencioso en la
   proyección. La ruta antigua lo ocultaba (el `by_column` ya lo descartaba). Se
   añade la rama que produce la expresión restrictiva y un `raise
   UnknownRuleType` para cualquier tipo sin expresión de proyección, en línea con
   `render` — un tipo futuro sin rama debe fallar, no pasar la columna.

3. **El gate no podía detectar BH-001 (BH-002).** La única prueba multi-regla
   (`test_applies_rename_cast_map_and_returns_values`) usaba columnas distintas;
   con 100% de cobertura de líneas el defecto vivía oculto. Se añaden cinco
   pruebas de regresión que fijan los valores: `map`+`cast`, `domain`+`map`,
   `cast`+`filter`, `domain` con `allowed` y tipo sin proyección → error.

**Verde.** `./scripts/test.sh`: 457 pruebas (452 → 457), `ruff` limpio, cobertura
100% (2197 sentencias, 490 ramas), `fail_under=100`, `EXIT=0`.

**Verificación end-to-end.** Camino real del API (`rules.add` → `run.execute` →
`run.record`): `map`+`cast` sobre `monto` con `[["1.5"],["2,5"],["x"]]` devuelve
`[[1.5],[2.5],[None]]` y registra 3 filas (antes: `[[None],[None]]`).

**Pendiente / bloqueo.**

- **Codex Security no es viable con DeepInfra.** Se resolvió la autenticación
  (perfil `model_providers.deepinfra` con `wire_api: responses`), y el escaneo
  supera el preflight, pero falla de forma determinista: DeepInfra rechaza el
  registro de herramientas MCP del plugin
  (`namespaced function names must be unique across all tools: [_create_file,
  _search, _fetch, …]`). `wire_api: chat` está descartado (Codex ya no lo
  admite). Es incompatibilidad plugin↔proveedor, no del código.

**Sustituto aplicado.** Al no ser viable Codex Security, se ejecutó un escaneo
estático real con triaje manual de cada hallazgo:

- `bandit -r src` → 11 B608 (SQLi) y `semgrep --config=auto src` → 15 hallazgos:
  **todos falsos positivos** en el código propio. El motor cita siempre los
  identificadores (`_ident`/`_q`, `""`) y parametriza/escapa los valores;
  refutado con ataques reales (nombre de columna `x" ); DROP TABLE t; --` no
  altera la tabla; valor `a'b` se escapa a `a''b`).
- `insecure-file-permissions` (`server.py:207`, `SOCKET_MODE = 0o660`): **riesgo
  aceptado (bajo)**. El socket vive en `$XDG_RUNTIME_DIR/motor/` (directorio
  `0700` del usuario) y `panda` es el único miembro de su grupo.
- `pip-audit --path .venv/lib/python3.12/site-packages`: **5 avisos, todos en
  `chromadb 1.5.9`** (transitiva de crewai, sin corrección). Riesgo aceptado, ya
  documentado. (Un `pip-audit` sin `--path` audita el Python del sistema y
  produce ruido irrelevante: `requests`, `urllib3`, `ufw`…).

El informe completo quedó en `audit/bug-hunter.md`.

## Criterios de cierre — 2026-11-12 (dictamen)

**Dictamen histórico: CIERRE CONDICIONADO.** El informe vigente y actualizado
está en `audit/criterios-de-cierre.md`; esta sección conserva la evidencia de la
evaluación del 2026-11-12 y no representa por sí sola el estado de `HEAD`.

**Evaluación histórica de los 16 criterios.** 15 **Cumple**; 1 **Parcial**
(consistencia de entrega §13: no había tag). Evidencia clave:

- Funcionalidad e2e real: `read_any → profile_table → store → rules →
  run.execute → run.record` → `[['Cusco',120.0],['Cusco',340.0]]`.
- Gate histórico: `./scripts/test.sh` → **457 pruebas, 100 % cobertura, ruff limpio,
  EXIT=0**.
- Seguridad: `semgrep`+`bandit` → 0 vulnerabilidades explotables (SQLi
  refutados con ataques reales); 5 avisos solo en `chromadb` (riesgo aceptado).
- Arquitectura: 6 ADRs, import sin ciclos, DuckDB único motor.
- Reproducibilidad: `uv lock --check` OK (171 paquetes).

**Condiciones de cierre** (evidencia de aceptación):

1. Commitear las correcciones de esta sesión (`git status` limpio).
2. Confirmar el gate sobre el commit final (la última ejecución actual registró
   470/100 %/ruff/EXIT=0).
3. Fijar la versión (tag `v0.1.0`, o decisión explícita de no etiquetar).
4. Formalizar T-001/T-003/T-005/T-011/T-013 como *fuera de alcance* de la
   versión.

**Riesgos aceptados** (todos explícitos y documentados): `chromadb 1.5.9`,
socket `0660` (ADR-016), T-011, T-013, T-001, T-005, T-003.

Informe completo en `audit/criterios-de-cierre.md`.

**Recomendación:** congelar el alcance M1–M6 + ingesta + API v1 + UI; cerrar las
4 condiciones y detener el desarrollo de este alcance.

## Sincronización documental — 2026-11-15

Se contrastó la documentación vigente contra `HEAD` (`6468c3b`) y las fuentes
primarias del código:

- API v1: **20 operaciones**, coincidentes entre `src/motor/api/routes.py` y
  `docs/api/openapi.yaml` (9 estáticas + 11 dinámicas).
- Escritorio: **7 vistas**, según `desktop/yachay_desktop/app.py`.
- Gate actual: **470 pruebas**, `ruff` limpio y cobertura total del código fuente
  al ejecutar `./scripts/test.sh`.
- Entrada: `openpyxl` y `odfpy` están declaradas en `pyproject.toml` y se
  documentan como dependencias existentes, no pendientes.

La actualización afecta al README, ADR-016/019, contrato API, arquitectura,
diagramas, seguimiento y auditorías de cierre/UI. Los informes con resultados
anteriores conservan sus cifras y quedan identificados como evidencia histórica.

## Reparación de hallazgos — 2026-11-15

- **INT-01:** `POST /v1/inputs` guarda una única copia en `data/model-inputs/`
  y crea un hard link en `data/inbox/`. El worker conserva su ruta única de
  admisión y su limpieza ya no invalida el perfilado ni los modelos. Los dos
  nombres comparten inode y no duplican bytes.
- **INT-02:** límites comunes en `src/motor/limits.py`: fuente 16 MiB, ZIP
  expandido 32 MiB y 2048 partes, tabla de 100 000 filas / 10 000 columnas /
  1 000 000 celdas, documentos de 5000 páginas / 100 000 filas / 32 MiB de
  texto. Se rechaza la repetición ODS antes de expandirla.
- **INT-03:** se conserva PyMuPDF 1.26.7 porque Yachay solo abre PDF y extrae
  texto; no llama a `Page.add_freetext_annot`, la ruta del aviso CVE revisado.
  PyMuPDF 2.1 añadiría múltiples dependencias de layout/LLM que no usa el
  producto. El pin se volverá a evaluar cuando la corrección relevante pueda
  adoptarse sin esa expansión o si el código empieza a usar anotaciones.
- **INT-04:** nuevos directorios de datos se crean con modo 0750, nuevos archivos
  de carga con 0640, y el CLI usa temporalmente `umask 0027` durante la orden.
  No se cambian permisos de instalaciones existentes.
- **INT-05:** se conserva `odfpy` porque el soporte ODS está implementado y
  cubierto; no se halló un defecto o CVE que justifique un reemplazo. Antes de
  distribuir el producto debe verificarse el inventario de licencias del
  artefacto, dado que PyPI declara Apache/GPL/LGPL y el release no publica
  provenance.
- **INT-06/07:** Hatchling queda fijado en 1.33.0; CI ejecuta `uv sync --locked`,
  build y `scripts/test.sh` en Python 3.11–3.13 con acciones fijadas por SHA. La
  contradicción histórica sobre CrewAI queda marcada como tal.
- **UI-01:** el helper de tooltip no tenía llamadas en las vistas; se retiró
  para no conservar una interacción solo-hover sin contenido de ayuda real.

**Validación:** baseline anterior a cambios: `./scripts/test.sh` → 477 pruebas,
7 omitidas, cobertura 100 %, Ruff limpio. Cierre: 493 pruebas, 7 omitidas,
100 % de cobertura y Ruff limpio; `uv lock --check`, `uv build` y
`pip-audit --local` finalizaron sin hallazgos conocidos. El detalle y límites de validación
están en `audit/evaluacion-integral-2026-11-15.md`.

La política de actualizaciones automatizadas se documenta en
`docs/mantenimiento-dependencias.md` y se ejecuta mediante
`.github/dependabot.yml`; las versiones concretas permanecen en los manifiestos,
lockfile y workflow de CI.

La coherencia entre rutas y documentación se mantiene con
`python scripts/sync_api_docs.py --write`; `scripts/test.sh` valida el registro
contra OpenAPI y el índice generado.

### Visualización local y propuestas acotadas (ADR-020)

- Resultados de corridas del modelo: Parquet local con nombre de fuente y SHA-256;
  migración aditiva v4 en SQLite.
- Informes HTML locales, SVG y tabla accesible; regeneración/borrado con confirmación
  en la vista dedicada. Los datos de corrida sobreviven al borrado del informe.
- Propuestas opcionales de IA sobre el registro actual de reglas; vista previa y
  aceptación humana crean nueva versión, sin ejecución automática. Solo se envían
  instrucción + perfil, sin filas. Requiere `DEEPINFRA_API_KEY` y puede generar costo.
- Se mantiene fuera de alcance el gateway general, ejecución de código arbitrario,
  y la generación automática de nuevas funciones.
