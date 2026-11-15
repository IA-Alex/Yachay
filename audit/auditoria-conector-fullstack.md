# Auditoría conector-fullstack — Yachay (motor de datos local)

> **Registro histórico:** esta auditoría fue ejecutada sobre `b77fcef` y sus
> cifras de cobertura y superficie corresponden a esa revisión. Para el estado
> actual, la fuente primaria es `src/motor/api/routes.py` junto con
> `docs/api/openapi.yaml`; ambos exponen 20 operaciones.

## 1. Alcance y contexto
- **Objetivo y entorno:** verificar que todo lo que el conector de escritorio invoca tiene contraparte real en la API v1 (`src/motor/api`) y que la forma de los datos coincide con el contrato OpenAPI declarado. Pruebas locales/efímeras (socket Unix temporal, SQLite temporal).
- **Stack/topología/transportes:** Python 3.12 · cliente Tk (`desktop/yachay_desktop`) → HTTP/1.1 sobre socket Unix `AF_UNIX` → servidor `http.server` (`src/motor/api/server.py`) → router regex (`routes.py`) → SQLite/DuckDB. Sin TCP, sin colas, sin GraphQL/gRPC.
- **Flujos incluidos:** todos los endpoints v1 (19 operaciones) y todos los métodos de `ApiClient` (19 métodos).
- **Excluidos:** proceso worker y sus consumos internos; `yachay` CLI (no habla con la API, ver §5); flujo CrewAI (`flow.json`, no toca HTTP).
- **Evidencia disponible:** estática (`archivo:línea`), contrato (`docs/api/openapi.yaml`) e integración/E2E con servidor real levantado en socket temporal.
- **Revisado en:** `b77fcef` (worktree: limpio).

## 2. Dictamen
- **Estado:** **CORREGIDO** (ver *Nota de cierre* al final; la conclusión original se conserva abajo)
- **Resumen:** las 19 rutas del cliente existen en el proveedor con método y forma correctos; los 9 tests E2E (`ClientAgainstRealApiTests`/`ClientTransportTests`/`ClientModelTests`) pasan y los payloads de lectura/escritura coinciden con el OpenAPI. Pero la codificación de parámetros en query está rota en el cliente: el nombre de archivo va **sin codificar** en media docena de métodos y, más grave, `upload()` **no codifica nada**, de modo que un archivo llamado `a&b.csv` se deposita en la bandeja como `a` y la API responde `201 accepted` — una llamada que el conector corre en el camino feliz del usuario y que produce una escritura con nombre equivocado. Además, el cliente trata "cuerpo vacío" como éxito cuando este proveedor nunca emite `204`.
- **Severidad:** crítico 1 · alto 1 · medio 2 · bajo 2
- **Cobertura:** identificados 19 · trazados 19 · contratos 19 · E2E 9 pruebas / 8 operaciones con E2E directo · fallos 6 · pendientes 2.

## 3. Matriz de trazabilidad
| ID | Origen (conector) | Transporte/operación | Proveedor (archivo:línea) | Resultado | Evidencia | Estado |
|---|---|---|---|---|---|---|
| C-01 | `client.py:141` | `GET /v1/health` | `routes.py:841` → `_health` | `{status, api_version, database}` | E2E `test_reads_match_the_provider`; probe | OK |
| C-02 | `client.py:145` | `GET /v1/jobs?limit=&state=` | `routes.py:842` → `_jobs_list` | `{jobs:[...], count}`; valida `state` ∈ `JOB_STATES` | E2E; estático `theme.py:212` = `schema.py:11` | OK |
| C-03 | `client.py:153` | `GET /v1/jobs/{job_id}` | `routes.py:853` → `_job_detail` | mismo `Job` + `run_id` | E2E `not_found` | OK |
| C-04 | `client.py:157` | `GET /v1/datasets?limit=` | `routes.py:844` → `_datasets_list` | `{datasets, count}` | E2E | OK |
| C-05 | `client.py:162` | `GET /v1/datasets/{run_id}/manifest` | `routes.py:860` → `_dataset_manifest` | manifest JSON | estático | OK |
| C-06 | `client.py:168` | `POST /v1/jobs/{job_id}/cancel` | `routes.py:854` → `_job_cancel` | `{job_id, cancel_requested}` | estático | OK |
| C-07 | `client.py:172` | `POST /v1/jobs/{job_id}/retry` | `routes.py:856` → `_job_retry` | `{job_id, state:"queued"}` | estático | OK |
| C-08 | `client.py:176` | `POST /v1/inputs?name=` (cuerpo crudo) | `routes.py:843` → `_inputs_upload` | `{accepted, name, size_bytes}` | probe: `"a&b.csv"` → `{name:"a"}`, disco `inbox/a` | **DESAJUSTE** (H-01) |
| C-09 | `client.py:194` | `GET /v1/models` | `routes.py:845` → `_models_list` | `{models:[{model_id,name,version,entities:[str]}], count}` | probe | OK |
| C-10 | `client.py:199` | `GET /v1/models/{id}` | `routes.py:862` → `_model_detail` | `Model` completo | probe | OK |
| C-11 | `client.py:203` | `POST /v1/models?name=&source=` | `routes.py:846` → `_model_create` | `Model` completo, 201 | E2E `_model()` | OK |
| C-12 | `client.py:211` | `GET /v1/profile?name=` | `routes.py:847` → `_profile_input` | `{file,columns,rows,note,profile[]}`; `ColumnProfile.as_dict` tiene los 8 campos que la UI lee (`app.py:1099-1105`) | E2E + probe | OK |
| C-13 | `client.py:217` | `GET /v1/models/{id}/runs` | `routes.py:864` → `_runs_list` | `{runs:[{run_id,created_at,output,rows,columns,hash}], count}` | probe | OK |
| C-14 | `client.py:222` | `POST /v1/models/{id}/runs?source=` | `routes.py:866` → `_run_create` | `{run_id,model_id,columns,rows_out,hash}`, 201 | probe; la UI lee justo esas claves (`app.py:1374-1380`) | OK |
| C-15 | `client.py:230` | `GET /v1/diff?before=&after=` | `routes.py:848` → `_diff` | `{changed,reason,rows_before,rows_after}` | E2E; probe `not_found` | OK |
| C-16 | `client.py:236` | `GET /v1/models/{id}/rules` | `routes.py:868` → `_rules_list` | `{rules:[{name,type,column,params}], count}` | E2E `test_rules_round_trip` | OK |
| C-17 | `client.py:241` | `POST /v1/models/{id}/rules?name=&type=&column=&…` | `routes.py:870` → `_rule_create`; params solo del tipo (`routes.py:587-615`) | `RuleCreated` | E2E ×2 + probe acento/`&` en JSON | OK |
| C-18 | `client.py:274` | `DELETE /v1/models/{id}/rules/{name}` | `routes.py:872` → `_rule_delete` | `{model_id,name,deleted}`; exige match exacto del nombre (`routes.py:693-696`) | probe: `delete_rule("año")` → `not_found` con la regla aún en la lista | **DESAJUSTE** (H-02) |
| C-19 | `client.py:282` | `GET /v1/orphans?left=&right=&on=&right_on=` | `routes.py:849` → `_orphans` | `{columns,rows_out,orphans[:50]}` | E2E `test_orphans_of_two_files` | OK |
| S-01 | — | Envelope `{api_version,ok,data|error}` | `envelope.py:57-70`; error `{code}` cerrado | coincide con `client.py:25-33` | E2E `test_error_envelope_*` | OK |
| S-02 | `client.py:126-128` | Cuerpo vacío ⇒ `return None` | el servidor **siempre** envía JSON con `Content-Length`; nunca 204 (`server.py:160-172`) | rama inalcanzable con este proveedor | estático | **DESAJUSTE** (H-03) |

Todas las rutas cliente ocurren también en `docs/api/openapi.yaml` (19/19); no hay rutas huérfanas en ningún lado salvo lo indicado en §5.

## 4. Hallazgos

### H-01 — [CRÍTICO] `upload()` no codifica la query: guarda el archivo con nombre truncado y responde éxito
- **Flujos:** C-08 (subir CSV, `app.py:806`); C-12 (`app.py:1075-1088` depende del `name` devuelto).
- **Evidencia:** [E2E] servidor real en socket temporal:
  - `client.upload("a&b.csv", b"id\n1\n")` → `{'accepted': True, 'name': 'a', 'size_bytes': 5}`; en `inbox/` aparece **`a`** (verificado por glob sobre `cfg.paths.inbox`).
  - `"Sin título.csv"`, `"ventas año.csv"`, `"100% real.csv"` → `http.client.InvalidURL: URL can't contain control characters` (excepción que **no** es `ApiError`/`ApiUnreachable`).
  - `"datos#1.csv"` → el `#1.csv` se trata como fragmento y el servidor recibe `name=datos` → esquivó el 409 de duplicado; luego el 400 de `_SAFE_NAME` invierte el veredicto (`datos#1.csv` es nombre VÁLIDO per `routes.py:30` y OpenAPI `^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$`).
  - [ESTÁTICO] `client.py:181-190`: `f"/v1/inputs?name={name}"` sin `quote`. El `body` y la cabecera `X-Yachay-Filename` (que el servidor acepta como alternativa, `routes.py:321`) van bien; el problema es solo la query.
- **Síntoma:** el usuario ve "✓ a depositado" para un archivo llamado `a&b.csv`; el motor recoge `a` (puede carecer de extensión y fallar detección después). En el peor caso pisa la semántica de admisión: `a` y `a&b.csv` colisionan en el mismo destino sin que el 409 de duplicado lo detecte.
- **Causa:** **CONFIRMADO** — interpolación cruda de un valor arbitrario en la query string. `&` corta el parámetro, `#` corta en fragmento, espacio y no-ASCII invalidan la URL. Contraste con los demás métodos, que sí usan `quote` (`client.py:208,215,227,261-271`).
- **Impacto:** escritura con nombre de archivo equivocado es integridad de datos, no un error cosmético; cualquier archivo con `&`, `#`, `+` o no-ASCII es inalcanzable desde la UI.
- **Corrección mínima propuesta:**
  ```python
  # desktop/yachay_desktop/client.py, upload()
  from urllib.parse import quote
  return self._request(
      "POST", f"/v1/inputs?name={quote(name, safe='')}",
      body=data, headers={...},
  )
  ```
  Además: en `_request`, envolver `conn.request(...)` en `except (InvalidURL, UnicodeEncodeError)` → `ApiError("bad_request", ...)`, para que el UI (`app.py:810-821`, que solo captura `ApiError`/`ApiUnreachable`/`OSError`) no deje la excepción sin tratar en el hilo.
- **Regresión recomendada:** en `ClientAgainstRealApiTests`, subir `"a&b.csv"`, `"100% real.csv"`, `"ventas año.csv"` y `"datos#1.csv"`, y afirmar `result["name"] == nombre` y que el manifest de `inbox/` contiene exactamente esos nombres.

### H-02 — [ALTO] `profile`, `execute`, `create_model` y `orphans` codifican el nombre de archivo y el servidor lo rechaza; `a&b.csv` no sobrevive el round-trip
- **Flujos:** C-12 (perfilar archivo, `app.py:1075-1088`), C-14 (ejecutar modelo, `app.py:1356`), C-11 (`app.py:1239`), C-19 (`client.py:282`).
- **Evidencia:** [E2E] con el archivo `a&b.csv` ya presente en `inbox/`:
  ```
  profile("a&b.csv")        -> bad_request: el nombre debe ser un nombre simple
  execute(mid, "a&b.csv")   -> bad_request: el nombre debe ser un nombre simple
  create_model("m9","a&b.csv") -> bad_request: ...
  orphans("a&b.csv","ok.csv","id") -> bad_request: ...
  ```
  y `delete_rule(mid, "año")` → `not_found` mientras `rules(mid)` sigue devolviendo `['año']`.
- **Síntoma:** el usuario sube un archivo con `&`/`+`/espacio en el nombre y luego la vista "Archivos" no puede perfilarlo. Con H-01 corregido el archivo se deposita con el nombre correcto, pero **seguirá siendo inusualizable** por estos cuatro métodos.
- **Causa:** **CONFIRMADO** — doble codificación cruzada de capas: el conector `quote`-a sin `safe=''`; el servidor aplica `parse_qs` (decodifica una vez) y luego valida contra `_SAFE_NAME` (`routes.py:445,484,543,749-758`), que **no** admite `&`, `+`, espacio ni acentos. Ni el conector ni el proveedor codifican/validan respecto al mismo alfabeto que el contrato declara. `+` prueba la asimetría en sentido contrario: `upload("datos+uno.csv")` llega al servidor como `datos uno.csv` (parse_qs decodifica `+` a espacio) → 400, aunque `+` es legal en `_SAFE_NAME`.
- **Impacto:** silencioso pero reproducible: el nombre que el servidor acepta al subir no es el que acepta en cualquier operación posterior. Afecta `delete_rule` con nombres no-ASCII o con `&` (los nombres de regla no tienen restricción de alfabeto en el servidor, `routes.py:648`).
- **Corrección mínima propuesta:** alinear los dos lados sobre un único conjunto:
  - Proveedor: en `_SAFE_NAME` (y en `upload`), validar **después** de `unquote` ya está hecho por `parse_qs`; el problema es el alfabeto. Ampliarlo a `^[^\x00-\x1f/\\]{1,200}$` (rechaza separadores y control, admite Unicode y `&+#`), o restringir la UI a `[A-Za-z0-9._-]` **antes** de subir con mensaje claro.
  - Conector: `quote(name, safe='')` en **todos** los métodos que interpolan nombre (`client.py:183,208,215,227,279,286-288`) y `delete_rule` para nombres unicode.
- **Regresión recomendada:** prueba de ida y vuelta `upload → profile → execute → orphans` con los cuatro nombres difíciles; y `create_rule("año")→delete_rule("año")`.

### H-03 — [MEDIO] El cliente acepta "cuerpo vacío" como éxito; el proveedor nunca emite 204
- **Flujos:** S-02, transversal a los 19 métodos.
- **Evidencia:** [ESTÁTICO] `client.py:126-128` → `if not raw: return None`. `server.py:160-172` (`_send`) serializa siempre `ok(...)`/`error(...)` con `Content-Length`; en rutas solo existe `Response` normal (`routes.py:932`). Verificado: no hay `204` en `src/` ni en `docs/api/openapi.yaml`.
- **Síntoma:** un proxy, un corte a mitad de respuesta o un bug futuro que devuelva 204 se interpretan como éxito; un método como `health()` devolvería `None` y `app.py:293-294` reventaría con `AttributeError` fuera de los `except` declarados.
- **Causa:** **CONFIRMADO** (rama y docstring desalineadas con la conducta real del proveedor; `204` no está en el contrato).
- **Impacto:** enmascara fallos del transporte como operación exitosa. Bajo hoy — el servidor está en el mismo host sobre un socket — pero es exactamente el tipo de desalineación que aquí se audita.
- **Corrección mínima propuesta:** eliminar la rama (lanzar `ApiError("internal_error", "respuesta vacía")`) o declarar `204` en OpenAPI y tratarlo por método.
- **Regresión recomendada:** stub que responda `204` sin cuerpo y `200` con cuerpo vacío → ambos deben elevar `ApiError`, no devolver `None`.

### H-04 — [MEDIO] Sin tests de contrato automático: el OpenAPI no se valida contra el servidor
- **Flujos:** todos.
- **Evidencia:** [ESTÁTICO] `docs/api/openapi.yaml` declara shapes validables (`additionalProperties: false`, `required`) pero ningún test lo carga (`grep` de `openapi` en `tests/` → 0). El E2E `ClientAgainstRealApiTests` cubre 8 de 19 operaciones (health, jobs, datasets, models, profile, rules×2, orphans, y arrastra upload/create_model/execute) pero **no** `job detail`, `cancel`, `retry`, `manifest`, `diff`, `model detail` individual y `create_rule` de cast/filter como contrato.
- **Síntoma:** el drift de H-01/H-02 no se detectó porque ningún test compara el `name` devuelto con el enviado en casos difíciles, y ningún test valida la respuesta contra el YAML.
- **Causa:** **CONFIRMADO** (ausencia de verificación de contrato). Reforzado por `fail_under = 100` en `pyproject.toml`: el gate de cobertura da falsa confianza al no penalizar *qué* se ejercita.
- **Impacto:** el daño de H-01 pasó "verde" por el CI.
- **Corrección mínima propuesta:** añadir a `ClientAgainstRealApiTests` el camino de nombres con `[`a&b.csv`, `ventas año.csv`, `100% real.csv`, `datos#1.csv`]` y una validación ligera del YAML (yal es dependencia; sin ella, asertar keys de cada respuesta contra un dict de contrato).
- **Regresión recomendada:** la misma prueba debe fallar con el cliente actual y pasar tras H-01/H-02.

### H-05 — [BAJO] `job()` no se usa en la UI pero es el único lector de `run_id`
- **Evidencia:** [ESTÁTICO] `client.py:153-155` existe y se prueba (`test_error_envelope...`); `app.py` nunca lo llama. La UI deduce el `run_id` de la lista de datasets (`Dataset.run_id`), no del trabajo.
- **Causa:** **CONFIRMADO** (operación cubierta por el cliente pero sin consumidor en este repo). No es un fallo; es un huérfano de conector (§5).
- **Impacto:** ninguno funcional. Es deuda: un ciclo de vida trabajo→dataset que la UI no muestra aunque el contrato lo ofrece.
- **Corrección mínima propuesta:** o conectar la selección de trabajo a `job()` (mostrar `run_id` al completar), o retirar el método hasta tener consumidor.
- **Regresión recomendada:** ninguna si se retira; si se conecta, E2E de `job()` contra servidor real con `run_id` no nulo.

### H-06 — [BAJO] `ERROR_CODES` del cliente es una copia manual del conjunto cerrado del servidor
- **Evidencia:** [ESTÁTICO] `client.py:25-33` vs `envelope.py:13-22`; coinciden hoy los 7 códigos. Nada los compara en tests.
- **Síntoma:** un octavo código añadido en el servidor aparecería en la UI como el código en bruto (`ApiError.label` degrada a `self.code`).
- **Causa:** **CONFIRMADO** — fuente de verdad duplicada (`SIN CONTRATO`).
- **Impacto:** cosmético hoy, real en la siguiente extensión del conjunto.
- **Corrección mínima propuesta:** derivarlo de `motor.api.envelope.ErrorCode` (el desktop ya puede importar `motor` en este repo), o exponerlo en `GET /v1/health.capabilities`.
- **Regresión recomendada:** test que compare `set(ERROR_CODES)` con `{v for k,v in vars(ErrorCode).items() if not k.startswith("_")}`.

## 5. Operaciones sin correspondencia
- **Proveedor sin consumidor en este repo:** `GET /v1/jobs/{job_id}` (H-05). Puede tener consumidores externos (scripts, operadores); no se afirma que no existan.
- **Proveedor sin consumidor en este repo:** `GET /v1/datasets/{run_id}` y `GET /v1/datasets/{run_id}/manifest` son alias (`routes.py:390-396`); la UI usa solo `manifest`. El detalle por `run_id` es redundante con la lista.
- **Conector sin consumidor:** `ApiClient.rules/create_rule/delete_rule` se usan en tests pero **no en la UI** (`grep` de `client.rules(` en `app.py` → 0). Las reglas se administran por API/tests, no por escritorio: es una capacidad sin superficie de usuario. No es huérfano del proveedor.
- **CLI `yachay`:** `src/motor/__main__.py` no habla con la API (solo arranca `serve`); no es un segundo conector para esta matriz.

## 6. No verificado y límites
- **Servidor/paths reales en disco:** todas las pruebas usan `MotorConfig.from_root(tmp)`; no se tocó el workspace real (`data/`). Comportamiento equivalente por lectura de `config.py:39-55`, pero no ejecutado sobre datos reales.
- **UI bajo X:** `AppSmokeTests` requiere display; no se ejecutaron. Los caminos de UI (`app.py:806,1075,1356`) se evaluaron por lectura + E2E del cliente subyacente, no con clics.
- **Proxy/permisos del socket** (`0660`, grupo): fuera del repo; no se pudo observar si un tercero filtra o reescribe query strings antes del servidor. Si existiera un proxy, H-01/H-02 podrían empeorar; no se asume.
- **Scanner del motor** y qué hace con un archivo llamado `a` sin extensión: no ejecutado; impacta la severidad *práctica* de H-01 pero no su causa.

## Nota de cierre (posterior a la auditoría)
- **H-01 y H-02 — corregidos.** Ni el servidor ni el conector deben aceptar nombres fuera de `_SAFE_NAME` (ADR-016 §12, solo US-ASCII). El defecto real era que el conector no codificaba y el proveedor respondía mal: `upload("a&b.csv")` guardaba `a` con un 201. Ahora el conector rechaza en la UI con un mensaje explicativo, codifica una sola vez (`safe=''`, `_quote_target`) y nunca renombra en silencio. **La afirmación original «el servidor acepta nombres no-ASCII» era errónea**: la confirmó la verificación con servidor real.
- **H-02 (bis) — `delete_rule` con `#`/espacio/acento.** Corregido: `_resolve` decodifica solo el valor capturado tras casar la ruta, así `%2F` no crea segmentos y el log conserva la plantilla.
- **H-03 — corregido.** Cuerpo vacío ⇒ `ApiError`; el proveedor nunca emite 204.
- **H-06 — corregido.** `ErrorCode.codes()` es la fuente única; el cliente deriva `ERROR_CODES`.
- **H-04 — corregido.** `OpenApiContractTests` ya validaba rutas, códigos y estados, pero **no la forma de las respuestas**: borrar `database` del esquema `Health` dejaba el gate en verde. Añadido `tests/unit/openapi_min.py` (validador sin dependencias nuevas) y un test que levanta el servidor real y compara cada envelope contra el esquema. Cubierto por 4 tests de falsación (campo perdido, tipo cambiado, desvío conocido que desaparece, coherencia intacta).
- **H-05 — pendiente por decisión de producto.** `job()` sigue sin consumidor: la UI deriva `run_id` de datasets.

### Hallazgos nuevos al cerrar H-04
- **H-07 [BAJO] — `run_id` es entero en el motor y `string` en el OpenAPI.** Evidencia E2E: `POST /v1/models/{id}/runs` → `{"run_id": 1, ...}` y `GET .../runs` → `[{"run_id": 1, ...}]`, frente a `RunCreateData.run_id: type: string` y `Run.run_id: type: string`. No rompe el conector (lo trata como valor opaco), pero el contrato miente. Se **documenta** en `KNOWN_CONTRACT_GAPS` para que el gate no lo oculte y falle si se corrige sin borrar la entrada.
- **H-08 [MEDIO] — `columns` se declara `integer` y el motor devuelve una lista.** `RunCreateData.columns` y `Run.columns` son `type: integer`; la respuesta real es `["id","e"]`. Por el skill de visualización: cualquier consumidor que intentara leer `columns` como recuento para graficar obtendría basura. También catalogado en `KNOWN_CONTRACT_GAPS`.
- Gate: `ruff` + 474 tests + cobertura 100% (incluye `openapi_min.py`).

## 7. Plan de corrección
| Orden | Hallazgo | Dependencia | Cambio mínimo | Verificación de cierre |
|---|---|---|---|---|
| 1 | H-01 | — | `quote(name, safe='')` en `client.upload`; capturar `InvalidURL`/`UnicodeEncodeError` en `_request` → `ApiError` | E2E: `upload("a&b.csv")` devuelve `name == "a&b.csv"` y el archivo en `inbox/` tiene ese nombre |
| 2 | H-02 | H-01 | Alinear alfabeto: ampliar `_SAFE_NAME` o restringir en UI; `quote(safe='')` en `profile/execute/create_model/orphans/delete_rule` | E2E round-trip `upload→profile→execute→orphans` con `a&b.csv`, `ventas año.csv`, `100% real.csv`, `datos#1.csv`; `create_rule("año")→delete_rule("año")` |
| 3 | H-04 | 1, 2 | Añadir esos casos a `ClientAgainstRealApiTests` y validación de keys contra OpenAPI | El test falla con el cliente previo y pasa con el corregido |
| 4 | H-03 | — | Sustituir la rama de cuerpo vacío por error (o declarar 204 en OpenAPI) | Stub 204/200-vacío → `ApiError` |
| 5 | H-06 | — | Derivar `ERROR_CODES` de `envelope.ErrorCode` o de `/v1/health` | Test de igualdad de conjuntos |
| 6 | H-05 | — | Conectar `job()` a la vista de cola o retirarlo | E2E `job()` con `run_id` no nulo (si se conecta) |
