# Diagramas de arquitectura — Yachay motor de modelado

> **Qué es esto.** Una **app de escritorio** (tkinter/ttk) para uso frecuente de
> un solo operador en su propio equipo: el operador abre la ventana y el motor
> local queda detrás. La API sobre socket Unix **no es un servicio de red**: es
> el transporte local entre la ventana y el motor en la misma máquina
> (`AF_UNIX`, sin TCP; autenticación Bearer opcional — ADR-016). El CLI es la misma cosa sin
> ventana. Ver `ARQUITECTURA-MODELADO.md` §1: «desde una **app de escritorio**».
>
> Estado: **derivado del código** (no de la intención). Cada relación se verificó
> contra las fuentes primarias el 2026-11-12: `src/motor/**`, `desktop/yachay_desktop/**`,
> `src/motor/storage/schema.py`, `src/motor/api/routes.py` y `docs/api/openapi.yaml`.
>
> Notación: **Mermaid** como formato fuente (texto versionable, se renderiza en
> GitHub y en cualquier visor Markdown). No añade dependencias al proyecto
> (ADR-016). Fallback si hiciera falta imagen estática: exportar a SVG con
> `npx -y @mermaid-js/mermaid-cli`.
>
> Cuando un diagrama muestra algo inferido y no literal del código, se marca
> **(inferido)**. Todo lo demás tiene una línea de código detrás.

## Índice y elección de notación

| # | Pregunta que responde | Diagrama | Audiencia |
| --- | --- | --- | --- |
| 1 | ¿Qué piezas hay y de qué depende cada una en tiempo de ejecución? | Componentes (C4-nivel 2, UML-like) | Arquitecto, dev |
| 2 | ¿Cómo viaja un dato desde el disco hasta Parquet y vuelta a la app? | Secuencia | Dev, operador |
| 3 | ¿Qué tablas hay y cómo se relacionan en SQLite? | ERD | Dev, DBA |
| 4 | ¿Cómo se mueve un trabajo por los 9 estados? | Máquina de estados | Operador, dev |
| 5 | ¿Qué capas importan a qué capas? | Grafo de dependencias | Arquitecto |

Se usa **UML-like (C4, ERD, máquina de estados, grafo) y no UML formal** porque
la pregunta es de arquitectura de una aplicación concreta, no de intercambio de
modelos entre herramientas: no hay XMI, ni round-trip, ni auditoría de
conformidad UML. Mermaid comunica cada pregunta con menos ceremonia y queda
embebido en la documentación del repo.

---

## 1. Diagrama de componentes (C4 nivel 2)

La app de escritorio es la **interfaz principal**: el operador la abre, y detrás
vive el motor local servido por el socket Unix en el mismo equipo. El CLI es la
alternativa sin ventana para las mismas operaciones.

```mermaid
flowchart TB
    subgraph fuera["Fuera de la app"]
        Hojas["Hojas Excel / CSV / ODS<br/>y documentos .docx / .pdf / .txt"]
        Operador(["Operador<br/>(un solo usuario, su equipo)"])
        Provider["DeepInfra<br/>HTTPS fijo"]
    end

    subgraph app["App de escritorio Yachay (misma máquina)"]
        direction TB

        subgraph ui["Ventana tkinter/ttk — interfaz principal"]
            Desktop["yachay_desktop<br/>app · widgets · theme (tokens §18)"]
        end

        subgraph motor["Motor local (Python, en proceso aparte)"]
            direction TB
            CLI["__main__.py<br/>CLI alternativa: run · api · inspect · model"]
            Worker["worker.py<br/>ciclo scan→admit→claim→run→publish→commit"]
            API["api/<br/>server socket AF_UNIX + routes + envelope"]
            Gateway["deepinfra_gateway.py<br/>socket AF_UNIX + salida HTTPS acotada"]

            subgraph ing["ingestion/"]
                Scanner["scanner · admission · identity"]
            end

            subgraph srcs["sources/"]
                Detect["detect"]
                Tables["tables"]
                Docs["documents"]
                Profile["profile"]
            end

            subgraph mdl["model/ (M2-M5)"]
                Store["store"]
                Infer["infer"]
                Rules["rules"]
                Joins["joins"]
                Engine["engine"]
                Run["run · diff"]
            end

            Pipe["pipelines/csv_pipeline"]
            Publish["publishing/publish"]
            Storage["storage/ (db, schema, jobs)"]
            Infra["config · limits · log · reconcile · gc"]
        end

    end

    subgraph estado["Estado persistido local (root/data/)"]
        SQLite[("SQLite WAL<br/>motor.sqlite3")]
        FS[("Sistema de ficheros<br/>inbox · processing · failed · quarantine<br/>tmp · processed · datasets/")]
    end

    DuckDB[("DuckDB<br/>embebido")]
    Parquet[("Parquet + manifest.json")]

    Hojas -->|deposita| FS
    Operador -->|abre y usa| Desktop
    Operador -->|CLI sin ventana| CLI
    Desktop -->|HTTP sobre socket AF_UNIX 0660<br/>misma máquina, sin red| API
    API -->|AF_UNIX<br/>solo instrucción + perfil de columnas| Gateway
    Gateway -->|HTTPS TLS<br/>sin redirecciones; host fijo| Provider
    CLI --> Worker
    CLI --> API
    CLI --> Contrato

    Worker --> Scanner
    Worker --> srcs
    Worker --> Pipe
    Worker --> Publish
    Worker --> Infra
    Scanner --> Storage
    Worker --> Storage

    API --> Storage
    API --> Store
    API --> Infer
    API --> srcs

    srcs --> Detect
    Tables --> Detect
    Docs --> Detect
    Profile --> Tables
    Infer --> Profile
    Infer --> Tables
    Engine --> Rules
    Engine --> Joins
    Engine --> DuckDB
    Run --> Engine
    Store --> Storage

    Pipe --> DuckDB
    Pipe --> Hojas
    Publish --> Parquet
    Publish --> FS
    Storage --> SQLite
    Worker --> FS
    API --> FS
```

**Lectura.** Es **una app de escritorio local**, no un servicio web; la única
excepción de salida a Internet es el adaptador aislado para asistencia:

- **La ventana manda.** `yachay_desktop` (tkinter/ttk) es la interfaz que usa el
  operador a diario. El `worker` y la `api` corren en la misma máquina; la
  ventana se comunica con ellos por el socket Unix `data/motor.sock` (modo
  `0660`). API y worker no tienen red IP; `YACHAY_API_TOKEN` añade autenticación
  Bearer cuando se configura (ADR-016).
- **La asistencia sale por una frontera aparte.** `motor-deepinfra` corre con
  UID `yachay-net`, no acepta TCP y recibe propuestas por AF_UNIX. nftables
  restringe su salida a TCP/443 hacia las IP DNS refrescadas para DeepInfra; el
  proceso usa una lista corta con caducidad y no hace DNS. No puede leer
  `data/`; el API no posee la clave ni acceso de red.
- **Dos planos internos del motor**, separados por una frontera real:
  - El **plano de ingesta** (`worker.py` + `ingestion/` + `pipelines/` +
    `publishing/`) mueve bytes del disco a Parquet. No conoce el "modelo del
    usuario".
  - El **plano de modelado** (`model/`) implementa ADR-019: entidades, reglas y
    cruces sobre DuckDB, con su catálogo en SQLite.
- Comparten `storage/`, `sources/` y la misma base SQLite. La **UI no importa
  `motor`** (verificado: `desktop/` no tiene ningún `import motor`); por eso el
  motor puede estar en un proceso distinto sin que la ventana se acople a él.
- **M6 (entregada)**: la ventana incluye las vistas de carga/perfilado, modelo,
  ejecución y resultados, además de estado, cola y catálogo. El panel de estado
  resume las capacidades documentadas y cada vista conserva el contrato de la
  API v1; la UI no inventa operaciones que el motor no expone.

---

## 2. Secuencia — ingesta de una hoja hasta dataset publicado

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operador
    participant FS as Sistema de ficheros
    participant W as worker.Worker
    participant S as ingestion.scanner
    participant J as storage.jobs
    participant P as csv_pipeline (DuckDB)
    participant Pub as publishing.publish
    participant DB as SQLite
    participant API as api.routes

    Op->>FS: deposita hoja.xlsx en inbox/
    Note over W: un ciclo = reconcile → scan → claim → run → publish → collect
    W->>DB: reconcile(conn, paths)
    W->>FS: itera inbox/
    W->>S: record_snapshot + is_stable_persisted
    alt archivo aún inestable
        S-->>W: no estable → se salta el ciclo
    else archivo estable
        W->>S: file_hash
        W->>W: compute_job_key(hash, pipeline, rules, config_hash)
        W->>DB: SELECT run_id FROM datasets WHERE job_key=?
        alt ya publicado con ese job_key
            W->>FS: unlink (idempotencia)
        else nuevo
            W->>J: enqueue(job_id, job_key)
            W->>FS: move inbox/ → processing/
            W->>J: mark_queued
        end
    end

    W->>J: claim_next(worker_id, now) → lease
    W->>DB: mark_running
    W->>P: run_csv_pipeline(...)
    P->>P: DuckDB lee y transforma
    P-->>W: resultado + schema_fingerprint
    W->>Pub: publish(...) → Parquet + manifest.json
    Pub->>FS: escritura atómica, os.replace
    W->>DB: INSERT datasets (commit físico, ADR-014)
    W->>J: terminal → committed
    W->>FS: processing/ → processed/

    Op->>API: GET /v1/datasets/{run_id}
    API->>DB: lee datasets
    API->>FS: lee manifest.json
    API-->>Op: envelope {api_version, ok, data}
```

**Notas de fidelidad.** El commit es por `job_key` (ADR-014) e idempotente: la
segunda subida del mismo contenido no crea un segundo dataset. El perfilado de
las hojas no vive aquí — `read_any`/`profile_table` se usan tanto por el CLI
`inspect` como por `POST /v1/models` (M1/M2); esta secuencia cubre la ruta
legacy de ingesta, que sigue vigente como capacidad.

---

## 3. ERD — esquema SQLite (`storage/schema.py`)

```mermaid
erDiagram
    jobs ||--o{ job_runs : "tiene ejecuciones"
    jobs ||--|| input_files : "1 input por trabajo"
    jobs ||--o| datasets : "job_key publica dataset"

    models ||--o{ entities : "contiene"
    models ||--o{ rules : "define"
    models ||--o{ joins : "declara"
    models ||--o{ runs : "acumula ejecuciones"
    entities ||--o{ attributes : "tiene"
    entities ||--o{ entity_keys : "clave"

    jobs {
        TEXT job_id PK
        TEXT job_key
        TEXT state "CHECK 9 estados"
        INTEGER attempt
        TEXT run_id
        TEXT worker_id
        REAL lease_until
        INTEGER cancel_requested
        TEXT deferred_reason
        INTEGER priority
        REAL created_at
        REAL started_at
    }
    job_runs {
        TEXT run_id PK
        TEXT job_id FK
        INTEGER attempt
        REAL started_at
        REAL finished_at
    }
    input_files {
        TEXT job_id PK
        TEXT path
        TEXT sha256
        INTEGER size_bytes
        INTEGER mtime_ns
    }
    datasets {
        TEXT run_id PK
        TEXT job_key "UNIQUE (1 committed por job_key)"
        TEXT dataset
        TEXT manifest_path
        REAL committed_at
    }
    audit_events {
        INTEGER id PK
        REAL at
        TEXT action
        TEXT ref_id
        TEXT ref_hash
    }
    scan_snapshots {
        TEXT path PK
        INTEGER size_bytes
        INTEGER mtime_ns
        REAL seen_at
    }

    models {
        INTEGER model_id PK
        TEXT name "UNIQUE (name, version)"
        INTEGER version
        REAL created_at
    }
    entities {
        INTEGER entity_id PK
        INTEGER model_id FK
        TEXT name "UNIQUE (model_id, name)"
    }
    attributes {
        INTEGER attribute_id PK
        INTEGER entity_id FK
        TEXT name
        TEXT type
        INTEGER nullable
    }
    entity_keys {
        INTEGER entity_id PK "PK compuesta"
        TEXT attribute PK
        INTEGER position
    }
    rules {
        INTEGER rule_id PK
        INTEGER model_id FK
        TEXT name "UNIQUE (model_id, name)"
        TEXT type "rename|cast|map|domain|filter"
        TEXT column
        TEXT params "{} JSON"
        INTEGER position
    }
    joins {
        INTEGER join_id PK
        INTEGER model_id FK
        TEXT name
        TEXT left_ref
        TEXT right_ref
        TEXT on_pairs "[] JSON"
        TEXT type "inner|left|anti"
        INTEGER position
    }
    runs {
        INTEGER run_id PK
        INTEGER model_id FK
        REAL created_at
        TEXT output
        TEXT columns "[] JSON"
        INTEGER rows_count
        TEXT hash
    }
```

**Frontera explícita (ADR-005).** SQLite guarda **solo estado y metadatos**; los
datos tabulares viven en DuckDB y en Parquet. Por eso `runs.hash`/`runs.columns`
son huellas, no contenido. `audit_events` y `scan_snapshots` no tienen FK: son
registros append-only / snapshots por ruta.

**Puntos de integridad reales:**
- `datasets.job_key` es **UNIQUE**: máximo un dataset committed por `job_key`
  (ADR-014, commit idempotente).
- `entities`, `attributes`, `rules`, `joins`, `runs` tienen `ON DELETE CASCADE`
  desde `models`: borrar un modelo limpia su catálogo.
- `jobs.state` está acotado por `CHECK` a los 9 estados (ver §4).
- `schema_version` registra v1 (ingesta) → v2 (modelado) → v3 (ejecuciones).

---

## 4. Máquina de estados de `jobs`

```mermaid
stateDiagram-v2
    [*] --> queued: enqueue (scanner admite)
    queued --> claimed: claim_next (lease, attempt++)
    claimed --> running: mark_running
    claimed --> queued: lease vencido → reconcile lo devuelve
    running --> committed: pipeline + publish OK
    running --> failed: error recuperable, sin presupuesto
    running --> deferred: intento agotado (attempt > MAX)
    running --> cancelled: cancel_requested observado
    running --> quarantined: input inválido / no admitible
    committed --> archived: retención / GC
    failed --> queued: retry (attempt=0, limpia run/lease)
    quarantined --> queued: retry
    cancelled --> queued: retry
    failed --> [*]
    archived --> [*]
    quarantined --> [*]
    deferred --> [*]

    note right of claimed
        attempt se incrementa en el CLAIM,
        no en el fallo (ADR-015)
    end note
    note right of running
        9 estados reales, con CHECK en SQLite
    end note
```

**Matiz verificado.** `POST .../retry` acepta `failed|quarantined|cancelled`
(`docs/api/openapi.yaml`, `retryJob`) y deja `state='queued'`, `attempt=0`,
limpiando `run_id`, `worker_id`, `lease_until` y `cancel_requested`. El
incremento de `attempt` sucede en el claim (ADR-015).

---

## 5. Grafo de dependencias internas (capas)

```mermaid
flowchart LR
    subgraph L1["Entrypoints"]
        Cli["motor/__main__.py"]
        Deskm["desktop/…/__main__.py"]
    end
    subgraph L2["Interfaz"]
        Apisrv["api/server.py"]
        ApiRoutes["api/routes.py"]
        Apienv["api/envelope.py"]
        Deskapp["desktop/…/app.py\n+ client, widgets, theme"]
    end
    subgraph L3["Orquestación"]
        Wk["worker.py"]
        Rec["reconcile.py"]
        Gc["gc.py"]
    end
    subgraph L4["Dominio de modelado"]
        MStore["model/store.py"]
        MInfer["model/infer.py"]
        MRules["model/rules.py"]
        MJoins["model/joins.py"]
        MEngine["model/engine.py"]
        MRun["model/run.py"]
        MDiff["model/diff.py"]
    end
    subgraph L5["Fuentes y transformación"]
        SrcInit["sources/__init__.py"]
        SrcDetect["sources/detect.py"]
        SrcTables["sources/tables.py"]
        SrcDocs["sources/documents.py"]
        SrcProfile["sources/profile.py"]
        Pipe["pipelines/csv_pipeline.py"]
        Pub["publishing/publish.py"]
        Ing["ingestion/(scanner, admission, identity)"]
    end
    subgraph L6["Fundación"]
        StorDb["storage/db.py"]
        StorSchema["storage/schema.py"]
        StorJobs["storage/jobs.py"]
        Cfg["config.py"]
        Lim["limits.py"]
        Log["log.py"]
    end

    Cli --> Apisrv
    Cli --> Wk
    Apisrv --> ApiRoutes
    Apisrv --> Apienv
    ApiRoutes --> ApiRoutes
    Deskapp --> Deskapp
    Deskm --> Deskapp

    ApiRoutes --> MStore
    ApiRoutes --> MInfer
    ApiRoutes --> MRun
    ApiRoutes --> MRules
    ApiRoutes --> MDiff
    ApiRoutes --> MEngine
    ApiRoutes --> MJoins
    ApiRoutes --> SrcInit
    ApiRoutes --> StorJobs
    ApiRoutes --> StorDb
    ApiRoutes --> StorSchema

    Wk --> Ing
    Wk --> SrcInit
    Wk --> Pipe
    Wk --> Pub
    Wk --> Rec
    Wk --> Gc
    Wk --> StorJobs
    Wk --> StorDb
    Wk --> Cfg
    Wk --> Lim
    Wk --> Log

    MInfer --> SrcProfile
    MInfer --> SrcTables
    MEngine --> MRules
    MEngine --> MJoins
    MEngine --> SrcTables
    MRun --> MEngine
    MRun --> MJoins
    MRun --> SrcTables
    MDiff --> MRun
    MStore --> StorDb

    SrcInit --> SrcDetect
    SrcInit --> SrcTables
    SrcInit --> SrcProfile
    SrcTables --> SrcDetect
    SrcDocs --> SrcDetect
    SrcDocs --> SrcTables
    SrcProfile --> SrcTables

    StorDb --> StorSchema
    Rec --> StorJobs
    Gc --> Rec
```

**Regla de dirección.** El flujo es de un solo sentido: `Entrypoint → Interfaz →
Orquestación → Dominio → Fuentes → Fundación`. No hay dependencia de una capa
inferior hacia una superior.

**Riesgo de acoplamiento (no es un ciclo que falle).** Los `__init__.py` de
`model/` y `sources/` re-exportan sus submódulos, y algunos submódulos hacen
`from motor.model import …` / `from motor.sources import detect`. Hoy funciona
porque el `__init__` importa `detect` (y `diff`, `engine`, …) en orden
alfabético antes de que el submódulo lo necesite. Es una **cadena frágil**: una
renumeración de imports puede romper la carga. No hay ciclo de ejecución, pero
sí un acoplamiento bidireccional paquete↔submódulo que conviene documentar.

---

## Sustento y verificación

- Grafo de componentes, capas y dependencias: derivado de los `import` reales
  del árbol `src/motor/**` y `desktop/yachay_desktop/**`.
- ERD: copiado de `src/motor/storage/schema.py` (`_SCHEMA`, `_MODEL_SCHEMA`,
  `_SCHEMA_VERSION`).
- Estados: `JOB_STATES` en `schema.py`; transiciones de retry/lease desde
  `docs/api/openapi.yaml` y los ADR-014/015/017.
- Rutas de API: **9 estáticas + 15 dinámicas = 24 operaciones** en
  `src/motor/api/routes.py` (`_STATIC_ROUTES`, `_DYNAMIC_ROUTES`). El "17 rutas"
  de la documentación histórica corresponde a una versión intermedia; el contrato
  actual está en `docs/api/openapi.yaml`.

## Cómo re-renderizar

Los bloques son Mermaid válido. Para SVG/PNG estático:

```bash
npx -y @mermaid-js/mermaid-cli -i docs/modelado/DIAGRAMAS-ARQUITECTURA.md -o out/
```

Para mantenerlos vivos: cualquier PR que cambie `schema.py`, `routes.py` o los
`import` de `src/motor/**` debería actualizar el diagrama correspondiente. Si el
repo añade CI, un paso que compare estos diagramas contra el código (o al menos
que los valide como Mermaid sintácticamente correcto) cierra el bucle.
